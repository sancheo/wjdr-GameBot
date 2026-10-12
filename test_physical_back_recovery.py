"""Android Back-key recovery when no calibrated exit button is visible."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot
from test_help_bot import Clock
from test_runtime_recovery import asset, frame


class PhysicalBackRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.unknown = Image.new("L", bot.EXPECTED_SIZE)
        self.city = frame("feature-return").convert("L")
        self.clock = Clock()
        stack = self.enterContext(contextlib.ExitStack())
        for name in ("gather_search_page", "intelligence_page", "treasure_page", "town_button",
                     "pet_page", "pet_reward_page", "stamina_page", "recruit_hero_exit_page",
                     "chat_page", "island_page"):
            stack.enter_context(patch.object(bot, name, return_value=False))
        for name in ("social_back_target", "alliance_back_target", "recruit_back_target",
                     "explore_back_target", "resource_more_target", "training_back_target",
                     "reconnect_popup_target", "treasure_back_target", "gather_back_target",
                     "intelligence_back_target"):
            stack.enter_context(patch.object(bot, name, return_value=None))
        stack.enter_context(patch.object(bot, "recognize_text", return_value=[]))
        stack.enter_context(patch.object(bot, "warehouse_reward_timer", return_value=(False, None)))
        stack.enter_context(patch.object(bot, "forced_offline", return_value=False))
        stack.enter_context(patch.object(bot.time, "monotonic", self.clock.monotonic))
        stack.enter_context(patch.object(bot.time, "sleep", self.clock.sleep))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def recover(self):
        return bot.return_to_city("test", asset("help-icon"), asset("city-nav"), None, None)

    def test_back_key_recovers_unknown_page_and_confirms_city(self):
        with (patch.object(bot, "screenshot", side_effect=[self.unknown] * 2 + [self.city] * 3),
              patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb") as adb):
            self.assertTrue(self.recover())
        adb.assert_called_once_with("test", "shell", "input", "keyevent", "4")

    def test_one_loading_frame_does_not_press_back(self):
        with (patch.object(bot, "screenshot", side_effect=[self.unknown] + [self.city] * 3),
              patch.object(bot, "task_back") as back):
            self.assertTrue(self.recover())
        back.assert_not_called()

    def test_unknown_page_has_bounded_spaced_back_attempts(self):
        pressed_at = []
        with (patch.object(bot, "screenshot", return_value=self.unknown),
              patch.object(bot, "task_back", side_effect=lambda _: pressed_at.append(self.clock.now)),
              self.assertRaisesRegex(RuntimeError, "60 秒内仍未确认主城")):
            self.recover()
        self.assertEqual(len(pressed_at), 3)
        self.assertTrue(all(right - left >= 6 for left, right in zip(pressed_at, pressed_at[1:])))

    def test_no_back_key_after_forced_offline(self):
        with (patch.object(bot, "screenshot", return_value=self.unknown),
              patch.object(bot, "forced_offline", return_value=True),
              patch.object(bot, "task_back") as back):
            self.assertFalse(self.recover())
        back.assert_not_called()

    def test_no_keyevent_when_game_loses_foreground(self):
        with (patch.object(bot, "screenshot", return_value=self.unknown),
              patch.object(bot, "game_foreground", return_value=False),
              patch.object(bot, "adb") as adb,
              self.assertRaisesRegex(RuntimeError, "当前前台不是目标游戏")):
            self.recover()
        adb.assert_not_called()

    def test_retry_recovery_uses_the_same_physical_back_fallback(self):
        color_city = self.city.convert("RGB")
        with (patch.object(bot, "color_screenshot", side_effect=[self.unknown.convert("RGB"), color_city]),
              patch.object(bot, "screenshot", side_effect=[self.unknown] * 2 + [self.city] * 3),
              patch.object(bot, "task_back") as back):
            image = bot.recover_task_city("test", asset("help-icon"), asset("city-nav"), None, None)
        self.assertIs(image, color_city)
        back.assert_called_once_with("test")


if __name__ == "__main__":
    unittest.main()
