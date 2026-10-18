"""Real-screen regressions for rally pages, hero identities and dispatch rules."""

import unittest
from unittest.mock import Mock, call, patch

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

    def test_sampled_shortcuts_and_bear_detail_are_recognized(self):
        field, detail = self.frame("wilderness"), self.frame("detail")
        self.assertEqual(bear.icon_target(bot, field, "entry", (750, 1500, 930, 2110)), (835, 1985))
        self.assertEqual(bear.icon_target(bot, field, "war-entry", (920, 400, 1080, 1700)), (987, 951))
        self.assertEqual(bear.bear_detail_target(bot, detail), (692, 1798))
        self.assertIsNone(bear.bear_detail_target(bot, field))
        self.assertIsNone(bear.bear_detail_target(bot, self.frame("follow-squad")))

    def test_war_shortcut_survives_changed_badge_and_map_background(self):
        box = (920, 400, 1080, 1700)
        self.assertEqual(bear.icon_target(bot, self.frame("wilderness-5556-active-war"), "war-entry", box),
                         (987, 951))
        for name in ("squad-5556-plain-title", "war-join-race-5556", "selection-empty"):
            self.assertIsNone(bear.icon_target(bot, self.frame(name), "war-entry", box), name)

    def test_driver_navigates_from_sampled_entry_to_confirmed_bear_rally(self):
        field, detail, rally = self.frame("wilderness"), self.frame("detail"), self.frame("rally")
        with (patch.object(bot, "task_tap") as tap,
              patch.object(bot, "town_button", return_value=True),
              patch.object(bot, "task_screen", side_effect=[field, field, detail, rally]),
              patch.object(bear, "start_rally", return_value=(field, True)) as start):
            result, issued = bear.drive_rally(bot, "test", field)
        self.assertTrue(issued)
        self.assertIs(result, field)
        self.assertEqual(tap.call_args_list, [call("test", 835, 1985), call("test", 540, 1030),
                                             call("test", 692, 1798)])
        start.assert_called_once_with(bot, "test", rally)

    def test_follow_refuses_changed_non_bear_target_before_squad_preparation(self):
        war, squad = self.frame("war-joinable"), self.frame("follow-squad")
        with (patch.object(bear, "bear_join_targets", return_value=[(963, 722)]),
              patch.object(bot, "recognize_text", return_value=[("队长甲", ())]),
              patch.object(bot, "task_tap"), patch.object(bot, "task_back") as back,
              patch.object(bot, "task_screen", side_effect=[squad, war]),
              patch.object(bear, "squad_page", return_value=True),
              patch.object(bear, "bear_squad_target", return_value=False),
              patch.object(bear, "dispatch") as dispatch):
            result, issued = bear.follow_rally(bot, "test", war, max_attempts=1)
        self.assertFalse(issued)
        self.assertIs(result, war)
        back.assert_called_once_with("test")
        dispatch.assert_not_called()

    def test_driver_rechecks_bear_target_before_dispatch(self):
        with (patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_screen", return_value=self.frame("follow-squad")),
              patch.object(bear, "bear_squad_target", return_value=False),
              patch.object(bear, "dispatch") as dispatch,
              self.assertRaisesRegex(RuntimeError, "未确认出征目标为变异巨熊")):
            bear.start_rally(bot, "test", self.frame("rally"))
        tap.assert_called_once_with("test", 540, 1538)
        dispatch.assert_not_called()

    def test_join_requires_green_plus_and_bear_label_in_the_same_row(self):
        self.assertEqual(bear.bear_join_targets(bot, self.frame("war-joinable")), [(963, 722)])
        for name in ("war-mixed", "war-disabled", "war-scrolled-ice", "follow-squad"):
            self.assertEqual(bear.bear_join_targets(bot, self.frame(name)), [], name)

    def test_ice_proxy_squad_must_not_pass_real_bear_target_confirmation(self):
        image = self.frame("follow-squad")
        self.assertTrue(bear.squad_page(bot, image))
        self.assertFalse(bear.bear_squad_target(bot, image))
        self.assertFalse(bear.bear_squad_target(bot, image, verified_entry=True))
        # Only the target caption is mocked; the squad comes from the live join flow.
        recognize = bot.recognize_text
        def caption(crop):
            return [("目标：变异巨熊", (0, 0, 200, 50))] if crop.size == (630, 75) else recognize(crop)
        with patch.object(bot, "recognize_text", side_effect=caption):
            self.assertTrue(bear.bear_squad_target(bot, image))

    def test_plain_bear_dispatch_title_requires_a_verified_bear_entry(self):
        image = self.frame("squad-5556-plain-title")
        self.assertTrue(bear.squad_page(bot, image))
        self.assertFalse(bear.bear_squad_target(bot, image))
        self.assertTrue(bear.bear_squad_target(bot, image, verified_entry=True))

    def test_startup_unwinds_existing_bear_squad_and_war_list(self):
        squad, war = self.frame("squad-5556-plain-title"), self.frame("war-join-race-5556")
        field = self.frame("wilderness")
        with (patch.object(bot, "task_screen", side_effect=[squad, war, field]),
              patch.object(bot, "task_back") as back):
            result = bear.leave_bear_pages(bot, "test")
        self.assertIs(result, field)
        self.assertEqual(back.call_args_list, [call("test"), call("test")])

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

    def test_live_war_bonus_row_offsets_hero_controls(self):
        image = self.frame("squad-war-bonus")
        self.assertEqual(bear.squad_layout(bot, image), (91, [True, True, True]))
        self.assertTrue(bear.squad_page(bot, image))
        # Removing the lead hero clears the entire squad, as on the live screen.
        empty = self.frame("squad-empty")
        with (patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_screen", return_value=empty)):
            self.assertIs(bear.remove(bot, "test", image, 0), empty)
        tap.assert_called_once_with("test", 356, 619)

    def test_live_war_title_variant_still_rejects_disabled_join_buttons(self):
        image = self.frame("war-live-disabled")
        self.assertTrue(bear.war_page(bot, image))
        self.assertEqual(bear.bear_join_targets(bot, image), [])

    def test_follower_success_can_return_to_alliance_war_list(self):
        squad, war = self.frame("squad-default"), self.frame("war-live-disabled")
        target = bear.dispatch_target(bot, squad)
        with (patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_screen", return_value=war),
              patch.object(bot.hunting, "notice", return_value=None)):
            result, issued = bear.dispatch(bot, "test", squad, flow="follow")
        self.assertTrue(issued)
        self.assertIs(result, war)
        tap.assert_called_once_with("test", *target)

    def test_troop_limit_confirmation_exits_current_rally_without_changing_troops(self):
        squad, popup = self.frame("squad-default"), self.frame("troop-limit")
        war = self.frame("war-live-disabled")
        self.assertEqual(bear.troop_limit_confirmation(bot, popup), (540, 1460))
        events = Mock()
        with (patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_back") as back,
              patch.object(bot, "task_screen", side_effect=[popup, squad, war]),
              patch.object(bot, "print")):
            events.attach_mock(tap, "tap")
            events.attach_mock(back, "back")
            result, issued = bear.dispatch(bot, "test", squad, flow="follow")
        self.assertFalse(issued)
        self.assertIs(result, war)
        self.assertEqual(events.mock_calls,
                         [call.tap("test", *bear.dispatch_target(bot, squad)),
                          call.tap("test", 540, 1460), call.back("test")])

    def test_follow_retries_another_leader_even_when_rejected_row_moves(self):
        war, squad = self.frame("war-joinable"), self.frame("squad-default")
        with (patch.object(bear, "bear_join_targets", side_effect=[[(963, 722)], [(963, 1333), (963, 722)]]),
              patch.object(bot, "recognize_text", side_effect=[[("队长甲", ())], [("队长甲", ())], [("队长乙", ())]]),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_screen", return_value=squad),
              patch.object(bear, "bear_squad_target", return_value=True),
              patch.object(bear, "dispatch", side_effect=[(war, False), (war, True)]) as dispatch,
              patch.object(bot, "print"),
              patch.object(bear, "squad_page", return_value=True)):
            result, issued = bear.follow_rally(bot, "test", war)
        self.assertTrue(issued)
        self.assertIs(result, war)
        self.assertEqual(tap.call_args_list, [call("test", 963, 722), call("test", 963, 722)])
        self.assertEqual(dispatch.call_count, 2)

    def test_join_rejection_keeps_scanning_instead_of_timing_out(self):
        war = self.frame("war-join-race-5556")
        with (patch.object(bot, "task_screen", return_value=war) as capture,
              patch.object(bear, "war_page", return_value=True),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "adb") as adb,
              patch.object(bear, "bear_join_targets", side_effect=[[(963, 722)], []]),
              patch.object(bot, "recognize_text", return_value=[("队长甲", ())]),
              patch.object(bear, "dispatch") as dispatch,
              patch.object(bot, "print")):
            result, issued = bear.follow_rally(bot, "test", war, max_attempts=2)
        self.assertFalse(issued)
        self.assertIs(result, war)
        self.assertEqual(capture.call_count, 3)
        tap.assert_called_once_with("test", 963, 722)
        dispatch.assert_not_called()
        adb.assert_not_called()

    def test_join_waits_through_loading_but_accepts_the_actual_squad(self):
        war, squad = self.frame("war-join-race-5556"), self.frame("squad-default")
        loading = Image.new("RGB", (1080, 2340))
        with patch.object(bot, "task_screen", side_effect=[war, loading, war, squad]):
            result, entered = bear.wait_for_join(bot, "test")
        self.assertTrue(entered)
        self.assertIs(result, squad)

    def test_short_screen_dispatch_footer_maps_back_to_input(self):
        image = self.frame("squad-default")
        scene = Image.new("RGB", (1080, 2340))
        with (patch.object(bot, "intelligence_text_target", side_effect=[None, (820, 2020)]),
              patch("screen_geometry.scene_view", return_value=(scene, lambda x, y: (x, y + 100), None))):
            self.assertEqual(bear.dispatch_target(bot, image), (820, 2120))

    def test_follow_returns_when_join_falls_back_to_wilderness(self):
        war, field = self.frame("war-joinable"), self.frame("wilderness")
        with (patch.object(bear, "war_page", side_effect=[True, False]),
              patch.object(bear, "bear_join_targets", return_value=[(963, 722)]),
              patch.object(bot, "recognize_text", return_value=[("队长甲", ())]),
              patch.object(bot, "task_tap"), patch.object(bot, "print"),
              patch.object(bear, "wait_for_join", return_value=(field, False)),
              patch.object(bear, "dispatch") as dispatch):
            image, issued = bear.follow_rally(bot, "test", war, flow="follow_no_heroes")
        self.assertIs(image, field)
        self.assertFalse(issued)
        dispatch.assert_not_called()

    def test_join_returned_to_wilderness_retries_without_dispatch(self):
        field = self.frame("wilderness")
        with (patch.object(bot, "task_screen", return_value=field),
              patch.object(bear, "squad_page", return_value=False),
              patch.object(bear, "war_page", return_value=False),
              patch.object(bot, "town_button", return_value=True),
              patch.object(bear, "bear_detail_target", return_value=None),
              patch.object(bot, "print")):
            image, entered = bear.wait_for_join(bot, "test")
        self.assertFalse(entered)
        self.assertIs(image, field)

    def test_unknown_join_page_keeps_error_and_failure_screenshot(self):
        unknown = Image.new("RGB", (1080, 2340))
        with (patch.object(bot, "task_screen", return_value=unknown),
              patch.object(bear, "squad_page", return_value=False),
              patch.object(bear, "war_page", return_value=False),
              patch.object(bot, "town_button", return_value=False),
              patch.object(bot, "save_task_failure", return_value="failure.png") as save,
              self.assertRaisesRegex(RuntimeError, "未进入编队界面") as caught):
            bear.wait_for_join(bot, "test")
        self.assertEqual(caught.exception.diagnostic_screenshot, "failure.png")
        save.assert_called_once()

    def test_follow_scrolls_for_another_joinable_rally(self):
        war, squad = self.frame("war-joinable"), self.frame("squad-default")
        with (patch.object(bear, "bear_join_targets", side_effect=[[], [(963, 722)]]),
              patch.object(bot, "recognize_text", return_value=[("队长乙", ())]),
              patch.object(bot, "adb") as adb,
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_screen", side_effect=[war, squad]),
              patch.object(bear, "bear_squad_target", return_value=True),
              patch.object(bear, "dispatch", return_value=(war, True)),
              patch.object(bot, "print"),
              patch.object(bear, "squad_page", return_value=True)):
            result, issued = bear.follow_rally(bot, "test", war)
        self.assertTrue(issued)
        self.assertIs(result, war)
        adb.assert_called_once_with("test", "shell", "input", "swipe", "550", "1840", "550", "740", "450")
        tap.assert_called_once_with("test", 963, 722)

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

    def test_nox_short_screen_hero_controls(self):
        from screen_geometry import ScreenAdapter
        with Image.open(bot.ROOT / "tests/fixtures/bear-nox-squad-native.png") as native:
            image = ScreenAdapter().frame("nox-test", native.convert("RGB"))
        controls = bear.adapted_hero_controls(bot, image)
        self.assertEqual([state for state, _ in controls], [True, True, False])
        self.assertTrue(bear.squad_page(bot, image))

    def test_no_hero_follow_clears_all_with_first_minus(self):
        initial, empty = self.frame("squad-default"), self.frame("squad-empty")
        dy, _ = bear.squad_layout(bot, initial)
        with (patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_screen", return_value=empty),
              patch.object(bear, "hero_name") as identify):
            self.assertIs(bear.prepare_squad(bot, "test", initial, flow="follow_no_heroes"), empty)
        tap.assert_called_once_with("test", 356, 528 + dy)
        identify.assert_not_called()
        with patch.object(bot, "task_tap") as tap:
            self.assertIs(bear.prepare_squad(bot, "test", empty, flow="follow_no_heroes"), empty)
        tap.assert_not_called()

    def test_driver_keeps_unrestricted_heroes_without_recognizing_or_assigning(self):
        for name in ("remove-second", "squad-rear-forbidden", "squad-empty"):
            with self.subTest(name=name):
                image = self.frame(name)
                with (patch.object(bot, "task_tap") as tap,
                      patch.object(bot, "task_screen") as capture,
                      patch.object(bear, "hero_name") as identify):
                    self.assertIs(bear.prepare_squad(bot, "test", image, flow="drive"), image)
                tap.assert_not_called()
                capture.assert_not_called()
                identify.assert_not_called()

    def test_start_rally_dispatches_unrestricted_squad_directly(self):
        rally, squad = self.frame("rally"), self.frame("remove-second")
        wilderness = Image.new("RGB", (1080, 2340))
        target = bear.dispatch_target(bot, squad)
        self.assertIsNotNone(target)
        with (patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_screen", side_effect=[squad, wilderness]),
              patch.object(bear, "bear_squad_target", return_value=True),
              patch.object(bot, "town_button", return_value=True),
              patch.object(bot, "gather_search_page", return_value=False),
              patch.object(bot.hunting, "notice", return_value=None),
              patch.object(bear, "hero_name") as identify):
            result, issued = bear.start_rally(bot, "test", rally)
        self.assertTrue(issued)
        self.assertIs(result, wilderness)
        self.assertEqual([call.args[1:] for call in tap.call_args_list], [(540, 1538), target])
        identify.assert_not_called()

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

    def test_follower_dispatches_without_any_heroes_when_lead_candidate_missing(self):
        initial = self.frame("remove-second")
        empty, selection = self.frame("squad-empty"), self.frame("selection-empty")
        wilderness = Image.new("RGB", (1080, 2340))
        target = bear.dispatch_target(bot, empty)
        self.assertIsNotNone(target)
        with (patch.object(bot, "task_tap") as tap,
              patch.object(bot, "task_screen", side_effect=[empty, selection, empty, wilderness]),
              patch.object(bear, "choose", return_value=(selection, False)),
              patch.object(bot, "town_button", return_value=True),
              patch.object(bot, "gather_search_page", return_value=False),
              patch.object(bot.hunting, "notice", return_value=None),
              patch.object(bot, "print")):
            result, issued = bear.dispatch(bot, "test", initial, flow="follow")
        self.assertTrue(issued)
        self.assertIs(result, wilderness)
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(356, 528), (254, 705), (960, 507), target])


if __name__ == "__main__":
    unittest.main()
