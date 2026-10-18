"""Replay the unchanged alliance technology failure captured on October 16."""
import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot
from test_help_bot import Clock
from test_runtime_recovery import asset, frame


class FrozenTechTest(unittest.TestCase):
    def setUp(self):
        with Image.open(bot.ROOT / 'tests/fixtures/frozen-tech-1016.png') as image:
            self.frozen = image.convert('L')
        self.city = frame('stamina-ready').convert('L')
        self.clock = Clock()
        self.enterContext(patch.object(bot.time, 'monotonic', self.clock.monotonic))
        self.enterContext(patch.object(bot.time, 'sleep', self.clock.sleep))
        self.enterContext(patch.object(bot, 'task_back'))
        self.enterContext(patch.object(bot, 'check_game_guide'))
        recognize = bot.recognize_text
        recognized = {}
        def cached_text(image):
            key = (image.mode, image.size, image.tobytes())
            if key not in recognized:
                recognized[key] = recognize(image)
            return recognized[key]
        self.enterContext(patch.object(bot, 'recognize_text', side_effect=cached_text))
        # Other page recognizers are unrelated to this observed technology tree.
        for name in ('gather_search_page', 'intelligence_page', 'treasure_page', 'town_button'):
            self.enterContext(patch.object(bot, name, return_value=False))
        for name in ('social_back_target', 'treasure_dispatch_close', 'healing_back_target'):
            self.enterContext(patch.object(bot, name, return_value=None))
        self.enterContext(patch.object(bot, 'forced_offline', return_value=False))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def capture(self, image):
        self.clock.sleep(4)
        return image

    def recover(self, **kwargs):
        return bot.return_to_city('test', asset('help-icon'), asset('city-nav'),
                                  None, None, **kwargs)

    def test_actual_ignored_back_taps_reopen_after_full_timeout(self):
        taps = []
        with (patch.object(bot, 'screenshot', side_effect=lambda _: self.capture(self.frozen)),
              patch.object(bot, 'task_tap', side_effect=lambda *args: taps.append(args)),
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'reopen_frozen_game', return_value=True) as reopen):
            self.assertTrue(self.recover())
        reopen.assert_called_once()
        self.assertGreaterEqual(self.clock.now, 160)
        self.assertGreaterEqual(len(taps), 2)
        self.assertTrue(all(tap == ('test', 60, 190) for tap in taps))

    def test_changing_page_even_if_final_frame_returns_does_not_reopen(self):
        changed = self.frozen.copy()
        changed.paste('white', (300, 900, 700, 1200))
        frames = iter([self.frozen, changed])
        with (patch.object(bot, 'screenshot', side_effect=lambda _: self.capture(next(frames, self.frozen))),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'reopen_frozen_game') as reopen,
              self.assertRaisesRegex(RuntimeError, '60 秒内仍未确认主城')):
            self.recover()
        reopen.assert_not_called()

    def test_normal_back_transition_confirms_city_without_reopen(self):
        with (patch.object(bot, 'screenshot', side_effect=[self.frozen] + [self.city] * 3),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'reopen_frozen_game') as reopen):
            self.assertTrue(self.recover())
        tap.assert_called_once_with('test', 60, 190)
        reopen.assert_not_called()

    def test_nested_recovery_cannot_reopen_again(self):
        with (patch.object(bot, 'screenshot', side_effect=lambda _: self.capture(self.frozen)),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'reopen_frozen_game') as reopen,
              self.assertRaisesRegex(RuntimeError, '60 秒内仍未确认主城')):
            self.recover(_reopen_frozen=False)
        reopen.assert_not_called()

    def test_foreground_loss_prevents_reopen(self):
        with (patch.object(bot, 'screenshot', side_effect=lambda _: self.capture(self.frozen)),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'game_foreground', return_value=False),
              patch.object(bot, 'reopen_frozen_game') as reopen,
              self.assertRaisesRegex(RuntimeError, '未重新打开')):
            self.recover()
        reopen.assert_not_called()

    def test_cancellation_before_reopen_is_respected(self):
        with (patch.object(bot, 'screenshot', side_effect=lambda _: self.capture(self.frozen)),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'check_stop', side_effect=bot.StopRequested()),
              patch.object(bot, 'reopen_frozen_game') as reopen,
              self.assertRaises(bot.StopRequested)):
            self.recover()
        reopen.assert_not_called()

    def test_short_observation_does_not_reopen(self):
        with (patch.object(bot, 'screenshot', side_effect=lambda _: self.capture(self.frozen)),
              patch.object(bot, 'task_tap'),
              patch.object(bot, 'reopen_frozen_game') as reopen,
              self.assertRaisesRegex(RuntimeError, '10 秒内仍未确认主城')):
            self.recover(timeout=10)
        reopen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
