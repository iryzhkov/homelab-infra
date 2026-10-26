"""Closed web MCP profiles. All issuer/host operations use the synthetic real-role fixture."""
import copy
import json
from pathlib import Path
import importlib.util
import time
from unittest import mock
from importlib.metadata import version
from itertools import combinations, permutations
import types
import unittest

from deepmerge import always_merger
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

import test_machine_identities as base

mi = base.mi
fake = base.fake


def access(service, origin="https://auth.example.test"):
    return base.ACCESS | {
        "slug": "chaldea-" + service, "name": "Chaldea " + service + " machine access",
        "group": "chaldea-" + service + "-machine-identities", "client_id": "chaldea-" + service + "-machine",
        "resource": "https://" + ("media" if service == "holonet" else service) + ".mcp.ryzhkov.dev/mcp", "scope": service + ".read",
        "issuer": origin + "/application/o/chaldea-" + service + "/",
        "jwks_url": origin + "/application/o/chaldea-" + service + "/jwks/",
    }


def identity(service):
    item = base.identity(profile=service + "-reader") | {"service": service}
    item.pop("project")
    grants = {"ov": {"ov_namespaces": ["viking://resources/namespaceA"]},
              "homelab": {"homelab_services": ["dns"], "homelab_hosts": ["host-a"]},
              "ha": {"ha_entities": ["sensor.a"], "ha_actions": ["state.read"]}}
    if service == "holonet":
        golden = json.loads((Path(__file__).parent / "holonet-catalog.json").read_text())
        return item | {key: golden[key] for key in mi.HOLONET_FIELDS}
    return item | grants[service]


def spec(identities, services):
    return mi.machine_identity_spec(identities, base.ACCESS, base.HOSTS, 180,
                                    base.NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), services)


def evaluate(expression, user, group, requested=("ha.read",)):
    scope = {"user": user, "token": types.SimpleNamespace(scope=list(requested)),
             "ak_is_group_member": lambda u, name: name == group}
    exec("def mapping():\n" + "\n".join("    " + line for line in expression.splitlines()), scope)
    return scope["mapping"]()


def merged_claims(mappings, user, group, requested):
    # Authentik version/2026.8.4, commit 410f46e5cf18f97a1f0056b30984a145242df55d:
    # providers/oauth2/views/userinfo.py:62-98 selects token.scope mappings,
    # orders by scope_name, evaluates with token, then always_merger.merge.
    # Its pyproject.toml and uv.lock pin deepmerge==2.1.0, whose lists append.
    assert version("deepmerge") == "2.1.0"
    claims = {}
    for mapping in sorted(mappings, key=lambda m: m["scope_name"]):
        if mapping["scope_name"] not in requested:
            continue
        value = evaluate(mapping["expression"], user, group, requested)
        if isinstance(value, dict):
            always_merger.merge(claims, value)
    return claims


class ProfileTest(unittest.TestCase):
    def test_holonet_catalog_contract_and_composed_scope_denials(self):
        item = identity("holonet")
        s = spec([item], {"holonet": access("holonet")})
        app = s["services"]["holonet"]
        user = types.SimpleNamespace(username=s["identities"][0]["username"], type="service_account",
                                     attributes=copy.deepcopy(s["identities"][0]["grant_attributes"]))
        mappings = [{"scope_name": key, "expression": value}
                    for key, value in app["scope_expressions"].items()]
        expected = {"aud": app["resource"]} | s["identities"][0]["grant"]
        self.assertEqual(merged_claims(mappings, user, app["group"], ["holonet.read"]), expected)
        self.assertEqual(set(expected), mi.HOLONET_FIELDS | {"aud"})
        # No combined scope can append/widen claims or turn another application's token into HoloNet.
        scopes = ("holonet.read", "holonet.curate", "holonet.home")
        requests = [list(order) for size in range(1, 4) for selected in combinations(scopes, size)
                    for order in permutations(selected)]
        for requested in requests + [[], ["ha.read"], ["holonet.read", "holonet.read"]]:
            self.assertEqual(merged_claims(mappings, user, app["group"], requested),
                             expected if requested == ["holonet.read"] else {})
        for key, value in (("holonet_server", "c" * 32), ("holonet_libraries", ["456"]),
                           ("holonet_catalog_users", ["c" * 32]), ("holonet_namespace", "other")):
            changed = copy.deepcopy(user)
            changed.attributes["chaldea_holonet"][key] = value
            self.assertEqual(merged_claims(mappings, changed, app["group"], ["holonet.read"]), {})
        for foreign in ("ha", "ov", "homelab"):
            other = spec([identity(foreign)], {foreign: access(foreign)})["services"][foreign]
            self.assertEqual(evaluate(other["expression"], user, other["group"]), {})
        self.assertEqual(evaluate(app["expression"], user, "foreign", ["holonet.read"]), {})
        for key, bad in (("holonet_libraries", ["0"]), ("holonet_libraries", ["01"]),
                         ("holonet_libraries", [str(2**63)]), ("holonet_libraries", ["123", "456"]),
                         ("holonet_catalog_users", []), ("holonet_catalog_users", ["B" * 32]),
                         ("holonet_server", "a" * 31), ("holonet_namespace", "Bad"),
                         ("holonet_history_users", ["b" * 32]), ("holonet_target_users", ["b" * 32]),
                         ("holonet_actions", ["catalog.read", "userdata.read"])):
            with self.subTest(key=key, bad=bad), self.assertRaises(mi.AnsibleFilterError):
                spec([item | {key: bad}], {"holonet": access("holonet")})
        for scope in ("holonet.curate", "holonet.read holonet.home", "holonet.read holonet.read"):
            with self.assertRaises(mi.AnsibleFilterError):
                spec([], {"holonet": access("holonet") | {"scope": scope}})
        for key in mi.HOLONET_FIELDS:
            missing = dict(item)
            del missing[key]
            with self.assertRaises(mi.AnsibleFilterError):
                spec([missing], {"holonet": access("holonet")})

    def test_legacy_shape_names_and_ownership_remain_compatible(self):
        old = base.spec([base.identity()])
        explicit = spec([base.identity() | {"service": "jocasta"}], {"jocasta": base.ACCESS})
        self.assertEqual(old, explicit)
        self.assertEqual(old["services"]["jocasta"]["expression"], mi.expression(base.ACCESS))
        self.assertEqual(old["marker"], explicit["marker"])

    def test_closed_profiles_and_unique_accounts(self):
        profiles = {"jocasta": base.ACCESS, "ov": access("ov"), "homelab": access("homelab"), "ha": access("ha")}
        s = spec([base.identity(), identity("ov"), identity("homelab"), identity("ha")], profiles)
        self.assertEqual(len({i["username"] for i in s["identities"]}), 4)
        self.assertEqual(len({i["secret_path"] for i in s["identities"]}), 4)
        with self.assertRaisesRegex(mi.AnsibleFilterError, "collides|declared twice"):
            spec([identity("ov"), identity("homelab") | {"profile": "ov-reader"}], profiles)
        for bad in ({"other": access("ov")}, {"ov": access("ov") | {"scope": "ov.write"}},
                    {"homelab": access("homelab") | {"token_seconds": True}},
                    {"ov": access("ov") | {"claims": {"admin": True}}},
                    {"ov": access("ov") | {"resource": "https://other.example/mcp"}},
                    {"ov": access("ov") | {"issuer": access("ov")["issuer"].rstrip("/")}},
                    {"ov": access("ov") | {"jwks_url": access("ov")["jwks_url"].rstrip("/")}},
                    {"ov": access("ov"), "homelab": access("homelab") | {"client_id": access("ov")["client_id"]}}):
            with self.subTest(bad=bad), self.assertRaises(mi.AnsibleFilterError):
                spec([], bad)

    def test_grants_match_merged_verifiers_without_expansion(self):
        for roots in ([], ["*"], ["viking://resources/a/"], ["viking://resources/a%2Fb"],
                      ["viking://resources/.."], ["viking://resources/a", "viking://resources/a/b"],
                      ["viking://resources/a"] * 2, ["viking://resources/" + str(n) for n in range(17)],
                      ["viking://resources/" + "a" * 1025], "viking://resources/a"):
            with self.subTest(roots=roots), self.assertRaises(mi.AnsibleFilterError):
                spec([identity("ov") | {"ov_namespaces": roots}], {"ov": access("ov")})
        for key in ("homelab_services", "homelab_hosts"):
            for grants in (None, "*", ["*"], ["a.b"], ["a"] * 2, ["a" * 65],
                           [str(n) for n in range(129)]):
                with self.subTest(key=key, grants=grants), self.assertRaises(mi.AnsibleFilterError):
                    spec([identity("homelab") | {key: grants}], {"homelab": access("homelab")})
            missing = identity("homelab")
            missing.pop(key)
            with self.assertRaises(mi.AnsibleFilterError):
                spec([missing], {"homelab": access("homelab")})
        empty = identity("homelab") | {"homelab_services": [], "homelab_hosts": []}
        self.assertEqual(spec([empty], {"homelab": access("homelab")})["identities"][0]["grant"],
                         {"homelab_services": [], "homelab_hosts": []})

    def test_ha_exact_bounded_entities_actions_and_explicit_scope_ceiling(self):
        for key, invalid in (
                ("ha_entities", ([], None, ["*"], ["light.*"], ["scene.a"], ["area.a"],
                                 ["Light.a"], ["sensor.A"], ["sensor." + "a" * 97],
                                 ["sensor.a"] * 2, ["sensor.e" + str(n) for n in range(65)])),
                ("ha_actions", ([], None, ["*"], ["ha.admin"], ["script.execute"], ["state.read"] * 2))):
            for grants in invalid:
                with self.subTest(key=key, grants=grants), self.assertRaises(mi.AnsibleFilterError):
                    spec([identity("ha") | {key: grants}], {"ha": access("ha")})
            missing = identity("ha")
            missing.pop(key)
            with self.assertRaises(mi.AnsibleFilterError):
                spec([missing], {"ha": access("ha")})
        for scope in ("", "ha.admin", "ha.read ha.read", "ha.read\tha.control", "ha.read other"):
            with self.subTest(scope=scope), self.assertRaises(mi.AnsibleFilterError):
                spec([], {"ha": access("ha") | {"scope": scope}})
        for scope, actions in (("ha.read", ["light.control"]), ("ha.control", ["state.read"])):
            with self.assertRaisesRegex(mi.AnsibleFilterError, "corresponding explicit scope"):
                spec([identity("ha") | {"ha_actions": actions}], {"ha": access("ha") | {"scope": scope}})
        for scope in ("ha.read ha.control", "ha.control ha.read", "ha.control"):
            actions = ["light.control"] + (["state.read"] if "ha.read" in scope else [])
            s = spec([identity("ha") | {"ha_entities": ["light.a"], "ha_actions": actions}],
                     {"ha": access("ha") | {"scope": scope}})
            self.assertEqual(set(s["services"]["ha"]["scope_mappings"]), set(scope.split(" ")))

    def test_new_profiles_require_public_rsa_metadata_but_jocasta_is_unchanged(self):
        for service in ("ov", "homelab", "ha"):
            s = spec([identity(service)], {service: access(service)})
            for metadata in ({}, {"key_type": None}, {"key_type": "ec"}, {"key_type": "ed25519"},
                             {"key_type": "ed448"}, {"key_type": "dsa"}, {"key_type": "RSA"},
                             {"key_type": True}):
                key = {"item": {"kind": "keypair", "id": fake.KEYPAIR}, "status": 200,
                       "json": {"private_key_available": True} | metadata}
                with self.subTest(service=service, metadata=metadata):
                    self.assertTrue(any("must have key_type rsa" in p
                                        for p in mi.machine_identity_refusals(s, [], [key])))
            key["json"]["key_type"] = "rsa"
            self.assertEqual(mi.machine_identity_refusals(s, [], [key]), [])
        legacy = base.spec([base.identity()])
        key["json"].pop("key_type")
        self.assertEqual(mi.machine_identity_refusals(legacy, [], [key]), [])

    def test_ha_composition_preserves_exact_requested_action_ceiling(self):
        # Demonstrate the dependency's append semantics, rather than emulate dedup.
        self.assertEqual(always_merger.merge({"a": [1]}, {"a": [1]}), {"a": [1, 1]})
        grant = {"ha_entities": ["light.a", "switch.a"],
                 "ha_actions": ["state.read", "light.control", "switch.control"]}
        for declared in ("ha.read", "ha.control", "ha.read ha.control", "ha.control ha.read"):
            item = identity("ha") | grant
            item["ha_actions"] = [a for a in grant["ha_actions"]
                                  if ("ha.read" if a == "state.read" else "ha.control") in declared.split()]
            s = spec([item], {"ha": access("ha") | {"scope": declared}})
            app = s["services"]["ha"]
            user = types.SimpleNamespace(username=s["identities"][0]["username"], type="service_account",
                                         attributes=copy.deepcopy(s["identities"][0]["grant_attributes"]))
            mappings = [{"scope_name": scope, "expression": expression}
                        for scope, expression in app["scope_expressions"].items()]
            requests = [(scope,) for scope in declared.split()] + list(permutations(declared.split()))
            for requested in requests:
                with self.subTest(declared=declared, requested=requested):
                    actions = [a for a in item["ha_actions"]
                               if ("ha.read" if a == "state.read" else "ha.control") in requested]
                    expected = {"aud": app["resource"], "ha_entities": grant["ha_entities"], "ha_actions": actions}
                    self.assertEqual(merged_claims(mappings, user, app["group"], requested), expected)
                    self.assertEqual(merged_claims(mappings, user, "foreign", requested), {})
                    changed = copy.deepcopy(user)
                    changed.attributes["chaldea_ha"]["ha_entities"].append("light.foreign")
                    self.assertEqual(merged_claims(mappings, changed, app["group"], requested), {})

    def test_ov_actions_scopes_and_resources_root_are_closed(self):
        full = access("ov") | {"scope": "ov.read ov.write ov.remove ov.maintain"}
        root = identity("ov") | {"ov_namespaces": ["viking://resources"],
                                 "ov_actions": ["read", "write", "remove", "maintain"]}
        s = spec([root], {"ov": full})
        self.assertEqual(s["identities"][0]["grant"], {"ov_namespaces": ["viking://resources"],
                                                       "ov_actions": ["read", "write", "remove", "maintain"]})
        self.assertEqual(s["identities"][0]["grant_attributes"], {"chaldea_ov": s["identities"][0]["grant"]})
        self.assertEqual(list(s["services"]["ov"]["scope_mappings"]),
                         ["ov.read", "ov.write", "ov.remove", "ov.maintain"])
        # One owned mapping per declared scope, all on the one provider.
        self.assertEqual(sorted(o[1] for o in s["objects"] if o[0] == "scopemapping"),
                         sorted(full["name"] + ": " + x for x in full["scope"].split()))
        for scope in ("ov.read", "ov.read ov.write", "ov.read ov.maintain", "ov.read ov.remove ov.maintain"):
            self.assertEqual(list(spec([], {"ov": access("ov") | {"scope": scope}})["services"]["ov"]
                                  ["scope_mappings"]), scope.split())
        # ov.read is required and first; no duplicate, unknown or reordered family.
        for scope in ("ov.write", "ov.write ov.read", "ov.read ov.read", "ov.read ov.admin",
                      "ov.read  ov.write", "ov.read\tov.write", "ov.read ov.maintain ov.write",
                      "ov.read ov.write ov.write", "", "ov.*", "ov.read ha.read"):
            with self.subTest(scope=scope), self.assertRaises(mi.AnsibleFilterError):
                spec([], {"ov": access("ov") | {"scope": scope}})
        # Unknown, duplicate, reordered or read-less actions refuse.
        for actions in ([], None, "read", ["write"], ["read", "read"], ["write", "read"],
                        ["read", "admin"], ["read", "*"], ["read", True], ["READ"],
                        ["read", "maintain", "write"], ["read", "write", "remove", "maintain", "read"]):
            with self.subTest(actions=actions), self.assertRaises(mi.AnsibleFilterError):
                spec([root | {"ov_actions": actions}], {"ov": full})
        # Every signed action needs its own explicitly declared scope family.
        for scope, actions in (("ov.read", ["read", "write"]), ("ov.read ov.write", ["read", "remove"]),
                               ("ov.read ov.write ov.remove", ["read", "write", "remove", "maintain"])):
            with self.subTest(scope=scope), self.assertRaisesRegex(mi.AnsibleFilterError,
                                                                   "corresponding explicit scope"):
                spec([root | {"ov_actions": actions}], {"ov": access("ov") | {"scope": scope}})
        # The resources root is exact and canonical, and nothing may overlap it.
        for roots in (["viking://resources/"], ["viking://resources", "viking://resources/a"],
                      ["viking://resources"] * 2, ["viking://resourcesX"], ["viking://user"],
                      ["viking://user/memories"], ["viking://agent/a"], ["viking://session/a"],
                      ["viking://resources//a"], ["viking://resources/a/../b"]):
            with self.subTest(roots=roots), self.assertRaises(mi.AnsibleFilterError):
                spec([root | {"ov_namespaces": roots}], {"ov": full})

    def test_ov_composition_keeps_old_grants_read_only_and_requested_ceiling(self):
        self.assertEqual(always_merger.merge({"a": [1]}, {"a": [1]}), {"a": [1, 1]})
        node = "viking://resources/homeassistant-api/rest-api.md"
        old = identity("ov") | {"profile": "ov-old", "ov_namespaces": [node]}
        full = identity("ov") | {"profile": "ov-full", "ov_namespaces": ["viking://resources"],
                                 "ov_actions": ["read", "write", "remove", "maintain"]}
        partial = identity("ov") | {"profile": "ov-partial", "ov_namespaces": ["viking://resources/a"],
                                    "ov_actions": ["read", "write"]}
        narrow = spec([old], {"ov": access("ov")})
        wide = spec([old, full, partial], {"ov": access("ov") | {"scope": "ov.read ov.write ov.remove ov.maintain"}})
        app = wide["services"]["ov"]
        mappings = [{"scope_name": scope, "expression": expression}
                    for scope, expression in app["scope_expressions"].items()]
        users = {}
        for item in wide["identities"]:
            users[item["profile"]] = types.SimpleNamespace(
                username=item["username"], type="service_account",
                attributes=copy.deepcopy(item["grant_attributes"]))
        scopes = list(app["scope_mappings"])
        requests = [list(order) for size in range(1, 5) for chosen in combinations(scopes, size)
                    for order in permutations(chosen)]
        for requested in requests:
            valid = "ov.read" in requested
            for profile, user in users.items():
                claims = merged_claims(mappings, user, app["group"], requested)
                with self.subTest(profile=profile, requested=requested):
                    if not valid:
                        self.assertEqual(claims, {})
                    elif profile == "ov-old":
                        # No signed action, whatever the request: the original read-only shape.
                        self.assertEqual(json.dumps(claims), json.dumps(
                            {"ov_namespaces": [node], "aud": app["resource"]}))
                    else:
                        grant = (full if profile == "ov-full" else partial)
                        self.assertEqual(claims, {
                            "aud": app["resource"], "ov_namespaces": grant["ov_namespaces"],
                            "ov_actions": [a for a in grant["ov_actions"] if "ov." + a in requested]})
                    self.assertEqual(merged_claims(mappings, user, "foreign", requested), {})
        for requested in ([], ["ov.read", "ov.read"], ["ov.read", "ov.admin"], ["ov.read", "ha.read"],
                          ["ov.write", "ov.remove", "ov.maintain"], ["ov.write"]):
            for profile, user in users.items():
                with self.subTest(profile=profile, requested=requested):
                    for scope, expression in app["scope_expressions"].items():
                        self.assertEqual(evaluate(expression, user, app["group"], requested), {})
        # Escalating attributes on the same subject never sign anything.
        for profile, change in (("ov-old", {"ov_actions": ["read", "write"]}),
                                ("ov-partial", {"ov_actions": ["read", "write", "remove", "maintain"]}),
                                ("ov-partial", {"ov_namespaces": ["viking://resources"]}),
                                ("ov-full", {"ov_actions": ["read", "write", "remove", "maintain", "admin"]})):
            changed = copy.deepcopy(users[profile])
            changed.attributes["chaldea_ov"].update(change)
            with self.subTest(profile=profile, change=change):
                self.assertEqual(merged_claims(mappings, changed, app["group"], scopes), {})
        # The single-scope application keeps its original mapping text and output.
        expected = narrow["services"]["ov"]["account_grants"]
        legacy = "\n".join([
            "# Rendered by homelab-infra roles/authentik_machine_identities.",
            f"expected = {expected!r}",
            "grant = user.attributes.get('chaldea_ov') if user else None",
            "if (not user or str(user.type) != 'service_account'",
            f"        or not ak_is_group_member(user, name={app['group']!r})",
            "        or user.username not in expected or grant != expected[user.username]):",
            "    return {}",
            f"return dict(grant, aud={app['resource']!r})"])
        self.assertEqual(narrow["services"]["ov"]["scope_expressions"], {"ov.read": legacy})
        self.assertEqual(json.dumps(evaluate(legacy, users["ov-old"], app["group"], ["ov.read"])),
                         json.dumps(merged_claims(mappings, users["ov-old"], app["group"], ["ov.read"])))

    def test_mapping_exact_claims_and_cross_service_and_same_subject_denials(self):
        s = spec([identity("ov"), identity("homelab"), identity("ha")],
                 {"ov": access("ov"), "homelab": access("homelab"), "ha": access("ha")})
        for item in s["identities"]:
            own = s["services"][item["service"]]
            user = types.SimpleNamespace(username=item["username"], type="service_account",
                                         attributes=copy.deepcopy(item["grant_attributes"]))
            self.assertEqual(evaluate(own["expression"], user, own["group"]),
                             {"aud": own["resource"]} | item["grant"])
            self.assertEqual(evaluate(own["expression"], user, "foreign"), {})
            self.assertEqual(evaluate(own["expression"], None, own["group"]), {})
            user.type = "internal"
            self.assertEqual(evaluate(own["expression"], user, own["group"]), {})
            user.type = "service_account"
            other = s["services"]["homelab" if item["service"] == "ov" else "ov"]
            self.assertEqual(evaluate(other["expression"], user, other["group"]), {})
            # The same account/subject cannot reuse a previous grant or widen it.
            user.attributes["chaldea_" + item["service"]] = {"ov_namespaces": ["viking://resources/other"]}
            self.assertEqual(evaluate(own["expression"], user, own["group"]), {})


class HolonetRoleTest(unittest.TestCase):
    def test_catalog_profile_actual_role_custody_noop_and_owned_cleanup(self):
        x = base.Scratch(self)
        origin = f"https://127.0.0.1:{x.server.server_port}"
        app = access("holonet", origin)
        item = identity("holonet")
        code, output = x.run([item], services={"holonet": app})
        self.assertEqual(code, 0, output)
        username = "chaldea-agent-one-" + item["profile"]
        user = types.SimpleNamespace(**x.state.users[username])
        mapping = x.state.mappings[app["name"] + ": holonet.read"]["expression"]
        self.assertEqual(evaluate(mapping, user, app["group"], ["holonet.read"]),
                         {"aud": app["resource"]} | {key: item[key] for key in mi.HOLONET_FIELDS})
        secret = x.secret("agent-one", item["profile"])
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(secret.stat().st_nlink, 1)
        code, output = x.run([item], services={"holonet": app})
        self.assertEqual(code, 0, output)
        self.assertEqual(x.changed(output), 0)
        code, output = x.run([item | {"state": "absent"}],
                             services={"holonet": app | {"state": "absent"}})
        self.assertEqual(code, 0, output)
        self.assertFalse(secret.exists())
        self.assertNotIn(username, x.state.users)
        self.assertEqual(set(x.state.apps), {"budget"})


class MultiServiceRoleTest(unittest.TestCase):
    def setUp(self):
        self.x = base.Scratch(self)
        origin = f"https://127.0.0.1:{self.x.server.server_port}"
        self.services = {"jocasta": base.ACCESS, "ov": access("ov", origin),
                         "homelab": access("homelab", origin), "ha": access("ha", origin)}
        self.items = [base.identity(), identity("ov"), identity("homelab"), identity("ha")]

    def run_role(self, items=None, services=None, ok=True):
        code, output = self.x.run(self.items if items is None else items,
                                  services=self.services if services is None else services)
        self.assertEqual(code == 0, ok, output)
        return output

    def test_legacy_upgrade_coexistence_exact_claims_idempotence_and_full_cleanup(self):
        code, output = self.x.run([base.identity()])
        self.assertEqual(code, 0, output)
        legacy = self.x.secret("agent-one").read_bytes()
        self.run_role()
        self.assertEqual(self.x.secret("agent-one").read_bytes(), legacy)
        for item in self.items:
            service = item.get("service", "jocasta")
            app = self.services[service]
            username = "chaldea-agent-one-" + item["profile"]
            user = self.x.state.users[username]
            self.assertEqual(self.x.state._user_view(user)["groups"],
                             [self.x.state.groups[app["group"]]["pk"]])
            self.assertEqual(self.x.secret("agent-one", item["profile"]).read_text(),
                             self.x.state.tokens[username + "-g1"]["key"])
            mapping = self.x.state.mappings[app["name"] + ": " + app["scope"]]["expression"]
            expected = ({"jocasta_projects": ["projectA"]} if service == "jocasta" else
                        {k: item[k] for k in mi.SERVICES[service]["account_keys"]})
            self.assertEqual(evaluate(mapping, types.SimpleNamespace(**user), app["group"]),
                             {"aud": app["resource"]} | expected)
            for slug, other in self.x.state.apps.items():
                self.assertIs(bool(self.x.state.passing(other, user)), slug == app["slug"])
        output = self.run_role()
        self.assertEqual(self.x.changed(output), 0, output)
        absent = [i | {"state": "absent"} for i in self.items]
        self.run_role(absent, {k: v | {"state": "absent"} for k, v in self.services.items()})
        self.assertEqual(set(self.x.state.apps), {"budget"})
        self.assertFalse(self.x.state.tokens or self.x.state.providers or self.x.state.mappings)
        self.assertEqual(set(self.x.state.users), {"igor"})
        for item in self.items:
            self.assertFalse(self.x.secret("agent-one", item["profile"]).exists())
        self.assertEqual(self.x.changed(self.run_role(
            absent, {k: v | {"state": "absent"} for k, v in self.services.items()})), 0)

    def test_rotation_and_one_service_removal_leave_siblings_unchanged(self):
        self.run_role()
        sibling = self.x.secret("agent-one", "homelab-reader").read_bytes()
        rotated = [i | {"generation": 2} if i.get("service") == "ov" else i for i in self.items]
        self.run_role(rotated)
        ov_user = "chaldea-agent-one-ov-reader"
        self.assertNotIn(ov_user + "-g1", self.x.state.tokens)
        self.assertIn(ov_user + "-g2", self.x.state.tokens)
        removed = [i | {"state": "absent"} if i.get("service") == "ov" else i for i in rotated]
        self.run_role(removed, self.services | {"ov": self.services["ov"] | {"state": "absent"}})
        self.assertNotIn("chaldea-ov", self.x.state.apps)
        self.assertEqual(self.x.secret("agent-one", "homelab-reader").read_bytes(), sibling)
        self.assertFalse(self.x.secret("agent-one", "ov-reader").exists())

    def test_explicit_ha_read_control_has_two_independently_named_scope_mappings(self):
        self.services["ha"]["scope"] = "ha.read ha.control"
        self.items[-1] |= {"ha_entities": ["light.a", "switch.a"],
                           "ha_actions": ["state.read", "light.control", "switch.control"]}
        self.run_role()
        app = self.services["ha"]
        provider = self.x.state.providers[app["name"]]
        mappings = [self.x.state.mappings[app["name"] + ": " + scope] for scope in ("ha.read", "ha.control")]
        self.assertEqual(set(provider["property_mappings"]), {m["pk"] for m in mappings})
        user = types.SimpleNamespace(**self.x.state.users["chaldea-agent-one-ha-reader"])
        expected = {"aud": app["resource"], "ha_entities": self.items[-1]["ha_entities"],
                    "ha_actions": self.items[-1]["ha_actions"]}
        for scope, mapping in zip(("ha.read", "ha.control"), mappings):
            self.assertEqual(mapping["scope_name"], scope)
        for requested in (("ha.read", "ha.control"), ("ha.control", "ha.read"),
                          ("ha.read",), ("ha.control",)):
            actions = [a for a in expected["ha_actions"]
                       if ("ha.read" if a == "state.read" else "ha.control") in requested]
            self.assertEqual(merged_claims(mappings, user, app["group"], requested),
                             expected | {"ha_actions": actions})
        absent = [i | {"state": "absent"} for i in self.items]
        self.run_role(absent, {k: v | {"state": "absent"} for k, v in self.services.items()})
        self.assertFalse(self.x.state.mappings or self.x.state.providers)

    def test_new_profiles_refuse_missing_or_nonrsa_metadata_before_mutation_or_delivery(self):
        for service in ("ov", "homelab", "ha"):
            for key_type in (None, "ec", "ed25519"):
                with self.subTest(service=service, key_type=key_type):
                    key = self.x.state.keypairs[fake.KEYPAIR]
                    key.pop("key_type", None)
                    if key_type is not None:
                        key["key_type"] = key_type
                    output = self.run_role([identity(service)], {service: self.services[service]}, ok=False)
                    self.assertIn("must have key_type rsa", output)
                    self.assertFalse(self.x.writes() or self.x.key_reads())
                    self.assertFalse(self.x.secret("agent-one", service + "-reader").exists())
                    self.assertFalse(any("/view_private_key/" in p for _, p, _ in self.x.state.requests))

    def test_signing_key_type_drift_after_apply_blocks_delivery(self):
        complete_apply = self.x.state.complete_apply
        def apply_then_change_key_type():
            complete_apply()
            self.x.state.keypairs[fake.KEYPAIR]["key_type"] = "ec"
        self.x.state.complete_apply = apply_then_change_key_type
        output = self.run_role([identity("ha")], {"ha": self.services["ha"]}, ok=False)
        self.assertIn("must retain private-key availability and RSA certificate metadata", output)
        self.assertEqual(len([p for m, p in self.x.writes() if p.endswith("/apply/")]), 1)
        self.assertFalse(self.x.key_reads())
        self.assertFalse(self.x.secret("agent-one", "ha-reader").exists())

    def test_cross_service_membership_drift_refused_before_mutation(self):
        self.run_role()
        self.x.state.groups[self.services["homelab"]["group"]]["users"].append("chaldea-agent-one-ov-reader")
        output = self.run_role(ok=False)
        self.assertIn("member of a group this role does not own", output)
        self.assertFalse(self.x.writes() or self.x.key_reads())

    def test_foreign_provider_consumer_blocks_partial_service_removal(self):
        self.run_role()
        provider = self.x.state.providers[self.services["ov"]["name"]]
        self.x.state.apps["budget"]["backchannel_providers"] = [provider["pk"]]
        removed = [i | {"state": "absent"} if i.get("service") == "ov" else i for i in self.items]
        output = self.run_role(removed, self.services | {"ov": self.services["ov"] | {"state": "absent"}}, ok=False)
        self.assertIn("foreign application budget uses it", output)
        self.assertFalse(self.x.writes() or self.x.key_reads())

    def test_wrong_issuer_origin_refused_before_requests(self):
        output = self.run_role(services=self.services | {"ov": access("ov")}, ok=False)
        self.assertIn("Require service trust URLs to name this issuer", output)
        self.assertFalse(self.x.state.requests)

    def test_unexpected_cross_app_admission_blocks_every_delivery(self):
        self.x.state.passing = lambda app, user: True
        output = self.run_role(ok=False)
        self.assertIn("Authentik's access answer for budget", output)
        self.assertFalse(self.x.key_reads())
        for item in self.items:
            self.assertFalse(self.x.secret("agent-one", item["profile"]).exists())


# Native 2026.8.4 keeps certificate metadata separate from private-key JWKs.
_jwks_spec = importlib.util.spec_from_file_location(
    "authentik_machine_jwks", base.HERE.parent / "library/authentik_machine_jwks.py")
jwks = importlib.util.module_from_spec(_jwks_spec)
_jwks_spec.loader.exec_module(jwks)


class SigningEvidenceDataTest(unittest.TestCase):
    def test_unique_native_signer_selection_and_key_bounds(self):
        key = fake.signing_jwk(rsa.generate_private_key(public_exponent=65537, key_size=2048))
        encryption = key | {"use": "enc", "alg": "RSA-OAEP-256"}
        for keys in ([key], [key, encryption], [encryption, key]):
            self.assertTrue(jwks.signing_evidence({"keys": keys}))
        ec_key = fake.signing_jwk(ec.generate_private_key(ec.SECP256R1()))
        for keys in ([], [None], [key] * 3, [key, key], [encryption], [ec_key, encryption],
                     [key | {"use": "other"}], [key | {"alg": "ES256"}],
                     [key | {"d": "private-must-not-be-returned"}],
                     [key | {"n": ""}], [key | {"e": "A"}], [key | {"n": "AA"}]):
            with self.subTest(keys=keys):
                self.assertFalse(jwks.signing_evidence({"keys": keys}))

    def test_whole_exchange_deadline_and_sanitized_failure(self):
        class Refused(Exception):
            pass
        module = mock.Mock()
        module.params = {"jwks_url": "https://auth.example.test/application/o/ha/jwks/"}
        module.fail_json.side_effect = Refused
        # A blocked read without a socket inactivity timeout must still lose to
        # the whole-exchange alarm. No network or issuer is used here.
        with mock.patch.object(jwks, "AnsibleModule", return_value=module), \
                mock.patch.object(jwks, "DEADLINE_SECONDS", 0.05), \
                mock.patch.object(jwks, "fetch_evidence", side_effect=lambda _: time.sleep(1)):
            started = time.monotonic()
            with self.assertRaises(Refused):
                jwks.main()
            self.assertLess(time.monotonic() - started, 0.5)
        module.fail_json.assert_called_once_with(msg="Public provider signing evidence refused")
        module.exit_json.assert_not_called()


class SigningEvidenceRoleTest(unittest.TestCase):
    def app(self, x, service):
        return access(service, f"https://127.0.0.1:{x.server.server_port}")

    def refuse(self, x, service):
        code, output = x.run([identity(service)], services={service: self.app(x, service)})
        self.assertNotEqual(code, 0, output)
        self.assertIn("Validate each new service's actual public signing evidence", output)
        self.assertFalse(x.key_reads())
        self.assertFalse(x.secret("agent-one", service + "-reader").exists())
        self.assertFalse(any("/view_private_key/" in p for _, p, _ in x.state.requests))
        self.assertTrue(x.state.public_authorization)
        self.assertTrue(all(a is None for a in x.state.public_authorization))
        return output

    def test_public_rsa_signer_with_optional_encryption_key_allows_delivery(self):
        x = base.Scratch(self)
        signer = x.state.jwks["keys"][0]
        x.state.jwks = {"keys": [signer | {"use": "enc", "alg": "RSA-OAEP-256"}, signer]}
        code, output = x.run([identity("ha")], services={"ha": self.app(x, "ha")})
        self.assertEqual(code, 0, output)
        public = [i for i, (_, p, _) in enumerate(x.state.requests) if p.startswith("/application/o/")]
        private = [i for i, (_, p, _) in enumerate(x.state.requests) if p.endswith("/view_key/")]
        self.assertEqual(len(public), 1)
        self.assertTrue(private and max(public) < min(private))
        self.assertTrue(x.secret("agent-one", "ha-reader").exists())
        self.assertEqual(x.state.public_authorization, [None])

    def test_rsa_certificate_cannot_mask_ec_or_ed25519_private_signer_for_any_new_service(self):
        # signals.py takes key_type from the cert; serializer accepts cert/key
        # independently. JWKSView and jwt_key use the actual private key, even
        # when x5c contains an RSA cert. No token or private-key API is used.
        for service in ("ov", "homelab", "ha"):
            x = base.Scratch(self)
            certificate = base.x509.load_pem_x509_certificate(x.cert.read_bytes())
            self.assertIsInstance(certificate.public_key(), rsa.RSAPublicKey)
            for private in (ec.generate_private_key(ec.SECP256R1()), ed25519.Ed25519PrivateKey.generate()):
                with self.subTest(service=service, private_type=type(private).__name__):
                    self.assertEqual(x.state.keypairs[fake.KEYPAIR]["key_type"], "rsa")
                    self.assertIs(x.state.keypairs[fake.KEYPAIR]["private_key_available"], True)
                    signer = fake.signing_jwk(private, certificate)
                    self.assertIn(signer["alg"], ("ES256", "EdDSA"))
                    self.assertTrue(signer["x5c"])
                    encryption = fake.signing_jwk(rsa.generate_private_key(public_exponent=65537, key_size=2048))
                    x.state.jwks = {"keys": [encryption | {"use": "enc", "alg": "RSA-OAEP-256"}, signer]}
                    self.refuse(x, service)

    def test_redirect_and_oversized_public_response_never_deliver_or_expose_errors(self):
        x = base.Scratch(self)
        path = "/application/o/chaldea-ha/jwks/"
        x.state.redirects[("GET", path)] = "https://untrusted.invalid/MUST-NOT-PRINT"
        output = self.refuse(x, "ha")
        self.assertNotIn("MUST-NOT-PRINT", output)
        self.assertEqual([p for _, p, _ in x.state.requests if p.startswith("/application/o/")], [path])
        x.state.redirects.clear()
        x.state.jwks["padding"] = "MUST-NOT-PRINT" * (jwks.MAX_BODY // 14 + 1)
        for missing_length in (False, True):
            x.state.public_without_length = missing_length
            output = self.refuse(x, "ha")
            self.assertNotIn("MUST-NOT-PRINT", output)
