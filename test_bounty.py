"""Master bounty runs last and a confirmed defeat disables only bounty for the run."""
import contextlib
import io
import unittest
from unittest.mock import patch
from PIL import Image
import help_bot as bot
from test_help_bot import Clock


class BountyTest(unittest.TestCase):
    def test_captured_icon_and_queue_coordinates(self):
        start = Image.open(bot.ROOT / 'artifacts/bounty-live-start.png').convert('RGB')
        won = Image.open(bot.ROOT / 'artifacts/bounty-map-after-battle.png').convert('RGB')
        target = bot.bounty_target(start)
        self.assertEqual(target, (639, 1436))
        self.assertFalse(bot.intelligence_completed(start, target))
        self.assertTrue(bot.intelligence_completed(won, target))
        refreshed = Image.open(bot.ROOT / 'artifacts/bounty-refresh-map.png').convert('RGB')
        self.assertIsNotNone(bot.intelligence_target(refreshed))
        # Overlapping ordinary icons cover the bounty; never select an ice texture instead.
        self.assertIsNone(bot.bounty_target(refreshed))
        final = Image.open(bot.ROOT / 'artifacts/bounty-final-map.png').convert('RGB')
        ordinary = bot.intelligence_target(final)
        self.assertIsNotNone(ordinary)
        self.assertLess(ordinary[0], 210)  # Gold wolf close to the left edge.
        self.assertIsNotNone(bot.intelligence_target(final, [ordinary]))
        self.assertIsNone(bot.intelligence_target(start))
        self.assertIsNotNone(bot.bounty_target(final))
        defeated = Image.open(bot.ROOT / 'artifacts/bounty-defeat-map.png').convert('RGB')
        failed_target = bot.bounty_target(defeated)
        self.assertIsNotNone(failed_target)
        self.assertFalse(bot.intelligence_completed(defeated, failed_target))
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        with patch.object(bot, 'recognize_text', return_value=[
                ('（619,643）', (80, 118, 100, 30)), ('1/4', (300, 40, 50, 30))]):
            self.assertEqual(bot.bounty_queue_entries(frame), {(619, 643): 528})

    def test_win_defeat_and_unknown_result(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        target = (639, 1436)
        for outcome in ('win', 'defeat', 'unknown'):
            clock, state = Clock(), {}

            def dispatch(_serial, image, bounty_trip):
                bounty_trip.update(queue_before={}, travel=2)
                return image, True

            def screen(_serial):
                clock.sleep(3)
                return frame

            with (patch.object(bot, 'task_screen', side_effect=screen),
                  patch.object(bot, 'execute_intelligence', side_effect=dispatch) as execute,
                  patch.object(bot, 'bounty_name', return_value='大师悬赏：06号'),
                  patch.object(bot, 'bounty_queue_entries', return_value={(619, 643): 528}),
                  patch.object(bot, 'town_button', return_value=True),
                  patch.object(bot, 'recognize_text', return_value=[('返回中', (0, 0, 60, 20))]),
                  patch.object(bot, 'open_intelligence', return_value=frame),
                  patch.object(bot, 'intelligence_completed', return_value=outcome == 'win'),
                  patch.object(bot, 'bounty_target', return_value=None if outcome == 'unknown' else target),
                  patch.object(bot, 'task_tap'),
                  patch.object(bot.time, 'monotonic', clock.monotonic),
                  patch.object(bot.time, 'sleep', clock.sleep),
                  contextlib.redirect_stdout(io.StringIO()) as output):
                if outcome == 'unknown':
                    with self.assertRaisesRegex(RuntimeError, '未确认原大师悬赏'):
                        bot.execute_bounty('test', frame, target, state)
                else:
                    self.assertTrue(bot.execute_bounty('test', frame, target, state)[1])
            execute.assert_called_once()
            self.assertEqual(state['disabled'], outcome != 'win')
            self.assertEqual('大师悬赏怪打不过' in output.getvalue(), outcome == 'defeat')
            if outcome == 'defeat':
                self.assertIn('⚠️', output.getvalue())
                self.assertIn('本次程序运行期间不会再挑战', output.getvalue())

    def test_tracking_timeout_keeps_latch_without_claiming_defeat(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        clock, state = Clock(), {}

        def dispatch(_serial, image, bounty_trip):
            bounty_trip.update(queue_before={}, travel=2)
            return image, True

        with (patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'execute_intelligence', side_effect=dispatch) as execute,
              patch.object(bot, 'bounty_name', return_value='大师悬赏：06号'),
              patch.object(bot, 'bounty_queue_entries', return_value={(619, 643): 528}),
              patch.object(bot, 'town_button', return_value=True),
              patch.object(bot, 'recognize_text', return_value=[('(619,643)', (0, 0, 60, 20))]),
              patch.object(bot, 'task_tap'),
              patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep),
              contextlib.redirect_stdout(io.StringIO()) as output):
            with self.assertRaisesRegex(RuntimeError, '跟踪超时'):
                bot.execute_bounty('test', frame, (639, 1436), state)
        execute.assert_called_once()
        self.assertTrue(state['disabled'])
        self.assertNotIn('怪打不过', output.getvalue())

    def test_bounty_runs_after_ordinary_and_stays_disabled_on_later_rounds(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        state, events = {}, []

        def ordinary(*args, **kwargs):
            events.append('ordinary')
            return frame, True

        def bounty(_serial, image, _target, state):
            events.append('bounty')
            state['disabled'] = True
            return image, True

        with (patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'intelligence_page', return_value=True),
              patch.object(bot, 'inspect', return_value=(False, None, 0)),
              patch.object(bot, 'town_button', return_value=True),
              patch.object(bot, 'open_intelligence', return_value=frame),
              patch.object(bot, 'intelligence_completed', side_effect=lambda _, t: t != (639, 1436)),
              patch.object(bot, 'intelligence_text_target', return_value=None),
              patch.object(bot, 'intelligence_target', side_effect=[(353, 1009), None, None, None]),
              patch.object(bot, 'execute_intelligence', side_effect=ordinary),
              patch.object(bot, 'bounty_target', return_value=(639, 1436)),
              patch.object(bot, 'execute_bounty', side_effect=bounty),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'return_to_city', return_value=True),
              contextlib.redirect_stdout(io.StringIO())):
            for _ in range(2):
                bot.run_intelligence('test', *([None] * 4), bounty=True, bounty_state=state)
        self.assertEqual(events, ['ordinary', 'bounty'])

    def test_cli_keeps_bounty_state_outside_refresh_timers(self):
        with (patch('sys.argv', ['help_bot.py', '--device', 'test', '--intelligence', '--bounty']),
              patch.object(bot, 'emulator_device', return_value=True),
              patch.object(bot, 'return_to_city', return_value=True),
              patch.object(bot, 'run_schedule') as schedule):
            bot.main()
        task = schedule.call_args.args[4]['intelligence']
        with patch.object(bot, 'run_intelligence') as intelligence:
            task()
            state = intelligence.call_args.kwargs['bounty_state']
            state['disabled'] = True
            schedule.call_args.kwargs['timers'].clear()
            task()
        self.assertTrue(intelligence.call_args.kwargs['bounty'])
        self.assertIs(intelligence.call_args.kwargs['bounty_state'], state)
        self.assertTrue(state['disabled'])


if __name__ == '__main__':
    unittest.main()
