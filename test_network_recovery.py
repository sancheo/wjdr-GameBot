"""Regressions for the October 8 network interruption and frozen map."""
import contextlib
import io
import unittest
from unittest.mock import patch
from PIL import Image
import help_bot as bot
from test_help_bot import Clock
from test_runtime_recovery import asset, frame


def failure(name):
    with Image.open(bot.ROOT / f"artifacts/{name}.png") as im:
        return im.convert("L")


class NetworkRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.network = failure("network-disconnected-1008")
        self.map = failure("frozen-map-1008")
        self.city = frame("stamina-ready").convert("L")
        self.clock = Clock()
        self.enterContext(patch.object(bot.time, "monotonic", self.clock.monotonic))
        self.enterContext(patch.object(bot.time, "sleep", self.clock.sleep))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def recover(self):
        return bot.return_to_city("test", asset("help-icon"), asset("city-nav"), asset("forced-text"), asset("reconnect"))

    def test_network_requires_all_three_regions_and_is_not_forced_logout(self):
        self.assertEqual(bot.network_reconnect_target(self.network), (780, 1480))
        self.assertFalse(bot.forced_offline(self.network, asset("forced-text"), asset("reconnect")))
        for box in ((465,925,615,980), (90,1180,960,1230), (675,1435,885,1500)):
            missing = self.network.copy()
            missing.paste(0, box)
            self.assertIsNone(bot.network_reconnect_target(missing))
        for im in (self.city, self.map, frame("reconnect-current-emulator").convert("L")):
            self.assertIsNone(bot.network_reconnect_target(im))

    def test_network_reconnect_waits_for_confirmed_city(self):
        with (patch.object(bot, "screenshot", side_effect=[self.network, self.city, self.city, self.city]),
              patch.object(bot, "task_tap") as tap):
            self.assertTrue(self.recover())
        tap.assert_called_once_with("test", 780, 1480)

    def test_network_reconnect_is_bounded_and_spaced(self):
        times=[]
        with (patch.object(bot, "screenshot", return_value=self.network),
              patch.object(bot, "task_tap", side_effect=lambda *args: times.append(self.clock.now)),
              patch.object(bot, "adb") as adb,
              self.assertRaisesRegex(RuntimeError, "仍未确认主城")):
            self.recover()
        self.assertEqual(len(times), 3)
        self.assertTrue(all(b-a >= 10 for a,b in zip(times,times[1:])))
        adb.assert_not_called()

    def test_monitor_dry_run_leaves_network_dialog_untouched(self):
        with (patch.object(bot, "screenshot", return_value=self.network),
              patch.object(bot, "return_to_city") as recovery, patch.object(bot, "adb") as adb):
            bot.run("test", 10, 1, True, 1, asset("help-icon"), asset("city-nav"), asset("forced-text"), asset("reconnect"))
        recovery.assert_not_called()
        adb.assert_not_called()

    def test_frozen_map_reopens_once_and_confirms_city(self):
        restarted=[]
        def capture(_):
            return self.city if restarted else self.map
        with (patch.object(bot, "TOWN_TEMPLATE", asset("town-button")),
              patch.object(bot, "screenshot", side_effect=capture),
              patch.object(bot, "task_tap"), patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb", side_effect=lambda *a: restarted.append(a))):
            self.assertTrue(self.recover())
        self.assertEqual(restarted, [("test", "shell", "am", "force-stop", "com.gof.china"),
                                    ("test", "shell", "monkey", "-p", "com.gof.china", "-c", "android.intent.category.LAUNCHER", "1")])

    def test_map_that_changes_does_not_trigger_frozen_reopen(self):
        def capture(_):
            im=self.map.copy()
            im.paste(min(255, int(self.clock.now)), (300, 400, 600, 700))
            return im
        with (patch.object(bot, "TOWN_TEMPLATE", asset("town-button")),
              patch.object(bot, "screenshot", side_effect=capture),
              patch.object(bot, "task_tap"), patch.object(bot, "adb") as adb,
              self.assertRaisesRegex(RuntimeError, "仍未确认主城")):
            self.recover()
        adb.assert_not_called()

    def test_frozen_reopen_failure_stops_after_one_attempt(self):
        with (patch.object(bot, "TOWN_TEMPLATE", asset("town-button")),
              patch.object(bot, "screenshot", return_value=self.map),
              patch.object(bot, "task_tap"), patch.object(bot, "game_foreground", return_value=True),
              patch.object(bot, "adb") as adb,
              self.assertRaisesRegex(RuntimeError, "180 秒内仍未确认主城")):
            self.recover()
        self.assertEqual(adb.call_count, 2)

if __name__ == "__main__":
    unittest.main()
