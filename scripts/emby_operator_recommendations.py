"""Operator-owned Movie playlists and native Home rows. No transport or secrets.

The adapter is a security boundary, not a convenience HTTP wrapper. See the
companion contract before implementing it. Public operations lock the journal.
Journal version 1 holds the configured legacy list. Version 2 holds up to
MAX_LISTS lists for the same recipient and library under the same lock, run,
pending step and shared Home baseline. Only explicit migrate and downgrade
convert between them, and only explicit init creates an empty version 2
journal; reads never rewrite either version.
"""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from contextlib import contextmanager
from typing import Protocol

# Derived from the unchanged 512-request adapter budget at the worst create path.
MAX_LISTS = 4
# Completed keys are never evicted. Reaching the ceiling refuses new requests.
DONE_LIMIT = 64
LEGACY_SUBTITLE = "operator-provisioned"
MARKER = re.compile(r"holonet-recommendations-[0-9a-f]{32}")
# Arguments bound into each list-scoped request and its intent digest.
LIST_OPERATIONS = {"create-list": ("title", "movies"), "set-members": ("movies",),
                   "set-title": ("title",), "detach-row": (), "attach-row": ("title",),
                   "delete-list": (), "move-row": ("position",)}
# Ordered mutation phases. apply, remove and set-display-title keep their
# version 1 request shapes. Rows are always created with the marker title.
PHASES = {"apply": ("create", "share", "row", "members"), "remove": ("unrow", "delete"),
          "set-display-title": ("title",),
          "create-list": ("create", "share", "row", "title", "members"),
          "set-members": ("members",), "set-title": ("title",), "detach-row": ("unrow",),
          "attach-row": ("row", "title"), "delete-list": ("unrow", "delete"),
          "move-row": ("move",)}
RESULT = {"apply": "applied", "remove": "removed", "set-display-title": "display-title-set",
          "create-list": "created", "set-members": "members-set", "set-title": "title-set",
          "detach-row": "detached", "attach-row": "attached", "delete-list": "deleted",
          "move-row": "moved"}
# Snapshot Home is bounded to this many rows, so a final row index is below it.
MAX_HOME = 100
V1_FIELDS = frozenset("version scope baseline current row pending run done display_title".split())
V2_FIELDS = frozenset("version scope baseline current lists pending run done".split())
# Completed request recorded under an init key. It is never a list request.
INIT_REQUEST = {"operation": "init"}


class Refused(RuntimeError):
    """No safe automatic action is available."""


class NotDispatched(Refused):
    """The trusted adapter proves no mutation transport was attempted."""


def require(condition, message):
    if not condition:
        raise Refused(message)


def opaque(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value),
            "Invalid opaque ID")
    return value


def row_id(value):
    require((type(value) is int and value >= 0) or
            (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)),
            "Invalid row ID")
    return (type(value).__name__, value)


def movie_ids(values, minimum=1):
    require(isinstance(values, (list, tuple)) and minimum <= len(values) <= 25,
            "Expected %d..25 Movies" % minimum)
    result = [opaque(v) for v in values]
    require(len(set(result)) == len(result), "Duplicate Movie IDs")
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def list_marker(scope, list_id):
    """Unchanged marker formula; the legacy list keeps its exact playlist Name."""
    return "holonet-recommendations-" + digest(dict(scope, recipe=opaque(list_id)))[:32]


def list_record(journal, list_id):
    """Version 1 keeps its single list record inline in the journal."""
    return journal if journal["version"] == 1 else journal["lists"].get(list_id)


def display_title(journal, marker):
    """Optional v1 field. Reading legacy journals never migrates their bytes."""
    title = journal.get("display_title", marker) if journal is not None else marker
    require(isinstance(title, str) and 1 <= len(title) <= 256 and
            all(ord(c) >= 32 and ord(c) != 127 for c in title),
            "Invalid journal display title")
    return title


# Fixed existing-row write contract. Unknown stable fields remain readable but
# cannot be silently dropped or forwarded by a title write.
ROW_TITLE_FIELDS = frozenset("UserId Name CustomName Id SectionType Subtitle ViewType "
    "ImageType DisplayMode Monitor ItemTypes ExcludedFolders CardSizeOffset "
    "ScrollDirection ParentId SortBy SortOrder IncludeNextUpInResume Query".split())


def checked_title(title):
    return display_title({"display_title": title}, "")


def list_request(operation, list_id, args):
    """Canonical list-scoped request; the digest binds operation, list and args."""
    require(isinstance(operation, str) and operation in LIST_OPERATIONS and
            set(args) == set(LIST_OPERATIONS[operation]), "Invalid list operation")
    request = dict(operation=operation, list=opaque(list_id))
    if "title" in args:
        request["title"] = checked_title(args["title"])
    if "movies" in args:
        request["movies"] = movie_ids(args["movies"], 0)
    if "position" in args:
        request["position"] = row_position(args["position"])
    return request


def row_position(value):
    """Final 0-based index of an owned row in the normalized Home list. Never bool."""
    require(type(value) is int and 0 <= value < MAX_HOME, "Invalid row position")
    return value


def row_index(home, row):
    """Index of the one row whose typed ID equals the saved row ID."""
    found = [n for n, r in enumerate(home) if row_id(r["Id"]) == row_id(row)]
    require(len(found) == 1, "Missing saved row")
    return found[0]


def native_move_index(normal, edit, row, position):
    """Edit-view Move index that puts the saved row at final normal index position.

    None means the row is already there, so no write is needed. Emby's edit view
    can show one generated section where the normal view shows several rows with
    other typed IDs, so a normal index is never sent as the native index. The
    target is the normal row that will follow the moved row, matched by typed ID
    in the edit view (or the end of both views); an unmatched target refuses.
    """
    ids, edit_ids = [row_id(r["Id"]) for r in normal], [row_id(r["Id"]) for r in edit]
    require(len(normal) <= MAX_HOME and len(edit) <= MAX_HOME and
            len(ids) == len(set(ids)) and len(edit_ids) == len(set(edit_ids)), "Duplicate Home IDs")
    key = row_id(row)
    require(key in ids and key in edit_ids and
            normal[ids.index(key)] == edit[edit_ids.index(key)], "Saved row differs between Home views")
    shared = set(ids) & set(edit_ids)
    require([x for x in ids if x in shared] == [x for x in edit_ids if x in shared],
            "Home views disagree on row order")
    require(type(position) is int and 0 <= position < len(normal), "Row position out of range")
    if ids.index(key) == position:
        return None
    ids.remove(key)
    edit_ids.remove(key)
    if position == len(ids):
        return len(edit_ids)
    require(ids[position] in edit_ids, "Unsupported generated Home section at position")
    return edit_ids.index(ids[position])


def title_row(record, playlist, home, recipient, marker):
    """The saved row of one list. Same-title sibling or manual rows are irrelevant."""
    require(playlist is not None and record is not None and record["row"] is not None,
            "Display title requires retained playlist and row")
    rows = [r for r in home if row_id(r["Id"]) == row_id(record["row"])]
    require(len(rows) == 1, "Missing saved row")
    r = rows[0]
    require(set(r) <= ROW_TITLE_FIELDS and
            {"Id", "Name", "CustomName", "SectionType", "ParentId"} <= set(r),
            "Unsupported fixed row fields")
    require(r["SectionType"] == "playlist" and
            r["ParentId"] == playlist["id"] and
            r["CustomName"] == display_title(record, marker) and
            r.get("UserId", recipient) == recipient,
            "Foreign row or display title drift")
    return copy.deepcopy(r)


class ProtectedAPI(Protocol):
    """All methods use one pinned authenticated admin session; never a global key.

    Reads are complete, bounded, detached snapshots. They fail on truncation.
    Mutation methods dispatch exactly once with redirects and retries disabled.
    Identity evidence and complete read semantics are specified in the contract.
    snapshot derives every owned marker from the held journal, never the caller.
    """
    session_binding: str

    def verified_identity(self) -> dict: ...
    def snapshot(self, recipient: str, marker: str) -> dict: ...
    def query_movies(self, recipient: str, library: str, ids: list) -> list: ...
    def create_playlist(self, actor: str, marker: str): ...
    def share_read(self, playlist: str, recipient: str): ...
    def create_row(self, recipient: str, body: dict): ...
    def set_row_display_title(self, recipient: str, body: dict): ...
    def delete_entries(self, playlist: str, actor: str, entries: list): ...
    def add_movies(self, playlist: str, actor: str, movies: list): ...
    def delete_row(self, recipient: str, row): ...
    def move_index(self, recipient: str, home: list, row, position: int): ...
    def move_row(self, recipient: str, args: dict): ...
    def delete_owned_playlist(self, playlist: str, marker: str, actor: str): ...


class Journal:
    """One held no-follow directory, one lock and fsynced atomic journal.

    root_safe is required by the real entry. Default retains unprivileged core
    fake-test use, still holding the directory against path replacement.
    """
    def __init__(self, directory, *, root_safe=False):
        self.directory = Path(directory)
        self.path = self.directory / "journal.json"  # descriptive only
        require(self.directory.is_absolute() and ".." not in self.directory.parts,
                "Unsafe journal path")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in self.directory.parts[1:]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
                s = os.fstat(fd)
                if root_safe:
                    require(s.st_uid == 0 and not s.st_mode & 0o022, "Unsafe journal ancestor")
            s = os.fstat(fd)
            require(s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) == 0o700,
                    "Journal directory must be owned mode 0700")
            self._fd, fd = fd, None
            self._locked = False
        finally:
            if fd is not None:
                os.close(fd)

    def close(self):
        require(not self._locked, "Journal still locked")
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    @contextmanager
    def locked(self):
        require(self._fd is not None and not self._locked, "Journal unavailable or reentrant")
        fd = os.open("lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=self._fd)
        try:
            self._check(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._locked = True
            yield
        finally:
            self._locked = False
            os.close(fd)

    def _check(self, fd):
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and
                stat.S_IMODE(s.st_mode) == 0o600 and s.st_nlink == 1 and
                s.st_size <= 4 * 1024 * 1024, "Unsafe journal file")

    def load(self):
        require(self._locked, "Journal lock required")
        try:
            fd = os.open("journal.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=self._fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd) as stream:
            self._check(stream.fileno())
            def pairs(values):
                result = {}
                for k, v in values:
                    require(k not in result, "Duplicate journal key")
                    result[k] = v
                return result
            try:
                loaded = json.load(stream, object_pairs_hook=pairs,
                                   parse_constant=lambda _: require(False, "Nonfinite journal"))
                require(type(loaded) is dict, "Malformed journal")
                return loaded
            except (ValueError, RecursionError):
                raise Refused("Malformed journal") from None

    def save(self, value):
        require(self._locked, "Journal lock required")
        # Validate existing custody before replacement as well.
        self.load()
        temporary = ".journal-" + os.urandom(16).hex()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self._fd)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, "journal.json", src_dir_fd=self._fd, dst_dir_fd=self._fd)
            os.fsync(self._fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self._fd)
            except FileNotFoundError:
                pass


class Operator:
    def __init__(self, api: ProtectedAPI, journal: Journal, *, server, actor,
                 recipient, library, recipe):
        for value in (server, actor, recipient):
            require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value),
                    "Expected exact server/user ID")
        opaque(library)
        opaque(recipe)
        self.api, self.journal = api, journal
        self.scope = dict(server=server, actor=actor, recipient=recipient,
                          library=library, recipe=recipe)
        # Version 2 is recipient-scoped; recipe names the legacy list.
        self.base = {k: v for k, v in self.scope.items() if k != "recipe"}
        self.marker = list_marker(self.base, recipe)

    def marker_for(self, list_id):
        return self.marker if list_id is None else list_marker(self.base, list_id)

    def _identity(self):
        identity = self.api.verified_identity()
        require(isinstance(identity, dict) and identity.get("verified") is True and
                identity.get("is_admin") is True, "Unverified/nonadmin identity")
        require(identity == dict(self.base, verified=True, is_admin=True,
                                 auth_mode="authenticated-admin-session",
                                 session_binding=self.api.session_binding),
                "Verified admin session/actor/server/recipient/library required")
        require(isinstance(self.api.session_binding, str) and bool(self.api.session_binding),
                "Missing session binding")

    def _markers(self, d):
        if d is None or d["version"] == 1:
            return {self.marker}, 1
        ids = set(d["lists"])
        if d["pending"] and d["pending"]["operation"] == "create":
            ids.add(d["pending"]["list"])
        return {self.marker_for(i) for i in ids}, MAX_LISTS

    def _read(self, d):
        self._identity()
        s = copy.deepcopy(self.api.snapshot(self.scope["recipient"], self.marker))
        require(isinstance(s, dict) and set(s) == {"home", "playlists"}, "Invalid snapshot")
        require(isinstance(s["home"], list) and len(s["home"]) <= 100 and
                all(isinstance(r, dict) for r in s["home"]), "Unbounded/invalid Home")
        rows = [row_id(r.get("Id")) for r in s["home"]]
        require(len(rows) == len(set(rows)), "Duplicate Home row IDs")
        markers, limit = self._markers(d)
        require(isinstance(s["playlists"], list) and len(s["playlists"]) <= limit,
                "Ambiguous owned marker")
        for p in s["playlists"]:
            require(set(p) == {"id", "marker", "owner", "access", "entries"}, "Invalid playlist")
            opaque(p["id"])
            require(isinstance(p["marker"], str) and p["marker"] in markers and
                    p["owner"] == self.scope["actor"], "Foreign playlist")
            require(p["access"] in ([], [self.scope["recipient"]]), "Unexpected playlist access")
            require(isinstance(p["entries"], list) and len(p["entries"]) <= 25, "Too many occurrences")
            entries = []
            for entry in p["entries"]:
                require(isinstance(entry, dict) and set(entry) == {"movie", "entry"}, "Invalid occurrence")
                opaque(entry["movie"])
                entries.append(opaque(entry["entry"]))
            require(len(set(entries)) == len(entries), "Duplicate occurrence IDs")
        found = [p["marker"] for p in s["playlists"]]
        require(found == sorted(set(found)) and
                len({p["id"] for p in s["playlists"]}) == len(found), "Ambiguous owned marker")
        return s

    def _visible(self, movies):
        if not movies:
            return
        result = self.api.query_movies(self.scope["recipient"], self.scope["library"], movies)
        require(result == [{"Id": m, "Type": "Movie", "LibraryId": self.scope["library"]}
                           for m in movies], "Recipient Movie visibility/order mismatch")

    def _journal(self):
        """Validate custody before any snapshot. Never migrates or saves."""
        d = self.journal.load()
        if d is None:
            return None
        require(type(d) is dict, "Foreign journal")
        if d.get("version") != 2:
            require(d.get("version") == 1 and d.get("scope") == self.scope, "Foreign journal")
            display_title(d, self.marker)
            return d
        require(set(d) - {"migrated_from"} == V2_FIELDS and d["scope"] == self.base and
                type(d["lists"]) is dict and len(d["lists"]) <= MAX_LISTS and
                type(d["done"]) is dict, "Foreign journal")
        for list_id, record in d["lists"].items():
            require(type(record) is dict and "row" in record and
                    set(record) <= {"row", "display_title"}, "Invalid list record")
            display_title(record, self.marker_for(list_id))
        require(sorted(p["marker"] for p in d["current"]["playlists"]) ==
                sorted(self.marker_for(i) for i in d["lists"]),
                "Journal list records disagree with saved playlists")
        return d

    def _fresh(self, snapshot, d, version):
        if d is not None:
            return d
        require(not snapshot["playlists"], "Existing playlist requires explicit operator recovery")
        require(not any(r.get("CustomName") == self.marker for r in snapshot["home"]),
                "Existing managed row requires explicit operator recovery")
        if version == 1:
            return dict(version=1, scope=self.scope, baseline=snapshot, current=snapshot,
                        row=None, pending=None, run=None, done={})
        return dict(version=2, scope=self.base, baseline=snapshot, current=snapshot,
                    lists={}, pending=None, run=None, done={})

    @staticmethod
    def _request(d, legacy, scoped):
        request = legacy if d["version"] == 1 else scoped
        require(request is not None, "Journal migration required")
        return request

    @staticmethod
    def _name(request):
        return request.get("operation") or ("remove" if request["remove"] else "apply")

    def _target(self, d, snapshot, list_id):
        marker = self.marker_for(list_id)
        found = [p for p in snapshot["playlists"] if p["marker"] == marker]
        return list_record(d, list_id), marker, (found[0] if found else None)

    def _row_body(self, playlist, record, marker):
        return dict(SectionType="playlist",
                    CustomName=display_title(record, marker),
                    Subtitle="", ViewType="cards",
                    DisplayMode="mobile,desktop,tv", ScrollDirection="Horizontal",
                    ImageType="Primary", ParentId=playlist, SortBy=None,
                    SortOrder="Ascending", ItemTypes=[], IncludeNextUpInResume=True,
                    ExcludedFolders=[], Query={})

    def _after(self, before, after, operation, args, marker):
        """Accept only the exact mutation footprint, including new server IDs."""
        expected = copy.deepcopy(before)
        owned = [p for p in expected["playlists"] if p["marker"] == marker]
        target = owned[0] if owned else None
        new = [p for p in after["playlists"] if p["marker"] == marker]
        if operation in ("share", "clear", "add", "delete") and target is None:
            return False
        if operation == "create":
            if target is not None or len(new) != 1:
                return False
            expected["playlists"] = sorted(expected["playlists"] + [dict(
                id=new[0]["id"], marker=marker, owner=self.scope["actor"],
                access=[self.scope["recipient"]] if self.scope["actor"] == self.scope["recipient"] else [],
                entries=[])], key=lambda p: p["marker"])
        elif operation == "row":
            if len(after["home"]) != len(before["home"]) + 1:
                return False
            row = after["home"][-1]
            if not all(row.get(k) == v for k, v in args.items()):
                return False
            if row_id(row["Id"]) in [row_id(r["Id"]) for r in before["home"]]:
                return False
            expected["home"].append(row)
        elif operation == "display-title":
            rows = [r for r in expected["home"] if row_id(r["Id"]) == row_id(args["Id"])]
            if len(rows) != 1 or rows[0].get("ParentId") != args["ParentId"]:
                return False
            # Pending bodies from older versions retain the label. Only an
            # explicitly journaled exact old-label-to-blank change may remove it.
            if rows[0].get("Subtitle") == LEGACY_SUBTITLE and args.get("Subtitle") == "":
                rows[0]["Subtitle"] = ""
            rows[0].update(CustomName=args["CustomName"], Name=args["CustomName"])
        elif operation == "share":
            target["access"] = [self.scope["recipient"]]
        elif operation == "clear":
            target["entries"] = []
        elif operation == "add":
            if len(new) != 1 or [e["movie"] for e in new[0]["entries"]] != args:
                return False
            target["entries"] = new[0]["entries"]
        elif operation == "unrow":
            expected["home"] = [r for r in before["home"] if row_id(r["Id"]) != row_id(args)]
        elif operation == "move":
            # The identical row object moves to its final index; every other row
            # keeps its object and relative order. A move never journals a no-op,
            # so an unchanged Home after an attempted dispatch stays unresolved.
            if type(args) is not dict or set(args) != {"row", "position"}:
                return False
            rows = [n for n, r in enumerate(before["home"]) if row_id(r["Id"]) == row_id(args["row"])]
            position = args["position"]
            if (len(rows) != 1 or type(position) is not int or
                    not 0 <= position < len(before["home"]) or rows[0] == position):
                return False
            expected["home"].insert(position, expected["home"].pop(rows[0]))
        elif operation == "delete":
            expected["playlists"].remove(target)
        return expected == after

    def _intent(self, d, snapshot, request):
        """Version 2 binds operation, list and arguments, never only a snapshot."""
        if d["version"] == 2:
            return digest(dict(version=2, scope=self.base, request=request, snapshot=snapshot))
        if request.get("operation") == "set-display-title":
            return self._title_digest(snapshot, request["title"])
        return digest(snapshot)

    def _title_digest(self, snapshot, title):
        return digest(dict(scope=self.scope, operation="set-display-title",
                           snapshot=snapshot, title=title))

    def _check(self, d, snapshot, request):
        """Preconditions shared by preview and a new request, before any write."""
        name = self._name(request)
        record, marker, playlist = self._target(d, snapshot, request.get("list"))
        if name in ("set-display-title", "set-title") and record is not None:
            title_row(record, playlist, snapshot["home"], self.scope["recipient"], marker)
        if d["version"] == 1:
            return
        title = request.get("title")
        require(title is None or title == marker or not MARKER.fullmatch(title),
                "Marker-shaped title")
        if name == "create-list":
            require(record is None, "List already exists")
            require(len(d["lists"]) < MAX_LISTS, "List limit reached")
            self.api.check_create_absent(request["list"])
            return
        require(record is not None, "Unknown list")
        require(name != "detach-row" or record["row"] is not None, "List already detached")
        require(name != "attach-row" or record["row"] is None, "List already attached")
        if name == "move-row":
            # Gates run in preview and before a run is journaled, never only in the adapter.
            require(record["row"] is not None, "List detached")
            title_row(record, playlist, snapshot["home"], self.scope["recipient"], marker)
            require(request["position"] < len(snapshot["home"]), "Row position out of range")
            # position is a final normal-view index. The backend translates it
            # against a fresh edit view and refuses here, before any run exists.
            self.api.move_index(self.scope["recipient"], copy.deepcopy(snapshot["home"]),
                                record["row"], request["position"])

    def _preview(self, legacy, scoped):
        with self.journal.locked():
            d = self._journal()
            s = self._read(d)
            d = self._fresh(s, d, 1 if legacy is not None else 2)
            request = self._request(d, legacy, scoped)
            require(d["pending"] is None and d["run"] is None, "Unfinished operation")
            require(s == d["current"], "Manual drift")
            self._check(d, s, request)
            self._visible(request.get("movies") or [])
            return self._intent(d, s, request)

    def _legacy(self, operation, **args):
        return list_request(operation, self.scope["recipe"], args)

    def preview(self, movies):
        movies = movie_ids(movies)
        intent = self._preview(dict(movies=movies, remove=False),
                               self._legacy("set-members", movies=movies))
        return dict(scope=copy.deepcopy(self.scope), movies=movies,
                    baseline_digest=intent, marker=self.marker)

    def display_title_preview(self, title):
        title = checked_title(title)
        intent = self._preview(dict(operation="set-display-title", title=title),
                               self._legacy("set-title", title=title))
        return dict(title=title, baseline_digest=intent)

    def list_preview(self, operation, list, **args):
        request = list_request(operation, list, args)
        return dict(request=request, intent_digest=self._preview(None, request))

    def set_display_title(self, key, title, baseline_digest=None):
        title = checked_title(title)
        return self._execute(key, baseline_digest, dict(operation="set-display-title", title=title),
                             self._legacy("set-title", title=title))

    def apply(self, key, movies, baseline_digest=None):
        movies = movie_ids(movies)
        return self._execute(key, baseline_digest, dict(movies=movies, remove=False),
                             self._legacy("set-members", movies=movies))

    def remove(self, key, baseline_digest=None):
        # On version 2 this needs a delete-list digest, never a membership one.
        return self._execute(key, baseline_digest, dict(movies=[], remove=True),
                             self._legacy("delete-list"))

    def create_list(self, key, list, title, movies, intent_digest=None):
        return self._scoped(key, intent_digest, "create-list", list, title=title, movies=movies)

    def set_members(self, key, list, movies, intent_digest=None):
        return self._scoped(key, intent_digest, "set-members", list, movies=movies)

    def set_title(self, key, list, title, intent_digest=None):
        return self._scoped(key, intent_digest, "set-title", list, title=title)

    def detach_row(self, key, list, intent_digest=None):
        return self._scoped(key, intent_digest, "detach-row", list)

    def attach_row(self, key, list, title, intent_digest=None):
        return self._scoped(key, intent_digest, "attach-row", list, title=title)

    def delete_list(self, key, list, intent_digest=None):
        return self._scoped(key, intent_digest, "delete-list", list)

    def move_row(self, key, list, position, intent_digest=None):
        return self._scoped(key, intent_digest, "move-row", list, position=position)

    def _scoped(self, key, intent, operation, list_id, **args):
        return self._execute(key, intent, None, list_request(operation, list_id, args))

    def reconcile(self):
        """Reads only, including no journal updates. Never retries a mutation."""
        with self.journal.locked():
            d = self._journal()
            s = self._read(d)
            d = self._fresh(s, d, 1)
            p = d["pending"]
            if p:
                result = {"state": "observed-applied" if self._after(
                    p["before"], s, p["operation"], p["args"], self.marker_for(p.get("list")))
                    else "unresolved", "operation": p["operation"]}
                if "list" in p:
                    result["list"] = p["list"]
                return result
            return {"state": "clean" if s == d["current"] else "drift"}

    def _row_view(self, d, snapshot, record, playlist):
        # A pending row create/delete may have been observed but not saved.
        saved = next((r for r in d["current"]["home"]
                      if record is not None and record["row"] is not None and
                      row_id(r["Id"]) == row_id(record["row"])), None)
        return dict(saved_id=record["row"] if record is not None else None,
                    saved=copy.deepcopy(saved),
                    observed=[copy.deepcopy(r) for r in snapshot["home"]
                              if playlist is not None and r.get("ParentId") == playlist["id"]])

    def show(self, list=None):
        """Read journal-owned lists only; never adopt, migrate or save."""
        with self.journal.locked():
            d = self._journal()
            s = self._read(d)
            d = self._fresh(s, d, 1)
            p = d["pending"]
            if p:
                applied = self._after(p["before"], s, p["operation"], p["args"],
                                      self.marker_for(p.get("list")))
                require(applied or s == p["before"] == d["current"], "Manual drift")
                state = "observed-applied" if applied else "unresolved"
            else:
                require(s == d["current"], "Manual drift")
                state = "clean"
            require(isinstance(d["done"], dict) and len(d["done"]) <= DONE_LIMIT,
                    "Journal request limit reached")
            status = dict(revision=digest(s), journal_digest=digest(d),
                          pending={k: p[k] for k in ("operation", "list") if k in p} if p else None,
                          run={"key": d["run"]["key"]} if d["run"] else None,
                          done_count=len(d["done"]), done_limit=DONE_LIMIT,
                          keys_exhausted=len(d["done"]) >= DONE_LIMIT,
                          reconcile={"state": state})
            if d["version"] == 1:
                require(list is None, "Journal migration required")
                record, marker, playlist = self._target(d, s, None)
                return dict(status, scope=copy.deepcopy(self.scope), marker=marker,
                            display_title=display_title(d, marker),
                            playlist_name=marker if s["playlists"] else None,
                            playlist_expected_name=marker,
                            playlists=copy.deepcopy(s["playlists"]),
                            row=self._row_view(d, s, record, playlist))
            lists = []
            for list_id in sorted(d["lists"]):
                record, marker, playlist = self._target(d, s, list_id)
                # Observed final index of the saved row, for placement readback and
                # a same-row inverse. Version 1 show output is unchanged.
                position = next((n for n, r in enumerate(s["home"]) if record["row"] is not None
                                 and row_id(r["Id"]) == row_id(record["row"])), None)
                if list is None or list == list_id:
                    lists.append(dict(list=list_id, marker=marker,
                                      legacy=list_id == self.scope["recipe"],
                                      display_title=display_title(record, marker),
                                      playlist=copy.deepcopy(playlist),
                                      row=self._row_view(d, s, record, playlist),
                                      position=position))
            require(list is None or len(lists) == 1, "Unknown list")
            return dict(status, version=2, scope=copy.deepcopy(self.base),
                        legacy_list=self.scope["recipe"], lists=lists,
                        list_count=len(d["lists"]), list_limit=MAX_LISTS,
                        migrated_from=copy.deepcopy(d.get("migrated_from")))

    def migrate_preview(self):
        return self._convert("migrate", None, None)

    def migrate(self, key, intent_digest=None):
        return self._convert("migrate", opaque(key), intent_digest)

    def downgrade_preview(self):
        return self._convert("downgrade", None, None)

    def downgrade(self, key, intent_digest=None):
        return self._convert("downgrade", opaque(key), intent_digest)

    def _convert(self, direction, key, intent):
        """Explicit journal-only conversion in one save; zero Emby writes."""
        with self.journal.locked():
            d = self._journal()
            require(d is not None, "No journal to convert")
            s = self._read(d)
            if (direction == "migrate" and key is not None and d["version"] == 2 and
                    (d.get("migrated_from") or {}).get("key") == key):
                require(s == d["current"], "Manual drift")
                return {"state": "already-migrated", "writes": 0}
            source = 1 if direction == "migrate" else 2
            require(d["version"] == source, "Journal version %d required" % source)
            require(d["pending"] is None and d["run"] is None, "Unfinished operation")
            require(s == d["current"], "Manual drift")
            require(isinstance(d["done"], dict) and len(d["done"]) <= DONE_LIMIT,
                    "Journal request limit reached")
            converted = self._migrated(d, key) if direction == "migrate" else self._downgraded(d)
            expected = digest(dict(operation=direction, scope=self.scope,
                                   journal=digest(d), snapshot=s))
            if key is None:
                lists = (converted if direction == "migrate" else d)["lists"]
                return dict(operation=direction, intent_digest=expected, lists=sorted(lists),
                            journal_digest=digest(d), revision=digest(s))
            require(intent == expected, "Fresh preview digest required")
            self.journal.save(converted)
            return {"state": "migrated" if direction == "migrate" else "downgraded", "writes": 0}

    def init_preview(self):
        return self._init(None, None)

    def init(self, key, intent_digest=None):
        return self._init(opaque(key), intent_digest)

    def _init(self, key, intent):
        """Explicit empty version 2 journal from a fresh baseline; zero Emby writes.

        Only an absent journal initializes, and nothing is adopted: the legacy
        marker playlist or any marker-shaped Home row refuses. The init key is
        kept as a completed request, so replaying it on a clean journal is a
        no-op and any other reuse refuses. Every other existing journal refuses.
        """
        with self.journal.locked():
            d = self._journal()
            s = self._read(d)
            if d is not None:
                require(key is not None and d["version"] == 2 and
                        d["done"].get(key) == INIT_REQUEST, "Journal already exists")
                require(d["pending"] is None and d["run"] is None, "Unfinished operation")
                require(s == d["current"], "Manual drift")
                return {"state": "already-initialized", "writes": 0}
            require(not any(isinstance(r.get(k), str) and MARKER.fullmatch(r[k])
                            for r in s["home"] for k in ("Name", "CustomName")),
                    "Existing managed row requires explicit operator recovery")
            fresh = self._fresh(s, None, 2)
            expected = digest(dict(operation="init", scope=self.base, journal=None, snapshot=s))
            if key is None:
                return dict(operation="init", intent_digest=expected, lists=[],
                            revision=digest(s))
            require(intent == expected, "Fresh preview digest required")
            # Save replaces unconditionally, so recheck absence under the held lock.
            require(self.journal.load() is None, "Journal appeared during initialization")
            fresh["done"][key] = dict(INIT_REQUEST)
            self.journal.save(fresh)
            return {"state": "initialized", "writes": 0}

    def _migrated(self, d, key):
        """Keep the legacy marker, saved IDs, title, snapshot, baseline and done."""
        require(set(d) <= V1_FIELDS, "Unknown journal field; refusing lossy migration")
        owned = bool(d["current"]["playlists"])
        require(owned == (d["row"] is not None) and (owned or "display_title" not in d),
                "Journal is not cleanly migratable")
        lists = {}
        if owned:
            lists[self.scope["recipe"]] = {k: d[k] for k in ("row", "display_title") if k in d}
        return dict(version=2, scope=self.base, baseline=d["baseline"], current=d["current"],
                    lists=lists, pending=None, run=None, done=d["done"],
                    migrated_from=dict(version=1, recipe=self.scope["recipe"],
                                       journal_digest=digest(d), key=key))

    def _downgraded(self, d):
        """Lossless only for the configured attached list or no list at all."""
        lists = d["lists"]
        require(set(lists) <= {self.scope["recipe"]} and
                all(r["row"] is not None for r in lists.values()),
                "Downgrade requires only the attached configured list")
        record = lists.get(self.scope["recipe"], {"row": None})
        require(record["row"] is not None or d["current"] == d["baseline"],
                "Journal is not cleanly downgradable")
        result = dict(version=1, scope=self.scope, baseline=d["baseline"], current=d["current"],
                      row=record["row"], pending=None, run=None, done=d["done"])
        if "display_title" in record:
            result["display_title"] = record["display_title"]
        return result

    def _execute(self, key, intent, legacy, scoped):
        opaque(key)
        with self.journal.locked():
            d = self._journal()
            s = self._read(d)
            d = self._fresh(s, d, 1 if legacy is not None else 2)
            request = self._request(d, legacy, scoped)
            if key in d["done"]:
                require(d["done"][key] == request, "Idempotency key reused")
                require(s == d["current"], "Manual drift")
                return {"state": "replayed", "writes": 0}
            if d["run"] is None:
                require(d["pending"] is None and s == d["current"], "Manual drift")
                require(intent == self._intent(d, s, request), "Fresh preview digest required")
                self._check(d, s, request)
                require(len(d["done"]) < DONE_LIMIT, "Journal request limit reached")
                self._visible(request.get("movies") or [])
                d["run"] = dict(key=key, request=request)
                self.journal.save(d)
            require(d["run"] == dict(key=key, request=request), "Another request is unfinished")
            return self._steps(d, key, request)

    def _complete(self, d, pending, snapshot):
        """Record an exactly observed step. Returns whether it set a row title."""
        operation, list_id = pending["operation"], pending.get("list")
        if d["version"] == 2 and operation == "create":
            d["lists"][list_id] = {"row": None}
        if d["version"] == 2 and operation == "delete":
            del d["lists"][list_id]
        record = list_record(d, list_id)
        if operation == "row":
            record["row"] = snapshot["home"][-1]["Id"]
            if d["version"] == 2:
                record["display_title"] = self.marker_for(list_id)
        if operation == "unrow":
            record["row"] = None
        if operation == "display-title":
            record["display_title"] = pending["args"]["CustomName"]
        return operation == "display-title"

    def _next(self, d, snapshot, request, titled):
        record, marker, p = self._target(d, snapshot, request.get("list"))
        title, movies = request.get("title"), request.get("movies")
        actor, recipient = self.scope["actor"], self.scope["recipient"]
        for phase in PHASES[self._name(request)]:
            step = None
            if phase == "create" and p is None:
                step = ("create", None)
            elif phase == "share" and p["access"] == [] and actor != recipient:
                step = ("share", None)
            elif phase == "row" and record["row"] is None:
                # Known qualified path: marker title first, then a same-row title save.
                template = dict(record, display_title=marker) if d["version"] == 2 else record
                step = ("row", self._row_body(p["id"], template, marker))
            elif phase == "title":
                row = title_row(record, p, snapshot["home"], recipient, marker)
                if not titled and (row["CustomName"] != title or row["Name"] != title or
                                   row.get("Subtitle") == LEGACY_SUBTITLE):
                    row["CustomName"] = title
                    if row.get("Subtitle") == LEGACY_SUBTITLE:
                        row["Subtitle"] = ""
                    step = ("display-title", row)
            elif phase == "members" and [e["movie"] for e in p["entries"]] != movies:
                step = (("clear", [e["entry"] for e in p["entries"]]) if p["entries"]
                        else ("add", movies))
            elif phase == "unrow" and record is not None and record["row"] is not None:
                step = ("unrow", record["row"])
            elif phase == "delete" and p is not None:
                step = ("delete", None)
            elif phase == "move":
                title_row(record, p, snapshot["home"], recipient, marker)
                position = request["position"]
                require(position < len(snapshot["home"]), "Row position out of range")
                if row_index(snapshot["home"], record["row"]) != position:
                    step = ("move", {"row": record["row"], "position": position})
            if step:
                return step + (p, marker)
        return None, None, p, marker

    def _finish(self, d, snapshot, name):
        if d["version"] == 1:
            require(name != "remove" or snapshot == d["baseline"], "Removal did not restore baseline")
            if name == "remove":
                d.pop("display_title", None)
        elif name == "delete-list":
            owned = {row_id(r["row"]) for r in d["lists"].values() if r["row"] is not None}
            require([r for r in snapshot["home"] if row_id(r["Id"]) not in owned] ==
                    d["baseline"]["home"], "Removal did not restore baseline")

    def _steps(self, d, key, request):
        name, movies = self._name(request), request.get("movies")
        # Every request uses at most six steps; this bound is unchanged.
        writes = 0
        titled = False
        for _ in range(9):
            s = self._read(d)
            if d["pending"]:
                p = d["pending"]
                require(self._after(p["before"], s, p["operation"], p["args"],
                                    self.marker_for(p.get("list"))),
                        "Uncertain mutation; exact readback required, no retry")
                # Completing a historical pending title write must not dispatch
                # a second write to clean up its retained old subtitle.
                titled = self._complete(d, p, s) or titled
                d["current"], d["pending"] = s, None
                # Title completion and done must share one journal save. A
                # crash between them could otherwise dispatch a second write
                # when recovering a historical body that retained the label.
                if p["operation"] != "display-title":
                    self.journal.save(d)
            require(s == d["current"], "Manual Home/playlist drift")
            if movies:
                self._visible(movies)
            operation, args, playlist, marker = self._next(d, s, request, titled)
            if operation is None:
                self._finish(d, s, name)
                d["done"][key], d["run"] = request, None
                self.journal.save(d)
                return {"state": RESULT[name], "writes": writes}
            # Recheck the entire Home and exact owned playlists immediately before dispatch.
            require(self._read(d) == s, "Concurrent drift before mutation")
            d["pending"] = dict(operation=operation, args=copy.deepcopy(args), before=s)
            if "list" in request:
                d["pending"]["list"] = request["list"]
            self.journal.save(d)
            try:
                self._dispatch(operation, args, playlist, marker)
            except NotDispatched:
                d["pending"] = None
                self.journal.save(d)
                raise
            writes += 1
        raise Refused("Mutation budget exhausted")

    def _dispatch(self, operation, args, playlist, marker):
        a, u = self.scope["actor"], self.scope["recipient"]
        if operation == "create":
            self.api.create_playlist(a, marker)
        elif operation == "share":
            self.api.share_read(playlist["id"], u)
        elif operation == "row":
            self.api.create_row(u, copy.deepcopy(args))
        elif operation == "display-title":
            self.api.set_row_display_title(u, copy.deepcopy(args))
        elif operation == "clear":
            self.api.delete_entries(playlist["id"], a, list(args))
        elif operation == "add":
            self.api.add_movies(playlist["id"], a, list(args))
        elif operation == "unrow":
            self.api.delete_row(u, args)
        elif operation == "move":
            self.api.move_row(u, copy.deepcopy(args))
        elif operation == "delete":
            self.api.delete_owned_playlist(playlist["id"], marker, a)
        else:
            raise Refused("Unknown internal mutation")
