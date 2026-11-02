"""Bounded protected operator transport. Managed grant and journal provenance only."""
import copy
import ipaddress
import queue
import threading
import ssl
from xml.etree import ElementTree
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
                                           native_move_index, EPISODE, canonical_id, catalog_ids,
                                           series_library_ids, series_map, typed_items)

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
# Root-operator CLI only; neither the shared entry nor the MCP socket admits it.
INVENTORY_OPERATION = 'recommendation-inventory'
MAX_LIBRARIES = 100
# Series reads: fixed recipient-scoped queries under one configured Series library.
SERIES_LIBRARY_TYPE = 'tvshows'
MAX_CATALOG_LIMIT = 10
MAX_CATALOG_OFFSET = 1000
REPRESENTATIVE_PAGE = 25
REPRESENTATIVE_SORT = 'ParentIndexNumber,IndexNumber,SortName'


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
    # Optional series_libraries opts this recipient into typed Episode lists and
    # Series reads; without it the configuration and behavior are unchanged.
    require(type(c) is dict and set(c) - {'series_libraries'} == {
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
    if 'series_libraries' in c:
        series = series_map(c['series_libraries'])
        require(c['library'] not in series and
                all(v['guid'] != c['library_guid'] for v in series.values()),
                "Invalid Series libraries")
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


def collection_type(value):
    """Explicit CollectionType token, or None for a mixed library. Never inferred."""
    require(value is None or (isinstance(value, str) and
                              re.fullmatch('[a-z]{1,32}', value) is not None),
            'Invalid library type')
    return value


def inventory_request(config, request):
    """Validate an inventory request before any credential load or network send.

    Exactly the configured recipient, plus at most one canonical decimal
    episode_id. Returns (recipient, episode_id or None).
    """
    require(type(request) is dict and
            set(request) in ({'recipient'}, {'recipient', 'episode_id'}),
            'Invalid operator request fields')
    require(request['recipient'] == config['recipient'], 'Foreign recipient')
    episode = request.get('episode_id')
    require('episode_id' not in request or catalog_id(episode), 'Invalid episode ID')
    return request['recipient'], episode


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
    return metadata_fields(row, item, 'Invalid Movie metadata')


def series_item(row, library, category):
    """Allowlisted Series facts with the same bounded metadata as Movies.

    Category is the configured category of the exact library, never inferred.
    """
    require(row.get('Type') == 'Series' and printable(row.get('Name'), MAX_TITLE_BYTES) and
            catalog_id(row.get('Id')), 'Invalid Series metadata')
    item = {'Id': row['Id'], 'Type': 'Series', 'Name': row['Name'], 'LibraryId': library,
            'Category': category}
    return metadata_fields(row, item, 'Invalid Series metadata')


def metadata_fields(row, item, message):
    """Add known ProductionYear, Genres and People to item, else refuse with message."""
    year = row.get('ProductionYear')
    if year is not None:
        require(type(year) is int and 1 <= year <= 9999, message)
        item['ProductionYear'] = year
    genres = row.get('Genres')
    if genres is not None:
        require(type(genres) is list and len(genres) <= MAX_GENRES and
                all(printable(g, MAX_NAME_BYTES) for g in genres), message)
        item['Genres'] = list(genres)
    people = row.get('People')
    if people is not None:
        require(type(people) is list and len(people) <= MAX_RAW_CREDITS and
                all(type(p) is dict for p in people), message)
        credits, seen, producers, others = [], set(), 0, 0
        for p in people:
            if p.get('Type') not in CREDIT_ROLES:
                continue
            require(catalog_id(p.get('Id')) and printable(p.get('Name'), MAX_NAME_BYTES),
                    message)
            if (p['Id'], p['Type']) in seen:
                continue
            seen.add((p['Id'], p['Type']))
            if p['Type'] == 'Producer':
                if producers >= MAX_PRODUCERS:
                    continue
                producers += 1
            else:
                require(others < MAX_CREDITS, message)
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

    def verified_identity(self, series=()):
        """Admin, principals and Movie library; plus each named Series library.

        A Series library must be the exact configured folder (ID, GUID, name,
        tvshows) and a current recipient view. The same two reads serve both
        checks, so the request count is unchanged.
        """
        c = self._config
        series = self._series_libraries(series) if series else {}
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
        for library, expected in series.items():
            found = [f for f in folders if f.get('ItemId') == library]
            require(len(found) == 1 and found[0].get('Guid') == expected['guid'] and
                    found[0].get('Name') == expected['name'] and
                    found[0].get('CollectionType') == SERIES_LIBRARY_TYPE,
                    "Series library identity drift")
        views = complete(self._http('GET', '/Users/' + c['recipient'] + '/Views'), 100)
        require(sum(v.get('Id') == c['library'] for v in views) == 1,
                "Recipient library view unavailable")
        require(all(sum(v.get('Id') == library for v in views) == 1 for library in series),
                "Recipient Series library view unavailable")
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

    def _series_libraries(self, libraries):
        """The configured identities of 1..2 sorted granted Series library IDs."""
        configured = self._config.get('series_libraries') or {}
        libraries = series_library_ids(list(libraries))
        require(all(x in configured for x in libraries), 'Series library outside configuration')
        return {x: configured[x] for x in libraries}

    def _items(self, recipient, query, cap):
        """One fixed recipient-scoped Items GET without user data; complete page only."""
        query = dict(query, EnableUserData='false')
        return complete(self._http('GET', '/Users/' + recipient + '/Items', query), cap)

    def _scoped_episodes(self, recipient, library, ids):
        """Visible Episodes among ids inside one library: {id: (SeriesId, SeriesName)}."""
        rows = self._items(recipient, {
            'Recursive': 'true', 'IncludeItemTypes': 'Episode', 'ParentId': library,
            'Ids': ','.join(ids), 'StartIndex': 0, 'Limit': len(ids) + 1}, len(ids))
        found = {}
        for r in rows:
            require(r.get('Type') == 'Episode' and r.get('Id') in ids and r['Id'] not in found,
                    'Unrequested, duplicate or non-Episode item')
            series = r.get('SeriesId')
            require(catalog_id(series) and series != r['Id'] and
                    (r.get('SeriesName') is None or printable(r.get('SeriesName'), MAX_TITLE_BYTES)),
                    'Episode and Series differ')
            found[r['Id']] = (series, r.get('SeriesName'))
        return found

    def _scoped_series(self, recipient, library, ids, fields=False):
        """Visible Series among ids inside one library, by ID, in returned order."""
        query = {'Recursive': 'true', 'IncludeItemTypes': 'Series', 'ParentId': library,
                 'Ids': ','.join(ids), 'StartIndex': 0, 'Limit': len(ids) + 1}
        if fields:
            query['Fields'] = METADATA_FIELDS
        found = {}
        for r in self._items(recipient, query, len(ids)):
            require(r.get('Type') == 'Series' and r.get('Id') in ids and r['Id'] not in found and
                    printable(r.get('Name'), MAX_TITLE_BYTES), 'Unrequested, duplicate or non-Series item')
            found[r['Id']] = r
        return found

    def query_episodes(self, recipient, libraries, items):
        """Write proof for typed items: every item, in order, or refuse.

        Each Episode is a current recipient-visible Episode inside its library
        whose SeriesId is the item's Series, and that Series is visible in the
        same library. A Series ID is never accepted where an Episode is needed.
        """
        c = self._config
        require(recipient == c['recipient'], 'Foreign query scope')
        items = typed_items(items, 1)
        granted = self._series_libraries(libraries)
        used = sorted({i['library_id'] for i in items})
        require(all(x in granted for x in used), 'Item library outside Series grant')
        self.verified_identity(used)
        self._prove_episode_bindings(recipient, items)
        return items

    def _prove_episode_bindings(self, recipient, items):
        # Private proof after this invocation's identity check. A snapshot
        # batches four lists plus the durable replacement request, without
        # repeating identity or DTO reads. This does not change per-list limits.
        require(1 <= len(items) <= (MAX_LISTS + 1) * 25, 'Too many Episode bindings')
        for library in sorted({i['library_id'] for i in items}):
            group = [i for i in items if i['library_id'] == library]
            episodes = self._scoped_episodes(recipient, library,
                                            sorted({i['episode_id'] for i in group}))
            require(set(episodes) == {i['episode_id'] for i in group} and
                    all(episodes[i['episode_id']][0] == i['series_id'] for i in group),
                    'Recipient Episode set incomplete or unauthorized')
            series = self._scoped_series(recipient, library, sorted({i['series_id'] for i in group}))
            require(set(series) == {i['series_id'] for i in group} and
                    all(episodes[i['episode_id']][1] in (None, series[i['series_id']]['Name'])
                        for i in group), 'Recipient Series set incomplete or unauthorized')

    def series_seeds(self, recipient, episode_ids, libraries):
        """Read-only: the visible parent Series of played Episodes, per granted library.

        Episodes resolve through recipient-scoped Episode.SeriesId inside each
        granted library; invisible or unparented Episodes are skipped. An
        Episode in two libraries, or a SeriesName contradiction, refuses. Series
        are returned in first submitted-Episode order with their EpisodeIds.
        """
        c = self._config
        require(recipient == c['recipient'], 'Foreign query scope')
        episode_ids = catalog_ids(episode_ids, 'Episode')
        granted = self._series_libraries(libraries)
        self.verified_identity(list(granted))
        homes = {}
        for library in granted:
            for episode, parent in self._scoped_episodes(recipient, library, episode_ids).items():
                require(episode not in homes, 'Episode in several libraries')
                homes[episode] = (library,) + parent
        rows = {}
        for library in granted:
            wanted = sorted({h[1] for h in homes.values() if h[0] == library})
            if wanted:
                for sid, row in self._scoped_series(recipient, library, wanted, True).items():
                    require(sid not in rows, 'Series in several libraries')
                    rows[sid] = (library, row)
        result, index = [], {}
        for episode in episode_ids:
            if episode not in homes:
                continue
            library, sid, name = homes[episode]
            if sid not in rows or rows[sid][0] != library:
                continue  # Parent not visible in the same library: insufficient, skipped.
            row = rows[sid][1]
            require(name in (None, row['Name']), 'Episode and Series differ')
            if sid not in index:
                item = series_item(row, library, granted[library]['category'])
                item['EpisodeIds'] = []
                index[sid] = item
                result.append(item)
            index[sid]['EpisodeIds'].append(episode)
        return result

    def recipient_series(self, recipient, ids, libraries):
        """Read-only visible Series by ID in request order, with optional Representative."""
        c = self._config
        require(recipient == c['recipient'], 'Foreign query scope')
        ids = catalog_ids(ids, 'Series')
        granted = self._series_libraries(libraries)
        self.verified_identity(list(granted))
        found = {}
        for library in granted:
            for sid, row in self._scoped_series(recipient, library, ids, True).items():
                require(sid not in found, 'Series in several libraries')
                found[sid] = series_item(row, library, granted[library]['category'])
        result = [found[x] for x in ids if x in found]
        self._attach_representatives(recipient, result)
        return result

    def series_catalog(self, recipient, library, limit, offset=0, person=None, libraries=()):
        """Read-only bounded page of visible Series in one granted library.

        next_offset is offset + limit while more rows exist and that offset stays
        within MAX_CATALOG_OFFSET, else null. Null at the bound is not a claim
        that the catalog is exhausted. With a person filter every returned
        Series must carry that exact credit after projection, or the read refuses.
        """
        c = self._config
        require(recipient == c['recipient'], 'Foreign query scope')
        granted = self._series_libraries(libraries)
        require(library in granted, 'Library outside Series grant')
        require(type(limit) is int and 1 <= limit <= MAX_CATALOG_LIMIT, 'Invalid limit')
        require(type(offset) is int and 0 <= offset <= MAX_CATALOG_OFFSET, 'Invalid offset')
        require(person is None or (type(person) is tuple and len(person) == 2 and
                                   catalog_id(person[0]) and person[1] in CREDIT_ROLES),
                'Invalid person filter')
        self.verified_identity([library])
        query = {'Recursive': 'true', 'IncludeItemTypes': 'Series', 'ParentId': library,
                 'Fields': METADATA_FIELDS, 'SortBy': 'SortName', 'SortOrder': 'Ascending',
                 'StartIndex': offset, 'Limit': limit, 'EnableUserData': 'false'}
        if person is not None:
            query.update(PersonIds=person[0], PersonTypes=person[1])
        dto = self._http('GET', '/Users/' + recipient + '/Items', query)
        require(type(dto) is dict and type(dto.get('Items')) is list and
                type(dto.get('TotalRecordCount')) is int and dto['TotalRecordCount'] >= 0 and
                all(type(x) is dict for x in dto['Items']), 'Incomplete or unbounded result')
        rows, total = dto['Items'], dto['TotalRecordCount']
        require(len(rows) == max(0, min(limit, total - offset)), 'Incomplete or unbounded result')
        result, seen = [], set()
        for row in rows:
            item = series_item(row, library, granted[library]['category'])
            require(item['Id'] not in seen, 'Duplicate Series')
            seen.add(item['Id'])
            if person is not None:
                require(any(p['Id'] == person[0] and p['Type'] == person[1]
                            for p in item.get('People', [])), 'Series lacks the filtered credit')
            result.append(item)
        self._attach_representatives(recipient, result)
        following = offset + limit
        return {'series': result, 'next_offset': following
                if total > following and following <= MAX_CATALOG_OFFSET else None}

    def _attach_representatives(self, recipient, series):
        """Optional first normal Episode per Series from one bounded sorted page.

        The choice is the least (season, episode, ID) among positive-indexed
        Episodes in the page. A Series without one keeps no Representative; no
        further page and no watched state is read. Chosen Episodes are then
        proven inside each Series library with one batched query.
        """
        chosen = {}
        for item in series:
            dto = self._http('GET', '/Users/' + recipient + '/Items', {
                'Recursive': 'true', 'IncludeItemTypes': 'Episode', 'ParentId': item['Id'],
                'SortBy': REPRESENTATIVE_SORT, 'SortOrder': 'Ascending', 'StartIndex': 0,
                'Limit': REPRESENTATIVE_PAGE, 'EnableUserData': 'false'})
            require(type(dto) is dict and type(dto.get('Items')) is list and
                    len(dto['Items']) <= REPRESENTATIVE_PAGE and
                    type(dto.get('TotalRecordCount')) is int and
                    dto['TotalRecordCount'] >= len(dto['Items']) and
                    all(type(r) is dict for r in dto['Items']), 'Incomplete or unbounded result')
            normal, seen = [], set()
            for r in dto['Items']:
                require(r.get('Type') == 'Episode' and catalog_id(r.get('Id')) and
                        r['Id'] not in seen and r.get('SeriesId') == item['Id'] and
                        r.get('SeriesName') in (None, item['Name']), 'Representative differs from Series')
                seen.add(r['Id'])
                season, number = r.get('ParentIndexNumber'), r.get('IndexNumber')
                if (type(season) is int and type(number) is int and season > 0 and number > 0
                        and r.get('LocationType') != 'Virtual'):
                    normal.append((season, number, int(r['Id']), r['Id']))
            if normal:
                season, number, _, episode = min(normal)
                chosen[item['Id']] = {'Id': episode, 'Type': EPISODE, 'SeriesId': item['Id'],
                                      'SeriesName': item['Name'], 'ParentIndexNumber': season,
                                      'IndexNumber': number}
        for library in sorted({s['LibraryId'] for s in series if s['Id'] in chosen}):
            wanted = [chosen[s['Id']]['Id'] for s in series
                      if s['Id'] in chosen and s['LibraryId'] == library]
            found = self._scoped_episodes(recipient, library, wanted)
            require(set(found) == set(wanted) and
                    all(found[chosen[s['Id']]['Id']][0] == s['Id'] for s in series
                        if s['Id'] in chosen and s['LibraryId'] == library),
                    'Representative outside Series library')
        for item in series:
            if item['Id'] in chosen:
                item['Representative'] = chosen[item['Id']]

    def janitor_protections(self, recipient, library, target):
        """Positive-only Movie protections. Absence never means not liked."""
        c = self._config
        require(recipient == c['recipient'] and library == c['library'] and
                catalog_id(target) and catalog_id(library), 'Foreign janitor query scope')
        self.verified_identity()
        rows = complete(self._http('GET', '/Users/' + recipient + '/Items', {
            'Recursive': 'true', 'IncludeItemTypes': 'Movie', 'ParentId': library,
            'Ids': target, 'StartIndex': 0, 'Limit': 2, 'EnableUserData': 'true'}), 1)
        result = []
        for row in rows:
            require(row.get('Id') == target and row.get('Type') == 'Movie',
                    'Invalid janitor Movie')
            require('LibraryId' not in row or row['LibraryId'] == library,
                    'Foreign janitor Movie')
            data = row.get('UserData')
            protection = 'unknown'
            if type(data) is dict:
                require('ItemId' not in data or data['ItemId'] == target,
                        'Foreign janitor user data')
                if data.get('IsFavorite') is True or data.get('Likes') is True:
                    protection = 'liked'
            result.append({'id': target, 'kind': 'Movie', 'protection': protection})
        return result

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

    def recommendation_inventory(self, recipient, episode_id=None):
        """Read-only recipient library inventory and one optional Episode-to-Series map.

        Root operator CLI only. After the unchanged identity verification it
        returns the recipient's current views that are exactly one virtual
        folder, as Id/Guid/Name/CollectionType only, and at most one Episode
        mapping. Every request is a fixed recipient-scoped GET without user
        data or Fields. Unmappable or invisible items are insufficient facts.
        """
        request = {'recipient': recipient}
        if episode_id is not None:
            request['episode_id'] = episode_id
        inventory_request(self._config, request)
        self.verified_identity()
        libraries = self._visible_libraries(recipient)
        result = {'recipient': recipient, 'libraries': libraries}
        if episode_id is not None:
            result['episode'] = self._episode_series(recipient, episode_id, libraries)
        return result

    def _visible_libraries(self, recipient):
        c = self._config
        folders = self._http('GET', '/Library/VirtualFolders')
        require(type(folders) is list and len(folders) <= MAX_LIBRARIES and
                all(type(f) is dict for f in folders), 'Invalid library response')
        ids = [opaque(f.get('ItemId')) for f in folders]
        guids = [f.get('Guid') for f in folders]
        require(len(ids) == len(set(ids)) and len(guids) == len(set(guids)) and
                all(isinstance(g, str) and re.fullmatch('[0-9a-f]{32}', g) for g in guids),
                'Ambiguous library identity')
        by_id = dict(zip(ids, folders))
        views = complete(self._http('GET', '/Users/' + recipient + '/Views'), MAX_LIBRARIES)
        view_ids = [opaque(v.get('Id')) for v in views]
        require(len(view_ids) == len(set(view_ids)), 'Duplicate recipient view')
        libraries = []
        for view_id, view in zip(view_ids, views):
            folder = by_id.get(view_id)
            if folder is None:
                continue  # Not a library folder, for example the Playlists view.
            name, kind = folder.get('Name'), collection_type(folder.get('CollectionType'))
            require(printable(name, MAX_TITLE_BYTES) and view.get('Name') == name and
                    collection_type(view.get('CollectionType')) == kind,
                    'Recipient view and library folder differ')
            libraries.append({'Id': view_id, 'Guid': folder['Guid'], 'Name': name,
                              'CollectionType': kind})
        require(sum(x['Id'] == c['library'] and x['Guid'] == c['library_guid']
                    for x in libraries) == 1, 'Recipient library view unavailable')
        return libraries

    def _visible_items(self, recipient, item_id, parent=None, kind=None):
        """At most one recipient-visible item by exact ID, optionally inside one library."""
        query = {'Ids': item_id, 'EnableUserData': 'false', 'StartIndex': 0, 'Limit': 2}
        if parent is not None:
            query.update(Recursive='true', ParentId=parent, IncludeItemTypes=kind)
        rows = complete(self._http('GET', '/Users/' + recipient + '/Items', query), 1)
        require(all(r.get('Id') == item_id for r in rows), 'Unrequested inventory item')
        require(kind is None or all(r.get('Type') == kind for r in rows),
                'Inventory item type differs')
        return rows

    def _episode_series(self, recipient, episode_id, libraries):
        insufficient = {'Id': episode_id, 'status': 'insufficient-facts'}
        rows = self._visible_items(recipient, episode_id)
        if not rows:
            return insufficient
        episode = rows[0]
        require(episode.get('Type') == 'Episode', 'Requested item is not an Episode')
        series_id = episode.get('SeriesId')
        if series_id is None:
            return insufficient
        require(catalog_id(series_id) and series_id != episode_id, 'Invalid Series ID')
        rows = self._visible_items(recipient, series_id)
        if not rows:
            return insufficient
        series = rows[0]
        require(series.get('Type') == 'Series' and
                printable(series.get('Name'), MAX_TITLE_BYTES) and
                episode.get('SeriesName') in (None, series['Name']), 'Episode and Series differ')
        homes = []
        for library in libraries:
            scoped = self._visible_items(recipient, series_id, library['Id'], 'Series')
            require(not scoped or scoped[0].get('Name') == series['Name'],
                    'Scoped Series differs')
            if scoped:
                homes.append(library)
        require(len(homes) <= 1, 'Ambiguous Series library')
        if not homes:
            return insufficient
        scoped = self._visible_items(recipient, episode_id, homes[0]['Id'], 'Episode')
        require(scoped and scoped[0].get('SeriesId') == series_id and
                scoped[0].get('SeriesName') in (None, series['Name']),
                'Episode outside its Series library or mapping differs')
        return {'Id': episode_id, 'Type': 'Episode', 'SeriesId': series_id,
                'SeriesType': 'Series', 'SeriesName': series['Name'],
                'LibraryId': homes[0]['Id'], 'status': 'mapped'}

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
        records = self._records(d)
        request = ((d or {}).get('run') or {}).get('request') or {}
        # Recheck identities even for empty or detached typed lists. Every
        # snapshot, including the dispatch guard, uses current recipient views.
        series = {library for record in records.values() if 'media_kind' in record
                  for library in record['libraries']}
        series.update(i['library_id'] for i in request.get('items', []))
        self.verified_identity(sorted(series))
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
        # Typed Episode lists, from protected custody: saved records or a typed run.
        kinds = {m: EPISODE for m, r in records.items() if 'media_kind' in r}
        if 'items' in request:
            kinds[self._marker(request.get('list'))] = EPISODE
        # Prove the durable request too, including before its first member
        # exists. Thus every pre-dispatch and readback snapshot covers both
        # current members and the exact pending replacement, in the same reads.
        playlists, observed = [], list(request.get('items', []))
        for m in sorted(candidates):
            pid = self._discover(rows, m, saved.get(m), d, records)
            if pid is not None:
                playlist = self._playlist(pid, m, kinds.get(m, 'Movie'))
                if kinds.get(m) == EPISODE:
                    bindings = {i['episode_id']: i for i in records.get(m, {}).get('items', [])}
                    if request.get('list') is not None and self._marker(request['list']) == m:
                        for binding in request.get('items', []):
                            prior = bindings.get(binding['episode_id'])
                            require(prior is None or prior == binding, 'Typed membership binding differs')
                            bindings[binding['episode_id']] = binding
                    for entry in playlist['entries']:
                        require(entry['episode'] in bindings,
                                'Uncertain mutation; exact readback required, no retry')
                        observed.append(bindings[entry['episode']])
                playlists.append(playlist)
        if observed:
            self._prove_episode_bindings(recipient, observed)
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

    def _playlist(self, pid, marker, kind='Movie'):
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
        # Exact member type per list: an Episode list never accepts a Series or
        # Movie occurrence (Series expansion is caught by the exact readback).
        key = 'episode' if kind == EPISODE else 'movie'
        for e in entries:
            require(e.get('Type') == kind, 'Non-%s playlist occurrence' % kind)
            occurrence = opaque(e.get('PlaylistItemId'))
            occurrence_ids.append(occurrence)
            normalized.append({key: opaque(e.get('Id')), 'entry': occurrence})
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
        intent = self._owned('add', movies, playlist)
        target = intent[3]
        require('media_kind' not in (intent[4] or {}), 'List media kind differs')
        require(not target['entries'], 'Membership no longer empty')
        self.query_movies(self._config['recipient'], self._config['library'], movies)
        self._post('/Playlists/' + playlist + '/Items', body={
            'Ids': ','.join(movies), 'userId': actor, 'SkipDuplicates': False})

    @mutation_dispatch
    def add_episodes(self, playlist, actor, items):
        """Append validated Episode IDs only; never a Series ID.

        The journal-owned list record must be a typed Episode list bound to
        every item's library, and each item is re-proven just before the POST.
        """
        require(actor == self._config['actor'], 'Foreign membership actor')
        items = typed_items(items, 1)
        intent = self._owned('add-episodes', items, playlist)
        target, record = intent[3], intent[4]
        require(record is not None and record.get('media_kind') == EPISODE and
                all(i['library_id'] in record['libraries'] for i in items),
                'List media kind differs')
        require(not target['entries'], 'Membership no longer empty')
        self.query_episodes(self._config['recipient'], record['libraries'], items)
        self._post('/Playlists/' + playlist + '/Items', body={
            'Ids': ','.join(i['episode_id'] for i in items), 'userId': actor,
            'SkipDuplicates': False})

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


# Importer inventory is a separate fixed-GET transport. It never uses Emby
# credentials, history, the recipient journal or the mutation adapter.
IMPORTER_BACKENDS = frozenset({'radarr', 'sonarr'})
IMPORTER_MAX_BODY = 256 * 1024
IMPORTER_MAX_LISTS = 32


def validate_importer_config(value):
    require(type(value) is dict and set(value) == {
        'version', 'backend', 'resource_id', 'origin', 'expected_version',
        'app_config_file', 'app_config_owner_uid'}, 'Invalid importer configuration')
    require(type(value['version']) is int and value['version'] == 1 and
            isinstance(value['backend'], str) and value['backend'] in IMPORTER_BACKENDS and
            type(value['app_config_owner_uid']) is int and
            0 <= value['app_config_owner_uid'] < 2 ** 31,
            'Invalid importer configuration')
    opaque(value['resource_id'])
    require(isinstance(value['expected_version'], str) and
            re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,8}){3}', value['expected_version']),
            'Invalid importer version')
    require(isinstance(value['app_config_file'], str), 'Invalid importer config path')
    p = PurePosixPath(value['app_config_file'])
    require(p.is_absolute() and
            str(p) == value['app_config_file'] and '..' not in p.parts and
            len(p.parts) > 2 and len(str(p)) <= 4096, 'Invalid importer config path')
    origin = value['origin']
    require(isinstance(origin, str) and printable(origin, 1024), 'Invalid importer origin')
    try:
        url = urllib.parse.urlsplit(origin)
        port = url.port
        loopback = url.hostname == 'localhost'
        if not loopback:
            try:
                loopback = ipaddress.ip_address(url.hostname or '').is_loopback
            except ValueError:
                loopback = False
        require(url.scheme in ('http', 'https') and url.hostname and
                url.username is None and url.password is None and
                not url.query and not url.fragment and '?' not in origin and '#' not in origin and
                url.path in ('', '/') and
                (port is None or 1 <= port <= 65535) and
                (url.scheme == 'https' or loopback), 'Invalid importer origin')
    except (TypeError, ValueError):
        raise Refused('Invalid importer origin') from None
    return copy.deepcopy(value)


def existing_importer_key(path, owner_uid):
    """Read the app's existing private config.xml by directory FDs, no copy.

    The instance API key has admin power. Fixed GET routes, not key privileges,
    enforce this operation's read-only boundary.
    """
    fd = None
    try:
        p = PurePosixPath(path)
        require(p.is_absolute() and str(p) == path and '..' not in p.parts and
                len(p.parts) > 2, 'Invalid importer config path')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in p.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            info = os.fstat(fd)
            require(info.st_uid in (0, owner_uid) and not info.st_mode & 0o022,
                    'Unsafe importer config ancestor')
        leaf = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(leaf)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == owner_uid and
                    stat.S_IMODE(info.st_mode) in (0o400, 0o600) and info.st_nlink == 1 and
                    info.st_size <= IMPORTER_MAX_BODY, 'Unsafe importer config file')
            with os.fdopen(leaf, 'rb', closefd=False) as stream:
                raw = stream.read(IMPORTER_MAX_BODY + 1)
            require(len(raw) <= IMPORTER_MAX_BODY and
                    b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper(),
                    'Unsafe importer XML')
            # Reject UTF-16/32 and NUL-obfuscated declarations before XML parsing.
            text = raw.decode('utf-8')
            require('\x00' not in text, 'Unsafe importer XML')
            tree = ElementTree.fromstring(text)
            keys = tree.findall('ApiKey')
            bases = tree.findall('UrlBase')
            require(tree.tag == 'Config' and len(keys) == 1 and not keys[0].attrib and
                    len(list(keys[0])) == 0 and
                    re.fullmatch('[0-9a-fA-F]{32}', keys[0].text or '') and
                    len(bases) <= 1 and all(not (base.text or '').strip() for base in bases),
                    'Invalid importer XML binding')
            return keys[0].text
        finally:
            os.close(leaf)
    except (OSError, ValueError, TypeError, UnicodeError, ElementTree.ParseError):
        raise Refused('Importer config unavailable or unsafe') from None
    finally:
        if fd is not None:
            os.close(fd)


class ImporterInventoryAdapter:
    """Two fixed GETs, one ten-second budget, no retry and no write method."""
    def __init__(self, config, *, request_guard):
        self._config = validate_importer_config(config)
        require(callable(request_guard), 'Importer deadline required')
        self._guard = request_guard
        self._deadline = time.monotonic() + min(10, request_guard())
        self._key = None
        self._calls = 0
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(
                cafile='/etc/ssl/certs/ca-certificates.crt')))

    def _get(self, path):
        require(path in ('/api/v3/system/status', '/api/v3/importlist') and
                self._calls < 2 and self._key is not None, 'Importer route refused')
        remaining = min(self._deadline - time.monotonic(), self._guard())
        require(remaining > 0, 'Importer deadline reached')
        self._calls += 1
        request = urllib.request.Request(self._config['origin'].rstrip('/') + path,
            headers={'Accept': 'application/json', 'X-Api-Key': self._key}, method='GET')
        # urllib timeouts are per blocking read, so a slowly streaming header
        # or body must not extend the overall budget. One daemon I/O thread
        # belongs to this single-request worker process, which exits after its
        # response. A late GET cannot cause a retry, another send, or a write.
        completed = queue.Queue(maxsize=1)
        opener = self._opener
        def read_response():
            try:
                with opener.open(request, timeout=remaining) as response:
                    require(response.status == 200, 'Importer inventory unavailable')
                    lengths = response.headers.get_all('Content-Length', [])
                    transfers = response.headers.get_all('Transfer-Encoding', [])
                    require(len(lengths) <= 1 and len(transfers) <= 1 and
                            not (lengths and transfers) and
                            (not transfers or transfers[0].strip().lower() == 'chunked'),
                            'Invalid importer response framing')
                    declared = None
                    if lengths:
                        require(re.fullmatch('[0-9]{1,9}', lengths[0]) is not None,
                                'Invalid importer response framing')
                        declared = int(lengths[0])
                        require(declared <= IMPORTER_MAX_BODY, 'Importer response too large')
                    raw = response.read(IMPORTER_MAX_BODY + 1)
                    require(len(raw) <= IMPORTER_MAX_BODY, 'Importer response too large')
                    require(declared is None or len(raw) == declared,
                            'Incomplete importer response')
                completed.put((raw, False))
            except Exception:
                completed.put((None, True))
        threading.Thread(target=read_response, daemon=True).start()
        try:
            raw, failed = completed.get(timeout=remaining)
        except queue.Empty:
            raise Refused('Importer deadline reached') from None
        require(not failed, 'Importer inventory unavailable')
        require(time.monotonic() < self._deadline and self._guard() > 0,
                'Importer deadline reached')
        return strict_json(raw)

    def inventory(self):
        try:
            require(time.monotonic() < self._deadline and self._guard() > 0,
                    'Importer deadline reached')
            self._key = existing_importer_key(self._config['app_config_file'],
                                             self._config['app_config_owner_uid'])
            status = self._get('/api/v3/system/status')
            require(type(status) is dict and
                    status.get('appName') == self._config['backend'].capitalize() and
                    status.get('version') == self._config['expected_version'] and
                    status.get('urlBase') in (None, ''), 'Importer identity mismatch')
            rows = self._get('/api/v3/importlist')
            require(type(rows) is list and len(rows) <= IMPORTER_MAX_LISTS,
                    'Incomplete importer inventory')
            found = {}
            def boolean(row, key):
                value = row.get(key)
                return value if type(value) is bool else None
            for row in rows:
                require(type(row) is dict and type(row.get('id')) is int and
                        1 <= row['id'] <= 2 ** 31 - 1 and row['id'] not in found and
                        printable(row.get('name'), 256) and
                        printable(row.get('implementation'), 64), 'Invalid importer list')
                radarr = self._config['backend'] == 'radarr'
                found[row['id']] = {
                    'id': row['id'], 'name': row['name'], 'implementation': row['implementation'],
                    'enabled': boolean(row, 'enabled') if radarr else None,
                    'automatic_add': boolean(row, 'enableAuto' if radarr else 'enableAutomaticAdd'),
                    'search_on_add': boolean(row, 'searchOnAdd' if radarr else 'searchForMissingEpisodes')}
            return [found[key] for key in sorted(found)]
        finally:
            self._key = None
