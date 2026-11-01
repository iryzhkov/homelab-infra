"""Bounded protected operator transport. Managed grant and journal provenance only."""
import copy
import json
import os
from pathlib import PurePosixPath
import re
import secrets
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

from functools import wraps
from emby_operator_recommendations import (MAX_LISTS, NotDispatched, Refused, require, movie_ids,
                                           opaque, row_id, display_title, title_row, list_marker,
                                           native_move_index)

MAX_BYTES = 4 * 1024 * 1024
MAX_BODY = 16 * 1024
MAX_REQUESTS = 512
MAX_SECONDS = 120
TIMEOUT = 5
# Movie metadata projection, matching the HoloNet catalog metadata projection.
CREDIT_ROLES = ('Director', 'Producer', 'Composer')
MAX_RAW_CREDITS = 512
# Director and Composer share one budget whose overflow refuses, as before.
# Producer has its own budget; further valid Producer credits are skipped.
MAX_CREDITS = 16
MAX_PRODUCERS = 16
MAX_GENRES = 16
MAX_NAME_BYTES = 128
MAX_TITLE_BYTES = 512
METADATA_FIELDS = 'People,Genres'


def strict_json(raw):
    def pairs(values):
        result = {}
        for k, v in values:
            require(k not in result, "Duplicate JSON key")
            result[k] = v
        return result
    def constant(_):
        raise Refused("Nonfinite JSON")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise Refused("Invalid JSON") from None


def private_read(path, limit=MAX_BODY):
    """Open each component by directory FD, never following symlinks.

    All ancestors root-owned and not group/world writable; immediate directory
    root-owned 0700; leaf root-owned regular single-link 0600. No path in errors.
    """
    fd = None
    try:
        p = PurePosixPath(path)
        require(p.is_absolute() and '..' not in p.parts and len(p.parts) > 2,
                "Unsafe protected path")
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in p.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=fd)
            os.close(fd)
            fd = nxt
            s = os.fstat(fd)
            require(s.st_uid == 0 and not s.st_mode & 0o022,
                    "Unsafe protected ancestor")
        require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700,
                "Protected directory must be mode 0700")
        leaf = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            s = os.fstat(leaf)
            require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and
                    stat.S_IMODE(s.st_mode) == 0o600 and s.st_nlink == 1 and
                    s.st_size <= limit, "Unsafe protected file")
            with os.fdopen(leaf, 'rb', closefd=False) as stream:
                raw = stream.read(limit + 1)
            require(len(raw) <= limit, "Protected file too large")
            return raw
        finally:
            os.close(leaf)
    except (OSError, ValueError, TypeError):
        raise Refused("Protected file unavailable or unsafe") from None
    finally:
        if fd is not None:
            os.close(fd)


def validate_config(c):
    require(type(c) is dict and set(c) == {
        'enabled', 'origin', 'server', 'actor', 'recipient', 'library',
        'library_guid', 'recipe', 'credential_file', 'journal_directory'},
        "Invalid protected configuration")
    require(type(c['enabled']) is bool, "Invalid enable flag")
    for k in ('server', 'actor', 'recipient', 'library_guid'):
        require(isinstance(c[k], str) and re.fullmatch('[0-9a-f]{32}', c[k]),
                "Invalid configured identity")
    for k in ('library', 'recipe'):
        opaque(c[k])
    # Origin only, no path/query/userinfo. API prefix is fixed below.
    require(isinstance(c['origin'], str), "Invalid pinned origin")
    try:
        u = urllib.parse.urlsplit(c['origin'])
        require(u.scheme == 'https' and u.hostname and u.username is None and
                u.password is None and u.path == '' and not u.query and
                not u.fragment and u.port in (None, 443) and
                c['origin'] == 'https://' + u.netloc and
                re.fullmatch(r'[A-Za-z0-9.-]+(?::443)?', u.netloc),
                "Expected protected HTTPS origin")
    except ValueError:
        raise Refused("Invalid pinned origin") from None
    for k in ('credential_file', 'journal_directory'):
        require(isinstance(c[k], str) and c[k].startswith('/') and
                '..' not in PurePosixPath(c[k]).parts, "Invalid protected path")
    return copy.deepcopy(c)


def load_config(path):
    require(os.geteuid() == 0 and os.getuid() == 0, "Root operator required")
    return validate_config(strict_json(private_read(path)))


def load_credentials(path):
    require(os.geteuid() == 0 and os.getuid() == 0, "Root operator required")
    c = strict_json(private_read(path))
    require(type(c) is dict and set(c) == {'username', 'password'},
            "Only fresh username/password authentication is supported")
    for k in c:
        require(isinstance(c[k], str) and 1 <= len(c[k]) <= 1024 and
                not any(ord(x) < 32 for x in c[k]), "Invalid credential staging")
    return c


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def complete(dto, cap):
    require(type(dto) is dict and type(dto.get('Items')) is list and
            type(dto.get('TotalRecordCount')) is int and
            dto['TotalRecordCount'] == len(dto['Items']) <= cap,
            "Incomplete or unbounded result")
    require(all(type(x) is dict for x in dto['Items']), "Invalid item DTO")
    return dto['Items']


def normalize_home(dto):
    rows = complete(dto, 100) if type(dto) is dict else dto
    require(type(rows) is list and len(rows) <= 100 and
            all(type(r) is dict for r in rows), "Invalid Home response")
    ids = [row_id(r.get('Id')) for r in rows]
    require(len(ids) == len(set(ids)), "Duplicate Home IDs")
    result = [{k: copy.deepcopy(v) for k, v in r.items()
               if k not in ('ParentItem', 'UserData')} for r in rows]
    empty_query = {k: [] for k in ('StudioIds', 'TagIds', 'GenreIds', 'CollectionTypes')}
    for row in result:
        # Exact 4.10.1.0 empty-query expansion; retain every other query value.
        if row.get('Query') == empty_query:
            row['Query'] = {}
    return result


def printable(value, limit):
    """1..limit UTF-8 bytes, no control characters, as the HoloNet projection."""
    if not isinstance(value, str):
        return False
    try:
        size = len(value.encode('utf-8'))
    except UnicodeEncodeError:
        return False
    return 1 <= size <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def catalog_id(value):
    """Canonical positive decimal int64, as HoloNet ValidCatalogID."""
    return (isinstance(value, str) and re.fullmatch('[1-9][0-9]{0,18}', value) is not None
            and int(value) < 2 ** 63)


def movie_metadata_item(row, library):
    """Allowlisted Movie facts; a missing or null field stays unknown.

    Any malformed known field refuses the whole result instead of guessing.
    Only Director, Producer and Composer credits are kept, as {Id, Name, Type}:
    at most 16 Director/Composer (a 17th refuses) plus at most 16 Producer
    (later valid Producer credits are skipped), so at most 32 in total.
    """
    require(row.get('Type') == 'Movie' and printable(row.get('Name'), MAX_TITLE_BYTES),
            'Invalid Movie metadata')
    item = {'Id': opaque(row.get('Id')), 'Type': 'Movie', 'LibraryId': library,
            'Name': row['Name']}
    year = row.get('ProductionYear')
    if year is not None:
        require(type(year) is int and 1 <= year <= 9999, 'Invalid Movie metadata')
        item['ProductionYear'] = year
    genres = row.get('Genres')
    if genres is not None:
        require(type(genres) is list and len(genres) <= MAX_GENRES and
                all(printable(g, MAX_NAME_BYTES) for g in genres), 'Invalid Movie metadata')
        item['Genres'] = list(genres)
    people = row.get('People')
    if people is not None:
        require(type(people) is list and len(people) <= MAX_RAW_CREDITS and
                all(type(p) is dict for p in people), 'Invalid Movie metadata')
        credits, seen, producers, others = [], set(), 0, 0
        for p in people:
            if p.get('Type') not in CREDIT_ROLES:
                continue
            require(catalog_id(p.get('Id')) and printable(p.get('Name'), MAX_NAME_BYTES),
                    'Invalid Movie metadata')
            if (p['Id'], p['Type']) in seen:
                continue
            seen.add((p['Id'], p['Type']))
            if p['Type'] == 'Producer':
                if producers >= MAX_PRODUCERS:
                    continue
                producers += 1
            else:
                require(others < MAX_CREDITS, 'Invalid Movie metadata')
                others += 1
            credits.append({'Id': p['Id'], 'Name': p['Name'], 'Type': p['Type']})
        item['People'] = credits
    return item


def mutation_dispatch(method):
    """Classify failures only while this invocation has attempted no POST."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        attempted = self._mutation_attempts
        try:
            return method(self, *args, **kwargs)
        except Exception:
            if self._mutation_attempts == attempted:
                raise NotDispatched("Mutation not dispatched; resume same request") from None
            raise
    return guarded


class OperatorAdapter:
    """Typed transport bound to the core journal under its existing lock.

    The constructor is trusted operator code, never exposed through WebMCP.
    A token can only originate in this instance's AuthenticateByName response.
    request_guard is an optional trusted callback run before every send. It
    returns the caller's remaining seconds or raises Refused; it never
    changes routes, bodies or retries.
    """
    def __init__(self, config, journal=None, *, request_guard=None):
        self._config = validate_config(config)
        self._journal = journal
        self._guard = request_guard
        self._base = {k: self._config[k] for k in ('server', 'actor', 'recipient', 'library')}
        self._legacy = list_marker(self._base, self._config['recipe'])
        self._routes = set()
        require(self._config['enabled'], "Operator disabled")
        self._token = None
        self._auth = None
        self.session_binding = secrets.token_hex(16)
        self._started = time.monotonic()
        self._requests = 0
        self._mutation_attempts = 0
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect())

    def close(self):
        self._token = None
        self._auth = None

    def _http(self, method, path, query=None, body=None, *, authenticating=False):
        # Internal fixed allowlist, no raw route CLI or caller-controlled host.
        c = self._config
        allowed = {('POST', '/Users/AuthenticateByName'), ('GET', '/Users'),
                   ('GET', '/Library/VirtualFolders'),
                   ('GET', '/Users/' + c['actor']),
                   ('GET', '/Users/' + c['recipient']),
                   ('GET', '/Users/' + c['recipient'] + '/Views'),
                   ('GET', '/Users/' + c['recipient'] + '/Items'),
                   ('GET', '/Users/' + c['recipient'] + '/HomeSections'),
                   ('GET', '/Users/' + c['actor'] + '/Items'),
                   ('GET', '/Users/ItemAccess')} | self._routes
        require((method, path) in allowed, "Route outside operator scope")
        require(authenticating == (path == '/Users/AuthenticateByName'),
                "Invalid authentication route")
        require(authenticating or self._token is not None, "Fresh authentication required")
        now = time.monotonic()
        remaining = MAX_SECONDS - (now - self._started)
        require(self._requests < MAX_REQUESTS and remaining > 0, "Transport budget exhausted")
        if self._guard is not None:
            # Caller deadline and disconnect, before every send including
            # authentication and reads. A stop here precedes the mutation
            # count, so a mutation method still reports NotDispatched.
            remaining = min(remaining, self._guard())
            require(remaining > 0, "Caller deadline exceeded")
        deadline = now + remaining
        raw = json.dumps(body, allow_nan=False).encode() if body is not None else None
        require(raw is None or len(raw) <= MAX_BODY, "Request body too large")
        url = c['origin'] + '/emby' + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        require(len(url) <= 8192, "Request URL too large")
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'X-Emby-Authorization': 'MediaBrowser Client="HoloNetOperator", Device="Operator", DeviceId="holonet-operator", Version="1"'}
        if not authenticating:
            headers['X-Emby-Token'] = self._token
        request = urllib.request.Request(url, data=raw, headers=headers, method=method)
        self._requests += 1  # Count dispatch before attempting, including errors.
        if method == 'POST' and not authenticating:
            self._mutation_attempts += 1  # From here onward a write is uncertain.
        try:
            with self._opener.open(request, timeout=min(TIMEOUT, remaining)) as response:
                require(response.status in ((200,) if method == 'GET' or authenticating else (200, 204)) and response.geturl() == url,
                        "Unexpected HTTP status or redirect")
                # Chunked reads check a wall deadline as well as socket timeouts.
                # Without a guard the deadline is exactly started + MAX_SECONDS.
                parts, size = [], 0
                while True:
                    require(time.monotonic() < deadline, "Transport deadline exceeded")
                    part = response.read1(min(65536, MAX_BYTES + 1 - size))
                    if not part:
                        break
                    parts.append(part)
                    size += len(part)
                    require(size <= MAX_BYTES, "Response too large")
                raw_response = b''.join(parts)
                content_length = response.headers.get('Content-Length')
                if content_length is not None:
                    require(content_length.isdecimal() and int(content_length) == size,
                            "Partial response")
                return strict_json(raw_response) if raw_response else None
        except Refused:
            self.close()
            raise
        except Exception:
            # Never echo URL, request, token, password, response or exception text.
            self.close()
            raise Refused("Operator HTTP failed; no retry") from None

    def authenticate(self, credentials):
        require(self._auth is None and self._requests == 0, "Fresh adapter required")
        require(type(credentials) is dict and set(credentials) == {'username', 'password'},
                "Only fresh username/password authentication is supported")
        for value in credentials.values():
            require(isinstance(value, str) and 1 <= len(value) <= 1024 and
                    not any(ord(x) < 32 for x in value), "Invalid staged credential")
        d = self._http('POST', '/Users/AuthenticateByName', body={
            'Username': credentials['username'], 'Pw': credentials['password']},
            authenticating=True)
        c = self._config
        require(type(d) is dict and d.get('ServerId') == c['server'] and
                type(d.get('User')) is dict and d['User'].get('Id') == c['actor'] and
                type(d['User'].get('Policy')) is dict and
                d['User']['Policy'].get('IsAdministrator') is True,
                "Authentication did not verify configured admin")
        token = d.get('AccessToken')
        require(isinstance(token, str) and 1 <= len(token) <= 4096 and
                re.fullmatch(r'[A-Za-z0-9_-]+', token), "Invalid authentication token")
        self._token = token
        self._auth = (c['server'], c['actor'], self.session_binding)
        self.verified_identity()

    def verified_identity(self):
        c = self._config
        require(self._auth == (c['server'], c['actor'], self.session_binding),
                "Missing session-bound authentication evidence")
        users = self._http('GET', '/Users')
        require(type(users) is list and len(users) <= 100 and
                all(type(u) is dict for u in users), "Invalid admin capability response")
        ids = [u.get('Id') for u in users]
        require(all(isinstance(x, str) and re.fullmatch('[0-9a-f]{32}', x) for x in ids)
                and len(ids) == len(set(ids)) and c['actor'] in ids and
                c['recipient'] in ids, "Configured principals not verified")
        for who in ('actor', 'recipient'):
            u = self._http('GET', '/Users/' + c[who])
            require(type(u) is dict and u.get('Id') == c[who] and
                    u.get('ServerId') == c['server'] and type(u.get('Policy')) is dict and
                    u['Policy'].get('IsDisabled') is False, "Principal identity/policy drift")
            if who == 'actor':
                require(u['Policy'].get('IsAdministrator') is True, "Admin policy drift")
        folders = self._http('GET', '/Library/VirtualFolders')
        require(type(folders) is list and len(folders) <= 100 and
                all(type(f) is dict for f in folders), "Invalid library response")
        matches = [f for f in folders if f.get('ItemId') == c['library']]
        require(len(matches) == 1 and matches[0].get('Guid') == c['library_guid'] and
                matches[0].get('CollectionType') == 'movies', "Library identity drift")
        views = complete(self._http('GET', '/Users/' + c['recipient'] + '/Views'), 100)
        require(sum(v.get('Id') == c['library'] for v in views) == 1,
                "Recipient library view unavailable")
        return {k: c[k] for k in ('server', 'actor', 'recipient', 'library')} | {
            'verified': True, 'is_admin': True,
            'auth_mode': 'authenticated-admin-session', 'session_binding': self.session_binding}

    def query_movies(self, recipient, library, ids):
        c = self._config
        require(recipient == c['recipient'] and library == c['library'], "Foreign query scope")
        ids = movie_ids(ids)
        self.verified_identity()
        result = complete(self._http('GET', '/Users/' + recipient + '/Items', {
            'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'ParentId': library,
            'Ids': ','.join(ids), 'StartIndex': 0, 'Limit': 26, 'EnableUserData': 'false'}), 25)
        got = [x.get('Id') for x in result]
        require(all(type(x) is str for x in got) and len(got) == len(set(got)) and
                set(got) == set(ids) and all(x.get('Type') == 'Movie' for x in result),
                "Recipient Movie set incomplete or unauthorized")
        return [{'Id': x, 'Type': 'Movie', 'LibraryId': library} for x in ids]

    def movie_metadata(self, recipient, library, ids):
        """Read-only metadata of the requested Movies this recipient can see.

        Unlike query_movies, which proves every ID for a write, this returns
        only the visible subset in request order and silently omits the rest.
        The same fixed recipient/library Movie query proves type, library and
        visibility. No user data, paths or other users' facts are requested.
        """
        c = self._config
        require(recipient == c['recipient'] and library == c['library'], 'Foreign query scope')
        ids = movie_ids(ids)
        self.verified_identity()
        rows = complete(self._http('GET', '/Users/' + recipient + '/Items', {
            'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'ParentId': library,
            'Ids': ','.join(ids), 'Fields': METADATA_FIELDS, 'StartIndex': 0, 'Limit': 26,
            'EnableUserData': 'false'}), 25)
        found = {}
        for row in rows:
            item = movie_metadata_item(row, library)
            require(item['Id'] in ids and item['Id'] not in found,
                    'Unrequested or duplicate Movie')
            found[item['Id']] = item
        return [found[x] for x in ids if x in found]

    def _marker(self, list_id):
        return self._legacy if list_id is None else list_marker(self._base, list_id)

    def _records(self, d):
        """Owned list records by marker, from protected journal custody only."""
        if d is None:
            return {}
        if d['version'] == 1:
            return {self._legacy: d}
        return {self._marker(k): r for k, r in d['lists'].items()}

    def _state(self, marker=None):
        require(self._journal is not None and self._journal._locked,
                'Held core journal lock required')
        c = self._config
        require(marker is None or marker == self._legacy, 'Foreign marker')
        d = self._journal.load()
        if d is not None:
            require(type(d) is dict and (
                (d.get('version') == 1 and d.get('scope') == dict(self._base, recipe=c['recipe'])) or
                (d.get('version') == 2 and d.get('scope') == self._base and
                 type(d.get('lists')) is dict and len(d['lists']) <= MAX_LISTS)),
                'Foreign journal provenance')
        for m, record in self._records(d).items():
            display_title(record, m)
        return d, self._legacy

    def check_create_absent(self, list_id):
        d, _ = self._state()
        require(d is None or (d['version'] == 2 and list_id not in d['lists']),
                'List already exists')
        marker = self._marker(opaque(list_id))
        self.verified_identity()
        rows = complete(self._http('GET', '/Users/' + self._config['actor'] + '/Items', {
            'Recursive': 'true', 'IncludeItemTypes': 'Playlist', 'EnableUserData': 'false',
            'StartIndex': 0, 'Limit': 129}), 128)
        require(all(r.get('Type') == 'Playlist' and isinstance(r.get('Name'), str)
                    for r in rows), 'Invalid playlist discovery DTO')
        require(not any(r['Name'] == marker for r in rows),
                'Existing playlist requires explicit operator recovery')

    def snapshot(self, recipient, marker):
        c = self._config
        require(recipient == c['recipient'], 'Foreign recipient')
        d, legacy = self._state(marker)
        self.verified_identity()
        home = normalize_home(self._http('GET', '/Users/' + recipient + '/HomeSections'))
        rows = complete(self._http('GET', '/Users/' + c['actor'] + '/Items', {
            'Recursive': 'true', 'IncludeItemTypes': 'Playlist', 'EnableUserData': 'false',
            'StartIndex': 0, 'Limit': 129}), 128)
        seen = []
        for r in rows:
            seen.append(opaque(r.get('Id')))
            require(r.get('Type') == 'Playlist' and isinstance(r.get('Name'), str),
                    'Invalid playlist discovery DTO')
        require(len(seen) == len(set(seen)), 'Duplicate discovered ID')
        pending = d.get('pending') if d else None
        records = self._records(d)
        known = d['current']['playlists'] if d else []
        require(len(known) <= (MAX_LISTS if d and d['version'] == 2 else 1), 'Ambiguous saved ID')
        saved = {}
        for p in known:
            require(p['marker'] in records and p['marker'] not in saved and
                    p['owner'] == c['actor'], 'Foreign saved provenance')
            saved[p['marker']] = p
        # Journal-owned markers plus the legacy marker, which still refuses when
        # unexplained. Marker playlists of unknown list IDs are never adopted.
        candidates = {legacy} | set(saved)
        if pending and pending['operation'] == 'create':
            candidates.add(self._marker(pending.get('list')))
        playlists = []
        for m in sorted(candidates):
            pid = self._discover(rows, m, saved.get(m), d, records)
            if pid is not None:
                playlists.append(self._playlist(pid, m))
        return {'home': home, 'playlists': playlists}

    def _discover(self, rows, marker, saved, d, records):
        pending = d.get('pending') if d else None
        pending_marker = self._marker(pending.get('list')) if pending else None
        matches = [r for r in rows if r['Name'] == marker]
        require(len(matches) <= 1, 'Ambiguous marker discovery')
        if saved is not None:
            exact = [r for r in rows if r['Id'] == saved['id']]
            if not exact:
                require(pending and pending['operation'] == 'delete' and
                        pending_marker == marker and not matches, 'Saved playlist disappeared')
                return None
            # Immutable playlist item Name, independent of any Home title.
            require(exact[0]['Name'] == marker and
                    all(r['Id'] == saved['id'] for r in matches),
                    'Saved playlist renamed or substituted')
            # Once created, protected provenance and the exact ID own discovery.
            # A display title never establishes ownership.
            return opaque(exact[0]['Id'])
        if not matches:
            return None
        require(display_title(records.get(marker), marker) == marker,
                'Pending create display title changed')
        require(pending and pending['operation'] == 'create' and pending_marker == marker and
                all(p['marker'] != marker for p in pending['before']['playlists']) and
                d.get('run'), 'Unexplained existing marker playlist')
        return opaque(matches[0]['Id'])

    def _playlist(self, pid, marker):
        c = self._config
        recipient = c['recipient']
        path = '/Users/' + c['actor'] + '/Items/' + pid
        members_path = '/Playlists/' + pid + '/Items'
        self._routes.update({('GET', path), ('GET', members_path)})
        item = self._http('GET', path, {'Fields': 'ShareLevel', 'EnableUserData': 'false'})
        require(type(item) is dict and item.get('Id') == pid and
                item.get('Type') == 'Playlist' and item.get('Name') == marker and
                item.get('CanEditItems') is True and
                (item.get('CanDelete') is True if c['actor'] == recipient else
                 item.get('CanManageAccess') is True),
                'Playlist identity or edit capability drift')
        # Self access is proven by explicit edit/delete capabilities plus the
        # protected creation provenance above. Access candidates may omit self.
        managed_access = [recipient]
        if c['actor'] != recipient:
            # Only this managed recipient grant, not a complete effective ACL.
            access = complete(self._http('GET', '/Users/ItemAccess', {'ItemId': pid}), 100)
            ids = [r.get('Id') for r in access]
            require(all(isinstance(i, str) and re.fullmatch('[0-9a-f]{32}', i) for i in ids)
                    and len(ids) == len(set(ids)) and
                    all(r.get('ServerId') == c['server'] for r in access), 'Invalid access candidates')
            grants = [r for r in access if r['Id'] == recipient]
            require(len(grants) == 1 and grants[0].get('UserItemShareLevel') in ('None', 'Read'),
                    'Missing or unexpected managed recipient grant')
            managed_access = [recipient] if grants[0]['UserItemShareLevel'] == 'Read' else []
        entries = complete(self._http('GET', members_path, {
            'UserId': c['actor'], 'StartIndex': 0, 'Limit': 26,
            'EnableUserData': 'false'}), 25)
        normalized, occurrence_ids = [], []
        for e in entries:
            require(e.get('Type') == 'Movie', 'Non-Movie playlist occurrence')
            occurrence = opaque(e.get('PlaylistItemId'))
            occurrence_ids.append(occurrence)
            normalized.append({'movie': opaque(e.get('Id')), 'entry': occurrence})
        require(len(occurrence_ids) == len(set(occurrence_ids)), 'Duplicate occurrence ID')
        return {'id': pid, 'marker': marker, 'owner': c['actor'], 'access': managed_access,
                'entries': normalized}

    def _intent(self, operation, args):
        """Durable intent for exactly one list: (journal, snapshot, marker, playlist, record)."""
        d, legacy = self._state()
        require(d is not None and d.get('run') and d.get('pending'), 'No durable operation intent')
        p = d['pending']
        require(p['operation'] == operation and p['args'] == args, 'Foreign mutation intent')
        current = self.snapshot(self._config['recipient'], legacy)
        require(current == p['before'] == d['current'], 'Drift before typed dispatch')
        marker = self._marker(p.get('list'))
        target = [x for x in current['playlists'] if x['marker'] == marker]
        record = d if d['version'] == 1 else d['lists'].get(p.get('list'))
        return d, current, marker, (target[0] if target else None), record

    def _post(self, path, *, query=None, body=None):
        self._routes.add(('POST', path))
        self._http('POST', path, query, body)

    def _owned(self, operation, args, playlist):
        intent = self._intent(operation, args)
        require(intent[3] is not None and intent[3]['id'] == opaque(playlist),
                'Foreign playlist mutation')
        return intent

    @mutation_dispatch
    def create_playlist(self, actor, marker):
        require(actor == self._config['actor'], 'Foreign creator')
        d, current, expected, target, record = self._intent('create', None)
        require(marker == expected and target is None and
                display_title(record, expected) == expected and
                (d['version'] == 1 or (record is None and len(d['lists']) < MAX_LISTS)),
                'Existing or foreign playlist/title')
        self._post('/Playlists', query={'Name': marker, 'userId': actor})

    @mutation_dispatch
    def share_read(self, playlist, recipient):
        require(recipient == self._config['recipient'] and recipient != self._config['actor'],
                'Foreign or unnecessary self-share recipient')
        target = self._owned('share', None, playlist)[3]
        require(target['access'] == [], 'Managed grant changed')
        self._post('/Items/Access', body={'UserIds': [recipient], 'ItemIds': [playlist],
                                         'ItemAccess': 'Read'})

    @mutation_dispatch
    def create_row(self, recipient, body):
        require(recipient == self._config['recipient'] and type(body) is dict and 'Id' not in body,
                'Foreign row request')
        _, current, marker, _, record = self._owned('row', body, body.get('ParentId'))
        # Core owns the exact template; intent and complete baseline bind every field.
        # Rows are created only with the immutable marker title (qualified path);
        # same-title manual or sibling rows neither block nor prove ownership.
        require(record is not None and record['row'] is None and
                body.get('SectionType') == 'playlist' and
                body.get('CustomName') == marker and
                not any(r.get('CustomName') == marker for r in current['home']),
                'Foreign row marker')
        self._post('/Users/' + recipient + '/HomeSections', body=body)

    @mutation_dispatch
    def set_row_display_title(self, recipient, body):
        require(recipient == self._config['recipient'] and type(body) is dict,
                'Foreign display title recipient')
        _, current, marker, target, record = self._intent('display-title', body)
        expected = title_row(record, target, current['home'], recipient, marker)
        expected['CustomName'] = display_title({'display_title': body.get('CustomName')}, marker)
        if expected.get('Subtitle') == 'operator-provisioned' and body.get('Subtitle') == '':
            expected['Subtitle'] = ''
        require(body == expected, 'Display title must preserve exact saved row')
        self._post('/Users/' + recipient + '/HomeSections', body=body)

    @mutation_dispatch
    def delete_entries(self, playlist, actor, entries):
        require(actor == self._config['actor'] and type(entries) is list, 'Foreign occurrence actor')
        target = self._owned('clear', entries, playlist)[3]
        require(entries and entries == [e['entry'] for e in target['entries']],
                'Stale occurrences')
        self._post('/Playlists/' + playlist + '/Items/Delete', body={'EntryIds': ','.join(entries)})

    @mutation_dispatch
    def add_movies(self, playlist, actor, movies):
        require(actor == self._config['actor'], 'Foreign membership actor')
        movies = movie_ids(movies)
        target = self._owned('add', movies, playlist)[3]
        require(not target['entries'], 'Membership no longer empty')
        self.query_movies(self._config['recipient'], self._config['library'], movies)
        self._post('/Playlists/' + playlist + '/Items', body={
            'Ids': ','.join(movies), 'userId': actor, 'SkipDuplicates': False})

    @mutation_dispatch
    def delete_row(self, recipient, row):
        require(recipient == self._config['recipient'], 'Foreign row recipient')
        _, current, marker, target, record = self._intent('unrow', row)
        matches = [r for r in current['home'] if row_id(r['Id']) == row_id(row)]
        require(record is not None and record['row'] == row and len(matches) == 1 and
                target is not None and
                matches[0].get('CustomName') == display_title(record, marker) and
                matches[0].get('SectionType') == 'playlist' and
                matches[0].get('ParentId') == target['id'], 'Foreign saved row')
        self._post('/Users/' + recipient + '/HomeSections/Delete', body={'ids': [row]})

    @mutation_dispatch
    def move_row(self, recipient, args):
        require(recipient == self._config['recipient'], 'Foreign row recipient')
        _, current, marker, target, record = self._intent('move', args)
        # Only the journal-owned saved row of the pending list; never a caller row ID.
        position = args.get('position') if type(args) is dict else None
        require(record is not None and record['row'] is not None and target is not None and
                type(args) is dict and set(args) == {'row', 'position'} and
                row_id(args['row']) == row_id(record['row']) and
                type(position) is int and 0 <= position < len(current['home']),
                'Foreign row move')
        title_row(record, target, current['home'], recipient, marker)
        index = [row_id(r['Id']) for r in current['home']].index(row_id(record['row']))
        require(index != position, 'Row already at position')
        # Recompute the native index from fresh views; args never carry one.
        native = self._native_index(recipient, current['home'], record['row'], position)
        require(native is not None, 'Row already at position')
        # Saved row ID keeps its JSON type, as for delete_row.
        self._post('/Users/' + recipient + '/HomeSections/Move',
                   body={'ids': [record['row']], 'newIndex': native})

    def _native_index(self, recipient, home, row, position):
        """Fresh bounded edit view, then the shared normal-to-native translation."""
        edit = normalize_home(self._http('GET', '/Users/' + recipient + '/HomeSections',
                                         {'isEditMode': 'true'}))
        return native_move_index(home, edit, row, position)

    def move_index(self, recipient, home, row, position):
        """Read-only translation preflight; core runs it before preview or a run."""
        require(recipient == self._config['recipient'], 'Foreign row recipient')
        self._state()
        return self._native_index(recipient, home, row, position)

    @mutation_dispatch
    def delete_owned_playlist(self, playlist, marker, actor):
        require(actor == self._config['actor'], 'Foreign delete actor')
        _, current, expected, _, record = self._owned('delete', None, playlist)
        require(marker == expected and record is not None and record['row'] is None and
                not any(r.get('ParentId') == playlist or r.get('CustomName') == marker
                        for r in current['home']), 'Playlist still referenced')
        self._post('/Items/Delete', query={'Ids': playlist})
