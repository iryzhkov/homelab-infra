"""Synthetic-only importer read checks. Parent runs with asserted Chaldea HOME."""
import copy
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import emby_operator_adapter as adapter
import emby_operator_socket as worker
from emby_operator_recommendations import Refused
import test_emby_operator_adapter as base
import test_emby_operator_socket as socket_tests

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / 'docs/emby-janitor-importers-wire-v1.json').read_text())
KEY = 'f' * 32


def config(backend='radarr'):
    return dict(version=1, backend=backend, resource_id=backend + '-primary',
                origin='https://synthetic.invalid',
                expected_version='6.4.4.10685' if backend == 'radarr' else '4.0.20.3014',
                app_config_file='/synthetic/' + backend + '/config.xml', app_config_owner_uid=1000)


def policy():
    value = socket_tests.policy()
    value['janitor_importers'] = copy.deepcopy(FIXTURE['policy'])
    return worker.validate_policy(value)


class ImporterContractTests(unittest.TestCase):
    def test_wire_independent_opt_in_and_old_callers(self):
        p = policy()
        self.assertNotIn('janitor', p)
        self.assertNotIn('history_users', p)
        call = worker.parse_request(socket_tests.encode(FIXTURE['frame'])[:-1])
        worker.authorize(call, p)
        self.assertEqual(call, FIXTURE['frame'])
        worker.authorize(worker.parse_request(socket_tests.encode(socket_tests.frame())[:-1]), p)
        for mutate in (
            lambda c: c['caller'].update(actions=['catalog.read']),
            lambda c: c['caller'].update(namespace='managed-recommendations'),
            lambda c: c['caller'].update(target_library='8'),
            lambda c: c['caller'].update(history_users=[base.R]),
            lambda c: c['caller'].update(policy_revision='f' * 40),
            lambda c: c.update(operation='apply'),
            lambda c: c['request'].update(backend='other'),
            lambda c: c['request'].update(url='https://foreign.invalid'),
        ):
            value = copy.deepcopy(FIXTURE['frame'])
            mutate(value)
            with self.assertRaises(worker.WireError):
                worker.authorize(worker.parse_request(socket_tests.encode(value)[:-1]), p)
        p.pop('janitor_importers')
        with self.assertRaises(worker.WireError):
            worker.authorize(call, p)

    def test_policy_and_config_fail_closed(self):
        for change in (
            {'target_library': '8'}, {'revision': 'bad'}, {'backends': {}},
            {'backends': {'other': '/synthetic/x'}}, {'history_users': [base.R]},
        ):
            p = policy()
            p['janitor_importers'].update(change)
            with self.assertRaises(Refused):
                worker.validate_policy(p)
        for change in (
            {'backend': []}, {'version': True}, {'origin': 'http://remote.invalid'},
            {'origin': 'https://user:password@synthetic.invalid'},
            {'origin': 'https://synthetic.invalid/base'}, {'origin': 'https://synthetic.invalid?'},
            {'origin': 'file:///etc/passwd'}, {'expected_version': '6.4.4.10685-ls318'},
            {'app_config_file': None}, {'app_config_file': '/a/../config.xml'},
            {'app_config_owner_uid': True}, {'api_key': KEY},
        ):
            with self.subTest(change=change), self.assertRaises(Refused):
                adapter.validate_importer_config(dict(config(), **change))
        for origin in ('http://127.0.0.1:7878', 'http://[::1]:8989', 'http://localhost:8989'):
            self.assertEqual(adapter.validate_importer_config(dict(config(), origin=origin))['origin'], origin)


class ImporterXMLTests(unittest.TestCase):
    def setUp(self):
        home = ROOT / '.cache/holonet-operator-tests/home'
        self.assertEqual(os.environ['HOME'], str(home))
        self.assertEqual(os.environ['TMPDIR'], str(home / 'tmp'))
        self.temp = tempfile.TemporaryDirectory(dir=home / 'tmp')
        self.addCleanup(self.temp.cleanup)
        app = Path(self.temp.name) / 'app'
        app.mkdir(mode=0o700)
        self.path = app / 'config.xml'
        # Keep real FD/symlink/permission checks inside an isolated synthetic
        # root; CI checkout ancestors may intentionally be group-writable.
        real_open = os.open

        def fixture_open(path, flags, *args, **kwargs):
            if path == '/' and 'dir_fd' not in kwargs:
                return real_open(self.temp.name, flags, *args, **kwargs)
            return real_open(path, flags, *args, **kwargs)

        opened = patch.object(adapter.os, 'open', side_effect=fixture_open)
        opened.start()
        self.addCleanup(opened.stop)

    def read(self, body):
        self.path.write_text(body)
        self.path.chmod(0o600)
        return adapter.existing_importer_key('/app/config.xml', os.getuid())

    def test_existing_xml_only_and_no_copy(self):
        body = '<Config><ApiKey>' + KEY + '</ApiKey><UrlBase></UrlBase><Other>private</Other></Config>'
        self.assertEqual(self.read(body), KEY)
        self.assertEqual(self.path.read_text(), body)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ['config.xml'])
        for body in (
            '<Config/>', '<Config><ApiKey>' + KEY + '</ApiKey><ApiKey>' + KEY + '</ApiKey></Config>',
            '<Config><ApiKey>' + KEY + '</ApiKey><UrlBase>/radarr</UrlBase></Config>',
            '<!DOCTYPE x [<!ENTITY x "x">]><Config><ApiKey>&x;</ApiKey></Config>',
            '<Config><ApiKey>bad</ApiKey></Config>', '<Config><ApiKey x="x">' + KEY + '</ApiKey></Config>',
            '<Config><ApiKey>' + KEY + '</ApiKey></Config>' + 'x' * adapter.IMPORTER_MAX_BODY,
        ):
            with self.subTest(body=body[:30]), self.assertRaises(Refused):
                self.read(body)
        self.read('<Config><ApiKey>' + KEY + '</ApiKey></Config>')
        self.path.chmod(0o644)
        with self.assertRaises(Refused):
            adapter.existing_importer_key('/app/config.xml', os.getuid())
        self.path.chmod(0o600)
        link = self.path.parent / 'link.xml'
        link.symlink_to(self.path)
        with self.assertRaises(Refused):
            adapter.existing_importer_key('/app/link.xml', os.getuid())
        with self.assertRaises(Refused):
            adapter.existing_importer_key('/app/config.xml', os.getuid() + 1)
        self.path.parent.chmod(0o770)
        with self.assertRaisesRegex(Refused, 'Unsafe importer config ancestor'):
            adapter.existing_importer_key('/app/config.xml', os.getuid())


class ImporterTransportTests(unittest.TestCase):

    def test_cleanup_assessment_levels_unknown_and_fixed_routes(self):
        for backend in ('radarr', 'sonarr'):
            known = {'disabled', 'logOnly', 'keepAndUnmonitor'}
            known |= {'keepAndTag'} if backend == 'sonarr' else {'removeAndKeep', 'removeAndDelete'}
            for level in sorted(known) + [None, 'Disabled', 'future-level', 0, True, [], {}]:
                with self.subTest(backend=backend, level=level), base.RecordingHTTP() as http:
                    c = config(backend)
                    http.responses += [dict(appName=backend.capitalize(), version=c['expected_version']),
                                       {'listSyncLevel': level, 'secret': 'private'}, []]
                    api = adapter.ImporterInventoryAdapter(c, request_guard=lambda: 10)
                    api._opener = http
                    with patch.object(adapter, 'existing_importer_key', return_value=KEY):
                        got = api.assessment()
                    expected = level if type(level) is str and level in known else None
                    self.assertEqual(got, {'lists': [], 'cleanup_level': expected})
                    self.assertNotIn('private', json.dumps(got))
                    self.assertEqual([x[0] for x in http.calls], ['GET'] * 3)
                    self.assertEqual([x[1] for x in http.calls],
                        ['/api/v3/system/status', '/api/v3/config/importlist', '/api/v3/importlist'])
                    self.assertIsNone(api._key)
        for settings, failure in [({}, False), ([], True), (None, True),
                                  (b'{"listSyncLevel":', True),
                                  ((500, {}), True),
                                  (b'x' * (adapter.IMPORTER_MAX_BODY + 1), True)]:
            with self.subTest(settings=str(settings)[:40]), base.RecordingHTTP() as http:
                http.responses += [dict(appName='Radarr', version=config()['expected_version']), settings, []]
                api = adapter.ImporterInventoryAdapter(config(), request_guard=lambda: 10)
                api._opener = http
                with patch.object(adapter, 'existing_importer_key', return_value=KEY):
                    if failure:
                        with self.assertRaises(Refused):
                            api.assessment()
                        self.assertEqual(api._calls, 2)  # no lists or fallback
                    else:
                        self.assertIsNone(api.assessment()['cleanup_level'])
                self.assertIsNone(api._key)

    def test_cleanup_read_preserves_aggregate_deadline(self):
        remaining = [10]
        with base.RecordingHTTP() as http:
            def respond(call):
                if call[1] == '/api/v3/system/status':
                    return dict(appName='Radarr', version=config()['expected_version'])
                remaining[0] = 0
                return {'listSyncLevel': 'disabled'}
            http.responder = respond
            api = adapter.ImporterInventoryAdapter(config(), request_guard=lambda: remaining[0])
            api._opener = http
            with patch.object(adapter, 'existing_importer_key', return_value=KEY), self.assertRaises(Refused):
                api.assessment()
            self.assertEqual(api._calls, 2)
            self.assertIsNone(api._key)

    def test_backend_specific_flags_fixed_get_and_privacy(self):
        for backend in ('radarr', 'sonarr'):
            with self.subTest(backend=backend), base.RecordingHTTP() as http:
                c = config(backend)
                row = dict(id=1, name='Synthetic ' + backend + ' list', implementation='SyntheticImport',
                           fields=[{'value': 'private'}], path='/private/path')
                if backend == 'radarr':
                    row.update(enabled=False, enableAuto=True, searchOnAdd=False)
                else:
                    row.update(enableAutomaticAdd=True, searchForMissingEpisodes=False, enabled=True)
                http.responses += [dict(appName=backend.capitalize(), version=c['expected_version']), [row]]
                api = adapter.ImporterInventoryAdapter(c, request_guard=lambda: 10)
                api._opener = http
                with patch.object(adapter, 'existing_importer_key', return_value=KEY) as key:
                    got = api.inventory()
                self.assertEqual(got, FIXTURE['responses'][backend]['result']['lists'])
                key.assert_called_once_with(c['app_config_file'], 1000)
                self.assertIsNone(api._key)
                self.assertNotIn('private', json.dumps(got))
                self.assertEqual([x[0] for x in http.calls], ['GET', 'GET'])
                self.assertEqual([x[1] for x in http.calls], ['/api/v3/system/status', '/api/v3/importlist'])
                self.assertTrue(all(x[2]['X-Api-Key'] == KEY for x in http.calls))
                with self.assertRaises(Refused):
                    api._get('/api/v3/movie/1')

    def test_truncated_and_ambiguous_http_framing_never_become_empty(self):
        cases = [
            ([('Content-Length', '100')], True),
            ([('Content-Length', '2'), ('Content-Length', '2')], True),
            ([('Content-Length', '2'), ('Transfer-Encoding', 'chunked')], True),
            ([('Content-Length', str(adapter.IMPORTER_MAX_BODY + 1))], True),
            ([('Content-Length', '2')], False),
        ]
        for headers, refused in cases:
            with self.subTest(headers=headers):
                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass
                    def do_GET(self):
                        self.send_response(200)
                        if self.path == '/api/v3/system/status':
                            body = json.dumps(dict(appName='Radarr',
                                version=config()['expected_version'])).encode()
                            self.send_header('Content-Length', str(len(body)))
                        else:
                            body = b'[]'
                            for name, value in headers:
                                self.send_header(name, value)
                        self.end_headers()
                        self.wfile.write(body)
                        self.close_connection = True
                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    c = dict(config(), origin='http://127.0.0.1:' + str(server.server_port))
                    api = adapter.ImporterInventoryAdapter(c, request_guard=lambda: 10)
                    with patch.object(adapter, 'existing_importer_key', return_value=KEY):
                        if refused:
                            with self.assertRaises(Refused):
                                api.inventory()
                        else:
                            self.assertEqual(api.inventory(), [])
                    self.assertIsNone(api._key)
                    self.assertEqual(api._calls, 2)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_streaming_or_hung_read_cannot_extend_aggregate_budget(self):
        release = threading.Event()
        finished = threading.Event()
        class Slow:
            def open(self, request, timeout):
                try:
                    release.wait(1)
                    raise OSError('synthetic late read')
                finally:
                    finished.set()
        api = adapter.ImporterInventoryAdapter(config(), request_guard=lambda: 10)
        api._opener = Slow()
        api._deadline = time.monotonic() + 0.03
        started = time.monotonic()
        try:
            with patch.object(adapter, 'existing_importer_key', return_value=KEY), self.assertRaises(Refused):
                api.inventory()
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertEqual(api._calls, 1)
            self.assertIsNone(api._key)
        finally:
            release.set()
            self.assertTrue(finished.wait(1))

    def test_unknown_flags_empty_and_refusals(self):
        cases = [
            ([], None),
            ([dict(id=1, name='N', implementation='I', enableAuto='true', enabled=1)], None),
            ([dict(id=1, name='N', implementation='I')] * 2, Refused),
            ([dict(id=True, name='N', implementation='I')], Refused),
            ({'Items': []}, Refused),
            (b'x' * (adapter.IMPORTER_MAX_BODY + 1), Refused),
        ]
        for rows, error in cases:
            with self.subTest(rows=str(rows)[:40]), base.RecordingHTTP() as http:
                http.responses += [dict(appName='Radarr', version=config()['expected_version']), rows]
                api = adapter.ImporterInventoryAdapter(config(), request_guard=lambda: 10)
                api._opener = http
                with patch.object(adapter, 'existing_importer_key', return_value=KEY):
                    if error:
                        with self.assertRaises(error):
                            api.inventory()
                    else:
                        got = api.inventory()
                        if got:
                            self.assertIsNone(got[0]['automatic_add'])
                            self.assertIsNone(got[0]['enabled'])
                        else:
                            self.assertEqual(got, [])
        for status in ({'appName': 'Sonarr', 'version': config()['expected_version']},
                       {'appName': 'Radarr', 'version': 'new'}, (302, {})):
            with base.RecordingHTTP() as http:
                http.responses += [status]
                api = adapter.ImporterInventoryAdapter(config(), request_guard=lambda: 10)
                api._opener = http
                with patch.object(adapter, 'existing_importer_key', return_value=KEY), self.assertRaises(Refused):
                    api.inventory()
                self.assertEqual(len(http.calls), 1)  # no retry or redirect
        with patch.object(adapter, 'existing_importer_key') as key:
            api = adapter.ImporterInventoryAdapter(config(), request_guard=lambda: 0)
            with self.assertRaises(Refused):
                api.inventory()
            key.assert_not_called()


class ImporterSocketTests(socket_tests.WorkerCase):
    def setUp(self):
        super().setUp()
        self.policy['janitor_importers'] = copy.deepcopy(FIXTURE['policy'])
        self.policy = worker.validate_policy(self.policy)
        self.config_reads = []
        def no_emby(path):
            self.fail('Emby config must not be read')
        patcher = patch.object(worker, 'load_config', no_emby)
        patcher.start()
        self.addCleanup(patcher.stop)
        original = adapter.ImporterInventoryAdapter
        def make_api(c, *, request_guard):
            api = original(c, request_guard=request_guard)
            api._opener = self.http
            return api
        for target, name, value in (
            (worker, 'ImporterInventoryAdapter', make_api),
            (worker, 'private_read', lambda path: json.dumps(config()).encode()),
            (adapter, 'existing_importer_key', lambda path, uid: KEY),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.http.responder = lambda call: (
            dict(appName='Radarr', version=config()['expected_version'])
            if call[1] == '/api/v3/system/status'
            else [dict(id=1, name='Synthetic radarr list', implementation='SyntheticImport',
                       enabled=False, enableAuto=True, searchOnAdd=False)])

    def request(self):
        value = copy.deepcopy(FIXTURE['frame'])
        value['caller']['expires_at'] = int(time.time()) + 300
        value['deadline_unix_ms'] = int(time.time() * 1000) + 60000
        return value

    def test_real_socket_without_emby_history_or_journal_writes(self):
        before = {str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}
        raw, result = self.call(socket_tests.encode(self.request()))
        self.assertEqual(result, FIXTURE['responses']['radarr'])
        self.assertNotIn(KEY.encode(), raw)
        self.assertEqual(self.credential_reads, 0)
        self.assertEqual([x[0] for x in self.backend_calls()], ['GET', 'GET'])
        self.assertEqual({str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}, before)

    def test_cleanup_assessment_socket_compatibility_and_privacy(self):
        fixture = json.loads((ROOT / 'docs/emby-janitor-importers-wire-v2.json').read_text())
        request = self.request()
        request['operation'] = 'janitor-importers-assessment'
        original = self.http.responder
        self.http.responder = lambda call: ({'listSyncLevel': 'removeAndDelete', 'private': 'secret'}
            if call[1] == '/api/v3/config/importlist' else original(call))
        before = {str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}
        raw, result = self.call(socket_tests.encode(request))
        self.assertEqual(result, fixture['responses']['radarr'])
        self.assertNotIn(b'secret', raw)
        self.assertEqual(self.credential_reads, 0)
        self.assertEqual([x[0] for x in self.backend_calls()], ['GET'] * 3)
        self.assertEqual({str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}, before)
        # The new operation has exactly the old independent ceiling.
        self.policy.pop('janitor_importers')
        with patch.object(worker, 'private_read') as read:
            _, refused = self.call(socket_tests.encode(request))
            self.assertFalse(refused['ok'])
            read.assert_not_called()
        self.assertEqual(len(self.backend_calls()), 3)

    def test_missing_opt_in_before_any_file_or_backend(self):
        self.policy.pop('janitor_importers')
        with patch.object(worker, 'private_read') as read:
            _, result = self.call(socket_tests.encode(self.request()))
            self.assertFalse(result['ok'])
            self.assertEqual(result['error']['code'], 'refused')
            read.assert_not_called()
        self.assertEqual(self.backend_calls(), [])


if __name__ == '__main__':
    unittest.main()
