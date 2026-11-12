#!/usr/bin/env python3
"""Run retirement tasks with scratch files and a recording service-manager boundary."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class RetirementTest(unittest.TestCase):
    def test_check_apply_repeat_and_absent_preserve_state(self):
        scratch = Path(os.environ["TMPDIR"]).resolve()
        self.assertEqual(Path.home().resolve(), scratch / ".scratch-home")
        original = yaml.safe_load((ROOT / "playbooks/retire-dashboard.yml").read_text())[0]
        units = next(iter(original["vars"].values()))
        self.assertEqual(len(units), 4)
        self.assertTrue(units[0].endswith(".socket"))
        route_task = next(t for t in yaml.safe_load(
            (ROOT / "roles/traefik/tasks/main.yml").read_text())
            if t.get("name") == "Remove the retired dashboard route")
        for present in (True, False):
            with self.subTest(present=present), tempfile.TemporaryDirectory(dir=scratch) as directory:
                root = Path(directory)
                unitdir = root / "units"
                unitdir.mkdir()
                state = root / "state.db"
                state.write_bytes(b"archive me")
                route = root / "route.yml"
                if present:
                    for unit in units:
                        (unitdir / unit).write_text("installed")
                    route.write_text("installed")
                firewall = root / "firewall"
                firewall.write_text("192.168.20.33\n192.168.20.4\n" if present else "")
                events = root / "events"
                helper = root / "manager.py"
                helper.write_text(
                    "import pathlib,sys\n"
                    f"root=pathlib.Path({str(root)!r})\n"
                    "if sys.argv[1] == 'show':\n"
                    " print('loaded' if (root/'units'/sys.argv[2]).exists() else 'not-found')\n"
                    "elif sys.argv[1] == 'ufw':\n"
                    " path=root/'firewall'\n"
                    " rows=path.read_text().splitlines()\n"
                    " address=sys.argv[2]\n"
                    " print('Rule deleted' if address in rows else 'Could not delete non-existent rule')\n"
                    " path.write_text(''.join(row+'\\n' for row in rows if row != address))\n"
                    "else:\n"
                    " with (root/'events').open('a') as out: out.write(sys.argv[1]+'\\n')\n")
                play = copy.deepcopy(original)
                play.update(hosts="localhost", become=False)
                for task in play["tasks"]:
                    if "ansible.builtin.command" in task:
                        argv = task["ansible.builtin.command"]["argv"]
                        if argv[0] == "ufw":
                            self.assertEqual(argv, ["ufw", "--force", "delete", "allow", "from",
                                "{{ item }}", "to", "any", "port", "7393", "proto", "tcp"])
                            self.assertEqual(task["loop"], ["192.168.20.33", "192.168.20.4"])
                            action = "ufw"
                        else:
                            action = "show"
                        task["ansible.builtin.command"]["argv"] = [
                            sys.executable, str(helper), action, "{{ item }}"]
                    if "ansible.builtin.systemd_service" in task:
                        args = task.pop("ansible.builtin.systemd_service")
                        if "name" in args:
                            self.assertEqual(args["state"], "stopped")
                            self.assertIs(args["enabled"], False)
                        task["ansible.builtin.command"] = {"argv": [
                            sys.executable, str(helper),
                            "reload" if args.get("daemon_reload") else "{{ item.item }}"]}
                    if "ansible.builtin.file" in task:
                        task["ansible.builtin.file"]["path"] = str(unitdir) + "/{{ item }}"
                route_task = copy.deepcopy(route_task)
                route_task["ansible.builtin.file"]["path"] = str(route)
                play["tasks"].append(route_task)
                path = root / "play.yml"
                path.write_text(yaml.safe_dump([play]))
                for check, changed in ((True, present), (False, present), (False, False)):
                    proc = subprocess.run([
                        "ansible-playbook", "-i", "localhost,", "-c", "local", str(path),
                        "--tags", original["tags"][0], *(["--check"] if check else [])],
                        capture_output=True, text=True,
                        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(root / "local"),
                             "ANSIBLE_REMOTE_TEMP": str(root / "remote")})
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(state.read_bytes(), b"archive me")
                    self.assertRegex(proc.stdout, r"failed=0")
                    if check:
                        self.assertEqual(route.exists(), present)
                        self.assertEqual(len(list(unitdir.iterdir())), len(units) if present else 0)
                        self.assertFalse(events.exists())
                        self.assertEqual(bool(firewall.read_text()), present)
                    else:
                        self.assertFalse(route.exists())
                        self.assertEqual(firewall.read_text(), "")
                        self.assertEqual(list(unitdir.iterdir()), [])
                    if not changed:
                        self.assertRegex(proc.stdout, r"changed=0\s")
                recorded = events.read_text().splitlines() if events.exists() else []
                self.assertEqual(recorded, units + ["reload"] if present else [])


if __name__ == "__main__":
    unittest.main()
