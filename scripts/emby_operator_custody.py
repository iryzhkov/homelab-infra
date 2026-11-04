"""Read-only, fixed-root Arr byte observations. Never deletion authority.

A continuation is a position in an observed manifest, not proof that earlier
chunks were read. The private Go consumer must accumulate every chunk itself.
Stable stat metadata does not establish an immutable NFS snapshot or seed paths.
"""
import base64
import contextlib
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import stat
import time

from emby_operator_adapter import (
    ImporterInventoryAdapter, existing_importer_key, private_read, strict_json)
from emby_operator_recommendations import Refused, require

OPERATION = 'janitor-custody-attest'
ACTIONS = ['janitor.importers.read', 'janitor.custody.read']
MAX_FILES = 64
CHUNK_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 1 << 40
MAX_REPLY_BYTES = 240 * 1024  # Leave room for the worker envelope.
KINDS = {'radarr': 'movie', 'sonarr': 'series'}


def normalized(path):
    require(type(path) is str and 1 < len(path.encode('utf-8')) <= 4096 and
            path.startswith('/') and not path.startswith('//') and
            str(PurePosixPath(path)) == path and
            all(p not in ('.', '..') for p in path.split('/')[1:]),
            'Invalid custody path')
    return path


def beneath(path, root):
    return path.startswith(root + '/')


def validate_block(block, importer):
    require(type(block) is dict and set(block) == {'backends'} and
            type(block['backends']) is dict and 1 <= len(block['backends']) <= 2 and
            set(block['backends']) <= set(importer['backends']),
            'Invalid custody policy')
    all_hosts = []
    for entry in block['backends'].values():
        require(type(entry) is dict and set(entry) == {'roots', 'recycle'} and
                type(entry['roots']) is list and 1 <= len(entry['roots']) <= 4,
                'Invalid custody policy')
        apps, hosts = [], []
        for mapping in [*entry['roots'], entry['recycle']]:
            require(type(mapping) is dict and
                    set(mapping) == {'app_prefix', 'host_prefix'}, 'Invalid custody mapping')
            apps.append(normalized(mapping['app_prefix']))
            hosts.append(normalized(mapping['host_prefix']))
        for values in (apps, hosts):
            for i, left in enumerate(values):
                require(not any(left == right or beneath(left, right) or beneath(right, left)
                                for right in values[i + 1:]), 'Overlapping custody roots')
        all_hosts.extend(hosts)
    # Different backends cannot alias each other's declared custody roots.
    require(len(set(all_hosts)) == len(all_hosts), 'Duplicate custody roots')
    for i, left in enumerate(all_hosts):
        require(not any(beneath(left, right) or beneath(right, left)
                        for right in all_hosts[i + 1:]), 'Overlapping custody roots')


def request(value):
    require(type(value) is dict and
            {'backend', 'target_kind', 'target_id'} <= set(value) <=
            {'backend', 'target_kind', 'target_id', 'cursor'} and
            type(value['backend']) is str and value['backend'] in KINDS and
            value['target_kind'] == KINDS[value['backend']] and
            type(value['target_id']) is int and 1 <= value['target_id'] < 2 ** 31,
            'Invalid custody request')
    if 'cursor' in value:
        require(type(value['cursor']) is str and len(value['cursor']) <= 512 and
                re.fullmatch('[A-Za-z0-9_-]+', value['cursor']),
                'Invalid custody cursor')
    return value


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(compact(value)).hexdigest()


def mount_id(fd):
    # Fixed kernel metadata, never a caller-selected file.
    with open('/proc/self/fdinfo/%d' % fd, encoding='ascii') as stream:
        rows = [line.split()[1] for line in stream if line.startswith('mnt_id:')]
    require(len(rows) == 1 and rows[0].isdigit(), 'Custody mount identity unavailable')
    return int(rows[0])


def identity(fd):
    info = os.fstat(fd)
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink, mount_id(fd)]


def open_absolute(path, guard):
    """Pin every component without following links. Caller closes returned fd."""
    normalized(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    anchors = [identity(fd)]
    try:
        for part in PurePosixPath(path).parts[1:]:
            guard()
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY |
                              os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            anchors.append(identity(fd))
        return fd, anchors
    except BaseException:
        os.close(fd)
        raise


class Root:
    def __init__(self, path, guard):
        self.path, self.guard = path, guard
        self.fd, self.anchors = open_absolute(path, guard)
        self.mount = self.anchors[-1][-1]

    def close(self):
        os.close(self.fd)

    def check(self):
        fd, anchors = open_absolute(self.path, self.guard)
        try:
            require(anchors == self.anchors, 'Custody roots changed')
        finally:
            os.close(fd)

    def file(self, relative):
        require(relative and not relative.startswith('/') and
                all(part not in ('', '.', '..') for part in relative.split('/')),
                'Invalid custody relative path')
        fd = os.dup(self.fd)
        try:
            parts = relative.split('/')
            for index, part in enumerate(parts):
                self.guard()
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if index != len(parts) - 1:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
                require(mount_id(fd) == self.mount, 'Custody nested mount refused')
            require(stat.S_ISREG(os.fstat(fd).st_mode), 'Custody file is not regular')
            return fd
        except BaseException:
            os.close(fd)
            raise


class CustodyAdapter(ImporterInventoryAdapter):
    """Four fixed GETs and one bounded chunk. No writes or worker journal."""
    def __init__(self, config, entry, value, *, request_guard):
        self.value = request(value)
        self.entry = entry
        super().__init__(config, request_guard=request_guard)
        require(self._config['backend'] == value['backend'], 'Custody backend mismatch')
        name, target = value['target_kind'], value['target_id']
        self.routes = (
            '/api/v3/system/status', '/api/v3/config/mediamanagement',
            '/api/v3/%s/%d' % (name, target),
            '/api/v3/%sfile?%sId=%d' %
            ('movie' if name == 'movie' else 'episode', name, target))

    def _get_routes(self):
        return self.routes

    def _call_limit(self):
        return 4

    def _ready(self):
        require(self._guard() > 0, 'Custody deadline reached')

    def _map(self, app_path, roots):
        path = normalized(app_path)
        selected = [m for m in self.entry['roots'] if beneath(path, m['app_prefix'])]
        require(len(selected) == 1, 'Custody path outside declared roots')
        mapping = selected[0]
        return roots[mapping['host_prefix']], path[len(mapping['app_prefix']) + 1:]

    def observe(self, binding):
        self._ready()
        try:
            self._key = existing_importer_key(self._config['app_config_file'],
                                             self._config['app_config_owner_uid'])
            status = self._get(self.routes[0])
            require(type(status) is dict and
                    status.get('appName') == self.value['backend'].capitalize() and
                    status.get('version') == self._config['expected_version'] and
                    status.get('urlBase') in (None, ''), 'Custody identity mismatch')
            settings = self._get(self.routes[1])
            parent = self._get(self.routes[2])
            rows = self._get(self.routes[3])
        finally:
            self._key = None
        require(type(settings) is dict and type(settings.get('recycleBinCleanupDays')) is int
                and settings['recycleBinCleanupDays'] == 0, 'Custody retention unavailable')
        recycle_path = normalized(settings.get('recycleBin'))
        recycle = self.entry['recycle']
        require(recycle_path == recycle['app_prefix'] or
                beneath(recycle_path, recycle['app_prefix']), 'Custody recycle mismatch')
        require(type(parent) is dict and type(parent.get('id')) is int and
                parent['id'] == self.value['target_id'], 'Custody target unavailable')
        parent_path = normalized(parent.get('path'))
        require(type(rows) is list and len(rows) <= MAX_FILES,
                'Custody file manifest unavailable')
        found, paths = set(), set()
        for row in rows:
            require(type(row) is dict and type(row.get('id')) is int and
                    1 <= row['id'] < 2 ** 31 and row['id'] not in found and
                    type(row.get(self.value['target_kind'] + 'Id')) is int and
                    row[self.value['target_kind'] + 'Id'] == self.value['target_id'] and
                    type(row.get('size')) is int and 0 <= row['size'] <= MAX_FILE_BYTES,
                    'Invalid custody file manifest')
            path = normalized(row.get('path'))
            require(beneath(path, parent_path) and path not in paths,
                    'Invalid custody file scope')
            found.add(row['id'])
            paths.add(path)
        with contextlib.ExitStack() as stack:
            roots = {}
            for mapping in [*self.entry['roots'], recycle]:
                root = Root(mapping['host_prefix'], self._ready)
                stack.callback(root.close)
                roots[mapping['host_prefix']] = root
            self._map(parent_path + '/.scope-check', roots)
            # The configured recycle directory must actually exist, not just its prefix.
            mapped_recycle = recycle['host_prefix'] + recycle_path[len(recycle['app_prefix']):]
            actual_recycle = Root(mapped_recycle, self._ready)
            stack.callback(actual_recycle.close)
            recycle_fd = actual_recycle.fd
            require(mount_id(recycle_fd) == roots[recycle['host_prefix']].mount,
                    'Custody recycle mount mismatch')
            files, private, opened = [], [], []
            for row in sorted(rows, key=lambda r: r['id']):
                root, relative = self._map(row['path'], roots)
                fd = root.file(relative)
                try:
                    stamp = identity(fd)
                finally:
                    os.close(fd)
                require(stamp[3] == row['size'], 'Custody file size changed')
                files.append({'id': row['id'], 'size': stamp[3], 'dev': stamp[0],
                    'ino': stamp[1], 'mode': stamp[2], 'mtime_ns': stamp[4],
                    'ctime_ns': stamp[5], 'nlink': stamp[6], 'mount_id': stamp[7],
                    'chunks_total': max(1, (stamp[3] + CHUNK_BYTES - 1) // CHUNK_BYTES)})
                private.append([row['path'], stamp])
                opened.append((stamp, root, relative))
            revision = fingerprint([binding, self._config, self.entry, private, files,
                [self.value[k] for k in ('backend', 'target_kind', 'target_id')], CHUNK_BYTES,
                parent_path, settings['recycleBin'], identity(recycle_fd),
                [r.anchors for r in roots.values()]])
            position = [0, 0]
            if 'cursor' in self.value:
                try:
                    encoded = self.value['cursor']
                    raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4),
                                           altchars=b'-_', validate=True)
                    cursor = strict_json(raw)
                except (ValueError, UnicodeError):
                    raise Refused('Invalid custody cursor') from None
                require(type(cursor) is dict and set(cursor) == {'revision', 'file', 'chunk'}
                        and cursor['revision'] == revision and
                        type(cursor['file']) is int and type(cursor['chunk']) is int,
                        'Custody observation changed')
                position = [cursor['file'], cursor['chunk']]
            if files:
                i, chunk = position
                require(0 <= i < len(files) and 0 <= chunk < files[i]['chunks_total'],
                        'Invalid custody position')
                stamp, selected_root, selected_relative = opened[i]
                fd = selected_root.file(selected_relative)
                stack.callback(os.close, fd)
                require(identity(fd) == stamp, 'Custody observation changed')
                offset = chunk * CHUNK_BYTES
                length = min(CHUNK_BYTES, stamp[3] - offset)
                digest = hashlib.sha256()
                remaining = length
                while remaining:
                    self._ready()
                    data = os.pread(fd, min(1024 * 1024, remaining), offset)
                    require(data, 'Custody content changed')
                    digest.update(data)
                    offset += len(data)
                    remaining -= len(data)
                self._ready()
                observed_chunk = {'file_id': files[i]['id'], 'index': chunk,
                                  'bytes': length, 'sha256': digest.hexdigest()}
                next_position = ([i, chunk + 1] if chunk + 1 < files[i]['chunks_total']
                                 else [i + 1, 0])
            else:
                require('cursor' not in self.value, 'Invalid empty custody continuation')
                observed_chunk, next_position = None, [0, 0]
            # Re-open every path to detect substitution, not just writes to held descriptors.
            for stamp, root, relative in opened:
                self._ready()
                check_fd = root.file(relative)
                try:
                    require(stamp == identity(check_fd),
                            'Custody observation changed')
                finally:
                    os.close(check_fd)
            for root in roots.values():
                root.check()
            actual_recycle.check()
            cursor_out = None
            if next_position[0] < len(files):
                cursor_out = base64.urlsafe_b64encode(compact({
                    'revision': revision, 'file': next_position[0],
                    'chunk': next_position[1]})).decode().rstrip('=')
            result = {'qualification': 'arr_custody_v1', **binding,
                'backend': self.value['backend'], 'resource_id': self._config['resource_id'],
                'target': {k: self.value[k] for k in ('target_kind', 'target_id')},
                'observed_at_unix_ms': int(time.time() * 1000),
                'inventory_revision': revision, 'arr_file_inventory_complete': True,
                'files': files, 'chunk': observed_chunk, 'cursor': cursor_out,
                'position_complete': cursor_out is None,
                'recycle': {'configured': True, 'cleanup_days': 0},
                'seed_topology': 'unqualified', 'media_removal_authorized': False}
            require(len(compact(result)) <= MAX_REPLY_BYTES, 'Custody reply too large')
            self._ready()
            return result


def attest(policy, call, guard):
    """Only called after the socket worker's independent authorization."""
    backend = call['request']['backend']
    block = policy['janitor_custody']
    validate_block(block, policy['janitor_importers'])
    config = strict_json(private_read(policy['janitor_importers']['backends'][backend]))
    caller = call['caller']
    binding = {'server': caller['server'], 'library': caller['target_library'],
               'policy_revision': caller['policy_revision']}
    return CustodyAdapter(config, block['backends'][backend], call['request'],
                          request_guard=guard).observe(binding)
