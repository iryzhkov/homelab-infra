"""Movie-only positive protection contract checks. Synthetic isolated Chaldea only."""
import copy
import json
import time
import unittest
from pathlib import Path

import emby_operator_adapter as adapter
import emby_operator_socket as worker
from emby_operator_recommendations import Refused
import test_emby_operator_adapter as base
import test_emby_operator_socket as socket_tests

FIXTURE = json.loads((Path(__file__).resolve().parent.parent /
                     'docs/emby-janitor-protections-wire-v1.json').read_text())


class JanitorPolicyTests(unittest.TestCase):
    def policy(self):
        value = copy.deepcopy(socket_tests.FIXTURES['policy'])
        value['history_users'] = [base.R]
        value['janitor'] = copy.deepcopy(FIXTURE['policy'])
        return value

    def test_shared_wire_and_old_callers(self):
        policy = worker.validate_policy(self.policy())
        call = worker.parse_request(socket_tests.encode(FIXTURE['frame'])[:-1])
        worker.authorize(call, policy)
        self.assertEqual(call, FIXTURE['frame'])
        # Coexistence changes subject membership only for admitted read purposes.
        coexist = copy.deepcopy(policy)
        coexist['subjects'] = [coexist['caller']['subject'], 'chaldea-laptop']
        coexist = worker.validate_policy(coexist)
        changed = copy.deepcopy(call)
        changed['caller']['subject'] = 'chaldea-laptop'
        with self.assertRaises(worker.WireError):
            worker.authorize(changed, coexist)

        # The optional block changes none of the established caller grammars.
        worker.authorize(worker.parse_request(socket_tests.encode(socket_tests.frame())[:-1]), policy)
        for mutate in (
            lambda p: p.pop('janitor'),
            lambda p: p['janitor'].update(revision='f' * 40),
        ):
            p = self.policy()
            mutate(p)
            with self.assertRaises(worker.WireError):
                worker.authorize(call, worker.validate_policy(p))
        for mutate in (
            lambda c: c['caller'].update(namespace='managed-recommendations'),
            lambda c: c['caller'].update(catalog_user=base.A),
            lambda c: c['caller'].update(actions=worker.JANITOR_ACTIONS + ['catalog.read']),
            lambda c: c['caller'].update(target_library='8'),
            lambda c: c['caller'].update(history_users=['e' * 32]),
            lambda c: c.update(operation='apply'),
            lambda c: c['request'].update(target='episode'),
            lambda c: c['request'].update(kind='Movie'),
        ):
            value = copy.deepcopy(FIXTURE['frame'])
            mutate(value)
            with self.assertRaises(worker.WireError):
                worker.authorize(worker.parse_request(socket_tests.encode(value)[:-1]), policy)

    def test_malformed_policy_rejected_before_deploy(self):
        for change in (
            {'protected_users': []}, {'protected_users': [base.R, 'e' * 32]},
            {'target_library': '8'}, {'target_library': 'episode'},
            {'revision': 'bad'}, {'enabled': True},
        ):
            value = self.policy()
            value['janitor'].update(change)
            with self.subTest(change=change), self.assertRaises(Refused):
                worker.validate_policy(value)
        value = self.policy()
        value.pop('history_users')
        with self.assertRaises(Refused):
            worker.validate_policy(value)


class JanitorAdapterTests(unittest.TestCase):
    authenticated = base.MovieMetadataTests.authenticated
    def test_positive_only_and_private_query(self):
        cases = [
            ({'IsFavorite': True}, 'liked'), ({'Likes': True}, 'liked'),
            ({'Likes': False, 'IsFavorite': False}, 'unknown'),
            ({'Likes': 1, 'IsFavorite': 'true'}, 'unknown'),
            ({'Played': True, 'PlayCount': 7}, 'unknown'),
            ({}, 'unknown'), (None, 'unknown'), ([{'Likes': True}], 'unknown'),
        ]
        for data, expected in cases:
            with self.subTest(data=data), base.RecordingHTTP() as http:
                api = self.authenticated(http)
                http.responses += base.identity_responses() + [{
                    'Items': [{'Id': '8001', 'Type': 'Movie', 'UserData': data,
                               'Name': 'private', 'DateCreated': '1900-01-01'}],
                    'TotalRecordCount': 1}]
                self.assertEqual(api.janitor_protections(base.R, '7', '8001'),
                                 [{'id': '8001', 'kind': 'Movie', 'protection': expected}])
                from urllib.parse import urlsplit, parse_qs
                method, path, _, _ = http.calls[-1]
                self.assertEqual(method, 'GET')
                self.assertEqual(urlsplit(path).path, '/emby/Users/' + base.R + '/Items')
                self.assertEqual(parse_qs(urlsplit(path).query), {
                    'Recursive': ['true'], 'IncludeItemTypes': ['Movie'], 'ParentId': ['7'],
                    'Ids': ['8001'], 'StartIndex': ['0'], 'Limit': ['2'], 'EnableUserData': ['true']})

    def test_hidden_wrong_type_duplicate_truncated_and_scope(self):
        for rows, total in [
            ([], 0),
            ([{'Id': '8001', 'Type': 'Episode'}], 1),
            ([{'Id': '8002', 'Type': 'Movie'}], 1),
            ([{'Id': '8001', 'Type': 'Movie', 'LibraryId': '8'}], 1),
            ([{'Id': '8001', 'Type': 'Movie', 'UserData': {'ItemId': '8', 'Likes': True}}], 1),
            ([{'Id': '8001', 'Type': 'Movie'}] * 2, 2), ([], 1),
        ]:
            with self.subTest(rows=rows), base.RecordingHTTP() as http:
                api = self.authenticated(http)
                http.responses += base.identity_responses() + [{'Items': rows, 'TotalRecordCount': total}]
                if not rows and not total:
                    self.assertEqual(api.janitor_protections(base.R, '7', '8001'), [])
                else:
                    with self.assertRaises(Refused):
                        api.janitor_protections(base.R, '7', '8001')


class JanitorSocketTests(socket_tests.WorkerCase):
    def setUp(self):
        super().setUp()
        self.policy['history_users'] = [base.R]
        self.policy['janitor'] = copy.deepcopy(FIXTURE['policy'])
        self.policy = worker.validate_policy(self.policy)
        original = self.backend.respond
        def respond(call):
            if 'EnableUserData=true' in call[1]:
                return {'Items': [{'Id': '8001', 'Type': 'Movie',
                                  'UserData': {'Likes': True}, 'Name': 'private'}],
                        'TotalRecordCount': 1}
            return original(call)
        self.http.responder = respond

    def request(self):
        value = copy.deepcopy(FIXTURE['frame'])
        value['caller']['expires_at'] = int(time.time()) + 300
        value['deadline_unix_ms'] = int(time.time() * 1000) + 60000
        return value

    def tree(self):
        return {str(p.relative_to(self.directory)): p.read_bytes()
                for p in self.directory.rglob('*') if p.is_file()}

    def test_real_socket_read_without_journal_or_media_mutation(self):
        before = self.tree()
        raw, result = self.call(socket_tests.encode(self.request()))
        self.assertEqual(result, FIXTURE['response'])
        self.assertNotIn(b'private', raw)
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.mutation_posts(), self.writes()), ([], []))

    def test_opt_out_or_wrong_namespace_before_config_or_credentials(self):
        for missing in (True, False):
            value = self.request()
            if missing:
                self.policy.pop('janitor')
            else:
                self.policy['janitor'] = copy.deepcopy(FIXTURE['policy'])
                value['caller']['namespace'] = 'managed-recommendations'
            _, result = self.call(socket_tests.encode(value))
            self.assertFalse(result['ok'])
            self.assertEqual(result['error']['code'], 'refused')
            self.assertEqual((self.config_reads, self.credential_reads, self.backend_calls()), ([], 0, []))


if __name__ == '__main__':
    unittest.main()
