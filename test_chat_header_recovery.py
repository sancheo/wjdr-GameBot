"""Replay the October 14 chat interruption that exhausted city recovery."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot
from test_help_bot import Clock
from test_runtime_recovery import asset, frame


class ChatHeaderRecoveryTest(unittest.TestCase):
    def setUp(self):
        with Image.open(bot.ROOT / "tests/fixtures/chat-header-1014.png") as image:
            self.chat = image.convert("L")

    def test_observed_offset_identifies_chat_and_calibrated_back(self):
        template = asset("chat-title")
        self.assertGreater(bot.difference(self.chat.crop(bot.CHAT_BOX), template), 15)
        self.assertTrue(bot.chat_page(self.chat))
        self.assertEqual(bot.social_back_target(self.chat), (60, 190))

    def test_missing_title_and_uncalibrated_frames_are_rejected(self):
        missing = self.chat.copy()
        missing.paste(0, (138, 148, 247, 232))
        for image in (missing, self.chat.resize((540, 1170)),
                      frame("feature-return").convert("L"),
                      frame("stamina-ready").convert("L")):
            with self.subTest(size=image.size):
                self.assertFalse(bot.chat_page(image))

    def test_chat_back_requires_three_city_confirmations(self):
        clock = Clock()
        city = frame("feature-return").convert("L")
        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot.time, "sleep", clock.sleep),
              patch.object(bot, "screenshot", side_effect=[self.chat] + [city] * 3) as capture,
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("test", asset("help-icon"),
                                               asset("city-nav"), None, None))
        tap.assert_called_once_with("test", 60, 190)
        self.assertEqual(capture.call_count, 4)

    def test_forced_offline_does_not_click_chat_back(self):
        clock = Clock()
        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot, "screenshot", return_value=self.chat),
              patch.object(bot, "forced_offline", return_value=True),
              patch.object(bot, "task_tap") as tap):
            self.assertFalse(bot.return_to_city("test", asset("help-icon"),
                                                asset("city-nav"), None, None))
        tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
