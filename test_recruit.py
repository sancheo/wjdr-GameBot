"""Recruitment regressions sampled on emulator-5556."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot


def asset(name, mode="RGB"):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as image:
        return image.convert(mode)


def frame(name):
    with Image.open(bot.ROOT / f"artifacts/{name}.png") as image:
        return image.convert("RGB")


class RecruitTest(unittest.TestCase):
    def setUp(self):
        self.templates = {name: asset(name) for name in bot.TASK_BOXES}

    def test_5556_title_survives_background_difference_in_color_and_gray(self):
        image = frame("recruit-5556-before")
        self.assertGreater(bot.difference(image.crop(bot.TASK_BOXES["recruit-title"]),
                                         self.templates["recruit-title"]), 12)
        for mode in ("RGB", "L"):
            self.assertTrue(bot.task_page(image.convert(mode), self.templates, "recruit-title"))
        for name in ("feature-return", "feature-drawer-top", "feature-tech", "feature-recruit-after"):
            self.assertFalse(bot.task_page(frame(name), self.templates, "recruit-title"))
        for color in ("black", "white", "lightblue"):
            self.assertFalse(bot.task_page(Image.new("RGB", bot.EXPECTED_SIZE, color),
                                           self.templates, "recruit-title"))

    def test_5556_both_free_buttons_are_recognized(self):
        for kind in bot.RECRUIT_TYPES:
            self.assertEqual(bot.recruit_button_state(frame("recruit-5556-before"),
                                                       self.templates, kind)[0], "free")

    def test_real_cooldown_buttons_are_never_free(self):
        image = frame("recruit-emulator-5556-cooldown-cooldown")
        for kind in bot.RECRUIT_TYPES:
            self.assertEqual(bot.recruit_button_state(image, self.templates, kind)[0], "cooldown")

    def test_drawer_free_text_fallback_completes_recruitment(self):
        templates = dict(self.templates, **{"free-label": asset("free-label")})
        drawer = frame("recruit-5556-drawer")
        frames = [frame("recruit-5556-before"), frame("feature-recruit-after"),
                  frame("recruit-emulator-5556-cooldown-free"), frame("feature-return")]
        with (patch.object(bot, "TASK_DRAWER_SCROLL", 0),
              patch.object(bot, "task_screen", side_effect=frames),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "find_green", return_value=(.85, 278, 1156)),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_city_tasks("test", asset("help-icon", "L"), asset("city-nav", "L"),
                                templates, initial_image=drawer, donate=False)
        self.assertIn(("test", 280, 1690), [call.args for call in tap.call_args_list])

    def test_paid_advanced_button_does_not_use_free_hint_above_it(self):
        image = frame("recruit-5556-before")
        image.paste((240, 170, 30), (130, 1630, 440, 1740))
        self.assertNotEqual(bot.recruit_button_state(image, self.templates, "recruit_advanced")[0], "free")

    def test_advanced_then_epic_uses_same_page_without_reopening_drawer(self):
        templates = dict(self.templates, **{"free-label": asset("free-label")})
        drawer = frame("recruit-5556-drawer")
        epic_free = frame("recruit-emulator-5556-cooldown-free")
        frames = [frame("recruit-5556-before"), frame("feature-recruit-after"), epic_free,
                  frame("feature-recruit-after"), frame("recruit-emulator-5556-cooldown-cooldown"),
                  frame("feature-return")]
        timers = {"recruit_epic": 99999}
        with (patch.object(bot, "task_screen", side_effect=frames),
              patch.object(bot, "inspect", return_value=(True, None, 0)),
              patch.object(bot, "task_drawer", return_value=drawer) as open_drawer,
              patch.object(bot, "recruit_row", return_value=(drawer, 1100)),
              patch.object(bot, "find_green", return_value=(1, 278, 1110)),
              patch.object(bot, "countdown", return_value=7200),
              patch.object(bot.time, "monotonic", return_value=100),
              patch.object(bot, "recruit_timer", return_value=drawer) as read_timer,
              patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            image = bot.run_city_tasks("test", None, None, templates, initial_image=drawer,
                                       donate=False, keep_open=True, timers=timers)
            self.assertIs(image, epic_free)
            self.assertEqual(timers, {"recruit_advanced": 7300, "recruit_epic": 0})
            image = bot.run_city_tasks("test", None, None, templates, initial_image=image,
                                       donate=False, recruit_kind="recruit_epic", keep_open=True, timers=timers)
        self.assertIs(image, drawer)
        open_drawer.assert_called_once()
        read_timer.assert_called_once()
        self.assertEqual(read_timer.call_args.args[-1], "recruit_epic")
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(600, 1115), (280, 1690), (60, 190), (280, 2230), (60, 190), (60, 190)])

    def test_existing_epic_cooldown_page_never_taps_recruit_button(self):
        image = frame("recruit-emulator-5556-cooldown-cooldown")
        city = frame("feature-return")
        with (patch.object(bot, "task_screen", return_value=city),
              patch.object(bot, "task_drawer") as open_drawer,
              patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            result = bot.run_city_tasks("test", asset("help-icon", "L"), asset("city-nav", "L"),
                                        self.templates, initial_image=image, donate=False,
                                        recruit_kind="recruit_epic")
        self.assertIs(result, city)
        tap.assert_called_once_with("test", 60, 190)
        open_drawer.assert_not_called()

    def test_owned_hero_result_is_recognized_without_exit_prompt(self):
        with Image.open(bot.ROOT / "tests/fixtures/recruit-owned-hero.png") as image:
            self.assertTrue(bot.recruit_hero_exit_page(image.convert("RGB")))
        self.assertFalse(bot.recruit_hero_exit_page(frame("recruit-5556-before")))

    def test_owned_advanced_hero_returns_to_free_epic_on_same_page(self):
        with Image.open(bot.ROOT / "tests/fixtures/recruit-owned-hero.png") as source:
            hero = source.convert("RGB")
        page = frame("recruit-emulator-5556-cooldown-free")
        with Image.open(bot.ROOT / "tests/fixtures/recruit-owned-reward.png") as source:
            reward = source.convert("RGB")
        frames = [hero, reward, page]
        timers = {}
        with (patch.object(bot, "task_screen", side_effect=frames),
              patch.object(bot, "task_drawer") as open_drawer,
              patch.object(bot, "countdown", return_value=7200),
              patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            image = bot.run_city_tasks("test", None, None, self.templates,
                                       initial_image=frame("recruit-5556-before"),
                                       donate=False, keep_open=True, timers=timers)
        self.assertIs(image, page)
        self.assertEqual([call.args[1:] for call in tap.call_args_list], [(280, 1690), (540, 2100), (60, 190)])
        self.assertEqual(timers["recruit_epic"], 0)
        open_drawer.assert_not_called()

    def test_green_advanced_button_still_requires_free_text(self):
        image = frame("recruit-5556-before")
        image.paste((30, 190, 50), (130, 1630, 440, 1740))
        self.assertNotEqual(bot.recruit_button_state(image, self.templates, "recruit_advanced")[0], "free")

    def test_main_city_recovery_exits_recruit_page(self):
        recruit = frame("recruit-5556-before").convert("L")
        city = frame("feature-return").convert("L")
        with (patch.object(bot, "screenshot", side_effect=[recruit, city, city, city]),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "task_tap") as tap, patch.object(bot.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("test", asset("help-icon", "L"),
                                                asset("city-nav", "L"), None, None))
        tap.assert_called_once_with("test", 60, 190)

    def test_recruitment_row_at_drawer_bottom_is_not_skipped(self):
        image = frame("recruit-5556-drawer")
        with (patch.object(bot, "task_drawer", return_value=image),
              patch.object(bot, "recognize_text", return_value=[("高级招募", (30, 770, 120, 30))]),
              patch.object(bot, "swipe_drawer") as swipe):
            _, bottom = bot.recruit_row("test", image, self.templates, "recruit_advanced")
        self.assertEqual(bottom, 1510)
        swipe.assert_not_called()

    def test_missing_advanced_row_above_current_view_resets_once(self):
        image = frame("recruit-5556-drawer")
        with (patch.object(bot, "task_drawer", return_value=image),
              patch.object(bot, "TASK_DRAWER_SCROLL", 3),
              patch.object(bot, "reset_drawer", return_value=image) as reset,
              patch.object(bot, "recognize_text", side_effect=[
                  [("史诗招募", (30, 100, 120, 30))],
                  [("高级招募", (30, 200, 120, 30))]])):
            _, bottom = bot.recruit_row("test", image, self.templates, "recruit_advanced")
        self.assertEqual(bottom, 940)
        reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
