#!/usr/bin/env python3
"""Freshness regressions: no SSH, cloud writes, or notifications."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "roles/offsite_phone/files/homelab-offsite-run"


class FreshnessTests(unittest.TestCase):
    def run_case(self, timestamps, status=0):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            stage = home / "stage"
            stage.mkdir()
            binaries = home / "bin"
            binaries.mkdir()
            calls = home / "calls"
            config = home / "env"
            config.write_text(f'OFFSITE_STAGE="{stage}"\n'
                              'OFFSITE_TARGETS="oci:oci:a:7 phone:phone:a:7 travel-router:router:a:2"\n')
            for name, seconds_ago in timestamps.items():
                (stage / f".last-delivery-{name}").write_text(str(int(time.time()) - seconds_ago) + "\n")
            # Simulate a successful aggregate delivery regardless of phone age.
            (stage / ".last-delivery").write_text(str(int(time.time())) + "\n")
            for name, body in {
                "homelab-offsite-bundle": f"exit {status}\n",
                "feed": 'printf "%s\\n" "$*" >> "$CALLS"\n',
                "homelab-notify": 'cat >/dev/null\n',
            }.items():
                file = binaries / name
                file.write_text("#!/bin/sh\n" + body)
                file.chmod(0o755)
            env = dict(os.environ, HOME=str(home), HOMELAB_OFFSITE_CONFIG=str(config),
                       CALLS=str(calls), PATH=str(binaries) + ":" + os.environ["PATH"])
            run = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
            return run.returncode, calls.read_text() if calls.exists() else ""

    def test_oci_success_does_not_hide_stale_phone(self):
        status, calls = self.run_case({"oci": 0, "phone": 4 * 86400})
        self.assertNotEqual(status, 0)
        self.assertIn("phone backup stale", calls)
        self.assertNotIn("oci backup stale", calls)

    def test_fresh_required_copies_and_absent_router_are_healthy(self):
        self.assertEqual(self.run_case({"oci": 0, "phone": 3600}), (0, ""))

    def test_never_delivered_phone_is_not_reported_as_999_days(self):
        status, calls = self.run_case({"oci": 0})
        self.assertNotEqual(status, 0)
        self.assertIn("never delivered", calls)
        self.assertNotIn("999d", calls)

    def test_build_failure_is_not_hidden(self):
        status, calls = self.run_case({"oci": 0, "phone": 0}, status=1)
        self.assertEqual(status, 1)
        self.assertIn("could not be built", calls)

    def test_zero_deliveries_is_a_service_failure(self):
        status, _ = self.run_case({"oci": 0, "phone": 0}, status=2)
        self.assertNotEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
