#!/usr/bin/env python3
"""Source-only OV packaging fixtures; no live Docker, backend or issuer access."""
import copy
from dataclasses import replace
import hashlib
import sys
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


def full_fixture():
    value = fixture()
    value["image"]["contract_revision"] = "1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319"
    value["image"]["source_revision"] = "1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319"
    value["config"].update({
        "semantic_profile": "resources-directories-v052-v1",
        "semantic_backend_revision": "7f3ae1af822d936c1d0b070537772b0f1d59122f",
        "namespace_roots": ["viking://resources"],
        "mutation_profile": "resources-document-mutations-v1"})
    return value


def local_fixture():
    value = full_fixture()
    value["image"] = {
        "kind": "local-config",
        "reference": "sha256:95cdf45c81564bb231c0c92088ab2d78272f59cb9aa648c60c0bf258d53e36d6",
        "source_revision": "d171897f64a839772686c9f6a3b07cfa9900cbf2",
        "contract_revision": "1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319",
        "provenance": "https://github.com/iryzhkov/ov-mcp/actions/runs/41162764846"}
    return value


def local_inspect():
    return [{"Id": local_fixture()["image"]["reference"], "RepoDigests": [],
             "Config": {"User": "8098:8098", "WorkingDir": "/app",
                        "Entrypoint": ["python", "-m", "ov_http", "--config",
                                       "/etc/ov-http/runtime.toml"],
                        "Labels": {"org.opencontainers.image.revision":
                                   local_fixture()["image"]["source_revision"]}}}]


def invalid_full_packages():
    good = full_fixture()
    semantic = ("semantic_profile", "semantic_backend_revision", "namespace_roots",
                "mutation_profile")
    for mask in range(15):
        bad = copy.deepcopy(good)
        for i, key in enumerate(semantic):
            if not mask & (1 << i):
                del bad["config"][key]
        yield "partial-" + str(mask), bad
    for key, replacement in (
        ("semantic_profile", "namespace-directories-v1"),
        ("semantic_profile", "disabled"),
        ("semantic_backend_revision", "68e66ec8998957e1af3cae8a661947fe77cd9578"),
        ("semantic_backend_revision", "f" * 40),
        ("namespace_roots", ["viking://resources/homeassistant-api"]),
        ("namespace_roots", ["viking://resources", "viking://user"]),
        ("namespace_roots", ["viking://resources/"]),
        ("namespace_roots", []),
        ("namespace_roots", "viking://resources"),
        ("mutation_profile", "disabled"),
        ("mutation_profile", "future-mutations"),
        ("extra", "refused"),
    ):
        bad = copy.deepcopy(good)
        bad["config"][key] = replacement
        yield key + "-" + str(replacement), bad
    for key, replacement in (
        ("contract_revision", "f" * 40),
        ("contract_revision", "5f5c52c2171a2fef9ebe1439342921b1e58169bd"),
        ("source_revision", "f" * 40),
        ("source_revision", "5f5c52c2171a2fef9ebe1439342921b1e58169bd"),
    ):
        bad = copy.deepcopy(good)
        bad["image"][key] = replacement
        yield key + "-" + replacement, bad


class OVPackagingTest(unittest.TestCase):

    def test_personal_config_is_closed_and_cannot_select_old_images(self):
        good = {"personal_profile": "shared-owner-v1", "personal_account": "default",
                "personal_user": "default", "personal_subjects": ["synthetic-pc", "synthetic-laptop"],
                "personal_backend_revision": "7f3faf6982f81116d6626bc7091f87f786a309c7"}
        self.assertIs(contract.ov_personal_config(good), good)
        for key in good:
            bad = dict(good)
            del bad[key]
            with self.subTest(missing=key), self.assertRaises(AnsibleFilterError):
                contract.ov_personal_config(bad)
        for key, value in (("personal_profile", "future"), ("personal_account", "other"),
                           ("personal_user", "other"), ("personal_subjects", []),
                           ("personal_subjects", "subject"), ("personal_subjects", ["same", "same"]),
                           ("personal_subjects", ["a"] * 17), ("personal_subjects", [None]),
                           ("personal_subjects", ["x" * 257]), ("personal_subjects", ["has space"]),
                           ("personal_backend_revision", ""), ("personal_backend_revision", "HEAD")):
            with self.subTest(key=key), self.assertRaises(AnsibleFilterError):
                contract.ov_personal_config(good | {key: value})
        accepted = local_fixture()
        accepted["config"].update(good)
        before = copy.deepcopy(accepted)
        self.assertIs(contract.ov_contract(accepted), accepted)
        self.assertEqual(accepted, before)
        old = local_fixture()
        old["image"].update({
            "reference": "sha256:9fc44fb1bb2410e327fd450cd4a83a46d36ed851ca2724fb9a13f30edfcb1e31",
            "source_revision": "d5b1b104b5a39f15c9443c117bca9dd1c52eac68",
            "provenance": "https://github.com/iryzhkov/ov-mcp/actions/runs/40992612927"})
        for package in (fixture(), full_fixture(), old):
            package["config"].update(good)
            with self.assertRaises(AnsibleFilterError):
                contract.ov_contract(package)
        for revision in ("a" * 40, "7adf8b5fbcaa3293ef2a0b0ed44f864477401c39"):
            bad = copy.deepcopy(accepted)
            bad["config"]["personal_backend_revision"] = revision
            with self.assertRaisesRegex(AnsibleFilterError, "exact qualified"):
                contract.ov_contract(bad)
        # The accepted review commit has the same tree as publication, but the
        # image label and closed object bind the measured publication revision.
        bad = copy.deepcopy(accepted)
        bad["image"]["source_revision"] = "e094cb32a085e9ad3fc9976aba7824db830c8999"
        with self.assertRaises(AnsibleFilterError):
            contract.ov_contract(bad)
        for package in (fixture(), full_fixture(), local_fixture()):
            package["config"]["personal_profile"] = "disabled"
            self.assertIs(contract.ov_contract(package), package)
            package["config"]["personal_subjects"] = ["PRIVATE_SUBJECT_SENTINEL"]
            with self.assertRaises(AnsibleFilterError) as caught:
                contract.ov_contract(package)
            self.assertNotIn("PRIVATE_SUBJECT_SENTINEL", str(caught.exception))
        # The same template carries validated personal inputs, without defaults.
        import jinja2
        template = (ROOT / "roles/ov_http_compose/templates/runtime.toml.j2").read_text()
        env = jinja2.Environment()
        env.filters["to_json"] = json.dumps
        rendered = env.from_string(template).render(ov_http={"config": good})
        self.assertEqual(tomllib.loads(rendered), good)


    def test_local_release_is_a_closed_binding(self):
        good = local_fixture()
        before = copy.deepcopy(good)
        self.assertIs(contract.ov_contract(good), good)
        self.assertEqual(good, before)
        for key, replacement in (
            ("kind", "distribution"), ("kind", None), ("kind", {}),
            ("reference", "sha256:" + "a" * 64),
            ("reference", "registry.invalid/ov@sha256:" + good["image"]["reference"][7:]),
            ("source_revision", full_fixture()["image"]["source_revision"]),
            ("provenance", good["image"]["provenance"] + "/"),
            ("contract_revision", contract.OV_CONTRACT), ("unknown", "refused")):
            bad = copy.deepcopy(good)
            bad["image"][key] = replacement
            with self.subTest(key=key), self.assertRaises(AnsibleFilterError):
                contract.ov_contract(bad)
        bad = copy.deepcopy(good)
        del bad["image"]["kind"]
        with self.assertRaises(AnsibleFilterError):
            contract.ov_contract(bad)
        bad["image"]["reference"] = fixture()["image"]["reference"]
        with self.assertRaises(AnsibleFilterError):
            contract.ov_contract(bad)  # New source cannot enter the old full allowlist.
        for name, invalid in invalid_full_packages():
            bad = copy.deepcopy(good)
            bad["config"] = invalid["config"]
            if bad["config"] != good["config"]:
                with self.subTest(config=name), self.assertRaises(AnsibleFilterError):
                    contract.ov_contract(bad)

    def test_local_inspection_refuses_before_role_writes(self):
        tasks = yaml.safe_load((ROOT / "roles/ov_http_compose/tasks/main.yml").read_text())[0]["block"]
        # Execute the actual role with only external inspection/guard boundaries mocked.
        # The guard sentinel prevents any protected paths or actual Docker calls.
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as scratch:
            base = Path(scratch)
            modules = base / "modules"
            modules.mkdir()
            (modules / "web_mcp_guard.py").write_text(
                "from ansible.module_utils.basic import AnsibleModule\n"
                "m=AnsibleModule(argument_spec=dict(kind=dict(type='str'),release=dict(type='str')))\n"
                "m.fail_json(msg='REACHED_READ_ONLY_GUARD_BOUNDARY')\n")
            collection = base / "collections/ansible_collections/community/docker/plugins/modules"
            collection.mkdir(parents=True)
            (collection / "docker_image_info.py").write_text(
                "import json,os\nfrom ansible.module_utils.basic import AnsibleModule\n"
                "m=AnsibleModule(argument_spec=dict(docker_host=dict(type='str'),name=dict(type='str')),supports_check_mode=True)\n"
                "assert m.params['docker_host']=='unix:///var/run/docker.sock'\n"
                "assert m.params['name']=='" + local_fixture()["image"]["reference"] + "'\n"
                "m.exit_json(changed=False,images=json.loads(os.environ['OV_INSPECT_FIXTURE']))\n")
            # Collection roots do not fall through module-by-module. Keep all
            # role Docker boundaries resolvable in this isolated mock collection.
            for module in ("docker_compose_v2", "docker_compose_v2_pull"):
                (collection / (module + ".py")).write_text(
                    "from ansible.module_utils.basic import AnsibleModule\n"
                    "AnsibleModule(argument_spec={}).fail_json(msg='FORBIDDEN_" + module + "')\n")
            inventory = base / "inventory.yml"
            inventory.write_text("all:\n  hosts:\n    fixture:\n      ansible_connection: local\n")
            play = base / "play.yml"
            play.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                "vars": {"ov_http_compose_enabled": True, "ov_http_state": "active",
                         "ov_http_activate": True, "ov_http_package": local_fixture()},
                "roles": ["ov_http_compose"]}]))
            env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                ANSIBLE_LIBRARY=str(modules),
                ANSIBLE_COLLECTIONS_PATH=str(base / "collections") + ":" + os.environ.get(
                    "ANSIBLE_COLLECTIONS_PATH", os.environ["HOME"] + "/.ansible/collections"),
                ANSIBLE_LOCAL_TEMP=str(base / "local-tmp"), ANSIBLE_REMOTE_TEMP=str(base / "remote-tmp"))
            cases = [("accepted", local_inspect()), ("absent", [])]
            for key, wrong in (("User", "0:0"), ("WorkingDir", "/other"),
                               ("Entrypoint", ["sh"]), ("Cmd", ["--other"]),
                               ("Labels", {"org.opencontainers.image.revision": "a" * 40})):
                bad = local_inspect()
                bad[0]["Config"][key] = wrong
                cases.append((key, bad))
            bad = local_inspect()
            bad[0]["Id"] = "sha256:" + "a" * 64
            cases.append(("Id", bad))
            for name, images in cases:
                result = web.checked_run(["ansible-playbook", "-i", str(inventory), str(play)],
                    env=dict(env, OV_INSPECT_FIXTURE=json.dumps(images)), check=False,
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("REACHED_READ_ONLY_GUARD_BOUNDARY" if name == "accepted"
                              else "Require the measured local image identity", result.stdout)
                if name != "accepted":
                    self.assertNotIn("Guard all package ancestors", result.stdout)
                for forbidden in ("FORBIDDEN_docker_compose_v2", "FORBIDDEN_docker_compose_v2_pull",
                                  "Pull only the reviewed", "Validate actual mounted", "Activate only"):
                    self.assertNotIn(forbidden, result.stdout)
            pull = next(t for t in tasks if t["name"] == "Stage and validate an immutable release")["block"]
            pull = next(t for t in pull if "community.docker.docker_compose_v2_pull" in t)
            self.assertIn("!= 'local-config'", pull["when"])
            activate = next(t for t in tasks if t["name"] == "Activate only the validated owned service")
            self.assertEqual(activate["community.docker.docker_compose_v2"]["pull"], "never")

    def test_closed_full_contract_and_unchanged_legacy(self):
        legacy = fixture()
        before = copy.deepcopy(legacy)
        self.assertIs(contract.ov_contract(legacy), legacy)
        self.assertEqual(legacy, before)
        self.assertEqual(len(legacy["config"]), 7)
        for revision in ("1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319",
                         "4cc776bc4f9a9211b1106f2ba3299d92e82690be"):
            full = full_fixture()
            full["image"]["source_revision"] = revision
            before = copy.deepcopy(full)
            self.assertIs(contract.ov_contract(full), full)
            self.assertEqual(full, before)
            self.assertEqual(len(full["config"]), 11)
        for name, bad in invalid_full_packages():
            with self.subTest(name=name), self.assertRaises(AnsibleFilterError):
                contract.ov_contract(bad)
        for key in ("semantic_profile", "semantic_backend_revision", "namespace_roots",
                    "mutation_profile"):
            bad = fixture()
            bad["config"][key] = full_fixture()["config"][key]
            with self.subTest(legacy_extra=key), self.assertRaises(AnsibleFilterError):
                contract.ov_contract(bad)

    def check_source_bound_loader(self, path, full, base):
        source = os.environ.get("OV_HTTP_CONFIG_SOURCE")
        if not source:
            return  # Ordinary packaging CI has no separately bound adapter input.
        source = Path(source).resolve()
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         "e02fc83629be65ebf40252450306f7787f1bc35b7f8cbfa017c77b73b7646fa9")
        spec = importlib.util.spec_from_file_location("accepted_ov_config", source)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded = module.Config.load(str(path))
        self.assertEqual(loaded.uri_policy,
                         "resources-utf8-read-v1" if full else "legacy-ascii-v1")
        self.assertEqual(loaded.mutation_profile,
                         "resources-document-mutations-v1" if full else "disabled")
        self.assertEqual(loaded.namespace_roots, ("viking://resources",) if full else ())
        self.assertEqual(loaded.admits_namespace("viking://resources/fixture"), full)
        self.assertFalse(loaded.admits_namespace("viking://resources-other/fixture"))
        key = base / "synthetic-backend-key"
        key.write_text("synthetic-only-key\n")
        key.chmod(0o600)
        self.assertEqual(replace(loaded, backend_secret_file=str(key)).secret(),
                         "synthetic-only-key")
        print("OV_CONFIG_IMPORT " + json.dumps({
            "path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "pin": "1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319",
            "tree": "651a695611641667e63b9b1d78e27b6b5387dc7b",
            "profile": "full" if full else "legacy"}), flush=True)
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
            self.assertEqual((base / "runtime.toml").read_bytes(), (
                'backend_secret_file = "/etc/ov-http/backend-key"\n'
                'backend_url = "http://192.168.70.200:1933/api/v1"\n'
                'issuer = "https://auth.example.invalid/ov/"\n'
                'jwks_url = "https://auth.example.invalid/ov/jwks/"\n'
                'listen_host = "0.0.0.0"\n'
                'listen_port = 8098\n'
                'resource = "https://ov.mcp.example.invalid/mcp"\n').encode())
            self.check_source_bound_loader(base / "runtime.toml", False, base)
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
            # The owning role refuses invalid profiles in its first task, before guards,
            # credential metadata, directories, templates or Docker can be touched.
            for name, bad in list(invalid_full_packages())[::8]:
                values_bad = {"ov_http_compose_enabled": True, "ov_http_package": bad}
                enabled.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                    "vars": values_bad, "roles": ["ov_http_compose"]}]))
                result = run(enabled, check=False)
                self.assertNotEqual(result.returncode, 0, name)
                self.assertIn("Validate fixed OV packaging inputs before writes", result.stdout)
                self.assertNotIn("Require an explicit lifecycle choice", result.stdout)
                self.assertNotIn("Guard all package ancestors", result.stdout)
            full = full_fixture()
            values["ov_http"] = contract.ov_contract(full)
            render.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                "vars": values, "tasks": [{"name": "Render full runtime", "ansible.builtin.template":
                    {"src": str(ROOT / "roles/ov_http_compose/templates/runtime.toml.j2"),
                     "dest": str(base / "runtime.toml"), "mode": "0600"}}]}]))
            self.assertIn("changed=1", run(render).stdout)
            self.assertIn("changed=0", run(render).stdout)
            self.assertEqual(tomllib.loads((base / "runtime.toml").read_text()), full["config"])
            self.check_source_bound_loader(base / "runtime.toml", True, base)
            values["ov_http"] = contract.ov_contract(local_fixture())
            render.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                "vars": values, "tasks": [{"name": "Render local image Compose", "ansible.builtin.template":
                    {"src": str(ROOT / "stacks/ov-http/compose.yml.j2"),
                     "dest": str(base / "compose.yml"), "mode": "0600"}}]}]))
            self.assertIn("changed=1", run(render).stdout)
            local_service = yaml.safe_load((base / "compose.yml").read_text())["services"]["ov-http"]
            self.assertEqual(local_service["image"], local_fixture()["image"]["reference"])
            self.assertEqual(local_service["labels"]["org.opencontainers.image.revision"],
                             local_fixture()["image"]["source_revision"])
            self.assertEqual(len(local_service["volumes"]), 2)
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
