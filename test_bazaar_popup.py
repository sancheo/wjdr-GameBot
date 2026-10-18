"""Actual bazaar startup frame: close only the verified exit, then confirm city."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import gui_web
import help_bot as bot
from test_help_bot import Clock
from test_startup_popup import asset, frame


class BazaarPopupTest(unittest.TestCase):
    def test_real_nox_frame_normalizes_heading_and_native_close(self):
        from screen_geometry import ScreenAdapter
        with Image.open(bot.ROOT / "tests/fixtures/nox-bazaar-1920.png") as raw:
            native = raw.convert("RGB")
        adapter = ScreenAdapter()
        image = adapter.frame("nox", native)
        self.assertEqual(bot.bazaar_close_target(image), (1018, 200))
        self.assertEqual(adapter.geometry("nox").point(1018, 200), (1018, 64))
        for box in ((350, 300, 720, 380), (985, 165, 1050, 235)):
            masked = image.copy()
            masked.paste("black", box)
            self.assertIsNone(bot.bazaar_close_target(masked))

    def popup(self):
        with Image.open(bot.ROOT / "tests/fixtures/bazaar-popup-1016.png") as image:
            return image.convert("RGB")

    def test_actual_popup_is_loaded_and_only_its_exit_is_selected(self):
        for mode in ("RGB", "L"):
            image = self.popup().convert(mode)
            self.assertEqual(bot.bazaar_close_target(image), (1018, 200))
            self.assertEqual(bot.reconnect_popup_target(image), (1018, 200))
            self.assertTrue(gui_web.game_loaded(image.convert("L")))

    def test_both_heading_and_close_are_required(self):
        for box in ((350, 300, 720, 380), (985, 165, 1050, 235)):
            image = self.popup()
            image.paste("black", box)
            self.assertIsNone(bot.bazaar_close_target(image))

    def test_city_other_sales_and_offline_are_not_bazaar(self):
        for name in ("stamina-ready", "startup-5556-before", "feature-tech",
                     "reconnect-current-emulator", "reconnect-activity-current"):
            self.assertIsNone(bot.bazaar_close_target(frame(name)), name)

    def test_recovery_closes_before_slow_page_scanning_and_checks_city(self):
        clock = Clock()
        popup, city = self.popup().convert("L"), frame("stamina-ready").convert("L")
        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot.time, "sleep", clock.sleep),
              patch.object(bot, "screenshot", side_effect=[popup] + [city] * 3) as capture,
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "gather_search_page", return_value=False) as scan,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("test", asset("help-icon"), asset("city-nav"), None, None))
        tap.assert_called_once_with("test", 1018, 200)
        self.assertEqual(capture.call_count, 4)
        self.assertEqual(scan.call_count, 3)


if __name__ == "__main__":
    unittest.main()
