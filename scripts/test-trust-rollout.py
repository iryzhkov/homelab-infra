#!/usr/bin/env python3
"""Rollout contracts and a fresh synthetic --check; no host connections or keys."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]

class RolloutTest(unittest.TestCase):
    def test_registry_and_config_expose_only_the_first_sources(self):
        v = yaml.safe_load((ROOT / "vars/chaldea-runtime.yml").read_text())
        v.update(yaml.safe_load((ROOT / "vars/akasha-registry.yml").read_text()))
        v.update(akasha_state_root="/var/lib/akasha", akasha_nats_seed_file="/etc/akasha/nats.seed")
        env = Environment(undefined=StrictUndefined)
        env.filters["to_nice_json"] = json.dumps
        c = json.loads(env.from_string((ROOT / "roles/akasha/templates/config.json.j2").read_text()).render(v))
        self.assertEqual(c["access_state"], "/var/lib/akasha/access-state.db")
        self.assertEqual(c["bundle_file"], "/var/lib/chaldea-access-trust-public/bundle.jws")
        self.assertEqual(c["clock_health_file"], "/run/chaldea-access-trust/clock-health.json")
        self.assertEqual(c["anchors"], [{
            "kid": "deployment-2026-10",
            "public_key": {"kty": "OKP", "crv": "Ed25519", "x": "re7EPlrspBhs_OHsN7z1VqDZ8HkVib4glgZCczaKjRc"}}])
        self.assertEqual([(r["source"],r["profile"],r["project"],r["kinds"]) for r in c["registry"]], [
            ("chaldea.daily-health.agent-a","daily-health","homelab",["report"]),
            ("agent-a.fleet","fleet-timers","chaldea",["job_finished"]),
            ("agent-a.toolfeedback","toolfeedback","chaldea",["tool_call"])])
        self.assertTrue(all(r["host"] == "agent-a" and not r.get("global_read") and not r.get("roots")
                            for r in c["registry"]))
        self.assertEqual(c["bind"], "127.0.0.1")
        self.assertEqual(c["port"], 1935)

    def test_ported_units_and_bootstrap_never_create_credentials_or_state(self):
        role = ROOT / "roles/chaldea_access_trust"
        tasks = (role / "tasks/main.yml").read_text()
        self.assertNotIn("initialize-state", tasks)
        self.assertNotIn("genpkey", tasks)
        self.assertNotIn("ssh-keygen", tasks)
        self.assertIn('mode: "0600"', tasks)
        clock = (role / "templates/chaldea-clock-health.service.j2").read_text()
        self.assertIn("systemd-timesyncd.service", clock)
        self.assertIn("-/run/chrony", clock)
        updater = (role / "templates/chaldea-access-trust.service.j2").read_text()
        for value in ["User=root", "NoNewPrivileges=true", "ProtectHome=true",
                      "ProtectSystem=strict", "PrivateDevices=true", "CapabilityBoundingSet="]:
            self.assertIn(value, updater)

    def test_fresh_full_check_is_local_and_leaves_managed_paths_absent(self):
        # The actual check-mode play runs with a scratch inventory that has no
        # addresses or SSH users. Empty service facts model a cold host in a
        # namespace without init. The mocked local filesystem
        # type: a Chaldea job uses overlayfs while production requires ext4.
        home = Path(os.environ["HOME"]).resolve()
        self.assertIn(".scratch", str(home))
        self.assertEqual(home, Path(os.environ["ROLLOUT_TEST_HOME"]).resolve())
        with tempfile.TemporaryDirectory(dir=home) as d:
            d = Path(d)
            bindir = d / "bin"
            bindir.mkdir()
            probe = bindir / "findmnt"
            probe.write_text("#!/bin/sh\nprintf 'ext4\\n'\n")
            probe.chmod(0o700)
            inventory = d / "hosts.yml"
            inventory.write_text(yaml.safe_dump({"all": {"hosts": {"homelab": {
                "ansible_connection": "local", "ansible_become": False, "ansible_facts": {"services": {}}, "ansible_python_interpreter": os.sys.executable}}}}))
            before = {p: Path(p).exists() for p in [
                "/opt/chaldea/node", "/opt/chaldea-host", "/etc/akasha",
                "/etc/chaldea-access-trust", "/var/lib/akasha"]}
            result = subprocess.run(["ansible-playbook", "-i", str(inventory),
                str(ROOT / "playbooks/akasha.yml"), "--check", "--diff",
                "-e", json.dumps({"ansible_facts": {"services": {}}})],
                env={**os.environ, "PATH": str(bindir) + ":" + os.environ["PATH"],
                     "ANSIBLE_LOCAL_TEMP": str(d / "local"), "ANSIBLE_REMOTE_TEMP": str(d / "remote")},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("failed=0", result.stdout)
            self.assertEqual(before, {p: Path(p).exists() for p in before})

if __name__ == "__main__":
    unittest.main()
