"""Real-screen regressions for rally pages, hero identities and dispatch rules."""

import unittest
from unittest.mock import patch

from PIL import Image
import help_bot as bot
import bear_hunting as bear


class BearHuntingTest(unittest.TestCase):
    def frame(self, name):
        with Image.open(bot.ROOT / f"tests/fixtures/bear-{name}.png") as image:
            return image.convert("RGB")

    def test_rally_and_squad_are_distinct_confirmed_pages(self):
        self.assertTrue(bear.rally_page(bot, self.frame("rally")))
        self.assertFalse(bear.squad_page(bot, self.frame("rally")))
        for name in ("squad-default", "squad-empty", "squad-jasser", "squad-seoyoon"):
            self.assertTrue(bear.squad_page(bot, self.frame(name)), name)
        self.assertFalse(bear.squad_page(bot, self.frame("selection-empty")))
        self.assertFalse(bear.squad_page(bot, self.frame("war-mixed")))

    def test_join_requires_green_plus_and_bear_label_in_the_same_row(self):
        self.assertEqual(bear.bear_join_targets(bot, self.frame("war-joinable")), [(963, 722)])
        for name in ("war-mixed", "war-disabled", "war-scrolled-ice", "follow-squad"):
            self.assertEqual(bear.bear_join_targets(bot, self.frame(name)), [], name)

    def test_ice_proxy_squad_must_not_pass_real_bear_target_confirmation(self):
        image = self.frame("follow-squad")
        self.assertTrue(bear.squad_page(bot, image))
        self.assertFalse(bear.bear_squad_target(bot, image))
        # Only the target caption is mocked; the squad comes from the live join flow.
        recognize = bot.recognize_text
        def caption(crop):
            return [("目标：变异巨熊", (0, 0, 200, 50))] if crop.size == (630, 75) else recognize(crop)
        with patch.object(bot, "recognize_text", side_effect=caption):
            self.assertTrue(bear.bear_squad_target(bot, image))

    def test_joined_squad_rear_repair_and_shield_only_roster_samples(self):
        before, after = self.frame("follow-rear-forbidden"), self.frame("follow-rear-repaired")
        self.assertEqual(bear.hero_name(bot, before.crop(bear.FACE_BOX)), "jessie")
        self.assertEqual(bear.hero_name(bot, before.crop(bear.shifted(bear.FACE_BOX, 284))), "jasser")
        self.assertEqual(bear.hero_name(bot, after.crop(bear.FACE_BOX)), "jessie")
        for dx in bear.SLOT_OFFSETS[1:]:
            self.assertIsNone(bear.hero_name(bot, after.crop(bear.shifted(bear.FACE_BOX, dx))))
        shield = self.frame("selection-third-shield-only")
        self.assertEqual(bear.slots(bot, shield, True), [True, True, False])
        self.assertEqual(len(bear.cards(bot, shield)), 3)
        self.assertTrue(all(bear.card_hero(bot, shield, point) is None for point in bear.cards(bot, shield)))

    def test_three_first_heroes_and_forbidden_rear_hero(self):
        for name, expected in (("squad-default", "jessie"), ("squad-jasser", "jasser"),
                               ("squad-seoyoon", "seoyoon")):
            image = self.frame(name)
            self.assertEqual(bear.hero_name(bot, image.crop(bear.FACE_BOX)), expected)
            for dx in bear.SLOT_OFFSETS[1:]:
                self.assertIsNone(bear.hero_name(bot, image.crop(bear.shifted(bear.FACE_BOX, dx))))
        image = self.frame("squad-rear-forbidden")
        self.assertEqual(bear.hero_name(bot, image.crop(bear.shifted(bear.FACE_BOX, 284))), "jessie")

    def test_selected_candidate_and_scrolled_three_hero_icons(self):
        for name, expected in (("selection-empty", "jessie"), ("selection-jasser", "jasser"),
                               ("selection-auto-excluded", "jessie"), ("selection-auto-allowed", None)):
            image = self.frame(name)
            focused = bear.focused_card(bot, image)
            self.assertIsNotNone(focused, name)
            self.assertEqual(bear.card_hero(bot, image, focused), expected)
        image = self.frame("selection-scrolled")
        self.assertIn("seoyoon", [bear.card_hero(bot, image, point) for point in bear.cards(bot, image)])
        self.assertIsNone(bear.focused_card(bot, image))  # selected first row is off-screen

    def test_each_minus_has_observed_scope_and_no_hero_squad(self):
        self.assertEqual(bear.slots(bot, self.frame("remove-second")), [True, False, True])
        self.assertEqual(bear.slots(bot, self.frame("remove-third")), [True, False, False])
        self.assertEqual(bear.slots(bot, self.frame("squad-empty")), [False, False, False])

    def test_assignment_toast_is_retried_before_counting_slots(self):
        toast, settled = self.frame("selection-toast"), self.frame("selection-third")
        with self.assertRaises(RuntimeError):
            bear.slots(bot, toast, selection=True)
        with patch.object(bot, "task_screen", side_effect=[toast, settled]) as capture:
            actual = bear.wait_for(bot, "test", lambda frame: bear.slots(bot, frame, True) == [True, True, False],
                                   "not settled")
        self.assertIs(actual, settled)
        self.assertEqual(capture.call_count, 2)

    def test_correct_existing_squad_does_not_open_or_assign_heroes(self):
        image = self.frame("squad-default")
        with patch.object(bot, "task_tap") as tap, patch.object(bot, "task_screen") as capture:
            self.assertIs(bear.prepare_squad(bot, "test", image), image)
        tap.assert_not_called()
        capture.assert_not_called()

    def test_missing_candidate_keeps_empty_squad_without_assignment(self):
        initial = self.frame("remove-second")  # first hero is outside the allowed three
        empty, selection = self.frame("squad-empty"), self.frame("selection-empty")
        with (patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_screen", side_effect=[empty, selection, empty]),
              patch.object(bear, "choose", return_value=(selection, False)),
              patch.object(bot, "print")):
            result = bear.prepare_squad(bot, "test", initial)
        self.assertEqual(bear.slots(bot, result), [False, False, False])
        self.assertEqual([call.args[1:] for call in tap.call_args_list], [(356, 528), (254, 705), (960, 507)])


if __name__ == "__main__":
    unittest.main()
