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
import stat
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

    def __init__(self, mounts, uid, owners=None, devices=None, modes=None, groups=None):
        self.mounts, self.uid = mounts, uid
        self.owners, self.devices = owners or {}, devices or {}
        self.modes, self.groups = modes or {}, groups or {}
        self.asked, self.inspected = [], []

    def lstat(self, path):
        self.inspected.append(path)
        real = os.lstat(path)
        mode = self.modes.get(path)
        return types.SimpleNamespace(st_mode=real.st_mode if mode is None else stat.S_IFDIR | mode,
                                     st_uid=self.owners.get(path, self.uid),
                                     st_gid=self.groups.get(path, real.st_gid),
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

    def budget_config(self, stores=("data",)):
        config = self.config()  # Keep the legacy hard-cap fixtures independent.
        for label in stores:
            config[label].update(
                acceptance_mode="operational_budget", physical_cap=None,
                operational_budget={"bytes": 2_000_000_000_000 if label == "data" else 17_179_869_184,
                                    "risk_acceptance": "evidence:unquotaed-" + label + "-risk"})
        return qualify(config)

    def mapped_config(self, budget=True):
        config = self.budget_config() if budget else self.config()
        config["gid"] = 1000
        config["data"]["identity_mapping"] = {
            "owner_uid": 977, "owner_gid": 988, "evidence": "fixture:process-1000-1000-nfs-977-988"}
        return qualify(config)

    def probe(self, **kwargs):
        return Probe(copy.deepcopy(self.mounts), 1000, **kwargs)


def qualify(config):
    config["qualifications"]["configuration"] = preflight.digest(config)
    return config


# Synthetic immutable-shaped references only. No document or live pass is asserted.
FIXTURE_REFERENCE = "jocasta:" + "0" * 32 + "@1"


def operator_qualify(config, composite=False):
    config["operating_contract"] = "operator_budget_v1"
    current = preflight.digest(config)
    records = {"configuration": current}
    for gate in preflight.DURABILITY_GATES:
        kind = ("accepted_risk" if gate in ("full_volume", "mount_loss") else
                "composite" if composite and gate != "last_copy_policy" else "measured")
        record = {"type": kind, "references": [FIXTURE_REFERENCE],
                  "limitation": "Synthetic fixture only; no live evidence or operational authority."}
        if kind == "composite":
            record.update(measured_configuration="sha256:" + "a" * 64,
                          qualifying_configuration=current, difference_reference=FIXTURE_REFERENCE)
        else:
            record["configuration"] = current
        records[gate] = record
    config["qualifications"] = records
    return config


class Scratch(unittest.TestCase):
    def setUp(self):
        self.home = scratch_home()
        self.temp = tempfile.TemporaryDirectory(dir=self.home)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()


class OperatorContractTest(Scratch):
    def setUp(self):
        super().setUp()
        self.fixture = Fixture(self.base)
        self.config = operator_qualify(self.fixture.budget_config(("data", "metadata")), True)

    def refused(self, config, message):
        with self.assertRaisesRegex(preflight.Refusal, message):
            preflight.validate(config, Exploding())

    def test_opt_in_reports_each_class_and_local_validation_limits(self):
        before = snapshot(self.base)
        result = preflight.validate(self.config, self.fixture.probe())
        self.assertEqual(result["status"], "passed-startup-preflight")
        self.assertEqual(result["operating_contract"], "operator_budget_v1")
        self.assertEqual(result["qualifications"], self.config["qualifications"])
        self.assertIn("no evidence fetched or measured configuration bytes verified",
                      result["qualification_validation"])
        self.assertEqual(snapshot(self.base), before)
        for composite in (False, True):
            config = operator_qualify(copy.deepcopy(self.config), composite)
            self.assertEqual(preflight.validate(config, self.fixture.probe())["configuration"],
                             preflight.digest(config))

    def test_selector_is_explicit_and_requires_both_budget_modes(self):
        self.assertNotIn("operating_contract", CANONICAL)
        for value in (None, "", "operator_budget_v2", True, 1, [], {}):
            config = copy.deepcopy(self.config)
            config["operating_contract"] = value
            self.refused(config, "unknown operating_contract")
            config["enabled"] = False
            self.refused(config, "unknown operating_contract")
        for label in ("data", "metadata"):
            for value in (None, "physical_cap", "unknown", True, 1, [], {}):
                config = copy.deepcopy(self.config)
                config[label]["acceptance_mode"] = value
                self.refused(config, "requires explicit data and metadata")
            config = copy.deepcopy(self.config)
            del config[label]["acceptance_mode"]
            self.refused(config, "requires explicit data and metadata")
        config = copy.deepcopy(self.config)
        del config["operating_contract"]
        qualify(config)
        self.refused(config, "unqualified live gate")
        # Legacy string gates remain valid with explicit or omitted physical-cap mode.
        for config in (self.fixture.config(), self.fixture.budget_config(("data", "metadata"))):
            result = preflight.validate(config, self.fixture.probe())
            self.assertNotIn("operating_contract", result)
            self.assertNotIn("qualifications", result)

    def test_gate_matrix_and_required_envelope(self):
        for gate in preflight.DURABILITY_GATES:
            for value in (None, "", FIXTURE_REFERENCE, True, 1, [], {}):
                config = copy.deepcopy(self.config)
                config["qualifications"][gate] = value
                self.refused(config, "typed qualification|wrong qualification type")
            config = copy.deepcopy(self.config)
            del config["qualifications"][gate]
            self.refused(config, "exactly configuration and all five gates")
            for kind in ("measured", "composite", "accepted_risk", "unknown", None, [], {}):
                allowed = (("accepted_risk",) if gate in ("full_volume", "mount_loss")
                           else ("measured",) if gate == "last_copy_policy"
                           else ("measured", "composite"))
                if kind in allowed:
                    continue
                config = copy.deepcopy(self.config)
                config["qualifications"][gate]["type"] = kind
                self.refused(config, "wrong qualification type")
        for value in (None, [], "", True, 1):
            config = copy.deepcopy(self.config)
            config["qualifications"] = value
            self.refused(config, "exactly configuration and all five gates")
        for key in ("configuration", "unexpected"):
            config = copy.deepcopy(self.config)
            if key == "configuration":
                del config["qualifications"][key]
            else:
                config["qualifications"][key] = None
            self.refused(config, "exactly configuration and all five gates")
        config = copy.deepcopy(self.config)
        config["qualifications"] = {key: None for key in config["qualifications"]}
        self.refused(config, "exact configuration digest")
        config["qualifications"]["configuration"] = preflight.digest(config)
        self.refused(config, "typed qualification")

    def test_exact_record_keys_and_binding_fields(self):
        for gate in preflight.DURABILITY_GATES:
            original = self.config["qualifications"][gate]
            for key in (*original, "unexpected"):
                config = copy.deepcopy(self.config)
                record = config["qualifications"][gate]
                if key in record:
                    del record[key]
                else:
                    record[key] = None
                self.refused(config, "fields|wrong qualification type")
            binding = "qualifying_configuration" if original["type"] == "composite" else "configuration"
            for value in (None, True, 1, [], {}, "sha256:" + "0" * 64, "SHA256:" + "a" * 64):
                config = copy.deepcopy(self.config)
                config["qualifications"][gate][binding] = value
                self.refused(config, "bind current qualifying configuration")
        for value in (None, True, 1, [], {}, "", "a" * 64, "sha256:" + "A" * 64,
                      "sha256:" + "a" * 63, "sha256:" + "a" * 64 + "\n"):
            config = copy.deepcopy(self.config)
            config["qualifications"]["consistent_restore"]["measured_configuration"] = value
            self.refused(config, "canonical measured configuration digest")

    def test_bounded_references_and_utf8_limitations(self):
        invalid = (None, "", " ", True, 1, [], {}, "https://example.invalid/proof",
                   "jocasta:" + "A" * 32 + "@1", "jocasta:" + "a" * 32,
                   "jocasta:" + "a" * 32 + "@0", "jocasta:" + "a" * 32 + "@01",
                   FIXTURE_REFERENCE + "\n", FIXTURE_REFERENCE + " ",
                   "jocasta:" + "a" * 32 + "@" + "1" * 56)
        for gate in preflight.DURABILITY_GATES:
            for value in (None, "", True, 1, {}, [], [FIXTURE_REFERENCE] * 9,
                          *([item] for item in invalid)):
                config = copy.deepcopy(self.config)
                config["qualifications"][gate]["references"] = value
                self.refused(config, "1..8 immutable Jocasta references")
            for value in (None, "", " \n\t", True, 1, [], {}, "x" * 4097, "é" * 2049, "\ud800"):
                config = copy.deepcopy(self.config)
                config["qualifications"][gate]["limitation"] = value
                self.refused(config, "nonblank limitation")
        for value in invalid:
            config = copy.deepcopy(self.config)
            config["qualifications"]["consistent_restore"]["difference_reference"] = value
            self.refused(config, "immutable difference reference")
        config = copy.deepcopy(self.config)
        # Maximum reference length and UTF-8 byte count are inclusive.
        reference = "jocasta:" + "a" * 32 + "@" + "1" * 54
        self.assertEqual(len(reference), 95)
        config["qualifications"]["consistent_restore"]["difference_reference"] = reference + "1"
        for gate in preflight.DURABILITY_GATES:
            config["qualifications"][gate]["references"] = [reference + "1"] * 8
            config["qualifications"][gate]["limitation"] = "é" * 2048
        preflight.validate(config, self.fixture.probe())

    def test_discontinuous_composites_preserve_actual_measured_binding(self):
        legacy = self.fixture.budget_config(("data", "metadata"))
        legacy["data"]["unknown_filecount_risk_acceptance"] = "fixture:old-nonempty-risk"
        old = preflight.digest(legacy)
        current = copy.deepcopy(legacy)
        current["data"]["unknown_filecount_risk_acceptance"] = "fixture:new-S3-risk"
        operator_qualify(current, True)
        new = preflight.digest(current)
        self.assertNotEqual(old, new)
        current["qualifications"]["fresh_client_roundtrip"]["measured_configuration"] = old
        preflight.validate(current, self.fixture.probe())
        # Startup checks syntax/bindings only; no receipt fetch or historical-byte comparison.
        current["qualifications"]["consistent_restore"]["measured_configuration"] = "sha256:" + "b" * 64
        preflight.validate(current, self.fixture.probe())
        for change in (
            lambda c: c["data"].update(unknown_filecount_risk_acceptance="fixture:drift"),
            lambda c: c["metadata"]["operational_budget"].update(bytes=12345),
            lambda c: c.update(data_fsync=False),
        ):
            config = copy.deepcopy(current)
            change(config)
            self.refused(config, "exact configuration digest")
        # Removing fields does not recreate the old nonempty risk reference.
        del current["operating_contract"]
        del current["data"]["unknown_filecount_risk_acceptance"]
        self.assertNotEqual(preflight.digest(current), old)

    def test_typed_contract_preserves_mount_custody_and_io_refusals(self):
        for change in ("source", "options", "custody", "io"):
            probe = self.fixture.probe()
            if change == "custody":
                probe.modes[str(self.fixture.blobs)] = 0o777
            elif change == "io":
                probe.mount = lambda path: (_ for _ in ()).throw(OSError("injected"))
            else:
                probe.mounts[str(self.fixture.nas)][change] = (
                    "wrong:/export" if change == "source" else "ro,soft")
            with self.subTest(change=change), self.assertRaises((preflight.Refusal, OSError)):
                preflight.validate(self.config, probe)


class DefaultTest(Scratch):
    def test_canonical_source_is_disabled_and_inspects_nothing(self):
        self.assertIs(CANONICAL["enabled"], False)
        for key in ("host", "uid", "gid"):
            self.assertIsNone(CANONICAL[key])
        for label in ("data", "metadata"):
            self.assertIsNone(CANONICAL[label]["path"])
        self.assertEqual(CANONICAL["data"]["acceptance_mode"], "operational_budget")
        self.assertEqual(CANONICAL["data"]["operational_budget"],
                         {"bytes": 2_000_000_000_000, "risk_acceptance": None})
        self.assertIsNone(CANONICAL["data"]["physical_cap"])
        self.assertEqual(CANONICAL["metadata"]["acceptance_mode"], "operational_budget")
        self.assertEqual(CANONICAL["metadata"]["operational_budget"],
                         {"bytes": 17_179_869_184, "risk_acceptance": None})
        self.assertIsNone(CANONICAL["metadata"]["physical_cap"])
        for label in ("data", "metadata"):
            self.assertEqual(CANONICAL[label]["mount"],
                             {"target": None, "source": None, "fstype": None,
                              "fsroot": None, "options": []})
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

    def test_legacy_digest_golden_vector_excludes_only_qualifications(self):
        # Fixed synthetic JSON bytes, independent of scratch paths and host facts.
        config = {"enabled": True, "host": "fixture-only", "data_fsync": True,
                  "metadata_fsync": True,
                  "data": {"unknown_filecount_risk_acceptance": "fixture:legacy-nonempty"},
                  "qualifications": {"mount_loss": "legacy string"}}
        expected = "sha256:7c08e6c7a690acf3782c10a4b9dcbcc5c7babbb1ad6745bd2802adf58d92a8c9"
        self.assertEqual(preflight.digest(config), expected)
        config["qualifications"] = {"configuration": None}
        self.assertEqual(preflight.digest(config), expected)
        config["operating_contract"] = "operator_budget_v1"
        self.assertNotEqual(preflight.digest(config), expected)
        del config["operating_contract"]
        config["data"]["unknown_filecount_risk_acceptance"] = "fixture:new-reference"
        self.assertNotEqual(preflight.digest(config), expected)

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

    def test_metadata_parent_and_config_require_trusted_custodians(self):
        fixture = Fixture(self.base)
        for stores in ((), ("metadata",), ("data", "metadata")):
            for directory in (fixture.objectstore, fixture.objectstore / "config"):
                path = str(directory)
                for mode in (0o755, 0o1777):
                    with self.subTest(path=path, owner=2001, mode=oct(mode)):
                        self.assert_refused(
                            fixture, fixture.budget_config(stores),
                            fixture.probe(owners={path: 2001}, modes={path: mode}),
                            "must be owned by root or uid 1000")
                for owner in (0, 1000):
                    for mode in (0o775, 0o757, 0o1777):
                        with self.subTest(path=path, owner=owner, mode=oct(mode)):
                            self.assert_refused(
                                fixture, fixture.budget_config(stores),
                                fixture.probe(owners={path: owner}, modes={path: mode}),
                                "must not be group or other writable")

    def test_every_metadata_ancestor_requires_custody(self):
        fixture = Fixture(self.base)
        for stores in ((), ("metadata",), ("data", "metadata")):
            # Include /, ancestors above the mount, the mount and the Swarm root.
            for directory in fixture.objectstore.parents:
                path = str(directory)
                for owner, mode, message in (
                        (2001, 0o755, "must be owned by root or uid 1000"),
                        (2001, 0o1777, "must be owned by root or uid 1000"),
                        (0, 0o775, "must not be group or other writable"),
                        (0, 0o757, "must not be group or other writable"),
                        (1000, 0o1777, "must not be group or other writable")):
                    with self.subTest(path=path, owner=owner, mode=oct(mode)):
                        self.assert_refused(
                            fixture, fixture.budget_config(stores),
                            fixture.probe(owners={path: owner}, modes={path: mode}),
                            "metadata: " + path + " " + message)

    def test_root_and_selected_uid_custody_preserve_shared_nfs_traversal(self):
        fixture = Fixture(self.base)
        for stores in ((), ("metadata",), ("data", "metadata")):
            ancestors = [str(path) for path in fixture.objectstore.parents]
            custody = ancestors + [str(fixture.objectstore), str(fixture.objectstore / "config")]
            # The known ML root stays 988:988 mode 0770; local custody rules must
            # not turn this into a blanket refusal of the shared export.
            fixture.nas.chmod(0o770)
            for owner in (0, 1000):
                with self.subTest(owner=owner):
                    owners = dict.fromkeys(custody, owner)
                    owners[str(fixture.nas)] = 988
                    probe = fixture.probe(owners=owners, modes=dict.fromkeys(custody, 0o755))
                    before = snapshot(self.base)
                    result = preflight.validate(fixture.budget_config(stores), probe)
                    self.assertEqual(result["status"], "passed-startup-preflight")
                    self.assertTrue(set(custody) <= set(probe.inspected))
                    self.assertEqual(snapshot(self.base), before)
            # Root-owned sticky strict ancestors protect the next trusted-owned
            # component, including above the mount and at filesystem roots.
            for path in ancestors:
                with self.subTest(sticky_ancestor=path):
                    probe = fixture.probe(owners={path: 0}, modes={path: 0o1777})
                    before = snapshot(self.base)
                    self.assertEqual(preflight.validate(fixture.budget_config(stores), probe)["status"],
                                     "passed-startup-preflight")
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


class BudgetTest(Scratch):
    assert_refused = PreflightTest.assert_refused

    def test_unquotaed_data_with_bounded_metadata_passes_without_writes(self):
        fixture = Fixture(self.base)
        for declared_null in (True, False):
            with self.subTest(declared_null=declared_null):
                config = fixture.budget_config()
                if not declared_null:
                    del config["data"]["physical_cap"]
                probe, before = fixture.probe(), snapshot(self.base)
                result = preflight.validate(qualify(config), probe)
                self.assertEqual(result["status"], "passed-startup-preflight")
                self.assertEqual(result["data_acceptance"],
                                 {"mode": "operational_budget",
                                  "aggregate_budget_bytes": 2_000_000_000_000})
                self.assertEqual(result["configuration"], preflight.digest(config))
                self.assertIn(str(fixture.blobs), probe.asked)
                self.assertIn(str(fixture.objectstore / "meta"), probe.asked)
                warning = next(item for item in result["limitations"]
                               if "operational data budget only" in item)
                for phrase in ("artifacts and results", "no physical byte or inode enforcement",
                               "no reserved space", "no multipart constraint", "NAS exhaustion"):
                    self.assertIn(phrase, warning)
                self.assertEqual(snapshot(self.base), before)

    def test_shared_metadata_budget_and_mixed_modes_pass_without_writes(self):
        fixture = Fixture(self.base)
        (fixture.local / "swarm/other-stack").mkdir()
        (fixture.local / "docker").mkdir()
        for stores in (("metadata",), ("data", "metadata")):
            for declared_null in (True, False):
                with self.subTest(stores=stores, declared_null=declared_null):
                    config = fixture.budget_config(stores)
                    if not declared_null:
                        del config["metadata"]["physical_cap"]
                    qualify(config)
                    original, before = copy.deepcopy(config), snapshot(self.base)
                    probe = fixture.probe()
                    result = preflight.validate(config, probe)
                    self.assertEqual(result["metadata_acceptance"],
                                     {"mode": "operational_budget",
                                      "aggregate_budget_bytes": 17_179_869_184})
                    self.assertEqual(result["data_acceptance"]["mode"],
                                     "operational_budget" if "data" in stores else "physical_cap")
                    warning = next(item for item in result["limitations"]
                                   if "operational metadata budget only" in item)
                    for phrase in ("no physical byte or inode enforcement", "no reserved space",
                                   "exhaust the shared Docker filesystem", "point-in-time",
                                   "not continuous admission or monitoring"):
                        self.assertIn(phrase, warning)
                    self.assertIn(str(fixture.objectstore / "config"), probe.asked)
                    self.assertEqual(config, original)
                    self.assertEqual(snapshot(self.base), before)

    def test_metadata_budget_preserves_mount_device_alias_and_private_meta_checks(self):
        fixture = Fixture(self.base)
        config = fixture.budget_config(("data", "metadata"))
        for mapping in (None, {}, fixture.mapped_config()["data"]["identity_mapping"]):
            changed = copy.deepcopy(config)
            changed["metadata"]["identity_mapping"] = mapping
            self.assert_refused(fixture, qualify(changed), Exploding(),
                                "allowed only on data, never metadata")
        declarations = [
            (lambda c: c["metadata"]["mount"].update(fstype="nfs"), "local filesystem"),
            (lambda c: c["metadata"]["mount"].update(fsroot="/sub"), "bind or subtree"),
            (lambda c: c["metadata"].update(path=str(fixture.local)), "strictly below"),
            (lambda c: c["metadata"].update(path=str(fixture.local / "${META}")),
             "Docker interpolation"),
            (lambda c: (c["metadata"]["mount"].update(target=str(fixture.nas)),
                        c["metadata"].update(path=str(fixture.nas / "garage-meta"))),
             "separate mounts"),
            (lambda c: c["metadata"].update(path=str(fixture.blobs / "meta"),
                                            mount={**c["metadata"]["mount"],
                                                   "target": str(fixture.nas)}),
             "separate mounts"),
        ]
        for change, message in declarations:
            changed = copy.deepcopy(config)
            change(changed)
            self.assert_refused(fixture, qualify(changed), Exploding(), message)
        for directory in (fixture.objectstore, fixture.objectstore / "meta",
                          fixture.objectstore / "config"):
            path = str(directory)
            self.assert_refused(fixture, config, fixture.probe(devices={path: -1}),
                                "on another device")
            probe = fixture.probe()
            probe.mounts[path] = dict(probe.mounts[str(fixture.local)])
            self.assert_refused(fixture, config, probe, "mount target")
        for mode, message in ((0o710, "no group or other access"),
                              (0o701, "no group or other access"),
                              (0o770, "not be group or other writable")):
            self.assert_refused(fixture, config,
                                fixture.probe(modes={str(fixture.objectstore / "meta"): mode}),
                                message)
        for key, value, message in (("source", "/dev/wrong", "mount source"),
                                    ("fstype", "xfs", "mount fstype"),
                                    ("fsroot", "/sub", "mount fsroot"),
                                    ("options", "ro", "lacks rw")):
            probe = fixture.probe()
            probe.mounts[str(fixture.local)][key] = value
            self.assert_refused(fixture, config, probe, message)
        probe = fixture.probe()
        del probe.mounts[str(fixture.local)]
        self.assert_refused(fixture, config, probe, "mount target")
        alias = fixture.local / "srv-swarm"
        alias.symlink_to(fixture.local / "swarm")
        changed = copy.deepcopy(config)
        changed["metadata"]["path"] = str(alias / "objectstore")
        self.assert_refused(fixture, qualify(changed), fixture.probe(), "symlink at")
        for child in ("meta", "config"):
            original = fixture.objectstore / child
            target = fixture.objectstore / (child + "-real")
            original.rename(target)
            original.symlink_to(target)
            self.assert_refused(fixture, config, fixture.probe(), "symlink at")
            original.unlink()
            target.rename(original)
        (fixture.objectstore / "meta").rmdir()
        self.assert_refused(fixture, config, fixture.probe(), "cannot inspect")

    def test_malformed_budget_declarations_refuse_without_probing(self):
        fixture = Fixture(self.base)
        for label in ("data", "metadata"):
            cases = []
            for value in (None, "", "budget", "operational_buget", True, {}, []):
                cases.append(("mode " + repr(value),
                              lambda c, v=value: c[label].update(acceptance_mode=v),
                              "acceptance_mode must be"))
            for value in (None, 0, -1, True, 2.5, "2000000000000"):
                cases.append(("bytes " + repr(value),
                              lambda c, v=value: c[label]["operational_budget"].update(bytes=v),
                              "positive aggregate bytes"))
            for value in (None, "", "  ", False, 1, {}, []):
                cases.append(("ack " + repr(value),
                              lambda c, v=value: c[label]["operational_budget"].update(
                                  risk_acceptance=v),
                              "risk_acceptance evidence reference"))
            for value in (None, True, "accepted", {}, {"bytes": 2_000_000_000_000},
                          {"risk_acceptance": "evidence:accepted"},
                          {"bytes": 2_000_000_000_000, "risk_acceptance": "evidence:accepted",
                           "acknowledged": True},
                          {"bytes": 100, "risk_acceptance": "evidence:accepted", "inodes": 100}):
                cases.append(("budget " + repr(value),
                              lambda c, v=value: c[label].update(operational_budget=v),
                              "requires exactly bytes and risk_acceptance"))
            for value in ({}, {"bytes": None, "inodes": None, "mechanism": None,
                               "scope": None, "evidence": None},
                          fixture.config()[label]["physical_cap"], False, "unquotaed"):
                cases.append(("contradictory cap " + repr(value),
                              lambda c, v=value: c[label].update(physical_cap=v),
                              "must not claim a physical cap"))
            for name, change, message in cases:
                with self.subTest(name):
                    config = fixture.budget_config((label,))
                    change(config)
                    self.assert_refused(fixture, qualify(config), Exploding(), message)
            config = fixture.budget_config((label,))
            config["enabled"] = False
            config[label]["acceptance_mode"] = "typo"
            self.assertEqual(preflight.validate(config, Exploding()), {"status": "disabled"})

    def test_metadata_physical_mode_still_requires_hard_byte_and_inode_bounds(self):
        fixture = Fixture(self.base)
        changes = [
            (lambda c: c["metadata"].update(physical_cap=None), "physical cap is unqualified"),
            (lambda c: c["metadata"]["physical_cap"].update(bytes=None), "bytes and inodes"),
            (lambda c: c["metadata"]["physical_cap"].update(inodes=0), "bytes and inodes"),
            (lambda c: c["metadata"]["physical_cap"].update(bytes=True), "bytes and inodes"),
            (lambda c: c["metadata"]["physical_cap"].update(evidence=None), "evidence is required"),
            (lambda c: c["metadata"]["physical_cap"].update(mechanism="operational_budget"),
             "must bound physical allocation"),
            (lambda c: c["metadata"].update(acceptance_mode="operational_budget"),
             "must not claim a physical cap"),
            (lambda c: c["metadata"].update(operational_budget={"bytes": 100}),
             "physical_cap mode must not declare an operational_budget"),
        ]
        for change, message in changes:
            with self.subTest(message=message, change=change):
                config = fixture.budget_config()
                change(config)
                self.assert_refused(fixture, qualify(config), Exploding(), message)

    def test_budget_mode_value_and_acknowledgment_are_digest_bound(self):
        fixture = Fixture(self.base)
        for label in ("data", "metadata"):
            for change in (
                    lambda c: c[label].update(acceptance_mode="physical_cap"),
                    lambda c: c[label]["operational_budget"].update(bytes=1),
                    lambda c: c[label]["operational_budget"].update(risk_acceptance="evidence:other")):
                config = fixture.budget_config((label,))
                change(config)
                self.assert_refused(fixture, config, Exploding(), "exact configuration digest")
            for gate in preflight.DURABILITY_GATES:
                config = fixture.budget_config((label,))
                config["qualifications"][gate] = None
                self.assert_refused(fixture, qualify(config), Exploding(), "unqualified live gate")

    def test_mode_transitions_preserve_legacy_physical_cap_semantics(self):
        fixture = Fixture(self.base)
        for label in ("data", "metadata"):
            legacy = fixture.config()
            original_digest = preflight.digest(legacy)
            result = preflight.validate(legacy, fixture.probe())
            self.assertEqual(result[label + "_acceptance"], {"mode": "physical_cap"})
            self.assertEqual(preflight.digest(legacy), original_digest)
            self.assertNotIn("acceptance_mode", legacy[label])
            explicit = copy.deepcopy(legacy)
            explicit[label]["acceptance_mode"] = "physical_cap"
            self.assertNotEqual(preflight.digest(explicit), original_digest)
            self.assert_refused(fixture, explicit, Exploding(), "exact configuration digest")
            preflight.validate(qualify(explicit), fixture.probe())
            # Losing physical evidence never opts into the budget path.
            legacy[label]["physical_cap"] = None
            self.assert_refused(fixture, qualify(legacy), Exploding(), "physical cap is unqualified")
            budget = fixture.budget_config((label,))
            del budget[label]["acceptance_mode"]
            self.assert_refused(fixture, qualify(budget), Exploding(),
                                "physical_cap mode must not declare an operational_budget")
            # Both transitions require removing the incompatible declaration.
            explicit[label]["acceptance_mode"] = "operational_budget"
            explicit[label]["operational_budget"] = fixture.budget_config((label,))[label]["operational_budget"]
            self.assert_refused(fixture, qualify(explicit), Exploding(), "must not claim a physical cap")
            explicit[label]["physical_cap"] = None
            preflight.validate(qualify(explicit), fixture.probe())
            explicit[label]["acceptance_mode"] = "physical_cap"
            explicit[label]["physical_cap"] = fixture.config()[label]["physical_cap"]
            self.assert_refused(fixture, qualify(explicit), Exploding(),
                                "must not declare an operational_budget")
            del explicit[label]["operational_budget"]
            preflight.validate(qualify(explicit), fixture.probe())

    def test_namespace_mount_and_custody_invariants_apply_in_budget_mode(self):
        fixture = Fixture(self.base)
        declarations = [
            (lambda c: c["data"].update(path=str(fixture.nas)), "strictly below"),
            (lambda c: c["data"].update(path=str(fixture.nas / "datasets/garage")),
             "top-level directory"),
            (lambda c: c["data"].update(path=str(fixture.nas / "${BLOB_DIR}")),
             "Docker interpolation"),
            (lambda c: c["data"]["mount"].update(fsroot="/subtree"), "bind or subtree"),
            (lambda c: c["data"]["mount"].update(fstype="ext4"), "explicit NFS"),
            (lambda c: c["data"]["mount"].update(options=["rw"]), "exactly one of hard or soft"),
            (lambda c: c["metadata"]["mount"].update(fstype="nfs"), "local filesystem"),
            (lambda c: (c["metadata"]["mount"].update(target=str(fixture.nas)),
                        c["metadata"].update(path=str(fixture.nas / "garage-meta")),
                        c["metadata"]["physical_cap"].update(
                            scope=str(fixture.nas / "garage-meta"))), "separate mounts"),
            (lambda c: c.update(uid=0), "non-root uid"),
        ]
        for change, message in declarations:
            with self.subTest(message):
                config = fixture.budget_config()
                change(config)
                self.assert_refused(fixture, qualify(config), Exploding(), message)
        for key, value, message in (("source", "nas:/wrong", "mount source"),
                                    ("target", "/", "mount target"),
                                    ("options", "ro,soft", "lacks rw")):
            probe = fixture.probe()
            record = probe.mounts[str(fixture.nas)]
            if key == "target":
                del probe.mounts[str(fixture.nas)]
            else:
                record[key] = value
            self.assert_refused(fixture, fixture.budget_config(), probe, message)
        probes = [
            (fixture.probe(owners={str(fixture.blobs): 988}), "must be owned by uid"),
            (fixture.probe(owners={str(fixture.objectstore): 988}), "owned by root or uid"),
            (fixture.probe(devices={str(fixture.blobs): -1}), "on another device"),
            (fixture.probe(modes={str(fixture.objectstore / "config"): 0o777}),
             "not be group or other writable"),
        ]
        overmount = fixture.probe()
        overmount.mounts[str(fixture.blobs)] = dict(overmount.mounts[str(fixture.nas)])
        probes.append((overmount, "mount target"))
        for probe, message in probes:
            with self.subTest(message):
                self.assert_refused(fixture, fixture.budget_config(), probe, message)
        target = fixture.nas / "elsewhere"
        target.mkdir(mode=0o700)
        fixture.blobs.rmdir()
        fixture.blobs.symlink_to(target)
        self.assert_refused(fixture, fixture.budget_config(), fixture.probe(), "symlink at")


class IdentityMappingTest(Scratch):
    """Synthetic owner mapping, never evidence for production qualification."""

    assert_refused = PreflightTest.assert_refused

    def mapped_probe(self, fixture, **kwargs):
        owners = {str(fixture.blobs): 977, **kwargs.pop("owners", {})}
        groups = {str(fixture.blobs): 988, **kwargs.pop("groups", {})}
        return fixture.probe(owners=owners, groups=groups, **kwargs)

    def test_process_1000_1000_accepts_measured_data_977_988_in_both_modes(self):
        fixture = Fixture(self.base)
        for budget, metadata_budget in ((False, False), (True, False),
                                        (False, True), (True, True)):
            with self.subTest(budget=budget, metadata_budget=metadata_budget):
                config = fixture.mapped_config(budget=budget)
                if metadata_budget:
                    config["metadata"] = fixture.budget_config(("metadata",))["metadata"]
                    qualify(config)
                original, before = copy.deepcopy(config), snapshot(self.base)
                probe = self.mapped_probe(fixture)
                result = preflight.validate(config, probe)
                self.assertEqual(result["status"], "passed-startup-preflight")
                self.assertEqual((config["uid"], config["gid"]), (1000, 1000))
                self.assertIn(str(fixture.blobs), probe.inspected)
                self.assertIn(str(fixture.objectstore / "meta"), probe.inspected)
                self.assertEqual(config, original)
                self.assertEqual(snapshot(self.base), before)

    def test_either_wrong_observed_data_id_refuses(self):
        fixture = Fixture(self.base)
        for owner, group in ((1000, 988), (977, 1000), (1000, 1000), (0, 988), (977, 0)):
            with self.subTest(owner=owner, group=group):
                probe = self.mapped_probe(fixture, owners={str(fixture.blobs): owner},
                                          groups={str(fixture.blobs): group})
                self.assert_refused(fixture, fixture.mapped_config(), probe,
                                    "must be owned by mapped uid:gid 977:988")

    def test_malformed_mapping_refuses_before_host_inspection(self):
        fixture = Fixture(self.base)
        valid = fixture.mapped_config()["data"]["identity_mapping"]
        cases: list[tuple[object, str]] = [(value, "requires exactly") for value in
                 (None, False, True, 0, "", "977:988", [], {}, {"owner_uid": 977})]
        for key in valid:
            incomplete = dict(valid)
            del incomplete[key]
            cases.append((incomplete, "requires exactly"))
        for key in ("mode", "uid", "gid", "process_uid", "process_gid"):
            cases.append(({**valid, key: 1000}, "requires exactly"))
        for key in ("owner_uid", "owner_gid"):
            for value in (None, True, False, 0, -1, 977.0, "977", [], {}):
                cases.append(({**valid, key: value}, "positive non-root integer"))
        for value in (None, "", " \n\t", True, 1, [], {}):
            cases.append(({**valid, "evidence": value}, "qualification evidence"))
        for mapping, message in cases:
            with self.subTest(mapping=mapping):
                config = fixture.mapped_config()
                config["data"]["identity_mapping"] = mapping
                self.assert_refused(fixture, qualify(config), Exploding(), message)
        for mapping in (None, {}, valid):
            with self.subTest(metadata_mapping=mapping):
                config = fixture.mapped_config()
                config["metadata"]["identity_mapping"] = mapping
                self.assert_refused(fixture, qualify(config), Exploding(),
                                    "allowed only on data, never metadata")

    def test_mapping_and_process_identity_require_exact_digest_requalification(self):
        fixture = Fixture(self.base)
        changes = [
            lambda c: c["data"].pop("identity_mapping"),
            lambda c: c["data"]["identity_mapping"].update(owner_uid=978),
            lambda c: c["data"]["identity_mapping"].update(owner_gid=989),
            lambda c: c["data"]["identity_mapping"].update(evidence="fixture:other-mapping"),
            lambda c: c.update(uid=1001),
            lambda c: c.update(gid=1001),
        ]
        for change in changes:
            config = fixture.mapped_config()
            change(config)
            self.assert_refused(fixture, config, Exploding(), "exact configuration digest")
        legacy = fixture.config()
        legacy["data"]["identity_mapping"] = fixture.mapped_config()["data"]["identity_mapping"]
        self.assert_refused(fixture, legacy, Exploding(), "exact configuration digest")
        config = fixture.mapped_config()
        config["data"]["identity_mapping"]["evidence"] = "fixture:other-mapping"
        preflight.validate(qualify(config), self.mapped_probe(fixture))
        for gate in preflight.DURABILITY_GATES:
            config = fixture.mapped_config()
            config["qualifications"][gate] = None
            self.assert_refused(fixture, qualify(config), Exploding(), "unqualified live gate")

    def test_absent_mapping_preserves_legacy_uid_only_contract_and_configuration(self):
        fixture = Fixture(self.base)
        config = fixture.config()
        original, original_digest = copy.deepcopy(config), preflight.digest(config)
        self.assertNotIn("identity_mapping", config["data"])
        self.assertNotIn("identity_mapping", CANONICAL["data"])
        # The original contract did not bind st_gid, even to the process gid.
        for group in (0, 988, 1000, 1234):
            probe = fixture.probe(groups={str(fixture.blobs): group})
            preflight.validate(config, probe)
        self.assertEqual(config, original)
        self.assertEqual(preflight.digest(config), original_digest)
        self.assert_refused(fixture, config, self.mapped_probe(fixture),
                            "must be owned by uid 1000")

    def test_data_mapping_never_trusts_nas_owner_for_local_metadata(self):
        fixture = Fixture(self.base)
        for budget in (False, True):
            config = fixture.mapped_config()
            if budget:
                config["metadata"] = fixture.budget_config(("metadata",))["metadata"]
                qualify(config)
            for path in (Path("/"), fixture.base, fixture.local, fixture.local / "swarm",
                         fixture.objectstore, fixture.objectstore / "config",
                         fixture.objectstore / "meta"):
                with self.subTest(path=path):
                    probe = self.mapped_probe(fixture, owners={str(path): 977})
                    self.assert_refused(fixture, config, probe, "owned by root or uid 1000")
            self.assert_refused(fixture, config, self.mapped_probe(
                fixture, owners={str(fixture.objectstore / "meta"): 0}), "must be owned by uid 1000")
            self.assert_refused(fixture, config, self.mapped_probe(
                fixture, modes={str(fixture.objectstore / "config"): 0o777}),
                "not be group or other writable")

    def test_mapping_preserves_data_namespace_mount_device_and_mode_refusals(self):
        fixture = Fixture(self.base)
        for path, message in ((fixture.nas, "strictly below"),
                              (fixture.nas / "datasets/garage", "top-level directory")):
            config = fixture.mapped_config()
            config["data"]["path"] = str(path)
            self.assert_refused(fixture, qualify(config), Exploding(), message)
        for mode in (0o710, 0o701, 0o770, 0o777):
            self.assert_refused(fixture, fixture.mapped_config(),
                                self.mapped_probe(fixture, modes={str(fixture.blobs): mode}),
                                "no group or other access")
        self.assert_refused(fixture, fixture.mapped_config(),
                            self.mapped_probe(fixture, devices={str(fixture.blobs): -1}),
                            "on another device")
        for key, value, message in (("source", "nas:/wrong", "mount source"),
                                    ("fstype", "ext4", "mount fstype"),
                                    ("fsroot", "/sub", "mount fsroot"),
                                    ("options", "ro,soft", "lacks rw")):
            probe = self.mapped_probe(fixture)
            probe.mounts[str(fixture.nas)][key] = value
            self.assert_refused(fixture, fixture.mapped_config(), probe, message)
        probe = self.mapped_probe(fixture)
        del probe.mounts[str(fixture.nas)]
        self.assert_refused(fixture, fixture.mapped_config(), probe, "mount target")
        probe = self.mapped_probe(fixture)
        probe.mounts[str(fixture.blobs)] = dict(probe.mounts[str(fixture.nas)])
        self.assert_refused(fixture, fixture.mapped_config(), probe, "mount target")
        config = fixture.mapped_config(budget=False)
        config["data"]["physical_cap"].update(mechanism="dedicated_filesystem",
                                               scope=str(fixture.nas))
        self.assert_refused(fixture, qualify(config), self.mapped_probe(fixture),
                            "mount is shared")
        target = fixture.nas / "elsewhere"
        target.mkdir(mode=0o700)
        fixture.blobs.rmdir()
        fixture.blobs.symlink_to(target)
        self.assert_refused(fixture, fixture.mapped_config(), self.mapped_probe(fixture),
                            "symlink at")


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
        budget = dict(fixture.budget_config(), host="localhost")
        qualify(budget)
        cases += [("budget " + name, stack, budget, script, message)
                  for name, stack, storage, script, message in cases if storage is qualified]
        for stores in (("metadata",), ("data", "metadata")):
            storage = dict(fixture.budget_config(stores), host="localhost")
            qualify(storage)
            cases += [(str(stores) + " " + name, stack, storage, script, message)
                      for name, stack, original, script, message in cases
                      if original is qualified]
        mapped = dict(fixture.mapped_config(), host="localhost")
        qualify(mapped)
        mapped_env = {**env, "OBJECTSTORE_GID": "1000"}
        mapped_stack = {**matched, "env_extra": mapped_env}
        cases.append(("mapped process identity", mapped_stack, mapped, passing, "Lint objectstore"))
        for key, wrong in (("OBJECTSTORE_UID", "977"), ("OBJECTSTORE_GID", "988")):
            cases.append(("mapped owner is not process " + key,
                          {**mapped_stack, "env_extra": {**mapped_env, key: wrong}},
                          mapped, passing, "objectstore env_extra " + key + " must match"))
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
                         "ANSIBLE_REMOTE_TEMP": str(self.base / "remote"),
                         "ANSIBLE_FILTER_PLUGINS": str(ROOT / "roles/swarm/filter_plugins")},
                    capture_output=True, text=True, timeout=120)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 2, output)
                self.assertIn(message, output)
                if message.startswith("objectstore env_extra"):
                    self.assertNotIn("TASK [Run the read-only storage preflight", output)
                    self.assertNotIn("TASK [Lint", output)
                self.assertFalse(effects.exists(), output)
                self.assertEqual(snapshot(fixture.base), before)



class ManagedDataTest(Scratch):
    """Mode/uid/gid are recording-probe observations, not NFS or privilege tests."""

    def setUp(self):
        super().setUp()
        self.fixture = Fixture(self.base)
        self.config = self.fixture.budget_config(("data", "metadata"))
        self.config["gid"] = 1000
        self.config["data"]["identity_mapping"] = {
            "owner_uid": 977, "owner_gid": 988, "evidence": "fixture:mapping-only"}
        self.config["data"]["access_profile"] = {
            "name": "managed_nfs_data_v1", "acceptance_reference": FIXTURE_REFERENCE}
        operator_qualify(self.config, True)

    def probe(self, mode=0o770):
        path = str(self.fixture.blobs)
        return self.fixture.probe(owners={path: 977}, groups={path: 988}, modes={path: mode})

    def test_stable_exact_modes_pass_root_check_without_repair_or_entry_walk(self):
        child = self.fixture.blobs / "not-inspected-at-start"
        child.write_bytes(b"maintenance checks entries separately")
        child.chmod(0o666)
        before = snapshot(self.base)
        for mode in (0o700, 0o770):
            self.assertEqual(preflight.validate(self.config, self.probe(mode))["status"],
                             "passed-startup-preflight")
        self.assertEqual(snapshot(self.base), before)

    def test_wrong_mode_bits_owner_and_local_custody_refuse(self):
        for mode in (0o750, 0o710, 0o777, 0o1770, 0o2770, 0o4700):
            with self.subTest(mode=mode), self.assertRaises(preflight.Refusal):
                preflight.validate(self.config, self.probe(mode))
        for uid, gid in ((1000, 988), (977, 1000), (0, 0)):
            path = str(self.fixture.blobs)
            with self.assertRaises(preflight.Refusal):
                preflight.validate(self.config, self.fixture.probe(
                    owners={path: uid}, groups={path: gid}, modes={path: 0o770}))
        probe = self.probe()
        probe.modes[str(self.fixture.objectstore / "meta")] = 0o770
        with self.assertRaises(preflight.Refusal):
            preflight.validate(self.config, probe)

    def test_profile_mapping_contract_and_exact_keys_refuse_before_io(self):
        changes = [
            lambda c: c.pop("operating_contract"),
            lambda c: c["data"].pop("identity_mapping"),
            lambda c: c["data"]["access_profile"].update(name="managed-nfs-typo"),
            lambda c: c["data"]["access_profile"].update(extra=True),
            lambda c: c["data"]["access_profile"].update(acceptance_reference="jocasta:latest"),
            lambda c: c["data"]["access_profile"].update(acceptance_reference="jocasta:" + "a"*32 + "@" + "9"*100),
            lambda c: c["data"].update(access_proflie={}),
            lambda c: c["data"].update(unknown=True),
            lambda c: c["metadata"].update(access_profile=c["data"]["access_profile"]),
        ]
        for change in changes:
            config = copy.deepcopy(self.config)
            change(config)
            if "operating_contract" in config:
                operator_qualify(config, True)
            else:
                qualify(config)
            with self.subTest(change=change), self.assertRaises(preflight.Refusal):
                preflight.validate(config, Exploding())

    def test_omission_preserves_strict_legacy_and_profile_changes_digest(self):
        before = preflight.digest(self.config)
        self.config["data"].pop("access_profile")
        operator_qualify(self.config, True)
        self.assertNotEqual(before, preflight.digest(self.config))
        with self.assertRaises(preflight.Refusal):
            preflight.validate(self.config, self.probe())
        self.assertEqual(preflight.validate(self.config, self.probe(0o700))["status"],
                         "passed-startup-preflight")


if __name__ == "__main__":
    unittest.main()
