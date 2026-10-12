"""AVD enumeration and launch prerequisites without touching real emulators."""

import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import call, patch

import emulator_runtime as runtime


class EmulatorRuntimeTest(unittest.TestCase):
    def test_resolver_finds_windows_default_sdk(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/Local"}, clear=True), \
                patch.object(runtime.sys, "platform", "win32"), \
                patch.object(Path, "is_file", return_value=True):
            self.assertEqual(runtime.resolve_emulator(), Path("C:/Local/Android/Sdk/emulator/emulator.exe"))

    def test_resolver_prefers_configured_sdk_and_can_use_adb_sibling(self):
        with patch.dict(os.environ, {"ANDROID_HOME": "D:/SDK", "ANDROID_SDK_ROOT": "E:/SDK"}, clear=True), \
                patch.object(runtime.sys, "platform", "win32"), \
                patch.object(Path, "is_file", return_value=True):
            self.assertEqual(runtime.resolve_emulator(), Path("D:/SDK/emulator/emulator.exe"))
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(runtime.sys, "platform", "win32"), \
                patch.object(runtime, "ADB", Path("D:/SDK/platform-tools/adb.exe")), \
                patch.object(Path, "is_file", return_value=True):
            self.assertEqual(runtime.resolve_emulator(), Path("D:/SDK/emulator/emulator.exe"))

    def discover(self, avds="Pixel_5\nPixel_5_2\n", devices="List of devices attached\n", properties=None,
                 avd_error=None, adb_error=None):
        def run(command, **kwargs):
            if command[-1] == "-list-avds":
                if avd_error:
                    raise avd_error
                result = avds
            elif command[1:3] == ["devices", "-l"]:
                if adb_error:
                    raise adb_error
                result = devices
            else:
                result = (properties or {}).get(tuple(command[1:]), "")
            return subprocess.CompletedProcess(command, 0, result)
        with patch.object(runtime.subprocess, "run", side_effect=run), \
                patch.object(runtime, "resolve_emulator", return_value=Path("sdk/emulator.exe")):
            return runtime.discover_emulators()

    def test_lists_stopped_avds_even_without_connected_devices(self):
        found, error = self.discover()
        self.assertFalse(error)
        self.assertEqual([(item["name"], item["status"], item["serial"]) for item in found],
                         [("Pixel_5", "stopped", ""), ("Pixel_5_2", "stopped", "")])

    def test_boot_incomplete_is_starting_and_anonymous_offline_prevents_duplicate_launch(self):
        props = {("-s", "emulator-5554", "emu", "avd", "name"): "Pixel_5\nOK\n",
                 ("-s", "emulator-5554", "get-state"): "device",
                 ("-s", "emulator-5554", "shell", "getprop", "sys.boot_completed"): "0"}
        found, error = self.discover(devices="List of devices attached\nemulator-5554 device\n", properties=props)
        self.assertFalse(error)
        self.assertEqual(found[0]["status"], "starting")
        self.assertEqual(found[1]["status"], "stopped")
        found, error = self.discover(devices="List of devices attached\nemulator-5554 offline\n")
        self.assertIn("正在连接", error)
        self.assertEqual([item["status"] for item in found], ["unknown", "unknown", "starting"])

    def test_partial_failure_keeps_installed_or_connected_emulators(self):
        found, error = self.discover(adb_error=OSError("no adb"))
        self.assertIn("连接状态失败", error)
        self.assertEqual([item["status"] for item in found], ["unknown", "unknown"])
        props = {("-s", "emulator-5554", "emu", "avd", "name"): "Pixel_5\nOK\n",
                 ("-s", "emulator-5554", "get-state"): "device",
                 ("-s", "emulator-5554", "shell", "getprop", "sys.boot_completed"): "1"}
        found, error = self.discover(avd_error=OSError("no emulator"), properties=props,
                                     devices="List of devices attached\nemulator-5554 device\n")
        self.assertIn("AVD 列表失败", error)
        self.assertEqual([(item["name"], item["status"]) for item in found], [("Pixel_5", "running")])

    def test_readiness_requires_boot_completion_and_handles_disconnection(self):
        with patch.object(runtime, "adb_output", side_effect=["device", "1"]):
            self.assertTrue(runtime.device_ready("emulator-5554", avd=True))
        with patch.object(runtime, "adb_output", side_effect=["device", "0"]):
            self.assertFalse(runtime.device_ready("emulator-5554", avd=True))
        with patch.object(runtime, "adb_output", side_effect=OSError("disconnected")):
            self.assertFalse(runtime.device_ready("emulator-5554", avd=True))

    def test_stop_waits_for_transport_and_console_before_returning(self):
        outputs = ["OK", "List of devices attached\nemulator-5554 offline\n",
                   "List of devices attached\n", "List of devices attached\n"]
        with patch.object(runtime, "avd_key", return_value="avd:Pixel_5"), \
                patch.object(runtime, "adb_output", side_effect=outputs) as adb, \
                patch.object(runtime, "emulator_console_open", side_effect=[True, False]) as console, \
                patch.object(runtime.time, "sleep") as sleep:
            runtime.stop_emulator("emulator-5554", "Pixel_5")
        self.assertEqual(adb.call_args_list, [call("-s", "emulator-5554", "emu", "kill"),
                                            call("devices"), call("devices"), call("devices")])
        self.assertEqual(console.call_count, 2)
        console.assert_called_with(5554)
        self.assertEqual(sleep.call_count, 2)

    def test_windows_shutdown_waits_for_listener_and_ignores_connection_timeouts(self):
        listeners = "  TCP    127.0.0.1:5554    0.0.0.0:0    LISTENING    123\n"
        # TIME_WAIT and connections to this remote port are not local listeners.
        closed = ("  TCP    127.0.0.1:5554    127.0.0.1:64000    TIME_WAIT    0\n"
                  "  TCP    127.0.0.1:64000    127.0.0.1:5554    ESTABLISHED    456\n"
                  "  TCP    [::1]:5556    [::]:0    LISTENING    789\n")
        with patch.object(runtime.sys, "platform", "win32"), \
                patch.object(runtime, "avd_key", return_value="avd:Pixel_5"), \
                patch.object(runtime, "adb_output", side_effect=["OK", "List of devices attached\n", "List of devices attached\n"]), \
                patch.object(runtime.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, listeners),
                                                                      subprocess.CompletedProcess([], 0, closed)]) as netstat, \
                patch.object(runtime.socket, "create_connection", side_effect=TimeoutError("timed out")) as socket_probe, \
                patch.object(runtime.time, "sleep") as sleep:
            runtime.stop_emulator("emulator-5554", "Pixel_5")
        socket_probe.assert_not_called()
        self.assertEqual(netstat.call_count, 2)
        self.assertEqual(netstat.call_args.args[0][1:], ["-ano", "-p", "tcp"])
        sleep.assert_called_once_with(0.5)

    def test_windows_listener_query_failure_cannot_confirm_shutdown(self):
        with patch.object(runtime.sys, "platform", "win32"), \
                patch.object(runtime.subprocess, "run", side_effect=subprocess.TimeoutExpired("netstat", 4)):
            with self.assertRaisesRegex(RuntimeError, "无法确认模拟器控制台端口"):
                runtime.emulator_console_open(5554)

    def test_socket_timeout_retries_until_refused_or_overall_deadline(self):
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime, "avd_key", return_value="avd:Pixel_5"), \
                patch.object(runtime, "adb_output", return_value="List of devices attached\n"), \
                patch.object(runtime.socket, "create_connection", side_effect=[TimeoutError("timed out"), ConnectionRefusedError()]) as probe, \
                patch.object(runtime.time, "sleep") as sleep:
            runtime.stop_emulator("emulator-5554", "Pixel_5")
        self.assertEqual(probe.call_count, 2)
        sleep.assert_called_once_with(0.5)
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime, "avd_key", return_value="avd:Pixel_5"), \
                patch.object(runtime, "adb_output", return_value="List of devices attached\n"), \
                patch.object(runtime.socket, "create_connection", side_effect=TimeoutError("timed out")), \
                patch.object(runtime.time, "monotonic", side_effect=[0, 1, 31]), \
                patch.object(runtime.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "等待模拟器关闭超时"):
                runtime.stop_emulator("emulator-5554", "Pixel_5")

    def test_stop_rejects_reused_serial_and_console_failure(self):
        with patch.object(runtime, "avd_key", return_value="avd:Other"), \
                patch.object(runtime, "adb_output") as adb:
            with self.assertRaisesRegex(ValueError, "连接标识已变化"):
                runtime.stop_emulator("emulator-5554", "Pixel_5")
            adb.assert_not_called()
        with patch.object(runtime, "avd_key", return_value="avd:Pixel_5"), \
                patch.object(runtime, "adb_output", return_value="KO: cannot kill"):
            with self.assertRaisesRegex(RuntimeError, "关闭模拟器失败"):
                runtime.stop_emulator("emulator-5554", "Pixel_5")

    def test_stop_timeout_and_adb_failure_do_not_confirm_shutdown(self):
        with patch.object(runtime, "avd_key", return_value="avd:Pixel_5"), \
                patch.object(runtime, "adb_output", return_value="OK"), \
                patch.object(runtime.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(RuntimeError, "关闭超时"):
                runtime.stop_emulator("emulator-5554", "Pixel_5")
        with patch.object(runtime, "avd_key", return_value="avd:Pixel_5"), \
                patch.object(runtime, "adb_output", side_effect=["OK", OSError("ADB failed")]):
            with self.assertRaises(OSError):
                runtime.stop_emulator("emulator-5554", "Pixel_5")


if __name__ == "__main__":
    unittest.main()
