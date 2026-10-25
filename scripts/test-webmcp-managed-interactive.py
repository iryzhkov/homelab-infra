#!/usr/bin/env python3
"""Offline Authentik contract/journal tests. No real store, HTTP or credentials."""
import copy
import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "roles/authentik_webmcp_interactive/tests"))
import used_by_seam as seam
import provider_inventory_seam as inventory
spec = importlib.util.spec_from_file_location("managed", ROOT / "roles/authentik_webmcp_interactive/library/webmcp_managed_enrollment.py")
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

OWNER = "00000000-0000-4000-8000-000000000000"
SECRET = "SYNTHETIC-secret-sentinel"
NOW = dt.datetime(2026, 10, 24, tzinfo=dt.timezone.utc)


class FakeAPI:
    def __init__(self):
        self.data = {k: [] for k in m.PATHS}
        self.calls = []
        self.next = 100
        self.fail_next = False
        self.superuser = True
        self.foreign = []
        self.evidence = None
        self.used_by_reads = 0

    require_superuser = m.APIClient.require_superuser
    mapping_uses = m.APIClient.mapping_uses

    def rows(self, kind, owned=None):
        return copy.deepcopy(self.data[kind])

    def request(self, method, path, body=None, public=False):
        self.calls.append((method, path, copy.deepcopy(body), public))
        if self.fail_next and method in ("POST", "PATCH", "DELETE"):
            self.fail_next = False
            raise m.Refused()
        if path == "/core/users/me/":
            return {"user": {"is_superuser": self.superuser}}
        if path.startswith("/propertymappings/all/") and path.endswith("/used_by/"):
            self.used_by_reads += 1
            pk = path.split("/")[3]
            result = seam.references(pk, self.data["provider"], self.foreign)
            if self.evidence is not None:
                return self.evidence(result)
            return result
        if public:
            assert path == "/application/o/jocasta-managed-interactive/jwks/"
            return {"keys": [{"use": "sig", "kty": "RSA", "alg": "RS256", "n": "public", "e": "AQAB"}]}
        if path.startswith("/flows/instances/"):
            return {"results": [
                {"pk": "authorization", "slug": "default-provider-authorization-implicit-consent"},
                {"pk": "invalidation", "slug": "default-provider-invalidation-flow"}],
                "pagination": {"next": 0, "count": 2}}
        if path.endswith("/view_key/"):
            return {"key": SECRET}
        kind = next(k for k, prefix in m.PATHS.items() if path.startswith(prefix))
        if method == "POST":
            self.next += 1
            # Deliberately non-name-derived IDs, including UUIDs.
            pk = self.next if kind in ("user", "provider") else f"00000000-0000-4000-8000-{self.next:012d}"
            result = {**body, "pk": pk}
            if kind == "user":
                result["is_superuser"] = False
            if kind == "provider":
                result["client_secret"] = SECRET  # must never reach receipts
            if kind == "application":
                assert self.data["policy"][0]["expression"] == "return False"
                assert self.data["provider"][0]["property_mappings"] == []
            self.data[kind].append(result)
            return copy.deepcopy(result)
        field = "identifier" if kind == "token" else "slug" if kind == "application" else "pk"
        lookup = path[len(m.PATHS[kind]):].strip("/")
        found = next(x for x in self.data[kind] if str(x[field]) == lookup)
        if method == "PATCH":
            found.update(body)
            return copy.deepcopy(found)
        assert method == "DELETE"
        self.data[kind].remove(found)
        return {}


FOREIGN_SECRET = "SYNTHETIC-foreign-provider-secret"


class InventoryAPI(FakeAPI):
    """Real collection consumer against the pinned public/detail producer contract."""
    rows = m.APIClient.rows

    def __init__(self):
        super().__init__()
        self.allowed_provider_ids = set()
        self.data["provider"].append({
            "pk": 999, "name": "foreign OAuth2 provider", "property_mappings": [],
            "client_id": "foreign-client", "client_secret": FOREIGN_SECRET,
            "meta_model_name": "authentik_providers_oauth2.oauth2provider",
        })
        self.data["application"].append({
            "pk": "foreign-app", "slug": "foreign-app", "provider": 999,
            "backchannel_providers": [999],
        })

    def request(self, method, path, body=None, public=False):
        if method == "GET" and path.startswith("/providers/oauth2/"):
            # A forbidden request fails the regression, before producing a body.
            assert path in {f"/providers/oauth2/{pk}/" for pk in self.allowed_provider_ids}, path
            self.calls.append((method, path, copy.deepcopy(body), public))
            pk = int(path.split("/")[3])
            return inventory.oauth_provider(next(r for r in self.data["provider"] if r["pk"] == pk))
        if method == "GET" and path == "/providers/all/?page_size=500":
            rows = [inventory.public_provider(row) for row in self.data["provider"]]
        elif method == "GET" and path == "/core/applications/?page_size=500&superuser_full_list=true":
            rows = [inventory.application_row(row, self.data["provider"])
                    for row in self.data["application"]]
        elif method == "GET" and path in {
                prefix + "?page_size=500" for kind, prefix in m.PATHS.items()
                if kind not in ("provider", "application")}:
            kind = next(k for k, prefix in m.PATHS.items() if path == prefix + "?page_size=500")
            rows = copy.deepcopy(self.data[kind])
        else:
            result = super().request(method, path, body, public)
            if method == "POST" and path == m.PATHS["provider"]:
                self.allowed_provider_ids.add(result["pk"])
            return result
        self.calls.append((method, path, copy.deepcopy(body), public))
        return {"results": rows, "pagination": {"count": len(rows), "next": 0}}


def hosts(generation=1, state="present"):
    return [{"host": "laptop", "generation": generation, "expires": "2026-11-24T00:00:00Z", "state": state}]


class Tests(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ["HOME"])
        assert scratch == Path(os.environ.get("WEBMCP_TEST_HOME", str(ROOT / ".check-home"))) and scratch.is_dir()
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.path = Path(self.temp.name)
        self.path.chmod(0o700)
        self.fd = m.secure_dir(str(self.path))
        self.api = FakeAPI()

    def tearDown(self):
        os.close(self.fd)
        self.temp.cleanup()

    def enroll(self):
        return m.Enrollment(self.api, self.fd, OWNER)

    def test_enroll_noop_rotation_and_revocation(self):
        self.assertTrue(self.enroll().converge(hosts(), "present"))
        value = json.loads((self.path / "laptop.json").read_text())
        self.assertEqual(value["password"], SECRET)
        self.assertEqual(value["username"], "webmcp-interactive-laptop")
        self.assertEqual((self.path / "laptop.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn(SECRET, (self.path / "receipt.json").read_text())
        before = len([x for x in self.api.calls if x[0] != "GET"])
        self.assertFalse(self.enroll().converge(hosts(), "present"))
        self.assertEqual(before, len([x for x in self.api.calls if x[0] != "GET"]))
        self.assertTrue(self.enroll().converge(hosts(2), "present"))
        self.assertEqual([x["identifier"] for x in self.api.data["token"]], ["webmcp-interactive-laptop-g2"])
        with self.assertRaises(m.Refused):
            self.enroll().converge(hosts(1), "present")
        self.assertTrue(self.enroll().converge(hosts(2, "absent"), "absent"))
        self.assertFalse((self.path / "laptop.json").exists())
        self.assertFalse(self.api.data["user"][0]["is_active"])
        self.assertFalse(self.api.data["token"] or self.api.data["application"] or self.api.data["provider"])
        self.assertFalse(self.enroll().converge(hosts(2, "absent"), "absent"))
        for generation in (1, 2):
            before = len(self.api.calls)
            with self.assertRaises(m.Refused):
                self.enroll().converge(hosts(generation), "present")
            self.assertEqual(len(self.api.calls), before)
        self.assertTrue(self.enroll().converge(hosts(3), "present"))

    def test_identity_reuse_and_host_gate(self):
        m.declaration(hosts(), OWNER, False, NOW)
        for host in ("agent-a", "agent-b", "omarchy-pc"):
            declaration = [dict(hosts()[0], host=host)]
            with self.assertRaises(m.Refused):
                m.declaration(declaration, OWNER, False, NOW)
            m.declaration(declaration, OWNER, True, NOW)
        for declaration in ([dict(hosts()[0], host="homelab")], hosts() + hosts(),
                            [dict(hosts()[0], expires="2026-10-24T01:00:00Z")],
                            [dict(hosts()[0], expires="2028-10-24T01:00:00Z")]):
            with self.assertRaises(m.Refused):
                m.declaration(declaration, OWNER, True, NOW)

    def test_foreign_collision_and_drift_refuse(self):
        self.api.data["user"].append({"pk": 4, "username": "webmcp-interactive-laptop"})
        with self.assertRaises(m.Refused):
            self.enroll().converge(hosts(), "present")
        self.assertFalse(any(x[0] != "GET" for x in self.api.calls))
        self.api.data["user"].clear()
        self.enroll().converge(hosts(), "present")
        self.api.data["user"][0]["groups"] = ["foreign"]
        with self.assertRaises(m.Refused):
            self.enroll()
        self.api.data["user"][0]["groups"] = []
        self.api.data["provider"].append({"pk": 999, "property_mappings": [self.api.data["mapping"][0]["pk"]]})
        with self.assertRaises(m.Refused):
            self.enroll().converge(hosts(), "present")

    def assert_reference_refusal(self):
        receipt = (self.path / "receipt.json").read_bytes()
        credential = (self.path / "laptop.json").read_bytes()
        for state in ("present", "absent"):
            with self.subTest(state=state):
                before = len(self.api.calls)
                with self.assertRaises(m.Refused):
                    self.enroll().converge(hosts(state=state), state)
                self.assertFalse(any(method != "GET" or path.endswith("/view_key/")
                                     for method, path, _, _ in self.api.calls[before:]))
                self.assertEqual((self.path / "receipt.json").read_bytes(), receipt)
                self.assertEqual((self.path / "laptop.json").read_bytes(), credential)
        self.assertTrue(self.api.data["mapping"])

    def test_real_owned_producer_output_is_accepted(self):
        self.enroll().converge(hosts(), "present")
        provider = self.api.data["provider"][0]
        pk = self.api.data["mapping"][0]["pk"]
        result = self.api.mapping_uses(pk)
        self.assertEqual(result, [
            {"app": "authentik_core", "model_name": "provider",
             "pk": str(provider["pk"]), "name": m.CLIENT, "action": "cascade_many"},
            {"app": "authentik_core", "model_name": "providerpropertymapping",
             "pk": result[1]["pk"],
             "name": f"ProviderPropertyMapping for Provider {provider['pk']} and PropertyMapping {pk}.",
             "action": "cascade"},
        ])
        # The view retrieves the concrete subclass, not the base mapping.
        self.assertIsInstance(seam.ScopeMapping.objects.get(pk=pk), seam.PropertyMapping)
        self.assertEqual(seam.ScopeMapping._meta.pk.name, "propertymapping_ptr")
        self.assertEqual(seam.ProviderPropertyMapping._meta.pk.get_internal_type(), "AutoField")
        self.assertFalse(self.enroll().converge(hosts(), "present"))

    def test_foreign_provider_mapping_reference_refuses_present_and_global_revocation(self):
        self.enroll().converge(hosts(), "present")
        self.api.data["provider"].append({
            "pk": 999, "name": "unrelated-proxy",
            "property_mappings": [self.api.data["mapping"][0]["pk"]],
        })
        # Common Provider reverse managers include proxy and other subclasses.
        self.assert_reference_refusal()

    def test_real_dcr_and_source_references_refuse_before_any_effect(self):
        self.enroll().converge(hosts(), "present")
        for relation, expected_model in (
                ("dcr", "oauth2dynamicclientregistration"),
                ("source_user", "source"), ("source_group", "source")):
            with self.subTest(relation=relation):
                self.api.foreign = [relation]
                pk = self.api.data["mapping"][0]["pk"]
                result = self.api.mapping_uses(pk)
                self.assertIn(expected_model, {row["model_name"] for row in result})
                self.assert_reference_refusal()
        self.api.foreign = []

    def test_used_by_malformed_oversize_and_unavailable_evidence_refuses(self):
        self.enroll().converge(hosts(), "present")
        def unavailable(rows):
            raise m.Refused()
        transforms = [
            lambda rows: {}, lambda rows: None, lambda rows: {"results": rows},
            lambda rows: rows * 251, lambda rows: rows[:1], lambda rows: [],
            lambda rows: rows + [rows[1]],
            lambda rows: [dict(rows[0], extra="unexpected"), rows[1]],
            lambda rows: [dict(rows[0], pk=103), rows[1]],
            lambda rows: [dict(rows[0], name="x" * 1025), rows[1]],
            lambda rows: [dict(rows[0], action="cascade"), rows[1]],
            lambda rows: [rows[0], dict(rows[1], name="editable display name")],
            lambda rows: [rows[0], dict(rows[1], pk="0")],
            lambda rows: [rows[0], dict(rows[1], name=f"ProviderPropertyMapping for Provider 999 and PropertyMapping {self.api.data['mapping'][0]['pk']}.")],
            unavailable,
        ]
        for index, transform in enumerate(transforms):
            with self.subTest(index=index):
                self.api.evidence = transform
                self.assert_reference_refusal()
        self.api.evidence = None

    def test_filtered_actor_evidence_refuses_before_used_by_and_effects(self):
        self.enroll().converge(hosts(), "present")
        for flag in (False, None, "true", 1):
            with self.subTest(flag=flag):
                self.api.superuser = flag
                reads = self.api.used_by_reads
                self.assert_reference_refusal()
                self.assertEqual(self.api.used_by_reads, reads)
        # The stand-in never presents filtered evidence as complete.
        pk = self.api.data["mapping"][0]["pk"]
        seam.references(pk, self.api.data["provider"])
        with self.assertRaises(AssertionError):
            seam.used_by(pk, superuser=False)

    def test_used_by_checks_actor_without_previous_application_load(self):
        api = m.APIClient("synthetic-admin-token")
        pk = "00000000-0000-4000-8000-000000000001"
        for actor in ({}, {"user": None}, {"user": {}}, {"user": {"is_superuser": 1}},
                      {"user": {"is_superuser": True}}):
            calls = []
            def request(method, path):
                calls.append((method, path))
                return actor if path == "/core/users/me/" else []
            api.request = request
            if isinstance(actor.get("user"), dict) and actor["user"].get("is_superuser") is True:
                self.assertEqual(api.mapping_uses(pk), [])
                self.assertEqual(calls[-1], ("GET", f"/propertymappings/all/{pk}/used_by/"))
            else:
                with self.assertRaises(m.Refused):
                    api.mapping_uses(pk)
                self.assertEqual(calls, [("GET", "/core/users/me/")])

    def test_rechecks_reference_evidence_before_view_key(self):
        self.enroll().converge(hosts(), "present")
        before_reads = self.api.used_by_reads
        def late_foreign(rows):
            # Two mappings pass the first guard, then drift appears on readback.
            if self.api.used_by_reads > before_reads + 2:
                return seam.references(self.api.data["mapping"][0]["pk"],
                                       self.api.data["provider"], ["dcr"])
            return rows
        self.api.evidence = late_foreign
        self.assert_reference_refusal()

    def test_unattached_mapping_requires_empty_evidence(self):
        self.enroll().converge(hosts(), "present")
        enrollment = self.enroll()
        # Model a confirmed bootstrap boundary before mappings are attached.
        enrollment.receipt["objects"]["provider"]["body"]["property_mappings"] = []
        self.api.data["provider"][0]["property_mappings"] = []
        enrollment.no_foreign_references()
        self.api.foreign = ["dcr"]
        with self.assertRaises(m.Refused):
            enrollment.no_foreign_references()

    def test_collections_still_require_complete_bounded_results(self):
        api = m.APIClient("synthetic-admin-token")
        for result in (
                {"results": [], "pagination": {"count": 1, "next": 0}},
                {"results": [], "pagination": {"count": 0, "next": 2}},
                {"results": [], "pagination": {}},
                {"results": [{}] * 501, "pagination": {"count": 501, "next": 0}}):
            api.request = lambda method, path: (
                {"user": {"is_superuser": True}} if path == "/core/users/me/" else result)
            with self.assertRaises(m.Refused):
                api.rows("provider")

    def test_inventory_collections_require_verified_superuser(self):
        api = m.APIClient("synthetic-admin-token")
        for kind, path in (
                ("provider", "/providers/all/?page_size=500"),
                ("application", "/core/applications/?page_size=500&superuser_full_list=true")):
            for flag in (False, None, "true", 1, True):
                with self.subTest(kind=kind, flag=flag):
                    calls = []
                    def request(method, path):
                        calls.append((method, path))
                        if path == "/core/users/me/":
                            return {"user": {"is_superuser": flag}}
                        return {"results": [], "pagination": {"count": 0, "next": 0}}
                    api.request = request
                    if flag is True:
                        self.assertEqual(api.rows(kind), [])
                        self.assertEqual(calls[-1], ("GET", path))
                    else:
                        with self.assertRaises(m.Refused):
                            api.rows(kind)
                        self.assertEqual(calls, [("GET", "/core/users/me/")])


    def test_public_inventory_and_nested_applications_never_request_foreign_secret(self):
        self.api = InventoryAPI()
        # The pinned privileged OAuth2 producer would expose the sentinel.
        self.assertEqual(inventory.oauth_provider(self.api.data["provider"][0])["client_secret"],
                         FOREIGN_SECRET)
        rows = self.api.rows("provider")
        self.assertEqual([r["pk"] for r in rows], [999])
        self.assertNotIn("client_secret", rows[0])
        self.assertNotIn("client_id", rows[0])
        apps = self.api.rows("application")
        self.assertEqual(apps[0]["provider_obj"]["pk"], 999)
        self.assertEqual(apps[0]["backchannel_providers_obj"][0]["pk"], 999)
        self.assertNotIn(FOREIGN_SECRET, json.dumps([rows, apps]))
        self.assertFalse(any("/providers/oauth2/" in c[1] for c in self.api.calls))

    def test_public_foreign_name_collision_never_authorizes_detail(self):
        self.api = InventoryAPI()
        self.api.data["provider"][0]["name"] = m.CLIENT
        with self.assertRaises(m.Refused):
            self.enroll().converge(hosts(), "present")
        self.assertFalse(any("/providers/oauth2/" in c[1] for c in self.api.calls))
        self.assertNotIn(FOREIGN_SECRET, (self.path / "receipt.json").read_text())

    def test_owned_detail_noop_rotation_cohort_and_global_removal(self):
        self.api = InventoryAPI()
        self.assertTrue(self.enroll().converge(hosts(3), "present"))
        owned = json.loads((self.path / "receipt.json").read_text())["objects"]
        pk = owned["provider"]["pk"]
        before = len(self.api.calls)
        self.assertFalse(self.enroll().converge(hosts(3), "present"))
        self.assertTrue(any(c[1] == f"/providers/oauth2/{pk}/" for c in self.api.calls[before:]))
        self.assertFalse(any(c[0] != "GET" for c in self.api.calls[before:]))
        self.assertTrue(self.enroll().converge(hosts(4), "present"))
        laptop = (self.path / "laptop.json").read_bytes()
        laptop_stat = (self.path / "laptop.json").stat()
        laptop_user = copy.deepcopy(next(r for r in self.api.data["user"]
                                        if r["username"] == "webmcp-interactive-laptop"))
        laptop_token = copy.deepcopy(next(r for r in self.api.data["token"]
                                         if r["identifier"] == "webmcp-interactive-laptop-g4"))
        cohort = hosts(4) + [dict(hosts()[0], host=h) for h in ("agent-a", "agent-b", "omarchy-pc")]
        m.declaration(cohort, OWNER, True, NOW)
        before = len(self.api.calls)
        self.assertTrue(self.enroll().converge(cohort, "present"))
        self.assertEqual((self.path / "laptop.json").read_bytes(), laptop)
        after_stat = (self.path / "laptop.json").stat()
        self.assertEqual((after_stat.st_ino, after_stat.st_mtime_ns),
                         (laptop_stat.st_ino, laptop_stat.st_mtime_ns))
        self.assertIn(laptop_user, self.api.data["user"])
        self.assertIn(laptop_token, self.api.data["token"])
        laptop_paths = (f"/core/users/{laptop_user['pk']}/",
                        "/core/tokens/webmcp-interactive-laptop-g4/")
        self.assertFalse(any(c[0] != "GET" and c[1] in laptop_paths
                             for c in self.api.calls[before:]))
        self.assertEqual(json.loads((self.path / "receipt.json").read_text())["objects"]["provider"]["pk"], pk)
        self.assertFalse(self.enroll().converge(cohort, "present"))
        self.assertNotIn(FOREIGN_SECRET, (self.path / "receipt.json").read_text())
        self.assertTrue(self.enroll().converge(cohort, "absent"))
        self.assertEqual([r["pk"] for r in self.api.data["provider"]], [999])
        self.assertFalse(self.enroll().converge(cohort, "absent"))

    def test_owned_inventory_collision_identity_and_body_drift_refuse(self):
        self.api = InventoryAPI()
        self.enroll().converge(hosts(), "present")
        receipt = json.loads((self.path / "receipt.json").read_text())
        owned = receipt["objects"]
        record = owned["provider"]
        pk = record["pk"]
        provider = next(r for r in self.api.data["provider"] if r["pk"] == pk)
        for change in ("foreign-name", "renamed", "missing-id", "lookup", "body"):
            with self.subTest(change=change):
                saved = copy.deepcopy(self.api.data["provider"])
                context = copy.deepcopy(owned)
                if change == "foreign-name":
                    self.api.data["provider"][0]["name"] = m.CLIENT
                elif change == "renamed":
                    provider["name"] = "renamed owned provider"
                elif change == "missing-id":
                    provider["pk"] = 998
                elif change == "lookup":
                    context["provider"]["lookup"] = 999
                else:
                    provider["grant_types"] = ["authorization_code"]
                before = len(self.api.calls)
                with self.assertRaises(m.Refused):
                    self.api.rows("provider", owned=context)
                details = [c[1] for c in self.api.calls[before:] if "/providers/oauth2/" in c[1]]
                self.assertEqual(details, [f"/providers/oauth2/{pk}/"] if change == "body" else [])
                self.api.data["provider"] = saved
                provider = next(r for r in saved if r["pk"] == pk)
        self.assertEqual(json.loads((self.path / "receipt.json").read_text()), receipt)

    def test_public_inventory_keeps_reference_and_pending_refusals(self):
        self.api = InventoryAPI()
        self.enroll().converge(hosts(), "present")
        self.api.data["provider"][0]["property_mappings"] = [self.api.data["mapping"][0]["pk"]]
        self.assert_reference_refusal()
        self.api.data["provider"][0]["property_mappings"] = []
        # A failed confirmed-ID PATCH leaves pending and prevents even discovery.
        self.api.fail_next = True
        with self.assertRaises(m.Refused):
            self.enroll().converge(hosts(state="absent"), "present")
        self.assertEqual(json.loads((self.path / "receipt.json").read_text())["pending"]["method"], "PATCH")
        before = len(self.api.calls)
        with self.assertRaises(m.Refused):
            self.enroll()
        self.assertEqual(len(self.api.calls), before)

    def test_unsafe_shared_scratch_ancestor_is_rejected(self):
        shared = self.path / "shared"
        private = shared / "private"
        private.mkdir(parents=True, mode=0o700)
        shared.chmod(0o777)
        with self.assertRaises(m.Refused):
            m.secure_dir(str(private))
        shared.chmod(0o700)
        fd = m.secure_dir(str(private))
        os.close(fd)

    def test_uncertain_operation_never_replayed(self):
        enrollment = self.enroll()
        self.api.fail_next = True
        with self.assertRaises(m.Refused):
            enrollment.converge(hosts(), "present")
        receipt = json.loads((self.path / "receipt.json").read_text())
        self.assertEqual(receipt["pending"]["method"], "POST")
        count = len(self.api.calls)
        with self.assertRaises(m.Refused):
            self.enroll()
        self.assertEqual(len(self.api.calls), count)

    def test_no_receipt_adoption_or_shared_files(self):
        (self.path / "foreign").write_text("public")
        with self.assertRaises(m.Refused):
            self.enroll()
        (self.path / "foreign").unlink()
        self.enroll()
        with self.assertRaises(m.Refused):
            m.Enrollment(self.api, self.fd, "10000000-0000-4000-8000-000000000000")
        (self.path / "receipt.json").chmod(0o640)
        with self.assertRaises(m.Refused):
            self.enroll()

    def test_complete_issuer_and_claims(self):
        self.enroll().converge(hosts(), "present")
        provider = self.api.data["provider"][0]
        self.assertEqual(provider["grant_types"], ["client_credentials"])
        self.assertEqual(provider["redirect_uris"], [])
        self.assertEqual(provider["access_token_validity"], "seconds=300")
        self.assertTrue(provider["include_claims_in_id_token"])
        self.assertEqual(provider["signing_key"], m.SIGNING_KEY)
        self.assertEqual(len(provider["property_mappings"]), 2)
        self.assertEqual(self.api.data["application"][0]["policy_engine_mode"], "all")
        self.assertEqual(len(self.api.data["binding"]), 1)
        identity, policy = m.expressions(self.api.data["user"], OWNER)
        empty = NS(exists=lambda: False)
        user = NS(**{k: v for k, v in self.api.data["user"][0].items() if k != "groups"}, groups=empty)
        user.roles = empty
        provider = NS(client_id=m.CLIENT, application=NS(slug=m.CLIENT))
        def evaluate(source, **context):
            local = {}
            exec("def expression():\n" + "\n".join("    " + line for line in source.splitlines()), context, local)
            return local["expression"]()
        claims = evaluate(identity, user=user, provider=provider)
        self.assertEqual(claims, {"aud": m.RESOURCE, "jocasta_operator": True, "webmcp_identity": user.username})
        self.assertNotIn("jocasta_projects", claims)
        class Post(dict):
            def getlist(self, key):
                return [self[key]] if key in self else []
        post = Post(client_id=m.CLIENT, username=user.username, password=SECRET,
                    grant_type="client_credentials", resource=m.RESOURCE, scope="jocasta.read jocasta.write")
        request = NS(user=user, http_request=NS(method="POST", POST=post),
                     context={"oauth_grant_type": "client_credentials", "oauth_scopes": {"jocasta.read", "jocasta.write"}})
        self.assertTrue(evaluate(policy, request=request))
        for field, value in (("resource", "https://other.invalid/mcp"), ("username", "igor"),
                             ("client_secret", SECRET), ("client_assertion", SECRET),
                             ("scope", "jocasta.read"), ("scope", "jocasta.read jocasta.write offline_access")):
            old = dict(post)
            post[field] = value
            self.assertFalse(evaluate(policy, request=request))
            post.clear()
            post.update(old)
        original = user.pk
        user.pk = 4
        self.assertFalse(evaluate(policy, request=request))
        self.assertEqual(evaluate(identity, user=user, provider=provider), {})
        user.pk = original
        provider.client_id = "other"
        self.assertEqual(evaluate(identity, user=user, provider=provider), {})

    def test_transport_redirect_and_failure_are_generic(self):
        self.assertRaises(m.Refused, m.NoRedirect().redirect_request, None, None, None, None, None, None)
        api = m.APIClient("synthetic-admin-token")
        class Opener:
            def open(self, *args, **kwargs):
                raise RuntimeError(SECRET)
        api.opener = Opener()
        with self.assertRaises(m.Refused) as failure:
            api.request("POST", "/core/users/", {"username": "fixture"})
        self.assertNotIn(SECRET, str(failure.exception))


if __name__ == "__main__":
    unittest.main()
