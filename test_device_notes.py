"""Notes survive restarts and never rewrite device task settings."""

import tempfile
import unittest

from device_notes import load_note, note_path, save_note


class DeviceNotesTest(unittest.TestCase):
    def test_notes_round_trip_isolate_devices_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(load_note(root, "avd:A"), "")
            save_note(root, "avd:A", "  主号  ")
            save_note(root, "serial:PHONE123", "小号 <手机>")
            self.assertEqual(load_note(root, "avd:A"), "主号")
            self.assertEqual(load_note(root, "serial:PHONE123"), "小号 <手机>")
            save_note(root, "avd:A", "")
            self.assertEqual(load_note(root, "avd:A"), "")
            self.assertEqual(load_note(root, "serial:PHONE123"), "小号 <手机>")

    def test_invalid_input_never_overwrites_existing_note(self):
        with tempfile.TemporaryDirectory() as root:
            save_note(root, "avd:A", "主号")
            for value in (None, 1, "x" * 81, "a\nb", "a\x00b"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    save_note(root, "avd:A", value)
                self.assertEqual(load_note(root, "avd:A"), "主号")

    def test_corrupt_or_mismatched_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            save_note(root, "avd:A", "主号")
            path = note_path(root, "avd:A")
            for value in ('[]', '{broken', '{"version":1,"key":"avd:B","note":"wrong"}'):
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_note(root, "avd:A")
