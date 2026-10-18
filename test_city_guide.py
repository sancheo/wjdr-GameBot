"""Mandatory city guide observed during the October 12 task failures."""
import unittest
from unittest.mock import patch

from PIL import Image
import gui_web as desktop
import help_bot as bot
from test_runtime_recovery import asset, frame


class CityGuideTest(unittest.TestCase):
    def setUp(self):
        self.guides = []
        for pose in ('first', 'treasure', 'last'):
            with Image.open(bot.ROOT / f'tests/fixtures/city-guide-1012-{pose}.png') as im:
                self.guides.append(im.convert('RGB'))

    def test_all_real_failure_poses_block_loading(self):
        for pose, image in enumerate(self.guides):
            with self.subTest(pose=pose):
                self.assertTrue(desktop.startup_guide_blocked(image))
                self.assertFalse(desktop.game_loaded(image))

    def test_ordinary_city_drawer_and_rewards_are_not_guides(self):
        for name in ('stamina-ready', 'treasure-completed-drawer', 'treasure-reward'):
            with self.subTest(page=name):
                self.assertFalse(desktop.startup_guide_blocked(frame(name)))
        with Image.open(bot.ROOT / 'tests/fixtures/frozen-city-1008.png') as image:
            self.assertFalse(desktop.startup_guide_blocked(image))

    def test_drawer_stops_before_any_input_or_restart(self):
        with (patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'reopen_frozen_game') as restart,
              self.assertRaisesRegex(desktop.StartupGuideBlocked, '强制游戏引导')):
            bot.task_drawer('test', self.guides[0], {'drawer-open': asset('drawer-open', 'RGB')})
        tap.assert_not_called()
        restart.assert_not_called()

    def test_city_recovery_never_accepts_guide_as_city(self):
        with (patch.object(bot, 'screenshot', return_value=self.guides[-1].convert('L')),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_back') as back,
              self.assertRaises(desktop.StartupGuideBlocked)):
            bot.return_to_city('test', *[asset(name) for name in
                               ('help-icon', 'city-nav', 'forced-text', 'reconnect')])
        tap.assert_not_called()
        back.assert_not_called()

    def test_task_screen_stops_before_help_or_healing(self):
        with (patch.object(bot.time, 'sleep'),
              patch.object(bot, 'color_screenshot', return_value=self.guides[1]),
              patch.object(bot, 'check_wilderness_healing') as healing,
              patch.object(bot, 'task_tap') as tap,
              self.assertRaises(desktop.StartupGuideBlocked)):
            bot.task_screen('test')
        healing.assert_not_called()
        tap.assert_not_called()

    def test_guide_never_consumes_task_retries(self):
        with (patch.object(bot, 'screenshot') as capture,
              patch.object(bot.time, 'sleep') as sleep,
              patch.object(bot, 'save_task_failure') as save):
            with self.assertRaises(desktop.StartupGuideBlocked):
                bot.retry_once('task', lambda: bot.check_game_guide(self.guides[0]),
                               'test', None, None)
        capture.assert_not_called()
        sleep.assert_not_called()
        save.assert_not_called()

    def test_help_monitor_never_accepts_guide_as_city(self):
        with self.assertRaises(desktop.StartupGuideBlocked):
            bot.inspect(self.guides[-1].convert('L'), asset('help-icon'), asset('city-nav'))

    def test_guide_after_cold_reopen_never_launches_again(self):
        with (patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'screenshot', return_value=self.guides[0].convert('L')),
              patch.object(bot, 'adb') as adb,
              self.assertRaises(desktop.StartupGuideBlocked)):
            bot.reopen_frozen_game('test', *[asset(name) for name in
                                  ('help-icon', 'city-nav', 'forced-text', 'reconnect')])
        self.assertEqual(adb.call_count, 2)


if __name__ == '__main__':
    unittest.main()
