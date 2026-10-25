"""playbooks/authentik-ov-http-identities.yml: the declaration as plain data, and
the actual entrypoint played against the fake Authentik with every destination
a scratch directory. A sentinel stands in for SOPS and returns only the fake
admin token; nothing here reaches Authentik, a host or a secret store."""
import copy
import os
import subprocess
import types
import unittest

import yaml

import test_machine_identities as base

mi = base.mi
fake = base.fake
ROOT = base.ROOT
ENTRYPOINT = ROOT / "playbooks/authentik-ov-http-identities.yml"
VARS = ROOT / "playbooks/vars/authentik-ov-http-identities.yml"
BLUEPRINT = "homelab-infra ov http a63e197f identities"
NODE = "viking://resources/homeassistant-api/rest-api.md"
OV = {"state": "present", "slug": "ov-http-a63e197f", "name": "OV HTTP a63e197f machine access",
      "group": "ov-http-a63e197f-machine-identities", "client_id": "ov-http-a63e197f-machine",
      "resource": "https://ov.mcp.ryzhkov.dev/mcp", "signing_key": "8bbd6516-af33-4e64-a73b-caa39e692c29",
      "scope": "ov.read", "token_seconds": 300,
      "issuer": "https://auth.ryzhkov.dev/application/o/ov-http-a63e197f/",
      "jwks_url": "https://auth.ryzhkov.dev/application/o/ov-http-a63e197f/jwks/"}
CONTROLLER = {"host": "controller", "profile": "ov-http-a63e197f-reader", "service": "ov",
              "ov_namespaces": [NODE], "generation": 1, "expires": "2027-01-15T00:00:00Z", "state": "present"}
LAPTOP = {"host": "laptop", "profile": "ov-tooling-codex", "service": "ov",
          "ov_namespaces": [NODE], "generation": 1, "expires": "2027-01-15T00:00:00Z", "state": "present"}
# The laptop's separate ordinary interactive Codex identity (fleet helper
# `webmcp-interactive-headers ov laptop`), never the limited preset's account.
INTERACTIVE = {"host": "laptop", "profile": "ov-interactive-codex", "service": "ov",
               "ov_namespaces": [NODE], "generation": 1, "expires": "2027-01-15T00:00:00Z", "state": "present"}
COHORT = [CONTROLLER, LAPTOP, INTERACTIVE]
HOSTS = {"controller": {"ssh_user": "igor", "home": "/home/igor", "connection": "local"},
         "laptop": {"ssh_user": "igor", "home": "", "connection": "ssh"}}
BINDINGS = {"controller": {"hostname": "omarchy-pc", "uid": 1000},
            "laptop": {"hostname": "omarchy-macbook-air-igor", "uid": 1001}}


def spec(identities, services=None, destinations=None, hosts=HOSTS):
    return mi.machine_identity_spec(identities, base.ACCESS, hosts, 180, base.NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    {"ov": OV} if services is None else services, destinations)


def evaluate(expression, user, group):
    scope = {"user": user, "token": types.SimpleNamespace(scope=["ov.read"]),
             "ak_is_group_member": lambda u, name: name == group}
    exec("def mapping():\n" + "\n".join("    " + line for line in expression.splitlines()), scope)
    return scope["mapping"]()


class DeclarationTest(unittest.TestCase):
    def test_source_declaration_is_exact_isolated_and_bound(self):
        values = yaml.safe_load(VARS.read_text())
        self.assertEqual(values["authentik_machine_services"], {"ov": OV})
        self.assertEqual(values["authentik_machine_identities"], COHORT)
        self.assertEqual(values["authentik_machine_identity_destinations"], BINDINGS)
        self.assertEqual(values["authentik_machine_identities_blueprint_name"], BLUEPRINT)
        self.assertIs(values["authentik_machine_identity_existing_owned_only"], True)
        self.assertEqual(values["ov_http_identities_authentik_origin"], "https://auth.ryzhkov.dev")
        # No baseline, apply switch or inventory connection is populated in source.
        self.assertEqual(set(values), {
            "authentik_machine_identities_enabled", "authentik_machine_identity_existing_owned_only",
            "authentik_machine_identities_blueprint_name", "ov_http_identities_authentik_origin",
            "authentik_machine_services", "authentik_machine_identity_destinations",
            "authentik_machine_identities"})
        integrated = yaml.safe_load((ROOT / "playbooks/vars/authentik.yml").read_text())
        self.assertIs(integrated["authentik_machine_identities_enabled"], False)
        play = yaml.safe_load(ENTRYPOINT.read_text())
        self.assertEqual(len(play), 1)
        self.assertNotIn("roles", play[0])
        self.assertEqual(play[0]["vars_files"], ["vars/authentik-ov-http-identities.yml"])
        self.assertEqual([t["ansible.builtin.include_role"]["name"] for t in play[0]["tasks"]],
                         ["authentik_machine_identities"])

        s = spec(values["authentik_machine_identities"], destinations=values["authentik_machine_identity_destinations"])
        controller, laptop, interactive = s["identities"]
        self.assertEqual(controller["username"], "chaldea-controller-ov-http-a63e197f-reader")
        self.assertEqual(laptop["username"], "chaldea-laptop-ov-tooling-codex")
        self.assertEqual(laptop["secret_path"], "/home/igor/.config/chaldea/authentik/ov-tooling-codex.app-password")
        # A separate ordinary account and file: the interactive helper's fixed path.
        self.assertEqual(interactive["username"], "chaldea-laptop-ov-interactive-codex")
        self.assertEqual(interactive["secret_path"],
                         "/home/igor/.config/chaldea/authentik/ov-interactive-codex.app-password")
        self.assertNotEqual(interactive["token"], laptop["token"])
        self.assertEqual((controller["destination"], laptop["destination"], interactive["destination"]),
                         (BINDINGS["controller"], BINDINGS["laptop"], BINDINGS["laptop"]))
        self.assertNotEqual(controller["destination"], laptop["destination"])
        self.assertEqual(s["services"]["ov"]["account_grants"], {
            controller["username"]: {"ov_namespaces": [NODE]}, laptop["username"]: {"ov_namespaces": [NODE]},
            interactive["username"]: {"ov_namespaces": [NODE]}})
        # Ownership names exactly the existing objects plus the two laptop accounts and tokens.
        self.assertEqual(sorted(tuple(o) for o in s["objects"]), sorted([
            ("application", OV["slug"], "present"), ("group", OV["group"], "present"),
            ("provider", OV["name"], "present"), ("scopemapping", OV["name"] + ": ov.read", "present"),
            ("token", controller["token"], "present"), ("token", laptop["token"], "present"),
            ("token", interactive["token"], "present"),
            ("user", controller["username"], "present"), ("user", laptop["username"], "present"),
            ("user", interactive["username"], "present")]))

    def test_destination_bindings_are_exact(self):
        for bad in ({"controller": BINDINGS["controller"]},
                    BINDINGS | {"agent-a": {"hostname": "agent-a", "uid": 1000}},
                    BINDINGS | {"laptop": {"hostname": "omarchy-macbook-air-igor"}},
                    BINDINGS | {"laptop": {"hostname": "omarchy-macbook-air-igor", "uid": True}},
                    BINDINGS | {"laptop": {"hostname": "omarchy-macbook-air-igor", "uid": -1}},
                    BINDINGS | {"laptop": {"hostname": "omarchy-macbook-air-igor", "uid": "1001"}},
                    BINDINGS | {"laptop": {"hostname": "bad host;", "uid": 1001}},
                    BINDINGS | {"laptop": BINDINGS["laptop"] | {"home": "/root"}}, ["controller"]):
            with self.subTest(bad=bad), self.assertRaises(mi.AnsibleFilterError):
                spec(COHORT, destinations=bad)
        unbound = spec(COHORT)
        self.assertTrue(all("destination" not in i for i in unbound["identities"]))

    def test_existing_only_accepts_exactly_one_owned_application(self):
        s = spec([CONTROLLER])
        content = (s["marker"] + "\nversion: 1\nentries:\n"
                   "- model: authentik_providers_oauth2.oauth2provider\n"
                   "  id: ov-provider\n  identifiers:\n    name: " + OV["name"] +
                   "\n  attrs:\n    client_id: " + OV["client_id"] +
                   "\n- model: authentik_core.application\n  identifiers:\n    slug: " + OV["slug"] +
                   "\n  attrs:\n    provider: !KeyOf ov-provider\n")
        digest = mi.sha512(content.encode()).hexdigest()
        baseline = {"provider_pk": 42, "application_pk": "3621ae7a-e719-4cdf-9cb4-26bf110cfd78",
                    "content_sha512": digest}
        args = dict(
            instance={"content": content, "status": "successful", "enabled": True, "path": "",
                      "context": {}, "last_applied_hash": digest},
            baseline=baseline,
            inventory={"status": 200, "json": fake.page([
                {"pk": 42, "name": OV["name"], "meta_model_name": "authentik_providers_oauth2.oauth2provider"},
                {"pk": 102, "name": "Chaldea Jocasta tooling access",
                 "meta_model_name": "authentik_providers_oauth2.oauth2provider"}])},
            applications={"status": 200, "json": fake.page([
                {"pk": baseline["application_pk"], "slug": OV["slug"], "provider": 42}])},
            actor={"status": 200, "json": {"user": {"is_superuser": True}}})
        proof = mi.machine_identity_existing_provider(spec(COHORT), **args)
        self.assertEqual(proof, {"kind": "owned_provider", "id": OV["name"], "pk": 42,
                                 "client_id": OV["client_id"], "path": "/providers/oauth2/42/"})
        queries = mi.machine_identity_queries(spec(COHORT), mi.machine_identity_owned(content), proof)
        self.assertEqual([q["path"] for q in queries if q["path"].startswith("/providers/")],
                         ["/providers/oauth2/42/"])
        both = spec([CONTROLLER, base.identity(host="laptop")], {"jocasta": base.ACCESS, "ov": OV})
        for changed in (both, spec([CONTROLLER | {"state": "absent"}], {"ov": OV | {"state": "absent"}})):
            with self.assertRaisesRegex(mi.AnsibleFilterError, "exactly one unchanged present application"):
                mi.machine_identity_existing_provider(changed, **args)


class EntrypointTest(unittest.TestCase):
    def setUp(self):
        x = self.x = base.Scratch(self)
        self.origin = f"https://127.0.0.1:{x.server.server_port}"
        self.ov = OV | {"issuer": self.origin + "/application/o/ov-http-a63e197f/",
                        "jwks_url": self.origin + "/application/o/ov-http-a63e197f/jwks/"}
        x.state.keypairs[OV["signing_key"]] = dict(x.state.keypairs[fake.KEYPAIR], pk=OV["signing_key"])
        x.homes = {}
        hosts = {}
        for host in ("controller", "laptop"):
            home = x.base / host
            home.mkdir(mode=0o755)
            x.homes[host] = home
            hosts[host] = {"ansible_connection": "local", "ssh_user": base.USER,
                           "authentik_machine_identity_home": str(home),
                           "ansible_python_interpreter": base.sys.executable}
        x.inventory.write_text(yaml.safe_dump({"all": {"hosts": hosts}}))
        # The fixture's two destinations are one machine; bind both to it.
        here = {"hostname": os.uname().nodename, "uid": os.getuid()}
        self.bindings = {"controller": dict(here), "laptop": dict(here)}
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

    def play(self, baseline=None, apply=True, check=False, online=False, **overrides):
        x = self.x
        extra = {"authentik_machine_services": {"ov": self.ov},
                 "ov_http_identities_authentik_origin": self.origin,
                 "authentik_machine_identity_destinations": self.bindings,
                 "authentik_machine_identity_existing_provider_baseline": baseline or {}} | overrides
        extra_path = x.base / "entrypoint-extra.yml"
        extra_path.write_text(yaml.safe_dump(extra))
        (x.base / "sops-loaded").unlink(missing_ok=True)
        x.state.requests.clear()
        env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"), ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCAL_TEMP=str(x.base / "ansible-local"),
                   ANSIBLE_REMOTE_TEMP=str(x.base / "ansible-remote"),
                   ANSIBLE_COLLECTIONS_PATH=str(x.base / "collections"), SSL_CERT_FILE=str(x.cert))
        argv = ["ansible-playbook", "-i", str(x.inventory), str(ENTRYPOINT), "--diff", "-e", "@" + str(extra_path)]
        argv += ["--check"] if check else []
        argv += ["-e", "ov_http_identities_apply=true"] if apply and not check else []
        argv += ["-e", "ov_http_identities_online_check=true"] if online else []
        result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        output = result.stdout + result.stderr
        self.assertNotIn(fake.ADMIN, output)
        self.assertNotIn(fake.KEY_PREFIX, output)
        return result.returncode, output

    def ok(self, *args, **kwargs):
        code, output = self.play(*args, **kwargs)
        self.assertEqual(code, 0, output)
        return output

    def refused(self, fragment, *args, **kwargs):
        code, output = self.play(*args, **kwargs)
        self.assertNotEqual(code, 0, output)
        self.assertIn(fragment, output)
        return output

    def secret(self, host, item):
        return self.x.secret(host, item["profile"])

    def controller_snapshot(self):
        user = "chaldea-controller-" + CONTROLLER["profile"]
        path = self.secret("controller", CONTROLLER)
        return (copy.deepcopy(self.x.state.users[user]), copy.deepcopy(self.x.state.tokens[user + "-g1"]),
                path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_mode & 0o777)

    def private_reads(self):
        return [p for _, p, _ in self.x.state.requests if p.startswith("/api/v3/providers/oauth2/")]

    def baseline(self):
        x = self.x
        content = next(iter(x.state.blueprints.values()))["content"]
        return {"provider_pk": x.state.providers[OV["name"]]["pk"], "application_pk": x.state.apps[OV["slug"]]["pk"],
                "content_sha512": mi.sha512(content.encode()).hexdigest()}

    def untouched(self, before):
        self.assertEqual((self.x.writes(), self.x.key_reads()), ([], []))
        self.assertEqual(self.controller_snapshot(), before)
        self.assertFalse(self.secret("laptop", LAPTOP).exists())
        self.assertFalse(self.secret("laptop", INTERACTIVE).exists())

    def test_laptop_addition_preserves_controller_custody_and_converges(self):
        x = self.x
        # Fixture-only stand-in for the existing live canary enrollment.
        code, output = x.run([CONTROLLER], services={"ov": self.ov},
                             extra_vars={"authentik_machine_identities_blueprint_name": BLUEPRINT})
        self.assertEqual(code, 0, output)
        before = self.controller_snapshot()
        original = self.baseline()
        x.state.providers["Foreign"] = {"pk": 999, "name": "Foreign", "client_id": "foreign-client",
                                       "meta_model_name": "authentik_providers_oauth2.oauth2provider",
                                       "client_secret": "FOREIGN-SECRET-SENTINEL"}

        # Off unless explicitly requested; the offline plan touches nothing.
        self.ok(original, check=True)
        self.assertFalse(x.state.requests or (x.base / "sops-loaded").exists())
        self.refused("ov_http_identities_apply=true", original, apply=False)
        self.assertFalse(x.state.requests or (x.base / "sops-loaded").exists())

        # The existing application, controller identity and bindings cannot be
        # changed or dropped through extra vars.
        guard = "keeps its controller identity present and unchanged"
        wider = [NODE, "viking://resources/other"]
        for overrides in (
                {"authentik_machine_identities": [CONTROLLER | {"state": "absent"}, LAPTOP, INTERACTIVE]},
                {"authentik_machine_identities": [LAPTOP, INTERACTIVE]},
                {"authentik_machine_identities": [CONTROLLER | {"generation": 2}, LAPTOP, INTERACTIVE]},
                {"authentik_machine_identities": [CONTROLLER | {"ov_namespaces": ["viking://resources"]}, LAPTOP,
                                                  INTERACTIVE]},
                # The limited laptop identity is preserved exactly as well.
                {"authentik_machine_identities": [CONTROLLER, INTERACTIVE]},
                {"authentik_machine_identities": [CONTROLLER, LAPTOP | {"state": "absent"}, INTERACTIVE]},
                {"authentik_machine_identities": [CONTROLLER, LAPTOP | {"generation": 2}, INTERACTIVE]},
                {"authentik_machine_identities": [CONTROLLER, LAPTOP | {"ov_namespaces": wider}, INTERACTIVE]},
                # The interactive identity cannot widen, move or multiply.
                {"authentik_machine_identities": [CONTROLLER, LAPTOP]},
                {"authentik_machine_identities": [CONTROLLER, LAPTOP, INTERACTIVE | {"ov_namespaces": wider}]},
                {"authentik_machine_identities": [CONTROLLER, LAPTOP,
                                                  INTERACTIVE | {"ov_namespaces": ["viking://resources"]}]},
                {"authentik_machine_identities": [CONTROLLER, LAPTOP, INTERACTIVE | {"host": "controller"}]},
                {"authentik_machine_identities": [CONTROLLER, LAPTOP, INTERACTIVE | {"profile": "ov-tooling-codex"}]},
                {"authentik_machine_identities": [CONTROLLER, LAPTOP, INTERACTIVE,
                                                  INTERACTIVE | {"profile": "ov-interactive-claude"}]},
                {"authentik_machine_identities": [CONTROLLER, INTERACTIVE, LAPTOP]},
                {"authentik_machine_services": {"ov": self.ov | {"state": "absent"}}},
                {"authentik_machine_services": {"ov": self.ov, "jocasta": base.ACCESS}},
                {"authentik_machine_identity_existing_owned_only": False},
                {"authentik_machine_identity_destinations": {}}):
            with self.subTest(overrides=list(overrides)):
                self.refused(guard, original, **overrides)
                self.assertFalse(x.state.requests or (x.base / "sops-loaded").exists())
        self.assertEqual(self.controller_snapshot(), before)

        # No retained baseline: no private detail, API write or key read. The
        # gate task is no_log, so its own message is hidden; name the task.
        gate = "Require the operator's prior baseline before any private detail"
        self.refused(gate)
        self.assertEqual(self.private_reads(), [])
        self.untouched(before)

        # An inventory that maps a host onto another machine or account stops
        # before Authentik changes or any app password is read.
        for host, change in (("controller", {"uid": os.getuid() + 1}), ("laptop", {"hostname": "omarchy-pc"})):
            with self.subTest(remapped=host):
                bindings = copy.deepcopy(self.bindings)
                bindings[host].update(change)
                self.refused("does not reach its bound destination", original,
                             authentik_machine_identity_destinations=bindings)
                self.untouched(before)

        self.ok(original)
        expected_path = "/api/v3/providers/oauth2/" + str(original["provider_pk"]) + "/"
        self.assertEqual(set(self.private_reads()), {expected_path})
        self.assertEqual(self.controller_snapshot(), before)
        self.assertEqual((x.state.providers[OV["name"]]["pk"], x.state.apps[OV["slug"]]["pk"]),
                         (original["provider_pk"], original["application_pk"]))
        username = "chaldea-laptop-" + LAPTOP["profile"]
        interactive = "chaldea-laptop-" + INTERACTIVE["profile"]
        self.assertEqual(interactive, "chaldea-laptop-ov-interactive-codex")
        user = x.state.users[username]
        self.assertEqual(user["attributes"]["chaldea_ov"], {"ov_namespaces": [NODE]})
        self.assertEqual(x.state.users[interactive]["attributes"]["chaldea_ov"], {"ov_namespaces": [NODE]})
        self.assertEqual(sorted(x.state.groups[OV["group"]]["users"]),
                         sorted([username, interactive, "chaldea-controller-" + CONTROLLER["profile"]]))
        secrets = {}
        for name, item in ((username, LAPTOP), (interactive, INTERACTIVE)):
            secret = self.secret("laptop", item)
            self.assertEqual(secret.read_text(), x.state.tokens[name + "-g1"]["key"])
            self.assertEqual((secret.stat().st_mode & 0o777, secret.stat().st_nlink), (0o600, 1))
            self.assertEqual(secret.parent.stat().st_mode & 0o777, 0o700)
            self.assertNotEqual(secret.read_bytes(), before[2])
            secrets[name] = secret
        # Separate accounts hold separate app passwords in separate files.
        self.assertNotEqual(secrets[username].read_bytes(), secrets[interactive].read_bytes())
        self.assertEqual(secrets[interactive].name, "ov-interactive-codex.app-password")
        mapping = x.state.mappings[OV["name"] + ": ov.read"]["expression"]
        for name in (username, interactive, "chaldea-controller-" + CONTROLLER["profile"]):
            account = types.SimpleNamespace(**x.state.users[name])
            self.assertEqual(evaluate(mapping, account, OV["group"]), {"aud": OV["resource"], "ov_namespaces": [NODE]})
        for name in (username, interactive):
            widened = copy.deepcopy(x.state.users[name])
            widened["attributes"]["chaldea_ov"] = {"ov_namespaces": [NODE, "viking://resources/other"]}
            self.assertEqual(evaluate(mapping, types.SimpleNamespace(**widened), OV["group"]), {})
            for slug, app in x.state.apps.items():
                self.assertIs(bool(x.state.passing(app, x.state.users[name])), slug == OV["slug"])
        laptop_bytes = [(s.read_bytes(), s.stat().st_ino, s.stat().st_mtime_ns) for s in secrets.values()]

        # Changed content is never auto-accepted as the next baseline.
        self.refused(gate, original)
        self.assertEqual((self.private_reads(), x.writes(), x.key_reads()), ([], [], []))

        # Explicitly confirmed baseline: second convergence changes nothing.
        confirmed = self.baseline()
        self.assertEqual(x.changed(self.ok(confirmed)), 0)
        self.assertEqual(set(self.private_reads()), {expected_path})
        self.assertEqual(self.controller_snapshot(), before)
        self.assertEqual([(s.read_bytes(), s.stat().st_ino, s.stat().st_mtime_ns) for s in secrets.values()],
                         laptop_bytes)
        self.assertIn("budget", x.state.apps)


if __name__ == "__main__":
    unittest.main()
