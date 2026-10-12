"""Delayed and closed drawer frames from the emulator-5554 treasure failure."""
import unittest
from unittest.mock import call, patch

from PIL import Image
import help_bot as bot


class DrawerRecoveryTest(unittest.TestCase):
    def setUp(self):
        def frame(name):
            with Image.open(bot.ROOT / name) as image:
                return image.convert('RGB')
        self.template = frame('assets/emulator-drawer-open.png')
        self.closed = frame('tests/fixtures/treasure-1007-drawer-failure-5554.png')
        self.opened = frame('artifacts/treasure-completed-drawer.png')
        self.wilderness = frame('tests/fixtures/treasure-1007-before-5554.png')
        self.reward = frame('artifacts/treasure-reward.png')
        for name, value in (('TASK_DRAWER_SCROLL', 2), ('TASK_HELP', None)):
            patcher = patch.object(bot, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_only_closed_city_handle_allows_reopening(self):
        self.assertTrue(bot.closed_city_drawer(self.closed, self.template))
        for frame in (self.opened, self.wilderness, self.reward):
            self.assertFalse(bot.closed_city_drawer(frame, self.template))

    def test_delayed_frame_waits_without_reopening(self):
        with (patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_screen', side_effect=[self.closed, self.opened]) as screen):
            self.assertIs(bot.swipe_drawer('test', self.template), self.opened)
        self.assertEqual(bot.TASK_DRAWER_SCROLL, 3)
        self.assertEqual(adb.call_count, 1)
        self.assertEqual(screen.call_args_list, [call('test'), call('test', help_enabled=False)])
        tap.assert_not_called()

    def test_ignored_open_tap_retries_only_a_confirmed_closed_handle(self):
        with (patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_screen', side_effect=[self.closed, self.closed, self.opened])):
            self.assertIs(bot.task_drawer('test', self.closed, {'drawer-open': self.template}), self.opened)
        self.assertEqual(tap.call_args_list, [call('test', 20, 1100)] * 2)
        self.assertEqual(bot.TASK_DRAWER_SCROLL, 0)

    def test_closed_drawer_replays_forward_and_reverse_position(self):
        for reverse, expected in ((False, 3), (True, 1)):
            with (self.subTest(reverse=reverse),
                  patch.object(bot, 'TASK_DRAWER_SCROLL', 2),
                  patch.object(bot, 'game_foreground', return_value=True),
                  patch.object(bot, 'adb') as adb,
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot, 'task_screen', side_effect=
                               [self.closed] * 5 + [self.opened] * (1 + expected))):
                self.assertIs(bot.swipe_drawer('test', self.template, reverse=reverse), self.opened)
                self.assertEqual(bot.TASK_DRAWER_SCROLL, expected)
                tap.assert_called_once_with('test', 20, 1100)
                self.assertEqual(adb.call_count, 1 + expected)

    def test_unknown_page_clears_scroll_without_tapping(self):
        with (patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_screen', return_value=self.reward),
              self.assertRaisesRegex(RuntimeError, '滑动后未能确认主城抽屉')):
            bot.swipe_drawer('test', self.template)
        self.assertIsNone(bot.TASK_DRAWER_SCROLL)
        self.assertEqual(adb.call_count, 1)
        tap.assert_not_called()

    def test_repeated_closure_does_not_recover_twice(self):
        with (patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_screen', side_effect=
                           [self.closed] * 5 + [self.opened] + [self.closed] * 5),
              self.assertRaisesRegex(RuntimeError, '滑动后未能确认主城抽屉')):
            bot.swipe_drawer('test', self.template)
        self.assertIsNone(bot.TASK_DRAWER_SCROLL)
        self.assertEqual(adb.call_count, 2)
        tap.assert_called_once_with('test', 20, 1100)


if __name__ == '__main__':
    unittest.main()
