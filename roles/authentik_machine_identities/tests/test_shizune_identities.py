"""Shizune's closed owner grant and isolated role lifecycle."""
import copy
import types
import unittest
import yaml
from test_service_profiles import access, spec, evaluate
import test_machine_identities as base

ROOT = base.ROOT


def app(origin="https://auth.example.test"):
    return access("shizune", origin) | {"scope": "shizune.use"}


def account():
    item = base.identity(profile="shizune-interactive") | {"service": "shizune", "shizune_owner": "igor"}
    item.pop("project")
    return item


class ShizuneTest(unittest.TestCase):
    def test_closed_grant_and_mapping(self):
        s = spec([account()], {"shizune": app()})
        a, i = s["services"]["shizune"], s["identities"][0]
        self.assertEqual(i["grant_attributes"], {"chaldea_shizune": {"shizune_owner": "igor"}})
        user = types.SimpleNamespace(username=i["username"], type="service_account",
                                     attributes=copy.deepcopy(i["grant_attributes"]))
        self.assertEqual(evaluate(a["expression"], user, a["group"], ["shizune.use"]),
                         {"aud": a["resource"], "shizune_owner": "igor"})
        for changes in ({"username": "foreign"}, {"type": "internal"},
                        {"attributes": {"chaldea_shizune": {"shizune_owner": "other"}}},
                        {"attributes": {"chaldea_shizune": {"shizune_owner": "igor", "extra": True}}}):
            bad = copy.deepcopy(user)
            for k, v in changes.items():
                setattr(bad, k, v)
            self.assertEqual(evaluate(a["expression"], bad, a["group"]), {})
        self.assertEqual(evaluate(a["expression"], user, "foreign"), {})
        self.assertEqual(evaluate(a["expression"], None, a["group"]), {})
        for owner in ("other", "", None, True, ["igor"], {"igor": True}):
            with self.assertRaises(base.mi.AnsibleFilterError):
                spec([account() | {"shizune_owner": owner}], {"shizune": app()})
        for scope in ("shizune.read", "shizune.use shizune.use", "shizune.use ov.read"):
            with self.assertRaises(base.mi.AnsibleFilterError):
                spec([account()], {"shizune": app() | {"scope": scope}})

    def test_source_accounts_and_bound_paths(self):
        values = yaml.safe_load((ROOT / "playbooks/vars/authentik-shizune-identities.yml").read_text())
        items = values["authentik_machine_identities"]
        hosts = {i["host"]: {"ssh_user": "igor", "home": "/home/igor", "connection": "ssh"} for i in items}
        s = base.mi.machine_identity_spec(items, base.ACCESS, hosts, 180,
            base.NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), values["authentik_machine_services"],
            values["authentik_machine_identity_destinations"])
        self.assertEqual([i["username"] for i in s["identities"]],
                         ["chaldea-omarchy-pc-shizune-interactive", "chaldea-laptop-shizune-interactive"])
        for i in s["identities"]:
            self.assertEqual(i["secret_path"], "/home/igor/.config/chaldea/authentik/shizune-interactive.app-password")

    def test_entrypoint_offline_check_and_guard(self):
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir=base.scratch_home()) as directory:
            inventory = Path(directory) / "inventory.yml"
            inventory.write_text(yaml.safe_dump({"all": {"hosts": {
                "omarchy-pc": {"ssh_user": "igor"},
                "laptop": {"ssh_user": "igor"}}}}))
            command = ["ansible-playbook", "-i", str(inventory),
                       "playbooks/authentik-shizune-identities.yml", "--check"]
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("failed=0", result.stdout)
            result = subprocess.run(command + ["-e", '{"authentik_machine_identities": []}'],
                                    cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Preserve the closed Shizune", result.stdout)

    def test_real_role_create_repeat_cleanup(self):
        x = base.Scratch(self)
        a = app(f"https://127.0.0.1:{x.server.server_port}")
        i = account()
        for n in range(2):
            code, output = x.run([i], services={"shizune": a})
            self.assertEqual(code, 0, output)
            if n:
                self.assertEqual(x.changed(output), 0, output)
        self.assertEqual(x.secret("agent-one", i["profile"]).stat().st_mode & 0o777, 0o600)
        code, output = x.run([i | {"state": "absent"}], services={"shizune": a | {"state": "absent"}})
        self.assertEqual(code, 0, output)
        self.assertFalse(x.secret("agent-one", i["profile"]).exists())
        self.assertEqual(set(x.state.apps), {"budget"})


if __name__ == "__main__":
    unittest.main()
