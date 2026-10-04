"""Resource search recognition, dispatch limits, and CLI task order."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot
from test_help_bot import Clock


class GatherTest(unittest.TestCase):
    def test_monitor_accepts_wilderness_without_returning_to_city(self):
        clock = Clock()
        frame = self.frame('gather-live-start').convert('L')
        nav = Image.open(bot.ROOT / 'assets/emulator-city-nav.png').convert('L')
        help_template = Image.open(bot.ROOT / 'assets/emulator-help-icon.png').convert('L')
        town = Image.open(bot.ROOT / 'assets/emulator-town-button.png').convert('L')
        with (patch.object(bot, 'TOWN_TEMPLATE', town),
              patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'screenshot', return_value=frame),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'return_to_city') as back,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertFalse(bot.inspect(frame, help_template, nav)[0])
            bot.run('test', 6, 1, False, 1, help_template, nav, None, None,
                    help_enabled=False, allow_wilderness=True)
        back.assert_not_called()
        self.assertEqual(clock.now, 106)

    def frame(self, name):
        return Image.open(bot.ROOT / f'artifacts/{name}.png').convert('RGB')

    def test_captured_search_controls(self):
        low = self.frame('gather-search-selected')
        maximum = self.frame('gather-level-max')
        wilderness = self.frame('gather-live-start')
        self.assertTrue(bot.gather_search_page(low))
        self.assertFalse(bot.gather_search_page(wilderness))
        self.assertFalse(bot.gather_level_max(low))
        self.assertTrue(bot.gather_level_max(maximum))
        self.assertTrue(bot.gather_full_only(maximum))
        maximum.paste((40, 70, 100), (300, 2110, 355, 2165))
        self.assertFalse(bot.gather_full_only(maximum))
        self.assertEqual(bot.gather_back_target(low), (540, 1550))

    def test_hidden_coal_swipes_then_maximizes_and_checks_full_only(self):
        low, maximum = self.frame('gather-search-selected'), self.frame('gather-level-max')
        with (patch.object(bot, 'intelligence_text_target', side_effect=[(1030, 1850), (710, 1850)]),
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_screen', side_effect=[low, low, maximum, maximum]),
              patch.object(bot, 'gather_full_only', side_effect=[False, True])):
            self.assertIs(bot.prepare_gather_search('test', low, 'coal'), maximum)
        adb.assert_called_once_with('test', 'shell', 'input', 'swipe', '900', '1780',
                                    '200', '1780', '600')
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(710, 1850), (730, 2000), (328, 2135)])

    def test_already_maximum_and_checked_are_kept(self):
        maximum = self.frame('gather-level-max')
        with (patch.object(bot, 'intelligence_text_target', return_value=(949, 1850)),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_screen', return_value=maximum)):
            bot.prepare_gather_search('test', maximum, 'iron')
        tap.assert_called_once_with('test', 949, 1850)

    def test_gathers_in_resource_order_and_stops_when_queue_fills(self):
        frame = self.frame('gather-live-start')
        for queues, expected in [([True], []),
                                 ([False, False, False, False, True], ['meat', 'wood']),
                                 ([False, True], [])]:
            with (patch.object(bot, 'enter_wilderness', return_value=frame),
                  patch.object(bot, 'town_button', return_value=True),
                  patch.object(bot, 'intelligence_queue_full', side_effect=queues) as queue,
                  patch.object(bot, 'prepare_gather_search', return_value=frame) as prepare,
                  patch.object(bot, 'gather_search_page', return_value=False),
                  patch.object(bot, 'intelligence_text_target', side_effect=lambda _im, labels, _box:
                               (540, 1245) if labels == ('采集',) else (825, 2240)),
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'return_to_city', return_value=True) as back,
                  contextlib.redirect_stdout(io.StringIO())):
                bot.run_gather('test', *([None] * 4), resources=('iron', 'coal', 'wood', 'meat'))
            self.assertEqual(queue.call_count, len(queues))
            searched = [call.args[2] for call in prepare.call_args_list]
            self.assertEqual(searched, expected or (['meat'] if len(queues) == 2 else []))
            self.assertEqual([call.args[1:] for call in tap.call_args_list].count((825, 2240)),
                             len(expected))
            back.assert_not_called()

    def test_only_selected_resources_are_searched(self):
        frame = self.frame('gather-live-start')
        with (patch.object(bot, 'enter_wilderness', return_value=frame),
              patch.object(bot, 'town_button', return_value=True),
              patch.object(bot, 'intelligence_queue_full', return_value=False),
              patch.object(bot, 'prepare_gather_search', return_value=frame) as prepare,
              patch.object(bot, 'gather_search_page', return_value=False),
              patch.object(bot, 'intelligence_text_target', return_value=(825, 2240)),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'return_to_city', return_value=True),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_gather('test', *([None] * 4), resources=('iron',))
        self.assertEqual([call.args[2] for call in prepare.call_args_list], ['iron'])

    def test_invalid_resources_fail_before_clicking(self):
        for resources in ((), ('gold',)):
            with (patch.object(bot, 'task_screen') as screen,
                  self.assertRaisesRegex(ValueError, '至少要勾选一项')):
                bot.run_gather('test', *([None] * 4), resources=resources)
            screen.assert_not_called()

    def test_cli_intelligence_then_gather_or_gather_alone(self):
        for intelligence in (False, True):
            argv = ['help_bot.py', '--device', 'test', '--gather', '--resources', 'wood', 'iron']
            if intelligence:
                argv.append('--intelligence')
            with (patch('sys.argv', argv),
                  patch.object(bot, 'emulator_device', return_value=True),
                  patch.object(bot, 'return_to_city', return_value=True),
                  patch.object(bot, 'run_schedule') as schedule):
                bot.main()
            tasks = schedule.call_args.args[4]
            self.assertEqual(list(tasks), ['intelligence', 'gather'] if intelligence else ['gather'])
            calls = []
            with (patch.object(bot, 'run_intelligence', side_effect=lambda *a, **kw: calls.append('intelligence')),
                  patch.object(bot, 'run_gather', side_effect=lambda *a, **kw: calls.append('gather')) as gather):
                for task in tasks.values():
                    task()
            self.assertEqual(calls, list(tasks))
            self.assertEqual(gather.call_args.kwargs['resources'], ['wood', 'iron'])


if __name__ == '__main__':
    unittest.main()
