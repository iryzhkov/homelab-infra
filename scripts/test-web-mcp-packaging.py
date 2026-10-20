#!/usr/bin/env python3
"""Synthetic source contracts and isolated Ansible render fixtures. No live inventory."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import hashlib
import struct
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import jinja2
import yaml
from ansible.errors import AnsibleFilterError

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load("contract", "filter_plugins/web_mcp.py")
guard = load("guard", "playbooks/library/web_mcp_guard.py")
local_artifact = load("local_artifact", "roles/web_mcp_endpoint/library/web_mcp_local_artifact.py")


def static_elf(machine=62, program_type=1):
    """Minimal Linux amd64 executable: one RX load segment, exit(0), no loader."""
    code = b"\xb8\x3c\x00\x00\x00\x31\xff\x0f\x05"
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", header, 16, 2, machine, 1)
    struct.pack_into("<Q", header, 24, 0x400000 + 120)
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<HHH", header, 52, 64, 56, 1)
    program = struct.pack("<IIQQQQQQ", program_type, 5, 0, 0x400000, 0x400000,
                          120 + len(code), 120 + len(code), 4096)
    return bytes(header + program + code)


def fixture(service="ha-mcp"):
    value = {
        "service": service, "uid": 731, "gid": 731, "listen": "192.168.70.240:8787",
        "artifact": {
            "url": "https://github.com/iryzhkov/" + contract.SERVICES[service][0]
                   + "/releases/download/synthetic-v1/" + service + "-linux-amd64",
            "sha256": "a" * 64, "source_revision": "b" * 40,
            "contract_revision": contract.SERVICES[service][1]},
        "credentials": [],
    }
    if service == "ha-mcp":
        value["config"] = {
            "listen": value["listen"], "resource_url": "https://ha.mcp.example.invalid/mcp",
            "issuer_url": "https://auth.example.invalid/application/o/ha/",
            "jwks_url": "https://auth.example.invalid/application/o/ha/jwks/",
            "ha_url": "http://192.168.70.93:8123",
            "backend_token_file": "/etc/ha-mcp/backend-token",
            "entities": [{"id": "sensor.synthetic", "name": "Synthetic", "actions": ["state.read"]}],
        }
        value["credentials"] = [{"name": "backend-token",
                                 "source": "/protected/synthetic-input", "source_uid": 1000}]
    else:
        value["config"] = {
            "resource": "https://homelab.mcp.example.invalid/mcp",
            "issuer": "https://auth.example.invalid/application/o/homelab/",
            "jwks_url": "https://auth.example.invalid/application/o/homelab/jwks/",
            "inventory_revision": "c" * 40, "stale_after_seconds": 180,
            "hosts": [{"id": "hostA", "name": "Synthetic"}],
            "services": [{"id": "serviceA", "name": "Synthetic", "host_id": "hostA",
                          "probes": [{"backend_id": "gatusA", "endpoint_key": "synthetic"}]}],
            "gatus": [{"id": "gatusA", "base_url": "https://gatus.example.invalid",
                       "version": "v5.37.0",
                       "source_revision": "ae605f291928ed9b3bdbe60ae5528cc57631433b"}],
        }
    return value


def checked_run(*args, **kwargs):
    try:
        return subprocess.run(*args, **kwargs)
    except subprocess.CalledProcessError as error:
        print(error.stdout, file=sys.stderr)
        raise


def route():
    return {"service": "ha-mcp", "state": "present", "directory_uid": 1000,
            "resource": "https://ha.mcp.example.invalid/mcp",
            "upstream": "http://192.168.70.240:8787"}


class PackagingTest(unittest.TestCase):
    def rejected(self, value):
        with self.assertRaises((AnsibleFilterError, ValueError, TypeError)):
            contract.endpoint_contract(value)

    def test_accepted_interfaces_and_separate_credentials(self):
        for service in contract.SERVICES:
            self.assertEqual(contract.endpoint_contract(fixture(service)), fixture(service))
        value = fixture("homelab-observer")
        value["config"]["gatus"][0]["credentials_file"] = "/etc/homelab-observer/gatus-gatusA.json"
        self.rejected(value)
        value["credentials"] = [{"name": "gatus-gatusA.json", "source": "/protected/gatus",
                                 "source_uid": 1000}]
        contract.endpoint_contract(value)
        value["config"]["gatus"][0]["token_file"] = "/legacy"
        self.rejected(value)

    def test_observer_url_byte_limits_and_escaped_paths(self):
        for field in ("issuer", "jwks_url"):
            value = fixture("homelab-observer")
            prefix = "https://auth.example.invalid/"
            value["config"][field] = prefix + "k" * (511 - len(prefix)) + "/"
            self.assertEqual(len(value["config"][field].encode()), 512)
            contract.endpoint_contract(value)
            value["config"][field] += "/"
            self.rejected(value)
        value = fixture("homelab-observer")
        value["config"]["gatus"][0]["base_url"] = "https://" + "é" * 252
        self.assertEqual(len(value["config"]["gatus"][0]["base_url"].encode()), 512)
        contract.endpoint_contract(value)
        value["config"]["gatus"][0]["base_url"] += "a"
        self.rejected(value)
        for field, path in (("resource", "%6dcp"), ("issuer", "%61/"),
                            ("jwks_url", "keys%2fextra"), ("jwks_url", "%61")):
            value = fixture("homelab-observer")
            value["config"][field] = "https://auth.example.invalid/" + path
            self.rejected(value)
        value = fixture("homelab-observer")
        value["config"]["jwks_url"] = "https://auth.example.invalid/canonical%20keys"
        contract.endpoint_contract(value)

    def test_identity_bind_artifact_and_trust_refusals(self):
        for field, bad in (("uid", 0), ("gid", 65534), ("listen", ":8787"),
                           ("listen", "0.0.0.0:8787"), ("listen", "8.8.8.8:8787"),
                           ("listen", "192.168.70.240:80")):
            value = fixture()
            value[field] = bad
            self.rejected(value)
        for field, bad in (("sha256", "latest"), ("source_revision", ""),
                           ("contract_revision", "d" * 40),
                           ("url", "http://github.com/unreviewed"),
                           ("url", fixture()["artifact"]["url"] + "?token=unsafe")):
            value = fixture()
            value["artifact"][field] = bad
            self.rejected(value)
        for field, bad in (("resource_url", "http://ha.example.invalid/mcp"),
                           ("issuer_url", "https://auth.example.invalid/no-slash"),
                           ("jwks_url", "https://user:password@auth.example.invalid/key"),
                           ("backend_token_file", "/root/token"),
                           ("ha_url", "http://backend/path")):
            value = fixture()
            value["config"][field] = bad
            self.rejected(value)

    def test_dedicated_identity_refuses_unexpected_nss_memberships(self):
        service, uid, gid = "ha-mcp", 731, 731
        user = SimpleNamespace(pw_name=service, pw_uid=uid, pw_gid=gid,
                               pw_dir="/nonexistent", pw_shell="/usr/sbin/nologin")
        # Membership in the dedicated primary group itself is harmless.
        group = SimpleNamespace(gr_name=service, gr_gid=gid, gr_mem=[service])
        with mock.patch.multiple(guard.pwd,
                getpwnam=mock.Mock(return_value=user), getpwuid=mock.Mock(return_value=user),
                getpwall=mock.Mock(return_value=[user])), mock.patch.multiple(guard.grp,
                getgrnam=mock.Mock(return_value=group), getgrgid=mock.Mock(return_value=group)), \
                mock.patch.object(guard.os, "getgrouplist", return_value=[gid]) as memberships:
            guard.dedicated(service, uid, gid)
            memberships.assert_called_once_with(service, gid)
            memberships.return_value = [gid, 999]  # Synthetic docker supplementary GID.
            with self.assertRaises(ValueError):
                guard.dedicated(service, uid, gid)
            memberships.return_value = [gid]
            group.gr_mem.append("unrelated")
            with self.assertRaises(ValueError):
                guard.dedicated(service, uid, gid)

    def test_ha_read_only_qualification_and_canonical_url_parity(self):
        for entity_id in ("sensor.synthetic", "light.synthetic"):
            value = fixture()
            value["config"]["entities"][0].update(id=entity_id, control_qualified=True)
            self.rejected(value)
            value["config"]["entities"][0]["control_qualified"] = False
            contract.endpoint_contract(value)
        cases = (
            ("issuer_url", "https://auth.example.invalid:443/application/o/ha/"),
            ("jwks_url", "https://auth.example.invalid:443/application/o/ha/jwks/"),
            ("ha_url", "http://backend.example.invalid:80"),
            ("ha_url", "https://backend.example.invalid:443"),
            ("issuer_url", "https://auth.example.invalid./application/o/ha/"),
            ("jwks_url", "https://auth.example.invalid./keys"),
            ("ha_url", "http://backend.example.invalid.:8123"),
            ("issuer_url", "HTTPS://auth.example.invalid/application/o/ha/"),
            ("issuer_url", "https://auth.example.invalid/application/o/ha/?"),
            ("jwks_url", "https://auth.example.invalid/keys#"),
            ("jwks_url", "https://auth.example.invalid:/keys"),
            ("ha_url", "http://backend.example.invalid:08123"),
            ("issuer_url", "https://auth.example.invalid/%61pplication/o/ha/"),
            ("jwks_url", "https://auth.example.invalid/keys%2fextra"),
        )
        for field, invalid in cases:
            with self.subTest(field=field, url=invalid):
                value = fixture()
                value["config"][field] = invalid
                self.rejected(value)
        value = fixture()
        value["config"]["issuer_url"] = "https://auth.example.invalid:8443/application/o/ha/"
        value["config"]["jwks_url"] = "https://auth.example.invalid/qualified%20keys"
        contract.endpoint_contract(value)

    def test_control_and_registry_refusals(self):
        value = fixture()
        entity = value["config"]["entities"][0]
        entity.update(id="light.synthetic", actions=["light.control"])
        self.rejected(value)
        entity["control_qualified"] = True
        contract.endpoint_contract(value)
        entity["brightness"] = {"min_percent": 10}
        self.rejected(value)
        entity["brightness"]["max_percent"] = 90
        contract.endpoint_contract(value)
        entity["id"] = "switch.synthetic"
        self.rejected(value)
        value = fixture("homelab-observer")
        value["config"]["services"][0]["host_id"] = "unknown"
        self.rejected(value)
        value = fixture("homelab-observer")
        value["config"]["services"].append(copy.deepcopy(value["config"]["services"][0]))
        value["config"]["services"][1]["id"] = "other"
        self.rejected(value)
        value = fixture("homelab-observer")
        value["config"]["gatus"][0]["version"] = "latest"
        self.rejected(value)

    def test_route_and_unit_rendered_contract(self):
        env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
        template = (ROOT / "roles/web_mcp_route/templates/route.yml.j2").read_text()
        edge = contract.route_contract(route())
        dynamic = yaml.safe_load(env.from_string(template).render(
            web_mcp_edge=edge, **yaml.safe_load(
                (ROOT / "roles/web_mcp_route/vars/main.yml").read_text())))
        router = dynamic["http"]["routers"]["web-mcp-ha-mcp"]
        self.assertIn("Path(`/mcp`)", router["rule"])
        self.assertIn("Path(`/.well-known/oauth-protected-resource/mcp`)", router["rule"])
        self.assertNotIn("PathPrefix", router["rule"])
        self.assertNotIn("health", template)
        self.assertNotIn("middlewares", dynamic["http"])
        self.assertEqual(router["tls"]["domains"], [{"main": edge["host"]}])
        self.assertTrue(dynamic["http"]["services"]["web-mcp-ha-mcp"]["loadBalancer"]["passHostHeader"])
        for upstream in ("http://0.0.0.0:8787", "http://127.0.0.1:8787",
                         "https://8.8.8.8:8787", "http://192.168.70.240:8787/mcp"):
            bad = route()
            bad["upstream"] = upstream
            with self.assertRaises(AnsibleFilterError):
                contract.route_contract(bad)
        for service in contract.SERVICES:
            unit = env.from_string((ROOT / "roles/web_mcp_endpoint/templates/endpoint.service.j2").read_text()).render(
                web_mcp=fixture(service))
            self.assertIn("User=731", unit)
            self.assertIn("ProtectSystem=strict", unit)
            self.assertIn("CapabilityBoundingSet=\n", unit)
            self.assertIn("/releases/" + "a" * 64 + "/" + service, unit)
            self.assertNotIn("ReadWritePaths", unit)
            if service == "homelab-observer":
                self.assertIn("--listen 192.168.70.240:8787", unit)

    def test_protected_paths_reject_links_modes_and_unowned_route(self):
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as scratch:
            base = Path(scratch)
            target = base / "credential"
            target.write_text("synthetic, never a backend credential")
            target.chmod(0o600)
            guard.inspect(target, {os.getuid()}, exact_mode=0o600, required=True)
            target.chmod(0o644)
            with self.assertRaises(ValueError):
                guard.inspect(target, {os.getuid()}, exact_mode=0o600)
            link = base / "link"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                guard.inspect(link, {os.getuid()})
            parent = base / "writable"
            parent.mkdir(mode=0o777)
            parent.chmod(0o777)
            with self.assertRaises(ValueError):
                guard.inspect(parent / "new", {os.getuid()})
            alias = base / "alias"
            alias.symlink_to(base, target_is_directory=True)
            with self.assertRaises(ValueError):
                guard.inspect(alias / "missing", {os.getuid()})
            setattr(guard, "EDGE", base)
            routefile = base / "web-mcp-ha-mcp.yml"
            routefile.write_text("# operator owned unrelated file\n")
            routefile.chmod(0o644)
            with self.assertRaises(ValueError):
                guard.route("ha-mcp", os.getuid())

    def test_static_binary_gate_refuses_dynamic_or_wrong_checksum(self):
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as scratch:
            base = Path(scratch)
            setattr(guard, "PACKAGE_ROOT", base)
            header = bytearray(64)
            header[:6] = bytes((127, 69, 76, 70, 2, 1))
            struct.pack_into("<H", header, 18, 62)
            struct.pack_into("<Q", header, 32, 64)
            struct.pack_into("<HH", header, 54, 56, 1)
            program = bytearray(56)
            payload = header + program
            digest = hashlib.sha256(payload).hexdigest()
            directory = base / "ha-mcp" / "releases" / digest
            directory.mkdir(parents=True)
            binary = directory / "ha-mcp"
            binary.write_bytes(payload)
            binary.chmod(0o555)
            guard.binary("ha-mcp", digest)
            program[0] = 3  # PT_INTERP means this is not a static executable.
            binary.chmod(0o755)
            binary.write_bytes(header + program)
            binary.chmod(0o555)
            with self.assertRaises(ValueError):
                guard.binary("ha-mcp", digest)
            program[0] = 0
            binary.chmod(0o755)
            binary.write_bytes(header + program + b"changed")
            binary.chmod(0o555)
            with self.assertRaises(ValueError):
                guard.binary("ha-mcp", digest)

    def test_local_source_contract_keeps_all_provenance_required(self):
        for service in contract.SERVICES:
            value = fixture(service)
            value["artifact"]["local_source"] = {"path": "/protected/release", "owner_uid": 1000}
            contract.endpoint_contract(value)
            for field in ("url", "sha256", "source_revision", "contract_revision"):
                bad = copy.deepcopy(value)
                del bad["artifact"][field]
                self.rejected(bad)
            for source in ({}, None, {"path": "/protected/release"},
                           {"path": "/protected/release", "owner_uid": True},
                           {"path": "/protected/release", "owner_uid": -1},
                           {"path": "/protected/release", "owner_uid": 2147483648},
                           {"path": "/protected/release", "owner_uid": 0, "token": "forbidden"}):
                bad = copy.deepcopy(value)
                bad["artifact"]["local_source"] = source
                self.rejected(bad)
            for path in ("relative", "/", "/a/../b", "/a/./b", "/a//b", "/a/",
                         "/a b", "/a\\\\b", "/a?", "/" + "a" * 4096):
                bad = copy.deepcopy(value)
                bad["artifact"]["local_source"]["path"] = path
                self.rejected(bad)

    def test_local_artifact_descriptor_and_byte_refusals(self):
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as scratch:
            base = Path(scratch)
            source = base / "artifact"
            payload = static_elf()
            digest = hashlib.sha256(payload).hexdigest()
            source.write_bytes(payload)
            source.chmod(0o600)
            self.assertEqual(local_artifact.validate(str(source), os.getuid(), digest),
                             hashlib.sha1(payload).hexdigest())
            with self.assertRaises(ValueError):
                local_artifact.validate(str(source), os.getuid() + 1, digest)
            for mode in (0o400, 0o644, 0o660, 0o700):
                source.chmod(mode)
                with self.assertRaises(ValueError):
                    local_artifact.validate(str(source), os.getuid(), digest)
            source.chmod(0o600)
            link = base / "link"
            link.symlink_to(source)
            with self.assertRaises((ValueError, OSError)):
                local_artifact.validate(str(link), os.getuid(), digest)
            link.unlink()
            os.link(source, link)
            with self.assertRaises(ValueError):
                local_artifact.validate(str(source), os.getuid(), digest)
            link.unlink()
            base.chmod(0o777)
            with self.assertRaises(ValueError):
                local_artifact.validate(str(source), os.getuid(), digest)
            base.chmod(0o700)
            # Ancestor owner checks use synthetic metadata because the job maps only UID0.
            real_fstat = local_artifact.os.fstat
            def unsafe_parent(fd):
                info = real_fstat(fd)
                if stat.S_ISDIR(info.st_mode):
                    return SimpleNamespace(st_uid=os.getuid() + 1, st_mode=info.st_mode)
                return info
            with mock.patch.object(local_artifact.os, "fstat", side_effect=unsafe_parent):
                with self.assertRaises(ValueError):
                    local_artifact.validate(str(source), os.getuid(), digest)
            alias = base / "alias"
            alias.symlink_to(base, target_is_directory=True)
            with self.assertRaises(OSError):
                local_artifact.validate(str(alias / "artifact"), os.getuid(), digest)
            for bad in (b"not executable".ljust(120, b" "), static_elf(machine=183),
                        static_elf(program_type=2), static_elf(program_type=3)):
                source.write_bytes(bad)
                with self.assertRaises(ValueError):
                    local_artifact.validate(str(source), os.getuid(),
                                            hashlib.sha256(bad).hexdigest())
            source.write_bytes(payload)
            with self.assertRaises(ValueError):
                local_artifact.validate(str(source), os.getuid(), "0" * 64)
            for size in (0, 63, local_artifact.MAX_BYTES + 1):
                with source.open("wb") as stream:
                    stream.truncate(size)
                with self.assertRaises(ValueError):
                    local_artifact.validate(str(source), os.getuid(), digest)
            source.write_bytes(payload)
            # A late mutation after hashing is refused by the descriptor metadata check.
            calls = 0
            def changed_source(fd):
                nonlocal calls
                info = real_fstat(fd)
                if stat.S_ISREG(info.st_mode):
                    calls += 1
                    if calls == 3:
                        source.write_bytes(payload + b"late mutation")
                        info = real_fstat(fd)
                return info
            with mock.patch.object(local_artifact.os, "fstat", side_effect=changed_source):
                with self.assertRaises(ValueError):
                    local_artifact.validate(str(source), os.getuid(), digest)

    def test_local_staging_actual_ansible_check_apply_noop_and_race(self):
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as scratch:
            base = Path(scratch)
            inputs = base / "inputs"
            inputs.mkdir(mode=0o700)
            source = inputs / "artifact"
            payload = static_elf()
            source.write_bytes(payload)
            source.chmod(0o600)
            digest = hashlib.sha256(payload).hexdigest()
            package = base / "package"
            library = base / "library"
            library.mkdir()
            # Keep production guard code and UID0 checks. Redirect package and CA boundaries.
            ca = base / "synthetic-ca.crt"
            ca.write_text("synthetic metadata fixture, never a trust bundle")
            ca.chmod(0o644)
            wrapper = (
                "#!/usr/bin/python\nimport importlib.util\nfrom pathlib import Path\n"
                "from ansible.module_utils.basic import AnsibleModule\n"
                f"spec = importlib.util.spec_from_file_location('guard', {str(ROOT / 'playbooks/library/web_mcp_guard.py')!r})\n"
                "guard = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(guard)\n"
                f"guard.PACKAGE_ROOT = Path({str(package)!r})\n"
                "inspect = guard.inspect\n"
                "def fixture_inspect(path, *args, **kwargs):\n"
                f"    path = {str(ca)!r} if str(path) == '/etc/ssl/certs/ca-certificates.crt' else path\n"
                "    return inspect(path, *args, **kwargs)\n"
                "guard.inspect = fixture_inspect\nguard.main()\n")
            (library / "web_mcp_guard.py").write_text(wrapper)
            inventory = base / "inventory.yml"
            inventory.write_text("all:\n  hosts:\n    fixture:\n      ansible_connection: local\n")
            original = yaml.safe_load((ROOT / "roles/web_mcp_endpoint/tasks/main.yml").read_text())[0]
            staged = copy.deepcopy(original)
            tasks = []
            for task in staged["block"]:
                if task["name"] == "Create a root-owned protected configuration directory":
                    break  # This fixture qualifies staging, never runtime/config/activation.
                if any(key in task for key in ("ansible.builtin.group", "ansible.builtin.user")):
                    continue  # Account creation cannot run in this job's single-UID namespace.
                if "ansible.builtin.service_facts" in task:
                    task = {"name": task["name"], "ansible.builtin.set_fact": {
                        "ansible_facts": "{{ ansible_facts | combine({'services': fixture_services}) }}"}}
                # Chaldea maps UID/GID0 as job, without root NSS entries.
                # Numeric 0 retains the exact production ownership requirement.
                for args in task.values():
                    if isinstance(args, dict):
                        for field in ("owner", "group"):
                            if args.get(field) == "root":
                                args[field] = "0"
                tasks.append(task)
            staged["block"] = yaml.safe_load(yaml.safe_dump(tasks).replace("/opt/web-mcp", str(package)))
            values = fixture("homelab-observer")
            values["artifact"].update(sha256=digest, local_source={
                "path": str(source), "owner_uid": os.getuid()})
            defaults = yaml.safe_load((ROOT / "roles/web_mcp_endpoint/defaults/main.yml").read_text())
            playbook = base / "stage.yml"
            def write_play(race=False):
                chosen = copy.deepcopy(staged)
                if race:
                    index = next(i for i, task in enumerate(chosen["block"])
                                 if task["name"] == "Create root-owned release directories")
                    chosen["block"].insert(index, {"name": "Synthetic source change after preflight",
                        "ansible.builtin.copy": {"content": "alternate bytes must never install",
                                               "dest": str(source), "mode": "0600"}})
                playbook.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                    "vars": {**defaults, "web_mcp_endpoint_enabled": True,
                             "web_mcp_endpoint": values, "fixture_services": {},
                             "ansible_facts": {"system": "Linux", "os_family": "Debian",
                                "architecture": "x86_64", "service_mgr": "systemd"}},
                    "tasks": [chosen]}]))
            env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                ANSIBLE_LIBRARY=os.pathsep.join((str(library),
                    str(ROOT / "roles/web_mcp_endpoint/library"))),
                ANSIBLE_LOCAL_TEMP=str(base / "local-tmp"),
                ANSIBLE_REMOTE_TEMP=str(base / "remote-tmp"))
            command = ["ansible-playbook", "-i", str(inventory), str(playbook)]
            def run(*extra, success=True):
                result = subprocess.run(command + list(extra), env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                if success and result.returncode:
                    self.fail(result.stdout)
                if not success:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                return result.stdout
            target = package / values["service"] / "releases" / digest / values["service"]
            # Every malformed input fails in the real controller module before mkdir/copy.
            for bad in (b"not ELF".ljust(120, b" "), static_elf(machine=183),
                        static_elf(program_type=2), static_elf(program_type=3)):
                source.write_bytes(bad)
                values["artifact"]["sha256"] = hashlib.sha256(bad).hexdigest()
                write_play()
                refused = run(success=False)
                self.assertIn("Validate protected local artifact bytes", refused)
                self.assertNotIn("Create root-owned release directories", refused)
                self.assertFalse(package.exists())
            values["artifact"]["sha256"] = digest
            source.write_bytes(payload)
            for mutation in ("checksum", "mode", "owner", "parent", "symlink", "hardlink"):
                alias = base / "alias"
                if mutation == "checksum":
                    values["artifact"]["sha256"] = "0" * 64
                elif mutation == "mode":
                    source.chmod(0o644)
                elif mutation == "owner":
                    values["artifact"]["local_source"]["owner_uid"] = os.getuid() + 1
                elif mutation == "parent":
                    inputs.chmod(0o777)
                elif mutation == "symlink":
                    alias.symlink_to(source)
                    values["artifact"]["local_source"]["path"] = str(alias)
                else:
                    os.link(source, alias)
                write_play()
                refused = run(success=False)
                self.assertIn("Validate protected local artifact bytes", refused)
                self.assertNotIn("Create root-owned release directories", refused)
                self.assertFalse(package.exists())
                if alias.exists():
                    alias.unlink()
                inputs.chmod(0o700)
                source.chmod(0o600)
                values["artifact"].update(sha256=digest, local_source={
                    "path": str(source), "owner_uid": os.getuid()})
            write_play()
            checked = run("--check")
            self.assertFalse(package.exists())
            self.assertIn("changed=", checked)
            run()
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(target.stat().st_uid, 0)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o555)
            checked_run([str(target)], check=True)  # Qualify the synthetic ELF itself.
            self.assertIn("changed=0", run())
            self.assertIn("changed=0", run("--check"))
            # With local_source omitted, the existing get_url checksum path is a no-op.
            del values["artifact"]["local_source"]
            write_play()
            self.assertIn("changed=0", run())
            values["artifact"]["local_source"] = {"path": str(source), "owner_uid": os.getuid()}
            write_play()
            for state in ({"state": "running"}, {"status": "enabled"}):
                run("-e", json.dumps({"fixture_services": {"homelab-observer.service": state}}),
                    success=False)
                self.assertEqual(target.read_bytes(), payload)
            # Make a new target absent, then mutate bytes between preflight and copy.
            target.unlink()
            write_play(race=True)
            refused = run(success=False)
            self.assertIn("Stage the prevalidated local executable", refused)
            self.assertFalse(target.exists())

    def test_enabled_route_role_without_inventory_traefik_variables(self):
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as scratch:
            base = Path(scratch)
            edge = base / "edge"
            edge.mkdir()
            fixed = yaml.safe_load((ROOT / "roles/web_mcp_route/vars/main.yml").read_text())
            self.assertEqual(fixed, {
                "_web_mcp_route_directory": str(load("fixed_guard",
                    "playbooks/library/web_mcp_guard.py").EDGE),
                "_web_mcp_route_resolver": "letencrypt"})
            # Redirect only the fixed filesystem boundary for this local fixture.
            # The actual role tasks, vars, template and guard implementation run.
            library = base / "library"
            library.mkdir()
            wrapper = (
                "import importlib.util\n"
                "from pathlib import Path\n"
                "from ansible.module_utils.basic import AnsibleModule\n"
                f"spec = importlib.util.spec_from_file_location('guard', {str(ROOT / 'playbooks/library/web_mcp_guard.py')!r})\n"
                "assert spec is not None and spec.loader is not None\n"
                "guard = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(guard)\n"
                f"guard.EDGE = Path({str(edge)!r})\n"
                "guard.main()\n"
            )
            (library / "web_mcp_guard.py").write_text(wrapper)
            inventory = base / "inventory.yml"
            inventory.write_text("all:\n  hosts:\n    fixture:\n      ansible_connection: local\n")
            selected = route()
            selected["directory_uid"] = os.getuid()
            playbook = base / "enabled-route.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "fixture", "gather_facts": False,
                "vars": {"web_mcp_route_enabled": True, "web_mcp_route": selected},
                "roles": ["web_mcp_route"],
                "post_tasks": [{"name": "No inventory Traefik variables supplied",
                    "ansible.builtin.assert": {"that": [
                        "traefik_dynamic_dir is not defined",
                        "traefik_cert_resolver is not defined"]}}]}]))
            env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                       ANSIBLE_LIBRARY=str(library),
                       ANSIBLE_LOCAL_TEMP=str(base / "local-tmp"),
                       ANSIBLE_REMOTE_TEMP=str(base / "remote-tmp"))
            command = ["ansible-playbook", "-i", str(inventory), str(playbook),
                       "-e", json.dumps({"_web_mcp_route_directory": str(edge)})]
            first = checked_run(command, env=env, check=True, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
            second = checked_run(command, env=env, check=True, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
            self.assertIn("changed=1", first)
            self.assertIn("changed=0", second)
            routefile = edge / "web-mcp-ha-mcp.yml"
            dynamic = yaml.safe_load(routefile.read_text())
            self.assertEqual(dynamic["http"]["routers"]["web-mcp-ha-mcp"]["tls"]["certResolver"],
                             "letencrypt")
            for name, invalid in (("traefik_dynamic_dir", "/unapproved"),
                                  ("traefik_cert_resolver", "unapproved")):
                refused = subprocess.run(command + ["-e", json.dumps({name: invalid})],
                    env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("Require the existing file-provider", refused.stdout)
                self.assertNotIn("Guard the route directory", refused.stdout)
            selected["state"] = "absent"
            removed = checked_run(command + ["-e", json.dumps({"web_mcp_route": selected})],
                env=env, check=True, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT).stdout
            self.assertIn("changed=1", removed)
            self.assertFalse(routefile.exists())

    def test_ansible_syntax_and_template_idempotence(self):
        # The generated inventory and all outputs stay within the asserted scratch HOME.
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as scratch:
            base = Path(scratch)
            inventory = base / "inventory.yml"
            inventory.write_text("all:\n  hosts:\n    fixture:\n      ansible_connection: local\n")
            env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                       ANSIBLE_LIBRARY=str(ROOT / "playbooks/library"),
                       ANSIBLE_LOCAL_TEMP=str(base / "local-tmp"),
                       ANSIBLE_REMOTE_TEMP=str(base / "remote-tmp"))
            for playbook in ("web-mcp-endpoint", "web-mcp-route"):
                command = ["ansible-playbook", "-i", str(inventory), "--syntax-check",
                           str(ROOT / "playbooks" / (playbook + ".yml"))]
                checked_run(command, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            # A limit must not bypass explicit scope gates or reach facts/managed paths.
            for playbook, target in (("web-mcp-endpoint", "web_mcp_target"),
                                      ("web-mcp-route", "web_mcp_edge_target")):
                refused = subprocess.run(["ansible-playbook", "-i", str(inventory),
                    str(ROOT / "playbooks" / (playbook + ".yml")), "--limit", "fixture",
                    "-e", target + "=fixture"], env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("Refuse implicit scope", refused.stdout)
                self.assertNotIn("Gather platform facts", refused.stdout)
            tasks = [{"name": "Validate actual Ansible values", "ansible.builtin.assert": {
                "that": ["(web_mcp | web_mcp_endpoint_contract).service == 'ha-mcp'",
                         "(route_input | web_mcp_route_contract).host == 'ha.mcp.example.invalid'"]}}]
            values = {"route_input": route(), "web_mcp_edge": contract.route_contract(route()),
                      "web_mcp": fixture(), **yaml.safe_load(
                          (ROOT / "roles/web_mcp_route/vars/main.yml").read_text())}
            for role, source, dest in (("web_mcp_route", "route.yml.j2", "route.yml"),
                                       ("web_mcp_endpoint", "endpoint.service.j2", "endpoint.service")):
                tasks.append({"name": "Render " + role, "ansible.builtin.template": {
                    "src": str(ROOT / "roles" / role / "templates" / source),
                    "dest": str(base / dest), "mode": "0600"}})
            playbook = base / "render.yml"
            playbook.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                                                "vars": values, "tasks": tasks}]))
            command = ["ansible-playbook", "-i", str(inventory), str(playbook)]
            first = checked_run(command, env=env, check=True, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
            second = checked_run(command, env=env, check=True, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
            self.assertIn("changed=2", first)
            self.assertIn("changed=0", second)
            # Actual no-op defaults, with no live facts, paths, credentials or systemd.
            disabled = base / "disabled.yml"
            disabled.write_text(yaml.safe_dump([{"hosts": "fixture", "gather_facts": False,
                "roles": ["web_mcp_endpoint", "web_mcp_route"]}]))
            result = checked_run(["ansible-playbook", "-i", str(inventory), str(disabled)],
                                    env=env, check=True, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
            self.assertIn("changed=0", result)


if __name__ == "__main__":
    expected = ROOT / ".cache/web-mcp-tests/home"
    assert Path(os.environ["HOME"]) == expected
    assert Path(os.environ["TMPDIR"]) == expected / "tmp"
    unittest.main(verbosity=2)
