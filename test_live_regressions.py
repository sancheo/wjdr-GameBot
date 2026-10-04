"""Regression fixtures captured while verifying the Windows emulator."""

import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot


class LiveRegressionTest(unittest.TestCase):
    def test_pet_cooldown_falls_back_to_its_own_icon_after_ocr_noise(self):
        frame = Image.open(bot.ROOT / 'artifacts/pet-cooldown-ocr-windows.png').convert('RGB')
        self.assertEqual(bot.pet_skill_state(frame, '心灵伴侣'), ('cooldown', 54398))

    def test_tree_count_has_ocr_fallback_for_changed_label_rendering(self):
        frame = Image.open(bot.ROOT / 'artifacts/rewards-drawer-windows.png').convert('RGB')
        with patch.object(bot, 'find_green', return_value=(0, 0, 0)):
            amount, y = bot.tree_drawer_amount(frame, None)
        self.assertEqual(amount, 5040)
        self.assertTrue(1190 <= y <= 1210)

    def test_drawer_waits_for_open_animation_without_toggling_again(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        with (patch.object(bot, 'task_page', side_effect=[False, False, True]),
              patch.object(bot, 'task_screen', return_value=frame) as screen,
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'TASK_DRAWER_SCROLL', 0)):
            self.assertIs(bot.task_drawer('test', frame, {'drawer-open': None}), frame)
        self.assertEqual(screen.call_count, 2)
        tap.assert_called_once_with('test', 20, 1100)

    def test_warehouse_reward_timer_uses_next_parcel_countdown(self):
        for name, seconds in [('warehouse-reward-windows', 59), ('flow-warehouse-reward', 1199)]:
            frame = Image.open(bot.ROOT / f'artifacts/{name}.png').convert('RGB')
            self.assertEqual(bot.warehouse_reward_timer(frame), (True, seconds))
        frame = Image.open(bot.ROOT / 'artifacts/pet-active-transport.png').convert('RGB')
        self.assertEqual(bot.warehouse_reward_timer(frame), (False, None))

    def test_transport_active_buff_is_not_unknown_or_ready(self):
        frame = Image.open(bot.ROOT / 'artifacts/pet-active-transport.png').convert('RGB')
        self.assertEqual(bot.pet_skill_state(frame, '重物搬运'), ('active', None))

    def test_pet_reward_requires_title_and_exit_hint(self):
        frame = Image.open(bot.ROOT / 'artifacts/pet-reward-overlay.png').convert('RGB')
        self.assertTrue(bot.pet_reward_page(frame))
        frame.paste('black', (280, 2130, 800, 2250))
        self.assertFalse(bot.pet_reward_page(frame))

    def test_dimmed_drawer_reward_titles_are_located(self):
        frame = Image.open(bot.ROOT / 'artifacts/rewards-drawer-windows.png').convert('RGB')
        for asset, label, shift in [('warehouse-title', '仓库补给', 30),
                                    ('dawn-row-title', '晨曦回礼', 50)]:
            with self.subTest(label=label):
                title = Image.open(bot.ROOT / f'assets/emulator-{asset}.png')
                score, _, y = bot.reward_row(frame, title, label)
                self.assertGreaterEqual(score, .85)
                self.assertEqual(bot.training_status(frame, y + shift), 'completed')

    def test_full_crystal_count_is_read_without_losing_leading_digit(self):
        frame = Image.open(bot.ROOT / 'artifacts/rewards-drawer-windows.png').convert('RGB')
        self.assertEqual(bot.tree_amount(frame, 204, 1200), 5040)
        self.assertEqual(bot.parse_tree_amount('可收集5，040/5，040'), 5040)

    def test_stamina_claims_from_current_warehouse_view_without_navigation(self):
        frame = lambda name: Image.open(bot.ROOT / f'artifacts/{name}.png').convert('RGB')
        drawer, ready, modal, claimed = map(frame, ('warehouse-after-drawer', 'stamina-ready',
                                                   'flow-warehouse-can-click', 'stamina-claimed'))
        timers = {'warehouse': 5000}
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=True),
              patch.object(bot, 'task_drawer', side_effect=AssertionError('不得打开抽屉')),
              patch.object(bot, 'reward_row', side_effect=AssertionError('不得检查仓库补给行')),
              patch.object(bot, 'reset_drawer', side_effect=AssertionError('不得重新定位兵营')),
              patch.object(bot, 'task_screen', side_effect=[ready, modal, claimed]),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertIs(bot.run_stamina('test', None, None, None, timers,
                                         initial_image=drawer), claimed)
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(695, 1100), bot.stamina_target(ready), (530, 1700)])
        self.assertEqual(timers, {'warehouse': 5000, 'stamina': 11127})
        self.assertIn('已领取体力罐头', output.getvalue())

    def test_stamina_at_warehouse_without_icon_defers_without_navigation(self):
        frame = Image.open(bot.ROOT / 'artifacts/stamina-claimed.png').convert('RGB')
        timers = {'warehouse': 5000}
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'task_drawer', side_effect=AssertionError('不得打开抽屉')),
              patch.object(bot, 'reward_row', side_effect=AssertionError('不得检查仓库补给行')),
              patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertIs(bot.run_stamina('test', None, None, None, timers,
                                         initial_image=frame), frame)
        tap.assert_not_called()
        adb.assert_not_called()
        self.assertEqual(timers, {'warehouse': 5000, 'stamina': 160})
        self.assertIn('当前主城画面未发现体力罐头图标', output.getvalue())

    def test_stamina_locates_shield_barracks_then_warehouse_before_detecting_can(self):
        frame = lambda name: Image.open(bot.ROOT / f'artifacts/{name}.png').convert('RGB')
        away, drawer, ready, modal, claimed = map(frame, ('after-training-back', 'training-drawer',
                                                        'stamina-ready', 'flow-warehouse-can-click',
                                                        'stamina-claimed'))
        timers = {'warehouse': 5000}
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'reset_drawer', return_value=drawer) as reset,
              patch.object(bot, 'task_screen', side_effect=[away, away, ready, modal, claimed]),
              patch.object(bot, 'stamina_target', wraps=bot.stamina_target) as detect,
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb,
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_stamina('test', None, None, None, timers,
                                         initial_image=away), claimed)
        self.assertEqual(adb.call_count, 2)
        adb.assert_called_with('test', 'shell', 'input', 'swipe',
                               '200', '650', '800', '1000', '800')
        reset.assert_called_once_with('test', away, {'drawer-open': None})
        self.assertIs(detect.call_args_list[0].args[0], ready)
        self.assertTrue(all(action.args[0] is not away for action in detect.call_args_list))
        self.assertEqual([action.args[1:] for action in tap.call_args_list],
                         [(608, bot.TRAIN_ROWS[0][1]),
                          bot.stamina_target(ready, bot.warehouse_position(ready)), (530, 1700)])
        self.assertEqual(timers, {'warehouse': 5000, 'stamina': 11127})

    def test_stamina_does_not_detect_can_when_warehouse_navigation_fails(self):
        frame = Image.open(bot.ROOT / 'artifacts/after-training-back.png').convert('RGB')
        for drawer_stuck in (True, False):
            with (self.subTest(drawer_stuck=drawer_stuck),
                  patch.object(bot, 'inspect', return_value=(True, None, 0)),
                  patch.object(bot, 'task_page', side_effect=[False] + [drawer_stuck] * 5),
                  patch.object(bot, 'reset_drawer', return_value=frame),
                  patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'game_foreground', return_value=True),
                  patch.object(bot, 'adb') as adb,
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot, 'stamina_target') as detect,
                  self.assertRaisesRegex(RuntimeError, '未能通过盾兵训练|未能定位仓库')):
                bot.run_stamina('test', None, None, None, initial_image=frame)
            detect.assert_not_called()
            tap.assert_called_once_with('test', 608, bot.TRAIN_ROWS[0][1])
            self.assertEqual(adb.call_count, 0 if drawer_stuck else 3)


if __name__ == '__main__':
    unittest.main()
