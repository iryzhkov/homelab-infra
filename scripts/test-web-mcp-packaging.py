#!/usr/bin/env python3
"""Synthetic source contracts and isolated Ansible render fixtures. No live inventory."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
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
    elif service == "holonet":
        value["artifact"]["source_revision"] = contract.SERVICES[service][1]
        value["config"] = {
            "resource": "https://media.mcp.ryzhkov.dev/mcp",
            "issuer": "https://auth.example.invalid/application/o/holonet/",
            "jwks_url": "https://auth.example.invalid/application/o/holonet/jwks/",
            "server_id": "a" * 32, "backend_url": "http://127.0.0.1:8096/emby",
            "credential_file": "/etc/holonet/backend.json", "policy_revision": "b" * 40,
            "policy": {"Server": "a" * 32, "Actions": ["catalog.read"],
                       "Libraries": ["1"], "CatalogUsers": ["c" * 32], "HistoryUsers": []},
            "library_guids": {"1": "d" * 32},
        }
        value["credentials"] = [{"name": "backend.json",
                                 "source": "/protected/holonet-backend", "source_uid": 1000}]
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


def jocasta_route():
    return {"service": "jocasta", "state": "present", "directory_uid": 1000,
            "resource": "https://jocasta.mcp.ryzhkov.dev/mcp",
            "upstream": "http://10.42.0.2:1935"}


# Synthetic stand-ins for root's reviewed playlist_operator build pins.
OPERATOR_SOURCE, OPERATOR_INTERFACE = "e" * 40, "f" * 40
# The fixed protected cohort as provisioned (docs/emby-operator-viewers.md).
OPERATOR_COHORT = {
    "545014d1c9d1416b8e0b5a445d6ff9ec": "/var/lib/holonet-operator/igor-pr116-retain-20261031-next/config.json",
    "b8f4f6de49b443f1948cf0118679e863": "/var/lib/holonet-operator/viewers-20261101/tv/config.json",
    "db2f3bfb1f84438ebcde94f228183011": "/var/lib/holonet-operator/viewers-20261101/kostya/config.json",
    "313e39bb4d8c4b92b81d4b170c3f1b8e": "/var/lib/holonet-operator/viewers-20261101/lisa/config.json",
    "c06c7d2c3e224d8ba2e466efa5bcb97b": "/var/lib/holonet-operator/viewers-20261101/oleg/config.json",
    "22703968ab0b48eebb4faf7c1d16262c": "/var/lib/holonet-operator/viewers-20261101/tanya/config.json",
    "e05efa68303043b893b0f5c2e200ccd7": "/var/lib/holonet-operator/viewers-20261101/tetiana/config.json",
}


def operator_pins():
    return mock.patch.multiple(contract, HOLONET_OPERATOR_SOURCE=OPERATOR_SOURCE,
                               HOLONET_OPERATOR_CONTRACT=OPERATOR_INTERFACE)


def operator_fixture(recipients=None):
    value = fixture("holonet")
    value.update(uid=940, gid=940)
    value["artifact"].update(source_revision=OPERATOR_SOURCE, contract_revision=OPERATOR_INTERFACE)
    config = value["config"]
    config["server_id"] = "3c86e6d7ff6c4a3296522cdf03d22bb0"
    config["policy"].update(Server=config["server_id"], Libraries=["3"],
                            CatalogUsers=["54b7d36cef804d3c8478474553ed3a82"])
    config["library_guids"] = {"3": "d" * 32}
    config["playlist_operator"] = {
        "wire_version": 1, "subject": "writer-subject", "namespace": "managed-recommendations",
        "library": "3", "catalog_user": "54b7d36cef804d3c8478474553ed3a82",
        "recipients": list(OPERATOR_COHORT) if recipients is None else recipients}
    return value


# Synthetic stand-ins for the pending reviewed playback build pins.
PLAYBACK_SOURCE, PLAYBACK_INTERFACE = "1" * 40, "2" * 40


def playback_pins():
    return mock.patch.multiple(contract, HOLONET_PLAYBACK_SOURCE=PLAYBACK_SOURCE,
                               HOLONET_PLAYBACK_CONTRACT=PLAYBACK_INTERFACE)


def playback_fixture(history=None):
    """Management plus scoped playback, as the reviewed playback build accepts it."""
    value = operator_fixture()
    value["artifact"].update(source_revision=PLAYBACK_SOURCE, contract_revision=PLAYBACK_INTERFACE)
    config = value["config"]
    config["policy"].update(Actions=["catalog.read", "history.read"],
                            HistoryUsers=[sorted(OPERATOR_COHORT)[0]] if history is None else history)
    config["playback"] = {"jellystat_url": "http://127.0.0.1:3012",
                          "credential_file": "/etc/holonet/jellystat.json",
                          "credential_expires_at": "2027-02-15T00:00:00Z"}
    value["credentials"].append({"name": "jellystat.json", "source": "/protected/holonet-jellystat",
                                 "source_uid": 1000})
    return value


def directory(path, mode=0o755):
    path.mkdir()
    path.chmod(mode)  # The job umask must not change the intended fixture mode.
    return path


def private_file(path, content, mode):
    path.write_text(content)
    path.chmod(mode)
    return path


def operator_layout(base):
    """Synthetic fixed root paths and two provisioned recipients below scratch."""
    for part in ("opt", "run", "bin", "etc", "etc/systemd", "etc/systemd/system", "etc/tmpfiles.d"):
        directory(base / part)
    private_file(base / "bin/python3.11", "synthetic interpreter", 0o755)
    (base / "bin/python3").symlink_to("python3.11")
    state = directory(base / "state")
    directory(state / "viewers", 0o700)
    journals, configs = [], []
    for root in (state / "igor", state / "viewers" / "tv"):
        directory(root, 0o700)
        journal = directory(root / "journal", 0o700)
        private_file(journal / "journal.json", "synthetic retained journal", 0o600)
        configs.append(str(private_file(root / "config.json", "{}", 0o600)))
        journals.append(str(journal))
    credential = private_file(state / "igor" / "credential.json", "synthetic", 0o600)
    units = (base / "etc/systemd/system/holonet-playlists-worker.socket",
             base / "etc/systemd/system/holonet-playlists-worker@.service",
             base / "etc/tmpfiles.d/holonet-playlists.conf")

    def patch():
        return mock.patch.multiple(
            guard, OPERATOR_PACKAGE=base / "opt/holonet-playlists",
            OPERATOR_POLICY=base / "etc/holonet-playlists", OPERATOR_RUNTIME=base / "run/holonet-playlists",
            OPERATOR_STATE=state, OPERATOR_INTERPRETER=base / "bin/python3", OPERATOR_UNITS=units,
            ROOT_UID=os.getuid())
    return SimpleNamespace(base=base, state=state, journals=journals, configs=configs,
                           credential=credential, units=units, patch=patch)


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

    def test_holonet_reviewed_catalog_upgrade_and_original_rollback(self):
        old = "4aceab9770f0098b4369e37657f6b64051884baf"
        new = "31817f70f7fed5900622bc7274c6a8a31275189b"
        self.assertEqual(contract.HOLONET_CATALOG_SOURCE_REVISIONS, {old, new})
        self.assertEqual(contract.HOLONET_SOURCE_REVISIONS, {old, new, contract.HOLONET_WRITER_SOURCE})
        self.assertEqual(contract.SERVICES["holonet"][1], old)
        for revision in (old, new, old):
            value = fixture("holonet")
            value["artifact"]["source_revision"] = revision
            value["artifact"]["contract_revision"] = old
            value["artifact"]["local_source"] = {"path": "/protected/holonet-binary", "owner_uid": 1000}
            value["artifact"].pop("url")
            before = copy.deepcopy(value)
            self.assertEqual(contract.endpoint_contract(value), before)
            # The reviewed optional query/metadata extension cannot widen the
            # same unchanged config, credentials or one-library/user policy.
            for field, bad in (("HistoryUsers", ["e" * 32]),
                               ("Actions", ["catalog.read", "userdata.read"]),
                               ("Actions", ["playlist.manage"])):
                broken = copy.deepcopy(value)
                broken["config"]["policy"][field] = bad
                self.rejected(broken)
            for source in ("a" * 40, new.upper(), new[:-1], contract.SERVICES["ha-mcp"][1]):
                broken = copy.deepcopy(value)
                broken["artifact"]["source_revision"] = source
                self.rejected(broken)
            for interface in (new, "a" * 40):
                broken = copy.deepcopy(value)
                broken["artifact"]["contract_revision"] = interface
                self.rejected(broken)

    def test_holonet_writer_config_requires_exact_new_artifact_and_bounded_policy(self):
        value = fixture("holonet")
        value["artifact"].update(source_revision=contract.HOLONET_WRITER_SOURCE,
                                 contract_revision=contract.HOLONET_WRITER_CONTRACT)
        policy = {
            "state_directory": "/var/lib/holonet/recommendations", "owner": "c" * 32,
            "subjects": ["writer-subject"], "namespaces": ["managed-recommendations"],
            "actions": ["catalog.read", "playlist.read", "playlist.manage", "object.share", "home.read", "home.manage"],
            "libraries": ["1"], "catalog_users": ["c" * 32],
            "targets": {"e" * 32: {"baseline_ids": ["baseline", 1], "anchor_id": "baseline"}},
        }
        value["config"]["recommendations"] = policy
        before = copy.deepcopy(value)
        self.assertEqual(contract.endpoint_contract(value), before)
        for source in contract.HOLONET_CATALOG_SOURCE_REVISIONS:
            bad = copy.deepcopy(value)
            bad["artifact"].update(source_revision=source, contract_revision=contract.SERVICES["holonet"][1])
            self.rejected(bad)
        bad = copy.deepcopy(value)
        bad["artifact"]["contract_revision"] = contract.SERVICES["holonet"][1]
        self.rejected(bad)
        for key, item in (
                ("state_directory", "/var/lib/holonet"), ("state_directory", "/var/lib/holonet/../other"),
                ("owner", "e" * 32), ("subjects", []), ("subjects", ["same", "same"]),
                ("subjects", ["a b"]), ("namespaces", ["other"]),
                ("actions", ["playlist.manage"]), ("actions", policy["actions"] + ["media.delete"]),
                ("libraries", ["2"]), ("catalog_users", ["e" * 32]), ("targets", {}),
                ("targets", {"e" * 32: {"baseline_ids": ["baseline"], "anchor_id": "missing"}}),
                ("targets", {"e" * 32: {"baseline_ids": [True], "anchor_id": True}}),
                ("targets", {"e" * 32: {"baseline_ids": [1, 1], "anchor_id": 1}})):
            bad = copy.deepcopy(value)
            bad["config"]["recommendations"][key] = item
            with self.subTest(key=key, item=item):
                self.rejected(bad)
        value["config"]["recommendations"]["actions"].append("object.delete")
        contract.endpoint_contract(value)
        template = (ROOT / "roles/web_mcp_endpoint/templates/endpoint.service.j2").read_text()
        env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
        enabled = env.from_string(template).render(web_mcp=value)
        self.assertEqual(enabled.count("ReadWritePaths="), 1)
        self.assertIn("ReadWritePaths=/var/lib/holonet/recommendations", enabled)
        self.assertIn("ProtectSystem=strict", enabled)
        value["config"].pop("recommendations")
        self.assertNotIn("ReadWritePaths", env.from_string(template).render(web_mcp=value))

    def test_holonet_private_journal_preflight_preserves_unknown_paths(self):
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as scratch:
            state = Path(scratch) / "state"
            with mock.patch.object(guard, "JOURNAL_ROOT", state):
                guard.recommendation_journal(os.getuid(), os.getgid())
                state.mkdir(mode=0o755)
                state.chmod(0o755)  # Job umask must not change the intended parent fixture.
                journal = state / "recommendations"
                journal.mkdir(mode=0o700)
                guard.recommendation_journal(os.getuid(), os.getgid())
                sentinel = journal / "state.json"
                sentinel.write_text("synthetic existing state")
                original = sentinel.read_bytes()
                journal.chmod(0o755)
                with self.assertRaises(ValueError):
                    guard.recommendation_journal(os.getuid(), os.getgid())
                self.assertEqual(sentinel.read_bytes(), original)
                journal.chmod(0o700)
                with self.assertRaises(ValueError):
                    guard.recommendation_journal(os.getuid() + 1, os.getgid())
                with self.assertRaises(ValueError):
                    guard.recommendation_journal(os.getuid(), os.getgid() + 1)
                journal.rename(state / "saved")
                journal.symlink_to(state / "saved", target_is_directory=True)
                with self.assertRaises(ValueError):
                    guard.recommendation_journal(os.getuid(), os.getgid())
                self.assertEqual(sentinel.read_bytes(), original)

    def test_holonet_journal_tasks_check_apply_repeat_and_preserve(self):
        original = yaml.safe_load((ROOT / "roles/web_mcp_endpoint/tasks/main.yml").read_text())[0]["block"]
        names = [task["name"] for task in original]
        parent_name = "Create the fixed root-owned HoloNet state parent"
        child_name = "Create only the private runtime-owned recommendation journal directory"
        self.assertLess(names.index("Guard fixed paths and dedicated identities without reading secrets"),
                        names.index(parent_name))
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as scratch:
            base = Path(scratch)
            state = base / "state"
            tasks = []
            for task in original:
                if task["name"] in (parent_name, child_name):
                    task = copy.deepcopy(task)
                    args = task["ansible.builtin.file"]
                    args["path"] = args["path"].replace("/var/lib/holonet", str(state))
                    for field in ("owner", "group"):
                        if args.get(field) == "root":
                            args[field] = "0"
                    tasks.append(task)
            self.assertEqual(len(tasks), 2)
            play = base / "journal.yml"
            play.write_text(yaml.safe_dump([{"hosts": "localhost", "connection": "local",
                "gather_facts": False, "vars": {"web_mcp": {"service": "holonet",
                    "uid": os.getuid(), "gid": os.getgid(), "config": {"recommendations": {"selected": True}}}},
                "tasks": tasks}]))
            env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                       ANSIBLE_LOCAL_TEMP=str(base / "local-tmp"), ANSIBLE_REMOTE_TEMP=str(base / "remote-tmp"))
            def run(*flags):
                with mock.patch.object(guard, "JOURNAL_ROOT", state):
                    guard.recommendation_journal(os.getuid(), os.getgid())
                result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(play), *flags],
                                        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout)
                return result.stdout
            run("--check")
            self.assertFalse(state.exists())
            run()
            journal = state / "recommendations"
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o700)
            sentinel = journal / "state.json"
            sentinel.write_text("synthetic retained journal")
            self.assertIn("changed=0", run())
            self.assertIn("changed=0", run("--check"))
            self.assertEqual(sentinel.read_text(), "synthetic retained journal")
            # Existing unsafe state refuses in the exact production preflight
            # before the selected role tasks get an opportunity to chmod it.
            journal.chmod(0o755)
            with self.assertRaises(ValueError):
                run()
            self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o755)
            self.assertEqual(sentinel.read_text(), "synthetic retained journal")

    def test_holonet_catalog_only_contract_and_protected_artifact(self):
        value = fixture("holonet")
        artifact = value["artifact"]
        del artifact["url"]
        artifact["local_source"] = {"path": "/protected/holonet-binary", "owner_uid": 1000}
        self.assertEqual(contract.endpoint_contract(value), value)
        for field, bad in (("local_source", {"path": "/protected/../binary", "owner_uid": 1000}),
                           ("source_revision", "a" * 40), ("source", "/unregistered")):
            broken = copy.deepcopy(value)
            broken["artifact"][field] = bad
            self.rejected(broken)
        for field, bad in (("resource", "https://other.mcp.ryzhkov.dev/mcp"),
                           ("credential_file", "/root/operator-key"),
                           ("backend_url", "http://192.168.70.240:8096/emby"),
                           ("backend_url", "https://example.invalid/emby?key=unsafe"),
                           ("server_id", "a" * 31), ("library_guids", {"1": "1"})):
            broken = copy.deepcopy(value)
            broken["config"][field] = bad
            self.rejected(broken)
        for field, bad in (("Actions", ["catalog.read", "userdata.read"]),
                           ("Actions", ["playlist.manage"]), ("HistoryUsers", ["e" * 32]),
                           ("Libraries", ["1", "2"]), ("Libraries", ["01"]),
                           ("Libraries", [str(2**63)]), ("CatalogUsers", []),
                           ("CatalogUsers", ["c" * 32, "e" * 32]), ("Server", "f" * 32)):
            broken = copy.deepcopy(value)
            broken["config"]["policy"][field] = bad
            self.rejected(broken)
        edge = route()
        edge.update(service="holonet", resource=value["config"]["resource"])
        self.assertEqual(contract.route_contract(edge)["host"], "media.mcp.ryzhkov.dev")
        edge["resource"] = "https://wrong.example.invalid/mcp"
        with self.assertRaises(AnsibleFilterError):
            contract.route_contract(edge)
        # Protected binary transport reuses the existing no-follow single-link source gate.
        tasks = yaml.safe_load((ROOT / "roles/web_mcp_endpoint/tasks/main.yml").read_text())[0]["block"]
        names = [task["name"] for task in tasks]
        self.assertLess(names.index("Validate protected local artifact bytes before managed writes"),
                        names.index("Create the dedicated system group"))
        self.assertLess(names.index("Validate HoloNet config as the runtime identity without opening credentials"),
                        names.index("Install separately supplied protected backend credentials"))
        check = next(task for task in tasks if task["name"].startswith("Validate HoloNet config"))
        self.assertIn("--check-config", check["ansible.builtin.command"]["argv"])
        self.assertTrue(check["no_log"])
        self.assertFalse(check["changed_when"])
        self.assertEqual(check["become_user"], "{{ web_mcp.service }}")

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
            if service in ("homelab-observer", "holonet"):
                self.assertIn("--listen 192.168.70.240:8787", unit)
                self.assertIn("--config /etc/" + service + "/config.json", unit)

    def test_jocasta_route_is_fixed_and_only_it_rewrites_host(self):
        # trim_blocks matches Ansible's template rendering.
        env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True,
                                 trim_blocks=True)
        source = (ROOT / "roles/web_mcp_route/templates/route.yml.j2").read_text()
        fixed = yaml.safe_load((ROOT / "roles/web_mcp_route/vars/main.yml").read_text())
        edge = contract.route_contract(jocasta_route())
        self.assertEqual((edge["host"], edge["host_rewrite"]), ("jocasta.mcp.ryzhkov.dev", "10.42.0.2:1935"))
        dynamic = yaml.safe_load(env.from_string(source).render(web_mcp_edge=edge, **fixed))
        router = dynamic["http"]["routers"]["web-mcp-jocasta"]
        self.assertEqual(router["rule"], "Host(`jocasta.mcp.ryzhkov.dev`) && (Path(`/mcp`) || "
                         "Path(`/.well-known/oauth-protected-resource/mcp`)) && !HeaderRegexp(`Cf-Ray`, `.*`)")
        self.assertEqual(router["middlewares"], ["web-mcp-jocasta-host"])
        self.assertEqual(router["tls"], {"certResolver": "letencrypt",
                                         "domains": [{"main": "jocasta.mcp.ryzhkov.dev"}]})
        # Only Host is rewritten; Origin and every other header pass unchanged.
        self.assertEqual(dynamic["http"]["middlewares"], {"web-mcp-jocasta-host": {
            "headers": {"customRequestHeaders": {"Host": "10.42.0.2:1935"}}}})
        self.assertEqual(dynamic["http"]["services"]["web-mcp-jocasta"]["loadBalancer"]["servers"],
                         [{"url": "http://10.42.0.2:1935"}])
        for key, value in (("resource", "https://jocasta.ryzhkov.dev/mcp"),
                           ("resource", "https://media.mcp.ryzhkov.dev/mcp"),
                           ("upstream", "http://10.42.0.3:1935"), ("upstream", "http://10.42.0.2:8080"),
                           ("upstream", "https://10.42.0.2:1935"), ("upstream", "http://127.0.0.1:1935")):
            with self.subTest(**{key: value}), self.assertRaises(AnsibleFilterError):
                contract.route_contract(dict(jocasta_route(), **{key: value}))
        removal = {"service": "jocasta", "state": "absent", "directory_uid": 1000}
        self.assertEqual(contract.route_contract(removal), removal)
        # Every other service renders exactly the template without the Jocasta blocks.
        plain = re.sub(r"\{% if web_mcp_edge.service == 'jocasta' %\}\n.*?\{% endif %\}\n", "",
                       source, flags=re.S)
        self.assertEqual(source.count("web_mcp_edge.service == 'jocasta'"), 2)
        self.assertNotIn("jocasta", plain)
        for service, resource in (("ha-mcp", "https://ha.mcp.example.invalid/mcp"),
                                  ("homelab-observer", "https://homelab.mcp.example.invalid/mcp"),
                                  ("holonet", "https://media.mcp.ryzhkov.dev/mcp"),
                                  ("ov-http", "https://ov.mcp.example.invalid/mcp")):
            other = contract.route_contract(dict(route(), service=service, resource=resource))
            self.assertNotIn("host_rewrite", other)
            rendered = env.from_string(source).render(web_mcp_edge=other, **fixed)
            self.assertEqual(rendered, env.from_string(plain).render(web_mcp_edge=other, **fixed))
            self.assertNotIn("middlewares", rendered)

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
                if service == "holonet" and field == "url":
                    contract.endpoint_contract(bad)
                else:
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
            # The actual role and guard also accept the fixed Jocasta route.
            jocasta = dict(jocasta_route(), directory_uid=os.getuid())
            checked_run(command + ["-e", json.dumps({"web_mcp_route": jocasta})], env=env,
                        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            jocasta_file = (edge / "web-mcp-jocasta.yml").read_text()
            self.assertTrue(jocasta_file.startswith("# Managed by homelab-infra web-mcp jocasta\n"))
            self.assertEqual(yaml.safe_load(jocasta_file)["http"]["middlewares"], {"web-mcp-jocasta-host": {
                "headers": {"customRequestHeaders": {"Host": "10.42.0.2:1935"}}}})

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

    def test_playlist_operator_is_default_off_and_needs_its_reviewed_artifact(self):
        for service in contract.SERVICES:
            self.assertEqual(contract.playlist_operator_plan(fixture(service)), {"selected": False})
        # Synthetic pins cannot select the separately pinned production candidate.
        self.rejected(operator_fixture())
        with operator_pins():
            managed = operator_fixture()
            before = copy.deepcopy(managed)
            self.assertEqual(contract.endpoint_contract(managed), before)
            self.assertTrue(contract.playlist_operator_plan(managed)["selected"])
            # The new build alone stays catalog-only: absent and null both mean off.
            for null in (False, True):
                value = copy.deepcopy(managed)
                value["config"].pop("playlist_operator")
                if null:
                    value["config"]["playlist_operator"] = None
                before = copy.deepcopy(value)
                self.assertEqual(contract.endpoint_contract(value), before)
                self.assertEqual(contract.playlist_operator_plan(value), {"selected": False})
            # Every existing exact pair stays accepted for catalog-only config and
            # refuses management, including a null key that old binaries reject.
            pairs = [(source, contract.SERVICES["holonet"][1])
                     for source in sorted(contract.HOLONET_CATALOG_SOURCE_REVISIONS)]
            pairs.append((contract.HOLONET_WRITER_SOURCE, contract.HOLONET_WRITER_CONTRACT))
            for source, interface in pairs:
                value = copy.deepcopy(managed)
                value["artifact"].update(source_revision=source, contract_revision=interface)
                with self.subTest(source=source):
                    self.rejected(value)
                    value["config"]["playlist_operator"] = None
                    self.rejected(value)
                    value["config"].pop("playlist_operator")
                    before = copy.deepcopy(value)
                    self.assertEqual(contract.endpoint_contract(value), before)
            for interface in (contract.SERVICES["holonet"][1], contract.HOLONET_WRITER_CONTRACT):
                value = copy.deepcopy(managed)
                value["artifact"]["contract_revision"] = interface
                self.rejected(value)

    def test_playback_is_default_off_and_requires_exact_build_pins(self):
        self.assertEqual(contract.HOLONET_PLAYBACK_SOURCE, "6c1312693a9d20432f46e6a33ac256698ee46f74")
        self.assertEqual(contract.HOLONET_PLAYBACK_CONTRACT, "cfea03b6e99e8b501ce5b6f1abbe0d32545ecd3e")
        self.rejected(playback_fixture())
        with operator_pins():
            self.rejected(playback_fixture())
            # The current management build refuses the key, even null.
            value = operator_fixture()
            value["config"]["playback"] = None
            self.rejected(value)
            value = playback_fixture()
            value["artifact"].update(source_revision=OPERATOR_SOURCE, contract_revision=OPERATOR_INTERFACE)
            self.rejected(value)
        with operator_pins(), playback_pins():
            value = playback_fixture()
            before = copy.deepcopy(value)
            self.assertEqual(contract.endpoint_contract(value), before)
            # Interface pin is exact for the playback build.
            value["artifact"]["contract_revision"] = OPERATOR_INTERFACE
            self.rejected(value)
            # Disabled: the playback build without playback keeps the catalog-only
            # ceiling and the single Emby credential, exactly as before.
            for null in (False, True):
                off = operator_fixture()
                off["artifact"].update(source_revision=PLAYBACK_SOURCE, contract_revision=PLAYBACK_INTERFACE)
                if null:
                    off["config"]["playback"] = None
                before = copy.deepcopy(off)
                self.assertEqual(contract.endpoint_contract(off), before)
                widened = copy.deepcopy(off)
                widened["config"]["policy"].update(Actions=["catalog.read", "history.read"],
                                                   HistoryUsers=[sorted(OPERATOR_COHORT)[0]])
                self.rejected(widened)
                extra = copy.deepcopy(off)
                extra["credentials"] = playback_fixture()["credentials"]
                self.rejected(extra)
            # Existing management pins keep working while playback is pending.
            self.assertEqual(contract.endpoint_contract(operator_fixture()), operator_fixture())

    def test_playback_config_credentials_and_scoped_history(self):
        ids = sorted(OPERATOR_COHORT)
        with operator_pins(), playback_pins():
            for url in ("https://jellystat.example.invalid", "https://jellystat.example.invalid:8443"):
                value = playback_fixture()
                value["config"]["playback"]["jellystat_url"] = url
                contract.endpoint_contract(value)
            value = playback_fixture(ids)
            contract.endpoint_contract(value)
            cases = []
            for url in ("http://127.0.0.1:3012/", "http://127.0.0.1:3013", "http://localhost:3012",
                        "http://192.168.70.240:3012", "https://jellystat.example.invalid/",
                        "https://jellystat.example.invalid/api", "https://u:p@jellystat.example.invalid",
                        "https://jellystat.example.invalid?x=1", "https://jellystat.example.invalid#x",
                        "https://jellystat.example.invalid:443", "https://JELLYSTAT.example.invalid",
                        "https://jelly%73tat.example.invalid", "", None):
                cases.append(("url " + str(url), lambda v, u=url: v["config"]["playback"].update(jellystat_url=u)))
            for path in ("/etc/holonet/backend.json", "/etc/holonet/other.json", "jellystat.json",
                         "/srv/swarm/jellystat/janitorr-api-key", "/etc/holonet/../holonet/jellystat.json"):
                cases.append(("path " + path, lambda v, p=path: v["config"]["playback"].update(credential_file=p)))
            for expires in ("2027-02-15", "2027-02-15T00:00:00+00:00", "2027-02-15T00:00:00.000Z",
                            "2027-02-30T00:00:00Z", "2027-02-15T24:00:00Z", "2027-02-15T00:00:60Z",
                            "2027-02-15t00:00:00z", 1893456000, None):
                cases.append(("expiry " + str(expires),
                              lambda v, e=expires: v["config"]["playback"].update(credential_expires_at=e)))
            for actions in (["catalog.read"], ["history.read", "catalog.read"],
                            ["catalog.read", "history.read", "userdata.read"], ["catalog.read", "userdata.read"],
                            ["catalog.read", "history.read", "history.read"]):
                cases.append(("actions " + str(actions),
                              lambda v, a=actions: v["config"]["policy"].update(Actions=a)))
            for history in ([], ["a" * 32], [ids[0], ids[0]], [ids[0].upper()], [1], ids[0],
                            ["%032x" % i for i in range(17)]):
                cases.append(("history " + str(history),
                              lambda v, h=history: v["config"]["policy"].update(HistoryUsers=h)))
            cases += [
                ("history outside configured recipients",
                 lambda v: v["config"]["playlist_operator"].update(recipients=[ids[1]])),
                ("no management", lambda v: v["config"].pop("playlist_operator")),
                ("null management", lambda v: v["config"].update(playlist_operator=None)),
                ("unknown field", lambda v: v["config"]["playback"].update(api_key="x")),
                ("url selector", lambda v: v["config"]["playback"].update(route="/api/getHistory")),
                ("missing field", lambda v: v["config"]["playback"].pop("credential_expires_at")),
                ("not an object", lambda v: v["config"].update(playback=[])),
                ("no Jellystat credential", lambda v: v["credentials"].pop()),
                ("renamed credential", lambda v: v["credentials"][-1].update(name="jellystat-admin.json")),
                ("extra credential", lambda v: v["credentials"].append(
                    {"name": "admin.json", "source": "/protected/admin", "source_uid": 1000})),
                # The Emby backend file is never installed again as the Jellystat key.
                ("copied Emby source", lambda v: v["credentials"][-1].update(source="/protected/holonet-backend")),
                ("uid", lambda v: v.update(uid=941)),
                ("subject", lambda v: v["config"]["playlist_operator"].update(subject="")),
                ("library", lambda v: v["config"]["playlist_operator"].update(library="1")),
                ("namespace", lambda v: v["config"]["playlist_operator"].update(namespace="movies-catalog")),
                ("library ceiling", lambda v: v["config"]["policy"].update(Libraries=["3", "4"])),
                ("catalog ceiling", lambda v: v["config"]["policy"].update(CatalogUsers=["c" * 32])),
            ]
            for name, mutate in cases:
                value = playback_fixture()
                mutate(value)
                with self.subTest(name):
                    self.rejected(value)

    def test_playback_leaves_worker_plan_unit_and_guard_names_bounded(self):
        env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
        template = (ROOT / "roles/web_mcp_endpoint/templates/endpoint.service.j2").read_text()
        # Compare plans over the current module bytes; the worker pins themselves
        # are owned and checked by the operator tests above.
        current = {name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest()
                   for name in contract.OPERATOR_MODULES}
        with operator_pins(), playback_pins(), mock.patch.dict(contract.OPERATOR_MODULES, current):
            value = playback_fixture()
            off = copy.deepcopy(value)
            off["config"].pop("playback")
            off["config"]["policy"].update(Actions=["catalog.read"], HistoryUsers=[])
            off["credentials"].pop()
            plan, plan_off = contract.playlist_operator_plan(value), contract.playlist_operator_plan(off)
        # The root worker receives only the explicit history ceiling.
        self.assertEqual(plan["policy"].pop("history_users"),
                         sorted(value["config"]["policy"]["HistoryUsers"]))
        self.assertNotIn("history_users", plan_off["policy"])
        self.assertEqual(plan, plan_off)
        self.assertNotIn("jellystat", json.dumps(plan).lower())
        # Same hardened unit: no new writable path, capability or socket.
        self.assertEqual(env.from_string(template).render(web_mcp=value),
                         env.from_string(template).render(web_mcp=off))
        self.assertNotIn("ReadWritePaths", env.from_string(template).render(web_mcp=value))
        # Two exact runtime credential names for HoloNet; others unchanged.
        for service, name, allowed in (
                ("holonet", "backend.json", True), ("holonet", "jellystat.json", True),
                ("holonet", "jellystat-admin.json", False), ("holonet", "../jellystat.json", False),
                ("holonet", "backend-token", False), ("ha-mcp", "jellystat.json", False),
                ("ha-mcp", "backend-token", True), ("homelab-observer", "gatus-a.json", True),
                ("homelab-observer", "jellystat.json", False)):
            with self.subTest(service=service, name=name):
                self.assertIs(guard.credential_name_allowed(service, name), allowed)

    def test_playlist_operator_contract_through_ansible_extra_vars(self):
        # Real extra vars are tagged by Ansible 2.19, unlike plain fixture dicts.
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as scratch:
            base = Path(scratch)
            value = operator_fixture()
            value["artifact"].update(source_revision=contract.HOLONET_OPERATOR_SOURCE,
                                     contract_revision=contract.HOLONET_OPERATOR_CONTRACT)
            play = base / "validate.yml"
            play.write_text(yaml.safe_dump([{
                "hosts": "localhost", "connection": "local", "gather_facts": False,
                "tasks": [
                    {"ansible.builtin.set_fact": {
                        "validated": "{{ web_mcp_endpoint | web_mcp_endpoint_contract }}"}},
                    {"ansible.builtin.assert": {"that":
                        ["(validated | web_mcp_playlist_operator).selected"]}},
                ],
            }]))
            variables = base / "vars.json"
            env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                       ANSIBLE_FILTER_PLUGINS=str(ROOT / "filter_plugins"),
                       ANSIBLE_LOCAL_TEMP=str(base / "local-tmp"),
                       ANSIBLE_REMOTE_TEMP=str(base / "remote-tmp"))
            for version in (1, True, False, 1.0, "1", 2):
                with self.subTest(version=version):
                    value["config"]["playlist_operator"]["wire_version"] = version
                    variables.write_text(json.dumps({"web_mcp_endpoint": value}))
                    result = subprocess.run(
                        ["ansible-playbook", "-i", "localhost,", str(play),
                         "--check", "--extra-vars", "@" + str(variables)],
                        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    if type(version) is int and version == 1:
                        self.assertEqual(result.returncode, 0, result.stdout)
                    else:
                        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_playlist_operator_ceiling_fixed_cohort_and_plan(self):
        self.assertEqual(contract.OPERATOR_RECIPIENTS, OPERATOR_COHORT)
        ids = sorted(OPERATOR_COHORT)
        with operator_pins():
            for field, item in (
                    ("wire_version", True), ("wire_version", 1.0), ("wire_version", "1"), ("wire_version", 2),
                    ("subject", ""), ("subject", "a b"), ("subject", "x" * 129), ("subject", "é"),
                    ("subject", ["writer"]), ("namespace", "other"), ("library", 3), ("library", "1"),
                    ("library", "03"), ("catalog_user", "c" * 32), ("recipients", []),
                    ("recipients", [ids[0], ids[0]]), ("recipients", ["%032x" % i for i in range(17)]),
                    ("recipients", ["a" * 32]), ("recipients", [ids[0].upper()]), ("recipients", [1]),
                    ("recipients", ids[0])):
                value = operator_fixture()
                value["config"]["playlist_operator"][field] = item
                with self.subTest(field=field, item=item):
                    self.rejected(value)

            def foreign_library(v):
                v["config"]["policy"]["Libraries"] = ["1"]
                v["config"]["library_guids"] = {"1": "d" * 32}

            def foreign_server(v):
                v["config"]["server_id"] = v["config"]["policy"]["Server"] = "a" * 32

            for name, mutate in (
                    ("missing field", lambda v: v["config"]["playlist_operator"].pop("subject")),
                    ("socket selector", lambda v: v["config"]["playlist_operator"].update(socket="/tmp/s")),
                    ("recipient path", lambda v: v["config"]["playlist_operator"].update(configs=["/x"])),
                    ("legacy writer", lambda v: v["config"].update(recommendations={})),
                    ("uid", lambda v: v.update(uid=941)), ("gid", lambda v: v.update(gid=941)),
                    ("server", foreign_server), ("library ceiling", foreign_library),
                    ("catalog ceiling", lambda v: v["config"]["policy"].update(CatalogUsers=["c" * 32])),
                    ("issuer bound", lambda v: v["config"].update(
                        issuer="https://auth.example.invalid/" + "a" * 600 + "/"))):
                value = operator_fixture()
                mutate(value)
                with self.subTest(name=name):
                    self.rejected(value)
            value = operator_fixture([ids[3], ids[0]])
            plan = contract.playlist_operator_plan(value)
        manifest = "".join(hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest() + "  " + name + "\n"
                           for name in sorted(contract.OPERATOR_MODULES))
        release = hashlib.sha256(manifest.encode()).hexdigest()
        self.assertEqual(plan["release"], release)
        self.assertEqual(plan["release_directory"], "/opt/holonet-playlists/releases/" + release)
        self.assertEqual(plan["pins"], contract.OPERATOR_MODULES)
        self.assertEqual([(m["name"], Path(m["source"])) for m in plan["modules"]],
                         [(name, ROOT / "scripts" / name) for name in sorted(contract.OPERATOR_MODULES)])
        selected = sorted([ids[3], ids[0]])
        self.assertEqual(plan["policy"], {
            "version": 1, "enabled": True, "peer": {"uid": 940, "gid": 940},
            "caller": {"issuer": value["config"]["issuer"], "subject": "writer-subject",
                       "namespace": "managed-recommendations", "server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
                       "library": "3", "catalog_user": "54b7d36cef804d3c8478474553ed3a82"},
            "recipients": {r: OPERATOR_COHORT[r] for r in selected}})
        self.assertEqual(plan["configs"], [OPERATOR_COHORT[r] for r in selected])
        self.assertEqual(plan["journals"], [str(Path(c).parent / "journal") for c in plan["configs"]])
        # No credential, ROOT/source or CLI path is derived or installed.
        self.assertNotIn("credential", json.dumps(plan))
        self.assertNotIn("/source", json.dumps(plan))
        # A module whose bytes differ from its reviewed pin is never planned.
        with operator_pins(), mock.patch.dict(contract.OPERATOR_MODULES, {"emby_operator_socket.py": "0" * 64}):
            with self.assertRaises(AnsibleFilterError):
                contract.playlist_operator_plan(value)

    def test_playlist_operator_units_render_the_reviewed_worker_boundary(self):
        env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
        templates = ROOT / "roles/web_mcp_endpoint/templates"

        def render(name, **variables):
            return env.from_string((templates / name).read_text()).render(**variables)
        with operator_pins():
            value = operator_fixture()
            plan = contract.playlist_operator_plan(value)
        service = render("holonet-playlists-worker@.service.j2", web_mcp_operator=plan)
        lines = service.splitlines()
        self.assertTrue(service.startswith(guard.OPERATOR_HEADER))
        self.assertEqual([line for line in lines if line.startswith("ExecStart=")],
                         ["ExecStart=/usr/bin/python3 -E -s -B " + plan["release_directory"]
                          + "/emby_operator_socket.py"])
        for setting in ("User=root", "NoNewPrivileges=yes", "CapabilityBoundingSet=", "AmbientCapabilities=",
                        "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "RuntimeMaxSec=150",
                        "TasksMax=8", "MemoryMax=128M", "LimitNOFILE=64",
                        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6"):
            self.assertIn(setting, lines)
        self.assertEqual(len(plan["journals"]), 7)
        self.assertEqual([line for line in lines if line.startswith("ReadWritePaths=")],
                         ["ReadWritePaths=" + " ".join(plan["journals"])])
        for word in (" -I ", "ReadOnlyPaths=", "RuntimeDirectory", "Listen", "credential.json", "/source"):
            self.assertNotIn(word, service)
        socket_unit = render("holonet-playlists-worker.socket.j2")
        socket_lines = socket_unit.splitlines()
        self.assertTrue(socket_unit.startswith(guard.OPERATOR_HEADER))
        self.assertEqual([line for line in socket_lines if line.startswith("Listen")],
                         ["ListenStream=/run/holonet-playlists/operator.sock"])
        for setting in ("Accept=yes", "SocketUser=root", "SocketGroup=holonet", "SocketMode=0660",
                        "DirectoryMode=0750", "MaxConnections=4", "RemoveOnStop=yes",
                        "AssertPathIsDirectory=/run/holonet-playlists"):
            self.assertIn(setting, socket_lines)
        self.assertNotIn("worker.sock", socket_unit)
        tmpfiles = render("holonet-playlists.tmpfiles.j2")
        self.assertTrue(tmpfiles.startswith(guard.OPERATOR_HEADER))
        self.assertEqual([line for line in tmpfiles.splitlines() if not line.startswith("#")],
                         ["d /run/holonet-playlists 0750 root holonet -"])
        # Accept=yes pairs NAME.socket with NAME@.service.
        self.assertEqual(guard.OPERATOR_UNITS[0].name.removesuffix(".socket") + "@.service",
                         guard.OPERATOR_UNITS[1].name)
        # The existing holonet.service is byte-identical with management configured.
        catalog = copy.deepcopy(value)
        catalog["config"].pop("playlist_operator")
        self.assertEqual(render("endpoint.service.j2", web_mcp=value), render("endpoint.service.j2", web_mcp=catalog))
        self.assertNotIn("ReadWritePaths", render("endpoint.service.j2", web_mcp=value))

    def operator_install(self, layout, plan):
        """Hand-built installed state matching what the role tasks produce."""
        package = directory(layout.base / "opt/holonet-playlists")
        private_file(package / ".homelab-infra-owned", guard.OPERATOR_OWNER, 0o644)
        release = directory(directory(package / "releases") / plan["release"])
        for name in plan["pins"]:
            (release / name).write_bytes((ROOT / "scripts" / name).read_bytes())
            (release / name).chmod(0o444)
        release.chmod(0o555)
        policy = directory(layout.base / "etc/holonet-playlists", 0o700)
        private_file(policy / "policy.json", json.dumps(plan["policy"]), 0o600)
        for unit in layout.units:
            private_file(unit, guard.OPERATOR_HEADER + "synthetic\n", 0o644)
        runtime = directory(layout.base / "run/holonet-playlists", 0o750)
        os.chown(runtime, -1, os.getegid())
        return release, policy, runtime

    def test_playlist_operator_guard_refuses_unsafe_state_without_opening_protected_files(self):
        with operator_pins():
            plan = contract.playlist_operator_plan(operator_fixture())
        gid = os.getegid()
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as scratch:
            layout = operator_layout(Path(scratch))
            base = layout.base

            def selected(**override):
                args = dict(release=plan["release"], modules=plan["pins"], journals=layout.journals,
                            configs=layout.configs)
                args.update(override)
                with layout.patch():
                    return guard.playlist_operator(gid, True, **args)

            def refused(**override):
                with self.assertRaises(ValueError):
                    selected(**override)
            self.assertEqual(selected(), {"installed": False, "runtime": False})
            with layout.patch():
                self.assertEqual(guard.playlist_operator(gid, False), {"installed": False, "runtime": False})
            # No adoption: any fixed name without the ownership marker refuses
            # management, while a catalog-only run neither judges nor touches it.
            for path in (base / "etc/holonet-playlists", base / "run/holonet-playlists", base / "opt/holonet-playlists"):
                directory(path, 0o700)
                with self.subTest(unowned=path.name):
                    refused()
                    with layout.patch():
                        self.assertEqual(guard.playlist_operator(gid, False),
                                         {"installed": False, "runtime": False})
                path.rmdir()
            release, policy, runtime = self.operator_install(layout, plan)
            self.assertEqual(selected(), {"installed": True, "runtime": True})
            # Once owned, an unselected run reports the socket for stopping and
            # still refuses unsafe owned state rather than acting on it.
            with layout.patch():
                self.assertEqual(guard.playlist_operator(gid, False), {"installed": True, "runtime": True})
                runtime.chmod(0o755)
                with self.assertRaises(ValueError):
                    guard.playlist_operator(gid, False)
                runtime.chmod(0o750)
            # Selected recipients must be the fixed, already provisioned journal/config pairs.
            igor, viewer = layout.journals
            for journals, configs in (([igor], layout.configs), ([igor, igor], layout.configs),
                                      ([str(base / "journal")], [str(base / "config.json")]),
                                      ([igor, viewer], list(reversed(layout.configs))), ([], [])):
                with self.subTest(journals=journals, configs=configs):
                    refused(journals=journals, configs=configs)
            refused(release="../" + plan["release"][3:])
            refused(modules=dict(plan["pins"], extra="0" * 64))

            def drift(path, change, restore):
                change(path)
                with self.subTest(drift=str(path.relative_to(base))):
                    refused()
                restore(path)
                selected()
            journal, config = Path(igor), Path(layout.configs[0])

            def mode(value):
                return lambda path: path.chmod(value)
            drift(journal, mode(0o755), mode(0o700))
            drift(config, mode(0o644), mode(0o600))
            drift(config.parent, mode(0o750), mode(0o700))
            drift(layout.state, mode(0o775), mode(0o755))
            drift(release, mode(0o755), mode(0o555))
            drift(release / "emby_operator_entry.py", mode(0o644), mode(0o444))
            drift(policy, mode(0o750), mode(0o700))
            drift(policy / "policy.json", mode(0o644), mode(0o600))
            drift(runtime, mode(0o755), mode(0o750))
            drift(layout.units[1], lambda p: p.write_text("# foreign unit\n"),
                  lambda p: p.write_text(guard.OPERATOR_HEADER))
            node = runtime / "operator.sock"
            drift(node, lambda p: private_file(p, "", 0o660), lambda p: p.unlink())
            missing = release / "emby_operator_entry.py"
            held = base / "held-module"
            drift(release, lambda p: (p.chmod(0o755), missing.rename(held), p.chmod(0o555)),
                  lambda p: (p.chmod(0o755), held.rename(missing), p.chmod(0o555)))
            extra = release / "sitecustomize.py"
            drift(release, lambda p: (p.chmod(0o755), private_file(extra, "", 0o444), p.chmod(0o555)),
                  lambda p: (p.chmod(0o755), extra.unlink(), p.chmod(0o555)))
            module = release / "emby_operator_recommendations.py"
            original = module.read_bytes()

            def rewrite(content):
                def change(path):
                    path.chmod(0o644)
                    path.write_bytes(content)
                    path.chmod(0o444)  # Same mode: only the bytes differ from the pin.
                return change
            drift(module, rewrite(original + b"#"), rewrite(original))
            saved = base / "saved-journal"
            drift(journal, lambda p: (p.rename(saved), p.symlink_to(saved, target_is_directory=True)),
                  lambda p: (p.unlink(), saved.rename(p)))
            drift(base / "bin/python3", lambda p: (p.unlink(), p.symlink_to("../python3.11")),
                  lambda p: (p.unlink(), p.symlink_to("python3.11")))
            # Refusal never repairs: the unsafe journal mode remains for inspection.
            journal.chmod(0o755)
            refused()
            self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o755)
            journal.chmod(0o700)
            # A passing preflight never opens, lists or stats a credential, never
            # opens or lists a recipient config, and touches journals by lstat only.
            seen = {"open": [], "list": [], "stat": []}
            real = {"io.open": io.open, "os.listdir": os.listdir, "os.scandir": os.scandir,
                    "os.stat": os.stat, "os.lstat": os.lstat}

            def recorder(kind, name):
                def call(path, *args, **kwargs):
                    if isinstance(path, (str, os.PathLike)):
                        seen[kind].append(Path(os.fspath(path)))
                    return real[name](path, *args, **kwargs)
                return call
            with mock.patch("io.open", recorder("open", "io.open")), \
                    mock.patch("os.listdir", recorder("list", "os.listdir")), \
                    mock.patch("os.scandir", recorder("list", "os.scandir")), \
                    mock.patch("os.stat", recorder("stat", "os.stat")), \
                    mock.patch("os.lstat", recorder("stat", "os.lstat")):
                self.assertEqual(selected(), {"installed": True, "runtime": True})
            self.assertTrue(seen["open"] and seen["stat"])
            journals = [Path(path) for path in layout.journals]
            for kind, paths in seen.items():
                for path in paths:
                    self.assertNotEqual(path, layout.credential)
                    for journal_path in journals:
                        inside = path.is_relative_to(journal_path)
                        self.assertFalse(inside and (kind != "stat" or path != journal_path), (kind, path))
            self.assertFalse(set(seen["open"] + seen["list"]) & {Path(c) for c in layout.configs})
            for path in [layout.credential] + [Path(j) / "journal.json" for j in layout.journals]:
                self.assertEqual(path.read_text(), "synthetic" if path == layout.credential
                                 else "synthetic retained journal")

    def test_playlist_operator_tasks_guard_first_check_apply_repeat_and_preserve(self):
        source = (ROOT / "roles/web_mcp_endpoint/tasks/main.yml").read_text()
        original = yaml.safe_load(source)[0]["block"]
        names = [task["name"] for task in original]
        first_write = names.index("Create the dedicated system group")
        for name in ("Refuse an externally supplied playlist operator plan",
                     "Derive the fixed playlist operator worker plan from the validated contract",
                     "Guard playlist operator root paths and selected journals without opening them",
                     "Refuse staging the playlist operator over a running or enabled socket"):
            self.assertLess(names.index(name), first_write)
        by_name = {task["name"]: task for task in original}
        stage = by_name["Stage the root playlist operator worker for configured management"]
        self.assertEqual(stage["when"], "web_mcp_operator.selected")
        for name in ("Create the declared socket parent before the first activation",
                     "Activate the playlist operator socket only with explicit activation"):
            self.assertIn("web_mcp_operator.selected", by_name[name]["when"])
            self.assertIn("web_mcp_activate | bool", by_name[name]["when"])
        self.assertEqual(by_name["Stop and disable an installed socket when management is not configured"]
                         ["ansible.builtin.systemd_service"],
                         {"name": "holonet-playlists-worker.socket", "enabled": False, "state": "stopped"})
        self.assertEqual({task["ansible.builtin.template"]["dest"] for task in stage["block"]
                          if "ansible.builtin.template" in task}, {str(path) for path in guard.OPERATOR_UNITS})
        # The role never names a journal, recipient config, credential or ROOT/source path.
        for word in ("/var/lib/holonet-operator", "credential.json", "/source", "journal.json"):
            self.assertNotIn(word, source)
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as scratch:
            base = Path(scratch)
            layout = operator_layout(base)
            with operator_pins():
                plan = contract.playlist_operator_plan(operator_fixture())
            plan["release_directory"] = str(base) + plan["release_directory"]
            text = yaml.safe_dump(stage["block"])
            for fixed in ("/opt/holonet-playlists", "/etc/holonet-playlists", "/etc/systemd/system/",
                          "/etc/tmpfiles.d/"):
                text = text.replace(fixed, str(base) + fixed)
            tasks = yaml.safe_load(text)
            for task in tasks:
                for module in ("ansible.builtin.file", "ansible.builtin.copy", "ansible.builtin.template"):
                    args = task.get(module, {})
                    for field in ("owner", "group"):
                        if args.get(field) == "root":
                            args[field] = "0"
                    if module == "ansible.builtin.template" and args:
                        args["src"] = str(ROOT / "roles/web_mcp_endpoint/templates" / args["src"])
            play = base / "operator.yml"
            play.write_text(yaml.safe_dump([{"hosts": "localhost", "connection": "local",
                "gather_facts": False, "vars": {"web_mcp_operator": plan}, "tasks": tasks}]))
            env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                       ANSIBLE_LOCAL_TEMP=str(base / "local-tmp"), ANSIBLE_REMOTE_TEMP=str(base / "remote-tmp"))

            def run(*flags):
                with layout.patch():  # The production preflight runs before every pass.
                    guard.playlist_operator(os.getegid(), True, plan["release"], plan["pins"],
                                            layout.journals, layout.configs)
                result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(play), *flags],
                                        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout)
                return result.stdout
            protected = [layout.credential] + [Path(j) / "journal.json" for j in layout.journals]
            saved = {path: (path.read_bytes(), path.stat().st_mode) for path in protected}
            run("--check")
            for path in (base / "opt/holonet-playlists", base / "etc/holonet-playlists", *layout.units):
                self.assertFalse(path.exists(), path)
            run()
            release = Path(plan["release_directory"])
            self.assertEqual(stat.S_IMODE(release.stat().st_mode), 0o555)
            self.assertEqual(sorted(os.listdir(release)), sorted(plan["pins"]))
            for name, digest in plan["pins"].items():
                self.assertEqual(stat.S_IMODE((release / name).stat().st_mode), 0o444)
                self.assertEqual(hashlib.sha256((release / name).read_bytes()).hexdigest(), digest)
            policy = base / "etc/holonet-playlists/policy.json"
            self.assertEqual(json.loads(policy.read_text()), plan["policy"])
            self.assertEqual(stat.S_IMODE(policy.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(policy.parent.stat().st_mode), 0o700)
            for unit in layout.units:
                self.assertTrue(unit.read_text().startswith(guard.OPERATOR_HEADER))
            self.assertIn("changed=0", run())
            self.assertIn("changed=0", run("--check"))
            self.assertEqual({path: (path.read_bytes(), path.stat().st_mode) for path in protected}, saved)
            # Drift refuses in the production preflight before any task could chmod it.
            module = release / "emby_operator_socket.py"
            module.chmod(0o644)
            with self.assertRaises(ValueError):
                run()
            self.assertEqual(stat.S_IMODE(module.stat().st_mode), 0o644)


if __name__ == "__main__":
    expected = ROOT / ".cache/web-mcp-tests/home"
    assert Path(os.environ["HOME"]) == expected
    assert Path(os.environ["TMPDIR"]) == expected / "tmp"
    unittest.main(verbosity=2)
