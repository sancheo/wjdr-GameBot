"""Real-map regressions for artwork-independent pins and bounty separation."""
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
import help_bot as bot


def frame(name):
    with Image.open(bot.ROOT / f"artifacts/{name}.png") as image:
        return image.convert("RGB")


def near(points, target):
    return any(abs(x - target[0]) < 12 and abs(y - target[1]) < 16 for x, y in points)


class IntelligenceShapeTest(unittest.TestCase):
    def test_multiple_real_maps_and_occlusion(self):
        cases = {
            "intel-map": [(502, 892), (354, 1023), (835, 1118), (760, 1390)],
            "intel-map-remaining": [(278, 1021), (259, 1238)],
            "bounty-final-map": [(160, 1155), (666, 948)],
            "5556-intelligence-final-page": [(648, 749), (272, 1105),
                                              (927, 1163), (151, 1213), (695, 1416)],
        }
        for name, expected in cases.items():
            points = bot.ordinary_pins(frame(name))
            for target in expected:
                with self.subTest(name=name, target=target):
                    self.assertTrue(near(points, target), points)

    def test_unknown_artwork_and_colors_without_templates(self):
        original = frame("intel-map")
        # Retain only a real pin's outer border, replace its entire head interior.
        # No new activity screenshot/template is needed for these regressions.
        for color in ("gold", "cyan", "magenta", "white", "black", "lime"):
            image = original.copy()
            draw = ImageDraw.Draw(image)
            draw.ellipse((793, 1076, 877, 1160), fill=color)
            draw.polygon([(835, 1086), (812, 1145), (862, 1135)], fill="red")
            for mode in ("RGB", "L"):
                with self.subTest(color=color, mode=mode), patch.object(
                        bot, "find_white", side_effect=AssertionError("glyph search used")):
                    self.assertTrue(near(bot.ordinary_pins(image.convert(mode)), (835, 1118)))

    def test_completed_and_marching_pins_are_excluded(self):
        completed = frame("intel-map-progress")
        exclusions = bot.ordinary_pins(completed)
        for target in ((502, 892), (835, 1118), (760, 1390)):
            self.assertTrue(bot.intelligence_completed(completed, target))
        original = frame("intel-map")
        pending = []
        while (target := bot.intelligence_target(original, pending)) is not None:
            self.assertFalse(near(pending, target))
            pending.append((target[0] + 3, target[1] - 3))
        self.assertEqual(len(pending), len(bot.ordinary_pins(original)))
        self.assertGreaterEqual(len(pending), 5)
        self.assertIsNone(bot.intelligence_target(completed, exclusions))

    def test_fire_bounties_and_unrelated_shapes_never_become_ordinary(self):
        for name in ("bounty-live-start", "bounty-defeat-map", "bounty-map-after-battle"):
            with self.subTest(name=name):
                self.assertEqual(bot.ordinary_pins(frame(name)), [])
        image = frame("intel-map")
        image.paste((185, 210, 230), (60, 450, 1020, 1950))
        draw = ImageDraw.Draw(image)
        draw.ellipse((200, 700, 305, 805), fill="gold", outline="black", width=4)
        draw.polygon([(500, 900), (450, 1000), (550, 1000)], fill="purple", outline="black", width=4)
        draw.rectangle((700, 1200, 930, 1500), fill="black")
        self.assertEqual(bot.ordinary_pins(image), [])
        for name in ("intelligence-1005-map", "intelligence-1005-before"):
            self.assertEqual(bot.ordinary_pins(frame(name)), [])

    def test_empty_refresh_banner_never_becomes_a_task(self):
        image = frame("intel-empty-refresh-banner")
        for mode in ("RGB", "L"):
            with self.subTest(mode=mode):
                self.assertEqual(bot.ordinary_pins(image.convert(mode)), [])
                self.assertIsNone(bot.intelligence_target(image.convert(mode)))

    def test_bounty_detail_blocks_ordinary_dispatch(self):
        image = Image.new("RGB", bot.EXPECTED_SIZE)
        with (patch.object(bot, "bounty_name", return_value="大师悬赏：06号"),
              patch.object(bot, "task_tap") as tap,
              self.assertRaisesRegex(RuntimeError, "未执行出征")):
            bot.execute_intelligence("test", image)
        tap.assert_not_called()

    def test_green_task_body_is_not_a_completion_badge(self):
        image = frame("intel-shape-green-map")
        for target in ((648, 749), (928, 1028), (271, 1106), (412, 1294)):
            with self.subTest(target=target):
                self.assertFalse(bot.intelligence_completed(image, target))
        # The same green pin becomes completed only when the white tick badge appears.
        source = frame("intel-map-progress")
        image.paste(source.crop((511, 830, 562, 881)), (657, 686))
        self.assertTrue(bot.intelligence_completed(image, (648, 749)))

    def test_event_burning_background_is_not_a_pin(self):
        image = frame("intel-shape-burning-map")
        points = bot.ordinary_pins(image)
        self.assertFalse(near(points, (951, 959)))
        self.assertTrue(near(points, (928, 1029)))
        # The novel gold activity glyph is an ordinary pin; the fire bounty is not.
        self.assertTrue(near(points, (343, 1468)))
        self.assertFalse(near(points, (402, 1450)))


if __name__ == "__main__":
    unittest.main()
