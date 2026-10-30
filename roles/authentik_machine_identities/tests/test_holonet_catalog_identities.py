"""playbooks/authentik-holonet-catalog.yml: the declaration as plain data, the
read-only baseline producer, and the actual entrypoint played against the fake
Authentik with every destination a scratch directory. A sentinel stands in for
SOPS and returns only the fake admin token; nothing here reaches Authentik, a
host or a secret store."""
import copy
import hashlib
import os
import subprocess
import types
import unittest

import yaml

import test_machine_identities as base

mi = base.mi
fake = base.fake
ROOT = base.ROOT
ENTRYPOINT = ROOT / "playbooks/authentik-holonet-catalog.yml"
VARS = ROOT / "playbooks/vars/authentik-holonet-catalog.yml"
BLUEPRINT = "homelab-infra holonet catalog identities"
HOLONET = {"state": "present", "slug": "chaldea-holonet", "name": "Chaldea HoloNet catalog access",
           "group": "chaldea-holonet-machine-identities", "client_id": "chaldea-holonet-machine",
           "resource": "https://media.mcp.ryzhkov.dev/mcp", "signing_key": "8bbd6516-af33-4e64-a73b-caa39e692c29",
           "scope": "holonet.read", "token_seconds": 300,
           "issuer": "https://auth.ryzhkov.dev/application/o/chaldea-holonet/",
           "jwks_url": "https://auth.ryzhkov.dev/application/o/chaldea-holonet/jwks/"}
# The live-matched public historical grant: one server, the Movies library, one
# catalog user, no history or target users, catalog.read only.
GRANT = {"holonet_server": "3c86e6d7ff6c4a3296522cdf03d22bb0", "holonet_libraries": ["3"],
         "holonet_catalog_users": ["54b7d36cef804d3c8478474553ed3a82"], "holonet_history_users": [],
         "holonet_target_users": [], "holonet_actions": ["catalog.read"], "holonet_namespace": "movies-catalog"}
HOMELAB = {"host": "homelab", "profile": "holonet-movies-catalog", "service": "holonet", **GRANT,
           "generation": 1, "expires": "2026-11-20T00:00:00Z", "state": "present"}
LAPTOP = {"host": "laptop", "profile": "holonet-movies-catalog-claude", "service": "holonet", **GRANT,
          "generation": 1, "expires": "2026-11-20T00:00:00Z", "state": "present"}
COHORT = [HOMELAB, LAPTOP]
INTERACTIVE = LAPTOP | {"profile": "holonet-interactive"}
WRITER = LAPTOP | {"profile": "holonet-recommendations", "holonet_target_users": ["c" * 32],
                   "holonet_actions": [*mi.HOLONET_WRITER_ACTIONS, "object.delete"],
                   "holonet_namespace": "managed-recommendations"}
ANCHOR = {"blueprint_pk": "2514696c-15e3-494e-8df6-eb0c098a9efb",
          "content_sha256": "aef30eb3096776f9edddff5d00cb3eb3aac7d1128b05a730aa138407bc577f58"}
# scope_expression_sha256 from the 2026-10-22 read-only issuer receipt, which
# matched the stored mapping against the homelab-only declaration.
HISTORICAL_EXPRESSION_SHA256 = "8874e2d192e9a64a36c9a9725edc508be269fb6aa1694a25725ede5080f7521a"
HOSTS = {"homelab": {"ssh_user": "iryzhkov", "home": "/home/iryzhkov", "connection": "ssh"},
         "laptop": {"ssh_user": "igor", "home": "/home/igor", "connection": "local"}}
# Fixed so the expiry window does not depend on the day the suite runs.
NOW = "2026-10-26T00:00:00Z"


def spec(identities, services=None, hosts=HOSTS, now=NOW):
    return mi.machine_identity_spec(identities, {}, hosts, 180, now,
                                    {"holonet": HOLONET} if services is None else services)


def evaluate(expression, user, group, scope=("holonet.read",)):
    namespace = {"user": user, "token": types.SimpleNamespace(scope=list(scope)),
                 "ak_is_group_member": lambda u, name: name == group}
    exec("def mapping():\n" + "\n".join("    " + line for line in expression.splitlines()), namespace)
    return namespace["mapping"]()


def stored(owner_spec):
    """A typed stand-in for the stored historical blueprint content."""
    return (owner_spec["marker"] + "\nversion: 1\nentries:\n"
            "- model: authentik_providers_oauth2.oauth2provider\n"
            "  id: holonet-provider\n  identifiers:\n    name: " + HOLONET["name"] +
            "\n  attrs:\n    client_id: " + HOLONET["client_id"] +
            "\n- model: authentik_core.application\n  identifiers:\n    slug: " + HOLONET["slug"] +
            "\n  attrs:\n    provider: !KeyOf holonet-provider\n")


class DeclarationTest(unittest.TestCase):
    def test_source_declaration_is_exact_isolated_and_unbound(self):
        values = yaml.safe_load(VARS.read_text())
        self.assertEqual(values["authentik_machine_services"], {"holonet": HOLONET})
        self.assertEqual(values["authentik_machine_identities"], COHORT)
        self.assertEqual(values["authentik_machine_identities_blueprint_name"], BLUEPRINT)
        self.assertIs(values["authentik_machine_identity_existing_owned_only"], True)
        self.assertEqual(values["authentik_machine_identity_max_days"], 180)
        self.assertEqual(values["holonet_catalog_baseline_anchor"], ANCHOR)
        # No baseline PK/hash, destination, apply switch or connection in source.
        self.assertEqual(set(values), {
            "authentik_machine_identities_enabled", "authentik_machine_identity_existing_owned_only",
            "authentik_machine_identities_blueprint_name", "authentik_machine_identity_max_days",
            "holonet_catalog_baseline_anchor",
            "authentik_machine_services", "authentik_machine_identities",
            "holonet_catalog_interactive_enabled", "holonet_catalog_interactive_identity",
            "holonet_catalog_recommendations_enabled", "holonet_catalog_recommendations_identity"})
        self.assertIs(values["holonet_catalog_interactive_enabled"], False)
        self.assertEqual(values["holonet_catalog_interactive_identity"], INTERACTIVE)
        self.assertIs(values["holonet_catalog_recommendations_enabled"], False)
        self.assertEqual(values["holonet_catalog_recommendations_identity"], WRITER | {
            "holonet_target_users": [], "expires": ""})
        integrated = yaml.safe_load((ROOT / "playbooks/vars/authentik.yml").read_text())
        self.assertIs(integrated["authentik_machine_identities_enabled"], False)
        play = yaml.safe_load(ENTRYPOINT.read_text())
        self.assertEqual(len(play), 1)
        self.assertNotIn("roles", play[0])
        self.assertEqual(play[0]["vars_files"], ["vars/authentik-holonet-catalog.yml"])
        self.assertEqual([(t["ansible.builtin.include_role"]["name"],
                           t["ansible.builtin.include_role"].get("tasks_from")) for t in play[0]["tasks"]],
                         [("authentik_machine_identities", None), ("authentik_machine_identities", "baseline.yml")])

        homelab, laptop = spec(COHORT)["identities"]
        self.assertEqual(homelab["username"], "chaldea-homelab-holonet-movies-catalog")
        self.assertEqual(laptop["username"], "chaldea-laptop-holonet-movies-catalog-claude")
        self.assertEqual(laptop["secret_path"],
                         "/home/igor/.config/chaldea/authentik/holonet-movies-catalog-claude.app-password")
        self.assertNotEqual(laptop["token"], homelab["token"])
        self.assertEqual(laptop["grant"], homelab["grant"])

    def test_homelab_identity_and_mapping_match_the_live_matched_baseline(self):
        historical = spec([HOMELAB])
        # The stored mapping the read-only receipt matched is what current
        # source renders for the homelab-only declaration.
        self.assertEqual(hashlib.sha256(historical["services"]["holonet"]["expression"].encode()).hexdigest(),
                         HISTORICAL_EXPRESSION_SHA256)
        self.assertEqual(historical["identities"][0]["attributes"], {
            "chaldea_holonet": GRANT, "goauthentik.io/user/token-maximum-lifetime": "days=180",
            "homelab-infra": {"host": "homelab", "profile": "holonet-movies-catalog"}})
        cohort = spec(COHORT)
        # Generation, expiry, grant, attributes, token and custody are unchanged.
        self.assertEqual(cohort["identities"][0], historical["identities"][0])
        laptop = cohort["identities"][1]
        self.assertEqual(sorted(cohort["objects"]), sorted(historical["objects"] + [
            ["user", laptop["username"], "present"], ["token", laptop["token"], "present"]]))
        for key in HOLONET:
            self.assertEqual(cohort["services"]["holonet"][key], historical["services"]["holonet"][key])
        # Exactly the expected-account literal changes in the mapping.
        before = historical["services"]["holonet"]["expression"].splitlines()
        after = cohort["services"]["holonet"]["expression"].splitlines()
        self.assertEqual((len(after), after[0], after[2:]), (len(before), before[0], before[2:]))
        self.assertEqual(after[1], "expected = " + repr(
            historical["services"]["holonet"]["account_grants"] | {laptop["username"]: laptop["grant"]}))
        # A later run with only the laptop absent restores the historical mapping.
        removed = spec([HOMELAB, LAPTOP | {"state": "absent"}])
        self.assertEqual(removed["services"]["holonet"]["expression"], historical["services"]["holonet"]["expression"])
        self.assertEqual(removed["identities"][0], historical["identities"][0])

    def test_mapping_emits_the_fixed_grant_only_to_declared_accounts(self):
        s = spec(COHORT)
        expression = s["services"]["holonet"]["expression"]
        expected = GRANT | {"aud": HOLONET["resource"]}
        for item in s["identities"]:
            user = types.SimpleNamespace(username=item["username"], type="service_account",
                                         attributes=copy.deepcopy(item["attributes"]))
            self.assertEqual(evaluate(expression, user, HOLONET["group"]), expected)
            self.assertEqual(evaluate(expression, user, "foreign"), {})
            for scope in (["holonet.read", "holonet.home"], ["holonet.curate"], []):
                self.assertEqual(evaluate(expression, user, HOLONET["group"], scope), {})
            for key, value in (("holonet_history_users", GRANT["holonet_catalog_users"]),
                               ("holonet_actions", ["catalog.read", "userdata.read"]),
                               ("holonet_libraries", ["4"]), ("holonet_namespace", "tv-catalog")):
                widened = copy.deepcopy(user)
                widened.attributes["chaldea_holonet"][key] = value
                self.assertEqual(evaluate(expression, widened, HOLONET["group"]), {})
        stranger = types.SimpleNamespace(username="chaldea-laptop-holonet-movies-native-20261022",
                                         type="service_account", attributes={"chaldea_holonet": GRANT})
        self.assertEqual(evaluate(expression, stranger, HOLONET["group"]), {})
        # The closed profile still refuses a broader laptop grant outright.
        for key, bad in (("holonet_history_users", GRANT["holonet_catalog_users"]),
                         ("holonet_target_users", GRANT["holonet_catalog_users"]),
                         ("holonet_actions", ["catalog.read", "history.read"]),
                         ("holonet_libraries", ["3", "4"])):
            with self.subTest(key=key), self.assertRaises(mi.AnsibleFilterError):
                spec([HOMELAB, LAPTOP | {key: bad}])


class BaselineTest(unittest.TestCase):
    """The read-only producer of the existing-only gate's public baseline."""

    def setUp(self):
        self.content = stored(spec([HOMELAB]))
        digest = mi.sha512(self.content.encode()).hexdigest()
        self.anchor = {"blueprint_pk": ANCHOR["blueprint_pk"],
                       "content_sha256": hashlib.sha256(self.content.encode()).hexdigest()}
        self.args = dict(
            identities=[HOMELAB], services={"holonet": HOLONET}, max_days=180, now=NOW, anchor=self.anchor,
            instance={"pk": ANCHOR["blueprint_pk"], "name": BLUEPRINT, "content": self.content,
                      "status": "successful", "enabled": True, "path": "", "context": {},
                      "last_applied_hash": digest},
            inventory={"status": 200, "json": fake.page([
                {"pk": 77, "name": HOLONET["name"], "meta_model_name": "authentik_providers_oauth2.oauth2provider"},
                {"pk": 42, "name": "OV HTTP a63e197f machine access",
                 "meta_model_name": "authentik_providers_oauth2.oauth2provider"}])},
            applications={"status": 200, "json": fake.page([
                {"pk": "0b9d5f3e-1111-4c4c-8888-123456789abc", "slug": HOLONET["slug"], "provider": 77}])},
            actor={"status": 200, "json": {"user": {"is_superuser": True}}})

    def produce(self, **changes):
        return mi.machine_identity_existing_baseline(**(copy.deepcopy(self.args) | changes))

    def test_only_anchored_content_yields_the_allowlisted_baseline(self):
        result = self.produce()
        baseline = {"provider_pk": 77, "application_pk": "0b9d5f3e-1111-4c4c-8888-123456789abc",
                    "content_sha512": mi.sha512(self.content.encode()).hexdigest()}
        self.assertEqual(result, {
            "blueprint_pk": ANCHOR["blueprint_pk"], "content_sha256": self.anchor["content_sha256"],
            "authentik_machine_identity_existing_provider_baseline": baseline,
            "stored_content_matches_anchor": True, "ownership_matches_declaration": True,
            "existing_only_gate_holds": True})
        # The produced baseline is what a later online run of the cohort accepts.
        proof = mi.machine_identity_existing_provider(
            spec(COHORT), self.args["instance"], baseline, self.args["inventory"],
            self.args["applications"], self.args["actor"])
        self.assertEqual(proof["path"], "/providers/oauth2/77/")

    def test_any_drift_or_incomplete_evidence_refuses(self):
        instance = self.args["instance"]
        cohort_content = stored(spec(COHORT))
        page = fake.page
        provider = {"pk": 77, "name": HOLONET["name"], "meta_model_name": "authentik_providers_oauth2.oauth2provider"}
        app = self.args["applications"]["json"]["results"][0]
        cases = {
            "anchor shape": dict(anchor={"blueprint_pk": ANCHOR["blueprint_pk"]}),
            "anchor case": dict(anchor=self.anchor | {"content_sha256": self.anchor["content_sha256"].upper()}),
            "other blueprint": dict(instance=instance | {"pk": "11111111-2222-4333-8444-555555555555"}),
            "no blueprint": dict(instance={}),
            "changed content": dict(instance=instance | {"content": self.content + " "}),
            # Content whose hash matches the anchor but owns the new cohort.
            "other ownership": dict(instance=instance | {"content": cohort_content}, anchor=self.anchor | {
                "content_sha256": hashlib.sha256(cohort_content.encode()).hexdigest()}),
            "not last applied": dict(instance=instance | {"last_applied_hash": "0" * 128}),
            "failed": dict(instance=instance | {"status": "error"}),
            "disabled": dict(instance=instance | {"enabled": False}),
            "declared cohort": dict(identities=COHORT),
            "second service": dict(services={"holonet": HOLONET, "jocasta": base.ACCESS}),
            "missing provider": dict(inventory={"status": 200, "json": page([])}),
            "duplicate provider": dict(inventory={"status": 200, "json": page([provider, provider | {"pk": 78}])}),
            "non-oauth provider": dict(inventory={"status": 200, "json": page([provider | {"meta_model_name": "x"}])}),
            "incomplete providers": dict(inventory={"status": 200, "json": page([provider]) | {
                "pagination": {"count": 2, "current": 1, "total_pages": 1}}}),
            "providers unavailable": dict(inventory={"status": 403, "json": {}}),
            "rebound application": dict(applications={"status": 200, "json": page([app | {"provider": 78}])}),
            "two applications": dict(applications={"status": 200, "json": page([app, app])}),
            "no application": dict(applications={"status": 200, "json": page([])}),
            "not superuser": dict(actor={"status": 200, "json": {"user": {"is_superuser": False}}}),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(mi.AnsibleFilterError):
                self.produce(**change)


class EntrypointTest(unittest.TestCase):
    """The actual entrypoint, whose guard pins the real source cohort."""

    def setUp(self):
        # The pinned homelab expiry is 2026-11-20 and the role refuses a present
        # identity within a day of expiry, so these plays run only while that
        # declaration is still acceptable. Extending it is a reviewed change.
        if mi._time(HOMELAB["expires"], "expires") < base.NOW + mi.MIN_REMAINING:
            self.skipTest("the pinned homelab expiry has passed the role's window")
        x = self.x = base.Scratch(self)
        self.origin = f"https://127.0.0.1:{x.server.server_port}"
        self.holonet = HOLONET | {"issuer": self.origin + "/application/o/chaldea-holonet/",
                                  "jwks_url": self.origin + "/application/o/chaldea-holonet/jwks/"}
        x.state.keypairs[HOLONET["signing_key"]] = dict(x.state.keypairs[fake.KEYPAIR], pk=HOLONET["signing_key"])
        x.homes = {}
        hosts = {}
        for host in ("homelab", "laptop"):
            home = x.base / host
            home.mkdir(mode=0o755)
            x.homes[host] = home
            hosts[host] = {"ansible_connection": "local", "ssh_user": base.USER,
                           "authentik_machine_identity_home": str(home),
                           "ansible_python_interpreter": base.sys.executable}
        x.inventory.write_text(yaml.safe_dump({"all": {"hosts": hosts}}))
        here = {"hostname": os.uname().nodename, "uid": os.getuid()}
        self.bindings = {"homelab": dict(here), "laptop": dict(here)}
        self.use_patched = False
        plugins = x.base / "collections/ansible_collections/community/sops/plugins"
        (plugins / "action").mkdir(parents=True)
        (plugins / "modules").mkdir()
        (plugins / "modules/load_vars.py").write_text("")
        api = {"authentik_api_url": self.origin + "/api/v3", "authentik_api_token": fake.ADMIN}
        (plugins / "action/load_vars.py").write_text(
            "from pathlib import Path\n"
            "from ansible.plugins.action import ActionBase\n"
            "class ActionModule(ActionBase):\n"
            "    def run(self, tmp=None, task_vars=None):\n"
            f"        Path({str(x.base / 'sops-loaded')!r}).touch()\n"
            f"        return {{'changed': False, 'ansible_facts': {{'authentik_api': {api!r}}}}}\n")

    def env(self):
        x = self.x
        return dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"), ANSIBLE_NOCOLOR="1",
                    ANSIBLE_LOCAL_TEMP=str(x.base / "ansible-local"),
                    ANSIBLE_REMOTE_TEMP=str(x.base / "ansible-remote"),
                    ANSIBLE_COLLECTIONS_PATH=str(x.base / "collections"), SSL_CERT_FILE=str(x.cert))

    def ansible(self, argv):
        result = subprocess.run(argv, cwd=ROOT, env=self.env(), capture_output=True, text=True, timeout=600)
        output = result.stdout + result.stderr
        self.assertNotIn(fake.ADMIN, output)
        self.assertNotIn(fake.KEY_PREFIX, output)
        return result.returncode, output

    def entrypoint(self, patched):
        if not patched:
            return ENTRYPOINT
        # Test-transport boundary only: a scratch copy whose literal API pin
        # names this fixture's origin, so plays can reach the fake. It proves
        # the role's logic against the fake, never the production origin pin,
        # which the unpatched entrypoint's refusals below exercise.
        text = ENTRYPOINT.read_text()
        literal = "'https://auth.ryzhkov.dev/api/v3'"
        self.assertEqual(text.count(literal), 1)
        target = self.x.base / "playbooks" / ENTRYPOINT.name
        (target.parent / "vars").mkdir(parents=True, exist_ok=True)
        (target.parent / "vars" / VARS.name).write_text(VARS.read_text())
        target.write_text(text.replace(literal, repr(self.origin + "/api/v3")))
        return target

    def play(self, baseline=None, apply=True, check=False, online=False, baseline_read=False, patched=None,
             source_services=False, **overrides):
        x = self.x
        path = self.entrypoint(self.use_patched if patched is None else patched)
        extra = {"authentik_machine_services": {"holonet": self.holonet},
                 "authentik_machine_identity_destinations": self.bindings,
                 "authentik_machine_identity_existing_provider_baseline": baseline or {}} | overrides
        if source_services:
            extra.pop("authentik_machine_services", None)
        extra_path = x.base / "entrypoint-extra.yml"
        extra_path.write_text(yaml.safe_dump(extra))
        (x.base / "sops-loaded").unlink(missing_ok=True)
        x.state.requests.clear()
        argv = ["ansible-playbook", "-i", str(x.inventory), str(path), "--diff", "-e", "@" + str(extra_path)]
        argv += ["--check"] if check else []
        argv += ["-e", "holonet_catalog_apply=true"] if apply and not check else []
        argv += ["-e", "holonet_catalog_online_check=true"] if online else []
        argv += ["-e", "holonet_catalog_baseline_read=true"] if baseline_read else []
        return self.ansible(argv)

    def ok(self, *args, **kwargs):
        code, output = self.play(*args, **kwargs)
        self.assertEqual(code, 0, output)
        return output

    def refused(self, fragment, *args, **kwargs):
        code, output = self.play(*args, **kwargs)
        self.assertNotEqual(code, 0, output)
        self.assertIn(fragment, output)
        return output

    def produce_baseline(self, anchor):
        """Play the role's read-only baseline task directly with a fixture anchor."""
        x = self.x
        play = [{"name": "Baseline read against a fake Authentik", "hosts": "localhost",
                 "connection": "local", "gather_facts": False,
                 "vars": {"ansible_python_interpreter": base.sys.executable,
                          "authentik_url": self.origin + "/api/v3",
                          "authentik_headers": {"Authorization": "Bearer " + fake.ADMIN},
                          "authentik_machine_identities_blueprint_name": BLUEPRINT,
                          "authentik_machine_services": {"holonet": self.holonet},
                          "machine_identity_baseline_anchor": anchor,
                          "machine_identity_baseline_identities": [HOMELAB]},
                 "tasks": [{"ansible.builtin.include_role": {"name": "authentik_machine_identities",
                                                              "tasks_from": "baseline.yml"}}]}]
        path = x.base / "baseline-play.yml"
        path.write_text(yaml.safe_dump(play))
        x.state.requests.clear()
        return self.ansible(["ansible-playbook", "-i", str(x.inventory), str(path), "--check"])

    def secret(self, host, item):
        return self.x.secret(host, item["profile"])

    def homelab_snapshot(self):
        user = "chaldea-homelab-" + HOMELAB["profile"]
        path = self.secret("homelab", HOMELAB)
        return (copy.deepcopy(self.x.state.users[user]), copy.deepcopy(self.x.state.tokens[user + "-g1"]),
                path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_mode & 0o777)

    def private_reads(self):
        return [p for _, p, _ in self.x.state.requests if p.startswith("/api/v3/providers/oauth2/")]

    def baseline(self):
        x = self.x
        content = next(iter(x.state.blueprints.values()))["content"]
        return {"provider_pk": x.state.providers[HOLONET["name"]]["pk"],
                "application_pk": x.state.apps[HOLONET["slug"]]["pk"],
                "content_sha512": mi.sha512(content.encode()).hexdigest()}

    def untouched(self, before):
        self.assertEqual((self.x.writes(), self.x.key_reads(), self.private_reads()), ([], [], []))
        self.assertEqual(self.homelab_snapshot(), before)
        self.assertFalse(self.secret("laptop", LAPTOP).exists())

    def test_ordinary_reader_checkpoint_is_pinned_read_only_and_requires_exact_cohort(self):
        x = self.x
        options = {"check": True, "baseline_read": True,
                   "holonet_catalog_baseline_checkpoint": "ordinary-reader",
                   "holonet_catalog_interactive_enabled": True}
        for changed in ({"holonet_catalog_baseline_checkpoint": "unknown"},
                        {"holonet_catalog_interactive_enabled": False},
                        {"holonet_catalog_recommendations_enabled": True},
                        {"apply": True, "check": False}):
            self.refused("The baseline read runs alone", **(options | changed))
            self.assertFalse(x.state.requests or (x.base / "sops-loaded").exists())
        code, output = x.run(COHORT + [INTERACTIVE], services={"holonet": self.holonet},
                             extra_vars={"authentik_machine_identities_blueprint_name": BLUEPRINT})
        self.assertEqual(code, 0, output)
        before = self.homelab_snapshot()
        baseline = self.baseline()
        # Real literal refuses synthetic stored bytes after discovery, without writes.
        self.refused("Derive the baseline only from content matching the anchor",
                     patched=True, **options)
        self.assertEqual((x.writes(), x.key_reads(), self.private_reads()), ([], [], []))
        # Fixture transport and trusted-anchor substitution only. Never adopt a
        # response hash in production; the source literal is pinned above.
        entrypoint = self.entrypoint
        content = next(iter(x.state.blueprints.values()))["content"]
        fixture_hash = hashlib.sha256(content.encode()).hexdigest()
        def fixture_entrypoint(patched):
            path = entrypoint(True)
            text = path.read_text()
            literal = "7bfb4856b2a0af4ef437182c944b30a4b88dcabd8cc1e00143f14633ce804c9a"
            self.assertEqual(text.count(literal), 1)
            text = text.replace(literal, fixture_hash)
            pk_literal = "{{ {'blueprint_pk': '2514696c-15e3-494e-8df6-eb0c098a9efb'"
            self.assertEqual(text.count(pk_literal), 1)
            path.write_text(text.replace(pk_literal, "{{ {'blueprint_pk': " + repr(next(iter(x.state.blueprints)))))
            return path
        self.entrypoint = fixture_entrypoint
        output = self.ok(**options)
        self.assertIn(baseline["content_sha512"], output)
        self.assertEqual(len(x.state.requests), 4)
        self.assertEqual((x.writes(), x.key_reads(), self.private_reads()), ([], [], []))
        self.assertEqual(self.homelab_snapshot(), before)
        # Even a matching content hash cannot excuse a different declared cohort.
        self.refused("Derive the baseline only from content matching the anchor",
                     **(options | {"holonet_catalog_interactive_identity": INTERACTIVE | {"generation": 2}}))
        self.assertEqual((x.writes(), x.key_reads(), self.private_reads()), ([], [], []))

    def test_baseline_read_and_guards_refuse_before_any_change(self):
        x = self.x
        # Fixture-only stand-in for the existing persistent homelab enrollment.
        code, output = x.run([HOMELAB], services={"holonet": self.holonet},
                             extra_vars={"authentik_machine_identities_blueprint_name": BLUEPRINT})
        self.assertEqual(code, 0, output)
        before = self.homelab_snapshot()
        x.state.providers["Foreign"] = {"pk": 999, "name": "Foreign", "client_id": "foreign-client",
                                       "meta_model_name": "authentik_providers_oauth2.oauth2provider",
                                       "client_secret": "FOREIGN-SECRET-SENTINEL"}
        stored_content = next(iter(x.state.blueprints.values()))["content"]

        # The read-only producer derives exactly the baseline an operator would
        # otherwise compute by hand, with discovery reads only.
        anchor = {"blueprint_pk": next(iter(x.state.blueprints)),
                  "content_sha256": hashlib.sha256(stored_content.encode()).hexdigest()}
        code, output = self.produce_baseline(anchor)
        self.assertEqual(code, 0, output)
        original = self.baseline()
        for value in (str(original["provider_pk"]), original["application_pk"], original["content_sha512"]):
            self.assertIn(value, output)
        for leaked in ("homelab-infra-owned", "expected =", "FOREIGN-SECRET-SENTINEL", "client_secret"):
            self.assertNotIn(leaked, output)
        self.assertEqual(sorted({(m, p) for m, p, _ in x.state.requests}), sorted({
            ("GET", "/api/v3/managed/blueprints/"), ("GET", "/api/v3/core/users/me/"),
            ("GET", "/api/v3/providers/all/"), ("GET", "/api/v3/core/applications/")}))
        self.untouched(before)
        # A different anchor yields nothing.
        code, output = self.produce_baseline(anchor | {"content_sha256": "0" * 64})
        self.assertNotEqual(code, 0, output)
        self.assertNotIn(original["content_sha512"], output)

        # Through the entrypoint, the pinned historical anchor refuses this
        # fixture's content after discovery reads only, and needs check mode alone.
        code, output = self.play(baseline_read=True, check=True, patched=True,
                                 authentik_machine_identity_destinations={})
        self.assertNotEqual(code, 0, output)
        self.assertIn("Derive the baseline only from content matching the anchor", output)
        self.assertNotIn(original["content_sha512"], output)
        self.untouched(before)
        for mode in ({"apply": True}, {"check": True, "online": True}):
            self.refused("The baseline read runs alone", baseline_read=True, **mode)
            self.assertFalse(x.state.requests or (x.base / "sops-loaded").exists())

        # Off unless explicitly requested; the offline plan touches nothing.
        self.ok(original, check=True)
        self.assertFalse(x.state.requests or (x.base / "sops-loaded").exists())
        self.refused("holonet_catalog_apply=true", original, apply=False)
        self.assertFalse(x.state.requests or (x.base / "sops-loaded").exists())

        # The guard pins the real source cohort; extra vars cannot change it.
        guard = "keeps its homelab identity present and unchanged"
        wide = GRANT["holonet_catalog_users"]
        for overrides in (
                {"authentik_machine_identities": [HOMELAB | {"state": "absent"}, LAPTOP]},
                {"authentik_machine_identities": [LAPTOP]},
                {"authentik_machine_identities": [HOMELAB | {"generation": 2}, LAPTOP]},
                {"authentik_machine_identities": [HOMELAB | {"expires": "2027-01-15T00:00:00Z"}, LAPTOP]},
                {"authentik_machine_identities": [HOMELAB | {"holonet_libraries": ["4"]}, LAPTOP]},
                {"authentik_machine_identities": [HOMELAB]},
                {"authentik_machine_identities": [HOMELAB, LAPTOP | {"holonet_history_users": wide}]},
                {"authentik_machine_identities": [HOMELAB, LAPTOP | {"holonet_target_users": wide}]},
                {"authentik_machine_identities": [HOMELAB, LAPTOP | {"holonet_namespace": "tv-catalog"}]},
                {"authentik_machine_identities": [HOMELAB, LAPTOP | {"host": "homelab"}]},
                {"authentik_machine_identities": [HOMELAB, LAPTOP | {"profile": "holonet-movies-catalog-codex"}]},
                {"authentik_machine_identities": [HOMELAB, LAPTOP, LAPTOP | {"profile": "holonet-tv-catalog"}]},
                {"authentik_machine_identities": [LAPTOP, HOMELAB]},
                {"authentik_machine_services": {"holonet": self.holonet | {"state": "absent"}}},
                {"authentik_machine_services": {"holonet": self.holonet, "jocasta": base.ACCESS}},
                {"authentik_machine_identity_existing_owned_only": False},
                {"authentik_machine_identity_max_days": 90},
                {"holonet_catalog_baseline_anchor": anchor}):
            with self.subTest(overrides=list(overrides)):
                self.refused(guard, original, **overrides)
                self.assertFalse(x.state.requests or (x.base / "sops-loaded").exists())
        self.assertEqual(self.homelab_snapshot(), before)

        # Every host-touching mode needs both destinations bound.
        for bindings in ({}, {"laptop": self.bindings["laptop"]}):
            self.refused("binding exactly homelab and laptop", original,
                         authentik_machine_identity_destinations=bindings)
            self.assertFalse(x.state.requests or (x.base / "sops-loaded").exists())

    def test_moved_api_url_and_origin_refuse_before_any_request(self):
        """The unpatched entrypoint: the decrypted credential is never sent to
        any origin but the literal Authentik API, however the inputs move."""
        x = self.x
        moved = self.origin + "/api/v3"
        cases = (
            # The sentinel's credential itself names this fixture, a foreign origin.
            {},
            # The URL and the former expected-origin input moved together.
            {"authentik_url": moved, "holonet_catalog_authentik_origin": self.origin},
            {"authentik_url": moved + "/", "holonet_catalog_authentik_origin": self.origin},
            {"authentik_url": "https://auth.ryzhkov.dev.example.test/api/v3",
             "holonet_catalog_authentik_origin": "https://auth.ryzhkov.dev.example.test"},
        )
        pin = "Pin administrative credential transport to the existing Authentik API"
        for overrides in cases:
            for mode in ({}, {"check": True, "online": True}, {"check": True, "baseline_read": True}):
                with self.subTest(overrides=sorted(overrides), mode=sorted(mode)):
                    code, output = self.play(**mode, **overrides)
                    self.assertNotEqual(code, 0, output)
                    self.assertIn(pin, output)
                    # Refused after the credential was loaded, before any request.
                    self.assertTrue((x.base / "sops-loaded").exists())
                    self.assertEqual(x.state.requests, [])

    def test_reserved_derived_facts_refuse_before_credential_load(self):
        """The unpatched entrypoint with the legitimate literal authentik_url:
        an extra-vars machine_identity_api, the URL every role request uses,
        or a baseline include input is refused before SOPS loading, so no
        credential reaches any receiver."""
        x = self.x
        legitimate = {"authentik_url": "https://auth.ryzhkov.dev/api/v3"}
        anchor = {"blueprint_pk": ANCHOR["blueprint_pk"], "content_sha256": "0" * 64}
        reserved = "Refuse extra vars for facts this entrypoint and its role derive"
        for overrides in ({"machine_identity_api": self.origin + "/api/v3"},
                          {"machine_identity_baseline_anchor": anchor},
                          {"machine_identity_baseline_identities": [LAPTOP]}):
            for mode in ({}, {"check": True, "online": True}, {"check": True, "baseline_read": True}):
                with self.subTest(overrides=sorted(overrides), mode=sorted(mode)):
                    code, output = self.play(**mode, **legitimate, **overrides)
                    self.assertNotEqual(code, 0, output)
                    self.assertIn(reserved, output)
                    self.assertFalse((x.base / "sops-loaded").exists())
                    self.assertEqual(x.state.requests, [])

    def test_real_cohort_apply_preserves_homelab_and_converges(self):
        x = self.x
        self.use_patched = True
        code, output = x.run([HOMELAB], services={"holonet": self.holonet},
                             extra_vars={"authentik_machine_identities_blueprint_name": BLUEPRINT})
        self.assertEqual(code, 0, output)
        before = self.homelab_snapshot()
        original = self.baseline()
        x.state.providers["Foreign"] = {"pk": 999, "name": "Foreign", "client_id": "foreign-client",
                                       "meta_model_name": "authentik_providers_oauth2.oauth2provider",
                                       "client_secret": "FOREIGN-SECRET-SENTINEL"}

        # No retained baseline: no private detail, API write or key read.
        gate = "Require the operator's prior baseline before any private detail"
        self.refused(gate)
        self.untouched(before)

        # A remapped destination stops before Authentik changes or a key read.
        for host, change in (("homelab", {"uid": os.getuid() + 1}), ("laptop", {"hostname": "omarchy-pc"})):
            with self.subTest(remapped=host):
                bindings = copy.deepcopy(self.bindings)
                bindings[host].update(change)
                self.refused("does not reach its bound destination", original,
                             authentik_machine_identity_destinations=bindings)
                self.assertEqual((x.writes(), x.key_reads()), ([], []))
                self.assertEqual(self.homelab_snapshot(), before)

        self.ok(original)
        expected_path = "/api/v3/providers/oauth2/" + str(original["provider_pk"]) + "/"
        self.assertEqual(set(self.private_reads()), {expected_path})
        self.assertEqual(self.homelab_snapshot(), before)
        self.assertEqual((x.state.providers[HOLONET["name"]]["pk"], x.state.apps[HOLONET["slug"]]["pk"]),
                         (original["provider_pk"], original["application_pk"]))
        username = "chaldea-laptop-holonet-movies-catalog-claude"
        homelab_user = "chaldea-homelab-" + HOMELAB["profile"]
        self.assertEqual(x.state.users[username]["attributes"]["chaldea_holonet"], GRANT)
        self.assertEqual(sorted(x.state.groups[HOLONET["group"]]["users"]), sorted([username, homelab_user]))
        secret = self.secret("laptop", LAPTOP)
        self.assertEqual(secret.read_text(), x.state.tokens[username + "-g1"]["key"])
        self.assertEqual((secret.stat().st_mode & 0o777, secret.stat().st_nlink), (0o600, 1))
        self.assertEqual(secret.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotEqual(secret.read_bytes(), before[2])
        mapping = x.state.mappings[HOLONET["name"] + ": holonet.read"]["expression"]
        for name in (username, homelab_user):
            account = types.SimpleNamespace(**x.state.users[name])
            self.assertEqual(evaluate(mapping, account, HOLONET["group"]), GRANT | {"aud": HOLONET["resource"]})
            for slug, app in x.state.apps.items():
                self.assertIs(bool(x.state.passing(app, x.state.users[name])), slug == HOLONET["slug"])
        laptop_bytes = (secret.read_bytes(), secret.stat().st_ino, secret.stat().st_mtime_ns)

        # Changed content is never auto-accepted as the next baseline.
        self.refused(gate, original)
        self.assertEqual((self.private_reads(), x.writes(), x.key_reads()), ([], [], []))

        # Explicitly confirmed baseline: second convergence changes nothing.
        self.assertEqual(x.changed(self.ok(self.baseline())), 0)
        self.assertEqual(set(self.private_reads()), {expected_path})
        self.assertEqual(self.homelab_snapshot(), before)
        self.assertEqual((secret.read_bytes(), secret.stat().st_ino, secret.stat().st_mtime_ns), laptop_bytes)
        self.assertIn("budget", x.state.apps)

    def test_writer_offline_shape_and_refusals_precede_credential_loading(self):
        full = self.holonet | {"scope": "holonet.read holonet.curate holonet.home"}
        options = {"holonet_catalog_recommendations_enabled": True,
                   "holonet_catalog_recommendations_identity": WRITER,
                   "authentik_machine_services": {"holonet": full}}
        for interactive in (False, True):
            self.ok(check=True, **options, holonet_catalog_interactive_enabled=interactive)
            self.assertFalse(self.x.state.requests or (self.x.base / "sops-loaded").exists())
        for key, value in (("host", "homelab"), ("profile", "other"),
                           ("holonet_libraries", ["4"]), ("holonet_namespace", "other"),
                           ("holonet_history_users", GRANT["holonet_catalog_users"]),
                           ("holonet_actions", list(mi.HOLONET_WRITER_ACTIONS)),
                           ("holonet_target_users", []), ("holonet_target_users", ["*"]),
                           ("holonet_target_users", ["c" * 32] * 2),
                           ("generation", True), ("expires", ""), ("expires", "2026-02-31T00:00:00Z")):
            with self.subTest(key=key, value=value):
                chosen = options | {"holonet_catalog_recommendations_identity": WRITER | {key: value}}
                code, output = self.play(check=True, online=True, **chosen)
                self.assertNotEqual(code, 0, output)
                self.assertFalse(self.x.state.requests or (self.x.base / "sops-loaded").exists())
        # Exercise the source-derived scope, without the fixture extra-var override.
        self.ok(check=True, source_services=True, **options)
        self.assertFalse(self.x.state.requests or (self.x.base / "sops-loaded").exists())
        code, output = self.play(check=True, baseline_read=True, **options)
        self.assertNotEqual(code, 0, output)
        self.assertIn("The baseline read runs alone", output)
        self.assertFalse(self.x.state.requests or (self.x.base / "sops-loaded").exists())

    def test_writer_actual_add_repeat_and_inverse_preserve_every_reader(self):
        x = self.x
        self.use_patched = True
        code, output = x.run(COHORT + [INTERACTIVE], services={"holonet": self.holonet},
                             extra_vars={"authentik_machine_identities_blueprint_name": BLUEPRINT})
        self.assertEqual(code, 0, output)
        def readers():
            result = {}
            for identity in COHORT + [INTERACTIVE]:
                username = "chaldea-" + identity["host"] + "-" + identity["profile"]
                path = self.secret(identity["host"], identity)
                result[username] = (copy.deepcopy(x.state.users[username]),
                                    copy.deepcopy(x.state.tokens[username + "-g1"]),
                                    path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            return result
        before = readers()
        original = self.baseline()
        full = self.holonet | {"scope": "holonet.read holonet.curate holonet.home"}
        options = {"holonet_catalog_recommendations_enabled": True,
                   "holonet_catalog_recommendations_identity": WRITER,
                   "holonet_catalog_interactive_enabled": True,
                   "authentik_machine_services": {"holonet": full}}
        gate = "Require the operator's prior baseline before any private detail"
        self.refused(gate, **options)
        self.assertEqual((x.writes(), x.key_reads(), self.private_reads()), ([], [], []))
        self.assertEqual(readers(), before)
        self.ok(original, **options)
        self.assertEqual(readers(), before)
        username = "chaldea-laptop-holonet-recommendations"
        path = self.secret("laptop", WRITER)
        self.assertEqual(path.read_text(), x.state.tokens[username + "-g1"]["key"])
        self.assertEqual((path.stat().st_mode & 0o777, path.stat().st_nlink), (0o600, 1))
        self.assertEqual(x.state.users[username]["attributes"]["chaldea_holonet"],
                         {k: WRITER[k] for k in mi.HOLONET_FIELDS})
        secret_before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        self.assertEqual(x.changed(self.ok(self.baseline(), **options)), 0)
        self.assertEqual(readers(), before)
        self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns), secret_before)
        self.ok(self.baseline(), **(options | {
            "holonet_catalog_recommendations_identity": WRITER | {"state": "absent"}}))
        self.assertNotIn(username, x.state.users)
        self.assertNotIn(username + "-g1", x.state.tokens)
        self.assertFalse(path.exists())
        self.assertEqual(readers(), before)
        mapping = x.state.mappings[HOLONET["name"] + ": holonet.read"]["expression"]
        for identity in COHORT + [INTERACTIVE]:
            name = "chaldea-" + identity["host"] + "-" + identity["profile"]
            self.assertEqual(evaluate(mapping, types.SimpleNamespace(**x.state.users[name]), HOLONET["group"]),
                             GRANT | {"aud": HOLONET["resource"]})

    def test_opted_in_reader_preserves_both_existing_accounts_and_has_scoped_inverse(self):
        x = self.x
        self.use_patched = True
        code, output = x.run(COHORT, services={"holonet": self.holonet},
                             extra_vars={"authentik_machine_identities_blueprint_name": BLUEPRINT})
        self.assertEqual(code, 0, output)
        before = self.homelab_snapshot()
        limited_name = "chaldea-laptop-" + LAPTOP["profile"]
        limited_path = self.secret("laptop", LAPTOP)
        def limited():
            return (copy.deepcopy(x.state.users[limited_name]), copy.deepcopy(x.state.tokens[limited_name + "-g1"]),
                    limited_path.read_bytes(), limited_path.stat().st_ino, limited_path.stat().st_mtime_ns)
        old_limited = limited()
        original = self.baseline()
        opts = {"holonet_catalog_interactive_enabled": True}
        # Closed grant refuses before SOPS or any API request.
        for key, value in (("host", "homelab"), ("profile", "other"), ("holonet_libraries", ["4"]),
                           ("holonet_history_users", GRANT["holonet_catalog_users"]),
                           ("holonet_actions", ["catalog.read", "userdata.read"])):
            self.refused("Preserve the existing HoloNet", original, **opts,
                         holonet_catalog_interactive_identity=INTERACTIVE | {key: value})
            self.assertFalse((x.base / "sops-loaded").exists())
            self.assertEqual(x.state.requests, [])
        self.ok(original, **opts)
        self.assertEqual(self.homelab_snapshot(), before)
        self.assertEqual(limited(), old_limited)
        username = "chaldea-laptop-holonet-interactive"
        self.assertEqual(x.state.users[username]["attributes"]["chaldea_holonet"], GRANT)
        path = self.secret("laptop", INTERACTIVE)
        self.assertEqual(path.read_text(), x.state.tokens[username + "-g1"]["key"])
        self.assertEqual((path.stat().st_mode & 0o777, path.stat().st_nlink), (0o600, 1))
        new_bytes = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        self.assertEqual(x.changed(self.ok(self.baseline(), **opts)), 0)
        self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns), new_bytes)
        mapping = x.state.mappings[HOLONET["name"] + ": holonet.read"]["expression"]
        for account in spec(COHORT + [INTERACTIVE])["identities"]:
            self.assertEqual(evaluate(mapping, types.SimpleNamespace(**x.state.users[account["username"]]),
                                      HOLONET["group"]), GRANT | {"aud": HOLONET["resource"]})
        # Explicit new-only absence, still opted in, restores the original cohort.
        self.ok(self.baseline(), **opts, holonet_catalog_interactive_identity=INTERACTIVE | {"state": "absent"})
        self.assertNotIn(username, x.state.users)
        self.assertNotIn(username + "-g1", x.state.tokens)
        self.assertFalse(path.exists())
        self.assertEqual(self.homelab_snapshot(), before)
        self.assertEqual(limited(), old_limited)
        self.assertEqual(x.state.mappings[HOLONET["name"] + ": holonet.read"]["expression"],
                         spec(COHORT)["services"]["holonet"]["expression"])


if __name__ == "__main__":
    unittest.main()
