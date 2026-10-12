"""Cross-process exclusion and automatic recovery after process termination."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from device_lease import DeviceLease
from platform_support import terminate_task_process


class DeviceLeaseTest(unittest.TestCase):
    def test_avd_survives_port_changes_and_serial_alias_cannot_bypass_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            with DeviceLease("emulator-5554", "avd:A", directory):
                for serial, identity in (("emulator-5556", "avd:A"), ("emulator-5554", None)):
                    with self.assertRaisesRegex(RuntimeError, "已有 GameBot"):
                        DeviceLease(serial, identity, directory).acquire()
                with DeviceLease("emulator-5558", "avd:B", directory):
                    pass
            with DeviceLease("emulator-5556", "avd:A", directory):
                pass

    def test_child_exit_releases_lock_without_stale_file_cleanup(self):
        code = "from device_lease import DeviceLease; import sys; " \
               "lease=DeviceLease('child','avd:A',sys.argv[1]).acquire(); " \
               "print('ready',flush=True); sys.stdin.read()"
        with tempfile.TemporaryDirectory() as directory:
            child = subprocess.Popen([sys.executable, "-u", "-c", code, directory],
                                     cwd=Path(__file__).resolve().parent,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8")
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                with self.assertRaises(RuntimeError):
                    DeviceLease("parent", "avd:A", directory).acquire()
                terminate_task_process(child)
                child.wait(timeout=5)
                with DeviceLease("parent", "avd:A", directory):
                    pass
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)

    def test_failed_acquisition_releases_all_previously_acquired_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            with DeviceLease("shared", None, directory):
                lease = DeviceLease("shared", "avd:unique", directory)
                with self.assertRaises(RuntimeError):
                    lease.acquire()
                self.assertEqual(lease.handles, [])
                with DeviceLease("different", "avd:unique", directory):
                    pass
