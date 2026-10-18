"""Real October 14 headers and the daily-tab badge that OCR missed."""
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot
import task_rewards as rewards


def frame(name, mode="RGB"):
    with Image.open(bot.ROOT / f"tests/fixtures/{name}-1014.png") as image:
        return image.convert(mode)


class HeaderBadgeRecoveryTest(unittest.TestCase):
    def test_shifted_technology_title_keeps_the_populated_page_check(self):
        image = frame("tech-header")
        templates = {"tech-title": bot.open_template(bot.ROOT / "assets/emulator-tech-title.png").convert("RGB")}
        self.assertTrue(bot.tech_list_ready(image, templates))
        self.assertEqual(bot.alliance_back_target(image), (60, 190))
        image.paste("black", (50, 650, 1030, 2200))
        self.assertFalse(bot.tech_list_ready(image, templates))
        image.paste("black", (120, 140, 380, 235))
        self.assertFalse(bot.task_page(image, templates, "tech-title"))

    def test_shifted_pre_200_income_dialog_keeps_its_actual_close_position(self):
        image = frame("explore-income")
        x, y = bot.explore_back_target(image)
        self.assertEqual(x, 945)
        # The replacement Nox title crop shifts the best match by one pixel.
        self.assertAlmostEqual(y, 690, delta=1)
        image.paste("black", (420, 620, 680, 760))
        self.assertIsNone(bot.explore_back_target(image))

    def test_search_drawer_is_recognized_and_city_is_rejected(self):
        for device in ("5554", "5556"):
            image = frame(f"search-header-{device}")
            self.assertTrue(bot.gather_search_page(image))
            image.paste("black", (480, 2195, 610, 2290))
            self.assertFalse(bot.gather_search_page(image))
        self.assertFalse(bot.gather_search_page(frame("task-rewards-badge")))

    def test_shifted_explore_header_allows_claim_state_and_exit(self):
        templates = {name: bot.open_template(bot.ROOT / f"assets/emulator-explore-{name}.png").convert("L")
                     for name in bot.EXPLORE_BOXES}
        for device in ("5554", "5556"):
            image = frame(f"explore-header-{device}")
            self.assertEqual(bot.explore_claim_state(image, templates), "ready")
            self.assertEqual(bot.explore_back_target(image), (60, 190))
            image.paste("black", (120, 140, 380, 235))
            self.assertIsNone(bot.explore_claim_state(image, templates))

    def test_alliance_header_recovers_without_clicking_technology_on_header_alone(self):
        templates = {name: bot.open_template(bot.ROOT / f"assets/emulator-{name}.png")
                     for name in bot.TASK_BOXES}
        for mode in ("RGB", "L"):
            image = frame("alliance-header", mode)
            page_templates = {name: template.convert(mode) for name, template in templates.items()}
            self.assertTrue(bot.task_page(image, page_templates, "alliance-title"))
            self.assertEqual(bot.alliance_back_target(image), (60, 190))
            self.assertIsNotNone(bot.alliance_tech_target(image, page_templates))
        missing = frame("alliance-header")
        missing.paste("black", (120, 140, 380, 235))
        self.assertFalse(bot.task_page(missing, templates, "alliance-title"))
        self.assertIsNone(bot.alliance_tech_target(missing, templates))

    def test_near_matching_headers_confirm_exact_text_and_allow_map_exit(self):
        for name, check, exit_target in (
                ("treasure-header", bot.treasure_page, bot.treasure_back_target),
                ("intel-header", bot.intelligence_page, bot.intelligence_back_target)):
            for mode in ("RGB", "L"):
                with self.subTest(page=name, mode=mode):
                    image = frame(name, mode)
                    self.assertTrue(check(image))
                    self.assertEqual(exit_target(image), (60, 190))
            missing = frame(name)
            missing.paste("black", (120, 140, 380, 235))
            with patch.object(bot, "recognize_text", side_effect=AssertionError("no matching header")):
                self.assertFalse(check(missing))

    def test_near_match_with_wrong_ocr_title_is_rejected(self):
        for name, check in (("treasure-header", bot.treasure_page),
                            ("intel-header", bot.intelligence_page)):
            with patch.object(bot, "recognize_text", return_value=[("其他页面", (0, 0, 100, 40))]):
                self.assertFalse(check(frame(name)))

    def test_treasure_count_uses_confirmed_page(self):
        self.assertEqual(bot.treasure_remaining(frame("treasure-header")), 4)

    def test_daily_badge_local_ocr_makes_panel_ready(self):
        image = frame("task-rewards-badge")
        tabs = rewards.tabs(bot, image)
        self.assertEqual({name: count for name, _, count in tabs},
                         {"成长任务": 0, "每日任务": 1})
        with patch.object(rewards, "wait_panel", side_effect=AssertionError("already ready")):
            self.assertEqual(rewards.stable_tabs(bot, "test", image), (image, tabs))
        self.assertIsNotNone(rewards.green_claim(bot, image, ("一键领取",), (300, 1720, 780, 1890)))

    def test_unreadable_red_badge_remains_unknown(self):
        image = frame("task-rewards-badge")
        with patch.object(bot, "recognize_text", return_value=[]):
            self.assertIsNone(rewards.badge_count(bot, image, (799, 1972, 909, 2078)))


if __name__ == "__main__":
    unittest.main()
