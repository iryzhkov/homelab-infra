#!/usr/bin/env python3
"""Offline Authentik contract/journal tests. No real store, HTTP or credentials."""
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
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

    def rows(self, kind):
        if kind == "all_provider":
            # The common endpoint includes OAuth2 and other provider subclasses.
            return copy.deepcopy(self.data["provider"] + self.data[kind])
        return copy.deepcopy(self.data[kind])

    def request(self, method, path, body=None, public=False):
        self.calls.append((method, path, copy.deepcopy(body), public))
        if self.fail_next and method in ("POST", "PATCH", "DELETE"):
            self.fail_next = False
            raise m.Refused()
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

    def test_proxy_mapping_reference_refuses_present_and_global_revocation(self):
        self.enroll().converge(hosts(), "present")
        self.api.data["all_provider"].append({
            "pk": 999, "name": "unrelated-proxy",
            "meta_model_name": "authentik_providers_proxy.proxyprovider",
            "property_mappings": [self.api.data["mapping"][0]["pk"]],
        })
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

    def test_incomplete_common_provider_reference_evidence_refuses(self):
        self.enroll().converge(hosts(), "present")
        self.api.data["all_provider"].append({"pk": 999})
        before = len(self.api.calls)
        with self.assertRaises(m.Refused):
            self.enroll().converge(hosts(), "present")
        self.assertFalse(any(method != "GET" or path.endswith("/view_key/")
                             for method, path, _, _ in self.api.calls[before:]))

    def test_common_provider_collection_requires_complete_bounded_results(self):
        api = m.APIClient("synthetic-admin-token")
        called = []
        response = {"results": [], "pagination": {"count": 0, "next": 0}}
        def request(method, path):
            called.append((method, path))
            return response
        api.request = request
        self.assertEqual(api.rows("all_provider"), [])
        self.assertEqual(called, [("GET", "/providers/all/?page_size=500")])
        for pagination in ({"count": 1, "next": 0}, {"count": 0, "next": 2}, {}):
            response["pagination"] = pagination
            with self.assertRaises(m.Refused):
                api.rows("all_provider")

    def test_application_collection_requires_verified_superuser(self):
        api = m.APIClient("synthetic-admin-token")
        for flag in (False, None, "true", 1, True):
            with self.subTest(flag=flag):
                calls = []
                def request(method, path):
                    calls.append((method, path))
                    if path == "/core/users/me/":
                        return {"user": {"is_superuser": flag}}
                    return {"results": [], "pagination": {"count": 0, "next": 0}}
                api.request = request
                if flag is True:
                    self.assertEqual(api.rows("application"), [])
                    self.assertEqual(calls[-1], ("GET", "/core/applications/?page_size=500&superuser_full_list=true"))
                else:
                    with self.assertRaises(m.Refused):
                        api.rows("application")
                    self.assertEqual(calls, [("GET", "/core/users/me/")])

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
