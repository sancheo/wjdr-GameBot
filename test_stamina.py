"""Stamina navigation regressions from emulator-5554 and the failure log."""
import contextlib
import io
import unittest
import tempfile
from pathlib import Path
import stamina_state
from unittest.mock import patch

from PIL import Image
from test_help_bot import Clock

import help_bot as bot


def frame(name):
    return Image.open(bot.ROOT / f'artifacts/{name}.png').convert('RGB')


class StaminaNavigationTest(unittest.TestCase):
    def setUp(self):
        state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(state_dir.cleanup)
        state_patch = patch.object(stamina_state, 'state_path',
                                   side_effect=lambda root, serial: Path(state_dir.name) / (serial + '.json'))
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_restart_checks_actual_city_then_waits_only_for_future_saved_time(self):
        image = frame('stamina-claimed')
        for deadline, expected in ((1500, 600), (999, 160), (1000, 160)):
            with self.subTest(deadline=deadline):
                bot.save_refresh(bot.ROOT, 'test', deadline - 300, 300)
                timers = {'warehouse': 5000}
                with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
                      patch.object(bot, 'task_page', return_value=False),
                      patch.object(bot, 'warehouse_position', return_value=(0, 0)),
                      patch.object(bot, 'stamina_target', return_value=None) as detect,
                      patch.object(bot, 'task_screen', return_value=image),
                      patch.object(bot, 'task_tap') as tap,
                      patch.object(bot.time, 'time', return_value=1000),
                      patch.object(bot.time, 'monotonic', return_value=100),
                      contextlib.redirect_stdout(io.StringIO())):
                    bot.run_stamina('test', None, None, None, timers, image)
                self.assertEqual(detect.call_count, 5)
                tap.assert_not_called()
                self.assertEqual(timers, {'warehouse': 5000, 'stamina': expected})

    def test_early_refresh_replaces_saved_time_before_claim_even_without_runtime_timers(self):
        ready, modal, claimed = map(frame, ('stamina-ready', 'flow-warehouse-can-click', 'stamina-claimed'))
        for timers in ({}, None):
            with self.subTest(timers=timers):
                bot.save_refresh(bot.ROOT, 'test', 1000, 500)
                def tap(serial, x, y):
                    if (x, y) == (530, 1700):
                        self.assertEqual(bot.load_refresh(bot.ROOT, serial), 1300)
                with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
                      patch.object(bot, 'task_page', return_value=False),
                      patch.object(bot, 'task_screen', side_effect=[modal, claimed]),
                      patch.object(bot, 'stamina_countdown', return_value=300),
                      patch.object(bot, 'task_tap', side_effect=tap),
                      patch.object(bot.time, 'time', return_value=1000),
                      patch.object(bot.time, 'monotonic', return_value=100),
                      contextlib.redirect_stdout(io.StringIO())):
                    self.assertIs(bot.run_stamina('test', None, None, None, timers, ready), claimed)
                self.assertEqual(bot.load_refresh(bot.ROOT, 'test'), 1300)
                if timers is not None:
                    self.assertEqual(timers['stamina'], 400)

    def test_invalid_saved_time_falls_back_and_does_not_skip_detection(self):
        image = frame('stamina-claimed')
        stamina_state.state_path(bot.ROOT, 'test').write_text('{broken', encoding='utf-8')
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'warehouse_position', return_value=(0, 0)),
              patch.object(bot, 'stamina_target', return_value=None) as detect,
              patch.object(bot, 'task_screen', return_value=image),
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_stamina('test', None, None, None, timers := {}, image)
        self.assertEqual(detect.call_count, 5)
        self.assertEqual(timers['stamina'], 160)

    def test_unreadable_countdown_never_claims_or_overwrites_saved_time(self):
        ready, modal = map(frame, ('stamina-ready', 'flow-warehouse-can-click'))
        bot.save_refresh(bot.ROOT, 'test', 1000, 500)
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'task_screen', return_value=modal),
              patch.object(bot, 'stamina_countdown', return_value=None),
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO()),
              self.assertRaisesRegex(RuntimeError, '剩余时间未识别，未领取')):
            bot.run_stamina('test', None, None, None, {}, ready)
        self.assertEqual(bot.load_refresh(bot.ROOT, 'test'), 1500)
        self.assertNotIn(('test', 530, 1700), [call.args for call in tap.call_args_list])

    def test_stored_times_are_separate_for_each_emulator(self):
        bot.save_refresh(bot.ROOT, 'emulator-5556', 1000, 300)
        bot.save_refresh(bot.ROOT, 'emulator-5554', 1000, 600)
        self.assertEqual(bot.load_refresh(bot.ROOT, 'emulator-5556'), 1300)
        self.assertEqual(bot.load_refresh(bot.ROOT, 'emulator-5554'), 1600)

    def test_scheduler_checks_at_saved_deadline_and_claims_with_a_new_deadline(self):
        ready, modal, claimed = map(frame, ('stamina-ready', 'flow-warehouse-can-click', 'stamina-claimed'))
        clock, events, timers = Clock(), [], {}
        bot.save_refresh(bot.ROOT, 'test', 1000, 30)
        claim_screens = iter((modal, claimed))
        def task():
            events.append(clock.now)
            return bot.run_stamina('test', None, None, None, timers,
                                   claimed if len(events) == 1 else ready)
        def screen(*args, **kwargs):
            return claimed if len(events) == 1 else next(claim_screens)
        with (patch.object(bot.time, 'time', side_effect=lambda: clock.now + 900),
              patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot, 'maybe_cleanup_diagnostic_screenshots'),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'warehouse_position', return_value=(0, 0)),
              patch.object(bot, 'stamina_target', side_effect=lambda im, *args: (500, 1000) if im is ready else None),
              patch.object(bot, 'task_screen', side_effect=screen),
              patch.object(bot, 'stamina_countdown', return_value=300),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'pet_page', return_value=False),
              patch.object(bot, 'run', side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds)),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 35, 2, False, {'stamina': task},
                             *([None] * 4), help_enabled=False, timers=timers)
        self.assertEqual(events, [100, 130])
        self.assertEqual(bot.load_refresh(bot.ROOT, 'test'), 1330)
        self.assertEqual(timers['stamina'], 430)
        self.assertEqual(tap.call_args_list[-1].args, ('test', 530, 1700))

    def test_research_arrow_from_live_drawer(self):
        self.assertEqual(bot.research_drawer_target(frame('stamina-research-drawer')), (608, 1520))

    def test_research_arrow_tracks_row_position_and_rejects_clipped_row(self):
        image = Image.new('RGB', bot.EXPECTED_SIZE)
        for y, expected in ((450, (608, 1284)), (750, None)):
            with self.subTest(y=y), patch.object(bot, 'recognize_text', return_value=[
                    ('科技研究', (60, y, 131, 34))]):
                self.assertEqual(bot.research_drawer_target(image), expected)

    def test_original_empty_edge_is_not_warehouse(self):
        self.assertIsNone(bot.warehouse_position(frame('stamina-empty-edge')))

    def test_lighting_changed_facade_is_detected_but_edge_is_not_safe(self):
        offset = bot.warehouse_position(frame('stamina-warehouse-lighting'))
        self.assertIsNotNone(offset)
        self.assertFalse(bot.stamina_warehouse_visible(offset))
        self.assertTrue(bot.stamina_warehouse_visible(bot.warehouse_position(frame('stamina-ready'))))

    def test_same_camera_position_survives_day_night_lighting(self):
        offsets = [bot.warehouse_position(frame(f'stamina-facade-{lighting}'))
                   for lighting in ('day', 'night')]
        self.assertEqual(offsets, [(138, 290), (138, 290)])
        self.assertTrue(all(bot.stamina_warehouse_visible(offset) for offset in offsets))

    def test_visible_facade_previously_lost_by_raw_coarse_match(self):
        offset = bot.warehouse_position(frame('stamina-facade-missed'))
        self.assertEqual(offset, (-158, 87))
        self.assertTrue(bot.stamina_warehouse_visible(offset))

    def test_stamina_suspends_help_in_drawer_helpers_and_restores_on_error(self):
        previous = (object(), object(), object())
        def fail(*_args):
            self.assertIsNone(bot.TASK_HELP)
            raise RuntimeError('navigation interrupted')
        with (patch.object(bot, 'TASK_HELP', previous),
              patch.object(bot, '_run_stamina', side_effect=fail),
              self.assertRaisesRegex(RuntimeError, 'navigation interrupted')):
            try:
                bot.run_stamina('test', None, None, None)
            finally:
                self.assertIs(bot.TASK_HELP, previous)

    def test_transient_detection_loss_waits_and_rechecks_before_deferring(self):
        image = frame('stamina-facade-day')
        timers = {'warehouse': 5000}
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'warehouse_position', side_effect=[(138, 290), None] + [(138, 290)] * 4),
              patch.object(bot, 'stamina_map_screen', return_value=image) as wait,
              patch.object(bot, 'task_screen', return_value=image),
              patch.object(bot, 'stamina_target', return_value=None),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'adb') as adb,
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_stamina('test', None, None, None, timers, image), image)
        wait.assert_called_once()
        tap.assert_not_called()
        adb.assert_not_called()
        self.assertEqual(timers, {'warehouse': 5000, 'stamina': 160})

    def test_detection_drift_repositions_before_scanning_again(self):
        image = frame('stamina-facade-day')
        offsets = [(138, 290), (486, 216), (486, 216)] + [(0, -200)] * 4
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'warehouse_position', side_effect=offsets),
              patch.object(bot, 'stamina_map_screen', return_value=image),
              patch.object(bot, 'task_screen', return_value=image),
              patch.object(bot, 'stamina_target', return_value=None) as detect,
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb,
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_stamina('test', None, None, None, initial_image=image)
        adb.assert_called_once()
        self.assertTrue(all(bot.stamina_warehouse_visible(call.args[1]) for call in detect.call_args_list))

    def test_persistent_detection_loss_does_not_report_no_can_or_record_timer(self):
        image = frame('stamina-facade-day')
        timers = {}
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'warehouse_position', side_effect=[(138, 290), None, None]),
              patch.object(bot, 'stamina_map_screen', return_value=image),
              patch.object(bot, 'task_screen', return_value=image),
              patch.object(bot, 'stamina_target', return_value=None) as detect,
              patch.object(bot, 'task_tap') as tap,
              self.assertRaisesRegex(RuntimeError, '检测期间仓库视角发生变化')):
            bot.run_stamina('test', None, None, None, timers, image)
        self.assertEqual(detect.call_count, 1)
        tap.assert_not_called()
        self.assertEqual(timers, {})

    def test_map_waits_for_motion_to_finish(self):
        before = Image.new('RGB', bot.EXPECTED_SIZE, 'black')
        moving = Image.new('RGB', bot.EXPECTED_SIZE, 'gray')
        settled = Image.new('RGB', bot.EXPECTED_SIZE, 'white')
        with (patch.object(bot, 'task_screen', side_effect=[moving, settled, settled]) as screen,
              patch.object(bot, 'inspect', return_value=(True, None, 0))):
            self.assertIs(bot.stamina_map_screen('test', None, None, before), settled)
        self.assertEqual(screen.call_count, 3)

    def test_unstable_map_or_lost_city_stops_detection(self):
        black = Image.new('RGB', bot.EXPECTED_SIZE, 'black')
        white = Image.new('RGB', bot.EXPECTED_SIZE, 'white')
        with (patch.object(bot, 'task_screen', side_effect=[white, black, white, black, white]),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              self.assertRaisesRegex(RuntimeError, '地图移动未稳定')):
            bot.stamina_map_screen('test', None, None, black)
        with (patch.object(bot, 'task_screen', return_value=black),
              patch.object(bot, 'inspect', return_value=(False, None, 99)),
              self.assertRaisesRegex(RuntimeError, '未确认主城')):
            bot.stamina_map_screen('test', None, None, black)

    def test_missing_research_arrow_never_starts_map_drags_or_can_detection(self):
        image = frame('stamina-empty-edge')
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'reset_drawer', return_value=image),
              patch.object(bot, 'research_drawer_target', return_value=None),
              patch.object(bot, 'swipe_drawer', return_value=image),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'stamina_target') as detect,
              self.assertRaisesRegex(RuntimeError, '未能定位科技研究右侧箭头')):
            bot.run_stamina('test', None, None, None, initial_image=image)
        tap.assert_not_called()
        adb.assert_not_called()
        detect.assert_not_called()

    def test_partial_warehouse_is_repositioned_before_claim_detection(self):
        partial = frame('stamina-warehouse-lighting')
        ready, modal, claimed = map(frame, ('stamina-ready', 'flow-warehouse-can-click', 'stamina-claimed'))
        safe_offset = bot.warehouse_position(ready)
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'warehouse_position', side_effect=[(486, 216), safe_offset, safe_offset]),
              patch.object(bot, 'stamina_map_screen', return_value=ready),
              patch.object(bot, 'task_screen', side_effect=[modal, claimed]),
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'stamina_target', wraps=bot.stamina_target) as detect,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_stamina('test', None, None, None, initial_image=partial), claimed)
        adb.assert_called_once_with('test', 'shell', 'input', 'swipe', '500', '950', '200', '700', '1500')
        self.assertIs(detect.call_args_list[0].args[0], ready)


class StaminaAnimatedMapTest(unittest.TestCase):
    def sample(self, number):
        return Image.open(bot.ROOT / f'tests/fixtures/stamina-motion-1010-{number}.png').convert('L')

    def screen(self, number):
        image = Image.new('RGB', bot.EXPECTED_SIZE)
        image.paste(self.sample(number).resize((820, 1450)), (80, 350))
        return image

    def test_observed_research_guide_animation_does_not_disable_can_task(self):
        # These consecutive live frames share the same camera position but
        # snow, healing effects and the research hint exceed the old limit.
        samples = [self.sample(i) for i in (1, 2, 3)]
        self.assertTrue(all(bot.difference(a, b) > 3 for a, b in zip(samples, samples[1:])))
        frames = [self.screen(i) for i in (1, 2, 3, 2, 1, 2)]
        with (patch.object(bot, 'task_screen', side_effect=frames[1:]) as screen,
              patch.object(bot, 'inspect', return_value=(True, None, 0))):
            self.assertIs(bot.stamina_map_screen('test', None, None, frames[0]), frames[2])
        self.assertEqual(screen.call_count, 2)

    def test_observed_camera_drag_never_counts_as_animation(self):
        before, after = self.sample(5), self.sample(6)
        self.assertFalse(bot.stamina_map_stationary(before, after))
        self.assertFalse(bot.stamina_map_stationary(after, before))
        frames = [self.screen(i) for i in (5, 6, 5, 6, 5, 6)]
        with (patch.object(bot, 'task_screen', side_effect=frames[1:]),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              self.assertRaisesRegex(RuntimeError, '地图移动未稳定')):
            bot.stamina_map_screen('test', None, None, frames[0])

    def test_one_stationary_pair_does_not_end_wait_before_motion(self):
        frames = [self.screen(i) for i in (1, 2, 5, 6, 5, 6)]
        with (patch.object(bot, 'task_screen', side_effect=frames[1:]),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              self.assertRaisesRegex(RuntimeError, '地图移动未稳定')):
            bot.stamina_map_screen('test', None, None, frames[0])

    def test_untextured_or_unrelated_frames_cannot_establish_camera_position(self):
        self.assertFalse(bot.stamina_map_stationary(Image.new('L', (164, 290)),
                                                  Image.new('L', (164, 290), 255)))
        self.assertFalse(bot.stamina_map_stationary(self.sample(1), self.sample(5)))

    def test_lost_city_is_rejected_even_when_camera_position_matches(self):
        with (patch.object(bot, 'task_screen', return_value=self.screen(2)),
              patch.object(bot, 'inspect', return_value=(False, None, 99)),
              self.assertRaisesRegex(RuntimeError, '未确认主城')):
            bot.stamina_map_screen('test', None, None, self.screen(1))


if __name__ == '__main__':
    unittest.main()
