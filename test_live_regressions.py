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

    def test_warehouse_navigation_uses_only_confirmed_drawer_row(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for found in (True, False):
            with (self.subTest(found=found),
                  patch.object(bot, 'inspect', return_value=(True, None, 0)),
                  patch.object(bot, 'task_page', return_value=False),
                  patch.object(bot, 'task_drawer', return_value=frame),
                  patch.object(bot, 'swipe_drawer', return_value=frame),
                  patch.object(bot, 'TASK_DRAWER_SCROLL', bot.DRAWER_SWIPES),
                  patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'game_foreground', return_value=True),
                  patch.object(bot, 'warehouse_position', side_effect=[None, None, (0, 0)]),
                  patch.object(bot, 'reward_row', return_value=(1 if found else 0, 270, 820)),
                  patch.object(bot, 'stamina_target', return_value=None),
                  patch.object(bot, 'warehouse_countdown', return_value=600),
                  patch.object(bot, 'adb'), patch.object(bot, 'task_tap') as tap,
                  contextlib.redirect_stdout(io.StringIO())):
                if found:
                    bot.run_stamina('test', None, None, None, {}, initial_image=frame)
                    self.assertIn(('test', 608, 850), [call.args for call in tap.call_args_list])
                else:
                    with self.assertRaisesRegex(RuntimeError, '未找到仓库补给任务行'):
                        bot.run_stamina('test', None, None, None, {}, initial_image=frame)
                    tap.assert_not_called()
                self.assertNotIn(('test', 530, 1700), [call.args for call in tap.call_args_list])


if __name__ == '__main__':
    unittest.main()
