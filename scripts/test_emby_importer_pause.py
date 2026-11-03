"""Pause-only fixtures. Real loopback HTTP and native socket; no live service."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import emby_operator_adapter as adapter
import emby_operator_socket as worker
import test_emby_importer_inventory as inventory
import test_emby_operator_socket as socket_tests
from emby_operator_recommendations import Refused

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / 'docs/emby-janitor-importers-pause-wire-v1.json').read_text())
KEY = inventory.KEY


class Backend:
    def __init__(self, backend='radarr'):
        self.backend = backend
        self.config = inventory.config(backend)
        self.row = dict(id=1, name='Synthetic ' + backend + ' list',
                        implementation='SyntheticImport', fields=[{'value': 'private-provider-value'}])
        if backend == 'radarr':
            self.row.update(enabled=False, enableAuto=True, searchOnAdd=False)
        else:
            self.row.update(enableAutomaticAdd=True, searchForMissingEpisodes=False)
        self.cleanup = 'disabled'
        self.app_name = backend.capitalize()
        self.version = self.config['expected_version']
        self.calls = []
        self.puts = 0
        self.list_reads = 0
        self.ack_status = 202
        self.ack_body = None
        self.framing = None
        self.readback_failure = False
        self.rows_override = None

    @property
    def field(self):
        return 'enableAuto' if self.backend == 'radarr' else 'enableAutomaticAdd'

    def __enter__(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def reply(self, status, value, headers=None):
                body = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                for key, val in (headers or [('Content-Length', str(len(body)))]):
                    self.send_header(key, val)
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
            def do_GET(self):
                owner.calls.append(('GET', self.path, None, dict(self.headers)))
                if self.path == '/api/v3/system/status':
                    self.reply(200, dict(appName=owner.app_name, version=owner.version, urlBase=''))
                elif self.path == '/api/v3/config/importlist':
                    self.reply(200, {'listSyncLevel': owner.cleanup, 'private': 'private-config-value'})
                elif self.path == '/api/v3/importlist':
                    owner.list_reads += 1
                    if owner.readback_failure and owner.list_reads > 1:
                        self.reply(500, {'error': 'private-error'})
                    else:
                        self.reply(200, [owner.row] if owner.rows_override is None else owner.rows_override)
                else:
                    self.reply(404, {})
            def do_PUT(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                owner.calls.append(('PUT', self.path, raw, dict(self.headers)))
                owner.puts += 1
                # Mutate before every reply, even 4xx, to prove status alone
                # must never be used as a definitive no-effect receipt.
                owner.row[owner.field] = False
                body = owner.ack_body if owner.ack_body is not None else [owner.row]
                self.reply(owner.ack_status, body, owner.framing)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.config['origin'] = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def api(self, cls=adapter.ImporterPauseAdapter, guard=lambda: 10):
        return cls(self.config, request_guard=guard)

    def expected(self):
        return copy.deepcopy(FIXTURE['responses'][self.backend]['result']['before'])


class PauseAdapterTests(unittest.TestCase):
    def setUp(self):
        home = ROOT / '.cache/holonet-operator-tests/home'
        self.assertEqual(os.environ['HOME'], str(home))
        self.assertEqual(os.environ['TMPDIR'], str(home / 'tmp'))

    def run_pause(self, backend, api=None, expected=None):
        api = api or backend.api()
        with patch.object(adapter, 'existing_importer_key', return_value=KEY):
            result = api.pause(1, backend.expected() if expected is None else expected, int(time.time()) + 300)
        self.assertIsNone(api._key)
        return result

    def test_exact_five_calls_bodies_and_private_projection(self):
        for backend in ('radarr', 'sonarr'):
            with self.subTest(backend=backend), Backend(backend) as service:
                result = self.run_pause(service)
                expected = FIXTURE['responses'][backend]['result']
                self.assertEqual(result, {k: expected[k] for k in
                    ('stage', 'reason', 'cleanup_level', 'before', 'ack', 'after', 'calls')})
                self.assertNotIn('private', json.dumps(result))
                self.assertEqual([(c[0], c[1]) for c in service.calls], [
                    ('GET', '/api/v3/system/status'), ('GET', '/api/v3/config/importlist'),
                    ('GET', '/api/v3/importlist'), ('PUT', '/api/v3/importlist/bulk'),
                    ('GET', '/api/v3/importlist')])
                self.assertEqual(service.calls[3][2],
                    json.dumps({'ids': [1], service.field: False}, separators=(',', ':')).encode())
                self.assertTrue(all(c[3]['X-Api-Key'] == KEY for c in service.calls))
                self.assertEqual(service.puts, 1)

    def test_preflight_unknown_and_drift_never_send(self):
        for case in ('cleanup', 'unknown_cleanup', 'app', 'version', 'missing', 'duplicate', 'drift', 'already_false'):
            with self.subTest(case=case), Backend() as service:
                if case == 'cleanup':
                    service.cleanup = 'removeAndDelete'
                elif case == 'unknown_cleanup':
                    service.cleanup = 'future-level'
                elif case == 'app':
                    service.app_name = 'Sonarr'
                elif case == 'version':
                    service.version = '0.0.0.0'
                elif case == 'missing':
                    service.rows_override = []
                elif case == 'duplicate':
                    service.rows_override = [service.row, service.row]
                elif case == 'drift':
                    service.row['name'] = 'New list'
                elif case == 'already_false':
                    service.row[service.field] = False
                result = self.run_pause(service)
                self.assertEqual(result['stage'], 'not_sent')
                self.assertEqual(service.puts, 0)
                self.assertIsNone(result['ack'])
        for field, value in [('name', 'Other'), ('implementation', 'Other'), ('enabled', True),
                             ('search_on_add', True)]:
            with self.subTest(field=field), Backend() as service:
                expected = service.expected()
                expected[field] = value
                self.assertEqual(self.run_pause(service, expected=expected)['stage'], 'not_sent')
                self.assertEqual(service.puts, 0)

    def test_ambiguous_ack_including_4xx_never_claims_no_effect(self):
        cases = [(401, None, None), (404, None, None), (500, None, None), (302, None, None),
                 (202, [], None), (202, [{'id': 2, 'enableAuto': False}], None),
                 (202, b'[]', [('Content-Length', '100')]),
                 (202, b'[{"id":1,"id":1,"enableAuto":false}]', None),
                 (202, b'not-json', None)]
        for status, body, framing in cases:
            with self.subTest(status=status, body=body), Backend() as service:
                service.ack_status, service.ack_body, service.framing = status, body, framing
                result = self.run_pause(service)
                self.assertEqual(result['stage'], 'dispatch_unknown')
                self.assertIsNone(result['ack'])
                self.assertFalse(result['after']['automatic_add'])
                self.assertEqual(service.puts, 1)
                self.assertEqual(result['calls'], 5)
        with Backend() as service:
            service.readback_failure = True
            result = self.run_pause(service)
            self.assertEqual(result['stage'], 'acknowledged')
            self.assertEqual(result['ack']['status'], 202)
            self.assertIsNone(result['after'])
            self.assertEqual(service.puts, 1)

    def test_proven_connect_failure_and_pre_send_expiry(self):
        class RefusedConnection:
            def set_debuglevel(self, level):
                pass
            def connect(self):
                raise ConnectionRefusedError('synthetic')
            def close(self):
                pass
        with Backend() as service:
            api = service.api()
            with patch.object(api, '_pause_connection', return_value=RefusedConnection()):
                result = self.run_pause(service, api)
            self.assertEqual((result['stage'], result['reason']), ('not_sent', 'connect_failed'))
            self.assertEqual(service.puts, 0)
        class Expiring(adapter.ImporterPauseAdapter):
            def _put_once(self, list_id, expires_at):
                self._deadline = time.monotonic() - 1
                return super()._put_once(list_id, expires_at)
        with Backend() as service:
            result = self.run_pause(service, service.api(Expiring))
            self.assertEqual((result['stage'], result['reason']), ('not_sent', 'deadline'))
            self.assertEqual(service.puts, 0)

    def test_thread_timeout_is_unknown_and_cannot_send_late(self):
        release, finished, requested = threading.Event(), threading.Event(), threading.Event()
        class SlowConnection:
            sock = None
            def set_debuglevel(self, level):
                pass
            def connect(self):
                release.wait(1)
            def request(self, *args, **kwargs):
                requested.set()
                raise AssertionError('must not send after deadline')
            def close(self):
                finished.set()
        class Short(adapter.ImporterPauseAdapter):
            def _put_once(self, list_id, expires_at):
                self._deadline = time.monotonic() + 0.03
                return super()._put_once(list_id, expires_at)
        with Backend() as service:
            api = service.api(Short)
            try:
                with patch.object(api, '_pause_connection', return_value=SlowConnection()):
                    started = time.monotonic()
                    result = self.run_pause(service, api)
                    self.assertLess(time.monotonic() - started, 0.5)
                    self.assertEqual(result['stage'], 'dispatch_unknown')
                    self.assertEqual(result['calls'], 4)
                    release.set()
                    self.assertTrue(finished.wait(1))
                    self.assertFalse(requested.is_set())
            finally:
                release.set()
            self.assertEqual(service.puts, 0)


class PauseSocketTests(socket_tests.WorkerCase):
    def setUp(self):
        super().setUp()
        self.policy['janitor_importers'] = copy.deepcopy(FIXTURE['policy'])
        self.policy['janitor_importers_pause'] = copy.deepcopy(FIXTURE['pause_policy'])
        self.policy = worker.validate_policy(self.policy)
        self.service = Backend().__enter__()
        self.addCleanup(self.service.__exit__)
        for target, name, value in (
            (worker, 'private_read', lambda path: json.dumps(self.service.config).encode()),
            (adapter, 'existing_importer_key', lambda path, uid: KEY),
            (worker, 'load_config', lambda path: self.fail('Emby config must not load')),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def request(self):
        call = copy.deepcopy(FIXTURE['frame'])
        call['caller']['expires_at'] = int(time.time()) + 300
        call['deadline_unix_ms'] = int(time.time() * 1000) + 10000
        return call

    def test_actual_socket_and_http_unchanged_files(self):
        before = {str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}
        raw, result = self.call(socket_tests.encode(self.request()))
        self.assertEqual(result, FIXTURE['responses']['radarr'])
        self.assertNotIn(KEY.encode(), raw)
        self.assertNotIn(b'private', raw)
        self.assertEqual(self.credential_reads, 0)
        self.assertEqual(self.service.puts, 1)
        self.assertEqual({str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}, before)

    def test_root_ceiling_and_closed_request_before_files(self):
        for mutate in (
            lambda call: call['request'].update(list_id=2, expected=dict(call['request']['expected'], id=2)),
            lambda call: call['caller'].update(actions=['janitor.importers.read']),
            lambda call: call['caller'].update(actions=list(reversed(worker.IMPORTER_PAUSE_ACTIONS))),
            lambda call: call['caller'].update(namespace='managed-recommendations'),
            lambda call: call['caller'].update(policy_revision='a' * 40),
            lambda call: call['request'].update(body={}),
            lambda call: call['request'].pop('resource_id'),
            lambda call: call['request'].update(expected=dict(call['request']['expected'], automatic_add=False)),
        ):
            call = self.request()
            mutate(call)
            with patch.object(worker, 'private_read') as read, patch.object(adapter, 'existing_importer_key') as key:
                _, result = self.call(socket_tests.encode(call))
                self.assertFalse(result['ok'])
                read.assert_not_called()
                key.assert_not_called()
        self.policy.pop('janitor_importers_pause')
        with patch.object(worker, 'private_read') as read:
            _, result = self.call(socket_tests.encode(self.request()))
            self.assertFalse(result['ok'])
            read.assert_not_called()
        self.assertEqual(self.service.calls, [])

    def test_resource_mismatch_before_credentials_or_network(self):
        self.service.config['resource_id'] = 'foreign-instance'
        with patch.object(adapter, 'existing_importer_key') as key:
            _, result = self.call(socket_tests.encode(self.request()))
            self.assertTrue(result['ok'])
            self.assertEqual(result['result']['stage'], 'not_sent')
            self.assertEqual(result['result']['reason'], 'identity_mismatch')
            self.assertEqual(result['result']['calls'], 0)
            key.assert_not_called()
        self.assertEqual(self.service.calls, [])

    def test_bad_pause_policy_refuses(self):
        for block in ({}, {'backends': {}}, {'backends': {'other': [1]}},
                      {'backends': {'radarr': [True]}}, {'backends': {'radarr': [1, 1]}},
                      {'backends': {'radarr': [0]}}, {'backends': {'radarr': list(range(1, 34))}}):
            value = copy.deepcopy(self.policy)
            value['janitor_importers_pause'] = block
            with self.assertRaises(Refused):
                worker.validate_policy(value)
        value = copy.deepcopy(self.policy)
        value.pop('janitor_importers')
        with self.assertRaises(Refused):
            worker.validate_policy(value)


if __name__ == '__main__':
    unittest.main()
