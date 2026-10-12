"""Resource shortages must not disable training or trap city recovery."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot


def frame(name):
    with Image.open(bot.ROOT / f"artifacts/{name}.png") as image:
        return image.convert("RGB")


class TrainingResourceTest(unittest.TestCase):
    def test_resource_panel_requires_both_title_and_close_button(self):
        image = frame("training-resource-shortage")
        for mode in ("RGB", "L"):
            self.assertEqual(bot.resource_more_target(image.convert(mode)), (1000, 480))
        shifted = Image.new("RGB", image.size)
        shifted.paste(image, (0, -40))
        self.assertEqual(bot.resource_more_target(shifted), (1000, 440))
        for box in ((430, 445, 650, 520), (972, 450, 1028, 510)):
            erased = image.copy()
            erased.paste("black", box)
            self.assertIsNone(bot.resource_more_target(erased))
        for name in ("spear-training-ready", "upgrade-shield-dialog", "pet-city",
                     "pet-reward-overlay"):
            self.assertIsNone(bot.resource_more_target(frame(name)), name)

    def test_startup_closes_resource_panel_then_leaves_barracks(self):
        popup, ready, city = map(frame, ("training-resource-shortage", "spear-training-ready",
                                       "upgrade-city-sample"))
        help_icon = Image.open(bot.ROOT / "assets/emulator-help-icon.png").convert("L")
        nav = Image.open(bot.ROOT / "assets/emulator-city-nav.png").convert("L")
        with (patch.object(bot, "screenshot", side_effect=[image.convert("L") for image in
                                                          (popup, ready, city, city, city)]),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("test", help_icon, nav, None, None))
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(1000, 480), (60, 190)])

    def test_startup_recovers_promotion_from_greyscale_screenshots(self):
        popup, dialog, ready, city = map(frame, ("training-resource-shortage", "upgrade-shield-dialog",
                                               "upgrade-shield-selected", "upgrade-city-sample"))
        help_icon = Image.open(bot.ROOT / "assets/emulator-help-icon.png").convert("L")
        nav = Image.open(bot.ROOT / "assets/emulator-city-nav.png").convert("L")
        with (patch.object(bot, "screenshot", side_effect=[image.convert("L") for image in
                                                          (popup, dialog, ready, city, city, city)]),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("test", help_icon, nav, None, None))
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(1000, 480), (997, 719), (60, 190)])

    def test_retry_recovers_resource_panel_and_promotion_dialog(self):
        popup, dialog, ready, city = map(frame, ("training-resource-shortage", "upgrade-shield-dialog",
                                               "upgrade-shield-selected", "upgrade-city-sample"))
        help_icon = Image.open(bot.ROOT / "assets/emulator-help-icon.png").convert("L")
        nav = Image.open(bot.ROOT / "assets/emulator-city-nav.png").convert("L")
        with (patch.object(bot, "color_screenshot", return_value=popup),
              patch.object(bot, "task_screen", side_effect=[dialog, ready, city]),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "return_to_city", side_effect=AssertionError("must recover directly"))):
            self.assertIs(bot.recover_task_city("test", help_icon, nav, None, None), city)
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(1000, 480), (997, 719), (60, 190)])

    def test_shortage_skips_training_or_promotion_and_keeps_other_units_scheduled(self):
        for promote in (False, True):
            for busy_seconds in (300, 3600):
                with self.subTest(promote=promote, busy_seconds=busy_seconds):
                    unit = "shield" if promote else "spear"
                    city = frame("upgrade-city-sample")
                    drawer = frame("upgrade-drawer-sample")
                    ready = frame("upgrade-shield-selected" if promote else "spear-training-ready")
                    popup = frame("training-resource-shortage")
                    dialog = frame("upgrade-shield-dialog")
                    frames = [city, ready]
                    if promote:
                        frames += [ready, dialog]
                    # The server may report the shortage after an unchanged frame.
                    frames += [popup] if promote else [ready, popup]
                    if promote:
                        frames += [dialog]
                    else:
                        frames += [popup]  # Closing the panel may also animate slowly.
                    frames += [ready, city]
                    timers = {}
                    title = Image.open(bot.ROOT / f"assets/emulator-training-{unit}-title.png")
                    with (patch.object(bot, "reset_drawer", return_value=drawer),
                          patch.object(bot, "task_drawer", return_value=drawer),
                          patch.object(bot, "inspect", return_value=(True, None, 0)),
                          patch.object(bot, "training_status", side_effect=["idle", "busy"]),
                          patch.object(bot, "task_screen", side_effect=frames),
                          patch.object(bot, "scan_training_upgrade", return_value=(ready, (945, 1480))),
                          patch.object(bot, "countdown", return_value=busy_seconds),
                          patch.object(bot, "task_tap") as tap,
                          patch.object(bot.time, "monotonic", return_value=100),
                          contextlib.redirect_stdout(io.StringIO()) as output):
                        result = bot.run_training("test", None, None, None, {unit: title},
                                                  initial_image=drawer, keep_open=True,
                                                  units={unit, "archer"}, timers=timers, upgrade=promote)
                    self.assertIs(result, drawer)
                    self.assertEqual(timers["train"], min(700, 100 + busy_seconds))
                    actions = [call.args[1:] for call in tap.call_args_list]
                    self.assertEqual(actions.count((775, 1630) if promote else (795, 2100)), 1)
                    self.assertEqual(actions[-2:], [(1000, 480), (60, 190)] if not promote else
                                     [(997, 719), (60, 190)])
                    self.assertIn("普通资源不足", output.getvalue())
                    self.assertIn("射手已在训练中", output.getvalue())

    def test_single_unit_shortage_with_or_without_schedule(self):
        drawer, city, ready, popup = map(frame, ("upgrade-drawer-sample", "upgrade-city-sample",
                                                "spear-training-ready", "training-resource-shortage"))
        title = Image.open(bot.ROOT / "assets/emulator-training-spear-title.png")
        for timers in (None, {}):
            with (self.subTest(scheduled=timers is not None),
                  patch.object(bot, "reset_drawer", return_value=drawer),
                  patch.object(bot, "task_drawer", return_value=drawer),
                  patch.object(bot, "inspect", return_value=(True, None, 0)),
                  patch.object(bot, "training_status", return_value="idle"),
                  patch.object(bot, "task_screen", side_effect=[city, ready, popup, ready, city]),
                  patch.object(bot, "task_tap"),
                  patch.object(bot.time, "monotonic", return_value=100),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertIs(bot.run_training("test", None, None, None, {"spear": title},
                                               initial_image=drawer, keep_open=True,
                                               units={"spear"}, timers=timers), drawer)
            if timers is not None:
                self.assertEqual(timers, {"train": 700})

    def test_unconfirmed_training_still_fails_instead_of_being_skipped(self):
        drawer, city, ready = map(frame, ("upgrade-drawer-sample", "upgrade-city-sample",
                                          "spear-training-ready"))
        title = Image.open(bot.ROOT / "assets/emulator-training-spear-title.png")
        with (patch.object(bot, "reset_drawer", return_value=drawer),
              patch.object(bot, "inspect", return_value=(True, None, 0)),
              patch.object(bot, "training_status", return_value="idle"),
              patch.object(bot, "task_screen", side_effect=[city, ready] + [ready] * 5),
              patch.object(bot, "task_tap"),
              self.assertRaisesRegex(RuntimeError, "训练开始后未确认状态变化")):
            bot.run_training("test", None, None, None, {"spear": title},
                             initial_image=drawer, units={"spear"})

    def test_delayed_entry_and_training_result_do_not_repeat_start(self):
        drawer, city, ready, started = map(frame, ("upgrade-drawer-sample", "upgrade-city-sample",
                                                  "spear-training-ready", "spear-training-started"))
        title = Image.open(bot.ROOT / "assets/emulator-training-spear-title.png")
        with (patch.object(bot, "reset_drawer", return_value=drawer),
              patch.object(bot, "task_drawer", return_value=drawer),
              patch.object(bot, "inspect", side_effect=[(True, None, 0)] * 2 +
                           [(False, None, 80), (True, None, 0), (True, None, 0)]),
              patch.object(bot, "training_status", return_value="idle"),
              patch.object(bot, "task_screen", side_effect=[city, city, ready, ready, started, started, city]),
              patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.run_training("test", None, None, None, {"spear": title},
                                           initial_image=drawer, keep_open=True, units={"spear"}), drawer)
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(608, 1225), (725, 1590), (795, 2100), (60, 190)])


if __name__ == "__main__":
    unittest.main()
