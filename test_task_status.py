"""Structured state is validated, bounded, isolated from logs and retryable."""

import json
import tempfile
import unittest
from unittest.mock import Mock

from gui_web import DesktopApi
from task_status import validate_task_event


class TaskStatusTest(unittest.TestCase):
    def test_bear_events_are_visible_in_desktop_status(self):
        for status in ("running", "waiting", "retrying", "stopped", "failed"):
            event = validate_task_event({"kind": "task", "name": "bear", "status": status})
            self.assertEqual(event["label"], "自动打熊")

    def test_help_clicker_events_have_a_visible_chinese_label(self):
        for status in ("running", "retrying", "stopped"):
            event = validate_task_event({"kind": "task", "name": "help_clicker", "status": status})
            self.assertEqual(event["label"], "互助连点")
            self.assertEqual(event["status"], status)

    def test_rejects_malformed_or_nonfinite_events(self):
        event = {"kind": "task", "name": "treasure", "status": "disabled", "reason": "页面异常"}
        self.assertIsNotNone(validate_task_event(event))
        for field, value in (("name", []), ("status", {}), ("name", "unknown"), ("next_run", float("nan")),
                             ("next_run", True), ("reason", "x" * 4097)):
            with self.subTest(field=field, value=type(value).__name__):
                self.assertIsNone(validate_task_event({**event, field: value}))

    def test_retry_request_uses_control_pipe_and_does_not_reset_other_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            api._process = Mock()
            api._controlled_process = True
            for name, status in (("treasure", "disabled"), ("train", "waiting")):
                api._receive_task_event(json.dumps({"kind": "task", "name": name, "status": status,
                                                   "next_run": 9000 if name == "train" else None}))
            state = api.retry_task("treasure")
            api._process.stdin.write.assert_called_once_with('{"action": "retry", "task": "treasure"}\n')
            self.assertEqual(state["task_status"][1]["next_run"], 9000)
            with self.assertRaises(ValueError):
                api.retry_task("treasure")
            with self.assertRaises(ValueError):
                api.retry_task("train")
            state["task_status"][0]["reason"] = "mutation"
            self.assertNotEqual(api.get_state()["task_status"][0]["reason"], "mutation")
            api._process = None

    def test_bad_state_message_does_not_break_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            for payload in ("not-json", "[]", '{"kind":"task","name":[],"status":"disabled"}', "x" * 16385):
                api._receive_task_event(payload)
            self.assertEqual(api.get_state()["task_status"], [])
            self.assertEqual(len(api.get_state()["logs"]), 4)
