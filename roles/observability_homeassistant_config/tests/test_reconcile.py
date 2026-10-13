"""Guarded HA SSH reconciliation acceptance; no real remote calls."""
import base64
import contextlib
import io
import json
import pathlib
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROLE = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = ROLE / "files/homelab_prometheus.yaml"
INCLUDE = "    homelab_prometheus: !include packages/homelab_prometheus.yaml\n"
BASE = "default_config:\nhomeassistant:\n  packages:\n    existing: !include packages/existing.yaml\n"

class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.main = runpy.run_path(str(ROLE / "files/reconcile.py"))["main"]
        self.calls = []
        self.config = BASE.replace("  packages:\n", "  packages:\n" + INCLUDE)
        self.package = PACKAGE.read_text()
        self.fail_check = False

    def remote(self, argv, **kwargs):
        command = argv[-1]
        self.calls.append(command)
        if command == "cat /config/configuration.yaml":
            output = self.config
        elif command.startswith("test ! -f"):
            output = self.package
        elif command == "cd /config && git rev-parse HEAD":
            output = "a" * 40 + "\n"
        elif command == "ha core check" and self.fail_check:
            return subprocess.CompletedProcess(argv, 1, "PRIVATE", "PRIVATE")
        else:
            output = ""
        if " | base64 -d > " in command:
            import shlex
            fields = shlex.split(command)
            encoded = fields[fields.index("printf") + 2]
            target = fields[fields.index(">") + 1]
            value = base64.b64decode(encoded).decode()
            if target.startswith("/config/configuration.yaml"):
                self.config = value
            else:
                self.package = value
        if command == "rm -f /config/packages/homelab_prometheus.yaml":
            self.package = ""
        return subprocess.CompletedProcess(argv, 0, output, "")

    def invoke(self, *args):
        output = io.StringIO()
        with patch.object(sys, "argv", ["reconcile", "--package", str(PACKAGE), *args]), \
             patch.object(subprocess, "run", self.remote), contextlib.redirect_stdout(output):
            self.main()
        return json.loads(output.getvalue())

    def test_normal_converged_does_not_write_or_restart(self):
        self.assertFalse(self.invoke("--maintenance", "--restart")["changed"])
        self.assertEqual(len(self.calls), 2)

    def test_check_drift_does_not_write_even_with_restart_flags(self):
        self.config, self.package = BASE, ""
        self.assertTrue(self.invoke("--check", "--maintenance", "--restart")["changed"])
        self.assertEqual(len(self.calls), 2)

    def test_drift_without_maintenance_refuses_before_write(self):
        self.config = BASE
        with self.assertRaisesRegex(RuntimeError, "requires explicit maintenance"):
            self.invoke("--restart")
        self.assertEqual(len(self.calls), 2)

    def test_invalid_check_restores_existing_package_without_restart(self):
        self.config, self.package, self.fail_check = BASE, "# previous\n", True
        before = (self.config, self.package)
        with self.assertRaisesRegex(RuntimeError, "restored"):
            self.invoke("--maintenance", "--restart")
        self.assertEqual((self.config, self.package), before)
        self.assertNotIn("ha core restart", self.calls)

    def test_invalid_check_removes_previously_absent_package(self):
        self.config, self.package, self.fail_check = BASE, "", True
        with self.assertRaisesRegex(RuntimeError, "restored"):
            self.invoke("--maintenance", "--restart")
        self.assertEqual((self.config, self.package), (BASE, ""))
        self.assertNotIn("ha core restart", self.calls)

    def test_valid_change_without_restart_stages_checked_source(self):
        self.config, self.package = BASE, ""
        result = self.invoke("--maintenance")
        self.assertTrue(result["configuration_valid"])
        self.assertFalse(result["restart"])
        self.assertIn(INCLUDE.strip(), self.config)
        backup_index = next(i for i, x in enumerate(self.calls) if "tar -cf" in x)
        write_index = next(i for i, x in enumerate(self.calls) if "base64 -d" in x)
        self.assertLess(backup_index, write_index)
        self.assertNotIn("ha core restart", self.calls)

    def test_valid_change_restarts_once_only_when_explicit(self):
        self.config, self.package = BASE, ""
        self.assertTrue(self.invoke("--maintenance", "--restart")["restart"])
        self.assertEqual(self.calls.count("ha core restart"), 1)

if __name__ == "__main__":
    unittest.main()
