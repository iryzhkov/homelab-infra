#!/usr/bin/env python3
"""Synthetic contract tests. Run only in an isolated job with asserted HOME."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "filter_plugins"))
import service_access as sa


def sample():
    placement = {"state": "verified-none", "origin": "synthetic fixture",
                 "observed_at": "2026-10-30T00:00:00Z", "entries": {}}
    placement["sha256"] = hashlib.sha256(sa.canonical({}).encode()).hexdigest()
    return {
        "version": 1, "resolved": True, "source_pin": "0" * 40, "placement": placement,
        "channels": [{"id": x, "context": x, "classification": "transport",
                      "owner": "fixture", "qualified": True} for x in sa.CONTEXTS],
        "backends": [{"id": "app", "kind": "swarm", "strategy": "load_balance",
                      "owner": "fixture", "provenance": "fixture:app",
                      "endpoints": [{"host": "worker-b", "url": "http://b.invalid:8080/root"},
                                    {"host": "worker-a", "url": "http://a.invalid:8080/root"}],
                      "health": False}],
        "services": [{
            "id": "app", "hostname": "app.invalid", "owner": "fixture", "backend_ref": "app",
            "application_auth": {"mode": "native_login", "owner": "fixture", "coverage": "fixture/API"},
            "contexts": {x: {"allowed": True, "channel": x, "proxy_auth": "none"} for x in sa.CONTEXTS},
            "publication": {"approved": False}, "dependencies": [], "dns_ref": "fixture:dns",
            "hold_ref": "fixture:hold", "custody_ref": "fixture:custody",
            "routes": [{"id": "app-lan", "context": "lan", "entrypoints": ["private"],
                        "rule": "Host(app.invalid)", "priority": 1, "middlewares": ["first", "second"],
                        "callback": {}, "tls": {"resolver": "existing"}, "provenance": "fixture:route"}]
        }], "unmodelled_routes": []}


def codes(result):
    return {x["code"] for x in result["diagnostics"]}


class Contract(unittest.TestCase):
    def test_determinism_and_purity(self):
        data = sample()
        before = copy.deepcopy(data)
        with patch("builtins.open", side_effect=AssertionError("ambient file read")), \
             patch.dict(os.environ, {"HOME": "/unavailable", "SSH_AUTH_SOCK": "/forbidden"}):
            first = sa.plan(data, data)
            second = sa.plan(data, data)
        self.assertEqual(data, before)
        self.assertEqual(first, second)
        self.assertTrue(first["planning_ready"])
        self.assertTrue(first["model_equivalent"])
        self.assertFalse(first["deployment_ready"])
        self.assertEqual(first["changes"], [])

    def test_placement_unknown_never_reads_home(self):
        data = sample()
        data["placement"]["state"] = "unknown"
        with patch("builtins.open", side_effect=AssertionError("HOME read")):
            result = sa.plan(data, data)
        self.assertIn("placement_unknown", codes(result))
        self.assertFalse(result["model_equivalent"])

    def test_placement_integrity_and_resolution(self):
        data = sample()
        backend = data["backends"][0]
        backend.update(kind="compose", host="worker-a")
        data["placement"].update(state="present", entries={"app": "worker-b"})
        self.assertIn("placement_hash", codes(sa.plan(data)))
        data["placement"]["sha256"] = hashlib.sha256(sa.canonical({"app": "worker-b"}).encode()).hexdigest()
        result = sa.plan(data)
        self.assertTrue(result["planning_ready"])
        self.assertEqual(result["backends"]["app"]["endpoints"][0]["host"], "worker-b")
        data["placement"]["entries"]["app"] = "absent"
        self.assertIn("placement_endpoint", codes(sa.plan(data)))

    def test_swarm_wins_once_and_preserves_provenance(self):
        data = sample()
        retained = copy.deepcopy(data["backends"][0])
        retained.update(kind="compose", provenance="retained-compose")
        data["backends"].append(retained)
        result = sa.plan(data)
        self.assertTrue(result["planning_ready"])
        self.assertEqual(result["backends"]["app"]["kind"], "swarm")
        self.assertEqual(len(result["backends"]), 1)
        self.assertIn("retained-compose", result["backends"]["app"]["retained_provenance"])
        self.assertEqual([x["host"] for x in result["backends"]["app"]["endpoints"]], ["worker-a", "worker-b"])
        data["backends"].append(copy.deepcopy(data["backends"][0]))
        self.assertIn("backend_ambiguity", codes(sa.plan(data)))

    def test_behavior_changes_are_visible(self):
        baseline = sample()
        for modify in (
            lambda x: x["services"][0]["routes"][0].update(middlewares=["second", "first"]),
            lambda x: x["services"][0]["routes"][0].update(priority=2),
            lambda x: x["services"][0]["routes"][0].update(entrypoints=["public"]),
            lambda x: x["services"][0]["routes"][0].update(tls={"resolver": "changed"}),
            lambda x: x["backends"][0].update(health={"path": "/ready"}),
            lambda x: x["backends"][0].update(strategy="failover"),
        ):
            with self.subTest(modify=modify):
                data = copy.deepcopy(baseline)
                modify(data)
                result = sa.plan(data, baseline)
                self.assertTrue(result["changes"])
                self.assertFalse(result["model_equivalent"])

    def test_failover_order_preserved(self):
        data = sample()
        data["backends"][0]["strategy"] = "failover"
        before = copy.deepcopy(data)
        data["backends"][0]["endpoints"].reverse()
        result = sa.plan(data, before)
        self.assertTrue(result["changes"])
        self.assertEqual(result["backends"]["app"]["endpoints"], data["backends"][0]["endpoints"])

    def test_context_identity_and_duplicates(self):
        data = sample()
        route = copy.deepcopy(data["services"][0]["routes"][0])
        route.update(id="app-public", context="public", entrypoints=["public"])
        data["services"][0]["routes"].append(route)
        self.assertTrue(sa.plan(data)["planning_ready"])
        data["services"][0]["routes"].append(copy.deepcopy(route))
        self.assertIn("router_duplicate", codes(sa.plan(data)))
        self.assertIn("router_overlap", codes(sa.plan(data)))

    def test_native_has_no_implicit_gate_and_explicit_gate_is_required(self):
        data = sample()
        for mode in ("native_login", "native_oidc"):
            auth = data["services"][0]["application_auth"]
            auth.update(mode=mode, issuer_ref="auth", callback_ref="callback", secret_ref="reference-only")
            result = sa.plan(data)
            self.assertEqual(result["services"]["app"]["contexts"]["public"]["proxy_auth"], "none")
        data["services"][0]["contexts"]["public"].update(proxy_auth="forward_auth",
            provider_ref="provider", callback_ref="callback")
        self.assertIn("extra_gate", codes(sa.plan(data)))
        data["services"][0]["contexts"]["public"]["extra_gate_decision"] = "fixture:explicit-decision"
        self.assertNotIn("extra_gate", codes(sa.plan(data)))

    def test_callback_and_dependency_boundaries(self):
        data = sample()
        data["services"][0]["routes"][0]["callback"] = {
            "path": "/callback", "priority": 100, "proxy_auth": "forward_auth", "backend_ref": "absent"}
        self.assertIn("callback_recursion", codes(sa.plan(data)))
        self.assertIn("callback_backend", codes(sa.plan(data)))
        data["services"][0]["dependencies"] = ["missing"]
        self.assertIn("dependency_missing", codes(sa.plan(data)))
        data["services"][0]["dependencies"] = ["app"]
        self.assertIn("dependency_cycle", codes(sa.plan(data)))

    def test_common_move_checker(self):
        data = sample()
        dependency = copy.deepcopy(data["services"][0])
        dependency.update(id="auth", hostname="auth.invalid", routes=[
            dict(dependency["routes"][0], id="auth-lan", rule="Host(auth.invalid)")])
        data["services"].append(dependency)
        data["services"][0]["dependencies"] = ["auth"]
        self.assertIn("move_dependency", codes(sa.plan(data, move_set=["app"], available=[])))
        self.assertTrue(sa.plan(data, move_set=["app"], available=["auth"])["planning_ready"])
        self.assertIn("move_available", codes(sa.plan(data, move_set=["app"])))

    def test_whisper_private_policy_and_forwarder_boundary(self):
        data = sample()
        svc = data["services"][0]
        svc.update(id="whisper", lan_segments=sorted(sa.TRUSTED_LAN), nebula_peers="all-valid-direct",
            controls={"ordinary_denied_paths": ["/load"], "owner_ref": "fixture",
                      "backend_policy_ref": "fixture"})
        svc["contexts"]["public"]["allowed"] = False
        self.assertTrue(sa.plan(data)["planning_ready"])
        svc["publication"] = {"approved": True, "channel": "nebula", "owner": "fixture"}
        result = sa.plan(data)
        self.assertIn("private_service", codes(result))
        self.assertIn("publication_channel", codes(result))
        svc["lan_segments"] = ["192.168.0.0/16"]
        svc["nebula_peers"] = "except-oci"
        self.assertIn("trusted_lan", codes(sa.plan(data)))
        self.assertIn("nebula_entitlement", codes(sa.plan(data)))
        data["channels"][0]["classification"] = "X-Forwarded-For"
        self.assertIn("source_classification", codes(sa.plan(data)))

    def test_unknowns_and_specials_block_equivalence(self):
        data = sample()
        data["unmodelled_routes"] = [{"owner": "fixture", "receipt": "fixture:special"}]
        self.assertFalse(sa.plan(data, data)["model_equivalent"])
        data["services"][0]["hold_ref"] = ""
        self.assertFalse(sa.plan(data)["planning_ready"])
        data["backends"][0]["endpoints"][0]["url"] = "{{ lookup('env', 'HOME') }}"
        self.assertIn("unresolved_lookup", codes(sa.plan(data)))
        data["services"][0]["password"] = "not-a-real-password"
        self.assertIn("unsupported_field", codes(sa.plan(data)))

    def test_malformed_input_returns_diagnostics(self):
        for value in (None, [], {"version": 1}, dict(sample(), services=[{"id": []}]),
                      dict(sample(), channels=[{"id": "bad", "context": []}]),
                      dict(sample(), backends=[{"id": "bad", "kind": []}])):
            with self.subTest(value=value):
                self.assertFalse(sa.plan(value)["planning_ready"])

    def test_cli_explicit_files_and_no_apply_mode(self):
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(sample()))
            cli = [sys.executable, "-B", str(ROOT / "scripts/plan-service-access.py"), str(path)]
            first = subprocess.run(cli, capture_output=True, text=True)
            second = subprocess.run(cli, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            result = json.loads(first.stdout)
            self.assertFalse(result["deployment_ready"])
            self.assertNotEqual(subprocess.run(cli + ["--apply"], capture_output=True).returncode, 0)
            path.write_text('{"version":1,"version":2}')
            self.assertNotEqual(subprocess.run(cli, capture_output=True).returncode, 0)


if __name__ == "__main__":
    expected = os.environ.get("SERVICE_ACCESS_TEST_HOME")
    if not expected or os.environ.get("HOME") != expected or not expected.endswith("/.cache/service-access-tests/home"):
        raise SystemExit("Refusing tests without the asserted isolated service-access HOME")
    if not os.environ.get("TMPDIR", "").startswith(expected + "/"):
        raise SystemExit("Refusing tests without disk scratch TMPDIR under isolated HOME")
    unittest.main()
