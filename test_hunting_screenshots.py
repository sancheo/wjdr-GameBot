"""Observed monster search, target and rally screens from both emulators."""
import unittest
from unittest.mock import patch
from PIL import Image
import help_bot as bot
import hunting
from screen_geometry import detect


class HuntingScreenshotsTest(unittest.TestCase):
    def test_phone_category_sharpening_recovers_exact_polar_beast(self):
        with Image.open(bot.ROOT / "tests/fixtures/phone-hunt-search-1016.png") as native:
            image = detect(native).normalize(native.convert("RGB"))
        target = hunting.category_target(bot, image, "冰原巨兽")
        self.assertIsNotNone(target)
        self.assertTrue(535 <= target[0] <= 675 and 1810 <= target[1] <= 1860)

    def test_category_fallback_rejects_similar_text_and_non_search_page(self):
        image = Image.new("RGB", bot.EXPECTED_SIZE)
        with (patch.object(bot, "gather_search_page", return_value=True),
              patch.object(bot, "intelligence_text_target", return_value=None),
              patch.object(bot, "recognize_text", return_value=[("冰原巨善", (530, 30, 140, 40))])):
            self.assertIsNone(hunting.category_target(bot, image, "冰原巨兽"))
        with (patch.object(bot, "gather_search_page", return_value=False),
              patch.object(bot, "recognize_text") as ocr):
            self.assertIsNone(hunting.category_target(bot, image, "冰原巨兽"))
        ocr.assert_not_called()

    def frame(self, name):
        with Image.open(bot.ROOT / f"tests/fixtures/hunt-{name}.png") as image:
            return image.convert("RGB")

    def test_search_numbers_limit_and_disabled_minimum(self):
        beast = self.frame("beast-search")
        ice = self.frame("ice-minimum")
        self.assertTrue(bot.gather_search_page(beast))
        self.assertTrue(bot.gather_search_page(ice))
        self.assertEqual(hunting.level(bot, beast, 30), 29)
        self.assertEqual(hunting.challenge_limit(bot, beast), 29)
        self.assertEqual(hunting.level(bot, ice, 6), 2)
        self.assertIsNone(hunting.challenge_limit(bot, ice))
        self.assertTrue(hunting.minimum_level(bot, ice))
        self.assertFalse(hunting.minimum_level(bot, beast))

    def test_delayed_search_opens_before_any_selection_tap(self):
        opened = self.frame("ice-delayed-1012")
        pending = Image.new("RGB", bot.EXPECTED_SIZE, "white")
        self.assertTrue(bot.gather_search_page(opened))
        self.assertFalse(bot.gather_search_page(pending))
        with (patch.object(bot, "task_screen", side_effect=[pending, opened, opened]) as screen,
              patch.object(bot, "task_tap") as taps,
              patch.object(bot, "intelligence_text_target", return_value=(350, 1835))):
            def select(*args):
                self.assertEqual(screen.call_count, 2)
            taps.side_effect = select
            image, selected = hunting.prepare(bot, "test", pending, "polar_beast", 5)
        self.assertIs(image, opened)
        self.assertEqual(selected, 5)
        taps.assert_called_once_with("test", 350, 1835)

    def test_search_never_opens_fails_without_selection_or_dispatch(self):
        pending = Image.new("RGB", bot.EXPECTED_SIZE, "white")
        with (patch.object(bot, "task_screen", return_value=pending) as screen,
              patch.object(bot, "task_tap") as taps):
            with self.assertRaisesRegex(RuntimeError, "未确认打怪搜索界面"):
                hunting.prepare(bot, "test", pending, "polar_beast", 5)
        self.assertEqual(screen.call_count, 5)
        taps.assert_not_called()

    def test_delayed_category_swipe_waits_without_swiping_again(self):
        opened = self.frame("ice-delayed-1012")
        pending = Image.new("RGB", bot.EXPECTED_SIZE, "white")
        with (patch.object(bot, "task_screen", side_effect=[pending, opened, opened]),
              patch.object(bot, "task_tap") as taps,
              patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb") as adb,
              patch.object(hunting, "category_target", side_effect=[None, (350, 1835)])):
            self.assertEqual(hunting.prepare(bot, "test", opened, "polar_beast", 5), (opened, 5))
        adb.assert_called_once_with("test", "shell", "input", "swipe", "200", "1780", "900", "1780", "600")
        taps.assert_called_once_with("test", 350, 1835)

    def test_both_monster_action_buttons_are_inside_calibrated_regions(self):
        target = hunting.action(bot, self.frame("beast-detail"), "beast")
        self.assertIsNotNone(target)
        self.assertTrue(1150 <= target[1] <= 1300)
        target = hunting.action(bot, self.frame("ice-detail"), "polar_beast")
        self.assertIsNotNone(target)
        self.assertTrue(950 <= target[1] <= 1080)

    def test_avatar_stamina_is_read_on_real_monster_screens(self):
        self.assertEqual(bot.intelligence_stamina(self.frame("beast-detail")), 75)
        self.assertEqual(bot.intelligence_stamina(self.frame("ice-detail")), 216)

    def test_rally_button_has_distinct_label_from_target_action(self):
        image = self.frame("rally")
        self.assertIsNone(hunting.action(bot, image, "polar_beast"))
        target = bot.intelligence_text_target(image, ("发起集结",), (280, 1450, 850, 1800))
        self.assertIsNotNone(target)
        self.assertTrue(1450 <= target[1] <= 1600)


if __name__ == "__main__":
    unittest.main()
