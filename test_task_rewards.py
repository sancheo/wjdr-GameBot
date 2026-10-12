"""Task badge, batch claim, tab navigation and monitor regression checks."""

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import help_bot as bot
import task_rewards as rewards
from test_help_bot import Clock


class TaskRewardsTest(unittest.TestCase):
    def frame(self, name):
        return Image.open(bot.ROOT / f"tests/fixtures/task-rewards-{name}.png").convert("RGB")

    def test_real_badges_tabs_and_claim_buttons(self):
        daily, growth = self.frame("daily"), self.frame("growth")
        self.assertEqual(rewards.page(bot, daily), "每日任务")
        self.assertEqual(rewards.page(bot, growth), "成长任务")
        self.assertEqual({name: count for name, _, count in rewards.tabs(bot, daily)},
                         {"成长任务": 2, "每日任务": 3})
        self.assertEqual({name: count for name, _, count in rewards.tabs(bot, self.frame("daily-one"))},
                         {"成长任务": 0, "每日任务": 1})
        self.assertIsNotNone(rewards.green_claim(bot, daily, ("一键领取",), (300, 1720, 780, 1890)))
        target = rewards.green_claim(bot, growth, ("领取",), (740, 580, 1020, 1890))
        self.assertTrue(1100 < target[1] < 1200)  # Never click the blue 前往 button above it.

    def test_entry_requires_surface_icon_red_badge_and_positive_number(self):
        frame = self.frame("entry")
        with patch.object(bot, "inspect", return_value=(True, None, 0)):
            self.assertEqual(rewards.entry(bot, frame, None, None), (65, 1995))
            blank = frame.copy()
            blank.paste((90, 130, 180), rewards.ENTRY_BADGE)
            self.assertIsNone(rewards.entry(bot, blank, None, None))
            with patch.object(bot, "recognize_text", return_value=[("0", (0, 0, 20, 20))]):
                self.assertIsNone(rewards.entry(bot, frame, None, None))
            with patch.object(bot, "recognize_text", return_value=[]):
                self.assertIsNone(rewards.entry(bot, frame, None, None))
        with patch.object(bot, "inspect", return_value=(False, None, 100)):
            self.assertIsNone(rewards.entry(bot, frame, None, None))

    def test_reward_popup_dismissed_before_returning_panel(self):
        popup, daily = self.frame("popup"), self.frame("daily")
        confirmed = Mock()
        with (patch.object(bot, "task_screen", side_effect=[popup, daily]),
              patch.object(bot, "task_tap") as tap):
            self.assertIs(rewards.wait_panel(bot, "test", on_reward=confirmed), daily)
        tap.assert_called_once_with("test", 540, 2160)
        confirmed.assert_called_once_with()

    def test_daily_batch_then_only_badged_other_tabs_and_close(self):
        frame = Image.new("RGB", bot.EXPECTED_SIZE)
        current = "成长任务"
        counts = {"成长任务": 1, "每日任务": 2, "联盟任务": 0}
        calls = []
        def tap(serial, x, y):
            nonlocal current
            calls.append((x, y))
            if (x, y) == (700, 1980):
                current = "每日任务"
            elif (x, y) == (360, 1980):
                current = "成长任务"
            elif (x, y) in ((540, 1820), (895, 1150)):
                counts[current] = 0
        def claim(runtime, image, labels, box):
            return ((540, 1820) if current == "每日任务" else (895, 1150)) if counts[current] else None
        with (patch.object(bot, "task_page", return_value=False),
              patch.object(rewards, "page", side_effect=lambda *a: current),
              patch.object(rewards, "tabs", side_effect=lambda *a: [
                  ("成长任务", (360, 1980), counts["成长任务"]),
                  ("每日任务", (700, 1980), counts["每日任务"]),
                  ("联盟任务", (900, 1980), 0)]),
              patch.object(rewards, "green_claim", side_effect=claim),
              patch.object(rewards, "wait_panel", return_value=frame),
              patch.object(bot, "difference", return_value=0),
              patch.object(bot, "task_tap", side_effect=tap),
              patch.object(bot, "task_screen", return_value=frame),
              patch.object(bot, "inspect", return_value=(True, None, 0)),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(rewards.run(bot, "test", None, None, initial_image=frame), frame)
        self.assertEqual(calls, [(700, 1980), (540, 1820), (360, 1980), (895, 1150), (1030, 460)])

    def test_idle_wait_keeps_sixty_second_reward_schedule_without_badge_polling(self):
        clock, calls = Clock(), []
        def monitor(serial, seconds, *args, **kwargs):
            clock.sleep(seconds)
            self.assertTrue(kwargs["allow_wilderness"])
            self.assertFalse(kwargs["help_enabled"])
            self.assertNotIn("task_rewards_pending", kwargs)
            return False
        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot.task_rewards, "entry") as entry,
              patch.object(bot, "run", side_effect=monitor),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule("test", 131, 2, False,
                             {"task_rewards": lambda: calls.append(clock.now)},
                             *([None] * 4), help_enabled=False)
        self.assertEqual(calls, [100, 160, 220])
        entry.assert_not_called()

    def test_tab_ocr_waits_for_claim_animation(self):
        frame = Image.new("RGB", bot.EXPECTED_SIZE)
        available = [("每日任务", (715, 1978), 0), ("成长任务", (366, 1978), 1)]
        with (patch.object(rewards, "tabs", side_effect=[[], available]),
              patch.object(rewards, "wait_panel", return_value=frame) as wait):
            self.assertEqual(rewards.stable_tabs(bot, "test", frame), (frame, available))
        wait.assert_called_once()

    def test_growth_chain_confirmed_by_popup_when_button_and_count_stay_same(self):
        frame = Image.new("RGB", bot.EXPECTED_SIZE)
        claims = []
        current = "成长任务"
        def tap(serial, x, y):
            nonlocal current
            if (x, y) == (715, 1978):
                current = "每日任务"
            elif (x, y) == (366, 1978):
                current = "成长任务"
            elif (x, y) == (895, 790):
                claims.append((x, y))
        def wait(runtime, serial, on_reward=None):
            if on_reward:
                on_reward()
            return frame
        with (patch.object(bot, "task_page", return_value=False),
              patch.object(rewards, "page", side_effect=lambda *a: current),
              patch.object(rewards, "tabs", side_effect=lambda *a: [
                  ("每日任务", (715, 1978), 0), ("成长任务", (366, 1978), int(len(claims) < 2))]),
              patch.object(rewards, "green_claim", return_value=(895, 790)),
              patch.object(rewards, "wait_panel", side_effect=wait),
              patch.object(bot, "difference", return_value=0),
              patch.object(bot, "task_tap", side_effect=tap),
              patch.object(bot, "task_screen", return_value=frame),
              patch.object(bot, "inspect", return_value=(True, None, 0)),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(rewards.run(bot, "test", None, None, initial_image=frame), frame)
        self.assertEqual(len(claims), 2)

    def test_actual_idle_monitor_does_not_capture_or_inspect_reward_badges(self):
        gray, frame = Image.new("L", bot.EXPECTED_SIZE), Image.new("RGB", bot.EXPECTED_SIZE)
        clock = Clock()
        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot.time, "sleep", clock.sleep),
              patch.object(bot, "screenshot", return_value=gray),
              patch.object(bot, "color_screenshot", return_value=frame) as capture,
              patch.object(bot.task_rewards, "entry") as entry,
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "city_relocation_target", return_value=None),
              patch.object(bot, "network_reconnect_target", return_value=None),
              patch.object(bot, "inspect", return_value=(True, (30, 0, 0), 0)),
              patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            bot.run("test", 10, 2, False, 10, *([None] * 4), help_enabled=False)
        capture.assert_not_called()
        entry.assert_not_called()
        tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
