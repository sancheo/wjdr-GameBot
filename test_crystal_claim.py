"""Replay actual island frames before and after the October 15 collection."""

import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot


def frame(name):
    with Image.open(bot.ROOT / f"tests/fixtures/crystal-1015-{name}.png") as image:
        return image.convert("RGB")


class CrystalClaimTest(unittest.TestCase):
    def template(self, name):
        return bot.open_template(bot.ROOT / f"assets/emulator-{name}.png")

    def test_actual_tutorial_icon_is_still_collectible(self):
        self.assertEqual(bot.crystal_target(frame("before"), self.template("crystal-claim")),
                         (524, 730))

    def test_collected_island_pond_and_foliage_are_not_claim_icons(self):
        template = self.template("crystal-claim")
        for name in ("pond", "decoration"):
            with self.subTest(name=name):
                image = frame(name)
                self.assertTrue(bot.island_page(image, self.template("island-title").convert("L")))
                self.assertIsNone(bot.crystal_target(image, template))
                self.assertIsNone(bot.crystal_target(image, template, near=(524, 730)))

    def test_clean_icons_win_over_decorations_at_different_positions(self):
        for name in ("crystal-claim", "crystal-claim-tutorial"):
            for position in ((220, 600), (650, 1300)):
                with self.subTest(name=name, position=position):
                    image = frame("decoration")
                    icon = self.template(name).convert("RGB")
                    image.paste(icon, position)
                    expected = (position[0] + icon.width // 2, position[1] + icon.height // 2)
                    self.assertEqual(bot.crystal_target(image, self.template("crystal-claim")), expected)

    def test_actual_claim_then_absent_icon_finishes_without_decoration_taps(self):
        before, after = frame("before"), frame("pond")
        with (patch.object(bot, "task_screen", return_value=after),
              patch.object(bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO()) as output):
            result = bot.collect_crystals("test", before, self.template("island-title").convert("L"))
        self.assertIs(result, after)
        tap.assert_called_once_with("test", 524, 730)
        self.assertIn("共 1 处", output.getvalue())

    def test_empty_island_does_not_claim_success_without_a_collection(self):
        with (patch.object(bot, "task_screen", return_value=frame("pond")),
              patch.object(bot, "task_tap") as tap,
              self.assertRaisesRegex(RuntimeError, "未确认领取成功")):
            bot.collect_crystals("test", frame("pond"), self.template("island-title").convert("L"))
        tap.assert_not_called()

    def test_shared_exploration_icon_does_not_make_island_a_city(self):
        help_icon, nav = self.template("help-icon").convert("L"), self.template("city-nav").convert("L")
        for name in ("before", "pond", "decoration"):
            with self.subTest(name=name), patch.object(bot, "recognize_text", side_effect=AssertionError("no OCR")):
                self.assertFalse(bot.inspect(frame(name).convert("L"), help_icon, nav)[0])
                self.assertFalse(bot.inspect(frame(name).convert("L"), help_icon, nav, allow_wilderness=True)[0])

    def test_recovery_exits_actual_island_before_accepting_city(self):
        with Image.open(bot.ROOT / "artifacts/stamina-ready.png") as image:
            city = image.convert("RGB")
        assets = [self.template(name).convert("L") for name in
                  ("help-icon", "city-nav", "forced-text", "reconnect")]
        with (patch.object(bot, "color_screenshot", return_value=frame("decoration")),
              patch.object(bot, "task_screen", return_value=city),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "network_reconnect_target", return_value=None),
              patch.object(bot, "city_relocation_target", return_value=None),
              patch.object(bot, "gather_search_page", return_value=False),
              patch.object(bot, "return_to_city", side_effect=AssertionError("known island exit"))):
            self.assertIs(bot.recover_task_city("test", *assets), city)
        tap.assert_called_once_with("test", 60, 190)


if __name__ == "__main__":
    unittest.main()
