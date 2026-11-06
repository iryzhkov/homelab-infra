"""Synthetic seed/library stat pair: loopback HTTP, real socket, real hardlinks.

Run only in Chaldea scratch with the asserted operator-test HOME. No live
host, backend, media file or credential is touched.
"""
import builtins
import contextlib
import copy
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import emby_operator_seed_join as seed_join
import emby_operator_socket as worker
import emby_operator_torrents as torrents
from emby_operator_recommendations import Refused
import test_emby_importer_inventory as inventory
import test_emby_operator_adapter as adapter_tests
import test_emby_operator_socket as existing

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'docs/emby-janitor-seed-join-wire-v1.json').read_bytes())
TORRENT_FIXTURE = json.loads((ROOT / 'docs/emby-janitor-torrents-wire-v1.json').read_bytes())
HASH = 'f' * 40
SID = 'QBT_SID_8080=' + 'a+/B' * 8
SECRET = 'synthetic-password-only'
DATA = b'0123456789abcdef'
ARR_KEY = '0123456789abcdef' * 2  # Distinct from every echoed request ID.
RESULT_KEYS = set(FIXTURE['responses']['linked']['result'])


class SeedJoinTests(unittest.TestCase):
    def setUp(self):
        self.scratch = existing.isolated(self)
        self.scratch.chmod(0o700)
        self.library = self.scratch / 'library'
        self.seed = self.scratch / 'seed'
        self.title = self.library / 'Title (2026)'
        self.title.mkdir(parents=True)
        self.seed.mkdir()
        self.library_file = self.title / 'Title.mkv'
        self.library_file.write_bytes(DATA)
        self.seed_file = self.seed / 'Title.mkv'
        os.link(self.library_file, self.seed_file)
        patch.object(seed_join, 'LIBRARY_HOST', str(self.library)).start()
        patch.object(seed_join, 'SEED_CEILING', str(self.seed)).start()
        self.addCleanup(patch.stopall)
        self.calls = []
        self.overrides = {}
        self.logout_status = 204
        self.movie = {'id': 42, 'path': '/movies/Title (2026)'}
        self.rows = [{'id': 9, 'movieId': 42, 'path': '/movies/Title (2026)/Title.mkv',
                      'size': len(DATA)}]
        self.info = {**TORRENT_FIXTURE['response']['result'], 'hash': HASH,
                     'save_path': str(self.seed), 'content_path': str(self.seed_file),
                     'total_size': len(DATA), 'comment': SECRET}
        self.files = [{'index': 0, 'name': 'Title.mkv', 'size': len(DATA), 'progress': 1,
                       'priority': 1}]
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.respond()
            def do_POST(self):
                self.respond()
            def respond(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                owner.calls.append((self.command, self.path))
                headers = []
                if self.path == '/api/v3/system/status':
                    status, body = 200, {'appName': 'Radarr', 'version': '6.4.4.10685',
                                         'urlBase': ''}
                elif self.path == '/api/v3/movie/42':
                    status, body = 200, owner.movie
                elif self.path == '/api/v3/moviefile?movieId=42':
                    status, body = 200, owner.rows
                elif self.path.endswith('/login'):
                    status, body, headers = 204, None, [('Set-Cookie', SID)]
                elif self.path.endswith('/logout'):
                    status, body = owner.logout_status, None
                elif self.path.endswith('/version'):
                    status, body = 200, b'v5.3.0'
                elif '/info?' in self.path:
                    status, body = 200, [owner.info]
                elif '/files?' in self.path:
                    status, body = 200, owner.files
                else:
                    status, body = 404, None
                status, body = owner.overrides.get(self.path, (status, body))
                raw = (b'' if body is None else body if isinstance(body, bytes)
                       else json.dumps(body).encode())
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = 'http://127.0.0.1:' + str(server.server_port)
        credentials = self.scratch / 'qbit-auth.json'
        self.write(credentials, {'username': 'synthetic-user', 'password': SECRET})
        self.torrent_config = dict(TORRENT_FIXTURE['descriptor'], origin=origin,
                                   credential_file=str(credentials),
                                   payload_prefixes=[str(self.seed)])
        self.torrent_path = self.scratch / 'qbit.json'
        self.write(self.torrent_path, self.torrent_config)
        self.arr_path = self.scratch / 'radarr.json'
        self.write(self.arr_path, dict(inventory.config('radarr'), origin=origin))
        self.policy = self.build_policy()
        self.stderr = io.StringIO()
        def fixture_read(original):
            def read(*args, **kwargs):
                with adapter_tests.CustodyTests().synthetic_root_ancestors():
                    return original(*args, **kwargs)
            return read
        patch.object(seed_join, 'private_read', side_effect=fixture_read(seed_join.private_read)).start()
        patch.object(torrents, 'private_read', side_effect=fixture_read(torrents.private_read)).start()
        patch.object(seed_join, 'existing_importer_key', return_value=ARR_KEY).start()
        patch.object(worker, 'load_policy', lambda: worker.validate_policy(self.policy)).start()
        patch.object(worker, 'selected_config', side_effect=AssertionError('Emby config accessed')).start()
        patch.object(worker, 'Journal', side_effect=AssertionError('Journal accessed')).start()

    def write(self, path, value):
        Path(path).write_text(json.dumps(value))
        Path(path).chmod(0o600)

    def build_policy(self):
        value = existing.policy()
        value['caller'].update({k: FIXTURE['frame']['caller'][k] for k in ('issuer', 'subject', 'server')})
        value['caller']['library'] = '7'
        binding = {'target_library': '7', 'revision': FIXTURE['policy']['revision']}
        value['janitor_torrents'] = dict(binding, config=str(self.torrent_path))
        value['janitor_importers'] = dict(binding, backends={'radarr': str(self.arr_path)})
        block = copy.deepcopy(FIXTURE['policy'])
        block['library_root']['host_prefix'] = str(self.library)
        block['seed_root'] = {'app_prefix': str(self.seed), 'host_prefix': str(self.seed)}
        value['janitor_seed_join'] = block
        return worker.validate_policy(value)

    def request(self, **changes):
        call = copy.deepcopy(FIXTURE['frame'])
        call['caller']['expires_at'] = int(time.time()) + 300
        call['deadline_unix_ms'] = int(time.time() * 1000) + 20000
        call['request'].update(changes)
        return call

    @contextlib.contextmanager
    def media_instrumentation(self):
        """During every media walk: O_PATH opens only, no read/pread/hash, and
        builtin opens limited to the kernel's fdinfo metadata."""
        # Violations are collected, not raised: the worker would otherwise
        # fold an AssertionError into a fail-closed unknown result.
        active, opened, self.violations = [], [], []
        real_open, real_walk, real_builtin = os.open, seed_join.walk, builtins.open
        originals = {'read': os.read, 'pread': os.pread, 'sha256': hashlib.sha256}
        def guarded_open(path, flags, *args, **kwargs):
            if active:
                opened.append(flags)
                if not (flags & os.O_PATH and flags & os.O_NOFOLLOW):
                    self.violations.append('media open without O_PATH no-follow')
            return real_open(path, flags, *args, **kwargs)
        def refuse(name):
            def forbidden(*args, **kwargs):
                if active:
                    self.violations.append(name + ' during media walk')
                    raise AssertionError(name)
                return originals[name](*args, **kwargs)
            return forbidden
        def metadata_open(file, *args, **kwargs):
            if active and not str(file).startswith('/proc/self/fdinfo/'):
                self.violations.append('content open during media walk')
                raise AssertionError('content open')
            return real_builtin(file, *args, **kwargs)
        def walk(*args, **kwargs):
            active.append(True)
            try:
                return real_walk(*args, **kwargs)
            finally:
                active.pop()
        with patch.object(os, 'open', guarded_open), patch.object(os, 'read', refuse('read')), \
                patch.object(os, 'pread', refuse('pread')), \
                patch.object(hashlib, 'sha256', refuse('sha256')), \
                patch.object(builtins, 'open', metadata_open), \
                patch.object(seed_join, 'walk', walk):
            yield opened

    def call(self, call=None):
        left, right = socket.socketpair()
        def run():
            with right, contextlib.redirect_stderr(self.stderr):
                worker.serve(right)
        thread = threading.Thread(target=run)
        with self.media_instrumentation() as opened:
            thread.start()
            with left:
                left.sendall(existing.encode(call or self.request()))
                raw = b''
                while True:
                    chunk = left.recv(262145)
                    if not chunk:
                        break
                    raw += chunk
            thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.violations, [])
        self.opened = opened
        text = raw.decode()
        for secret in (SECRET, SID, str(self.scratch), '/movies', 'Title', ARR_KEY):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, self.stderr.getvalue())
        response = json.loads(raw)
        if response['ok']:
            result = response['result']
            self.assertEqual(set(result), RESULT_KEYS)
            self.assertIs(result['media_removal_authorized'], False)
            self.assertEqual(result['reason'] is None, result['status'] != 'unknown')
            self.assertIn(result['status'], FIXTURE['statuses'])
            self.assertIn(result['reason'], FIXTURE['reasons'] + [None])
        return response

    def outcome(self, call=None):
        response = self.call(call)
        self.assertTrue(response['ok'], response)
        result = response['result']
        return result['status'] if result['status'] != 'unknown' else result['reason']

    def routes(self):
        return [path for _, path in self.calls]

    # Results ---------------------------------------------------------------

    def test_hardlink_is_linked_and_matches_fixture(self):
        started = int(time.time() * 1000)
        response = self.call()
        result = response['result']
        self.assertTrue(started <= result['observed_at_unix_ms'] <= int(time.time() * 1000))
        expected = copy.deepcopy(FIXTURE['responses']['linked'])
        expected['result']['observed_at_unix_ms'] = result['observed_at_unix_ms']
        self.assertEqual(response, expected)
        self.assertEqual(self.routes(), [
            '/api/v3/system/status', '/api/v3/movie/42', '/api/v3/moviefile?movieId=42',
            '/api/v2/auth/login', '/api/v2/app/version', '/api/v2/torrents/info?hashes=' + HASH,
            '/api/v2/torrents/files?hash=' + HASH, '/api/v2/auth/logout'])
        # Four walks of 1 + components opens each, all O_PATH no-follow.
        components = len(self.library_file.parts) + len(self.seed_file.parts)
        self.assertEqual(len(self.opened), 2 * components)
        self.assertEqual(self.library_file.read_bytes(), DATA)

    def test_same_size_copy_is_not_linked(self):
        self.seed_file.unlink()
        shutil.copyfile(self.library_file, self.seed_file)
        self.assertEqual(self.outcome(), 'not_linked')

    def test_leaf_symlink_to_same_inode_is_unsafe(self):
        self.seed_file.unlink()
        self.seed_file.symlink_to(self.library_file)
        self.assertEqual(self.outcome(), 'unsafe_path')

    def test_ancestor_symlink_is_unsafe(self):
        real = self.scratch / 'elsewhere'
        real.mkdir()
        os.link(self.library_file, real / 'Title.mkv')
        (self.seed / 'sub').symlink_to(real)
        self.info.update(save_path=str(self.seed / 'sub'),
                         content_path=str(self.seed / 'sub/Title.mkv'))
        self.assertEqual(self.outcome(), 'unsafe_path')
        # The library side traverses through a symlinked title directory too.
        self.info.update(save_path=str(self.seed), content_path=str(self.seed_file))
        shutil.move(str(self.title), str(self.scratch / 'moved'))
        self.title.symlink_to(self.scratch / 'moved')
        self.assertEqual(self.outcome(), 'unsafe_path')

    def test_escape_and_out_of_mapping_paths(self):
        for path in ('/movies/../etc/Title.mkv', '/other/Title (2026)/Title.mkv',
                     '/movies//Title.mkv', 'movies/Title (2026)/Title.mkv'):
            self.rows[0]['path'] = path
            with self.subTest(path=path):
                self.assertEqual(self.outcome(), 'source_unqualified')
        self.rows[0]['path'] = '/movies/Title (2026)/Title.mkv'
        mappings = [self.policy['janitor_seed_join'][k] for k in ('library_root', 'seed_root')]
        for app, expected in (('/movies/x', mappings[1]), (str(self.seed) + '/x', mappings[0]),
                              ('/elsewhere/x', mappings[0])):
            with self.assertRaises(seed_join.Unknown) as caught:
                seed_join.host_path(app, expected, mappings)
            self.assertEqual(caught.exception.reason, 'unsafe_path')
        deep = '/movies/' + '/'.join(['d'] * 64) + '/Title.mkv'
        with self.assertRaises(seed_join.Unknown):
            seed_join.walk(seed_join.host_path(deep, mappings[0], mappings),
                           str(self.library).count('/'), lambda: 10)

    def test_size_differences_are_changed(self):
        self.rows[0]['size'] = len(DATA) + 1
        self.assertEqual(self.outcome(), 'changed')
        self.rows[0]['size'] = len(DATA)
        self.files[0]['size'] = len(DATA) - 1
        self.assertEqual(self.outcome(), 'changed')

    def test_missing_and_denied(self):
        self.seed_file.unlink()
        self.assertEqual(self.outcome(), 'missing')
        os.link(self.library_file, self.seed_file)
        real_open = os.open
        def denied(path, flags, *args, **kwargs):
            if path == 'Title.mkv' and 'dir_fd' in kwargs:
                raise PermissionError(errno.EACCES, 'synthetic denial')
            return real_open(path, flags, *args, **kwargs)
        with patch.object(seed_join.os, 'open', denied):
            self.assertEqual(self.outcome(), 'denied')
        def stale(path, flags, *args, **kwargs):
            if path == 'Title.mkv' and 'dir_fd' in kwargs:
                raise OSError(errno.EIO, 'synthetic I/O error')
            return real_open(path, flags, *args, **kwargs)
        with patch.object(seed_join.os, 'open', stale):
            self.assertEqual(self.outcome(), 'unavailable')

    def test_mount_mismatch_and_nested_mount_are_not_linked(self):
        real = seed_join.mount_id
        def mount(fd):
            target = os.readlink('/proc/self/fd/%d' % fd)
            return real(fd) + (1000000 if target.startswith(str(self.seed)) else 0)
        with patch.object(seed_join, 'mount_id', mount):
            self.assertEqual(self.outcome(), 'incomparable')
        def nested(fd):
            target = os.readlink('/proc/self/fd/%d' % fd)
            return real(fd) + (1 if target == str(self.title) else 0)
        with patch.object(seed_join, 'mount_id', nested):
            self.assertEqual(self.outcome(), 'unsafe_path')

    def test_equal_inode_with_anomalous_link_count_is_incomparable(self):
        real = seed_join.observe
        def observe(fd, nlink=1):
            info, mount = real(fd)
            if not stat.S_ISREG(info.st_mode):
                return info, mount
            fields = {k: getattr(info, k) for k in ('st_dev', 'st_ino', 'st_mode', 'st_size',
                                                    'st_mtime_ns', 'st_ctime_ns')}
            return SimpleNamespace(**fields, st_nlink=nlink), mount
        with patch.object(seed_join, 'observe', observe):
            self.assertEqual(self.outcome(), 'incomparable')

    def test_replacement_between_passes_is_changed(self):
        depth = str(self.library).count('/')
        first = len(self.library_file.parts) + len(self.seed_file.parts)
        calls = []
        def guard():
            calls.append(1)
            if len(calls) == first + 1:
                replacement = self.title / 'replacement'
                replacement.write_bytes(DATA)
                os.replace(replacement, self.library_file)
            return 10
        with self.assertRaises(seed_join.Unknown) as caught:
            seed_join.stat_pair((str(self.library_file), depth, len(DATA)),
                                (str(self.seed_file), depth, len(DATA)), guard)
        self.assertEqual(caught.exception.reason, 'changed')
        self.assertEqual(len(calls), 2 * first)

    def test_guard_precedes_every_open_and_fds_close(self):
        depth = str(self.library).count('/')
        events = []
        real_open = os.open
        def recording(*args, **kwargs):
            events.append('open')
            return real_open(*args, **kwargs)
        before = len(os.listdir('/proc/self/fd'))
        with patch.object(seed_join.os, 'open', recording):
            status = seed_join.stat_pair((str(self.library_file), depth, len(DATA)),
                                         (str(self.seed_file), depth, len(DATA)),
                                         lambda: events.append('guard') or 10)
        self.assertEqual(status, 'linked')
        self.assertEqual(events, ['guard', 'open'] * (len(events) // 2))
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)
        with self.assertRaises(seed_join.Unknown) as caught:
            seed_join.walk(str(self.title), depth, lambda: 10)  # Directory leaf.
        self.assertEqual(caught.exception.reason, 'unsafe_path')
        with self.assertRaises(FileNotFoundError):
            seed_join.walk(str(self.seed / 'missing'), depth, lambda: 10)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    # Sources ---------------------------------------------------------------

    def test_arr_row_count_and_identity_are_source_unqualified(self):
        for rows in ([], self.rows * 2, [dict(self.rows[0], movieId=43)],
                     [dict(self.rows[0], size=True)], [dict(self.rows[0], size=-1)]):
            self.rows = rows
            with self.subTest(rows=rows):
                self.assertEqual(self.outcome(), 'source_unqualified')
        self.rows = [{'id': 9, 'movieId': 42, 'path': '/movies/Title (2026)/Title.mkv', 'size': 16}]
        self.movie = {'id': 43, 'path': '/movies/Title (2026)'}
        self.assertEqual(self.outcome(), 'source_unqualified')
        self.movie = {'id': 42, 'path': '/movies/Other'}
        self.assertEqual(self.outcome(), 'source_unqualified')
        self.movie = {'id': 42, 'path': '/movies/Title (2026)'}
        self.calls.clear()
        self.overrides['/api/v3/system/status'] = (200, {'appName': 'Sonarr', 'version': '6.4.4.10685'})
        self.assertEqual(self.outcome(), 'source_unqualified')
        self.assertEqual(self.routes(), ['/api/v3/system/status'])

    def test_arr_transport_failure_is_unavailable_without_qbit(self):
        self.overrides['/api/v3/moviefile?movieId=42'] = (500, None)
        with patch.object(seed_join, 'walk', wraps=seed_join.walk) as walked:
            self.assertEqual(self.outcome(), 'unavailable')
        walked.assert_not_called()
        self.assertEqual(self.routes(), ['/api/v3/system/status', '/api/v3/movie/42',
                                         '/api/v3/moviefile?movieId=42'])

    def test_torrent_incomplete_or_absent_file_is_source_unqualified(self):
        self.info.update(state='downloading', progress=.5, amount_left=4)
        self.assertEqual(self.outcome(), 'source_unqualified')
        self.info.update(state='stalledUP', progress=1, amount_left=0)
        self.assertEqual(self.outcome(self.request(file_index=1)), 'source_unqualified')
        self.info['content_path'] = str(self.seed / 'Other.mkv')
        self.assertEqual(self.outcome(), 'source_unqualified')

    def test_multifile_torrent_selects_only_the_indexed_file(self):
        (self.seed / 'Pack').mkdir()
        os.link(self.library_file, self.seed / 'Pack/Title.mkv')
        (self.seed / 'Pack/Extra.mkv').write_bytes(b'x' * 4)
        self.info.update(content_path=str(self.seed / 'Pack'), total_size=len(DATA) + 4)
        self.files = [{'index': 0, 'name': 'Pack/Extra.mkv', 'size': 4, 'progress': 1, 'priority': 1},
                      {'index': 1, 'name': 'Pack/Title.mkv', 'size': len(DATA), 'progress': 1,
                       'priority': 1}]
        self.assertEqual(self.outcome(self.request(file_index=1)), 'linked')
        # Another file of the same torrent is a different inode, not a link.
        self.assertEqual(self.outcome(self.request(file_index=0)), 'not_linked')

    def test_qbit_logout_failure_is_unavailable_before_any_walk(self):
        self.logout_status = 500
        with patch.object(seed_join, 'walk', wraps=seed_join.walk) as walked:
            self.assertEqual(self.outcome(), 'unavailable')
        walked.assert_not_called()
        self.assertEqual(self.routes()[-1], '/api/v2/auth/logout')
        self.assertLessEqual(len([r for r in self.routes() if r.startswith('/api/v2/')]), 5)

    def test_descriptor_or_config_mismatch_is_unavailable_error(self):
        self.write(self.torrent_path, dict(self.torrent_config, payload_prefixes=['/synthetic/other']))
        response = self.call()
        self.assertEqual(response, FIXTURE['responses']['error'])
        self.write(self.torrent_path, self.torrent_config)
        self.write(self.arr_path, dict(inventory.config('sonarr')))
        self.assertEqual(self.call(), FIXTURE['responses']['error'])
        self.assertEqual(self.calls, [])

    def test_deadline_is_a_wire_error(self):
        call = self.request()
        start = time.time()
        call['deadline_unix_ms'] = int(start * 1000) + 4000
        real = seed_join.walk
        def slow(*args, **kwargs):
            time.sleep(max(0, start + 4.2 - time.time()))
            return real(*args, **kwargs)
        with patch.object(seed_join, 'walk', slow):
            response = self.call(call)
        self.assertFalse(response['ok'])
        self.assertEqual(response['error']['code'], 'deadline')
        self.assertNotIn('result', response)

    # Authority and policy -------------------------------------------------

    def test_authorization_refuses_before_files_or_network(self):
        for key, value in [('issuer', 'wrong'), ('subject', 'wrong'), ('server', 'b' * 32),
                           ('namespace', 'managed-recommendations'), ('target_library', '8'),
                           ('policy_revision', 'a' * 40), ('actions', []),
                           ('actions', ['janitor.torrents.read']),
                           ('actions', ['janitor.importers.read', 'janitor.custody.read']),
                           ('actions', ['janitor.seed_join.read', 'janitor.torrents.read']),
                           ('expires_at', 1)]:
            call = self.request()
            call['caller'][key] = value
            with self.subTest(key=key, value=value), \
                    patch.object(seed_join, 'private_read', side_effect=AssertionError('read')):
                response = self.call(call)
                self.assertFalse(response['ok'])
                self.assertIn(response['error']['code'], ('refused', 'deadline'))
        call = self.request()
        call['recipient'] = 'd' * 32
        self.assertEqual(self.call(call)['error']['code'], 'refused')
        self.policy.pop('janitor_seed_join')
        self.assertEqual(self.call()['error']['code'], 'refused')
        self.assertEqual(self.calls, [])

    def test_new_action_is_refused_for_old_operations(self):
        policy = self.build_policy()
        for operation, request in (('janitor-torrents-inventory', {'torrent_hash': HASH}),
                                   ('janitor-importers-inventory', {'backend': 'radarr'})):
            call = self.request()
            call['operation'], call['request'] = operation, request
            parsed = worker.parse_request(existing.encode(call)[:-1])
            with self.assertRaises(worker.WireError):
                worker.authorize(parsed, policy)
        parsed = worker.parse_request(existing.encode(self.request())[:-1])
        worker.authorize(parsed, policy)
        self.assertLessEqual(worker.remaining_seconds(parsed, int(time.time() * 1000)), 25)

    def test_request_shape(self):
        for change in ({'arr_movie_id': True}, {'arr_movie_id': 0}, {'arr_movie_id': 2 ** 31},
                       {'arr_movie_id': '42'}, {'file_index': True}, {'file_index': -1},
                       {'file_index': 256}, {'torrent_hash': 'F' * 40}, {'torrent_hash': 'f' * 64},
                       {'path': '/etc/passwd'}):
            call = self.request(**change)
            with self.subTest(change=change):
                self.assertEqual(self.call(call)['error']['code'], 'refused')
        call = self.request()
        del call['request']['file_index']
        self.assertEqual(self.call(call)['error']['code'], 'refused')
        self.assertEqual(self.calls, [])

    def test_policy_block_validation(self):
        good = copy.deepcopy(self.policy)
        worker.validate_policy(good)
        def variant(mutate):
            value = copy.deepcopy(good)
            mutate(value)
            return value
        block = lambda v: v['janitor_seed_join']
        bad = [
            lambda v: block(v).update(extra=1),
            lambda v: block(v).pop('seed_root'),
            lambda v: block(v)['seed_root'].update(host_prefix=str(self.seed) + '/x'),
            lambda v: block(v)['seed_root'].update(app_prefix='/downloads'),
            lambda v: block(v).update(seed_root=[block(v)['seed_root']]),
            lambda v: block(v)['library_root'].update(app_prefix='/media'),
            lambda v: block(v)['library_root'].update(host_prefix=str(self.seed)),
            lambda v: block(v)['library_root'].update(extra='x'),
            lambda v: block(v).update(revision='a' * 40),
            lambda v: block(v).update(target_library='8'),
            lambda v: v.pop('janitor_torrents'),
            lambda v: v.pop('janitor_importers'),
            lambda v: v['janitor_importers'].update(backends={'sonarr': str(self.arr_path)}),
            lambda v: v['janitor_torrents'].update(revision='a' * 40),
            lambda v: v.update(janitor_custody={'backends': {'radarr': {
                'roots': [{'app_prefix': '/movies', 'host_prefix': str(self.scratch / 'drift')}],
                'recycle': {'app_prefix': '/recycle', 'host_prefix': str(self.scratch / 'recycle')}}}}),
        ]
        for mutate in bad:
            with self.subTest(mutate=mutate), self.assertRaises(Refused):
                worker.validate_policy(variant(mutate))
        with patch.object(seed_join, 'LIBRARY_HOST', str(self.seed / 'nested')):
            nested = variant(lambda v: block(v)['library_root'].update(host_prefix=str(self.seed / 'nested')))
            with self.assertRaises(Refused):
                worker.validate_policy(nested)
        custody = variant(lambda v: v.update(janitor_custody={'backends': {'radarr': {
            'roots': [copy.deepcopy(block(v)['library_root'])],
            'recycle': {'app_prefix': '/recycle', 'host_prefix': str(self.scratch / 'recycle')}}}}))
        worker.validate_policy(custody)
        same = variant(lambda v: block(v)['library_root'].update(app_prefix=str(self.library)))
        worker.validate_policy(same)

    def test_fixture_policy_and_frame_with_fixed_ceilings(self):
        patch.stopall()
        self.assertEqual(seed_join.SEED_CEILING, '/mnt/shared/downloads/complete/movies')
        self.assertEqual(seed_join.LIBRARY_HOST, '/mnt/shared/media/movies')
        value = existing.policy()
        value['caller'].update({k: FIXTURE['frame']['caller'][k] for k in ('issuer', 'subject', 'server')})
        value['caller']['library'] = '7'
        binding = {'target_library': '7', 'revision': FIXTURE['policy']['revision']}
        value['janitor_torrents'] = dict(binding, config='/etc/holonet-playlists/qbit.json')
        value['janitor_importers'] = dict(binding, backends={'radarr': '/etc/holonet-playlists/radarr.json'})
        value['janitor_seed_join'] = copy.deepcopy(FIXTURE['policy'])
        policy = worker.validate_policy(value)
        call = worker.parse_request(existing.encode(FIXTURE['frame'])[:-1])
        worker.authorize(call, policy)
        self.assertEqual(call, FIXTURE['frame'])
        self.assertEqual(worker.remaining_seconds(call, FIXTURE['frame']['deadline_unix_ms'] - 30000), 25)
        for name, response in FIXTURE['responses'].items():
            if name == 'error':
                continue
            result = response['result']
            self.assertEqual(set(result), RESULT_KEYS)
            self.assertEqual(result['status'], name)
            self.assertEqual(result['reason'] is None, name != 'unknown')
        self.assertEqual(tuple(FIXTURE['reasons']), seed_join.REASONS)

    def test_module_never_reads_hashes_or_uses_custody_content_paths(self):
        source = (ROOT / 'scripts/emby_operator_seed_join.py').read_text()
        for token in ('hashlib', 'os.read', 'pread', 'O_RDONLY', 'CustodyAdapter', 'open_absolute',
                      'attest', 'Root(', 'open(', 'print('):
            self.assertNotIn(token, source.replace('os.open(', ''))
        self.assertNotIn('hashlib', vars(seed_join))
        self.assertEqual(seed_join.DIRECTORY & os.O_PATH, os.O_PATH)
        self.assertEqual(seed_join.LEAF & (os.O_PATH | os.O_NOFOLLOW), os.O_PATH | os.O_NOFOLLOW)


if __name__ == '__main__':
    unittest.main()
