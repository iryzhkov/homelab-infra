#!/usr/bin/env python3
"""Authentik drift must name every change to who may open what, and fail loudly when it cannot look."""
import copy
import hashlib
import importlib.machinery
import importlib.util
import sys

sys.dont_write_bytecode = True
import unittest
import urllib.error
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "roles/drift_check/files/homelab-drift-check"
loader = importlib.machinery.SourceFileLoader("checker", str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
checker = importlib.util.module_from_spec(spec)
loader.exec_module(checker)

CONTENT = "version: 1\nentries: []"
MANIFEST = {
    "blueprint": "homelab-infra access",
    "blueprint_sha256": hashlib.sha256(CONTENT.encode()).hexdigest(),
    "apps": {"budget": {"groups": ["family"], "users": []},
             "ai": {"groups": [], "users": ["igor"]}},
    "groups": {"family": ["igor", "lisa"]},
}
# What Authentik holds when nothing has drifted.
CLEAN = {
    "content": CONTENT,
    "status": "successful",
    "apps": {
        "budget": {"pk": "b", "policy_engine_mode": "any",
                   "bindings": [{"group_obj": {"name": "family"}, "enabled": True, "negate": False}]},
        "ai": {"pk": "a", "policy_engine_mode": "any",
               "bindings": [{"user_obj": {"username": "igor"}, "enabled": True, "negate": False}]},
    },
    "groups": {"family": ["igor", "lisa"]},
    "providers": ["budget", "ai"],
}


def fake(state):
    """An Authentik API answering from `state`, shaped like CLEAN."""
    by_pk = {app["pk"]: app for app in state["apps"].values()}

    def get(path):
        if path.startswith("/managed/blueprints/"):
            return {"results": [{"content": state["content"], "status": state["status"]}]}
        if path.startswith("/core/applications/"):
            slug = path.split("/")[3]
            if "check_access" in path:
                raise AssertionError("check_access answers for the caller, not for_user")
            if slug not in state["apps"]:
                raise urllib.error.HTTPError(path, 404, "Not Found", None, None)
            return state["apps"][slug]
        if path.startswith("/policies/bindings/"):
            pk = path.split("target=")[1].split("&")[0]
            return {"results": by_pk[pk]["bindings"]}
        if path.startswith("/core/groups/"):
            name = path.split("name=")[1].split("&")[0]
            members = state["groups"].get(name)
            return {"results": [] if members is None else
                    [{"users_obj": [{"username": u} for u in members]}]}
        if path.startswith("/providers/oauth2/"):
            return {"results": [{"assigned_application_slug": s} for s in state["providers"]]}
        if path.startswith("/providers/"):
            return {"results": []}
        raise AssertionError(path)
    return get


class AuthentikDriftTest(unittest.TestCase):
    def observe(self, change=None):
        state = copy.deepcopy(CLEAN)
        if change:
            change(state)
        return checker.authentik_findings(MANIFEST, fake(state))

    def test_matching_state_is_quiet(self):
        self.assertEqual(self.observe(), ([], False))

    def test_bindings_added_removed_and_disabled(self):
        def change(state):
            state["apps"]["budget"]["bindings"].append(
                {"user_obj": {"username": "kostya"}, "enabled": True, "negate": False})
            state["apps"]["ai"]["bindings"][0]["enabled"] = False
            state["apps"]["ai"]["policy_engine_mode"] = "all"
        findings, broken = self.observe(change)
        self.assertFalse(broken)
        self.assertEqual(findings, [
            "Authentik ai: policy engine mode is all, the repository expects any",
            "Authentik ai: binding to user igor is disabled",
            "Authentik ai: binding to user igor is missing",
            "Authentik budget: bound to user kostya outside the repository",
        ])

    def test_group_membership(self):
        def change(state):
            state["groups"]["family"] = ["igor", "mallory"]
        findings, _ = self.observe(change)
        self.assertEqual(findings, [
            "Authentik group family: mallory added outside the repository",
            "Authentik group family: lisa removed outside the repository",
        ])

    def test_blueprint_edited_or_failing(self):
        def change(state):
            state["content"] += "\n# edited"
            state["status"] = "error"
        findings, _ = self.observe(change)
        self.assertEqual(findings, [
            "Authentik: blueprint 'homelab-infra access' changed outside the repository",
            "Authentik: blueprint 'homelab-infra access' is error",
        ])

    def test_unmanaged_and_deleted_applications(self):
        def change(state):
            del state["apps"]["ai"]
            state["providers"] = ["budget", "wiki"]
        findings, broken = self.observe(change)
        self.assertFalse(broken)
        self.assertEqual(findings, [
            "Authentik ai: application is gone",
            "Authentik wiki: application not managed by the repository",
        ])

    def test_standalone_provider_requires_reviewed_exact_identity(self):
        state = copy.deepcopy(CLEAN)
        state["providers"].append("machine")
        get_base = fake(state)
        def get(path):
            result = get_base(path)
            if path.startswith("/providers/oauth2/"):
                for row in result["results"]:
                    if row["assigned_application_slug"] == "machine":
                        row["pk"] = 42
            return result
        owners = {"machine": {"provider": 42, "owner": "enrollment"}}
        self.assertEqual(checker.authentik_findings(MANIFEST, get, owners), ([], False))
        owners["machine"]["provider"] = 43
        self.assertIn("Authentik machine: application not managed by the repository",
                      checker.authentik_findings(MANIFEST, get, owners)[0])
        # An ownership entry cannot suppress household access drift.
        owners["ai"] = {"provider": 1, "owner": "other"}
        state["apps"]["ai"]["bindings"] = []
        self.assertIn("Authentik ai: binding to user igor is missing",
                      checker.authentik_findings(MANIFEST, get, owners)[0])

    def test_standalone_compose_requires_name_and_owner_path(self):
        projects = {"ov-http": {"ConfigFiles": "/opt/releases/v1/compose.yml"},
                    "unknown": {"ConfigFiles": "/opt/releases/v1/compose.yml"}}
        owners = {"ov-http": {"owner": "release role", "config": "/opt/releases/*/compose.yml"}}
        self.assertEqual(checker.standalone_projects(projects, owners), {"ov-http"})
        for config in ("/elsewhere/compose.yml",
                       "/opt/releases/v1/compose.yml,/etc/unowned/compose.yml",
                       "/opt/releases/nested/v1/compose.yml"):
            projects["ov-http"]["ConfigFiles"] = config
            self.assertEqual(checker.standalone_projects(projects, owners), set())

    def test_unreachable_api_is_broken_not_clean(self):
        def get(path):
            raise urllib.error.URLError("connection refused")
        findings, broken = checker.authentik_findings(MANIFEST, get)
        self.assertTrue(broken)
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].startswith("Authentik: drift observation failed"))


if __name__ == "__main__":
    unittest.main()
