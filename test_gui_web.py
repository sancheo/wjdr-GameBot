"""Desktop bridge regressions without operating an emulator."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from emulator_config import load_config, save_config
import gui_web as desktop
import emulator_runtime


def inventory(*rows):
    return ([{"id": serial, "key": "avd:" + serial, "name": name, "avd": name,
              "serial": serial, "status": "running"} for serial, name in rows], "")


class DesktopBackendTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        lease_type = desktop.DeviceLease
        leases = patch.object(desktop, "DeviceLease", side_effect=lambda serial, identity=None:
                              lease_type(serial, identity, directory=Path(self.directory.name) / "leases"))
        leases.start()
        self.addCleanup(leases.stop)
        self.api = desktop.DesktopApi(self.directory.name)
        self.addCleanup(self.api._close)
        ready = patch.object(desktop, "device_ready", return_value=True)
        ready.start()
        self.addCleanup(ready.stop)
        identity = patch.object(desktop, "emulator_config_key", side_effect=lambda serial: "avd:" + serial)
        identity.start()
        self.addCleanup(identity.stop)

    def devices(self):
        with patch.object(desktop, "discover_emulators", return_value=inventory(("emulator-5554", "A"), ("emulator-5556", "B"))):
            self.api._scan()

    def test_discovery_merges_installed_avds_and_connected_emulators(self):
        devices = "List of devices attached\nemulator-5554 device model:Pixel_5\n" \
                  "127.0.0.1:62001 device model:Nox_Player\nother-device device\nemulator-5556 offline\n"

        def fake_run(command, **kwargs):
            if command[-1] == "-list-avds":
                return subprocess.CompletedProcess(command, 0, "Pixel_5\nPixel_5_2\n")
            if command[1:3] == ["devices", "-l"]:
                return subprocess.CompletedProcess(command, 0, devices)
            if command[-3:] == ["emu", "avd", "name"]:
                return subprocess.CompletedProcess(command, 0, "Pixel_5\nOK\n" if command[2] == "emulator-5554" else "Pixel_5_2\nOK\n")
            if command[-1] == "get-state":
                return subprocess.CompletedProcess(command, 0, "device\n")
            if command[-1] == "sys.boot_completed":
                return subprocess.CompletedProcess(command, 0, "1\n")
            return subprocess.CompletedProcess(command, 0, "1\n" if command[2] == "127.0.0.1:62001" else "0\n")

        with patch.object(desktop.subprocess, "run", side_effect=fake_run), \
                patch.object(emulator_runtime, "resolve_emulator", return_value=Path("sdk/emulator.exe")):
            found, error = desktop.discover_emulators()
        self.assertFalse(error)
        self.assertEqual([(item["id"], item["status"]) for item in found], [
            ("avd:Pixel_5", "running"), ("avd:Pixel_5_2", "starting"),
            ("serial:127.0.0.1:62001", "running")])
        self.assertEqual(found[0]["serial"], "emulator-5554")

    def test_avd_name_is_stable_and_serial_is_fallback(self):
        with patch.object(desktop.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "Pixel_5\nOK\n")
            self.assertEqual(emulator_runtime.avd_key("emulator-5554"), "avd:Pixel_5")
            self.assertEqual(emulator_runtime.avd_key("emulator-5556"), "avd:Pixel_5")
            self.assertEqual(run.call_args.args[0][-3:], ["emu", "avd", "name"])
            run.reset_mock()
            self.assertEqual(emulator_runtime.avd_key("127.0.0.1:62001"), "serial:127.0.0.1:62001")
            run.assert_not_called()
            run.side_effect = subprocess.TimeoutExpired("adb", 4)
            self.assertEqual(emulator_runtime.avd_key("emulator-5554"), "serial:emulator-5554")

    def test_corrupt_saved_config_falls_back_with_visible_warning(self):
        for contents in ["{broken", "[]", '{"version": 99}']:
            with self.subTest(contents=contents):
                path = Path(self.directory.name) / "configs"
                path.mkdir(exist_ok=True)
                from emulator_config import config_path
                config_path(self.directory.name, "avd:emulator-5554").write_text(contents, encoding="utf-8")
                self.api = desktop.DesktopApi(self.directory.name)
                self.devices()
                state = self.api.get_state()
                self.assertEqual(state["config"], desktop.default_config())
                self.assertTrue(any("配置读取失败" in line["text"] for line in state["logs"]))

    def test_launch_failure_never_saves_and_save_failure_does_not_interrupt_task(self):
        self.devices()
        with patch.object(desktop.subprocess, "Popen", side_effect=OSError("failed")), \
                patch.object(desktop, "save_config") as save:
            with self.assertRaises(OSError):
                self.api.start(desktop.default_config())
            save.assert_not_called()
            self.assertFalse(self.api.get_state()["running"])
        process = Mock(stdout=io.StringIO("任务结束\n"))
        process.wait.return_value = 0
        with patch.object(desktop.subprocess, "Popen", return_value=process), \
                patch.object(desktop, "save_config", side_effect=PermissionError("read only")):
            self.api.start(desktop.default_config())
            self.api._reader.join(timeout=2)
        self.assertFalse(self.api.get_state()["running"])
        self.assertTrue(any("配置保存失败" in line["text"] for line in self.api.get_state()["logs"]))

    def test_command_preserves_task_options_and_per_resource_levels(self):
        config = desktop.default_config()
        config["options"].update(help=False, upgrade=True, spear=False, bounty=True, wood=False)
        config["entries"].update(tree_threshold="1250", stamina_threshold="40", reconnect_minutes="0.5")
        config["resource_levels"]["iron"] = "8级矿"
        command = desktop.build_command(config, "emulator-5554")
        self.assertEqual(command[:5], [sys.executable, "-X", "utf8", "-u", str(desktop.ROOT / "help_bot.py")])
        self.assertIn("--no-help", command)
        self.assertIn("--no-spear", command)
        self.assertIn("--upgrade", command)
        self.assertIn("--bounty", command)
        for flag, value in [("--device", "emulator-5554"), ("--tree-threshold", "1250"),
                            ("--stamina-threshold", "40"), ("--reconnect-minutes", "0.5"), ("--iron-level", "8")]:
            self.assertEqual(command[command.index(flag) + 1], value)
        start = command.index("--resources")
        self.assertEqual(command[start + 1:start + 4], ["meat", "coal", "iron"])
        self.assertNotIn("--wood-level", command)

    def test_help_clicker_defaults_off_and_old_settings_remain_compatible(self):
        config = desktop.default_config()
        self.assertFalse(config["options"].pop("help_clicker"))
        restored = desktop.checked_config(config, from_disk=True)
        self.assertFalse(restored["options"]["help_clicker"])
        self.assertTrue(restored["options"]["help"])

    def test_task_rewards_default_legacy_config_and_disable_flag(self):
        config = desktop.default_config()
        self.assertTrue(config["options"].pop("task_rewards"))
        restored = desktop.checked_config(config, from_disk=True)
        self.assertTrue(restored["options"]["task_rewards"])
        self.assertNotIn("--no-task-rewards", desktop.build_command(restored, "emulator-test"))
        restored["options"]["task_rewards"] = False
        self.assertIn("--no-task-rewards", desktop.build_command(restored, "emulator-test"))

    def test_bear_defaults_and_legacy_config_upgrade(self):
        config = desktop.default_config()
        self.assertFalse(config["options"].pop("bear"))
        self.assertFalse(config["options"].pop("bear_drive"))
        self.assertTrue(config["options"].pop("bear_follow"))
        restored = desktop.checked_config(config, from_disk=True)
        self.assertFalse(restored["options"]["bear"])
        self.assertFalse(restored["options"]["bear_drive"])
        self.assertTrue(restored["options"]["bear_follow"])

    def test_bear_modes_saved_per_emulator_and_other_preferences_preserved(self):
        self.devices()
        config = desktop.default_config()
        config["options"].update(bear=True, bear_drive=True, bear_follow=False, train=False)
        self.api.update_config(config)
        restored = desktop.DesktopApi(self.directory.name)
        self.addCleanup(restored._close)
        restored._keys = {"emulator-5554": "avd:emulator-5554"}
        restored._choose("emulator-5554")
        self.assertEqual(restored.get_state()["config"]["options"], config["options"])
        self.api.select_device("emulator-5556", config)
        self.assertFalse(self.api.get_state()["config"]["options"]["bear"])
        self.assertTrue(self.api.get_state()["config"]["options"]["bear_follow"])

    def test_bear_never_launches_daily_tasks_before_runtime_is_connected(self):
        self.devices()
        config = desktop.default_config()
        config["options"].update(bear=True, beast=False, polar_beast=False)
        config["hunt_levels"]["beast"] = "invalid"
        self.assertTrue(desktop.checked_config(config)["options"]["bear"])
        with patch.object(desktop.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValueError, "自动打熊执行流程尚未接入"):
                self.api.start(config)
        launch.assert_not_called()
        config["options"]["help_clicker"] = True
        with self.assertRaisesRegex(ValueError, "不能同时开启"):
            desktop.checked_config(config)

    def test_legacy_saved_help_clicker_is_ignored_but_live_confirmation_is_kept(self):
        config = desktop.default_config()
        config["options"].update(help_clicker=True, train=False)
        self.assertFalse(desktop.checked_config(config, from_disk=True)["options"]["help_clicker"])
        self.assertTrue(desktop.checked_config(config)["options"]["help_clicker"])
        self.assertFalse(desktop.checked_config(config, from_disk=True)["options"]["train"])

    def test_help_clicker_command_excludes_other_tasks_and_keeps_reconnect(self):
        config = desktop.default_config()
        config["options"].update(help_clicker=True, upgrade=True, bounty=True)
        config["entries"].update(tree_threshold="invalid", stamina_threshold="invalid", reconnect_minutes="0.5")
        command = desktop.build_command(config, "emulator-5554")
        self.assertEqual(command[5:], ["--device", "emulator-5554", "--forever", "--control",
                                     "--reconnect-minutes", "0.5", "--help-clicker"])
        config["options"]["reconnect"] = False
        config["entries"]["reconnect_minutes"] = "invalid"
        command = desktop.build_command(config, "emulator-5554")
        self.assertEqual(command[5:], ["--device", "emulator-5554", "--forever", "--control",
                                     "--no-reconnect", "--help-clicker"])

    def test_help_clicker_launch_and_saved_config_ignore_inactive_task_validation(self):
        self.devices()
        config = desktop.default_config()
        config["options"].update(help_clicker=True, beast=False, polar_beast=False)
        config["entries"].update(tree_threshold="invalid", stamina_threshold="invalid")
        config["resource_levels"]["meat"] = "invalid"
        config["hunt_levels"]["beast"] = "invalid"
        process = Mock(stdout=io.StringIO(""))
        process.wait.return_value = 0
        with patch.object(desktop.subprocess, "Popen", return_value=process) as launch:
            self.api.start(config)
            self.api._reader.join(timeout=2)
        self.assertIn("--help-clicker", launch.call_args.args[0])
        saved = load_config(self.directory.name, "avd:emulator-5554")
        self.assertNotIn("help_clicker", saved["options"])
        self.assertTrue(self.api.get_state()["config"]["options"]["help_clicker"])
        self.assertFalse(saved["options"]["beast"])
        logs = [line["text"] for line in self.api.get_state()["logs"]]
        self.assertTrue(any("小号模式" in line for line in logs))
        self.assertFalse(any("自动打怪配置" in line or "资源采集配置" in line for line in logs))
        restored = desktop.DesktopApi(self.directory.name)
        self.addCleanup(restored._close)
        restored._keys = {"emulator-5554": "avd:emulator-5554"}
        restored._choose("emulator-5554")
        self.assertFalse(restored.get_state()["config"]["options"]["help_clicker"])

    def test_help_clicker_keeps_reconnect_validation(self):
        self.devices()
        config = desktop.default_config()
        config["options"]["help_clicker"] = True
        for value in ["0", "-1", "nan", "inf", "invalid"]:
            with self.subTest(value=value), patch.object(desktop.subprocess, "Popen") as launch:
                config["entries"]["reconnect_minutes"] = value
                with self.assertRaisesRegex(ValueError, "重连等待时间"):
                    self.api.start(config)
                launch.assert_not_called()

    def test_invalid_configuration_never_spawns_a_task(self):
        self.devices()
        for entry, value in [("tree_threshold", "0"), ("stamina_threshold", "-1"),
                             ("reconnect_minutes", "nan"), ("reconnect_minutes", "inf")]:
            config = desktop.default_config()
            config["entries"][entry] = value
            with self.subTest(entry=entry, value=value), patch.object(desktop.subprocess, "Popen") as process:
                with self.assertRaises(ValueError):
                    self.api.start(config)
                process.assert_not_called()
        config = desktop.default_config()
        for name, _ in desktop.RESOURCES:
            config["options"][name] = False
        with self.assertRaisesRegex(ValueError, "资源"):
            desktop.build_command(config, "emulator-5554")
        self.api._close()
        with self.assertRaisesRegex(ValueError, "关闭"):
            self.api.start(desktop.default_config())

    def test_fourth_level_reaches_launch_and_saved_config_for_each_resource(self):
        self.devices()
        for resource, _ in desktop.RESOURCES:
            with self.subTest(resource=resource):
                config = desktop.default_config()
                config["options"].update({name: name == resource for name, _ in desktop.RESOURCES})
                config["resource_levels"][resource] = "4级矿"
                self.api.update_config(config)
                process = Mock(stdout=io.StringIO(""))
                process.wait.return_value = 0
                with patch.object(desktop.subprocess, "Popen", return_value=process) as launch:
                    self.api.start(self.api.get_state()["config"])
                    self.api._reader.join(timeout=2)
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index(f"--{resource}-level") + 1], "4")
                saved = load_config(self.directory.name, "avd:emulator-5554")
                self.assertEqual(saved["resource_levels"][resource], "4级矿")
                restored = desktop.DesktopApi(self.directory.name)
                restored._keys = {"emulator-5554": "avd:emulator-5554"}
                restored._choose("emulator-5554")
                self.assertEqual(restored.get_state()["config"]["resource_levels"][resource], "4级矿")
                self.assertTrue(any(f"{dict(desktop.RESOURCES)[resource]} 4级矿" in line["text"]
                                    for line in self.api.get_state()["logs"]))

    def test_invalid_bridge_level_does_not_silently_launch_level_one(self):
        self.devices()
        for invalid in ("4", 4, "4级", None):
            with self.subTest(invalid=invalid):
                config = desktop.default_config()
                config["resource_levels"]["meat"] = invalid
                with patch.object(desktop.subprocess, "Popen") as launch:
                    with self.assertRaisesRegex(ValueError, "资源采集等级"):
                        self.api.start(config)
                    launch.assert_not_called()
                self.assertEqual(desktop.checked_config(config, from_disk=True)["resource_levels"]["meat"],
                                 "1级矿")

    def test_emulators_keep_independent_settings_and_stable_identity(self):
        config_b = desktop.default_config()
        config_b["entries"]["tree_threshold"] = "2500"
        save_config(self.directory.name, "avd:emulator-5556", "emulator-5556", config_b)
        self.devices()
        config_a = self.api.get_state()["config"]
        config_a["entries"]["tree_threshold"] = "1500"
        state = self.api.select_device("emulator-5556", config_a)
        self.assertEqual(state["config"]["entries"]["tree_threshold"], "2500")
        state = self.api.select_device("emulator-5554", state["config"])
        self.assertEqual(state["config"]["entries"]["tree_threshold"], "1500")
        changed = inventory(("emulator-5560", "A"))
        changed[0][0]["key"] = "avd:emulator-5554"
        with patch.object(desktop, "discover_emulators", return_value=changed):
            self.api._scan()
        state = self.api.get_state()
        self.assertEqual(state["selected"], "emulator-5560")
        self.assertEqual(state["config"]["entries"]["tree_threshold"], "1500")

    def test_corrupted_saved_numbers_restore_defaults(self):
        config = desktop.default_config()
        config["entries"].update(tree_threshold="0", reconnect_minutes="nan", stamina_threshold="-1")
        config["options"].update({name: False for name, _ in desktop.RESOURCES})
        checked = desktop.checked_config(config, from_disk=True)
        self.assertEqual(checked["entries"], desktop.default_config()["entries"])
        self.assertTrue(all(checked["options"][name] for name, _ in desktop.RESOURCES))

    def test_logs_are_bounded_incremental_and_state_is_a_copy(self):
        for index in range(2010):
            self.api._append(f"日志 {index}")
        state = self.api.get_state(0)
        self.assertTrue(state["log_reset"])
        self.assertEqual(len(state["logs"]), 2000)
        self.assertEqual(self.api.get_state(state["last_log"])["logs"], [])
        state["config"]["entries"]["tree_threshold"] = "999"
        self.assertEqual(self.api.get_state()["config"]["entries"]["tree_threshold"], "1000")
        self.api._append("⚠️ 当前任务暂停")
        delta = self.api.get_state(state["last_log"])
        self.assertFalse(delta["log_reset"])
        self.assertEqual(len(delta["logs"]), 1)
        self.assertTrue(delta["logs"][0]["error"])

    def test_start_saves_config_drains_final_output_and_clears_running(self):
        self.devices()
        process = Mock(stdout=io.StringIO("正常日志\nValueError: 最后一条异常\n"))
        process.wait.return_value = 1
        with patch.object(desktop.subprocess, "Popen", return_value=process):
            self.api.start(desktop.default_config())
            self.api._reader.join(timeout=2)
        self.assertFalse(self.api.get_state()["running"])
        self.assertEqual(self.api.get_state()["notice"]["message"], "ValueError: 最后一条异常")
        self.assertEqual(desktop.checked_config(load_config(self.directory.name, "avd:emulator-5554"),
                                               from_disk=True), desktop.default_config())
        self.assertTrue(process.stdout.closed)

    def test_reader_saves_ready_only_after_confirmed_navigation_and_clears_on_exit(self):
        messages = [
            ("小号模式互助连点已启用，其他任务已跳过；停止程序可结束连点", False),
            ("已进入联盟互助，持续快速点击底部全部帮助区域", True),
            ("启动时主城检查已通过，开始运行任务", True),
        ]
        runtime_path = Path(self.directory.name) / "configs" / "runtime" / f"desktop-{os.getpid()}.json"
        for message, expected_ready in messages:
            with self.subTest(message=message):
                process = Mock(stdout=io.StringIO(f"[2026-10-10 12:00:00] {message}\n"),
                               stdin=None, pid=12345)
                self.api._process = process
                self.api._active_key = "avd:emulator-5554"
                self.api._task_ready = False
                self.api._save_runtime()

                def inspect_live_runtime():
                    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
                    self.assertEqual(runtime["task_ready"], expected_ready)
                    self.assertTrue(runtime["running"])
                    self.assertEqual(runtime["task_pid"], 12345)
                    return 0

                process.wait.side_effect = inspect_live_runtime
                self.api._read_process(process)
                stopped = json.loads(runtime_path.read_text(encoding="utf-8"))
                self.assertFalse(stopped["task_ready"])
                self.assertFalse(stopped["running"])
                self.assertTrue(process.stdout.closed)

    def test_stop_is_idempotent_and_closed_window_terminates_task(self):
        self.devices()
        process = Mock()
        process.poll.return_value = None
        self.api._process = process
        self.api.stop()
        self.api.stop()
        process.terminate.assert_called_once()
        self.assertTrue(self.api.get_state()["stopping"])
        with self.assertRaises(ValueError):
            self.api.select_device("emulator-5556", desktop.default_config())
        with self.assertRaises(ValueError):
            self.api.start(desktop.default_config())
        self.api._process = None
        self.api._stopping = False
        self.api._process = Mock()
        self.api._process.poll.return_value = None
        self.api._close()
        self.api._process.terminate.assert_called_once()
        self.api._process = None

    def test_pipe_protocol_rejects_private_methods_and_closes_on_eof(self):
        requests = ["not json", json.dumps({"id": 1, "method": "_close"}),
                    json.dumps({"id": 2, "method": "get_state"}),
                    json.dumps({"id": 3, "method": "start", "args": [desktop.default_config()]})]
        output = io.StringIO()
        desktop.serve_bridge(self.api, io.StringIO("\n".join(requests) + "\n"), output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(replies), 4)
        self.assertIn("error", replies[0])
        self.assertIn("error", replies[1])
        self.assertFalse(replies[2]["result"]["running"])
        self.assertIn("模拟器", replies[3]["error"])
        self.assertTrue(self.api._closed)

    def stopped_device(self):
        device = {"id": "avd:Pixel_5", "key": "avd:Pixel_5", "name": "Pixel_5",
                  "avd": "Pixel_5", "serial": "", "status": "stopped"}
        with patch.object(desktop, "discover_emulators", return_value=([device], "")):
            self.api._scan()
        return device

    def test_stopped_and_booting_emulators_cannot_start_tasks(self):
        device = self.stopped_device()
        config = desktop.default_config()
        config["entries"]["tree_threshold"] = "1500"
        self.api.update_config(config)
        for status, message in [("stopped", "未启动"), ("starting", "正在启动"), ("unknown", "未连接")]:
            device["status"] = status
            with patch.object(desktop.subprocess, "Popen") as process:
                with self.assertRaisesRegex(ValueError, message):
                    self.api.start(config)
                process.assert_not_called()
        self.assertEqual(self.api.get_state()["config"], config)

    def test_emulator_launch_uses_requested_flags_and_rejects_duplicate_launch(self):
        self.stopped_device()
        process = Mock()
        process.poll.return_value = None
        with patch.object(desktop, "resolve_emulator", return_value=Path("sdk/emulator.exe")), \
                patch.object(desktop.subprocess, "Popen", return_value=process) as launch, \
                patch.object(self.api, "_monitor_launches"):
            state = self.api.launch_emulator("avd:Pixel_5")
            self.assertEqual(launch.call_args.args[0], [str(Path("sdk/emulator.exe")), "-avd", "Pixel_5", "-noaudio", "-no-snapshot-load"])
            self.assertEqual(state["devices"][0]["status"], "starting")
            self.assertFalse(state["running"])
            with self.assertRaisesRegex(ValueError, "正在启动"):
                self.api.launch_emulator("avd:Pixel_5")
            launch.assert_called_once()
        self.api._close()
        process.terminate.assert_not_called()

    def test_emulator_launch_failure_keeps_stopped_state(self):
        self.stopped_device()
        with patch.object(desktop.subprocess, "Popen", side_effect=OSError("missing emulator")):
            with self.assertRaises(OSError):
                self.api.launch_emulator("avd:Pixel_5")
        self.assertEqual(self.api.get_state()["devices"][0]["status"], "stopped")
        self.assertFalse(self.api._launches)

    def restarting_device(self):
        device = self.stopped_device()
        device.update(status="running", serial="emulator-5554")
        with patch.object(desktop.threading, "Thread"):
            state = self.api.restart_emulator(device["id"])
        self.assertEqual(state["devices"][0]["status"], "restarting")
        self.assertFalse(state["running"])
        return device

    def test_restart_closes_before_reusing_launch_command_and_preserves_config(self):
        device = self.restarting_device()
        config = desktop.default_config()
        config["entries"]["tree_threshold"] = "1700"
        self.api.update_config(config)
        with self.assertRaisesRegex(ValueError, "正在重启"):
            self.api.restart_emulator(device["id"])
        with self.assertRaises(ValueError):
            self.api.start(config)
        order = []
        process = Mock()
        process.poll.return_value = None
        def shutdown(*args):
            self.assertEqual(args, ("emulator-5554", "Pixel_5"))
            order.append("closed")
        def launch(*args, **kwargs):
            self.assertEqual(order, ["closed"])
            order.append("launched")
            return process
        with patch.object(desktop, "stop_emulator", side_effect=shutdown), \
                patch.object(desktop, "resolve_emulator", return_value=Path("sdk/emulator.exe")), \
                patch.object(desktop.subprocess, "Popen", side_effect=launch) as popen, \
                patch.object(desktop.threading, "Thread"):
            self.api._restart_emulator(device["id"])
        self.assertEqual(order, ["closed", "launched"])
        self.assertEqual(popen.call_args.args[0], [str(Path("sdk/emulator.exe")), "-avd", "Pixel_5", "-noaudio", "-no-snapshot-load"])
        state = self.api.get_state()
        self.assertEqual(state["devices"][0]["status"], "starting")
        self.assertEqual(state["devices"][0]["serial"], "")
        self.assertEqual(state["config"], config)
        self.assertEqual(state["selected"], device["id"])
        self.assertFalse(self.api._restarts)

    def test_restart_failures_do_not_launch_before_confirmed_shutdown(self):
        device = self.restarting_device()
        with patch.object(desktop, "stop_emulator", side_effect=RuntimeError("关闭超时")), \
                patch.object(desktop.subprocess, "Popen") as launch:
            self.api._restart_emulator(device["id"])
        launch.assert_not_called()
        state = self.api.get_state()
        self.assertEqual(state["devices"][0]["status"], "unknown")
        self.assertIn("关闭超时", state["notice"]["message"])
        self.assertFalse(self.api._restarts)
        device["status"] = "running"
        with patch.object(desktop.threading, "Thread"):
            self.api.restart_emulator(device["id"])
        with patch.object(desktop, "stop_emulator"), \
                patch.object(desktop, "resolve_emulator", return_value=Path("sdk/emulator.exe")), \
                patch.object(desktop.subprocess, "Popen", side_effect=OSError("launch failed")):
            self.api._restart_emulator(device["id"])
        self.assertEqual(self.api.get_state()["devices"][0]["status"], "stopped")
        self.assertIn("launch failed", self.api.get_state()["notice"]["message"])

    def test_scan_retains_restart_state_and_ignores_prelaunch_inventory(self):
        device = self.restarting_device()
        with patch.object(desktop, "discover_emulators", return_value=([], "")):
            self.api._scan()
        self.assertEqual(self.api.get_state()["devices"][0]["status"], "restarting")
        def stale_inventory():
            with patch.object(desktop, "stop_emulator"), \
                    patch.object(desktop, "resolve_emulator", return_value=Path("sdk/emulator.exe")), \
                    patch.object(desktop.subprocess, "Popen", return_value=Mock()), \
                    patch.object(desktop.threading, "Thread"):
                self.api._restart_emulator(device["id"])
            return ([dict(device, status="running")], "")
        with patch.object(desktop, "discover_emulators", side_effect=stale_inventory):
            self.api._scan()
        self.assertEqual(self.api.get_state()["devices"][0]["status"], "starting")
        self.assertIn(device["id"], self.api._launches)

    def test_restart_rejects_stopped_non_avd_and_task_running_devices(self):
        device = self.stopped_device()
        with self.assertRaises(ValueError):
            self.api.restart_emulator(device["id"])
        device.update(status="running", serial="emulator-5554", avd="")
        with self.assertRaisesRegex(ValueError, "Android AVD"):
            self.api.restart_emulator(device["id"])
        device["avd"] = "Pixel_5"
        self.api._process = Mock()
        with self.assertRaisesRegex(ValueError, "先停止任务"):
            self.api.restart_emulator(device["id"])
        self.api._process = None

    def test_window_close_during_shutdown_prevents_relaunch(self):
        device = self.restarting_device()
        with patch.object(desktop, "stop_emulator", side_effect=lambda *args: self.api._close()), \
                patch.object(desktop.subprocess, "Popen") as launch:
            self.api._restart_emulator(device["id"])
        launch.assert_not_called()
        self.assertFalse(self.api._restarts)

    def test_launch_monitor_updates_ready_state_and_preserves_config(self):
        device = self.stopped_device()
        config = desktop.default_config()
        config["entries"]["tree_threshold"] = "1600"
        self.api.update_config(config)
        process = Mock()
        process.poll.return_value = None
        with patch.object(desktop, "resolve_emulator", return_value=Path("sdk/emulator.exe")), \
                patch.object(desktop.subprocess, "Popen", return_value=process), \
                patch.object(self.api, "_monitor_launches"):
            self.api.launch_emulator(device["id"])
        stopped = dict(device, status="stopped")
        with patch.object(desktop, "discover_emulators", return_value=([stopped], "")):
            self.api._scan()
        self.assertEqual(self.api.get_state()["devices"][0]["status"], "starting")
        ready = dict(device, status="running", serial="emulator-5560")
        with patch.object(desktop, "discover_emulators", return_value=([ready], "")):
            self.api._scan()
        self.assertEqual(self.api.get_state()["selected"], "avd:Pixel_5")
        self.assertEqual(self.api.get_state()["config"], config)
        self.assertFalse(self.api._launches)
        task = Mock(stdout=io.StringIO(""))
        task.wait.return_value = 0
        with patch.object(desktop, "emulator_config_key", return_value="avd:Pixel_5"), \
                patch.object(desktop.subprocess, "Popen", return_value=task) as launch:
            self.api.start(config)
            self.api._reader.join(timeout=2)
        self.assertIn("emulator-5560", launch.call_args.args[0])
        self.assertEqual(desktop.checked_config(load_config(self.directory.name, "avd:Pixel_5"),
                                               from_disk=True), config)

    def test_launch_process_failure_and_timeout_are_reported(self):
        for code in [1, None]:
            with self.subTest(code=code):
                self.stopped_device()
                process = Mock()
                process.poll.return_value = code
                self.api._launches["avd:Pixel_5"] = (process, 0)
                with patch.object(desktop, "discover_emulators", return_value=([dict(self.api._devices[0])], "")), \
                        patch.object(desktop.time, "monotonic", return_value=181):
                    self.api._scan()
                self.assertFalse(self.api._launches)
                self.assertIn("Pixel_5", self.api.get_state()["notice"]["message"])
                self.assertEqual(self.api.get_state()["devices"][0]["status"], "stopped" if code is not None else "unknown")

    def test_refresh_while_running_preserves_active_selection_and_config(self):
        self.devices()
        self.api._process = Mock()
        previous = self.api.get_state()
        with patch.object(desktop.threading, "Thread") as thread:
            self.assertTrue(self.api.refresh_devices()["scanning"])
            thread.return_value.start.assert_called_once()
        with patch.object(desktop, "discover_emulators", return_value=inventory(("emulator-5556", "B"))):
            self.api._scan()
        self.assertEqual(self.api.get_state()["selected"], previous["selected"])
        self.assertEqual(self.api.get_state()["config"], previous["config"])

    def test_stale_running_device_is_checked_before_task_launch(self):
        self.devices()
        with patch.object(desktop, "device_ready", return_value=False), \
                patch.object(desktop.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValueError, "未连接"):
                self.api.start(desktop.default_config())
            launch.assert_not_called()

    def test_scan_failure_preserves_selection_and_marks_status_unknown(self):
        self.devices()
        previous = self.api.get_state()["selected"]
        with patch.object(desktop, "discover_emulators", side_effect=OSError("ADB failed")):
            self.api._scan()
        state = self.api.get_state()
        self.assertEqual(state["selected"], previous)
        self.assertTrue(all(item["status"] == "unknown" for item in state["devices"]))
        self.assertFalse(state["scanning"])

    def test_serial_reused_by_another_avd_cannot_launch_task(self):
        self.devices()
        with patch.object(desktop, "emulator_config_key", return_value="avd:another"), \
                patch.object(desktop.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValueError, "连接标识已变化"):
                self.api.start(desktop.default_config())
            launch.assert_not_called()

    def test_background_boot_checks_do_not_block_switching_or_second_launch(self):
        self.devices()
        with patch.object(desktop.threading, "Thread"):
            self.api._begin_scan(background=True)
        self.assertFalse(self.api.get_state()["scanning"])
        self.api.select_device("emulator-5556", desktop.default_config())
        self.assertEqual(self.api.get_state()["selected"], "emulator-5556")
        self.api._devices[0]["status"] = "stopped"
        process = Mock()
        process.poll.return_value = None
        with patch.object(desktop, "resolve_emulator", return_value=Path("sdk/emulator.exe")), \
                patch.object(desktop.subprocess, "Popen", return_value=process), \
                patch.object(self.api, "_monitor_launches"):
            self.api.launch_emulator("emulator-5554")
        self.assertEqual(self.api.get_state()["devices"][0]["status"], "starting")


if __name__ == "__main__":
    unittest.main()
