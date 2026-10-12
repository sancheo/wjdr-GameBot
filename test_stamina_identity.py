"""Stamina deadlines follow AVD identity, including old-state migration."""

import json
import tempfile
import unittest

import stamina_state as state


class StaminaIdentityTest(unittest.TestCase):
    def tearDown(self):
        for serial in ("old", "new", "other"):
            state.clear_device_identity(serial)

    def test_port_change_preserves_deadline_and_other_avds_stay_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            state.set_device_identity("old", "avd:A")
            state.save_refresh(directory, "old", 1000, 300)
            state.set_device_identity("new", "avd:A")
            state.set_device_identity("other", "avd:B")
            self.assertEqual(state.load_refresh(directory, "new"), 1300)
            self.assertIsNone(state.load_refresh(directory, "other"))

    def test_legacy_migration_preserves_exact_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state.state_path(directory, "old")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"version": 1, "device": "old", "refresh_at": 1234}), encoding="utf-8")
            state.set_device_identity("old", "avd:A")
            self.assertEqual(state.load_refresh(directory, "old"), 1234)
            self.assertTrue(path.exists())
            state.set_device_identity("new", "avd:A")
            self.assertEqual(state.load_refresh(directory, "new"), 1234)

    def test_corrupt_legacy_record_is_not_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state.state_path(directory, "old")
            path.parent.mkdir(parents=True)
            path.write_text('{"version":1,"device":"wrong","refresh_at":1234}', encoding="utf-8")
            state.set_device_identity("old", "avd:A")
            with self.assertRaises(ValueError):
                state.load_refresh(directory, "old")
            self.assertFalse(state.state_path(directory, "avd:A").exists())
