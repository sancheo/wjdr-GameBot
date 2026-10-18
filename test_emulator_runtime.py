"""AVD enumeration and launch prerequisites without touching real emulators."""

import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import call, patch

import emulator_runtime as runtime


class EmulatorRuntimeTest(unittest.TestCase):







    def discover(self, devices="List of devices attached\n", properties=None):
        def run(command, **kwargs):
            if command[1:3] == ["devices", "-l"]:
                return subprocess.CompletedProcess(command, 0, devices)
            return subprocess.CompletedProcess(command, 0, (properties or {}).get(tuple(command[1:]), ""))
        with patch.object(runtime.subprocess, "run", side_effect=run), patch.object(runtime, "nox_endpoints", return_value={}):
            return runtime.discover_emulators()

    def test_android_studio_emulators_are_excluded(self):
        found, error = self.discover(devices="List of devices attached\nemulator-5554 device model:Pixel_5\nemulator-5556 offline\n")
        self.assertEqual(found, [])
        self.assertFalse(error)

    def test_nox_is_named_and_classified_without_qemu_property(self):
        serial = "127.0.0.1:62001"
        with patch.object(runtime, "nox_endpoints", return_value={serial: "夜神模拟器 (nox)"}), patch.object(runtime, "connect_nox"), patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, f"List of devices attached\n{serial} device model:SM_N976N\n")):
            found, error = runtime.discover_emulators()
        self.assertEqual(found[0]["kind"], "emulator")
        self.assertEqual(found[0]["name"], "夜神模拟器 (nox)")
        self.assertEqual(found[0]["status"], "running")

    def test_usb_phone_is_listed_by_model_with_stable_serial_key(self):
        found, error = self.discover(devices="List of devices attached\nPHONE123 device usb:1-2 model:Mi_11 device:venus transport_id:3\n")
        self.assertFalse(error)
        self.assertEqual(found[-1], {"id": "serial:PHONE123", "key": "serial:PHONE123",
                                    "name": "Mi 11", "serial": "PHONE123", "avd": "",
                                    "status": "running", "kind": "physical"})

    def test_nox_stale_offline_transport_is_stopped_when_port_is_closed(self):
        serial = "127.0.0.1:62001"
        for reachable, expected in [(set(), "stopped"), ({serial}, "offline")]:
            with self.subTest(reachable=reachable), \
                    patch.object(runtime, "nox_endpoints", return_value={serial: "夜神模拟器 (nox)"}), \
                    patch.object(runtime, "connect_nox", return_value=reachable), \
                    patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, f"List of devices attached\n{serial} offline\n")):
                found, error = runtime.discover_emulators()
            self.assertFalse(error)
            self.assertEqual(found[0]["status"], expected)
            self.assertEqual(found[0]["kind"], "emulator")

    def test_phone_authorization_and_offline_states_do_not_block_avd_launch(self):
        found, error = self.discover(devices="List of devices attached\nPHONE123 unauthorized usb:1-2\nPHONE456 offline usb:1-3\n")
        self.assertFalse(error)
        self.assertEqual([item["status"] for item in found], ["unauthorized", "offline"])
        self.assertEqual([item["kind"] for item in found[-2:]], ["physical", "physical"])
        self.assertEqual(found[-2]["name"], "安卓设备")


    def test_third_party_emulator_remains_an_emulator(self):
        serial = "127.0.0.1:62001"
        for prop in ("ro.kernel.qemu", "ro.boot.qemu"):
            with self.subTest(prop=prop):
                found, error = self.discover(devices=f"List of devices attached\n{serial} device model:Nox_Player\n",
                                             properties={("-s", serial, "shell", "getprop", prop): "1"})
                self.assertFalse(error)
                self.assertEqual(found[-1]["kind"], "emulator")
                self.assertEqual(found[-1]["status"], "running")

    def test_nox_connects_only_when_local_port_is_open(self):
        endpoints = {"127.0.0.1:62001": "夜神模拟器 (nox)"}
        with patch.object(runtime.socket, "create_connection") as probe, \
                patch.object(runtime, "adb_output") as adb:
            self.assertEqual(runtime.connect_nox(endpoints), set(endpoints))
        probe.assert_called_once_with(("127.0.0.1", 62001), timeout=0.2)
        adb.assert_called_once_with("connect", "127.0.0.1:62001", timeout=2)
        with patch.object(runtime.socket, "create_connection", side_effect=ConnectionRefusedError), \
                patch.object(runtime, "adb_output") as adb:
            self.assertEqual(runtime.connect_nox(endpoints), set())
        adb.assert_not_called()

    def test_nox_open_port_remains_reachable_when_adb_connect_fails(self):
        endpoints = {"127.0.0.1:62001": "夜神模拟器 (nox)"}
        with patch.object(runtime.socket, "create_connection"), \
                patch.object(runtime, "adb_output", side_effect=subprocess.TimeoutExpired("adb", 2)):
            self.assertEqual(runtime.connect_nox(endpoints), set(endpoints))

    def test_phone_metadata_failure_retains_adb_device(self):
        with patch.object(runtime, "adb_output", side_effect=OSError("disconnected")):
            found, error = self.discover(devices="List of devices attached\nPHONE123 device model:Pixel_7\n")
        self.assertFalse(error)
        self.assertEqual(found[-1]["serial"], "PHONE123")

    def test_phone_readiness_checks_transport_without_emulator_commands(self):
        with patch.object(runtime, "adb_output", return_value="device") as adb:
            self.assertTrue(runtime.device_ready("PHONE123"))
        adb.assert_called_once_with("-s", "PHONE123", "get-state")








if __name__ == "__main__":
    unittest.main()
