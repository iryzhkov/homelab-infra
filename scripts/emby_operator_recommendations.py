"""One operator-owned Movie playlist and native Home row. No transport or secrets.

The adapter is a security boundary, not a convenience HTTP wrapper. See the
companion contract before implementing it. Public operations lock the journal.
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


def movie_ids(values):
    require(isinstance(values, (list, tuple)) and 1 <= len(values) <= 25,
            "Expected 1..25 Movies")
    result = [opaque(v) for v in values]
    require(len(set(result)) == len(result), "Duplicate Movie IDs")
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def display_title(journal, marker):
    """Optional v1 field. Reading legacy journals never migrates their bytes."""
    title = journal.get("display_title", marker) if journal is not None else marker
    require(isinstance(title, str) and 1 <= len(title) <= 256 and
            all(ord(c) >= 32 and ord(c) != 127 for c in title),
            "Invalid journal display title")
    return title


class ProtectedAPI(Protocol):
    """All methods use one pinned authenticated admin session; never a global key.

    Reads are complete, bounded, detached snapshots. They fail on truncation.
    Mutation methods dispatch exactly once with redirects and retries disabled.
    Identity evidence and complete read semantics are specified in the contract.
    """
    session_binding: str

    def verified_identity(self) -> dict: ...
    def snapshot(self, recipient: str, marker: str) -> dict: ...
    def query_movies(self, recipient: str, library: str, ids: list) -> list: ...
    def create_playlist(self, actor: str, marker: str): ...
    def share_read(self, playlist: str, recipient: str): ...
    def create_row(self, recipient: str, body: dict): ...
    def delete_entries(self, playlist: str, actor: str, entries: list): ...
    def add_movies(self, playlist: str, actor: str, movies: list): ...
    def delete_row(self, recipient: str, row): ...
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
                return json.load(stream, object_pairs_hook=pairs,
                                 parse_constant=lambda _: require(False, "Nonfinite journal"))
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
        self.marker = "holonet-recommendations-" + digest(self.scope)[:32]

    def _identity(self):
        identity = self.api.verified_identity()
        require(isinstance(identity, dict) and identity.get("verified") is True and
                identity.get("is_admin") is True, "Unverified/nonadmin identity")
        expected = dict(self.scope)
        expected.pop("recipe")
        require(identity == dict(expected, verified=True, is_admin=True,
                                 auth_mode="authenticated-admin-session",
                                 session_binding=self.api.session_binding),
                "Verified admin session/actor/server/recipient/library required")
        require(isinstance(self.api.session_binding, str) and bool(self.api.session_binding),
                "Missing session binding")

    def _read(self):
        self._identity()
        s = copy.deepcopy(self.api.snapshot(self.scope["recipient"], self.marker))
        require(isinstance(s, dict) and set(s) == {"home", "playlists"}, "Invalid snapshot")
        require(isinstance(s["home"], list) and len(s["home"]) <= 100 and
                all(isinstance(r, dict) for r in s["home"]), "Unbounded/invalid Home")
        rows = [row_id(r.get("Id")) for r in s["home"]]
        require(len(rows) == len(set(rows)), "Duplicate Home row IDs")
        require(isinstance(s["playlists"], list) and len(s["playlists"]) <= 1,
                "Ambiguous owned marker")
        for p in s["playlists"]:
            require(set(p) == {"id", "marker", "owner", "access", "entries"}, "Invalid playlist")
            opaque(p["id"])
            require(p["marker"] == self.marker and p["owner"] == self.scope["actor"],
                    "Foreign playlist")
            require(p["access"] in ([], [self.scope["recipient"]]), "Unexpected playlist access")
            require(isinstance(p["entries"], list) and len(p["entries"]) <= 25, "Too many occurrences")
            entries = []
            for entry in p["entries"]:
                require(isinstance(entry, dict) and set(entry) == {"movie", "entry"}, "Invalid occurrence")
                opaque(entry["movie"])
                entries.append(opaque(entry["entry"]))
            require(len(set(entries)) == len(entries), "Duplicate occurrence IDs")
        return s

    def _visible(self, movies):
        if not movies:
            return
        result = self.api.query_movies(self.scope["recipient"], self.scope["library"], movies)
        require(result == [{"Id": m, "Type": "Movie", "LibraryId": self.scope["library"]}
                           for m in movies], "Recipient Movie visibility/order mismatch")

    def _load(self, snapshot):
        d = self.journal.load()
        if d is None:
            require(not snapshot["playlists"], "Existing playlist requires explicit operator recovery")
            require(not any(r.get("CustomName") == self.marker for r in snapshot["home"]),
                    "Existing managed row requires explicit operator recovery")
            return dict(version=1, scope=self.scope, baseline=snapshot, current=snapshot,
                        row=None, pending=None, run=None, done={})
        require(d.get("version") == 1 and d.get("scope") == self.scope, "Foreign journal")
        display_title(d, self.marker)
        return d

    def _row_body(self, playlist):
        return dict(SectionType="playlist",
                    CustomName=display_title(self.journal.load(), self.marker),
                    Subtitle="operator-provisioned", ViewType="cards",
                    DisplayMode="mobile,desktop,tv", ScrollDirection="Horizontal",
                    ImageType="Primary", ParentId=playlist, SortBy=None,
                    SortOrder="Ascending", ItemTypes=[], IncludeNextUpInResume=True,
                    ExcludedFolders=[], Query={})

    def _after(self, before, after, operation, args):
        """Accept only the exact mutation footprint, including new server IDs."""
        expected = copy.deepcopy(before)
        if operation == "create":
            if len(after["playlists"]) != 1:
                return False
            p = after["playlists"][0]
            expected["playlists"] = [dict(id=p["id"], marker=self.marker,
                                         owner=self.scope["actor"],
                                         access=[self.scope["recipient"]] if self.scope["actor"] == self.scope["recipient"] else [],
                                         entries=[])]
        elif operation == "row":
            if len(after["home"]) != len(before["home"]) + 1:
                return False
            row = after["home"][-1]
            if not all(row.get(k) == v for k, v in args.items()):
                return False
            if row_id(row["Id"]) in [row_id(r["Id"]) for r in before["home"]]:
                return False
            expected["home"].append(row)
        elif operation == "share":
            expected["playlists"][0]["access"] = [self.scope["recipient"]]
        elif operation == "clear":
            expected["playlists"][0]["entries"] = []
        elif operation == "add":
            if len(after["playlists"]) != 1:
                return False
            entries = after["playlists"][0]["entries"]
            if [e["movie"] for e in entries] != args:
                return False
            expected["playlists"][0]["entries"] = entries
        elif operation == "unrow":
            expected["home"] = [r for r in before["home"] if row_id(r["Id"]) != row_id(args)]
        elif operation == "delete":
            expected["playlists"] = []
        return expected == after

    def preview(self, movies):
        movies = movie_ids(movies)
        with self.journal.locked():
            s = self._read()
            self._visible(movies)
            d = self._load(s)
            require(d["pending"] is None and d["run"] is None, "Unfinished operation")
            require(s == d["current"], "Manual drift")
            return dict(scope=copy.deepcopy(self.scope), movies=movies,
                        baseline_digest=digest(s), marker=self.marker)

    def reconcile(self):
        """Reads only, including no journal updates. Never retries a mutation."""
        with self.journal.locked():
            s = self._read()
            d = self._load(s)
            p = d["pending"]
            if p:
                return {"state": "observed-applied" if self._after(
                    p["before"], s, p["operation"], p["args"]) else "unresolved",
                        "operation": p["operation"]}
            return {"state": "clean" if s == d["current"] else "drift"}

    def show(self):
        """Read the configured journal-owned set only; never adopt or save."""
        with self.journal.locked():
            s = self._read()
            d = self._load(s)
            p = d["pending"]
            if p:
                applied = self._after(p["before"], s, p["operation"], p["args"])
                require(applied or s == p["before"] == d["current"], "Manual drift")
                state = "observed-applied" if applied else "unresolved"
            else:
                require(s == d["current"], "Manual drift")
                state = "clean"
            require(isinstance(d["done"], dict) and len(d["done"]) <= 64,
                    "Journal request limit reached")
            # A pending row create/delete may have been observed but not saved.
            saved_row = next((r for r in d["current"]["home"]
                              if d["row"] is not None and
                              row_id(r["Id"]) == row_id(d["row"])), None)
            return dict(scope=copy.deepcopy(self.scope), marker=self.marker,
                        display_title=display_title(d, self.marker),
                        playlists=copy.deepcopy(s["playlists"]),
                        row=dict(saved_id=d["row"], saved=copy.deepcopy(saved_row),
                                 observed=[copy.deepcopy(r) for r in s["home"]
                                           if s["playlists"] and
                                           r.get("ParentId") == s["playlists"][0]["id"]]),
                        revision=digest(s), journal_digest=digest(d),
                        pending={"operation": p["operation"]} if p else None,
                        run={"key": d["run"]["key"]} if d["run"] else None,
                        done_count=len(d["done"]), reconcile={"state": state})

    def apply(self, key, movies, baseline_digest=None):
        return self._execute(key, movie_ids(movies), baseline_digest)

    def remove(self, key, baseline_digest=None):
        return self._execute(key, [], baseline_digest)

    def _execute(self, key, movies, baseline_digest):
        opaque(key)
        request = dict(movies=movies, remove=not movies)
        with self.journal.locked():
            s = self._read()
            d = self._load(s)
            if key in d["done"]:
                require(d["done"][key] == request, "Idempotency key reused")
                require(s == d["current"], "Manual drift")
                return {"state": "replayed", "writes": 0}
            if d["run"] is None:
                require(d["pending"] is None and s == d["current"], "Manual drift")
                require(baseline_digest == digest(s), "Fresh preview digest required")
                require(len(d["done"]) < 64, "Journal request limit reached")
                self._visible(movies)
                d["run"] = dict(key=key, request=request)
                self.journal.save(d)
            require(d["run"] == dict(key=key, request=request), "Another request is unfinished")
            # At most seven mutations: create, share, row, clear, add, unrow, delete.
            writes = 0
            for _ in range(9):
                s = self._read()
                if d["pending"]:
                    p = d["pending"]
                    require(self._after(p["before"], s, p["operation"], p["args"]),
                            "Uncertain mutation; exact readback required, no retry")
                    if p["operation"] == "row":
                        d["row"] = s["home"][-1]["Id"]
                    if p["operation"] == "unrow":
                        d["row"] = None
                    d["current"], d["pending"] = s, None
                    self.journal.save(d)
                require(s == d["current"], "Manual Home/playlist drift")
                playlist = s["playlists"][0] if s["playlists"] else None
                operation, args = None, None
                if movies:
                    self._visible(movies)
                    if playlist is None:
                        operation = "create"
                    elif playlist["access"] == [] and self.scope["actor"] != self.scope["recipient"]:
                        operation = "share"
                    elif d["row"] is None:
                        operation, args = "row", self._row_body(playlist["id"])
                    elif [e["movie"] for e in playlist["entries"]] != movies:
                        operation, args = ("clear", [e["entry"] for e in playlist["entries"]]) if playlist["entries"] else ("add", movies)
                elif d["row"] is not None:
                    operation, args = "unrow", d["row"]
                elif playlist is not None:
                    operation = "delete"
                if operation is None:
                    require(bool(movies) or s == d["baseline"], "Removal did not restore baseline")
                    d["done"][key], d["run"] = request, None
                    self.journal.save(d)
                    return {"state": "applied" if movies else "removed", "writes": writes}
                # Recheck the entire Home and exact owned playlist immediately before dispatch.
                require(self._read() == s, "Concurrent drift before mutation")
                d["pending"] = dict(operation=operation, args=copy.deepcopy(args), before=s)
                self.journal.save(d)
                try:
                    self._dispatch(operation, args, playlist)
                except NotDispatched:
                    d["pending"] = None
                    self.journal.save(d)
                    raise
                writes += 1
            raise Refused("Mutation budget exhausted")

    def _dispatch(self, operation, args, playlist):
        a, u = self.scope["actor"], self.scope["recipient"]
        if operation == "create":
            self.api.create_playlist(a, self.marker)
        elif operation == "share":
            self.api.share_read(playlist["id"], u)
        elif operation == "row":
            self.api.create_row(u, copy.deepcopy(args))
        elif operation == "clear":
            self.api.delete_entries(playlist["id"], a, list(args))
        elif operation == "add":
            self.api.add_movies(playlist["id"], a, list(args))
        elif operation == "unrow":
            self.api.delete_row(u, args)
        elif operation == "delete":
            self.api.delete_owned_playlist(playlist["id"], self.marker, a)
        else:
            raise Refused("Unknown internal mutation")

