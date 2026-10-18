import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

loader = importlib.machinery.SourceFileLoader("nas_recovery", str(
    Path(__file__).parents[1] / "roles/nas_recovery/files/nas-recovery"))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

TARGET = "/mnt/shared"
SOURCE = "nas:/media"
INFO = {"source": SOURCE, "fstype": "nfs", "options": "soft,timeo=50,retrans=3"}
CONTAINER = {"Id": "a" * 64, "Name": "/Emby", "Mounts": [
    {"Type": "bind", "Source": TARGET, "Destination": TARGET}],
    "State": {"Running": True}}


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.recovery = module.Recovery({TARGET: SOURCE}, self.tmp.name)
        self.recovery.run = Mock(return_value=Mock(stdout="", returncode=0))
        self.recovery.probe = Mock(return_value=0)
        self.recovery.containers = Mock(return_value=[CONTAINER])
        self.recovery.mount_info = Mock(side_effect=lambda path, fstab=False:
                                        INFO if path == TARGET else None)

    def commands(self):
        return [call.args for call in self.recovery.run.call_args_list]

    def stale(self):
        self.recovery.probe.side_effect = lambda path: 116 if path == TARGET else 0

    def test_healthy_is_noop(self):
        self.recovery.cycle()
        self.assertEqual([], self.commands())
        self.recovery.containers.assert_not_called()

    def test_stale_preflight_then_replace_and_persist_consumers(self):
        # The root becomes healthy after the replacement mount command.
        healthy = False
        def command(*args, **kwargs):
            nonlocal healthy
            if args[0] == "mount" and args[-1] == TARGET:
                healthy = True
            return Mock(stdout="", returncode=0)
        self.recovery.run.side_effect = command
        self.recovery.probe.side_effect = lambda p: 116 if p == TARGET and not healthy else 0
        self.recovery.repair(TARGET, SOURCE, [CONTAINER])
        commands = self.commands()
        self.assertEqual("mount", commands[0][0])
        self.assertNotEqual(TARGET, commands[0][-1])
        self.assertEqual(("umount", TARGET), commands[1])
        self.assertEqual(("mount", "-t", "nfs", "-o", INFO["options"], SOURCE, TARGET), commands[2])
        restored = module.Recovery({TARGET: SOURCE}, self.tmp.name)
        self.assertIn(CONTAINER["Id"], restored.pending)
        self.recovery.refresh_containers()
        self.assertIn(("docker", "restart", "--time", "20", CONTAINER["Id"]), self.commands())
        self.assertEqual({}, self.recovery.pending)

    def test_unreachable_fresh_export_leaves_original_alone(self):
        self.stale()
        self.recovery.run.side_effect = subprocess.TimeoutExpired("mount", 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.recovery.repair(TARGET, SOURCE, [CONTAINER])
        self.assertNotIn(("umount", TARGET), self.commands())
        self.assertEqual({}, self.recovery.pending)

    def test_missing_bind_subdirectory_leaves_original_alone(self):
        self.stale()
        self.recovery.probe.side_effect = lambda p: 116 if p == TARGET else 1 if p.endswith("/movies") else 0
        container = {**CONTAINER, "Mounts": [{"Type": "bind", "Source": TARGET + "/movies"}]}
        with self.assertRaisesRegex(RuntimeError, "bind source"):
            self.recovery.repair(TARGET, SOURCE, [container])
        self.assertNotIn(("umount", TARGET), self.commands())

    def test_nonstale_error_does_not_remount(self):
        self.recovery.probe.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "without ESTALE"):
            self.recovery.cycle()
        self.assertEqual([], self.commands())

    def test_wrong_source_refused(self):
        self.recovery.mount_info.side_effect = None
        self.recovery.mount_info.return_value = {**INFO, "source": "wrong:/share"}
        self.stale()
        with self.assertRaisesRegex(RuntimeError, "fstab"):
            self.recovery.repair(TARGET, SOURCE, [CONTAINER])
        self.assertEqual([], self.commands())

    def test_busy_mount_never_force_unmounted(self):
        self.stale()
        def command(*args, **kwargs):
            if args == ("umount", TARGET):
                raise RuntimeError("busy")
            return Mock(stdout="", returncode=0)
        self.recovery.run.side_effect = command
        with self.assertRaisesRegex(RuntimeError, "busy"):
            self.recovery.repair(TARGET, SOURCE, [CONTAINER])
        self.assertTrue(self.recovery.pending)
        self.assertFalse(any("-f" in c or "-l" in c for c in self.commands()))

    def test_failed_restart_retried_next_cycle(self):
        self.recovery.remember([CONTAINER], TARGET)
        self.recovery.run.side_effect = RuntimeError("restart failed")
        with self.assertRaisesRegex(RuntimeError, "restart failed"):
            self.recovery.refresh_containers()
        self.assertTrue(self.recovery.pending)
        self.recovery.run.side_effect = None
        self.recovery.refresh_containers()
        self.assertEqual({}, self.recovery.pending)

    def test_stopped_container_not_started(self):
        self.recovery.remember([CONTAINER], TARGET)
        self.recovery.containers.return_value = []
        self.recovery.refresh_containers()
        self.assertEqual([], self.commands())
        self.assertEqual({}, self.recovery.pending)

    def test_pending_consumer_waits_for_all_mounts(self):
        self.recovery.remember([CONTAINER], TARGET)
        self.recovery.probe.return_value = 116
        self.recovery.refresh_containers()
        self.assertEqual([], self.commands())
        self.assertTrue(self.recovery.pending)

    def test_missing_mount_restored_without_unmount(self):
        self.recovery.mount_info.side_effect = lambda p, fstab=False: INFO if fstab else None
        self.recovery.repair(TARGET, SOURCE, [])
        self.assertNotIn(("umount", TARGET), self.commands())
        self.assertTrue(any(c[0] == "mount" and c[-1] == TARGET for c in self.commands()))


if __name__ == "__main__":
    unittest.main()
