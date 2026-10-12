"""Regression for emulator-5556 startup blocked by the legendary gift sale."""
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


def asset(name):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as image:
        return image.convert("L")


class StartupPopupTest(unittest.TestCase):
    def test_gift_target_in_color_and_greyscale(self):
        for mode in ("RGB", "L"):
            with self.subTest(mode=mode):
                image = frame("startup-5556-before").convert(mode)
                self.assertEqual(bot.reconnect_popup_target(image), (914, 472))

    def test_title_and_close_button_are_both_required(self):
        for box in ((130, 430, 440, 505), (879, 437, 949, 507)):
            with self.subTest(box=box):
                image = frame("startup-5556-before")
                image.paste("black", box)
                with (patch.object(bot, "RECONNECT_PROMO_CLOSE", None),
                      patch.object(bot, "RECONNECT_WELCOME_CLOSE", None),
                      patch.object(bot, "ACTIVITY_CLOSE", None)):
                    self.assertIsNone(bot.reconnect_popup_target(image))

    def test_other_pages_are_not_gift_popups(self):
        for name in ("stamina-ready", "feature-tech", "flow-chat-page", "profile-page",
                     "training-resource-shortage", "intel-map", "pet-reward-overlay",
                     "reconnect-current-emulator", "reconnect-activity-current"):
            with self.subTest(name=name):
                self.assertIsNone(bot.legendary_gift_close_target(frame(name)))

    def test_existing_activity_close_still_matches(self):
        with (patch.object(bot, "RECONNECT_PROMO_CLOSE", asset("reconnect-promo-close")),
              patch.object(bot, "RECONNECT_WELCOME_CLOSE", asset("reconnect-welcome-close")),
              patch.object(bot, "ACTIVITY_CLOSE", asset("activity-close"))):
            self.assertIsNotNone(bot.reconnect_popup_target(frame("reconnect-activity-current")))

    def test_startup_closes_gift_once_then_requires_three_city_frames(self):
        clock = Clock()
        popup, city = frame("startup-5556-before"), frame("stamina-ready")
        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot.time, "sleep", clock.sleep),
              patch.object(bot, "screenshot", side_effect=[popup.convert("L")] + [city.convert("L")] * 3) as capture,
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "forced_offline", return_value=False),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("emulator-5556", asset("help-icon"), asset("city-nav"), None, None))
        tap.assert_called_once_with("emulator-5556", 914, 472)
        self.assertEqual(capture.call_count, 4)


if __name__ == "__main__":
    unittest.main()
