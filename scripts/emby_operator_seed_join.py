"""Stat-only seed/library hardlink pair for one Radarr movie file. Default-off.

Contract jocasta:c3070dfe196e7f67732749db4b279689@3. The worker resolves both
current paths itself: three fixed Radarr GETs, then the existing bounded
qBittorrent read, whose logout completes before any filesystem step. Each host
path is then walked twice from / with O_PATH, no-follow, directory-FD opens and
fstat only. No media file is opened for I/O, read or hashed.

linked is one instantaneous same-host, same-mount inode relation. It never
means continuing seeding, durable identity, snapshot atomicity, reclaimable
space or removal authority. Results and errors carry no path or stat value.
"""
import errno
import os
import re
import stat
import time

from emby_operator_adapter import (ImporterInventoryAdapter, existing_importer_key,
                                   private_read, strict_json, validate_importer_config)
from emby_operator_custody import beneath, mount_id, normalized
from emby_operator_recommendations import Refused, require
from emby_operator_torrents import TorrentAdapter, descriptor as torrent_descriptor, json_body

OPERATION = 'janitor-seed-join-stat'
ACTIONS = ['janitor.seed_join.read']
QUALIFICATION = 'seed_join_stat_v1'
# The user-approved fixed ceiling and the existing Movies host root.
SEED_CEILING = '/mnt/shared/downloads/complete/movies'
LIBRARY_HOST = '/mnt/shared/media/movies'
LIBRARY_APP_ALIAS = '/movies'
REASONS = ('source_unqualified', 'missing', 'denied', 'unsafe_path', 'changed',
           'incomparable', 'unavailable')
MAX_PATH_BYTES = 4096
MAX_COMPONENTS = 64
MAX_FILE_BYTES = 1 << 40
MAX_ID = 2 ** 31 - 1
MAX_FILE_INDEX = 255
# Search permission only; an O_PATH descriptor permits no read.
DIRECTORY = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
LEAF = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
MISSING = frozenset({errno.ENOENT, errno.ESTALE})
DENIED = frozenset({errno.EACCES, errno.EPERM})
UNSAFE = frozenset({errno.ELOOP, errno.ENOTDIR})


class Unknown(Exception):
    """A closed backend or filesystem outcome. Carries only the reason enum."""
    def __init__(self, reason):
        assert reason in REASONS
        super().__init__(reason)
        self.reason = reason


def _mapping(value):
    require(type(value) is dict and set(value) == {'app_prefix', 'host_prefix'},
            'Invalid seed join policy')
    return normalized(value['app_prefix']), normalized(value['host_prefix'])


def _disjoint(left, right):
    return left != right and not beneath(left, right) and not beneath(right, left)


def validate_block(policy):
    """Root ceiling consistency only. No filesystem or backend access."""
    block = policy.get('janitor_seed_join')
    torrents, importers = policy.get('janitor_torrents'), policy.get('janitor_importers')
    require(type(block) is dict and set(block) ==
            {'target_library', 'revision', 'library_root', 'seed_root'} and
            type(torrents) is dict and type(importers) is dict and
            type(importers.get('backends')) is dict and 'radarr' in importers['backends'],
            'Invalid seed join policy')
    require(isinstance(block['target_library'], str) and isinstance(block['revision'], str) and
            re.fullmatch('[0-9a-f]{40,64}', block['revision']) is not None and
            block['target_library'] == torrents.get('target_library') ==
            importers.get('target_library') == policy['caller']['library'] and
            block['revision'] == torrents.get('revision') == importers.get('revision'),
            'Seed join policy binding mismatch')
    library_app, library_host = _mapping(block['library_root'])
    seed_app, seed_host = _mapping(block['seed_root'])
    require(seed_app == seed_host == SEED_CEILING and library_host == LIBRARY_HOST and
            library_app in (LIBRARY_APP_ALIAS, LIBRARY_HOST),
            'Seed join roots outside the fixed ceiling')
    require(all(_disjoint(a, b) for a in (library_app, library_host)
                for b in (seed_app, seed_host)), 'Overlapping seed join roots')
    custody = policy.get('janitor_custody')
    if custody is not None:
        radarr = custody['backends'].get('radarr')
        require(radarr is None or radarr['roots'][0] == block['library_root'],
                'Seed join library root differs from custody')


def request(value):
    require(type(value) is dict and set(value) == {'arr_movie_id', 'torrent_hash', 'file_index'} and
            type(value['arr_movie_id']) is int and 1 <= value['arr_movie_id'] <= MAX_ID and
            type(value['torrent_hash']) is str and
            re.fullmatch('[0-9a-f]{40}', value['torrent_hash']) is not None and
            type(value['file_index']) is int and 0 <= value['file_index'] <= MAX_FILE_INDEX,
            'Invalid seed join request')
    return value


def _source(condition):
    if not condition:
        raise Unknown('source_unqualified')


def _app_path(value):
    try:
        return normalized(value)
    except Refused:
        raise Unknown('source_unqualified') from None


class SeedJoinArrAdapter(ImporterInventoryAdapter):
    """Three fixed Radarr GETs under the existing ten-second importer budget."""
    def __init__(self, config, movie_id, *, request_guard):
        super().__init__(config, request_guard=request_guard)
        require(self._config['backend'] == 'radarr', 'Seed join backend mismatch')
        self.movie_id = movie_id
        self.routes = ('/api/v3/system/status', '/api/v3/movie/%d' % movie_id,
                       '/api/v3/moviefile?movieId=%d' % movie_id)

    def _get_routes(self):
        return self.routes

    def _call_limit(self):
        return 3

    def observe(self, library_app):
        """Radarr's one current file path and size, beneath the library root."""
        require(time.monotonic() < self._deadline and self._guard() > 0,
                'Importer deadline reached')
        try:
            options = ({'read_gid': self._config['app_config_read_gid']}
                       if 'app_config_read_gid' in self._config else {})
            self._key = existing_importer_key(self._config['app_config_file'],
                                             self._config['app_config_owner_uid'], **options)
            status = self._get(self.routes[0])
            _source(type(status) is dict and status.get('appName') == 'Radarr' and
                    status.get('version') == self._config['expected_version'] and
                    status.get('urlBase') in (None, ''))
            movie = self._get(self.routes[1])
            rows = self._get(self.routes[2])
        finally:
            self._key = None
        _source(type(movie) is dict and type(movie.get('id')) is int and
                movie['id'] == self.movie_id)
        movie_path = _app_path(movie.get('path'))
        _source(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict)
        row = rows[0]
        _source(type(row.get('movieId')) is int and row['movieId'] == self.movie_id and
                type(row.get('size')) is int and 0 <= row['size'] <= MAX_FILE_BYTES)
        path = _app_path(row.get('path'))
        _source(beneath(path, movie_path) and beneath(path, library_app))
        return path, row['size']


def seed_file(observed, file_index, seed_app):
    """The one complete torrent file path and size, beneath the seed root."""
    _source(observed.get('client_reports_complete') is True)
    files = [f for f in observed['files'] if f['index'] == file_index]
    _source(len(files) == 1 and files[0]['progress'] == 1)
    path = _app_path(observed['save_path'] + '/' + files[0]['name'])
    _source(beneath(path, seed_app))
    _source(len(observed['files']) != 1 or path == observed['content_path'])
    return path, files[0]['size']


def host_path(app_path, expected, mappings):
    """host_prefix + suffix under exactly the expected declared mapping."""
    selected = [m for m in mappings if beneath(app_path, m['app_prefix'])]
    if selected != [expected]:
        raise Unknown('unsafe_path')
    return expected['host_prefix'] + app_path[len(expected['app_prefix']):]


def observe(fd):
    """fstat and the fdinfo mount ID of one O_PATH descriptor; metadata only."""
    return os.fstat(fd), mount_id(fd)


def walk(path, depth, guard):
    """Fresh no-follow traversal from /. Returns (anchors, stamp); closes every fd.

    depth is the component count of the mapped root, whose mount every later
    component must share. At most two descriptors are open at once.
    """
    parts = path.split('/')[1:]
    if not (path.startswith('/') and len(path.encode('utf-8')) <= MAX_PATH_BYTES and
            depth < len(parts) <= MAX_COMPONENTS and
            all(p not in ('', '.', '..') for p in parts)):
        raise Unknown('unsafe_path')
    guard()
    fd = os.open('/', DIRECTORY)
    try:
        info, mount = observe(fd)
        anchors = [(info.st_dev, info.st_ino, info.st_mode, mount)]
        root_mount = None
        for index, part in enumerate(parts, 1):
            guard()
            following = os.open(part, LEAF if index == len(parts) else DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = following
            info, mount = observe(fd)
            anchors.append((info.st_dev, info.st_ino, info.st_mode, mount))
            if index == depth:
                root_mount = mount
            elif index > depth and mount != root_mount:
                raise Unknown('unsafe_path')  # Nested mount beneath the root.
        if not stat.S_ISREG(info.st_mode):
            raise Unknown('unsafe_path')  # Includes a no-follow leaf symlink.
        return anchors, (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                         info.st_nlink, info.st_mtime_ns, info.st_ctime_ns, mount)
    finally:
        os.close(fd)


def stat_pair(library, seed, guard):
    """library and seed are (host path, root depth, source size). Returns a status."""
    try:
        first = walk(library[0], library[1], guard), walk(seed[0], seed[1], guard)
        second = walk(library[0], library[1], guard), walk(seed[0], seed[1], guard)
    except OSError as error:
        if error.errno in MISSING:
            raise Unknown('missing') from None
        if error.errno in DENIED:
            raise Unknown('denied') from None
        if error.errno in UNSAFE:
            raise Unknown('unsafe_path') from None
        raise Unknown('unavailable') from None
    if first != second:
        raise Unknown('changed')
    left, right = first[0][1], first[1][1]
    if left[3] != library[2] or right[3] != seed[2]:
        raise Unknown('changed')
    if left[0] != right[0] or left[7] != right[7]:
        raise Unknown('incomparable')
    if left[1] == right[1]:
        # One inode seen through both names must agree on all observed metadata.
        if left == right and left[4] >= 2:
            return 'linked'
        raise Unknown('incomparable')
    return 'not_linked'


def _stopped(guard):
    return bool(getattr(guard, 'tripped', None)) or bool(
        getattr(guard, 'expired', lambda: False)())


def join(policy, call, guard):
    """Only called after the socket worker's independent authorization.

    Root configuration failures raise. Backend and filesystem failures return
    unknown with one closed reason, unless the caller deadline or cancellation
    stopped the call, which raises.
    """
    guard()
    validate_block(policy)
    block = policy['janitor_seed_join']
    caller, value = call['caller'], request(call['request'])
    torrent_config = torrent_descriptor(json_body(private_read(policy['janitor_torrents']['config'])))
    require(block['seed_root']['app_prefix'] in torrent_config['payload_prefixes'],
            'Seed join descriptor mismatch')
    arr_config = strict_json(private_read(policy['janitor_importers']['backends']['radarr']))
    validate_importer_config(arr_config)
    require(arr_config['backend'] == 'radarr', 'Seed join backend mismatch')
    result = {'qualification': QUALIFICATION, 'server': caller['server'],
              'library': caller['target_library'], 'policy_revision': caller['policy_revision'],
              'torrent_resource_id': torrent_config['resource_id'],
              'arr_movie_id': value['arr_movie_id'], 'torrent_hash': value['torrent_hash'],
              'file_index': value['file_index'], 'status': 'unknown', 'reason': None,
              'observed_at_unix_ms': None, 'media_removal_authorized': False}
    mappings = [block['library_root'], block['seed_root']]
    try:
        library_app, library_size = SeedJoinArrAdapter(
            arr_config, value['arr_movie_id'], request_guard=guard).observe(
            block['library_root']['app_prefix'])
        # The existing adapter logs out before returning or raising.
        observed = TorrentAdapter(torrent_config, request_guard=guard).inventory(
            value['torrent_hash'], caller)
        seed_app, seed_size = seed_file(observed, value['file_index'],
                                        block['seed_root']['app_prefix'])
        library = host_path(library_app, mappings[0], mappings)
        seed = host_path(seed_app, mappings[1], mappings)
        result['status'] = stat_pair(
            (library, mappings[0]['host_prefix'].count('/'), library_size),
            (seed, mappings[1]['host_prefix'].count('/'), seed_size), guard)
    except Unknown as outcome:
        result['reason'] = outcome.reason
    except Exception:
        if _stopped(guard):
            raise
        result['reason'] = 'unavailable'
    guard()
    result['observed_at_unix_ms'] = int(time.time() * 1000)
    return result
