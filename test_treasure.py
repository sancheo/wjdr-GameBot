"""Pet treasure dispatch, skip conditions and calibrated screenshot checks."""
import unittest
import contextlib
import io
from pathlib import Path
import tempfile
from unittest.mock import call, patch

from PIL import Image
import help_bot as bot


class TreasureTest(unittest.TestCase):
    def setUp(self):
        scroll = patch.object(bot, 'TASK_DRAWER_SCROLL', 0)
        scroll.start()
        self.addCleanup(scroll.stop)

    def test_failure_maps_offer_purple_and_orange_chests(self):
        frame = Image.open(bot.ROOT / 'artifacts/treasure-mixed-chests.png').convert('RGB')
        target = bot.treasure_chest_target(frame)
        self.assertEqual(target, (711, 1574))
        # 分别移除另一个品质的宝箱，确认两种品质都能识别。
        for box, expected in (((700, 400, 950, 850), (711, 1574)),
                              ((600, 1450, 850, 1700), (822, 638))):
            with self.subTest(expected=expected):
                isolated = frame.copy()
                isolated.paste((70, 90, 120), box)
                self.assertEqual(bot.treasure_chest_target(isolated), expected)

    def test_chest_priority_is_rare_then_intermediate_then_basic(self):
        page = Image.open(bot.ROOT / 'artifacts/treasure-page.png').convert('RGB')
        frame = Image.new('RGB', bot.EXPECTED_SIZE, (70, 90, 120))
        frame.paste(page.crop((140, 165, 345, 225)), (140, 165))
        chests = [('chest', (120, 600)), ('chest-purple', (500, 950)),
                  ('chest-orange', (700, 1500))]
        for name, position in chests:
            with Image.open(bot.ROOT / f'assets/emulator-treasure-{name}.png') as chest:
                frame.paste(chest, position)
        with patch.object(bot, 'recognize_text', return_value=[('00:10:00', (700, 1290, 198, 30))]):
            self.assertEqual(bot.treasure_chest_target(frame), (599, 1041))
        for name, (x, y) in reversed(chests):
            with self.subTest(name=name), patch.object(bot, 'recognize_text', return_value=[]):
                self.assertEqual(bot.treasure_chest_target(frame), (x + 99, y + 91))
            frame.paste((70, 90, 120), (x, y, x + 200, y + 200))

    def test_open_drawer_with_unknown_scroll_can_be_checked_without_reset(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        with (patch.object(bot, 'TASK_DRAWER_SCROLL', None),
              patch.object(bot, 'task_page', return_value=True),
              patch.object(bot, 'swipe_drawer') as swipe,
              patch.object(bot, 'task_tap') as tap):
            self.assertIs(bot.task_drawer('test', frame, {'drawer-open': frame},
                                         reset_unknown=False), frame)
            self.assertIsNone(bot.TASK_DRAWER_SCROLL)
        swipe.assert_not_called()
        tap.assert_not_called()

    def test_busy_chest_is_excluded_even_when_it_matches_better(self):
        page = Image.open(bot.ROOT / 'artifacts/treasure-page.png').convert('RGB')
        busy = Image.open(bot.ROOT / 'artifacts/treasure-mixed-chests.png').convert('RGB')
        frame = Image.new('RGB', bot.EXPECTED_SIZE, (70, 90, 120))
        frame.paste(page.crop((140, 165, 345, 225)), (140, 165))
        frame.paste(busy.crop((300, 1450, 550, 1790)), (300, 1450))
        with Image.open(bot.ROOT / 'assets/emulator-treasure-chest.png') as chest:
            frame.paste(chest, (314, 1483))
            self.assertIsNone(bot.treasure_chest_target(frame))
            frame.paste(chest, (290, 691))
        self.assertEqual(bot.treasure_chest_target(frame), (389, 782))

    def test_recheck_resets_drawer_scrolled_past_treasure(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        events = []
        def read(_image):
            events.append('detect')
            return [('生命之树', (0, 300, 120, 30))]
        def scroll(*_args, **_kwargs):
            events.append('reset')
            return frame
        with (patch.object(bot, 'color_screenshot', return_value=frame),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_drawer', return_value=frame),
              patch.object(bot, 'TASK_DRAWER_SCROLL', 2),
              patch.object(bot, 'swipe_drawer', side_effect=scroll) as swipe,
              patch.object(bot, 'recognize_text', side_effect=read),
              patch.object(bot, 'task_tap') as tap):
            self.assertIs(bot.run_treasure('test', None, None, frame), frame)
        self.assertEqual(swipe.call_args_list, [call('test', frame, reverse=True)] * 2)
        self.assertEqual(events, ['detect', 'reset', 'reset', 'detect'])
        tap.assert_not_called()

    def test_missing_current_row_rescans_from_top_then_down(self):
        current, top, row = [Image.new('RGB', bot.EXPECTED_SIZE) for _ in range(3)]
        with (patch.object(bot, 'color_screenshot', return_value=current),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_drawer', return_value=current),
              patch.object(bot, 'reset_drawer', return_value=top) as reset,
              patch.object(bot, 'swipe_drawer', return_value=row) as swipe,
              patch.object(bot, 'recognize_text', side_effect=[
                  [('生命之树', (0, 300, 120, 30))], [], [('宠物寻宝', (0, 200, 120, 30))]]),
              patch.object(bot, 'intelligence_text_target', side_effect=lambda _image, labels, _box:
                           (300, 955) if labels[0] == '可寻宝' else None),
              patch.object(bot, 'treasure_remaining', return_value=0),
              patch.object(bot, 'task_screen', return_value=current),
              patch.object(bot, 'task_tap') as tap):
            self.assertIs(bot.run_treasure('test', None, None, current), current)
        reset.assert_called_once_with('test', current, {'drawer-open': current})
        swipe.assert_called_once_with('test', current)
        self.assertEqual(tap.call_args_list, [call('test', 605, 955), call('test', 60, 190)])

    def test_stale_completed_drawer_does_not_claim_a_missing_marker(self):
        page = Image.open(bot.ROOT / 'artifacts/treasure-claimed-map.png').convert('RGB')
        drawer = Image.open(bot.ROOT / 'artifacts/treasure-completed-drawer.png').convert('RGB')
        with (patch.object(bot, 'color_screenshot', return_value=drawer),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_drawer', return_value=drawer),
              patch.object(bot, 'TASK_DRAWER_SCROLL', 2),
              patch.object(bot, 'reset_drawer') as reset,
              patch.object(bot, 'treasure_remaining', return_value=0),
              patch.object(bot, 'claim_treasure') as claim,
              patch.object(bot, 'task_screen', side_effect=[page, drawer]),
              patch.object(bot, 'task_tap') as tap):
            self.assertIs(bot.run_treasure('test', None, None, drawer), drawer)
        claim.assert_not_called()
        reset.assert_not_called()
        self.assertEqual(tap.call_args_list, [call('test', 605, 1408), call('test', 60, 190)])

    def test_chest_matches_bright_snow_without_matching_an_empty_map(self):
        frame = Image.open(bot.ROOT / 'artifacts/treasure-bright-snow.png').convert('RGB')
        target = bot.treasure_chest_target(frame)
        self.assertIsNotNone(target)
        self.assertTrue(any(abs(target[0] - x) <= 3 and abs(target[1] - y) <= 3
                            for x, y in ((147, 902), (951, 1290), (413, 1574))))
        empty = Image.new('RGB', bot.EXPECTED_SIZE, (200, 220, 245))
        empty.paste(frame.crop((140, 165, 345, 225)), (140, 165))
        self.assertIsNone(bot.treasure_chest_target(empty))

    def test_multiple_completed_chests_are_claimed_even_when_drawer_status_is_stale(self):
        page = Image.open(bot.ROOT / 'artifacts/treasure-bright-snow.png').convert('RGB')
        cleared = Image.open(bot.ROOT / 'artifacts/treasure-claimed-map.png').convert('RGB')
        one_left = page.copy()
        one_left.paste(cleared.crop((0, 750, 300, 1070)), (0, 750))
        first = bot.treasure_completed_target(page)
        second = bot.treasure_completed_target(one_left)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        self.assertIsNone(bot.treasure_completed_target(one_left, near=first))
        self.assertIsNotNone(bot.treasure_completed_target(page, near=first))
        detail, reward, claimed = [Image.open(bot.ROOT / f'artifacts/treasure-{name}.png').convert('RGB')
                                   for name in ('claim-result', 'reward', 'claimed')]
        drawer = Image.open(bot.ROOT / 'artifacts/treasure-completed-drawer.png').convert('RGB')
        original = bot.intelligence_text_target
        def target(image, labels, box):
            # 抽屉缓存为“可寻宝”，仍以地图上的完成标记为准。
            if image is drawer:
                return (300, 1408) if labels[0] == '可寻宝' else None
            return original(image, labels, box)
        with (patch.object(bot, 'color_screenshot', return_value=drawer),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_drawer', return_value=drawer),
              patch.object(bot, 'treasure_remaining', return_value=0),
              patch.object(bot, 'treasure_chest_target') as chest,
              patch.object(bot, 'intelligence_text_target', side_effect=target),
              patch.object(bot, 'task_screen', side_effect=
                           [page, detail, reward, claimed, one_left,
                            detail, reward, claimed, cleared, drawer]),
              patch.object(bot, 'task_tap') as tap):
            self.assertIs(bot.run_treasure('test', None, None, drawer), drawer)
        chest.assert_not_called()
        self.assertEqual([c.args[1:] for c in tap.call_args_list],
                         [(605, 1408), first, (540, 1490), (540, 2100), (960, 640),
                          second, (540, 1490), (540, 2100), (960, 640), (60, 190)])

    def test_treasure_retry_preserves_color_failure_screenshots(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE, (10, 80, 200))
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(bot, 'ROOT', Path(directory)),
              patch.object(bot, 'color_screenshot', return_value=frame),
              patch.object(bot, 'screenshot', return_value=frame.convert('L')),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot.time, 'sleep'),
              contextlib.redirect_stdout(io.StringIO()) as output):
            (Path(directory) / 'artifacts').mkdir()
            with self.assertRaisesRegex(bot.RetriesExhausted, '未找到宝箱图标'):
                bot.retry_once('宠物寻宝', lambda: (_ for _ in ()).throw(RuntimeError('未找到宝箱图标')),
                               'test', None, None)
            frames = list((Path(directory) / 'artifacts').glob('treasure-failure-*.png'))
            self.assertEqual(len(frames), 4)
            with Image.open(frames[0]) as saved:
                self.assertEqual(saved.getpixel((100, 100)), (10, 80, 200))
        self.assertEqual(output.getvalue().count('失败截图：'), 4)

    def test_alliance_red_dot_claims_before_zero_remaining_exit(self):
        page = Image.open(bot.ROOT / 'artifacts/treasure-page.png').convert('RGB')
        marked = page.copy()
        marked.paste((240, 30, 30), (990, 2160, 1010, 2180))
        alliance, reward, loading = [Image.new('RGB', bot.EXPECTED_SIZE) for _ in range(3)]

        def target(image, labels, _box):
            if image is alliance:
                return {'联盟宝藏': (540, 510), '一键领取': (537, 1902)}.get(labels[0])
            if image is reward and labels[0] == '点击任意位置退出':
                return 540, 2195
            return None

        with (patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_screen') as screen):
            self.assertIs(bot.claim_alliance_treasure('test', page), page)
            noise = page.copy()
            noise.paste((240, 30, 30), (995, 2165, 1000, 2170))
            self.assertIs(bot.claim_alliance_treasure('test', noise), noise)
            other = marked.copy()
            other.paste('black', (140, 165, 345, 225))
            self.assertIs(bot.claim_alliance_treasure('test', other), other)
        tap.assert_not_called()
        screen.assert_not_called()

        with (patch.object(bot, 'color_screenshot', return_value=loading),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_drawer', return_value=loading),
              patch.object(bot, 'recognize_text', return_value=[('宠物寻宝', (0, 200, 120, 30))]),
              patch.object(bot, 'intelligence_text_target', side_effect=lambda image, labels, box:
                           (300, 955) if labels[0] == '可寻宝' else target(image, labels, box)),
              patch.object(bot, 'treasure_remaining', return_value=0),
              patch.object(bot, 'treasure_chest_target') as chest,
              patch.object(bot, 'task_screen', side_effect=
                           [marked, loading, alliance, loading, reward, alliance, page, loading]),
              patch.object(bot, 'task_tap') as tap):
            self.assertIs(bot.run_treasure('test', None, None, loading), loading)
        chest.assert_not_called()
        self.assertEqual([c.args[1:] for c in tap.call_args_list],
                         [(605, 955), (950, 2220), (537, 1902), (540, 2100),
                          (1000, 510), (60, 190)])

        for frames, error, taps in (([loading] * 5, '一键领取按钮', 1),
                                    ([alliance] + [loading] * 10, '奖励页面', 2),
                                    ([alliance, reward] + [loading] * 5, '未返回宠物寻宝', 3)):
            with (self.subTest(error=error),
                  patch.object(bot, 'intelligence_text_target', side_effect=target),
                  patch.object(bot, 'task_screen', side_effect=frames),
                  patch.object(bot, 'task_tap') as tap,
                  self.assertRaisesRegex(RuntimeError, error)):
                bot.claim_alliance_treasure('test', marked)
            self.assertEqual(tap.call_count, taps)

        with patch.object(bot, 'intelligence_text_target', side_effect=target):
            self.assertEqual(bot.treasure_back_target(alliance), (1000, 510))

    def test_chest_matches_left_edge_and_coarse_grid_offsets(self):
        page = Image.open(bot.ROOT / 'artifacts/treasure-page.png').convert('RGB')
        claimed = Image.open(bot.ROOT / 'artifacts/treasure-claimed-map.png').convert('RGB')
        self.assertEqual(bot.treasure_chest_target(page), (951, 1290))
        target = bot.treasure_chest_target(claimed)
        self.assertIsNotNone(target)
        self.assertTrue(130 <= target[0] <= 170 and 880 <= target[1] <= 930)
        with Image.open(bot.ROOT / 'assets/emulator-treasure-chest.png') as chest:
            shifted = Image.new('RGB', bot.EXPECTED_SIZE, (70, 90, 120))
            shifted.paste(page.crop((140, 165, 345, 225)), (140, 165))
            shifted.paste(chest, (290, 691))
        self.assertEqual(bot.treasure_chest_target(shifted), (389, 782))
        shifted.paste('black', (140, 165, 345, 225))
        self.assertIsNone(bot.treasure_chest_target(shifted))

    def test_dispatch_waits_for_chest_and_never_taps_an_unconfirmed_chest(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for targets in ([None, (389, 782)], [None] * 5):
            with (self.subTest(targets=targets),
                  patch.object(bot, 'color_screenshot', return_value=frame),
                  patch.object(bot, 'inspect', return_value=(True, None, 0)),
                  patch.object(bot, 'task_drawer', return_value=frame),
                  patch.object(bot, 'recognize_text', return_value=[('宠物寻宝', (0, 200, 120, 30))]),
                  patch.object(bot, 'intelligence_text_target', side_effect=lambda _image, labels, _box:
                               {'可寻宝': (300, 955), '寻宝派遣': (540, 1850),
                                '开始寻宝': (540, 1760)}.get(labels[0])),
                  patch.object(bot, 'treasure_remaining', side_effect=[1, 0]),
                  patch.object(bot, 'treasure_chest_target', side_effect=targets) as chest,
                  patch.object(bot, 'task_screen', return_value=frame) as screen,
                  patch.object(bot, 'task_tap') as tap):
                if targets[-1] is None:
                    with self.assertRaisesRegex(RuntimeError, '未找到宝箱图标'):
                        bot.run_treasure('test', None, None, frame)
                    tap.assert_called_once_with('test', 605, 955)
                    self.assertEqual(screen.call_count, 5)
                else:
                    self.assertIs(bot.run_treasure('test', None, None, frame), frame)
                    self.assertEqual(tap.call_args_list[1], call('test', 389, 782))
                self.assertEqual(chest.call_count, len(targets))

    def test_completed_treasure_claims_with_no_remaining_attempts(self):
        frames = [Image.open(bot.ROOT / f'artifacts/treasure-{name}.png').convert('RGB')
                  for name in ('completed', 'claim-result', 'reward', 'claimed')]
        completed, detail, reward, claimed = frames
        cleared = Image.open(bot.ROOT / 'artifacts/treasure-claimed-map.png').convert('RGB')
        drawer = Image.open(bot.ROOT / 'artifacts/treasure-completed-drawer.png').convert('RGB')
        self.assertEqual(bot.treasure_completed_target(completed), (465, 715))
        shifted = completed.copy()
        shifted.paste((50, 60, 70), (420, 670, 510, 760))
        shifted.paste(completed.crop((420, 670, 510, 760)), (430, 680))
        self.assertEqual(bot.treasure_completed_target(shifted), (475, 725))
        self.assertIsNone(bot.treasure_completed_target(cleared))
        self.assertEqual(bot.treasure_back_target(detail), (960, 640))
        with (patch.object(bot, 'color_screenshot', return_value=drawer),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_drawer', return_value=drawer),
              patch.object(bot, 'task_screen', side_effect=[completed, detail, reward, claimed, cleared, drawer]),
              patch.object(bot, 'task_tap') as tap):
            self.assertIs(bot.run_treasure('test', None, None, drawer), drawer)
        self.assertEqual([c.args[1:] for c in tap.call_args_list],
                         [(605, 1408), (465, 715), (540, 1490), (540, 2100), (960, 640), (60, 190)])
        with (patch.object(bot, 'task_tap') as tap,
              self.assertRaisesRegex(RuntimeError, '未找到打勾宝箱')):
            bot.claim_treasure('test', cleared)
        tap.assert_not_called()

    def test_dispatch_and_zero_remaining(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for remaining, active, after in ((1, False, 0), (1, True, 0), (0, False, 0),
                                         (1, False, 1), (1, False, None)):
            with (self.subTest(remaining=remaining, active=active, after=after),
                patch.object(bot, 'color_screenshot', return_value=frame),
                patch.object(bot, 'inspect', return_value=(True, None, 0)),
                patch.object(bot, 'task_drawer', return_value=frame),
                patch.object(bot, 'recognize_text', return_value=[('宠物寻宝', (0, 200, 120, 30))]),
                patch.object(bot, 'intelligence_text_target', side_effect=lambda _image, labels, _box:
                             {'可寻宝': (300, 955), '寻宝派遣': (540, 1850),
                              '开始寻宝': (540, 1760),
                              '寻宝中': (540, 750) if active else None}.get(labels[0])),
                patch.object(bot, 'treasure_remaining', side_effect=
                             [remaining, None, after] if active else [remaining] + [after] * 5),
                patch.object(bot, 'treasure_chest_target', return_value=(381, 774)),
                patch.object(bot, 'task_screen', return_value=frame),
                patch.object(bot, 'task_tap') as tap,
            ):
                failed = remaining > 0 and (after is None or after >= remaining)
                if failed:
                    with self.assertRaisesRegex(RuntimeError, '未确认今日剩余次数减少'):
                        bot.run_treasure('test', None, None, frame)
                else:
                    self.assertIs(bot.run_treasure('test', None, None, frame), frame)
            expected = [call('test', 605, 955)]
            if remaining:
                expected += [call('test', 381, 774), call('test', 540, 1850),
                             call('test', 540, 1760)]
            if active:
                expected += [call('test', 960, 680)]
            self.assertEqual(tap.call_args_list, expected if failed else expected + [call('test', 60, 190)])

    def test_unavailable_or_absent_row_skips_without_tapping(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for texts in ([('宠物寻宝', (0, 200, 120, 30))],
                      [('生命之树', (0, 300, 120, 30))]):
            with (self.subTest(texts=texts),
                patch.object(bot, 'color_screenshot', return_value=frame),
                patch.object(bot, 'inspect', return_value=(True, None, 0)),
                patch.object(bot, 'task_drawer', return_value=frame),
                patch.object(bot, 'recognize_text', return_value=texts),
                patch.object(bot, 'intelligence_text_target', return_value=None),
                patch.object(bot, 'swipe_drawer') as swipe,
                patch.object(bot, 'task_tap') as tap,
            ):
                self.assertIs(bot.run_treasure('test', None, None, frame), frame)
            swipe.assert_not_called()
            tap.assert_not_called()

    def test_saved_pages_and_recovery_targets(self):
        frames = {name: Image.open(bot.ROOT / f'artifacts/treasure-{name}.png').convert('RGB')
                  for name in ('page', 'detail', 'dispatch')}
        self.assertEqual(bot.treasure_remaining(frames['page']), 1)
        completed = Image.open(bot.ROOT / 'artifacts/treasure-live-map.png').convert('RGB')
        self.assertEqual(bot.treasure_remaining(completed), 0)
        self.assertIsNone(bot.treasure_remaining(frames['detail']))
        self.assertEqual(bot.treasure_back_target(frames['page']), (60, 190))
        self.assertEqual(bot.treasure_back_target(frames['detail']), (960, 595))
        self.assertEqual(bot.treasure_back_target(frames['dispatch']), (995, 610))
        active = Image.open(bot.ROOT / 'artifacts/treasure-live-after-start.png').convert('RGB')
        self.assertEqual(bot.treasure_back_target(active), (960, 680))
        drawer = Image.open(bot.ROOT / 'artifacts/treasure-drawer.png').convert('RGB')
        with (patch.object(bot, 'color_screenshot', return_value=drawer),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_drawer', return_value=drawer),
              patch.object(bot, 'swipe_drawer') as swipe,
              patch.object(bot, 'task_tap') as tap):
            self.assertIs(bot.run_treasure('test', None, None, drawer), drawer)
        swipe.assert_not_called()
        tap.assert_not_called()
        with Image.open(bot.ROOT / 'assets/emulator-treasure-chest.png') as chest:
            score, x, y = bot.find_white(frames['page'], chest,
                                        range(80, 881, 20), range(430, 1851, 20))
        self.assertGreaterEqual(score, .90)
        self.assertEqual((x, y), (282, 683))
        for text, expected in (('今日剩余寻宝次数：0', 0),
                               ('今日剩余寻宝次数: 12', 12),
                               ('今日剩余寻宝次数：', None)):
            with patch.object(bot, 'recognize_text', return_value=[(text, (0, 0, 1, 1))]):
                self.assertEqual(bot.treasure_remaining(frames['page']), expected)


if __name__ == '__main__':
    unittest.main()
