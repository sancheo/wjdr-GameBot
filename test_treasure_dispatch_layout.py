"""Replay dispatch controls outside the former fixed button and close positions."""
import contextlib
import io
import unittest
from unittest.mock import call, patch

from PIL import Image
import help_bot as bot
from test_help_bot import Clock


def asset(name):
    return bot.open_template(bot.ROOT / f"assets/emulator-{name}.png").convert("L")


def fixture(name):
    with Image.open(bot.ROOT / f"tests/fixtures/{name}.png") as image:
        return image.convert("RGB")


def dispatch(offset=0):
    original = fixture("treasure-dispatch")
    if not offset:
        return original
    shifted = Image.new("RGB", bot.EXPECTED_SIZE)
    shifted.paste(original.crop((0, 540, 1080, 1950)), (0, 540 + offset))
    return shifted


class TreasureDispatchLayoutTest(unittest.TestCase):
    def test_start_and_actual_close_follow_short_and_tall_dialogs(self):
        for offset in (-160, 0, 220):
            for mode in ("RGB", "L"):
                with self.subTest(offset=offset, mode=mode):
                    image = dispatch(offset).convert(mode)
                    target = bot.treasure_action_target(image, "开始寻宝")
                    self.assertIsNotNone(target)
                    self.assertAlmostEqual(target[0], 540, delta=3)
                    self.assertAlmostEqual(target[1], 1759 + offset, delta=3)
                    if offset:
                        self.assertIsNone(bot.intelligence_text_target(
                            image, ("开始寻宝",), (300, 1700, 780, 1940)))
                    with patch.object(bot, "recognize_text", side_effect=AssertionError("exit needs no OCR")):
                        self.assertEqual(bot.treasure_back_target(image), (995, 605 + offset))

    def test_dispatch_title_and_close_are_both_required(self):
        for box in ((430, 580, 650, 640), (970, 585, 1020, 635)):
            image = dispatch()
            image.paste("black", box)
            self.assertIsNone(bot.treasure_dispatch_close(image))
        for name in ("treasure-map-1015", "treasure-header-1014", "treasure-stamina-1010"):
            self.assertIsNone(bot.treasure_dispatch_close(fixture(name)))
        self.assertIsNone(bot.treasure_dispatch_close(dispatch().resize((540, 1170))))

    def test_reported_map_still_confirms_remaining_count_chest_and_exit(self):
        image = fixture("treasure-map-1015")
        self.assertEqual(bot.treasure_remaining(image), 1)
        self.assertEqual(bot.treasure_chest_target(image), (583, 697))
        self.assertEqual(bot.treasure_back_target(image), (60, 190))

    def test_recovery_closes_resized_dialog_before_any_unrelated_popup_ocr(self):
        city = bot.open_template(bot.ROOT / "artifacts/stamina-ready.png")
        for retry in (False, True):
            for offset in (-160, 220):
                image = dispatch(offset)
                clock = Clock()
                with (self.subTest(retry=retry, offset=offset),
                      patch.object(bot.time, "monotonic", clock.monotonic),
                      patch.object(bot.time, "sleep", clock.sleep),
                      patch.object(bot, "screenshot", side_effect=[image.convert("L")] + [city.convert("L")] * 3),
                      patch.object(bot, "color_screenshot", return_value=image),
                      patch.object(bot, "task_screen", return_value=city),
                      patch.object(bot, "forced_offline", return_value=False),
                      patch.object(bot, "recognize_text", side_effect=AssertionError("unrelated popup OCR")),
                      patch.object(bot, "task_tap") as tap,
                      patch.object(bot, "task_back", side_effect=AssertionError("known close icon")),
                      contextlib.redirect_stdout(io.StringIO())):
                    if retry:
                        self.assertIs(bot.recover_task_city("test", asset("help-icon"), asset("city-nav"), None, None), city)
                    else:
                        self.assertTrue(bot.return_to_city("test", asset("help-icon"), asset("city-nav"), None, None))
                tap.assert_called_once_with("test", 995, 605 + offset)

    def test_dispatch_flow_uses_detected_start_and_confirms_count_decrease(self):
        drawer = Image.new("RGB", bot.EXPECTED_SIZE)
        page = fixture("treasure-map-1015")
        detail = bot.open_template(bot.ROOT / "artifacts/treasure-detail.png")
        real_read = bot.recognize_text
        real_target = bot.intelligence_text_target
        for offset in (-160, 220):
            with (self.subTest(offset=offset),
                  patch.object(bot, "TASK_DRAWER_SCROLL", 0),
                  patch.object(bot, "color_screenshot", return_value=drawer),
                  patch.object(bot, "inspect", return_value=(True, None, 0)),
                  patch.object(bot, "task_drawer", return_value=drawer),
                  patch.object(bot, "recognize_text", side_effect=lambda image:
                               [("宠物寻宝", (0, 200, 120, 30))] if image.size == (310, 880)
                               else real_read(image)),
                  patch.object(bot, "intelligence_text_target", side_effect=lambda image, labels, box:
                               (300, 955) if image is drawer and labels == ("可寻宝",)
                               else real_target(image, labels, box)),
                  patch.object(bot, "treasure_remaining", side_effect=[1, 0]),
                  patch.object(bot, "treasure_chest_target", return_value=(583, 696)),
                  patch.object(bot, "task_screen", side_effect=[page, detail, dispatch(offset), page, drawer]),
                  patch.object(bot, "task_tap") as tap,
                  contextlib.redirect_stdout(io.StringIO()) as output):
                self.assertIs(bot.run_treasure("test", None, None, drawer), drawer)
            self.assertIn(call("test", 540, 1759 + offset), tap.call_args_list)
            self.assertEqual(tap.call_args_list[-1], call("test", 60, 190))
            self.assertIn("今日剩余次数 1 → 0", output.getvalue())


if __name__ == "__main__":
    unittest.main()
