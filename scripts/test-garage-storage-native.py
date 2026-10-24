#!/usr/bin/env python3
"""Every-start guard contracts, using the existing fake-mount storage fixture."""
import contextlib
import copy
import io
import importlib.util
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import types


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = load("native", "garage-storage-native.py")
fixture = load("fixture", "test-garage-storage-preflight.py")


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=fixture.scratch_home())
        self.addCleanup(self.temp.cleanup)
        self.f = fixture.Fixture(Path(self.temp.name))
        self.storage = self.f.budget_config(("data", "metadata"))
        self.storage["gid"] = 1000
        self.storage = fixture.qualify(self.storage)
        self.config = tomllib.loads((Path(__file__).parents[1] /
            "stacks/objectstore/garage-native.toml.example").read_text())
        self.config["data_dir"] = str(self.f.blobs)
        self.config["metadata_dir"] = str(self.f.objectstore / "meta")
        self.probe = fixture.Probe(self.f.mounts, 1000)
        self.free = patch.object(native.os, "statvfs", return_value=types.SimpleNamespace(
            f_bavail=1024**3, f_frsize=4096, f_files=200000, f_ffree=100000, f_favail=100000))
        self.free.start()
        self.addCleanup(self.free.stop)

    def observed_space(self, path):
        # Actual read-only receipts, root and uid 1000 agreed on both tuples.
        if path == str(self.f.blobs):
            return types.SimpleNamespace(f_files=0, f_ffree=0, f_favail=0,
                                         f_bavail=15497819, f_frsize=1048576)
        return types.SimpleNamespace(f_files=19660800, f_ffree=18270611,
                                     f_favail=18270611, f_bavail=52847741, f_frsize=4096)

    def opt_in(self):
        self.storage["data"]["unknown_filecount_risk_acceptance"] = "fixture:accept-unknown-nfs"
        self.storage = fixture.qualify(self.storage)

    def test_observed_all_zero_nfs_is_unknown_only_with_explicit_acceptance(self):
        self.opt_in()
        with patch.object(native.os, "statvfs", side_effect=self.observed_space):
            warning = io.StringIO()
            with contextlib.redirect_stderr(warning):
                result = native.check(self.storage, self.config, probe=self.probe)
        self.assertEqual(result["startup_headroom"]["data"]["filecounts"], "UNKNOWN")
        self.assertIsNone(result["startup_headroom"]["data"]["available_inodes"])
        self.assertEqual(result["startup_headroom"]["metadata"]["filecounts"], "known")
        self.assertTrue(any("no free-inode capacity established" in item
                            for item in result["limitations"]))
        self.assertIn("UNKNOWN", warning.getvalue())
        self.assertIn("no free-inode capacity established", warning.getvalue())

    def test_actual_config_and_each_start_recheck_mounts(self):
        native.check(self.storage, self.config, probe=self.probe)
        self.probe.mounts[str(self.f.nas)]["source"] = "wrong:/export"
        with self.assertRaises(ValueError):
            native.check(self.storage, self.config, probe=self.probe)

    def test_disabled_identity_qualification_paths_fsync_and_network_refuse(self):
        for change in ("disabled", "uid", "qualification", "data", "metadata", "fsync", "s3", "rpc", "admin"):
            with self.subTest(change=change):
                storage, config = copy.deepcopy(self.storage), copy.deepcopy(self.config)
                if change == "disabled": storage["enabled"] = False
                elif change == "uid": storage["uid"] = 1001
                elif change == "qualification": storage["qualifications"]["configuration"] = "wrong"
                elif change == "data": config["data_dir"] = "/other"
                elif change == "metadata": config["metadata_dir"] = "/other"
                elif change == "fsync": config["data_fsync"] = False
                elif change == "s3": config["s3_api"]["api_bind_addr"] = "0.0.0.0:3900"
                elif change == "rpc": config["rpc_bind_addr"] = "0.0.0.0:3901"
                else: config["admin"] = {"api_bind_addr": "0.0.0.0:3903"}
                with self.assertRaises(ValueError):
                    native.check(storage, config, probe=self.probe)

    def test_low_space_or_inodes_refuse(self):
        for field in ("f_bavail", "f_favail"):
            values = dict(f_bavail=1024**3, f_frsize=4096, f_files=200000, f_ffree=100000, f_favail=100000)
            values[field] = 0
            with patch.object(native.os, "statvfs", return_value=types.SimpleNamespace(**values)):
                with self.assertRaises(ValueError):
                    native.check(self.storage, self.config, probe=self.probe)

    def test_unknown_default_and_every_live_gate_stay_strict(self):
        with patch.object(native.os, "statvfs", side_effect=self.observed_space):
            with self.assertRaisesRegex(ValueError, "UNKNOWN filecounts"):
                native.check(self.storage, self.config, probe=self.probe)
            self.opt_in()
            for gate in fixture.preflight.DURABILITY_GATES:
                storage = copy.deepcopy(self.storage)
                storage["qualifications"][gate] = None
                with self.subTest(gate=gate), self.assertRaisesRegex(ValueError, "unqualified live gate"):
                    native.check(storage, self.config, probe=self.probe)
            storage = copy.deepcopy(self.storage)
            storage["data"]["unknown_filecount_risk_acceptance"] = "fixture:changed"
            with self.assertRaisesRegex(ValueError, "exact configuration digest"):
                native.check(storage, self.config, probe=self.probe)

    def test_opt_in_schema_refuses_wrong_store_mode_type_and_missing_evidence(self):
        self.opt_in()
        cases = [
            lambda s: s["metadata"].update(unknown_filecount_risk_acceptance="fixture:no"),
            lambda s: s.update(data=self.f.config()["data"] | {
                "unknown_filecount_risk_acceptance": "fixture:no"}),
            lambda s: s["data"]["mount"].update(fstype="ext4"),
            lambda s: s["data"]["mount"].update(fstype="unknown"),
        ] + [lambda s, v=v: s["data"].update(unknown_filecount_risk_acceptance=v)
             for v in (None, "", "  ", True, 1, {}, [])]
        for change in cases:
            storage = copy.deepcopy(self.storage)
            change(storage)
            fixture.qualify(storage)
            with self.subTest(change=change), self.assertRaises(ValueError):
                native.check(storage, self.config, probe=fixture.Exploding())

    def test_all_zero_never_waives_mount_type_source_options_or_custody(self):
        self.opt_in()
        for change in ("fstype", "source", "options", "custody"):
            probe = self.f.probe()
            if change == "custody":
                probe.modes[str(self.f.blobs)] = 0o777
            else:
                probe.mounts[str(self.f.nas)][change] = {
                    "fstype": "ext4", "source": "wrong:/export", "options": "ro,soft"}[change]
            with self.subTest(change=change), patch.object(
                    native.os, "statvfs", side_effect=self.observed_space):
                with self.assertRaises(ValueError):
                    native.check(self.storage, self.config, probe=probe)

    def test_count_and_byte_failures_never_become_unknown(self):
        self.opt_in()
        bad = [
            {"f_files": 10000, "f_ffree": 5000, "f_favail": 0},
            {"f_files": 10000, "f_ffree": 5000, "f_favail": 4095},
            {"f_files": 0, "f_ffree": 5000, "f_favail": 5000},
            {"f_files": 10000, "f_ffree": 4095, "f_favail": 4096},
            {"f_files": 10000, "f_ffree": 20000, "f_favail": 5000},
            {"f_bavail": 0}, {"f_bavail": 1023, "f_frsize": 1048576},
            {"f_bavail": -1}, {"f_frsize": 0}, {"f_frsize": -1},
        ]
        for field in ("f_files", "f_ffree", "f_favail"):
            bad += [{field: value} for value in (None, -1, True, 1.5, "0")]
        for label in ("data", "metadata"):
            for changes in bad + ([{"f_files": 0, "f_ffree": 0, "f_favail": 0}] if label == "metadata" else []):
                def space(path):
                    value = self.observed_space(path)
                    if path == self.storage[label]["path"]:
                        value.__dict__.update(changes)
                    return value
                with self.subTest(label=label, changes=changes), patch.object(
                        native.os, "statvfs", side_effect=space), self.assertRaises(ValueError):
                    native.check(self.storage, self.config, probe=self.probe)
        # Missing counters are malformed, even if the only reported counter is ample.
        with patch.object(native.os, "statvfs", return_value=types.SimpleNamespace(
                f_bavail=1024**3, f_frsize=4096, f_favail=100000)):
            with self.assertRaisesRegex(ValueError, "malformed filecounts"):
                native.check(self.storage, self.config, probe=self.probe)
        with patch.object(native.os, "statvfs", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                native.check(self.storage, self.config, probe=self.probe)

    def test_exact_headroom_floors_and_nfs4(self):
        self.opt_in()
        self.storage["data"]["mount"]["fstype"] = "nfs4"
        self.probe.mounts[str(self.f.nas)]["fstype"] = "nfs4"
        fixture.qualify(self.storage)
        def space(path):
            minimum = 1024**3 if path == str(self.f.blobs) else 2 * 1024**3
            return types.SimpleNamespace(f_bavail=minimum, f_frsize=1,
                                         f_files=5000, f_ffree=4096, f_favail=4096)
        with patch.object(native.os, "statvfs", side_effect=space):
            result = native.check(self.storage, self.config, probe=self.probe)
        self.assertEqual(result["startup_headroom"]["data"]["available_inodes"], 4096)
        self.assertEqual(result["startup_headroom"]["metadata"]["available_bytes"], 2 * 1024**3)

    def test_unit_cannot_force_kill_or_auto_restart_and_is_inert(self):
        unit = (Path(__file__).parents[1] / "stacks/objectstore/garage-objectstore.service.example").read_text()
        for line in ("User=1000", "Group=1000", "SendSIGKILL=no", "Restart=no",
                     "KillMode=control-group", "TimeoutStopSec=30s", "MemoryMax=2G", "TasksMax=128"):
            self.assertIn(line, unit.splitlines())
        for directive in ("ProtectSystem", "ProtectHome", "ReadWritePaths", "ReadOnlyPaths",
                          "BindPaths", "BindReadOnlyPaths", "PrivateMounts", "PrivateTmp",
                          "RootDirectory", "RootImage", "TemporaryFileSystem"):
            self.assertFalse(any(line.startswith(directive + "=") for line in unit.splitlines()),
                             "native guard must inherit the host storage mount view")
        self.assertNotIn("[Install]", unit)
        self.assertEqual([x for x in unit.splitlines() if x.startswith("Exec")],
            ["ExecStart=/usr/bin/python3 -I -B /usr/local/libexec/garage-storage-native.py --config /etc/garage/storage.json"])


if __name__ == "__main__":
    unittest.main()
