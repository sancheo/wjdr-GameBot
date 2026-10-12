"""Exploration must tolerate loading delays and recover to the city."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot


def frame(name):
    with Image.open(bot.ROOT / f"artifacts/{name}.png") as image:
        return image.convert("RGB")


def asset(name):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as image:
        return image.convert("L")


class ExploreTest(unittest.TestCase):
    def setUp(self):
        self.templates = {name: asset(f"explore-{name}") for name in bot.EXPLORE_BOXES}
        self.help, self.nav = asset("help-icon"), asset("city-nav")

    def test_failure_screenshot_is_a_ready_page_and_can_be_exited_in_greyscale(self):
        image = frame("explore-5556-failure")
        self.assertEqual(bot.explore_claim_state(image, self.templates), "ready")
        self.assertEqual(bot.explore_back_target(image.convert("L")), (60, 190))
        image.paste("black", bot.EXPLORE_BOXES["title"])
        self.assertIsNone(bot.explore_back_target(image))
        self.assertIsNone(bot.explore_claim_state(image, self.templates))

    def test_loading_is_not_mistaken_for_a_cooldown_and_other_pages_are_not_exited(self):
        image = frame("explore-step5")
        self.assertEqual(bot.explore_claim_state(image, self.templates), "empty")
        image.paste("black", (835, 1675, 1000, 1750))
        self.assertIsNone(bot.explore_claim_state(image, self.templates))
        for name in ("stamina-ready", "training-resource-shortage", "feature-tech"):
            self.assertIsNone(bot.explore_back_target(frame(name)), name)

    def test_waits_at_every_stage_without_repeating_claims_or_starting_a_battle(self):
        city, ready, income, reward, empty = map(frame, ("explore-step1", "explore-5556-failure",
                                                       "explore-step3", "explore-step4", "explore-step5"))
        loading = ready.copy()
        loading.paste("black", (835, 1675, 1000, 1750))
        frames = [city, loading, ready, ready, income, income, reward, reward, empty, empty, city]
        with (patch.object(bot, "color_screenshot", return_value=city),
              patch.object(bot, "task_screen", side_effect=frames),
              patch.object(bot, "task_tap") as tap, contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_explore("test", self.help, self.nav, self.templates), city)
        self.assertEqual([c.args[1:] for c in tap.call_args_list],
                         [(95, 2240), (915, 1718), (540, 1660), (540, 2050), (60, 190)])

    def test_no_red_dot_still_enters_and_cooldown_toast_defers_without_claiming(self):
        city, empty, cooldown = map(frame, ("explore-step1", "explore-step5",
                                           "explore-direct-cooldown-income-5554"))
        city.paste("black", (130, 2180, 160, 2210))
        timers = {}
        with (patch.object(bot, "color_screenshot", return_value=city),
              patch.object(bot, "task_screen", side_effect=[empty, cooldown, city]),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot.time, "monotonic", return_value=100),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertIs(bot.run_explore("test", self.help, self.nav, self.templates, timers), city)
        self.assertEqual([c.args[1:] for c in tap.call_args_list], [(95, 2240), (915, 1718), (60, 190)])
        self.assertEqual(timers["explore"], 681)
        self.assertIn("尚未到领取时间", output.getvalue())
        self.assertNotIn("已领取探险经验", output.getvalue())

    def test_sampled_toast_countdown_and_no_false_positive_on_other_pages(self):
        cooldown = frame("explore-direct-cooldown-income-5554")
        self.assertEqual(bot.explore_cooldown(cooldown, self.templates), 580)
        for name in ("explore-step1", "explore-step3", "explore-step4", "explore-step5"):
            self.assertIsNone(bot.explore_cooldown(frame(name), self.templates), name)
        with (patch.object(bot, "recognize_text", return_value=[("探险小队正努力探险中", (0, 0, 1, 1))]),
              self.assertRaisesRegex(RuntimeError, "未识别剩余倒计时")):
            bot.explore_cooldown(cooldown, self.templates)

    def test_missing_toast_on_consistently_disabled_button_defers_without_success(self):
        city, empty = map(frame, ("explore-step1", "explore-step5"))
        timers = {}
        with (patch.object(bot, "color_screenshot", return_value=city),
              patch.object(bot, "task_screen", side_effect=[empty] * 6 + [city]) as screen,
              patch.object(bot, "task_tap") as tap,
              patch.object(bot.time, "monotonic", return_value=100),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertIs(bot.run_explore("test", self.help, self.nav, self.templates, timers), city)
        self.assertEqual(screen.call_count, 7)
        self.assertEqual(timers["explore"], 700)
        self.assertIn("10 分钟后重新检查", output.getvalue())
        self.assertNotIn("已领取探险经验", output.getvalue())
        self.assertEqual([c.args[1:] for c in tap.call_args_list], [(95, 2240), (915, 1718), (60, 190)])

    def test_missing_toast_does_not_hide_unknown_or_unresponsive_ready_button(self):
        city, empty, ready = map(frame, ("explore-step1", "explore-step5", "explore-5556-failure"))
        unknown = empty.copy()
        unknown.paste("black", (835, 1675, 1000, 1750))
        for entry, responses in ((ready, [ready] * 5), (empty, [empty] * 4 + [unknown])):
            timers = {}
            with (self.subTest(entry=entry is ready),
                  patch.object(bot, "color_screenshot", return_value=city),
                  patch.object(bot, "task_screen", side_effect=[entry] + responses),
                  patch.object(bot, "task_tap"), contextlib.redirect_stdout(io.StringIO()),
                  self.assertRaisesRegex(RuntimeError, "未确认探险挂机收益领取弹窗")):
                bot.run_explore("test", self.help, self.nav, self.templates, timers)
            self.assertNotIn("explore", timers)

    def test_success_schedules_8_hours_59_minutes_from_claim_and_ignores_red_dot(self):
        city, ready, income, reward, empty, result = map(frame, (
            "explore-current-5554", "explore-direct-entry-5554", "explore-direct-income-5554",
            "explore-direct-reward-5554", "explore-direct-empty-5554", "explore-direct-result-5554"))
        city.paste("black", (130, 2180, 160, 2210))
        timers = {}
        with (patch.object(bot, "color_screenshot", return_value=city),
              patch.object(bot, "task_screen", side_effect=[ready, income, reward, empty, result]),
              patch.object(bot, "task_tap"), patch.object(bot.time, "monotonic", return_value=100),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_explore("test", self.help, self.nav, self.templates, timers), result)
        self.assertEqual(timers["explore"], 32440)

    def test_cooldown_after_income_claim_is_not_reported_as_success(self):
        city, ready, income, cooldown = map(frame, ("explore-step1", "explore-5556-failure",
                                                  "explore-step3", "explore-direct-cooldown-income-5554"))
        timers = {}
        with (patch.object(bot, "color_screenshot", return_value=city),
              patch.object(bot, "task_screen", side_effect=[ready, income, cooldown, city]),
              patch.object(bot, "task_tap") as tap, patch.object(bot.time, "monotonic", return_value=100),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertIs(bot.run_explore("test", self.help, self.nav, self.templates, timers), city)
        self.assertEqual(timers["explore"], 681)
        self.assertNotIn("已领取探险经验", output.getvalue())
        self.assertEqual([c.args[1:] for c in tap.call_args_list],
                         [(95, 2240), (915, 1718), (540, 1660), (60, 190)])

    def test_unknown_entry_times_out_without_claiming(self):
        city = frame("explore-step1")
        loading = frame("explore-5556-failure")
        loading.paste("black", (835, 1675, 1000, 1750))
        with (patch.object(bot, "color_screenshot", return_value=city),
              patch.object(bot, "task_screen", return_value=loading) as screen,
              patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO()),
              self.assertRaisesRegex(RuntimeError, "未确认探险经验可领取页面")):
            bot.run_explore("test", self.help, self.nav, self.templates)
        self.assertEqual(screen.call_count, 5)
        tap.assert_called_once_with("test", 95, 2240)

    def test_unknown_post_claim_state_does_not_report_success(self):
        city, ready, income, reward = map(frame, ("explore-step1", "explore-5556-failure",
                                                  "explore-step3", "explore-step4"))
        with (patch.object(bot, "color_screenshot", return_value=city),
              patch.object(bot, "task_screen", side_effect=[ready, income, reward] + [ready] * 5),
              patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO()) as output,
              self.assertRaisesRegex(RuntimeError, "探险经验领取后状态未变化")):
            bot.run_explore("test", self.help, self.nav, self.templates)
        self.assertNotIn("已领取探险经验", output.getvalue())
        self.assertEqual([c.args[1:] for c in tap.call_args_list],
                         [(95, 2240), (915, 1718), (540, 1660), (540, 2050)])

    def test_startup_recovers_main_page_and_income_modal(self):
        ready, income, city = map(frame, ("explore-5556-failure", "explore-step3", "explore-step6"))
        for initial in (ready, income):
            frames = ([income] if initial is income else []) + [ready, city, city, city]
            with (self.subTest(modal=initial is income),
                  patch.object(bot, "screenshot", side_effect=[im.convert("L") for im in frames]),
                  patch.object(bot, "forced_offline", return_value=False),
                  patch.object(bot, "task_tap") as tap, patch.object(bot.time, "sleep"),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertTrue(bot.return_to_city("test", self.help, self.nav, None, None))
            actions = ([(945, 720)] if initial is income else []) + [(60, 190)]
            self.assertEqual([c.args[1:] for c in tap.call_args_list], actions)

    def test_retry_recovers_income_and_reward_modal(self):
        ready, city = frame("explore-5556-failure"), frame("explore-step6")
        for name, target in (("explore-step3", (945, 720)), ("explore-step4", (540, 2050)),
                             ("explore-income-pre200", (945, 691))):
            with (self.subTest(page=name), patch.object(bot, "color_screenshot", return_value=frame(name)),
                  patch.object(bot, "task_screen", side_effect=[ready, city]),
                  patch.object(bot, "forced_offline", return_value=False),
                  patch.object(bot, "task_tap") as tap,
                  patch.object(bot, "return_to_city", side_effect=AssertionError("must recover directly"))):
                self.assertIs(bot.recover_task_city("test", self.help, self.nav, None, None), city)
            self.assertEqual([c.args[1:] for c in tap.call_args_list], [target, (60, 190)])

    def test_taller_pre200_income_can_be_claimed_and_recovered_at_startup(self):
        city, ready, income, reward, empty = map(frame, ("explore-step1", "explore-5556-failure",
                                                        "explore-income-pre200", "explore-step4",
                                                        "explore-step5"))
        for mode in ("RGB", "L"):
            self.assertTrue(bot.explore_page(income.convert(mode), self.templates, "income"))
            self.assertEqual(bot.explore_back_target(income.convert(mode)), (945, 691))
        with (patch.object(bot, "color_screenshot", return_value=city),
              patch.object(bot, "task_screen", side_effect=[ready, income, reward, empty, city]),
              patch.object(bot, "task_tap") as tap, contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_explore("test", self.help, self.nav, self.templates), city)
        self.assertEqual([c.args[1:] for c in tap.call_args_list],
                         [(95, 2240), (915, 1718), (540, 1660), (540, 2050), (60, 190)])
        with (patch.object(bot, "screenshot", side_effect=[im.convert("L") for im in
                                                          (income, ready, city, city, city)]),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "task_tap") as tap, patch.object(bot.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("test", self.help, self.nav, None, None))
        self.assertEqual([c.args[1:] for c in tap.call_args_list], [(945, 691), (60, 190)])


if __name__ == "__main__":
    unittest.main()
