"""Retention and session isolation checks without persistent test artifacts."""

from datetime import datetime, timedelta
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import runtime_logs as logs


class RuntimeLogsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_runtime_and_exception_are_separate_and_traceback_is_complete(self):
        session = logs.SessionLogs(self.root, "avd:Pixel_5")
        for line in ("normal output", "Traceback (most recent call last):",
                     '  File "bot.py", line 10', "ValueError: broken", "normal again"):
            session.append(line)
        paths = session.paths()
        runtime = paths["runtime"].read_text(encoding="utf-8")
        exception = paths["exception"].read_text(encoding="utf-8")
        self.assertIn("normal output", runtime)
        self.assertIn("bot.py", exception)
        self.assertIn("ValueError: broken", exception)
        self.assertNotIn("normal", exception)
        self.assertTrue(paths["runtime"].name.startswith(session.session))

    def test_devices_cannot_collide_and_cross_midnight_keeps_start_filename(self):
        self.assertNotEqual(logs.emulator_directory("serial:a:b"), logs.emulator_directory("serial:a_b"))
        started = datetime(2026, 10, 7, 23, 59, 0)
        session = logs.SessionLogs(self.root, "avd:Pixel_5", started)
        with patch.object(logs, "datetime") as clock:
            clock.now.return_value = started
            session.append("before midnight")
            first = session.paths()
            clock.now.return_value = started + timedelta(minutes=2)
            session.append("after midnight")
            second = session.paths()
        self.assertEqual(first["runtime"].name, second["runtime"].name)
        self.assertNotEqual(first["runtime"].parent, second["runtime"].parent)
        self.assertIn("after midnight", second["runtime"].read_text(encoding="utf-8"))

    def test_retention_uses_file_age_and_removes_empty_folders_only(self):
        old = self.root / "logs" / "old-device" / "2026-09-29"
        old.mkdir(parents=True)
        expired = old / "20260929-010000-000000-runtime.log"
        expired.write_text("expired", encoding="utf-8")
        now = datetime.now().timestamp()
        os.utime(expired, (now - logs.RETENTION_SECONDS - 1,) * 2)
        keep = self.root / "logs" / "keep-device" / "2026-10-07"
        keep.mkdir(parents=True)
        recent = keep / "20261007-010000-000000-runtime.log"
        recent.write_text("recent", encoding="utf-8")
        custom = keep / "notes.txt"
        custom.write_text("user file", encoding="utf-8")
        logs.cleanup_logs(self.root, now=now)
        self.assertFalse(old.parent.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(custom.exists())

    def test_capture_buffers_partial_lines_and_restores_streams(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdout", output), patch.object(sys, "stderr", errors):
            with logs.capture_console(self.root, "avd:Pixel_5") as session:
                sys.stdout.write("partial")
                sys.stdout.write(" line\n")
                sys.stderr.write("failure details")
            self.assertIs(sys.stdout, output)
            self.assertIs(sys.stderr, errors)
        paths = session.paths()
        self.assertIn("partial line", paths["runtime"].read_text(encoding="utf-8"))
        self.assertIn("failure details", paths["exception"].read_text(encoding="utf-8"))

    def test_retry_exception_is_saved_even_before_retry_exhaustion(self):
        session = logs.SessionLogs(self.root, "avd:Pixel_5")
        session.append("启动时主城检查 首次异常：等待超时；10 秒后重试")
        self.assertIn("首次异常", session.paths()["exception"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
