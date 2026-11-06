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


def historian():
    """The writer with the explicit scoped playback history opt-in."""
    return writer() | {"holonet_actions": ACTIONS + ["object.delete", "history.read"],
                       "holonet_history_users": ["c" * 32]}


def app():
    return profiles.access("holonet") | {"scope": " ".join(SCOPES)}



class JanitorGrantTest(unittest.TestCase):
    def test_same_account_exact_janitor_projection_and_writer_preservation(self):
        original = historian() | {"holonet_libraries": ["3"]}
        janitor = original | {"holonet_janitor": {"library": "3", "actions": list(mi.HOLONET_JANITOR_ACTIONS)}}
        application = app() | {"scope": " ".join(mi.HOLONET_EXTENDED_SCOPES)}
        before = profiles.spec([original], {"holonet": app()})
        after = profiles.spec([janitor], {"holonet": application})
        old, current = before["identities"][0], after["identities"][0]
        for key in ("username", "token", "secret_path", "generation", "expires", "profile", "host"):
            self.assertEqual(old[key], current[key])
        self.assertEqual(len(before["identities"]), len(after["identities"]))
        access = after["services"]["holonet"]
        mappings = [{"scope_name": k, "expression": v} for k, v in access["scope_expressions"].items()]
        user = types.SimpleNamespace(username=current["username"], type="service_account",
                                     attributes=copy.deepcopy(current["grant_attributes"]))
        projected = {"aud": access["resource"], "holonet_server": current["grant"]["holonet_server"],
            "holonet_libraries": ["3"], "holonet_catalog_users": [], "holonet_history_users": [],
            "holonet_target_users": [], "holonet_actions": list(mi.HOLONET_JANITOR_ACTIONS),
            "holonet_namespace": "managed-janitor"}
        scopes = mi.HOLONET_EXTENDED_SCOPES
        requests = [list(p) for size in range(1, 5)
                    for subset in combinations(scopes, size) for p in permutations(subset)]
        requests += [[], ["holonet.read"] * 2, [*mi.HOLONET_JANITOR_SCOPES, "ha.read"]]
        for requested in requests:
            expected = (projected if len(requested) == 2 and set(requested) == set(mi.HOLONET_JANITOR_SCOPES)
                        else {"aud": access["resource"]} | old["grant"]
                        if len(requested) == 3 and set(requested) == set(SCOPES) else {})
            with self.subTest(requested=requested):
                claims = profiles.merged_claims(mappings, user, access["group"], requested)
                self.assertEqual(claims, expected)
                self.assertNotIn("holonet_janitor", claims)
        for key, value in (("username", "foreign"), ("type", "internal")):
            changed = copy.deepcopy(user)
            setattr(changed, key, value)
            self.assertEqual(profiles.merged_claims(mappings, changed, access["group"],
                                                   mi.HOLONET_JANITOR_SCOPES), {})
        changed = copy.deepcopy(user)
        changed.attributes["chaldea_holonet"]["holonet_janitor"]["actions"].append("janitor.importers.pause")
        self.assertEqual(profiles.merged_claims(mappings, changed, access["group"], mi.HOLONET_JANITOR_SCOPES), {})
        self.assertEqual(profiles.merged_claims(mappings, user, "foreign", mi.HOLONET_JANITOR_SCOPES), {})

    def test_enrollment_cannot_create_rotate_or_renew_an_account(self):
        from unittest.mock import patch
        item = writer() | {"holonet_libraries": ["3"],
                          "holonet_janitor": {"library": "3", "actions": list(mi.HOLONET_JANITOR_ACTIONS)}}
        s = profiles.spec([item], {"holonet": app() | {"scope": " ".join(mi.HOLONET_EXTENDED_SCOPES)}})
        part = next(mi._service_specs(s))
        account = part["identities"][0]
        user, token = ("user", account["username"]), ("token", account["token"])
        owned = [[*user, "present"], [*token, "present"]]
        found = {user: {"pk": 1}, token: {"expires": account["expires"]},
                 ("keypair", part["access"]["signing_key"]): {"private_key_available": True, "key_type": "rsa"}}
        def check(seen, owners=owned):
            with patch.object(mi, "_found", return_value=seen), patch.object(mi, "_binding_problems", return_value=[]):
                return mi._service_refusals(part, owners, [], None)
        self.assertEqual(check(found), [])
        for missing in (user, token):
            changed = copy.deepcopy(found)
            changed.pop(missing)
            self.assertIn("janitor enrollment requires an existing owned writer account and token", check(changed))
        self.assertIn("janitor enrollment requires an existing owned writer account and token", check(found, owned[:1]))
        changed = copy.deepcopy(found)
        changed[token]["expires"] = "2027-01-01T00:00:00Z"
        self.assertIn("janitor enrollment must preserve the existing token expiry", check(changed))
        part["identities"][0]["state"] = "absent"
        self.assertIn("janitor enrollment requires an existing owned writer account and token", check(found))

    def test_scope_alone_never_enrolls_an_account_and_nested_grant_is_closed(self):
        application = app() | {"scope": " ".join(mi.HOLONET_EXTENDED_SCOPES)}
        items = [profiles.identity("holonet"), writer()]
        spec = profiles.spec(items, {"holonet": application})
        access = spec["services"]["holonet"]
        mappings = [{"scope_name": k, "expression": v} for k, v in access["scope_expressions"].items()]
        for item in spec["identities"]:
            user = types.SimpleNamespace(username=item["username"], type="service_account",
                                         attributes=copy.deepcopy(item["grant_attributes"]))
            self.assertEqual(profiles.merged_claims(mappings, user, access["group"], mi.HOLONET_JANITOR_SCOPES), {})
        good = {"library": "3", "actions": list(mi.HOLONET_JANITOR_ACTIONS)}
        for bad in (None, {}, {"library": "4", "actions": good["actions"]},
                    good | {"namespace": "managed-janitor"}, good | {"history_users": []},
                    good | {"actions": ["janitor.importers.read"]},
                    good | {"actions": good["actions"] + ["janitor.importers.pause"]},
                    good | {"actions": good["actions"] + ["catalog.read"]},
                    good | {"actions": good["actions"][::-1]}):
            with self.subTest(bad=bad), self.assertRaises(mi.AnsibleFilterError):
                profiles.spec([writer() | {"holonet_libraries": ["3"], "holonet_janitor": bad}], {"holonet": application})
        with self.assertRaises(mi.AnsibleFilterError):
            profiles.spec([profiles.identity("holonet") | {"holonet_janitor": good}], {"holonet": application})
        with self.assertRaises(mi.AnsibleFilterError):
            profiles.spec([writer() | {"holonet_libraries": ["3"], "holonet_janitor": good}], {"holonet": app()})


class TorrentGrantTest(unittest.TestCase):
    def test_both_nested_grants_keep_old_and_new_projections_independent(self):
        item = historian() | {"holonet_libraries": ["3"],
            "holonet_janitor": {"library": "3", "actions": list(mi.HOLONET_JANITOR_ACTIONS)},
            "holonet_torrents": {"library": "3", "actions": list(mi.HOLONET_TORRENT_ACTIONS)}}
        s = profiles.spec([item], {"holonet": app() | {"scope": " ".join(mi.HOLONET_TORRENT_APP_SCOPES)}})
        access, current = s["services"]["holonet"], s["identities"][0]
        mappings = [{"scope_name": k, "expression": v} for k, v in access["scope_expressions"].items()]
        user = types.SimpleNamespace(username=current["username"], type="service_account",
                                     attributes=copy.deepcopy(current["grant_attributes"]))
        for scopes, actions in ((mi.HOLONET_JANITOR_SCOPES, mi.HOLONET_JANITOR_ACTIONS),
                                (mi.HOLONET_TORRENT_SCOPES, mi.HOLONET_TORRENT_ACTIONS)):
            claims = profiles.merged_claims(mappings, user, access["group"], scopes)
            self.assertEqual(claims["holonet_actions"], list(actions))
            self.assertEqual(claims["holonet_libraries"], ["3"])
            self.assertEqual(claims["holonet_namespace"], "managed-janitor")
            for key in ("holonet_catalog_users", "holonet_history_users", "holonet_target_users"):
                self.assertEqual(claims[key], [])
            self.assertNotIn("holonet_janitor", claims)
            self.assertNotIn("holonet_torrents", claims)
        claims = profiles.merged_claims(mappings, user, access["group"], SCOPES)
        self.assertNotIn("holonet_janitor", claims)
        self.assertNotIn("holonet_torrents", claims)

    def test_same_account_exact_janitor_projection_and_writer_preservation(self):
        original = historian() | {"holonet_libraries": ["3"]}
        janitor = original | {"holonet_torrents": {"library": "3", "actions": list(mi.HOLONET_TORRENT_ACTIONS)}}
        application = app() | {"scope": " ".join(mi.HOLONET_TORRENT_APP_SCOPES)}
        before = profiles.spec([original], {"holonet": app()})
        after = profiles.spec([janitor], {"holonet": application})
        old, current = before["identities"][0], after["identities"][0]
        for key in ("username", "token", "secret_path", "generation", "expires", "profile", "host"):
            self.assertEqual(old[key], current[key])
        self.assertEqual(len(before["identities"]), len(after["identities"]))
        access = after["services"]["holonet"]
        mappings = [{"scope_name": k, "expression": v} for k, v in access["scope_expressions"].items()]
        user = types.SimpleNamespace(username=current["username"], type="service_account",
                                     attributes=copy.deepcopy(current["grant_attributes"]))
        projected = {"aud": access["resource"], "holonet_server": current["grant"]["holonet_server"],
            "holonet_libraries": ["3"], "holonet_catalog_users": [], "holonet_history_users": [],
            "holonet_target_users": [], "holonet_actions": list(mi.HOLONET_TORRENT_ACTIONS),
            "holonet_namespace": "managed-janitor"}
        scopes = mi.HOLONET_TORRENT_APP_SCOPES
        requests = [list(p) for size in range(1, 6)
                    for subset in combinations(scopes, size) for p in permutations(subset)]
        requests += [[], ["holonet.read"] * 2, [*mi.HOLONET_TORRENT_SCOPES, "ha.read"]]
        for requested in requests:
            expected = (projected if len(requested) == 3 and set(requested) == set(mi.HOLONET_TORRENT_SCOPES)
                        else {"aud": access["resource"]} | old["grant"]
                        if len(requested) == 3 and set(requested) == set(SCOPES) else {})
            with self.subTest(requested=requested):
                claims = profiles.merged_claims(mappings, user, access["group"], requested)
                self.assertEqual(claims, expected)
                self.assertNotIn("holonet_torrents", claims)
        for key, value in (("username", "foreign"), ("type", "internal")):
            changed = copy.deepcopy(user)
            setattr(changed, key, value)
            self.assertEqual(profiles.merged_claims(mappings, changed, access["group"],
                                                   mi.HOLONET_TORRENT_SCOPES), {})
        changed = copy.deepcopy(user)
        changed.attributes["chaldea_holonet"]["holonet_torrents"]["actions"].append("janitor.importers.pause")
        self.assertEqual(profiles.merged_claims(mappings, changed, access["group"], mi.HOLONET_TORRENT_SCOPES), {})
        self.assertEqual(profiles.merged_claims(mappings, user, "foreign", mi.HOLONET_TORRENT_SCOPES), {})

    def test_enrollment_cannot_create_rotate_or_renew_an_account(self):
        from unittest.mock import patch
        item = writer() | {"holonet_libraries": ["3"],
                          "holonet_torrents": {"library": "3", "actions": list(mi.HOLONET_TORRENT_ACTIONS)}}
        s = profiles.spec([item], {"holonet": app() | {"scope": " ".join(mi.HOLONET_TORRENT_APP_SCOPES)}})
        part = next(mi._service_specs(s))
        account = part["identities"][0]
        user, token = ("user", account["username"]), ("token", account["token"])
        owned = [[*user, "present"], [*token, "present"]]
        found = {user: {"pk": 1}, token: {"expires": account["expires"]},
                 ("keypair", part["access"]["signing_key"]): {"private_key_available": True, "key_type": "rsa"}}
        def check(seen, owners=owned):
            with patch.object(mi, "_found", return_value=seen), patch.object(mi, "_binding_problems", return_value=[]):
                return mi._service_refusals(part, owners, [], None)
        self.assertEqual(check(found), [])
        for missing in (user, token):
            changed = copy.deepcopy(found)
            changed.pop(missing)
            self.assertIn("janitor enrollment requires an existing owned writer account and token", check(changed))
        self.assertIn("janitor enrollment requires an existing owned writer account and token", check(found, owned[:1]))
        changed = copy.deepcopy(found)
        changed[token]["expires"] = "2027-01-01T00:00:00Z"
        self.assertIn("janitor enrollment must preserve the existing token expiry", check(changed))
        part["identities"][0]["state"] = "absent"
        self.assertIn("janitor enrollment requires an existing owned writer account and token", check(found))

    def test_scope_alone_never_enrolls_an_account_and_nested_grant_is_closed(self):
        application = app() | {"scope": " ".join(mi.HOLONET_TORRENT_APP_SCOPES)}
        items = [profiles.identity("holonet"), writer()]
        spec = profiles.spec(items, {"holonet": application})
        access = spec["services"]["holonet"]
        mappings = [{"scope_name": k, "expression": v} for k, v in access["scope_expressions"].items()]
        for item in spec["identities"]:
            user = types.SimpleNamespace(username=item["username"], type="service_account",
                                         attributes=copy.deepcopy(item["grant_attributes"]))
            self.assertEqual(profiles.merged_claims(mappings, user, access["group"], mi.HOLONET_TORRENT_SCOPES), {})
        good = {"library": "3", "actions": list(mi.HOLONET_TORRENT_ACTIONS)}
        for bad in (None, {}, {"library": "4", "actions": good["actions"]},
                    good | {"namespace": "managed-janitor"}, good | {"history_users": []},
                    good | {"actions": ["janitor.importers.read"]},
                    good | {"actions": good["actions"] + ["janitor.importers.pause"]},
                    good | {"actions": good["actions"] + ["catalog.read"]},
                    good | {"actions": list(mi.HOLONET_JANITOR_ACTIONS)}):
            with self.subTest(bad=bad), self.assertRaises(mi.AnsibleFilterError):
                profiles.spec([writer() | {"holonet_libraries": ["3"], "holonet_torrents": bad}], {"holonet": application})
        with self.assertRaises(mi.AnsibleFilterError):
            profiles.spec([profiles.identity("holonet") | {"holonet_torrents": good}], {"holonet": application})
        with self.assertRaises(mi.AnsibleFilterError):
            profiles.spec([writer() | {"holonet_libraries": ["3"], "holonet_torrents": good}], {"holonet": app()})


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

    def test_scoped_history_only_for_writer_targets_with_history_read(self):
        history = ACTIONS + ["object.delete", "history.read"]
        for actions in (ACTIONS + ["history.read"], history):
            accepted = profiles.spec([historian() | {"holonet_actions": actions}], {"holonet": app()})
            self.assertEqual(accepted["identities"][0]["grant"]["holonet_history_users"], ["c" * 32])
        both = profiles.spec([historian() | {"holonet_history_users": ["d" * 32, "c" * 32]}],
                             {"holonet": app()})
        self.assertEqual(both["identities"][0]["grant"]["holonet_history_users"], ["d" * 32, "c" * 32])
        for field, bad in (
                ("holonet_history_users", []), ("holonet_history_users", ["e" * 32]),
                ("holonet_history_users", ["c" * 32] * 2), ("holonet_history_users", ["C" * 32]),
                ("holonet_history_users", "c" * 32), ("holonet_history_users", ["*"]),
                ("holonet_history_users", [format(i, "032x") for i in range(17)]),
                ("holonet_actions", ["history.read", *ACTIONS, "object.delete"]),
                ("holonet_actions", ACTIONS + ["history.read", "object.delete"]),
                ("holonet_actions", history + ["history.read"]),
                ("holonet_actions", history + ["userdata.read"]),
                ("holonet_actions", ["catalog.read", "history.read"])):
            with self.subTest(field=field, bad=bad), self.assertRaises(mi.AnsibleFilterError):
                profiles.spec([historian() | {field: bad}], {"holonet": app()})
        # Readers never carry history, history.read or userdata.read.
        reader = profiles.identity("holonet")
        for field, bad in (("holonet_history_users", ["c" * 32]),
                           ("holonet_actions", ["catalog.read", "history.read"]),
                           ("holonet_actions", ["catalog.read", "userdata.read"])):
            for application in (profiles.access("holonet"), app()):
                with self.subTest(field=field, bad=bad), self.assertRaises(mi.AnsibleFilterError):
                    profiles.spec([reader | {field: bad}], {"holonet": application})

    def test_history_grant_changes_only_the_writer_and_keeps_scopes(self):
        reader = profiles.identity("holonet")
        before = profiles.spec([reader, writer() | {"holonet_actions": ACTIONS + ["object.delete"]}],
                               {"holonet": app()})
        after = profiles.spec([reader, historian()], {"holonet": app()})
        self.assertEqual(after["identities"][0], before["identities"][0])
        self.assertEqual((after["objects"], after["marker"]), (before["objects"], before["marker"]))
        old, new = before["services"]["holonet"], after["services"]["holonet"]
        self.assertEqual((new["scope"], new["scope_mappings"]), (old["scope"], old["scope_mappings"]))
        changed = after["identities"][1]["grant"]
        self.assertEqual(changed, before["identities"][1]["grant"] | {
            "holonet_history_users": ["c" * 32], "holonet_actions": ACTIONS + ["object.delete", "history.read"]})
        # Only the embedded expected-account literal differs in each mapping.
        for scope, expression in new["scope_expressions"].items():
            a, b = old["scope_expressions"][scope].splitlines(), expression.splitlines()
            self.assertEqual([n for n, (x, y) in enumerate(zip(a, b)) if x != y], [1])
            self.assertEqual(len(a), len(b))
        mappings = [{"scope_name": scope, "expression": expression}
                    for scope, expression in new["scope_expressions"].items()]
        for item in after["identities"]:
            user = types.SimpleNamespace(username=item["username"], type="service_account",
                                         attributes=copy.deepcopy(item["grant_attributes"]))
            requested = ["holonet.read"] if item["grant"]["holonet_actions"] == ["catalog.read"] else list(SCOPES)
            self.assertEqual(profiles.merged_claims(mappings, user, new["group"], requested),
                             {"aud": new["resource"]} | item["grant"])
            # A widened stored history attribute never gains claims.
            widened = copy.deepcopy(user)
            widened.attributes["chaldea_holonet"]["holonet_history_users"] = ["c" * 32, "d" * 32]
            self.assertEqual(profiles.merged_claims(mappings, widened, new["group"], requested), {})


if __name__ == "__main__":
    unittest.main()
