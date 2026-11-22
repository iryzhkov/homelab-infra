"""HoloNet library management v1 worker operations (Sonarr, Radarr, qBittorrent, Emby).

Stateless root worker side; Go owns the keep list, 30-minute release search cache,
and write-ahead intent journal. All effects happen through existing Arr/qBit/Emby
HTTP APIs; no direct filesystem writes or new credentials.
"""
import copy
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import PurePosixPath
import queue
import re
import secrets
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from emby_operator_adapter import (
    ImporterInventoryAdapter,
    NoRedirect,
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
    'library-recommendations',
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
    'library-recommendations': 60,
    'library-add': 60,
    'library-grab': 60,
    'library-remove': 120,
}

MDBLIST_ORIGIN = 'https://api.mdblist.com'
MDBLIST_MAX_BYTES = 4 * 1024 * 1024
MDBLIST_CACHE_MAX_BYTES = 8 * 1024 * 1024
MDBLIST_QUOTA_MAX_BYTES = 16 * 1024
RECOMMENDATION_KINDS = frozenset({'movie', 'series', 'anime'})
RECOMMENDATION_FILTER_KINDS = RECOMMENDATION_KINDS | frozenset({'all'})
RECOMMENDATION_SOURCES = frozenset({'mdblist', 'radarr', 'all'})
RECOMMENDATION_SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,127}')
DEFAULT_MDBLIST_KEY_FILE = '/etc/holonet/mdblist.key'
DEFAULT_RECOMMENDATIONS_STATE_DIR = '/var/lib/holonet-operator/recommendations'
DEFAULT_RECOMMENDATIONS_DAILY_CEILING = 100
DEFAULT_RECOMMENDATIONS_CACHE_TTL = 21600
DEFAULT_RECOMMENDATIONS_MIN_REFRESH_AGE = 3600

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


class LibraryPostDispatchError(Exception):
    """Failure after at least one mutating call was sent; effects may be partial or uncertain."""
    def __init__(self, code, message, completed=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.completed = list(completed or [])


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
    req_block_keys = {
        'enabled', 'target_library', 'revision', 'subjects', 'libraries',
        'torrents', 'allowed_categories', 'removal_thresholds', 'limits',
    }
    need(type(value) is dict and req_block_keys <= set(value) <= (req_block_keys | {'recommendations'}),
         'Invalid library policy')
    need(type(value['enabled']) is bool, 'Invalid library policy')
    need(catalog_id(value['target_library']) and isinstance(value['revision'], str)
         and re.fullmatch(r'[0-9a-f]{40,64}', value['revision']), 'Invalid library policy')
    subjects = value['subjects']
    min_subjects = 1 if value['enabled'] else 0
    need(type(subjects) is list and min_subjects <= len(subjects) <= 4
         and all(isinstance(s, str) and HEX64.fullmatch(s) for s in subjects)
         and len(set(subjects)) == len(subjects), 'Invalid library policy')
    libs = value['libraries']
    need(type(libs) is dict and set(libs) == set(LIBRARY_SPECS), 'Invalid library policy')
    for name, spec in LIBRARY_SPECS.items():
        entry = libs[name]
        req_keys = {'library_id', 'kind', 'arr', 'descriptor', 'root_folder_path', 'quality_profile_id'}
        opt_keys = {'minimum_availability'} if name == 'movies' else set()
        need(type(entry) is dict and req_keys <= set(entry) <= (req_keys | opt_keys), 'Invalid library policy')
        need(entry['library_id'] == spec['library_id']
             and entry['kind'] == spec['kind']
             and entry['arr'] == spec['arr'], 'Invalid library policy')
        safe_path(entry['descriptor'])
        safe_path(entry['root_folder_path'])
        need(integer(entry['quality_profile_id'], 1, 2 ** 31 - 1), 'Invalid library policy')
        if 'minimum_availability' in entry:
            need(entry['minimum_availability'] in ('announced', 'inCinemas', 'released'), 'Invalid library policy')
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
    if 'recommendations' in value:
        recs = value['recommendations']
        need(type(recs) is dict and set(recs) == {
            'mdblist_key_file', 'state_dir', 'lists',
            'daily_ceiling', 'cache_ttl_seconds', 'min_refresh_age_seconds',
        }, 'Invalid library policy')
        safe_path(recs['mdblist_key_file'])
        safe_path(recs['state_dir'])
        lists = recs['lists']
        need(type(lists) is list and 1 <= len(lists) <= 16, 'Invalid library policy')
        seen_slugs = set()
        for entry in lists:
            need(type(entry) is dict and set(entry) == {'slug', 'kind'}, 'Invalid library policy')
            slug = entry['slug']
            need(isinstance(slug, str) and RECOMMENDATION_SLUG.fullmatch(slug) is not None
                 and slug not in seen_slugs, 'Invalid library policy')
            need(entry['kind'] in RECOMMENDATION_KINDS, 'Invalid library policy')
            seen_slugs.add(slug)
        need(integer(recs['daily_ceiling'], 1, 100)
             and integer(recs['cache_ttl_seconds'], 21600, 86400)
             and integer(recs['min_refresh_age_seconds'], 3600, 21600), 'Invalid library policy')
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
    elif operation == 'library-recommendations':
        need(fields <= {'kind', 'source', 'limit', 'include_in_library', 'include_excluded', 'min_score', 'refresh'},
             'Invalid library request')
        if 'kind' in value:
            need(isinstance(value['kind'], str) and value['kind'] in RECOMMENDATION_FILTER_KINDS,
                 'Invalid library request')
        if 'source' in value:
            need(isinstance(value['source'], str) and value['source'] in RECOMMENDATION_SOURCES,
                 'Invalid library request')
        if 'limit' in value:
            need(integer(value['limit'], 1, 50), 'Invalid library request')
        if 'include_in_library' in value:
            need(type(value['include_in_library']) is bool, 'Invalid library request')
        if 'include_excluded' in value:
            need(type(value['include_excluded']) is bool, 'Invalid library request')
        if 'min_score' in value:
            need(integer(value['min_score'], 0, 100), 'Invalid library request')
        if 'refresh' in value:
            need(type(value['refresh']) is bool, 'Invalid library request')
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
        base_req = {'mode', 'library', 'arr_id', 'target', 'remove_torrents', 'keep_emby_ids'}
        if mode == 'apply':
            base_req |= {'operation_id', 'plan_sha256'}
        need(fields == base_req, 'Invalid library request')
        _check_library_name(value['library'])
        need(integer(value['arr_id']), 'Invalid library request')
        need(type(value['remove_torrents']) is bool, 'Invalid library request')
        keep_ids = value['keep_emby_ids']
        need(type(keep_ids) is list and len(keep_ids) <= 4096
             and all(catalog_id(i) for i in keep_ids)
             and len(set(keep_ids)) == len(keep_ids)
             and keep_ids == sorted(keep_ids),
             'Invalid library request')
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
        if route.startswith(('/api/v3/movie', '/api/v3/series', '/api/v3/episode',
                             '/api/v3/history', '/api/v3/importlist')):
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

    def get_optional(self, route):
        self.verify_version()
        status, data = self._request('GET', route, accept_statuses=(200, 404))
        if status == 404:
            return None
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
        stats = raw.get('statistics') if type(raw.get('statistics')) is dict else {}
        row['size_on_disk'] = int(stats.get('sizeOnDisk') or raw.get('sizeOnDisk') or 0) if in_library else 0
    else:
        row['tvdb_id'] = raw.get('tvdbId') if type(raw.get('tvdbId')) is int else None
        stats = raw.get('statistics') if type(raw.get('statistics')) is dict else {}
        row['size_on_disk'] = int(stats.get('sizeOnDisk') or 0) if in_library else 0
        row['seasons'] = _series_seasons_summary(raw.get('seasons'))
    return row


def _fill_lookup_stats(row, detail, kind):
    if type(detail) is not dict or detail.get('id') != row.get('arr_id'):
        # The in-library title vanished or no longer matches between the lookup
        # and its detail GET: report the statistics as unknown, never as zero.
        row['size_on_disk'] = None
        for season in row.get('seasons') or []:
            season['episode_count'] = None
            season['episode_file_count'] = None
        return
    stats = detail.get('statistics') if type(detail.get('statistics')) is dict else {}
    if kind == 'movie':
        row['size_on_disk'] = int(stats.get('sizeOnDisk') or detail.get('sizeOnDisk') or 0)
    else:
        row['size_on_disk'] = int(stats.get('sizeOnDisk') or 0)
        if type(detail.get('seasons')) is list:
            row['seasons'] = _series_seasons_summary(detail.get('seasons'))


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


def _relative_under_root(path, root):
    if not isinstance(path, str) or not isinstance(root, str) or not path or not root:
        return None
    r = root.rstrip('/')
    p = path.rstrip('/')
    if p == r:
        return ''
    if p.startswith(r + '/'):
        return p[len(r) + 1:]
    return None


def _emby_path_matches(emby_path, arr_path, arr_root=None):
    if not isinstance(emby_path, str) or not emby_path or not isinstance(arr_path, str) or not arr_path:
        return False
    ep = emby_path.rstrip('/')
    ap = arr_path.rstrip('/')
    if ep == ap:
        return True
    if arr_root:
        rel = _relative_under_root(ap, arr_root)
        if rel and (ep == arr_root.rstrip('/') + '/' + rel or ep.endswith('/' + rel)):
            return True
    # Radarr may mount /movies while Emby mounts /mnt/shared/media/movies (policy root_folder_path).
    if ap.startswith('/movies/'):
        rel_movies = ap[len('/movies/'):]
        if rel_movies:
            if arr_root and ep == arr_root.rstrip('/') + '/' + rel_movies:
                return True
            if ep.endswith('/' + rel_movies):
                return True
    return False


def _resolve_movie_emby_ids(emby, recipient, library_id, *, tmdb_id=None, item_path=None, file_path=None, arr_root=None):
    """Resolve Emby Movie IDs via provider-id query with path-mapping fallback.

    Returns (ids_list, ambiguous_bool). Raises on Emby transport/API errors.
    """
    def path_hits(rows):
        out = []
        for r in rows:
            eid = r.get('Id')
            rpath = r.get('Path') or ''
            if not eid or not rpath:
                continue
            if (
                (file_path and _emby_path_matches(rpath, file_path, arr_root))
                or (item_path and (_in_root(rpath, item_path) or _emby_path_matches(rpath, item_path, arr_root)))
            ):
                out.append(eid)
        return out

    if tmdb_id is not None:
        rows = emby._items(recipient, {
            'Recursive': 'true',
            'IncludeItemTypes': 'Movie',
            'ParentId': library_id,
            'Fields': 'Path,ProviderIds',
            'AnyProviderIdEquals': f'Tmdb.{tmdb_id}',
            'StartIndex': 0,
            'Limit': 50,
        }, 50)
        prov_rows = [
            r for r in rows
            if r.get('Id') and (
                not r.get('ProviderIds')
                or (r.get('ProviderIds') or {}).get('Tmdb') == str(tmdb_id)
            )
        ]
        if prov_rows:
            matched = path_hits(prov_rows)
            if len(matched) == 1:
                return matched, False
            if len(prov_rows) == 1:
                return [prov_rows[0]['Id']], False
            if matched:
                return matched, len(matched) > 1
            return [r['Id'] for r in prov_rows], True

    # Fallback: query movies in this library and match by exact or mapped path.
    all_rows = emby._items(recipient, {
        'Recursive': 'true',
        'IncludeItemTypes': 'Movie',
        'ParentId': library_id,
        'Fields': 'Path,ProviderIds',
        'StartIndex': 0,
        'Limit': 500,
    }, 500)
    matched = path_hits(all_rows)
    return matched, len(matched) > 1


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
    term = q.get('term')
    arr_id = q.get('arr_id')
    matched = []
    seen_ext = set()
    ext_field = 'tmdbId' if kind == 'movie' else 'tvdbId'

    def matches_term(r):
        if term is None:
            return True
        t_low = term.lower()
        title_match = t_low in str(r.get('title', '')).lower()
        imdb_match = t_low == str(r.get('imdbId') or '').lower()
        ext_match = t_low == str(r.get(ext_field) or '').lower()
        return title_match or imdb_match or ext_match

    if arr_id is not None:
        r = api.get_optional(f'{endpoint}/{arr_id}')
        if type(r) is dict and r.get('id') == arr_id and _in_root(r.get('path'), lib['root_folder_path']):
            if matches_term(r):
                matched.append(_project_lookup_row(r, kind, in_library=True))
    elif term is not None:
        lookup_route = f'{endpoint}/lookup?' + urllib.parse.urlencode({'term': term})
        lookup_raw = api.get(lookup_route)
        need(type(lookup_raw) is list, FAILURE)
        in_lib_rows = []
        new_rows = []
        for r in lookup_raw:
            if type(r) is not dict:
                continue
            ext_id = r.get(ext_field)
            if ext_id is not None and ext_id in seen_ext:
                continue
            if type(r.get('id')) is int and r['id'] > 0:
                if _in_root(r.get('path'), lib['root_folder_path']):
                    if ext_id is not None:
                        seen_ext.add(ext_id)
                    in_lib_rows.append(_project_lookup_row(r, kind, in_library=True))
                continue
            if ext_id is not None:
                seen_ext.add(ext_id)
            new_rows.append(_project_lookup_row(r, kind, in_library=False))
        matched = in_lib_rows + new_rows
    else:
        existing_raw = api.get(endpoint)
        need(type(existing_raw) is list, FAILURE)
        for r in existing_raw:
            if type(r) is dict and _in_root(r.get('path'), lib['root_folder_path']):
                matched.append(_project_lookup_row(r, kind, in_library=True))

    truncated = len(matched) > cap
    items = matched[:cap]
    if arr_id is None and term is not None:
        for row in items:
            if row['in_library'] and row['arr_id'] is not None:
                _fill_lookup_stats(row, api.get_optional(f"{endpoint}/{row['arr_id']}"), kind)
    return {
        'library': lib_name,
        'kind': kind,
        'items': items,
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
        file_obj = None
        if m.get('hasFile') and type(m.get('movieFile')) is dict:
            mf = m['movieFile']
            full_path = mf.get('path') or (m['path'].rstrip('/') + '/' + str(mf.get('relativePath') or ''))
            emby_item_id = None
            if emby is not None:
                try:
                    m_ids, ambig = _resolve_movie_emby_ids(
                        emby, call['recipient'], lib['library_id'],
                        tmdb_id=m.get('tmdbId'), item_path=m.get('path'),
                        file_path=full_path, arr_root=lib['root_folder_path'],
                    )
                    if not ambig and len(m_ids) == 1:
                        emby_item_id = m_ids[0]
                except Exception:
                    emby_item_id = None
            file_obj = _extract_file_info(mf, emby_item_id=emby_item_id)
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
            ep = api.get(f"/api/v3/episode/{q['episode_arr_id']}")
            need(type(ep) is dict and ep.get('id') == q['episode_arr_id']
                 and ep.get('seriesId') == q['arr_id'], FAILURE)
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
    truncated = False

    if wanted in (None, 'movies'):
        lib = block['libraries']['movies']
        api = arr_factory('radarr', lib['descriptor'], SECONDS['library-queue'])
        data = api.get('/api/v3/queue?page=1&pageSize=100&includeUnknownMovieItems=false')
        need(type(data) is dict and type(data.get('records')) is list, FAILURE)
        if type(data.get('totalRecords')) is int and data['totalRecords'] > len(data['records']):
            truncated = True
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
        tv_root = block['libraries']['tv']['root_folder_path']
        anime_root = block['libraries']['anime']['root_folder_path']
        data = api.get('/api/v3/queue?page=1&pageSize=100&includeUnknownSeriesItems=false&includeSeries=true')
        need(type(data) is dict and type(data.get('records')) is list, FAILURE)
        if type(data.get('totalRecords')) is int and data['totalRecords'] > len(data['records']):
            truncated = True
        series_lib = {}

        def classify_series(sid, embedded=None):
            if sid in series_lib:
                return series_lib[sid]
            s_obj = embedded if type(embedded) is dict and isinstance(embedded.get('path'), str) else None
            if s_obj is None:
                s_obj = api.get_optional(f'/api/v3/series/{sid}')
            lib_match = None
            if type(s_obj) is dict:
                if _in_root(s_obj.get('path'), anime_root):
                    lib_match = 'anime'
                elif _in_root(s_obj.get('path'), tv_root):
                    lib_match = 'tv'
            series_lib[sid] = lib_match
            return lib_match

        for r in data['records']:
            if type(r) is not dict:
                continue
            sid = r.get('seriesId')
            if type(sid) is not int:
                continue
            lib_name = classify_series(sid, r.get('series'))
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
                'arr_id': sid,
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

    return {'library': wanted, 'items': items, 'truncated': truncated}


def _is_image_subtitle(stream):
    codec = str(stream.get('Codec') or '').lower()
    if codec in IMAGE_SUBTITLE_CODECS:
        return True
    if stream.get('IsTextSubtitleStream') is False:
        return True
    return False


def _is_english_language(lang):
    return str(lang or '').lower() in ('eng', 'en', 'english')


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
            m_ids, ambig = _resolve_movie_emby_ids(
                emby, recipient, lib['library_id'],
                tmdb_id=m.get('tmdbId'), item_path=m.get('path'),
                file_path=target_path, arr_root=lib['root_folder_path'],
            )
            eid = m_ids[0] if not ambig and len(m_ids) == 1 else None
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
        if not streams:
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
        eng_text_non_forced = [
            st for st in streams
            if st['is_text'] and not st['forced'] and _is_english_language(st['language'])
        ]
        eng_text_any = [
            st for st in streams
            if st['is_text'] and _is_english_language(st['language'])
        ]
        eng_image = [
            st for st in streams
            if not st['is_text'] and _is_english_language(st['language'])
        ]
        if eng_text_non_forced:
            selected = eng_text_non_forced[0]
        elif eng_text_any:
            selected = eng_text_any[0]
        elif eng_image:
            eng_image_non_forced = [st for st in eng_image if not st['forced']]
            selected = eng_image_non_forced[0] if eng_image_non_forced else eng_image[0]
        else:
            return {
                'library': lib_name,
                'arr_id': q['arr_id'],
                'emby_item_id': item['Id'],
                'streams': streams,
                'selected_stream': None,
                'status': 'no_english_text',
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
        found = api.get('/api/v3/movie/lookup?' + urllib.parse.urlencode({'term': f'tmdb:{tmdb_id}'}))
        need(type(found) is list, FAILURE)
        match = next((m for m in found if type(m) is dict and m.get('tmdbId') == tmdb_id), None)
        need(match is not None, 'Title not found in Arr lookup')
        if type(match.get('id')) is int and match['id'] > 0:
            refuse('already_in_library', 'Title is already in library')
        min_avail = lib.get('minimum_availability', 'released')
        plan = {
            'library': lib_name,
            'kind': 'movie',
            'title': match.get('title') or '',
            'year': match.get('year') if type(match.get('year')) is int else None,
            'tmdb_id': tmdb_id,
            'imdb_id': match.get('imdbId') or None,
            'root_folder_path': lib['root_folder_path'],
            'quality_profile_id': lib['quality_profile_id'],
            'minimum_availability': min_avail,
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
            'minimumAvailability': min_avail,
            'monitored': q['monitor'] != 'none',
            'addOptions': {'searchForMovie': q['search_now']},
        }
        try:
            status, created = api.post('/api/v3/movie', payload, accept_statuses=(200, 201))
            need(status in (200, 201) and type(created) is dict and integer(created.get('id')), FAILURE)
        except Exception:
            raise LibraryPostDispatchError(
                'uncertain', 'Movie add dispatched; verify state in library detail', completed=[],
            ) from None
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
    found = api.get('/api/v3/series/lookup?' + urllib.parse.urlencode({'term': f'tvdb:{tvdb_id}'}))
    need(type(found) is list, FAILURE)
    match = next((s for s in found if type(s) is dict and s.get('tvdbId') == tvdb_id), None)
    need(match is not None, 'Title not found in Arr lookup')
    if type(match.get('id')) is int and match['id'] > 0:
        refuse('already_in_library', 'Title is already in library')
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
    try:
        status, created = api.post('/api/v3/series', payload, accept_statuses=(200, 201))
        need(status in (200, 201) and type(created) is dict and integer(created.get('id')), FAILURE)
    except Exception:
        raise LibraryPostDispatchError(
            'uncertain', 'Series add dispatched; verify state in library detail', completed=[],
        ) from None
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
    try:
        status, data = api.post(
            '/api/v3/release',
            {'guid': q['guid'], 'indexerId': q['indexer_id']},
            accept_statuses=(200, 201, 202),
        )
    except Exception:
        raise LibraryPostDispatchError(
            'uncertain', 'Release grab dispatched; verify state in library queue', completed=[],
        ) from None
    if status in (400, 404, 410):
        refuse('release_expired', 'Release no longer cached by Arr; search again')
    if status not in (200, 201, 202):
        raise LibraryPostDispatchError(
            'uncertain', 'Release grab response unexpected; verify state in library queue', completed=[],
        )
    return {
        'mode': 'apply',
        'operation_id': q['operation_id'],
        'plan': plan,
        'plan_sha256': digest,
        'applied': True,
        'grabbed': True,
    }


def _evaluate_torrents(qbit, hashes_ownership, *, remove_torrents, allowed_categories, min_ratio, min_seeding_days):
    if not hashes_ownership:
        return []
    hashes = sorted(hashes_ownership)
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
        exclusive, keep_reason = hashes_ownership[h]
        if not remove_torrents:
            action, reason = 'keep', 'remove_torrents_false'
        elif cat not in allowed_categories:
            action, reason = 'keep', 'category_not_allowed'
        elif not exclusive:
            action, reason = 'keep', keep_reason or 'partially_kept'
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


def _ep_file_id(ep):
    ef = ep.get('episodeFile')
    if ep.get('hasFile') and type(ef) is dict and type(ef.get('id')) is int and ef['id'] > 0:
        return ef['id']
    if type(ep.get('episodeFileId')) is int and ep['episodeFileId'] > 0:
        return ep['episodeFileId']
    return None


def _ep_has_file(ep):
    return bool(ep.get('hasFile')) or _ep_file_id(ep) is not None


def _fetch_arr_history_all(api, download_id, *, page_size=200, max_pages=50):
    """Fetch all paginated Arr history records for download_id.

    Returns (records_list, None) on verified completeness, or (None, reason)
    where reason is 'ownership_unverified' on transport/API failure or
    'ownership_incomplete' when totalRecords is missing, inconsistent, or
    pagination is exhausted/capped before reaching totalRecords.
    """
    records = []
    expected_total = None
    seen_ids = set()
    for page in range(1, max_pages + 1):
        qs = urllib.parse.urlencode({
            'downloadId': download_id,
            'page': page,
            'pageSize': page_size,
        })
        try:
            resp = api.get(f'/api/v3/history?{qs}')
        except Exception:
            return None, 'ownership_unverified'
        if type(resp) is not dict or type(resp.get('records')) is not list:
            return None, 'ownership_incomplete'
        if 'totalRecords' not in resp or not integer(resp.get('totalRecords'), 0):
            return None, 'ownership_incomplete'
        if 'page' in resp and resp.get('page') != page:
            return None, 'ownership_incomplete'
        total = resp['totalRecords']
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            return None, 'ownership_incomplete'
        page_recs = resp['records']
        if expected_total == 0:
            if page_recs:
                return None, 'ownership_incomplete'
            return [], None
        if not page_recs:
            return None, 'ownership_incomplete'
        for rec in page_recs:
            if type(rec) is not dict:
                return None, 'ownership_incomplete'
            rid = rec.get('id')
            if type(rid) is int:
                if rid in seen_ids:
                    return None, 'ownership_incomplete'
                seen_ids.add(rid)
            records.append(rec)
        if len(records) >= expected_total:
            if len(records) != expected_total:
                return None, 'ownership_incomplete'
            return records, None
    return None, 'ownership_incomplete'


def _check_torrent_exclusivity(block, arr_factory, candidate_hashes, *, kind, arr_id, affected_ep_ids, by_id):
    """Establish exclusive torrent ownership across both Radarr and Sonarr history."""
    ownership = {}
    if not candidate_hashes:
        return ownership
    radarr_lib = block['libraries']['movies']
    sonarr_lib = block['libraries']['tv']
    try:
        radarr_api = arr_factory('radarr', radarr_lib['descriptor'], SECONDS['library-remove'])
    except Exception:
        radarr_api = None
    try:
        sonarr_api = arr_factory('sonarr', sonarr_lib['descriptor'], SECONDS['library-remove'])
    except Exception:
        sonarr_api = None

    for dl in sorted(candidate_hashes):
        if radarr_api is None or sonarr_api is None:
            ownership[dl] = (False, 'ownership_unverified')
            continue
        r_recs, r_err = _fetch_arr_history_all(radarr_api, dl)
        if r_err is not None:
            ownership[dl] = (False, r_err)
            continue
        s_recs, s_err = _fetch_arr_history_all(sonarr_api, dl)
        if s_err is not None:
            ownership[dl] = (False, s_err)
            continue
        if not r_recs and not s_recs:
            ownership[dl] = (False, 'ownership_unverified')
            continue

        exclusive = True
        keep_reason = None
        positive_owner = False

        # Check all Radarr history records for this downloadId.
        for rec in r_recs:
            rec_dl = rec.get('downloadId')
            if rec_dl is not None and str(rec_dl).lower() != dl:
                exclusive, keep_reason = False, 'ownership_incomplete'
                break
            ev = rec.get('eventType')
            if ev in ('movieFileDeleted', 'downloadFailed', 'downloadIgnored'):
                continue
            mid = rec.get('movieId')
            if type(mid) is not int:
                exclusive, keep_reason = False, 'ownership_incomplete'
                break
            if kind != 'movie' or mid != arr_id:
                exclusive, keep_reason = False, 'shared_torrent'
                break
            if ev == 'downloadFolderImported':
                positive_owner = True

        # Check all Sonarr history records for this downloadId.
        if exclusive:
            for rec in s_recs:
                rec_dl = rec.get('downloadId')
                if rec_dl is not None and str(rec_dl).lower() != dl:
                    exclusive, keep_reason = False, 'ownership_incomplete'
                    break
                ev = rec.get('eventType')
                if ev in ('episodeFileDeleted', 'downloadFailed', 'downloadIgnored'):
                    continue
                sid = rec.get('seriesId')
                eid = rec.get('episodeId')
                if type(sid) is not int:
                    exclusive, keep_reason = False, 'ownership_incomplete'
                    break
                if kind != 'series' or sid != arr_id:
                    exclusive, keep_reason = False, 'shared_torrent'
                    break
                if ev == 'grabbed' and eid is None:
                    continue
                if type(eid) is not int:
                    exclusive, keep_reason = False, 'ownership_incomplete'
                    break
                if eid not in affected_ep_ids:
                    ep_row = by_id.get(eid)
                    if ep_row is None or _ep_has_file(ep_row):
                        exclusive, keep_reason = False, 'partially_kept'
                        break
                elif ev == 'downloadFolderImported':
                    positive_owner = True

        if exclusive and not positive_owner:
            exclusive, keep_reason = False, 'ownership_unverified'

        ownership[dl] = (exclusive, keep_reason)
    return ownership


def _emby_items_complete(emby, recipient, query, *, page_size=500, max_pages=20):
    """Fetch all paginated Emby Items for recipient, refusing emby_unresolved on incomplete paging."""
    rows = []
    expected_total = None
    start_index = 0
    for _ in range(max_pages):
        page_query = dict(query, EnableUserData='false', StartIndex=start_index, Limit=page_size)
        dto = emby._http('GET', '/Users/' + recipient + '/Items', page_query)
        if type(dto) is not dict or type(dto.get('Items')) is not list:
            refuse('emby_unresolved', 'Incomplete Emby item listing while verifying keep list')
        total = dto.get('TotalRecordCount')
        if not integer(total, 0):
            refuse('emby_unresolved', 'Incomplete Emby item listing while verifying keep list')
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            refuse('emby_unresolved', 'Incomplete Emby item listing while verifying keep list')
        page_items = dto['Items']
        if expected_total == 0:
            if page_items:
                refuse('emby_unresolved', 'Incomplete Emby item listing while verifying keep list')
            return []
        if not page_items:
            refuse('emby_unresolved', 'Incomplete Emby item listing while verifying keep list')
        for item in page_items:
            if type(item) is not dict:
                refuse('emby_unresolved', 'Incomplete Emby item listing while verifying keep list')
            rows.append(item)
        if len(rows) >= expected_total:
            if len(rows) != expected_total:
                refuse('emby_unresolved', 'Incomplete Emby item listing while verifying keep list')
            return rows
        start_index += len(page_items)
    refuse('emby_unresolved', 'Incomplete Emby item listing while verifying keep list')


def _resolve_remove_emby_ids(
    emby, recipient, library_id, kind, *,
    provider_key=None, provider_val=None, item_path=None,
    file_paths=None, arr_root=None, target='title', affected_eps=None,
    resolved_ep_file_paths=None,
):
    """Resolve Emby item IDs affected by a library-remove request (fail-closed)."""
    ids = set()
    file_paths = list(file_paths or ())
    resolved_ep_file_paths = dict(resolved_ep_file_paths or {})
    if kind == 'movie':
        first_file = file_paths[0] if file_paths else None
        m_ids, ambig = _resolve_movie_emby_ids(
            emby, recipient, library_id,
            tmdb_id=provider_val if provider_key == 'Tmdb' else None,
            item_path=item_path, file_path=first_file, arr_root=arr_root,
        )
        if ambig or not m_ids:
            refuse('emby_unresolved', 'Cannot unambiguously resolve movie in Emby to verify keep list')
        ids.update(m_ids)
        return ids

    s_query = {
        'Recursive': 'true',
        'IncludeItemTypes': 'Series',
        'ParentId': library_id,
        'Fields': 'Path,ProviderIds',
    }
    if provider_key and provider_val is not None:
        s_query['AnyProviderIdEquals'] = f'{provider_key}.{provider_val}'
    s_rows = _emby_items_complete(emby, recipient, s_query, page_size=50)
    matched_series = []
    for r in s_rows:
        eid = r.get('Id')
        if not eid:
            continue
        prov_hit = provider_key and provider_val is not None and (r.get('ProviderIds') or {}).get(provider_key) == str(provider_val)
        path_hit = item_path and _emby_path_matches(r.get('Path') or '', item_path, arr_root)
        if prov_hit or path_hit:
            matched_series.append(eid)
    if not matched_series and provider_key and provider_val is not None and item_path:
        # Fallback to path search across series in this library.
        s_all = _emby_items_complete(emby, recipient, {
            'Recursive': 'true',
            'IncludeItemTypes': 'Series',
            'ParentId': library_id,
            'Fields': 'Path,ProviderIds',
        }, page_size=500)
        for r in s_all:
            if r.get('Id') and _emby_path_matches(r.get('Path') or '', item_path, arr_root):
                matched_series.append(r['Id'])
    if len(set(matched_series)) != 1:
        refuse('emby_unresolved', 'Cannot unambiguously resolve series in Emby to verify keep list')
    series_id = matched_series[0]
    ids.add(series_id)

    affected_eps = list(affected_eps or ())
    affected_seasons = {
        int(e.get('seasonNumber'))
        for e in affected_eps
        if type(e.get('seasonNumber')) is int
    }
    season_rows = _emby_items_complete(emby, recipient, {
        'Recursive': 'true',
        'IncludeItemTypes': 'Season',
        'ParentId': series_id,
        'Fields': 'IndexNumber',
    }, page_size=200)
    for r in season_rows:
        eid = r.get('Id')
        if eid and (target == 'title' or r.get('IndexNumber') in affected_seasons):
            ids.add(eid)

    ep_rows = _emby_items_complete(emby, recipient, {
        'Recursive': 'true',
        'IncludeItemTypes': 'Episode',
        'ParentId': series_id,
        'Fields': 'Path,ParentIndexNumber,IndexNumber',
    }, page_size=500)
    if target == 'title':
        for r in ep_rows:
            if r.get('Id'):
                ids.add(r['Id'])

    # Every affected episode that has a file (including whole-series title removal)
    # must resolve unambiguously to one Emby episode item.
    for ep in affected_eps:
        se = (
            (int(ep['seasonNumber']), int(ep['episodeNumber']))
            if type(ep.get('seasonNumber')) is int and type(ep.get('episodeNumber')) is int
            else None
        )
        fid = _ep_file_id(ep)
        ep_fpath = resolved_ep_file_paths.get(fid) if fid is not None else None
        if not ep_fpath and ep.get('hasFile') and type(ep.get('episodeFile')) is dict:
            ef = ep['episodeFile']
            ep_fpath = ef.get('path') or (
                (item_path.rstrip('/') + '/' + str(ef.get('relativePath') or '')) if item_path else None
            )
        both_matches = []
        se_matches = []
        path_matches = []
        for r in ep_rows:
            eid = r.get('Id')
            if not eid:
                continue
            path_hit = bool(ep_fpath and _emby_path_matches(r.get('Path') or '', ep_fpath, arr_root))
            se_hit = bool(se is not None and (r.get('ParentIndexNumber'), r.get('IndexNumber')) == se)
            if path_hit and se_hit:
                both_matches.append(eid)
            elif se_hit:
                se_matches.append(eid)
            elif path_hit:
                path_matches.append(eid)
        if both_matches:
            chosen = set(both_matches)
        elif se_matches and path_matches:
            chosen = set(se_matches) | set(path_matches)
        elif se_matches:
            chosen = set(se_matches)
        else:
            chosen = set(path_matches)
        if _ep_has_file(ep) and len(chosen) != 1:
            refuse('emby_unresolved', 'Cannot unambiguously resolve affected episode in Emby to verify keep list')
        ids.update(chosen)
    return ids


def op_remove(call, block, arr_factory, qbit_factory, emby_factory):
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
    keep_set = set(q['keep_emby_ids'])
    files = []
    episode_ids_to_unmonitor = []
    episode_file_ids_to_delete = []
    delete_title = target == 'title'
    candidate_hashes = set()
    affected_eps = []
    affected_ep_ids = set()
    by_id = {}
    provider_key = None
    provider_val = None
    item_path = None

    resolved_ep_file_paths = {}

    if kind == 'movie':
        m = api.get(f"/api/v3/movie/{q['arr_id']}")
        need(type(m) is dict and m.get('id') == q['arr_id']
             and _in_root(m.get('path'), lib['root_folder_path']), FAILURE)
        title = m.get('title') or ''
        provider_key, provider_val, item_path = 'Tmdb', m.get('tmdbId'), m.get('path')
        mf = m.get('movieFile') if type(m.get('movieFile')) is dict else None
        mf_id = m.get('movieFileId') if type(m.get('movieFileId')) is int and m['movieFileId'] > 0 else None
        if m.get('hasFile') or mf is not None or mf_id is not None:
            if mf is None and mf_id is not None:
                try:
                    fetched = api.get(f'/api/v3/moviefile/{mf_id}')
                except Exception:
                    fetched = None
                if type(fetched) is dict and fetched.get('id') == mf_id:
                    mf = fetched
            if type(mf) is not dict:
                refuse('file_metadata_missing', 'Cannot resolve movie file metadata before building plan')
            raw_path = mf.get('path') or ''
            rel_path = mf.get('relativePath') or ''
            if not raw_path and not rel_path:
                refuse('file_metadata_missing', 'Movie file metadata is missing path')
            fpath = raw_path or (m['path'].rstrip('/') + '/' + str(rel_path))
            files.append({'id': int(mf.get('id') or mf_id or 0), 'path': fpath, 'size': int(mf.get('size') or 0)})
        history = api.get(f"/api/v3/history/movie?movieId={q['arr_id']}")
        need(type(history) is list, FAILURE)
        for h_row in history:
            if type(h_row) is not dict:
                continue
            dl = str(h_row.get('downloadId') or '').lower()
            if HEX40.fullmatch(dl):
                candidate_hashes.add(dl)
    else:
        s = api.get(f"/api/v3/series/{q['arr_id']}")
        need(type(s) is dict and s.get('id') == q['arr_id']
             and _in_root(s.get('path'), lib['root_folder_path']), FAILURE)
        title = s.get('title') or ''
        provider_key, provider_val, item_path = 'Tvdb', s.get('tvdbId'), s.get('path')
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

        seen_file_ids = set()
        for e in selected_eps:
            ef = e.get('episodeFile') if e.get('hasFile') and type(e.get('episodeFile')) is dict else None
            fid = _ep_file_id(e)
            if e.get('hasFile') and fid is None:
                refuse('file_metadata_missing', 'Episode has file but episodeFileId is missing')
            if fid is not None and fid not in seen_file_ids:
                if ef is None:
                    try:
                        fetched = api.get(f'/api/v3/episodefile/{fid}')
                    except Exception:
                        fetched = None
                    if type(fetched) is dict and fetched.get('id') == fid:
                        ef = fetched
                if type(ef) is not dict:
                    refuse('file_metadata_missing', f'Cannot resolve episode file metadata for episodeFileId {fid}')
                raw_path = ef.get('path') or ''
                rel_path = ef.get('relativePath') or ''
                if not raw_path and not rel_path:
                    refuse('file_metadata_missing', f'Episode file metadata is missing path for episodeFileId {fid}')
                fpath = raw_path or (s['path'].rstrip('/') + '/' + str(rel_path))
                seen_file_ids.add(fid)
                episode_file_ids_to_delete.append(fid)
                resolved_ep_file_paths[fid] = fpath
                files.append({'id': fid, 'path': fpath, 'size': int(ef.get('size') or 0)})
        episode_file_ids_to_delete.sort()

        # Expand selection to include all sibling episodes sharing any deleted multi-episode file.
        affected_ep_ids = {e['id'] for e in selected_eps}
        if seen_file_ids:
            for e in by_id.values():
                if _ep_file_id(e) in seen_file_ids:
                    affected_ep_ids.add(e['id'])
        affected_eps = [by_id[eid] for eid in sorted(affected_ep_ids)]
        episode_ids_to_unmonitor = sorted(affected_ep_ids)

        history = api.get(f"/api/v3/history/series?seriesId={q['arr_id']}")
        need(type(history) is list, FAILURE)
        for h_row in history:
            if type(h_row) is not dict:
                continue
            dl = str(h_row.get('downloadId') or '').lower()
            ep_id = h_row.get('episodeId')
            if HEX40.fullmatch(dl) and (ep_id in affected_ep_ids or target == 'title'):
                candidate_hashes.add(dl)

    if keep_set:
        emby = emby_factory()
        need(emby is not None, FAILURE)
        emby_ids = _resolve_remove_emby_ids(
            emby, call['recipient'], lib['library_id'], kind,
            provider_key=provider_key, provider_val=provider_val,
            item_path=item_path, file_paths=[f['path'] for f in files],
            arr_root=lib['root_folder_path'], target=target, affected_eps=affected_eps,
            resolved_ep_file_paths=resolved_ep_file_paths,
        )
        if emby_ids & keep_set:
            refuse('keep_listed', 'Title is protected by keep list')

    files.sort(key=lambda f: (f['path'], f['id']))
    if len(files) > block['limits']['remove_max_files']:
        refuse('file_limit_exceeded', 'Removal exceeds per-call file limit')

    hashes_ownership = _check_torrent_exclusivity(
        block, arr_factory, candidate_hashes,
        kind=kind, arr_id=q['arr_id'], affected_ep_ids=affected_ep_ids, by_id=by_id,
    )

    qbit = None
    torrents = []
    try:
        if hashes_ownership:
            qbit = qbit_factory(block['torrents'])
            qbit.login()
            torrents = _evaluate_torrents(
                qbit,
                hashes_ownership,
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
            'affected_episode_arr_ids': sorted(affected_ep_ids),
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

        completed = []
        unmonitored_count = 0
        if delete_title:
            try:
                if kind == 'movie':
                    st, _ = api.delete(f"/api/v3/movie/{q['arr_id']}?deleteFiles=true&addImportExclusion=false")
                else:
                    st, _ = api.delete(f"/api/v3/series/{q['arr_id']}?deleteFiles=true&addImportListExclusion=false")
                need(st in (200, 204), FAILURE)
            except Exception:
                raise LibraryPostDispatchError(
                    'uncertain', 'Title delete dispatched; verify state in library detail', completed=completed,
                ) from None
            completed.append('title_deleted')
        else:
            if episode_ids_to_unmonitor:
                try:
                    st, _ = api.put('/api/v3/episode/monitor', {
                        'episodeIds': episode_ids_to_unmonitor,
                        'monitored': False,
                    }, accept_statuses=(200, 202))
                    need(st in (200, 202), FAILURE)
                except Exception:
                    raise LibraryPostDispatchError(
                        'uncertain', 'Episode unmonitor dispatched; verify state in library detail', completed=completed,
                    ) from None
                unmonitored_count = len(episode_ids_to_unmonitor)
                completed.append(f'episodes_unmonitored:{unmonitored_count}')
            for fid in episode_file_ids_to_delete:
                try:
                    st, _ = api.delete(f'/api/v3/episodefile/{fid}')
                    need(st in (200, 204), FAILURE)
                except Exception:
                    code = 'partial' if completed else 'uncertain'
                    raise LibraryPostDispatchError(
                        code, 'Episode file delete failed after dispatch; verify state in library detail', completed=completed,
                    ) from None
                completed.append(f'episode_file_deleted:{fid}')

        to_remove = [t['hash'] for t in torrents if t['action'] == 'remove']
        to_keep = [t['hash'] for t in torrents if t['action'] != 'remove']
        if to_remove and qbit is not None:
            try:
                qbit.delete_torrents(to_remove)
            except Exception:
                code = 'partial' if completed else 'uncertain'
                raise LibraryPostDispatchError(
                    code, 'Torrent delete failed after media removal; verify state in library detail', completed=completed,
                ) from None
            completed.append(f'torrents_removed:{len(to_remove)}')

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


def read_mdblist_key(path):
    """Read the root-only mdblist API key file using directory-FD O_NOFOLLOW custody.

    All ancestors must be root-owned and not group/world writable; immediate
    parent must have no group write or world access (0700 or 0750); leaf must
    be a root-owned regular single-link file with mode 0400 or 0600.
    """
    fd = None
    try:
        p = PurePosixPath(path)
        need(p.is_absolute() and str(p) == path and '..' not in p.parts and len(p.parts) > 2,
             'Invalid mdblist key path')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in p.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            s = os.fstat(fd)
            need(s.st_uid == 0 and not (s.st_mode & 0o022), 'Unsafe mdblist key ancestor')
        parent_stat = os.fstat(fd)
        need(not (parent_stat.st_mode & 0o027), 'Unsafe mdblist key directory')
        leaf = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            s = os.fstat(leaf)
            need(stat.S_ISREG(s.st_mode) and s.st_uid == 0
                 and stat.S_IMODE(s.st_mode) in (0o400, 0o600)
                 and s.st_nlink == 1 and 0 < s.st_size <= 1024,
                 'Unsafe mdblist key file')
            with os.fdopen(leaf, 'rb', closefd=False) as stream:
                raw = stream.read(1025)
            need(0 < len(raw) <= 1024, 'Unsafe mdblist key file')
            key = raw.decode('utf-8').rstrip('\r\n')
            need(re.fullmatch(r'[A-Za-z0-9._-]{8,128}', key) is not None,
                 'Invalid mdblist API key')
            return key
        finally:
            os.close(leaf)
    except Refused:
        raise
    except (OSError, ValueError, TypeError, UnicodeError):
        raise Refused('mdblist key unavailable or unsafe') from None
    finally:
        if fd is not None:
            os.close(fd)


def _open_state_dir(path):
    """Open a root-owned 0700 state directory via directory FDs with O_NOFOLLOW."""
    fd = None
    try:
        p = PurePosixPath(path)
        need(p.is_absolute() and str(p) == path and '..' not in p.parts and len(p.parts) > 2,
             'Invalid recommendations state path')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in p.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            s = os.fstat(fd)
            need(s.st_uid == 0 and not (s.st_mode & 0o022), 'Unsafe recommendations state ancestor')
        need(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, 'Recommendations state directory must be 0700')
        out = fd
        fd = None
        return out
    except Refused:
        raise
    except (OSError, ValueError, TypeError):
        raise Refused('Recommendations state directory unavailable') from None
    finally:
        if fd is not None:
            os.close(fd)


def _read_state_json(dir_fd, filename, limit):
    """Read a root-owned 0600 JSON state file from dir_fd, or return None if absent."""
    try:
        leaf = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise Refused('Recommendations state unreadable') from None
    try:
        s = os.fstat(leaf)
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0
             and stat.S_IMODE(s.st_mode) == 0o600
             and s.st_nlink == 1 and s.st_size <= limit,
             'Unsafe recommendations state file')
        with os.fdopen(leaf, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        need(len(raw) <= limit, 'Recommendations state file too large')
        return strict_json(raw)
    except Refused:
        raise
    except (OSError, ValueError, TypeError, UnicodeError):
        raise Refused('Recommendations state unreadable') from None
    finally:
        os.close(leaf)


def _write_state_json(dir_fd, filename, payload):
    """Atomically write a root-owned 0600 JSON state file into dir_fd."""
    raw = json.dumps(payload, separators=(',', ':'), allow_nan=False, ensure_ascii=True).encode('utf-8')
    tmp_name = f'.{filename}.tmp.{os.getpid()}.{secrets.token_hex(6)}'
    tmp_fd = None
    try:
        tmp_fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        os.fchmod(tmp_fd, 0o600)
        view = memoryview(raw)
        written = 0
        while written < len(raw):
            n = os.write(tmp_fd, view[written:])
            need(n > 0, 'Recommendations state write failed')
            written += n
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = None
        os.replace(tmp_name, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except Refused:
        raise
    except OSError:
        raise Refused('Recommendations state write failed') from None
    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except OSError:
                pass


class MdblistClient:
    """Bounded HTTPS-only client for api.mdblist.com; no redirects, 4 MiB cap."""

    def __init__(self, api_key, *, request_guard, budget_seconds=60):
        need(isinstance(api_key, str) and len(api_key) >= 8, 'Invalid mdblist API key')
        need(callable(request_guard), 'Deadline guard required')
        self._key = api_key
        self._guard = request_guard
        self._deadline = time.monotonic() + min(budget_seconds, request_guard())
        self._ssl_context = ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt')
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=self._ssl_context),
        )

    def close(self):
        self._key = None

    def get_json(self, route, params=None):
        """GET one JSON endpoint on api.mdblist.com. Returns (status, data, rate_limit_remaining)."""
        need(self._key is not None and isinstance(route, str) and route.startswith('/'), FAILURE)
        remaining = min(self._deadline - time.monotonic(), self._guard())
        need(remaining > 0, 'Caller deadline exceeded')
        query = {'apikey': self._key}
        if params:
            for k, v in params.items():
                query[k] = str(v)
        url = f'{MDBLIST_ORIGIN}{route}?{urllib.parse.urlencode(query)}'
        parsed = urllib.parse.urlsplit(url)
        need(parsed.scheme == 'https' and parsed.netloc == 'api.mdblist.com', FAILURE)
        req = urllib.request.Request(url, headers={'Accept': 'application/json'}, method='GET')
        completed = queue.Queue(maxsize=1)
        opener = self._opener

        def _parse_remaining(headers):
            if headers is None:
                return None
            val = headers.get('X-RateLimit-Remaining')
            if isinstance(val, str) and val.strip().isdigit():
                return int(val.strip())
            return None

        def io():
            try:
                with opener.open(req, timeout=remaining) as resp:
                    status = resp.status
                    rl = _parse_remaining(resp.headers)
                    data = importer_response_bytes(resp, limit=MDBLIST_MAX_BYTES)
                completed.put((status, data, rl, False))
            except urllib.error.HTTPError as err:
                rl = _parse_remaining(err.headers)
                completed.put((err.code, b'', rl, False))
            except Exception:
                completed.put((None, b'', None, True))

        threading.Thread(target=io, daemon=True).start()
        try:
            status, data, rl, failed = completed.get(timeout=remaining)
        except queue.Empty:
            raise Refused('Caller deadline exceeded') from None
        need(not failed, FAILURE)
        need(time.monotonic() < self._deadline and self._guard() > 0, 'Caller deadline exceeded')
        if status == 429:
            return 429, None, rl
        need(status == 200 and bool(data), FAILURE)
        return 200, strict_json(data), rl


def _truncate_description(text, limit=300):
    if not isinstance(text, str):
        return ''
    cleaned = ' '.join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit - 3].rstrip() + '...'


def _opt_pos_int(value):
    if type(value) is int and 1 <= value <= 2 ** 31 - 1:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        if 1 <= parsed <= 2 ** 31 - 1:
            return parsed
    return None


def _opt_nonneg_int(value):
    if type(value) is int and 0 <= value <= 2 ** 31 - 1:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        if 0 <= parsed <= 2 ** 31 - 1:
            return parsed
    return None


def _opt_float(value):
    if type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value):
        return round(float(value), 2)
    return None


def _opt_str(value, max_len=128):
    if isinstance(value, str):
        s = value.strip()
        if s:
            return s[:max_len]
    return None


def _extract_mdblist_genres(raw_genres):
    if not isinstance(raw_genres, list):
        return []
    out = []
    for g in raw_genres:
        if isinstance(g, str) and g.strip():
            out.append(g.strip()[:64])
        elif isinstance(g, dict):
            name = g.get('title') or g.get('name')
            if isinstance(name, str) and name.strip():
                out.append(name.strip()[:64])
        if len(out) >= 16:
            break
    return out


def _extract_mdblist_scores(item):
    mdblist_score = None
    imdb_val = None
    imdb_votes = None
    tmdb_val = None
    ratings = item.get('ratings')
    if isinstance(ratings, list):
        for r in ratings:
            if type(r) is not dict:
                continue
            src = str(r.get('source') or '').strip().lower()
            if src == 'mdblist':
                v = r.get('score') if r.get('score') is not None else r.get('value')
                if type(v) in (int, float) and not isinstance(v, bool) and math.isfinite(v):
                    mdblist_score = int(round(v))
            elif src == 'imdb':
                if imdb_val is None:
                    imdb_val = _opt_float(r.get('value'))
                if imdb_votes is None:
                    imdb_votes = _opt_nonneg_int(r.get('votes'))
            elif src == 'tmdb':
                if tmdb_val is None:
                    v = r.get('value') if r.get('value') is not None else r.get('score')
                    tmdb_val = _opt_float(v)
    if mdblist_score is None:
        top_score = item.get('score')
        if type(top_score) in (int, float) and not isinstance(top_score, bool) and math.isfinite(top_score):
            mdblist_score = int(round(top_score))
    return {
        'mdblist': mdblist_score,
        'imdb': imdb_val,
        'imdb_votes': imdb_votes,
        'tmdb': tmdb_val,
    }


def _normalize_mdblist_item(raw, kind, mediatype, list_name):
    if type(raw) is not dict:
        return None
    mediatype = raw.get('mediatype', mediatype)
    if mediatype not in ('movie', 'show'):
        return None
    kind = 'anime' if kind == 'anime' else ('movie' if mediatype == 'movie' else 'series')
    title = _opt_str(raw.get('title'), 256)
    if not title:
        return None
    ids_obj = raw.get('ids') if type(raw.get('ids')) is dict else {}
    tmdb_id = _opt_pos_int(ids_obj.get('tmdb')) or _opt_pos_int(raw.get('id'))
    tvdb_id = _opt_pos_int(ids_obj.get('tvdb')) or _opt_pos_int(raw.get('tvdb_id'))
    imdb_id = _opt_str(ids_obj.get('imdb'), 32) or _opt_str(raw.get('imdb_id'), 32)
    year = _opt_pos_int(raw.get('release_year')) or _opt_pos_int(raw.get('year'))
    language = _opt_str(raw.get('language'), 32) or _opt_str(raw.get('spoken_language'), 32)
    country = _opt_str(raw.get('country'), 32)
    status = _opt_str(raw.get('status'), 64)
    runtime = _opt_nonneg_int(raw.get('runtime'))
    genres = _extract_mdblist_genres(raw.get('genre'))
    description = _truncate_description(raw.get('description') or '', 300)
    scores = _extract_mdblist_scores(raw)
    return {
        'title': title,
        'year': year,
        'kind': kind,
        'mediatype': mediatype,
        'ids': {'tmdb': tmdb_id, 'tvdb': tvdb_id, 'imdb': imdb_id},
        'language': _language_code(language),
        'country': country,
        'genres': genres,
        'description': description,
        'status': status,
        'runtime': runtime,
        'scores': scores,
        'rank': _opt_pos_int(raw.get('rank')),
        'sources': [list_name],
    }


def _language_code(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    common = {
        'english': 'en', 'japanese': 'ja', 'french': 'fr', 'german': 'de',
        'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
        'chinese': 'zh', 'mandarin': 'zh', 'cantonese': 'zh', 'korean': 'ko',
        'hindi': 'hi', 'arabic': 'ar', 'dutch': 'nl', 'swedish': 'sv',
        'norwegian': 'no', 'danish': 'da', 'finnish': 'fi', 'polish': 'pl',
        'turkish': 'tr', 'thai': 'th', 'indonesian': 'id', 'vietnamese': 'vi',
        'czech': 'cs', 'greek': 'el', 'hebrew': 'he', 'ukrainian': 'uk',
    }
    codes = set(common.values())
    return value if value in codes else common.get(value)


def _recommendation_score(item):
    scores = item['scores']
    value = scores.get('mdblist')
    if value is None:
        rating = scores.get('imdb')
        if rating is None:
            rating = scores.get('tmdb')
        value = rating * 10 if rating is not None else None
    return max(0, min(100, value)) if value is not None else None


def _radarr_language(raw):
    lang = raw.get('originalLanguage')
    if type(lang) is dict:
        return _language_code(lang.get('name'))
    return _language_code(lang)


def _normalize_radarr_rec(raw):
    if type(raw) is not dict or raw.get('isRecommendation') is not True:
        return None
    title = _opt_str(raw.get('title'), 256)
    tmdb_id = _opt_pos_int(raw.get('tmdbId'))
    if not title or tmdb_id is None:
        return None
    genres = [g.strip()[:64] for g in (raw.get('genres') or []) if isinstance(g, str) and g.strip()][:16]
    lang = _radarr_language(raw)
    is_ja = isinstance(lang, str) and lang.lower() in ('ja', 'japanese')
    is_anim = any(g.lower() == 'animation' for g in genres)
    kind = 'anime' if (is_ja and is_anim) else 'movie'
    ratings = raw.get('ratings') if type(raw.get('ratings')) is dict else {}
    imdb_r = ratings.get('imdb') if type(ratings.get('imdb')) is dict else {}
    tmdb_r = ratings.get('tmdb') if type(ratings.get('tmdb')) is dict else {}
    return {
        'title': title,
        'year': _opt_pos_int(raw.get('year')),
        'kind': kind,
        'mediatype': 'movie',
        'ids': {
            'tmdb': tmdb_id,
            'tvdb': None,
            'imdb': _opt_str(raw.get('imdbId'), 32),
        },
        'language': 'ja' if is_ja else lang,
        'country': None,
        'genres': genres,
        'description': _truncate_description(raw.get('overview') or '', 300),
        'status': _opt_str(raw.get('status'), 64),
        'runtime': _opt_nonneg_int(raw.get('runtime')),
        'scores': {
            'mdblist': None,
            'imdb': _opt_float(imdb_r.get('value')),
            'imdb_votes': _opt_nonneg_int(imdb_r.get('votes')),
            'tmdb': _opt_float(tmdb_r.get('value')),
        },
        'sources': ['radarr-recommendation'],
        '_radarr_existing': raw.get('isExisting') is True,
        '_radarr_excluded': raw.get('isExcluded') is True,
    }


def _dedup_key(item):
    mt = item.get('mediatype', 'movie')
    ids = item['ids']
    if mt == 'show':
        if ids.get('tvdb') is not None:
            return ('show', 'tvdb', ids['tvdb'])
        if ids.get('tmdb') is not None:
            return ('show', 'tmdb', ids['tmdb'])
        if ids.get('imdb') is not None:
            return ('show', 'imdb', ids['imdb'])
    else:
        if ids.get('tmdb') is not None:
            return ('movie', 'tmdb', ids['tmdb'])
        if ids.get('imdb') is not None:
            return ('movie', 'imdb', ids['imdb'])
    return (mt, 'title', item['title'].lower(), item.get('year'))


def _merge_recommendation(existing, incoming):
    for src in incoming['sources']:
        if src not in existing['sources']:
            existing['sources'].append(src)
    if existing['kind'] == 'movie' and incoming['kind'] == 'anime':
        existing['kind'] = 'anime'
    for id_key in ('tmdb', 'tvdb', 'imdb'):
        if existing['ids'].get(id_key) is None and incoming['ids'].get(id_key) is not None:
            existing['ids'][id_key] = incoming['ids'][id_key]
    for score_key in ('mdblist', 'imdb', 'imdb_votes', 'tmdb'):
        if existing['scores'].get(score_key) is None and incoming['scores'].get(score_key) is not None:
            existing['scores'][score_key] = incoming['scores'][score_key]
    if not existing.get('description') and incoming.get('description'):
        existing['description'] = incoming['description']
    if not existing.get('genres') and incoming.get('genres'):
        existing['genres'] = list(incoming['genres'])
    if existing.get('language') is None and incoming.get('language') is not None:
        existing['language'] = incoming['language']
    if existing.get('country') is None and incoming.get('country') is not None:
        existing['country'] = incoming['country']
    if existing.get('status') is None and incoming.get('status') is not None:
        existing['status'] = incoming['status']
    if existing.get('runtime') is None and incoming.get('runtime') is not None:
        existing['runtime'] = incoming['runtime']
    if incoming.get('_radarr_existing'):
        existing['_radarr_existing'] = True
    if incoming.get('_radarr_excluded'):
        existing['_radarr_excluded'] = True


def _recommendation_add_target(item):
    mt = item.get('mediatype', 'movie')
    ids = item['ids']
    if mt == 'movie':
        if ids.get('tmdb') is not None:
            return {'library': 'movies', 'tmdb_id': ids['tmdb']}
        return None
    if ids.get('tvdb') is not None:
        lib_name = 'anime' if item['kind'] == 'anime' else 'tv'
        return {'library': lib_name, 'tvdb_id': ids['tvdb']}
    return None


def _load_quota(dir_fd, today):
    raw = _read_state_json(dir_fd, 'quota.json', MDBLIST_QUOTA_MAX_BYTES)
    if type(raw) is dict and raw.get('date') == today and type(raw.get('calls')) is int and raw['calls'] >= 0:
        rl = raw.get('mdblist_remaining')
        return {
            'date': today,
            'calls': raw['calls'],
            'mdblist_remaining': rl if (type(rl) is int and rl >= 0) else None,
        }
    return {'date': today, 'calls': 0, 'mdblist_remaining': None}


def _load_cache(dir_fd):
    raw = _read_state_json(dir_fd, 'cache.json', MDBLIST_CACHE_MAX_BYTES)
    if (type(raw) is dict and type(raw.get('fetched_at_ts')) in (int, float)
            and isinstance(raw.get('fetched_at'), str)
            and type(raw.get('items')) is list
            and type(raw.get('lists')) is list):
        return raw
    return None


def op_recommendations(call, block, arr_factory, *, mdblist_factory=None, request_guard=lambda: 60):
    recs_cfg = block.get('recommendations')
    if recs_cfg is None:
        refuse('recommendations_disabled', 'Library recommendations not configured by policy')

    req = call['request']
    kind_filter = req.get('kind', 'all')
    source_filter = req.get('source', 'all')
    limit = req.get('limit', 20)
    include_in_library = req.get('include_in_library', False)
    include_excluded = req.get('include_excluded', False)
    min_score = req.get('min_score')
    want_refresh = req.get('refresh', False)

    daily_ceiling = recs_cfg['daily_ceiling']
    cache_ttl = recs_cfg['cache_ttl_seconds']
    min_refresh_age = recs_cfg['min_refresh_age_seconds']
    budget = SECONDS['library-recommendations']

    warnings = []
    now = time.time()
    today = time.strftime('%Y-%m-%d', time.gmtime(now))

    lock_deadline = time.monotonic() + budget
    dir_fd = _open_state_dir(recs_cfg['state_dir'])
    lock_fd = None
    try:
        lock_fd = os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
        ls = os.fstat(lock_fd)
        need(stat.S_ISREG(ls.st_mode) and ls.st_uid == 0 and stat.S_IMODE(ls.st_mode) == 0o600,
             'Unsafe recommendations lock file')
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = min(lock_deadline - time.monotonic(), request_guard())
                need(remaining > 0, 'Caller deadline exceeded waiting for recommendations lock')
                time.sleep(min(0.05, remaining))

        quota = _load_quota(dir_fd, today)
        cache = _load_cache(dir_fd)
        stale = False

        if source_filter in ('mdblist', 'all'):
            age_seconds = max(0, int(now - cache['fetched_at_ts'])) if cache is not None else None
            expired = cache is None or age_seconds >= cache_ttl
            forced_refresh = want_refresh and cache is not None and age_seconds >= min_refresh_age
            need_fetch = expired or forced_refresh
            if want_refresh and not need_fetch:
                warnings.append('refresh ignored: cache is younger than the minimum refresh age')

            if need_fetch:
                if quota['calls'] >= daily_ceiling:
                    if cache is not None:
                        stale = True
                        warnings.append('mdblist daily quota ceiling reached; serving cached recommendations')
                    else:
                        refuse('quota_exhausted', 'Daily mdblist quota ceiling reached and no cache is available')
                else:
                    client = None
                    try:
                        need(mdblist_factory is not None, FAILURE)
                        client = mdblist_factory(recs_cfg)
                        rate_limited = False
                        quota_hit = False

                        quota['calls'] += 1
                        _write_state_json(dir_fd, 'quota.json', quota)
                        status, user_lists, rl = client.get_json('/lists/user')
                        if rl is not None:
                            quota['mdblist_remaining'] = rl
                        _write_state_json(dir_fd, 'quota.json', quota)

                        if status == 429:
                            rate_limited = True
                        else:
                            need(type(user_lists) is list, FAILURE)
                            by_slug = {}
                            for entry in user_lists:
                                if type(entry) is dict and isinstance(entry.get('slug'), str):
                                    by_slug[entry['slug']] = entry

                            fetched_lists = []
                            fetched_items = []
                            for cfg_list in recs_cfg['lists']:
                                slug = cfg_list['slug']
                                cfg_kind = cfg_list['kind']
                                meta = by_slug.get(slug)
                                if meta is None:
                                    warnings.append(f'Configured mdblist slug not found: {slug}')
                                    continue
                                list_id = _opt_pos_int(meta.get('id'))
                                if list_id is None:
                                    warnings.append(f'Configured mdblist slug has invalid id: {slug}')
                                    continue
                                if quota['calls'] >= daily_ceiling:
                                    quota_hit = True
                                    break
                                quota['calls'] += 1
                                _write_state_json(dir_fd, 'quota.json', quota)
                                st_items, payload, rl = client.get_json(
                                    f'/lists/{list_id}/items',
                                    params={
                                        'limit': 500,
                                        'append_to_response': 'genre,description,ratings',
                                    },
                                )
                                if rl is not None:
                                    quota['mdblist_remaining'] = rl
                                _write_state_json(dir_fd, 'quota.json', quota)
                                if st_items == 429:
                                    rate_limited = True
                                    break
                                need(type(payload) is dict, FAILURE)
                                list_name = _opt_str(meta.get('name'), 128) or slug
                                list_mediatype = _opt_str(meta.get('mediatype'), 32) or (
                                    'movie' if cfg_kind == 'movie' else 'show'
                                )
                                list_updated = _opt_str(meta.get('last_updated_at'), 64)
                                movies_arr = payload.get('movies') if type(payload.get('movies')) is list else []
                                shows_arr = payload.get('shows') if type(payload.get('shows')) is list else []
                                count = 0
                                for raw_item in movies_arr:
                                    norm = _normalize_mdblist_item(raw_item, cfg_kind, 'movie', list_name)
                                    if norm is not None:
                                        fetched_items.append(norm)
                                        count += 1
                                for raw_item in shows_arr:
                                    norm = _normalize_mdblist_item(raw_item, cfg_kind, 'show', list_name)
                                    if norm is not None:
                                        fetched_items.append(norm)
                                        count += 1
                                fetched_lists.append({
                                    'name': list_name,
                                    'mediatype': list_mediatype,
                                    'item_count': _opt_nonneg_int(meta.get('items')) if meta.get('items') is not None else count,
                                    'updated_at': list_updated,
                                })

                            if not rate_limited and not quota_hit:
                                fetched_at_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))
                                cache = {
                                    'fetched_at': fetched_at_iso,
                                    'fetched_at_ts': now,
                                    'lists': fetched_lists,
                                    'items': fetched_items,
                                    'warnings': list(warnings),
                                }
                                _write_state_json(dir_fd, 'cache.json', cache)
                                stale = False

                        if rate_limited:
                            if cache is not None:
                                stale = True
                                warnings.append('mdblist returned HTTP 429; serving cached recommendations')
                            else:
                                refuse('quota_exhausted', 'mdblist rate limit reached and no cache is available')
                        elif quota_hit:
                            if cache is not None:
                                stale = True
                                warnings.append('mdblist daily quota ceiling reached; serving cached recommendations')
                            else:
                                refuse('quota_exhausted', 'Daily mdblist quota ceiling reached and no cache is available')
                    except LibraryRefusal:
                        raise
                    except Exception:
                        if cache is None:
                            raise Refused('mdblist request failed and no cache is available') from None
                        stale = True
                        warnings.append('mdblist request failed; serving cached recommendations')
                    finally:
                        if client is not None:
                            client.close()

        if cache is not None:
            warnings = list(dict.fromkeys(warnings + cache.get('warnings', [])))
        cache_meta = {
            'fetched_at': cache['fetched_at'] if cache is not None else None,
            'age_seconds': max(0, int(now - cache['fetched_at_ts'])) if cache is not None else None,
            'stale': stale,
        }
        quota_meta = {
            'mdblist_calls_today': quota['calls'],
            'daily_ceiling': daily_ceiling,
            'mdblist_remaining': quota['mdblist_remaining'],
        }
        cached_lists = list(cache['lists']) if cache is not None else []
        mdblist_items = copy.deepcopy(cache['items']) if (cache is not None and source_filter in ('mdblist', 'all')) else []
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        os.close(dir_fd)

    # Fetch live Arr state (membership, exclusions, auto-add warnings, and optional Radarr recommendations).
    radarr = arr_factory('radarr', block['libraries']['movies']['descriptor'], budget)
    sonarr = arr_factory('sonarr', block['libraries']['tv']['descriptor'], budget)

    radarr_movies = radarr.get('/api/v3/movie')
    need(type(radarr_movies) is list, FAILURE)
    radarr_tmdb_ids = {
        m['tmdbId'] for m in radarr_movies
        if type(m) is dict and type(m.get('tmdbId')) is int
    }

    radarr_exclusions = radarr.get('/api/v3/exclusions')
    need(type(radarr_exclusions) is list, FAILURE)
    radarr_excluded_ids = {
        e['tmdbId'] for e in radarr_exclusions
        if type(e) is dict and type(e.get('tmdbId')) is int
    }

    radarr_importlists = radarr.get('/api/v3/importlist')
    need(type(radarr_importlists) is list, FAILURE)
    for il in radarr_importlists:
        if type(il) is dict and il.get('enabled') is True and il.get('enableAuto') is True:
            name = _opt_str(il.get('name'), 128) or str(il.get('id', '?'))
            warnings.append(f"Radarr import list '{name}' has automatic add enabled")

    radarr_rec_items = []
    if source_filter in ('radarr', 'all'):
        raw_recs = radarr.get('/api/v3/importlist/movie?includeRecommendations=true&includeTrending=false&includePopular=false')
        need(type(raw_recs) is list, FAILURE)
        for raw_rec in raw_recs:
            norm = _normalize_radarr_rec(raw_rec)
            if norm is not None:
                radarr_rec_items.append(norm)

    sonarr_series = sonarr.get('/api/v3/series')
    need(type(sonarr_series) is list, FAILURE)
    sonarr_tvdb_ids = {
        s['tvdbId'] for s in sonarr_series
        if type(s) is dict and type(s.get('tvdbId')) is int
    }

    sonarr_exclusions = sonarr.get('/api/v3/importlistexclusion')
    need(type(sonarr_exclusions) is list, FAILURE)
    sonarr_excluded_ids = {
        e['tvdbId'] for e in sonarr_exclusions
        if type(e) is dict and type(e.get('tvdbId')) is int
    }

    sonarr_importlists = sonarr.get('/api/v3/importlist')
    need(type(sonarr_importlists) is list, FAILURE)
    for il in sonarr_importlists:
        if type(il) is dict and il.get('enableAutomaticAdd') is True:
            name = _opt_str(il.get('name'), 128) or str(il.get('id', '?'))
            warnings.append(f"Sonarr import list '{name}' has automatic add enabled")

    merged = {}
    for item in mdblist_items + radarr_rec_items:
        key = _dedup_key(item)
        if key in merged:
            _merge_recommendation(merged[key], item)
        else:
            merged[key] = item

    results = []
    for item in merged.values():
        mt = item.get('mediatype', 'movie')
        ids = item['ids']
        if mt == 'movie':
            in_lib = (ids.get('tmdb') in radarr_tmdb_ids) or bool(item.get('_radarr_existing'))
            excluded = (ids.get('tmdb') in radarr_excluded_ids) or bool(item.get('_radarr_excluded'))
        else:
            in_lib = ids.get('tvdb') in sonarr_tvdb_ids
            excluded = ids.get('tvdb') in sonarr_excluded_ids

        if kind_filter != 'all' and item['kind'] != kind_filter:
            continue
        if not include_in_library and in_lib:
            continue
        if excluded and not include_excluded:
            continue
        if min_score is not None:
            sc = _recommendation_score(item)
            if sc is None or sc < min_score:
                continue

        results.append({
            'title': item['title'],
            'year': item['year'],
            'kind': item['kind'],
            'ids': dict(item['ids']),
            'language': item['language'],
            'country': item['country'],
            'genres': list(item['genres']),
            'description': item['description'],
            'status': item['status'],
            'runtime': item['runtime'],
            'rank': item.get('rank'),
            'score': _recommendation_score(item),
            'scores': dict(item['scores']),
            'sources': list(item['sources']),
            'in_library': in_lib,
            'excluded': excluded,
            'add': _recommendation_add_target(item),
        })

    def _sort_key(row):
        score = row['score']
        votes = row['scores'].get('imdb_votes')
        return (
            -(score if score is not None else -1),
            row.get('rank') if row.get('rank') is not None else 2 ** 31,
            -(votes if votes is not None else -1),
            row['title'].lower(),
        )

    results.sort(key=_sort_key)
    results = results[:limit]

    return {
        'items': results,
        'lists': cached_lists,
        'cache': cache_meta,
        'quota': quota_meta,
        'warnings': warnings,
    }


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

    def mdblist_factory(recs_cfg):
        key = read_mdblist_key(recs_cfg['mdblist_key_file'])
        return MdblistClient(key, request_guard=guard, budget_seconds=SECONDS['library-recommendations'])

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
        if op == 'library-recommendations':
            return op_recommendations(call, block, arr_factory, mdblist_factory=mdblist_factory, request_guard=guard)
        if op == 'library-add':
            return op_add(call, block, arr_factory)
        if op == 'library-grab':
            return op_grab(call, block, arr_factory)
        if op == 'library-remove':
            return op_remove(call, block, arr_factory, qbit_factory, emby_factory)
        need(False, 'Unsupported operation')
    finally:
        for client in arr_clients.values():
            client.close()
        if emby_client[0] is not None:
            emby_client[0].close()
