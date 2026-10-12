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
        self.assertIsNone(bot.gather_back_target(low))

    def test_hidden_coal_swipes_then_sets_level_and_checks_full_only(self):
        low, maximum = self.frame('gather-search-selected'), self.frame('gather-level-max')
        with (patch.object(bot, 'intelligence_text_target', side_effect=[(1030, 1850), (710, 1850)]),
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_screen', side_effect=[low, low, maximum, maximum]),
              patch.object(bot, 'gather_level', side_effect=[1, 2]),
              patch.object(bot, 'gather_full_only', side_effect=[False, True])):
            self.assertIs(bot.prepare_gather_search('test', low, 'coal', 2), maximum)
        adb.assert_called_once_with('test', 'shell', 'input', 'swipe', '900', '1780',
                                    '200', '1780', '600')
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(710, 1850), (730, 2000), (328, 2135)])

    def test_already_maximum_and_checked_are_kept(self):
        maximum = self.frame('gather-level-max')
        with (patch.object(bot, 'intelligence_text_target', return_value=(949, 1850)),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'gather_level', return_value=8),
              patch.object(bot, 'task_screen', return_value=maximum)):
            bot.prepare_gather_search('test', maximum, 'iron', 8)
        tap.assert_called_once_with('test', 949, 1850)

    def test_gathers_in_resource_order_and_stops_when_queue_fills(self):
        frame = self.frame('gather-live-start')
        for queues, expected in [([True], []),
                                 ([False] * 6 + [True], ['meat', 'wood']),
                                 ([False, True], []),
                                 ([False, False, True], [])]:
            with (patch.object(bot, 'enter_wilderness', return_value=frame),
                  patch.object(bot, 'town_button', return_value=True),
                  patch.object(bot, 'intelligence_queue_full', side_effect=queues) as queue,
                  patch.object(bot, 'prepare_gather_search', return_value=frame) as prepare,
                  patch.object(bot, 'gather_search_page', return_value=False),
                  patch.object(bot, 'gather_notice', return_value=None),
                  patch.object(bot, 'intelligence_text_target', side_effect=lambda _im, labels, _box:
                               (540, 1245) if labels == ('采集',) else (825, 2240)),
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'return_to_city', return_value=True) as back,
                  patch.object(bot, 'leave_gather', return_value=frame),
                  contextlib.redirect_stdout(io.StringIO())):
                bot.run_gather('test', *([None] * 4), resources=('iron', 'coal', 'wood', 'meat'))
            self.assertEqual(queue.call_count, len(queues))
            searched = [call.args[2] for call in prepare.call_args_list]
            self.assertEqual(searched, expected or (['meat'] if len(queues) > 1 else []))
            self.assertEqual([call.args[1:] for call in tap.call_args_list].count((540, 2240)),
                             2 if len(queues) == 7 else (1 if len(queues) == 3 else 0))
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
              patch.object(bot, 'gather_notice', return_value=None),
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

    def test_reads_level_from_captured_right_hand_number(self):
        self.assertEqual(bot.gather_level(self.frame('gather-search-selected')), 1)
        self.assertEqual(bot.gather_level(self.frame('gather-level-four')), 4)
        self.assertEqual(bot.gather_level(self.frame('gather-level-max')), 8)

    def test_real_no_target_and_empty_army_screens(self):
        self.assertEqual(bot.gather_notice(self.frame('gather-no-target')), 'no_target')
        empty = self.frame('gather-no-soldiers')
        self.assertEqual(bot.gather_notice(empty), 'no_soldiers')
        self.assertEqual(bot.gather_back_target(empty), (60, 190))

    def test_empty_army_returns_to_wilderness_without_city_recovery(self):
        empty, wilderness = self.frame('gather-no-soldiers'), self.frame('gather-live-start')
        with (patch.object(bot, 'town_button', side_effect=[True]),
              patch.object(bot, 'task_screen', return_value=wilderness),
              patch.object(bot, 'gather_back_target', side_effect=[(60, 190), None]),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'return_to_city') as city):
            self.assertIs(bot.leave_gather('test', empty), wilderness)
        tap.assert_called_once_with('test', 60, 190)
        city.assert_not_called()

    def test_sets_level_down_and_up_and_waits_for_unreadable_number(self):
        frame = self.frame('gather-search-selected')
        for numbers, level, clicks in [([8, 7, 6], 6, [105, 105]),
                                        ([None, 1, 2, 3], 3, [730, 730])]:
            with (patch.object(bot, 'gather_level', side_effect=numbers),
                  patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'task_tap') as tap):
                self.assertIs(bot.set_gather_level('test', frame, level), frame)
            self.assertEqual([c.args[1] for c in tap.call_args_list], clicks)

    def test_search_lowers_only_after_no_target_and_stops_at_found_resource(self):
        frame = self.frame('gather-search-selected')
        with (patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'intelligence_queue_full', return_value=False),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'gather_notice', side_effect=['no_target', 'no_target', None]),
              patch.object(bot, 'gather_search_page', return_value=False),
              patch.object(bot, 'intelligence_text_target', return_value=(540, 1245)),
              patch.object(bot, 'set_gather_level', return_value=frame) as adjust,
              contextlib.redirect_stdout(io.StringIO())):
            result = bot.search_gather_target('test', frame, 'coal', 4)
        self.assertEqual(result, (frame, (540, 1245), None))
        self.assertEqual([c.args[2] for c in adjust.call_args_list], [3, 2])
        self.assertEqual(tap.call_count, 3)

    def test_search_exhausts_at_one_without_clicking_below_one(self):
        frame = self.frame('gather-search-selected')
        with (patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'intelligence_queue_full', return_value=False),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'gather_notice', return_value='no_target'),
              patch.object(bot, 'set_gather_level') as adjust,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(bot.search_gather_target('test', frame, 'iron', 1),
                             (frame, None, 'no_target'))
        adjust.assert_not_called()
        tap.assert_called_once()

    def test_soldier_shortage_before_or_after_dispatch_leaves_wilderness(self):
        frame = self.frame('gather-live-start')
        for notices, dispatches in [(['no_soldiers'], 0), ([None, 'no_soldiers'], 1)]:
            with (patch.object(bot, 'enter_wilderness', return_value=frame),
                  patch.object(bot, 'town_button', return_value=True),
                  patch.object(bot, 'intelligence_queue_full', return_value=False),
                  patch.object(bot, 'prepare_gather_search', return_value=frame) as prepare,
                  patch.object(bot, 'search_gather_target', return_value=(frame, (540, 1245), None)),
                  patch.object(bot, 'gather_notice', side_effect=notices),
                  patch.object(bot, 'intelligence_text_target', return_value=(825, 2240)),
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'leave_gather', return_value=frame) as leave,
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertIs(bot.run_gather('test', *([None] * 4)), frame)
            self.assertEqual(prepare.call_count, 1)
            leave.assert_called_once()
            self.assertEqual(sum(c.args[1:] == (825, 2240) for c in tap.call_args_list), dispatches)

    def test_no_target_at_minimum_skips_resource_and_keeps_next_configured_level(self):
        frame = self.frame('gather-live-start')
        with (patch.object(bot, 'enter_wilderness', return_value=frame),
              patch.object(bot, 'town_button', return_value=True),
              patch.object(bot, 'intelligence_queue_full', return_value=False),
              patch.object(bot, 'prepare_gather_search', return_value=frame) as prepare,
              patch.object(bot, 'search_gather_target', side_effect=[
                  (frame, None, 'no_target'), (frame, None, 'no_soldiers')]),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'leave_gather', return_value=frame) as leave,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_gather('test', *([None] * 4),
                                        levels={'wood': 6}), frame)
        self.assertEqual([c.args[2:] for c in prepare.call_args_list], [('meat', 1), ('wood', 6)])
        self.assertEqual(leave.call_count, 2)

    def test_found_detail_can_overlay_the_search_panel(self):
        frame = self.frame('gather-search-selected')
        with (patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'intelligence_queue_full', return_value=False),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'gather_notice', return_value=None),
              patch.object(bot, 'intelligence_text_target', return_value=(540, 1245))):
            self.assertTrue(bot.gather_search_page(frame))
            self.assertEqual(bot.search_gather_target('test', frame, 'wood', 1),
                             (frame, (540, 1245), None))

    def test_next_resource_checks_fresh_queue_before_opening_search(self):
        stale, full = self.frame('gather-live-start'), self.frame('gather-queue-full')
        with (patch.object(bot, 'enter_wilderness', return_value=stale),
              patch.object(bot, 'town_button', return_value=True),
              patch.object(bot, 'task_screen', side_effect=[stale, full]),
              patch.object(bot, 'intelligence_queue_full', side_effect=lambda im: im is full) as queue,
              patch.object(bot, 'prepare_gather_search') as prepare,
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_gather('test', *([None] * 4)), full)
        queue.assert_called_once_with(full)
        prepare.assert_not_called()
        tap.assert_not_called()

    def test_lower_level_search_rechecks_queue_and_does_not_search_when_full(self):
        search, full = self.frame('gather-search-selected'), self.frame('gather-1007-stuck')
        with (patch.object(bot, 'task_screen', side_effect=[search, search, full]),
              patch.object(bot, 'intelligence_queue_full', side_effect=[False, True]) as queue,
              patch.object(bot, 'gather_notice', return_value='no_target'),
              patch.object(bot, 'set_gather_level', return_value=search),
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(bot.search_gather_target('test', search, 'wood', 5),
                             (full, None, 'queue_full'))
        self.assertEqual(queue.call_args_list[-1].args, (full,))
        tap.assert_called_once_with('test', 540, 2240)

    def test_leave_search_with_detail_uses_back_until_search_is_closed(self):
        detail, search, wilderness = map(self.frame, (
            'gather-1007-stuck', 'gather-1007-back', 'gather-1007-search-closed'))
        town = Image.open(bot.ROOT / 'assets/emulator-town-button.png').convert('L')
        with (patch.object(bot, 'TOWN_TEMPLATE', town),
              patch.object(bot, 'task_screen', side_effect=[search, wilderness]),
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb, patch.object(bot, 'task_tap') as tap):
            self.assertIs(bot.leave_gather('test', detail), wilderness)
        self.assertEqual([call.args for call in adb.call_args_list],
                         [('test', 'shell', 'input', 'keyevent', '4')] * 2)
        tap.assert_not_called()

    def test_unclosed_search_never_reports_wilderness(self):
        search = self.frame('gather-1007-back')
        with (patch.object(bot, 'task_screen', return_value=search),
              patch.object(bot, 'task_back') as back,
              patch.object(bot, 'town_button', return_value=True),
              self.assertRaisesRegex(RuntimeError, '未确认野外界面')):
            bot.leave_gather('test', search)
        self.assertEqual(back.call_count, 5)

    def test_notice_recognizes_empty_search_and_soldier_messages(self):
        frame = self.frame('gather-live-start')
        for text, expected in [('附近没有发现条件相符的目标', 'no_target'),
                               ('士兵不足，无法出征', 'no_soldiers'),
                               ('兵力不够', 'no_soldiers'), ('资源不足', None)]:
            with patch.object(bot, 'recognize_text', return_value=[(text, (0, 0, 10, 10))]):
                self.assertEqual(bot.gather_notice(frame), expected)

    def test_invalid_levels_fail_before_clicking(self):
        for level in (0, 9, '2', True):
            with (patch.object(bot, 'task_screen') as screen,
                  self.assertRaisesRegex(ValueError, '1 到 8')):
                bot.run_gather('test', *([None] * 4), levels={'meat': level})
            screen.assert_not_called()

    def test_cli_intelligence_then_gather_or_gather_alone(self):
        for intelligence in (False, True):
            argv = ['help_bot.py', '--device', 'test', '--gather', '--resources', 'wood', 'iron',
                    '--wood-level', '3', '--iron-level', '6']
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
            self.assertEqual(gather.call_args.kwargs['levels'], {'wood': 3, 'iron': 6})


if __name__ == '__main__':
    unittest.main()
