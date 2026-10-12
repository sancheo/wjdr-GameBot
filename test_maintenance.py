"""Maintenance selection and GUI restoration regressions without live clicking."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from emulator_config import save_config, write_json
import gui_web
import maintenance
from runtime_logs import SessionLogs


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_inspection_uses_matching_exception_file_for_latest_session(self):
        old = SessionLogs(self.root, "avd:A")
        old.session = "20261007-010000-000000"
        old.append("ValueError: old failure")
        new = SessionLogs(self.root, "avd:A")
        new.session = "20261007-020000-000000"
        new.append("normal")
        row = maintenance.inspection(["avd:A"], self.root)[0]
        self.assertIn("normal", row["runtime_tail"])
        self.assertEqual(row["exception_tail"], "")

    def test_stale_or_foreign_runtime_state_is_not_live(self):
        folder = self.root / "configs" / "runtime"
        folder.mkdir(parents=True)
        (folder / "desktop-2.json").write_text(json.dumps({"bridge_pid": 2, "closed": False}), encoding="utf-8")
        foreign = [{"ProcessId": 2, "ParentProcessId": 1, "CommandLine": "gui_web.py --bridge"},
                   {"ProcessId": 1, "ParentProcessId": 0, "ExecutablePath": str(self.root.parent / "GameBot.exe")}]
        self.assertEqual(maintenance.desktop_states(self.root, foreign), [])
        foreign[1]["ExecutablePath"] = str(self.root / "GameBot.exe")
        self.assertEqual(len(maintenance.desktop_states(self.root, foreign)), 1)

    def test_resume_selects_avd_name_after_serial_changes_and_restores_settings(self):
        config = gui_web.default_config()
        config["options"]["intelligence"] = False
        save_config(self.root, "avd:A", "emulator-5554", config)
        api = gui_web.DesktopApi(self.root)
        self.addCleanup(api._close)
        devices = [{"id": "avd:A", "key": "avd:A", "name": "A", "avd": "A",
                    "serial": "emulator-5560", "status": "running"}]
        with patch.object(gui_web, "discover_emulators", return_value=(devices, "")), \
                patch.object(api._launch_wakeup, "wait", return_value=False), \
                patch.object(api, "_wait_game_loaded", return_value=True) as wait_loaded, \
                patch("emulator_runtime.adb_output", side_effect=["package:/data/game.apk", "Events injected: 1",
                                                                 "mResumedActivity: com.gof.china/Main"]), \
                patch.object(api, "start", return_value={}) as start:
            api.resume_saved("avd:A")
        self.assertEqual(api._selected, "avd:A")
        wait_loaded.assert_called_once_with("emulator-5560")
        start.assert_called_once_with(config)

    def test_black_loading_frame_never_starts_the_bot(self):
        from PIL import Image
        import help_bot
        self.assertFalse(gui_web.game_loaded(Image.new("L", help_bot.EXPECTED_SIZE)))

    def test_passive_load_wait_does_not_click_or_send_back(self):
        import help_bot
        from PIL import Image
        api = gui_web.DesktopApi(self.root)
        self.addCleanup(api._close)
        with patch.object(api._launch_wakeup, "wait", side_effect=[False, False, True]), \
                patch.object(help_bot, "game_foreground", return_value=True), \
                patch.object(help_bot, "screenshot", return_value=Image.new("L", help_bot.EXPECTED_SIZE)), \
                patch.object(gui_web, "game_loaded", return_value=False), \
                patch.object(help_bot, "task_tap") as tap, patch.object(help_bot, "task_back") as back:
            self.assertFalse(api._wait_game_loaded("emulator-5554"))
        tap.assert_not_called()
        back.assert_not_called()

    def test_resume_does_not_start_bot_if_game_is_missing(self):
        save_config(self.root, "avd:A", "emulator-5554", gui_web.default_config())
        api = gui_web.DesktopApi(self.root)
        self.addCleanup(api._close)
        devices = [{"id": "avd:A", "key": "avd:A", "name": "A", "avd": "A",
                    "serial": "emulator-5554", "status": "running"}]
        with patch.object(gui_web, "discover_emulators", return_value=(devices, "")), \
                patch("emulator_runtime.adb_output", return_value=""), \
                patch.object(api, "start") as start:
            api.resume_saved("avd:A")
        start.assert_not_called()
        self.assertIn("未安装", api.get_state()["notice"]["message"])

    def test_hung_app_is_reopened_without_clearing_game_data(self):
        api = gui_web.DesktopApi(self.root)
        self.addCleanup(api._close)
        replies = ["package:/data/game.apk", "No activities found", "",
                   "Events injected: 1", "topResumedActivity=com.gof.china/Main"]
        with patch("emulator_runtime.adb_output", side_effect=replies) as adb, \
                patch.object(api._launch_wakeup, "wait", return_value=False), \
                patch.object(api, "_wait_game_loaded", return_value=True):
            self.assertTrue(api._open_game({"serial": "emulator-5554", "name": "A"}))
        commands = [call.args for call in adb.call_args_list]
        self.assertIn(("-s", "emulator-5554", "shell", "am", "force-stop", "com.gof.china"), commands)
        self.assertFalse(any("clear" in command or "uninstall" in command for command in commands))

    def test_prepare_stores_restore_plan_before_shutdown_and_inspects_after_exit(self):
        state_path = self.root / "configs" / "maintenance.json"
        running = [{"id": "avd:A", "key": "avd:A", "name": "A", "avd": "A",
                    "serial": "emulator-5554", "status": "running"}]
        stopped = [dict(running[0], serial="", status="stopped")]
        order = []

        def close_desktop(*args, **kwargs):
            data = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(data["targets"], ["avd:A"])
            self.assertEqual(data["phase"], "stopping")
            order.append("desktop")

        with patch.object(maintenance, "ROOT", self.root), patch.object(maintenance, "STATE", state_path), \
                patch.object(maintenance, "saved_keys", return_value=["avd:A"]), \
                patch.object(maintenance, "desktop_states", return_value=[]), \
                patch.object(maintenance, "process_inventory", return_value=[]), \
                patch.object(maintenance, "discover_emulators", side_effect=[(running, ""), (stopped, "")]), \
                patch.object(maintenance.subprocess, "run", side_effect=close_desktop), \
                patch.object(maintenance, "DeviceLease"), \
                patch.object(maintenance, "stop_emulator", side_effect=lambda *args: order.append("emulator")), \
                patch.object(maintenance, "inspection", side_effect=lambda keys: order.append("logs") or []):
            result = maintenance.prepare()
        self.assertEqual(order, ["desktop", "emulator", "logs"])
        self.assertEqual(result["phase"], "prepared")

    def test_status_does_not_complete_until_city_check_passes(self):
        state_path = self.root / "configs" / "maintenance.json"
        write_json(state_path, {"phase": "resuming", "targets": ["avd:A"]})
        row = {"emulator": "avd:A", "running": True, "task_pid": 10, "task_ready": False}
        with patch.object(maintenance, "STATE", state_path), \
                patch.object(maintenance, "saved_keys", return_value=["avd:A"]), \
                patch.object(maintenance, "process_inventory", return_value=[{"ProcessId": 10}]), \
                patch.object(maintenance, "desktop_states", return_value=[row]), \
                patch.object(maintenance, "discover_emulators", return_value=([{"key": "avd:A", "status": "running"}], "")), \
                patch.object(maintenance, "inspection", return_value=[]):
            self.assertFalse(maintenance.status()["complete"])
            row["task_ready"] = True
            self.assertTrue(maintenance.status()["complete"])
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["phase"], "complete")


if __name__ == "__main__":
    unittest.main()
