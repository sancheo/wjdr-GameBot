"""Check that a forced logout cannot trigger an early reconnect tap."""

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import help_bot


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ReconnectTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent
        self.templates = tuple(Image.open(root / f"assets/emulator-{name}.png").convert("L")
                               for name in ("help-icon", "city-nav", "forced-text", "reconnect"))
        self.offline = Image.new("L", help_bot.EXPECTED_SIZE)
        self.offline.paste(self.templates[2], help_bot.OFFLINE_TEXT_BOX)
        self.offline.paste(self.templates[3], help_bot.RECONNECT_BOX)
        self.city = Image.new("L", help_bot.EXPECTED_SIZE)
        self.city.paste(self.templates[1], help_bot.NAV_BOX)

    def test_waits_ten_minutes_before_reconnecting(self):
        clock = Clock()
        taps = []

        def fake_adb(_serial, *args):
            taps.append((clock.now, args))

        def fake_screenshot(_serial):
            return self.city if taps else self.offline

        with (patch.object(help_bot.time, "monotonic", clock.monotonic),
              patch.object(help_bot.time, "sleep", clock.sleep),
              patch.object(help_bot, "screenshot", fake_screenshot),
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot, "adb", fake_adb),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run("test-device", 5, 1, False, 1,
                         *self.templates)

        self.assertEqual(len(taps), 1)
        self.assertGreaterEqual(taps[0][0] - 100, 600)
        self.assertEqual(taps[0][1][:3], ("shell", "input", "tap"))

    def test_cancels_reconnect_when_dialog_disappears(self):
        clock = Clock()
        taps = []

        def fake_screenshot(_serial):
            return self.city if clock.now >= 220 else self.offline

        with (patch.object(help_bot.time, "monotonic", clock.monotonic),
              patch.object(help_bot.time, "sleep", clock.sleep),
              patch.object(help_bot, "screenshot", fake_screenshot),
              patch.object(help_bot, "adb", lambda *args: taps.append(args)),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run("test-device", 5, 1, False, 1, *self.templates)

        self.assertEqual(taps, [])

    def test_disabled_reconnect_stops_without_tapping(self):
        with (patch.object(help_bot, "screenshot", return_value=self.offline),
              patch.object(help_bot, "adb") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            with self.assertRaisesRegex(RuntimeError, "自动重连已关闭"):
                help_bot.run("test-device", 5, 1, False, 1, *self.templates,
                             reconnect=False)
        tap.assert_not_called()

    def test_disabled_help_does_not_tap(self):
        clock = Clock()
        with (patch.object(help_bot.time, "monotonic", clock.monotonic),
              patch.object(help_bot.time, "sleep", clock.sleep),
              patch.object(help_bot, "screenshot", return_value=self.city),
              patch.object(help_bot, "inspect", return_value=(True, (0, 0, 0), 0)),
              patch.object(help_bot, "adb") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run("test-device", 2, 1, False, 1, *self.templates,
                         help_enabled=False)
        tap.assert_not_called()

    def test_one_minute_reconnect_setting(self):
        clock = Clock()
        taps = []

        def fake_adb(_serial, *args):
            taps.append((clock.now, args))

        with (patch.object(help_bot, "RECONNECT_DELAY", 60),
              patch.object(help_bot.time, "monotonic", clock.monotonic),
              patch.object(help_bot.time, "sleep", clock.sleep),
              patch.object(help_bot, "screenshot", side_effect=lambda _: self.city if taps else self.offline),
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot, "adb", fake_adb),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run("test-device", 5, 1, False, 1, *self.templates)

        self.assertEqual(len(taps), 1)
        self.assertGreaterEqual(taps[0][0] - 100, 60)
        self.assertEqual(taps[0][1][-2:], ("752", "1482"))

    def test_emulator_popup_matches_real_screenshot(self):
        root = Path(__file__).resolve().parent
        text = Image.open(root / "assets/emulator-forced-text.png").convert("L")
        button = Image.open(root / "assets/emulator-reconnect.png").convert("L")
        with (patch.object(help_bot, "EXPECTED_SIZE", (1080, 2340)),
              patch.object(help_bot, "OFFLINE_TEXT_BOX", help_bot.EMULATOR_OFFLINE_TEXT_BOX),
              patch.object(help_bot, "RECONNECT_BOX", help_bot.EMULATOR_RECONNECT_BOX)):
            popup = Image.open(root / "artifacts/reconnect-current-emulator.png").convert("L")
            city = Image.open(root / "artifacts/flow-dawn-exit.png").convert("L")
            self.assertEqual(help_bot.reconnect_target(popup, text, button), (752, 1482))
            self.assertIsNone(help_bot.reconnect_target(city, text, button))

    def test_popups_after_reconnect_have_safe_close_targets(self):
        root = Path(__file__).resolve().parent
        promo = Image.open(root / "artifacts/reconnect-after-emulator.png").convert("L")
        welcome = Image.open(root / "artifacts/reconnect-after-close.png").convert("L")
        city = Image.open(root / "artifacts/flow-dawn-exit.png").convert("L")
        promo_template = Image.open(root / "assets/emulator-reconnect-promo-close.png").convert("L")
        welcome_template = Image.open(root / "assets/emulator-reconnect-welcome-close.png").convert("L")
        with (patch.object(help_bot, "RECONNECT_PROMO_CLOSE", promo_template),
              patch.object(help_bot, "RECONNECT_WELCOME_CLOSE", welcome_template)):
            self.assertEqual(help_bot.reconnect_popup_target(promo), (915, 540))
            self.assertEqual(help_bot.reconnect_popup_target(welcome), (992, 650))
            self.assertIsNone(help_bot.reconnect_popup_target(city))

    def test_reconnect_finds_activity_close_at_new_position(self):
        root = Path(__file__).resolve().parent
        close = Image.open(root / "assets/emulator-reconnect-promo-close.png").convert("L")
        popup = Image.new("L", (1080, 2340))
        popup.paste(close, (820, 405))
        with patch.object(help_bot, "RECONNECT_PROMO_CLOSE", close):
            self.assertEqual(help_bot.reconnect_popup_target(popup), (855, 440))

    def test_reconnect_closes_popups_before_resuming(self):
        root = Path(__file__).resolve().parent
        offline = Image.open(root / "artifacts/reconnect-current-emulator.png").convert("L")
        promo = Image.open(root / "artifacts/reconnect-after-emulator.png").convert("L")
        welcome = Image.open(root / "artifacts/reconnect-after-close.png").convert("L")
        city = Image.open(root / "artifacts/reconnect-after-welcome-close.png").convert("L")
        text = Image.open(root / "assets/emulator-forced-text.png").convert("L")
        reconnect = Image.open(root / "assets/emulator-reconnect.png").convert("L")
        promo_close = Image.open(root / "assets/emulator-reconnect-promo-close.png").convert("L")
        welcome_close = Image.open(root / "assets/emulator-reconnect-welcome-close.png").convert("L")
        clock = Clock()
        taps = []
        frames = (offline, promo, welcome, city)

        with (patch.object(help_bot, "EXPECTED_SIZE", (1080, 2340)),
              patch.object(help_bot, "OFFLINE_TEXT_BOX", help_bot.EMULATOR_OFFLINE_TEXT_BOX),
              patch.object(help_bot, "RECONNECT_BOX", help_bot.EMULATOR_RECONNECT_BOX),
              patch.object(help_bot, "RECONNECT_PROMO_CLOSE", promo_close),
              patch.object(help_bot, "RECONNECT_WELCOME_CLOSE", welcome_close),
              patch.object(help_bot, "RECONNECT_DELAY", 60),
              patch.object(help_bot.time, "monotonic", clock.monotonic),
              patch.object(help_bot.time, "sleep", clock.sleep),
              patch.object(help_bot, "screenshot", side_effect=lambda _: frames[len(taps)]),
              patch.object(help_bot, "inspect", side_effect=lambda image, *_: (image is city, None, 0)),
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot, "adb", side_effect=lambda _serial, *args: taps.append(args)),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run("test-device", 5, 1, False, 1,
                         Image.new("L", (1, 1)), Image.new("L", (1, 1)), text, reconnect)

        self.assertEqual([(tap[-2], tap[-1]) for tap in taps],
                         [("752", "1482"), ("915", "540"), ("992", "650")])

    def test_reconnect_waits_for_late_activity_popup(self):
        root = Path(__file__).resolve().parent
        close = Image.open(root / "assets/emulator-reconnect-promo-close.png").convert("L")
        popup = Image.new("L", help_bot.EXPECTED_SIZE)
        popup.paste(close, (820, 405))
        post_frames = iter((self.city, popup, self.city, self.city, self.city))
        clock = Clock()
        taps = []

        def fake_screenshot(_serial):
            return next(post_frames) if taps else self.offline

        with (patch.object(help_bot, "RECONNECT_PROMO_CLOSE", close),
              patch.object(help_bot, "RECONNECT_DELAY", 60),
              patch.object(help_bot.time, "monotonic", clock.monotonic),
              patch.object(help_bot.time, "sleep", clock.sleep),
              patch.object(help_bot, "screenshot", fake_screenshot),
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot, "adb", side_effect=lambda _serial, *args: taps.append(args)),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run("test-device", 5, 1, False, 1, *self.templates)

        self.assertEqual([tap[-2:] for tap in taps], [("752", "1482"), ("855", "440")])

    def test_startup_recovers_from_activity_chat_and_wilderness(self):
        root = Path(__file__).resolve().parent
        frames = [Image.open(root / "artifacts" / f"{name}.png").convert("L") for name in (
            "reconnect-activity-current", "flow-chat-page", "wilderness-current",
            "flow-dawn-exit", "flow-dawn-exit", "flow-dawn-exit")]
        town = Image.open(root / "assets/emulator-town-button.png").convert("L")
        activity = Image.open(root / "assets/emulator-activity-close.png").convert("L")
        chat = Image.open(root / "assets/emulator-chat-title.png").convert("L")
        help_icon = Image.open(root / "assets/emulator-help-icon.png").convert("L")
        city_nav = Image.open(root / "assets/emulator-city-nav.png").convert("L")
        text, reconnect = (Image.open(root / f"assets/emulator-{name}.png").convert("L")
                           for name in ("forced-text", "reconnect"))
        taps = []
        with (patch.object(help_bot, "EXPECTED_SIZE", (1080, 2340)),
              patch.object(help_bot, "NAV_BOX", (70, 2195, 150, 2275)),
              patch.object(help_bot, "CHAT_TEMPLATE", chat),
              patch.object(help_bot, "ACTIVITY_CLOSE", activity),
              patch.object(help_bot, "TOWN_TEMPLATE", town),
              patch.object(help_bot, "screenshot", side_effect=frames),
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot, "adb", side_effect=lambda _serial, *args: taps.append(args)),
              patch.object(help_bot.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertFalse(help_bot.inspect(frames[2], help_icon, city_nav)[0])
            self.assertTrue(help_bot.return_to_city("test-device", help_icon, city_nav,
                                                     text, reconnect))
        self.assertEqual([tap[-2:] for tap in taps],
                         [("953", "387"), ("60", "190"), ("960", "2230")])

    def test_help_monitor_returns_from_chat_without_counting_a_help(self):
        root = Path(__file__).resolve().parent
        clock = Clock()
        city = Image.open(root / "artifacts/flow-dawn-exit.png").convert("L")
        chat = Image.open(root / "artifacts/flow-chat-page.png").convert("L")
        returned = Image.open(root / "artifacts/flow-chat-return.png").convert("L")
        frames = iter((city, chat, returned, returned))
        actions = []
        help_bot.CHAT_TEMPLATE = Image.open(root / "assets/emulator-chat-title.png").convert("L")
        try:
            with (patch.object(help_bot.time, "monotonic", clock.monotonic),
                  patch.object(help_bot.time, "sleep", clock.sleep),
                  patch.object(help_bot, "screenshot", side_effect=lambda _: next(frames)),
                  patch.object(help_bot, "forced_offline", return_value=False),
                  patch.object(help_bot, "inspect", side_effect=((True, (0, 0, 0), 0),
                                                                (True, (0, 0, 0), 0),
                                                                (True, (30, 0, 0), 0))),
                  patch.object(help_bot, "game_foreground", return_value=True),
                  patch.object(help_bot, "adb", lambda _serial, *args: actions.append(args)),
                  contextlib.redirect_stdout(io.StringIO()) as output):
                help_bot.run("test-device", 3, 1, False, float("inf"), *self.templates)
        finally:
            help_bot.CHAT_TEMPLATE = None
        self.assertEqual(actions[-1], ("shell", "input", "tap", "60", "190"))
        self.assertIn("共确认 0 次互助", output.getvalue())


class CityTaskTest(unittest.TestCase):
    def setUp(self):
        help_bot.TASK_DRAWER_SCROLL = 0
        priority = patch.object(help_bot, 'check_priority_help')
        priority.start()
        self.addCleanup(priority.stop)

    def test_tree_amount_parses_three_and_four_digit_counts(self):
        self.assertEqual(help_bot.parse_tree_amount("777/5,040"), 777)
        self.assertEqual(help_bot.parse_tree_amount("4,949/5,040"), 4949)
        self.assertIsNone(help_bot.parse_tree_amount("可收集数量未知"))

    def test_only_selected_training_unit_is_checked(self):
        image = Image.new("RGB", help_bot.EXPECTED_SIZE)
        with (patch.object(help_bot, "inspect", return_value=(True, None, 0)),
              patch.object(help_bot, "task_drawer", return_value=image),
              patch.object(help_bot, "task_screen", return_value=image),
              patch.object(help_bot, "task_page", return_value=True),
              patch.object(help_bot, "task_tap") as tap,
              patch.object(help_bot, "training_status", return_value="busy") as status,
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_training("test-device", None, None, None, {},
                                  initial_image=image, keep_open=True, units={"spear"})
        status.assert_called_once_with(image, 1225)
        tap.assert_not_called()

    def test_tree_threshold_skips_amount_below_limit(self):
        image = Image.new("RGB", help_bot.EXPECTED_SIZE)
        help_bot.TASK_DRAWER_SCROLL = help_bot.DRAWER_SWIPES
        with (patch.object(help_bot, "inspect", return_value=(True, None, 0)),
              patch.object(help_bot, "task_drawer", return_value=image),
              patch.object(help_bot, "find_green", return_value=(.9, 200, 1000)),
              patch.object(help_bot, "tree_amount", return_value=4984),
              patch.object(help_bot, "task_page", return_value=False),
              patch.object(help_bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_rewards("test-device", None, None, None, None, None, None,
                                 initial_image=image, warehouse=False, dawn=False,
                                 tree_threshold=5000)
        tap.assert_not_called()

    def test_disabled_donation_skips_its_label(self):
        image = Image.new("RGB", help_bot.EXPECTED_SIZE)
        help_bot.TASK_DRAWER_SCROLL = 1
        with (patch.object(help_bot, "inspect", return_value=(True, None, 0)),
              patch.object(help_bot, "task_drawer", return_value=image),
              patch.object(help_bot, "task_page", return_value=True),
              patch.object(help_bot, "find_green", return_value=(0, 0, 0)) as find_label,
              patch.object(help_bot, "recognize_text", return_value=[("高级招募", (10, 100, 150, 35))]),
              patch.object(help_bot, "swipe_drawer", return_value=image),
              patch.object(help_bot, "task_screen", return_value=image),
              patch.object(help_bot, "task_tap"),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_city_tasks("test-device", None, None,
                                    {"free-label": image, "drawer-open": image},
                                    initial_image=image, donate=False)
        self.assertEqual(find_label.call_count, 1)
        self.assertIs(find_label.call_args.args[1], image)

    def test_task_help_restores_drawer_after_click(self):
        before, after, restored = (Image.new("RGB", (1080, 2340)) for _ in range(3))
        frames = iter((before, after, restored))
        actions = []
        help_bot.TASK_HELP = (Image.new("L", (82, 85)), Image.new("L", (80, 80)), before)
        try:
            with (patch.object(help_bot, "color_screenshot", side_effect=lambda _: next(frames)),
                  patch.object(help_bot, "inspect", side_effect=((True, (0, 0, 0), 0),
                                                                (True, (30, 0, 0), 0))),
                  patch.object(help_bot, "task_page", side_effect=(True, False, True)),
                  patch.object(help_bot, "game_foreground", return_value=True),
                  patch.object(help_bot.time, "sleep"),
                  patch.object(help_bot, "adb", lambda _serial, *args: actions.append(args)),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertIs(help_bot.task_screen("test-device"), restored)
        finally:
            help_bot.TASK_HELP = None
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[1], ("shell", "input", "tap", "20", "1100"))

    def test_chat_page_is_recognized_and_help_tap_returns_to_city(self):
        root = Path(__file__).resolve().parent
        before = Image.open(root / "artifacts/flow-dawn-exit.png").convert("RGB")
        chat = Image.open(root / "artifacts/flow-chat-page.png").convert("RGB")
        after = Image.open(root / "artifacts/flow-chat-return.png").convert("RGB")
        help_bot.CHAT_TEMPLATE = Image.open(root / "assets/emulator-chat-title.png").convert("L")
        help_bot.TASK_HELP = (Image.new("L", (82, 85)), Image.new("L", (80, 80)),
                              Image.open(root / "assets/emulator-drawer-open.png").convert("RGB"))
        actions = []
        try:
            self.assertTrue(help_bot.chat_page(chat))
            self.assertFalse(help_bot.chat_page(before))
            with (patch.object(help_bot, "color_screenshot", side_effect=(before, chat, after)),
                  patch.object(help_bot, "inspect", side_effect=((True, (0, 0, 0), 0),
                                                                (True, (0, 0, 0), 0),
                                                                (True, (0, 0, 0), 0))),
                  patch.object(help_bot, "game_foreground", return_value=True),
                  patch.object(help_bot.time, "sleep"),
                  patch.object(help_bot, "adb", lambda _serial, *args: actions.append(args)),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertIs(help_bot.task_screen("test-device"), after)
        finally:
            help_bot.CHAT_TEMPLATE = None
            help_bot.TASK_HELP = None
        self.assertEqual(actions[-1], ("shell", "input", "tap", "60", "190"))

    def test_task_help_restores_drawer_scroll_position(self):
        frames = [Image.new("RGB", (1080, 2340)) for _ in range(5)]
        help_bot.TASK_DRAWER_SCROLL = 2
        help_bot.TASK_HELP = (Image.new("L", (1, 1)), Image.new("L", (1, 1)), frames[0])
        actions = []
        try:
            with (patch.object(help_bot, "color_screenshot", side_effect=frames),
                  patch.object(help_bot, "inspect", side_effect=((True, (0, 0, 0), 0),
                                                                (True, (30, 0, 0), 0))),
                  patch.object(help_bot, "task_page", side_effect=(True, False, True, True, True)),
                  patch.object(help_bot, "game_foreground", return_value=True),
                  patch.object(help_bot.time, "sleep"),
                  patch.object(help_bot, "adb", lambda _serial, *args: actions.append(args)),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertIs(help_bot.task_screen("test-device"), frames[-1])
        finally:
            help_bot.TASK_HELP = None
        self.assertEqual(sum(action[2] == "swipe" for action in actions), 2)

    def test_busy_training_keeps_drawer_open_for_next_task(self):
        root = Path(__file__).resolve().parent
        city = Image.open(root / "artifacts/flow-chat-return.png").convert("RGB")
        drawer = Image.open(root / "artifacts/drawer-flow-top.png").convert("RGB")
        help_icon = Image.open(root / "assets/emulator-help-icon.png").convert("L")
        nav = Image.open(root / "assets/emulator-city-nav.png").convert("L")
        drawer_template = Image.open(root / "assets/emulator-drawer-open.png").convert("RGB")
        titles = {unit: Image.open(root / "assets" / f"emulator-training-{unit}-title.png").convert("L")
                  for unit, _ in help_bot.TRAIN_ROWS}
        actions = []
        with (patch.object(help_bot, "EXPECTED_SIZE", (1080, 2340)),
              patch.object(help_bot, "HELP_BOX", (748, 2070, 830, 2155)),
              patch.object(help_bot, "NAV_BOX", (70, 2195, 150, 2275)),
              patch.object(help_bot, "color_screenshot", return_value=city),
              patch.object(help_bot, "task_screen", side_effect=(drawer, drawer)),
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot, "adb", lambda _serial, *args: actions.append(args)),
              contextlib.redirect_stdout(io.StringIO())):
            result = help_bot.run_training("test-device", help_icon, nav, drawer_template,
                                           titles, keep_open=True)
        self.assertIs(result, drawer)
        self.assertEqual(len(actions), 1)
        self.assertNotIn(("shell", "input", "tap", "695", "1100"), actions)

    def test_daily_schedule_checks_tasks_every_ten_minutes(self):
        clock = Clock()
        task_times = []
        help_waits = []

        def task():
            self.assertIsNotNone(help_bot.TASK_HELP)
            task_times.append(clock.now)

        def help_run(_serial, seconds, _interval, _dry_run, max_clicks, *_templates, **_options):
            help_waits.append((clock.now, seconds, max_clicks))
            clock.sleep(seconds)

        with (patch.object(help_bot.time, "monotonic", clock.monotonic),
              patch.object(help_bot, "run", side_effect=help_run)):
            help_bot.run_schedule("test-device", 1201, 2, False, [task],
                                  *(Image.new("L", (1, 1)) for _ in range(4)))

        self.assertEqual(task_times, [100, 700, 1300])
        self.assertEqual([wait[1] for wait in help_waits], [600, 600, 1])
        self.assertTrue(all(wait[2] == float("inf") for wait in help_waits))
        self.assertIsNone(help_bot.TASK_HELP)

    def test_task_exception_reconnects_only_when_popup_is_confirmed(self):
        clock = Clock()
        attempts = []
        waits = []
        events = []

        def task():
            events.append('task')
            attempts.append(clock.now)
            if len(attempts) == 1:
                raise ValueError("页面已变化")

        def fake_run(_serial, seconds, *_args, **_options):
            waits.append(seconds)
            clock.sleep(60 if seconds == 1 and len(attempts) == 1 else seconds)

        with (patch.object(help_bot.time, "monotonic", clock.monotonic),
              patch.object(help_bot, "screenshot", return_value=Image.new("L", (1, 1))),
              patch.object(help_bot, "forced_offline", return_value=True),
              patch.object(help_bot, "inspect", return_value=(True, None, 0)),
              patch.object(help_bot, "check_priority_help", side_effect=lambda *_: events.append('help')),
              patch.object(help_bot, "run", side_effect=fake_run),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_schedule("test-device", 2, 1, False, [task],
                                  *(Image.new("L", (1, 1)) for _ in range(4)))

        self.assertEqual(attempts, [100, 160])
        self.assertEqual(events, ['help', 'task', 'help', 'task'])
        self.assertEqual(waits, [1, 2])
        self.assertIsNone(help_bot.TASK_HELP)

        clock = Clock()
        with (patch.object(help_bot.time, "monotonic", clock.monotonic),
              patch.object(help_bot, "screenshot", return_value=Image.new("L", (1, 1))),
              patch.object(help_bot, "forced_offline", return_value=False),
              patch.object(help_bot, "recover_task_city"),
              patch.object(help_bot.time, "sleep", clock.sleep),
              contextlib.redirect_stdout(io.StringIO()) as output):
            help_bot.run_schedule("test-device", 2, 1, False,
                                  [lambda: (_ for _ in ()).throw(RuntimeError("页面已变化"))],
                                  *(Image.new("L", (1, 1)) for _ in range(4)))
        self.assertIn("⚠️ 0 已连续执行失败3次，后续不再执行这个任务", output.getvalue())

    def test_explore_claims_income_before_drawer_tasks(self):
        root = Path(__file__).resolve().parent
        frame = lambda name: Image.open(root / "artifacts" / f"{name}.png").convert("RGB")
        frames = [frame(f"explore-step{n}") for n in (1, 2, 3, 4, 5, 6)]
        templates = {name: Image.open(root / f"assets/emulator-explore-{name}.png").convert("L")
                     for name in help_bot.EXPLORE_BOXES}
        actions = []
        with (patch.object(help_bot, "color_screenshot", return_value=frames[0]),
              patch.object(help_bot, "task_screen", side_effect=frames[1:]),
              patch.object(help_bot, "inspect", return_value=(True, None, 0)),
              patch.object(help_bot, "task_tap", side_effect=lambda _serial, x, y: actions.append((x, y))),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_explore("test-device", None, None, templates)
        self.assertEqual(actions, [(95, 2240), (915, 1718), (540, 1660), (540, 2050), (60, 190)])
        self.assertTrue(help_bot.explore_red_dot(frames[0]))
        self.assertFalse(help_bot.explore_red_dot(frames[5]))

        output = io.StringIO()
        with (patch.object(help_bot, "color_screenshot", return_value=frames[5]),
              patch.object(help_bot, "inspect", return_value=(True, None, 0)),
              patch.object(help_bot, "task_tap") as tap,
              contextlib.redirect_stdout(output)):
            help_bot.run_explore("test-device", None, None, templates)
        tap.assert_not_called()
        self.assertIn("探险经验：未发现可领取红点", output.getvalue())

    def test_warehouse_prompt_disappears_after_claim(self):
        root = Path(__file__).resolve().parent
        template = Image.open(root / "assets/emulator-warehouse-title.png").convert("RGB")
        for name, expected in (("warehouse-tree-drawer", True),
                               ("warehouse-after-drawer", False)):
            image = Image.open(root / "artifacts" / f"{name}.png").convert("RGB")
            score, _, _ = help_bot.find_white(image, template, range(240, 351, 5),
                                              range(650, 1360, 5))
            self.assertEqual(score >= 0.85, expected)

    def test_tree_above_threshold_collects_and_returns_to_city(self):
        root = Path(__file__).resolve().parent
        frame = lambda name: Image.open(root / "artifacts" / f"{name}.png").convert("RGB")
        drawer = frame("warehouse-after-drawer")
        island_before = frame("tree-after-drawer")
        island_after = frame("tree-page")
        city = frame("tree-return-city")
        frames = iter((*(drawer for _ in range(help_bot.DRAWER_SWIPES)), drawer, drawer, island_before, island_after,
                       city, *(drawer for _ in range(3)), city))
        asset = lambda name, mode: Image.open(root / "assets" / f"emulator-{name}.png").convert(mode)
        actions = []
        with (patch.object(help_bot, "EXPECTED_SIZE", (1080, 2340)),
              patch.object(help_bot, "HELP_BOX", (748, 2070, 830, 2155)),
              patch.object(help_bot, "NAV_BOX", (70, 2195, 150, 2275)),
              patch.object(help_bot, "color_screenshot", return_value=drawer),
              patch.object(help_bot, "task_screen", side_effect=lambda _: next(frames)),
              patch.object(help_bot, "wait_city_after_island", side_effect=lambda *_: next(frames)),
              patch.object(help_bot, "task_drawer", side_effect=lambda _serial, image, _templates: image),
              patch.object(help_bot, "tree_amount", return_value=4984),
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot, "adb"),
              patch.object(help_bot, "task_tap", side_effect=lambda _serial, x, y: actions.append((x, y))),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_rewards("test-device", asset("help-icon", "L"),
                                 asset("city-nav", "L"), asset("drawer-open", "RGB"),
                                 asset("warehouse-title", "RGB"),
                                 asset("tree-collect-label", "RGB"),
                                 asset("island-title", "L"))

        self.assertEqual(actions[-3:][0], (608, 1195))
        self.assertEqual(actions[-1], (60, 190))
        self.assertTrue(480 <= actions[-2][0] <= 580 and 680 <= actions[-2][1] <= 780)

    def test_warehouse_and_dawn_reward_are_claimed(self):
        root = Path(__file__).resolve().parent
        frame = lambda name: Image.open(root / "artifacts" / f"{name}.png").convert("RGB")
        drawer = frame("drawer-flow-3")
        frames = iter(frame(name) for name in (
            "flow-warehouse-entry", "flow-warehouse-reward", "flow-warehouse-can",
            "drawer-flow-top",
            "drawer-flow-1", "drawer-flow-1", "drawer-flow-2",
            "drawer-flow-2", "drawer-flow-3", "drawer-flow-3", "flow-dawn-entry",
            "flow-dawn-claimed", "flow-dawn-after-wait", "flow-dawn-exit",
        ))
        asset = lambda name, mode: Image.open(root / "assets" / f"emulator-{name}.png").convert(mode)
        actions = []
        help_bot.TASK_DRAWER_SCROLL = help_bot.DRAWER_SWIPES
        with (patch.object(help_bot, "EXPECTED_SIZE", (1080, 2340)),
              patch.object(help_bot, "HELP_BOX", (748, 2070, 830, 2155)),
              patch.object(help_bot, "NAV_BOX", (70, 2195, 150, 2275)),
              patch.object(help_bot, "task_screen", side_effect=lambda _: next(frames)),
              patch.object(help_bot, "tree_amount", return_value=0),
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot, "adb"),
              patch.object(help_bot, "task_tap", side_effect=lambda _serial, x, y: actions.append((x, y))),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_rewards("test-device", asset("help-icon", "L"),
                                 asset("city-nav", "L"), asset("drawer-open", "RGB"),
                                 asset("warehouse-title", "RGB"),
                                 asset("tree-collect-label", "RGB"),
                                 asset("island-title", "L"), initial_image=drawer)

        self.assertIn((530, 1650), actions)  # 晨曦回礼弹窗的领取按钮
        self.assertEqual(actions[-1], (530, 1900))

    def test_training_rows_distinguish_completed_idle_and_busy(self):
        root = Path(__file__).resolve().parent
        for name, expected in (
            ("resource-drawer", ("idle", "completed", "completed")),
            ("training-shield-drawer", ("idle", "idle", "busy")),
            ("training-spear-drawer", ("busy", "idle", "busy")),
        ):
            image = Image.open(root / "artifacts" / f"{name}.png").convert("RGB")
            actual = tuple(help_bot.training_status(image, y) for _, y in help_bot.TRAIN_ROWS)
            self.assertEqual(actual, expected)

    def test_partly_filled_training_bar_is_busy(self):
        image = Image.new("RGB", help_bot.EXPECTED_SIZE, (100, 100, 100))
        y = help_bot.TRAIN_ROWS[0][1]
        image.paste((20, 180, 30), (125, y + 5, 335, y + 40))
        self.assertEqual(help_bot.training_status(image, y), "busy")

    def test_drawer_arrow_survives_moving_city_background(self):
        root = Path(__file__).resolve().parent
        template = Image.open(root / "assets/emulator-drawer-open.png").convert("RGB")
        open_drawer = Image.open(root / "artifacts/city-tasks-failure-current.png").convert("RGB")
        closed_drawer = Image.open(root / "artifacts/current-2026-09-25.png").convert("RGB")
        self.assertTrue(help_bot.task_page(open_drawer, {"drawer-open": template}, "drawer-open"))
        self.assertFalse(help_bot.task_page(closed_drawer, {"drawer-open": template}, "drawer-open"))

    def test_drawer_opens_only_when_closed_and_syncs_unknown_startup_position(self):
        root = Path(__file__).resolve().parent
        templates = {"drawer-open": Image.open(root / "assets/emulator-drawer-open.png").convert("RGB")}
        opened = Image.open(root / "artifacts/feature-drawer-middle.png").convert("RGB")
        closed = Image.open(root / "artifacts/feature-return.png").convert("RGB")
        for initial, scroll, opens, swipes in ((closed, None, 1, 0), (opened, 2, 0, 0),
                                              (opened, None, 0, help_bot.DRAWER_SWIPES)):
            with (self.subTest(scroll=scroll, opens=opens),
                  patch.object(help_bot, "TASK_DRAWER_SCROLL", scroll),
                  patch.object(help_bot, "task_screen", return_value=opened),
                  patch.object(help_bot, "game_foreground", return_value=True),
                  patch.object(help_bot, "task_tap") as tap,
                  patch.object(help_bot, "adb") as adb):
                self.assertIs(help_bot.task_drawer("test", initial, templates), opened)
                self.assertEqual(tap.call_count, opens)
                self.assertEqual(adb.call_count, swipes)
                self.assertEqual(help_bot.TASK_DRAWER_SCROLL, 2 if scroll == 2 else 0)
                if opens:
                    tap.assert_called_once_with("test", 20, 1100)

    def test_donation_detects_visible_label_before_swiping(self):
        root = Path(__file__).resolve().parent
        frame = Image.open(root / "artifacts/feature-drawer-middle.png").convert("RGB")
        templates = {name: Image.open(root / f"assets/emulator-{name}.png").convert("RGB")
                     for name in ("drawer-open", "donate-label")}
        with patch.object(help_bot, "swipe_drawer") as swipe:
            image, x, y = help_bot.task_label("test", frame, templates, "donate-label")
        self.assertIs(image, frame)
        self.assertIsNotNone(y)
        swipe.assert_not_called()

    def test_recruit_relocates_row_when_entry_tap_leaves_drawer_open(self):
        root = Path(__file__).resolve().parent
        frame = lambda name: Image.open(root / f"artifacts/{name}.png").convert("RGB")
        entry = frame("feature-recruit-entry")
        missed = frame("feature-drawer-middle")
        frames = [missed, missed, frame("feature-recruit"), frame("feature-recruit-after"),
                  frame("feature-recruit-exit"), frame("feature-return")]
        templates = {name: Image.open(root / f"assets/emulator-{name}.png").convert("RGB")
                     for name in (*help_bot.TASK_BOXES, "free-label")}
        with (patch.object(help_bot, "inspect", return_value=(True, None, 0)),
              patch.object(help_bot, "task_screen", side_effect=frames),
              patch.object(help_bot, "task_tap") as tap,
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_city_tasks("test", None, None, templates, initial_image=entry, donate=False)
        entries = [call.args[1:] for call in tap.call_args_list if call.args[1] == 600]
        self.assertEqual(len(entries), 2)
        self.assertTrue(920 <= entries[0][1] <= 940)
        self.assertTrue(1020 <= entries[1][1] <= 1040)
        self.assertIn(("test", 280, 1690), [call.args for call in tap.call_args_list])

    def test_tech_detail_ignores_changing_tech_name(self):
        root = Path(__file__).resolve().parent
        template = Image.open(root / "assets/emulator-tech-detail.png").convert("RGB")
        old_detail = Image.open(root / "artifacts/hold-before.png").convert("RGB")
        new_detail = Image.open(root / "artifacts/tech-detail-failure-current.png").convert("RGB")
        tech_list = Image.open(root / "artifacts/feature-tech.png").convert("RGB")
        templates = {"tech-detail": template}
        self.assertTrue(help_bot.task_page(old_detail, templates, "tech-detail"))
        self.assertTrue(help_bot.task_page(new_detail, templates, "tech-detail"))
        self.assertFalse(help_bot.task_page(tech_list, templates, "tech-detail"))

    def test_missing_labels_return_to_city_without_spending(self):
        root = Path(__file__).resolve().parent
        city = Image.open(root / "artifacts/feature-return.png").convert("RGB")
        drawer = Image.open(root / "artifacts/feature-drawer-top.png").convert("RGB")
        frames = [city, *([drawer] * (1 + 3 * help_bot.DRAWER_SWIPES)), city]
        templates = {name: Image.open(root / "assets" / f"emulator-{name}.png").convert("RGB")
                     for name in (*help_bot.TASK_BOXES, "donate-label", "free-label", "thumb")}
        help_icon = Image.open(root / "assets/emulator-help-icon.png").convert("L")
        city_nav = Image.open(root / "assets/emulator-city-nav.png").convert("L")
        actions = []
        with (patch.object(help_bot, "EXPECTED_SIZE", (1080, 2340)),
              patch.object(help_bot, "HELP_BOX", (748, 2070, 830, 2155)),
              patch.object(help_bot, "NAV_BOX", (70, 2195, 150, 2275)),
              patch.object(help_bot, "color_screenshot", side_effect=frames) as screenshots,
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot.time, "sleep"),
              patch.object(help_bot, "adb", lambda _serial, *args: actions.append(args)),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_city_tasks("test-device", help_icon, city_nav, templates)

        self.assertEqual(screenshots.call_count, len(frames))
        self.assertNotIn(("shell", "input", "tap", "760", "1810"), actions)
        self.assertNotIn(("shell", "input", "tap", "280", "1690"), actions)
        self.assertEqual(actions[-1], ("shell", "input", "tap", "695", "1100"))

    def test_donate_and_recruit_return_to_city(self):
        root = Path(__file__).resolve().parent
        frames = [Image.open(root / "artifacts" / f"{name}.png").convert("RGB") for name in (
            "feature-return", "feature-drawer-top", "feature-drawer-middle",
            "feature-alliance", "feature-tech", "hold-before", "hold-after-2s",
            "hold-exhausted",
            "feature-tech", "feature-alliance", "feature-return", "feature-drawer-top",
            "feature-recruit-entry", "feature-recruit", "feature-recruit",  # 招募动画尚未结束
            "feature-recruit-after", "feature-recruit-exit", "feature-return",
        )]
        templates = {name: Image.open(root / "assets" / f"emulator-{name}.png").convert("RGB")
                     for name in (*help_bot.TASK_BOXES, "donate-label", "free-label", "thumb")}
        help_icon = Image.open(root / "assets/emulator-help-icon.png").convert("L")
        city_nav = Image.open(root / "assets/emulator-city-nav.png").convert("L")
        actions = []
        with (patch.object(help_bot, "EXPECTED_SIZE", (1080, 2340)),
              patch.object(help_bot, "HELP_BOX", (748, 2070, 830, 2155)),
              patch.object(help_bot, "NAV_BOX", (70, 2195, 150, 2275)),
              patch.object(help_bot, "color_screenshot", side_effect=frames) as screenshots,
              patch.object(help_bot, "game_foreground", return_value=True),
              patch.object(help_bot.time, "sleep"),
              patch.object(help_bot, "adb", lambda _serial, *args: actions.append(args)),
              contextlib.redirect_stdout(io.StringIO())):
            help_bot.run_city_tasks("test-device", help_icon, city_nav, templates)

        self.assertEqual(screenshots.call_count, len(frames))
        self.assertEqual(actions.count(("shell", "input", "swipe", "760", "1810",
                                        "760", "1810", "3000")), 2)
        self.assertNotIn(("shell", "input", "tap", "760", "1810"), actions)
        self.assertIn(("shell", "input", "tap", "280", "1690"), actions)
        self.assertEqual(actions[-1], ("shell", "input", "tap", "60", "190"))


if __name__ == "__main__":
    unittest.main()
