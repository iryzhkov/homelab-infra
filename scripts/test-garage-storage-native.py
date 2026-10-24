#!/usr/bin/env python3
"""Every-start guard contracts, using the existing fake-mount storage fixture."""
import copy
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
            f_bavail=1024**3, f_frsize=4096, f_favail=100000))
        self.free.start()
        self.addCleanup(self.free.stop)

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
            values = dict(f_bavail=1024**3, f_frsize=4096, f_favail=100000)
            values[field] = 0
            with patch.object(native.os, "statvfs", return_value=types.SimpleNamespace(**values)):
                with self.assertRaises(ValueError):
                    native.check(self.storage, self.config, probe=self.probe)

    def test_unit_cannot_force_kill_or_auto_restart_and_is_inert(self):
        unit = (Path(__file__).parents[1] / "stacks/objectstore/garage-objectstore.service.example").read_text()
        for line in ("User=1000", "Group=1000", "SendSIGKILL=no", "Restart=no",
                     "KillMode=control-group", "TimeoutStopSec=30s", "MemoryMax=2G", "TasksMax=128"):
            self.assertIn(line, unit.splitlines())
        self.assertNotIn("[Install]", unit)
        self.assertEqual([x for x in unit.splitlines() if x.startswith("Exec")],
            ["ExecStart=/usr/bin/python3 -I -B /usr/local/libexec/garage-storage-native.py --config /etc/garage/storage.json"])


if __name__ == "__main__":
    unittest.main()
