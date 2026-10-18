"""Desktop bridge regressions without operating an emulator."""

import io
import contextlib
import threading
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
    return ([{"id": serial, "key": "serial:" + serial, "name": name, "avd": "",
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

    def devices(self):
        with patch.object(desktop, "discover_emulators", return_value=inventory(("emulator-5554", "A"), ("emulator-5556", "B"))):
            self.api._scan()

    def test_open_screenshot_uses_default_viewer_and_preserves_spaces(self):
        image = Path(self.directory.name) / "artifacts/exception_screenshots/模拟器 A/2026-10-18/失败截图.png"
        image.parent.mkdir(parents=True)
        image.touch()
        with patch.object(desktop.os, "startfile") as open_image:
            self.assertTrue(self.api.open_screenshot(str(image)))
            open_image.assert_called_once_with(str(image.resolve()))

    def test_open_screenshot_rejects_missing_or_unrelated_files(self):
        root = Path(self.directory.name)
        with patch.object(desktop.os, "startfile") as open_image:
            for path, message in [
                ("artifacts/exception_screenshots/missing.png", "截图不存在"),
                (str(root / "outside.png"), "只能打开异常截图"),
                ("artifacts/exception_screenshots/../../outside.png", "只能打开异常截图"),
                ("artifacts/exception_screenshots/program.exe", "只能打开异常截图"),
            ]:
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, message):
                    self.api.open_screenshot(path)
            open_image.assert_not_called()

    def test_open_screenshot_reports_viewer_failure(self):
        image = Path(self.directory.name) / "artifacts/exception_screenshots/failure.png"
        image.parent.mkdir(parents=True)
        image.touch()
        with patch.object(desktop.os, "startfile", side_effect=OSError("没有关联的查看器")):
            with self.assertRaisesRegex(ValueError, "无法打开截图"):
                self.api.open_screenshot(str(image))


    def phone(self, status="running"):
        device = {"id": "serial:PHONE123", "key": "serial:PHONE123", "name": "Pixel 7",
                  "avd": "", "serial": "PHONE123", "status": status, "kind": "physical"}
        with patch.object(desktop, "discover_emulators", return_value=([device], "")):
            self.api._scan()
        return device

    def test_phone_task_uses_serial_and_saves_independent_config(self):
        self.devices()
        config = desktop.default_config()
        config["entries"]["tree_threshold"] = "2500"
        save_config(self.directory.name, "serial:PHONE123", "PHONE123", config)
        phone = self.phone()
        self.assertEqual(self.api.get_state()["config"], config)
        process = Mock(stdout=io.StringIO("任务结束\n"))
        process.wait.return_value = 0
        with patch.object(desktop.subprocess, "Popen", return_value=process) as launch, \
                patch.object(desktop, "device_ready", return_value=True) as ready:
            self.api.start(config)
            self.api._reader.join(timeout=2)
        command = launch.call_args.args[0]
        self.assertEqual(command[command.index("--device") + 1], "PHONE123")
        ready.assert_called_once_with("PHONE123")
        self.assertEqual(desktop.checked_config(load_config(self.directory.name, phone["key"]), from_disk=True), config)

    def test_phone_connection_errors_prevent_task_launch_with_actionable_message(self):
        for status, message in [("unauthorized", "允许 USB 调试"), ("offline", "数据线")]:
            with self.subTest(status=status):
                self.phone(status)
                self.assertIn(message, self.api.get_state()["device_status"])
                with patch.object(desktop.subprocess, "Popen") as launch:
                    with self.assertRaisesRegex(ValueError, message):
                        self.api.start(desktop.default_config())
                launch.assert_not_called()


    def test_disconnected_phone_is_rechecked_before_task_launch(self):
        self.phone()
        with patch.object(desktop, "device_ready", return_value=False), \
                patch.object(desktop.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValueError, "未连接"):
                self.api.start(desktop.default_config())
        launch.assert_not_called()

    def test_notes_persist_without_changing_selection_or_configuration(self):
        from emulator_config import config_path
        self.devices()
        config = self.api.get_state()["config"]
        path = save_config(self.directory.name, "serial:emulator-5554", "emulator-5554", config)
        before = path.read_bytes()
        selected = self.api.get_state()["selected"]
        state = self.api.set_device_note("emulator-5556", "小号")
        self.assertEqual(state["selected"], selected)
        self.assertEqual(state["config"], config)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(config_path(self.directory.name, "serial:emulator-5556").exists())
        # The AVD can acquire a different port after restart without losing its note.
        changed = inventory(("emulator-5560", "B"))
        changed[0][0]["key"] = "serial:emulator-5556"
        restored = desktop.DesktopApi(self.directory.name)
        self.addCleanup(restored._close)
        with patch.object(desktop, "discover_emulators", return_value=changed):
            restored._scan()
        self.assertEqual(restored.get_state()["devices"][0]["note"], "小号")
        restored.set_device_note("emulator-5560", "")
        with patch.object(desktop, "discover_emulators", return_value=changed):
            restored._scan()
        self.assertEqual(restored.get_state()["devices"][0]["note"], "")

    def test_phone_notes_survive_refresh_and_reject_missing_devices(self):
        phone = self.phone()
        self.api.set_device_note(phone["id"], "我的手机")
        self.phone()
        self.assertEqual(self.api.get_state()["devices"][0]["note"], "我的手机")
        with self.assertRaisesRegex(ValueError, "不在列表"):
            self.api.set_device_note("serial:missing", "备注")


    def test_corrupt_saved_config_falls_back_with_visible_warning(self):
        for contents in ["{broken", "[]", '{"version": 99}']:
            with self.subTest(contents=contents):
                path = Path(self.directory.name) / "configs"
                path.mkdir(exist_ok=True)
                from emulator_config import config_path
                config_path(self.directory.name, "serial:emulator-5554").write_text(contents, encoding="utf-8")
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
        config["gather_steps"] = [{"resource": name, "level": "8级"} for name in ("meat", "coal", "iron")]
        command = desktop.build_command(config, "emulator-5554")
        self.assertEqual(command[:5], [sys.executable, "-X", "utf8", "-u", str(desktop.ROOT / "help_bot.py")])
        self.assertIn("--no-help", command)
        self.assertIn("--no-spear", command)
        self.assertIn("--upgrade", command)
        self.assertIn("--bounty", command)
        for flag, value in [("--device", "emulator-5554"), ("--tree-threshold", "1250"),
                            ("--stamina-threshold", "40"), ("--reconnect-minutes", "0.5")]:
            self.assertEqual(command[command.index(flag) + 1], value)
        start = command.index("--gather-steps")
        self.assertEqual(command[start + 1:start + 4], ["meat:8", "coal:8", "iron:8"])
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

    def test_alliance_chests_default_legacy_config_and_independent_disable_flag(self):
        config = desktop.default_config()
        self.assertTrue(config['options'].pop('alliance_chests'))
        restored = desktop.checked_config(config, from_disk=True)
        self.assertTrue(restored['options']['alliance_chests'])
        restored['options']['donate'] = False
        command = desktop.build_command(restored, 'emulator-test')
        self.assertIn('--no-donate', command)
        self.assertNotIn('--no-alliance-chests', command)
        restored['options']['alliance_chests'] = False
        self.assertIn('--no-alliance-chests', desktop.build_command(restored, 'emulator-test'))

    def test_bear_defaults_and_legacy_config_upgrade(self):
        config = desktop.default_config()
        self.assertFalse(config["options"].pop("bear"))
        self.assertFalse(config["options"].pop("bear_drive"))
        self.assertFalse(config["options"].pop("bear_follow"))
        self.assertTrue(config["options"].pop("bear_follow_no_heroes"))
        restored = desktop.checked_config(config, from_disk=True)
        self.assertFalse(restored["options"]["bear"])
        self.assertFalse(restored["options"]["bear_drive"])
        self.assertFalse(restored["options"]["bear_follow"])
        self.assertTrue(restored["options"]["bear_follow_no_heroes"])

    def test_bear_modes_saved_per_emulator_and_other_preferences_preserved(self):
        self.devices()
        config = desktop.default_config()
        config["options"].update(bear=True, bear_drive=True, bear_follow=False, train=False)
        self.api.update_config(config)
        restored = desktop.DesktopApi(self.directory.name)
        self.addCleanup(restored._close)
        restored._keys = {"emulator-5554": "serial:emulator-5554"}
        restored._choose("emulator-5554")
        self.assertEqual(restored.get_state()["config"]["options"], config["options"])
        self.api.select_device("emulator-5556", config)
        self.assertFalse(self.api.get_state()["config"]["options"]["bear"])
        self.assertFalse(self.api.get_state()["config"]["options"]["bear_follow"])
        self.assertTrue(self.api.get_state()["config"]["options"]["bear_follow_no_heroes"])


    def test_bear_mode_combinations_and_reconnect_validation(self):
        config = desktop.default_config()
        config["options"].update(bear=True, bear_follow=False, bear_drive=True, bear_follow_no_heroes=False)
        self.assertEqual(desktop.build_command(config, "test")[-3:], ["--bear", "--bear-modes", "drive"])
        config["options"]["bear_follow"] = True
        self.assertEqual(desktop.build_command(config, "test")[-4:], ["--bear", "--bear-modes", "drive", "follow"])
        config["entries"]["reconnect_minutes"] = "invalid"
        with self.assertRaisesRegex(ValueError, "重连等待"):
            desktop.build_command(config, "test")
        config["options"]["reconnect"] = False
        self.assertIn("--no-reconnect", desktop.build_command(config, "test"))
        config["options"].update(bear_drive=False, bear_follow=False)
        with self.assertRaisesRegex(ValueError, "至少要勾选"):
            desktop.build_command(config, "test")

    def test_no_hero_follow_default_and_precedence(self):
        config = desktop.default_config()
        config["options"].update(bear=True, bear_follow=True)
        self.assertEqual(desktop.build_command(config, "test")[-3:],
                         ["--bear", "--bear-modes", "follow_no_heroes"])

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
        saved = load_config(self.directory.name, "serial:emulator-5554")
        self.assertNotIn("help_clicker", saved["options"])
        self.assertTrue(self.api.get_state()["config"]["options"]["help_clicker"])
        self.assertFalse(saved["options"]["beast"])
        logs = [line["text"] for line in self.api.get_state()["logs"]]
        self.assertTrue(any("小号模式" in line for line in logs))
        self.assertFalse(any("自动打怪配置" in line or "资源采集配置" in line for line in logs))
        restored = desktop.DesktopApi(self.directory.name)
        self.addCleanup(restored._close)
        restored._keys = {"emulator-5554": "serial:emulator-5554"}
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
        config["gather_steps"] = []
        with self.assertRaisesRegex(ValueError, "资源"):
            desktop.build_command(config, "emulator-5554")
        self.api._close()
        with self.assertRaisesRegex(ValueError, "关闭"):
            self.api.start(desktop.default_config())

    def test_gather_full_only_defaults_legacy_and_command(self):
        config = desktop.default_config()
        self.assertTrue(config['options']['gather_full_only'])
        del config['options']['gather_full_only']
        self.assertTrue(desktop.checked_config(config, from_disk=True)['options']['gather_full_only'])
        for enabled in (True, False):
            config['options']['gather_full_only'] = enabled
            checked = desktop.checked_config(config)
            command = desktop.build_command(checked, 'test')
            self.assertEqual('--no-gather-full-only' in command, not enabled)
            config['options']['gather'] = False
            self.assertNotIn('--no-gather-full-only', desktop.build_command(config, 'test'))
            config['options']['gather'] = True

    def test_gather_steps_defaults_limits_order_and_legacy_migration(self):
        config = desktop.default_config()
        self.assertEqual(config["gather_steps"], [
            {"resource": name, "level": "8级"} for name, _ in desktop.RESOURCES])
        for count in (1, 6):
            config["gather_steps"] = [{"resource": "wood", "level": f"{i + 1}级"} for i in range(count)]
            checked = desktop.checked_config(config)
            command = desktop.build_command(checked, "test")
            self.assertEqual(command[command.index("--gather-steps") + 1:],
                             [f"wood:{i + 1}" for i in range(count)])
        for invalid in ([], [{}] * 7, [{"resource": "bad", "level": "8级"}], None):
            config["gather_steps"] = invalid
            with self.assertRaises(ValueError):
                desktop.checked_config(config)
        config = desktop.default_config()
        del config["gather_steps"]
        config["options"]["meat"] = False
        config["resource_levels"] = {name: "7级矿" for name, _ in desktop.RESOURCES}
        self.assertEqual(desktop.checked_config(config, from_disk=True)["gather_steps"],
                         [{"resource": name, "level": "7级"} for name in ("wood", "coal", "iron")])

    def test_fourth_level_reaches_launch_and_saved_config_for_each_resource(self):
        self.devices()
        for resource, _ in desktop.RESOURCES:
            with self.subTest(resource=resource):
                config = desktop.default_config()
                config["options"].update({name: name == resource for name, _ in desktop.RESOURCES})
                config["gather_steps"] = [{"resource": resource, "level": "4级"}]
                config["options"]["gather_full_only"] = resource in ("meat", "coal")
                self.api.update_config(config)
                process = Mock(stdout=io.StringIO(""))
                process.wait.return_value = 0
                with patch.object(desktop.subprocess, "Popen", return_value=process) as launch:
                    self.api.start(self.api.get_state()["config"])
                    self.api._reader.join(timeout=2)
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index("--gather-steps") + 1], f"{resource}:4")
                saved = load_config(self.directory.name, "serial:emulator-5554")
                self.assertEqual(saved["options"]["gather_full_only"], config["options"]["gather_full_only"])
                self.assertEqual("--no-gather-full-only" in command, not config["options"]["gather_full_only"])
                self.assertEqual(saved["gather_steps"], config["gather_steps"])
                restored = desktop.DesktopApi(self.directory.name)
                restored._keys = {"emulator-5554": "serial:emulator-5554"}
                restored._choose("emulator-5554")
                self.assertEqual(restored.get_state()["config"]["gather_steps"], config["gather_steps"])
                self.assertEqual(restored.get_state()["config"]["options"]["gather_full_only"], config["options"]["gather_full_only"])
                self.assertTrue(any(f"{dict(desktop.RESOURCES)[resource]} 4级" in line["text"]
                                    for line in self.api.get_state()["logs"]))

    def test_invalid_bridge_level_does_not_silently_launch_level_one(self):
        self.devices()
        for invalid in ("4", 4, "4级矿", None):
            with self.subTest(invalid=invalid):
                config = desktop.default_config()
                config["gather_steps"][0]["level"] = invalid
                with patch.object(desktop.subprocess, "Popen") as launch:
                    with self.assertRaisesRegex(ValueError, "资源采集等级"):
                        self.api.start(config)
                    launch.assert_not_called()
                self.assertEqual(desktop.checked_config(config, from_disk=True)["gather_steps"],
                                 desktop.default_config()["gather_steps"])

    def test_emulators_keep_independent_settings_and_stable_identity(self):
        config_b = desktop.default_config()
        config_b["entries"]["tree_threshold"] = "2500"
        save_config(self.directory.name, "serial:emulator-5556", "emulator-5556", config_b)
        self.devices()
        config_a = self.api.get_state()["config"]
        config_a["entries"]["tree_threshold"] = "1500"
        state = self.api.select_device("emulator-5556", config_a)
        self.assertEqual(state["config"]["entries"]["tree_threshold"], "2500")
        state = self.api.select_device("emulator-5554", state["config"])
        self.assertEqual(state["config"]["entries"]["tree_threshold"], "1500")
        changed = inventory(("emulator-5560", "A"))
        changed[0][0]["key"] = "serial:emulator-5554"
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

    def test_full_logs_survive_stop_snapshot_and_remain_incremental(self):
        self.api._append("首次异常：凌晨的问题")
        for index in range(2010):
            self.api._append(f"日志 {index}")
        state = self.api.get_state(0)
        self.assertFalse(state["log_reset"])
        self.assertEqual(len(state["logs"]), 2011)
        stopped = self.api.stop()
        self.assertEqual(stopped["logs"], state["logs"])
        self.assertTrue(stopped["logs"][0]["error"])
        self.assertEqual(self.api.get_state(state["last_log"])["logs"], [])
        state["config"]["entries"]["tree_threshold"] = "999"
        self.assertEqual(self.api.get_state()["config"]["entries"]["tree_threshold"], "1000")
        self.api._append("⚠️ 当前任务暂停")
        delta = self.api.get_state(state["last_log"])
        self.assertFalse(delta["log_reset"])
        self.assertEqual(len(delta["logs"]), 1)
        self.assertTrue(delta["logs"][0]["error"])

    def test_retry_log_is_a_warning_instead_of_an_error(self):
        line = "资源采集 第 1 次重试异常：未确认野外；10 秒后尝试恢复主城并重试（第 2 次）"
        self.api._append(line)
        row, = self.api.get_state()["logs"]
        self.assertTrue(row["warning"])
        self.assertFalse(row["error"])
        self.assertIn(line, row["text"])

    def test_startup_does_not_restore_saved_logs_when_selecting_devices(self):
        logs = desktop.SessionLogs(self.directory.name, "serial:emulator-5554")
        logs.append("首次异常：保留旧异常")
        logs.append("此前运行的最后一行")
        self.devices()
        first = self.api.get_state()["logs"]
        self.assertEqual(first, [])
        self.api._choose("emulator-5556")
        self.api._choose("emulator-5554")
        self.assertEqual(self.api.get_state()["logs"], first)

    def test_current_session_logs_remain_separate_when_switching_accounts(self):
        self.devices()
        self.api._append("大号本次日志")
        large = self.api.get_state()
        self.api._choose("emulator-5556")
        self.assertEqual(self.api.get_state()["logs"], [])
        self.api._append("小号本次日志")
        small = self.api.get_state()
        self.assertNotEqual(large["log_key"], small["log_key"])
        self.assertEqual(len(small["logs"]), 1)
        self.assertIn("小号本次日志", small["logs"][0]["text"])
        self.api._choose("emulator-5554")
        self.assertEqual(self.api.get_state()["logs"], large["logs"])

    def test_start_saves_config_drains_final_output_and_clears_running(self):
        self.devices()
        process = Mock(stdout=io.StringIO("正常日志\nValueError: 最后一条异常\n"))
        process.wait.return_value = 1
        with patch.object(desktop.subprocess, "Popen", return_value=process):
            self.api.start(desktop.default_config())
            self.api._reader.join(timeout=2)
        self.assertFalse(self.api.get_state()["running"])
        self.assertEqual(self.api.get_state()["notice"]["message"], "ValueError: 最后一条异常")
        self.assertEqual(desktop.checked_config(load_config(self.directory.name, "serial:emulator-5554"),
                                               from_disk=True), desktop.default_config())
        self.assertTrue(process.stdout.closed)

    def test_reported_stop_is_displayed_once_and_still_sets_failure_state(self):
        reason = "RetriesExhausted: 启动时主城检查 重试失败：超时；失败截图：example.png"
        line = f"[2026-10-18 23:41:24] 已停止：{reason}"
        process = Mock(stdout=io.StringIO(line + "\n"), stdin=None)
        process.wait.return_value = 1
        self.api._process = process
        self.api._task_status = {"help": {"status": "running"}}
        self.api._read_process(process)
        state = self.api.get_state()
        self.assertEqual([row["text"] for row in state["logs"]], [line])
        self.assertFalse(state["running"])
        self.assertEqual(state["notice"]["message"], reason)
        self.assertEqual(self.api._task_status["help"]["status"], "failed")
        self.assertEqual(self.api._task_status["help"]["reason"], reason)

    def test_reader_saves_ready_only_after_confirmed_navigation_and_clears_on_exit(self):
        messages = [
            ("小号模式互助连点已启用，其他任务已跳过；停止程序可结束连点", False),
            ("已进入联盟互助，持续快速点击底部全部帮助区域", True),
            ("启动时主城检查已通过，开始运行任务", True),
            ("自动打熊已启用，其他任务已跳过；停止程序可结束打熊", False),
            ("自动打熊主城检查已通过，开始运行任务", True),
        ]
        runtime_path = Path(self.directory.name) / "configs" / "runtime" / f"desktop-{os.getpid()}.json"
        for message, expected_ready in messages:
            with self.subTest(message=message):
                process = Mock(stdout=io.StringIO(f"[2026-10-10 12:00:00] {message}\n"),
                               stdin=None, pid=12345)
                self.api._process = process
                self.api._active_key = "serial:emulator-5554"
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

    def test_background_diagnostics_do_not_corrupt_json_responses(self):
        original = self.api.get_state

        def noisy_state():
            print("屏幕适配：1176×2400 → 1080×2340", flush=True)
            thread = threading.Thread(target=lambda: print("后台线程诊断", flush=True))
            thread.start()
            thread.join()
            return original()

        protocol, errors, incidental = io.StringIO(), io.StringIO(), io.StringIO()
        request = io.StringIO(json.dumps({"id": 1, "method": "get_state"}) + "\n")
        with (patch.object(self.api, "get_state", side_effect=noisy_state),
              contextlib.redirect_stdout(incidental), contextlib.redirect_stderr(errors)):
            desktop.serve_bridge(self.api, request, protocol)
        replies = [json.loads(line) for line in protocol.getvalue().splitlines()]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["id"], 1)
        self.assertIn("result", replies[0])
        self.assertEqual(incidental.getvalue(), "")
        self.assertIn("屏幕适配", errors.getvalue())
        self.assertIn("后台线程诊断", errors.getvalue())

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

if __name__ == "__main__":
    unittest.main()
