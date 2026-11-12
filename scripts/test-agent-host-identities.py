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

load("ansible.module_utils.authentik_enrollment", "module_utils/authentik_enrollment.py")
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
                result.update(roles=[], is_superuser=False, groups=list(reversed(body["groups"])))
            if kind == "token":
                result["expires"] = body["expires"].replace("Z", "+00:00")
            self.data[kind].append(result)
            return copy.deepcopy(result)
        pk = path.rstrip("/").rsplit("/", 1)[1]
        result = next(x for x in self.data[kind] if str(x["pk"]) == pk or x.get("identifier") == pk)
        if method == "DELETE":
            self.data[kind].remove(result)
            return {}
        if body is not None:
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

    def test_expiry_window_only_before_bootstrap(self):
        stale = dict(item(), expires="2026-01-01T00:00:00Z")
        with self.assertRaises(Exception):
            c.declaration([stale], OWNER, NOW)
        c.declaration([stale], OWNER, NOW, {"laptop": {"generation": 1}})

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

    def test_api_scope_emits_no_resource_grants(self):
        for host, services in c.MATRIX.items():
            for service, grant in services.items():
                self.assertEqual(c.claims(service, grant, ["goauthentik.io/api"]), {})
                self.assertEqual(c.claims(service, grant, ["goauthentik.io/api", "jocasta.read"]), {})

    def test_managed_role_allowance(self):
        user = {"pk": 42, "roles": ["r"]}
        role = {"name": "ak-managed-role--user-42"}
        perm = {"codename": "view_token_key", "app_label": "authentik_core", "model": "token", "object_pk": "own"}
        tokens = [{"pk": "own", "user": 42}, {"pk": "victim", "user": 43}]
        self.assertTrue(c.managed_role_ok(user, {"r": role}, {"r": [perm]}, tokens))
        for changed in [dict(perm, object_pk=None), dict(perm, object_pk="victim"),
                        dict(perm, codename="change_token"), dict(perm, app_label="other")]:
            self.assertFalse(c.managed_role_ok(user, {"r": role}, {"r": [changed]}, tokens))
        self.assertFalse(c.managed_role_ok(user, {"r": dict(role, name="ak-managed-role--user-43")}, {"r": [perm]}, tokens))
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


    def test_ansible_module_packaging_without_network_or_host_writes(self):
        import subprocess
        library = str(ROOT / "roles/authentik_machine_identities/library")
        cases = [
            ("authentik_agent_hosts", {"hosts": [], "owner": OWNER, "receipt_dir": os.environ["HOME"],
                "baselines": [], "api_token": "SYNTHETIC-invalid-token"}, "Host enrollment refused"),
            ("authentik_host_credential", {"host": "laptop", "home": "/home/igor",
                "hostname": "SYNTHETIC-NOT-THIS-HOST", "uid": 999999, "generation": 1,
                "revoked": False}, "Host delivery refused"),
        ]
        for module, args, message in cases:
            result = subprocess.run(["ansible", "localhost", "-i", "localhost,", "-c", "local",
                "-M", library, "-m", module, "-a", json.dumps(args), "--check"],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stdout)

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


    def test_manual_reenable_only_and_local_tombstone(self):
        self.enrollment().users([item()])
        self.enrollment().users([item(revoked=True)])
        x = e.HostEnrollment(self.api, self.fd, OWNER, reenable_host="laptop")
        with self.assertRaises(Exception):
            x.users([item()])
        newer = dict(item(), generation=2, expires=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        users, _ = x.users([newer])
        self.assertTrue(users[0]["is_active"])
        self.assertNotIn("laptop", x.receipt["revoked"])
        # Repeat within the same operation never rotates the bootstrap again.
        x.rows = {k: self.api.rows(k) for k in e.PATHS}
        x.users([newer])
        d.credential(self.fd, "laptop", os.getuid(), 1, True)
        self.assertEqual(d.credential(self.fd, "laptop", os.getuid(), 2, False,
                                     "SYNTHETIC-reenabled-password", reenable=True), (True, False))

    def test_separate_provider_exact_scope_and_binding(self):
        import uuid
        api = self.api
        original = api.request
        api.data["mapping"] = [{"pk": str(uuid.uuid4()), "scope_name": "goauthentik.io/api"}]
        flows = [{"pk": str(uuid.uuid4()), "slug": slug} for slug in
                 ("default-provider-authorization-implicit-consent", "default-provider-invalidation-flow")]
        def request(method, path, body=None):
            if path.startswith("/flows/"):
                return {"results": flows, "pagination": {"count": 2, "next": None}}
            result = original(method, path, body)
            if method == "POST" and path == "/core/groups/":
                result["pk"] = str(uuid.uuid4())
                api.data["group"][-1]["pk"] = result["pk"]
            return result
        api.request = request
        signing_key = str(uuid.uuid4())
        x = self.enrollment()
        x.rotation(signing_key)
        x.rows = {k: api.rows(k) for k in e.PATHS}
        users, _ = x.users([item()])
        self.assertEqual(users[0]["attributes"]["rotation"]["client_id"], "chaldea-host-rotation")
        c.public_users(users)
        provider = api.data["provider"][0]
        self.assertEqual(provider["property_mappings"], [api.data["mapping"][0]["pk"]])
        self.assertEqual(provider["allowed_grant_types"], ["client_credentials"])
        self.assertEqual(provider["access_token_validity"], "seconds=300")
        binding = api.data["binding"][0]
        self.assertEqual(binding["group"], users[0]["attributes"]["rotation"]["group"])
        self.assertIsNone(binding["policy"])
        self.assertIsNone(binding["user"])
        # No resource provider is changed, no broad RBAC assignment occurs.
        before = len(api.calls)
        x = self.enrollment()
        x.rotation(signing_key)
        self.assertFalse(x.changed)
        self.assertFalse(any(method != "GET" for method, _, _ in api.calls[before:]))
        api.data["provider"][0]["property_mappings"].append("foreign")
        with self.assertRaises(Exception):
            self.enrollment().rotation(signing_key)

    def test_provider_preflight_does_not_write_before_expression_refusal(self):
        from unittest.mock import patch
        x = self.enrollment()
        modes = []
        x.resources = lambda *a: None
        x.rotation = lambda *a: modes.append(x.check)
        def refuse(*a):
            raise e.Refused()
        x.expressions = refuse
        params = dict(hosts=[item()], owner=OWNER, receipt_dir=self.temp.name,
                      baselines=[], api_token="SYNTHETIC-invalid-token", reenable_host="",
                      rotation_signing_key=OWNER)
        module = NS(params=params, check_mode=False,
                    fail_json=lambda **kw: None)
        with patch.object(e, "AnsibleModule", return_value=module), \
             patch.object(e, "HostEnrollment", return_value=x):
            e.main()
        self.assertEqual(modes, [True])
        self.assertFalse(any(method != "GET" for method, _, _ in self.api.calls))
        self.assertNotIn("rotation", e.read_private(self.fd, "hosts.json") if
                         "hosts.json" in os.listdir(self.fd) else {})

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
        self.assertEqual(self.api.data["token"], [])
        self.assertFalse(any(body and body.get("is_active") is True for method, p, body in self.api.calls if method == "PATCH"))

    def test_revoke_purges_token_minted_after_snapshot_and_reenable(self):
        self.enrollment().users([item()])
        x = self.enrollment()
        original = self.api.request
        def request(method, path, body=None):
            if method == "PATCH" and body == {"is_active": False}:
                self.api.data["token"].append(dict(self.api.data["token"][0],
                    pk=999, identifier="mid-rotation"))
            return original(method, path, body)
        self.api.request = request
        x.users([item(revoked=True)])
        self.assertEqual(self.api.data["token"], [])
        # A leftover from older revocation code must also be purged before enable.
        self.api.data["token"].append({"pk": 1000, "identifier": "leftover",
                                      "user": self.api.data["user"][0]["pk"]})
        newer = dict(item(), generation=2, expires=(dt.datetime.now(dt.timezone.utc)
                     + dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        e.HostEnrollment(self.api, self.fd, OWNER, reenable_host="laptop").users([newer])
        self.assertEqual([t["identifier"] for t in self.api.data["token"]], ["chaldea-laptop-g2"])

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

    def test_revocation_retry_removes_rotation_leftovers_after_tombstone(self):
        key = "SYNTHETIC-bootstrap-password"
        d.credential(self.fd, "laptop", 1000, 1, False, key)
        d.atomic(self.fd, "rotation.json", b"{}")
        d.atomic(self.fd, "host.app-password.stage", b"SYNTHETIC-staged-password")
        # Simulate an interruption after the tombstone and password unlink.
        marker = json.loads(d.read_file(self.fd, "host.generation.json"))
        marker["status"] = "revoked"
        d.atomic(self.fd, "host.generation.json", json.dumps(marker).encode())
        os.unlink("host.app-password", dir_fd=self.fd)
        self.assertEqual(d.credential(self.fd, "laptop", 1000, 1, True, check=True), (True, False))
        self.assertEqual(d.credential(self.fd, "laptop", 1000, 1, True), (True, False))
        self.assertEqual(sorted(os.listdir(self.fd)), ["host.generation.json"])
        self.assertEqual(d.credential(self.fd, "laptop", 1000, 1, True), (False, False))

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
        scopes = {"jocasta": ["jocasta.read"], "ha": ["ha.read"],
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
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls[calls:]))
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

    def test_resource_preflight_rejects_extra_api_mapping_and_relationship_drift(self):
        scope = {"pk": "read", "scope_name": "ha.read"}
        provider = {"pk": 7, "name": "HA", "property_mappings": ["read"],
                    "meta_model_name": "authentik_providers_oauth2.oauth2provider"}
        app = {"pk": "app", "slug": "ha", "provider": 7, "backchannel_providers": [],
               "policy_engine_mode": "any"}
        binding = {"pk": "bind", "target": "app", "group": c.GROUP_IDS["ha"],
                   "policy": None, "user": None, "enabled": True, "negate": False, "order": 0}
        content = "# homelab-infra-owned: " + json.dumps({"version": 1, "objects": [
            ["provider", "HA", "present"], ["application", "ha", "present"]]})
        self.api.blueprint = {"status": "successful", "content": content}
        baseline = {"kind": "mapping", "pk": "read", "service": "ha", "ownership": {
            "blueprint_pk": OWNER, "content_sha256": hashlib.sha256(content.encode()).hexdigest()}}
        # Limit the cohort's grant matrix to isolate this resource's preflight.
        from unittest.mock import patch
        with patch.dict(c.MATRIX, {"homelab": {"ha": {}}}):
            self.api.data.update(mapping=[scope], provider=[provider], application=[app], binding=[binding])
            self.enrollment().resources([item("homelab")], [baseline])
            good = copy.deepcopy(self.api.data)
            for drift in ("api", "missing", "provider", "binding", "backchannel"):
                self.api.data = copy.deepcopy(good)
                if drift == "api":
                    self.api.data["mapping"].append({"pk": "api", "scope_name": "goauthentik.io/api"})
                    self.api.data["provider"][0]["property_mappings"].append("api")
                elif drift == "missing":
                    self.api.data["provider"][0]["property_mappings"] = []
                elif drift == "provider":
                    self.api.data["application"][0]["provider"] = 999
                elif drift == "binding":
                    self.api.data["binding"][0]["negate"] = True
                else:
                    self.api.data["application"][0]["backchannel_providers"] = [7]
                with self.subTest(drift=drift), self.assertRaises(Exception):
                    self.enrollment().resources([item("homelab")], [baseline])
            self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls))
            self.assertEqual(os.listdir(self.fd), [])

    def test_jocasta_preflight_rejects_modified_write_and_api_mapping(self):
        read = {"pk": "read", "scope_name": "jocasta.read", "expression": "return {}"}
        write_body = {"name": "jocasta write", "scope_name": "jocasta.write", "description": "",
                      "expression": "return {}"}
        write = dict(write_body, pk="write")
        provider = {"pk": 9, "name": "Jocasta", "property_mappings": ["read", "write"],
                    "meta_model_name": "authentik_providers_oauth2.oauth2provider"}
        app_body = {"slug": "jocasta", "provider": 9}
        app = dict(app_body, pk="japp", backchannel_providers=[])
        binding_body = {"target": "japp", "policy": "policy", "order": 0}
        binding = dict(binding_body, pk="jbind", enabled=True, negate=False)
        # Outside the host receipt directory, which must stay empty here.
        receipts = tempfile.TemporaryDirectory(dir=os.environ["HOME"])
        self.addCleanup(receipts.cleanup)
        receipt_dir = receipts.name + "/jocasta-receipt"
        os.mkdir(receipt_dir, 0o700)
        fd = os.open(receipt_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            e.write_private(fd, "receipt.json", {"version": 1, "pending": None, "objects": {
                "write": {"pk": "write", "body": write_body},
                "provider": {"pk": 9}, "application": {"pk": "japp", "body": app_body},
                "binding": {"pk": "jbind", "body": binding_body}}})
        finally:
            os.close(fd)
        baseline = {"kind": "mapping", "pk": "read", "service": "jocasta",
                    "ownership": {"receipt_dir": receipt_dir}}
        from unittest.mock import patch
        with patch.dict(c.MATRIX, {"agent-b": {"jocasta": {}}}):
            self.api.data.update(mapping=[read, write], provider=[provider], application=[app], binding=[binding])
            self.enrollment().resources([item("agent-b")], [baseline])
            good = copy.deepcopy(self.api.data)
            for drift in ("write", "api"):
                self.api.data = copy.deepcopy(good)
                if drift == "write":
                    self.api.data["mapping"][1]["expression"] = "return {'ov_actions': ['read']}"
                else:
                    self.api.data["mapping"].append({"pk": "api", "scope_name": "goauthentik.io/api"})
                    self.api.data["provider"][0]["property_mappings"].append("api")
                with self.subTest(drift=drift), self.assertRaises(Exception):
                    self.enrollment().resources([item("agent-b")], [baseline])
            self.assertTrue(all(method == "GET" for method, _, _ in self.api.calls))

    def test_expressions_render_identically_for_reordered_api_data(self):
        user = {"pk": 7, "username": "chaldea-agent-b", "groups": ["g2", "g1"],
                "attributes": {"services": {"jocasta": {"b": 1, "a": [3, 1]}}, "host": "agent-b"}}
        other = copy.deepcopy(user)
        other["groups"] = ["g1", "g2"]
        other["attributes"] = {"host": "agent-b", "services": {"jocasta": {"a": [3, 1], "b": 1}}}
        from unittest.mock import patch
        with patch.object(c, "public_users", lambda users: None):
            self.assertEqual(c.jocasta_policy([user], "return False"), c.jocasta_policy([other], "return False"))
            self.assertEqual(c.scope_expression("jocasta", "jocasta.read", [user], "return {}"),
                             c.scope_expression("jocasta", "jocasta.read", [other], "return {}"))
        # List order inside attributes is data, not formatting, and is kept.
        self.assertEqual(c.canonical({"a": [3, 1]}), {"a": [3, 1]})

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
