"""Warehouse location must survive the observed upgrade scaffolding."""
import unittest

from PIL import Image
import help_bot as bot


def frame(name):
    with Image.open(bot.ROOT / f"tests/fixtures/warehouse-construction-{name}-1014.png") as image:
        return image.convert("RGB")


class WarehouseConstructionTest(unittest.TestCase):
    def test_scaffold_views_locate_the_same_calibrated_building(self):
        for name, expected in (("edge", (-332, -48)), ("visible", (-313, -467))):
            for mode in ("RGB", "L"):
                with self.subTest(view=name, mode=mode):
                    offset = bot.warehouse_position(frame(name).convert(mode))
                    self.assertEqual(offset, expected)
                    self.assertTrue(bot.stamina_warehouse_visible(offset))

    def test_roof_and_facade_use_the_same_coordinate_system(self):
        with Image.open(bot.ROOT / "artifacts/stamina-ready.png") as image:
            image = image.convert("RGB")
        expected = bot.warehouse_position(image)
        image.paste("black", (225, 1064, 385, 1184))
        self.assertEqual(bot.warehouse_position(image), expected)

    def test_research_anchor_without_visible_warehouse_is_rejected(self):
        self.assertIsNone(bot.warehouse_position(frame("absent")))

    def test_scaffolding_without_distinctive_roof_vent_is_rejected(self):
        image = frame("visible")
        image.paste("black", (212, 779, 362, 889))
        self.assertIsNone(bot.warehouse_position(image))


if __name__ == "__main__":
    unittest.main()
