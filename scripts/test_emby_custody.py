"""Synthetic custody tests: fixed HTTP replies and actual isolated filesystem."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import emby_operator_adapter as adapter
import emby_operator_custody as custody
import emby_operator_socket as worker
from emby_operator_recommendations import Refused
import test_emby_importer_inventory as inventory
import test_emby_operator_adapter as base
import test_emby_operator_socket as socket_tests

ROOT = Path(__file__).resolve().parent.parent
BINDING = dict(server='a' * 32, library='3', policy_revision='b' * 40)


class CustodyTests(unittest.TestCase):
    def setUp(self):
        home = ROOT / '.cache/holonet-operator-tests/home'
        self.assertEqual(os.environ['HOME'], str(home))
        self.assertEqual(os.environ['TMPDIR'], str(home / 'tmp'))
        self.temp = tempfile.TemporaryDirectory(dir=home / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'media'
        self.recycle = self.root / 'recycle'
        self.media.mkdir()
        self.recycle.mkdir()
        self.title = self.media / 'Title'
        self.title.mkdir()
        self.path = self.title / 'media.mkv'
        self.data = b'0123456789abcdef'
        self.path.write_bytes(self.data)
        self.entry = dict(roots=[dict(app_prefix='/movies', host_prefix=str(self.media))],
                          recycle=dict(app_prefix='/recycle', host_prefix=str(self.recycle)))
        self.value = dict(backend='radarr', target_kind='movie', target_id=1)

    def responses(self, backend='radarr'):
        name = custody.KINDS[backend]
        return [dict(appName=backend.capitalize(),
                     version=inventory.config(backend)['expected_version']),
                dict(recycleBin='/recycle', recycleBinCleanupDays=0),
                dict(id=1, path='/movies/Title'),
                [dict(id=2, path='/movies/Title/media.mkv', size=len(self.data),
                      **{name + 'Id': 1})]]

    def observe(self, responses=None, value=None, guard=None):
        value = copy.deepcopy(value or self.value)
        with base.RecordingHTTP() as http:
            http.responses += copy.deepcopy(responses or self.responses(value['backend']))
            api = custody.CustodyAdapter(inventory.config(value['backend']), self.entry,
                                        value, request_guard=guard or (lambda: 10))
            api._opener = http
            with patch.object(custody, 'existing_importer_key', return_value=inventory.KEY):
                result = api.observe(BINDING)
            self.assertIsNone(api._key)
            self.assertTrue(all(c[0] == 'GET' for c in http.calls))
            self.assertEqual([c[1] for c in http.calls], list(api.routes))
            return result

    def test_complete_manifest_one_chunk_privacy_and_shared_inode(self):
        seed = self.root / 'seed.mkv'
        os.link(self.path, seed)
        for backend in ('radarr', 'sonarr'):
            value = dict(self.value, backend=backend, target_kind=custody.KINDS[backend])
            got = self.observe(value=value)
            self.assertTrue(got['arr_file_inventory_complete'])
            self.assertTrue(got['position_complete'])
            self.assertIsNone(got['cursor'])
            self.assertEqual(got['chunk']['sha256'], hashlib.sha256(self.data).hexdigest())
            self.assertEqual(got['files'][0]['nlink'], 2)
            self.assertEqual(got['seed_topology'], 'unqualified')
            self.assertFalse(got['media_removal_authorized'])
            self.assertNotIn(str(self.root), json.dumps(got))
            self.assertNotIn('/movies', json.dumps(got))
        self.assertEqual(seed.read_bytes(), self.data)
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_continuation_is_position_not_proof_of_prior_reads(self):
        with patch.object(custody, 'CHUNK_BYTES', 8):
            first = self.observe()
            second = self.observe(value=dict(self.value, cursor=first['cursor']))
        self.assertFalse(first['position_complete'])
        self.assertEqual(first['chunk']['index'], 0)
        self.assertEqual(second['chunk']['index'], 1)
        self.assertEqual(first['inventory_revision'], second['inventory_revision'])
        self.assertEqual(first['chunk']['sha256'], hashlib.sha256(self.data[:8]).hexdigest())
        self.assertEqual(second['chunk']['sha256'], hashlib.sha256(self.data[8:]).hexdigest())
        self.assertTrue(second['position_complete'])

    def test_cursor_refuses_changed_file_target_or_inventory(self):
        with patch.object(custody, 'CHUNK_BYTES', 8):
            first = self.observe()
            responses = self.responses()
            responses[-1][0]['id'] = 3
            with self.assertRaises(Refused):
                self.observe(responses, dict(self.value, cursor=first['cursor']))
            responses = self.responses()
            responses[2]['id'] = 3
            responses[-1][0]['movieId'] = 3
            with self.assertRaises(Refused):
                self.observe(responses, dict(self.value, target_id=3, cursor=first['cursor']))
            self.path.write_bytes(b'x' * len(self.data))
            with self.assertRaises(Refused):
                self.observe(value=dict(self.value, cursor=first['cursor']))

    def test_forged_out_of_bounds_cursor_and_closed_request(self):
        got = self.observe()
        for file, chunk in ((-1, 0), (1, 0), (0, -1), (0, 99), (True, 0)):
            cursor = base64.urlsafe_b64encode(custody.compact(
                dict(revision=got['inventory_revision'], file=file, chunk=chunk))).decode().rstrip('=')
            with self.assertRaises(Refused):
                self.observe(value=dict(self.value, cursor=cursor))
        for change in ({'target_id': True}, {'target_id': 0}, {'target_kind': 'series'},
                       {'backend': 'other'}, {'path': '/etc/passwd'}, {'cursor': '..'}):
            with self.assertRaises(Refused):
                custody.request(dict(self.value, **change))

    def test_bad_identity_stops_after_first_get(self):
        with base.RecordingHTTP() as http:
            http.responses += [dict(appName='Other', version='1.2.3.4')]
            api = custody.CustodyAdapter(inventory.config(), self.entry, self.value,
                                        request_guard=lambda: 10)
            api._opener = http
            with patch.object(custody, 'existing_importer_key', return_value=inventory.KEY), \
                    self.assertRaises(Refused):
                api.observe(BINDING)
            self.assertEqual(len(http.calls), 1)
            self.assertIsNone(api._key)

    def test_invalid_config_and_manifest_refuse(self):
        variants = []
        for index, value in ((1, dict(recycleBin='', recycleBinCleanupDays=0)),
                             (1, dict(recycleBin='/recycle', recycleBinCleanupDays=7)),
                             (1, dict(recycleBin='/recycle', recycleBinCleanupDays=False)),
                             (2, dict(id=99, path='/movies/Title')),
                             (3, None)):
            rows = self.responses()
            rows[index] = value
            variants.append(rows)
        for change in ({'movieId': 99}, {'size': 1}, {'path': '/movies2/media.mkv'},
                       {'path': '/movies/Title/../media.mkv'}, {'id': True}):
            rows = self.responses()
            rows[-1][0].update(change)
            variants.append(rows)
        duplicate = self.responses()
        duplicate[-1] *= 2
        variants.append(duplicate)
        capped = self.responses()
        capped[-1] *= 65
        variants.append(capped)
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises((Refused, OSError)):
                self.observe(rows)

    def test_empty_manifest_is_only_arr_file_observation(self):
        rows = self.responses()
        rows[-1] = []
        got = self.observe(rows)
        self.assertEqual(got['files'], [])
        self.assertIsNone(got['chunk'])
        self.assertFalse(got['media_removal_authorized'])
        rows[2] = {}
        with self.assertRaises(Refused):
            self.observe(rows)

    def test_symlink_and_nested_mount_refusal(self):
        self.path.unlink()
        self.path.symlink_to(self.root / 'outside')
        with self.assertRaises(OSError):
            self.observe()
        self.path.unlink()
        self.path.write_bytes(self.data)
        real_mount = custody.mount_id
        def different_leaf(fd):
            return 999999 if os.fstat(fd).st_mode & 0o170000 == 0o100000 else real_mount(fd)
        with patch.object(custody, 'mount_id', side_effect=different_leaf), self.assertRaises(Refused):
            self.observe()

    def test_path_replacement_during_chunk_refuses(self):
        original = os.pread
        called = False
        def replace(fd, length, offset):
            nonlocal called
            data = original(fd, length, offset)
            if not called:
                called = True
                self.path.unlink()
                self.path.write_bytes(self.data)
            return data
        with patch.object(custody.os, 'pread', side_effect=replace), self.assertRaises(Refused):
            self.observe()

    def test_reply_bound_and_expiry_refuse(self):
        with patch.object(custody, 'MAX_REPLY_BYTES', 10), self.assertRaises(Refused):
            self.observe()
        with self.assertRaises(Refused):
            self.observe(guard=lambda: 0)

    def test_wire_fixture_is_closed_and_independently_opted_in(self):
        fixture = json.loads((ROOT / 'docs/emby-janitor-custody-wire-v1.json').read_text())
        policy = inventory.policy()
        policy['janitor_custody'] = fixture['custody_policy']
        policy = worker.validate_policy(policy)
        call = worker.parse_request(socket_tests.encode(fixture['frame'])[:-1])
        worker.authorize(call, policy)
        self.assertEqual(call, fixture['frame'])
        result = fixture['response']['result']
        self.assertFalse(result['media_removal_authorized'])
        self.assertEqual(result['seed_topology'], 'unqualified')
        self.assertEqual(fixture['bounds']['chunk_bytes'], custody.CHUNK_BYTES)
        self.assertEqual(fixture['bounds']['max_files'], custody.MAX_FILES)

    def test_sixty_four_files_fit_worker_fd_and_reply_limits(self):
        import resource
        rows = self.responses()
        rows[-1] = []
        for index in range(64):
            path = self.title / ('file-%d.mkv' % index)
            path.write_bytes(self.data)
            rows[-1].append(dict(id=index + 1, movieId=1,
                path='/movies/Title/' + path.name, size=len(self.data)))
        old = resource.getrlimit(resource.RLIMIT_NOFILE)
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(64, old[0]), old[1]))
            got = self.observe(rows)
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, old)
        self.assertEqual(len(got['files']), 64)
        self.assertFalse(got['position_complete'])
        self.assertLess(len(custody.compact(got)), custody.MAX_REPLY_BYTES)

    def test_deadline_during_content_returns_no_partial_digest(self):
        stopped = False
        original = os.pread
        def read(fd, size, offset):
            nonlocal stopped
            data = original(fd, size, offset)
            stopped = True
            return data
        with patch.object(custody.os, 'pread', side_effect=read), self.assertRaises(Refused):
            self.observe(guard=lambda: 0 if stopped else 10)

    def test_policy_and_authorization_before_access(self):
        policy = inventory.policy()
        policy['janitor_custody'] = dict(backends=dict(radarr=self.entry))
        policy = worker.validate_policy(policy)
        call = copy.deepcopy(inventory.FIXTURE['frame'])
        call.update(operation=custody.OPERATION, request=self.value)
        call['caller']['actions'] = custody.ACTIONS[:]
        call = worker.parse_request(socket_tests.encode(call)[:-1])
        worker.authorize(call, policy)
        for mutate in (lambda p: p.pop('janitor_custody'),
                       lambda p: p['janitor_custody']['backends'].clear()):
            changed = copy.deepcopy(policy)
            mutate(changed)
            with self.assertRaises(worker.WireError):
                worker.authorize(call, changed)
        for key, value in (('actions', ['janitor.importers.read']),
                           ('namespace', 'managed-recommendations'),
                           ('target_library', '99')):
            changed = copy.deepcopy(call)
            changed['caller'][key] = value
            with self.assertRaises(worker.WireError):
                worker.authorize(changed, policy)
        for mapping in (dict(app_prefix='/movies', host_prefix=str(self.media)),
                        dict(app_prefix='/movies/..', host_prefix=str(self.root / 'x'))):
            changed = copy.deepcopy(policy)
            changed['janitor_custody']['backends']['radarr']['recycle'] = mapping
            with self.assertRaises(Refused):
                worker.validate_policy(changed)
        self.assertNotIn('history_users', call['caller'])
        self.assertNotIn('janitor', policy)


class CustodySocketTests(socket_tests.WorkerCase):
    def setUp(self):
        super().setUp()
        self.policy['janitor_importers'] = copy.deepcopy(inventory.FIXTURE['policy'])
        media = self.directory / 'custody-media'
        recycle = self.directory / 'custody-recycle'
        (media / 'Title').mkdir(parents=True)
        recycle.mkdir()
        (media / 'Title' / 'media.mkv').write_bytes(b'abc')
        entry = dict(roots=[dict(app_prefix='/movies', host_prefix=str(media))],
                     recycle=dict(app_prefix='/recycle', host_prefix=str(recycle)))
        self.policy['janitor_custody'] = dict(backends=dict(radarr=entry))
        self.policy = worker.validate_policy(self.policy)
        original = custody.CustodyAdapter
        def make_api(config, mapping, value, *, request_guard):
            api = original(config, mapping, value, request_guard=request_guard)
            api._opener = self.http
            return api
        for owner, name, value in (
            (custody, 'CustodyAdapter', make_api),
            (custody, 'private_read', lambda path: json.dumps(inventory.config()).encode()),
            (custody, 'existing_importer_key', lambda *args: inventory.KEY),
            (worker, 'load_config', lambda *args: self.fail('Emby config read')),
        ):
            patcher = patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        responses = {
            '/api/v3/system/status': dict(appName='Radarr',
                version=inventory.config()['expected_version']),
            '/api/v3/config/mediamanagement': dict(recycleBin='/recycle',
                recycleBinCleanupDays=0),
            '/api/v3/movie/1': dict(id=1, path='/movies/Title'),
            '/api/v3/moviefile?movieId=1': [
                dict(id=2, movieId=1, path='/movies/Title/media.mkv', size=3)],
        }
        self.http.responder = lambda call: responses[call[1]]

    def request(self):
        import time
        fixture = json.loads((ROOT / 'docs/emby-janitor-custody-wire-v1.json').read_text())
        value = fixture['frame']
        value['caller']['expires_at'] = int(time.time()) + 300
        value['deadline_unix_ms'] = int(time.time() * 1000) + 60000
        return value

    def test_actual_socket_read_only_private_projection(self):
        before = {str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}
        raw, result = self.call(socket_tests.encode(self.request()))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['chunk']['sha256'], hashlib.sha256(b'abc').hexdigest())
        self.assertFalse(result['result']['media_removal_authorized'])
        self.assertNotIn(str(self.directory).encode(), raw)
        self.assertNotIn(inventory.KEY.encode(), raw)
        self.assertEqual(self.credential_reads, 0)
        self.assertEqual([x[0] for x in self.backend_calls()], ['GET'] * 4)
        after = {str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_missing_opt_in_and_action_refuse_before_config(self):
        self.policy.pop('janitor_custody')
        with patch.object(custody, 'private_read') as read:
            _, result = self.call(socket_tests.encode(self.request()))
            self.assertEqual(result['error']['code'], 'refused')
            read.assert_not_called()
        self.assertEqual(self.backend_calls(), [])


if __name__ == '__main__':
    unittest.main()
