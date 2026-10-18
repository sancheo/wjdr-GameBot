"""Real-frame regressions for the dawn-gift sale blocking cold restoration."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import gui_web as desktop
import help_bot as bot
from test_help_bot import Clock


def popup(mode="L", name="startup-dawn-gift-1012"):
    with Image.open(bot.ROOT / f"tests/fixtures/{name}.png") as image:
        return image.convert(mode)


class DawnGiftTest(unittest.TestCase):
    def test_real_sale_finishes_loading_and_has_only_close_target(self):
        for mode in ("RGB", "L"):
            with self.subTest(mode=mode):
                image = popup(mode)
                self.assertEqual(bot.dawn_gift_close_target(image), (914, 472))
                self.assertEqual(bot.reconnect_popup_target(image), (914, 472))
                self.assertTrue(desktop.game_loaded(image.convert("L")))

    def test_requires_both_title_and_close_button(self):
        for box in ((130, 430, 570, 505), (879, 437, 949, 507)):
            with self.subTest(box=box):
                image = popup()
                image.paste(0, box)
                self.assertIsNone(bot.dawn_gift_close_target(image))
        self.assertIsNone(bot.dawn_gift_close_target(Image.new("L", (540, 1170))))

    def test_level_19_heading_shift_also_recovers(self):
        image = popup(name="startup-dawn-gift-19-1012")
        self.assertEqual(bot.dawn_gift_close_target(image), (914, 472))
        self.assertEqual(bot.reconnect_popup_target(image), (914, 472))
        self.assertTrue(desktop.game_loaded(image))

    def test_unrelated_pages_do_not_match(self):
        for name in ("stamina-ready", "feature-tech", "profile-page",
                     "training-resource-shortage", "intel-map", "pet-reward-overlay"):
            with self.subTest(name=name):
                with Image.open(bot.ROOT / f"artifacts/{name}.png") as image:
                    self.assertIsNone(bot.dawn_gift_close_target(image))

    def test_city_recovery_closes_sale_then_requires_city_frames(self):
        clock = Clock()
        with Image.open(bot.ROOT / "artifacts/stamina-ready.png") as image:
            city = image.convert("L")
        help_icon = bot.open_template(bot.ROOT / "assets/emulator-help-icon.png").convert("L")
        nav = bot.open_template(bot.ROOT / "assets/emulator-city-nav.png").convert("L")
        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot.time, "sleep", clock.sleep),
              patch.object(bot, "screenshot", side_effect=[popup()] + [city] * 3) as capture,
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "forced_offline", return_value=False),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("emulator-5554", help_icon, nav, None, None))
        tap.assert_called_once_with("emulator-5554", 914, 472)
        self.assertEqual(capture.call_count, 4)


if __name__ == "__main__":
    unittest.main()
