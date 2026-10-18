"""Stop requests preempt retries, ADB actions and long waits without game access."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

import gui_web
import help_bot as bot
import runtime_control as control
from test_help_bot import Clock


class RuntimeControlTest(unittest.TestCase):
    def tearDown(self):
        control.activate(None)

    def test_cli_rejects_nonfinite_values_before_any_device_operation(self):
        for flag, value in (("--interval", "nan"), ("--interval", "inf"), ("--reconnect-minutes", "nan")):
            with (self.subTest(flag=flag, value=value),
                  patch.object(bot.sys, "argv", ["help_bot.py", flag, value]),
                  patch.object(bot, "connected_device") as discover,
                  contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised):
                bot.main()
            self.assertEqual(raised.exception.code, 2)
            discover.assert_not_called()

    def test_main_restores_templates_and_settings_after_keyboard_interrupt(self):
        original = bot.TOWN_TEMPLATE, bot.RECONNECT_DELAY
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(bot, "ROOT", Path(directory)),
                  patch.object(bot.sys, "argv", ["help_bot.py", "--device", "test", "--reconnect-minutes", "0.5"]),
                  patch.object(bot.Image, "open", return_value=bot.Image.new("RGB", (32, 48))),
                  patch.object(bot, "adb", side_effect=KeyboardInterrupt),
                  contextlib.redirect_stdout(io.StringIO())):
                bot.main()
        self.assertIs(bot.TOWN_TEMPLATE, original[0])
        self.assertEqual(bot.RECONNECT_DELAY, original[1])

    def test_cli_discovers_authorized_phone_and_requires_selection_for_multiple_devices(self):
        for output, expected in [("List of devices attached\nPHONE123\tdevice\nPHONE456\tunauthorized\n", "PHONE123"),
                                 ("List of devices attached\nPHONE123\tdevice\nemulator-5554\tdevice\n", None)]:
            with self.subTest(output=output), patch.object(bot.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)):
                if expected:
                    self.assertEqual(bot.connected_device(), expected)
                else:
                    with self.assertRaisesRegex(RuntimeError, "--device"):
                        bot.connected_device()

    def test_main_accepts_phone_and_reaches_task_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(bot, "ROOT", Path(directory)),
                  patch.object(bot.sys, "argv", ["help_bot.py", "--device", "PHONE123", "--dry-run"]),
                  patch.object(bot.Image, "open", return_value=bot.Image.new("RGB", (32, 48))),
                  patch.object(bot, "adb", return_value=b"device\n"),
                  patch.object(bot, "run", side_effect=KeyboardInterrupt) as run,
                  contextlib.redirect_stdout(io.StringIO())):
                bot.main()
        self.assertEqual(run.call_args.args[0], "PHONE123")

    def test_stop_blocks_next_adb_action_and_bypasses_retry_diagnostics(self):
        state = control.RuntimeControl()
        control.activate(state)
        state.stop()
        with patch.object(bot.subprocess, "run") as run, patch.object(bot, "save_task_failure") as save:
            with self.assertRaises(control.StopRequested):
                bot.retry_once("test", lambda: bot.adb("test", "shell", "input", "tap", "1", "1"),
                               "test", None, None)
        run.assert_not_called()
        save.assert_not_called()

    def test_stop_interrupts_long_sleep(self):
        state = control.RuntimeControl()
        control.activate(state)
        ready, stopped = threading.Event(), threading.Event()

        def worker():
            ready.set()
            try:
                control.clock.sleep(3600)
            except control.StopRequested:
                stopped.set()

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(ready.wait(2))
        state.stop()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(stopped.is_set())

    def test_protocol_only_accepts_disabled_tasks_and_eof_stops(self):
        state = control.RuntimeControl()
        state.set_retryable({"treasure"})
        self.assertFalse(state.request_retry("train"))
        state.listen(io.StringIO('not-json\n[]\n' + json.dumps({"action": "retry", "task": "treasure"}) + '\n'))
        self.assertEqual(state.take_retries(), {"treasure"})
        self.assertTrue(state.stopped.is_set())

    def test_desktop_stop_closes_real_controlled_child_gracefully(self):
        code = '''import sys,threading
from runtime_control import RuntimeControl,activate,clock,StopRequested
c=RuntimeControl(); activate(c)
threading.Thread(target=c.listen,args=(sys.stdin,),daemon=True).start()
print("ready",flush=True)
try: clock.sleep(3600)
except StopRequested: print("cancelled",flush=True)
'''
        child = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=bot.ROOT,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            api = gui_web.DesktopApi(directory)
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                api._process, api._controlled_process = child, True
                reader = threading.Thread(target=api._read_process, args=(child,))
                reader.start()
                api.stop()
                api.stop()
                reader.join(5)
                self.assertFalse(reader.is_alive())
                self.assertEqual(child.returncode, 0)
                self.assertFalse(api.get_state()["running"])
                self.assertTrue(any("cancelled" in line["text"] for line in api.get_state()["logs"]))
            finally:
                api._close()
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)

    def test_stop_timeout_forces_termination(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("test", 18), 0]
        with tempfile.TemporaryDirectory() as directory:
            api = gui_web.DesktopApi(directory)
            api._wait_stop(process)
            process.terminate.assert_called_once()
            self.assertTrue(any("超时" in line["text"] for line in api.get_state()["logs"]))

    def test_retry_runs_only_disabled_task_and_preserves_other_deadlines(self):
        state, clock, calls = control.RuntimeControl(), Clock(), {"treasure": 0, "donate": 0}
        control.activate(state)

        def treasure():
            calls["treasure"] += 1
            if calls["treasure"] <= 4:
                raise RuntimeError("测试页面异常")

        def donate():
            calls["donate"] += 1

        def monitor(_, seconds, *args, **kwargs):
            if calls["treasure"] == 4:
                self.assertTrue(state.request_retry("treasure"))
                self.assertFalse(state.request_retry("donate"))
                clock.sleep(1)
            else:
                clock.sleep(seconds)

        with (patch.object(bot.time, "monotonic", clock.monotonic),
              patch.object(bot.time, "sleep", clock.sleep), patch.object(bot, "run", side_effect=monitor),
              patch.object(bot, "screenshot"), patch.object(bot, "forced_offline", return_value=False),
              patch.object(bot, "recover_task_city"), patch.object(bot, "save_task_failure", return_value="test"),
              contextlib.redirect_stdout(io.StringIO()) as output):
            bot.run_schedule("test", 100, 2, False, {"donate": donate, "treasure": treasure},
                             *([None] * 4), help_enabled=False)
        self.assertEqual(calls, {"treasure": 5, "donate": 1})
        events = [json.loads(line[len(control.EVENT_PREFIX):]) for line in output.getvalue().splitlines()
                  if line.startswith(control.EVENT_PREFIX)]
        statuses = [event["status"] for event in events if event["name"] == "treasure"]
        self.assertIn("disabled", statuses)
        self.assertEqual(statuses[-2:], ["running", "waiting"])
