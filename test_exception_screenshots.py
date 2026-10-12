"""Failure screenshots must survive recovery and never hide the original error."""

import contextlib
from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import gui_web as desktop
import help_bot as bot


class ExceptionScreenshotTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root = patch.object(bot, 'ROOT', self.root)
        root.start()
        self.addCleanup(root.stop)
        self.frame = Image.new('RGB', (32, 48), (10, 80, 200))

    def test_only_third_retry_failure_is_saved_before_outer_recovery(self):
        events = []

        def operation():
            events.append('operation')
            raise ValueError('页面异常')

        def capture(_serial):
            events.append('capture')
            return self.frame

        with (patch.object(bot, 'color_screenshot', side_effect=capture),
              patch.object(bot, 'screenshot', return_value=self.frame.convert('L')),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot.time, 'sleep', side_effect=lambda _: events.append('wait')),
              contextlib.redirect_stdout(io.StringIO()) as output):
            with self.assertRaisesRegex(bot.RetriesExhausted, '页面异常') as raised:
                bot.retry_once('自动练兵', operation, 'emulator-test', None, None,
                               lambda: events.append('recover'))

        self.assertEqual(events, ['operation', 'wait', 'recover'] * 3 + ['operation', 'capture'])
        paths = list((self.root / 'artifacts' / bot.DIAGNOSTIC_SCREENSHOT_DIR).glob('*/*/exception-*.png'))
        self.assertEqual(len(paths), 1)
        failures = [line for line in output.getvalue().splitlines() if '异常：页面异常' in line]
        self.assertEqual(len(failures), 4)
        self.assertNotIn('失败截图：', failures[0])
        self.assertNotIn('失败截图：', failures[1])
        self.assertNotIn('失败截图：', failures[2])
        self.assertNotIn('同一异常', output.getvalue())
        for path in paths:
            self.assertIn(str(path), failures[3])
            with Image.open(path) as saved:
                # A wrong screen size is still useful evidence and keeps its color.
                self.assertEqual(saved.size, self.frame.size)
                self.assertEqual(saved.getpixel((1, 1)), (10, 80, 200))
        with patch.object(bot, 'color_screenshot') as capture_again:
            diagnostic = bot.exception_screenshot('emulator-test', raised.exception)
        capture_again.assert_not_called()
        self.assertIn(diagnostic, failures[-1])

    def test_bounty_first_failure_saves_scene_and_context_before_recovery(self):
        events, state = [], {}
        original = RuntimeError('未确认大师悬赏任务详情')
        with (patch.object(bot, 'task_tap', side_effect=lambda *a: events.append('tap')),
              patch.object(bot, 'wait_intelligence_detail', return_value=self.frame),
              patch.object(bot, 'intelligence_page', return_value=False),
              patch.object(bot, 'task_screen', return_value=self.frame),
              patch.object(bot, 'bounty_name', side_effect=original),
              patch.object(bot, 'color_screenshot', side_effect=lambda _: (
                  events.append('capture') or self.frame)),
              contextlib.redirect_stdout(io.StringIO()) as output):
            with self.assertRaises(RuntimeError) as raised:
                bot.execute_bounty('emulator-5556', self.frame, (500, 1200), state)
        self.assertIs(raised.exception, original)
        self.assertEqual(events, ['tap', 'capture'])
        self.assertTrue(state['disabled'])
        paths = list((self.root / 'artifacts' / bot.DIAGNOSTIC_SCREENSHOT_DIR).glob('*/*/*.png'))
        self.assertEqual(len(paths), 1)
        metadata = json.loads(paths[0].with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['device'], 'emulator-5556')
        self.assertEqual(metadata['stage'], '任务详情')
        self.assertEqual(metadata['target'], [500, 1200])
        self.assertIn(str(original), metadata['reason'])
        self.assertIn(str(paths[0]), output.getvalue())
        self.assertIn(str(original), output.getvalue())
        self.assertEqual(original.diagnostic_screenshot, state['diagnostic_screenshot'])

    def test_bounty_capture_failure_preserves_original_error_and_pause(self):
        state = {}
        with (patch.object(bot, 'task_tap'),
              patch.object(bot, 'wait_intelligence_detail', return_value=self.frame),
              patch.object(bot, 'intelligence_page', return_value=False),
              patch.object(bot, 'task_screen', return_value=self.frame),
              patch.object(bot, 'bounty_name', return_value=None),
              patch.object(bot, 'color_screenshot', side_effect=OSError('ADB断开')),
              contextlib.redirect_stdout(io.StringIO()) as output):
            with self.assertRaisesRegex(RuntimeError, '未确认大师悬赏任务详情'):
                bot.execute_bounty('emulator-5556', self.frame, (500, 1200), state)
        self.assertTrue(state['disabled'])
        self.assertIn('ADB断开', output.getvalue())

    def test_capture_errors_preserve_task_reason_and_retry_policy(self):
        failures = (subprocess.TimeoutExpired('adb', 15),
                    subprocess.CalledProcessError(1, 'adb'),
                    OSError('设备断开'), ValueError('PNG 损坏'))
        for failure in failures:
            with (self.subTest(failure=type(failure).__name__),
                patch.object(bot, 'color_screenshot', side_effect=failure),
                patch.object(bot, 'screenshot', side_effect=OSError('设备断开')),
                patch.object(bot.time, 'sleep') as wait,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                with self.assertRaisesRegex(bot.RetriesExhausted, '原始异常'):
                    bot.retry_once('仓库补给', lambda: (_ for _ in ()).throw(RuntimeError('原始异常')),
                                   'emulator-test', None, None)
                self.assertEqual(wait.call_count, 3)
                self.assertEqual(output.getvalue().count('失败截图保存失败：'), 1)
                self.assertIn(type(failure).__name__, output.getvalue())

    def test_unwritable_directory_reports_capture_failure(self):
        (self.root / 'artifacts').write_text('not a directory', encoding='utf-8')
        with patch.object(bot, 'color_screenshot', return_value=self.frame):
            diagnostic = bot.save_task_failure('emulator-test', '生命结晶', 'first')
        self.assertIn('失败截图保存失败：', diagnostic)

    def test_missing_device_does_not_attempt_adb_capture(self):
        with patch.object(bot, 'color_screenshot') as capture:
            diagnostic = bot.save_task_failure(None, '查找设备', 'first')
        capture.assert_not_called()
        self.assertIn('尚未确定模拟器', diagnostic)

    def test_screenshots_are_grouped_by_device_and_capture_date(self):
        directory = self.root / 'artifacts' / bot.DIAGNOSTIC_SCREENSHOT_DIR
        with (patch.object(bot, 'color_screenshot', return_value=self.frame),
              patch.object(bot, 'datetime') as clock):
            clock.now.return_value = datetime(2026, 10, 5, 23, 59, 59, 123456)
            first = bot.save_task_failure('emulator-5554', '练兵', 'first')
            bot.save_task_failure('emulator-5556', '宠物寻宝', 'first')
            bot.save_task_failure('127.0.0.1:5555', '史诗招募', 'first')
            clock.now.return_value = datetime(2026, 10, 6, 0, 0, 0, 123456)
            bot.save_task_failure('emulator-5554', '练兵', 'first')
        expected = [
            directory / 'emulator-5554/2026-10-05/exception-20261005-235959-123456.png',
            directory / 'emulator-5556/2026-10-05/treasure-failure-20261005-235959-123456.png',
            directory / '127_0_0_1_5555/2026-10-05/recruit-failure-20261005-235959-123456.png',
            directory / 'emulator-5554/2026-10-06/exception-20261006-000000-123456.png',
        ]
        self.assertEqual(set(directory.glob('*/*/*.png')), set(expected))
        self.assertIn(str(expected[0]), first)

    def test_cleanup_failure_preserves_the_saved_path_and_original_error(self):
        with (patch.object(bot, 'color_screenshot', return_value=self.frame),
              patch.object(bot, 'screenshot', return_value=self.frame.convert('L')),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'maybe_cleanup_diagnostic_screenshots', side_effect=RuntimeError('清理异常')),
              patch.object(bot.time, 'sleep'),
              contextlib.redirect_stdout(io.StringIO()) as output):
            with self.assertRaisesRegex(bot.RetriesExhausted, '原始异常'):
                bot.retry_once('自动练兵', lambda: (_ for _ in ()).throw(ValueError('原始异常')),
                               'emulator-test', None, None)
        paths = list((self.root / 'artifacts' / bot.DIAGNOSTIC_SCREENSHOT_DIR).glob('*/*/exception-*.png'))
        self.assertEqual(len(paths), 1)
        self.assertIn('故障截图清理失败：RuntimeError: 清理异常', output.getvalue())
        for path in paths:
            self.assertIn(str(path), output.getvalue())

    def test_offline_interruptions_never_accumulate_to_a_screenshot(self):
        original = RuntimeError('任务中断')
        with (patch.object(bot, 'color_screenshot') as capture,
              patch.object(bot, 'screenshot', return_value=self.frame.convert('L')),
              patch.object(bot, 'forced_offline', return_value=True),
              patch.object(bot.time, 'sleep') as wait,
              contextlib.redirect_stdout(io.StringIO()) as output):
            for _ in range(4):
                with self.assertRaises(RuntimeError) as raised:
                    bot.retry_once('晨曦回礼', lambda: (_ for _ in ()).throw(original),
                                   'emulator-test', None, None)
        self.assertIs(raised.exception, original)
        wait.assert_not_called()
        capture.assert_not_called()
        self.assertNotIn('同一异常', output.getvalue())

    def test_main_reuses_exhausted_retry_screenshot_in_exit_message(self):
        def exhausted(*_args, **_kwargs):
            with (patch.object(bot, 'forced_offline', return_value=False),
                  patch.object(bot, 'screenshot', return_value=self.frame.convert('L')),
                  patch.object(bot.time, 'sleep')):
                bot.retry_once('监控', lambda: (_ for _ in ()).throw(ValueError('意外错误')),
                               'emulator-test', None, None)

        with (patch.object(bot.sys, 'argv', ['help_bot.py', '--device', 'emulator-test', '--forever']),
              patch.object(bot.Image, 'open', return_value=self.frame),
              patch.object(bot, 'emulator_device', return_value=True),
              patch.object(bot, 'return_to_city', return_value=True),
              patch.object(bot, 'run_schedule', side_effect=exhausted),
              patch.object(bot, 'color_screenshot', return_value=self.frame) as capture,
              contextlib.redirect_stdout(io.StringIO()),
              contextlib.redirect_stderr(io.StringIO()) as errors):
            with self.assertRaises(SystemExit) as raised:
                bot.main()
        self.assertEqual(raised.exception.code, 1)
        capture.assert_called_once_with('emulator-test')
        path, = (self.root / 'artifacts' / bot.DIAGNOSTIC_SCREENSHOT_DIR).glob('*/*/exception-*.png')
        reason = desktop.exception_reason(errors.getvalue().splitlines(), 1)
        self.assertIn('意外错误', reason)
        self.assertIn(str(path), reason)

    def test_first_startup_asset_failure_does_not_capture(self):
        with (patch.object(bot.sys, 'argv', ['help_bot.py', '--device', 'emulator-test']),
              patch.object(bot.Image, 'open', side_effect=FileNotFoundError('模板缺失')),
              patch.object(bot, 'color_screenshot', return_value=self.frame),
              contextlib.redirect_stderr(io.StringIO()) as errors):
            with self.assertRaises(SystemExit):
                bot.main()
        self.assertIn('模板缺失', errors.getvalue())
        self.assertNotIn('失败截图：', errors.getvalue())
        self.assertNotIn('同一异常', errors.getvalue())
        self.assertFalse((self.root / 'artifacts').exists())

    def test_three_failed_retries_capture_even_when_reasons_change(self):
        errors = iter((ValueError('原因 A'), ValueError('原因 B'),
                       ValueError('原因 A'), ValueError('原因 B')))

        def operation():
            raise next(errors)

        with (patch.object(bot, 'color_screenshot') as capture,
              patch.object(bot, 'screenshot', return_value=self.frame.convert('L')),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot.time, 'sleep'),
              contextlib.redirect_stdout(io.StringIO()) as output):
            with self.assertRaises(bot.RetriesExhausted):
                bot.retry_once('练兵', operation, 'emulator-test', None, None)
        capture.assert_called_once_with('emulator-test')
        self.assertIn('失败截图：', output.getvalue())

    def test_separate_retry_cycles_do_not_accumulate_failures(self):
        with (patch.object(bot, 'color_screenshot') as capture,
              patch.object(bot, 'screenshot', return_value=self.frame.convert('L')),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot.time, 'sleep'),
              contextlib.redirect_stdout(io.StringIO()) as output):
            for _ in range(4):
                outcomes = iter((ValueError('同一原因'), ValueError('同一原因'),
                                 ValueError('同一原因'), 'ok'))
                def operation():
                    result = next(outcomes)
                    if isinstance(result, Exception):
                        raise result
                    return result
                self.assertEqual(bot.retry_once('练兵', operation, 'emulator-test', None, None), 'ok')
        capture.assert_not_called()
        self.assertNotIn('失败截图', output.getvalue())
        self.assertNotIn('同一异常', output.getvalue())

    def test_outer_handlers_do_not_capture_unretried_errors(self):
        original = RuntimeError('同一个异常')
        with patch.object(bot, 'color_screenshot') as capture:
            for _ in range(4):
                self.assertEqual(bot.exception_screenshot('emulator-test', original), '')
        capture.assert_not_called()

    def test_keyboard_interrupt_does_not_capture_failure(self):
        with (patch.object(bot.sys, 'argv', ['help_bot.py', '--device', 'emulator-test']),
              patch.object(bot.Image, 'open', return_value=self.frame),
              patch.object(bot, 'emulator_device', side_effect=KeyboardInterrupt),
              patch.object(bot, 'color_screenshot') as capture,
              contextlib.redirect_stdout(io.StringIO()) as output):
            bot.main()
        capture.assert_not_called()
        self.assertIn('已由键盘停止', output.getvalue())

    def test_cleanup_only_prunes_generated_exception_screenshots(self):
        artifacts = self.root / 'artifacts'
        directory = artifacts / bot.DIAGNOSTIC_SCREENSHOT_DIR / 'emulator-5554' / '2026-10-05'
        directory.mkdir(parents=True)
        now = 2000000000
        for index in range(23):
            recent = directory / f'exception-20261005-120000-{index:06d}.png'
            recent.touch()
            os.utime(recent, (now, now))
        old_directory = artifacts / bot.DIAGNOSTIC_SCREENSHOT_DIR / 'emulator-5556' / '2026-09-27'
        old_directory.mkdir(parents=True)
        old = old_directory / 'exception-20260927-120000-000000.png'
        old.touch()
        old.with_suffix('.json').write_text('{}', encoding='utf-8')
        os.utime(old, (now - 7 * 86400 - 1, now - 7 * 86400 - 1))
        boundary = directory / 'exception-20260928-120000-000000.png'
        boundary.touch()
        os.utime(boundary, (now - 7 * 86400, now - 7 * 86400))
        manual = directory / 'exception-manual.png'
        manual.touch()
        os.utime(manual, (0, 0))
        fixture = artifacts / 'tree-page.png'
        fixture.touch()
        os.utime(fixture, (0, 0))
        with (patch.object(bot.time, 'time', return_value=now),
              contextlib.redirect_stdout(io.StringIO())):
            bot.cleanup_diagnostic_screenshots()
        self.assertEqual(len(list(directory.glob('exception-20261005-*.png'))), 23)
        self.assertFalse(old.exists())
        self.assertFalse(old.with_suffix('.json').exists())
        self.assertFalse(old_directory.parent.exists())
        self.assertTrue(boundary.exists())
        self.assertTrue(manual.exists())
        self.assertTrue(fixture.exists())


if __name__ == '__main__':
    unittest.main()
