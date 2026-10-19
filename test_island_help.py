"""Replay Nox island help, including moving list rows and changing scenery."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot
import island_help as island
from screen_geometry import ScreenAdapter


SAMPLES = bot.ROOT / 'tests/fixtures/nox/island-help-20261018'
LIVE = bot.ROOT / 'tests/fixtures/nox/island-help-live-20261018-183615'
COMPLETED = bot.ROOT / 'tests/fixtures/nox/island-help-live-20261018-183851'


def frame(name, directory=SAMPLES):
    with Image.open(directory / f'{name}-native.png') as image:
        return ScreenAdapter().frame('small', image.convert('RGB'))


class IslandHelpTest(unittest.TestCase):
    def test_live_white_heart_is_recognized_against_snow(self):
        with Image.open(bot.ROOT / 'tests/fixtures/nox/island-help-white-heart-1019-native.png') as native:
            image = ScreenAdapter().frame('small', native.convert('RGB'))
        self.assertEqual(island.heart(bot, image)[0], 'white')

    def test_live_grey_warning_control_identifies_visitor_island(self):
        with Image.open(bot.ROOT / 'tests/fixtures/nox/island-help-visitor-grey-1019-native.png') as native:
            image = ScreenAdapter().frame('small', native.convert('RGB'))
        self.assertTrue(island.visitor(bot, image))
        self.assertIsNotNone(island.tend_target(bot, image))
        self.assertEqual(island.heart(bot, image)[0], 'white')

    def assert_icon_center(self, actual, expected):
        self.assertIsNotNone(actual)
        self.assertLessEqual(abs(actual[0] - expected[0]), 4)
        self.assertLessEqual(abs(actual[1] - expected[1]), 4)

    def test_two_live_visits_recheck_trophy_on_current_island_and_exit_when_clear(self):
        first = frame('001', COMPLETED)
        following = [frame(name, COMPLETED) for name in
                     ('002', '003', '005', '008', '016', '017', '018', '019', '021')]
        own = following[-1]
        template = bot.open_template(bot.ROOT / 'assets/emulator-island-title.png').convert('L')
        with (patch.object(bot, 'task_screen', side_effect=following),
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(island.run(bot, 'small', first, template), own)
        calls = [call.args[1:] for call in tap.call_args_list]
        self.assertEqual(calls[0], island.tend_target(bot, first))
        self.assertEqual(calls[1], (1000, 1398))
        # Continue through the visitor's trophy directly, without returning home.
        self.assertEqual(calls[2], (1005, 296))
        self.assertEqual(calls[-1], (60, 190))
        self.assertEqual(calls.count((60, 190)), 1)
        self.assertEqual(calls.count((1000, 1398)), 1)  # Already-red second heart is untouched.

    def test_no_trophy_badge_exits_without_opening_list(self):
        image = frame('021', COMPLETED)
        template = bot.open_template(bot.ROOT / 'assets/emulator-island-title.png').convert('L')
        self.assertFalse(island.trophy_badge(bot, image))
        with (patch.object(bot, 'task_screen', return_value=image),
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(island.run(bot, 'small', image, template), image)
        tap.assert_not_called()

    def test_persistent_help_icon_is_not_reported_as_success_or_liked(self):
        image = frame('visit-before')
        with (patch.object(bot, 'task_screen', return_value=image),
              patch.object(bot, 'task_tap') as tap,
              self.assertRaisesRegex(RuntimeError, '未确认打理成功')):
            island.run(bot, 'small', image, None)
        tap.assert_called_once_with('small', *island.tend_target(bot, image))

    def test_scan_requires_person_eye_and_its_own_badge(self):
        self.assertIsNone(island.visit_target(bot, frame('prosperity')))
        self.assert_icon_center(island.visit_target(bot, frame('list-01')), (963, 1551))
        # A real scrolled frame: the same column is at a different height.
        self.assert_icon_center(island.visit_target(bot, frame('004', LIVE)), (963, 1447))

    def test_smaller_podium_visit_icons_are_also_detected(self):
        # Keep the actual Nox icon pixels; inject only whether its badge is set.
        with patch.object(island, 'badge', side_effect=lambda im, box: box[0] < 400):
            target = island.visit_target(bot, frame('prosperity'))
        self.assertIsNotNone(target)
        self.assertTrue(300 < target[0] < 380)

    def test_tend_icon_disappears_and_white_heart_becomes_red(self):
        before, tended, liked = [frame(name) for name in ('visit-before', 'visit-tended', 'visit-liked')]
        self.assertEqual(island.tend_target(bot, before), (540, 785))
        self.assertIsNone(island.tend_target(bot, tended))
        self.assertEqual(island.heart(bot, before), ('white', (1000, 1398)))
        self.assertEqual(island.heart(bot, liked), ('red', (1000, 1398)))
        self.assertTrue(island.trophy_badge(bot, liked))

    def test_white_icons_survive_another_islands_bright_background(self):
        image = frame('016', LIVE)
        self.assertTrue(island.visitor(bot, image))
        self.assertEqual(island.heart(bot, image), ('white', (1000, 1398)))
        self.assertIsNotNone(island.tend_target(bot, image))
        self.assertFalse(island.visitor(bot, frame('before-trophy')))

    def test_scan_scrolls_slowly_then_uses_fresh_row_position(self):
        first, lower = frame('prosperity'), frame('list-01')
        with (patch.object(bot, 'adb') as swipe,
              patch.object(bot, 'task_screen', return_value=lower)):
            result, target = island.find_visit(bot, 'small', first)
        self.assertIs(result, lower)
        self.assert_icon_center(target, (963, 1551))
        self.assertEqual(swipe.call_args.args[-1], '1400')
        self.assertEqual(swipe.call_args.args[1:4], ('shell', 'input', 'swipe'))

    def test_stale_badge_at_bottom_does_not_loop_forever(self):
        image = frame('prosperity')
        with (patch.object(bot, 'adb') as swipe,
              patch.object(bot, 'task_screen', return_value=image),
              contextlib.redirect_stdout(io.StringIO())):
            result, target = island.find_visit(bot, 'small', image)
        self.assertIsNone(target)
        self.assertEqual(swipe.call_count, 2)


if __name__ == '__main__':
    unittest.main()
