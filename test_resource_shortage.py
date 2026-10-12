"""Replay observed treasure stamina and hunting soldier shortages."""
import contextlib
import io
import unittest
from unittest.mock import patch, call

from PIL import Image
import help_bot as bot
import hunting


class ResourceShortageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.refill = Image.open(bot.ROOT / 'tests/fixtures/treasure-stamina-1010.png').convert('RGB')
        cls.empty_squad = Image.open(bot.ROOT / 'tests/fixtures/hunt-no-soldiers-1010.png').convert('RGB')

    def test_observed_shortages_are_recognized(self):
        self.assertEqual(bot.stamina_refill_target(self.refill), (1000, 480))
        self.assertEqual(hunting.notice(bot, self.empty_squad), '士兵不足')

    def test_treasure_shortage_at_each_dispatch_stage_exits_without_start_claim(self):
        drawer, page, detail, dispatch = [Image.new('RGB', bot.EXPECTED_SIZE) for _ in range(4)]
        def target(image, labels, box):
            return {'可寻宝': (300, 955), '寻宝派遣': (540, 1850),
                    '开始寻宝': (540, 1760)}.get(labels[0])
        for stage in range(3):
            frames = [page] + [detail, dispatch][:stage] + [self.refill, dispatch, detail, page, drawer]
            with (self.subTest(stage=stage),
                  patch.object(bot, 'color_screenshot', return_value=drawer),
                  patch.object(bot, 'inspect', return_value=(True, None, 0)),
                  patch.object(bot, 'task_drawer', return_value=drawer),
                  patch.object(bot, 'recognize_text', return_value=[('宠物寻宝', (0, 200, 120, 30))]),
                  patch.object(bot, 'intelligence_text_target', side_effect=target),
                  patch.object(bot, 'treasure_remaining', return_value=1),
                  patch.object(bot, 'claim_alliance_treasure', side_effect=lambda serial, image: image),
                  patch.object(bot, 'treasure_completed_target', return_value=None),
                  patch.object(bot, 'treasure_chest_target', return_value=(381, 774)),
                  patch.object(bot, 'stamina_refill_target', side_effect=lambda image: (1000, 480) if image is self.refill else None),
                  patch.object(bot, 'treasure_page', side_effect=lambda image: image is page),
                  patch.object(bot, 'treasure_back_target', side_effect=lambda image: (995, 610) if image is dispatch else (960, 595)),
                  patch.object(bot, 'task_screen', side_effect=frames),
                  patch.object(bot, 'task_tap') as taps,
                  contextlib.redirect_stdout(io.StringIO()) as output):
                self.assertIs(bot.run_treasure('test', None, None, drawer), drawer)
            expected = [(605, 955), (381, 774)] + [(540, 1850), (540, 1760)][:stage]
            expected += [(1000, 480), (995, 610), (960, 595), (60, 190)]
            self.assertEqual([c.args[1:] for c in taps.call_args_list], expected)
            self.assertIn('体力不足', output.getvalue())
            self.assertNotIn('已开始宠物寻宝', output.getvalue())

    def test_treasure_unknown_exit_does_not_claim_recovery(self):
        unknown = Image.new('RGB', bot.EXPECTED_SIZE)
        with (patch.object(bot, 'stamina_refill_target', return_value=(1000, 480)),
              patch.object(bot, 'task_screen', return_value=unknown),
              patch.object(bot, 'treasure_page', return_value=False),
              patch.object(bot, 'treasure_back_target', return_value=None),
              patch.object(bot, 'task_tap') as taps):
            with self.assertRaisesRegex(RuntimeError, '未返回宠物寻宝'):
                bot.leave_treasure_stamina('test', self.refill)
        self.assertEqual(taps.call_args_list, [call('test', 1000, 480)])

    def test_treasure_offer_unwinds_real_dispatch_and_detail_pages(self):
        dispatch, detail, page = [Image.open(bot.ROOT / f'artifacts/treasure-{name}.png').convert('RGB')
                                  for name in ('dispatch', 'detail', 'page')]
        with (patch.object(bot, 'task_screen', side_effect=[dispatch, detail, page]),
              patch.object(bot, 'task_tap') as taps,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertIs(bot.leave_treasure_stamina('test', self.refill), page)
        self.assertEqual(taps.call_args_list, [call('test', 1000, 480),
                                             call('test', 995, 610), call('test', 960, 595)])

    def test_hunt_no_soldiers_skips_at_search_squad_and_post_dispatch(self):
        initial, detail, squad, returned = [Image.new('RGB', bot.EXPECTED_SIZE) for _ in range(4)]
        for kind in ('beast', 'polar_beast'):
            for stage in range(3):
                frames = [detail, squad][:stage] + [self.empty_squad]
                with (self.subTest(kind=kind, stage=stage),
                      patch.object(bot, 'task_screen', side_effect=frames),
                      patch.object(bot, 'intelligence_queue_full', return_value=False),
                      patch.object(hunting, 'notice', side_effect=[None] * stage + ['士兵不足']),
                      patch.object(hunting, 'action', return_value=(540, 1230)),
                      patch.object(bot, 'intelligence_text_target', return_value=(825, 2220)),
                      patch.object(hunting, 'leave', return_value=returned) as leave,
                      patch.object(bot, 'task_tap') as taps,
                      contextlib.redirect_stdout(io.StringIO())):
                    self.assertEqual(hunting.dispatch(bot, 'test', initial, kind), (returned, False))
                leave.assert_called_once_with(bot, 'test', self.empty_squad)
                self.assertEqual([c.args[1:] for c in taps.call_args_list],
                                 [(540, 2240)] + [(540, 1230), (825, 2220)][:stage])

    def test_soldier_shortage_unwinds_actual_squad_to_field(self):
        returned = Image.open(bot.ROOT / 'artifacts/hunt-live-before.png').convert('RGB')
        template = bot.open_template(bot.ROOT / 'assets/emulator-town-button.png').convert('L')
        with (patch.object(bot, 'TOWN_TEMPLATE', template),
              patch.object(bot, 'task_screen', return_value=returned),
              patch.object(bot, 'task_back') as back,
              patch.object(bot, 'task_tap') as taps,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(hunting.wait_for_soldiers(bot, 'test', self.empty_squad), (returned, False))
        back.assert_called_once_with('test')
        taps.assert_not_called()

    def test_failed_field_exit_propagates_instead_of_claiming_skip(self):
        with patch.object(hunting, 'leave', side_effect=RuntimeError('未确认野外主界面')), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, '未确认野外主界面'):
                hunting.wait_for_soldiers(bot, 'test', self.empty_squad)


if __name__ == '__main__':
    unittest.main()
