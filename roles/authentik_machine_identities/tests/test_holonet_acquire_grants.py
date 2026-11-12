"""Separate acquisition-purpose claims. No enrollment, credentials or live calls."""
import copy
import types
import unittest
import test_service_profiles as profiles
import test_holonet_writer_grants as writers

mi = profiles.mi


class AcquireGrantTests(unittest.TestCase):
    def mapped(self, actions=None):
        item = writers.seed_join_writer() | {
            'holonet_libraries': ['3', '615'],
            'holonet_acquire': {'library': '3', 'actions':
                list(mi.HOLONET_ACQUIRE_ACTIONS) if actions is None else actions}}
        spec = profiles.spec([item], {'holonet': writers.app() |
            {'scope': ' '.join(mi.HOLONET_ACQUIRE_APP_SCOPES)}})
        access, current = spec['services']['holonet'], spec['identities'][0]
        mappings = [{'scope_name': k, 'expression': v}
                    for k, v in access['scope_expressions'].items()]
        user = types.SimpleNamespace(username=current['username'], type='service_account',
                                     attributes=copy.deepcopy(current['grant_attributes']))
        return access, current, mappings, user

    def test_three_purposes_are_flat_and_disjoint(self):
        access, current, mappings, user = self.mapped()
        for extra, actions in (('', ['janitor.acquire.read']),
                ('holonet.acquire.start', ['janitor.acquire.read', 'janitor.acquire.start']),
                ('holonet.acquire.media', ['janitor.acquire.read', 'janitor.acquire.media'])):
            requested = list(mi.HOLONET_ACQUIRE_SCOPES) + ([extra] if extra else [])
            expected = dict(aud=access['resource'],
                holonet_server=current['grant']['holonet_server'], holonet_libraries=['3'],
                holonet_catalog_users=[], holonet_history_users=[], holonet_target_users=[],
                holonet_actions=actions, holonet_namespace='managed-janitor')
            for scopes in (requested, list(reversed(requested))):
                self.assertEqual(profiles.merged_claims(mappings, user, access['group'], scopes), expected)
        for requested in (list(mi.HOLONET_ACQUIRE_SCOPES) +
                ['holonet.acquire.start', 'holonet.acquire.media'],
                list(mi.HOLONET_ACQUIRE_SCOPES) + ['holonet.torrents'],
                list(mi.HOLONET_ACQUIRE_SCOPES) + ['holonet.acquire'],
                ['holonet.acquire'], [], ['holonet.read', 'holonet.acquire.start']):
            self.assertEqual(profiles.merged_claims(mappings, user, access['group'], requested), {})

    def test_unselected_purpose_cannot_be_requested(self):
        access, _, mappings, user = self.mapped(['janitor.acquire.read'])
        for extra in ('holonet.acquire.start', 'holonet.acquire.media'):
            self.assertEqual(profiles.merged_claims(mappings, user, access['group'],
                list(mi.HOLONET_ACQUIRE_SCOPES) + [extra]), {})

    def test_old_grants_do_not_leak_nested_acquisition(self):
        access, _, mappings, user = self.mapped()
        for requested in (mi.HOLONET_WRITER_SCOPES, mi.HOLONET_JANITOR_SCOPES,
                          mi.HOLONET_TORRENT_SCOPES, mi.HOLONET_SEED_JOIN_SCOPES):
            claims = profiles.merged_claims(mappings, user, access['group'], list(requested))
            self.assertTrue(claims)
            self.assertNotIn('holonet_acquire', claims)
            self.assertNotIn('janitor.acquire.start', claims['holonet_actions'])

    def test_identity_and_account_lifecycle_preserved(self):
        access, current, mappings, user = self.mapped()
        old_item = writers.seed_join_writer() | {'holonet_libraries': ['3', '615']}
        before = profiles.spec([old_item], {'holonet': writers.app() |
            {'scope': ' '.join(mi.HOLONET_SEED_JOIN_APP_SCOPES)}})['identities'][0]
        for field in ('username', 'token', 'secret_path', 'generation', 'expires', 'profile', 'host'):
            self.assertEqual(before[field], current[field])
        user.attributes['chaldea_holonet']['holonet_acquire']['actions'].append('object.delete')
        self.assertEqual(profiles.merged_claims(mappings, user, access['group'],
            list(mi.HOLONET_ACQUIRE_SCOPES)), {})


if __name__ == '__main__':
    unittest.main()
