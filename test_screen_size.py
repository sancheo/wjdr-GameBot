"""The screenshot/input boundary consistently uses the logical canvas."""

import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import gui_web
import help_bot as bot
from screen_geometry import ScreenAdapter


class ScreenSizeTest(unittest.TestCase):
    def png(self):
        with Image.open(bot.ROOT / "tests/fixtures/phone-city-1176.png") as image:
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()

    def test_both_capture_modes_are_normalized_before_game_checks(self):
        adapter = ScreenAdapter()
        with (patch.object(bot, "screen_adapter", adapter),
              patch.object(bot, "adb", return_value=self.png()),
              contextlib.redirect_stdout(io.StringIO())):
            rgb = bot.color_screenshot("usb")
            gray = bot.screenshot("usb")
        self.assertEqual(rgb.size, (1080, 2340))
        self.assertEqual(gray.size, (1080, 2340))
        self.assertEqual((rgb.mode, gray.mode), ("RGB", "L"))
        self.assertTrue(gui_web.game_loaded(gray))
        self.assertEqual(rgb.info["native_frame"].size, (1176, 2400))

    def test_direct_adb_tap_and_swipe_are_mapped_once(self):
        adapter = ScreenAdapter()
        with Image.open(bot.ROOT / "tests/fixtures/phone-city-1176.png") as native:
            adapter.frame("usb", native.convert("RGB"))
        with (patch.object(bot, "screen_adapter", adapter),
              patch.object(bot.subprocess, "run") as run):
            bot.adb("usb", "shell", "input", "tap", "110", "2235")
            self.assertEqual(run.call_args.args[0][-2:], ["120", "2286"])
            bot.adb("usb", "shell", "input", "swipe", "110", "2235", "110", "2235", "3000")
            self.assertEqual(run.call_args.args[0][-5:], ["120", "2286", "120", "2286", "3000"])

    def test_first_input_calibrates_from_a_real_capture(self):
        adapter = ScreenAdapter()
        png = self.png()
        with (patch.object(bot, "screen_adapter", adapter),
              patch.object(bot.subprocess, "run") as run,
              contextlib.redirect_stdout(io.StringIO())):
            run.return_value.stdout = png
            bot.adb("usb", "shell", "input", "tap", "110", "2235")
        self.assertEqual(len(run.call_args_list), 2)
        self.assertEqual(run.call_args_list[0].args[0][-3:], ["exec-out", "screencap", "-p"])
        self.assertEqual(run.call_args_list[1].args[0][-2:], ["120", "2286"])

    def test_adapter_diagnostics_never_enter_the_json_stdout_pipe(self):
        output, errors = io.StringIO(), io.StringIO()
        with (patch.object(bot, "screen_adapter", ScreenAdapter()),
              patch.object(bot, "adb", return_value=self.png()),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors)):
            bot.color_screenshot("usb")
        self.assertEqual(output.getvalue(), "")
        self.assertIn("屏幕适配", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
