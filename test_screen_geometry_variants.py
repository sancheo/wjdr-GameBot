"""Independent UI-anchor rendering across phone sizes and system-bar layouts."""

import unittest

from PIL import Image

import help_bot as bot
import hunting
from screen_geometry import ScreenAdapter, detect


class PhoneSizeVariantsTest(unittest.TestCase):
    layouts = (((720, 1280), 32, 48), ((720, 1600), 64, 0),
               ((1080, 1920), 84, 72), ((1080, 2400), 0, 80),
               ((1440, 3200), 120, 96))

    def canvas(self, size, top, bottom):
        image = Image.new("RGB", size)
        image.paste((30, 50, 75), (0, top, size[0], size[1] - bottom))
        return image

    def stamp(self, native, source, box, position):
        scale = native.width / 1080
        crop = source.crop(box)
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
        native.paste(crop, tuple(round(value) for value in position))

    def test_top_page_title_and_native_back_target(self):
        box = (135, 150, 255, 225)
        title = bot.open_template(bot.ROOT / "assets/emulator-alliance-title.png").convert("RGB")
        source = Image.new("RGB", bot.EXPECTED_SIZE, (30, 50, 75))
        source.paste(title, box[:2])
        for size, top, bottom in self.layouts:
            with self.subTest(size=size, bars=(top, bottom)):
                scale = size[0] / 1080
                native = self.canvas(size, top, bottom)
                self.stamp(native, source, box, (135 * scale, top + (150 - 136) * scale))
                adapter = ScreenAdapter()
                image = adapter.frame("new-phone", native)
                self.assertTrue(bot.task_page(image, {"alliance-title": title}, "alliance-title"))
                x, y = adapter.geometry("new-phone").point(60, 190)
                self.assertAlmostEqual(x, 60 * scale, delta=2)
                self.assertAlmostEqual(y, top + 54 * scale, delta=2)

    def test_centered_pet_panel_and_skill_tap_share_the_same_anchor(self):
        raw = Image.open(bot.ROOT / "tests/fixtures/phone-pet-panel-1176.png").convert("RGB")
        source = detect(raw).normalize(raw)
        for size, top, bottom in self.layouts:
            with self.subTest(size=size, bars=(top, bottom)):
                scale = size[0] / 1080
                center = (top + size[1] - bottom) / 2
                native = self.canvas(size, top, bottom)
                self.stamp(native, source, (0, 430, 1080, 1780),
                           (0, center + (430 - 1238) * scale))
                adapter = ScreenAdapter()
                image = adapter.frame("new-phone", native)
                self.assertTrue(bot.pet_page(image))
                x, y = adapter.geometry("new-phone").point(645, 680)
                self.assertAlmostEqual(x, 645 * scale, delta=2)
                self.assertAlmostEqual(y, center + (680 - 1238) * scale, delta=2)
                self.assertTrue(bot.pet_skill_available(image, (645, 680)))

    def test_search_footer_category_level_and_search_tap_stay_together(self):
        source = Image.open(bot.ROOT / "tests/fixtures/hunt-beast-search.png").convert("RGB")
        for size, top, bottom in self.layouts:
            with self.subTest(size=size, bars=(top, bottom)):
                scale = size[0] / 1080
                native = self.canvas(size, top, bottom)
                self.stamp(native, source, (0, 136, 1080, 350), (0, top))
                self.stamp(native, source, (0, 1700, 1080, 2340),
                           (0, size[1] - bottom - 640 * scale))
                adapter = ScreenAdapter()
                image = adapter.frame("new-phone", native)
                self.assertTrue(bot.gather_search_page(image))
                self.assertEqual(hunting.level(bot, image, 30), 29)
                target = hunting.category_target(bot, image, "野兽")
                self.assertIsNotNone(target)
                x, y = adapter.geometry("new-phone").point(540, 2240)
                self.assertAlmostEqual(x, size[0] / 2, delta=2)
                self.assertAlmostEqual(y, size[1] - bottom - 100 * scale, delta=2)


if __name__ == "__main__":
    unittest.main()
