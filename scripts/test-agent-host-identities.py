#!/usr/bin/env python3
"""Recording API and filesystem tests; never uses Authentik or a real host HOME."""
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
assert os.environ["HOME"].startswith(("/home/job", str(ROOT / ".cache"), "/root/.cache", str(ROOT / "authentik-identities-")))
assert os.environ["TMPDIR"].startswith(os.environ["HOME"])

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

c = load("ansible.module_utils.agent_host_contract", "module_utils/agent_host_contract.py")
e = load("host_enrollment", "roles/authentik_machine_identities/library/authentik_agent_hosts.py")
d = load("host_delivery", "roles/authentik_machine_identities/library/authentik_host_credential.py")
OWNER = "00000000-0000-4000-8000-000000000000"
NOW = dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc)

def item(host="laptop", revoked=False):
    return {"host": host, "generation": 1, "expires": "2026-12-12T00:00:00Z",
            "revoked": revoked, "destination": {"hostname": host, "uid": 1000}}

class API:
    def __init__(self):
        self.data = {k: [] for k in e.PATHS}
        self.data["group"] = [{"pk": c.GROUP_IDS[s], "name": n, "roles": [], "parents": [], "is_superuser": False}
                              for s, n in c.GROUPS.items()]
        self.calls = []
        self.n = 100
    def require_superuser(self):
        pass
    def rows(self, kind):
        return copy.deepcopy(self.data[kind])
    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path.startswith("/managed/blueprints/"):
            return copy.deepcopy(self.blueprint)
        kind = next(k for k, prefix in e.PATHS.items() if path.startswith(prefix))
        if method == "POST":
            self.n += 1
            result = dict(body, pk=self.n)
            if kind == "user":
                result.update(roles=[], is_superuser=False)
            self.data[kind].append(result)
            return copy.deepcopy(result)
        pk = path.rstrip("/").rsplit("/", 1)[1]
        result = next(x for x in self.data[kind] if str(x["pk"]) == pk)
        result.update(body)
        return copy.deepcopy(result)

def function(source, **context):
    namespace = {}
    exec("def run():\n" + "".join("    " + line + "\n" for line in source.splitlines()), context, namespace)
    return namespace["run"]()

class Contract(unittest.TestCase):
    def test_exact_matrix(self):
        self.assertEqual(set(c.MATRIX), {"laptop", "omarchy-pc", "agent-a", "agent-b", "homelab"})
        expected = {"laptop": {"jocasta", "ov", "holonet", "shizune"},
                    "omarchy-pc": {"jocasta", "ov", "shizune"}, "agent-a": {"jocasta"},
                    "agent-b": {"jocasta"}, "homelab": {"holonet", "ha", "homelab"}}
        for host, services in expected.items():
            self.assertEqual(set(c.MATRIX[host]), services)
        ov = c.MATRIX["laptop"]["ov"]
        self.assertEqual(ov, {"ov_namespaces": ["viking://resources"],
            "ov_actions": ["read", "write", "remove", "maintain"], "ov_personal": {
                "account": "default", "user": "default", "actions": [
                "memory.read", "memory.write", "session.read", "session.create", "session.append"]}})
        self.assertEqual(c.MATRIX["omarchy-pc"]["ov"], ov)
        self.assertEqual(c.MATRIX["homelab"]["ha"], {"ha_entities": ["sensor.zigbee2mqtt_bridge_version"],
                                                   "ha_actions": ["state.read"]})
        self.assertEqual(c.MATRIX["homelab"]["homelab"], {"homelab_hosts": ["homelab"], "homelab_services": []})
        p = c.MATRIX["laptop"]["holonet"]["purposes"]
        self.assertEqual(set(p), {"catalog", "writer", "janitor", "torrents", "seed_join"})
        self.assertEqual(p["catalog"]["claims"]["holonet_libraries"], ["3"])
        self.assertEqual(p["catalog"]["claims"]["holonet_actions"], ["catalog.read"])
        self.assertEqual(p["catalog"]["claims"]["holonet_target_users"], [])
        self.assertEqual(p["catalog"]["claims"]["holonet_history_users"], [])
        w = p["writer"]["claims"]
        self.assertEqual(w["holonet_libraries"], ["3", "615", "1132"])
        self.assertEqual(w["holonet_history_users"], w["holonet_target_users"])
        self.assertEqual(w["holonet_target_users"], [
            "545014d1c9d1416b8e0b5a445d6ff9ec", "b8f4f6de49b443f1948cf0118679e863",
            "db2f3bfb1f84438ebcde94f228183011", "313e39bb4d8c4b92b81d4b170c3f1b8e",
            "c06c7d2c3e224d8ba2e466efa5bcb97b", "22703968ab0b48eebb4faf7c1d16262c",
            "e05efa68303043b893b0f5c2e200ccd7"])
        self.assertEqual(w["holonet_actions"], ["catalog.read", "playlist.read", "playlist.manage",
            "object.share", "home.read", "home.manage", "object.delete", "history.read"])
        for purpose, actions in [("janitor", ["janitor.importers.read", "janitor.custody.read"]),
                                  ("torrents", ["janitor.torrents.read"]), ("seed_join", ["janitor.seed_join.read"])]:
            claims = p[purpose]["claims"]
            self.assertEqual(claims["holonet_actions"], actions)
            self.assertEqual(claims["holonet_libraries"], ["3"])
            self.assertEqual(claims["holonet_namespace"], "managed-janitor")
            self.assertEqual(claims["holonet_catalog_users"], [])
        self.assertEqual(c.MATRIX["homelab"]["holonet"]["purposes"], {"catalog": p["catalog"]})

    def test_declaration_closed(self):
        c.declaration([item()], OWNER, NOW)
        for value in [dict(item(), host="normandy"), dict(item(), revoked="false"),
                      dict(item(), services={"ha": {}}), dict(item(), generation=True),
                      dict(item(), expires="2027-11-12T00:00:00Z")]:
            with self.assertRaises(Exception):
                c.declaration([value], OWNER, NOW)

    def test_purpose_and_resource_isolation(self):
        for host, services in c.MATRIX.items():
            for service, grant in services.items():
                if service == "holonet":
                    for p in grant["purposes"].values():
                        self.assertEqual(c.claims(service, grant, p["scopes"]), p["claims"])
                    for bad in [["holonet.read", "holonet.home"], ["holonet.read", "holonet.janitor", "holonet.curate"],
                                ["holonet.read", "holonet.acquire"], ["holonet.read", "ov.read"]]:
                        self.assertEqual(c.claims(service, grant, bad), {})
                else:
                    self.assertEqual(c.claims(service, grant, ["foreign.read"]), {})
        self.assertEqual(c.claims("ov", c.MATRIX["laptop"]["ov"], ["ov.read"]), {
            "ov_namespaces": ["viking://resources"], "ov_actions": ["read"]})

    def test_emitted_mapping_elects_once_and_falls_back_only_for_old_accounts(self):
        user = {"pk": 42, "username": "chaldea-laptop", "groups": [c.GROUP_IDS[s] for s in ("ov", "holonet", "shizune")],
                "attributes": c.attributes("laptop", OWNER)}
        # Execute the actual generated expression, replacing only the ORM guard
        # with an instrumented predicate. Separate runtime guard tests below.
        for service in ("ov", "holonet", "shizune", "jocasta"):
            grant = c.MATRIX["laptop"][service]
            requests = ([p["scopes"] for p in grant["purposes"].values()] if service == "holonet" else
                        [["ov.read", "ov.write"]] if service == "ov" else
                        [["shizune.use"]] if service == "shizune" else [["jocasta.read", "jocasta.write"]])
            for request in requests:
                output = []
                for scope in request:
                    expression = c.scope_expression(service, scope, [user], "return {'old': True}")
                    expression = expression.replace(c.RUNTIME_GUARD, "def host_allowed(u, e):\n    return True\n")
                    result = function(expression, user=NS(**user, path=c.PATH), token=NS(scope=request),
                                      provider=NS(client_id="jocasta-managed-interactive", application=NS(slug="jocasta-managed-interactive")))
                    if result:
                        output.append(result)
                self.assertEqual(len(output), 1)
                self.assertEqual(output[0]["aud"], c.RESOURCES[service])
                self.assertFalse(any(k.startswith(("ha_", "homelab_")) for k in output[0]))
        expression = c.scope_expression("ha", "ha.read", [user], "return {'old': True}")
        expression = expression.replace(c.RUNTIME_GUARD, "def host_allowed(u, e):\n    return True\n")
        self.assertEqual(function(expression, user=NS(**user, path=c.PATH), token=NS(scope=["ha.read"])), {})
        self.assertEqual(function(expression, user=NS(username="old", path="legacy")), {"old": True})

    def test_managed_role_allowance(self):
        user = {"pk": 42, "roles": ["r"]}
        role = {"name": "ak-managed-role--user-42", "managed": "ak-managed-role--user-42"}
        perm = {"codename": "view_token_key", "app_label": "authentik_core", "model": "token", "object_pk": "own"}
        tokens = [{"pk": "own", "user": 42}, {"pk": "victim", "user": 43}]
        self.assertTrue(c.managed_role_ok(user, {"r": role}, {"r": [perm]}, tokens))
        for changed in [dict(perm, object_pk=None), dict(perm, object_pk="victim"),
                        dict(perm, codename="change_token"), dict(perm, app_label="other")]:
            self.assertFalse(c.managed_role_ok(user, {"r": role}, {"r": [changed]}, tokens))
        self.assertFalse(c.managed_role_ok(user, {"r": dict(role, managed=None)}, {"r": [perm]}, tokens))
        self.assertFalse(c.managed_role_ok(dict(user, roles=["r", "x"]), {"r": role}, {"r": [perm]}, tokens))


    def test_runtime_guard_checks_permissions_owner_and_revocation(self):
        from unittest.mock import patch
        from types import ModuleType
        class Query(list):
            def exists(self):
                return bool(self)
            def select_related(self, *args):
                return self
            def values_list(self, *args, **kwargs):
                return list(self)
        state = {"global": [], "object": []}
        class Global:
            objects = NS(filter=lambda **kw: Query(state["global"]))
        class Object:
            objects = NS(filter=lambda **kw: Query(state["object"]))
        class Token:
            objects = NS(filter=lambda **kw: Query([1] if kw == {"pk": "own", "user": user} else []))
        gm, am = ModuleType("guardian.models"), ModuleType("authentik.core.models")
        gm.RoleModelPermission, gm.RoleObjectPermission = Global, Object
        am.Token = Token
        guard = {}
        with patch.dict(sys.modules, {"guardian.models": gm, "authentik.core.models": am}):
            exec(c.RUNTIME_GUARD, guard)
            expected = {"attributes": c.attributes("agent-a", OWNER), "groups": []}
            user = NS(pk=42, is_active=True, type="service_account", is_superuser=False,
                path=c.PATH, attributes=copy.deepcopy(expected["attributes"]), all_groups=lambda: Query(),
                all_roles=lambda: Query())
            check = lambda: guard["host_allowed"](user, expected)
            self.assertTrue(check())
            user.is_active = False
            self.assertFalse(check())
            user.is_active = True
            user.attributes["revoked"] = True
            self.assertFalse(check())
            user.attributes = copy.deepcopy(expected["attributes"])
            role = NS(name="ak-managed-role--user-42", managed="ak-managed-role--user-42",
                      users=Query([42]), groups=Query())
            user.all_roles = lambda: Query([role])
            permission = NS(permission=NS(codename="view_token_key",
                content_type=NS(app_label="authentik_core", model="token")), object_pk="own")
            state["object"] = [permission]
            self.assertTrue(check())
            permission.object_pk = "victim"
            self.assertFalse(check())
            permission.object_pk = "own"
            state["global"] = [1]
            self.assertFalse(check())
            state["global"] = []
            role.users.append(43)
            self.assertFalse(check())

class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        os.chmod(self.temp.name, 0o700)
        self.fd = os.open(self.temp.name, os.O_RDONLY | os.O_DIRECTORY)
        self.api = API()
    def tearDown(self):
        os.close(self.fd)
        self.temp.cleanup()
    def enrollment(self, check=False):
        return e.HostEnrollment(self.api, self.fd, OWNER, check)

    def test_check_then_apply_then_no_change(self):
        x = self.enrollment(True)
        x.users([item()])
        self.assertTrue(x.changed)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(os.listdir(self.fd), [])
        x = self.enrollment()
        users, delivery = x.users([item()])
        self.assertEqual(users[0]["username"], "chaldea-laptop")
        self.assertEqual(delivery[0]["token"], "chaldea-laptop-g1")
        self.assertNotIn("roles", next(b for method, p, b in self.api.calls if method == "POST" and p == "/core/users/"))
        before = copy.deepcopy(self.api.calls)
        x = self.enrollment(True)
        x.users([item()])
        self.assertFalse(x.changed)
        self.assertEqual(before, self.api.calls)

    def test_admin_revocation_and_stale_inventory(self):
        self.enrollment().users([item()])
        self.api.data["user"][0]["is_active"] = False
        x = self.enrollment()
        users, delivery = x.users([item()])
        self.assertEqual(users, [])
        self.assertTrue(delivery[0]["revoked"])
        self.api.data["user"][0]["is_active"] = True  # latch still wins
        self.enrollment().users([item()])
        self.assertFalse(self.api.data["user"][0]["is_active"])
        self.assertFalse(any(body and body.get("is_active") is True for method, p, body in self.api.calls if method == "PATCH"))

    def test_inventory_revocation_prevents_creation(self):
        self.enrollment().users([item(revoked=True)])
        self.enrollment().users([item()])
        self.assertEqual(self.api.calls, [])

    def test_rotation_does_not_recreate_deleted_bootstrap(self):
        self.enrollment().users([item()])
        self.api.data["token"].clear()
        before = len(self.api.calls)
        self.enrollment().users([item()])
        self.assertEqual(len(self.api.calls), before)

    def test_name_collision_and_pending_refuse(self):
        self.api.data["user"] = [{"pk": 200, "username": "chaldea-laptop"}]
        with self.assertRaises(Exception):
            self.enrollment().users([item()])
        e.write_private(self.fd, "hosts.json", {"version": 1, "owner": OWNER, "pending": {"method": "POST"}})
        with self.assertRaises(Exception):
            self.enrollment()

    def test_generation_guard_and_tombstone(self):
        key = "SYNTHETIC-bootstrap-password"
        self.assertEqual(d.credential(self.fd, "laptop", 1000, 1, False, check=True), (True, True))
        self.assertEqual(os.listdir(self.fd), [])
        d.credential(self.fd, "laptop", 1000, 1, False, key)
        self.assertEqual(d.credential(self.fd, "laptop", 1000, 1, False, key), (False, False))
        # Simulate the future rotation writer using the same locked marker contract.
        marker = json.loads(d.read_file(self.fd, "host.generation.json"))
        newer = b"SYNTHETIC-rotated-password"
        marker.update(generation=2, sha256=hashlib.sha256(newer).hexdigest())
        d.atomic(self.fd, "host.app-password", newer)
        d.atomic(self.fd, "host.generation.json", json.dumps(marker).encode())
        self.assertEqual(d.credential(self.fd, "laptop", 1000, 1, False, key), (False, False))
        self.assertEqual(d.read_file(self.fd, "host.app-password"), newer)
        d.credential(self.fd, "laptop", 1000, 1, True)
        self.assertIsNone(d.read_file(self.fd, "host.app-password"))
        self.assertEqual(d.credential(self.fd, "laptop", 1000, 1, False, key), (False, False))
        self.assertIsNone(d.read_file(self.fd, "host.app-password"))

    def test_pending_or_foreign_marker_and_links_refuse(self):
        d.atomic(self.fd, "host.generation.json", json.dumps({"host": "laptop", "uid": 1000,
            "generation": 1, "status": "pending"}).encode())
        with self.assertRaises(Exception):
            d.credential(self.fd, "laptop", 1000, 1, False, "SYNTHETIC-password")
        os.unlink("host.generation.json", dir_fd=self.fd)
        os.symlink("foreign", "host.app-password", dir_fd=self.fd)
        with self.assertRaises(Exception):
            d.credential(self.fd, "laptop", 1000, 1, False)


    def test_mapping_baselines_and_check_accuracy(self):
        self.enrollment().users([item()])
        scopes = {"jocasta": ["jocasta.read", "jocasta.write"], "ha": ["ha.read"],
            "homelab": ["homelab.read"], "shizune": ["shizune.use"],
            "ov": ["ov." + a for a in c.MATRIX["laptop"]["ov"]["ov_actions"]] +
                  ["ov.personal." + a for a in c.MATRIX["laptop"]["ov"]["ov_personal"]["actions"]],
            "holonet": sorted({s for p in c.MATRIX["laptop"]["holonet"]["purposes"].values() for s in p["scopes"]})}
        baselines = []
        prior = {"version": 1, "pending": None, "objects": {}}
        objects = [["group", n, "present"] for n in c.GROUPS.values()]
        prior_dir = self.temp.name + "/prior"
        os.mkdir(prior_dir, 0o700)
        for service, values in scopes.items():
            for scope in values:
                pk = str(len(baselines))
                original = "return {}"
                self.api.data["mapping"].append({"pk": pk, "name": scope, "scope_name": scope, "expression": original})
                objects.append(["scopemapping", scope, "present"])
                if service == "jocasta":
                    prior["objects"][scope] = {"kind": "mapping", "pk": pk,
                                              "body": self.api.data["mapping"][-1]}
                baselines.append({"kind": "mapping", "pk": pk, "service": service, "scope": scope,
                    "sha256": hashlib.sha256(original.encode()).hexdigest(), "ownership": {"receipt_dir": prior_dir} if service == "jocasta" else {}})
        self.api.data["policy"].append({"pk": "policy", "expression": "return False"})
        baselines.append({"kind": "policy", "pk": "policy", "service": "jocasta", "scope": "",
            "sha256": hashlib.sha256(b"return False").hexdigest(), "ownership": {"receipt_dir": prior_dir}})
        prior["objects"]["policy"] = {"kind": "policy", "pk": "policy", "body": self.api.data["policy"][0]}
        prior_fd = os.open(prior_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            e.write_private(prior_fd, "receipt.json", prior)
        finally:
            os.close(prior_fd)
        content = "# homelab-infra-owned: " + json.dumps({"version": 1, "objects": objects})
        self.api.blueprint = {"status": "successful", "content": content}
        for baseline in baselines:
            if baseline["service"] != "jocasta":
                baseline["ownership"] = {"blueprint_pk": OWNER,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
        x = self.enrollment(True)
        users, _ = x.users([item()])
        calls = len(self.api.calls)
        x.expressions(users, baselines)
        self.assertTrue(x.changed)
        self.assertEqual(len(self.api.calls), calls)
        x = self.enrollment()
        users, _ = x.users([item()])
        x.expressions(users, baselines)
        x = self.enrollment(True)
        users, _ = x.users([item()])
        x.expressions(users, baselines)
        self.assertFalse(x.changed)
        self.api.data["mapping"][0]["expression"] = "return {'foreign': True}"
        with self.assertRaises(Exception):
            self.enrollment(True).expressions(users, baselines)

    def test_mid_commit_crash_leaves_explicit_pending(self):
        atomic = d.atomic
        calls = []
        def fail(fd, name, body):
            calls.append(name)
            if name == "host.app-password":
                raise OSError("synthetic crash")
            atomic(fd, name, body)
        d.atomic = fail
        try:
            with self.assertRaises(OSError):
                d.credential(self.fd, "laptop", 1000, 1, False, "SYNTHETIC-bootstrap-password")
        finally:
            d.atomic = atomic
        self.assertEqual(json.loads(d.read_file(self.fd, "host.generation.json"))["status"], "pending")
        with self.assertRaises(Exception):
            d.credential(self.fd, "laptop", 1000, 1, False, "SYNTHETIC-bootstrap-password")

if __name__ == "__main__":
    unittest.main()
