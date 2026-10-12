"""Observed cold-start story recovery without operating a live emulator."""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from PIL import Image
import gui_web as desktop
import help_bot as bot

ROOT = Path(__file__).resolve().parent

class StartupStoryTest(unittest.TestCase):
    def setUp(self):
        self.story = Image.open(ROOT / 'tests/fixtures/startup-story-1009.png').convert('L')
        self.city = Image.open(ROOT / 'artifacts/stamina-ready.png').convert('L')
        self.api = object.__new__(desktop.DesktopApi)
        self.api._launch_wakeup = Mock()
        self.api._launch_wakeup.wait.return_value = False
        self.api._append = Mock()

    def test_observed_story_requires_both_markers(self):
        self.assertEqual(desktop.startup_story_skip_target(self.story), (950, 280))
        for box in ((845, 235, 1045, 326), (940, 2210, 1030, 2310)):
            frame = self.story.copy()
            frame.paste(0, box)
            self.assertIsNone(desktop.startup_story_skip_target(frame))

    def test_city_other_popups_and_wrong_resolution_are_rejected(self):
        self.assertIsNone(desktop.startup_story_skip_target(self.city))
        self.assertIsNone(desktop.startup_story_skip_target(self.story.resize((540, 1170))))
        for name in ('startup-5556-before.png', 'network-disconnected-1008.png'):
            self.assertIsNone(desktop.startup_story_skip_target(Image.open(ROOT / 'artifacts' / name)))

    def test_story_skip_requires_fresh_confirmed_city(self):
        self.assertFalse(desktop.game_loaded(self.story))
        with patch.object(bot, 'game_foreground', return_value=True), \
             patch.object(bot, 'screenshot', side_effect=[self.story, self.city]), \
             patch.object(bot, 'adb') as adb, \
             patch.object(desktop.time, 'monotonic', side_effect=[0, 1, 2]):
            self.assertTrue(self.api._wait_game_loaded('emulator-test', timeout=10))
        adb.assert_called_once_with('emulator-test', 'shell', 'input', 'tap', '950', '280')

    def test_stuck_story_never_repeats_skip_or_declares_loaded(self):
        with patch.object(bot, 'game_foreground', return_value=True), \
             patch.object(bot, 'screenshot', return_value=self.story), \
             patch.object(bot, 'adb') as adb, \
             patch.object(desktop.time, 'monotonic', side_effect=[0, 1, 2, 2, 2, 11]):
            with self.assertRaisesRegex(RuntimeError, '加载超时'):
                self.api._wait_game_loaded('emulator-test', timeout=10)
        self.assertEqual(adb.call_count, 1)

    def test_mandatory_guide_never_counts_as_loaded_or_starts_navigation(self):
        guide = Image.open(ROOT / 'tests/fixtures/startup-guide-1009.png').convert('L')
        self.assertTrue(desktop.startup_guide_blocked(guide))
        self.assertFalse(desktop.game_loaded(guide))
        self.assertFalse(desktop.startup_guide_blocked(self.city))
        self.assertFalse(desktop.startup_guide_blocked(self.story))
        shifted = Image.new('L', guide.size)
        shifted.paste(guide, (8, -12))
        self.assertTrue(desktop.startup_guide_blocked(shifted))
        with patch.object(bot, 'game_foreground', return_value=True), \
             patch.object(bot, 'screenshot', return_value=guide), \
             patch.object(bot, 'adb') as adb, \
             patch.object(desktop.time, 'monotonic', side_effect=[0, 1]):
            with self.assertRaises(desktop.StartupGuideBlocked):
                self.api._wait_game_loaded('emulator-test')
            adb.assert_not_called()

    def test_guide_does_not_trigger_repeated_cold_launches(self):
        self.api._closed = False
        self.api._wait_game_loaded = Mock(side_effect=desktop.StartupGuideBlocked('guide'))
        with patch('emulator_runtime.adb_output', side_effect=[
                'package:installed', 'Events injected: 1',
                'mResumedActivity: com.gof.china/main']) as adb:
            with self.assertRaises(desktop.StartupGuideBlocked):
                self.api._open_game({'serial': 'emulator-test', 'name': 'test'})
        self.assertEqual(adb.call_count, 3)
        self.api._wait_game_loaded.assert_called_once()

    def test_background_game_and_cancellation_never_tap(self):
        with patch.object(bot, 'game_foreground', return_value=False), \
             patch.object(bot, 'screenshot') as capture, \
             patch.object(bot, 'adb') as adb, \
             patch.object(desktop.time, 'monotonic', side_effect=[0, 1, 1, 1, 11]):
            with self.assertRaises(RuntimeError):
                self.api._wait_game_loaded('emulator-test', timeout=10)
            capture.assert_not_called()
            adb.assert_not_called()
        self.api._launch_wakeup.wait.return_value = True
        with patch.object(bot, 'adb') as adb, \
             patch.object(desktop.time, 'monotonic', side_effect=[0, 1]):
            self.assertFalse(self.api._wait_game_loaded('emulator-test'))
            adb.assert_not_called()

if __name__ == '__main__':
    unittest.main()
