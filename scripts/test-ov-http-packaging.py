#!/usr/bin/env python3
"""Source-only OV packaging fixtures; no live Docker, backend or issuer access."""
import copy
import errno
import stat
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock

import yaml
from ansible.errors import AnsibleFilterError

# Reuse the existing isolated fixture loader and process diagnostics.
import importlib.util
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("web_tests", ROOT / "scripts/test-web-mcp-packaging.py")
assert spec is not None and spec.loader is not None
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)
contract, guard = web.contract, web.guard


def fixture():
    return {
        "release": "synthetic-v1", "listen": "192.168.70.240:8098",
        "image": {"reference": "registry.example.invalid/ov-http@sha256:" + "a" * 64,
                  "source_revision": "b" * 40, "contract_revision": contract.OV_CONTRACT,
                  "provenance": "https://build.example.invalid/receipts/synthetic-v1"},
        "credential": {"source": "/protected/synthetic-key", "source_uid": 1000},
        "config": {"resource": "https://ov.mcp.example.invalid/mcp",
                   "issuer": "https://auth.example.invalid/ov/",
                   "jwks_url": "https://auth.example.invalid/ov/jwks/",
                   "backend_url": "http://192.168.70.200:1933/api/v1",
                   "backend_secret_file": "/etc/ov-http/backend-key",
                   "listen_host": "0.0.0.0", "listen_port": 8098}}


class OVPackagingTest(unittest.TestCase):
    def test_contract_refusals_and_static_package_separation(self):
        value = fixture()
        self.assertEqual(contract.ov_contract(value), value)
        mutations = [
            ("listen", "0.0.0.0:8098"), ("listen", "8.8.8.8:8098"),
            ("listen", "127.0.0.1:443"), ("release", "../other"),
            ("image.reference", "python:latest"), ("image.source_revision", "main"),
            ("image.contract_revision", "c" * 40), ("image.provenance", ""),
            ("config.backend_url", "http://192.168.70.200:1933"),
            ("config.backend_secret_file", "/srv/swarm/openviking/ov.conf"),
            ("config.listen_host", "127.0.0.1"), ("config.listen_port", True),
            ("config.issuer", "https://auth.example.invalid/ov"),
            ("config.resource", "http://ov.example.invalid/mcp"),
            ("config.jwks_url", "https://auth.example.invalid/%61"),
            ("config.semantic_profile", "namespace-directories-v1"),
            ("config.backend_token", "never-inline"), ("credential.source", "/unsafe/../key"),
        ]
        for key, replacement in mutations:
            bad = copy.deepcopy(value)
            target = bad
            parts = key.split(".")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = replacement
            with self.subTest(key=key), self.assertRaises((AnsibleFilterError, ValueError)):
                contract.ov_contract(bad)
        bad = web.fixture()
        bad["service"] = "ov-http"
        with self.assertRaises(AnsibleFilterError):
            contract.endpoint_contract(bad)

    def test_nonroot_files_and_bounded_path_ownership(self):
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as scratch:
            root = Path(scratch) / "ov-http"
            with mock.patch.object(guard, "OV_ROOT", root), \
                 mock.patch.object(guard, "optional_account", return_value=None):
                guard.ov_package("synthetic-v1")
                root.mkdir()
                with self.assertRaises(ValueError):
                    guard.ov_package("synthetic-v1")
                (root / ".homelab-infra-owned").write_text(guard.OV_OWNER)
                (root / ".homelab-infra-owned").chmod(0o644)
                release = root / "releases/synthetic-v1"
                release.mkdir(parents=True)
                marker = release / ".homelab-infra-owned"
                marker.write_text(guard.OV_OWNER)
                marker.chmod(0o644)
                (release / "compose.yml").write_text("# synthetic")
                (release / "compose.yml").chmod(0o644)
                for name in ("runtime.toml", "backend-key"):
                    p = release / name
                    p.write_text("synthetic-only")
                    p.chmod(0o600)
                # Root-owned private files are NOT readable by the runtime UID.
                with self.assertRaises(ValueError):
                    guard.ov_package("synthetic-v1", True)
                try:
                    for name in ("runtime.toml", "backend-key"):
                        os.chown(release / name, 8098, 8098)
                except OSError as error:
                    if error.errno not in (errno.EINVAL, errno.EPERM):
                        raise
                    # Chaldea maps only UID0. Keep real traversal/type/mode checks;
                    # simulate only the two regular leaf files' numeric ownership.
                    print("Single-ID namespace: UID/GID8098 metadata is simulated")
                    original_lstat = Path.lstat
                    def runtime_metadata(path, *args, **kwargs):
                        info = original_lstat(path, *args, **kwargs)
                        if path in (release / "runtime.toml", release / "backend-key") and stat.S_ISREG(info.st_mode):
                            fields = list(info)
                            fields[4:6] = [8098, 8098]
                            return os.stat_result(fields)
                        return info
                    self.enterContext(mock.patch.object(Path, "lstat", runtime_metadata))
                guard.ov_package("synthetic-v1", True)
                (release / "unexpected").touch()
                with self.assertRaises(ValueError):
                    guard.ov_package("synthetic-v1", True)
                (release / "unexpected").unlink()
                key = release / "backend-key"
                key.chmod(0o640)
                with self.assertRaises(ValueError):
                    guard.ov_package("synthetic-v1", True)
                key.unlink()
                key.symlink_to(release / "runtime.toml")
                with self.assertRaises(ValueError):
                    guard.ov_package("synthetic-v1", True)
                key.unlink()
                release.chmod(0o777)
                with self.assertRaises(ValueError):
                    guard.ov_package("synthetic-v1")
                release.chmod(0o755)
                with mock.patch.object(guard, "optional_account", return_value=object()):
                    with self.assertRaises(ValueError):
                        guard.ov_package("synthetic-v1")

    def test_project_inspection_refuses_unowned_objects_without_mutation(self):
        def response(args, **kwargs):
            self.assertNotIn("down", args)
            self.assertNotIn("rm", args)
            if args[2:4] == ["container", "ls"]:
                return subprocess.CompletedProcess(args, 0, "synthetic\n", "")
            if args[2:4] == ["container", "inspect"]:
                return subprocess.CompletedProcess(args, 0, json.dumps([obj]), "")
            return subprocess.CompletedProcess(args, 0, "", "")
        obj = {"Name": "/ov-http", "Config": {"Labels": {
            guard.OV_LABEL: "v1", "com.docker.compose.project": "ov-http",
            "com.docker.compose.service": "ov-http"}}}
        with mock.patch("subprocess.run", side_effect=response):
            guard.ov_runtime()
            obj["Config"]["Labels"].pop(guard.OV_LABEL)
            with self.assertRaises(ValueError):
                guard.ov_runtime()
            obj["Config"]["Labels"][guard.OV_LABEL] = "v1"
            obj["Name"] = "/ov-http-check"
            with self.assertRaises(ValueError):
                guard.ov_runtime()

    def test_actual_templates_syntax_defaults_and_lifecycle_gates(self):
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as scratch:
            base = Path(scratch)
            inventory = base / "inventory.yml"
            inventory.write_text("all:\n  hosts:\n    fixture:\n      ansible_connection: local\n")
            env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                       ANSIBLE_LIBRARY=str(ROOT / "playbooks/library"),
                       ANSIBLE_LOCAL_TEMP=str(base / "local-tmp"),
                       ANSIBLE_REMOTE_TEMP=str(base / "remote-tmp"))
            def run(path, *args, check=True):
                return web.checked_run(["ansible-playbook", "-i", str(inventory), str(path), *args],
                    env=env, check=check, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT)
            run(ROOT / "playbooks/ov-http.yml", "--syntax-check")
            disabled = base / "disabled.yml"
            disabled.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                                                "roles": ["ov_http_compose"]}]))
            self.assertIn("changed=0", run(disabled).stdout)
            # Scope gates precede module calls, even with a limit.
            refused = run(ROOT / "playbooks/ov-http.yml", "--limit", "fixture",
                          "-e", "ov_http_target=fixture", check=False)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("Refuse implicit scope", refused.stdout)
            value = fixture()
            release_dir = "/opt/web-mcp/ov-http/releases/synthetic-v1"
            values = {"ov_http": value, "ov_http_release_dir": release_dir,
                      "web_mcp_edge": contract.route_contract({
                          **web.route(), "service": "ov-http",
                          "resource": value["config"]["resource"]}),
                      **yaml.safe_load((ROOT / "roles/web_mcp_route/vars/main.yml").read_text())}
            templates = [
                ("stacks/ov-http/compose.yml.j2", "compose.yml"),
                ("roles/ov_http_compose/templates/runtime.toml.j2", "runtime.toml"),
                ("roles/web_mcp_route/templates/route.yml.j2", "route.yml")]
            render = base / "render.yml"
            render.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                "vars": values, "tasks": [{"name": "Render " + dest, "ansible.builtin.template":
                    {"src": str(ROOT / src), "dest": str(base / dest), "mode": "0600"}}
                    for src, dest in templates]}]))
            self.assertIn("changed=3", run(render).stdout)
            self.assertIn("changed=0", run(render).stdout)
            self.assertEqual(tomllib.loads((base / "runtime.toml").read_text()), value["config"])
            compose = yaml.safe_load((base / "compose.yml").read_text())
            service = compose["services"]["ov-http"]
            self.assertEqual(set(compose["services"]), {"ov-http"})
            self.assertEqual(service["ports"], [value["listen"] + ":8098"])
            self.assertEqual(service["user"], "8098:8098")
            self.assertTrue(service["read_only"])
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
            self.assertEqual(service["logging"]["options"]["compress"], "false")
            self.assertEqual(len(service["volumes"]), 2)
            self.assertTrue(all(v["read_only"] and not v["bind"]["create_host_path"]
                                for v in service["volumes"]))
            self.assertNotIn("environment", service)
            route = yaml.safe_load((base / "route.yml").read_text())
            router = route["http"]["routers"]["web-mcp-ov-http"]
            self.assertNotIn("health", router["rule"])
            self.assertNotIn("PathPrefix", router["rule"])
            self.assertIn("Path(`/mcp`)", router["rule"])
            self.assertIn("Path(`/.well-known/oauth-protected-resource/mcp`)", router["rule"])
            self.assertTrue(route["http"]["services"]["web-mcp-ov-http"]["loadBalancer"]["passHostHeader"])
            # Real lifecycle assertions refuse before package paths or Docker inspection.
            enabled = base / "enabled.yml"
            enabled.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                "vars": {"ov_http_compose_enabled": True, "ov_http_package": value},
                "roles": ["ov_http_compose"]}]))
            for state in ("active", "absent"):
                result = run(enabled, "-e", "ov_http_state=" + state, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Require an explicit lifecycle", result.stdout)
                self.assertNotIn("Guard all package ancestors", result.stdout)
            # Optional CLI parsing needs no daemon; CI still exercises actual Ansible/YAML.
            if os.environ.get("OV_HTTP_COMPOSE_PARSE") == "1":
                parsed = subprocess.run(["docker", "compose", "-f", str(base / "compose.yml"),
                                         "config", "--format", "json"],
                                        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(json.loads(parsed.stdout)["services"]["ov-http"]["ports"][0]["host_ip"],
                                 "192.168.70.240")


if __name__ == "__main__":
    assert Path(os.environ["HOME"]) == ROOT / ".cache/web-mcp-tests/home"
    assert Path(os.environ["TMPDIR"]) == Path(os.environ["HOME"]) / "tmp"
    unittest.main(verbosity=2)
