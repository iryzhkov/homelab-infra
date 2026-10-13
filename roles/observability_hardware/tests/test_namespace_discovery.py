#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "files/discover-smart-drives.py"
spec = importlib.util.spec_from_file_location("smart_discovery", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NamespaceDiscoveryTest(unittest.TestCase):
    def test_one_apple_controller_is_one_declared_physical_disk(self):
        paths = ["/dev/disk/by-id/nvme-APPLE_SERIAL", "/dev/disk/by-id/nvme-APPLE_SERIAL_1",
                 "/dev/disk/by-id/nvme-APPLE_SERIAL_2", "/dev/disk/by-id/nvme-APPLE_SERIAL_3",
                 "/dev/disk/by-id/nvme-APPLE_SERIAL-part1"]
        def physical(path):
            return "/dev/nvme0n" + ("2" if path.endswith("_2") else "3" if path.endswith("_3") else "1")
        nsid = lambda path: int(physical(path)[-1])
        with patch.object(module.os, "stat", return_value=types.SimpleNamespace(st_mode=0o060000)), patch.object(module.os.path, "realpath", side_effect=physical):
            with self.assertRaises(ValueError):
                module.discover([{"serial": "SERIAL"}], "smartctl", paths, lambda path: "SERIAL", nsid)
            devices = module.discover([{"serial": "SERIAL", "namespace": 1, "model": "APPLE SSD"}], "smartctl", paths, lambda path: "SERIAL", nsid)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device"], "/dev/disk/by-id/nvme-APPLE_SERIAL")

    def test_duplicate_declared_serials_fail_instead_of_double_counting(self):
        with self.assertRaises(ValueError):
            module.discover([{"serial": "S"}, {"serial": "S"}], "smartctl", [], lambda path: "S")


if __name__ == "__main__":
    unittest.main()
