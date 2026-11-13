"""Offline round-1 checks; no live credential or systemd access."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ProducerAuthTests(unittest.TestCase):
    def test_retired_grants_removed_without_changing_other_enrollment(self):
        home = Path(os.environ["HOME"])
        self.assertTrue(home.is_relative_to(Path(os.environ["TMPDIR"]).parent))
        with tempfile.TemporaryDirectory(dir=home) as scratch:
            scratch = Path(scratch)
            path = scratch / "helper.json"
            retained = dict(uid=1000, source="backup", action="append", kind="feed_post")
            # Same source name under another UID is not the retired reader.
            other_uid = dict(uid=1000, source="mission-control-reader", action="read", kind="report")
            original = dict(issuer=dict(uid=995), state_file="/unchanged",
                            grants=[retained, other_uid] + [dict(uid=940, source="mission-control-reader",
                                                     action="read", kind=kind)
                                                for kind in ("agent_status", "job_finished", "report")])
            path.write_text(json.dumps(original))
            plays = yaml.safe_load((ROOT / "playbooks/akasha-producer-auth.yml").read_text())
            play = next(p for p in plays if p["name"] == "Remove retired Normandy Mission Control reader grants")
            play["hosts"] = "localhost"
            for task in play["tasks"]:
                task.pop("become", None)
                if "ansible.builtin.slurp" in task:
                    task["ansible.builtin.slurp"]["src"] = str(path)
                if "ansible.builtin.copy" in task:
                    task["ansible.builtin.copy"].update(dest=str(path), owner=str(os.getuid()), group=str(os.getgid()))
            # The fleet play consumes this override; check mode must already
            # see the filtered grants.
            play["tasks"].append({"name": "Override excludes the retired reader", "ansible.builtin.assert": {
                "that": ["akasha_helper_grants_override == " + json.dumps([retained, other_uid])]}})
            fixture = scratch / "play.yml"
            fixture.write_text(yaml.safe_dump([play]))
            env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(scratch / "local"),
                       ANSIBLE_REMOTE_TEMP=str(scratch / "remote"))
            args = ["ansible-playbook", "-i", "localhost,", "-c", "local", str(fixture)]
            widened = [retained, other_uid, dict(uid=1000, source="new-reader", action="read", kind="report")]
            supplied = subprocess.run(args + ["-e", json.dumps({"akasha_helper_grants_override": widened})],
                                      env=env, capture_output=True, text=True)
            self.assertNotEqual(supplied.returncode, 0, supplied.stdout)
            self.assertEqual(json.loads(path.read_text()), original)
            check = subprocess.run(args + ["--check"], env=env, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertEqual(json.loads(path.read_text()), original)
            first = subprocess.run(args, env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(json.loads(path.read_text()), original | dict(grants=[retained, other_uid]))
            repeat = subprocess.run(args, env=env, check=True, capture_output=True, text=True)
            self.assertIn("changed=0", repeat.stdout)

    def test_route_namespace_and_persistent_mode(self):
        plays = yaml.safe_load((ROOT / "playbooks/akasha-producer-auth.yml").read_text())
        # The first play refuses multiple hosts before any mutating play runs.
        self.assertEqual(plays[0]["hosts"], "all")
        self.assertEqual([list(t) for t in plays[0]["tasks"]], [["name", "ansible.builtin.assert"]])
        self.assertIn("ansible_play_hosts_all | length == 1", plays[0]["tasks"][0]["ansible.builtin.assert"]["that"])
        tasks = next(p for p in plays if p["name"].startswith("Expose host credential"))["tasks"]
        binding = next(t for t in tasks if "ansible.builtin.copy" in t)
        self.assertIn("ProtectHome=tmpfs", binding["ansible.builtin.copy"]["content"])
        self.assertIn("BindReadOnlyPaths=", binding["ansible.builtin.copy"]["content"])
        self.assertIn("chaldea_token_helper_authentik_enabled", binding["when"])
        self.assertTrue(any(t.get("ansible.builtin.file", {}).get("state") == "absent" for t in tasks))


if __name__ == "__main__":
    unittest.main()
