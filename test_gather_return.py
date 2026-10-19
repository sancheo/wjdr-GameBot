"""Each march waits for its own gathering and return timers before confirmation."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot
from gather_return import GatherTask, MarchRow
from test_help_bot import Clock


class GatherReturnTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        timer = patch.object(bot.time, 'monotonic', self.clock.monotonic)
        timer.start()
        self.addCleanup(timer.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        self.task = GatherTask(bot, 'test', (None,) * 4)
        self.wood = Image.new('RGB', (54, 48), (130, 80, 20))
        self.hunt = Image.new('RGB', (54, 48), (230, 30, 30))
        self.task.icons = [self.wood]
        self.task.first = False

    def row(self, phase, seconds, icon=None):
        return MarchRow(icon if icon is not None else self.wood, phase, seconds)

    def sync(self, rows):
        return self.task._sync(rows, self.clock.now)

    def ready(self, rows):
        with (patch.object(bot, 'task_screen'), patch.object(bot, 'enter_wilderness'),
              patch.object(self.task, 'rows', return_value=rows)):
            return self.task.ready()

    def test_waits_for_travel_gathering_then_return_before_queue_confirmation(self):
        self.sync([self.row('outbound', 20)])
        self.assertEqual(self.task.next_check_at, 120)
        for now in (101, 110, 119):
            self.clock.now = now
            with patch.object(bot, 'task_screen') as screen:
                self.assertFalse(self.task.ready())
            screen.assert_not_called()
        self.clock.now = 120
        self.assertFalse(self.ready([self.row('gathering', 3600)]))
        self.assertEqual(self.task.next_check_at, 3720)
        self.clock.now = 3720
        self.assertFalse(self.ready([self.row('returning', 30)]))
        self.assertEqual(self.task.next_check_at, 3750)
        self.clock.now = 3749
        with patch.object(bot, 'task_screen') as screen:
            self.assertFalse(self.task.ready())
        screen.assert_not_called()
        self.clock.now = 3750
        self.assertFalse(self.ready([]))
        self.assertEqual(self.task.next_check_at, 3755)
        self.clock.now = 3755
        self.assertTrue(self.ready([]))
        self.assertEqual(self.task.queues, [])

    def test_disappearance_without_observed_return_timer_never_triggers(self):
        self.sync([self.row('gathering', 10)])
        self.clock.now = 110
        self.assertFalse(self.ready([]))
        self.assertEqual(self.task.next_check_at, 170)
        self.clock.now = 170
        self.assertFalse(self.ready([]))
        self.assertEqual(self.task.next_check_at, 230)

    def test_expired_return_ignores_other_marches_replacing_its_capacity(self):
        self.sync([self.row('returning', 10), self.row('outbound', 8, self.hunt)])
        self.clock.now = 110
        # An intelligence march can replace the returning resource's capacity.
        self.assertFalse(self.ready([self.row('outbound', 300, self.hunt),
                                     self.row('outbound', 300, self.hunt)]))
        self.assertEqual(self.task.next_check_at, 115)
        self.clock.now = 115
        self.assertTrue(self.ready([self.row('outbound', 295, self.hunt),
                                    self.row('outbound', 295, self.hunt)]))

    def test_only_returned_configured_item_is_released(self):
        self.task = GatherTask(bot, 'test', (None,) * 4,
                               steps=[('meat', 8), ('wood', 8), ('coal', 3)])
        icons = [self.hunt, self.wood, Image.new('RGB', (54, 48), (30, 30, 30))]
        self.task.icons = icons
        self.sync([self.row('gathering', seconds, icon)
                   for seconds, icon in zip((500, 1000, 2000), icons)])
        self.assertEqual(self.task.pending, [])
        self.clock.now = 600
        self.sync([self.row('returning', 10, icons[0]),
                   self.row('gathering', 500, icons[1]), self.row('gathering', 1500, icons[2])])
        self.clock.now = 610
        remaining = [self.row('gathering', 490, icons[1]), self.row('gathering', 1490, icons[2]),
                     self.row('outbound', 100, Image.new('RGB', (54, 48), (250, 250, 250)))]
        self.assertFalse(self.sync(remaining))
        self.clock.now = 615
        self.assertTrue(self.sync(remaining))
        with patch.object(self.task, 'rows', return_value=remaining):
            self.assertEqual(self.task.available_steps(object()), [(0, ('meat', 8))])
        self.assertEqual(len(self.task.queues), 2)

    def test_existing_configured_queues_prevent_replay_on_restart(self):
        self.task = GatherTask(bot, 'test', (None,) * 4, steps=[('coal', 8)] * 3)
        rows = [self.row('gathering', n) for n in (100, 200, 300)]
        rows.append(self.row('outbound', 40, self.hunt))
        with patch.object(self.task, 'rows', return_value=rows):
            self.assertEqual(self.task.available_steps(object()), [])
            self.assertEqual(self.task.available_steps(object()), [])
        self.assertEqual([q.step for q in self.task.queues], [0, 1, 2])

    def test_delayed_dispatch_ocr_reserves_item_until_it_can_be_identified(self):
        self.task = GatherTask(bot, 'test', (None,) * 4,
                               steps=[('wood', 8), ('coal', 3)])
        with patch.object(self.task, 'rows', side_effect=[[], None]):
            self.task.dispatched(object(), object(), step=0)
        with patch.object(self.task, 'rows', return_value=[self.row('gathering', 100)]):
            self.assertEqual(self.task.available_steps(object()), [(1, ('coal', 3))])
        self.assertEqual([q.step for q in self.task.queues], [0])

    def test_extra_existing_gather_queues_do_not_create_additional_config_slots(self):
        self.task = GatherTask(bot, 'test', (None,) * 4, steps=[('coal', 8)] * 3)
        rows = [self.row('gathering', n) for n in (100, 200, 300, 400, 500)]
        with patch.object(self.task, 'rows', return_value=rows):
            self.assertEqual(self.task.available_steps(object()), [])
        self.assertEqual([q.step for q in self.task.queues], [0, 1, 2, None, None])

    def test_unreadable_drawer_cannot_dispatch_configured_items(self):
        with patch.object(self.task, 'rows', return_value=None):
            with self.assertRaises(bot.QueueCapacityUnavailable):
                self.task.available_steps(object())

    def test_startup_unidentified_return_cannot_duplicate_a_configured_item(self):
        self.task = GatherTask(bot, 'test', (None,) * 4, steps=[('wood', 8)] * 3)
        rows = [self.row('gathering', 1000), self.row('gathering', 2000),
                self.row('returning', 20, self.hunt)]
        with patch.object(self.task, 'rows', return_value=rows):
            self.assertEqual(self.task.available_steps(object()), [])
        self.assertEqual(len(self.task.queues), 2)
        with patch.object(self.task, 'rows', return_value=rows[:2]):
            self.assertEqual(self.task.available_steps(object()), [(2, ('wood', 8))])

    def test_repeated_sync_of_same_frame_does_not_skip_return_confirmation(self):
        self.sync([self.row('returning', 10)])
        self.clock.now = 110
        self.assertFalse(self.sync([]))
        self.assertFalse(self.sync([]))
        self.assertEqual(len(self.task.queues), 1)
        self.clock.now = 115
        self.assertTrue(self.sync([]))

    def test_returning_resource_still_visible_is_not_home(self):
        self.sync([self.row('returning', 10), self.row('outbound', 10, self.hunt)])
        self.clock.now = 110
        self.assertFalse(self.ready([self.row('returning', 4)]))
        self.assertEqual(self.task.next_check_at, 114)

    def test_duplicate_resource_queues_keep_separate_deadlines_after_reorder(self):
        self.sync([self.row('gathering', 100), self.row('gathering', 200)])
        first, second = self.task.queues
        self.clock.now = 200
        self.assertFalse(self.ready([self.row('gathering', 100), self.row('returning', 15)]))
        self.assertEqual((first.phase, first.due), ('returning', 215))
        self.assertEqual((second.phase, second.due), ('gathering', 300))
        self.assertEqual(self.task.next_check_at, 215)
        self.clock.now = 215
        self.assertFalse(self.ready([self.row('gathering', 85)]))
        self.clock.now = 220
        self.assertTrue(self.ready([self.row('gathering', 80)]))
        self.assertEqual([queue.number for queue in self.task.queues], [second.number])
        self.assertEqual(self.task.next_check_at, 300)

    def test_failed_ocr_retries_after_sixty_seconds_without_five_second_poll(self):
        self.sync([self.row('gathering', 10), self.row('gathering', 200)])
        self.clock.now = 110
        self.assertFalse(self.ready(None))
        self.assertEqual(self.task.next_check_at, 170)
        self.clock.now = 115
        with patch.object(bot, 'task_screen') as screen:
            self.assertFalse(self.task.ready())
        screen.assert_not_called()

    def test_close_duplicate_countdowns_preserve_both_queue_identities(self):
        self.sync([self.row('gathering', 100), self.row('gathering', 110)])
        first, second = self.task.queues
        self.clock.now = 200
        self.ready([self.row('gathering', 9), self.row('gathering', 18)])
        self.assertEqual((first.due, second.due), (209, 218))

    def test_return_label_is_recorded_with_its_own_countdown(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        lines = [('行军', (65, 44, 70, 38)), ('1/4', (295, 45, 65, 36)),
                 ('返回中', (92, 117, 120, 27)), ('00:00:21', (144, 152, 90, 21))]
        with patch.object(bot, 'recognize_text', return_value=lines):
            rows = self.task.rows(frame)
        self.assertEqual([(row.phase, row.seconds) for row in rows], [('returning', 21)])

    def test_drawer_disappearance_is_confirmed_visually_when_ocr_is_empty(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        with patch.object(bot, 'recognize_text', return_value=[]):
            self.assertIsNone(self.task.rows(frame))
            self.task.header = frame.crop((10, 455, 360, 515)).convert('RGB')
            self.assertIsNone(self.task.rows(frame))
            # Regression fixture for the drawer's absence, not a matching asset.
            frame.paste('white', (10, 455, 360, 515))
            self.assertEqual(self.task.rows(frame), [])

    def test_other_hunt_return_does_not_trigger_gather(self):
        self.sync([self.row('gathering', 1000), self.row('outbound', 10, self.hunt)])
        self.assertEqual(self.task.next_check_at, 1100)
        self.clock.now = 110
        with patch.object(bot, 'task_screen') as screen:
            self.assertFalse(self.task.ready())
        screen.assert_not_called()

    def test_duplicate_dispatch_learns_only_the_new_resource(self):
        self.task.icons = []
        with patch.object(self.task, 'rows', side_effect=[
                [self.row('outbound', 40), self.row('outbound', 30, self.hunt)],
                [self.row('outbound', 39), self.row('outbound', 29, self.hunt), self.row('outbound', 60)]]):
            self.task.dispatched(object(), object())
        self.assertTrue(self.task.known(self.wood))
        self.assertFalse(self.task.known(self.hunt))
        self.assertEqual(len(self.task.queues), 2)
        self.assertEqual(sorted(queue.due for queue in self.task.queues), [139, 160])

    def test_nox_march_rows_read_each_phase_and_countdown(self):
        with Image.open(bot.ROOT / 'tests/fixtures/nox/20261018-131652-404691/tour-gather-078.png') as frame:
            rows = self.task.rows(frame)
        self.assertEqual([row.phase for row in rows], ['gathering', 'gathering', 'gathering', 'outbound'])
        self.assertEqual([row.seconds for row in rows], [21721, 21828, 22025, 50])
        self.task.icons = []
        self.task.learn(rows)
        self.assertEqual(len(self.task.icons), 4)
        self.assertTrue(self.task.known(rows[-1].icon))

    def test_nox_resource_icons_identify_existing_outbound_item_by_configuration(self):
        from screen_geometry import ScreenAdapter
        path = bot.ROOT / 'tests/fixtures/nox/20261018-131652-404691/tour-gather-078-native.png'
        with Image.open(path) as source:
            frame = ScreenAdapter().frame('sample', source.convert('RGB'))
        self.task = GatherTask(bot, 'test', (None,) * 4,
                               steps=[('iron', 7), ('coal', 3), ('wood', 8), ('meat', 8)])
        rows = self.task.rows(frame)
        self.assertEqual([row.resource for row in rows], ['meat', 'wood', 'coal', 'iron'])
        self.sync(rows)
        self.assertEqual([queue.step for queue in self.task.queues], [3, 2, 1, 0])
        self.assertEqual(self.task.pending, [])

    def test_main_nox_three_coal_queues_ignore_live_intelligence_march(self):
        from screen_geometry import ScreenAdapter
        path = bot.ROOT / 'tests/fixtures/nox/gather-main-intel-mixed-1019-native.png'
        with Image.open(path) as source:
            frame = ScreenAdapter().frame('main', source.convert('RGB'))
        self.task = GatherTask(bot, 'test', (None,) * 4, steps=[('coal', 8)] * 3)
        rows = self.task.rows(frame)
        self.assertEqual([row.resource for row in rows], ['coal', 'coal', 'coal', None])
        self.assertEqual([row.phase for row in rows], ['gathering'] * 3 + ['outbound'])
        self.sync(rows)
        self.assertEqual(len(self.task.queues), 3)
        self.assertEqual(self.task.pending, [])

    def test_main_nox_return_flag_inherits_only_the_missing_coal_queue(self):
        from screen_geometry import ScreenAdapter
        frames = []
        for name in ('gather-main-intel-mixed-1019', 'gather-main-single-return-1019'):
            with Image.open(bot.ROOT / f'tests/fixtures/nox/{name}-native.png') as source:
                frames.append(ScreenAdapter().frame(name, source.convert('RGB')))
        self.task = GatherTask(bot, 'test', (None,) * 4, steps=[('coal', 8)] * 3)
        before, after = [self.task.rows(frame) for frame in frames]
        # The ordinary intelligence march has finished before the coal recall.
        self.sync(before[:3])
        first, second, third = self.task.queues
        self.sync(after)
        self.assertEqual((first.phase, second.phase, third.phase),
                         ('returning', 'gathering', 'gathering'))
        self.assertEqual(first.due, self.clock.now + after[0].seconds)
        self.assertEqual(self.task.pending, [])

    def test_other_new_return_flag_does_not_replace_visible_gathering_queue(self):
        self.sync([self.row('gathering', 1000), self.row('outbound', 30, self.hunt)])
        self.sync([self.row('gathering', 990), self.row('returning', 20, self.hunt)])
        self.assertEqual(len(self.task.queues), 1)
        self.assertEqual(self.task.queues[0].phase, 'gathering')

    def test_incomplete_or_failed_ocr_is_unknown(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for lines in ([], [('行军', (0, 0, 50, 30)), ('2/4', (200, 0, 50, 30))],
                      [('2/4', (200, 0, 50, 30)), ('00:00:03', (140, 150, 90, 24))]):
            with patch.object(bot, 'recognize_text', return_value=lines):
                self.assertIsNone(self.task.rows(frame))

    def test_repeated_runs_keep_full_configured_order(self):
        steps = [('wood', 8), ('iron', 7), ('wood', 6)]
        self.task.options['steps'] = steps
        with (patch.object(bot, 'run_gather', return_value=object()) as run,
              patch.object(self.task, 'rows', return_value=[self.row('gathering', 3600)])):
            self.task()
            self.task()
        self.assertEqual([call.kwargs['steps'] for call in run.call_args_list], [steps, steps])
        self.assertEqual(self.task.next_check_at, 3700)

    def test_scheduler_uses_saved_deadlines_for_probes_and_after_dispatch(self):
        timers, checks, calls = {}, [], []
        clock = self.clock
        class Task:
            next_check_at = 0
            def ready(self):
                checks.append(clock.now)
                self.next_check_at = {100: 110, 400: 430, 430: 435, 435: 9999}[clock.now]
                return clock.now in (100, 435)
            def __call__(self):
                calls.append(clock.now)
                self.next_check_at = 400 if clock.now == 100 else 10000
        def intelligence():
            bot.record_timer(timers, 'intelligence', 10)
        with patch.object(bot, 'run', side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds)):
            bot.run_schedule('test', 601, 2, False,
                             {'gather': Task(), 'intelligence': intelligence},
                             *([None] * 4), help_enabled=False, timers=timers)
        self.assertEqual(checks, [100, 400, 430, 435])
        self.assertEqual(calls, [100, 435])
        self.assertEqual(timers['gather'], 10000)


if __name__ == '__main__':
    unittest.main()
