"""Closed web MCP profiles. All issuer/host operations use the synthetic real-role fixture."""
import copy
from importlib.metadata import version
from itertools import permutations
import types
import unittest

from deepmerge import always_merger

import test_machine_identities as base

mi = base.mi
fake = base.fake


def access(service, origin="https://auth.example.test"):
    return base.ACCESS | {
        "slug": "chaldea-" + service, "name": "Chaldea " + service + " machine access",
        "group": "chaldea-" + service + "-machine-identities", "client_id": "chaldea-" + service + "-machine",
        "resource": "https://" + service + ".mcp.ryzhkov.dev/mcp", "scope": service + ".read",
        "issuer": origin + "/application/o/chaldea-" + service + "/",
        "jwks_url": origin + "/application/o/chaldea-" + service + "/jwks/",
    }


def identity(service):
    item = base.identity(profile=service + "-reader") | {"service": service}
    item.pop("project")
    grants = {"ov": {"ov_namespaces": ["viking://resources/namespaceA"]},
              "homelab": {"homelab_services": ["dns"], "homelab_hosts": ["host-a"]},
              "ha": {"ha_entities": ["sensor.a"], "ha_actions": ["state.read"]}}
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
        self.assertIn("must remain an available RSA private key", output)
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
