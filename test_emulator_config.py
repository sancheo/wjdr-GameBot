"""Persistence checks shared by the desktop backend."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import emulator_config


class EmulatorConfigTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_atomic_save_failure_preserves_previous_file_and_cleans_temporary(self):
        key = "serial:127.0.0.1:62001/../unsafe"
        path = emulator_config.save_config(self.directory, key, "serial", {"options": {"help": True}})
        self.assertEqual(path.parent, self.directory / "configs")
        original = path.read_bytes()
        with patch.object(emulator_config.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                emulator_config.save_config(self.directory, key, "serial", {"options": {"help": False}})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_missing_and_unicode_configuration_round_trip(self):
        key = "avd:我的模拟器"
        self.assertEqual(emulator_config.load_config(self.directory, key), {})
        config = {"options": {"help": False}, "entries": {"tree_threshold": "3000"},
                  "resource_levels": {"iron": "8级矿"}}
        path = emulator_config.save_config(self.directory, key, "emulator-5554", config)
        self.assertEqual(emulator_config.load_config(self.directory, key), config)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["emulator"], key)
        self.assertEqual(emulator_config.load_config(self.directory, "avd:另一台模拟器"), {})

    def test_rejects_corrupted_schema_and_wrong_emulator(self):
        key = "avd:A"
        path = emulator_config.config_path(self.directory, key)
        path.parent.mkdir()
        for data in [[], {"version": 99}, {"version": 1, "emulator": "avd:B", "settings": {}},
                     {"version": 1, "emulator": key, "settings": {"options": []}}]:
            with self.subTest(data=data):
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    emulator_config.load_config(self.directory, key)

    def test_help_clicker_is_never_written_and_does_not_mutate_live_settings(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                settings = {"options": {"help_clicker": enabled, "help": False},
                            "entries": {"reconnect_minutes": "5"}}
                path = emulator_config.save_config(self.directory, "avd:A", "emulator-5554", settings)
                saved = json.loads(path.read_text(encoding="utf-8"))["settings"]
                self.assertEqual(saved, {"options": {"help": False},
                                         "entries": {"reconnect_minutes": "5"}})
                self.assertEqual(settings["options"]["help_clicker"], enabled)


if __name__ == "__main__":
    unittest.main()
