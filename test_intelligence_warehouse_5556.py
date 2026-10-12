"""Regressions captured on emulator-5556 on October 5."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot


def frame(name):
    with Image.open(bot.ROOT / f"artifacts/{name}.png") as im:
        return im.convert("RGB")


def asset(name):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as im:
        return im.convert("L")


class Emulator5556Test(unittest.TestCase):
    def test_title_accepts_the_five_pixel_offset_in_color_and_grayscale(self):
        for name in ("intel-map", "intelligence-1005-map-menu"):
            for mode in ("RGB", "L"):
                self.assertTrue(bot.intelligence_page(frame(name).convert(mode)))
        for name in ("intelligence-1005-map", "intelligence-1005-wilderness",
                     "warehouse-1005-drawer-3"):
            self.assertFalse(bot.intelligence_page(frame(name)))

    def test_entry_moves_with_the_pet_shortcut(self):
        with patch.object(bot, "TOWN_TEMPLATE", asset("town-button")):
            self.assertEqual(bot.intelligence_entry_target(frame("wilderness-current")), (996, 1714))
            self.assertEqual(bot.intelligence_entry_target(frame("intelligence-1005-map")), (996, 1854))
            self.assertIsNone(bot.intelligence_entry_target(frame("intelligence-1005-before")))
            missing = frame("intelligence-1005-map")
            missing.paste("black", (940, 1620, 1080, 2030))
            self.assertIsNone(bot.intelligence_entry_target(missing))

    def test_transition_longer_than_three_frames_does_not_repeat_navigation(self):
        city = frame("intelligence-1005-before")
        loading = frame("intelligence-1005-wilderness")
        wilderness = frame("intelligence-1005-map")
        with (patch.object(bot, "TOWN_TEMPLATE", asset("town-button")),
              patch.object(bot, "task_page", return_value=False),
              patch.object(bot, "task_screen", side_effect=[loading] * 5 + [wilderness]),
              patch.object(bot, "task_tap") as tap):
            self.assertIs(bot.enter_wilderness("test", city, asset("help-icon"),
                                               asset("city-nav"), "灯塔情报"), wilderness)
        tap.assert_called_once_with("test", 960, 2230)

    def test_open_uses_current_frame_after_queue_wait_and_waits_for_page(self):
        stale = frame("intelligence-1005-before")
        wilderness = frame("intelligence-1005-map")
        page = frame("intelligence-1005-map-menu")
        with (patch.object(bot, "town_button", return_value=True),
              patch.object(bot, "intelligence_wait_queue", return_value=wilderness),
              patch.object(bot, "intelligence_entry_target", return_value=(996, 1854)) as locate,
              patch.object(bot, "task_screen", side_effect=[wilderness] * 4 + [page]),
              patch.object(bot, "task_tap") as tap):
            self.assertIs(bot.open_intelligence("test", stale), page)
        locate.assert_called_once_with(wilderness)
        tap.assert_called_once_with("test", 996, 1854)

    def test_missing_entry_never_clicks_the_old_map_coordinate(self):
        wilderness = frame("intelligence-1005-map")
        with (patch.object(bot, "town_button", return_value=True),
              patch.object(bot, "intelligence_wait_queue", return_value=wilderness),
              patch.object(bot, "intelligence_entry_target", return_value=None),
              patch.object(bot, "task_tap") as tap,
              self.assertRaisesRegex(RuntimeError, "未定位到灯塔情报入口")):
            bot.open_intelligence("test", wilderness)
        tap.assert_not_called()

    def test_last_warehouse_row_below_old_ocr_cutoff_is_completed(self):
        image = frame("warehouse-1005-drawer-3")
        score, _, y = bot.reward_row(image, asset("warehouse-title"), "仓库补给")
        self.assertGreaterEqual(score, .85)
        self.assertTrue(1490 <= y <= 1500)
        self.assertEqual(bot.training_status(image, y + 30), "completed")
        # OCR must also include the bottom row when the background defeats
        # the template; never misreport this frame as not ready.
        with patch.object(bot, "find_white", return_value=(0, 0, 0)):
            score, _, y = bot.reward_row(image, asset("warehouse-title"), "仓库补给")
        self.assertEqual(score, 1)
        self.assertTrue(1490 <= y <= 1500)

    def test_recovery_leaves_confirmed_intelligence_before_unrelated_ocr(self):
        page = frame("intelligence-1005-map-menu")
        wilderness = frame("intelligence-1005-map")
        city = frame("intelligence-1005-before")
        transition = frame("intelligence-1005-wilderness")
        with (patch.object(bot, "TOWN_TEMPLATE", asset("town-button")),
              patch.object(bot, "screenshot", side_effect=[im.convert("L") for im in
                                                         (page, page, transition, wilderness,
                                                          wilderness, transition, city, city, city)]),
              patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "alliance_back_target", side_effect=AssertionError("unrelated page")),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot.time, "sleep"), contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city("test", asset("help-icon"), asset("city-nav"), None, None))
        self.assertEqual([c.args[1:] for c in tap.call_args_list], [(60, 190), (960, 2230)])


if __name__ == "__main__":
    unittest.main()
