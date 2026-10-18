"""Actual USB-phone frames, input round trips and reference-device regressions."""

from pathlib import Path
import unittest

import numpy as np
from PIL import Image

import alliance_help_clicker
import gui_web
import help_bot as bot
import task_rewards
from screen_geometry import ScreenAdapter, detect

ROOT = Path(__file__).resolve().parent


class ScreenGeometryTest(unittest.TestCase):
    def test_phone_alliance_treasure_modal_title_button_and_close(self):
        raw = Image.open(ROOT / "tests/fixtures/phone-alliance-treasure-1016.png").convert("RGB")
        adapter = ScreenAdapter()
        image = adapter.frame("usb", raw)
        self.assertIsNotNone(bot.intelligence_text_target(image, ("联盟宝藏",), (400, 470, 680, 550)))
        target = bot.intelligence_text_target(image, ("一键领取",), (250, 1790, 830, 1990))
        self.assertIsNotNone(target)
        self.assertTrue(480 <= target[0] <= 590 and 1860 <= target[1] <= 1940)
        x, y = adapter.geometry("usb").point(1000, 510)
        self.assertAlmostEqual(x, 1089, delta=3)
        self.assertTrue(450 <= y <= 470)

    def test_phone_alliance_reward_exit_text_is_not_clipped(self):
        raw = Image.open(ROOT / "tests/fixtures/phone-alliance-reward-1016.png").convert("RGB")
        image = detect(raw).normalize(raw)
        self.assertIsNotNone(bot.intelligence_text_target(
            image, ("点击任意位置退出",), (250, 2090, 850, 2280)))

    def frame(self, name):
        with Image.open(ROOT / "tests/fixtures" / f"phone-{name}-1176.png") as image:
            return image.convert("RGB")

    def assets(self):
        return [bot.open_template(ROOT / f"assets/emulator-{name}.png").convert("L")
                for name in ("help-icon", "city-nav")]

    def test_real_phone_city_uses_existing_templates(self):
        adapter = ScreenAdapter()
        raw = self.frame("city")
        image = adapter.frame("usb", raw)
        help_icon, nav = self.assets()
        city, match, score = bot.inspect(image.convert("L"), help_icon, nav)
        self.assertTrue(city)
        self.assertLess(score, 5)
        # No request icon is visible in this real frame; do not invent a tap.
        self.assertGreater(match[0], bot.HELP_LIMIT)
        self.assertGreater(bot.difference(raw.resize((1080, 2340)).convert("L").crop(bot.NAV_BOX), nav),
                           bot.NAV_LIMIT)

    def test_real_phone_fullscreen_headers_and_help_page(self):
        adapter = ScreenAdapter()
        alliance = adapter.frame("usb", self.frame("alliance"))
        templates = {"alliance-title": bot.open_template(ROOT / "assets/emulator-alliance-title.png")}
        self.assertTrue(bot.task_page(alliance, templates, "alliance-title"))
        help_page = adapter.frame("usb", self.frame("help"))
        self.assertTrue(alliance_help_clicker.help_page(bot, help_page))
        explore = adapter.frame("usb", self.frame("explore"))
        templates = {name: bot.open_template(ROOT / f"assets/emulator-explore-{name}.png").convert("L")
                     for name in bot.EXPLORE_BOXES}
        self.assertTrue(bot.explore_page(explore, templates, "title"))
        income = adapter.frame("usb", self.frame("explore-income"))
        self.assertIsNotNone(bot.explore_page_box(income, templates, "income"))
        self.assertTrue(bot.green_button(income, (350, 1600, 740, 1730)))
        reward = adapter.frame("usb", self.frame("explore-reward"))
        self.assertTrue(bot.explore_page(reward, templates, "reward"))

    def test_phone_recruit_cards_follow_bottom_anchor_without_changing_header(self):
        raw = Image.open(ROOT / "tests/fixtures/phone-recruit-1016.png").convert("RGB")
        adapter = ScreenAdapter()
        image = adapter.frame("usb", raw)
        templates = {name: bot.open_template(ROOT / f"assets/emulator-{name}.png").convert("RGB")
                     for name in bot.TASK_BOXES}
        self.assertTrue(bot.task_page(image, templates, "recruit-title"))
        self.assertEqual(bot.recruit_button_state(image, templates, "recruit_advanced")[0], "free")
        self.assertEqual(bot.recruit_button_state(image, templates, "recruit_epic")[0], "cooldown")
        x, y = adapter.geometry("usb").point(280, 1690)
        self.assertAlmostEqual(x, 305, delta=3)
        self.assertAlmostEqual(y, 1692, delta=5)

    def test_phone_level_21_offer_uses_centered_close_and_heading(self):
        raw = Image.open(ROOT / "tests/fixtures/phone-dawn-gift-1016.png").convert("RGB")
        adapter = ScreenAdapter()
        image = adapter.frame("usb", raw)
        self.assertEqual(bot.dawn_gift_close_target(image), (914, 472))
        x, y = adapter.geometry("usb").point(914, 472)
        self.assertAlmostEqual(x, 995, delta=3)
        self.assertAlmostEqual(y, 415, delta=3)
        for box in ((110, 410, 650, 520), (879, 437, 949, 507)):
            masked = image.copy()
            masked.paste(0, box)
            self.assertIsNone(bot.dawn_gift_close_target(masked))

    def test_nav_input_lands_on_actual_phone_icon(self):
        adapter = ScreenAdapter()
        raw = self.frame("city")
        adapter.frame("usb", raw)
        args = adapter.input("usb", ("shell", "input", "tap", "110", "2235"))
        x, y = map(int, args[3:])
        # Independently measured native icon center, not derived from the map.
        self.assertLessEqual(abs(x - 120), 2)
        self.assertLessEqual(abs(y - 2286), 2)
        swipe = adapter.input("usb", ("shell", "input", "swipe", "110", "2235", "110", "2235", "3000"))
        self.assertEqual(swipe[3:5], args[3:5])
        self.assertEqual(swipe[5:7], args[3:5])
        self.assertEqual(swipe[7], "3000")
        self.assertEqual(adapter.input("usb", ("shell", "input", "keyevent", "4")),
                         ("shell", "input", "keyevent", "4"))

    def test_phone_pet_entry_and_centered_panel(self):
        adapter = ScreenAdapter()
        city = adapter.frame("usb", self.frame("pet-city"))
        template = bot.open_template(ROOT / "assets/emulator-pet-button.png").convert("L")
        self.assertLess(bot.difference(city.crop(bot.PET_BUTTON_BOX).convert("L"), template), 15)
        panel = adapter.frame("usb", self.frame("pet-panel"))
        self.assertTrue(bot.pet_page(panel))
        for name, (_, target) in bot.PET_SKILLS.items():
            self.assertEqual(bot.pet_skill_available(panel, target), name != "pet_gift")

    def test_real_lower_tier_training_page_is_loaded_and_recoverable(self):
        image = ScreenAdapter().frame("usb", self.frame("training")).convert("L")
        self.assertEqual(bot.training_back_target(image), (60, 190))
        self.assertTrue(gui_web.game_loaded(image))

    def test_scaled_phone_frames_reuse_templates_without_resampling_assets(self):
        raw = self.frame("city")
        help_icon, nav = self.assets()
        for size in ((588, 1200), (784, 1600), (1470, 3000)):
            with self.subTest(size=size):
                adapter = ScreenAdapter()
                normalized = adapter.frame("usb", raw.resize(size, Image.Resampling.LANCZOS))
                self.assertTrue(bot.inspect(normalized.convert("L"), help_icon, nav)[0])

    def test_different_aspect_ratios_and_bottom_system_bar(self):
        with Image.open(ROOT / "tests/fixtures/task-rewards-entry.png") as source:
            reference = source.convert("RGB")
        help_icon, nav = self.assets()
        for size in ((720, 1280), (1080, 2400), (1440, 3200)):
            with self.subTest(size=size):
                width, height = size
                scale, safe_top, safe_bottom = width / 1080, 50, height - 60
                native = Image.new("RGB", size)
                native.paste((25, 40, 65), (0, safe_top, width, safe_bottom))
                # Render real controls using independent top/bottom UI anchors.
                header = reference.crop((0, 136, 1080, 400)).resize((width, round(264 * scale)))
                footer = reference.crop((0, 1790, 1080, 2340)).resize((width, round(550 * scale)))
                native.paste(header, (0, safe_top))
                native.paste(footer, (0, safe_bottom - footer.height))
                adapter = ScreenAdapter()
                image = adapter.frame("device", native)
                self.assertTrue(bot.inspect(image.convert("L"), help_icon, nav)[0])
                x, y = adapter.geometry("device").point(110, 2235)
                self.assertLessEqual(abs(x - 110 * scale), 2)
                self.assertLessEqual(abs(y - (safe_bottom - 105 * scale)), 2)

    def test_pixel5_frames_remain_pixel_identical(self):
        with Image.open(ROOT / "tests/fixtures/task-rewards-entry.png") as source:
            image = source.convert("RGB")
        normalized = ScreenAdapter().frame("emulator", image)
        self.assertTrue(np.array_equal(np.asarray(image), np.asarray(normalized)))

    def test_loading_frame_does_not_recalibrate_or_leak_between_devices(self):
        adapter = ScreenAdapter()
        adapter.frame("usb", self.frame("city"))
        original = adapter.geometry("usb")
        adapter.frame("usb", Image.new("RGB", (1176, 2400)))
        self.assertEqual(adapter.geometry("usb"), original)
        adapter.frame("other", self.frame("city").resize((588, 1200)))
        self.assertEqual(adapter.geometry("usb"), original)
        self.assertEqual(adapter.geometry("other").size, (588, 1200))
        adapter.frame("usb", self.frame("city").resize((588, 1200)))
        self.assertEqual(adapter.geometry("usb").size, (588, 1200))

    def test_landscape_and_uncalibrated_input_fail_before_tapping(self):
        with self.assertRaisesRegex(RuntimeError, "竖屏"):
            detect(Image.new("RGB", (2400, 1176)))
        with self.assertRaisesRegex(RuntimeError, "截图"):
            ScreenAdapter().input("unknown", ("shell", "input", "tap", "1", "2"))

    def test_diagnostic_keeps_native_resolution(self):
        native = self.frame("city")
        image = ScreenAdapter().frame("usb", native)
        self.assertEqual(image.size, (1080, 2340))
        self.assertIs(image.info["native_frame"], native)
        self.assertEqual(image.info["native_frame"].size, (1176, 2400))

    def test_actual_phone_daily_panel_and_claim_input(self):
        raw = Image.open(ROOT / "tests/fixtures/phone-task-rewards-1016.png").convert("RGB")
        adapter = ScreenAdapter()
        image = adapter.frame("usb", raw)
        self.assertEqual(task_rewards.page(bot, image), "每日任务")
        self.assertEqual({name: count for name, _, count in task_rewards.tabs(bot, image)},
                         {"成长任务": 0, "每日任务": 10})
        target = task_rewards.green_claim(bot, image, ("一键领取",), (300, 1720, 780, 1890))
        self.assertIsNotNone(target)
        x, y = adapter.geometry("usb").point(*target)
        # Measured on the native screenshot, independent of the adapter map.
        self.assertLess(abs(x - 588), 5)
        self.assertLess(abs(y - 1883), 5)
        for negative in (self.frame("city"), self.frame("pet-panel"), self.frame("training")):
            self.assertIsNone(task_rewards.page(bot, ScreenAdapter().frame("other", negative)))

    def test_phone_single_digit_task_badge_requires_a_numeric_read(self):
        raw = Image.open(ROOT / "tests/fixtures/phone-task-rewards-single-1016.png").convert("RGB")
        image = ScreenAdapter().frame("usb", raw)
        self.assertEqual({name: count for name, _, count in task_rewards.tabs(bot, image)},
                         {"成长任务": 0, "每日任务": 1})

    def test_actual_phone_wilderness_intelligence_icon_and_input(self):
        raw = Image.open(ROOT / "tests/fixtures/phone-wilderness-1016.png").convert("RGB")
        adapter = ScreenAdapter()
        image = adapter.frame("usb", raw)
        original = bot.TOWN_TEMPLATE
        try:
            bot.TOWN_TEMPLATE = bot.open_template(ROOT / "assets/emulator-town-button.png").convert("L")
            self.assertTrue(bot.town_button(image))
            target = bot.intelligence_entry_target(image)
            self.assertIsNotNone(target)
            x, y = adapter.geometry("usb").point(*target)
            self.assertLess(abs(x - 1085), 4)
            self.assertLess(abs(y - 1718), 4)
            self.assertIsNone(bot.intelligence_entry_target(adapter.frame("usb", self.frame("city"))))
        finally:
            bot.TOWN_TEMPLATE = original


if __name__ == "__main__":
    unittest.main()
