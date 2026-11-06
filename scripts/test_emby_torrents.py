"""Synthetic HTTP + real socket + protected files. Run only in Chaldea scratch."""
import contextlib
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import patch

import emby_operator_socket as worker
import emby_operator_torrents as torrents
import test_emby_operator_socket as existing
import test_emby_operator_adapter as adapter_tests
from emby_operator_recommendations import Refused

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'docs/emby-janitor-torrents-wire-v1.json').read_bytes())
SID = 'QBT_SID_8080=' + 'a+/B' * 8
SECRET = 'synthetic-password-only'


class TorrentTests(unittest.TestCase):
    def setUp(self):
        self.directory = existing.isolated(self)
        self.directory.chmod(0o700)
        self.calls = []
        self.info = {**FIXTURE['response']['result'], 'hash': 'f' * 40,
                     'comment': SECRET, 'magnet_uri': SECRET, 'trackers': [SECRET]}
        self.files = copy.deepcopy(self.info['files'])
        self.login_status, self.logout_status = 204, 204
        self.cookies = [SID + '; HttpOnly; Path=/; Secure; SameSite=Strict; Expires=Wed, 01 Jan 2030 00:00:00 GMT']
        self.overrides = {}
        self.delays = {}
        self.trickle = None
        self.trickle_started = threading.Event()
        self.trickle_done = threading.Event()
        self.trickle_hook = None
        self.active_io_threads = []
        self.readers_at_logout = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.respond()
            def do_POST(self):
                self.respond()
            def respond(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                owner.calls.append((self.command, self.path, dict(self.headers), body))
                time.sleep(owner.delays.get(self.path, 0))
                if self.path.endswith('/login'):
                    status, raw, headers = owner.login_status, b'', [('Set-Cookie', c) for c in owner.cookies]
                elif self.path.endswith('/logout'):
                    owner.readers_at_logout.append([t.is_alive() for t in owner.active_io_threads[:-1]])
                    status, raw, headers = owner.logout_status, b'', [('Set-Cookie', 'QBT_SID_8080=; Max-Age=0')]
                elif self.path.endswith('/version'):
                    status, raw, headers = 200, b'v5.3.0', [('Set-Cookie', 'QBT_SID_8080=' + 'Z' * 32)]
                elif '/info?' in self.path:
                    status, raw, headers = 200, json.dumps([owner.info]).encode(), []
                else:
                    status, raw, headers = 200, json.dumps(owner.files).encode(), []
                status, raw, headers = owner.overrides.get(self.path, (status, raw, headers))
                if owner.trickle and self.path.endswith('/version'):
                    owner.trickle_started.set()
                    try:
                        self.wfile.write(b'HTTP/1.1 200 OK\r\n')
                        self.wfile.write(b'X-Slow: ' if owner.trickle == 'headers'
                                         else b'Connection: close\r\nContent-Length: 400\r\n\r\n')
                        self.wfile.flush()
                        if owner.trickle_hook is not None:
                            owner.trickle_hook()
                        # Every byte arrives before the per-read socket timeout.
                        for _ in range(400):
                            self.wfile.write(b'x')
                            self.wfile.flush()
                            time.sleep(.01)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        owner.trickle_done.set()
                    return
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.config = copy.deepcopy(FIXTURE['descriptor'])
        self.config['origin'] = 'http://127.0.0.1:' + str(server.server_port)
        self.config['credential_file'] = str(self.directory / 'credentials.json')
        self.write(self.config['credential_file'], {'username': 'synthetic-user', 'password': SECRET})
        self.config_path = str(self.directory / 'descriptor.json')
        self.write(self.config_path, self.config)
        self.policy = existing.policy()
        self.policy['caller'].update({k: FIXTURE['frame']['caller'][k]
            for k in ('issuer', 'subject', 'server')})
        self.policy['caller']['library'] = '7'
        self.policy['janitor_torrents'] = {**FIXTURE['policy'], 'config': self.config_path}
        self.policy = worker.validate_policy(self.policy)
        self.stderr = io.StringIO()
        self.addCleanup(patch.stopall)
        # CI mounts the checkout beneath runner-owned ancestors. Emulate the
        # protected deployment ancestry only during synthetic file reads, using
        # the existing custody fixture; real leaf/link/size checks still run.
        private_read = torrents.private_read
        def read_fixture(*args, **kwargs):
            with adapter_tests.CustodyTests().synthetic_root_ancestors():
                return private_read(*args, **kwargs)
        patch.object(torrents, 'private_read', side_effect=read_fixture).start()
        patch.object(worker, 'load_policy', lambda: worker.validate_policy(self.policy)).start()
        patch.object(worker, 'selected_config', side_effect=AssertionError('Emby config accessed')).start()
        patch.object(worker, 'Journal', side_effect=AssertionError('Journal accessed')).start()

    def write(self, path, value):
        Path(path).write_text(json.dumps(value))
        Path(path).chmod(0o600)

    def request(self):
        call = copy.deepcopy(FIXTURE['frame'])
        call['caller']['expires_at'] = int(time.time()) + 300
        call['deadline_unix_ms'] = int(time.time() * 1000) + 10000
        return call

    def call(self, call=None):
        left, right = socket.socketpair()
        served = {}
        def run():
            with right, contextlib.redirect_stderr(self.stderr):
                served['value'] = worker.serve(right)
        thread = threading.Thread(target=run)
        thread.start()
        with left:
            left.sendall(existing.encode(call or self.request()))
            raw = b''
            while True:
                chunk = left.recv(262145)
                if not chunk:
                    break
                raw += chunk
        thread.join(12)
        self.assertFalse(thread.is_alive())
        self.assertNotIn(SECRET, raw.decode())
        self.assertNotIn(SID, raw.decode())
        self.assertNotIn(SECRET, self.stderr.getvalue())
        return json.loads(raw)

    def fail_call(self):
        result = self.call()
        self.assertFalse(result['ok'])
        self.assertNotIn('result', result)

    def test_fixture_and_exact_requests(self):
        with patch.dict(os.environ, {'http_proxy': 'http://invalid:1', 'https_proxy': 'http://invalid:1'}):
            self.assertEqual(self.call(), FIXTURE['response'])
        self.assertEqual([(m, p) for m, p, _, _ in self.calls], [
            ('POST', '/api/v2/auth/login'), ('GET', '/api/v2/app/version'),
            ('GET', '/api/v2/torrents/info?hashes=' + 'f' * 40),
            ('GET', '/api/v2/torrents/files?hash=' + 'f' * 40), ('POST', '/api/v2/auth/logout')])
        for i, (_, _, headers, _) in enumerate(self.calls):
            self.assertEqual(headers.get('Cookie'), SID if i else None)
            self.assertFalse(set(k.lower() for k in headers) &
                             {'origin', 'referer', 'sec-fetch-site', 'x-forwarded-for'})
        self.assertEqual(self.calls[0][3], b'username=synthetic-user&password=synthetic-password-only')

    def test_runner_owned_ancestor_is_only_a_fixture_seam(self):
        # A mounted CI checkout is not a production protected credential path.
        home = Path(os.environ['HOME'])
        mode = home.stat().st_mode & 0o777
        try:
            home.chmod(0o777)
            with self.assertRaisesRegex(Refused, 'Unsafe protected ancestor'):
                adapter_tests.adapter.private_read(self.config_path)
            self.assertEqual(self.call(), FIXTURE['response'])
        finally:
            home.chmod(mode)

    def test_authorization_before_files(self):
        for key, value in [('issuer', 'wrong'), ('subject', 'wrong'), ('server', 'b' * 32),
                           ('namespace', 'other'), ('target_library', '8'),
                           ('policy_revision', 'a' * 40), ('actions', []),
                           ('actions', ['janitor.importers.read']), ('expires_at', 1)]:
            call = self.request()
            call['caller'][key] = value
            with patch.object(torrents, 'private_read') as read:
                self.assertFalse(self.call(call)['ok'])
                read.assert_not_called()
        self.policy.pop('janitor_torrents')
        with patch.object(torrents, 'private_read') as read:
            self.fail_call()
            read.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_whole_policy_validation(self):
        for block in [None, {}, {**FIXTURE['policy'], 'revision': 'bad'},
                      {**FIXTURE['policy'], 'target_library': '8'},
                      {**FIXTURE['policy'], 'config': '/a/../b'}]:
            policy = copy.deepcopy(self.policy)
            policy['janitor_torrents'] = block
            with self.assertRaises(Refused):
                worker.validate_policy(policy)

    def test_no_enumeration_or_extra_methods(self):
        for request in [{}, {'torrent_hash': ''}, {'torrent_hash': 'f' * 64},
                        {'torrent_hash': 'f' * 40 + '|'}, {'torrent_hash': 'F' * 40},
                        {'torrent_hash': 'f' * 40, 'route': 'downloadFile'}]:
            call = self.request()
            call['request'] = request
            self.assertFalse(self.call(call)['ok'])
        for operation in ['downloadFile', 'torrents-info', 'janitor-torrents-delete']:
            call = self.request()
            call['operation'] = operation
            self.assertFalse(self.call(call)['ok'])
        self.assertEqual(self.calls, [])

    def test_login_cookies_and_status(self):
        for cookies in [[], [SID, SID], ['SID=' + 'a' * 32], ['QBT_SID_1='],
                        ['QBT_SID_123456=' + 'a' * 32], ['QBT_SID_1=' + '_' * 32],
                        [SID + ', other=secret'], [SID + '; other=secret'],
                        [SID + '; Path=/' + 'x' * 4096]]:
            self.cookies = cookies
            self.fail_call()
        self.cookies = [SID]
        for status in (200, 302, 401, 403):
            self.login_status = status
            self.fail_call()
        self.assertTrue(all(p == '/api/v2/auth/login' for _, p, _, _ in self.calls))

    def test_cleanup_failure_withholds_facts(self):
        for status in (302, 400, 500):
            self.logout_status = status
            self.fail_call()
            self.assertEqual(self.calls[-1][1], '/api/v2/auth/logout')
        self.logout_status = 200
        self.assertTrue(self.call()['ok'])
        self.overrides['/api/v2/auth/logout'] = (200, b'x', [])
        self.fail_call()

    def test_failure_cleanup_and_strict_bodies(self):
        route = '/api/v2/torrents/info?hashes=' + 'f' * 40
        for raw in [b'[]', b'[{},{}]', b'[{"hash":"wrong"}]', b'{} garbage', b'\xff',
                    b'[{"a":1,"a":2}]', b'[' * 33 + b'0' + b']' * 33,
                    json.dumps([self.info]).encode('utf-16-le'),
                    b'\xef\xbb\xbf' + json.dumps([self.info]).encode(), b' ' * 262145]:
            self.overrides[route] = (200, raw, [])
            self.fail_call()
            self.assertEqual(self.calls[-1][1], '/api/v2/auth/logout')
        for headers in [[('Content-Length', '1')], [('Transfer-Encoding', 'gzip')]]:
            self.overrides[route] = (200, b'[]', headers)
            self.fail_call()
        self.overrides[route] = (302, b'', [('Location', '/forbidden')])
        self.fail_call()
        self.assertFalse(any(p == '/forbidden' for _, p, _, _ in self.calls))

    def test_projection_refusals(self):
        original = copy.deepcopy(self.info)
        for key, value in [('category', 'denied'), ('hash', 'e' * 40),
                           ('save_path', '/outside'), ('content_path', '/synthetic/downloads/../bad'),
                           ('progress', True), ('amount_left', 17), ('total_size', 11),
                           ('state', '\nsecret')]:
            self.info = {**original, key: value}
            self.fail_call()
        self.info = original
        original_files = copy.deepcopy(self.files)
        for key, value in [('index', 1), ('index', True), ('name', '../bad'),
                           ('name', '/absolute'), ('name', 'a//b'), ('name', 'a\\b'),
                           ('name', 'a/./b'), ('name', 'a\n'), ('size', -1),
                           ('progress', True), ('priority', 2), ('priority', True)]:
            self.files = [{**original_files[0], key: value}]
            self.fail_call()
        for files in [[], original_files * 257, original_files * 2,
                      [original_files[0], {**original_files[0], 'index': 1}]]:
            self.files = files
            self.fail_call()

    def test_incomplete_and_multifile(self):
        self.files.append({**self.files[0], 'index': 1, 'name': 'sub/Other.mkv', 'size': 4,
                           'priority': 0, 'progress': 0})
        self.info.update(state='moving', progress=.5, amount_left=4)
        result = self.call()['result']
        self.assertFalse(result['client_reports_complete'])
        self.assertFalse(result['paths_qualified'])
        self.assertFalse(result['media_removal_authorized'])
        self.assertEqual(result['seed_topology'], 'unqualified')

    def test_descriptor_and_version_refusal(self):
        for change in [{'expected_version': 'v5.2.0'}, {'resource_id': ''},
                       {'categories': []}, {'categories': ['x', 'x']},
                       {'payload_prefixes': ['/a', '/a/b']},
                       {'origin': 'http://192.0.2.1'}, {'origin': 'https://example.invalid/path'},
                       {'origin': 'https://user:password@example.invalid'},
                       {'origin': 'https://example.invalid?query'}]:
            self.write(self.config_path, {**self.config, **change})
            self.fail_call()
        self.assertEqual(self.calls, [])
        self.write(self.config_path, self.config)
        for raw in [b'v5.3.0\n', b'v5.2.0', b'x' * 65]:
            self.overrides['/api/v2/app/version'] = (200, raw, [])
            self.fail_call()
            self.assertEqual(self.calls[-1][1], '/api/v2/auth/logout')
        self.overrides.clear()
        for status, raw in [(204, b'x'), (200, b'Ok.')]:
            self.overrides['/api/v2/auth/login'] = (status, raw, [('Set-Cookie', SID)])
            self.fail_call()

    def test_credential_shape_and_limits(self):
        credential = Path(self.config['credential_file'])
        for value in [{}, {'username': '', 'password': SECRET},
                      {'username': 'a', 'password': 'x' * 4097},
                      {'username': 'x' * 257, 'password': SECRET},
                      {'username': 'a', 'password': 'bad\n'},
                      {'username': 'a', 'password': SECRET, 'extra': 'bad'}]:
            self.write(credential, value)
            self.fail_call()
        credential.write_bytes(b'{"username":"a","username":"b","password":"secret"}')
        self.fail_call()
        credential.write_bytes(b' ' * 16385)
        self.fail_call()
        self.write(credential, {'username': 'a', 'password': SECRET})
        os.link(credential, credential.with_name('hardlink'))
        self.fail_call()
        self.assertEqual(self.calls, [])

    def test_credential_custody(self):
        credential = Path(self.config['credential_file'])
        credential.chmod(0o644)
        self.fail_call()
        credential.chmod(0o600)
        linked = credential.with_name('link')
        linked.symlink_to(credential)
        self.config['credential_file'] = str(linked)
        self.write(self.config_path, self.config)
        self.fail_call()
        self.assertEqual(self.calls, [])

    def test_deadline_refuses_login_and_reserves_cleanup(self):
        call = self.request()
        call['deadline_unix_ms'] = int(time.time() * 1000) + 2900
        self.assertFalse(self.call(call)['ok'])
        self.assertEqual(self.calls, [])
        self.delays['/api/v2/app/version'] = 1.5
        call['deadline_unix_ms'] = int(time.time() * 1000) + 3300
        started = time.monotonic()
        self.assertFalse(self.call(call)['ok'])
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual([p for _, p, _, _ in self.calls],
                         ['/api/v2/auth/login', '/api/v2/app/version', '/api/v2/auth/logout'])

    @contextlib.contextmanager
    def capture_io_threads(self):
        original = threading.Thread
        self.active_io_threads = []
        def create(*args, **kwargs):
            thread = original(*args, **kwargs)
            if getattr(kwargs.get('target'), '__name__', '') == 'io':
                self.active_io_threads.append(thread)
            return thread
        with patch.object(torrents.threading, 'Thread', side_effect=create):
            yield self.active_io_threads

    def test_trickling_headers_and_body_stop_before_logout(self):
        for kind in ('headers', 'body'):
            with self.subTest(kind=kind):
                self.trickle = kind
                self.trickle_started.clear()
                self.trickle_done.clear()
                self.readers_at_logout.clear()
                self.calls.clear()
                call = self.request()
                call['deadline_unix_ms'] = int(time.time() * 1000) + 3300
                started = time.monotonic()
                with self.capture_io_threads() as readers:
                    self.assertFalse(self.call(call)['ok'])
                self.assertLess(time.monotonic() - started, 3)
                self.assertTrue(self.trickle_started.is_set())
                self.assertEqual(len(readers), 3)  # login, stalled GET, logout
                self.assertTrue(all(not t.is_alive() for t in readers))
                self.assertEqual(self.readers_at_logout, [[False, False]])
                self.assertEqual([p for _, p, _, _ in self.calls], [
                    '/api/v2/auth/login', '/api/v2/app/version', '/api/v2/auth/logout'])
                self.assertTrue(self.trickle_done.wait(.5))

    def test_late_connect_cannot_send_or_overlap_logout(self):
        entered, release = threading.Event(), threading.Event()
        original = torrents.http.client.HTTPConnection.connect
        count = 0
        def connect(conn):
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
                release.wait(5)
            return original(conn)
        api = torrents.TorrentAdapter(self.config, request_guard=lambda: 10)
        api.deadline = time.monotonic() + 3.3
        readers = []
        try:
            with patch.object(torrents.http.client.HTTPConnection, 'connect', connect):
                with self.capture_io_threads() as readers, self.assertRaises(Refused):
                    api.inventory('f' * 40, self.request()['caller'])
                self.assertTrue(entered.is_set())
                self.assertTrue(api.io_stalled)
                self.assertEqual(len(readers), 2)
                self.assertIsNone(api.cookie)
        finally:
            release.set()
            for reader in readers:
                reader.join(1)
        self.assertTrue(all(not t.is_alive() for t in readers))
        self.assertEqual([p for _, p, _, _ in self.calls], ['/api/v2/auth/login'])

    def test_inflight_cancellation_stops_reader_without_logout(self):
        cancelled = threading.Event()
        self.trickle = 'body'
        self.trickle_hook = cancelled.set
        api = torrents.TorrentAdapter(self.config,
            request_guard=lambda: 0 if cancelled.is_set() else 10)
        with self.capture_io_threads() as readers, self.assertRaises(Refused):
            api.inventory('f' * 40, self.request()['caller'])
        self.assertTrue(self.trickle_started.is_set())
        self.assertEqual(len(readers), 2)
        self.assertTrue(all(not t.is_alive() for t in readers))
        self.assertIsNone(api.cookie)
        self.assertEqual([p for _, p, _, _ in self.calls],
                         ['/api/v2/auth/login', '/api/v2/app/version'])
        self.assertTrue(self.trickle_done.wait(.5))

    def test_cancelled_authority_does_not_logout(self):
        config = torrents.descriptor(self.config)
        active = True
        def guard():
            if not active:
                raise Refused('Caller cancelled')
            return 10
        api = torrents.TorrentAdapter(config, request_guard=guard)
        original = api.exchange
        def exchange(*args, **kwargs):
            nonlocal active
            result = original(*args, **kwargs)
            if args[1] == '/api/v2/app/version':
                active = False
            return result
        api.exchange = exchange
        with self.assertRaises(Refused):
            api.inventory('f' * 40, self.request()['caller'])
        self.assertIsNone(api.cookie)
        self.assertEqual(len(self.calls), 2)


if __name__ == '__main__':
    unittest.main()
