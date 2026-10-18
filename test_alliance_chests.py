"""Real Nox reward samples and guarded alliance collection transitions."""
import unittest
from unittest.mock import patch
from PIL import Image
import help_bot as bot
import alliance_chests as chests


def frame(name):
    with Image.open(bot.ROOT / f'tests/fixtures/nox/{name}-1018.png') as image:
        return image.convert('RGB')


class AllianceChestsTest(unittest.TestCase):
    def new_frame(self, name):
        with Image.open(bot.ROOT / f'tests/fixtures/nox/alliance-incentive-{name}-1019.png') as image:
            return image.convert('RGB')

    def test_new_weekly_icon_retains_old_icon_and_disappears_after_claim(self):
        self.assertTrue(chests.matches(bot, self.new_frame('new-entry'), 'incentive-icon'))
        self.assertTrue(chests.matches(bot, frame('alliance-chests-entry'), 'incentive-icon'))
        self.assertFalse(chests.matches(bot, self.new_frame('new-entry-after'), 'incentive-icon'))

    def test_open_glowing_chest_animation_excludes_closed_chest_and_overlay(self):
        for name in ('open-before', 'open-animation'):
            image = self.new_frame(name)
            self.assertEqual(chests.page(bot, image), 'incentive')
            self.assertTrue(chests.matches(bot, image, 'incentive-open'))
        for image in (self.new_frame('open-after'), self.new_frame('weekly-reward'),
                      frame('alliance-incentive-before')):
            self.assertFalse(chests.matches(bot, image, 'incentive-open'))

    def test_weekly_claim_precedes_personal_claim_and_waits_for_closed_chest(self):
        menu, opened = self.new_frame('new-entry'), self.new_frame('open-before')
        personal, closed = frame('alliance-incentive-before'), self.new_frame('open-after')
        with (patch.object(chests, 'wait', side_effect=[menu, opened, opened, personal, closed, menu]),
              patch.object(chests.task_rewards, 'badge_count', return_value=0),
              patch.object(bot, 'task_tap') as tap):
            chests.collect_on_alliance(bot, 'test', menu)
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(540, 2230), (540, 950), (540, 1650), (60, 190)])

    def test_weekly_claim_without_personal_red_dot_only_claims_weekly(self):
        menu, opened, closed = (self.new_frame(name) for name in
                                ('new-entry', 'open-before', 'open-after'))
        with (patch.object(bot, 'task_screen', side_effect=[
                  opened, self.new_frame('weekly-reward'), closed, self.new_frame('new-entry-after')]),
              patch.object(chests.task_rewards, 'badge_count', return_value=0),
              patch.object(bot, 'task_tap') as tap):
            chests.collect_on_alliance(bot, 'test', menu)
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(540, 2230), (540, 950), (540, 2110), (60, 190)])

    def test_weekly_settlement_overlay_is_dismissed_before_checking_personal(self):
        overlay, closed = self.new_frame('weekly-reward'), self.new_frame('open-after')
        with (patch.object(bot, 'task_screen', side_effect=[overlay, closed]),
              patch.object(bot, 'task_tap') as tap):
            self.assertIs(chests.wait(bot, 'test', lambda im: chests.page(bot, im) == 'incentive'), closed)
        tap.assert_called_once_with('test', 540, 2110)

    def test_stale_weekly_chest_stops_without_repeated_claim_or_personal_click(self):
        menu, opened = self.new_frame('new-entry'), self.new_frame('open-before')
        with (patch.object(chests, 'wait', side_effect=[menu, opened] + [opened] * 8),
              patch.object(chests.task_rewards, 'badge_count', return_value=0),
              patch.object(bot, 'task_tap') as tap,
              self.assertRaisesRegex(RuntimeError, '仍处于打开状态')):
            chests.collect_on_alliance(bot, 'test', menu)
        self.assertEqual([call.args[1:] for call in tap.call_args_list], [(540, 2230), (540, 950)])

    def test_real_pages_tabs_and_badges(self):
        menu = frame('alliance-chests-entry')
        gifts = frame('alliance-chests-loot-before')
        loot = frame('alliance-chests-loot-tab')
        incentive = frame('alliance-incentive-before')
        self.assertTrue(chests.alliance(bot, menu))
        self.assertIsNone(chests.page(bot, menu))
        self.assertEqual(chests.selected(bot, gifts), 'gifts')
        self.assertEqual(chests.selected(bot, loot), 'loot')
        self.assertEqual(chests.page(bot, incentive), 'incentive')
        self.assertEqual(chests.task_rewards.badge_count(bot, menu, chests.BADGES['entry']), 49)
        self.assertEqual(chests.task_rewards.badge_count(bot, loot, chests.BADGES['loot']), 44)
        self.assertTrue(chests.matches(bot, menu, 'incentive-icon'))
        self.assertTrue(bot.red_dot(incentive, chests.PERSONAL_BADGE))
        for image in (gifts, loot, incentive):
            self.assertEqual(bot.alliance_back_target(image), (60, 190))

    def test_both_starting_tabs_claim_current_then_other_before_incentive(self):
        menu = frame('alliance-chests-entry')
        original_matches = chests.matches
        for first in ('gifts', 'loot'):
            other = 'loot' if first == 'gifts' else 'gifts'
            image = frame('alliance-chests-loot-before' if first == 'gifts' else 'alliance-chests-loot-tab')
            second = frame('alliance-chests-loot-before' if other == 'gifts' else 'alliance-chests-loot-tab')
            with (patch.object(chests, 'wait', side_effect=[menu, image, second, menu]),
                  patch.object(chests, 'claim_tab', side_effect=lambda b, s, im, tab: im) as claim,
                  patch.object(chests, 'matches', side_effect=lambda b, im, name:
                               False if name == 'incentive-icon' else original_matches(b, im, name)),
                  patch.object(bot, 'task_tap') as tap):
                self.assertIs(chests.collect_on_alliance(bot, 'test', menu), menu)
            self.assertEqual([call.args[3] for call in claim.call_args_list], [first, other])
            self.assertEqual(tap.call_args_list[-1].args, ('test', 60, 190))

    def test_animated_incentive_icon_and_claimed_state(self):
        root = bot.ROOT / 'tests/fixtures/nox'
        with Image.open(root / 'alliance-chests-live-20261018-160055/006.png') as image:
            self.assertTrue(chests.matches(bot, image, 'incentive-icon'))
        with Image.open(root / 'alliance-chests-live-20261018-160236/006.png') as image:
            self.assertFalse(chests.matches(bot, image, 'incentive-icon'))
            self.assertEqual(chests.task_rewards.badge_count(bot, image, chests.BADGES['entry']), 0)
        with Image.open(root / 'alliance-chests-live-20261018-160236/005.png') as image:
            self.assertEqual(chests.page(bot, image), 'incentive')
            self.assertFalse(bot.red_dot(image, chests.PERSONAL_BADGE))

    def test_batch_claim_ocr_includes_the_first_character(self):
        path = bot.ROOT / 'tests/fixtures/nox/alliance-chests-live-20261018-155843/009.png'
        with Image.open(path) as image:
            target = chests.task_rewards.green_claim(bot, image, ('一键领取',), (280, 2100, 800, 2340))
        self.assertIsNotNone(target)
        self.assertTrue(2180 < target[1] < 2280)

    def test_no_entry_badge_skips_chests_but_checks_incentive(self):
        menu = frame('alliance-chests-entry')
        incentive = frame('alliance-incentive-before')
        with (patch.object(chests, 'wait', side_effect=[menu, incentive, incentive, menu]),
              patch.object(chests.task_rewards, 'badge_count', return_value=0),
              patch.object(bot, 'red_dot', side_effect=[True, False]),
              patch.object(bot, 'task_tap') as tap):
            chests.collect_on_alliance(bot, 'test', menu)
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(540, 2230), (540, 1650), (60, 190)])

    def test_no_personal_dot_does_not_claim(self):
        menu = frame('alliance-chests-entry')
        incentive = frame('alliance-incentive-before')
        with (patch.object(chests, 'wait', side_effect=[menu, incentive, menu]),
              patch.object(chests.task_rewards, 'badge_count', return_value=0),
              patch.object(bot, 'red_dot', return_value=False), patch.object(bot, 'task_tap') as tap):
            chests.collect_on_alliance(bot, 'test', menu)
        self.assertEqual([call.args[1:] for call in tap.call_args_list], [(540, 2230), (60, 190)])

    def test_empty_menu_exits_without_claims(self):
        menu = frame('alliance-chests-entry')
        with (patch.object(chests, 'wait', return_value=menu),
              patch.object(chests.task_rewards, 'badge_count', return_value=0),
              patch.object(chests, 'matches', return_value=False), patch.object(bot, 'task_tap') as tap):
            self.assertIs(chests.collect_on_alliance(bot, 'test', menu), menu)
        tap.assert_not_called()

    def test_unknown_number_never_blindly_enters(self):
        menu = frame('alliance-chests-entry')
        with (patch.object(chests, 'wait', return_value=menu),
              patch.object(chests.task_rewards, 'badge_count', return_value=None),
              patch.object(bot, 'task_tap') as tap, self.assertRaisesRegex(RuntimeError, '红点数字')):
            chests.collect_on_alliance(bot, 'test', menu)
        tap.assert_not_called()

    def test_stale_claim_does_not_repeat_forever(self):
        image = frame('alliance-chests-loot-tab')
        with (patch.object(chests, 'wait', return_value=image),
              patch.object(chests.task_rewards, 'green_claim', return_value=(540, 2230)),
              patch.object(bot, 'task_tap') as tap,
              self.assertRaisesRegex(RuntimeError, '状态变化')):
            chests.claim_tab(bot, 'test', image, 'loot')
        tap.assert_called_once()

    def test_standalone_task_enters_alliance_and_returns_city(self):
        city = frame('alliance-chests-city-before-live')
        menu = frame('alliance-chests-entry')
        timers = {}
        with (patch.object(chests, 'collect_on_alliance', return_value=menu) as collect,
              patch.object(chests, 'wait', side_effect=[menu, city]),
              patch.object(bot, 'task_tap') as tap):
            result = chests.run(bot, 'test', bot.open_template(bot.ROOT / 'assets/emulator-help-icon.png').convert('L'),
                                bot.open_template(bot.ROOT / 'assets/emulator-city-nav.png').convert('L'),
                                initial_image=city, timers=timers)
        self.assertIs(result, city)
        self.assertEqual([call.args[1:] for call in tap.call_args_list], [(800, 2240), (60, 190)])
        collect.assert_called_once_with(bot, 'test', menu)
        self.assertIn('alliance_chests', timers)

    def test_unconfirmed_screen_cannot_enter_alliance(self):
        with (patch.object(bot, 'task_tap') as tap,
              self.assertRaisesRegex(RuntimeError, '未确认主城')):
            chests.run(bot, 'test', bot.open_template(bot.ROOT / 'assets/emulator-help-icon.png').convert('L'),
                       bot.open_template(bot.ROOT / 'assets/emulator-city-nav.png').convert('L'),
                       initial_image=Image.new('RGB', bot.EXPECTED_SIZE))
        tap.assert_not_called()


if __name__ == '__main__':
    unittest.main()
