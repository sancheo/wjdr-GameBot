"""Regression coverage for the October 7 alliance navigation failure."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot
from test_help_bot import Clock
from test_runtime_recovery import asset, frame


class DonationNavigationTest(unittest.TestCase):
    def setUp(self):
        self.templates = {name: asset(name, "RGB") for name in bot.TASK_BOXES}
        self.rules = frame("donation-1007-current-5554")
        self.stronghold = frame("donation-1007-engineering-5554")
        self.alliance = frame("donation-1007-alliance-5554")
        self.loading = frame("donation-1007-entry-00")
        self.city = frame("feature-return")

    def test_menu_header_does_not_authorize_fixed_coordinate_click(self):
        self.assertTrue(bot.task_page(self.loading, self.templates, "alliance-title"))
        self.assertIsNone(bot.alliance_tech_target(self.loading, self.templates))
        target = bot.alliance_tech_target(self.alliance, self.templates)
        self.assertTrue(790 <= target[0] <= 960 and 1510 <= target[1] <= 1580)
        self.assertIsNone(bot.alliance_tech_target(self.stronghold, self.templates))
        self.assertIsNone(bot.alliance_tech_target(self.rules, self.templates))

    def test_button_is_located_after_menu_layout_changes(self):
        moved = self.alliance.copy()
        moved.paste(self.alliance.crop((520, 850, 1040, 2000)), (520, 1050))
        target = bot.alliance_tech_target(self.alliance, self.templates)
        moved_target = bot.alliance_tech_target(moved, self.templates)
        self.assertLessEqual(abs(moved_target[0] - target[0]), 3)
        self.assertLessEqual(abs(moved_target[1] - target[1] - 200), 3)

    def test_waits_for_stable_button_after_loading_and_animation(self):
        targets = [None, (870, 1450), (870, 1540), (872, 1542)]
        with (patch.object(bot, "alliance_tech_target", side_effect=targets),
              patch.object(bot, "task_screen", return_value=self.alliance) as capture):
            image, target = bot.wait_alliance_tech("test", self.loading, self.templates)
        self.assertIs(image, self.alliance)
        self.assertEqual(target, (872, 1542))
        self.assertEqual(capture.call_count, 3)

    def test_missing_menu_never_taps_old_technology_coordinates(self):
        with (patch.object(bot, "task_drawer", return_value=self.city),
              patch.object(bot, "task_label", return_value=(self.city, 260, 900)),
              patch.object(bot, "task_screen", return_value=self.loading),
              patch.object(bot, "task_tap") as tap,
              self.assertRaisesRegex(RuntimeError, "联盟科技入口未加载")):
            bot.run_city_tasks("test", asset("help-icon"), asset("city-nav"),
                               self.templates, initial_image=self.city, recruit=False)
        tap.assert_called_once_with("test", 800, 2240)

    def test_rules_require_title_and_close_and_work_in_greyscale(self):
        for mode in ("L", "RGB"):
            self.assertEqual(bot.alliance_back_target(self.rules.convert(mode)), (960, 620))
            self.assertEqual(bot.alliance_back_target(self.stronghold.convert(mode)), (60, 190))
        for box in ((370, 585, 710, 650), (930, 590, 990, 655)):
            image = self.rules.copy()
            image.paste("black", box)
            self.assertIsNone(bot.alliance_back_target(image))

    def test_startup_and_retry_recover_rules_stronghold_alliance_then_city(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                clock = Clock()
                frames = [self.rules, self.stronghold, self.alliance, self.city,
                          self.city, self.city]
                with (patch.object(bot.time, "monotonic", clock.monotonic),
                      patch.object(bot.time, "sleep", clock.sleep),
                      patch.object(bot, "screenshot", side_effect=[im.convert("L") for im in frames]),
                      patch.object(bot, "color_screenshot", return_value=self.rules),
                      patch.object(bot, "task_screen", side_effect=frames[1:]),
                      patch.object(bot, "forced_offline", return_value=False),
                      patch.object(bot, "task_tap") as tap,
                      contextlib.redirect_stdout(io.StringIO())):
                    if retry:
                        self.assertIs(bot.recover_task_city("test", asset("help-icon"),
                                                           asset("city-nav"), None, None), self.city)
                    else:
                        self.assertTrue(bot.return_to_city("test", asset("help-icon"),
                                                           asset("city-nav"), None, None))
                self.assertEqual([call.args[1:] for call in tap.call_args_list],
                                 [(960, 620), (60, 190), (60, 190)])


if __name__ == "__main__":
    unittest.main()
