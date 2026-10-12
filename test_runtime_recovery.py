"""Regressions for the October 5 emulator-5554 failures."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot
from test_help_bot import Clock


def frame(name):
    with Image.open(bot.ROOT / f"artifacts/{name}.png") as image:
        return image.convert("RGB")


def asset(name, mode="L"):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as image:
        return image.convert(mode)


class RuntimeRecoveryTest(unittest.TestCase):
    def test_5556_treasure_map_recovers_without_popup_ocr(self):
        treasure, city = frame('treasure-1007-current-5556'), frame('stamina-ready')
        clock = Clock()
        for retry in (False, True):
            with (self.subTest(retry=retry),
                  patch.object(bot.time, 'monotonic', clock.monotonic),
                  patch.object(bot.time, 'sleep', clock.sleep),
                  patch.object(bot, 'screenshot', side_effect=[im.convert('L') for im in
                               (treasure, treasure, city, city, city)]),
                  patch.object(bot, 'color_screenshot', return_value=treasure),
                  patch.object(bot, 'task_screen', return_value=city),
                  patch.object(bot, 'forced_offline', return_value=False),
                  patch.object(bot, 'recognize_text', side_effect=AssertionError('map exit needs no OCR')),
                  patch.object(bot, 'task_tap') as tap,
                  contextlib.redirect_stdout(io.StringIO())):
                if retry:
                    self.assertIs(bot.recover_task_city('test', asset('help-icon'), asset('city-nav'), None, None), city)
                else:
                    self.assertTrue(bot.return_to_city('test', asset('help-icon'), asset('city-nav'), None, None))
                tap.assert_called_once_with('test', 60, 190)

    def test_social_pages_in_color_and_greyscale(self):
        for name, expected in (("popularity-popup", (540, 1540)),
                               ("profile-page", (60, 190)), ("flow-chat-page", (60, 190)),
                               ("stamina-ready", None), ("feature-tech", None),
                               ("hold-before", None), ("training-resource-shortage", None)):
            for mode in ("RGB", "L"):
                with self.subTest(name=name, mode=mode):
                    self.assertEqual(bot.social_back_target(frame(name).convert(mode)), expected)

    def test_popularity_requires_both_title_and_acknowledgement(self):
        for box in ((360, 780, 725, 860), (445, 1510, 645, 1565)):
            image = frame("popularity-popup")
            image.paste("black", box)
            self.assertIsNone(bot.social_back_target(image))

    def test_startup_leaves_popularity_then_profile(self):
        city = frame("stamina-ready")
        frames = [frame("popularity-popup"), frame("profile-page"), city, city, city]
        with (patch.object(bot, "screenshot", side_effect=[im.convert("L") for im in frames]),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "task_tap") as tap, patch.object(bot.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("test", asset("help-icon"), asset("city-nav"), None, None))
        self.assertEqual([c.args[1:] for c in tap.call_args_list], [(540, 1540), (60, 190)])

    def test_city_recovery_closes_search_before_using_town_button(self):
        search = frame('gather-1007-back')
        wilderness, city = frame('gather-1007-search-closed'), frame('stamina-ready')
        clock = Clock()
        with (patch.object(bot, 'TOWN_TEMPLATE', asset('town-button')),
              patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'screenshot', side_effect=[im.convert('L') for im in
                           (search, wilderness, city, city, city)]),
              patch.object(bot, 'task_screen', return_value=wilderness),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'task_back') as back, patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city('test', asset('help-icon'), asset('city-nav'), None, None))
        back.assert_called_once_with('test')
        tap.assert_called_once_with('test', 960, 2230)

    def test_retry_recovery_closes_search_before_city_navigation(self):
        search, wilderness, city = map(frame, (
            'gather-1007-back', 'gather-1007-search-closed', 'stamina-ready'))
        with (patch.object(bot, 'TOWN_TEMPLATE', asset('town-button')),
              patch.object(bot, 'color_screenshot', return_value=search),
              patch.object(bot, 'task_screen', side_effect=[wilderness, city]),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'task_back') as back, patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'return_to_city') as fallback):
            self.assertIs(bot.recover_task_city('test', asset('help-icon'), asset('city-nav'), None, None), city)
        back.assert_called_once_with('test')
        tap.assert_called_once_with('test', 960, 2230)
        fallback.assert_not_called()

    def test_retry_leaves_popup_profile_and_delayed_chat(self):
        popup, profile, chat, city = map(frame, ("popularity-popup", "profile-page", "flow-chat-page", "stamina-ready"))
        with (patch.object(bot, "color_screenshot", return_value=popup),
              patch.object(bot, "task_screen", side_effect=[profile, chat, city]),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "return_to_city", side_effect=AssertionError("must recover directly"))):
            self.assertIs(bot.recover_task_city("test", asset("help-icon"), asset("city-nav"), None, None), city)
        self.assertEqual([c.args[1:] for c in tap.call_args_list], [(540, 1540), (60, 190), (60, 190)])

    def test_task_capture_closes_a_delayed_chat_without_priority_help(self):
        chat, city = map(frame, ("flow-chat-page", "stamina-ready"))
        with (patch.object(bot, "color_screenshot", side_effect=[chat, city]),
              patch.object(bot, "TASK_HELP", None), patch.object(bot, "task_tap") as tap,
              patch.object(bot.time, "sleep")):
            self.assertIs(bot.task_screen("test", help_enabled=False), city)
        tap.assert_called_once_with("test", 60, 190)

    def test_stamina_does_not_click_help_while_observing_warehouse(self):
        city = frame("stamina-claimed")
        with (patch.object(bot, "color_screenshot", return_value=city),
              patch.object(bot, "TASK_HELP", (asset("help-icon"), asset("city-nav"), None)),
              patch.object(bot, "HELP_RETRY_AT", 0),
              patch.object(bot, "inspect", return_value=(True, (0, 0, 0), 0)),
              patch.object(bot, "task_tap") as tap, patch.object(bot, "adb") as adb,
              patch.object(bot.time, "sleep"), contextlib.redirect_stdout(io.StringIO())):
            result = bot.run_stamina("test", asset("help-icon"), asset("city-nav"), asset("drawer-open", "RGB"),
                                     initial_image=city)
        self.assertIs(result, city)
        tap.assert_not_called()
        adb.assert_not_called()

    def test_monitor_recovers_delayed_chat_and_dry_run_does_not_tap(self):
        chat, city = frame("flow-chat-page").convert("L"), frame("stamina-ready").convert("L")
        for dry_run in (False, True):
            clock = Clock()
            with (self.subTest(dry_run=dry_run), patch.object(bot.time, "monotonic", clock.monotonic),
                  patch.object(bot.time, "sleep", clock.sleep),
                  patch.object(bot, "screenshot", side_effect=[chat, city]),
                  patch.object(bot, "forced_offline", return_value=False),
                  patch.object(bot, "task_tap") as tap, contextlib.redirect_stdout(io.StringIO())):
                bot.run("test", 2, 1, dry_run, 10, asset("help-icon"), asset("city-nav"), None, None,
                        help_enabled=False)
            self.assertEqual(tap.call_count, 0 if dry_run else 1)

    def test_alliance_recovery_covers_detail_and_list(self):
        for name, target in (("hold-before", (960, 575)), ("feature-tech", (60, 190)),
                             ("feature-alliance", (60, 190)), ("stamina-ready", None)):
            self.assertEqual(bot.alliance_back_target(frame(name)), target)

    def test_tech_header_does_not_prove_the_tree_has_loaded(self):
        templates = {name: asset(name, "RGB") for name in ("tech-title", "tech-detail")}
        loading = frame("alliance-tech-loading")
        self.assertTrue(bot.task_page(loading, templates, "tech-title"))
        self.assertFalse(bot.tech_list_ready(loading, templates))
        self.assertTrue(bot.tech_list_ready(frame("feature-tech"), templates))
        self.assertTrue(bot.tech_detail_ready(frame("hold-before"), templates))
        self.assertTrue(bot.tech_detail_ready(frame("hold-exhausted"), templates))
        detail_loading = frame("hold-before")
        detail_loading.paste((192, 212, 235), (740, 1690, 900, 1745))
        self.assertTrue(bot.task_page(detail_loading, templates, "tech-detail"))
        self.assertFalse(bot.tech_detail_ready(detail_loading, templates))

    def test_training_endpoint_ignores_portrait_animation_but_detects_scroll(self):
        left = frame("upgrade-spear-left")
        animated = left.copy()
        animated.paste("white", (500, 1360, 600, 1440))
        self.assertGreater(bot.difference(left.crop(bot.TRAIN_UNIT_ROW_BOX), animated.crop(bot.TRAIN_UNIT_ROW_BOX)), 2)
        self.assertTrue(bot.training_row_stable(left, animated))
        self.assertFalse(bot.training_row_stable(left, frame("upgrade-spear-right")))
        self.assertFalse(bot.training_row_stable(left, frame("upgrade-spear-end")))

    def test_crystal_relocates_and_retries_a_missed_tap(self):
        image = Image.new("RGB", bot.EXPECTED_SIZE)
        with (patch.object(bot, "island_page", return_value=True),
              patch.object(bot, "crystal_target", side_effect=[(200, 700)] + [(210, 710)] * 8 + [None] * 5),
              patch.object(bot, "task_screen", return_value=image), patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.collect_crystals("test", image, None), image)
        self.assertEqual([c.args[1:] for c in tap.call_args_list], [(200, 700), (210, 710)])

    def test_crystal_retry_is_bounded_and_never_assumes_success(self):
        image = Image.new("RGB", bot.EXPECTED_SIZE)
        with (patch.object(bot, "island_page", return_value=True),
              patch.object(bot, "crystal_target", return_value=(200, 700)),
              patch.object(bot, "task_screen", return_value=image), patch.object(bot, "task_tap") as tap,
              self.assertRaisesRegex(RuntimeError, "领取图标仍未消失")):
            bot.collect_crystals("test", image, None)
        self.assertEqual(tap.call_count, 3)

    def test_donation_waits_for_detail_and_returns_without_repeating_navigation(self):
        city, drawer, alliance, tech, detail, exhausted = map(frame, (
            "feature-return", "feature-drawer-middle", "feature-alliance", "feature-tech", "hold-before", "hold-exhausted"))
        frames = [alliance, alliance, frame("alliance-tech-loading"), tech, tech, detail] + [detail] * 6 + [exhausted,
                  exhausted, tech, tech, alliance, alliance, city]
        templates = {name: asset(name, "RGB") for name in (*bot.TASK_BOXES, "donate-label", "free-label", "thumb")}
        with (patch.object(bot, "task_drawer", return_value=drawer),
              patch.object(bot, "task_label", return_value=(drawer, 260, 900)),
              patch.object(bot, "task_screen", side_effect=frames),
              patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "task_tap") as tap, patch.object(bot, "adb") as adb,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_city_tasks("test", asset("help-icon"), asset("city-nav"), templates,
                                           initial_image=city, recruit=False, keep_open=True), drawer)
        actions = [c.args[1:] for c in tap.call_args_list]
        self.assertEqual(actions.count((760, 1810)), 1)
        self.assertEqual(actions.count((60, 190)), 2)
        adb.assert_called_once_with("test", "shell", "input", "swipe", "760", "1810", "760", "1810", "3000")


if __name__ == "__main__":
    unittest.main()
