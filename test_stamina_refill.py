"""Replay the stamina dialog that previously exhausted intelligence/hunt retries."""
import contextlib
import io
import unittest
from unittest.mock import patch
from PIL import Image
import help_bot as bot
import hunting


class StaminaRefillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.refill = Image.open(bot.ROOT / 'tests/fixtures/stamina-refill-1010.png').convert('RGB')

    def test_real_refill_is_identified_by_title_close_resource_and_amount(self):
        self.assertEqual(bot.stamina_refill_target(self.refill), (1000, 480))
        self.assertEqual(hunting.notice(bot, self.refill), '体力不足')

    def test_generic_resource_offer_and_stamina_header_without_dialog_are_rejected(self):
        cases = [(None, [('领主体力', (0, 0, 1, 1)), ('6/200', (0, 0, 1, 1))]),
                 ((1000, 480), [('木材', (0, 0, 1, 1)), ('6/200', (0, 0, 1, 1))]),
                 ((1000, 480), [('领主体力', (0, 0, 1, 1)), ('300', (0, 0, 1, 1))])]
        for close, texts in cases:
            with self.subTest(close=close, texts=texts), patch.object(bot, 'resource_more_target', return_value=close), patch.object(bot, 'recognize_text', return_value=texts):
                self.assertIsNone(bot.stamina_refill_target(self.refill))

    def test_intelligence_closes_offer_without_dispatch_or_refill_purchase(self):
        returned = Image.new('RGB', bot.EXPECTED_SIZE)
        with (patch.object(bot, 'task_screen', side_effect=[self.refill, returned]),
              patch.object(bot, 'town_button', return_value=True),
              patch.object(bot, 'bounty_name', return_value=None),
              patch.object(bot, 'intelligence_text_target', return_value=(540, 1680)),
              patch.object(bot, 'task_tap') as taps,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(bot.execute_intelligence('test', returned), (returned, False))
        self.assertEqual([call.args[1:] for call in taps.call_args_list], [(540, 1680), (1000, 480)])

    def test_intelligence_shortage_unwinds_squad_and_target_detail(self):
        squad, detail, returned = [Image.new('RGB', bot.EXPECTED_SIZE) for _ in range(3)]
        with (patch.object(bot, 'town_button', side_effect=lambda image: image is returned),
              patch.object(bot, 'intelligence_page', return_value=False),
              patch.object(bot, 'intelligence_back_target', side_effect=[(60, 190), (100, 1900)]),
              patch.object(bot, 'task_screen', side_effect=[detail, returned]),
              patch.object(bot, 'task_tap') as taps):
            self.assertIs(bot.leave_intelligence_stamina('test', squad), returned)
        self.assertEqual([call.args[1:] for call in taps.call_args_list], [(60, 190), (100, 1900)])

    def test_hunt_shortage_at_search_squad_and_after_dispatch_is_normal_skip(self):
        initial, detail, squad, returned = [Image.new('RGB', bot.EXPECTED_SIZE) for _ in range(4)]
        for stage in range(3):
            timers = {"hunt": 50}
            frames = [detail, squad, self.refill, returned] if stage == 2 else ([detail, self.refill, returned] if stage == 1 else [self.refill, returned])
            with (self.subTest(stage=stage),
                  patch.object(bot, 'task_screen', side_effect=frames),
                  patch.object(bot, 'intelligence_queue_full', return_value=False),
                  patch.object(hunting, 'notice', side_effect=[None] * stage + ['体力不足']),
                  patch.object(hunting, 'action', return_value=(540, 1230)),
                  patch.object(bot, 'intelligence_text_target', return_value=(825, 2220)),
                  patch.object(hunting, 'leave', return_value=returned),
                  patch.object(bot, 'task_tap') as taps,
                  patch.object(bot, 'task_back') as back,
                  patch.object(bot.time, 'monotonic', return_value=200),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(hunting.dispatch(bot, 'test', initial, 'beast', timers), (returned, False))
            self.assertEqual(timers['hunt'], 260)
            self.assertEqual(timers[hunting.STAMINA_RECHECK_TIMER], 260)
            expected = [(540, 2240)] + ([(540, 1230)] if stage >= 1 else []) + ([(825, 2220)] if stage == 2 else []) + [(1000, 480)]
            self.assertEqual([call.args[1:] for call in taps.call_args_list], expected)
            back.assert_not_called()


if __name__ == '__main__':
    unittest.main()
