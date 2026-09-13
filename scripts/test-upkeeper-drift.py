#!/usr/bin/env python3
"""The fleet projection must make dry-run changes visible even with exit zero."""
import importlib.machinery
import importlib.util
import json
import subprocess
import sys

sys.dont_write_bytecode = True
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "roles/drift_check/files/homelab-drift-check"
loader = importlib.machinery.SourceFileLoader("checker", str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
checker = importlib.util.module_from_spec(spec)
loader.exec_module(checker)


class DriftTest(unittest.TestCase):
    def observe(self, hosts, code=0):
        with patch.object(checker.os.path, "isfile", return_value=True), \
             patch.object(checker.shutil, "which", return_value="/bin/upkeeper"), \
             patch.object(checker.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                [], code, json.dumps({"hosts": hosts}), "")
            result = checker.upkeeper_findings()
            self.assertEqual(run.call_args.args[0][1:],
                             ["pull", "--all", "--skip-unreachable", "--dry-run", "--json"])
            return result

    def test_clean_and_offline_are_quiet(self):
        self.assertEqual(self.observe([
            {"host": "normandy", "reachable": True, "result": "converged",
             "components": [{"name": "huyang", "state": "converged"}]},
            {"host": "laptop", "skipped_unreachable": True},
            {"host": "laptop", "reachable": False, "self_managed": True},
        ]), ([], False))

    def test_dry_run_updated_is_drift_even_with_exit_zero(self):
        findings, broken = self.observe([
            {"host": "normandy", "reachable": True, "result": "updated",
             "components": [{"name": "agent-environment", "state": "updated"}]},
        ])
        self.assertEqual(findings, ["UpKeeper normandy/agent-environment: updated"])
        self.assertFalse(broken)

    def test_failed_observation_is_checker_failure(self):
        findings, broken = self.observe([{"host": "homelab", "reachable": False}], 40)
        self.assertTrue(findings)
        self.assertTrue(broken)

    def test_unconfigured_hosts_do_not_need_upkeeper(self):
        with patch.object(checker.os.path, "isfile", return_value=False), \
             patch.object(checker.subprocess, "run", side_effect=AssertionError("called")):
            self.assertEqual(checker.upkeeper_findings(), ([], False))


if __name__ == "__main__":
    unittest.main()
