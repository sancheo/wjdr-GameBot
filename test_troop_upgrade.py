"""Exercise promotion and ordinary-training fallback with emulator samples."""

import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot


def frame(name):
    with Image.open(bot.ROOT / "artifacts" / f"upgrade-{name}.png") as image:
        return image.convert("RGB")


class TroopUpgradeTest(unittest.TestCase):
    def test_real_markers_and_locked_portraits(self):
        with Image.open(bot.ROOT / "assets/emulator-training-upgrade-arrow.png") as arrow:
            self.assertEqual(bot.training_upgrade_target(frame("shield-left"), arrow), (945, 1480))
            self.assertEqual(bot.training_upgrade_target(frame("shield-ready"), arrow), (516, 1480))
            self.assertIsNone(bot.training_upgrade_target(frame("spear-left"), arrow))
            self.assertIsNone(bot.training_upgrade_target(frame("spear-end"), arrow))
        self.assertEqual(bot.highest_training_target(frame("spear-end")), (713, 1480))
        self.assertTrue(bot.training_upgrade_dialog(frame("shield-dialog")))
        self.assertFalse(bot.training_upgrade_dialog(frame("shield-selected")))
        selected = frame("shield-selected")
        for unit in bot.UNIT_TYPES:
            title = Image.open(bot.ROOT / f"assets/emulator-training-{unit}-title.png")
            self.assertEqual(bot.training_page(selected, title), unit == "shield")

    def test_scans_from_lowest_tier_then_falls_back_at_highest_unlocked(self):
        left, right, end = (frame("spear-" + name) for name in ("left", "right", "end"))
        title = Image.open(bot.ROOT / "assets/emulator-training-spear-title.png")
        with (patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb") as swipe,
              patch.object(bot, "task_screen", side_effect=[left, left, right, end, end, end]),
              patch.object(bot, "task_tap") as tap):
            image, target = bot.scan_training_upgrade("test", frame("spear-ready"), title)
        self.assertIs(image, end)
        self.assertIsNone(target)
        self.assertEqual([call.args[4:7] for call in swipe.call_args_list],
                         [("180", "1480", "930")] * 2 + [("900", "1480", "540")] * 3)
        tap.assert_called_once_with("test", 713, 1480)

    def test_marker_in_later_window_and_no_click_on_unconfirmed_page(self):
        left, right = frame("spear-left"), frame("shield-ready")
        title = Image.open(bot.ROOT / "assets/emulator-training-shield-title.png")
        with (patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb"), patch.object(bot, "training_page", return_value=True),
              patch.object(bot, "task_screen", side_effect=[left, left, right]),
              patch.object(bot, "task_tap") as tap):
            _, target = bot.scan_training_upgrade("test", right, title)
        self.assertEqual(target, (516, 1480))
        tap.assert_not_called()
        with (patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb"), patch.object(bot, "task_screen", return_value=frame("city-sample")),
              patch.object(bot, "task_tap") as tap):
            with self.assertRaisesRegex(RuntimeError, "未确认兵营"):
                bot.scan_training_upgrade("test", right, title)
        tap.assert_not_called()

    def test_promotion_and_fallback_use_only_resource_buttons_and_return_to_city(self):
        drawer = frame("drawer-sample")
        city = frame("city-sample")
        for promote in (True, False):
            with self.subTest(promote=promote):
                unit = "shield" if promote else "spear"
                ready = frame(f"{unit}-ready")
                selected = frame("shield-selected") if promote else ready
                busy = selected.copy()
                busy.putpixel((400, 1900), (80, 100, 120))
                frames = [city, ready]
                if promote:
                    frames += [selected, frame("shield-dialog")]
                frames += [busy, city, city]
                with (patch.object(bot, "reset_drawer", return_value=drawer),
                      patch.object(bot, "inspect", return_value=(True, None, 0)),
                      patch.object(bot, "task_drawer", return_value=drawer),
                      patch.object(bot, "training_status", return_value="idle"),
                      patch.object(bot, "task_screen", side_effect=frames),
                      patch.object(bot, "scan_training_upgrade",
                                   return_value=(ready, (945, 1480) if promote else None)),
                      patch.object(bot, "task_tap") as tap,
                      patch.object(bot, "task_page", return_value=True),
                      contextlib.redirect_stdout(io.StringIO()) as output):
                    title = Image.open(bot.ROOT / f"assets/emulator-training-{unit}-title.png")
                    result = bot.run_training("test", None, None, None, {unit: title},
                                              initial_image=drawer, units={unit}, upgrade=True)
                self.assertIs(result, city)
                actions = [call.args[1:] for call in tap.call_args_list]
                self.assertEqual((775, 1630) in actions, promote)
                self.assertEqual((795, 2100) in actions, not promote)
                self.assertIn((60, 190), actions)
                self.assertEqual("⚠️ 城内无可升级兵种" in output.getvalue(), not promote)


if __name__ == "__main__":
    unittest.main()
