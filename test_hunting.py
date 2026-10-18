"""Monster selection, level caps, failed searches and field-priority regressions."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import gui_web
import help_bot as bot
import hunting
from emulator_config import load_config, save_config
from test_help_bot import Clock


class HuntingTest(unittest.TestCase):
    def test_default_config_migration_and_per_emulator_level_roundtrip(self):
        config = gui_web.checked_config({"options": {"intelligence": False}})
        self.assertTrue(all(config["options"][name] for name in ("hunt", "beast", "polar_beast")))
        config["hunt_levels"].update(beast="30级", polar_beast="6级")
        command = gui_web.build_command(config, "emulator-test")
        self.assertIn("--hunt", command)
        self.assertEqual(command[command.index("--beast-level") + 1], "30")
        self.assertEqual(command[command.index("--polar-beast-level") + 1], "6")
        with tempfile.TemporaryDirectory() as root:
            save_config(root, "avd:test", "emulator-test", config)
            self.assertEqual(gui_web.checked_config(load_config(root, "avd:test"), from_disk=True), config)

    def test_empty_selection_and_out_of_range_config_rejected(self):
        config = gui_web.default_config()
        config["options"].update(beast=False, polar_beast=False)
        with self.assertRaisesRegex(ValueError, "至少"):
            gui_web.checked_config(config)
        self.assertTrue(gui_web.checked_config(config, from_disk=True)["options"]["beast"])
        for kind, value in (("beast", "31级"), ("polar_beast", "1级"), ("polar_beast", "7级")):
            config = gui_web.default_config()
            config["hunt_levels"][kind] = value
            with self.assertRaises(ValueError):
                gui_web.checked_config(config)

    def test_beast_caps_to_limit_while_ice_uses_configured_level(self):
        frame = Image.new("RGB", bot.EXPECTED_SIZE)
        for kind, configured, expected in (("beast", 30, 19), ("beast", 5, 5), ("polar_beast", 6, 6)):
            with (patch.object(bot, "gather_search_page", return_value=True),
                  patch.object(bot, "intelligence_text_target", return_value=(130, 1835)),
                  patch.object(bot, "task_screen", return_value=frame), patch.object(bot, "task_tap"),
                  patch.object(hunting, "challenge_limit", return_value=19) as limit,
                  patch.object(hunting, "set_level", return_value=frame) as adjust,
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(hunting.prepare(bot, "test", frame, kind, configured)[1], expected)
                self.assertEqual(adjust.call_args.args[3], expected)
                self.assertEqual(limit.call_count, 0 if kind == "polar_beast" else 1)

    def test_numeric_levels_include_two_digits_and_ignore_noise(self):
        frame = Image.new("RGB", bot.EXPECTED_SIZE)
        with patch.object(bot, "recognize_text", return_value=[("29", (0, 0, 10, 10))]):
            self.assertEqual(hunting.level(bot, frame, 30), 29)
            self.assertIsNone(hunting.level(bot, frame, 6))
        with patch.object(bot, "recognize_text", return_value=[("最高可挑战 29 级野兽", (0, 0, 10, 10))]):
            self.assertEqual(hunting.challenge_limit(bot, frame), 29)

    def test_level_clicks_follow_verified_current_value(self):
        frame = Image.new("RGB", bot.EXPECTED_SIZE)
        with (patch.object(bot, "gather_search_page", return_value=True),
              patch.object(hunting, "level", side_effect=[None, 30, 29, 28]),
              patch.object(hunting, "minimum_level", return_value=False),
              patch.object(bot, "task_screen", return_value=frame), patch.object(bot, "task_tap") as taps):
            hunting.set_level(bot, "test", frame, 28, 30)
        self.assertEqual([call.args[1:] for call in taps.call_args_list], [(105, 2000), (105, 2000)])

    def test_server_minimum_reports_configuration_error_without_futile_clicks(self):
        frame = Image.new("RGB", bot.EXPECTED_SIZE)
        with (patch.object(bot, "gather_search_page", return_value=True),
              patch.object(hunting, "level", return_value=2),
              patch.object(hunting, "minimum_level", return_value=True), patch.object(bot, "task_tap") as taps):
            with self.assertRaisesRegex(RuntimeError, "最低可搜索 2 级"):
                hunting.set_level(bot, "test", frame, 1, 6)
        taps.assert_not_called()

    def test_missing_attack_reports_exception_without_dispatch(self):
        frame = Image.new("RGB", bot.EXPECTED_SIZE)
        with (patch.object(bot, "task_screen", return_value=frame),
              patch.object(bot, "intelligence_queue_full", return_value=False),
              patch.object(hunting, "notice", return_value=None),
              patch.object(hunting, "action", return_value=None), patch.object(bot, "task_tap") as taps):
            with self.assertRaisesRegex(RuntimeError, "未发现攻击"):
                hunting.dispatch(bot, "test", frame, "beast")
        taps.assert_called_once_with("test", 540, 2240)

    def test_ice_dispatch_checks_queue_before_detail_can_cover_header(self):
        initial, detail, rally, squad, returned = [Image.new("RGB", bot.EXPECTED_SIZE) for _ in range(5)]
        def squad_button(image, *args):
            return (826, 2214) if image is squad else None
        with (patch.object(bot, "task_screen", side_effect=[detail, rally, squad, returned]),
              patch.object(bot, "intelligence_queue_full", return_value=False) as queue,
              patch.object(hunting, "notice", return_value=None),
              patch.object(hunting, "action", return_value=(540, 1008)),
              patch.object(hunting, "button_target", return_value=(540, 1534)),
              patch.object(bot, "intelligence_text_target", side_effect=squad_button),
              patch.object(bot, "town_button", side_effect=lambda image: image is returned),
              patch.object(bot, "gather_search_page", return_value=False),
              patch.object(hunting, "leave", return_value=returned), patch.object(bot, "task_tap") as taps):
            self.assertEqual(hunting.dispatch(bot, "test", initial, "polar_beast"), (returned, True))
        queue.assert_called_once_with(initial)
        self.assertEqual([call.args[1:] for call in taps.call_args_list],
                         [(540, 2240), (540, 1008), (540, 1534), (826, 2214)])

    def test_checked_kinds_run_in_fixed_order(self):
        frame = Image.new("RGB", bot.EXPECTED_SIZE)
        with (patch.object(bot, "task_screen", return_value=frame),
              patch.object(bot, "enter_wilderness", return_value=frame),
              patch.object(bot, "town_button", return_value=True),
              patch.object(bot, "intelligence_stamina", return_value=100),
              patch.object(bot, "intelligence_queue_full", return_value=False), patch.object(bot, "task_tap"),
              patch.object(hunting, "prepare", return_value=(frame, 1)) as prepare,
              patch.object(hunting, "dispatch", return_value=(frame, True)),
              contextlib.redirect_stdout(io.StringIO())):
            hunting.run(bot, "test", None, None, ["polar_beast", "beast"])
        self.assertEqual([call.args[3] for call in prepare.call_args_list], ["beast", "polar_beast"])

    def test_field_order_and_each_intelligence_round_releases_hunt(self):
        clock, events, timers = Clock(), [], {}
        def intelligence():
            events.append("intelligence")
            bot.record_timer(timers, "intelligence", 10)
        def monitor(_, seconds, *args, **kwargs):
            clock.sleep(seconds)
        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot.time, "sleep", clock.sleep), patch.object(bot, "run", side_effect=monitor),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule("test", 11, 2, False,
                             {"gather": lambda: events.append("gather"),
                              "hunt": lambda: events.append("hunt"), "intelligence": intelligence},
                             *([None] * 4), timers=timers, help_enabled=False)
        self.assertEqual(events, ["intelligence", "hunt", "gather", "intelligence", "hunt"])

    def test_hunt_precedes_gather_when_intelligence_disabled(self):
        clock, events = Clock(), []
        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot, "run", side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds)),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule("test", 1, 2, False,
                             {"gather": lambda: events.append("gather"), "hunt": lambda: events.append("hunt")},
                             *([None] * 4), help_enabled=False)
        self.assertEqual(events, ["hunt", "gather"])


class HuntingStaminaTest(unittest.TestCase):
    def setUp(self):
        self.frame = Image.new("RGB", bot.EXPECTED_SIZE)
        self.clock = Clock()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (("task_screen", self.frame), ("enter_wilderness", self.frame),
                            ("town_button", True), ("intelligence_queue_full", False),
                            ("pet_page", False), ("task_page", False)):
            self.stack.enter_context(patch.object(bot, name, return_value=value))
        self.stack.enter_context(patch.object(bot.time, "monotonic", self.clock.monotonic))
        self.stack.enter_context(patch.object(bot, "run", side_effect=lambda _, seconds, *a, **kw: self.clock.sleep(seconds)))
        self.taps = self.stack.enter_context(patch.object(bot, "task_tap"))
        self.prepare = self.stack.enter_context(patch.object(hunting, "prepare", return_value=(self.frame, 2)))
        self.dispatch = self.stack.enter_context(patch.object(hunting, "dispatch", return_value=(self.frame, True)))
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_shortage_and_unknown_stamina_never_open_search(self):
        for kind, value in (("beast", 0), ("beast", 9), ("polar_beast", 24), ("polar_beast", None)):
            with self.subTest(kind=kind, stamina=value), patch.object(bot, "intelligence_stamina", return_value=value):
                timers = {}
                self.assertIs(hunting.run(bot, "test", None, None, [kind], timers=timers), self.frame)
                self.assertEqual(timers["hunt"], 160)
                self.assertEqual(timers[hunting.STAMINA_RECHECK_TIMER], 160)
        self.taps.assert_not_called()
        self.prepare.assert_not_called()
        self.dispatch.assert_not_called()

    def test_exact_required_stamina_resumes_and_clears_wait(self):
        for kind, value in hunting.STAMINA_COSTS.items():
            with self.subTest(kind=kind), patch.object(bot, "intelligence_stamina", return_value=value):
                timers = {hunting.STAMINA_RECHECK_TIMER: 100}
                hunting.run(bot, "test", None, None, [kind], timers=timers)
                self.assertNotIn(hunting.STAMINA_RECHECK_TIMER, timers)
        self.assertEqual(self.prepare.call_count, 2)
        self.assertEqual(self.dispatch.call_count, 2)

    def test_stamina_is_read_again_after_first_kind_consumes_it(self):
        with patch.object(bot, "intelligence_stamina", side_effect=[30, 20]) as read:
            timers = {}
            hunting.run(bot, "test", None, None, timers=timers)
        self.assertEqual(read.call_count, 2)
        self.assertEqual(self.prepare.call_args.args[3], "beast")
        self.assertEqual(self.prepare.call_count, 1)
        self.assertEqual(self.dispatch.call_count, 1)
        self.assertEqual(timers["hunt"], 160)

    def test_delayed_stamina_frame_is_read_before_search(self):
        with patch.object(bot, "intelligence_stamina", side_effect=[None, 25]) as read:
            hunting.run(bot, "test", None, None, ["polar_beast"])
        self.assertEqual(read.call_count, 2)
        self.dispatch.assert_called_once()

    def test_schedule_polls_stamina_and_intelligence_does_not_release_wait(self):
        timers, checks, intelligence_checks, heal_checks = {}, [], [], []
        def stamina(image):
            checks.append(self.clock.now)
            return 6 if self.clock.now < 220 else 25
        def intelligence():
            intelligence_checks.append(self.clock.now)
            bot.record_timer(timers, "intelligence", 10)
        with patch.object(bot, "intelligence_stamina", side_effect=stamina):
            bot.run_schedule("test", 121, 2, False,
                             {"intelligence": intelligence,
                              "hunt": lambda: hunting.run(bot, "test", None, None, ["polar_beast"], timers=timers),
                              "heal": lambda: heal_checks.append(self.clock.now)},
                             *([None] * 4), timers=timers, help_enabled=False)
        self.assertEqual(checks, [100, 160, 220])
        self.assertEqual(intelligence_checks, list(range(100, 221, 10)))
        self.assertGreater(len(heal_checks), 3)
        self.prepare.assert_called_once()
        self.dispatch.assert_called_once()

    def test_unknown_stamina_keeps_task_enabled_until_recognition_recovers(self):
        timers, checks = {}, []
        def stamina(image):
            checks.append(self.clock.now)
            return None if self.clock.now < 160 else 25
        with patch.object(bot, "intelligence_stamina", side_effect=stamina):
            bot.run_schedule("test", 61, 2, False,
                             {"hunt": lambda: hunting.run(bot, "test", None, None, ["polar_beast"], timers=timers)},
                             *([None] * 4), timers=timers, help_enabled=False)
        self.assertEqual(checks, [100, 100, 100, 160])
        self.dispatch.assert_called_once()
        self.assertNotIn("连续3次重试失败", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
