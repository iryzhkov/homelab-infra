"""Default-off typed staged acquisition. Stateless root side; Go owns the journal.

No public input selects a route, credential, category or path. Existing media and
torrents are never deleted, imported, moved or reconfigured.
"""
import copy
import datetime
import hashlib
import json
import os
import re
import time
import urllib.parse

from emby_operator_adapter import (ImporterInventoryAdapter, OperatorAdapter,
    importer_key, load_config, load_credentials, private_read,
    strict_json, catalog_id, printable)
from emby_operator_recommendations import Refused
from emby_operator_torrents import descriptor as torrent_descriptor, path, within

PREFIX = '/mnt/shared/downloads/staging/holonet'
CATEGORY = 'holonet-stage'
RESOURCE = 'homelab-qbit-stage'
READ = 'janitor.acquire.read'
START = 'janitor.acquire.start'
MEDIA = 'janitor.acquire.media'
OPERATIONS = tuple('janitor-acquire-' + x for x in
                   ('inspect', 'search', 'start', 'status', 'verify'))
SECONDS = dict(zip(OPERATIONS, (20, 60, 30, 10, 60)))
HEX = {n: re.compile('[0-9a-f]{' + str(n) + '}') for n in (32, 40, 64)}
FAILURE = 'Acquisition source unavailable'


def need(value):
    if not value:
        raise Refused(FAILURE)


def exact(value, keys):
    need(type(value) is dict and set(value) == set(keys.split()))


def integer(value, low=1, high=2 ** 31 - 1):
    return type(value) is int and low <= value <= high


def text(value, limit):
    return printable(value, limit) and not any(
        x in value.lower() for x in ('://', 'magnet:', 'apikey=', 'password='))


def hexid(value, n):
    return isinstance(value, str) and HEX[n].fullmatch(value) is not None


def instant(value):
    need(isinstance(value, str) and len(value) <= 40 and
         re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)', value))
    try:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        raise Refused(FAILURE) from None


def episodes(value):
    need(type(value) is list and 1 <= len(value) <= 64)
    last, ids = 0, set()
    for e in value:
        exact(e, 'sonarr_episode_id episode_number')
        need(integer(e['sonarr_episode_id']) and integer(e['episode_number'], 1, 9999)
             and e['episode_number'] > last and e['sonarr_episode_id'] not in ids)
        last = e['episode_number']
        ids.add(e['sonarr_episode_id'])


def target(value):
    exact(value, 'series_id library_id season tvdb_id sonarr_series_id')
    need(catalog_id(value['series_id']) and catalog_id(value['library_id']) and
         integer(value['season'], 0, 99) and
         isinstance(value['tvdb_id'], str) and
         re.fullmatch('[1-9][0-9]{0,9}', value['tvdb_id']) and
         integer(value['sonarr_series_id']))


def request(operation, value):
    need(operation in OPERATIONS)
    if operation.endswith(('inspect', 'search')):
        exact(value, 'series_id library_id season' +
              (' limit' if operation.endswith('search') else ''))
        need(catalog_id(value['series_id']) and catalog_id(value['library_id']) and
             integer(value['season'], 0, 99))
        if 'limit' in value:
            need(integer(value['limit'], 1, 25))
    elif operation.endswith('start'):
        exact(value, 'search_id handle operation_id target search_expires_at '
              'preview_expires_at expected_episodes expected')
        need(hexid(value['search_id'], 32) and hexid(value['handle'], 64))
        exact(value['expected'], 'title size info_hash')
        need(text(value['expected']['title'], 256) and
             integer(value['expected']['size'], 1, 1 << 40) and
             hexid(value['expected']['info_hash'], 40))
        instant(value['search_expires_at'])
        instant(value['preview_expires_at'])
    elif operation.endswith('verify'):
        exact(value, 'operation_id info_hash target expected_episodes')
        need(hexid(value['info_hash'], 40))
    else:
        exact(value, 'operation_id info_hash')
        need(hexid(value['info_hash'], 40))
    if 'operation_id' in value:
        need(hexid(value['operation_id'], 32))
    if 'target' in value:
        target(value['target'])
        episodes(value['expected_episodes'])
    return copy.deepcopy(value)


def validate_block(value):
    exact(value, 'target_library revision sonarr torrents series_libraries '
          'start_enabled media_enabled')
    need(catalog_id(value['target_library']) and isinstance(value['revision'], str)
         and re.fullmatch('[0-9a-f]{40,64}', value['revision']))
    for k in ('sonarr', 'torrents'):
        path(value[k])
    need(type(value['series_libraries']) is list and
         1 <= len(value['series_libraries']) <= 2 and
         len(set(value['series_libraries'])) == len(value['series_libraries']) and
         all(catalog_id(x) for x in value['series_libraries']) and
         type(value['start_enabled']) is bool and type(value['media_enabled']) is bool)
    return copy.deepcopy(value)


def actions(operation):
    return [READ] + ([START] if operation.endswith(('start', 'status')) else
                     [MEDIA] if operation.endswith('verify') else [])


def base(call, qualification, resource):
    c = call['caller']
    return dict(qualification=qualification, resource_id=resource, server=c['server'],
                library=c['target_library'], policy_revision=c['policy_revision'],
                media_removal_authorized=False)


def audio_claim(title):
    # Strip subtitle phrases first. Dual/multi alone says nothing about English.
    subtitle = r'(?i)\b(english|eng)[ ._-]*(soft|hard)?sub\w*\b|\b(soft|hard)?sub\w*[ ._-]+(english|eng)\b'
    cleaned = re.sub(subtitle, '', title)
    if re.search(r'(?i)\b(?:eng|english)[-_. ]+(?:audio|dub(?:bed)?)\b|'
                 r'\b(?:audio|dub(?:bed)?)[-_. ]+(?:eng|english)\b', cleaned):
        return 'english_audio_claimed'
    if re.search(subtitle, title):
        return 'subtitle_only_claimed'
    return 'unknown'


class Sonarr(ImporterInventoryAdapter):
    """Existing in-place key custody; only internally assembled GETs."""
    def __init__(self, config, guard):
        config = copy.deepcopy(config)
        group = config.pop('app_config_read_gid', None)
        super().__init__(config, request_guard=guard)
        need(config['backend'] == 'sonarr')
        if group is not None:
            need(type(group) is int and group == 988 and config['app_config_owner_uid'] == 1002)
            self._config['app_config_read_gid'] = group
        self._deadline = time.monotonic() + min(60, guard())
        self.routes = {'/api/v3/system/status', '/api/v3/rootfolder',
                       '/api/v3/downloadclient', '/api/v3/remotepathmapping',
                       '/api/v3/config/importlist'}
        self._key = importer_key(self._config)

    def _get_routes(self):
        return self.routes

    def _call_limit(self):
        return 14

    def selected(self, kind, **query):
        need(kind in ('series', 'episode', 'release'))
        allowed = {'series': {'tvdbId'}, 'episode': {'seriesId', 'seasonNumber', 'includeEpisodeFile'},
                   'release': {'seriesId', 'seasonNumber'}}
        need(set(query) == allowed[kind])
        route = '/api/v3/' + kind + '?' + urllib.parse.urlencode(query)
        self.routes.add(route)
        return self._get(route)

    def _response_limit(self, route):
        return 2 * 1024 * 1024 if route.startswith('/api/v3/release?') else 256 * 1024

    def close(self):
        self._key = None


def rows(value, cap):
    need(type(value) is list and len(value) <= cap and
         all(type(x) is dict for x in value))
    return value


def binding(result):
    b = result['binding']
    return dict(series_id=b['emby_series_id'], library_id=b['emby_library_id'],
                season=b['season'], tvdb_id=b['tvdb_id'],
                sonarr_series_id=b['sonarr_series_id'])


def episode_set(result):
    return [{k: e[k] for k in ('sonarr_episode_id', 'episode_number')}
            for e in result['episodes']]


def inspect(call, block, api, emby):
    q = call['request'].get('target', call['request'])
    need(q['library_id'] in block['series_libraries'])
    # Existing library GUID/name/type and recipient visibility checks, with a
    # dedicated ProviderIds field query. No household/history inference.
    libraries = emby._series_libraries([q['library_id']])
    need(q['library_id'] in libraries)
    found = emby._items(call['recipient'], {
        'Recursive': 'true', 'IncludeItemTypes': 'Series', 'ParentId': q['library_id'],
        'Ids': q['series_id'], 'StartIndex': 0, 'Limit': 2, 'Fields': 'ProviderIds'}, 1)
    need(len(found) == 1 and found[0].get('Id') == q['series_id'] and
         found[0].get('Type') == 'Series')
    tvdb = found[0].get('ProviderIds', {}).get('Tvdb')
    need(isinstance(tvdb, str) and re.fullmatch('[1-9][0-9]{0,9}', tvdb))
    status = api._get('/api/v3/system/status')
    need(type(status) is dict and status.get('version') == api._config['expected_version'])
    series = rows(api.selected('series', tvdbId=tvdb), 1)
    need(len(series) == 1 and series[0].get('tvdbId') == int(tvdb))
    s = series[0]
    need(integer(s.get('id')) and text(s.get('title'), 256))
    path(s.get('path'))
    raw = rows(api.selected('episode', seriesId=s['id'], seasonNumber=q['season'],
                            includeEpisodeFile='true'), 64)
    eps = []
    for e in raw:
        need(e.get('seriesId') == s['id'] and e.get('seasonNumber') == q['season'] and
             integer(e.get('id')) and integer(e.get('episodeNumber'), 1, 9999) and
             type(e.get('monitored')) is bool and type(e.get('hasFile')) is bool)
        f = None
        if e['hasFile']:
            f = e.get('episodeFile')
            need(type(f) is dict)
            path(f.get('relativePath'), False)
            need(integer(f.get('size'), 0, 1 << 50))
            mi = f.get('mediaInfo') or {}
            quality = f.get('quality', {}).get('quality', {}).get('name')
            need(text(quality, 64))
            for key in ('audioLanguages', 'subtitles'):
                need(mi.get(key) is None or text(mi[key], 256))
            f = dict(relative_path=f['relativePath'], size=f['size'],
                     audio_languages=mi.get('audioLanguages'), subtitles=mi.get('subtitles'),
                     quality=quality)
        eps.append(dict(episode_number=e['episodeNumber'], sonarr_episode_id=e['id'],
                        monitored=e['monitored'], has_file=e['hasFile'], file=f))
    eps.sort(key=lambda e: e['episode_number'])
    episodes(episode_set({'episodes': eps}))
    roots = []
    for r in rows(api._get('/api/v3/rootfolder'), 16):
        roots.append(path(r.get('path')))
    clients = []
    for c in rows(api._get('/api/v3/downloadclient'), 16):
        need(text(c.get('name'), 128) and text(c.get('implementation'), 128) and
             type(c.get('enable')) is bool and c.get('protocol') in ('torrent', 'usenet', 'unknown')
             and type(c.get('removeCompletedDownloads')) is bool)
        # Only one normal, exact category field; never copy generic settings.
        fields = rows(c.get('fields'), 128)
        category = [f.get('value') for f in fields
                    if f.get('name') == 'tvCategory' and f.get('privacy') == 'normal']
        need(len(category) <= 1)
        cat = category[0] if category and category[0] else None
        need(cat is None or text(cat, 64))
        clients.append(dict(name=c['name'], implementation=c['implementation'],
            enabled=c['enable'], protocol=c['protocol'], category=cat,
            remove_completed_downloads=c['removeCompletedDownloads']))
    mappings = []
    for m in rows(api._get('/api/v3/remotepathmapping'), 16):
        need(text(m.get('host'), 256))
        mappings.append(dict(host=m['host'], remote_path=path(m.get('remotePath').rstrip('/')),
                             local_path=path(m.get('localPath').rstrip('/'))))
    cleanup = api._get('/api/v3/config/importlist')
    need(type(cleanup) is dict)
    level = cleanup.get('listSyncLevel')
    need(level is None or text(level, 64))
    paths = roots + [s['path']] + [m['local_path'] for m in mappings]
    result = base(call, 'acquire_inspect_v1', api._config['resource_id'])
    result.update(binding=dict(emby_series_id=q['series_id'], emby_library_id=q['library_id'],
        tvdb_id=tvdb, sonarr_series_id=s['id'], sonarr_title=s['title'],
        sonarr_path=s['path'], season=q['season']), episodes=eps,
        sonarr_import_surface=dict(root_folders=roots, download_clients=clients,
            remote_path_mappings=mappings, list_sync_level=level),
        stage_safety=dict(prefix=PREFIX, category=CATEGORY,
            outside_root_folders=not any(within(x, PREFIX) or within(PREFIX, x) for x in paths),
            category_unused_by_arr=not any(c['enabled'] and c['category'] in (None, CATEGORY) for c in clients)))
    return result


def search(call, block, api, emby):
    observed = inspect(call, block, api, emby)
    t = binding(observed)
    source = rows(api.selected('release', seriesId=t['sonarr_series_id'],
                               seasonNumber=t['season']), 500)
    projected, private = [], {}
    for r in source:
        if (r.get('protocol') != 'torrent' or r.get('fullSeason') is not True or
                r.get('seasonNumber') != t['season'] or
                r.get('mappedSeriesId') != t['sonarr_series_id']):
            continue
        need(text(r.get('title'), 256) and text(r.get('indexer'), 64) and
             integer(r.get('size'), 1, 1 << 40) and isinstance(r.get('guid'), str)
             and len(r['guid']) <= 4096 and integer(r.get('indexerId')))
        digest_tuple = [t, r['indexerId'], r['guid'], r['title'], r['size'], r.get('infoHash')]
        handle = hashlib.sha256(json.dumps(digest_tuple, sort_keys=True,
            separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
        need(handle not in private)
        nums = r.get('episodeNumbers')
        need(type(nums) is list and len(nums) <= 64 and
             all(integer(n, 1, 9999) for n in nums) and nums == sorted(set(nums)))
        langs = rows(r.get('languages'), 8)
        languages = [l.get('name') for l in langs]
        need(all(text(x, 64) for x in languages))
        seeders = r.get('seeders')
        need(seeders is None or integer(seeders, 0, 2 ** 53 - 1))
        infohash = r.get('infoHash')
        need(infohash is None or hexid(infohash, 40))
        claim = audio_claim(r['title'])
        projected.append(dict(handle=handle, title=r['title'], indexer=r['indexer'],
            size=r['size'], seeders=seeders, season=t['season'], full_season=True,
            episode_numbers=nums, languages=languages, info_hash=infohash,
            audio_claim=claim, audio_evidence='Explicit release title claim; not audio verification'
            if claim == 'english_audio_claimed' else 'English audio unqualified'))
        private[handle] = r
    projected.sort(key=lambda r: (-(r['seeders'] or 0), r['handle']))
    limit = call['request'].get('limit', 25)
    result = base(call, 'acquire_search_v1', api._config['resource_id'])
    result.update(inspect=observed, releases=projected[:limit])
    return result, private


def download(raw_url, origins, guard):
    """One in-memory torrent fetch; no redirects, proxies or surfaced URL."""
    import queue
    import threading
    import urllib.request
    from emby_operator_adapter import NoRedirect, importer_response_bytes
    need(isinstance(raw_url, str) and len(raw_url) <= 8192 and
         not any(c in raw_url for c in '\r\n\x00'))
    u = urllib.parse.urlsplit(raw_url)
    need(u.scheme == 'https' and u.hostname and not u.username and not u.password and
         not u.fragment and 'https://' + u.netloc in origins)
    timeout = min(10, guard())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    completed = queue.Queue(maxsize=1)
    def read():
        try:
            req = urllib.request.Request(raw_url, headers={'Accept': 'application/x-bittorrent'})
            with opener.open(req, timeout=timeout) as response:
                need(response.status == 200 and response.geturl() == raw_url)
                raw = importer_response_bytes(response, limit=8 * 1024 * 1024)
            completed.put(raw)
        except Exception:
            completed.put(None)
    threading.Thread(target=read, daemon=True).start()
    try:
        raw = completed.get(timeout=timeout)
    except queue.Empty:
        need(False)
    need(guard() > 0 and raw is not None)
    return raw


def stage_directory(operation_id):
    """Exclusive empty operation directory. No existing name is adopted."""
    import stat
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in PREFIX.split('/')[1:]:
            nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            need(not os.fstat(fd).st_mode & 0o002)
        owner = os.fstat(fd)
        need(owner.st_uid != 0 and owner.st_gid == 988 and
             stat.S_IMODE(owner.st_mode) in (0o770, 0o2770))
        os.mkdir(operation_id, mode=0o700, dir_fd=fd)
        child = os.open(operation_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            # The worker owns the new directory. A member may select the existing
            # media group without CAP_CHOWN; qBit uses group access, not uid adoption.
            need(owner.st_gid in os.getgroups())
            os.fchown(child, -1, owner.st_gid)
            os.fchmod(child, 0o770)
            need(os.listdir(child) == [])
        finally:
            os.close(child)
    finally:
        os.close(fd)


def start(call, block, api, emby, config, origins):
    from emby_operator_acquire_torrents import StageTorrent, metainfo
    q = call['request']
    result = base(call, 'acquire_start_v1', RESOURCE)
    result.update(operation_id=q['operation_id'], stage='not_sent',
        reason='source_unavailable', info_hash=None, torrent_name=None,
        save_path=None, category=CATEGORY, files=None, calls=0)
    client, torrent_files, torrent_name = None, None, None
    def expires():
        need(time.time() < instant(q['search_expires_at']) and
             time.time() < instant(q['preview_expires_at']))
    try:
        expires()
        observed, releases = search(call, block, api, emby)
        inspection = observed['inspect']
        need(binding(inspection) == q['target'] and
             episode_set(inspection) == q['expected_episodes'] and
             all(inspection['stage_safety'][k] for k in
                 ('outside_root_folders', 'category_unused_by_arr')))
        release = releases.get(q['handle'])
        need(type(release) is dict and release.get('title') == q['expected']['title'] and
             release.get('size') == q['expected']['size'] and
             release.get('infoHash') == q['expected']['info_hash'] and
             audio_claim(release['title']) == 'english_audio_claimed')
        raw = download(release.get('downloadUrl'), origins, api._guard)
        digest, torrent_name, torrent_files, private = metainfo(raw)
        need(digest == q['expected']['info_hash'] and private == 0 and
             sum(f['size'] for f in torrent_files) == q['expected']['size'])
        client = StageTorrent(config, q['operation_id'], digest, api._guard, starting=True)
        client.deadline = min(client.deadline, api._deadline)
        def before_add():
            expires()
            api._guard()
            # The parent packet must separately qualify all Arr consumers,
            # aliases, duplicate semantics and share-limit behavior before
            # enabling start. This per-operation creation is not a global fence.
            stage_directory(q['operation_id'])
        client.add(raw, before_add)
        result.update(stage='started', reason=None, info_hash=digest,
            torrent_name=torrent_name, save_path=PREFIX + '/' + q['operation_id'],
            files=torrent_files)
    except Exception:
        if client is not None and client.add_may_send:
            result.update(stage='acknowledged' if getattr(client, 'acknowledged', False)
                          else 'dispatch_unknown',
                          reason=None if getattr(client, 'acknowledged', False) else 'add_uncertain',
                          info_hash=q['expected']['info_hash'])
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass  # A logout failure never erases an effective/uncertain add.
            result['calls'] = client.calls
    return result


def execute(policy, call, guard, config):
    """No state directory: each invocation owns and destroys its fresh sessions."""
    from emby_operator_acquire_torrents import StageTorrent
    block = validate_block(policy['janitor_acquire'])
    op, q = call['operation'], call['request']
    request(op, q)
    guard()
    torrent_config = torrent_descriptor(strict_json(private_read(block['torrents'])))
    need(torrent_config['resource_id'] == RESOURCE and
         torrent_config['categories'] == [CATEGORY] and
         torrent_config['payload_prefixes'] == [PREFIX])
    if op.endswith('status'):
        return StageTorrent(torrent_config, q['operation_id'], q['info_hash'], guard).status(call['caller'])
    descriptor = strict_json(private_read(block['sonarr']))
    need(type(descriptor) is dict and 'acquire_download_origins' in descriptor)
    origins = descriptor.pop('acquire_download_origins')
    need(type(origins) is list and 1 <= len(origins) <= 4 and len(set(origins)) == len(origins))
    for origin in origins:
        need(isinstance(origin, str) and len(origin) <= 256)
        u = urllib.parse.urlsplit(origin)
        need(u.scheme == 'https' and u.hostname and not u.username and not u.password and
             not u.path and not u.query and not u.fragment and origin == 'https://' + u.netloc)
    api, emby = None, None
    try:
        api = Sonarr(descriptor, guard)
        emby = OperatorAdapter(config, request_guard=guard)
        credentials = load_credentials(config['credential_file'])
        try:
            emby.authenticate(credentials)
        finally:
            credentials.clear()
        emby.verified_identity(block['series_libraries'])
        if op.endswith('inspect'):
            return inspect(call, block, api, emby)
        if op.endswith('search'):
            return search(call, block, api, emby)[0]
        if op.endswith('start'):
            return start(call, block, api, emby, torrent_config, origins)
        from emby_operator_acquire_media import verify
        return verify(call, block, api, emby,
            StageTorrent(torrent_config, q['operation_id'], q['info_hash'], guard))
    finally:
        if api is not None:
            api.close()
        if emby is not None:
            emby.close()
