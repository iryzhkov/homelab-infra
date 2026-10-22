#!/usr/bin/env python3
"""Exercise the disabled objectstore source and its read-only storage preflight.

Everything runs in a scratch tree under the scratch HOME. Mount identities
and owners come from a recording probe; lstat and listdir are real, so
symlinks, missing directories and modes are the filesystem's own.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/garage-storage-preflight.py"
INVENTORY = yaml.safe_load((ROOT / "inventory/group_vars/swarm/objectstore.yml").read_text())
CANONICAL = INVENTORY["objectstore_storage"]
SPEC = importlib.util.spec_from_file_location("garage_storage_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)
ML_FOLDERS = ("jobs", "datasets", "registry", "big data", "runs", "dsh-workspace")


def scratch_home():
    home = Path(os.environ["HOME"]).resolve()
    assert home == Path(os.environ["OBJECTSTORE_TEST_HOME"]).resolve(), home
    assert home.is_relative_to(Path(os.environ["TMPDIR"]).resolve()), home
    assert ".scratch" in str(home), home
    return home


def snapshot(base):
    entries = []
    for directory, names, files in os.walk(base):
        for name in names + files:
            path = os.path.join(directory, name)
            info = os.lstat(path)
            entries.append((os.path.relpath(path, base), info.st_mode, info.st_mtime_ns))
    return sorted(entries)


class Probe(preflight.HostProbe):
    """Real lstat/listdir; declared mounts, owners and devices."""

    def __init__(self, mounts, uid, owners=None, devices=None):
        self.mounts, self.uid = mounts, uid
        self.owners, self.devices = owners or {}, devices or {}
        self.asked = []

    def lstat(self, path):
        real = os.lstat(path)
        return types.SimpleNamespace(st_mode=real.st_mode,
                                     st_uid=self.owners.get(path, self.uid),
                                     st_dev=self.devices.get(path, real.st_dev))

    def mount(self, path):
        self.asked.append(path)
        target = max((t for t in self.mounts if path == t or path.startswith(t.rstrip("/") + "/")),
                     key=len)
        return dict(self.mounts[target], target=target)


class Exploding(preflight.HostProbe):
    def lstat(self, path):
        raise AssertionError("inspected " + path)

    listdir = mount = lstat


class Fixture:
    """An ML-style shared export and a local metadata disk, in scratch."""

    def __init__(self, base, dedicated=False):
        self.base = base
        self.nas = base / "nas/ml"
        self.blobs = self.nas / "garage-blobs"
        self.local = base / "docker-data"
        self.objectstore = self.local / "swarm/objectstore"
        for name in ("lost+found",) if dedicated else ML_FOLDERS:
            (self.nas / name).mkdir(parents=True)
        self.blobs.mkdir(mode=0o700, parents=True)
        (self.objectstore / "meta").mkdir(mode=0o700, parents=True)
        (self.objectstore / "config").mkdir(mode=0o755)
        for path, mode in ((self.blobs, 0o700), (self.objectstore, 0o700),
                           (self.objectstore / "meta", 0o700),
                           (self.objectstore / "config", 0o755)):
            path.chmod(mode)
        self.mounts = {
            "/": {"source": "/dev/root", "fstype": "ext4", "fsroot": "/", "options": "rw,relatime"},
            str(self.nas): {"source": "192.168.40.40:/var/nfs/shared/machine_learning",
                            "fstype": "nfs", "fsroot": "/",
                            "options": "rw,relatime,vers=3,soft,proto=tcp,sec=sys"},
            str(self.local): {"source": "/dev/sdb1", "fstype": "ext4", "fsroot": "/",
                              "options": "rw,relatime"},
        }

    def config(self, cap="nas_directory_hard_quota"):
        config = copy.deepcopy(CANONICAL)
        config.update(enabled=True, host="storage-host", uid=1000, gid=988)
        scope = str(self.blobs) if cap in preflight.DIRECTORY_CAPS else str(self.nas)
        config["data"] = {
            "path": str(self.blobs),
            "mount": {"target": str(self.nas), "fstype": "nfs", "fsroot": "/",
                      "source": "192.168.40.40:/var/nfs/shared/machine_learning",
                      "options": ["rw", "soft"]},
            "physical_cap": {"bytes": 2 ** 40, "inodes": 10 ** 7, "mechanism": cap,
                             "scope": scope, "evidence": "evidence:data-cap"},
        }
        config["metadata"] = {
            "path": str(self.objectstore),
            "mount": {"target": str(self.local), "source": "/dev/sdb1", "fstype": "ext4",
                      "fsroot": "/", "options": ["rw"]},
            "physical_cap": {"bytes": 2 ** 33, "inodes": 10 ** 6,
                             "mechanism": "filesystem_project_hard_quota",
                             "scope": str(self.objectstore), "evidence": "evidence:meta-cap"},
        }
        for gate in preflight.DURABILITY_GATES:
            config["qualifications"][gate] = "evidence:" + gate
        return qualify(config)

    def probe(self, **kwargs):
        return Probe(copy.deepcopy(self.mounts), 1000, **kwargs)


def qualify(config):
    config["qualifications"]["configuration"] = preflight.digest(config)
    return config


class Scratch(unittest.TestCase):
    def setUp(self):
        self.home = scratch_home()
        self.temp = tempfile.TemporaryDirectory(dir=self.home)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()


class DefaultTest(Scratch):
    def test_canonical_source_is_disabled_and_inspects_nothing(self):
        self.assertIs(CANONICAL["enabled"], False)
        for key in ("host", "uid", "gid"):
            self.assertIsNone(CANONICAL[key])
        for label in ("data", "metadata"):
            self.assertIsNone(CANONICAL[label]["path"])
            self.assertEqual(set(CANONICAL[label]["physical_cap"].values()), {None})
        self.assertEqual(set(CANONICAL["qualifications"].values()), {None})
        self.assertIs(CANONICAL["data_fsync"], True)
        self.assertIs(CANONICAL["metadata_fsync"], True)
        self.assertEqual(preflight.validate(CANONICAL, Exploding()), {"status": "disabled"})
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--config", "-"],
                                input=json.dumps(CANONICAL), capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "disabled"})

    def test_unknown_live_facts_prevent_activation(self):
        for enabled in (True, "false", None):
            with self.subTest(enabled=enabled):
                config = dict(copy.deepcopy(CANONICAL), enabled=enabled)
                with self.assertRaises(preflight.Refusal):
                    preflight.validate(config, Exploding())
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--config", "-"],
                                input=json.dumps(dict(CANONICAL, enabled=True)),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn("storage preflight refused: explicit storage host", result.stderr)

    def test_digest_cli_names_the_exact_configuration(self):
        config = Fixture(self.base).config()
        path = self.base / "storage.json"
        path.write_text(json.dumps(config))
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--config", str(path),
                                 "--digest"], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), config["qualifications"]["configuration"])
        flipped = dict(config, data_fsync=False)
        self.assertNotEqual(preflight.digest(flipped), preflight.digest(config))


class PreflightTest(Scratch):
    def test_isolated_namespace_on_shared_ml_export_passes(self):
        fixture = Fixture(self.base)
        probe, before = fixture.probe(), snapshot(self.base)
        result = preflight.validate(fixture.config(), probe)
        self.assertEqual(result["status"], "passed-startup-preflight")
        self.assertEqual(result["data_path"], str(fixture.blobs))
        self.assertEqual(result["metadata_path"], str(fixture.objectstore / "meta"))
        self.assertTrue(any("soft NFS" in item for item in result["limitations"]))
        self.assertTrue(any("no continuous mount-loss" in item for item in result["limitations"]))
        self.assertIn(str(fixture.blobs), probe.asked)
        self.assertIn(str(fixture.objectstore / "meta"), probe.asked)
        self.assertEqual(snapshot(self.base), before)

    def test_whole_mount_cap_passes_only_on_a_dedicated_mount(self):
        fixture = Fixture(self.base, dedicated=True)
        config = fixture.config(cap="dedicated_volume")
        config["data"]["mount"]["options"] = ["rw", "hard"]
        fixture.mounts[str(fixture.nas)]["options"] = "rw,vers=4.2,hard,sec=sys"
        fixture.mounts[str(fixture.nas)]["fstype"] = "nfs4"
        config["data"]["mount"]["fstype"] = "nfs4"
        result = preflight.validate(qualify(config), fixture.probe())
        self.assertEqual(result["status"], "passed-startup-preflight")
        self.assertFalse(any("soft NFS" in item for item in result["limitations"]))

    def test_literal_interpolation_paths_refuse_before_host_inspection(self):
        for label in ("data", "metadata"):
            for index, token in enumerate(("${BLOB_DIR}", "$BLOB_DIR", "$$")):
                with self.subTest(label=label, token=token):
                    fixture = Fixture(self.base / (label + str(index)))
                    config = fixture.config()
                    path = (fixture.nas if label == "data" else fixture.local) / token
                    path.mkdir(mode=0o700)
                    if label == "metadata":
                        (path / "meta").mkdir(mode=0o700)
                        (path / "config").mkdir(mode=0o755)
                    config[label]["path"] = str(path)
                    config[label]["physical_cap"]["scope"] = str(path)
                    self.assertTrue(path.is_dir())  # The literal path really exists.
                    self.assert_refused(fixture, qualify(config), Exploding(),
                                        "must not contain Docker interpolation")
        fixture = Fixture(self.base / "host")
        config = dict(fixture.config(), host="$STORAGE_HOST")
        self.assert_refused(fixture, qualify(config), Exploding(),
                            "storage host must not contain Docker interpolation")

    def test_nested_metadata_cannot_claim_a_whole_mount_cap(self):
        fixture = Fixture(self.base / "shared-parent")
        # The mount root contains only swarm, but swarm holds another consumer.
        (fixture.local / "swarm/other-stack").mkdir()
        self.assertEqual(os.listdir(fixture.local), ["swarm"])
        config = fixture.config()
        for mechanism in preflight.WHOLE_MOUNT_CAPS:
            with self.subTest(mechanism=mechanism):
                config["metadata"]["physical_cap"].update(
                    mechanism=mechanism, scope=str(fixture.local))
                self.assert_refused(fixture, qualify(config), Exploding(),
                                    "metadata whole-mount cap requires a top-level")
        # A hard quota scoped to objectstore still permits nested metadata.
        config["metadata"]["physical_cap"].update(
            mechanism="filesystem_project_hard_quota", scope=str(fixture.objectstore))
        self.assertEqual(preflight.validate(qualify(config), fixture.probe())["status"],
                         "passed-startup-preflight")

        # A dedicated metadata mount whose store is an immediate child passes.
        fixture = Fixture(self.base / "dedicated-parent")
        config = fixture.config()
        root = str(fixture.local / "swarm")
        fixture.mounts[root] = fixture.mounts[str(fixture.local)]
        config["metadata"]["mount"]["target"] = root
        config["metadata"]["physical_cap"].update(
            mechanism="dedicated_filesystem", scope=root)
        self.assertEqual(preflight.validate(qualify(config), fixture.probe())["status"],
                         "passed-startup-preflight")

    def test_refusals_before_any_side_effect(self):
        def data(config):
            return config["data"]

        def cap(config):
            return config["data"]["physical_cap"]

        def link(fixture, config):
            target = fixture.nas / "elsewhere"
            target.mkdir(mode=0o700)
            fixture.blobs.rmdir()
            fixture.blobs.symlink_to(target)

        def alias(fixture, config):
            (fixture.local / "srv-swarm").symlink_to(fixture.local / "swarm")
            config["metadata"]["path"] = str(fixture.local / "srv-swarm/objectstore")
            config["metadata"]["physical_cap"]["scope"] = config["metadata"]["path"]

        def same_mount(fixture, config):
            path = str(fixture.nas / "garage-meta")
            config["metadata"]["mount"]["target"] = str(fixture.nas)
            config["metadata"].update(path=path)
            config["metadata"]["physical_cap"]["scope"] = path

        def unmounted(fixture, config):
            del fixture.mounts[str(fixture.nas)]

        def overmount(fixture, config):
            fixture.mounts[str(fixture.blobs)] = dict(fixture.mounts[str(fixture.nas)])

        def observed(key, value):
            def change(fixture, config):
                fixture.mounts[str(fixture.nas)][key] = value
            return change

        cases = [
            ("host", lambda f, c: c.update(host=None), "explicit storage host"),
            ("root uid", lambda f, c: c.update(uid=0), "non-root uid"),
            ("bool uid", lambda f, c: c.update(uid=True), "non-root uid"),
            ("fsync type", lambda f, c: c.update(data_fsync="yes"), "data_fsync must be a boolean"),
            ("gate", lambda f, c: c["qualifications"].update(mount_loss=None),
             "unqualified live gate: mount_loss"),
            ("missing mount option list", lambda f, c: data(c)["mount"].update(options=[]),
             "must list rw"),
            ("neither hard nor soft", lambda f, c: data(c)["mount"].update(options=["rw"]),
             "exactly one of hard or soft"),
            ("declared local data", lambda f, c: data(c)["mount"].update(fstype="ext4"),
             "explicit NFS"),
            ("nfs source", lambda f, c: data(c)["mount"].update(source="nas"), "server:/export"),
            ("metadata on nfs", lambda f, c: c["metadata"]["mount"].update(fstype="nfs"),
             "local filesystem"),
            ("subtree bind", lambda f, c: data(c)["mount"].update(fsroot="/sub"),
             "bind or subtree"),
            ("escape", lambda f, c: data(c).update(path=str(f.nas) + "/../escape"),
             "canonical path"),
            ("trailing slash", lambda f, c: data(c).update(path=str(f.blobs) + "/"),
             "canonical path"),
            ("shared mount root", lambda f, c: data(c).update(path=str(f.nas)),
             "strictly below"),
            ("outside mount", lambda f, c: data(c).update(path=str(f.local / "blobs")),
             "strictly below"),
            ("inside ML folder", lambda f, c: data(c).update(path=str(f.nas / "datasets/garage")),
             "top-level directory"),
            ("free space cap", lambda f, c: cap(c).update(mechanism="free_space"),
             "must bound physical allocation"),
            ("bucket quota", lambda f, c: cap(c).update(mechanism="garage_bucket_quota"),
             "must bound physical allocation"),
            ("cleanup", lambda f, c: cap(c).update(mechanism="cleanup_scan"),
             "must bound physical allocation"),
            ("cap scoped to shared root", lambda f, c: cap(c).update(scope=str(f.nas)),
             "scoped to exactly"),
            ("no inode cap", lambda f, c: cap(c).update(inodes=None), "bytes and inodes"),
            ("no cap evidence", lambda f, c: cap(c).update(evidence=" "), "evidence is required"),
            ("whole-mount cap on shared ML root",
             lambda f, c: cap(c).update(mechanism="dedicated_filesystem", scope=str(f.nas)),
             "mount is shared (also holds big data, datasets, dsh-workspace"),
            ("same mount", same_mount, "separate mounts"),
            ("symlinked data path", link, "symlink at " + "{blobs}"),
            ("symlinked metadata ancestry", alias, "symlink at {local}/srv-swarm"),
            ("missing NFS mount", unmounted, "mount target of {nas} is '/'"),
            ("overmount below root", overmount, "mount target of {blobs}"),
            ("wrong export", observed("source", "192.168.40.40:/var/nfs/shared/media"),
             "mount source"),
            ("wrong type", observed("fstype", "ext4"), "mount fstype"),
            ("read only", observed("options", "ro,soft"), "lacks rw"),
            ("declared hard, mounted soft",
             lambda f, c: data(c)["mount"].update(options=["rw", "hard"]), "lacks hard"),
            ("missing meta", lambda f, c: (f.objectstore / "meta").rmdir(),
             "cannot inspect {objectstore}/meta"),
            ("group access", lambda f, c: f.blobs.chmod(0o770), "no group or other access"),
            ("writable config", lambda f, c: (f.objectstore / "config").chmod(0o777),
             "not be group or other writable"),
        ]
        for index, (name, change, message) in enumerate(cases):
            with self.subTest(name):
                fixture = Fixture(self.base / str(index))
                config = fixture.config()
                change(fixture, config)
                qualify(config)
                self.assert_refused(fixture, config, fixture.probe(), message)

        probes = [
            ("foreign owner", lambda f: {"owners": {str(f.blobs): 988}}, "must be owned by uid 1000"),
            ("meta owner", lambda f: {"owners": {str(f.objectstore / "meta"): 0}},
             "must be owned by uid 1000"),
            ("other device", lambda f: {"devices": {str(f.blobs): -1}}, "on another device"),
        ]
        for index, (name, kwargs, message) in enumerate(probes):
            with self.subTest(name):
                fixture = Fixture(self.base / ("probe" + str(index)))
                self.assert_refused(fixture, fixture.config(), fixture.probe(**kwargs(fixture)),
                                    message)

        with self.subTest("stale digest"):
            fixture = Fixture(self.base / "stale")
            config = fixture.config()
            config["data_fsync"] = False
            self.assert_refused(fixture, config, Exploding(), "exact configuration digest")

    def assert_refused(self, fixture, config, probe, message):
        message = message.format(**{key: str(value) for key, value in vars(fixture).items()
                                    if isinstance(value, Path)})
        before = snapshot(fixture.base)
        with self.assertRaises(preflight.Refusal) as caught:
            preflight.validate(config, probe)
        self.assertIn(message, str(caught.exception))
        self.assertEqual(snapshot(fixture.base), before)

    @unittest.skipUnless(shutil.which("findmnt"), "findmnt is not installed")
    def test_real_host_probe_refuses_scratch_as_nfs(self):
        fixture = Fixture(self.base)
        record = preflight.HostProbe().mount(str(fixture.blobs))
        self.assertTrue(str(fixture.blobs).startswith(record["target"].rstrip("/") + "/"))
        before = snapshot(self.base)
        with self.assertRaises(preflight.Refusal) as caught:
            preflight.validate(fixture.config())
        self.assertIn("mount target of " + str(fixture.nas), str(caught.exception))
        self.assertEqual(snapshot(self.base), before)


class SourceTest(Scratch):
    def test_garage_configuration_renders_configured_fsync(self):
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters["to_json"] = json.dumps
        template = env.from_string((ROOT / "stacks/objectstore/templates/garage.toml.j2").read_text())
        for data_fsync, metadata_fsync in ((True, True), (False, True), (True, False)):
            storage = dict(CANONICAL, data_fsync=data_fsync, metadata_fsync=metadata_fsync)
            rendered = tomllib.loads(template.render(objectstore_storage=storage))
            self.assertIs(rendered["data_fsync"], data_fsync)
            self.assertIs(rendered["metadata_fsync"], metadata_fsync)
            self.assertEqual(rendered["replication_factor"], 1)
            self.assertEqual(rendered["metadata_dir"], "/var/lib/garage/meta")
            self.assertEqual(rendered["data_dir"], "/var/lib/garage/data")
            self.assertEqual(rendered["rpc_bind_addr"], "127.0.0.1:3901")
            self.assertEqual(rendered["admin"]["api_bind_addr"], "127.0.0.1:3903")
            self.assertNotIn("rpc_secret", rendered)
            self.assertNotIn("admin_token", rendered["admin"])

    def test_stack_source_is_inert_and_lints(self):
        stack = ROOT / "stacks/objectstore"
        self.assertFalse((stack / "stack.yml").exists())
        self.assertFalse((stack / "swarm-prepare.yml").exists())
        for path in (ROOT / "inventory").rglob("*.yml"):
            document = yaml.safe_load(path.read_text()) or {}
            if not isinstance(document, dict):
                continue
            for key, entries in document.items():
                if key.startswith("swarm_stacks_"):
                    for entry in entries or []:
                        self.assertNotEqual(entry.get("source", entry.get("name")), "objectstore",
                                            path)
        example = yaml.safe_load((stack / "stack.yml.example").read_text())
        garage = example["services"]["garage"]
        self.assertEqual(garage["image"], "dxflrs/garage:v2.4.1@sha256:"
                         "9c96caa2612d3411acc5b0e6701fb238dbfba33e533a6d7d3d811a4b12d0d020")
        self.assertNotIn("ports", garage)
        self.assertTrue(example["secrets"]["garage_rpc_secret"]["external"])
        result = subprocess.run([sys.executable, str(ROOT / "scripts/stack-lint.py"),
                                 str(stack / "stack.yml.example")],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        prepare = yaml.safe_load((stack / "swarm-prepare.yml.example").read_text())
        self.assertEqual([task["name"] for task in prepare],
                         ["Validate objectstore storage again before rendering",
                          "Render Garage configuration on the storage host"])
        self.assertFalse(any("ansible.builtin.file" in task for task in prepare))

    def test_actual_stack_wrapper_refuses_before_deployment_effects(self):
        tasks = yaml.safe_load((ROOT / "roles/swarm/tasks/stack.yml").read_text())
        stop = next(i for i, task in enumerate(tasks) if task.get("name") == "Install the stack file")
        passing = self.base / "passing-preflight.py"
        passing.write_text("import json, sys\njson.load(sys.stdin)\n"
                           "print(json.dumps({'status': 'passed-startup-preflight'}))\n")
        fixture = Fixture(self.base / "tree")
        qualified = dict(fixture.config(), host="localhost")
        qualify(qualified)
        env = {
            "OBJECTSTORE_HOST": qualified["host"],
            "OBJECTSTORE_UID": str(qualified["uid"]),
            "OBJECTSTORE_GID": str(qualified["gid"]),
            "OBJECTSTORE_DATA_PATH": qualified["data"]["path"],
            "OBJECTSTORE_METADATA_PATH": qualified["metadata"]["path"],
        }
        matched = {"name": "objectstore", "pin": "localhost", "env_extra": env}
        cases = [
            ("canonical default", {"name": "objectstore", "pin": "localhost"}, CANONICAL,
             None, "objectstore storage is disabled"),
            ("renamed stack", {"name": "blobs", "source": "objectstore", "pin": "localhost"},
             CANONICAL, None, "objectstore storage is disabled"),
            ("unknown facts", {"name": "objectstore", "pin": "localhost"},
             dict(CANONICAL, enabled=True), None, "objectstore storage is disabled"),
            ("no pin", {"name": "objectstore"}, qualified, None,
             "objectstore storage is disabled"),
            ("real probe", matched, qualified, None, "storage preflight refused"),
            # A passing preflight reaches lint, which still refuses the inert
            # source: there is no stacks/objectstore/stack.yml to deploy.
            ("inert after pass", matched, qualified, passing, "Lint objectstore"),
        ]
        # Run the actual wrapper with a qualified config and otherwise-passing
        # probe. An independent deployment override must fail before that probe,
        # lint, or any deployment write. Include both paths and every identity.
        for key, wrong in (
                ("OBJECTSTORE_DATA_PATH", str(fixture.nas)),
                ("OBJECTSTORE_METADATA_PATH", str(fixture.local / "swarm/other-stack")),
                ("OBJECTSTORE_UID", "988"),
                ("OBJECTSTORE_GID", "1000"),
                ("OBJECTSTORE_HOST", "other-host")):
            cases.append((key, dict(matched, env_extra={**env, key: wrong}),
                          qualified, passing, "objectstore env_extra " + key + " must match"))
        for key in env:
            cases.append(("missing " + key,
                          dict(matched, env_extra={k: v for k, v in env.items() if k != key}),
                          qualified, passing, "objectstore env_extra " + key + " must match"))
        for index, (name, stack, storage, script, message) in enumerate(cases):
            with self.subTest(name):
                raw = json.dumps(tasks[:stop + 1])
                if script:
                    raw = raw.replace("{{ playbook_dir }}/../scripts/garage-storage-preflight.py",
                                      str(script))
                prefix = json.loads(raw.replace("{{ playbook_dir }}/..", str(ROOT)))
                effects = self.base / ("effects-" + str(index))
                play = self.base / "guard.yml"
                play.write_text(yaml.safe_dump([{
                    "hosts": "localhost", "gather_facts": False,
                    "vars": {"objectstore_storage": storage, "swarm_stack": stack,
                             "swarm_deploy_root": str(effects),
                             "ansible_python_interpreter": sys.executable},
                    "tasks": prefix,
                }]))
                before = snapshot(fixture.base)
                result = subprocess.run(
                    ["ansible-playbook", "-i", "localhost,", "-c", "local", str(play)],
                    env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(self.base / "ansible"),
                         "ANSIBLE_REMOTE_TEMP": str(self.base / "remote")},
                    capture_output=True, text=True, timeout=120)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 2, output)
                self.assertIn(message, output)
                if message.startswith("objectstore env_extra"):
                    self.assertNotIn("TASK [Run the read-only storage preflight", output)
                    self.assertNotIn("TASK [Lint", output)
                self.assertFalse(effects.exists(), output)
                self.assertEqual(snapshot(fixture.base), before)


if __name__ == "__main__":
    unittest.main()
