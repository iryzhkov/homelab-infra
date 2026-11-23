#!/usr/bin/env python3
"""HoloNet library prune: keep the managed media library under a byte target.

The weekly root unit holonet-library-prune.service runs `run`: it first removes
marked titles whose grace period ended and that are still eligible, then marks
new candidates until the projected library size is at or below the target.
`plan` evaluates the same rules and prints what a run would do without writing
state or touching any backend. The socket worker serves the read-only
library-prune-status and library-watch-history operations from this module.

Every removal goes through the existing library-remove internals (Arr recycle
bin, keep list, ownership proof). A torrent is removed only when the hardlink
verifier proved it (Arr history alone is not enough here) and the SeedPool
retention policy allows retiring it. Right before each apply the title's Emby
favourites, play state, Arr queue membership and quality profile are read
again. Any Arr, Emby, history, keep-list or qBittorrent read error aborts the
run before the first removal; a failure after it ends the run as partial with
every removed title recorded. Playback history comes from Emby's activity log through the
existing admin session; see docs/holonet-library.md for why that source was
chosen and how incomplete history coverage is handled.
"""
import copy
import datetime
import fcntl
import json
import os
from pathlib import PurePosixPath
import re
import secrets
import stat
import sys
import time

from emby_operator_adapter import catalog_id, printable, private_read, strict_json, load_credentials
from emby_operator_library import (
    FAILURE, LIBRARY_SPECS, PRUNE_MAX_TITLES, SECONDS, LibraryArrClient, LibraryEmbyClient,
    LibraryPostDispatchError, LibraryRefusal, LibraryTorrentClient, _in_root, _open_state_dir,
    _read_state_json, _write_state_json, integer, need, op_remove, refuse, validate_prune_block,
)
from emby_operator_recommendations import Refused
from emby_operator_torrents import descriptor as torrent_descriptor
from seedpool_policy import MINIMUM_SEED_SECONDS, retirement_plan, tracker_kind

DAY = 86400
PROTECTED_PROFILE = 'Favorites - UHD + Original'
MARKS_FILE = 'marks.json'
LAST_RUN_FILE = 'last_run.json'
LOCK_FILE = '.lock'
STATE_MAX_BYTES = 1024 * 1024
KEEP_JOURNAL_MAX_BYTES = 2 * 1024 * 1024
KEEP_CREDENTIAL = 'holonet-janitor-state'
MAX_MARKS = PRUNE_MAX_TITLES
MAX_SUMMARY_ROWS = 100
MAX_WARNINGS = 32
ARR_BUDGET = 120
EPISODEFILE_FETCH_LIMIT = 200
EPISODEFILE_PER_CLIENT = 40
EMBY_PAGE = 500
EMBY_MAX_PAGES = 40
ACTIVITY_PAGE = 1000
ACTIVITY_MAX_PAGES = 50
LOOKUP_CHUNK = 100
WATCH_DEFAULT_DAYS = 90
WATCH_MAX_DAYS = 365
WATCH_DEFAULT_LIMIT = 100
WATCH_MAX_LIMIT = 200
MAX_FAVORITES = 200
MAX_USERS_PER_ROW = 16
MAX_GENRES = 8
PLAYBACK_START_TYPES = frozenset({'playback.start', 'VideoPlayback'})
PROTECTION_NOTE = 'Favouriting a title in Emby (any user) protects it from automatic pruning.'
EMBY_FAILURE = 'Emby facts unavailable'
HISTORY_FAILURE = 'Playback history unavailable'
KEEP_FAILURE = 'Keep list unavailable'
STATE_FAILURE = 'Prune state invalid'
QBIT_FAILURE = 'Torrent snapshot unavailable'
RECHECK_MARGIN = 600  # seconds of activity log re-read before the run started
MAX_BYTES = 1 << 56
HEX32 = re.compile(r'[0-9a-f]{32}')
ISO_RE = re.compile(
    r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})?')
LIBRARY_BY_EMBY_ID = {spec['library_id']: name for name, spec in LIBRARY_SPECS.items()}


class PruneAbort(Exception):
    """Fail-closed stop with a stable reason code; nothing after it is removed."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# --- Time --------------------------------------------------------------------

def parse_time(value):
    """ISO 8601 (Arr/Emby forms, up to 9 fraction digits) to epoch seconds, else None."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    m = ISO_RE.fullmatch(value.strip())
    if m is None:
        return None
    try:
        dt = datetime.datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]),
                               tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    tz = m[8]
    if tz and tz != 'Z':
        sign = 1 if tz[0] == '+' else -1
        dt -= sign * datetime.timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6]))
    return int(dt.timestamp())


def iso(ts):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(ts)))


# --- Configuration -----------------------------------------------------------

def prune_config(block):
    cfg = block.get('prune')
    if cfg is None:
        refuse('prune_disabled', 'Library prune not configured by policy')
    return validate_prune_block(cfg)


# --- Arr facts ---------------------------------------------------------------

def _size(raw):
    stats = raw.get('statistics') if type(raw.get('statistics')) is dict else {}
    value = stats.get('sizeOnDisk', raw.get('sizeOnDisk', 0))
    need(type(value) is int and 0 <= value < 1 << 56, FAILURE)
    return value


def _arr_queue(api, flag, id_field):
    ids = set()
    seen = 0
    total = None
    for page in range(1, 21):
        data = api.get(f'/api/v3/queue?page={page}&pageSize=200&{flag}')
        need(type(data) is dict and type(data.get('records')) is list
             and integer(data.get('totalRecords'), 0, 4000), FAILURE)
        if total is None:
            total = data['totalRecords']
        need(data['totalRecords'] == total, FAILURE)
        for rec in data['records']:
            need(type(rec) is dict, FAILURE)
            if type(rec.get(id_field)) is int:
                ids.add(rec[id_field])
        seen += len(data['records'])
        if seen >= total:
            need(seen == total, FAILURE)
            return ids
        need(data['records'], FAILURE)
    need(False, FAILURE)


def _genres(raw):
    out = []
    for g in raw.get('genres') or []:
        if isinstance(g, str) and printable(g, 64) and g not in out:
            out.append(g)
    return out[:MAX_GENRES]


def collect_arr(block, arr, *, with_protection=True):
    """Managed titles, sizes and protections from Radarr and Sonarr (fail closed).

    arr(backend) returns a bounded client. Library size is the sum of Radarr
    movie and Sonarr series sizeOnDisk inside the configured library roots.
    """
    libs = block['libraries']
    radarr, sonarr = arr('radarr'), arr('sonarr')
    movies = radarr.get('/api/v3/movie')
    series = sonarr.get('/api/v3/series')
    need(type(movies) is list and type(series) is list, FAILURE)
    protected = {'radarr': set(), 'sonarr': set()}
    queue = set()
    if with_protection:
        for backend, api in (('radarr', radarr), ('sonarr', sonarr)):
            profiles = api.get('/api/v3/qualityprofile')
            need(type(profiles) is list and all(type(p) is dict for p in profiles), FAILURE)
            protected[backend] = {p['id'] for p in profiles
                                  if type(p.get('id')) is int and p.get('name') == PROTECTED_PROFILE}
        queue |= {('movies', i) for i in _arr_queue(radarr, 'includeUnknownMovieItems=false', 'movieId')}
        series_queue = _arr_queue(sonarr, 'includeUnknownSeriesItems=false', 'seriesId')
    titles = {}
    by_library = {name: 0 for name in LIBRARY_SPECS}
    for m in movies:
        need(type(m) is dict and integer(m.get('id')), FAILURE)
        if not _in_root(m.get('path'), libs['movies']['root_folder_path']):
            continue
        mf = m.get('movieFile') if type(m.get('movieFile')) is dict else None
        t = {
            'library': 'movies', 'arr_id': m['id'], 'kind': 'movie',
            'title': m.get('title') if printable(m.get('title'), 512) else '',
            'year': m.get('year') if integer(m.get('year'), 1800, 3000) else None,
            'tmdb_id': m.get('tmdbId') if integer(m.get('tmdbId')) else None,
            'tvdb_id': None, 'size': _size(m), 'added_ts': parse_time(m.get('added')),
            'file_ts': parse_time(mf.get('dateAdded')) if mf is not None else 0,
            'protected_profile': m.get('qualityProfileId') in protected['radarr'],
            'path': m['path'].rstrip('/'), 'genres': _genres(m),
        }
        titles[('movies', m['id'])] = t
        by_library['movies'] += t['size']
    for s in series:
        need(type(s) is dict and integer(s.get('id')), FAILURE)
        name = ('anime' if _in_root(s.get('path'), libs['anime']['root_folder_path'])
                else 'tv' if _in_root(s.get('path'), libs['tv']['root_folder_path']) else None)
        if name is None:
            continue
        t = {
            'library': name, 'arr_id': s['id'], 'kind': 'series',
            'title': s.get('title') if printable(s.get('title'), 512) else '',
            'year': s.get('year') if integer(s.get('year'), 1800, 3000) else None,
            'tmdb_id': None, 'tvdb_id': s.get('tvdbId') if integer(s.get('tvdbId')) else None,
            'size': _size(s), 'added_ts': parse_time(s.get('added')), 'file_ts': None,
            'protected_profile': s.get('qualityProfileId') in protected['sonarr'],
            'path': s['path'].rstrip('/'), 'genres': _genres(s),
        }
        titles[(name, s['id'])] = t
        by_library[name] += t['size']
        if with_protection and s['id'] in series_queue:
            queue.add((name, s['id']))
    return {'titles': titles, 'library_bytes': sum(by_library.values()),
            'by_library': by_library, 'queue': queue}


def series_file_ts(api, arr_id):
    """Newest episode file dateAdded; 0 without files; None when any date is unknown."""
    files = api.get(f'/api/v3/episodefile?seriesId={arr_id}')
    need(type(files) is list, FAILURE)
    newest = 0
    for f in files:
        need(type(f) is dict, FAILURE)
        ts = parse_time(f.get('dateAdded'))
        if ts is None:
            return None
        newest = max(newest, ts)
    return newest


class SeriesDates:
    """Lazy, bounded newest-file lookups with a fresh Sonarr client per batch."""
    def __init__(self, arr, limit=EPISODEFILE_FETCH_LIMIT):
        self.arr, self.limit, self.fetched, self.api = arr, limit, 0, None

    def __call__(self, title):
        if title['file_ts'] is not None:
            return title['file_ts']
        if self.fetched >= self.limit:
            return None
        if self.api is None or self.fetched % EPISODEFILE_PER_CLIENT == 0:
            self.api = self.arr('sonarr')
        self.fetched += 1
        title['file_ts'] = series_file_ts(self.api, title['arr_id'])
        return title['file_ts']


# --- Emby facts --------------------------------------------------------------

def _emby_pages(emby, path, query, *, page_size=EMBY_PAGE, max_pages=EMBY_MAX_PAGES,
                failure=EMBY_FAILURE):
    """Every page of one Emby listing; an inconsistent total refuses."""
    rows, total, start = [], None, 0
    for _ in range(max_pages):
        dto = emby._http('GET', path, dict(query, StartIndex=start, Limit=page_size))
        need(type(dto) is dict and type(dto.get('Items')) is list
             and integer(dto.get('TotalRecordCount'), 0, 10 ** 7), failure)
        if total is None:
            total = dto['TotalRecordCount']
        need(dto['TotalRecordCount'] == total and all(type(i) is dict for i in dto['Items']), failure)
        if total == 0:
            need(not dto['Items'], failure)
            return []
        need(dto['Items'], failure)
        rows.extend(dto['Items'])
        start += len(dto['Items'])
        if len(rows) >= total:
            need(len(rows) == total, failure)
            return rows
    need(False, failure)


def emby_users(emby):
    users = emby._http('GET', '/Users')
    need(type(users) is list and 1 <= len(users) <= 100, EMBY_FAILURE)
    out = []
    for u in users:
        need(type(u) is dict and isinstance(u.get('Id'), str) and HEX32.fullmatch(u['Id']), EMBY_FAILURE)
        name = u.get('Name') if printable(u.get('Name'), 128) else u['Id'][:8]
        out.append({'id': u['Id'], 'name': name})
    need(len({u['id'] for u in out}) == len(out), EMBY_FAILURE)
    return out


def _user_route(emby, user_id):
    route = '/Users/' + user_id + '/Items'
    emby.allow_read(route)
    return route


class TitleIndex:
    """Map Emby items to managed Arr titles by provider ID, then by path."""
    def __init__(self, titles, block):
        self.tmdb, self.tvdb, self.paths = {}, {}, {}
        self.roots = [lib['root_folder_path'] for lib in block['libraries'].values()]
        movies_root = block['libraries']['movies']['root_folder_path'].rstrip('/')
        for key, t in titles.items():
            if t['kind'] == 'movie' and t['tmdb_id'] is not None:
                self.tmdb.setdefault(t['tmdb_id'], set()).add(key)
            if t['kind'] == 'series' and t['tvdb_id'] is not None:
                self.tvdb.setdefault(t['tvdb_id'], set()).add(key)
            self.paths[t['path']] = key
            if t['path'].startswith('/movies/'):
                self.paths[movies_root + t['path'][len('/movies'):]] = key

    def _by_path(self, path):
        if not isinstance(path, str) or not path.startswith('/'):
            return None
        p = PurePosixPath(path.rstrip('/'))
        for candidate in [p, *list(p.parents)[:4]]:
            key = self.paths.get(str(candidate))
            if key is not None:
                return key
        return None

    def _by_provider(self, item, provider, table):
        raw = (item.get('ProviderIds') or {}).get(provider) if type(item.get('ProviderIds')) is dict else None
        if isinstance(raw, str) and raw.isdigit() and len(raw) <= 10:
            keys = table.get(int(raw), set())
            if len(keys) == 1:
                return next(iter(keys))
        return None

    def managed_path(self, path):
        """True when an Emby item path lies inside one of the managed library roots."""
        return isinstance(path, str) and any(_in_root(path, root) for root in self.roots)

    def movie(self, item):
        return self._by_provider(item, 'Tmdb', self.tmdb) or self._by_path(item.get('Path'))

    def series(self, item):
        return self._by_provider(item, 'Tvdb', self.tvdb) or self._by_path(item.get('Path'))


def _lookup(emby, routes, ids, fields):
    """Resolve exact Emby item IDs through each route in turn; returns {id: row}."""
    found = {}
    for route in routes:
        missing = sorted(i for i in ids if i not in found)
        for start in range(0, len(missing), LOOKUP_CHUNK):
            chunk = missing[start:start + LOOKUP_CHUNK]
            rows = _emby_pages(emby, route, {'Ids': ','.join(chunk), 'Fields': fields,
                                             'EnableUserData': 'false'}, page_size=LOOKUP_CHUNK + 1)
            for row in rows:
                rid = row.get('Id')
                need(rid in chunk, EMBY_FAILURE)
                found[rid] = row
        if len(found) == len(ids):
            break
    return found


class Resolver:
    """Emby item -> managed title key, resolving Season/Episode through SeriesId."""
    def __init__(self, emby, index, routes):
        self.emby, self.index, self.routes = emby, index, routes
        self.series = {}

    def keys(self, rows, *, routes=None, required=True):
        """Title keys for rows; returns ({row_id: key or None}, unresolved_series_ids)."""
        wanted = set()
        for row in rows:
            if row.get('Type') in ('Season', 'Episode'):
                sid = row.get('SeriesId')
                need(catalog_id(sid) or not required, EMBY_FAILURE)
                if catalog_id(sid) and sid not in self.series:
                    wanted.add(sid)
        if wanted:
            found = _lookup(self.emby, routes or self.routes, wanted, 'ProviderIds,Path')
            for sid in wanted:
                row = found.get(sid)
                if row is not None:
                    need(row.get('Type') == 'Series', EMBY_FAILURE)
                    self.series[sid] = self.index.series(row)
        out, unresolved = {}, set()
        for row in rows:
            kind = row.get('Type')
            if kind == 'Movie':
                out[row.get('Id')] = self.index.movie(row)
            elif kind == 'Series':
                out[row.get('Id')] = self.index.series(row)
            elif kind in ('Season', 'Episode'):
                sid = row.get('SeriesId')
                if sid in self.series:
                    out[row.get('Id')] = self.series[sid]
                else:
                    need(not required, EMBY_FAILURE)
                    unresolved.add(sid)
                    out[row.get('Id')] = None
            else:
                out[row.get('Id')] = None
        return out, unresolved


def collect_user_facts(emby, resolver, users, *, touched=True):
    """Per-user favourites and, optionally, items with any play state."""
    favorites, played = {}, set()
    for user in users:
        route = _user_route(emby, user['id'])
        base = {'Recursive': 'true', 'Fields': 'ProviderIds,Path,SeriesId', 'EnableUserData': 'false'}
        favs = _emby_pages(emby, route, dict(base, IncludeItemTypes='Movie,Series,Season,Episode',
                                             Filters='IsFavorite'))
        keys, _ = resolver.keys(favs, routes=[route] + resolver.routes)
        for key in keys.values():
            if key is not None:
                favorites.setdefault(key, set()).add(user['name'])
        if touched:
            for flt in ('IsPlayed', 'IsResumable'):
                rows = _emby_pages(emby, route, dict(base, IncludeItemTypes='Movie,Episode', Filters=flt))
                keys, _ = resolver.keys(rows, routes=[route] + resolver.routes)
                played |= {k for k in keys.values() if k is not None}
    return favorites, played


def collect_history(emby, resolver, users, *, now, window_start):
    """Attributable playback starts since window_start from Emby's activity log.

    The window is complete only when the log reaches back before window_start
    and every playback start inside it names both the item and the user.
    """
    path = '/System/ActivityLog/Entries'
    emby.allow_read(path)
    head = emby._http('GET', path, {'StartIndex': 0, 'Limit': 1})
    need(type(head) is dict and integer(head.get('TotalRecordCount'), 0, 10 ** 8), HISTORY_FAILURE)
    retention_since = None
    if head['TotalRecordCount'] > 0:
        oldest = emby._http('GET', path, {'StartIndex': head['TotalRecordCount'] - 1, 'Limit': 1})
        need(type(oldest) is dict and type(oldest.get('Items')) is list and len(oldest['Items']) <= 1,
             HISTORY_FAILURE)
        if oldest['Items']:
            retention_since = parse_time(oldest['Items'][0].get('Date'))
            need(retention_since is not None, HISTORY_FAILURE)
    entries = _emby_pages(emby, path, {'MinDate': iso(window_start)}, page_size=ACTIVITY_PAGE,
                          max_pages=ACTIVITY_MAX_PAGES, failure=HISTORY_FAILURE)
    names = {u['id']: u['name'] for u in users}
    by_name = sorted(((u['name'], u['name']) for u in users if u['name']), key=lambda p: -len(p[0]))

    def who(uid, entry):
        if uid in names:
            return names[uid]
        text = entry.get('Name') if isinstance(entry.get('Name'), str) else ''
        for name, label in by_name:
            if text.startswith(name + ' '):
                return label
        return 'user ' + uid[:8]
    events, unattributable, first_attributable = [], 0, None
    for e in entries:
        ts = parse_time(e.get('Date'))
        # The MinDate filter must hold; otherwise the listing is not the window.
        need(ts is not None and window_start - DAY <= ts <= now + DAY, HISTORY_FAILURE)
        if e.get('Type') not in PLAYBACK_START_TYPES:
            continue
        item, uid = e.get('ItemId'), e.get('UserId')
        # Emby 4.x activity entries name the user by its short internal
        # number ("6"), not the 32-hex user Id. Protection only needs the
        # item; the user is resolved for display when it can be.
        if catalog_id(item) and isinstance(uid, str) and (HEX32.fullmatch(uid) or catalog_id(uid)):
            events.append((item, uid, ts, who(uid, e)))
            first_attributable = ts if first_attributable is None else min(first_attributable, ts)
        else:
            unattributable += 1
    item_ids = {item for item, _, _, _ in events}
    rows = _lookup(emby, resolver.routes, item_ids, 'ProviderIds,Path,SeriesId')
    by_user = {}
    for item, uid, _, _ in events:
        if item not in rows and uid in names:
            by_user.setdefault(uid, set()).add(item)
    for uid, ids in by_user.items():
        rows.update(_lookup(emby, [_user_route(emby, uid)], ids, 'ProviderIds,Path,SeriesId'))
    keys, _ = resolver.keys(list(rows.values()), required=False)
    plays, unmapped, unmapped_managed = {}, 0, 0
    for item, uid, ts, label in events:
        key = keys.get(item)
        if key is None:
            # Deleted or re-imported items: a file change also resets the
            # newest-file date, so an unmapped play cannot unprotect a title.
            unmapped += 1
            row = rows.get(item)
            if row is not None and resolver.index.managed_path(row.get('Path')):
                # The item still exists inside a managed library but names no
                # title: its play cannot be attributed, so the window is not
                # complete and played titles stay protected.
                unmapped_managed += 1
            continue
        plays.setdefault(key, []).append((ts, label))
    complete = (retention_since is not None and retention_since <= window_start
                and unattributable == 0 and unmapped_managed == 0)
    return {
        'plays': plays,
        'unmapped_managed': unmapped_managed,
        'coverage': {
            'source': 'emby_activity_log',
            'window_start': iso(window_start),
            'complete': complete,
            'retention_since': iso(retention_since) if retention_since is not None else None,
            'attributable_since': iso(first_attributable) if first_attributable is not None else None,
            'unattributable_in_window': unattributable,
            'unmapped_plays': unmapped,
        },
    }


def read_keep_journal(path, server):
    """Active HoloNet keep targets by contract library from the Go janitor journal.

    The prune unit receives a read-only snapshot through systemd LoadCredential;
    a manual root `plan` reads the configured journal directly. Any unreadable
    or malformed journal refuses, because absence would silently drop keeps.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise Refused(KEEP_FAILURE) from None
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= KEEP_JOURNAL_MAX_BYTES
             and not info.st_mode & 0o077, KEEP_FAILURE)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(KEEP_JOURNAL_MAX_BYTES + 1)
    finally:
        os.close(fd)
    try:
        state = strict_json(raw)
    except Exception:
        raise Refused(KEEP_FAILURE) from None
    need(type(state) is dict and state.get('version') == 1, KEEP_FAILURE)
    keeps = state.get('janitor_keeps', {})
    need(type(keeps) is dict and len(keeps) <= 4096, KEEP_FAILURE)
    out = {name: set() for name in LIBRARY_SPECS}
    for record in keeps.values():
        need(type(record) is dict and type(record.get('active')) is bool
             and isinstance(record.get('server'), str) and isinstance(record.get('library'), str)
             and isinstance(record.get('target'), str), KEEP_FAILURE)
        # A target that is not an Emby item ID cannot name any managed title.
        if (record['active'] and record['server'] == server and catalog_id(record['target'])
                and record['library'] in LIBRARY_BY_EMBY_ID):
            out[LIBRARY_BY_EMBY_ID[record['library']]].add(record['target'])
    return {name: sorted(ids) for name, ids in out.items()}


def keep_titles(emby, resolver, keep_ids, warnings):
    """Managed title keys protected by an active keep on any of their Emby items."""
    ids = {i for ids in keep_ids.values() for i in ids}
    if not ids:
        return set()
    rows = _lookup(emby, resolver.routes, ids, 'ProviderIds,Path,SeriesId')
    keys, unresolved = resolver.keys(list(rows.values()), required=False)
    missing = len(ids) - len(rows) + len(unresolved)
    if missing:
        warnings.append(f'{missing} keep targets no longer resolve to Emby items')
    return {k for k in keys.values() if k is not None}


def collect_emby(emby, index, *, now, window_days, keep_ids=None, touched=True):
    users = emby_users(emby)
    routes = [_user_route(emby, emby.actor_id)] + [_user_route(emby, u['id']) for u in users
                                                    if u['id'] != emby.actor_id]
    resolver = Resolver(emby, index, routes)
    favorites, played = collect_user_facts(emby, resolver, users, touched=touched)
    try:
        history = collect_history(emby, resolver, users, now=now, window_start=now - window_days * DAY)
    except Refused:
        raise Refused(HISTORY_FAILURE) from None
    warnings = []
    kept = keep_titles(emby, resolver, keep_ids or {}, warnings) if keep_ids is not None else set()
    return {'users': users, 'favorites': favorites, 'touched': played, 'history': history,
            'kept': kept, 'warnings': warnings}


# --- Eligibility -------------------------------------------------------------

def last_play(emby_facts, key):
    plays = emby_facts['history']['plays'].get(key)
    return max(ts for ts, _ in plays) if plays else None


def last_activity(title, emby_facts):
    """Ordering key: the later of the Arr added date and the last attributable play."""
    return max(title['added_ts'] or 0, last_play(emby_facts, (title['library'], title['arr_id'])) or 0)


def eligibility(title, facts, emby_facts, cfg, now, file_ts):
    """(eligible, reason). Cheap checks run first; file_ts(title) may fetch."""
    key = (title['library'], title['arr_id'])
    idle = cfg['min_idle_days'] * DAY
    if key in emby_facts['favorites']:
        return False, 'favorite'
    if title['protected_profile']:
        return False, 'favorite_profile'
    if key in emby_facts['kept']:
        return False, 'kept'
    if key in facts['queue']:
        return False, 'downloading'
    if title['size'] <= 0:
        return False, 'no_files'
    if title['added_ts'] is None or now - title['added_ts'] < idle:
        return False, 'recently_added'
    if key in emby_facts['history']['plays']:
        return False, 'watched'
    if not emby_facts['history']['coverage']['complete'] and key in emby_facts['touched']:
        # Played at some unknown time before attributable history began.
        return False, 'history_incomplete'
    newest = file_ts(title)
    if newest is None:
        return False, 'file_date_unknown'
    if now - newest < idle:
        return False, 'recent_file'
    return True, None


# --- State -------------------------------------------------------------------

def _valid_mark(m):
    return (type(m) is dict and set(m) == {
        'library', 'arr_id', 'kind', 'title', 'year', 'size_bytes', 'last_activity',
        'marked_at', 'remove_after'}
        and m['library'] in LIBRARY_SPECS and integer(m['arr_id'])
        and m['kind'] == LIBRARY_SPECS[m['library']]['kind']
        and isinstance(m['title'], str) and len(m['title']) <= 512
        and (m['year'] is None or integer(m['year'], 1800, 3000))
        and integer(m['size_bytes'], 0, 1 << 56)
        and all(parse_time(m[k]) is not None for k in ('last_activity', 'marked_at', 'remove_after')))


def load_marks(dir_fd):
    raw = _read_state_json(dir_fd, MARKS_FILE, STATE_MAX_BYTES)
    if raw is None:
        return {}
    need(type(raw) is dict and set(raw) == {'version', 'marks'} and raw['version'] == 1
         and type(raw['marks']) is list and len(raw['marks']) <= MAX_MARKS
         and all(_valid_mark(m) for m in raw['marks']), STATE_FAILURE)
    marks = {(m['library'], m['arr_id']): m for m in raw['marks']}
    need(len(marks) == len(raw['marks']), STATE_FAILURE)
    return marks


def save_marks(dir_fd, marks):
    rows = sorted(marks.values(), key=lambda m: (m['remove_after'], m['library'], m['arr_id']))
    need(len(rows) <= MAX_MARKS and all(_valid_mark(m) for m in rows), STATE_FAILURE)
    _write_state_json(dir_fd, MARKS_FILE, {'version': 1, 'marks': rows}, label='Prune')


def load_last_run(dir_fd):
    raw = _read_state_json(dir_fd, LAST_RUN_FILE, STATE_MAX_BYTES)
    need(raw is None or type(raw) is dict, STATE_FAILURE)
    return raw


class StateLock:
    """Exclusive non-blocking run lock inside the root prune state directory."""
    def __init__(self, dir_fd):
        self.fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                          dir_fd=dir_fd)
        info = os.fstat(self.fd)
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600):
            os.close(self.fd)
            raise Refused(STATE_FAILURE)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise PruneAbort('busy') from None

    def release(self):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


# --- Torrent gate ------------------------------------------------------------

def torrent_gate(rows, hashes):
    """(allowed, reasons) for retiring these torrents under the SeedPool policy.

    SeedPool torrents need the whole-batch retirement plan to pass (10 days
    seeded, the 20 TiB active floor still held). Other trackers need the same
    10-day minimum. Unknown trackers, or an unknown snapshot, keep seeding.
    """
    by_hash = {str(r.get('hash', '')).lower(): r for r in rows if type(r) is dict}
    reasons = {}
    seedpool = []
    for h in hashes:
        row = by_hash.get(h)
        if row is None:
            reasons['torrent_missing'] = reasons.get('torrent_missing', 0) + 1
            continue
        kind = tracker_kind(row)
        if kind == 'seedpool':
            seedpool.append(h)
        elif kind == 'other':
            seeded = row.get('seeding_time')
            if not (type(seeded) is int and seeded >= MINIMUM_SEED_SECONDS):
                reasons['minimum_seed_age_not_met'] = reasons.get('minimum_seed_age_not_met', 0) + 1
        else:
            reasons['tracker_unknown'] = reasons.get('tracker_unknown', 0) + 1
    if seedpool:
        plan = retirement_plan(rows, seedpool)
        for reason, count in plan['blocked_reasons'].items():
            reasons[reason] = reasons.get(reason, 0) + count
    return not reasons, dict(sorted(reasons.items()))


# --- Run ---------------------------------------------------------------------

def _public(title, emby_facts, extra=None):
    row = {
        'library': title['library'], 'arr_id': title['arr_id'], 'kind': title['kind'],
        'title': title['title'], 'year': title['year'], 'size_bytes': title['size'],
        'last_activity': iso(last_activity(title, emby_facts)),
    }
    row.update(extra or {})
    return row


def _checked_plan(response, title):
    """The whole-title plan from one library-remove preview, shape-checked."""
    need(type(response) is dict and isinstance(response.get('plan_sha256'), str)
         and type(response.get('plan')) is dict, FAILURE)
    plan = response['plan']
    need(plan.get('library') == title['library'] and plan.get('arr_id') == title['arr_id']
         and plan.get('delete_title') is True and integer(plan.get('total_bytes'), 0, MAX_BYTES)
         and type(plan.get('torrents')) is list
         and all(type(t) is dict and isinstance(t.get('hash'), str) for t in plan['torrents']), FAILURE)
    return plan


def _prepare_removal(backends, title, keep_ids):
    """Preview one whole-title removal and decide whether its torrents may go.

    library-remove deletes a torrent with either ownership proof; prune asks
    for more. Every torrent the preview would delete must carry the hardlink
    proof (the verifier ran and passed), and the SeedPool retirement plan must
    allow retiring them. Arr history alone never retires a seeding torrent
    here: the title is previewed again and removed with remove_torrents false.
    """
    base = {'library': title['library'], 'arr_id': title['arr_id'], 'target': 'title',
            'keep_emby_ids': list(keep_ids.get(title['library'], []))}
    preview = backends.remove(dict(base, mode='preview', remove_torrents=True))
    plan = _checked_plan(preview, title)
    wanted = [t for t in plan['torrents'] if t.get('action') == 'remove']
    gate_reasons, rows, with_torrents = {}, [], True
    if wanted:
        unproven = sum(1 for t in wanted if t.get('ownership_proof') != 'hardlink')
        if unproven:
            with_torrents, gate_reasons = False, {'ownership_not_hardlink': unproven}
        else:
            rows = backends.qbit_rows()
            with_torrents, gate_reasons = torrent_gate(rows, [t['hash'] for t in wanted])
        if not with_torrents:
            # The plan digest binds remove_torrents, so apply uses this preview.
            preview = backends.remove(dict(base, mode='preview', remove_torrents=False))
            plan = _checked_plan(preview, title)
    return {'base': base, 'plan': plan, 'plan_sha256': preview['plan_sha256'],
            'with_torrents': with_torrents, 'rows': rows, 'torrent_gate': gate_reasons}


def _apply_removal(backends, prepared):
    """Dispatch the previewed removal; library-remove refuses if the plan changed."""
    return backends.remove(dict(prepared['base'], mode='apply', remove_torrents=prepared['with_torrents'],
                                operation_id=secrets.token_hex(16), plan_sha256=prepared['plan_sha256']))


def _byte_count(value):
    return value if type(value) is int and 0 <= value < MAX_BYTES else 0


def _removal_result(backends, prepared, applied):
    """(result row, warning or None) for a dispatched removal.

    Never raises: the title is already gone, so an odd response or a failed
    torrent re-read only costs accuracy in the counts, never the receipt.
    """
    warning = None
    removed_bytes = prepared['plan']['total_bytes']
    removed_hashes, kept = [], set()
    try:
        plan = applied.get('plan')
        if type(plan) is dict and integer(plan.get('total_bytes'), 0, MAX_BYTES):
            removed_bytes = plan['total_bytes']
        removed_hashes = [h for h in applied.get('removed_torrent_hashes') or [] if isinstance(h, str)]
        kept = {h.lower() for h in applied.get('kept_torrent_hashes') or [] if isinstance(h, str)}
    except Exception:
        warning = 'removal response unreadable; torrent counts are unknown'
    held = 0
    if kept:
        try:
            rows = prepared['rows'] or backends.qbit_rows()
            held = sum(_byte_count(r.get('size')) for r in rows
                       if type(r) is dict and str(r.get('hash', '')).lower() in kept)
        except Exception:
            held = 0
            warning = 'torrent sizes unavailable after removal; bytes_held_by_torrents undercounts'
    return {
        'removed_bytes': removed_bytes,
        'torrents_removed': len(removed_hashes),
        'torrents_kept': len(kept),
        'bytes_held_by_torrents': held,
        'torrent_gate': prepared['torrent_gate'],
    }, warning


def recheck_protection(backends, block, title, facts, emby_facts, *, now, series_cache):
    """Fresh protections for one title, read immediately before its removal apply.

    Eligibility used start-of-run snapshots. A favourite by any user, a new
    play or resume state, a playback start since the run began, a queued
    download or a move to the protected quality profile since then must still
    save the title. Returns the protecting reason or None; read errors raise
    Refused. The keep list is checked again by library-remove itself.
    """
    key = (title['library'], title['arr_id'])
    api = backends.arr(block['libraries'][title['library']]['arr'])
    try:
        if title['kind'] == 'movie':
            raw = api.get(f"/api/v3/movie/{title['arr_id']}")
            flag, field = 'includeUnknownMovieItems=false', 'movieId'
        else:
            raw = api.get(f"/api/v3/series/{title['arr_id']}")
            flag, field = 'includeUnknownSeriesItems=false', 'seriesId'
        need(type(raw) is dict and raw.get('id') == title['arr_id'], FAILURE)
        profiles = api.get('/api/v3/qualityprofile')
        need(type(profiles) is list and all(type(p) is dict for p in profiles), FAILURE)
        protected = {p['id'] for p in profiles
                     if type(p.get('id')) is int and p.get('name') == PROTECTED_PROFILE}
        if raw.get('qualityProfileId') in protected:
            return 'favorite_profile'
        if title['arr_id'] in _arr_queue(api, flag, field):
            return 'downloading'
    finally:
        api.close()
    emby = backends.emby()
    try:
        users = emby_users(emby)
        routes = [_user_route(emby, emby.actor_id)] + [_user_route(emby, u['id']) for u in users
                                                        if u['id'] != emby.actor_id]
        resolver = Resolver(emby, TitleIndex(facts['titles'], block), routes)
        resolver.series = series_cache
        favorites, played = collect_user_facts(emby, resolver, users)
        if key in favorites:
            return 'favorite'
        recent = collect_history(emby, resolver, users, now=max(now, int(time.time())),
                                 window_start=now - RECHECK_MARGIN)
    finally:
        emby.close()
    if key in recent['plays'] or (key in played and key not in emby_facts['touched']):
        return 'watched'
    if recent['coverage']['unattributable_in_window'] or recent['unmapped_managed']:
        # A playback start since the run began that names no title could be this one.
        return 'history_incomplete'
    if key in played and not (emby_facts['history']['coverage']['complete']
                              and recent['coverage']['complete']):
        # A played or resumable title is removable only while both the
        # start-of-run history and the fresh recheck history are complete;
        # otherwise a new play or resume state could be invisible.
        return 'history_incomplete'
    return None

def prune(block, backends, *, mode, now, keep_ids, marks):
    """Evaluate and, in run mode, apply one prune pass. Returns (summary, marks).

    Raises PruneAbort when a read fails before the first removal. Every read
    the marking phase needs also happens before the first removal. After it,
    no failure escapes: the run ends as partial with every removed title in
    the summary and the surviving marks returned. In run mode the caller holds
    the state lock and persists marks/summary from the result.
    """
    cfg = prune_config(block)
    need(mode in ('plan', 'run'), 'Invalid prune mode')
    target = cfg['target_bytes']
    summary = {
        'mode': mode, 'outcome': 'ok', 'abort_reason': None, 'started_at': iso(now),
        'finished_at': None, 'target_bytes': target, 'library_bytes_before': None,
        'library_bytes_after': None, 'projected_bytes': None, 'history': None,
        'removed': [], 'remove_skipped': [], 'marked': [], 'unmarked': [],
        'pending_count': 0, 'removed_bytes': 0, 'bytes_held_by_torrents': 0, 'warnings': [],
    }
    marks = copy.deepcopy(marks)
    try:
        facts = collect_arr(block, backends.arr)
    except Refused:
        raise PruneAbort('arr_unavailable') from None
    emby = None
    try:
        emby = backends.emby()
        emby_facts = collect_emby(emby, TitleIndex(facts['titles'], block), now=now,
                                  window_days=cfg['min_idle_days'], keep_ids=keep_ids)
    except Refused as error:
        raise PruneAbort('history_unavailable' if str(error) == HISTORY_FAILURE
                         else 'emby_unavailable') from None
    finally:
        if emby is not None:
            emby.close()
    try:
        backends.qbit_rows()
    except Refused:
        raise PruneAbort('qbit_unavailable') from None
    summary['history'] = emby_facts['history']['coverage']
    summary['warnings'].extend(emby_facts['warnings'])
    titles = facts['titles']
    size = facts['library_bytes']
    summary['library_bytes_before'] = size
    dates = SeriesDates(backends.arr)

    def check(title):
        try:
            return eligibility(title, facts, emby_facts, cfg, now, dates)
        except Refused:
            raise PruneAbort('arr_unavailable') from None

    def order(title):
        return (last_activity(title, emby_facts), -title['size'], title['library'], title['arr_id'])

    # Re-check every pending mark against this run's facts.
    for key, mark in sorted(marks.items()):
        title = titles.get(key)
        if title is None:
            summary['unmarked'].append(dict(mark, reason='gone'))
            del marks[key]
            continue
        ok, reason = check(title)
        if not ok:
            summary['unmarked'].append(dict(mark, reason=reason))
            del marks[key]
        else:
            mark.update(size_bytes=title['size'], title=title['title'],
                        last_activity=iso(last_activity(title, emby_facts)))

    # Evaluate marking candidates now, before anything is removed, so no lazy
    # Sonarr read can fail after a removal. The marking walk below follows the
    # same order and skip rules; it skips exactly the keys marked here (removed
    # or refused marks were all marked), and stops by the time new marks cover
    # size - target, since removals only lower the size. So it never reaches a
    # title past this simulation.
    verdicts = {}
    shortfall = size - target
    sim_marked = sim_bytes = 0
    for title in sorted(titles.values(), key=order):
        key = (title['library'], title['arr_id'])
        if shortfall <= 0 or sim_marked >= cfg['max_titles_per_run'] or sim_bytes >= shortfall:
            break
        if key in marks or sim_bytes + title['size'] > cfg['max_bytes_per_run']:
            continue
        verdicts[key], _ = check(title)
        if verdicts[key]:
            sim_marked += 1
            sim_bytes += title['size']
    if dates.fetched >= dates.limit:
        summary['warnings'].append('series file-date lookups reached the per-run limit')

    removed_titles = removed_bytes = 0
    settled = set()  # removed, refused as kept or protected here: never re-marked in this run
    series_cache = {}
    in_flight = None

    def stop(reason):
        summary.update(outcome='partial' if removed_titles else 'aborted', abort_reason=reason)

    try:
        # Apply: due marks, oldest activity first, while the library is over target.
        due = sorted((titles[k] for k, m in marks.items() if parse_time(m['remove_after']) <= now), key=order)
        for title in due:
            key = (title['library'], title['arr_id'])
            in_flight = None
            if size <= target:
                break
            if removed_titles >= cfg['max_titles_per_run']:
                break
            if mode == 'plan':
                if removed_bytes + title['size'] > cfg['max_bytes_per_run']:
                    summary['remove_skipped'].append(_public(title, emby_facts, {'reason': 'run_byte_cap'}))
                    continue
                result = {'removed_bytes': title['size'], 'planned': True}
            else:
                try:
                    prepared = _prepare_removal(backends, title, keep_ids)
                    plan_bytes = prepared['plan']['total_bytes']
                    # The cap counts what this removal deletes now, not the snapshot size.
                    if removed_bytes + plan_bytes > cfg['max_bytes_per_run']:
                        summary['remove_skipped'].append(_public(title, emby_facts, {
                            'size_bytes': plan_bytes, 'reason': 'run_byte_cap'}))
                        continue
                    try:
                        protection = recheck_protection(backends, block, title, facts, emby_facts,
                                                        now=now, series_cache=series_cache)
                    except Refused:
                        stop('recheck_unavailable')
                        break
                    if protection is not None:
                        settled.add(key)
                        summary['unmarked'].append(dict(marks.pop(key), reason=protection))
                        continue
                    in_flight = title
                    applied = _apply_removal(backends, prepared)
                except LibraryRefusal as error:
                    in_flight = None
                    if error.reason == 'keep_listed':
                        settled.add(key)
                        summary['unmarked'].append(dict(marks.pop(key), reason='kept'))
                    else:
                        summary['remove_skipped'].append(_public(title, emby_facts, {'reason': error.reason}))
                    continue
                except LibraryPostDispatchError as error:
                    in_flight = None
                    summary.update(outcome='partial', abort_reason='post_dispatch_' + error.code)
                    summary['remove_skipped'].append(_public(title, emby_facts, {'reason': error.code}))
                    break
                except Refused:
                    in_flight = None
                    stop('backend_error')
                    break
                result, warning = _removal_result(backends, prepared, applied)
                if warning is not None:
                    summary['warnings'].append(warning)
            summary['removed'].append(_public(title, emby_facts, dict(result, size_bytes=result['removed_bytes'])))
            in_flight = None
            settled.add(key)
            marks.pop(key, None)
            removed_titles += 1
            removed_bytes += result['removed_bytes']
            size -= result['removed_bytes']
            summary['bytes_held_by_torrents'] += result.get('bytes_held_by_torrents', 0)

        # Mark: only after a clean apply phase, until the projection meets the target.
        if summary['outcome'] == 'ok':
            if size <= target:
                for key in sorted(marks):
                    summary['unmarked'].append(dict(marks.pop(key), reason='under_target'))
            projected = size - sum(m['size_bytes'] for m in marks.values())
            marked = marked_bytes = 0
            if projected > target:
                for title in sorted(titles.values(), key=order):
                    key = (title['library'], title['arr_id'])
                    if projected <= target or marked >= cfg['max_titles_per_run'] or len(marks) >= MAX_MARKS:
                        break
                    if (key in marks or key in settled
                            or marked_bytes + title['size'] > cfg['max_bytes_per_run']):
                        continue
                    if not verdicts.get(key, False):
                        continue
                    row = _public(title, emby_facts, {
                        'marked_at': iso(now), 'remove_after': iso(now + cfg['grace_days'] * DAY)})
                    marks[key] = {k: row[k] for k in (
                        'library', 'arr_id', 'kind', 'title', 'year', 'size_bytes', 'last_activity',
                        'marked_at', 'remove_after')}
                    summary['marked'].append(row)
                    marked += 1
                    marked_bytes += title['size']
                    projected -= title['size']
    except Exception:
        # Anything unexpected after collection (a malformed backend answer, a
        # bug): keep the receipt. A removal in flight may have been dispatched.
        if in_flight is not None:
            summary['remove_skipped'].append(_public(in_flight, emby_facts, {'reason': 'uncertain'}))
            summary.update(outcome='partial', abort_reason='unexpected')
        else:
            stop('unexpected')
    summary['removed_bytes'] = removed_bytes
    summary['projected_bytes'] = size - sum(m['size_bytes'] for m in marks.values())
    summary['library_bytes_after'] = size
    summary['pending_count'] = len(marks)
    summary['finished_at'] = iso(time.time())
    # removed stays whole (at most max_titles_per_run rows): it is the receipt.
    for key in ('remove_skipped', 'marked', 'unmarked'):
        summary[key] = summary[key][:MAX_SUMMARY_ROWS]
    summary['warnings'] = summary['warnings'][:MAX_WARNINGS]
    return summary, marks

def event(summary):
    """One structured line for journald/Loki, counts only (run or plan summary)."""
    removed = summary['removed'] if 'removed' in summary else summary.get('would_remove', [])
    marked = summary['marked'] if 'marked' in summary else summary.get('would_mark', [])
    return json.dumps({
        'event': 'holonet_library_prune', 'mode': summary['mode'], 'outcome': summary['outcome'],
        'abort_reason': summary['abort_reason'], 'target_bytes': summary['target_bytes'],
        'library_bytes': summary['library_bytes_after'], 'projected_bytes': summary['projected_bytes'],
        'removed_count': len(removed), 'removed_bytes': summary['removed_bytes'],
        'bytes_held_by_torrents': summary['bytes_held_by_torrents'],
        'marked_count': len(marked), 'unmarked_count': len(summary['unmarked']),
        'skipped_count': len(summary['remove_skipped']), 'pending_count': summary['pending_count'],
        'history_complete': (summary['history'] or {}).get('complete'),
    }, sort_keys=True, separators=(',', ':'))

def aborted_summary(mode, now, target, reason):
    return {
        'mode': mode, 'outcome': 'aborted', 'abort_reason': reason, 'started_at': iso(now),
        'finished_at': iso(time.time()), 'target_bytes': target, 'library_bytes_before': None,
        'library_bytes_after': None, 'projected_bytes': None, 'history': None, 'removed': [],
        'remove_skipped': [], 'marked': [], 'unmarked': [], 'pending_count': None,
        'removed_bytes': 0, 'bytes_held_by_torrents': 0, 'warnings': [],
    }


def _persist_run(dir_fd, marks, summary):
    """Write marks, then the last-run record.

    Once titles were removed, a failed write does not drop the receipt: the
    run becomes partial (state_write_failed) and the summary is still returned
    for the journal event. Without removals the failure raises as before.
    """
    for write in (lambda: save_marks(dir_fd, marks),
                  lambda: _write_state_json(dir_fd, LAST_RUN_FILE, summary, label='Prune')):
        try:
            write()
        except Refused:
            if not summary['removed']:
                raise
            summary.update(outcome='partial', abort_reason='state_write_failed')


def run_locked(block, backends, *, mode, now, keep_ids):
    """State custody around prune(): lock, then atomic marks and last-run writes.

    A plan never writes. A run that aborts before its first removal keeps its
    marks unchanged and records the abort as the last run; prune() itself
    turns any later failure into a partial summary that is persisted here.
    """
    cfg = prune_config(block)
    dir_fd = _open_state_dir(cfg['state_dir'], label='Prune')
    lock = None
    try:
        if mode == 'run':
            try:
                lock = StateLock(dir_fd)
            except PruneAbort as error:
                # Another run owns the state; record nothing over its work.
                return aborted_summary(mode, now, cfg['target_bytes'], error.reason)
            if not cfg['enabled']:
                summary = aborted_summary(mode, now, cfg['target_bytes'], None)
                summary['outcome'] = 'disabled'
                _write_state_json(dir_fd, LAST_RUN_FILE, summary, label='Prune')
                return summary
        try:
            try:
                marks = load_marks(dir_fd)
            except Refused:
                raise PruneAbort('state_invalid') from None
            summary, marks = prune(block, backends, mode=mode, now=now, keep_ids=keep_ids, marks=marks)
        except Exception as error:
            # PruneAbort, or anything raised before prune() reached its first
            # removal: the marks stay as they were.
            reason = error.reason if isinstance(error, PruneAbort) else 'unexpected'
            summary = aborted_summary(mode, now, cfg['target_bytes'], reason)
            if mode == 'run':
                _write_state_json(dir_fd, LAST_RUN_FILE, summary, label='Prune')
            return summary
        if mode == 'run':
            _persist_run(dir_fd, marks, summary)
        else:
            summary['would_mark'] = summary.pop('marked')
            summary['would_remove'] = summary.pop('removed')
        return summary
    finally:
        if lock is not None:
            lock.release()
        os.close(dir_fd)

# --- Read-only socket operations ---------------------------------------------

def _arr_from_factory(block, arr_factory, budget):
    descs = {'radarr': block['libraries']['movies']['descriptor'],
             'sonarr': block['libraries']['tv']['descriptor']}
    return lambda backend: arr_factory(backend, descs[backend], budget)


def op_prune_status(call, block, arr_factory):
    cfg = prune_config(block)
    dir_fd = _open_state_dir(cfg['state_dir'], label='Prune')
    try:
        try:
            marks = load_marks(dir_fd)
            last_run = load_last_run(dir_fd)
        except Refused:
            refuse('prune_state_invalid', 'Prune state is unreadable or invalid')
    finally:
        os.close(dir_fd)
    facts = collect_arr(block, _arr_from_factory(block, arr_factory, SECONDS['library-prune-status']),
                        with_protection=False)
    pending = sorted(marks.values(), key=lambda m: (m['remove_after'], m['library'], m['arr_id']))
    pending_bytes = sum(m['size_bytes'] for m in pending)
    return {
        'enabled': cfg['enabled'],
        'target_bytes': cfg['target_bytes'],
        'library_bytes': facts['library_bytes'],
        'by_library': facts['by_library'],
        'over_target_bytes': max(0, facts['library_bytes'] - cfg['target_bytes']),
        'projected_bytes': facts['library_bytes'] - pending_bytes,
        'rules': {
            'min_idle_days': cfg['min_idle_days'], 'grace_days': cfg['grace_days'],
            'max_bytes_per_run': cfg['max_bytes_per_run'],
            'max_titles_per_run': cfg['max_titles_per_run'],
        },
        'pending': [dict(m) for m in pending],
        'pending_count': len(pending),
        'pending_bytes': pending_bytes,
        'last_run': last_run,
        'protection': PROTECTION_NOTE,
    }


def op_watch_history(call, block, arr_factory, emby_factory, *, now=None):
    req = call['request']
    days = req.get('days', WATCH_DEFAULT_DAYS)
    limit = req.get('limit', WATCH_DEFAULT_LIMIT)
    now = int(time.time()) if now is None else now
    facts = collect_arr(block, _arr_from_factory(block, arr_factory, SECONDS['library-watch-history']),
                        with_protection=False)
    emby = emby_factory()
    need(emby is not None, FAILURE)
    emby_facts = collect_emby(emby, TitleIndex(facts['titles'], block), now=now, window_days=days,
                              touched=False)
    titles = facts['titles']

    def ids(t):
        return {'library': t['library'], 'arr_id': t['arr_id'], 'kind': t['kind'],
                'title': t['title'], 'year': t['year'], 'tmdb_id': t['tmdb_id'],
                'tvdb_id': t['tvdb_id'], 'genres': list(t['genres'])}

    played = []
    for key, plays in emby_facts['history']['plays'].items():
        users = sorted({u for _, u in plays})
        played.append(dict(ids(titles[key]), play_count=len(plays), user_count=len(users),
                           users=users[:MAX_USERS_PER_ROW],
                           last_played=iso(max(ts for ts, _ in plays))))
    played.sort(key=lambda r: (r['last_played'], r['play_count']), reverse=True)
    favorites = [dict(ids(titles[key]), users=sorted(users)[:MAX_USERS_PER_ROW])
                 for key, users in emby_facts['favorites'].items()]
    favorites.sort(key=lambda r: (r['library'], r['title'].lower(), r['arr_id']))
    coverage = dict(emby_facts['history']['coverage'])
    coverage.pop('window_start')
    return {
        'days': days,
        'since': iso(now - days * DAY),
        'items': played[:limit],
        'items_total': len(played),
        'truncated': len(played) > limit,
        'favorites': favorites[:MAX_FAVORITES],
        'favorites_total': len(favorites),
        'history': coverage,
        'user_count': len(emby_facts['users']),
    }


# --- Root timer entry --------------------------------------------------------

class Backends:
    """Fresh bounded production clients per stage; tests substitute fakes."""
    def __init__(self, block, emby_config, recipient, guard):
        self.block, self.emby_config, self.recipient, self.guard = block, emby_config, recipient, guard
        self.descs = {'radarr': block['libraries']['movies']['descriptor'],
                      'sonarr': block['libraries']['tv']['descriptor']}

    def _descriptor(self, backend, path):
        desc = strict_json(private_read(path))
        need(type(desc) is dict and desc.get('backend') == backend, FAILURE)
        return desc

    def arr(self, backend):
        return LibraryArrClient(self._descriptor(backend, self.descs[backend]),
                                request_guard=self.guard, budget_seconds=ARR_BUDGET)

    def emby(self):
        client = LibraryEmbyClient(self.emby_config, request_guard=self.guard)
        creds = load_credentials(self.emby_config['credential_file'])
        try:
            client.authenticate(creds)
        finally:
            creds.clear()
        return client

    def qbit_rows(self):
        client = LibraryTorrentClient(torrent_descriptor(strict_json(private_read(self.block['torrents']))),
                                      request_guard=self.guard, budget_seconds=60)
        try:
            client.login()
            rows = client.info_all()
        except Refused:
            raise
        except Exception:
            raise Refused(QBIT_FAILURE) from None
        finally:
            client.logout()
        return rows

    def remove(self, request):
        """One library-remove call with fresh clients, exactly as the socket runs it."""
        arr_clients, emby_client = {}, [None]

        def arr_factory(backend, desc_path, budget):
            if desc_path not in arr_clients:
                arr_clients[desc_path] = LibraryArrClient(
                    self._descriptor(backend, desc_path), request_guard=self.guard, budget_seconds=budget)
            return arr_clients[desc_path]

        def emby_factory():
            if emby_client[0] is None:
                emby_client[0] = self.emby()
            return emby_client[0]

        def qbit_factory(desc_path):
            return LibraryTorrentClient(torrent_descriptor(strict_json(private_read(desc_path))),
                                        request_guard=self.guard, budget_seconds=30)

        try:
            return op_remove({'recipient': self.recipient, 'request': request}, self.block,
                             arr_factory, qbit_factory, emby_factory)
        finally:
            for client in arr_clients.values():
                client.close()
            if emby_client[0] is not None:
                emby_client[0].close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (['run'], ['plan']):
        print('usage: emby_operator_library_prune.py run|plan', file=sys.stderr)
        return 2
    mode = argv[0]
    from emby_operator_library import validate_block
    from emby_operator_socket import load_policy, selected_config, WireError
    now = int(time.time())
    deadline = time.monotonic() + 3000

    def guard():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Refused('Caller deadline exceeded')
        return remaining

    target = None
    try:
        policy = load_policy()
        block = validate_block(policy['library'])
        need(block['enabled'], 'Library management disabled')
        cfg = prune_config(block)
        target = cfg['target_bytes']
        recipient = sorted(policy['recipients'])[0]
        config = selected_config(policy, recipient)
        creds_dir = os.environ.get('CREDENTIALS_DIRECTORY')
        keep_path = (os.path.join(creds_dir, KEEP_CREDENTIAL) if creds_dir else cfg['keep_journal'])
        try:
            keep_ids = read_keep_journal(keep_path, policy['caller']['server'])
        except Refused:
            summary = aborted_summary(mode, now, target, 'keep_unavailable')
        else:
            summary = run_locked(block, Backends(block, config, recipient, guard), mode=mode, now=now,
                                 keep_ids=keep_ids)
    except (Refused, WireError):
        summary = aborted_summary(mode, now, target, 'config_unavailable')
    except Exception:
        summary = aborted_summary(mode, now, target, 'unexpected')
    if mode == 'plan':
        print(json.dumps(summary, indent=2, sort_keys=True))
    print(event(summary), flush=True)
    return 0 if summary['outcome'] in ('ok', 'disabled') else 1


if __name__ == '__main__':
    sys.exit(main())
