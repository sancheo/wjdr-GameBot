"""Real big-account Nox hospital samples and scheduler continuation regressions."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import help_bot as bot
import healing
import gui_web
from emulator_config import save_config, load_config
from runtime_control import StopRequested
from test_help_bot import Clock


class HealingTest(unittest.TestCase):
    def pixel5_frame(self, name):
        with Image.open(bot.ROOT / f'tests/fixtures/heal-5554-{name}.png') as image:
            return image.convert('RGB')

    def test_pixel5_labels_and_disabled_zero_unit_treatment(self):
        dialog, selected = (self.pixel5_frame(name) for name in ('dialog', 'selected'))
        for image in (dialog, selected):
            offset = healing.page(bot, image)
            self.assertEqual(offset, (1, -1))
            self.assertTrue(healing.button(bot, image, 'quick', offset))
            self.assertTrue(healing.button(bot, image, 'treat', offset))
        self.assertFalse(healing.treatment_ready(bot, dialog, offset))
        self.assertTrue(healing.treatment_ready(bot, selected, offset))

    def test_pixel5_waits_for_selection_before_starting_treatment(self):
        dialog, selected = (self.pixel5_frame(name) for name in ('dialog', 'selected'))
        started = self.pixel5_frame('started')
        self.assertTrue(healing.button(bot, started, 'help', healing.page(bot, started)))
        self.assertTrue(healing.in_progress(bot, started, healing.page(bot, started)))
        sequence = [dialog, dialog, selected, started,
                    self.frames['help-requested'], self.frames['returned']]
        with (patch.object(bot, 'task_screen', side_effect=sequence),
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            result = bot.run_healing('emulator-5554', self.help, self.nav, dialog)
        self.assertIs(result, self.frames['returned'])
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(201, 1694), (776, 1694), (776, 1694), (899, 662)])

    def test_pixel5_missed_quick_selection_never_treats_or_closes(self):
        dialog = self.pixel5_frame('dialog')
        with (patch.object(bot, 'task_screen', return_value=dialog),
              patch.object(bot, 'task_tap') as tap):
            with self.assertRaisesRegex(RuntimeError, '未确认已选中伤兵'):
                bot.run_healing('emulator-5554', self.help, self.nav, dialog)
        tap.assert_called_once_with('emulator-5554', 201, 1694)

    def test_unrecognized_hospital_state_never_silently_closes(self):
        with (patch.object(healing, 'button', return_value=False),
              patch.object(healing, 'in_progress', return_value=False),
              patch.object(bot, 'task_screen', return_value=self.frames['dialog']),
              patch.object(bot, 'task_tap') as tap):
            with self.assertRaisesRegex(RuntimeError, '未确认快速选择'):
                bot.run_healing('test', self.help, self.nav, self.frames['dialog'])
        tap.assert_not_called()

    def test_task_frame_heals_before_continuing_without_requiring_alliance_help(self):
        sequence = ['wilderness', 'dialog', 'selected', 'started', 'help-requested', 'returned']
        with (patch.object(bot, 'WILDERNESS_HEALING', (self.help, self.nav)),
              patch.object(bot, 'TASK_HELP', None),
              patch.object(bot, 'color_screenshot', side_effect=[self.frames[n] for n in sequence]),
              patch.object(bot, 'social_back_target', return_value=None),
              patch.object(bot.hunting, 'button_target', return_value=None),
              patch.object(bot.time, 'sleep'), patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            result = bot.task_screen('test', help_enabled=False)
        self.assertIs(result, self.frames['returned'])
        self.assertFalse(bot.HEALING_ACTIVE)
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(837, 1994), (200, 1553), (775, 1553), (775, 1553), (899, 662)])

    def test_disabled_automatic_healing_does_not_interrupt_field_task(self):
        with patch.object(bot, 'WILDERNESS_HEALING', None), patch.object(bot, 'run_healing') as heal:
            self.assertIs(bot.check_wilderness_healing('test', self.frames['wilderness']), self.frames['wilderness'])
        heal.assert_not_called()

    def setUp(self):
        self.frames = {}
        samples = {'before': 'city-ready', 'wilderness': 'field', 'dialog': 'dialog-sampled',
                   'selected': 'selected', 'started': 'progress-sampled',
                   'help-requested': 'help-requested', 'returned': 'returned'}
        for name, sample in samples.items():
            with Image.open(bot.ROOT / f'tests/fixtures/nox/heal-big-20261018-{sample}.png') as image:
                self.frames[name] = image.convert('RGB')
        self.help = bot.open_template(bot.ROOT / 'assets/emulator-help-icon.png').convert('L')
        self.nav = bot.open_template(bot.ROOT / 'assets/emulator-city-nav.png').convert('L')
        town = patch.object(bot, 'TOWN_TEMPLATE', bot.open_template(bot.ROOT / 'assets/emulator-town-button.png').convert('L'))
        town.start()
        self.addCleanup(town.stop)

    def test_only_untreated_wilderness_icon_triggers(self):
        for name, image in self.frames.items():
            with self.subTest(name=name):
                target = healing.entry(bot, image, self.help, self.nav)
                self.assertEqual(target, (837, 1994) if name == 'wilderness' else None)

    def test_full_flow_waits_for_delayed_treatment_and_help_then_returns_to_map(self):
        sequence = ['dialog', 'selected', 'selected', 'started', 'started', 'help-requested', 'returned']
        with (patch.object(bot, 'task_screen', side_effect=[self.frames[n] for n in sequence]),
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            result = bot.run_healing('test', self.help, self.nav, self.frames['wilderness'])
        self.assertIs(result, self.frames['returned'])
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(837, 1994), (200, 1553), (775, 1553), (775, 1553), (899, 662)])

    def test_existing_treatment_requests_help_without_restarting_or_cancelling(self):
        with (patch.object(bot, 'task_screen', side_effect=[self.frames['help-requested'], self.frames['returned']]),
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_healing('test', self.help, self.nav, self.frames['started'])
        self.assertEqual([call.args[1:] for call in tap.call_args_list], [(775, 1553), (899, 662)])

    def test_already_requested_help_only_closes_popup(self):
        with (patch.object(bot, 'task_screen', return_value=self.frames['returned']),
              patch.object(bot, 'task_tap') as tap):
            bot.run_healing('test', self.help, self.nav, self.frames['help-requested'])
        tap.assert_called_once_with('test', 899, 662)

    def test_no_wounds_does_not_tap(self):
        with patch.object(bot, 'task_tap') as tap:
            result = bot.run_healing('test', self.help, self.nav, self.frames['returned'])
        tap.assert_not_called()
        self.assertIs(result, self.frames['returned'])

    def test_missing_treatment_confirmation_never_clicks_help_or_close_blindly(self):
        with (patch.object(bot, 'task_screen', side_effect=[self.frames['selected']] * 9),
              patch.object(bot, 'task_tap') as tap):
            with self.assertRaisesRegex(RuntimeError, '未确认联盟互助'):
                bot.run_healing('test', self.help, self.nav, self.frames['dialog'])
        self.assertEqual([call.args[1:] for call in tap.call_args_list], [(200, 1553), (775, 1553)])

    def test_stop_during_selection_never_starts_treatment(self):
        with (patch.object(bot, 'task_screen', side_effect=StopRequested()),
              patch.object(bot, 'task_tap') as tap):
            with self.assertRaises(StopRequested):
                bot.run_healing('test', self.help, self.nav, self.frames['dialog'])
        tap.assert_called_once_with('test', 200, 1553)

    def test_resource_shortage_is_closed_and_returns_to_map_without_requesting_help(self):
        shortage = Image.new('RGB', bot.EXPECTED_SIZE)
        with (patch.object(bot, 'task_screen', side_effect=[self.frames['selected'], shortage,
                                                          self.frames['selected'], self.frames['returned']]),
              patch.object(bot, 'resource_more_target', side_effect=lambda image: (995, 550) if image is shortage else None),
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            result = bot.run_healing('test', self.help, self.nav, self.frames['dialog'])
        self.assertIs(result, self.frames['returned'])
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(200, 1553), (775, 1553), (995, 550), (899, 662)])

    def test_unchanged_help_button_does_not_claim_success(self):
        with (patch.object(bot, 'task_screen', return_value=self.frames['started']),
              patch.object(bot, 'task_tap') as tap):
            with self.assertRaisesRegex(RuntimeError, '互助请求尚未确认'):
                bot.run_healing('test', self.help, self.nav, self.frames['started'])
        tap.assert_called_once_with('test', 775, 1553)

    def test_shifted_hospital_popup_keeps_actions_on_buttons(self):
        shifted = self.frames['dialog'].copy()
        shifted.paste(self.frames['dialog'].crop((80, 608, 960, 1668)), (90, 548))
        offset = healing.page(bot, shifted)
        self.assertEqual(offset, (10, -202))
        self.assertTrue(healing.button(bot, shifted, 'quick', offset))
        self.assertTrue(healing.button(bot, shifted, 'treat', offset))

    def test_setting_defaults_on_is_persisted_and_independent_of_training(self):
        config = gui_web.default_config()
        self.assertIn('--heal', gui_web.build_command(config, 'emulator-5556'))
        config['options']['heal'] = False
        self.assertNotIn('--heal', gui_web.build_command(config, 'emulator-5556'))
        config['options'].update(heal=True, train=False, help=False)
        with tempfile.TemporaryDirectory() as directory:
            save_config(directory, 'avd:test', 'emulator-5556', config)
            restored = gui_web.checked_config(load_config(directory, 'avd:test'), from_disk=True)
        command = gui_web.build_command(restored, 'emulator-5556')
        self.assertIn('--heal', command)
        self.assertIn('--no-train', command)
        self.assertIn('--no-help', command)

    def test_scheduler_heals_after_wilderness_tasks_and_continues_other_tasks(self):
        clock, events = Clock(), []
        frame = self.frames['returned']
        def task(name):
            events.append(name)
            return frame
        def monitor(_, seconds, *args, **kwargs):
            self.assertTrue(kwargs['allow_wilderness'])
            clock.sleep(seconds)
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'run', side_effect=monitor),
              patch.object(bot, 'pet_page', return_value=False),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'recover_task_city', return_value=frame) as recover,
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 31, 2, False,
                             {name: lambda name=name: task(name) for name in ('intelligence', 'gather', 'heal')},
                             self.help, self.nav, None, None, help_enabled=False)
        self.assertEqual(events, ['intelligence', 'gather', 'heal', 'heal', 'heal'])
        recover.assert_not_called()


if __name__ == '__main__':
    unittest.main()
