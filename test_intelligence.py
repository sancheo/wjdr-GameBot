"""Check lighthouse task routing against captured screens and a stateful game flow."""
import contextlib
import io
import unittest
from unittest.mock import patch
from PIL import Image
import help_bot as bot
from test_help_bot import Clock


class IntelligenceTest(unittest.TestCase):
    def test_full_queue_skips_round_and_schedules_recheck(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for full, delay in [(True, 102), (True, None), (True, 0), (False, None)]:
            clock, timers = Clock(), {}
            with (patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'intelligence_page', return_value=False),
                  patch.object(bot, 'inspect', return_value=(False, None, 0)),
                  patch.object(bot, 'town_button', return_value=True),
                  patch.object(bot, 'intelligence_queue_full', return_value=full),
                  patch.object(bot, 'intelligence_queue_countdown', return_value=delay) as countdown,
                  patch.object(bot, 'open_intelligence', return_value=frame) as enter,
                  patch.object(bot, 'labelled_countdown', return_value=300),
                  patch.object(bot, 'intelligence_text_target', return_value=None),
                  patch.object(bot, 'intelligence_target', return_value=None),
                  patch.object(bot, 'bounty_target', return_value=None),
                  patch.object(bot, 'execute_intelligence') as execute,
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot, 'return_to_city', return_value=True) as back,
                  patch.object(bot.time, 'monotonic', clock.monotonic),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertIs(bot.run_intelligence('test', *([None] * 4),
                                                   timers=timers, bounty=True), frame)
            self.assertEqual(enter.call_count, int(not full))
            self.assertEqual(countdown.call_count, int(full))
            execute.assert_not_called()
            tap.assert_not_called()
            back.assert_not_called()
            expected = (60 if delay is None else max(1, delay)) if full else 300
            self.assertEqual(timers['intelligence'], clock.now + expected)

    def test_action_rechecks_queue_before_dispatch(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for full in (False, True):
            with (patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'recognize_text', return_value=[]),
                  patch.object(bot, 'intelligence_text_target', side_effect=[
                      (540, 1680), (540, 1230), None]),
                  patch.object(bot, 'intelligence_back_target', return_value=None),
                  patch.object(bot, 'town_button', return_value=True),
                  patch.object(bot, 'intelligence_queue_full', return_value=full) as queue,
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot.time, 'sleep'),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(bot.execute_intelligence('test', frame)[1], not full)
            queue.assert_called_once_with(frame)
            self.assertEqual([c.args[1:] for c in tap.call_args_list],
                             [(540, 1680)] if full else [(540, 1680), (540, 1230)])

    def test_grayscale_icons_and_completed_badges(self):
        original = Image.open(bot.ROOT / 'artifacts/intel-map.png').convert('RGB')
        completed = Image.open(bot.ROOT / 'artifacts/intel-map-progress.png').convert('RGB')
        self.assertTrue(bot.intelligence_page(original))
        self.assertFalse(bot.intelligence_page(Image.new('RGB', bot.EXPECTED_SIZE)))
        self.assertEqual(bot.intelligence_target(original), (353, 1009))
        remaining = Image.open(bot.ROOT / 'artifacts/intel-map-remaining.png').convert('RGB')
        self.assertIsNotNone(bot.intelligence_target(remaining))
        for name, position in [('wolf', (353, 1009)), ('swords', (502, 892)), ('tent', (837, 1120))]:
            template = Image.open(bot.ROOT / f'assets/emulator-intel-{name}.png').convert('L')
            self.assertEqual(template.mode, 'L')
            self.assertFalse(bot.intelligence_completed(original, position))
        for position in [(502, 892), (837, 1120), (759, 1390)]:
            self.assertTrue(bot.intelligence_completed(completed, position))
        # Different blue and gold backgrounds carry the same animal glyph.
        template = Image.open(bot.ROOT / 'assets/emulator-intel-wolf.png').convert('L')
        for box in [(285, 850, 420, 990), (315, 940, 395, 1050)]:
            score, _, _ = bot.find_white(original, template, range(box[0], box[2], 2),
                                         range(box[1], box[3], 2))
            self.assertGreater(score, .97)

    def test_queue_waits_then_resumes_and_unknown_capacity_fails(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for lines, expected in [([], False), (['探索', '1'], False),
                                (['行军', '1/4'], False), (['行军', '4/4'], True)]:
            with patch.object(bot, 'recognize_text', return_value=[(s, (0, 0, 10, 10)) for s in lines]):
                self.assertEqual(bot.intelligence_queue_full(frame), expected)
        with patch.object(bot, 'recognize_text', return_value=[('行军', (0, 0, 10, 10))]):
            with self.assertRaisesRegex(RuntimeError, '队列容量未识别'):
                bot.intelligence_queue_full(frame)
        for help_enabled in (False, True):
            for delays in ((102, 28), (None, 0)):
                clock, checked = Clock(), []

                def queue_full(_image):
                    checked.append(clock.now)
                    return len(checked) < 3

                with (patch.object(bot, 'TASK_HELP', object() if help_enabled else None),
                      patch.object(bot, 'intelligence_queue_full', side_effect=queue_full),
                      patch.object(bot, 'intelligence_queue_countdown', side_effect=delays) as timer,
                      patch.object(bot, 'town_button', return_value=True),
                      patch.object(bot, 'task_screen', return_value=frame) as screen,
                      patch.object(bot.time, 'sleep', clock.sleep),
                      patch.object(bot.time, 'monotonic', clock.monotonic),
                      contextlib.redirect_stdout(io.StringIO()) as output):
                    self.assertIs(bot.intelligence_wait_queue('test', frame), frame)
                first_delay = delays[0] if delays[0] is not None else 60
                self.assertEqual(checked, [100, 100 + first_delay,
                                           100 + first_delay + max(1, delays[1])])
                self.assertEqual(timer.call_count, 2)
                self.assertEqual(output.getvalue().count('野外行军队列已满'), 2)
                if help_enabled:
                    self.assertGreater(screen.call_count, 2)
                else:
                    self.assertEqual(screen.call_count, 2)

    def test_queue_countdown_uses_shortest_timer_inside_queue(self):
        for name, expected in [('gather-queue-full', 102), ('bounty-follow', 28)]:
            image = Image.open(bot.ROOT / f'artifacts/{name}.png').convert('RGB')
            self.assertEqual(bot.intelligence_queue_countdown(image), expected)
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for texts, expected in [(['00:02:10', '00：01：42'], 102),
                                (['00:00:00', '00:01:00'], 0),
                                (['无法识别'], None)]:
            lines = [(text, (0, 150 + index * 90, 100, 30))
                     for index, text in enumerate(texts)]
            lines += [('＋ 增加行军队列', (0, 500, 100, 30)),
                      ('00:00:01', (0, 700, 100, 30))]
            with patch.object(bot, 'recognize_text', return_value=lines):
                self.assertEqual(bot.intelligence_queue_countdown(frame), expected)

    def test_trigger_boundary_and_zero_skips_stamina_read(self):
        for threshold, value, starts in [(0, None, True), (100, 100, False), (100, 101, True)]:
            frame = Image.new('RGB', bot.EXPECTED_SIZE)
            with (patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'intelligence_page', return_value=True),
                  patch.object(bot, 'inspect', return_value=(False, None, 0)),
                  patch.object(bot, 'town_button', return_value=True),
                  patch.object(bot, 'intelligence_stamina', return_value=value) as stamina,
                  patch.object(bot, 'open_intelligence', return_value=frame) as enter,
                  patch.object(bot, 'intelligence_text_target', return_value=None),
                  patch.object(bot, 'intelligence_target', return_value=None),
                  patch.object(bot, 'task_tap'),
                  patch.object(bot, 'return_to_city', return_value=True) as back,
                  contextlib.redirect_stdout(io.StringIO())):
                bot.run_intelligence('test', *([None] * 4), threshold)
            self.assertEqual(stamina.call_count, int(threshold != 0))
            self.assertEqual(enter.call_count, int(starts))
            back.assert_not_called()

    def test_dispatch_and_reward_loop_stays_in_wilderness(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        with (patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'intelligence_page', return_value=True),
              patch.object(bot, 'inspect', return_value=(False, None, 0)),
              patch.object(bot, 'town_button', return_value=True),
              patch.object(bot, 'open_intelligence', return_value=frame),
              patch.object(bot, 'intelligence_completed', return_value=True),
              patch.object(bot, 'intelligence_text_target', side_effect=[None, (540, 2100)]),
              patch.object(bot, 'intelligence_back_target', return_value=None),
              patch.object(bot, 'intelligence_target', side_effect=[(353, 1009), None]),
              patch.object(bot, 'execute_intelligence', return_value=(frame, True)) as execute,
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'return_to_city', return_value=True) as back,
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_intelligence('test', *([None] * 4))
        execute.assert_called_once()
        self.assertIn(('test', 540, 2100), [c.args for c in tap.call_args_list])
        back.assert_not_called()

    def test_action_uses_march_confirmation_and_stops_at_insufficient_stamina(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        clock = Clock()
        for insufficient in (False, True):
            with (patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'recognize_text', return_value=(
                      [('体力不足', (0, 0, 10, 10))] if insufficient else [])),
                  patch.object(bot, 'intelligence_text_target', side_effect=[
                      (540, 1680), (540, 1230), (540, 1230), (825, 2220), None]),
                  patch.object(bot, 'intelligence_back_target', return_value=None),
                  patch.object(bot, 'town_button', return_value=True),
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot.time, 'sleep', clock.sleep),
                  patch.object(bot.time, 'monotonic', clock.monotonic),
                  contextlib.redirect_stdout(io.StringIO())):
                _, success = bot.execute_intelligence('test', frame)
            self.assertEqual(success, not insufficient)
            self.assertEqual([c.args[1:] for c in tap.call_args_list],
                             [(540, 1680), (100, 1900)] if insufficient else
                             [(540, 1680), (540, 1230), (825, 2220)])

    def test_cli_adds_intelligence_after_daily_tasks(self):
        with (patch('sys.argv', ['help_bot.py', '--device', 'test', '--daily-tasks', '--intelligence']),
              patch.object(bot, 'emulator_device', return_value=True),
              patch.object(bot, 'return_to_city', return_value=True),
              patch.object(bot, 'run_schedule') as schedule):
            bot.main()
        self.assertEqual(list(schedule.call_args.args[4])[-1], 'intelligence')
        with patch.object(bot, 'run_intelligence') as intelligence:
            schedule.call_args.args[4]['intelligence']()
        self.assertIs(intelligence.call_args.kwargs['timers'], schedule.call_args.kwargs['timers'])

    def test_refresh_timer_resumes_intelligence_without_delaying_daily_tasks(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for text, delay in [('下次刷新：00:00:12', 12), ('下次刷新：无法识别', 60)]:
            clock, timers, events = Clock(), {}, []

            def intelligence():
                events.append(('intelligence', clock.now))
                bot.run_intelligence('test', *([None] * 4), timers=timers)
                # Finishing this round must not restart its refresh countdown.
                clock.sleep(2)

            def daily():
                events.append(('daily', clock.now))
                bot.record_timer(timers, 'train', 5)

            with (patch.object(bot.time, 'monotonic', clock.monotonic),
                  patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'intelligence_page', return_value=True),
                  patch.object(bot, 'inspect', return_value=(False, None, 0)),
                  patch.object(bot, 'town_button', return_value=True),
                  patch.object(bot, 'open_intelligence', return_value=frame),
                  patch.object(bot, 'intelligence_text_target', return_value=None),
                  patch.object(bot, 'recognize_text', return_value=[(text, (0, 0, 10, 10))]),
                  patch.object(bot, 'intelligence_target', return_value=None),
                  patch.object(bot, 'task_tap'),
                  patch.object(bot, 'return_to_city', return_value=True),
                  patch.object(bot, 'run', side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds)),
                  contextlib.redirect_stdout(io.StringIO())):
                bot.run_schedule('test', delay + 4, 2, False,
                                 {'train': daily, 'intelligence': intelligence},
                                 *([None] * 4), help_enabled=False, timers=timers)
            self.assertEqual([when for name, when in events if name == 'intelligence'],
                             [100, 100 + delay])
            self.assertGreater(len([name for name, _ in events if name == 'daily']), 2)
            self.assertEqual(timers['intelligence'], 100 + 2 * delay)


if __name__ == '__main__':
    unittest.main()
