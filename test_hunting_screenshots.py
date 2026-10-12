"""Observed monster search, target and rally screens from both emulators."""
import unittest
from PIL import Image
import help_bot as bot
import hunting


class HuntingScreenshotsTest(unittest.TestCase):
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
