"""Regression test for the full interactive OV mode's rollback.

After the full declaration has been applied, the default narrow run is refused
before any write, and the full mode with both full accounts absent removes only
those accounts while the original three keep their bytes and read-only claims.
Fake Authentik and scratch destinations only, as in test_ov_http_identities.
"""
import types
import unittest

import test_ov_http_identities as ovt
import test_service_profiles as profiles


class RollbackTest(ovt.EntrypointTest):
    # Reuse the fixture only; test_ov_http_identities runs the inherited cases.
    test_laptop_addition_preserves_controller_custody_and_converges = None
    test_full_interactive_mode_adds_only_the_two_root_accounts = None

    def test_full_accounts_absent_preserves_original_cohort(self):
        x = self.x
        code, output = x.run([ovt.CONTROLLER], services={"ov": self.ov},
                             extra_vars={"authentik_machine_identities_blueprint_name": ovt.BLUEPRINT})
        self.assertEqual(code, 0, output)
        self.ok(self.baseline())
        before = self.controller_snapshot()

        def narrow_files():
            return [(self.secret("laptop", i).read_bytes(), self.secret("laptop", i).stat().st_ino,
                     self.secret("laptop", i).stat().st_mtime_ns) for i in (ovt.LAPTOP, ovt.INTERACTIVE)]

        narrow = narrow_files()
        full = {"ov_http_identities_full_interactive": True,
                "authentik_machine_services": {"ov": self.ov | {"scope": ovt.FULL_SCOPE}},
                "authentik_machine_identity_destinations": self.bindings | {"omarchy-pc": dict(self.bindings["laptop"])}}
        self.ok(self.baseline(), **full)
        full_users = ["chaldea-laptop-ov-full-interactive", "chaldea-omarchy-pc-ov-full-interactive"]
        self.assertTrue(all(u in x.state.users for u in full_users))
        mappings_before = {n: dict(m) for n, m in x.state.mappings.items()}

        # The default narrow run now fails closed before any write or key read.
        code, output = self.play(self.baseline())
        self.assertNotEqual(code, 0, output)
        self.assertEqual((x.writes(), x.key_reads()), ([], []))
        self.assertIn("declare it absent before dropping it", output)
        self.assertEqual({n: dict(m) for n, m in x.state.mappings.items()}, mappings_before)
        self.assertTrue(all(u in x.state.users for u in full_users))

        # Rollback: full mode, both full accounts absent.
        absent = ovt.FULL_COHORT[:3] + [ovt.FULL_LAPTOP | {"state": "absent"}, ovt.FULL_PC | {"state": "absent"}]
        rollback = full | {"authentik_machine_identities": absent}
        self.ok(self.baseline(), **rollback)
        for name in full_users:
            self.assertNotIn(name, x.state.users)
            self.assertNotIn(name + "-g1", x.state.tokens)
        for host in ("laptop", "omarchy-pc"):
            self.assertFalse(self.secret(host, ovt.FULL_LAPTOP).exists())
        old_users = ["chaldea-controller-" + ovt.CONTROLLER["profile"], "chaldea-laptop-" + ovt.LAPTOP["profile"],
                     "chaldea-laptop-" + ovt.INTERACTIVE["profile"]]
        self.assertEqual(sorted(x.state.groups[ovt.OV["group"]]["users"]), sorted(old_users))
        self.assertEqual(self.controller_snapshot(), before)
        self.assertEqual(narrow_files(), narrow)
        # The owned extra mappings stay; the original accounts keep exact read-only claims.
        mappings = [x.state.mappings[ovt.OV["name"] + ": " + s] for s in ovt.FULL_SCOPE.split()]
        for requested in (["ov.read"], ovt.FULL_SCOPE.split()):
            for name in old_users:
                account = types.SimpleNamespace(**x.state.users[name])
                self.assertEqual(profiles.merged_claims(mappings, account, ovt.OV["group"], requested),
                                 {"ov_namespaces": [ovt.NODE], "aud": ovt.OV["resource"]})
        # Identical rollback repeat changes nothing; the narrow run stays refused.
        self.assertEqual(x.changed(self.ok(self.baseline(), **rollback)), 0)
        code, output = self.play(self.baseline())
        self.assertNotEqual(code, 0, output)
        self.assertEqual((x.writes(), x.key_reads()), ([], []))
        self.assertEqual(self.controller_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
