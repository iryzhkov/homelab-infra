"""HoloNet library scope projection and gaming-pc second writer token tests.

No live enrollment, credentials or network calls.
"""
import copy
import types
import unittest
from unittest.mock import patch

import test_service_profiles as profiles
import test_holonet_writer_grants as writers

mi = profiles.mi


class LibraryGrantTests(unittest.TestCase):
    def mapped(self, actions=None, extra_tokens=None, hosts=None):
        item = writers.seed_join_writer() | {
            'holonet_libraries': ['3', '615', '1132'],
            'holonet_acquire': {'library': '3', 'actions': list(mi.HOLONET_ACQUIRE_ACTIONS[:1])},
            'holonet_library': {
                'libraries': list(mi.HOLONET_LIBRARY_LIBRARIES),
                'actions': list(mi.HOLONET_LIBRARY_ACTIONS) if actions is None else actions,
            },
        }
        if extra_tokens is not None:
            item['extra_tokens'] = extra_tokens
        inv_hosts = dict(profiles.base.HOSTS)
        if hosts:
            inv_hosts.update(hosts)
        spec = mi.machine_identity_spec(
            [item], profiles.base.ACCESS, inv_hosts, 180,
            profiles.base.NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
            {'holonet': writers.app() | {'scope': ' '.join(mi.HOLONET_LIBRARY_APP_SCOPES)}},
        )
        access, current = spec['services']['holonet'], spec['identities'][0]
        mappings = [{'scope_name': k, 'expression': v}
                    for k, v in access['scope_expressions'].items()]
        user = types.SimpleNamespace(
            username=current['username'],
            type='service_account',
            attributes=copy.deepcopy(current['grant_attributes']),
        )
        return spec, access, current, mappings, user

    def test_library_scope_projects_flat_grant_and_refuses_mixed_scopes(self):
        _, access, current, mappings, user = self.mapped()
        expected = dict(
            aud=access['resource'],
            holonet_server=current['grant']['holonet_server'],
            holonet_libraries=['3', '615', '1132'],
            holonet_catalog_users=[],
            holonet_history_users=[],
            holonet_target_users=[],
            holonet_actions=['library.read', 'library.manage'],
            holonet_namespace='managed-library',
        )
        self.assertEqual(
            profiles.merged_claims(mappings, user, access['group'], ['holonet.library']),
            expected,
        )
        for mixed in (
            ['holonet.read', 'holonet.library'],
            ['holonet.library', 'holonet.curate'],
            ['holonet.library', 'holonet.janitor'],
            ['holonet.library', 'holonet.acquire'],
            ['holonet.library', 'holonet.library'],
            [],
        ):
            self.assertEqual(
                profiles.merged_claims(mappings, user, access['group'], mixed),
                {},
                f'mixed scopes should be refused: {mixed}',
            )

    def test_old_grants_do_not_leak_nested_holonet_library(self):
        _, access, _, mappings, user = self.mapped()
        for requested in (
            mi.HOLONET_WRITER_SCOPES,
            mi.HOLONET_JANITOR_SCOPES,
            mi.HOLONET_TORRENT_SCOPES,
            mi.HOLONET_SEED_JOIN_SCOPES,
            mi.HOLONET_ACQUIRE_SCOPES,
        ):
            claims = profiles.merged_claims(mappings, user, access['group'], list(requested))
            self.assertTrue(claims)
            self.assertNotIn('holonet_library', claims)
            self.assertNotIn('library.manage', claims['holonet_actions'])

    def test_library_grant_requires_all_three_libraries_and_valid_actions(self):
        with self.assertRaises(mi.AnsibleFilterError):
            self.mapped(actions=['library.read', 'object.delete'])
        item = writers.seed_join_writer() | {
            'holonet_libraries': ['3', '615'],
            'holonet_library': {'libraries': ['3', '615', '1132'], 'actions': ['library.read']},
        }
        with self.assertRaises(mi.AnsibleFilterError):
            profiles.spec([item], {'holonet': writers.app() | {
                'scope': ' '.join(mi.HOLONET_LIBRARY_APP_SCOPES)}})

    def test_gaming_pc_second_writer_token_same_account_separate_delivery(self):
        extra = [{
            'host': 'omarchy-pc',
            'generation': 1,
            'expires': writers.seed_join_writer()['expires'],
            'state': 'present',
        }]
        spec, _, current, _, _ = self.mapped(
            extra_tokens=extra,
            hosts={'omarchy-pc': {'ssh_user': 'igor', 'connection': 'ssh'}},
        )
        # Still one service account, two tokens, two deliveries.
        self.assertEqual(len(spec['identities']), 1)
        self.assertEqual(len(spec['deliveries']), 2)
        primary, secondary = spec['deliveries']
        self.assertEqual(primary['username'], secondary['username'])
        self.assertNotEqual(primary['token'], secondary['token'])
        self.assertEqual(secondary['host'], 'omarchy-pc')
        self.assertEqual(secondary['token'], f"{current['username']}-omarchy-pc-g1")
        self.assertEqual(
            secondary['secret_path'],
            f"/home/igor/.config/chaldea/authentik/{current['profile']}.app-password",
        )
        self.assertIn(['token', secondary['token'], 'present'], spec['objects'])

        # Binding problems accept the extra token owned by the same writer account.
        part = next(mi._service_specs(spec))
        found = {
            ('token', primary['token']): {'user_obj': {'username': primary['username']}, 'intent': 'app_password'},
            ('token', secondary['token']): {'user_obj': {'username': primary['username']}, 'intent': 'app_password'},
        }
        owned = {('token', primary['token']), ('token', secondary['token'])}
        self.assertEqual(mi._binding_problems(part, found, owned), [])

        # Wrong owner on the extra token is refused.
        bad_found = copy.deepcopy(found)
        bad_found[('token', secondary['token'])]['user_obj']['username'] = 'other-user'
        self.assertTrue(mi._binding_problems(part, bad_found, owned))

        # Colliding host with primary is refused.
        with self.assertRaises(mi.AnsibleFilterError):
            self.mapped(
                extra_tokens=[dict(extra[0], host=current['host'])],
                hosts={'omarchy-pc': {'ssh_user': 'igor', 'connection': 'ssh'}},
            )

    def test_template_extra_token_references_outer_writer_user_key(self):
        import json as _json
        from pathlib import Path
        import jinja2

        catalog_item = dict(
            service='holonet',
            host='homelab',
            profile='holonet-catalog',
            generation=1,
            expires=writers.seed_join_writer()['expires'],
            state='present',
            holonet_server=writers.seed_join_writer()['holonet_server'],
            holonet_libraries=['3'],
            holonet_catalog_users=writers.seed_join_writer()['holonet_catalog_users'],
            holonet_history_users=[],
            holonet_target_users=[],
            holonet_actions=['catalog.read'],
            holonet_namespace='movies-catalog',
        )
        writer_item = writers.seed_join_writer() | {
            'holonet_libraries': ['3', '615', '1132'],
            'holonet_acquire': {'library': '3', 'actions': list(mi.HOLONET_ACQUIRE_ACTIONS[:1])},
            'holonet_library': {
                'libraries': list(mi.HOLONET_LIBRARY_LIBRARIES),
                'actions': list(mi.HOLONET_LIBRARY_ACTIONS),
            },
            'extra_tokens': [{
                'host': 'omarchy-pc',
                'generation': 1,
                'expires': writers.seed_join_writer()['expires'],
                'state': 'present',
            }],
        }
        inv_hosts = dict(profiles.base.HOSTS)
        inv_hosts['homelab'] = {'ssh_user': 'igor', 'connection': 'ssh'}
        inv_hosts['omarchy-pc'] = {'ssh_user': 'igor', 'connection': 'ssh'}
        spec = mi.machine_identity_spec(
            [catalog_item, writer_item], profiles.base.ACCESS, inv_hosts, 180,
            profiles.base.NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
            {'holonet': writers.app() | {'scope': ' '.join(mi.HOLONET_LIBRARY_APP_SCOPES)}},
        )
        self.assertEqual(len(spec['identities']), 2)
        writer_ident = spec['identities'][1]
        self.assertEqual(len(writer_ident['extra_tokens']), 1)
        extra_token_id = writer_ident['extra_tokens'][0]['token']

        template_path = Path(__file__).resolve().parents[1] / 'templates/machine-identities.yaml.j2'
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters['to_json'] = _json.dumps
        rendered = env.from_string(template_path.read_text()).render(
            ansible_managed='synthetic',
            authentik_machine_identities_blueprint_name='test-blueprint',
            machine_identity_spec=spec,
        )
        # Verify the writer is user-2 and its extra token references !KeyOf user-2 (not user-1).
        self.assertIn(f'    id: user-2\n    identifiers:\n      username: "{writer_ident["username"]}"', rendered)
        marker = f'      identifier: "{extra_token_id}"\n    attrs:\n      user: !KeyOf user-2\n'
        self.assertIn(marker, rendered)


if __name__ == '__main__':
    unittest.main()
