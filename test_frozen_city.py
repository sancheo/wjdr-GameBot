"""Recovery for the unchanged October 8 city that stopped three tasks."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot
from test_help_bot import Clock
from test_runtime_recovery import asset, frame


class FrozenCityTest(unittest.TestCase):
    def setUp(self):
        with Image.open(bot.ROOT / 'tests/fixtures/frozen-city-1008.png') as im:
            self.frozen = im.convert('RGB')
        self.city = frame('stamina-ready')
        self.opened = frame('treasure-completed-drawer')
        self.templates = {'drawer-open': asset('drawer-open', 'RGB')}
        self.clock = Clock()
        self.enterContext(patch.object(bot.time, 'monotonic', self.clock.monotonic))
        self.enterContext(patch.object(bot.time, 'sleep', self.clock.sleep))
        self.enterContext(patch.object(bot, 'TASK_HELP', None))
        self.enterContext(patch.object(bot, 'TASK_DRAWER_SCROLL', 4))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def drawer(self):
        return bot.task_drawer('test', self.frozen, self.templates)

    def test_real_failure_reopens_and_confirms_usable_drawer(self):
        restarted = []
        taps = []
        def screen(*args, **kwargs):
            self.clock.sleep(1)
            return self.opened if restarted else self.frozen
        with (patch.object(bot, 'TOWN_TEMPLATE', asset('town-button')),
              patch.object(bot, 'task_tap', side_effect=lambda *a: taps.append(self.clock.now)),
              patch.object(bot, 'task_screen', side_effect=screen),
              patch.object(bot, 'color_screenshot', return_value=self.city),
              patch.object(bot, 'screenshot', return_value=self.city.convert('L')),
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb', side_effect=lambda *a: restarted.append(a))):
            self.assertIs(self.drawer(), self.opened)
        self.assertEqual(restarted, [('test', 'shell', 'am', 'force-stop', 'com.gof.china'),
                                    ('test', 'shell', 'monkey', '-p', 'com.gof.china',
                                     '-c', 'android.intent.category.LAUNCHER', '1')])
        self.assertEqual(len(taps), 4)
        self.assertGreaterEqual(taps[-1] - taps[0], 15)
        self.assertEqual(bot.TASK_DRAWER_SCROLL, 0)

    def test_changing_city_does_not_restart_even_if_final_frame_returns(self):
        changed = self.frozen.copy()
        changed.paste('white', (300, 400, 600, 700))
        with (patch.object(bot, 'task_tap'),
              patch.object(bot, 'task_screen', side_effect=[self.frozen, changed] + [self.frozen] * 3),
              patch.object(bot, 'reopen_frozen_game') as reopen,
              self.assertRaisesRegex(RuntimeError, '未能确认主城抽屉')):
            self.drawer()
        reopen.assert_not_called()

    def test_delayed_open_during_observation_does_not_restart(self):
        with (patch.object(bot, 'task_tap'),
              patch.object(bot, 'task_screen', side_effect=[self.frozen] * 5 + [self.opened]),
              patch.object(bot, 'reopen_frozen_game') as reopen):
            self.assertIs(self.drawer(), self.opened)
        reopen.assert_not_called()
        self.assertEqual(bot.TASK_DRAWER_SCROLL, 0)

    def test_unchanged_after_restart_fails_without_second_restart(self):
        with (patch.object(bot, 'task_tap'),
              patch.object(bot, 'task_screen', return_value=self.frozen),
              patch.object(bot, 'color_screenshot', return_value=self.frozen),
              patch.object(bot, 'reopen_frozen_game') as reopen,
              self.assertRaisesRegex(RuntimeError, '未能确认主城抽屉')):
            self.drawer()
        reopen.assert_called_once()

    def test_unknown_page_does_not_restart(self):
        unknown = frame('treasure-reward')
        with (patch.object(bot, 'task_tap'),
              patch.object(bot, 'task_screen', return_value=unknown),
              patch.object(bot, 'reopen_frozen_game') as reopen,
              self.assertRaisesRegex(RuntimeError, '未能确认主城抽屉')):
            bot.task_drawer('test', unknown, self.templates)
        reopen.assert_not_called()

    def test_foreground_loss_prevents_restart(self):
        with (patch.object(bot, 'game_foreground', return_value=False),
              patch.object(bot, 'adb') as adb,
              self.assertRaisesRegex(RuntimeError, '未重新打开')):
            bot.reopen_frozen_game('test', *[asset(n) for n in
                                   ('help-icon', 'city-nav', 'forced-text', 'reconnect')])
        adb.assert_not_called()

    def test_restart_loading_failure_is_bounded(self):
        with (patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'screenshot', return_value=self.frozen),
              patch('gui_web.game_loaded', return_value=False),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'return_to_city') as recover,
              self.assertRaisesRegex(RuntimeError, '未确认游戏进入前台并加载完成')):
            bot.reopen_frozen_game('test', *[asset(n) for n in
                                   ('help-icon', 'city-nav', 'forced-text', 'reconnect')])
        self.assertEqual(adb.call_count, 6)
        recover.assert_not_called()
        self.assertEqual(self.clock.now, 640)

    def test_cold_launch_loses_foreground_then_second_launch_recovers(self):
        launches = []
        def command(*args):
            if 'monkey' in args:
                launches.append(self.clock.now)
        def foreground(_):
            return not launches or len(launches) >= 2
        with (patch.object(bot, 'game_foreground', side_effect=foreground),
              patch.object(bot, 'screenshot', return_value=self.city),
              patch('gui_web.game_loaded', return_value=True),
              patch.object(bot, 'adb', side_effect=command),
              patch.object(bot, 'return_to_city', return_value=True) as recover,
              patch.object(bot, 'task_tap') as tap):
            self.assertTrue(bot.reopen_frozen_game('test', *[asset(n) for n in
                            ('help-icon', 'city-nav', 'forced-text', 'reconnect')]))
        self.assertEqual(launches, [100, 280])
        recover.assert_called_once()
        self.assertFalse(recover.call_args.kwargs['_reopen_frozen'])
        tap.assert_not_called()

    def test_cancel_after_failed_launch_prevents_relaunch(self):
        def stop():
            if self.clock.now >= 280:
                raise bot.StopRequested()
        with (patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'screenshot', return_value=self.frozen),
              patch('gui_web.game_loaded', return_value=False),
              patch.object(bot, 'check_stop', side_effect=stop),
              patch.object(bot, 'adb') as adb,
              self.assertRaises(bot.StopRequested)):
            bot.reopen_frozen_game('test', *[asset(n) for n in
                                   ('help-icon', 'city-nav', 'forced-text', 'reconnect')])
        self.assertEqual(adb.call_count, 2)

    def test_loaded_game_without_city_does_not_relaunch(self):
        with (patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'screenshot', return_value=self.city),
              patch('gui_web.game_loaded', return_value=True),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'return_to_city', side_effect=RuntimeError('主城未恢复')),
              self.assertRaisesRegex(RuntimeError, '主城未恢复')):
            bot.reopen_frozen_game('test', *[asset(n) for n in
                                   ('help-icon', 'city-nav', 'forced-text', 'reconnect')])
        self.assertEqual(adb.call_count, 2)

    def test_forced_logout_after_restart_does_not_resume_tasks(self):
        with (patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'screenshot', return_value=self.city),
              patch('gui_web.game_loaded', return_value=True),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'return_to_city', return_value=False),
              self.assertRaisesRegex(RuntimeError, '强制下线，未恢复任务')):
            bot.reopen_frozen_game('test', *[asset(n) for n in
                                   ('help-icon', 'city-nav', 'forced-text', 'reconnect')])
        self.assertEqual(adb.call_count, 2)


if __name__ == '__main__':
    unittest.main()
