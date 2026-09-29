"""Exercise pet batches through the scheduler with a stateful game surface."""

import contextlib
import io
import unittest
from unittest.mock import patch

from PIL import Image

import help_bot as bot
from test_help_bot import Clock


class PetBatchTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.timers = {}
        self.actions = []
        self.page = 'city'
        self.selected = None
        self.states = {name: ('ready', None) for name in bot.PET_SKILLS}
        self.fail_uses = set()
        self.reward_skills = set()
        self.active_skills = set()
        self.opening_frames = 0
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        replacements = {
            'color_screenshot': lambda _: self.frame(),
            'screenshot': lambda _: self.frame(),
            'task_screen': self.screen,
            'inspect': lambda image, *_: (image.info['page'] == 'city', (100, 0, 0), 0),
            'pet_page': lambda image: image.info['page'] == 'pet',
            'pet_reward_page': lambda image: image.info['page'] == 'reward',
            'task_page': lambda *_: False,
            'difference': lambda *_: 0,
            'pet_skill_state': self.skill_state,
            'task_tap': self.tap,
            'forced_offline': lambda *_: False,
            'recover_task_city': self.recover,
            'run': lambda _, seconds, *a, **kw: self.clock.sleep(seconds),
            'maybe_cleanup_diagnostic_screenshots': lambda: None,
        }
        for name, replacement in replacements.items():
            self.stack.enter_context(patch.object(bot, name, replacement))
        self.stack.enter_context(patch.object(bot.time, 'monotonic', self.clock.monotonic))
        self.stack.enter_context(patch.object(bot.time, 'sleep', self.clock.sleep))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def frame(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        frame.info['page'] = self.page
        return frame

    def screen(self, _):
        frame = self.frame()
        if self.page == 'pet' and self.opening_frames:
            self.opening_frames -= 1
            frame.info['page'] = 'city'
        return frame

    def skill_state(self, image, label):
        self.assertEqual(image.info['page'], 'pet')
        self.assertEqual(bot.PET_SKILLS[self.selected][0], label)
        return self.states[self.selected]

    def tap(self, _, x, y):
        target = (x, y)
        self.actions.append(target)
        if target == (995, 1720):
            self.assertEqual(self.page, 'city')
            self.page = 'pet'
        elif target == (995, 505):
            self.assertEqual(self.page, 'pet')
            self.page = 'city'
        elif target == (540, 2100):
            self.assertEqual(self.page, 'reward')
            self.page = 'pet'
        else:
            self.assertEqual(self.page, 'pet')
            if target == (540, 1910):
                if self.selected not in self.fail_uses:
                    self.states[self.selected] = ('cooldown', 3600)
                    if self.selected in self.active_skills:
                        self.states[self.selected] = ('active', None)
                    if self.selected in self.reward_skills:
                        self.page = 'reward'
            else:
                self.selected = next(name for name, (_, point) in bot.PET_SKILLS.items() if point == target)

    def recover(self, *_):
        self.page = 'city'
        return self.frame()

    def tasks(self):
        return {name: lambda name=name: bot.run_pet_skill(
            'test', None, None, None, name, self.timers, keep_open=True)
            for name in bot.PET_SKILLS}

    def schedule(self, tasks=None, seconds=1):
        bot.run_schedule('test', seconds, 2, False, self.tasks() if tasks is None else tasks,
                         *([None] * 4), timers=self.timers, help_enabled=False)

    def test_four_ready_skills_open_and_close_once(self):
        self.schedule()
        self.assertEqual(self.actions.count((995, 1720)), 1)
        self.assertEqual(self.actions.count((995, 505)), 1)
        self.assertEqual(self.actions.count((540, 1910)), 4)
        self.assertEqual(self.page, 'city')
        self.assertEqual(self.timers, dict.fromkeys(bot.PET_SKILLS, 3700))

    def test_open_animation_is_waited_out_without_reopening(self):
        self.opening_frames = 2
        self.schedule()
        self.assertEqual(self.actions.count((995, 1720)), 1)
        self.assertEqual(self.actions.count((995, 505)), 1)
        self.assertEqual(self.actions.count((540, 1910)), 4)

    def test_cooldowns_continue_in_same_page_and_keep_separate_timers(self):
        self.states = {name: ('cooldown', (i + 1) * 100) for i, name in enumerate(bot.PET_SKILLS)}
        self.schedule()
        self.assertEqual(self.actions.count((995, 1720)), 1)
        self.assertEqual(self.actions.count((995, 505)), 1)
        self.assertNotIn((540, 1910), self.actions)
        self.assertEqual(self.timers, {name: 100 + (i + 1) * 100 for i, name in enumerate(bot.PET_SKILLS)})

    def test_reward_overlays_are_dismissed_without_reopening_skills(self):
        self.reward_skills = {'pet_companion', 'pet_gift'}
        self.schedule()
        self.assertEqual(self.actions.count((995, 1720)), 1)
        self.assertEqual(self.actions.count((995, 505)), 1)
        self.assertEqual(self.actions.count((540, 2100)), 2)
        self.assertEqual(self.actions.count((540, 1910)), 4)
        self.assertEqual(self.timers, dict.fromkeys(bot.PET_SKILLS, 3700))
        self.assertEqual(self.page, 'city')

    def test_active_buff_counts_as_success_and_keeps_batch_open(self):
        self.active_skills.add('pet_transport')
        self.schedule()
        self.assertEqual(self.actions.count((540, 1910)), 4)
        self.assertEqual(self.actions.count((995, 1720)), 1)
        self.assertEqual(self.actions.count((995, 505)), 1)
        self.assertEqual(self.timers['pet_transport'], 700)

    def test_already_active_skill_is_not_used_again(self):
        self.states['pet_transport'] = ('active', None)
        self.schedule()
        self.assertEqual(self.actions.count((540, 1910)), 3)
        self.assertEqual(self.actions.count((995, 1720)), 1)
        self.assertEqual(self.actions.count((995, 505)), 1)
        self.assertEqual(self.timers['pet_transport'], 700)

    def test_unknown_skill_closes_without_use_then_continues_remaining(self):
        self.states['pet_transport'] = ('unknown', None)
        self.schedule()
        self.assertEqual(self.actions.count((995, 1720)), 2)
        self.assertEqual(self.actions.count((995, 505)), 2)
        self.assertEqual(self.actions.count((540, 1910)), 3)
        self.assertEqual(self.timers['pet_transport'], 160)

    def test_expired_skill_only_is_rechecked(self):
        self.states = {name: ('cooldown', 1000) for name in bot.PET_SKILLS}
        self.states['pet_companion'] = ('cooldown', 10)
        self.schedule(seconds=25)
        self.assertEqual(self.actions.count((995, 1720)), 3)
        self.assertEqual(self.actions.count((995, 505)), 3)
        for name, (_, target) in bot.PET_SKILLS.items():
            self.assertEqual(self.actions.count(target), 3 if name == 'pet_companion' else 1)

    def test_failed_skill_retry_does_not_repeat_completed_skills(self):
        self.fail_uses.add('pet_transport')
        self.schedule()
        self.assertEqual(self.actions.count(bot.PET_SKILLS['pet_companion'][1]), 1)
        self.assertEqual(self.actions.count(bot.PET_SKILLS['pet_transport'][1]), 4)
        self.assertEqual(self.actions.count(bot.PET_SKILLS['pet_senses'][1]), 1)
        self.assertEqual(self.actions.count(bot.PET_SKILLS['pet_gift'][1]), 1)
        self.assertEqual(self.page, 'city')

    def test_non_pet_task_starts_after_page_closed(self):
        tasks = self.tasks()
        def city_task():
            self.assertEqual(self.page, 'city')
            return self.frame()
        tasks['donate'] = city_task
        self.schedule(tasks)
        self.assertEqual(self.actions.count((995, 505)), 1)

    def test_no_cooldown_confirmation_is_not_reported_as_success(self):
        self.fail_uses.add('pet_companion')
        with self.assertRaisesRegex(RuntimeError, '使用后未确认进入冷却'):
            self.tasks()['pet_companion']()
        self.assertNotIn('pet_companion', self.timers)


if __name__ == '__main__':
    unittest.main()
