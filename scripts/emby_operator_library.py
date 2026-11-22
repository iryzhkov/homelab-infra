"""HoloNet library management v1 worker operations (Sonarr, Radarr, qBittorrent, Emby).

Stateless root worker side; Go owns the keep list, 30-minute release search cache,
and write-ahead intent journal. All effects happen through existing Arr/qBit/Emby
HTTP APIs; no direct filesystem writes or new credentials.
"""
import copy
import hashlib
import http.client
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from emby_operator_adapter import (
    ImporterInventoryAdapter,
    OperatorAdapter,
    catalog_id,
    importer_key,
    importer_response_bytes,
    load_credentials,
    printable,
    private_read,
    strict_json,
)
from emby_operator_recommendations import Refused
from emby_operator_torrents import TorrentAdapter, descriptor as torrent_descriptor, json_body, path as safe_path, within

READ_ACTION = 'library.read'
MANAGE_ACTION = 'library.manage'
ALLOWED_ACTIONS = frozenset({READ_ACTION, MANAGE_ACTION})
NAMESPACE = 'managed-library'

READ_OPERATIONS = (
    'library-lookup',
    'library-detail',
    'library-releases',
    'library-queue',
    'library-subtitles',
)
MANAGE_OPERATIONS = (
    'library-add',
    'library-grab',
    'library-remove',
)
OPERATIONS = READ_OPERATIONS + MANAGE_OPERATIONS

SECONDS = {
    'library-lookup': 30,
    'library-detail': 30,
    'library-releases': 180,
    'library-queue': 20,
    'library-subtitles': 120,
    'library-add': 60,
    'library-grab': 60,
    'library-remove': 120,
}

LIBRARY_SPECS = {
    'movies': {'library_id': '3', 'kind': 'movie', 'arr': 'radarr'},
    'tv': {'library_id': '1132', 'kind': 'series', 'arr': 'sonarr'},
    'anime': {'library_id': '615', 'kind': 'series', 'arr': 'sonarr'},
}
MONITOR_MODES = frozenset({'all', 'future', 'missing', 'none'})
IMAGE_SUBTITLE_CODECS = frozenset({
    'pgssub',
    'hdmv_pgs_subtitle',
    'dvdsub',
    'dvd_subtitle',
    'vobsub',
    'dvbsub',
    'dvb_subtitle',
    'xsub',
    'pgs',
})
HEX32 = re.compile(r'[0-9a-f]{32}')
HEX40 = re.compile(r'[0-9a-f]{40}')
HEX64 = re.compile(r'[0-9a-f]{64}')
FAILURE = 'Library backend unavailable'


class LibraryRefusal(Refused):
    """Domain refusal with a stable machine-readable reason code."""
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def need(condition, message=FAILURE):
    if not condition:
        raise Refused(message)


def refuse(reason, message):
    raise LibraryRefusal(reason, message)


def integer(value, low=1, high=2 ** 31 - 1):
    return type(value) is int and low <= value <= high


def number(value, low=0.0, high=1e12):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def clean_text(value, limit):
    return (
        printable(value, limit)
        and not any(tok in value.lower() for tok in ('apikey=', 'password='))
    )


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def plan_digest(plan):
    return hashlib.sha256(canonical_json(plan)).hexdigest()


def release_handle(library, arr_id, indexer_id, guid, title, size):
    material = [library, arr_id, indexer_id, guid, title, size]
    return hashlib.sha256(canonical_json(material)).hexdigest()


def required_action(operation):
    return MANAGE_ACTION if operation in MANAGE_OPERATIONS else READ_ACTION


def validate_block(value):
    need(type(value) is dict and set(value) == {
        'enabled', 'target_library', 'revision', 'libraries',
        'torrents', 'allowed_categories', 'removal_thresholds', 'limits',
    }, 'Invalid library policy')
    need(type(value['enabled']) is bool, 'Invalid library policy')
    need(catalog_id(value['target_library']) and isinstance(value['revision'], str)
         and re.fullmatch(r'[0-9a-f]{40,64}', value['revision']), 'Invalid library policy')
    libs = value['libraries']
    need(type(libs) is dict and set(libs) == set(LIBRARY_SPECS), 'Invalid library policy')
    for name, spec in LIBRARY_SPECS.items():
        entry = libs[name]
        need(type(entry) is dict and set(entry) == {
            'library_id', 'kind', 'arr', 'descriptor', 'root_folder_path', 'quality_profile_id',
        }, 'Invalid library policy')
        need(entry['library_id'] == spec['library_id']
             and entry['kind'] == spec['kind']
             and entry['arr'] == spec['arr'], 'Invalid library policy')
        safe_path(entry['descriptor'])
        safe_path(entry['root_folder_path'])
        need(integer(entry['quality_profile_id'], 1, 2 ** 31 - 1), 'Invalid library policy')
    safe_path(value['torrents'])
    cats = value['allowed_categories']
    need(type(cats) is list and 1 <= len(cats) <= 16
         and all(clean_text(c, 64) for c in cats)
         and len(set(cats)) == len(cats), 'Invalid library policy')
    thresh = value['removal_thresholds']
    need(type(thresh) is dict and set(thresh) == {'min_ratio', 'min_seeding_days'}
         and number(thresh['min_ratio'], 0.0, 1000.0)
         and integer(thresh['min_seeding_days'], 0, 3650), 'Invalid library policy')
    limits = value['limits']
    need(type(limits) is dict and set(limits) == {
        'lookup_max_rows', 'releases_max_rows', 'subtitles_default_chars',
        'subtitles_max_chars', 'remove_max_files',
    }, 'Invalid library policy')
    need(integer(limits['lookup_max_rows'], 1, 25)
         and integer(limits['releases_max_rows'], 1, 50)
         and integer(limits['subtitles_default_chars'], 1, 60000)
         and integer(limits['subtitles_max_chars'], 1, 60000)
         and limits['subtitles_default_chars'] <= limits['subtitles_max_chars']
         and integer(limits['remove_max_files'], 1, 500), 'Invalid library policy')
    return copy.deepcopy(value)


def _check_library_name(value):
    need(isinstance(value, str) and value in LIBRARY_SPECS, 'Invalid library')


def request(operation, value):
    need(operation in OPERATIONS and type(value) is dict, 'Invalid library request')
    fields = set(value)
    if operation == 'library-lookup':
        need({'library'} <= fields <= {'library', 'term', 'arr_id'}, 'Invalid library request')
        _check_library_name(value['library'])
        if 'term' in value:
            need(clean_text(value['term'], 256), 'Invalid library request')
        if 'arr_id' in value:
            need(integer(value['arr_id']), 'Invalid library request')
    elif operation == 'library-detail':
        need(fields == {'library', 'arr_id'}, 'Invalid library request')
        _check_library_name(value['library'])
        need(integer(value['arr_id']), 'Invalid library request')
    elif operation == 'library-releases':
        need({'library', 'arr_id'} <= fields <= {'library', 'arr_id', 'season', 'episode_arr_id'},
             'Invalid library request')
        _check_library_name(value['library'])
        need(integer(value['arr_id']), 'Invalid library request')
        if value['library'] == 'movies':
            need('season' not in value and 'episode_arr_id' not in value, 'Invalid library request')
        else:
            need(('season' in value) != ('episode_arr_id' in value), 'Invalid library request')
            if 'season' in value:
                need(integer(value['season'], 0, 999), 'Invalid library request')
            if 'episode_arr_id' in value:
                need(integer(value['episode_arr_id']), 'Invalid library request')
    elif operation == 'library-queue':
        need(fields <= {'library'}, 'Invalid library request')
        if 'library' in value:
            _check_library_name(value['library'])
    elif operation == 'library-subtitles':
        need({'library', 'arr_id'} <= fields <= {
            'library', 'arr_id', 'episode_arr_id', 'emby_item_id',
            'stream_index', 'start_seconds', 'end_seconds', 'max_chars',
        }, 'Invalid library request')
        _check_library_name(value['library'])
        need(integer(value['arr_id']), 'Invalid library request')
        if 'episode_arr_id' in value:
            need(value['library'] != 'movies' and integer(value['episode_arr_id']),
                 'Invalid library request')
        if 'emby_item_id' in value:
            need(catalog_id(value['emby_item_id']), 'Invalid library request')
        if 'stream_index' in value:
            need(integer(value['stream_index'], 0, 1024), 'Invalid library request')
        if 'start_seconds' in value:
            need(number(value['start_seconds'], 0.0, 86400.0), 'Invalid library request')
        if 'end_seconds' in value:
            need(number(value['end_seconds'], 0.0, 86400.0), 'Invalid library request')
        if 'start_seconds' in value and 'end_seconds' in value:
            need(value['end_seconds'] >= value['start_seconds'], 'Invalid library request')
        if 'max_chars' in value:
            need(integer(value['max_chars'], 1, 60000), 'Invalid library request')
    elif operation == 'library-add':
        mode = value.get('mode')
        need(mode in ('preview', 'apply'), 'Invalid library request')
        base_req = {'mode', 'library', 'monitor', 'search_now'}
        id_key = 'tmdb_id' if value.get('library') == 'movies' else 'tvdb_id'
        base_req.add(id_key)
        opt = {'seasons'} if value.get('library') in ('tv', 'anime') else set()
        if mode == 'apply':
            base_req |= {'operation_id', 'plan_sha256'}
        need(base_req <= fields <= base_req | opt, 'Invalid library request')
        _check_library_name(value['library'])
        need(integer(value[id_key]), 'Invalid library request')
        need(value['monitor'] in MONITOR_MODES, 'Invalid library request')
        need(type(value['search_now']) is bool, 'Invalid library request')
        if 'seasons' in value:
            seasons = value['seasons']
            need(type(seasons) is list and len(seasons) <= 100
                 and all(integer(s, 0, 999) for s in seasons)
                 and len(set(seasons)) == len(seasons), 'Invalid library request')
    elif operation == 'library-grab':
        mode = value.get('mode')
        need(mode in ('preview', 'apply'), 'Invalid library request')
        base_req = {
            'mode', 'library', 'arr_id', 'search_id', 'handle',
            'guid', 'indexer_id', 'title', 'indexer', 'size', 'approved', 'rejections',
        }
        opt = {'allow_rejected'}
        if mode == 'apply':
            base_req |= {'operation_id', 'plan_sha256'}
        need(base_req <= fields <= base_req | opt, 'Invalid library request')
        _check_library_name(value['library'])
        need(integer(value['arr_id']), 'Invalid library request')
        need(isinstance(value['search_id'], str) and HEX32.fullmatch(value['search_id']),
             'Invalid library request')
        need(isinstance(value['handle'], str) and HEX64.fullmatch(value['handle']),
             'Invalid library request')
        need(clean_text(value['guid'], 4096) and integer(value['indexer_id'])
             and clean_text(value['title'], 512) and clean_text(value['indexer'], 128)
             and integer(value['size'], 0, 1 << 50)
             and type(value['approved']) is bool
             and type(value['rejections']) is list and len(value['rejections']) <= 64
             and all(clean_text(r, 512) for r in value['rejections']),
             'Invalid library request')
        if 'allow_rejected' in value:
            need(type(value['allow_rejected']) is bool, 'Invalid library request')
        expected_handle = release_handle(
            value['library'], value['arr_id'], value['indexer_id'],
            value['guid'], value['title'], value['size'],
        )
        need(value['handle'] == expected_handle, 'Release handle mismatch')
    elif operation == 'library-remove':
        mode = value.get('mode')
        need(mode in ('preview', 'apply'), 'Invalid library request')
        base_req = {'mode', 'library', 'arr_id', 'target', 'remove_torrents'}
        if mode == 'apply':
            base_req |= {'operation_id', 'plan_sha256'}
        need(fields == base_req, 'Invalid library request')
        _check_library_name(value['library'])
        need(integer(value['arr_id']), 'Invalid library request')
        need(type(value['remove_torrents']) is bool, 'Invalid library request')
        t = value['target']
        if t == 'title':
            pass
        elif type(t) is dict and set(t) == {'season'}:
            need(value['library'] != 'movies' and integer(t['season'], 0, 999),
                 'Invalid library request')
        elif type(t) is dict and set(t) == {'episode_arr_ids'}:
            ids = t['episode_arr_ids']
            need(value['library'] != 'movies' and type(ids) is list
                 and 1 <= len(ids) <= 500
                 and all(integer(i) for i in ids) and len(set(ids)) == len(ids),
                 'Invalid library request')
        else:
            need(False, 'Invalid library request')

    if 'operation_id' in value:
        need(isinstance(value['operation_id'], str) and HEX32.fullmatch(value['operation_id']),
             'Invalid library request')
    if 'plan_sha256' in value:
        need(isinstance(value['plan_sha256'], str) and HEX64.fullmatch(value['plan_sha256']),
             'Invalid library request')
    return copy.deepcopy(value)


class LibraryArrClient(ImporterInventoryAdapter):
    """Bounded Sonarr/Radarr v3 client reusing ImporterInventoryAdapter custody."""

    def __init__(self, config, *, request_guard, budget_seconds=60):
        cfg = copy.deepcopy(config)
        cfg.pop('acquire_download_origins', None)
        group = cfg.pop('app_config_read_gid', None)
        super().__init__(cfg, request_guard=request_guard)
        if group is not None:
            need(type(group) is int and group == 988 and cfg['app_config_owner_uid'] == 1002,
                 'Invalid Arr descriptor')
            self._config['app_config_read_gid'] = group
        self._deadline = time.monotonic() + min(budget_seconds, request_guard())
        self._key = importer_key(self._config)
        self._verified = False

    def _call_limit(self):
        return 520

    def _response_limit(self, route):
        if route.startswith('/api/v3/release'):
            return 4 * 1024 * 1024
        if route.startswith(('/api/v3/movie', '/api/v3/series', '/api/v3/episode', '/api/v3/history')):
            return 4 * 1024 * 1024
        return 512 * 1024

    def close(self):
        self._key = None

    def _request(self, method, route, body=None, *, accept_statuses=(200,)):
        need(method in ('GET', 'POST', 'PUT', 'DELETE') and route.startswith('/api/v3/')
             and self._calls < self._call_limit() and self._key is not None, FAILURE)
        remaining = min(self._deadline - time.monotonic(), self._guard())
        need(remaining > 0, 'Caller deadline exceeded')
        self._calls += 1
        raw_body = (
            json.dumps(body, separators=(',', ':'), allow_nan=False).encode('utf-8')
            if body is not None else None
        )
        headers = {'Accept': 'application/json', 'X-Api-Key': self._key}
        if raw_body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(
            self._config['origin'].rstrip('/') + route,
            data=raw_body,
            headers=headers,
            method=method,
        )
        import queue
        import threading
        completed = queue.Queue(maxsize=1)
        opener = self._opener
        limit = self._response_limit(route)

        def io():
            try:
                with opener.open(req, timeout=remaining) as resp:
                    status = resp.status
                    data = importer_response_bytes(resp, limit=limit)
                completed.put((status, data, False))
            except urllib.error.HTTPError as err:
                try:
                    data = err.read(65536)
                except Exception:
                    data = b''
                completed.put((err.code, data, False))
            except Exception:
                completed.put((None, b'', True))

        threading.Thread(target=io, daemon=True).start()
        try:
            status, data, failed = completed.get(timeout=remaining)
        except queue.Empty:
            raise Refused('Caller deadline exceeded') from None
        need(not failed, FAILURE)
        need(time.monotonic() < self._deadline and self._guard() > 0, 'Caller deadline exceeded')
        if status not in accept_statuses:
            return status, data
        if not data:
            return status, None
        return status, strict_json(data)

    def verify_version(self):
        if self._verified:
            return
        status, data = self._request('GET', '/api/v3/system/status')
        need(status == 200 and type(data) is dict
             and data.get('version') == self._config['expected_version'], FAILURE)
        self._verified = True

    def get(self, route):
        self.verify_version()
        status, data = self._request('GET', route, accept_statuses=(200,))
        need(status == 200, FAILURE)
        return data

    def post(self, route, body, *, accept_statuses=(200, 201)):
        self.verify_version()
        return self._request('POST', route, body, accept_statuses=accept_statuses)

    def put(self, route, body, *, accept_statuses=(200, 202)):
        self.verify_version()
        return self._request('PUT', route, body, accept_statuses=accept_statuses)

    def delete(self, route, *, accept_statuses=(200, 204)):
        self.verify_version()
        return self._request('DELETE', route, accept_statuses=accept_statuses)


class LibraryTorrentClient(TorrentAdapter):
    """Bounded qBittorrent v5.3.0 client for library-remove info/delete."""

    def __init__(self, config, *, request_guard, budget_seconds=30):
        super().__init__(config, request_guard=request_guard)
        self.deadline = time.monotonic() + min(budget_seconds, request_guard())
        self._extra_routes = set()

    def call_limit(self):
        return 16

    def allowed_exchanges(self):
        return {
            ('POST', '/api/v2/auth/login'),
            ('POST', '/api/v2/auth/logout'),
            ('GET', '/api/v2/app/version'),
            ('POST', '/api/v2/torrents/delete'),
        } | self._extra_routes

    def login(self):
        from http.cookies import SimpleCookie
        credentials = json_body(private_read(self.config['credential_file'], limit=16384))
        try:
            need(type(credentials) is dict and set(credentials) == {'username', 'password'}, FAILURE)
            for k, cap in (('username', 256), ('password', 4096)):
                v = credentials[k]
                need(isinstance(v, str) and 1 <= len(v.encode('utf-8')) <= cap
                     and not any(c in v for c in ('\x00', '\r', '\n')), FAILURE)
            status, headers, raw = self.exchange(
                'POST', '/api/v2/auth/login',
                urllib.parse.urlencode(credentials).encode('ascii'),
            )
        finally:
            credentials.clear()
        cookies = headers.get_all('Set-Cookie', [])
        need(status in (200, 204) and len(cookies) == 1
             and len(cookies[0].encode('utf-8')) <= 4096, FAILURE)
        cookie = cookies[0].split(';', 1)[0].strip()
        need(re.fullmatch(r'QBT_SID_[0-9]{1,5}=[A-Za-z0-9+/=]{16,64}', cookie) is not None, FAILURE)
        parsed = SimpleCookie()
        parsed.load(cookies[0])
        need(len(parsed) == 1 and next(iter(parsed.values())).value == cookie.split('=', 1)[1], FAILURE)
        self.cookie = cookie
        status, _, raw = self.exchange('GET', '/api/v2/app/version')
        need(status == 200 and raw == self.config['expected_version'].encode('ascii'), FAILURE)

    def logout(self):
        if self.cookie is None or self.io_stalled:
            return
        try:
            self.exchange('POST', '/api/v2/auth/logout', cleanup=True)
        except Exception:
            pass
        finally:
            self.cookie = None

    def info_by_hashes(self, hashes):
        if not hashes:
            return []
        route = '/api/v2/torrents/info?hashes=' + '|'.join(hashes)
        self._extra_routes.add(('GET', route))
        status, _, raw = self.exchange('GET', route)
        need(status == 200, FAILURE)
        rows = json_body(raw)
        need(type(rows) is list, FAILURE)
        return rows

    def delete_torrents(self, hashes):
        if not hashes:
            return
        body = urllib.parse.urlencode({'hashes': '|'.join(hashes), 'deleteFiles': 'true'}).encode('ascii')
        status, _, _ = self.exchange('POST', '/api/v2/torrents/delete', body)
        need(status in (200, 204), FAILURE)


class LibraryEmbyClient(OperatorAdapter):
    """OperatorAdapter extension for read-only subtitle stream extraction."""

    def subtitle_srt(self, item_id, media_source_id, stream_index):
        need(catalog_id(item_id) and clean_text(media_source_id, 128)
             and integer(stream_index, 0, 1024), FAILURE)
        need(self._token is not None, 'Fresh authentication required')
        remaining = min(120, self._guard()) if self._guard is not None else 120
        need(remaining > 0, 'Caller deadline exceeded')
        url = (
            f"{self._config['origin']}/emby/Videos/{item_id}/"
            f"{urllib.parse.quote(media_source_id, safe='')}/Subtitles/{stream_index}/Stream.srt"
        )
        headers = {
            'Accept': 'text/plain, application/x-subrip, */*',
            'X-Emby-Token': self._token,
            'X-Emby-Authorization': 'MediaBrowser Client="HoloNetOperator", Device="Operator", DeviceId="holonet-operator", Version="1"',
        }
        req = urllib.request.Request(url, headers=headers, method='GET')
        with self._opener.open(req, timeout=remaining) as resp:
            need(resp.status == 200 and resp.geturl() == url, FAILURE)
            raw = importer_response_bytes(resp, limit=4 * 1024 * 1024)
        return raw.decode('utf-8', errors='replace')


# --- Subtitle SRT parsing --------------------------------------------------

SRT_TIME_RE = re.compile(
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})'
)
MARKUP_RE = re.compile(r'<[^>]+>|\{\\[^}]*\}')


def _to_seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, '0')) / 1000.0


def _fmt_hhmmss(sec):
    total = max(0, int(sec))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}'


def parse_srt(raw_text, *, start_seconds=None, end_seconds=None, max_chars=20000):
    text = raw_text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    blocks = re.split(r'\n{2,}', text.strip())
    lines = []
    used = 0
    truncated = False
    for block in blocks:
        parts = [p.strip() for p in block.split('\n') if p.strip()]
        if not parts:
            continue
        match = None
        text_idx = 0
        for idx, line in enumerate(parts[:2]):
            m = SRT_TIME_RE.search(line)
            if m:
                match = m
                text_idx = idx + 1
                break
        if match is None:
            continue
        cue_start = _to_seconds(*match.groups()[:4])
        cue_end = _to_seconds(*match.groups()[4:])
        if start_seconds is not None and cue_end < start_seconds:
            continue
        if end_seconds is not None and cue_start > end_seconds:
            continue
        raw_body = ' '.join(parts[text_idx:])
        cleaned = re.sub(r'\s+', ' ', MARKUP_RE.sub('', raw_body)).strip()
        if not cleaned:
            continue
        formatted = f'[{_fmt_hhmmss(cue_start)}] {cleaned}'
        addition = len(formatted) + (1 if lines else 0)
        if used + addition > max_chars:
            truncated = True
            break
        lines.append(formatted)
        used += addition
    return lines, truncated


# --- Projections & helpers -------------------------------------------------

def _in_root(item_path, root_folder_path):
    if not isinstance(item_path, str) or not item_path:
        return False
    return within(item_path.rstrip('/'), root_folder_path.rstrip('/'))


def _series_seasons_summary(raw_seasons):
    out = []
    for s in raw_seasons or []:
        if type(s) is not dict or type(s.get('seasonNumber')) is not int:
            continue
        stats = s.get('statistics') or {}
        out.append({
            'season': s['seasonNumber'],
            'monitored': bool(s.get('monitored', False)),
            'episode_count': int(stats.get('totalEpisodeCount', stats.get('episodeCount', 0)) or 0),
            'episode_file_count': int(stats.get('episodeFileCount', 0) or 0),
        })
    out.sort(key=lambda r: r['season'])
    return out


def _project_lookup_row(raw, kind, *, in_library):
    row = {
        'arr_id': raw['id'] if in_library and type(raw.get('id')) is int else None,
        'title': raw.get('title') or '',
        'year': raw.get('year') if type(raw.get('year')) is int else None,
        'imdb_id': raw.get('imdbId') or None,
        'in_library': in_library,
        'monitored': bool(raw.get('monitored', False)) if in_library else False,
        'path': raw.get('path') if in_library else None,
    }
    if kind == 'movie':
        row['tmdb_id'] = raw.get('tmdbId') if type(raw.get('tmdbId')) is int else None
        row['size_on_disk'] = int(raw.get('sizeOnDisk') or 0) if in_library else 0
    else:
        row['tvdb_id'] = raw.get('tvdbId') if type(raw.get('tvdbId')) is int else None
        stats = raw.get('statistics') or {}
        row['size_on_disk'] = int(stats.get('sizeOnDisk') or 0) if in_library else 0
        row['seasons'] = _series_seasons_summary(raw.get('seasons'))
    return row


def _extract_file_info(f, emby_item_id=None):
    mi = f.get('mediaInfo') or {}
    quality = (f.get('quality') or {}).get('quality', {}).get('name')
    return {
        'relative_path': f.get('relativePath') or '',
        'size': int(f.get('size') or 0),
        'quality': quality or None,
        'audio_languages': mi.get('audioLanguages') or None,
        'subtitle_languages': mi.get('subtitles') or None,
        'date_added': f.get('dateAdded') or None,
        'emby_item_id': emby_item_id,
    }


def _resolve_emby_map(emby, recipient, library_id, kind, *, provider_key=None, provider_val=None, series_path=None):
    """Best-effort mapping of file path -> Emby item id inside the authorized library."""
    if emby is None:
        return {}
    try:
        if kind == 'movie':
            query = {
                'Recursive': 'true',
                'IncludeItemTypes': 'Movie',
                'ParentId': library_id,
                'Fields': 'Path,ProviderIds',
                'StartIndex': 0,
                'Limit': 50,
            }
            if provider_key and provider_val:
                query['AnyProviderIdEquals'] = f'{provider_key}.{provider_val}'
            rows = emby._items(recipient, query, 50)
            return {r.get('Path'): r.get('Id') for r in rows if r.get('Path') and r.get('Id')}
        # Series: find Series first, then episodes under it.
        s_query = {
            'Recursive': 'true',
            'IncludeItemTypes': 'Series',
            'ParentId': library_id,
            'Fields': 'Path,ProviderIds',
            'StartIndex': 0,
            'Limit': 20,
        }
        if provider_key and provider_val:
            s_query['AnyProviderIdEquals'] = f'{provider_key}.{provider_val}'
        s_rows = emby._items(recipient, s_query, 20)
        series_id = None
        for r in s_rows:
            if provider_val and (r.get('ProviderIds') or {}).get(provider_key) == str(provider_val):
                series_id = r.get('Id')
                break
            if series_path and r.get('Path') and r['Path'].rstrip('/') == series_path.rstrip('/'):
                series_id = r.get('Id')
                break
        if not series_id:
            return {}
        e_rows = emby._items(recipient, {
            'Recursive': 'true',
            'IncludeItemTypes': 'Episode',
            'ParentId': series_id,
            'Fields': 'Path',
            'StartIndex': 0,
            'Limit': 500,
        }, 500)
        return {r.get('Path'): r.get('Id') for r in e_rows if r.get('Path') and r.get('Id')}
    except Exception:
        return {}


# --- Operation handlers ----------------------------------------------------

def op_lookup(call, block, arr_factory):
    q = call['request']
    lib_name = q['library']
    lib = block['libraries'][lib_name]
    kind = lib['kind']
    cap = block['limits']['lookup_max_rows']
    api = arr_factory(lib['arr'], lib['descriptor'], SECONDS['library-lookup'])
    endpoint = '/api/v3/movie' if kind == 'movie' else '/api/v3/series'
    existing_raw = api.get(endpoint)
    need(type(existing_raw) is list, FAILURE)

    # Filter to this library's root folder.
    existing = [r for r in existing_raw if type(r) is dict and _in_root(r.get('path'), lib['root_folder_path'])]
    term = q.get('term')
    arr_id = q.get('arr_id')
    matched = []
    seen_ext = set()
    ext_field = 'tmdbId' if kind == 'movie' else 'tvdbId'

    for r in existing:
        if arr_id is not None and r.get('id') != arr_id:
            continue
        if term is not None:
            t_low = term.lower()
            title_match = t_low in str(r.get('title', '')).lower()
            imdb_match = t_low == str(r.get('imdbId') or '').lower()
            ext_match = t_low == str(r.get(ext_field) or '').lower()
            if not (title_match or imdb_match or ext_match):
                continue
        matched.append(_project_lookup_row(r, kind, in_library=True))
        if r.get(ext_field) is not None:
            seen_ext.add(r[ext_field])

    if term is not None and arr_id is None:
        lookup_route = f'{endpoint}/lookup?' + urllib.parse.urlencode({'term': term})
        lookup_raw = api.get(lookup_route)
        need(type(lookup_raw) is list, FAILURE)
        for r in lookup_raw:
            if type(r) is not dict:
                continue
            ext_id = r.get(ext_field)
            if ext_id is not None and ext_id in seen_ext:
                continue
            # Also skip if Arr lookup returned an item that is already in Arr (e.g. another root).
            if type(r.get('id')) is int and r['id'] > 0:
                continue
            if ext_id is not None:
                seen_ext.add(ext_id)
            matched.append(_project_lookup_row(r, kind, in_library=False))

    truncated = len(matched) > cap
    return {
        'library': lib_name,
        'kind': kind,
        'items': matched[:cap],
        'truncated': truncated,
    }


def op_detail(call, block, arr_factory, emby_factory):
    q = call['request']
    lib_name = q['library']
    lib = block['libraries'][lib_name]
    kind = lib['kind']
    api = arr_factory(lib['arr'], lib['descriptor'], SECONDS['library-detail'])
    emby = emby_factory()
    if kind == 'movie':
        m = api.get(f"/api/v3/movie/{q['arr_id']}")
        need(type(m) is dict and m.get('id') == q['arr_id']
             and _in_root(m.get('path'), lib['root_folder_path']), FAILURE)
        emby_map = _resolve_emby_map(
            emby, call['recipient'], lib['library_id'], 'movie',
            provider_key='Tmdb', provider_val=m.get('tmdbId'),
        )
        file_obj = None
        if m.get('hasFile') and type(m.get('movieFile')) is dict:
            mf = m['movieFile']
            full_path = mf.get('path') or (m['path'].rstrip('/') + '/' + str(mf.get('relativePath') or ''))
            file_obj = _extract_file_info(mf, emby_item_id=emby_map.get(full_path))
        return {
            'library': lib_name,
            'kind': 'movie',
            'arr_id': m['id'],
            'title': m.get('title') or '',
            'year': m.get('year') if type(m.get('year')) is int else None,
            'tmdb_id': m.get('tmdbId') if type(m.get('tmdbId')) is int else None,
            'imdb_id': m.get('imdbId') or None,
            'monitored': bool(m.get('monitored', False)),
            'path': m.get('path'),
            'size_on_disk': int(m.get('sizeOnDisk') or 0),
            'has_file': bool(m.get('hasFile', False)),
            'file': file_obj,
        }

    s = api.get(f"/api/v3/series/{q['arr_id']}")
    need(type(s) is dict and s.get('id') == q['arr_id']
         and _in_root(s.get('path'), lib['root_folder_path']), FAILURE)
    raw_eps = api.get(f"/api/v3/episode?seriesId={q['arr_id']}&includeEpisodeFile=true")
    need(type(raw_eps) is list, FAILURE)
    emby_map = _resolve_emby_map(
        emby, call['recipient'], lib['library_id'], 'series',
        provider_key='Tvdb', provider_val=s.get('tvdbId'), series_path=s.get('path'),
    )
    episodes = []
    for e in raw_eps:
        if type(e) is not dict or type(e.get('id')) is not int:
            continue
        ef = e.get('episodeFile') if e.get('hasFile') and type(e.get('episodeFile')) is dict else None
        f_info = None
        if ef is not None:
            full_path = ef.get('path') or (s['path'].rstrip('/') + '/' + str(ef.get('relativePath') or ''))
            f_info = _extract_file_info(ef, emby_item_id=emby_map.get(full_path))
        episodes.append({
            'arr_episode_id': e['id'],
            'season': int(e.get('seasonNumber', 0)),
            'episode': int(e.get('episodeNumber', 0)),
            'title': e.get('title') or '',
            'monitored': bool(e.get('monitored', False)),
            'has_file': bool(e.get('hasFile', False)),
            'relative_path': f_info['relative_path'] if f_info else None,
            'size': f_info['size'] if f_info else None,
            'quality': f_info['quality'] if f_info else None,
            'audio_languages': f_info['audio_languages'] if f_info else None,
            'subtitle_languages': f_info['subtitle_languages'] if f_info else None,
            'date_added': f_info['date_added'] if f_info else None,
            'emby_item_id': f_info['emby_item_id'] if f_info else None,
        })
    episodes.sort(key=lambda r: (r['season'], r['episode'], r['arr_episode_id']))
    stats = s.get('statistics') or {}
    return {
        'library': lib_name,
        'kind': 'series',
        'arr_id': s['id'],
        'title': s.get('title') or '',
        'year': s.get('year') if type(s.get('year')) is int else None,
        'tvdb_id': s.get('tvdbId') if type(s.get('tvdbId')) is int else None,
        'imdb_id': s.get('imdbId') or None,
        'monitored': bool(s.get('monitored', False)),
        'path': s.get('path'),
        'size_on_disk': int(stats.get('sizeOnDisk') or 0),
        'seasons': _series_seasons_summary(s.get('seasons')),
        'episodes': episodes,
    }


def op_releases(call, block, arr_factory):
    q = call['request']
    lib_name = q['library']
    lib = block['libraries'][lib_name]
    kind = lib['kind']
    cap = block['limits']['releases_max_rows']
    api = arr_factory(lib['arr'], lib['descriptor'], SECONDS['library-releases'])

    if kind == 'movie':
        m = api.get(f"/api/v3/movie/{q['arr_id']}")
        need(type(m) is dict and m.get('id') == q['arr_id']
             and _in_root(m.get('path'), lib['root_folder_path']), FAILURE)
        route = '/api/v3/release?' + urllib.parse.urlencode({'movieId': q['arr_id']})
    else:
        s = api.get(f"/api/v3/series/{q['arr_id']}")
        need(type(s) is dict and s.get('id') == q['arr_id']
             and _in_root(s.get('path'), lib['root_folder_path']), FAILURE)
        if 'episode_arr_id' in q:
            route = '/api/v3/release?' + urllib.parse.urlencode({'episodeId': q['episode_arr_id']})
        else:
            route = '/api/v3/release?' + urllib.parse.urlencode({
                'seriesId': q['arr_id'], 'seasonNumber': q['season'],
            })

    raw = api.get(route)
    need(type(raw) is list, FAILURE)
    releases = []
    seen_handles = set()
    for r in raw:
        if type(r) is not dict:
            continue
        guid = r.get('guid')
        indexer_id = r.get('indexerId')
        title = r.get('title')
        indexer = r.get('indexer') or ''
        size = r.get('size')
        if not (isinstance(guid, str) and guid and integer(indexer_id)
                and isinstance(title, str) and title and integer(size, 0, 1 << 50)):
            continue
        if any(tok in guid.lower() for tok in ('apikey=', 'password=')):
            continue
        h = release_handle(lib_name, q['arr_id'], indexer_id, guid, title, size)
        if h in seen_handles:
            continue
        seen_handles.add(h)
        q_name = (r.get('quality') or {}).get('quality', {}).get('name')
        langs = [
            l.get('name') for l in (r.get('languages') or [])
            if type(l) is dict and isinstance(l.get('name'), str)
        ]
        rejs = [str(x) for x in (r.get('rejections') or []) if isinstance(x, str)]
        age_days = r.get('age')
        if not number(age_days, 0.0, 1e6):
            age_days = 0
        seeders = r.get('seeders') if type(r.get('seeders')) is int else None
        leechers = r.get('leechers') if type(r.get('leechers')) is int else None
        cf_score = r.get('customFormatScore') if type(r.get('customFormatScore')) is int else 0
        releases.append({
            'handle': h,
            'guid': guid,
            'indexer_id': indexer_id,
            'title': title,
            'indexer': indexer,
            'size': size,
            'seeders': seeders,
            'leechers': leechers,
            'age_days': age_days,
            'quality': q_name or None,
            'languages': langs,
            'custom_format_score': cf_score,
            'full_season': bool(r.get('fullSeason', False)),
            'approved': bool(r.get('approved', not bool(r.get('rejected', False)))),
            'rejections': rejs,
        })
    releases.sort(key=lambda r: (
        not r['approved'],
        -r['custom_format_score'],
        -(r['seeders'] or 0),
        r['handle'],
    ))
    return {
        'library': lib_name,
        'arr_id': q['arr_id'],
        'releases': releases[:cap],
        'truncated': len(releases) > cap,
    }


def _queue_messages(raw_messages):
    out = []
    for entry in raw_messages or []:
        if type(entry) is not dict:
            continue
        title = entry.get('title')
        msgs = entry.get('messages') or []
        if isinstance(title, str) and title and not msgs:
            out.append(title)
        for m in msgs:
            if isinstance(m, str) and m:
                out.append(f'{title}: {m}' if isinstance(title, str) and title else m)
    return out


def op_queue(call, block, arr_factory):
    q = call['request']
    wanted = q.get('library')
    items = []

    if wanted in (None, 'movies'):
        lib = block['libraries']['movies']
        api = arr_factory('radarr', lib['descriptor'], SECONDS['library-queue'])
        data = api.get('/api/v3/queue?page=1&pageSize=100&includeUnknownMovieItems=false')
        need(type(data) is dict and type(data.get('records')) is list, FAILURE)
        for r in data['records']:
            if type(r) is not dict:
                continue
            size = float(r.get('size') or 0)
            size_left = float(r.get('sizeleft') or 0)
            prog = round((size - size_left) / size, 4) if size > 0 else 0.0
            items.append({
                'library': 'movies',
                'title': r.get('title') or '',
                'arr_id': r.get('movieId') if type(r.get('movieId')) is int else None,
                'episode_ids': [],
                'status': r.get('status') or '',
                'tracked_state': r.get('trackedDownloadState') or '',
                'progress': max(0.0, min(1.0, prog)),
                'size': int(size),
                'size_left': int(size_left),
                'time_left': r.get('timeleft') or None,
                'download_client': r.get('downloadClient') or None,
                'status_messages': _queue_messages(r.get('statusMessages')),
            })

    if wanted in (None, 'tv', 'anime'):
        sonarr_desc = block['libraries']['tv']['descriptor']
        api = arr_factory('sonarr', sonarr_desc, SECONDS['library-queue'])
        series_list = api.get('/api/v3/series')
        need(type(series_list) is list, FAILURE)
        series_lib = {}
        tv_root = block['libraries']['tv']['root_folder_path']
        anime_root = block['libraries']['anime']['root_folder_path']
        for s in series_list:
            if type(s) is not dict or type(s.get('id')) is not int:
                continue
            if _in_root(s.get('path'), anime_root):
                series_lib[s['id']] = 'anime'
            elif _in_root(s.get('path'), tv_root):
                series_lib[s['id']] = 'tv'
        data = api.get('/api/v3/queue?page=1&pageSize=100&includeUnknownSeriesItems=false')
        need(type(data) is dict and type(data.get('records')) is list, FAILURE)
        for r in data['records']:
            if type(r) is not dict:
                continue
            sid = r.get('seriesId')
            lib_name = series_lib.get(sid)
            if lib_name is None:
                continue
            if wanted is not None and lib_name != wanted:
                continue
            size = float(r.get('size') or 0)
            size_left = float(r.get('sizeleft') or 0)
            prog = round((size - size_left) / size, 4) if size > 0 else 0.0
            ep_ids = [r['episodeId']] if type(r.get('episodeId')) is int else []
            items.append({
                'library': lib_name,
                'title': r.get('title') or '',
                'arr_id': sid if type(sid) is int else None,
                'episode_ids': ep_ids,
                'status': r.get('status') or '',
                'tracked_state': r.get('trackedDownloadState') or '',
                'progress': max(0.0, min(1.0, prog)),
                'size': int(size),
                'size_left': int(size_left),
                'time_left': r.get('timeleft') or None,
                'download_client': r.get('downloadClient') or None,
                'status_messages': _queue_messages(r.get('statusMessages')),
            })

    return {'library': wanted, 'items': items}


def _is_image_subtitle(stream):
    codec = str(stream.get('Codec') or '').lower()
    if codec in IMAGE_SUBTITLE_CODECS:
        return True
    if stream.get('IsTextSubtitleStream') is False:
        return True
    return False


def op_subtitles(call, block, arr_factory, emby_factory):
    q = call['request']
    lib_name = q['library']
    lib = block['libraries'][lib_name]
    emby = emby_factory()
    need(emby is not None, FAILURE)
    recipient = call['recipient']
    item = None

    if 'emby_item_id' in q:
        rows = emby._items(recipient, {
            'Recursive': 'true',
            'ParentId': lib['library_id'],
            'Ids': q['emby_item_id'],
            'Fields': 'MediaSources,Path,ProviderIds',
            'StartIndex': 0,
            'Limit': 2,
        }, 1)
        need(len(rows) == 1 and rows[0].get('Id') == q['emby_item_id'],
             'Emby item not found in authorized library')
        item = rows[0]
    else:
        api = arr_factory(lib['arr'], lib['descriptor'], SECONDS['library-subtitles'])
        if lib['kind'] == 'movie':
            m = api.get(f"/api/v3/movie/{q['arr_id']}")
            need(type(m) is dict and m.get('id') == q['arr_id']
                 and _in_root(m.get('path'), lib['root_folder_path']), FAILURE)
            need(m.get('hasFile') and type(m.get('movieFile')) is dict, 'Movie has no file')
            target_path = m['movieFile'].get('path') or (
                m['path'].rstrip('/') + '/' + str(m['movieFile'].get('relativePath') or '')
            )
            emby_map = _resolve_emby_map(
                emby, recipient, lib['library_id'], 'movie',
                provider_key='Tmdb', provider_val=m.get('tmdbId'),
            )
            eid = emby_map.get(target_path)
            need(eid is not None, 'Emby item not found for movie')
        else:
            need('episode_arr_id' in q, 'Episode ID or Emby item ID required')
            s = api.get(f"/api/v3/series/{q['arr_id']}")
            need(type(s) is dict and s.get('id') == q['arr_id']
                 and _in_root(s.get('path'), lib['root_folder_path']), FAILURE)
            ep = api.get(f"/api/v3/episode/{q['episode_arr_id']}")
            need(type(ep) is dict and ep.get('seriesId') == q['arr_id'], FAILURE)
            need(ep.get('hasFile') and type(ep.get('episodeFile')) is dict, 'Episode has no file')
            target_path = ep['episodeFile'].get('path') or (
                s['path'].rstrip('/') + '/' + str(ep['episodeFile'].get('relativePath') or '')
            )
            emby_map = _resolve_emby_map(
                emby, recipient, lib['library_id'], 'series',
                provider_key='Tvdb', provider_val=s.get('tvdbId'), series_path=s.get('path'),
            )
            eid = emby_map.get(target_path)
            need(eid is not None, 'Emby item not found for episode')
        rows = emby._items(recipient, {
            'Recursive': 'true',
            'ParentId': lib['library_id'],
            'Ids': eid,
            'Fields': 'MediaSources,Path,ProviderIds',
            'StartIndex': 0,
            'Limit': 2,
        }, 1)
        need(len(rows) == 1 and rows[0].get('Id') == eid, FAILURE)
        item = rows[0]

    sources = item.get('MediaSources') or []
    need(type(sources) is list and len(sources) >= 1 and type(sources[0]) is dict, 'No media source found')
    ms = sources[0]
    ms_id = ms.get('Id')
    need(isinstance(ms_id, str) and ms_id, 'No media source ID')

    raw_streams = [
        st for st in (ms.get('MediaStreams') or [])
        if type(st) is dict and st.get('Type') == 'Subtitle' and type(st.get('Index')) is int
    ]
    streams = []
    for st in raw_streams:
        streams.append({
            'index': st['Index'],
            'language': st.get('Language') or None,
            'codec': st.get('Codec') or None,
            'title': st.get('Title') or st.get('DisplayTitle') or None,
            'forced': bool(st.get('IsForced', False)),
            'external': bool(st.get('IsExternal', False)),
            'is_text': not _is_image_subtitle(st),
        })

    selected = None
    if 'stream_index' in q:
        selected = next((st for st in streams if st['index'] == q['stream_index']), None)
        need(selected is not None, 'Subtitle stream not found')
    else:
        eng_text = [
            st for st in streams
            if st['is_text'] and not st['forced']
            and str(st['language'] or '').lower() in ('eng', 'en', 'english')
        ]
        any_text = [st for st in streams if st['is_text']]
        if eng_text:
            selected = eng_text[0]
        elif any_text:
            selected = any_text[0]
        elif streams:
            selected = streams[0]

    if selected is None:
        return {
            'library': lib_name,
            'arr_id': q['arr_id'],
            'emby_item_id': item['Id'],
            'streams': streams,
            'selected_stream': None,
            'status': 'no_subtitles',
            'lines': [],
            'truncated': False,
        }

    if not selected['is_text']:
        return {
            'library': lib_name,
            'arr_id': q['arr_id'],
            'emby_item_id': item['Id'],
            'streams': streams,
            'selected_stream': selected,
            'status': 'image_subtitle',
            'lines': [],
            'truncated': False,
        }

    max_chars = min(
        q.get('max_chars', block['limits']['subtitles_default_chars']),
        block['limits']['subtitles_max_chars'],
    )
    srt_text = emby.subtitle_srt(item['Id'], ms_id, selected['index'])
    lines, truncated = parse_srt(
        srt_text,
        start_seconds=q.get('start_seconds'),
        end_seconds=q.get('end_seconds'),
        max_chars=max_chars,
    )
    return {
        'library': lib_name,
        'arr_id': q['arr_id'],
        'emby_item_id': item['Id'],
        'streams': streams,
        'selected_stream': selected,
        'status': 'ok',
        'lines': lines,
        'truncated': truncated,
    }


def op_add(call, block, arr_factory):
    q = call['request']
    lib_name = q['library']
    lib = block['libraries'][lib_name]
    kind = lib['kind']
    api = arr_factory(lib['arr'], lib['descriptor'], SECONDS['library-add'])

    if kind == 'movie':
        tmdb_id = q['tmdb_id']
        existing = api.get('/api/v3/movie')
        need(type(existing) is list, FAILURE)
        if any(type(m) is dict and m.get('tmdbId') == tmdb_id for m in existing):
            refuse('already_in_library', 'Title is already in library')
        found = api.get('/api/v3/movie/lookup?' + urllib.parse.urlencode({'term': f'tmdb:{tmdb_id}'}))
        need(type(found) is list, FAILURE)
        match = next((m for m in found if type(m) is dict and m.get('tmdbId') == tmdb_id), None)
        need(match is not None, 'Title not found in Arr lookup')
        plan = {
            'library': lib_name,
            'kind': 'movie',
            'title': match.get('title') or '',
            'year': match.get('year') if type(match.get('year')) is int else None,
            'tmdb_id': tmdb_id,
            'imdb_id': match.get('imdbId') or None,
            'root_folder_path': lib['root_folder_path'],
            'quality_profile_id': lib['quality_profile_id'],
            'monitor': q['monitor'],
            'search_now': q['search_now'],
        }
        digest = plan_digest(plan)
        if q['mode'] == 'preview':
            return {'mode': 'preview', 'plan': plan, 'plan_sha256': digest}
        if digest != q['plan_sha256']:
            refuse('plan_changed', 'Plan changed since preview')
        payload = {
            'title': match['title'],
            'tmdbId': tmdb_id,
            'year': match.get('year'),
            'qualityProfileId': lib['quality_profile_id'],
            'rootFolderPath': lib['root_folder_path'],
            'monitored': q['monitor'] != 'none',
            'addOptions': {'searchForMovie': q['search_now']},
        }
        status, created = api.post('/api/v3/movie', payload, accept_statuses=(200, 201))
        need(status in (200, 201) and type(created) is dict and integer(created.get('id')), FAILURE)
        return {
            'mode': 'apply',
            'operation_id': q['operation_id'],
            'plan': plan,
            'plan_sha256': digest,
            'applied': True,
            'arr_id': created['id'],
            'path': created.get('path'),
        }

    tvdb_id = q['tvdb_id']
    existing = api.get('/api/v3/series')
    need(type(existing) is list, FAILURE)
    if any(type(s) is dict and s.get('tvdbId') == tvdb_id for s in existing):
        refuse('already_in_library', 'Title is already in library')
    found = api.get('/api/v3/series/lookup?' + urllib.parse.urlencode({'term': f'tvdb:{tvdb_id}'}))
    need(type(found) is list, FAILURE)
    match = next((s for s in found if type(s) is dict and s.get('tvdbId') == tvdb_id), None)
    need(match is not None, 'Title not found in Arr lookup')
    selected_seasons = sorted(q['seasons']) if 'seasons' in q else None
    plan = {
        'library': lib_name,
        'kind': 'series',
        'title': match.get('title') or '',
        'year': match.get('year') if type(match.get('year')) is int else None,
        'tvdb_id': tvdb_id,
        'imdb_id': match.get('imdbId') or None,
        'root_folder_path': lib['root_folder_path'],
        'quality_profile_id': lib['quality_profile_id'],
        'monitor': q['monitor'],
        'seasons': selected_seasons,
        'search_now': q['search_now'],
    }
    digest = plan_digest(plan)
    if q['mode'] == 'preview':
        return {'mode': 'preview', 'plan': plan, 'plan_sha256': digest}
    if digest != q['plan_sha256']:
        refuse('plan_changed', 'Plan changed since preview')
    seasons_payload = []
    for s_obj in (match.get('seasons') or []):
        if type(s_obj) is not dict or type(s_obj.get('seasonNumber')) is not int:
            continue
        sn = s_obj['seasonNumber']
        if selected_seasons is not None:
            mon = sn in selected_seasons
        else:
            mon = q['monitor'] != 'none' and sn > 0
        seasons_payload.append({'seasonNumber': sn, 'monitored': mon})
    payload = {
        'title': match['title'],
        'tvdbId': tvdb_id,
        'qualityProfileId': lib['quality_profile_id'],
        'rootFolderPath': lib['root_folder_path'],
        'monitored': q['monitor'] != 'none',
        'seasonFolder': True,
        'seriesType': 'anime' if lib_name == 'anime' else 'standard',
        'seasons': seasons_payload,
        'addOptions': {
            'monitor': q['monitor'],
            'searchForMissingEpisodes': q['search_now'],
        },
    }
    status, created = api.post('/api/v3/series', payload, accept_statuses=(200, 201))
    need(status in (200, 201) and type(created) is dict and integer(created.get('id')), FAILURE)
    return {
        'mode': 'apply',
        'operation_id': q['operation_id'],
        'plan': plan,
        'plan_sha256': digest,
        'applied': True,
        'arr_id': created['id'],
        'path': created.get('path'),
    }


def op_grab(call, block, arr_factory):
    q = call['request']
    lib_name = q['library']
    lib = block['libraries'][lib_name]
    kind = lib['kind']
    api = arr_factory(lib['arr'], lib['descriptor'], SECONDS['library-grab'])
    endpoint = '/api/v3/movie' if kind == 'movie' else '/api/v3/series'
    item = api.get(f"{endpoint}/{q['arr_id']}")
    need(type(item) is dict and item.get('id') == q['arr_id']
         and _in_root(item.get('path'), lib['root_folder_path']), FAILURE)

    allow_rejected = bool(q.get('allow_rejected', False))
    plan = {
        'library': lib_name,
        'arr_id': q['arr_id'],
        'target_title': item.get('title') or '',
        'search_id': q['search_id'],
        'handle': q['handle'],
        'title': q['title'],
        'indexer': q['indexer'],
        'indexer_id': q['indexer_id'],
        'size': q['size'],
        'approved': q['approved'],
        'rejections': list(q['rejections']),
        'allow_rejected': allow_rejected,
    }
    digest = plan_digest(plan)
    if q['mode'] == 'preview':
        return {'mode': 'preview', 'plan': plan, 'plan_sha256': digest}
    if digest != q['plan_sha256']:
        refuse('plan_changed', 'Plan changed since preview')
    if not q['approved'] and not allow_rejected:
        refuse('rejected_release', 'Rejected release requires allow_rejected true')
    status, data = api.post(
        '/api/v3/release',
        {'guid': q['guid'], 'indexerId': q['indexer_id']},
        accept_statuses=(200, 201, 202),
    )
    if status in (400, 404, 410):
        refuse('release_expired', 'Release no longer cached by Arr; search again')
    need(status in (200, 201, 202), FAILURE)
    return {
        'mode': 'apply',
        'operation_id': q['operation_id'],
        'plan': plan,
        'plan_sha256': digest,
        'applied': True,
        'grabbed': True,
    }


def _evaluate_torrents(qbit, hashes_info, *, remove_torrents, allowed_categories, min_ratio, min_seeding_days):
    if not hashes_info:
        return []
    hashes = sorted(hashes_info)
    torrents_raw = qbit.info_by_hashes(hashes)
    by_hash = {
        str(t.get('hash', '')).lower(): t
        for t in torrents_raw if type(t) is dict and t.get('hash')
    }
    min_seeding_seconds = min_seeding_days * 86400
    out = []
    for h in hashes:
        t = by_hash.get(h)
        if t is None:
            continue
        cat = str(t.get('category') or '')
        ratio = float(t.get('ratio') or 0.0)
        seeding_time = int(t.get('seeding_time') or 0)
        every_file_deleted = hashes_info[h]
        if not remove_torrents:
            action, reason = 'keep', 'remove_torrents_false'
        elif cat not in allowed_categories:
            action, reason = 'keep', 'category_not_allowed'
        elif not every_file_deleted:
            action, reason = 'keep', 'partially_kept'
        elif ratio < min_ratio and seeding_time < min_seeding_seconds:
            action, reason = 'keep', 'seeding_threshold_not_met'
        else:
            action, reason = 'remove', 'meets_threshold'
        out.append({
            'hash': h,
            'name': str(t.get('name') or ''),
            'category': cat,
            'ratio': round(ratio, 4),
            'seeding_time_seconds': seeding_time,
            'action': action,
            'reason': reason,
        })
    return out


def op_remove(call, block, arr_factory, qbit_factory):
    q = call['request']
    lib_name = q['library']
    lib = block['libraries'][lib_name]
    kind = lib['kind']
    api = arr_factory(lib['arr'], lib['descriptor'], SECONDS['library-remove'])

    # Refuse if recycle bin is unset.
    mm = api.get('/api/v3/config/mediamanagement')
    need(type(mm) is dict, FAILURE)
    recycle_bin = str(mm.get('recycleBin') or '').strip()
    if not recycle_bin:
        refuse('recycle_bin_unset', 'Arr recycle bin is not configured')

    target = q['target']
    files = []
    episode_ids_to_unmonitor = []
    episode_file_ids_to_delete = []
    delete_title = target == 'title'
    hashes_every_deleted = {}

    if kind == 'movie':
        m = api.get(f"/api/v3/movie/{q['arr_id']}")
        need(type(m) is dict and m.get('id') == q['arr_id']
             and _in_root(m.get('path'), lib['root_folder_path']), FAILURE)
        title = m.get('title') or ''
        if m.get('hasFile') and type(m.get('movieFile')) is dict:
            mf = m['movieFile']
            fpath = mf.get('path') or (m['path'].rstrip('/') + '/' + str(mf.get('relativePath') or ''))
            files.append({'id': int(mf.get('id') or 0), 'path': fpath, 'size': int(mf.get('size') or 0)})
        history = api.get(f"/api/v3/history/movie?movieId={q['arr_id']}")
        need(type(history) is list, FAILURE)
        for h_row in history:
            if type(h_row) is not dict:
                continue
            dl = str(h_row.get('downloadId') or '').lower()
            if HEX40.fullmatch(dl):
                hashes_every_deleted[dl] = True
    else:
        s = api.get(f"/api/v3/series/{q['arr_id']}")
        need(type(s) is dict and s.get('id') == q['arr_id']
             and _in_root(s.get('path'), lib['root_folder_path']), FAILURE)
        title = s.get('title') or ''
        raw_eps = api.get(f"/api/v3/episode?seriesId={q['arr_id']}&includeEpisodeFile=true")
        need(type(raw_eps) is list, FAILURE)
        by_id = {e['id']: e for e in raw_eps if type(e) is dict and type(e.get('id')) is int}
        if target == 'title':
            selected_eps = list(by_id.values())
        elif 'season' in target:
            selected_eps = [e for e in by_id.values() if e.get('seasonNumber') == target['season']]
            need(len(selected_eps) > 0, 'No episodes found for season')
        else:
            selected_eps = []
            for eid in target['episode_arr_ids']:
                need(eid in by_id, 'Episode not found in series')
                selected_eps.append(by_id[eid])
        selected_ep_ids = {e['id'] for e in selected_eps}
        episode_ids_to_unmonitor = sorted(selected_ep_ids)
        seen_file_ids = set()
        for e in selected_eps:
            ef = e.get('episodeFile') if e.get('hasFile') and type(e.get('episodeFile')) is dict else None
            if ef is not None and type(ef.get('id')) is int and ef['id'] not in seen_file_ids:
                seen_file_ids.add(ef['id'])
                episode_file_ids_to_delete.append(ef['id'])
                fpath = ef.get('path') or (s['path'].rstrip('/') + '/' + str(ef.get('relativePath') or ''))
                files.append({'id': ef['id'], 'path': fpath, 'size': int(ef.get('size') or 0)})
        episode_file_ids_to_delete.sort()

        history = api.get(f"/api/v3/history/series?seriesId={q['arr_id']}")
        need(type(history) is list, FAILURE)
        # Track every episode each downloadId supplied across the series.
        dl_episodes = {}
        for h_row in history:
            if type(h_row) is not dict:
                continue
            dl = str(h_row.get('downloadId') or '').lower()
            ep_id = h_row.get('episodeId')
            if HEX40.fullmatch(dl) and type(ep_id) is int:
                dl_episodes.setdefault(dl, set()).add(ep_id)
        for dl, ep_set in dl_episodes.items():
            if ep_set & selected_ep_ids:
                # Only allow torrent removal if every episode still having a file (or supplied by
                # this torrent) is being deleted.
                other_eps_with_files = {
                    eid for eid in (ep_set - selected_ep_ids)
                    if eid in by_id and by_id[eid].get('hasFile')
                }
                hashes_every_deleted[dl] = (len(other_eps_with_files) == 0)

    files.sort(key=lambda f: (f['path'], f['id']))
    if len(files) > block['limits']['remove_max_files']:
        refuse('file_limit_exceeded', 'Removal exceeds per-call file limit')

    qbit = None
    torrents = []
    try:
        if hashes_every_deleted:
            qbit = qbit_factory(block['torrents'])
            qbit.login()
            torrents = _evaluate_torrents(
                qbit,
                hashes_every_deleted,
                remove_torrents=q['remove_torrents'],
                allowed_categories=set(block['allowed_categories']),
                min_ratio=float(block['removal_thresholds']['min_ratio']),
                min_seeding_days=int(block['removal_thresholds']['min_seeding_days']),
            )

        plan = {
            'library': lib_name,
            'arr_id': q['arr_id'],
            'title': title,
            'target': target,
            'delete_title': delete_title,
            'recycle_bin': recycle_bin,
            'files': files,
            'total_bytes': sum(f['size'] for f in files),
            'remove_torrents': q['remove_torrents'],
            'torrents': torrents,
        }
        digest = plan_digest(plan)
        if q['mode'] == 'preview':
            return {'mode': 'preview', 'plan': plan, 'plan_sha256': digest}
        if digest != q['plan_sha256']:
            refuse('plan_changed', 'Plan changed since preview')

        unmonitored_count = 0
        if delete_title:
            if kind == 'movie':
                st, _ = api.delete(f"/api/v3/movie/{q['arr_id']}?deleteFiles=true&addImportExclusion=false")
            else:
                st, _ = api.delete(f"/api/v3/series/{q['arr_id']}?deleteFiles=true&addImportListExclusion=false")
            need(st in (200, 204), FAILURE)
        else:
            if episode_ids_to_unmonitor:
                st, _ = api.put('/api/v3/episode/monitor', {
                    'episodeIds': episode_ids_to_unmonitor,
                    'monitored': False,
                }, accept_statuses=(200, 202))
                need(st in (200, 202), FAILURE)
                unmonitored_count = len(episode_ids_to_unmonitor)
            for fid in episode_file_ids_to_delete:
                st, _ = api.delete(f'/api/v3/episodefile/{fid}')
                need(st in (200, 204), FAILURE)

        to_remove = [t['hash'] for t in torrents if t['action'] == 'remove']
        to_keep = [t['hash'] for t in torrents if t['action'] != 'remove']
        if to_remove and qbit is not None:
            qbit.delete_torrents(to_remove)

        return {
            'mode': 'apply',
            'operation_id': q['operation_id'],
            'plan': plan,
            'plan_sha256': digest,
            'applied': True,
            'title_deleted': delete_title,
            'deleted_file_count': len(files),
            'unmonitored_episode_count': unmonitored_count,
            'removed_torrent_hashes': to_remove,
            'kept_torrent_hashes': to_keep,
        }
    finally:
        if qbit is not None:
            qbit.logout()


def execute(policy, call, guard, config):
    """Run one validated library management operation with fresh sessions."""
    block = validate_block(policy['library'])
    op = call['operation']
    request(op, call['request'])
    guard()

    arr_clients = {}
    emby_client = [None]

    def arr_factory(backend, desc_path, budget):
        if desc_path not in arr_clients:
            desc = strict_json(private_read(desc_path))
            need(type(desc) is dict and desc.get('backend') == backend, FAILURE)
            arr_clients[desc_path] = LibraryArrClient(desc, request_guard=guard, budget_seconds=budget)
        return arr_clients[desc_path]

    def emby_factory():
        if emby_client[0] is None and config is not None:
            client = LibraryEmbyClient(config, request_guard=guard)
            creds = load_credentials(config['credential_file'])
            try:
                client.authenticate(creds)
            finally:
                creds.clear()
            emby_client[0] = client
        return emby_client[0]

    def qbit_factory(desc_path):
        desc = torrent_descriptor(strict_json(private_read(desc_path)))
        return LibraryTorrentClient(desc, request_guard=guard, budget_seconds=30)

    try:
        if op == 'library-lookup':
            return op_lookup(call, block, arr_factory)
        if op == 'library-detail':
            return op_detail(call, block, arr_factory, emby_factory)
        if op == 'library-releases':
            return op_releases(call, block, arr_factory)
        if op == 'library-queue':
            return op_queue(call, block, arr_factory)
        if op == 'library-subtitles':
            return op_subtitles(call, block, arr_factory, emby_factory)
        if op == 'library-add':
            return op_add(call, block, arr_factory)
        if op == 'library-grab':
            return op_grab(call, block, arr_factory)
        if op == 'library-remove':
            return op_remove(call, block, arr_factory, qbit_factory)
        need(False, 'Unsupported operation')
    finally:
        for client in arr_clients.values():
            client.close()
        if emby_client[0] is not None:
            emby_client[0].close()
