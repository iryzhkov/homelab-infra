#!/usr/bin/env python3
"""Trust role installation contracts; fetcher behaviour belongs to Chaldea host/tests."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/chaldea_access_trust"


class FetcherInstallTest(unittest.TestCase):
    def test_role_uses_only_the_pinned_artifact(self):
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        self.assertEqual(tasks[0]["ansible.builtin.include_role"]["name"], "chaldea_host_artifact")
        task = next(t for t in tasks if t["name"] == "Install byte-only Git downloader")
        self.assertEqual(task["ansible.builtin.copy"], {
            "src": "{{ chaldea_host_artifact_root }}/chaldea-access-trust-fetch",
            "remote_src": True, "dest": "{{ chaldea_access_trust_fetcher }}",
            "owner": "root", "group": "root", "mode": "0755"})
        self.assertEqual(task["when"], "chaldea_host_artifact_ready | bool")
        self.assertFalse((ROLE / "files/chaldea-access-trust-fetch").exists())

    def test_actual_copy_task_fresh_check_install_and_second_check(self):
        scratch = Path(os.environ["TMPDIR"]).resolve()
        home = Path(os.environ["HOME"]).resolve()
        self.assertTrue(home.is_relative_to(scratch))
        self.assertNotEqual(home, Path("/home/igor"))
        with tempfile.TemporaryDirectory(dir=home) as d:
            base = Path(d)
            source = base / "host"
            source.mkdir()
            target = base / "installed-fetcher"
            task = copy.deepcopy(next(t for t in yaml.safe_load(
                (ROLE / "tasks/main.yml").read_text())
                if t["name"] == "Install byte-only Git downloader"))
            task["become"] = False
            task["ansible.builtin.copy"]["owner"] = str(os.getuid())
            task["ansible.builtin.copy"]["group"] = str(os.getgid())
            play = base / "copy.yml"
            variables = {"chaldea_host_artifact_root": str(source),
                         "chaldea_access_trust_fetcher": str(target),
                         "chaldea_host_artifact_ready": False}
            def run(check=False):
                play.write_text(yaml.safe_dump([{
                    "hosts": "localhost", "connection": "local", "gather_facts": False,
                    "vars": variables, "tasks": [task]}]))
                return subprocess.run(["ansible-playbook", "-i", "localhost,", str(play),
                    *(["--check", "--diff"] if check else [])],
                    env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(base / "local"),
                         "ANSIBLE_REMOTE_TEMP": str(base / "remote")},
                    capture_output=True, text=True)
            fresh = run(check=True)
            self.assertEqual(fresh.returncode, 0, fresh.stdout + fresh.stderr)
            self.assertFalse(target.exists())
            payload = b"# synthetic host artifact, never executed\n"
            (source / "chaldea-access-trust-fetch").write_bytes(payload)
            variables["chaldea_host_artifact_ready"] = True
            first = run()
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            second = run(check=True)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("changed=0", second.stdout)


if __name__ == "__main__":
    unittest.main()
