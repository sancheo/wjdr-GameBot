"""Verify the actual Nox sample source, crops, and centered drawer geometry."""
import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import help_bot as bot
from screen_geometry import ScreenAdapter, scene_view


class NoxSamplesTest(unittest.TestCase):
    def test_nox_stamina_icon_and_centered_reward_are_recognized(self):
        adapter = ScreenAdapter()
        with Image.open(bot.ROOT / "tests/fixtures/nox/midday-state-1315-native.png") as native:
            frame = adapter.frame("small", native.convert("RGB"))
        self.assertIsNotNone(bot.stamina_target(frame, bot.warehouse_position(frame)))
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-132727-232061/tour-stamina-002-native.png") as native:
            reward = adapter.frame("small", native.convert("RGB"))
        self.assertTrue(bot.stamina_page(reward))
        self.assertEqual(bot.stamina_countdown(reward), 19945)
        self.assertEqual(adapter.geometry("small").point(530, 1700), (530, 1422))

    def test_six_unavailable_pets_is_a_normal_condition(self):
        path = bot.ROOT / "tests/fixtures/nox/treasure-pets-insufficient-native.png"
        with Image.open(path) as native:
            frame = ScreenAdapter().frame("small-nox", native.convert("RGB"))
        self.assertIsNotNone(bot.treasure_dispatch_close(frame))
        self.assertTrue(bot.treasure_pets_unavailable(frame))
        hidden = frame.copy()
        hidden.paste("black", (70, 800, 1010, 1700))
        self.assertFalse(bot.treasure_pets_unavailable(hidden))

    def test_no_soldiers_prompt_below_old_crop_is_recognized(self):
        path = bot.ROOT / "tests/fixtures/nox/gather-no-soldiers-native.png"
        with Image.open(path) as native:
            frame = ScreenAdapter().frame("small-nox", native.convert("RGB"))
        self.assertEqual(bot.gather_notice(frame), "no_soldiers")

    def test_gather_taps_the_exact_action_instead_of_the_speed_buff(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-005424-373441/tour-gather-019.png") as source:
            frame = source.convert("RGB")
        target = bot.gather_action_target(frame)
        self.assertIsNotNone(target)
        self.assertTrue(1320 < target[1] < 1450)
        frame.paste("black", (390, 1320, 690, 1450))
        self.assertIsNone(bot.gather_action_target(frame))
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-005424-373441/tour-gather-021.png") as boost:
            self.assertEqual(bot.gather_back_target(boost), (60, 190))

    def test_intelligence_claim_button_is_bottom_anchored_without_stretched_text(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-005424-373441/tour-bounty-007-native.png") as native:
            frame = ScreenAdapter().frame("small-rewards", native.convert("RGB"))
        self.assertIsNotNone(bot.intelligence_text_target(frame, ("一键领取",), (280, 2000, 800, 2190)))

    def test_monster_card_does_not_read_the_queue_header_it_covers(self):
        root = bot.ROOT / "tests/fixtures/nox/20261018-004038-288735"
        with Image.open(root / "tour-intelligence-013.png") as source:
            modal = source.convert("RGB")
        with Image.open(root / "tour-intelligence-010.png") as source:
            wilderness = source.convert("RGB")

        def target(frame, labels, box):
            if labels == ("前往查看",):
                return 540, 1650
            return (540, 1230) if frame is modal else None

        with (patch.object(bot, "bounty_name", return_value=None),
              patch.object(bot, "task_screen", side_effect=[modal, wilderness]),
              patch.object(bot, "task_tap") as tap,
              patch.object(bot, "time") as clock,
              patch.object(bot, "intelligence_text_target", side_effect=target),
              patch.object(bot, "stamina_refill_target", return_value=None),
              patch.object(bot, "intelligence_back_target", return_value=None),
              patch.object(bot, "town_button", return_value=True),
              patch.object(bot, "intelligence_queue_full", side_effect=AssertionError("occluded capacity"))):
            clock.monotonic.return_value = 0
            frame, issued = bot.execute_intelligence("test", modal)
        self.assertIs(frame, wilderness)
        self.assertTrue(issued)
        self.assertEqual(tap.call_count, 2)

    def test_intelligence_detail_button_keeps_its_centered_modal_position(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-002934-175024/tour-intelligence-068-native.png") as native:
            adapter = ScreenAdapter()
            frame = adapter.frame("small-detail", native.convert("RGB"))
        target = bot.intelligence_text_target(frame, ("前往查看",), (280, 1500, 800, 1770))
        self.assertIsNotNone(target)
        self.assertTrue(1350 <= adapter.geometry("small-detail").point(*target)[1] <= 1450)
        self.assertEqual(bot.intelligence_back_target(frame), (100, 1900))

    def test_recruit_result_exits_at_the_hint_without_tapping_recruit_again(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-002934-175024/tour-recruit_advanced-054.png") as frame:
            self.assertEqual(bot.recruit_result_exit_target(frame), (540, 2100))

    def test_training_portrait_upgrade_markers_stay_bottom_anchored(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-000159-489377/tour-train-035-native.png") as native:
            raw = native.convert("RGB")
        arrow = bot.open_template(bot.ROOT / "assets/emulator-training-upgrade-arrow.png").convert("RGB")
        changed_tier = raw.copy()
        changed_tier.paste("black", (400, 4, 540, 94))
        for source in (raw, changed_tier):
            frame = ScreenAdapter().frame("small-training", source)
            self.assertIsNotNone(bot.training_upgrade_target(frame, arrow))

    def test_wilderness_intelligence_and_pet_anchors_do_not_fold_the_map(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-000159-489377/tour-intelligence-080-native.png") as native:
            adapter = ScreenAdapter()
            frame = adapter.frame("small-wilderness", native.convert("RGB"))
        town = bot.open_template(bot.ROOT / "assets/emulator-town-button.png").convert("L")
        with patch.object(bot, "TOWN_TEMPLATE", town):
            target = bot.intelligence_entry_target(frame)
        self.assertIsNotNone(target)
        self.assertEqual(adapter.geometry("small-wilderness").point(*target), (996, 1294))

    def test_pet_icon_countdown_keeps_day_line_above_clock(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-000159-489377/tour-pet_transport-057.png") as frame:
            self.assertEqual(bot.countdown(frame, (545, 700, 755, 790)), 115665)

    def test_small_account_alliance_reward_exit_hint_uses_centered_panel_region(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-000159-489377/tour-treasure-019.png") as frame:
            target = bot.intelligence_text_target(frame, ("点击任意位置退出",), (250, 1850, 850, 2280))
        self.assertIsNotNone(target)
        self.assertLess(target[1], 2090)

    def test_small_account_tree_tutorial_collect_action_is_not_a_decoration(self):
        path = bot.ROOT / "tests/fixtures/nox/20261018-000159-489377/tour-tree-006.png"
        with Image.open(path) as frame:
            self.assertEqual(bot.selected_tree_collect_target(frame), (813, 1403))
            hidden = frame.copy()
            hidden.paste("black", (300, 850, 750, 1050))
            self.assertIsNone(bot.selected_tree_collect_target(hidden))
        with Image.open(bot.ROOT / "tests/fixtures/crystal-1015-pond.png") as frame:
            self.assertIsNone(bot.selected_tree_collect_target(frame))
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261018-002934-175024/tour-tree-009.png") as frame:
            self.assertIsNone(bot.selected_tree_collect_target(frame))

    def test_midnight_offer_requires_heading_and_exit_and_maps_to_native_exit(self):
        path = bot.ROOT / "tests/fixtures/nox/20261017-235622-645578/tour-tree-017-native.png"
        with Image.open(path) as native:
            adapter = ScreenAdapter()
            frame = adapter.frame("small-nox", native.convert("RGB"))
        self.assertEqual(bot.secret_choice_close_target(frame), (1005, 272))
        import gui_web
        self.assertTrue(gui_web.game_loaded(frame.convert("L")))
        self.assertEqual(adapter.geometry("small-nox").point(1005, 272), (1005, 136))
        for box in ((345, 230, 840, 320), (965, 230, 1045, 315)):
            hidden = frame.copy()
            hidden.paste("black", box)
            self.assertIsNone(bot.secret_choice_close_target(hidden))
        with Image.open(bot.ROOT / "tests/fixtures/nox/validation-second.png") as city:
            self.assertIsNone(bot.secret_choice_close_target(city))

    def test_small_account_warehouse_reward_keeps_bottom_anchored_countdown(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/20261017-235003-183736/tour-warehouse-030.png") as frame:
            self.assertEqual(bot.warehouse_reward_timer(frame), (True, 6448))

    def test_completed_treasure_modal_preserves_title_and_close(self):
        path = bot.ROOT / "tests/fixtures/nox/20261018-012220-305963/tour-treasure-035-native.png"
        with Image.open(path) as native:
            adapter = ScreenAdapter()
            frame = adapter.frame("small-nox", native.convert("RGB"))
        self.assertIsNotNone(bot.intelligence_text_target(frame, ("寻宝完成",), (350, 680, 730, 810)))
        self.assertEqual(bot.treasure_back_target(frame), (960, 640))
        self.assertEqual(adapter.geometry("small-nox").point(960, 640), (960, 362))

    def test_main_account_completed_treasure_ignores_pet_background(self):
        path = bot.ROOT / "tests/fixtures/nox/treasure-completed-main-1019-native.png"
        with Image.open(path) as native:
            adapter = ScreenAdapter()
            frame = adapter.frame("main-nox", native.convert("RGB"))
            self.assertIsNotNone(bot.intelligence_text_target(
                frame, ("寻宝完成",), (350, 680, 730, 810)))
            self.assertEqual(bot.treasure_back_target(frame), (960, 640))
            self.assertEqual(adapter.geometry("main-nox").point(960, 640), (960, 362))
            self.assertEqual(adapter.geometry("main-nox").point(540, 1490), (540, 1212))
            hidden = native.convert("RGB")
            hidden.paste("black", (440, 400, 640, 475))
            cleared = ScreenAdapter().frame("main-nox", hidden)
        self.assertIsNone(bot.intelligence_text_target(
            cleared, ("寻宝完成",), (350, 680, 730, 810)))

    def test_treasure_dispatch_button_keeps_center_anchor(self):
        path = bot.ROOT / "tests/fixtures/nox/20261018-012649-271148/tour-treasure-019-native.png"
        with Image.open(path) as native:
            frame = ScreenAdapter().frame("small-nox", native.convert("RGB"))
        self.assertIsNotNone(bot.treasure_action_target(frame, "寻宝派遣"))

    def test_sampled_assets_are_exact_crops_of_actual_nox_frames(self):
        manifest = bot.ROOT / "assets/nox-samples.json"
        self.assertTrue(manifest.is_file())
        sources = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertTrue(sources)
        for name, entry in sources.items():
            with self.subTest(asset=name):
                with Image.open(bot.ROOT / entry["native"]) as native:
                    self.assertEqual(native.size, (1080, 1920))
                with Image.open(bot.ROOT / entry["normalized"]) as image:
                    self.assertEqual(image.size, (1080, 2340))
                    crop = np.asarray(image.crop(entry["box"]).convert("RGB"))
                with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as asset:
                    np.testing.assert_array_equal(crop, np.asarray(asset.convert("RGB")))

    def test_drawer_rows_keep_their_native_height(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/drawer-middle-native.png") as image:
            raw = image.convert("RGB")
        adapter = ScreenAdapter()
        image = adapter.frame("nox", raw)
        geometry = adapter.geometry("nox")
        for y in (900, 1200, 1400, 1550, 1600):
            self.assertEqual(geometry.point(400, y + 40)[1] - geometry.point(400, y)[1], 40)
        drawer = bot.open_template(bot.ROOT / "assets/emulator-drawer-open.png")
        self.assertTrue(bot.task_page(image, {"drawer-open": drawer}, "drawer-open"))
        target = bot.research_drawer_target(image)
        self.assertIsNotNone(target)
        self.assertTrue(1050 <= target[1] <= 1250)

    def test_actual_upgrading_warehouse_survives_hud_mapping(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/city-native.png") as image:
            raw = image.convert("RGB")
        adapter = ScreenAdapter()
        image = adapter.frame("nox", raw)
        offset = bot.warehouse_position(image)
        self.assertIsNotNone(offset)
        scene, to_logical, _ = scene_view(image)
        scene_offset = bot.warehouse_position(scene)
        target = to_logical(260 + scene_offset[0], 1330 + scene_offset[1])
        self.assertEqual((260 + offset[0], 1330 + offset[1]), target)
        native_x, native_y = adapter.geometry("nox").point(*target)
        # Independently visible construction facade in this Nox frame.
        self.assertTrue(280 <= native_x <= 350)
        self.assertTrue(1350 <= native_y <= 1450)

    def test_scene_target_mapping_round_trips_across_stretched_hud_bands(self):
        with Image.open(bot.ROOT / "tests/fixtures/nox/city-native.png") as image:
            raw = image.convert("RGB")
        adapter = ScreenAdapter()
        image = adapter.frame("nox", raw)
        _, to_logical, from_logical = scene_view(image)
        for y in (400, 700, 1000, 1400, 1700, 1900):
            x2, y2 = from_logical(*to_logical(500, y))
            self.assertAlmostEqual(x2, 500, delta=1)
            self.assertAlmostEqual(y2, y, delta=1)


if __name__ == "__main__":
    unittest.main()
