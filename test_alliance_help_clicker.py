"""Exclusive dispatch, fixed-area clicking, cancellation and reconnect recovery."""

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import alliance_help_clicker as clicker
import help_bot as bot
import runtime_control as control
import stamina_state
from test_help_bot import Clock


def fixture(name):
    with Image.open(bot.ROOT / f"tests/fixtures/help-clicker-{name}.png") as image:
        return image.convert("L")


class HelpClickerTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = control.RuntimeControl()
        control.activate(self.state)
        self.addCleanup(control.activate, None)
        self.enterContext(patch.object(bot.time, "monotonic", self.clock.monotonic))
        self.enterContext(patch.object(bot.time, "sleep", self.clock.sleep))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.inspect = self.enterContext(patch.object(bot, "inspect", return_value=(False, None, 0)))

    def run_mode(self):
        bot.run_help_clicker("test", *([None] * 4))

    def test_page_heading_survives_missing_help_all_button(self):
        image = fixture("page")
        self.assertTrue(clicker.help_page(bot, image))
        image.paste(0, (300, 2170, 780, 2290))
        self.assertTrue(clicker.help_page(bot, image))
        empty = fixture("empty")
        self.assertTrue(clicker.help_page(bot, empty))
        self.assertEqual(bot.alliance_back_target(empty), (60, 190))
        self.assertFalse(clicker.help_page(bot, fixture("alliance")))
        self.assertEqual(bot.alliance_back_target(image), (60, 190))

    def test_navigation_waits_for_actual_help_entry_and_correct_page(self):
        alliance, page = fixture("alliance"), fixture("page")
        loading = alliance.copy()
        loading.paste(0, clicker.HELP_ENTRY_BOX)
        with (patch.object(bot, "screenshot", side_effect=[loading, alliance, alliance, page, page]),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "network_reconnect_target", return_value=None),
              patch.object(bot, "task_tap") as tap):
            clicker.enter(bot, "test", None, None)
        self.assertEqual([call.args for call in tap.call_args_list],
                         [("test", 800, 2240), ("test", 800, 1740)])

    def test_navigation_interrupt_never_taps_the_help_entry(self):
        with (patch.object(bot, "screenshot", return_value=fixture("alliance")),
              patch.object(bot, "forced_offline", return_value=True),
              patch.object(bot, "task_tap") as tap,
              self.assertRaises(clicker.ReconnectRequired)):
            clicker.enter(bot, "test", None, None)
        tap.assert_called_once_with("test", *clicker.CITY_ALLIANCE)

    def test_fixed_area_is_tapped_continuously_without_button_detection(self):
        calls = []
        image = fixture("page")
        image.paste(0, (300, 2170, 780, 2290))
        empty = fixture("empty")

        def tap(*args):
            calls.append(args)
            self.clock.sleep(.05)
            if len(calls) == 90:
                self.state.stop()

        ordinary_help, healing = object(), object()
        with (patch.object(bot, "TASK_HELP", ordinary_help),
              patch.object(bot, "WILDERNESS_HEALING", healing),
              patch.object(bot, "return_to_city", return_value=True) as city,
              patch.object(clicker, "enter") as enter,
              patch.object(bot, "screenshot", side_effect=[image, image, empty, empty]) as screenshot,
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "network_reconnect_target", return_value=None),
              patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "recognize_text") as ocr,
              patch.object(clicker, "help_page") as detect,
              patch.object(bot, "run_schedule") as schedule,
              patch.object(bot, "adb", side_effect=tap)):
            with self.assertRaises(control.StopRequested):
                self.run_mode()
            self.assertIs(bot.TASK_HELP, ordinary_help)
            self.assertIs(bot.WILDERNESS_HEALING, healing)
        self.assertEqual(calls, [("test", "shell", "input", "tap", "540", "2230")] * 90)
        self.assertEqual(screenshot.call_count, 4)
        city.assert_called_once()
        enter.assert_called_once()
        ocr.assert_not_called()
        detect.assert_not_called()
        schedule.assert_not_called()

    def test_offline_uses_existing_cooldown_then_reenters_and_resumes(self):
        offline = False
        disconnected = False
        taps, reconnect_times = [], []
        image = fixture("page")

        def capture(_):
            nonlocal offline, disconnected
            if not disconnected and self.clock.now >= 102:
                offline = disconnected = True
            return image

        def tap(_, *args):
            nonlocal offline
            if args[-2:] == ("540", "2230"):
                self.assertFalse(offline)
                taps.append(args)
                self.clock.sleep(.1)
                if len(taps) == 50:
                    self.state.stop()
            else:
                reconnect_times.append(self.clock.now)
                offline = False

        with (patch.object(bot, "screenshot", side_effect=capture),
              patch.object(bot, "forced_offline", side_effect=lambda *_: offline),
              patch.object(bot, "network_reconnect_target", return_value=None),
              patch.object(bot, "return_to_city", side_effect=lambda *_: not offline),
              patch.object(bot, "reconnect_target", return_value=(750, 1480)),
              patch.object(bot, "RECONNECT_DELAY", 60),
              patch.object(bot, "game_foreground", return_value=True),
              patch.object(clicker, "enter") as enter,
              patch.object(bot, "adb", side_effect=tap),
              self.assertRaises(control.StopRequested)):
            self.run_mode()
        self.assertEqual(len(reconnect_times), 1)
        self.assertGreaterEqual(reconnect_times[0], 162)
        self.assertEqual(enter.call_count, 2)
        self.assertEqual(len(taps), 50)

    def test_main_map_enters_alliance_without_returning_to_city(self):
        self.inspect.return_value = (True, None, 0)
        image = fixture("page")
        with (patch.object(bot, "screenshot", return_value=image),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "network_reconnect_target", return_value=None),
              patch.object(bot, "return_to_city") as city,
              patch.object(clicker, "enter", side_effect=control.StopRequested) as enter,
              self.assertRaises(control.StopRequested)):
            self.run_mode()
        city.assert_not_called()
        enter.assert_called_once_with(bot, "test", None, None)
        self.assertTrue(self.inspect.call_args.kwargs["allow_wilderness"])

    def test_network_reconnect_reenters_before_resuming_clicks(self):
        taps = []

        def tap(*args):
            taps.append(args)
            self.clock.sleep(2)
            if len(taps) == 2:
                self.state.stop()

        with (patch.object(bot, "screenshot"),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "network_reconnect_target",
                           side_effect=[None, None, (780, 1480), None, None]),
              patch.object(bot, "return_to_city", return_value=True) as city,
              patch.object(bot, "game_foreground", return_value=True),
              patch.object(clicker, "enter") as enter,
              patch.object(bot, "adb", side_effect=tap),
              self.assertRaises(control.StopRequested)):
            self.run_mode()
        self.assertEqual(enter.call_count, 2)
        self.assertEqual(city.call_count, 2)
        self.assertEqual(len(taps), 2)

    def test_stop_cancels_pending_navigation_before_any_tap(self):
        self.state.stop()
        with (patch.object(bot, "return_to_city") as city,
              patch.object(bot, "adb") as adb,
              self.assertRaises(control.StopRequested)):
            self.run_mode()
        city.assert_not_called()
        adb.assert_not_called()

    def test_disabled_forced_reconnect_stops_before_entry_or_help_taps(self):
        with (patch.object(bot, "return_to_city", return_value=False),
              patch.object(bot, "screenshot"),
              patch.object(bot, "forced_offline", return_value=True),
              patch.object(clicker, "enter") as enter,
              patch.object(bot, "adb") as adb,
              self.assertRaisesRegex(RuntimeError, "自动重连已关闭")):
            bot.run_help_clicker("test", *([None] * 4), reconnect=False)
        enter.assert_not_called()
        adb.assert_not_called()

    def test_dry_run_does_not_navigate_or_tap(self):
        with (patch.object(bot, "screenshot", return_value=fixture("page")),
              patch.object(bot, "return_to_city") as city,
              patch.object(bot, "adb") as adb):
            bot.run_help_clicker("test", *([None] * 4), dry_run=True)
        city.assert_not_called()
        adb.assert_not_called()


class HelpClickerDispatchTest(unittest.TestCase):
    def test_cli_excludes_all_regular_tasks_and_runtime_hooks(self):
        lease = Mock()
        args = ["help_bot.py", "--device", "test", "--help-clicker", "--daily-tasks",
                "--train", "--city-tasks", "--heal", "--intelligence", "--hunt", "--gather",
                "--bounty", "--no-help", "--duration", "1", "--max-clicks", "1"]

        def run_clicker(*_args, **_kwargs):
            self.assertIsNone(bot.WILDERNESS_HEALING)

        with (patch.object(bot.sys, "argv", args),
              patch.object(bot, "adb", return_value=b"device"),
              patch.object(bot, "avd_key", return_value="avd:test"),
              patch.object(bot, "bind_console_emulator"),
              patch.object(bot, "DeviceLease", return_value=Mock(acquire=Mock(return_value=lease))),
              patch.object(stamina_state, "set_device_identity"),
              patch.object(stamina_state, "clear_device_identity"),
              patch.object(bot, "run_help_clicker", side_effect=run_clicker) as click,
              patch.object(bot, "return_to_city") as city,
              patch.object(bot, "run_schedule") as schedule,
              patch.object(bot, "run") as monitor,
              contextlib.redirect_stdout(io.StringIO())):
            bot.main()
        click.assert_called_once()
        self.assertEqual(click.call_args.args[0], "test")
        self.assertEqual(click.call_args.kwargs, {"reconnect": True, "dry_run": False})
        city.assert_not_called()
        schedule.assert_not_called()
        monitor.assert_not_called()
        lease.release.assert_called_once()

    def test_mode_is_off_by_default(self):
        with (patch.object(bot.sys, "argv", ["help_bot.py", "--device", "test", "--dry-run"]),
              patch.object(bot, "adb", return_value=b"device"),
              patch.object(bot, "avd_key", return_value="avd:test"),
              patch.object(bot, "bind_console_emulator"),
              patch.object(bot, "run_help_clicker") as click,
              patch.object(bot, "run") as monitor,
              contextlib.redirect_stdout(io.StringIO())):
            bot.main()
        click.assert_not_called()
        monitor.assert_called_once()


if __name__ == "__main__":
    unittest.main()
