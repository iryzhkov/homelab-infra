#!/usr/bin/env python3
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "files/smartctl-apple-supported"
loader = importlib.machinery.SourceFileLoader("apple_smart", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class AppleReadOnlyCompatibilityTest(unittest.TestCase):
    def args(self, device="/dev/disk/by-id/nvme-APPLE_SERIAL"):
        return ["--json", "--info", "--health", "--attributes", "--capabilities",
                "--tolerance=verypermissive", "--nocheck=standby", "--format=brief",
                "--log=error", "--device=auto", device]

    def test_only_unsupported_optional_log_is_omitted(self):
        device = self.args()[-1]
        original = self.args()
        adapted = module.adapt_args(original, device)
        self.assertEqual(adapted, original[:8] + original[9:])
        self.assertIn("--health", adapted)
        self.assertIn("--nocheck=standby", adapted)
        self.assertEqual(adapted[-1], device)

    def test_unsafe_flags_wrong_device_or_changed_power_mode_fail_closed(self):
        device = self.args()[-1]
        for args in [self.args() + ["--smart=off"], self.args("/dev/nvme0n2"),
                     [x.replace("--nocheck=standby", "--nocheck=never") for x in self.args()],
                     [x.replace("--health", "--test=short") for x in self.args()]]:
            with self.assertRaises(ValueError):
                module.adapt_args(args, device)

    def test_namespace_serial_and_driver_identity_are_fenced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = root / "class/block/nvme0"
            namespace = controller / "nvme0n1"
            namespace.mkdir(parents=True)
            (root / "class/block/nvme0n1").symlink_to(namespace)
            (namespace / "nsid").write_text("1")
            (controller / "serial").write_text("SERIAL")
            (controller / "device").mkdir()
            driver = root / "nvme-apple"
            driver.mkdir()
            (controller / "device/driver").symlink_to(driver)
            config = {"device": "/dev/disk/by-id/nvme-APPLE_SERIAL", "serial": "SERIAL"}
            module.validate_namespace(config, root, lambda path: "/dev/nvme0n1")
            (namespace / "nsid").write_text("2")
            with self.assertRaises(ValueError):
                module.validate_namespace(config, root, lambda path: "/dev/nvme0n1")
            (namespace / "nsid").write_text("1")
            (controller / "serial").write_text("OTHER")
            with self.assertRaises(ValueError):
                module.validate_namespace(config, root, lambda path: "/dev/nvme0n1")
            (controller / "serial").write_text("SERIAL")
            (controller / "device/driver").unlink()
            (controller / "device/driver").symlink_to(root / "non-apple-driver")
            with self.assertRaises(ValueError):
                module.validate_namespace(config, root, lambda path: "/dev/nvme0n1")


if __name__ == "__main__":
    unittest.main()
