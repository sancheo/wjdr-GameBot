"""October 14 failures: hunting's shared carousel and an OCR letter O."""
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot


def frame(name):
    with Image.open(bot.ROOT / f"tests/fixtures/{name}-1014.png") as saved:
        return saved.convert("RGB")


class CarouselZeroRecoveryTest(unittest.TestCase):
    def test_rescue_action_is_preserved_when_the_hospital_icon_is_visible(self):
        image = frame("intelligence-rescue-healing")
        help_image = bot.open_template(bot.ROOT / 'assets/emulator-help-icon.png').convert('L')
        nav = bot.open_template(bot.ROOT / 'assets/emulator-city-nav.png').convert('L')
        town = bot.open_template(bot.ROOT / 'assets/emulator-town-button.png').convert('L')
        with (patch.object(bot, "TOWN_TEMPLATE", town),
              patch.object(bot, "WILDERNESS_HEALING", (help_image, nav)),
              patch.object(bot, "run_healing") as heal):
            self.assertIsNotNone(bot.healing.entry(bot, image, help_image, nav))
            self.assertIsNotNone(bot.intelligence_text_target(image, ('营救',), (300, 1150, 1050, 2300)))
            self.assertIs(bot.check_wilderness_healing('test', image), image)
        heal.assert_not_called()

    def test_each_intelligence_action_defers_healing_until_the_panel_closes(self):
        image = frame("intelligence-rescue-healing")
        for label in ('营救', '探险', '战斗'):
            with (patch.object(bot, 'WILDERNESS_HEALING', (None, None)),
                  patch.object(bot.healing, 'entry', return_value=(837, 1994)),
                  patch.object(bot, 'gather_search_page', return_value=False),
                  patch.object(bot, 'intelligence_page', return_value=False),
                  patch.object(bot, 'recognize_text', return_value=[(label, (100, 600, 60, 40))]),
                  patch.object(bot, 'run_healing') as heal):
                self.assertIs(bot.check_wilderness_healing('test', image), image)
            heal.assert_not_called()

    def test_wood_search_after_hunting_moves_toward_resources(self):
        image = frame("gather-carousel-monsters")
        self.assertTrue(bot.gather_search_page(image))
        self.assertIsNone(bot.intelligence_text_target(image, ("木材",), (0, 1790, 1080, 1880)))
        with (patch.object(bot, "intelligence_text_target", side_effect=[None, (540, 1850)]),
              patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb") as swipe,
              patch.object(bot, "task_screen", return_value=image),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "set_gather_level", return_value=image),
              patch.object(bot, "gather_full_only", return_value=True)):
            self.assertIs(bot.prepare_gather_search("test", image, "wood", 4), image)
        swipe.assert_called_once_with("test", "shell", "input", "swipe",
                                      "900", "1780", "200", "1780", "600")
        tap.assert_called_once_with("test", 540, 1850)

    def test_resource_to_left_of_current_carousel_moves_right(self):
        image = frame("gather-carousel-monsters")
        with (patch.object(bot, "intelligence_text_target", side_effect=[None, (540, 1850)]),
              patch.object(bot, "recognize_text", return_value=[("煤矿", (0, 0, 10, 10))]),
              patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb") as swipe,
              patch.object(bot, "task_screen", return_value=image),
              patch.object(bot, "task_tap"),
              patch.object(bot, "set_gather_level", return_value=image),
              patch.object(bot, "gather_full_only", return_value=True)):
            bot.prepare_gather_search("test", image, "meat")
        self.assertEqual(swipe.call_args.args[4:9], ("200", "1780", "900", "1780", "600"))

    def test_unknown_categories_try_both_directions_then_fail_without_selecting(self):
        image = frame("gather-carousel-monsters")
        with (patch.object(bot, "intelligence_text_target", return_value=None),
              patch.object(bot, "recognize_text", return_value=[]),
              patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb") as swipe,
              patch.object(bot, "task_screen", return_value=image),
              patch.object(bot, "task_tap") as tap,
              self.assertRaisesRegex(RuntimeError, "木材图标")):
            bot.prepare_gather_search("test", image, "wood")
        self.assertEqual([c.args[4] for c in swipe.call_args_list], ["900", "900", "200", "200"])
        tap.assert_not_called()

    def test_real_zero_is_numeric_even_when_line_ocr_reads_letter_o(self):
        image = frame("treasure-zero")
        self.assertTrue(bot.treasure_page(image))
        self.assertEqual(bot.treasure_remaining(image), 0)
        self.assertIsNotNone(bot.treasure_completed_target(image))

    def test_letter_o_with_unreadable_number_stays_unknown(self):
        image = frame("treasure-zero")
        with (patch.object(bot, "treasure_page", return_value=True),
              patch.object(bot, "recognize_text", side_effect=[
                  [("今日剩余寻宝次数：O", (0, 0, 10, 10))], [("O", (0, 0, 10, 10))]])):
            self.assertIsNone(bot.treasure_remaining(image))
        image.paste("black", (120, 140, 380, 235))
        self.assertIsNone(bot.treasure_remaining(image))


if __name__ == "__main__":
    unittest.main()
