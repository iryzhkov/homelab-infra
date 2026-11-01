"""Socket worker checks over real AF_UNIX framing and the shared entry. Parent runs in Chaldea only.

Requests travel over a real abstract-namespace AF_UNIX stream socket to
worker.serve in a thread, which reads the actual SO_PEERCRED of this test
process. Operations run through the real shared entry, core Journal/Operator
and OperatorAdapter against the loopback recording model from
test_emby_operator_adapter, with synthetic IDs only. The test user stands in
for root custody. Root file custody, the systemd units, the Go client and live
Emby behavior are not exercised; nothing here is runtime evidence.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import emby_operator_adapter as adapter
import emby_operator_entry as lifecycle
import emby_operator_socket as worker
import test_emby_operator_adapter as base
from emby_operator_recommendations import DONE_LIMIT, LIST_OPERATIONS, Journal, Refused

SCRIPTS = Path(__file__).resolve().parent
HOME = SCRIPTS.parent / '.cache/holonet-operator-tests/home'
FIXTURES = json.loads((SCRIPTS.parent / 'docs/emby-operator-mcp-wire-v1.json').read_text())
CONFIG_PATH = '/synthetic/recipient/config.json'
PASSWORD = 'synthetic-password'
DIGEST = '0' * 64
MEMBERS = {'operation': 'set-members', 'list': 'a', 'movies': ['m2']}
# One valid request for every list operation on the seeded list 'a' or a new list 'n'.
LIST_REQUESTS = {
    'create-list': {'operation': 'create-list', 'list': 'n', 'title': 'Canary', 'movies': ['m5']},
    'set-members': MEMBERS,
    'set-title': {'operation': 'set-title', 'list': 'a', 'title': 'Movies for tonight'},
    'attach-row': {'operation': 'attach-row', 'list': 'n', 'title': 'Canary'},
    'detach-row': {'operation': 'detach-row', 'list': 'n'},
    'move-row': {'operation': 'move-row', 'list': 'n', 'position': 0},
    'delete-list': {'operation': 'delete-list', 'list': 'n'},
}


def isolated(case):
    case.assertEqual(os.environ['HOME'], str(HOME))
    case.assertEqual(os.environ['TMPDIR'], str(HOME / 'tmp'))
    # Never create test files outside the parent's explicitly isolated HOME.
    scratch = tempfile.TemporaryDirectory(dir=HOME / 'tmp')
    case.addCleanup(scratch.cleanup)
    return Path(scratch.name)


def policy(**peer):
    value = copy.deepcopy(FIXTURES['policy'])
    value['peer'] = dict({'uid': os.getuid(), 'gid': os.getgid()}, **peer)
    return worker.validate_policy(value)


def frame(operation='show', request=None, *, caller=None, now=None, **top):
    now = int(time.time()) if now is None else now
    value = {'version': 1, 'caller': copy.deepcopy(FIXTURES['caller']), 'recipient': base.R,
             'deadline_unix_ms': now * 1000 + 60000, 'operation': operation,
             'request': {} if request is None else copy.deepcopy(request)}
    value['caller']['expires_at'] = now + 300
    value['caller'].update(caller or {})
    value.update(top)
    return value


def transformed(case):
    value = frame()
    for name in case.get('delete', []):
        del value[name]
    value.update(copy.deepcopy(case.get('set', {})))
    value['caller'].update(copy.deepcopy(case.get('caller_set', {})))
    return value


def encode(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode() + b'\n'


def expected_key(logical):
    """Independent derivation of the frozen v1 internal key."""
    c = FIXTURES['caller']
    material = [c['issuer'], c['subject'], c['namespace'], c['server'], c['library'],
                base.R, logical]
    canonical = json.dumps(material, separators=(',', ':'), ensure_ascii=False)
    return 'mcp-' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class WorkerCase(unittest.TestCase):
    """Real socket, worker, shared entry, core and adapter; recording backend."""
    def setUp(self):
        self.scratch = isolated(self)
        self.directory = self.scratch / 'journal'
        self.directory.mkdir(mode=0o700)
        self.directory.chmod(0o700)
        self.config = dict(base.config(), journal_directory=str(self.directory))
        self.backend = base.MultiBackend()
        self.http = base.RecordingHTTP()
        self.http.responder = self.backend.respond
        self.http.__enter__()
        self.addCleanup(self.http.__exit__, None, None, None)
        self.policy = policy()
        self.client = None
        self.reset()
        for target, name, value in ((worker, 'load_policy', lambda: copy.deepcopy(self.policy)),
                                    (worker, 'load_config', self.load_config),
                                    (lifecycle, 'load_credentials', self.load_credentials),
                                    (lifecycle, 'Journal', self.make_journal),
                                    (lifecycle, 'OperatorAdapter', self.make_api),
                                    # The read-only movie-metadata path builds these directly.
                                    (worker, 'load_credentials', self.load_credentials),
                                    (worker, 'Journal', self.make_journal),
                                    (worker, 'OperatorAdapter', self.make_api)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # The protected shared entry seeds list 'a' without the worker.
        self.direct('create-list', 'seed-a', 'a', title='A', movies=['m1'])
        self.reset()

    def reset(self):
        self.offset, self.write_offset = len(self.http.calls), len(self.backend.writes)
        self.config_reads, self.credential_reads, self.guards, self.apis = [], 0, [], []

    def load_config(self, path):
        self.config_reads.append(path)
        if path != CONFIG_PATH:
            raise Refused('Protected file unavailable or unsafe')
        return copy.deepcopy(self.config)

    def load_credentials(self, path):
        self.assertEqual(path, self.config['credential_file'])
        self.credential_reads += 1
        return {'username': 'synthetic-admin', 'password': PASSWORD}

    def make_journal(self, directory, *, root_safe):
        self.assertEqual((directory, root_safe), (str(self.directory), True))
        # The test user stands in for root custody; directory and lock code stay real.
        journal = Journal(directory)
        self.backend.journal = journal
        return journal

    def make_api(self, config, journal, **kwargs):
        self.assertLessEqual(set(kwargs), {'request_guard'})
        api = adapter.OperatorAdapter(config, journal, **kwargs)
        api._opener = self.http
        self.guards.append(kwargs.get('request_guard'))
        self.apis.append(api)
        return api

    def direct(self, operation, key, list_id, **args):
        """Protected manual path: shared entry without a guard, preview then run."""
        with lifecycle.operator_session(self.config) as operator:
            intent = operator.list_preview(operation, list_id, **args)['intent_digest']
        with lifecycle.operator_session(self.config) as operator:
            return getattr(operator, operation.replace('-', '_'))(
                key, list_id, intent_digest=intent, **args)

    def stored(self):
        return json.loads((self.directory / 'journal.json').read_bytes())

    def journal_bytes(self):
        return (self.directory / 'journal.json').read_bytes()

    def backend_calls(self):
        return self.http.calls[self.offset:]

    def mutation_posts(self):
        return [c[1] for c in self.backend_calls()
                if c[0] == 'POST' and c[1] != '/emby/Users/AuthenticateByName']

    def writes(self):
        return [w[0] for w in self.backend.writes[self.write_offset:]]

    def call(self, raw, *, chunks=None, shutdown_write=False):
        """One real connection: returns the raw response and the served envelope."""
        name = '\0holonet-worker-test-' + os.urandom(8).hex()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(name)
        listener.listen(1)
        served = {}

        def run():
            conn, _ = listener.accept()
            with conn:
                served['envelope'] = worker.serve(conn)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(client.close)
        client.settimeout(30)
        client.connect(name)
        self.client = client
        for part in (chunks if chunks is not None else [raw]):
            client.sendall(part)
        if shutdown_write:
            client.shutdown(socket.SHUT_WR)
        response = b''
        try:
            while len(response) <= worker.MAX_RESPONSE:
                part = client.recv(65536)
                if not part:
                    break
                response += part
        except OSError:
            pass
        thread.join(30)
        self.assertFalse(thread.is_alive())
        return response, served['envelope']

    def roundtrip(self, value, **kwargs):
        raw, served = self.call(encode(value) if type(value) is dict else value, **kwargs)
        self.assertTrue(raw.endswith(b'\n') and raw.count(b'\n') == 1, raw)
        self.assertLessEqual(len(raw), worker.MAX_RESPONSE)
        parsed = adapter.strict_json(raw[:-1].decode('ascii'))
        self.assertEqual(parsed, served)
        self.assertEqual(set(parsed), {'version', 'ok', 'result' if parsed['ok'] else 'error'})
        self.assertEqual(parsed['version'], 1)
        return parsed

    def ok(self, operation, request=None, **kwargs):
        response = self.roundtrip(frame(operation, request, **kwargs))
        self.assertTrue(response['ok'], response)
        return response['result']

    def error(self, value, code, message=None, **kwargs):
        response = self.roundtrip(value, **kwargs)
        self.assertFalse(response['ok'], response)
        self.assertLessEqual(set(response['error']), {'code', 'message', 'operation', 'list'})
        self.assertEqual(response['error']['code'], code)
        if message is not None:
            self.assertEqual(response['error']['message'], message)
        return response['error']

    def assert_untouched(self):
        """No backend send, credential read or caller-independent config read."""
        self.assertEqual(self.backend_calls(), [])
        self.assertEqual((self.config_reads, self.credential_reads), ([], 0))

    def previewed(self, request, logical):
        """Preview through the worker; returns the identical request to apply."""
        preview = self.ok('preview', request)
        self.assertEqual(preview['request'], request)
        return dict(preview['request'], key=logical, intent_digest=preview['intent_digest'])

    def unfinished_by_cancel(self, logical='logical-1'):
        """Cancel inside the mutation method, before its POST; returns the request."""
        preview = self.ok('preview', MEMBERS)
        request = dict(preview['request'], key=logical, intent_digest=preview['intent_digest'])
        original, fired = self.backend.respond, []

        def respond(call):
            journal = self.backend.journal
            if (not fired and call[0] == 'GET' and journal._locked and
                    (journal.load()['pending'] or {}).get('operation') == 'clear'):
                fired.append(call[1])
                self.client.shutdown(socket.SHUT_RDWR)
            return original(call)
        self.http.responder = respond
        self.reset()
        try:
            raw, served = self.call(encode(frame('apply', request)))
        finally:
            self.http.responder = original
        self.assertTrue(fired)
        self.assertEqual(raw, b'')
        self.assertEqual(served['error'], {'code': 'deadline', 'message': worker.STOPPED['cancelled']})
        return request


class WireFixtureTests(WorkerCase):
    def test_frozen_requests_parse_or_refuse_with_exact_codes(self):
        example = FIXTURES['frame_example'].encode()
        self.assertEqual(example[-1:], b'\n')
        call = worker.parse_request(example[:-1])
        worker.authorize(call, self.policy)
        self.assertEqual(json.dumps(call, separators=(',', ':')).encode() + b'\n', example)
        for case in FIXTURES['valid']:
            with self.subTest(case=case['name']):
                call = worker.parse_request(encode(frame(case['operation'], case['request']))[:-1])
                worker.authorize(call, self.policy)
                self.assertEqual(call['request'], case['request'])
        for case in FIXTURES['invalid'] + FIXTURES['denied']:
            with self.subTest(case=case['name']), self.assertRaises(worker.WireError) as caught:
                call = worker.parse_request(encode(transformed(case))[:-1])
                worker.authorize(call, self.policy)
            self.assertEqual((caught.exception.code, caught.exception.message),
                             (case['code'], case['message']))
        for case in FIXTURES['invalid_text']:
            with self.subTest(case=case['name']), self.assertRaises(worker.WireError) as caught:
                worker.parse_request(case['text'].encode())
            self.assertEqual((caught.exception.code, caught.exception.message),
                             (case['code'], case['message']))

    def test_frozen_internal_keys_and_response_lines(self):
        for case in FIXTURES['internal_key']:
            c = case['caller']
            material = [c['issuer'], c['subject'], c['namespace'], c['server'], c['library'],
                        case['recipient'], case['key']]
            canonical = json.dumps(material, separators=(',', ':'), ensure_ascii=False)
            self.assertEqual(canonical, case['canonical'])
            self.assertEqual('mcp-' + hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
                             case['internal_key'])
            self.assertEqual(worker.internal_key(c, case['recipient'], case['key']),
                             case['internal_key'])
        self.assertEqual(expected_key('logical-1'), FIXTURES['internal_key'][0]['internal_key'])
        for case in FIXTURES['responses']:
            with self.subTest(case=case['name']):
                if 'envelope' in case:
                    value = case['envelope']
                else:
                    e = case['error']
                    value = worker.envelope(error=worker.WireError(
                        e['code'], e['message'], e.get('operation'), e.get('list')))
                self.assertEqual(worker.encode(value), case['line'].encode())
        self.assertEqual(sorted(FIXTURES['error_codes']), sorted(worker.ERROR_CODES))
        self.assertEqual((FIXTURES['framing']['max_request_bytes_including_newline'],
                          FIXTURES['framing']['max_response_bytes_including_newline']),
                         (worker.MAX_REQUEST, worker.MAX_RESPONSE))

    def test_list_operation_table_matches_core_and_worker(self):
        table = FIXTURES['list_operations']
        self.assertEqual(set(table), set(LIST_OPERATIONS))
        self.assertEqual(set(worker.LIST_ACTIONS), set(LIST_OPERATIONS))
        self.assertEqual(set(LIST_REQUESTS), set(LIST_OPERATIONS))
        for name, entry in table.items():
            with self.subTest(operation=name):
                self.assertEqual(tuple(entry['arguments']), LIST_OPERATIONS[name])
                self.assertEqual(entry['actions'], sorted(worker.LIST_ACTIONS[name]))
                self.assertFalse(worker.LIST_ACTIONS[name] & worker.BASE_ACTIONS)
                self.assertLessEqual(worker.LIST_ACTIONS[name], set(FIXTURES['caller']['actions']))
        self.assertEqual(set(FIXTURES['required_actions']['every_call']), worker.BASE_ACTIONS)
        for name in ('set-members', 'set-title'):
            self.assertEqual({FIXTURES['required_actions'][name]}, worker.LIST_ACTIONS[name])
        self.assertEqual(worker.LIST_ACTIONS['create-list'] - worker.LIST_ACTIONS['set-members'],
                         {'home.manage', 'object.share'})
        self.assertIn('object.delete', worker.LIST_ACTIONS['delete-list'])

    def test_position_is_a_strict_integer_below_one_hundred(self):
        for position in (0, 1, 99):
            request = dict(LIST_REQUESTS['move-row'], position=position)
            self.assertEqual(worker.typed_request('preview', request), request)
        for position in (True, False, -1, 100, 1.0, '0', None, [0]):
            with self.subTest(position=position), self.assertRaises(worker.WireError) as caught:
                worker.typed_request('preview', dict(LIST_REQUESTS['move-row'], position=position))
            self.assertEqual((caught.exception.code, caught.exception.message),
                             ('refused', 'Invalid row position'))

    def test_deadline_is_min_of_expiry_caller_and_ceiling(self):
        now_ms = 1793491140000
        call = {'caller': {'expires_at': 10 ** 10}, 'deadline_unix_ms': 10 ** 13}
        self.assertEqual(worker.remaining_seconds(call, now_ms), worker.MAX_SECONDS)
        call['deadline_unix_ms'] = now_ms + 1500
        self.assertEqual(worker.remaining_seconds(call, now_ms), 1.5)
        call['caller']['expires_at'] = now_ms // 1000 + 1
        self.assertEqual(worker.remaining_seconds(call, now_ms), 1.0)
        for expires, deadline in ((now_ms // 1000, now_ms + 1000), (10 ** 10, now_ms)):
            call = {'caller': {'expires_at': expires}, 'deadline_unix_ms': deadline}
            with self.assertRaises(worker.WireError) as caught:
                worker.remaining_seconds(call, now_ms)
            self.assertEqual(caught.exception.code, 'deadline')


class SocketBoundaryTests(WorkerCase):
    def test_invalid_and_denied_requests_refuse_before_any_backend_or_file(self):
        for case in FIXTURES['invalid'] + FIXTURES['denied']:
            with self.subTest(case=case['name']):
                self.reset()
                self.error(transformed(case), case['code'], case['message'])
                self.assert_untouched()
        for case in FIXTURES['invalid_text']:
            with self.subTest(case=case['name']):
                self.reset()
                self.error(case['text'].encode() + b'\n', case['code'], case['message'])
                self.assert_untouched()

    def test_frame_bounds_split_reads_trailing_data_and_encoding(self):
        good = encode(frame('show'))
        oversize = good[:1] + b' ' * (worker.MAX_REQUEST + 1 - len(good)) + good[1:]
        limit = good[:1] + b' ' * (worker.MAX_REQUEST - len(good)) + good[1:]
        self.assertEqual((len(oversize), len(limit)), (worker.MAX_REQUEST + 1, worker.MAX_REQUEST))
        cases = [(oversize, {}, 'Request too large'),
                 (good[:-1], {'shutdown_write': True}, 'Incomplete request frame'),
                 (good + b'{}\n', {}, 'Trailing data after request'),
                 (good + good, {}, 'Trailing data after request'),
                 (b'\xff' + good, {}, 'Invalid UTF-8 request'),
                 (b'\xef\xbb\xbf' + good, {}, 'Invalid JSON'),
                 (good[:-1] + b'\r\n', {}, 'Invalid request frame'),
                 (b'\n', {}, 'Invalid request frame')]
        for raw, kwargs, message in cases:
            with self.subTest(message=message, size=len(raw)):
                self.reset()
                self.error(raw, 'refused', message, **kwargs)
                self.assert_untouched()
        self.reset()
        self.assertEqual(self.roundtrip(limit)['result']['lists'][0]['list'], 'a')
        half = len(good) // 2
        self.assertTrue(self.roundtrip(b'', chunks=[good[:half], good[half:]])['ok'])

    def test_request_read_is_bounded_without_a_frame(self):
        with patch.object(worker, 'READ_SECONDS', 0.3):
            started = time.monotonic()
            self.error(b'', 'deadline', 'Request not received in time', chunks=[])
            self.assertLess(time.monotonic() - started, 5)
        self.assert_untouched()

    def test_peer_uid_and_gid_must_both_match(self):
        self.assertEqual(worker.peer_credentials(self.socketpair()[0]),
                         (os.getpid(), os.getuid(), os.getgid()))
        for peer in ({'uid': os.getuid() + 1}, {'gid': os.getgid() + 1}):
            with self.subTest(peer=peer):
                self.policy = policy(**peer)
                self.reset()
                self.error(frame('show'), 'refused', 'Peer not authorized')
                self.assert_untouched()

    def socketpair(self):
        pair = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        for s in pair:
            self.addCleanup(s.close)
        return pair

    def test_peer_state_distinguishes_open_data_and_close(self):
        a, b = self.socketpair()
        self.assertEqual(worker.peer_state(a), 'open')
        b.sendall(b'x')
        self.assertEqual(worker.peer_state(a), 'data')
        c, d = self.socketpair()
        d.shutdown(socket.SHUT_WR)
        self.assertEqual(worker.peer_state(c), 'closed')
        e, f = self.socketpair()
        f.close()
        self.assertEqual(worker.peer_state(e), 'closed')

    def test_policy_and_selected_config_are_root_side_only(self):
        self.policy = dict(self.policy, enabled=False)
        self.error(frame('show'), 'unavailable', 'Management disabled')
        self.assert_untouched()
        self.policy = policy()
        with patch.object(worker, 'load_policy', side_effect=Refused('Unsafe protected file')):
            self.error(frame('show'), 'unavailable', 'Management unavailable')
        self.assert_untouched()
        for change, message in ((dict(server='b' * 32), 'Recipient configuration does not match policy'),
                                (dict(library='8'), 'Recipient configuration does not match policy'),
                                (dict(recipient='e' * 32), 'Recipient configuration does not match policy'),
                                (dict(enabled=False), 'Recipient management disabled')):
            with self.subTest(change=change):
                saved, self.config = self.config, dict(self.config, **change)
                self.reset()
                self.error(frame('show'), 'unavailable', message)
                self.config = saved
                self.assertEqual(self.config_reads, [CONFIG_PATH])
                self.assertEqual((self.backend_calls(), self.credential_reads), ([], 0))
        self.policy['recipients'] = {base.R: '/synthetic/other/config.json'}
        self.reset()
        self.error(frame('show'), 'unavailable', 'Recipient configuration unavailable')
        self.assertEqual(self.config_reads, ['/synthetic/other/config.json'])

    def test_each_missing_list_action_refuses_before_any_backend_or_file(self):
        for target, request in LIST_REQUESTS.items():
            for action in sorted(worker.LIST_ACTIONS[target]):
                actions = [a for a in FIXTURES['caller']['actions'] if a != action]
                for operation, body in (('preview', request),
                                        ('apply', dict(request, key='k', intent_digest=DIGEST))):
                    with self.subTest(target=target, missing=action, operation=operation):
                        self.reset()
                        self.error(frame(operation, body, caller={'actions': actions}),
                                   'refused', 'Missing required action')
                        self.assert_untouched()

    def test_expired_token_or_past_deadline_stop_before_any_backend(self):
        now = int(time.time())
        for value in (frame('show', caller={'expires_at': now - 1}),
                      frame('show', deadline_unix_ms=now * 1000 - 1)):
            self.reset()
            self.error(value, 'deadline', 'Deadline or token expiry already reached')
            self.assert_untouched()


class LifecycleTests(WorkerCase):
    def test_show_preview_apply_replay_conflict_and_title(self):
        shown = self.ok('show')
        fields = FIXTURES['show_result_fields']
        self.assertEqual(set(shown), set(fields['from_core_version_2'] + fields['added_by_worker']))
        self.assertEqual((shown['archived_count'], shown['completed_count']), (0, 1))
        self.assertEqual([x['list'] for x in shown['lists']], ['a'])
        self.assertIsNone(shown['run'])
        self.assertEqual((shown['done_count'], shown['done_limit'], shown['keys_exhausted'],
                          shown['capacity_warning']), (1, DONE_LIMIT, False, False))
        self.assertEqual(self.ok('reconcile'), {'state': 'clean'})
        preview = self.ok('preview', dict(MEMBERS, movies=['m2', 'm3']))
        self.assertEqual(preview['request'], dict(MEMBERS, movies=['m2', 'm3']))
        request = dict(preview['request'], key='logical-1', intent_digest=preview['intent_digest'])
        self.reset()
        self.assertEqual(self.ok('apply', request), {'state': 'members-set', 'writes': 2})
        self.assertEqual(self.writes(), ['clear', 'add'])
        self.assertEqual((self.config_reads, self.credential_reads), ([CONFIG_PATH], 1))
        self.assertIsInstance(self.guards[0], worker.Guard)
        done = self.stored()['done']
        self.assertEqual(done[expected_key('logical-1')], dict(MEMBERS, movies=['m2', 'm3']))
        self.assertNotIn('logical-1', done)
        self.reset()
        self.assertEqual(self.ok('apply', request), {'state': 'replayed', 'writes': 0})
        self.assertEqual(self.mutation_posts(), [])
        # Same logical key with a different request: the core refuses the reuse.
        self.error(frame('apply', dict(request, movies=['m4'])), 'refused', 'Idempotency key reused')
        # A stale digest never authorizes a new key.
        self.error(frame('apply', dict(request, key='logical-stale')), 'refused',
                   'Fresh preview digest required')
        preview = self.ok('preview', {'operation': 'set-title', 'list': 'a', 'title': 'Movies for tonight'})
        self.reset()
        self.assertEqual(self.ok('apply', dict(preview['request'], key='logical-2',
                                               intent_digest=preview['intent_digest'])),
                         {'state': 'title-set', 'writes': 1})
        self.assertEqual(self.writes(), ['display-title'])
        raw, _ = self.call(encode(frame('show')))
        for secret in (expected_key('logical-1'), expected_key('logical-2'), 'logical-1',
                       'synthetic-session-token', PASSWORD, 'seed-a'):
            self.assertNotIn(secret.encode(), raw)
        shown = json.loads(raw)['result']
        self.assertEqual(shown['lists'][0]['display_title'], 'Movies for tonight')
        self.assertEqual(shown['done_count'], 3)

    def test_full_lifecycle_exercises_every_new_operation_and_exact_inverse(self):
        """Canary order create, detach, attach, move, delete on a temporary list."""
        before = self.ok('show')
        home, items = copy.deepcopy(self.backend.home), copy.deepcopy(self.backend.items)
        steps = (('create-list', 'created', ['create', 'share', 'row', 'display-title', 'add']),
                 ('detach-row', 'detached', ['unrow']),
                 ('attach-row', 'attached', ['row', 'display-title']),
                 ('move-row', 'moved', ['move']),
                 ('delete-list', 'deleted', ['unrow', 'delete']))
        for n, (target, state, writes) in enumerate(steps):
            with self.subTest(operation=target):
                logical = 'lifecycle-%d' % n
                request = self.previewed(LIST_REQUESTS[target], logical)
                self.reset()
                self.assertEqual(self.ok('apply', request), {'state': state, 'writes': len(writes)})
                self.assertEqual(self.writes(), writes)
                self.assertEqual(self.stored()['done'][expected_key(logical)], LIST_REQUESTS[target])
                self.reset()
                self.assertEqual(self.ok('apply', request), {'state': 'replayed', 'writes': 0})
                self.assertEqual(self.mutation_posts(), [])
                shown = {x['list']: x for x in self.ok('show')['lists']}
                if target == 'move-row':
                    self.assertEqual(shown['n']['position'], 0)
                if target == 'delete-list':
                    self.assertNotIn('n', shown)
        after = self.ok('show')
        self.assertEqual([x['list'] for x in after['lists']], ['a'])
        self.assertEqual((after['run'], after['pending'], after['done_count']),
                         (None, None, before['done_count'] + len(steps)))
        self.assertEqual((self.backend.home, self.backend.items), (home, items))
        self.assertEqual(self.ok('reconcile'), {'state': 'clean'})

    def test_lost_create_response_resumes_only_with_identical_key_and_request(self):
        request = dict(LIST_REQUESTS['create-list'], movies=['m5', 'm6'])
        applied = self.previewed(request, 'create-n')
        self.backend.lose = 'share'
        self.reset()
        error = self.error(frame('apply', applied), 'refused', 'Operator HTTP failed; no retry')
        self.assertEqual(set(error), {'code', 'message'})
        self.backend.lose = None
        self.assertEqual(self.writes(), ['create', 'share'])
        stored = self.stored()
        self.assertEqual(stored['run'], {'key': expected_key('create-n'), 'request': request})
        self.assertEqual((stored['pending']['operation'], stored['pending']['list']), ('share', 'n'))
        raw, _ = self.call(encode(frame('show')))
        for secret in (expected_key('create-n'), 'create-n', 'synthetic-session-token', PASSWORD):
            self.assertNotIn(secret.encode(), raw)
        shown = json.loads(raw)['result']
        self.assertEqual(shown['run'], {'operation': 'create-list', 'list': 'n',
                                        'args': {'title': 'Canary', 'movies': ['m5', 'm6']}})
        self.assertEqual((shown['pending'], shown['reconcile']),
                         ({'operation': 'share', 'list': 'n'}, {'state': 'observed-applied'}))
        self.assertEqual(self.ok('reconcile'), {'state': 'observed-applied', 'operation': 'share',
                                                'list': 'n'})
        # Neither another key, changed arguments nor a fresh preview takes over the run.
        self.reset()
        for value in (frame('apply', dict(applied, key='create-other')),
                      frame('apply', dict(applied, movies=['m5'])),
                      frame('preview', request)):
            with self.subTest(value=value['request']):
                raw, _ = self.call(encode(value))
                self.assertEqual(json.loads(raw)['error'], {
                    'code': 'unfinished', 'message': 'Another request is unfinished',
                    'operation': 'create-list', 'list': 'n'})
                self.assertNotIn(expected_key('create-n').encode(), raw)
        self.assertEqual(self.mutation_posts(), [])
        # The identical key, request and digest complete it from exact readback.
        self.assertEqual(self.ok('apply', applied), {'state': 'created', 'writes': 3})
        self.assertEqual(self.writes(), ['row', 'display-title', 'add'])
        self.assertEqual(self.ok('apply', applied), {'state': 'replayed', 'writes': 0})
        self.assertEqual(self.stored()['done'][expected_key('create-n')], request)

    def test_guard_stop_before_playlist_delete_send_resumes_the_same_request(self):
        request = {'operation': 'delete-list', 'list': 'a'}
        applied = self.previewed(request, 'delete-a')
        original, fired = self.backend.respond, []

        def respond(call):
            journal = self.backend.journal
            if (not fired and call[0] == 'GET' and journal._locked and
                    (journal.load()['pending'] or {}).get('operation') == 'delete'):
                fired.append(call[1])
                self.guards[-1].deadline = time.monotonic() - 1
            return original(call)
        self.http.responder = respond
        self.reset()
        try:
            self.error(frame('apply', applied), 'deadline', worker.STOPPED['deadline'])
        finally:
            self.http.responder = original
        self.assertTrue(fired)
        self.assertEqual(self.guards[-1].tripped, 'deadline')
        self.assertEqual(self.writes(), ['unrow'])
        stored = self.stored()
        self.assertIsNone(stored['pending'])
        self.assertEqual(stored['run'], {'key': expected_key('delete-a'), 'request': request})
        self.assertEqual(stored['lists'], {'a': {'row': None, 'display_title': 'A'}})
        shown = self.ok('show')
        self.assertEqual(shown['run'], {'operation': 'delete-list', 'list': 'a', 'args': {}})
        self.reset()
        self.assertEqual(self.ok('apply', applied), {'state': 'deleted', 'writes': 1})
        self.assertEqual(self.writes(), ['delete'])
        self.assertEqual((self.stored()['lists'], self.backend.items), ({}, {}))
        self.assertEqual(self.ok('apply', applied), {'state': 'replayed', 'writes': 0})

    def test_worst_case_fourth_list_create_through_worker_fits_unchanged_budget(self):
        for list_id in 'bc':
            self.direct('create-list', 'seed-' + list_id, list_id, title='Same', movies=['m1'])
        movies = ['m%d' % n for n in range(1, 26)]
        request = {'operation': 'create-list', 'list': 'd', 'title': 'Fourth', 'movies': movies}
        applied = self.previewed(request, 'create-d')
        self.reset()
        self.assertEqual(self.ok('apply', applied), {'state': 'created', 'writes': 5})
        self.assertEqual(self.writes(), ['create', 'share', 'row', 'display-title', 'add'])
        # One worker invocation, distinct recipient, fourth list, 25 Movies: a
        # recorded synthetic count against the unchanged limits, not runtime evidence.
        self.assertEqual((adapter.MAX_REQUESTS, adapter.MAX_SECONDS, worker.MAX_SECONDS), (512, 120, 120))
        self.assertNotEqual(base.A, base.R)
        self.assertEqual(len(self.apis), 1)
        self.assertEqual(self.apis[0]._requests, len(self.backend_calls()))
        self.assertLess(self.apis[0]._requests, adapter.MAX_REQUESTS)

    def test_cancel_before_mutation_send_clears_only_pending_then_same_request_resumes(self):
        request = self.unfinished_by_cancel()
        self.assertEqual(self.writes(), [])
        stored = self.stored()
        self.assertIsNone(stored['pending'])
        self.assertEqual(stored['run'], {'key': expected_key('logical-1'), 'request': MEMBERS})
        self.reset()
        shown = self.ok('show')
        self.assertEqual(shown['run'], {'operation': 'set-members', 'list': 'a',
                                        'args': {'movies': ['m2']}})
        self.assertIsNone(shown['pending'])
        for value in (frame('apply', dict(request, key='logical-other')),
                      frame('apply', dict(request, movies=['m3'])),
                      frame('preview', MEMBERS)):
            with self.subTest(value=value['request']):
                raw, _ = self.call(encode(value))
                error = json.loads(raw)['error']
                self.assertEqual(error, {'code': 'unfinished', 'message': 'Another request is unfinished',
                                         'operation': 'set-members', 'list': 'a'})
                self.assertNotIn(expected_key('logical-1').encode(), raw)
        self.assertEqual(self.mutation_posts(), [])
        self.assertEqual(self.ok('apply', request), {'state': 'members-set', 'writes': 2})
        self.assertEqual(self.writes(), ['clear', 'add'])
        self.assertEqual(self.ok('apply', request), {'state': 'replayed', 'writes': 0})

    def test_guard_deadline_before_mutation_send_is_not_dispatched(self):
        preview = self.ok('preview', MEMBERS)
        request = dict(preview['request'], key='logical-1', intent_digest=preview['intent_digest'])
        original, fired = self.backend.respond, []

        def respond(call):
            journal = self.backend.journal
            if (not fired and call[0] == 'GET' and journal._locked and
                    (journal.load()['pending'] or {}).get('operation') == 'clear'):
                fired.append(call[1])
                self.guards[-1].deadline = time.monotonic() - 1
            return original(call)
        self.http.responder = respond
        self.reset()
        self.error(frame('apply', request), 'deadline', worker.STOPPED['deadline'])
        self.http.responder = original
        self.assertTrue(fired)
        self.assertEqual(self.guards[-1].tripped, 'deadline')
        self.assertEqual(self.writes(), [])
        self.assertIsNone(self.stored()['pending'])
        self.assertEqual(self.stored()['run']['request'], MEMBERS)
        self.assertEqual(self.ok('apply', request), {'state': 'members-set', 'writes': 2})

    def test_cancel_after_post_retains_pending_until_exact_readback(self):
        preview = self.ok('preview', MEMBERS)
        request = dict(preview['request'], key='logical-1', intent_digest=preview['intent_digest'])
        original, fired = self.backend.respond, []

        def respond(call):
            response = original(call)
            journal = self.backend.journal
            if (not fired and call[0] == 'POST' and journal._locked and
                    (journal.load()['pending'] or {}).get('operation') == 'clear'):
                fired.append(call[1])
                self.client.shutdown(socket.SHUT_RDWR)
            return response
        self.http.responder = respond
        self.reset()
        try:
            raw, served = self.call(encode(frame('apply', request)))
        finally:
            self.http.responder = original
        self.assertTrue(fired)
        self.assertEqual(raw, b'')
        self.assertEqual(served['error']['code'], 'deadline')
        self.assertEqual(self.writes(), ['clear'])
        stored = self.stored()
        self.assertEqual((stored['pending']['operation'], stored['pending']['list']), ('clear', 'a'))
        self.assertEqual(stored['run'], {'key': expected_key('logical-1'), 'request': MEMBERS})
        self.assertEqual(self.ok('reconcile'), {'state': 'observed-applied', 'operation': 'clear',
                                                'list': 'a'})
        shown = self.ok('show')
        self.assertEqual(shown['reconcile'], {'state': 'observed-applied'})
        self.assertEqual(shown['pending'], {'operation': 'clear', 'list': 'a'})
        self.assertNotIn('key', shown['run'])
        # Show and reconcile never recover by writing.
        self.assertEqual(self.writes(), ['clear'])
        self.assertEqual(self.ok('apply', request), {'state': 'members-set', 'writes': 1})
        self.assertEqual(self.writes(), ['clear', 'add'])

    def test_lost_post_response_is_refused_and_never_resent(self):
        preview = self.ok('preview', MEMBERS)
        request = dict(preview['request'], key='logical-1', intent_digest=preview['intent_digest'])
        self.backend.lose = 'clear'
        self.reset()
        error = self.error(frame('apply', request), 'refused', 'Operator HTTP failed; no retry')
        self.assertEqual(set(error), {'code', 'message'})
        self.backend.lose = None
        self.assertEqual(self.stored()['pending']['operation'], 'clear')
        self.assertEqual(self.ok('apply', request), {'state': 'members-set', 'writes': 1})
        self.assertEqual(self.writes(), ['clear', 'add'])

    def test_lock_contention_is_busy_with_zero_mutation(self):
        before = self.journal_bytes()
        holder = Journal(str(self.directory))
        self.addCleanup(holder.close)
        with holder.locked():
            for value in (frame('show'), frame('reconcile'), frame('preview', MEMBERS),
                          frame('apply', dict(MEMBERS, key='logical-busy', intent_digest=DIGEST))):
                with self.subTest(operation=value['operation']):
                    self.reset()
                    self.error(value, 'busy', 'Recipient journal busy; nothing was changed')
                    self.assertEqual(self.mutation_posts(), [])
        self.assertEqual(self.journal_bytes(), before)

    def test_show_capacity_warning_retirement_and_replay(self):
        preview = self.ok('preview', MEMBERS)
        request = dict(preview['request'], key='logical-1', intent_digest=preview['intent_digest'])
        self.ok('apply', request)

        def fill(count):
            holder = Journal(str(self.directory))
            try:
                with holder.locked():
                    d = holder.load()
                    start = len(d['done'])
                    # Synthetic completed history fixture.
                    d['done'].update({'history-%d' % n: dict(MEMBERS, movies=['m1'])
                                      for n in range(start, start + count)})
                    holder.save(d)
            finally:
                holder.close()
        fill(DONE_LIMIT - worker.CAPACITY_WARNING_FREE - 3)
        shown = self.ok('show')
        self.assertEqual((shown['done_count'], shown['keys_exhausted'], shown['capacity_warning']),
                         (DONE_LIMIT - worker.CAPACITY_WARNING_FREE - 1, False, False))
        fill(1)
        shown = self.ok('show')
        self.assertEqual((shown['keys_exhausted'], shown['capacity_warning']), (False, True))
        fill(DONE_LIMIT - shown['done_count'])
        shown = self.ok('show')
        self.assertEqual((shown['done_count'], shown['done_limit'], shown['keys_exhausted'],
                          shown['capacity_warning'], shown['archived_count'],
                          shown['completed_count']),
                         (DONE_LIMIT, DONE_LIMIT, True, True, 0, DONE_LIMIT))
        self.assertEqual(self.ok('apply', request), {'state': 'replayed', 'writes': 0})
        # The next new request first retires every active key into the archive.
        preview = self.ok('preview', dict(MEMBERS, movies=['m3']))
        self.reset()
        self.assertEqual(self.ok('apply', dict(preview['request'], key='logical-new',
                                               intent_digest=preview['intent_digest'])),
                         {'state': 'members-set', 'writes': 2})
        self.assertEqual(self.writes(), ['clear', 'add'])
        shown = self.ok('show')
        self.assertEqual((shown['done_count'], shown['done_limit'], shown['keys_exhausted'],
                          shown['capacity_warning'], shown['archived_count'],
                          shown['completed_count']),
                         (1, DONE_LIMIT, False, False, DONE_LIMIT, DONE_LIMIT + 1))
        before = self.journal_bytes()
        self.reset()
        self.assertEqual(self.ok('apply', request), {'state': 'replayed', 'writes': 0})
        self.error(frame('apply', dict(request, movies=['m1'])), 'refused', 'Idempotency key reused')
        self.assertEqual((self.mutation_posts(), self.journal_bytes()), ([], before))

    def test_uninitialized_or_version_1_journal_is_not_managed(self):
        self.directory = self.scratch / 'empty'
        self.directory.mkdir(mode=0o700)
        self.directory.chmod(0o700)
        self.config['journal_directory'] = str(self.directory)
        self.reset()
        self.error(frame('show'), 'refused', 'Journal migration required')
        self.error(frame('preview', MEMBERS), 'refused', 'Unknown list')
        self.assertEqual(self.mutation_posts(), [])
        self.assertFalse((self.directory / 'journal.json').exists())

    def test_cli_show_output_is_unchanged_while_worker_strips_the_key(self):
        self.unfinished_by_cancel()
        before = self.journal_bytes()
        entry = base.EntryTests().entry()
        with patch.object(entry, 'load_config', return_value=copy.deepcopy(self.config)), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(entry.main(['--config', '/synthetic/config', 'show']), 0)
        cli = json.loads(output.getvalue())
        self.assertEqual(cli['run'], {'key': expected_key('logical-1')})
        self.assertNotIn('capacity_warning', cli)
        shown = self.ok('show')
        self.assertEqual(shown['run'], {'operation': 'set-members', 'list': 'a',
                                        'args': {'movies': ['m2']}})
        self.assertEqual({k: v for k, v in shown.items() if k not in ('run', 'capacity_warning')},
                         {k: v for k, v in cli.items() if k != 'run'})
        self.assertEqual(self.journal_bytes(), before)


class AdapterGuardTests(unittest.TestCase):
    def test_guard_runs_before_every_send_and_caps_socket_timeout(self):
        with base.RecordingHTTP() as http:
            http.responses += [base.auth_response()] + base.identity_responses()
            timeouts, seen = [], []
            original = http.open

            def opened(request, timeout):
                timeouts.append(timeout)
                return original(request, timeout)
            http.open = opened

            def guard():
                seen.append(len(http.calls))
                return 0.75
            api = adapter.OperatorAdapter(base.config(), request_guard=guard)
            api._opener = http
            api.authenticate({'username': 'synthetic-admin', 'password': PASSWORD})
            self.assertEqual(seen, list(range(6)))
            self.assertEqual(len(timeouts), 6)
            self.assertTrue(all(0 < t <= 0.75 for t in timeouts))

    def test_guard_stop_before_mutation_post_is_not_dispatched(self):
        @adapter.mutation_dispatch
        def post(api):
            api._post('/Playlists', query={'Name': 'synthetic', 'userId': base.A})

        def stop():
            raise Refused('Caller cancelled')
        with base.RecordingHTTP() as http:
            api = adapter.OperatorAdapter(base.config(), request_guard=stop)
            api._opener, api._token = http, 'synthetic-session-token'
            with self.assertRaises(adapter.NotDispatched):
                post(api)
            self.assertEqual((api._requests, api._mutation_attempts, http.calls), (0, 0, []))
            # A sent POST with a failed response stays uncertain, never NotDispatched.
            api._guard = lambda: 5
            http.responses.append((500, b'synthetic failure'))
            with self.assertRaises(Refused) as caught:
                post(api)
            self.assertNotIsInstance(caught.exception, adapter.NotDispatched)
            self.assertEqual((api._requests, api._mutation_attempts, len(http.calls)), (1, 1, 1))

    def test_response_wait_never_exceeds_the_caller_deadline_budget(self):
        with base.RecordingHTTP() as http:
            def slow(call):
                time.sleep(1.5)
                return base.auth_response()
            http.responder = slow
            api = adapter.OperatorAdapter(base.config(), request_guard=lambda: 0.3)
            api._opener = http
            started = time.monotonic()
            with self.assertRaisesRegex(Refused, 'Operator HTTP failed; no retry'):
                api.authenticate({'username': 'synthetic-admin', 'password': PASSWORD})
            self.assertLess(time.monotonic() - started, 1.2)

    def test_default_adapter_has_no_guard(self):
        api = adapter.OperatorAdapter(base.config(), None)
        self.assertIsNone(api._guard)


class ActivationTests(unittest.TestCase):
    def test_no_arguments_and_fixed_policy_path(self):
        self.assertEqual(worker.POLICY_PATH, '/etc/holonet-playlists/policy.json')
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(worker.main(['--policy', '/synthetic/policy.json']), 2)
        self.assertIn('No arguments accepted', errors.getvalue())
        with patch.object(worker.os, 'geteuid', return_value=1000), \
             patch.object(worker, 'private_read') as read:
            with self.assertRaisesRegex(Refused, 'Root worker required'):
                worker.load_policy()
            read.assert_not_called()

    def test_activation_requires_exact_systemd_handoff(self):
        pair = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        for s in pair:
            self.addCleanup(s.close)
        for env in ({'LISTEN_PID': str(os.getpid() + 1), 'LISTEN_FDS': '1'},
                    {'LISTEN_PID': str(os.getpid()), 'LISTEN_FDS': '2'}, {}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=False):
                for name in {'LISTEN_PID', 'LISTEN_FDS'} - set(env):
                    os.environ.pop(name, None)
                with self.assertRaisesRegex(Refused, 'Socket activation required'):
                    worker.activated_socket(pair[0].fileno())
        fd = os.dup(pair[0].fileno())
        with patch.dict(os.environ, {'LISTEN_PID': str(os.getpid()), 'LISTEN_FDS': '1',
                                     'LISTEN_FDNAMES': 'connection'}):
            conn = worker.activated_socket(fd)
            self.addCleanup(conn.close)
            self.assertFalse({'LISTEN_PID', 'LISTEN_FDS', 'LISTEN_FDNAMES'} & set(os.environ))
        self.assertEqual((conn.family, conn.type), (socket.AF_UNIX, socket.SOCK_STREAM))

    def test_policy_is_exact_and_bounded(self):
        good = copy.deepcopy(FIXTURES['policy'])
        self.assertEqual(worker.validate_policy(good), good)
        bad = []
        for change in ({'extra': 1}, {'version': True}, {'enabled': 'yes'},
                       {'peer': {'uid': True, 'gid': 940}}, {'peer': {'uid': -1, 'gid': 940}},
                       {'peer': {'uid': 940}}, {'recipients': {}},
                       {'recipients': {'%032x' % n: '/synthetic/%d/config.json' % n
                                       for n in range(worker.MAX_RECIPIENTS + 1)}},
                       {'recipients': {base.R: 'synthetic/config.json'}},
                       {'recipients': {base.R: '/synthetic/../config.json'}},
                       {'recipients': {base.R: '/config.json'}},
                       {'recipients': {base.R: '/synthetic/config.json',
                                       'd' * 32: '/synthetic/config.json'}},
                       {'recipients': {base.R.upper(): '/synthetic/config.json'}}):
            bad.append(dict(copy.deepcopy(good), **change))
        for field, value in (('server', 'not-hex'), ('catalog_user', 'B' * 32), ('library', '../x'),
                             ('issuer', ''), ('subject', 'x\n')):
            value_policy = copy.deepcopy(good)
            value_policy['caller'][field] = value
            bad.append(value_policy)
        missing = copy.deepcopy(good)
        del missing['caller']['namespace']
        bad.append(missing)
        for value in bad:
            with self.subTest(value=value), self.assertRaisesRegex(Refused, 'Invalid management policy'):
                worker.validate_policy(value)


META = FIXTURES['movie_metadata']
E = 'e' * 32
OTHER_CONFIG = '/synthetic/other/config.json'
# Server-side synthetic items. Only Movies of library 7 visible to the recipient
# may ever be returned; every other title must never reach a response.
ITEMS = {
    '8001': dict(META['result_example']['movies'][0], LibraryId=None,
                 People=[{'Id': '71', 'Name': 'Synthetic Director', 'Type': 'Director',
                          'Role': 'private-role', 'PrimaryImageTag': 'private-image'},
                         {'Id': '73', 'Name': 'private-actor', 'Type': 'Actor'},
                         {'Id': '74', 'Name': 'private-music', 'Type': 'MusicDirector'},
                         {'Id': '72', 'Name': 'Synthetic Producer', 'Type': 'Producer'},
                         {'Id': '71', 'Name': 'Synthetic Director', 'Type': 'Director'}],
                 UserData={'Played': True, 'PlayCount': 3}, Path='/private/movie.mkv',
                 MediaSources=[{'Path': '/private/source'}], Overview='private-overview'),
    '9': {'Id': '9', 'Type': 'Movie', 'Name': 'Synthetic Movie Without Metadata',
          'ProductionYear': None},
    '7': {'Id': '7', 'Type': 'Movie', 'Name': 'private-hidden-title', 'hidden': True},
    '55': {'Id': '55', 'Type': 'Episode', 'Name': 'private-episode-title'},
    '66': {'Id': '66', 'Type': 'Movie', 'Name': 'private-off-library-title', 'library': '8'},
}


def history_policy(history=None, recipients=None, **change):
    value = copy.deepcopy(FIXTURES['policy'])
    value['peer'] = {'uid': os.getuid(), 'gid': os.getgid()}
    if recipients is not None:
        value['recipients'] = recipients
    value['history_users'] = list(META['policy_history_users'] if history is None else history)
    value.update(change)
    return worker.validate_policy(value)


def metadata_frame(ids=('8001', '7', '9'), *, caller=None, delete=(), **top):
    value = frame(META['operation'], {'ids': list(ids)}, **top)
    value['caller']['actions'] = list(META['caller_actions'])
    value['caller']['history_users'] = list(META['caller_history_users'])
    value['caller'].update(caller or {})
    for name in delete:
        del value['caller'][name]
    return value


class MovieMetadataTests(WorkerCase):
    """Read-only movie-metadata over the real socket, guard and adapter."""
    def setUp(self):
        super().setUp()
        self.policy = history_policy()
        self.metadata_queries = []
        self.items = copy.deepcopy(ITEMS)
        original = self.backend.respond

        def respond(call):
            from urllib.parse import urlsplit, parse_qs
            u = urlsplit(call[1])
            q = parse_qs(u.query)
            if call[0] == 'GET' and q.get('Fields') == [adapter.METADATA_FIELDS]:
                self.metadata_queries.append((u.path, q))
                ids = q['Ids'][0].split(',')
                # Emby applies the type, library and per-user visibility filters.
                rows = [{k: v for k, v in self.items[i].items() if k not in ('hidden', 'library')}
                        for i in reversed(ids) if i in self.items and
                        self.items[i]['Type'] == q['IncludeItemTypes'][0] and
                        self.items[i].get('library', '7') == q['ParentId'][0] and
                        not self.items[i].get('hidden')]
                for row in rows:
                    row.pop('LibraryId', None)
                return {'Items': rows, 'TotalRecordCount': len(rows)}
            return original(call)
        self.http.responder = respond

    def tree(self):
        """Every journal custody file and its bytes, including the replay archive."""
        return {str(p.relative_to(self.directory)): p.read_bytes()
                for p in sorted(self.directory.rglob('*')) if p.is_file()}

    def test_frozen_metadata_requests_parse_or_refuse_with_exact_codes(self):
        example = META['frame_example'].encode()
        call = worker.parse_request(example[:-1])
        worker.authorize(call, self.policy)
        self.assertEqual(json.dumps(call, separators=(',', ':')).encode() + b'\n', example)
        self.assertEqual(META['caller_actions'], worker.HISTORY_ACTIONS)
        for case in META['valid']:
            with self.subTest(case=case['name']):
                call = worker.parse_request(encode(metadata_frame(case['request']['ids']))[:-1])
                worker.authorize(call, self.policy)
                self.assertEqual(call['request'], case['request'])
        for case in META['invalid'] + META['denied']:
            value = metadata_frame(delete=case.get('caller_delete', ()))
            value.update(copy.deepcopy(case.get('set', {})))
            value['caller'].update(copy.deepcopy(case.get('caller_set', {})))
            with self.subTest(case=case['name']), self.assertRaises(worker.WireError) as caught:
                worker.authorize(worker.parse_request(encode(value)[:-1]), self.policy)
            self.assertEqual((caught.exception.code, caught.exception.message),
                             (case['code'], case['message']))
        self.assertEqual(META['credit_roles'], list(adapter.CREDIT_ROLES))
        bounds = META['bounds']
        self.assertEqual((bounds['raw_credits'], bounds['director_composer_credits'],
                          bounds['producer_credits'], bounds['projected_credits'],
                          bounds['genres'], bounds['credit_or_genre_name_bytes'],
                          bounds['title_bytes']),
                         (adapter.MAX_RAW_CREDITS, adapter.MAX_CREDITS, adapter.MAX_PRODUCERS,
                          adapter.MAX_CREDITS + adapter.MAX_PRODUCERS, adapter.MAX_GENRES,
                          adapter.MAX_NAME_BYTES, adapter.MAX_TITLE_BYTES))

    def test_visible_subset_in_request_order_with_only_allowlisted_metadata(self):
        before = self.tree()
        raw, served = self.call(encode(metadata_frame(['9', '55', '66', '7', '8001'])))
        self.assertTrue(served['ok'], served)
        expected = copy.deepcopy(META['result_example'])
        expected['movies'].reverse()
        self.assertEqual(served['result'], expected)
        for private in (b'private', b'UserData', b'Path', b'MediaSources', b'Overview', b'Actor'):
            self.assertNotIn(private, raw)
        self.assertEqual(self.metadata_queries, [('/emby/Users/' + base.R + '/Items', {
            'Recursive': ['true'], 'IncludeItemTypes': ['Movie'], 'ParentId': ['7'],
            'Ids': ['9,55,66,7,8001'], 'Fields': ['People,Genres'],
            'StartIndex': ['0'], 'Limit': ['26'], 'EnableUserData': ['false']})])
        # Read only: authentication is the only POST, nothing is written or journaled.
        self.assertEqual([c[0] for c in self.backend_calls()], ['POST'] + ['GET'] * 11)
        self.assertEqual((self.mutation_posts(), self.writes()), ([], []))
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.config_reads, self.credential_reads), ([CONFIG_PATH], 1))
        self.assertIsInstance(self.guards[-1], worker.Guard)
        self.assertIsNone(self.apis[-1]._token)
        result = self.ok(META['operation'], {'ids': ['7', '55']}, caller={
            'actions': list(META['caller_actions']), 'history_users': [base.R]})
        self.assertEqual(result, {'movies': []})

    def test_management_caller_and_operations_unchanged_under_history_policy(self):
        self.assertEqual(self.ok('show')['version'], 2)
        self.assertEqual(self.ok('preview', MEMBERS)['request'], MEMBERS)
        self.reset()
        self.error(frame(META['operation'], {'ids': ['8001']}), 'refused', 'Invalid caller')
        self.assert_untouched()
        self.policy = policy()
        self.assertEqual(self.ok('show')['version'], 2)

    def test_policy_caller_and_expiry_refused_before_config_credential_or_backend(self):
        now = int(time.time())
        two = {base.R: CONFIG_PATH, E: OTHER_CONFIG}
        cases = [
            (policy(), metadata_frame(), 'refused', 'History read not enabled by policy'),
            (history_policy([E], two), metadata_frame(caller={'history_users': [base.R, E]}),
             'refused', 'Recipient outside history policy'),
            (history_policy([base.R, E], two), metadata_frame(caller={'history_users': [E]}),
             'refused', 'Recipient outside history policy'),
            (history_policy(), metadata_frame(caller={'history_users': [base.R, E]}),
             'refused', 'History users outside management policy'),
            (history_policy(), metadata_frame(caller={'actions': list(worker.BASE_ACTIONS)}),
             'refused', 'History read needs exactly the fixed actions'),
            (history_policy(), metadata_frame(caller={'expires_at': now - 1}),
             'deadline', 'Deadline or token expiry already reached'),
            (history_policy(), metadata_frame(deadline_unix_ms=now * 1000 - 1),
             'deadline', 'Deadline or token expiry already reached'),
            (history_policy(enabled=False), metadata_frame(), 'unavailable', 'Management disabled'),
            (history_policy(), metadata_frame(['8001', '8001']), 'refused', 'Duplicate Movie IDs'),
            (history_policy(), metadata_frame([]), 'refused', 'Expected 1..25 Movies'),
        ]
        for current, value, code, message in cases:
            with self.subTest(message=message):
                self.policy = current
                self.reset()
                self.error(value, code, message)
                self.assert_untouched()
        self.policy = history_policy()
        self.config['enabled'] = False
        self.reset()
        self.error(metadata_frame(), 'unavailable', 'Recipient management disabled')
        self.assertEqual((self.backend_calls(), self.config_reads, self.credential_reads),
                         ([], [CONFIG_PATH], 0))

    def test_held_journal_lock_is_busy_before_credential_or_backend(self):
        journal = Journal(str(self.directory))
        self.addCleanup(journal.close)
        with journal.locked():
            self.error(metadata_frame(), 'busy', 'Recipient journal busy; nothing was changed')
        self.assertEqual((self.backend_calls(), self.credential_reads), ([], 0))

    def test_cancellation_and_deadline_stop_before_each_later_send(self):
        original = self.http.responder
        for stop, after in (('cancelled', 1), ('cancelled', 11), ('deadline', 1),
                            ('deadline', 6), ('deadline', 11)):
            with self.subTest(stop=stop, after=after):
                self.reset()
                offset = len(self.http.calls)

                def respond(call, stop=stop, after=after, offset=offset):
                    if len(self.http.calls) - offset == after:
                        if stop == 'cancelled':
                            self.client.shutdown(socket.SHUT_RDWR)
                        else:
                            self.guards[-1].deadline = time.monotonic() - 1
                    return original(call)
                self.http.responder = respond
                try:
                    raw, served = self.call(encode(metadata_frame()))
                finally:
                    self.http.responder = original
                self.assertEqual(served['error'], {'code': 'deadline', 'message': worker.STOPPED[stop]})
                self.assertEqual(len(self.backend_calls()), after)
                if stop == 'cancelled':
                    self.assertEqual(raw, b'')
                self.assertEqual(self.metadata_queries, [])

    def test_request_time_and_response_bounds(self):
        with patch.object(adapter, 'MAX_REQUESTS', 11):
            self.error(metadata_frame(), 'refused', 'Transport budget exhausted')
        self.assertEqual((len(self.backend_calls()), self.metadata_queries), (11, []))
        self.reset()
        with patch.object(adapter, 'MAX_SECONDS', 0):
            self.error(metadata_frame(), 'refused', 'Transport budget exhausted')
        self.assertEqual(self.backend_calls(), [])
        self.reset()
        with patch.object(worker, 'MAX_RESPONSE', 200):
            self.error(metadata_frame(), 'unavailable', 'Response too large')
        self.assertEqual(len(self.metadata_queries), 1)
        # A full page of 25 maximal Movies still fits the unchanged response cap.
        self.reset()
        self.items = {str(n): {'Id': str(n), 'Type': 'Movie', 'Name': 'n' * 512,
                               'ProductionYear': 2020, 'Genres': ['g' * 128] * 16,
                               'People': [{'Id': str(1000 + c), 'Name': 'p' * 128,
                                           'Type': ('Director', 'Producer')[c % 2]}
                                          for c in range(32)]}
                      for n in range(1, 26)}
        result = self.ok(META['operation'], {'ids': list(self.items)}, caller={
            'actions': list(META['caller_actions']), 'history_users': [base.R]})
        self.assertEqual([m['Id'] for m in result['movies']], list(self.items))
        self.assertTrue(all(len(m['People']) == 32 for m in result['movies']))

    def test_malformed_backend_metadata_returns_no_facts(self):
        self.items['8001']['Genres'] = ['g'] * 17
        self.error(metadata_frame(), 'refused', 'Invalid Movie metadata')
        self.items['8001']['Genres'] = ['Drama']
        self.items['8001']['People'] = [{'Id': str(100 + n), 'Name': 'D', 'Type': 'Director'}
                                        for n in range(17)]
        self.reset()
        self.error(metadata_frame(), 'refused', 'Invalid Movie metadata')
        self.items['8001']['People'] = [{'Id': str(100 + n), 'Name': 'P', 'Type': 'Producer'}
                                        for n in range(40)]
        self.reset()
        movies = self.ok(META['operation'], {'ids': ['8001']}, caller={
            'actions': list(META['caller_actions']), 'history_users': [base.R]})['movies']
        self.assertEqual(len(movies[0]['People']), 16)

    def test_history_policy_is_optional_exact_and_bounded(self):
        good = copy.deepcopy(FIXTURES['policy'])
        self.assertNotIn('history_users', worker.validate_policy(good))
        good['history_users'] = [base.R]
        self.assertEqual(worker.validate_policy(good), good)
        for value in ([], [E], [base.R, base.R], [base.R.upper()], base.R, None,
                      ['%032x' % n for n in range(17)]):
            with self.subTest(value=value), self.assertRaisesRegex(Refused, 'Invalid management policy'):
                worker.validate_policy(dict(copy.deepcopy(good), history_users=value))


if __name__ == '__main__':
    unittest.main()
