#!/usr/bin/env python3
"""Exercise Shizune's real prepare guards and optional credential provisioning in scratch."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ["HOME"])
assert os.environ["SHIZUNE_TEST_HOME"] == str(HOME)
assert Path(os.environ["TMPDIR"]).is_relative_to(HOME)
PIN = "git.ryzhkov.dev/igor/shizune:" + "a" * 40 + "@sha256:" + "b" * 64


class PrepareTest(unittest.TestCase):
    def test_guard_and_optional_credentials(self):
        tasks = yaml.safe_load((ROOT / "stacks/shizune/prepare.yml").read_text())
        # Execute the actual guard, storage and credential tasks; stop before
        # host firewall/systemd installation. All paths are beneath scratch HOME.
        selected = tasks[:6]
        # Chaldea maps only the job UID. Check production ownership literally,
        # then use the fixture UID for synthetic filesystem operations.
        for task in selected:
            for module in ("ansible.builtin.file", "ansible.builtin.copy"):
                if module in task:
                    self.assertEqual(task[module]["owner"], "10001")
                    self.assertEqual(task[module]["group"], "10001")
                    task[module]["owner"] = str(os.getuid())
                    task[module]["group"] = str(os.getgid())
        with tempfile.TemporaryDirectory(dir=HOME) as directory:
            path = Path(directory)
            source = path / "token"
            source.write_text("synthetic-only")
            source.chmod(0o600)
            stack = path / "stack"
            stack.mkdir()
            play = path / "play.yml"
            syntax = path / "syntax.yml"
            syntax.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False,
                "tasks": [{"ansible.builtin.import_tasks": str(ROOT / "stacks/shizune/prepare.yml")}]}]))
            result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(syntax), "--syntax-check"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            variables = {"stack_dir": str(stack),
                         "stack": {"env_extra": {"SHIZUNE_IMAGE": PIN, "HOST_IP": "10.42.0.2"}},
                         "shizune_actual_token_source": str(source)}
            play.write_text(yaml.safe_dump([{"hosts": "localhost", "connection": "local",
                                            "gather_facts": False, "vars": variables, "tasks": selected}]))
            command = ["ansible-playbook", "-i", "localhost,", str(play), "-e", "ansible_become=false"]
            for n in range(2):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("skipping provisioning", result.stdout)
                if n:
                    self.assertRegex(result.stdout, r"changed=0\s+unreachable=0\s+failed=0")
            self.assertEqual((stack / "secrets/actual-token").read_text(), "synthetic-only")
            self.assertEqual((stack / "secrets/actual-token").stat().st_mode & 0o777, 0o600)
            for pin in ("UNSET", "git.ryzhkov.dev/igor/shizune:latest", PIN.split("@")[0]):
                variables["stack"]["env_extra"]["SHIZUNE_IMAGE"] = pin
                play.write_text(yaml.safe_dump([{"hosts": "localhost", "connection": "local",
                                                "gather_facts": False, "vars": variables, "tasks": selected}]))
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Require a published immutable", result.stdout)


if __name__ == "__main__":
    unittest.main()
