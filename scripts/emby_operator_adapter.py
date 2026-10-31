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
from emby_operator_recommendations import NotDispatched, Refused, require, movie_ids, opaque, row_id, digest, display_title, title_row

MAX_BYTES = 4 * 1024 * 1024
MAX_BODY = 16 * 1024
MAX_REQUESTS = 512
MAX_SECONDS = 120
TIMEOUT = 5


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
    """
    def __init__(self, config, journal=None):
        self._config = validate_config(config)
        self._journal = journal
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
        remaining = MAX_SECONDS - (time.monotonic() - self._started)
        require(self._requests < MAX_REQUESTS and remaining > 0, "Transport budget exhausted")
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
                parts, size = [], 0
                while True:
                    require(time.monotonic() - self._started < MAX_SECONDS,
                            "Transport deadline exceeded")
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

    def _state(self, marker=None):
        require(self._journal is not None and self._journal._locked,
                'Held core journal lock required')
        c = self._config
        scope = {k: c[k] for k in ('server', 'actor', 'recipient', 'library', 'recipe')}
        expected_marker = 'holonet-recommendations-' + digest(scope)[:32]
        require(marker is None or marker == expected_marker, 'Foreign marker')
        d = self._journal.load()
        if d is not None:
            require(type(d) is dict and d.get('version') == 1 and d.get('scope') == scope,
                    'Foreign journal provenance')
        display_title(d, expected_marker)
        return d, expected_marker

    def snapshot(self, recipient, marker):
        c = self._config
        require(recipient == c['recipient'], 'Foreign recipient')
        d, marker = self._state(marker)
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
        matches = [r for r in rows if r['Name'] == marker]
        require(len(matches) <= 1, 'Ambiguous marker discovery')
        title = marker  # Immutable playlist item Name, independent of the Home title.
        pending = d.get('pending') if d else None
        known = d['current']['playlists'] if d else []
        require(len(known) <= 1, 'Ambiguous saved ID')
        if known:
            saved = known[0]
            require(saved['marker'] == marker and saved['owner'] == c['actor'],
                    'Foreign saved provenance')
            exact = [r for r in rows if r['Id'] == saved['id']]
            if not exact:
                require(pending and pending['operation'] == 'delete' and not matches,
                        'Saved playlist disappeared')
                return {'home': home, 'playlists': []}
            require(exact[0]['Name'] == title and
                    all(r['Id'] == saved['id'] for r in matches),
                    'Saved playlist renamed or substituted')
            # Once created, protected provenance and the exact ID own discovery.
            # A display title never establishes ownership.
            matches = exact
        elif matches:
            require(display_title(d, marker) == marker, 'Pending create display title changed')
            require(pending and pending['operation'] == 'create' and
                    pending['before']['playlists'] == [] and d.get('run'),
                    'Unexplained existing marker playlist')
        if not matches:
            return {'home': home, 'playlists': []}
        pid = opaque(matches[0]['Id'])
        path = '/Users/' + c['actor'] + '/Items/' + pid
        members_path = '/Playlists/' + pid + '/Items'
        self._routes.update({('GET', path), ('GET', members_path)})
        item = self._http('GET', path, {'Fields': 'ShareLevel', 'EnableUserData': 'false'})
        require(type(item) is dict and item.get('Id') == pid and
                item.get('Type') == 'Playlist' and item.get('Name') == title and
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
        return {'home': home, 'playlists': [{'id': pid, 'marker': marker,
            'owner': c['actor'], 'access': managed_access,
            'entries': normalized}]}

    def _intent(self, operation, args):
        d, marker = self._state()
        require(d is not None and d.get('run') and d.get('pending'), 'No durable operation intent')
        p = d['pending']
        require(p['operation'] == operation and p['args'] == args, 'Foreign mutation intent')
        current = self.snapshot(self._config['recipient'], marker)
        require(current == p['before'] == d['current'], 'Drift before typed dispatch')
        return current, marker

    def _post(self, path, *, query=None, body=None):
        self._routes.add(('POST', path))
        self._http('POST', path, query, body)

    def _owned(self, operation, args, playlist):
        current, marker = self._intent(operation, args)
        require(len(current['playlists']) == 1 and
                current['playlists'][0]['id'] == opaque(playlist), 'Foreign playlist mutation')
        return current, marker

    @mutation_dispatch
    def create_playlist(self, actor, marker):
        require(actor == self._config['actor'], 'Foreign creator')
        current, expected = self._intent('create', None)
        d, _ = self._state()
        require(marker == expected and display_title(d, expected) == expected and
                not current['playlists'], 'Existing or foreign playlist/title')
        self._post('/Playlists', query={'Name': marker, 'userId': actor})

    @mutation_dispatch
    def share_read(self, playlist, recipient):
        require(recipient == self._config['recipient'] and recipient != self._config['actor'],
                'Foreign or unnecessary self-share recipient')
        current, _ = self._owned('share', None, playlist)
        require(current['playlists'][0]['access'] == [], 'Managed grant changed')
        self._post('/Items/Access', body={'UserIds': [recipient], 'ItemIds': [playlist],
                                         'ItemAccess': 'Read'})

    @mutation_dispatch
    def create_row(self, recipient, body):
        require(recipient == self._config['recipient'] and type(body) is dict and 'Id' not in body,
                'Foreign row request')
        current, marker = self._owned('row', body, body.get('ParentId'))
        # Core owns the exact template; intent and complete baseline bind every field.
        d, _ = self._state()
        title = display_title(d, marker)
        require(body.get('SectionType') == 'playlist' and body.get('CustomName') == title and
                not any(r.get('CustomName') in (marker, title)
                        for r in current['home']), 'Foreign row marker')
        self._post('/Users/' + recipient + '/HomeSections', body=body)

    @mutation_dispatch
    def set_row_display_title(self, recipient, body):
        require(recipient == self._config['recipient'] and type(body) is dict,
                'Foreign display title recipient')
        current, marker = self._intent('display-title', body)
        d, _ = self._state()
        expected = title_row(d, current, marker)
        expected['CustomName'] = display_title({'display_title': body.get('CustomName')}, marker)
        if expected.get('Subtitle') == 'operator-provisioned' and body.get('Subtitle') == '':
            expected['Subtitle'] = ''
        require(body == expected, 'Display title must preserve exact saved row')
        self._post('/Users/' + recipient + '/HomeSections', body=body)

    @mutation_dispatch
    def delete_entries(self, playlist, actor, entries):
        require(actor == self._config['actor'] and type(entries) is list, 'Foreign occurrence actor')
        current, _ = self._owned('clear', entries, playlist)
        require(entries and entries == [e['entry'] for e in current['playlists'][0]['entries']],
                'Stale occurrences')
        self._post('/Playlists/' + playlist + '/Items/Delete', body={'EntryIds': ','.join(entries)})

    @mutation_dispatch
    def add_movies(self, playlist, actor, movies):
        require(actor == self._config['actor'], 'Foreign membership actor')
        movies = movie_ids(movies)
        current, _ = self._owned('add', movies, playlist)
        require(not current['playlists'][0]['entries'], 'Membership no longer empty')
        self.query_movies(self._config['recipient'], self._config['library'], movies)
        self._post('/Playlists/' + playlist + '/Items', body={
            'Ids': ','.join(movies), 'userId': actor, 'SkipDuplicates': False})

    @mutation_dispatch
    def delete_row(self, recipient, row):
        require(recipient == self._config['recipient'], 'Foreign row recipient')
        current, marker = self._intent('unrow', row)
        d, _ = self._state()
        matches = [r for r in current['home'] if row_id(r['Id']) == row_id(row)]
        require(d['row'] == row and len(matches) == 1 and len(current['playlists']) == 1 and
                matches[0].get('CustomName') == display_title(d, marker) and
                matches[0].get('SectionType') == 'playlist' and
                matches[0].get('ParentId') == current['playlists'][0]['id'], 'Foreign saved row')
        self._post('/Users/' + recipient + '/HomeSections/Delete', body={'ids': [row]})

    @mutation_dispatch
    def delete_owned_playlist(self, playlist, marker, actor):
        require(actor == self._config['actor'], 'Foreign delete actor')
        current, expected = self._owned('delete', None, playlist)
        d, _ = self._state()
        require(marker == expected and d['row'] is None and
                not any(r.get('ParentId') == playlist or r.get('CustomName') == marker
                        for r in current['home']), 'Playlist still referenced')
        self._post('/Items/Delete', query={'Ids': playlist})
