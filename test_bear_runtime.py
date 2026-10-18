"""Exclusive GUI/CLI bear execution without issuing real device commands."""

import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import bear_hunting as bear
import help_bot as bot
import runtime_control as control
import stamina_state
from test_help_bot import Clock


class BearRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = control.RuntimeControl()
        self.field = Image.new("RGB", (1080, 2340))
        self.backend = Mock(
            TASK_HELP=object(), WILDERNESS_HEALING=object(),
            time=SimpleNamespace(monotonic=self.clock.monotonic, time=self.clock.monotonic,
                                 sleep=self.clock.sleep),
            check_stop=self.state.check)
        self.previous = self.backend.TASK_HELP, self.backend.WILDERNESS_HEALING
        self.backend.task_screen.return_value = self.field
        self.backend.enter_wilderness.return_value = self.field
        self.backend.town_button.return_value = True
        self.backend.forced_offline.return_value = False
        self.backend.network_reconnect_target.return_value = None
        self.backend.intelligence_queue_full.return_value = False
        self.backend.return_to_city.return_value = True
        self.backend.game_foreground.return_value = True
        self.enterContext(patch.object(bear, "war_page", return_value=False))
        self.enterContext(patch.object(bear, "icon_target", return_value=(987, 951)))
        self.enterContext(patch.object(bear, "wait_for", return_value=self.field))
        self.enterContext(patch.object(bear, "leave_bear_pages", return_value=self.field))
        self.drive = self.enterContext(patch.object(bear, "drive_rally", return_value=(self.field, True)))
        self.follow = self.enterContext(patch.object(bear, "follow_rally", return_value=(self.field, True)))

    def run_mode(self, **kwargs):
        bear.run(self.backend, "test", *([None] * 4), forever=False, seconds=3, **kwargs)

    def test_selected_flows_run_exclusively_and_combined_modes_alternate(self):
        for modes, drives, follows in [(("drive",), 2, 0), (("follow",), 0, 2), (("drive", "follow"), 1, 1)]:
            with self.subTest(modes=modes):
                self.clock.now = 100
                self.drive.reset_mock()
                self.follow.reset_mock()
                self.run_mode(modes=modes)
                self.assertEqual(self.drive.call_count, drives)
                self.assertEqual(self.follow.call_count, follows)
                self.assertEqual((self.backend.TASK_HELP, self.backend.WILDERNESS_HEALING), self.previous)
        self.backend.run_schedule.assert_not_called()
        self.backend.run_healing.assert_not_called()
        self.backend.run_hunt.assert_not_called()
        statuses = [item.kwargs["status"] for item in self.backend.emit_task_event.call_args_list]
        self.assertIn("waiting", statuses)
        self.assertEqual(statuses[-1], "stopped")

    def test_full_march_queue_waits_without_opening_or_dispatching(self):
        self.backend.intelligence_queue_full.return_value = True
        self.run_mode(modes=("drive", "follow"))
        self.backend.task_tap.assert_not_called()
        self.drive.assert_not_called()
        self.follow.assert_not_called()
        self.assertGreaterEqual(self.clock.now, 110)

    def test_connection_loss_rechecks_city_then_resumes_selected_mode(self):
        self.backend.forced_offline.side_effect = [True, False, False, False, False]
        self.backend.return_to_city.side_effect = [True, False]
        self.run_mode()
        self.assertEqual(self.backend.return_to_city.call_count, 2)
        self.backend.run.assert_called_once()
        self.assertTrue(self.backend.run.call_args.kwargs["return_on_reconnect"])
        self.assertFalse(self.backend.run.call_args.kwargs["help_enabled"])
        self.assertTrue(self.follow.called)
        self.assertTrue(any(row.kwargs["status"] == "retrying"
                            for row in self.backend.emit_task_event.call_args_list))

    def test_reconnect_disabled_is_passed_to_shared_recovery(self):
        self.backend.return_to_city.return_value = False
        self.backend.run.side_effect = RuntimeError("自动重连已关闭")
        with self.assertRaisesRegex(RuntimeError, "自动重连已关闭"):
            self.run_mode(reconnect=False)
        self.assertFalse(self.backend.run.call_args.kwargs["reconnect"])
        self.follow.assert_not_called()
        self.assertEqual((self.backend.TASK_HELP, self.backend.WILDERNESS_HEALING), self.previous)

    def test_stop_preempts_navigation_and_interrupts_waiting(self):
        self.state.stop()
        with self.assertRaises(control.StopRequested):
            self.run_mode()
        self.backend.return_to_city.assert_not_called()
        self.backend.task_tap.assert_not_called()
        self.state = control.RuntimeControl()
        self.backend.check_stop = self.state.check
        self.backend.time.sleep = lambda _: self.state.stop()
        with self.assertRaises(control.StopRequested):
            self.run_mode()
        self.assertEqual(self.follow.call_count, 1)
        self.assertEqual((self.backend.TASK_HELP, self.backend.WILDERNESS_HEALING), self.previous)

    def test_dry_run_observes_without_navigation_or_dispatch(self):
        self.run_mode(dry_run=True)
        self.backend.color_screenshot.assert_called_once_with("test")
        self.backend.return_to_city.assert_not_called()
        self.backend.task_tap.assert_not_called()
        self.drive.assert_not_called()
        self.follow.assert_not_called()

    def test_guard_interrupts_disconnected_navigation_before_predicate(self):
        guarded = bear._ConnectedBot(self.backend, None, None)
        for forced, network in [(True, None), (False, (780, 1480))]:
            self.backend.forced_offline.return_value = forced
            self.backend.network_reconnect_target.return_value = network
            with self.assertRaises(bear.ReconnectRequired):
                guarded.task_screen("test")

    def test_uncertain_dispatch_stops_without_retrying(self):
        self.follow.side_effect = RuntimeError("未确认出征结果")
        with self.assertRaisesRegex(RuntimeError, "未确认出征结果"):
            self.run_mode()
        self.follow.assert_called_once()
        self.backend.run.assert_not_called()
        self.assertEqual(self.backend.emit_task_event.call_args.kwargs["status"], "failed")


class ConnectedBearScreenTest(unittest.TestCase):
    def setUp(self):
        self.text = bot.open_template(bot.ROOT / "assets/emulator-forced-text.png").convert("L")
        self.reconnect = bot.open_template(bot.ROOT / "assets/emulator-reconnect.png").convert("L")
        self.guarded = bear._ConnectedBot(bot, self.text, self.reconnect)

    def test_real_rgb_frame_passes_grayscale_reconnect_check_and_keeps_color(self):
        with Image.open(bot.ROOT / "tests/fixtures/bear-wilderness.png") as source:
            image = source.convert("RGB")
        with (patch.object(bot, "task_screen", return_value=image),
              patch.object(bot, "network_reconnect_target", return_value=None),
              patch.object(bot, "game_foreground", return_value=True)):
            result = self.guarded.task_screen("test")
        self.assertIs(result, image)
        self.assertEqual(result.mode, "RGB")

    def test_rgb_offline_frame_interrupts_before_any_followup_action(self):
        image = Image.new("L", bot.EXPECTED_SIZE)
        image.paste(self.text, bot.OFFLINE_TEXT_BOX)
        image.paste(self.reconnect, bot.RECONNECT_BOX)
        with (patch.object(bot, "task_screen", return_value=image.convert("RGB")),
              patch.object(bot, "network_reconnect_target") as network,
              patch.object(bot, "game_foreground") as foreground,
              self.assertRaises(bear.ReconnectRequired)):
            self.guarded.task_screen("test")
        network.assert_not_called()
        foreground.assert_not_called()

    def test_other_resolution_continues_connection_checks(self):
        for size in ((1176, 2400), (540, 1170)):
            with self.subTest(size=size):
                image = Image.new("RGB", size)
                with (patch.object(bot, "task_screen", return_value=image),
                      patch.object(bot, "network_reconnect_target", return_value=None) as network,
                      patch.object(bot, "game_foreground", return_value=True) as foreground):
                    self.assertIs(self.guarded.task_screen("test"), image)
                network.assert_called_once_with(image)
                foreground.assert_called_once_with("test")


class BearCliTest(unittest.TestCase):
    def test_cli_routes_selected_modes_and_excludes_daily_tasks_and_hooks(self):
        lease = Mock()
        args = ["help_bot.py", "--device", "test", "--bear", "--bear-modes", "drive", "follow",
                "--forever", "--daily-tasks", "--heal", "--hunt", "--gather", "--no-reconnect"]

        def run_bear(*_args, **_kwargs):
            self.assertIsNone(bot.WILDERNESS_HEALING)

        with (patch.object(bot.sys, "argv", args),
              patch.object(bot, "adb", return_value=b"device"),
              patch.object(bot, "avd_key", return_value="avd:test"),
              patch.object(bot, "bind_console_emulator"),
              patch.object(bot, "maybe_cleanup_diagnostic_screenshots"),
              patch.object(bot, "DeviceLease", return_value=Mock(acquire=Mock(return_value=lease))),
              patch.object(stamina_state, "set_device_identity"),
              patch.object(stamina_state, "clear_device_identity"),
              patch.object(bot, "run_bear", side_effect=run_bear) as run,
              patch.object(bot, "run_help_clicker") as clicker,
              patch.object(bot, "return_to_city") as city,
              patch.object(bot, "run_schedule") as schedule,
              contextlib.redirect_stdout(io.StringIO())):
            bot.main()
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs, {"modes": ["drive", "follow"], "reconnect": False,
                                               "dry_run": False, "forever": True, "seconds": 120, "interval": 2})
        city.assert_not_called()
        clicker.assert_not_called()
        schedule.assert_not_called()
        lease.release.assert_called_once()

    def test_conflicting_modes_are_rejected_before_device_access(self):
        with (patch.object(bot.sys, "argv", ["help_bot.py", "--bear", "--help-clicker"]),
              patch.object(bot, "adb") as adb,
              contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised):
            bot.main()
        self.assertEqual(raised.exception.code, 2)
        adb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
