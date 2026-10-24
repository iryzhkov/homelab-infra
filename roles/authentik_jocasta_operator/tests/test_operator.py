"""Synthetic policy/mapping checks and the real operator role on fake HTTPS.

This does not implement an OAuth server or qualify native login/rotation.
"""
from copy import deepcopy
from hashlib import sha256, sha512
from base64 import urlsafe_b64encode
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

import yaml
from deepmerge import always_merger

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "roles/authentik_machine_identities/tests"))
import test_machine_identities as shared
import fake_authentik as fake

_loader = importlib.util.spec_from_file_location("operator_contract", HERE.parent / "filter_plugins/jocasta_operator.py")
op = importlib.util.module_from_spec(_loader)
_loader.loader.exec_module(op)
PK = 791  # Synthetic only. The production defaults have no PK.
SPEC = op.operator_spec(PK)
CONTENT = op.operator_blueprint(SPEC)


class Backend(fake.Authentik):
    """Extend the existing blueprint/HTTPS fixture with these exact objects."""
    def __init__(self):
        super().__init__()
        self.users["igor"]["pk"] = PK
        self.keypairs = {op.KEY: {"pk": op.KEY, "key_type": "rsa", "private_key_available": True}}
        self.tables = {kind: {} for kind in op.ENDPOINTS}
        self.tables["application"] = {x["pk"]: self._app_view(x) for x in self.apps.values()}
        self.tables["group"] = {x["pk"]: self._group_view(x) for x in self.groups.values()}
        self.tables["mapping"]["builtin-offline"] = {
            "pk": "builtin-offline", "name": "built-in offline", "managed": op.OFFLINE,
            "scope_name": "offline_access", "expression": op.OFFLINE_EXPRESSION + "\n"}
        for slug in ("default-provider-authorization-explicit-consent", "default-provider-invalidation-flow"):
            pk = str(uuid.uuid4())
            self.tables["flow"][pk] = {"pk": pk, "slug": slug}

    def apply(self, content):
        ids = {}
        def resolve(value):
            if isinstance(value, tuple):
                tag, arg = value
                if tag == "KeyOf":
                    return ids[arg]
                model, (field, wanted) = arg
                if model == "authentik_core.user":
                    assert field == "pk" and wanted == PK
                    return wanted
                if model == "authentik_crypto.certificatekeypair":
                    assert wanted == op.KEY
                    return wanted
                kind = "flow" if model == "authentik_flows.flow" else "mapping"
                return next(x["pk"] for x in self.tables[kind].values() if x.get(field) == wanted)
            if isinstance(value, list):
                return [resolve(v) for v in value]
            if isinstance(value, dict):
                return {k: resolve(v) for k, v in value.items()}
            return value
        for entry in self.parse(content)["entries"]:
            kind = next(k for k, v in op.MODELS.items() if v == entry["model"])
            ident = entry["identifiers"]
            table = self.tables[kind]
            old = next((x for x in table.values() if all(x.get(k) == v for k, v in ident.items())), None)
            if entry.get("state") == "absent":
                if old:
                    del table[old["pk"]]
                continue
            # Authentik 2026.8.4 importer sets the child link PK, but Django
            # saves the default UUID on the concrete parent, then replaces
            # the child link. Group and PolicyBinding have no concrete parent.
            # See test_importer_semantics.py for the real importer/Django seam.
            inherited = kind in {"mapping", "policy", "application"}
            pk = (old or {}).get("pk") or (str(uuid.uuid4()) if inherited else ident.get("pk")) or self._pk()
            row = old or {}
            row.update(ident)
            row.update(resolve(entry["attrs"]))
            row["pk"] = pk
            if kind == "application":
                row.setdefault("backchannel_providers", [])
            if kind == "binding":
                for field in ("user", "group", "policy"):
                    row.setdefault(field, None)
            if kind == "group":
                row["users_obj"] = [{"pk": n, "username": "igor"} for n in row.pop("users")]
            table[pk] = row
            ids[entry["id"]] = pk

    def handle(self, method, raw_path, body):
        parts = urlsplit(raw_path)
        path = unquote(parts.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        api = path.removeprefix("/api/v3")
        if path.startswith("/application/o/") and method == "GET":
            self.requests.append((method, path, query))
            if self.tables["provider"]:
                return 200, self.jwks
            return 404, {}
        for kind, endpoint in op.ENDPOINTS.items():
            if method == "GET" and api == urlsplit(endpoint).path:
                self.requests.append((method, path, query))
                return self.responses.get((method, api), (200, fake.page(list(self.tables[kind].values()))))
        if method == "GET" and api == f"/core/users/{PK}/":
            self.requests.append((method, path, query))
            return 200, self._user_view(self.users["igor"])
        return super().handle(method, raw_path, body)

    def snapshot(self):
        return [{"item": {"kind": k}, "json": fake.page(list(v.values()))} for k, v in self.tables.items()]

    def instance(self, content=CONTENT):
        return {"pk": "owned-blueprint", "name": op.BLUEPRINT, "content": content,
                "path": "", "context": {}, "enabled": True, "status": "successful",
                "last_applied": "2026-10-24T12:00:00Z",
                "last_applied_hash": sha512(content.encode()).hexdigest()}


class Groups:
    def __init__(self, member=True, superuser=False):
        self.member, self.superuser = member, superuser

    def filter(self, **kwargs):
        assert kwargs == {"name": op.GROUP, "is_superuser": False}
        return SimpleNamespace(exists=lambda: self.member and not self.superuser)


def human(**changes):
    return SimpleNamespace(pk=PK, is_active=True, type="internal", groups=Groups(),
                           attributes={"jocasta_operator": True}) if not changes else human_changed(changes)


def human_changed(changes):
    value = human()
    value.__dict__.update(changes)
    return value


def expression(which):
    return next(x["attrs"]["expression"] for x in SPEC["objects"] if x["id"] == which)


def evaluate(source, **context):
    namespace = dict(context)
    exec("def result():\n" + "\n".join("    " + line for line in source.splitlines()), namespace)
    return namespace["result"]()


def provider(**changes):
    return SimpleNamespace(**({"name": op.NAME, "client_id": op.SLUG,
                               "application": SimpleNamespace(slug=op.SLUG)} | changes))


def request(user=None, **changes):
    ctx = {"oauth_grant_type": "authorization_code", "oauth_response_type": "code",
           "oauth_code_challenge": urlsafe_b64encode(sha256(b"synthetic-verifier").digest()).decode().rstrip("="),
           "oauth_code_challenge_method": "S256", "oauth_scopes": {"jocasta.read", "jocasta.write", "offline_access"},
           "oauth_redirect_uri": op.REDIRECTS[0]} | changes
    return SimpleNamespace(user=user or human(), context=ctx)


class ContractTest(unittest.TestCase):
    def test_pk_is_required_and_never_inferred(self):
        for pk in (None, "", "791", 0, -1, True, 2**31):
            with self.subTest(pk=pk), self.assertRaises(op.AnsibleFilterError):
                op.operator_spec(pk)
        defaults = yaml.safe_load((HERE.parent / "defaults/main.yml").read_text())
        self.assertIsNone(defaults["jocasta_operator_user_pk"])
        self.assertIs(defaults["jocasta_operator_enabled"], False)

    def test_public_provider_exact_grants_scopes_signer_lifetimes_redirects(self):
        attrs = next(x["attrs"] for x in SPEC["objects"] if x["id"] == "provider")
        self.assertEqual(attrs["client_type"], "public")
        self.assertEqual(attrs["client_id"], op.SLUG)
        self.assertEqual(attrs["grant_types"], ["authorization_code", "refresh_token"])
        self.assertEqual(attrs["access_code_validity"], "seconds=60")
        self.assertEqual(attrs["access_token_validity"], "seconds=300")
        self.assertEqual(attrs["refresh_token_validity"], "days=30")
        self.assertEqual(attrs["refresh_token_threshold"], "seconds=0")
        self.assertNotIn("client_secret", CONTENT)
        self.assertNotIn("authentik_core.user\n", CONTENT)
        self.assertEqual([r["url"] for r in attrs["redirect_uris"]], op.REDIRECTS)
        self.assertTrue(all(r["matching_mode"] == "strict" for r in attrs["redirect_uris"]))
        self.assertEqual(urlsafe_b64encode(sha256(op.RESOURCE.encode()).digest()[:9]).decode().rstrip("="),
                         "bbdouGp56E53")
        self.assertEqual(len(attrs["property_mappings"]), 3)
        self.assertEqual(attrs["signing_key"]["_value"][1], ["kp_uuid", op.KEY])
        self.assertTrue(attrs["include_claims_in_id_token"])
        app = next(x["attrs"] for x in SPEC["objects"] if x["id"] == "application")
        self.assertEqual(app["policy_engine_mode"], "all")

    def test_composed_claims_in_both_orders_and_read_only(self):
        for order in (("read", "write", "offline"), ("offline", "write", "read"), ("read",)):
            claims = {}
            for name in order:
                source = op.OFFLINE_EXPRESSION if name == "offline" else expression(name)
                always_merger.merge(claims, evaluate(source, user=human(), provider=provider()))
            self.assertEqual(claims, {"aud": op.RESOURCE, "jocasta_operator": True})
        self.assertEqual(evaluate(expression("write")), {})
        self.assertNotIn("scope", evaluate(expression("read"), user=human(), provider=provider()))
        self.assertNotIn("azp", evaluate(expression("read"), user=human(), provider=provider()))

    def test_wrong_user_type_active_pk_membership_and_provider(self):
        for u in (human(pk=PK+1), human(type="service_account"), human(type="internal_service_account"),
                  human(type="external"), human(is_active=False), human(groups=Groups(False)),
                  human(groups=Groups(True, True)), None):
            with self.subTest(user=u):
                self.assertEqual(evaluate(expression("read"), user=u, provider=provider()), {})
                self.assertFalse(evaluate(expression("policy"), request=request(u) if u is not None
                                          else SimpleNamespace(user=None, context={})))
        for p in (provider(name="limited"), provider(client_id="limited"),
                  provider(application=SimpleNamespace(slug="limited"))):
            self.assertEqual(evaluate(expression("read"), user=human(), provider=p), {})

    def test_refresh_claim_evaluation_uses_current_state_not_attributes(self):
        u = human()
        self.assertTrue(evaluate(expression("read"), user=u, provider=provider())["jocasta_operator"])
        u.groups.member = False
        self.assertEqual(evaluate(expression("read"), user=u, provider=provider()), {})
        u.groups.member = True
        u.is_active = False
        self.assertEqual(evaluate(expression("read"), user=u, provider=provider()), {})
        u.is_active = True
        u.type = "service_account"
        self.assertEqual(evaluate(expression("read"), user=u, provider=provider()), {})

    def test_pkce_and_closed_authorization_context(self):
        self.assertTrue(evaluate(expression("policy"), request=request()))
        self.assertNotIn("oauth_state", expression("policy"))
        for changes in ({"oauth_code_challenge": None}, {"oauth_code_challenge": ""},
                        {"oauth_code_challenge": "a"*43}, {"oauth_code_challenge": "A"*42+"B"},
                        {"oauth_code_challenge": "A"*43+"="}, {"oauth_code_challenge_method": "plain"},
                        {"oauth_code_challenge_method": None}, {"oauth_grant_type": "client_credentials"},
                        {"oauth_grant_type": "password"}, {"oauth_response_type": "token"},
                        {"oauth_scopes": {"jocasta.write"}}, {"oauth_scopes": {"jocasta.read", "profile"}},
                        {"oauth_scopes": "jocasta.read"}, {"oauth_redirect_uri": op.REDIRECTS[0]+"/"},
                        {"oauth_redirect_uri": "http://127.0.0.1:43184/callback"}):
            with self.subTest(changes=changes):
                self.assertFalse(evaluate(expression("policy"), request=request(**changes)))
        for url in op.REDIRECTS:
            self.assertTrue(evaluate(expression("policy"), request=request(oauth_redirect_uri=url)))

    def test_ownership_marker_no_adoption_no_rebind_no_replay(self):
        s = Backend()
        instance = s.instance()
        self.assertEqual(op.operator_instance(fake.page([instance]), SPEC), instance)
        for change in ({"content": CONTENT+"\nforeign"}, {"context": {"owner": "foreign"}},
                       {"status": "unknown"}, {"status": "error"}, {"last_applied_hash": "0"*128}):
            with self.subTest(change=change), self.assertRaises(op.AnsibleFilterError):
                op.operator_instance(fake.page([instance | change]), SPEC)
        with self.assertRaises(op.AnsibleFilterError):
            op.operator_instance(fake.page([instance]), op.operator_spec(PK+1))

    def test_collisions_foreign_references_privileges_and_tombstones(self):
        s = Backend()
        u, k = s.users["igor"], s.keypairs[op.KEY]
        self.assertTrue(op.operator_guard(SPEC, {}, s.snapshot(), u, k))
        s.apply(CONTENT)
        self.assertTrue(op.operator_guard(SPEC, s.instance(), s.snapshot(), u, k, True))
        with self.assertRaises(op.AnsibleFilterError):
            op.operator_guard(SPEC, {}, s.snapshot(), u, k)
        absent = op.operator_spec(PK, "absent")
        with self.assertRaises(op.AnsibleFilterError):
            op.operator_guard(absent, {}, s.snapshot(), {}, {})
        p = next(iter(s.tables["provider"].values()))
        foreign = {"pk": "foreign-app", "slug": "foreign", "provider": p["pk"], "backchannel_providers": []}
        s.tables["application"]["foreign-app"] = foreign
        with self.assertRaises(op.AnsibleFilterError):
            op.operator_guard(absent, s.instance(), s.snapshot(), {}, {})
        del s.tables["application"]["foreign-app"]
        group = next(x for x in s.tables["group"].values() if x["name"] == op.GROUP)
        for field, value in (("is_superuser", True), ("roles", ["admin"]), ("parents", ["parent"]),
                             ("users_obj", [{"pk": PK+1}])):
            old = group[field]; group[field] = value
            with self.subTest(field=field), self.assertRaises(op.AnsibleFilterError):
                op.operator_guard(absent, s.instance(), s.snapshot(), {}, {})
            group[field] = old
        s.apply(op.operator_blueprint(absent))
        self.assertTrue(op.operator_guard(absent, s.instance(), s.snapshot(), {}, {}, True))
        tombstone = s.instance(op.operator_blueprint(absent))
        self.assertTrue(op.operator_guard(absent, tombstone, s.snapshot(), {}, {}, True))
        s.apply(CONTENT)
        with self.assertRaises(op.AnsibleFilterError):
            op.operator_guard(absent, tombstone, s.snapshot(), {}, {})

    def test_replacement_provider_requires_owned_application_reference(self):
        s = Backend()
        s.apply(CONTENT)
        receipt = s.instance()
        self.assertEqual(op.operator_instance(fake.page([receipt]), SPEC), receipt)
        original = next(iter(s.tables["provider"].values()))
        replacement = original | {"pk": s._pk()}
        del s.tables["provider"][original["pk"]]
        s.tables["provider"][replacement["pk"]] = replacement
        app = next(x for x in s.tables["application"].values() if x.get("slug") == op.SLUG)
        absent = op.operator_spec(PK, "absent")
        for reference in (None, original["pk"]):
            app["provider"] = reference
            with self.subTest(reference=reference), self.assertRaisesRegex(
                    op.AnsibleFilterError, "exact reference"):
                op.operator_guard(absent, receipt, s.snapshot(), {}, {})
        del s.tables["application"][app["pk"]]
        with self.assertRaisesRegex(op.AnsibleFilterError, "exact reference"):
            op.operator_guard(absent, receipt, s.snapshot(), {}, {})

    def test_incomplete_collections_refuse(self):
        for body in ({}, {"results": [], "pagination": {"count": 3, "current": 1, "total_pages": 2}},
                     fake.page([{"pk": "x"}, {"pk": "x"}])):
            with self.assertRaises(op.AnsibleFilterError):
                op.rows(body)


class RoleTest(unittest.TestCase):
    def setUp(self):
        original = shared.fake.Authentik
        shared.fake.Authentik = Backend
        try:
            self.fixture = shared.Scratch(self)
        finally:
            shared.fake.Authentik = original
        self.state = self.fixture.state

    def run_role(self, state="present", check=False, offline=False, pk=PK):
        vars_ = {"ansible_python_interpreter": sys.executable, "jocasta_operator_enabled": True,
                 "jocasta_operator_user_pk": pk, "jocasta_operator_state": state,
                 "jocasta_operator_online_check": not offline}
        if not offline:
            vars_.update(authentik_url=f"https://127.0.0.1:{self.fixture.server.server_port}/api/v3",
                         authentik_headers={"Authorization": "Bearer " + fake.ADMIN})
        play = [{"hosts": "localhost", "connection": "local", "gather_facts": False, "vars": vars_,
                 "module_defaults": {"ansible.builtin.uri": {"ca_path": str(self.fixture.cert)}},
                 "tasks": [{"ansible.builtin.include_role": {"name": "authentik_jocasta_operator"}}]}]
        path = self.fixture.base / "operator.yml"
        path.write_text(yaml.safe_dump(play))
        env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"), ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCAL_TEMP=str(self.fixture.base / "al"),
                   ANSIBLE_REMOTE_TEMP=str(self.fixture.base / "ar"), SSL_CERT_FILE=str(self.fixture.cert))
        self.state.requests.clear()
        result = subprocess.run(["ansible-playbook", "-i", str(self.fixture.inventory), str(path)]
                                + (["--check"] if check else []), cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=180)
        output = result.stdout + result.stderr
        self.assertNotIn(fake.ADMIN, output)
        self.assertNotIn(fake.KEY_PREFIX, output)
        return result.returncode, output

    def ok(self, **kw):
        code, out = self.run_role(**kw)
        self.assertEqual(code, 0, out)
        return out

    def test_create_second_noop_absence_and_unrelated_preservation(self):
        baseline_user = deepcopy(self.state.users)
        baseline_family = deepcopy(self.state.tables["group"])
        self.ok()
        self.assertEqual(len(self.state.tables["provider"]), 1)
        self.assertFalse(self.state.public_authorization[-1])
        self.assertEqual(self.state.users, baseline_user)
        for key, value in baseline_family.items():
            self.assertEqual(self.state.tables["group"][key], value)
        self.ok()
        self.assertEqual(self.fixture.writes(), [])
        self.ok(state="absent")
        self.assertEqual(self.state.tables["provider"], {})
        self.assertEqual(self.state.tables["policy"], {})
        self.assertEqual(len(self.state.tables["mapping"]), 1)
        self.assertEqual(self.state.users, baseline_user)
        self.ok(state="absent")
        self.assertEqual(self.fixture.writes(), [])
        self.assertFalse(any("/view_key/" in p or "/core/tokens/" in p for _, p, _ in self.state.requests))

    def test_check_offline_and_online_have_no_writes(self):
        self.ok(check=True, offline=True)
        self.assertEqual(self.state.requests, [])
        self.ok(check=True)
        self.assertEqual(self.fixture.writes(), [])
        self.assertEqual(self.state.blueprints, {})

    def test_collision_and_absence_refuse_before_writes(self):
        group = {"pk": "foreign", "name": op.GROUP, "is_superuser": False, "roles": [], "parents": [],
                 "users_obj": [{"pk": PK}]}
        self.state.tables["group"]["foreign"] = group
        for state in ("present", "absent"):
            code, _ = self.run_role(state=state)
            self.assertNotEqual(code, 0)
            self.assertEqual(self.fixture.writes(), [])

    def test_successful_receipt_cannot_adopt_or_retire_unbound_replacement(self):
        self.ok()
        receipt = deepcopy(next(iter(self.state.blueprints.values())))
        self.assertEqual(op.operator_instance(fake.page([receipt]), SPEC), receipt)
        original = next(iter(self.state.tables["provider"].values()))
        replacement = original | {"pk": self.state._pk()}
        del self.state.tables["provider"][original["pk"]]
        self.state.tables["provider"][replacement["pk"]] = replacement
        app = next(x for x in self.state.tables["application"].values() if x.get("slug") == op.SLUG)
        self.assertEqual(app["provider"], original["pk"])
        app["provider"] = None
        for state in ("present", "absent"):
            with self.subTest(state=state):
                code, output = self.run_role(state=state)
                self.assertNotEqual(code, 0)
                self.assertIn("Refuse foreign ownership, references and unsafe input", output)
                self.assertEqual(self.fixture.writes(), [])
                self.assertEqual(self.state.blueprints[receipt["pk"]], receipt)
                self.assertEqual(self.state.tables["provider"][replacement["pk"]], replacement)
                self.assertIsNone(app["provider"])

    def test_failed_apply_is_not_replayed(self):
        self.state.apply_failure = True
        code, _ = self.run_role()
        self.assertNotEqual(code, 0)
        self.assertEqual(sum(p.endswith("/apply/") for m, p in self.fixture.writes()), 1)
        code, _ = self.run_role()
        self.assertNotEqual(code, 0)
        self.assertEqual(self.fixture.writes(), [])

    def test_entrypoint_offline_syntax_and_inputs_without_sops(self):
        plugins = self.fixture.base / "collections/ansible_collections/community/sops/plugins"
        (plugins / "action").mkdir(parents=True)
        (plugins / "modules").mkdir()
        (plugins / "modules/load_vars.py").write_text("")
        sentinel = self.fixture.base / "sops-called"
        (plugins / "action/load_vars.py").write_text(
            "from ansible.plugins.action import ActionBase\n"
            "from ansible.errors import AnsibleActionFail\n"
            "from pathlib import Path\n"
            "class ActionModule(ActionBase):\n"
            "    def run(self, tmp=None, task_vars=None):\n"
            f"        Path({str(sentinel)!r}).touch()\n"
            "        raise AnsibleActionFail('SOPS_DECRYPT_FORBIDDEN')\n")
        extra = self.fixture.base / "operator-input.yml"
        env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                   ANSIBLE_COLLECTIONS_PATH=str(self.fixture.base / "collections"),
                   ANSIBLE_LOCAL_TEMP=str(self.fixture.base / "el"),
                   ANSIBLE_REMOTE_TEMP=str(self.fixture.base / "er"))
        argv = ["ansible-playbook", "-i", str(self.fixture.inventory),
                str(ROOT / "playbooks/authentik-jocasta-operator.yml"), "-e", "@" + str(extra)]
        for enabled, pk, flag, success in ((True, PK, "--syntax-check", True),
                                          (True, PK, "--check", True),
                                          (False, PK, "--check", False),
                                          (True, None, "--check", False)):
            extra.write_text(yaml.safe_dump({"jocasta_operator_enabled": enabled,
                                             "jocasta_operator_user_pk": pk}))
            result = subprocess.run(argv + [flag], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.state.requests, [])

    def test_signer_gate_is_maintained_and_refuses_non_rsa(self):
        maintained = ROOT / "roles/authentik_machine_identities/library/authentik_machine_jwks.py"
        self.assertEqual((HERE.parent / "library/authentik_machine_jwks.py").read_bytes(), maintained.read_bytes())
        self.state.jwks = {"keys": [{"use": "sig", "kty": "EC", "alg": "ES256", "kid": "wrong-signer"}]}
        code, output = self.run_role()
        self.assertNotEqual(code, 0)
        self.assertIn("Verify actual public RSA RS256 signing evidence", output)
        self.assertTrue(self.state.public_authorization)
        self.assertTrue(all(header is None for header in self.state.public_authorization))
        self.assertEqual(sum(p.endswith("/apply/") for m, p in self.fixture.writes()), 1)

    def test_foreign_binding_and_shared_mapping_refuse_removal(self):
        self.ok()
        own = next(x for x in self.state.tables["binding"].values() if x["group"])
        foreign = deepcopy(own)
        foreign["pk"] = "foreign-binding"
        self.state.tables["binding"]["foreign-binding"] = foreign
        code, _ = self.run_role(state="absent")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.fixture.writes(), [])
        del self.state.tables["binding"]["foreign-binding"]
        provider = next(iter(self.state.tables["provider"].values()))
        self.state.tables["provider"][999] = {
            "pk": 999, "name": "unrelated", "client_id": "unrelated",
            "property_mappings": provider["property_mappings"][:1]}
        code, _ = self.run_role(state="absent")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.fixture.writes(), [])

    def test_wrong_bound_user_and_incomplete_snapshot_refuse(self):
        self.state.users["igor"]["type"] = "service_account"
        code, _ = self.run_role()
        self.assertNotEqual(code, 0)
        self.assertEqual(self.fixture.writes(), [])
        self.state.users["igor"]["type"] = "internal"
        self.state.responses[("GET", "/policies/bindings/")] = (200, {
            "pagination": {"count": 501, "current": 1, "total_pages": 2}, "results": []})
        code, _ = self.run_role()
        self.assertNotEqual(code, 0)
        self.assertEqual(self.fixture.writes(), [])


if __name__ == "__main__":
    shared.scratch_home()
    unittest.main()
