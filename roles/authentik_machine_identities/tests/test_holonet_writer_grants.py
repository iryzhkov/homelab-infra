"""Synthetic closed writer grants; no identity declaration or live enrollment."""
import copy
from itertools import combinations, permutations
import types
import unittest

import test_service_profiles as profiles

mi = profiles.mi
SCOPES = ("holonet.read", "holonet.curate", "holonet.home")
ACTIONS = ["catalog.read", "playlist.read", "playlist.manage",
           "object.share", "home.read", "home.manage"]


def writer():
    return profiles.identity("holonet") | {
        "profile": "holonet-writer", "holonet_target_users": ["c" * 32, "d" * 32],
        "holonet_actions": ACTIONS, "holonet_namespace": "managed-recommendations"}


def app():
    return profiles.access("holonet") | {"scope": " ".join(SCOPES)}


class WriterGrantTest(unittest.TestCase):
    def test_composed_mappings_emit_once_and_preserve_reader(self):
        reader = profiles.identity("holonet")
        s = profiles.spec([reader, writer()], {"holonet": app()})
        access = s["services"]["holonet"]
        mappings = [{"scope_name": scope, "expression": expression}
                    for scope, expression in access["scope_expressions"].items()]
        for item in s["identities"]:
            user = types.SimpleNamespace(username=item["username"], type="service_account",
                                         attributes=copy.deepcopy(item["grant_attributes"]))
            expected = {"aud": access["resource"]} | item["grant"]
            requests = [list(order) for size in range(1, 4)
                        for selected in combinations(SCOPES, size) for order in permutations(selected)]
            requests += [[], ["holonet.read", "holonet.read"], [*SCOPES, "ha.read"]]
            for requested in requests:
                allowed = (requested == ["holonet.read"] if item["grant"]["holonet_actions"] == ["catalog.read"]
                           else len(requested) == 3 and set(requested) == set(SCOPES))
                with self.subTest(profile=item["profile"], requested=requested):
                    self.assertEqual(profiles.merged_claims(mappings, user, access["group"], requested),
                                     expected if allowed else {})
            # A syntactically valid changed account attribute does not acquire
            # authority; the complete operator declaration is still matched.
            for key, value in (("holonet_target_users", ["e" * 32]),
                               ("holonet_actions", ACTIONS + ["object.delete"]),
                               ("holonet_namespace", "different")):
                changed = copy.deepcopy(user)
                changed.attributes["chaldea_holonet"][key] = value
                self.assertEqual(profiles.merged_claims(mappings, changed, access["group"], SCOPES), {})

    def test_writer_requires_explicit_application_and_complete_tuple(self):
        with self.assertRaises(mi.AnsibleFilterError):
            profiles.spec([writer()], {"holonet": profiles.access("holonet")})
        for field, bad in (
                ("holonet_history_users", ["e" * 32]),
                ("holonet_target_users", []), ("holonet_target_users", ["*"]),
                ("holonet_target_users", ["C" * 32]), ("holonet_target_users", ["c" * 32] * 2),
                ("holonet_target_users", [format(i, "032x") for i in range(17)]),
                ("holonet_actions", ACTIONS[:-1]), ("holonet_actions", ACTIONS + ["media.delete"]),
                ("holonet_actions", ACTIONS + ["object.delete", "object.delete"])):
            with self.subTest(field=field, bad=bad), self.assertRaises(mi.AnsibleFilterError):
                profiles.spec([writer() | {field: bad}], {"holonet": app()})
        allowed = profiles.spec([writer() | {"holonet_actions": ACTIONS + ["object.delete"]}],
                                {"holonet": app()})
        self.assertEqual(allowed["identities"][0]["grant"]["holonet_actions"], ACTIONS + ["object.delete"])
        for scope in ("holonet.curate", "holonet.read holonet.home", "holonet.read holonet.curate",
                      "holonet.read holonet.curate holonet.home holonet.admin"):
            with self.subTest(scope=scope), self.assertRaises(mi.AnsibleFilterError):
                profiles.spec([writer()], {"holonet": app() | {"scope": scope}})


if __name__ == "__main__":
    unittest.main()
