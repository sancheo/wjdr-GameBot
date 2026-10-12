"""Replay the October 10 zero-defense popup and healing/monitor race."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot
import gui_web
from test_help_bot import Clock
from test_runtime_recovery import asset, frame


class CityRelocationTest(unittest.TestCase):
    def setUp(self):
        with Image.open(bot.ROOT / "tests/fixtures/city-relocation-1010.png") as im:
            self.notice = im.convert("L")
        self.city = frame("stamina-ready").convert("L")
        self.templates = tuple(asset(name) for name in
                               ("help-icon", "city-nav", "forced-text", "reconnect"))
        self.clock = Clock()
        self.enterContext(patch.object(bot.time, "monotonic", self.clock.monotonic))
        self.enterContext(patch.object(bot.time, "sleep", self.clock.sleep))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_observed_notice_requires_title_message_and_confirm(self):
        self.assertEqual(bot.city_relocation_target(self.notice), (540, 1465))
        for box in ((490,925,590,980), (130,1130,940,1240), (440,1430,640,1500)):
            changed = self.notice.copy()
            changed.paste(0, box)
            self.assertIsNone(bot.city_relocation_target(changed))
        for image in (self.city, frame("network-disconnected-1008"),
                      frame("reconnect-current-emulator"), self.notice.resize((540,1170))):
            self.assertIsNone(bot.city_relocation_target(image))

    def test_recovers_only_after_three_city_confirmations(self):
        with (patch.object(bot, "screenshot", side_effect=[self.notice, self.city, self.city, self.city]),
              patch.object(bot, "task_tap") as tap):
            self.assertTrue(bot.return_to_city("test", *self.templates))
        tap.assert_called_once_with("test", 540, 1465)

    def test_persistent_notice_is_bounded_and_never_claims_recovery(self):
        times = []
        with (patch.object(bot, "screenshot", return_value=self.notice),
              patch.object(bot, "task_tap", side_effect=lambda *a: times.append(self.clock.now)),
              patch.object(bot, "task_back") as back,
              self.assertRaisesRegex(RuntimeError, "仍未确认主城")):
            bot.return_to_city("test", *self.templates)
        self.assertEqual(len(times), 3)
        self.assertTrue(all(b-a >= 10 for a,b in zip(times,times[1:])))
        back.assert_not_called()

    def test_confirmation_requires_game_foreground(self):
        with (patch.object(bot, "screenshot", return_value=self.notice),
              patch.object(bot, "game_foreground", return_value=False),
              patch.object(bot, "adb") as adb,
              self.assertRaisesRegex(RuntimeError, "前台不是目标游戏")):
            bot.return_to_city("test", *self.templates)
        adb.assert_not_called()

    def test_passive_loader_accepts_notice_without_clicking(self):
        with patch.object(bot, "adb") as adb:
            self.assertTrue(gui_web.game_loaded(self.notice))
        adb.assert_not_called()

    def test_wish_shop_requires_heading_and_close_and_can_return_to_city(self):
        with Image.open(bot.ROOT / "tests/fixtures/startup-wish-shop-1011.png") as im:
            shop = im.convert("L")
        self.assertTrue(gui_web.game_loaded(shop))
        self.assertEqual(bot.reconnect_popup_target(shop), (914, 540))
        for box in ((125,500,350,565), (879,505,949,575)):
            changed = shop.copy()
            changed.paste(0, box)
            self.assertIsNone(bot.wish_shop_close_target(changed))
        for image in (self.city, self.notice, frame("profile-page"),
                      frame("training-resource-shortage")):
            self.assertIsNone(bot.wish_shop_close_target(image))
        with (patch.object(bot, "screenshot", side_effect=[shop, self.city, self.city, self.city]),
              patch.object(bot, "task_tap") as tap):
            self.assertTrue(bot.return_to_city("test", *self.templates))
        tap.assert_called_once_with("test", 914, 540)

    def test_dry_monitor_does_not_acknowledge_notice(self):
        with (patch.object(bot, "screenshot", return_value=self.notice),
              patch.object(bot, "return_to_city") as recover,
              patch.object(bot, "adb") as adb):
            bot.run("test", 10, 1, True, 1, *self.templates)
        recover.assert_not_called()
        adb.assert_not_called()

    def test_healing_popup_reaches_recovery_without_unpacking_missing_match(self):
        with (patch.object(bot, "screenshot", side_effect=[self.city, self.notice]),
              patch.object(bot, "color_screenshot", return_value=self.city),
              patch.object(bot, "check_wilderness_healing", return_value=self.notice),
              patch.object(bot, "WILDERNESS_HEALING", object()),
              patch.object(bot, "return_to_city", return_value=True) as recover,
              patch.object(bot, "adb") as adb):
            self.assertTrue(bot.run("test", 10, 1, False, 1, *self.templates,
                                     allow_wilderness=True, return_on_reconnect=True))
        recover.assert_called_once()
        adb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
