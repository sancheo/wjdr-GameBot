"""Cold-start activity variants use the same calibration as city recovery."""
import unittest
from unittest.mock import patch

from PIL import Image
import gui_web as desktop
import help_bot as bot


class StartupActivityTest(unittest.TestCase):
    def test_observed_activity_variants_finish_loading_but_do_not_confirm_city(self):
        template = bot.open_template(bot.ROOT / 'assets/emulator-activity-close.png').convert('L')
        nav = bot.open_template(bot.ROOT / 'assets/emulator-city-nav.png').convert('L')
        for avd in ('Pixel_5', 'Pixel_5_2'):
            for mode in ('RGB', 'L'):
                with self.subTest(avd=avd, mode=mode):
                    image = Image.open(bot.ROOT / f'tests/fixtures/startup-activity-1010-{avd}.png').convert(mode)
                    score = bot.find_white(image, template, range(750, 1001, 5), range(300, 901, 5))[0]
                    self.assertGreaterEqual(score, .88)
                    self.assertLess(score, .95)
                    self.assertGreater(bot.difference(image.crop(bot.NAV_BOX).convert('L'), nav), bot.NAV_LIMIT)
                    self.assertTrue(desktop.game_loaded(image.convert('L')))
                    with patch.object(bot, 'ACTIVITY_CLOSE', template):
                        target = bot.reconnect_popup_target(image)
                    self.assertTrue(960 <= target[0] <= 1020 and 580 <= target[1] <= 640)

    def test_loading_and_unrelated_pages_do_not_match_activity_close(self):
        template = bot.open_template(bot.ROOT / 'assets/emulator-activity-close.png').convert('L')
        for name in ('profile-page', 'treasure-dispatch', 'training-resource-shortage'):
            with self.subTest(name=name):
                image = Image.open(bot.ROOT / f'artifacts/{name}.png').convert('L')
                self.assertLess(bot.find_white(image, template, range(750, 1001, 5), range(300, 901, 5))[0], .88)
                if name == 'profile-page':
                    self.assertFalse(desktop.game_loaded(image))
        self.assertFalse(desktop.game_loaded(Image.new('L', bot.EXPECTED_SIZE)))


if __name__ == '__main__':
    unittest.main()
