"""Regression checks for deadlines, startup switches, delayed help and island claims."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import help_bot as bot
import gui
from test_help_bot import Clock

PRIORITY_HELP = bot.check_priority_help


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        priority = patch.object(bot, 'check_priority_help')
        priority.start()
        self.addCleanup(priority.stop)

    def test_three_exhausted_tasks_stop_and_success_resets_streak(self):
        for outcomes, stopped in (([False, False, False, True], True),
                                  ([False, False, True, False, False, True], False)):
            clock, calls = Clock(), []
            def task(index):
                calls.append(index)
                if not outcomes[index]:
                    raise RuntimeError('页面异常')
            with (patch.object(bot.time, 'monotonic', clock.monotonic),
                  patch.object(bot.time, 'sleep', clock.sleep),
                  patch.object(bot, 'screenshot'),
                  patch.object(bot, 'forced_offline', return_value=False),
                  patch.object(bot, 'recover_task_city'),
                  contextlib.redirect_stdout(io.StringIO()) as output):
                tasks = {i: lambda i=i: task(i) for i in range(len(outcomes))}
                if stopped:
                    with self.assertRaisesRegex(bot.ConsecutiveTaskFailures, '三个任务连续执行异常'):
                        bot.run_schedule('test', 1, 2, False, tasks, *([None] * 4))
                    self.assertEqual(calls, [0] * 4 + [1] * 4 + [2] * 4)
                    self.assertIn('⚠️ 当前已出现三个任务连续执行异常因此自动中断脚本', output.getvalue())
                else:
                    bot.run_schedule('test', 1, 2, False, tasks, *([None] * 4))
                    self.assertEqual(calls[-1], 5)

    def test_upgrading_units_never_read_timer_or_tap(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for statuses, expected, count in ((['busy', 'upgrading', 'busy'], 250, 2),
                                           (['upgrading'] * 3, 700, 0)):
            timers = {'train': 1}
            with (patch.object(bot, 'reset_drawer', return_value=frame),
                  patch.object(bot, 'inspect', return_value=(True, None, 0)),
                  patch.object(bot, 'training_status', side_effect=statuses),
                  patch.object(bot, 'countdown', side_effect=[300, 150]) as read,
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot.time, 'monotonic', return_value=100),
                  contextlib.redirect_stdout(io.StringIO())):
                bot.run_training('test', None, None, None, {}, initial_image=frame,
                                 timers=timers, keep_open=True)
            tap.assert_not_called()
            self.assertEqual(read.call_count, count)
            self.assertEqual(timers['train'], expected)

    def test_pet_cooldowns_are_independent(self):
        clock, timers, events = Clock(), {}, []
        names = list(bot.PET_SKILLS)
        def task(name):
            events.append((name, clock.now))
            bot.record_timer(timers, name, 10 if name == names[0] else 1000)
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot, 'run', side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds)),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 25, 2, False,
                             {name: lambda name=name: task(name) for name in names},
                             *([None] * 4), timers=timers)
        self.assertEqual(events, [(name, 100) for name in names] + [(names[0], 110), (names[0], 120)])

    def test_pet_use_only_when_ready_and_record_confirmed_cooldown(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE)
        for state, uses, delay in [('ready', 1, 3600), ('cooldown', 0, 120), ('unknown', 0, 60)]:
            timers, actions = {}, []
            states = [(state, 120 if state == 'cooldown' else None), ('cooldown', 3600)]
            with (patch.object(bot, 'color_screenshot', return_value=frame),
                  patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'inspect', return_value=(True, None, 0)),
                  patch.object(bot, 'task_page', return_value=True),
                  patch.object(bot, 'difference', return_value=0),
                  patch.object(bot, 'pet_page', return_value=True),
                  patch.object(bot, 'pet_skill_state', side_effect=states),
                  patch.object(bot, 'task_tap', side_effect=lambda _, x, y: actions.append((x, y))),
                  patch.object(bot.time, 'monotonic', return_value=100),
                  contextlib.redirect_stdout(io.StringIO())):
                bot.run_pet_skill('test', None, None, None, 'pet_companion', timers)
            self.assertEqual(actions[0], (695, 1100))  # Drawer closes before the paw entry.
            self.assertEqual(actions[-1], (995, 505))
            self.assertEqual(actions.count((540, 1910)), uses)
            self.assertEqual(timers, {'pet_companion': 100 + delay})

    def test_pet_state_requires_name_and_enabled_use_button(self):
        frame = Image.new('RGB', bot.EXPECTED_SIZE, (60, 150, 240))
        with (patch.object(bot, 'pet_page', return_value=True),
              patch.object(bot, 'recognize_text', side_effect=[ [('心灵伴侣 等级3', ())], [('使用', ())] ])):
            self.assertEqual(bot.pet_skill_state(frame, '心灵伴侣'), ('ready', None))
        frame.paste((120, 120, 120), (350, 1870, 420, 1940))
        with (patch.object(bot, 'pet_page', return_value=True),
              patch.object(bot, 'recognize_text', side_effect=[ [('心灵伴侣 等级3', ())], [('使用', ())] ])):
            self.assertEqual(bot.pet_skill_state(frame, '心灵伴侣'), ('unknown', None))
        with (patch.object(bot, 'pet_page', return_value=True),
              patch.object(bot, 'recognize_text', return_value=[('其他技能 等级3', ())])):
            with self.assertRaisesRegex(RuntimeError, '未确认宠物技能'):
                bot.pet_skill_state(frame, '心灵伴侣')

    def test_pet_and_upgrade_real_samples(self):
        for name, sample, seconds in zip(bot.PET_SKILLS, (2, 3, 4, 6), (60815, 60790, 60790, 118411)):
            frame = Image.open(bot.ROOT / f'artifacts/pet-selected-{sample}.png').convert('RGB')
            self.assertEqual(bot.pet_skill_state(frame, bot.PET_SKILLS[name][0]), ('cooldown', seconds))
        frame = Image.open(bot.ROOT / 'artifacts/training-upgrade.png').convert('RGB')
        self.assertEqual([bot.training_status(frame, y) for _, y in bot.TRAIN_ROWS],
                         ['busy', 'busy', 'upgrading'])

    def test_startup_order_and_disabled_tasks(self):
        for disabled in ([], ['train', 'donate', 'warehouse', 'recruit', 'pet']):
            options = ['help_bot.py', '--device', 'emulator-test', '--daily-tasks']
            options += ['--no-' + name for name in disabled]
            with (patch('sys.argv', options), patch.object(bot, 'return_to_city', return_value=True),
                  patch.object(bot, 'run_schedule') as schedule):
                bot.main()
            tasks = schedule.call_args.args[4]
            self.assertEqual(list(tasks), [name for name in
                ['explore', 'train', 'donate', *bot.RECRUIT_TYPES, 'warehouse', 'stamina', 'tree', 'dawn', *bot.PET_SKILLS]
                if name not in disabled and not (name in bot.RECRUIT_TYPES and 'recruit' in disabled)
                and not (name in bot.PET_SKILLS and 'pet' in disabled)
                and not (name == 'stamina' and 'warehouse' in disabled)])
            with (patch.object(bot, 'run_training') as train,
                  patch.object(bot, 'run_city_tasks') as city,
                  patch.object(bot, 'run_rewards') as rewards, patch.object(bot, 'run_stamina') as stamina,
                  patch.object(bot, 'run_explore'), patch.object(bot, 'run_pet_skill') as pet):
                for task in tasks.values():
                    task()
            self.assertEqual(stamina.call_count, int('warehouse' not in disabled))
            self.assertEqual(pet.call_count, 0 if 'pet' in disabled else 4)
            self.assertEqual(train.call_count, int('train' not in disabled))
            if 'recruit' not in disabled:
                self.assertEqual([call.kwargs['recruit_kind'] for call in city.call_args_list
                                  if call.kwargs.get('donate') is False], list(bot.RECRUIT_TYPES))
            for call in (*train.call_args_list, *city.call_args_list, *rewards.call_args_list):
                self.assertTrue(call.kwargs['keep_open'])
            self.assertEqual([call.kwargs['tree'] for call in rewards.call_args_list],
                             [name == 'tree' for name in ['warehouse', 'tree', 'dawn'] if name not in disabled])

    def test_timers_and_ten_minute_tasks_are_independent(self):
        clock, timers, events = Clock(), {}, []
        names = ['train', 'donate', *bot.RECRUIT_TYPES, 'warehouse', 'stamina', 'tree', 'dawn', *bot.PET_SKILLS]
        delays = dict(train=80, recruit_advanced=200, recruit_epic=300, warehouse=1100, stamina=120, dawn=1400)
        def task(name):
            events.append((name, clock.now))
            if name in delays:
                bot.record_timer(timers, name, delays[name])
        def monitor(_serial, seconds, *_args, **_kwargs):
            clock.sleep(seconds)
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot, 'run', side_effect=monitor), contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 650, 2, False, {name: lambda name=name: task(name) for name in names},
                             *([None] * 4), timers=timers)
        self.assertEqual(events[:len(names)], [(name, 100) for name in names])
        self.assertEqual([when for name, when in events if name == 'warehouse'], [100])
        self.assertEqual([when for name, when in events if name == 'stamina'], [100, 220, 340, 460, 580, 700])
        self.assertEqual([when for name, when in events if name == 'donate'], [100, 700])
        self.assertEqual([when for name, when in events if name == 'tree'], [100, 700])
        self.assertEqual([when for name, when in events if name == 'train'], list(range(100, 741, 80)))
        self.assertEqual([when for name, when in events if name == 'recruit_advanced'], [100, 300, 500, 700])
        self.assertEqual([when for name, when in events if name == 'recruit_epic'], [100, 400, 700])

    def test_monitor_reconnect_invalidates_all_deadlines(self):
        clock, timers, events = Clock(), {}, []
        def task():
            events.append(clock.now)
            bot.record_timer(timers, 'train', 10000)
        def monitor(_serial, seconds, *_args, **kwargs):
            self.assertTrue(kwargs['return_on_reconnect'])
            clock.sleep(10 if len(events) == 1 else seconds)
            return len(events) == 1
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot, 'run', side_effect=monitor), contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 25, 2, False, {'train': task}, *([None] * 4), timers=timers)
        self.assertEqual(events, [100, 110])

    def test_help_precedes_tasks_on_startup_and_after_reconnect(self):
        clock, events = Clock(), []
        def monitor(_serial, seconds, *_args, **_kwargs):
            clock.sleep(10 if events.count('task') == 1 else seconds)
            return events.count('task') == 1
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot, 'check_priority_help', side_effect=lambda *_: events.append('help')),
              patch.object(bot, 'run', side_effect=monitor),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 25, 2, False,
                             {'train': lambda: events.append('task') or bot.record_timer(timers, 'train', 10000)},
                             *([None] * 4), timers=(timers := {}))
        self.assertEqual(events, ['help', 'task', 'help', 'task'])

    def test_disabled_help_skips_priority_check(self):
        clock = Clock()
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot, 'check_priority_help') as check,
              patch.object(bot, 'run', side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds))):
            bot.run_schedule('test', 1, 2, False, {}, *([None] * 4), help_enabled=False)
        check.assert_not_called()

    def test_priority_help_checks_city_and_offline_before_task_screen(self):
        frame = Image.new('L', bot.EXPECTED_SIZE)
        with (patch.object(bot, 'screenshot', return_value=frame),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'inspect', return_value=(True, (30, 0, 0), 0)),
              patch.object(bot, 'task_screen') as check):
            PRIORITY_HELP('test', None, None, None, None)
        check.assert_called_once_with('test')
        with (patch.object(bot, 'screenshot', return_value=frame),
              patch.object(bot, 'forced_offline', return_value=True),
              patch.object(bot, 'task_screen') as check):
            with self.assertRaisesRegex(RuntimeError, '强制下线'):
                PRIORITY_HELP('test', None, None, None, None)
        check.assert_not_called()

    def test_training_uses_earliest_selected_unit_completion(self):
        frame = Image.new('RGB', (1080, 2340))
        timers = {}
        with (patch.object(bot, 'TASK_DRAWER_SCROLL', 0),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_drawer', return_value=frame),
              patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'task_page', return_value=True),
              patch.object(bot, 'task_tap'), patch.object(bot, 'training_status', return_value='busy'),
              patch.object(bot, 'countdown', side_effect=[300, 150]),
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_training('test', None, None, None, {}, initial_image=frame,
                             keep_open=True, units={'shield', 'archer'}, timers=timers)
        self.assertEqual(timers['train'], 250)

    def test_countdown_parser_and_log_reason(self):
        for text, expected in [('08:05:37', 29137), ('1天 02:18:33', 94713),
                               ('00：00：01', 1), ('12天11:13:00', 1077180), ('已完成', None),
                               ('01:80:00', None), ('1,234/5,040', None)]:
            self.assertEqual(bot.parse_countdown(text), expected)
        line = gui.timestamp_line('运行中')
        self.assertRegex(line, r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 运行中$')
        self.assertEqual(gui.timestamp_line(line), line)
        self.assertEqual(gui.exception_reason(['正常日志', gui.timestamp_line('已停止：真正原因')], 1), '真正原因')
        self.assertEqual(gui.exception_reason(['历史日志', 'ValueError: broken'], 1), 'ValueError: broken')

    def test_delayed_help_and_unchanged_help_do_not_stop(self):
        frame = Image.new('L', (1080, 2340))
        clock = Clock()
        with (patch.object(bot, 'inspect', side_effect=[(True, (0, 0, 0), 0)] * 2 + [(True, (30, 0, 0), 0)]),
              patch.object(bot, 'chat_page', return_value=False), patch.object(bot, 'HELP_COUNT_BOX', None),
              patch.object(bot.time, 'sleep', clock.sleep)):
            self.assertTrue(bot.wait_help_update('test', frame, frame, None, None, lambda _: frame)[1])
        self.assertEqual(clock.now, 102)
        with (patch.object(bot, 'inspect', return_value=(True, (0, 0, 0), 0)),
              patch.object(bot, 'chat_page', return_value=False), patch.object(bot, 'HELP_COUNT_BOX', None),
              patch.object(bot, 'HELP_RETRY_AT', 0), patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep), contextlib.redirect_stdout(io.StringIO())):
            self.assertFalse(bot.wait_help_update('test', frame, frame, None, None, lambda _: frame)[1])
            self.assertEqual(bot.HELP_RETRY_AT, clock.now + 30)

    def test_multiple_crystals_are_all_claimed(self):
        frame = Image.new('RGB', (1080, 2340))
        with (patch.object(bot, 'crystal_target', side_effect=[(200, 700), None, (600, 900), None, None]),
              patch.object(bot, 'task_tap') as tap, patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'island_page', return_value=True), contextlib.redirect_stdout(io.StringIO())):
            bot.collect_crystals('test', frame, None)
        self.assertEqual([call.args[1:] for call in tap.call_args_list], [(200, 700), (600, 900)])

    def test_island_return_waits_for_two_city_frames(self):
        frame = Image.new('RGB', (1080, 2340))
        states = iter((False, True, True))
        with (patch.object(bot, 'task_screen', return_value=frame) as capture,
              patch.object(bot, 'inspect', side_effect=lambda *_: (next(states), None, 0))):
            self.assertIs(bot.wait_city_after_island('test', None, None), frame)
        self.assertEqual(capture.call_count, 3)

    def test_island_return_timeout_saves_failure_frame(self):
        frame = Image.new('RGB', (1080, 2340))
        with (patch.object(bot, 'task_screen', return_value=frame) as capture,
              patch.object(bot, 'inspect', return_value=(False, None, 27.5)),
              patch.object(frame, 'save') as save):
            with self.assertRaisesRegex(RuntimeError, '返回主城超时；底栏差异 27.5'):
                bot.wait_city_after_island('test', None, None)
        self.assertEqual(capture.call_count, 8)
        self.assertIn('island-return-', str(save.call_args.args[0]))

    def test_cleanup_only_removes_old_or_excess_generated_screenshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'artifacts'
            directory.mkdir()
            now = 2000000000
            for index in range(23):
                path = directory / f'island-return-20260926-120000-{index:06d}.png'
                path.touch()
                os.utime(path, (now - index, now - index))
            old = directory / 'island-return-20250801-120000-000000.png'
            old.touch()
            os.utime(old, (now - 31 * 86400, now - 31 * 86400))
            fixture = directory / 'tree-page.png'
            fixture.touch()
            manual = directory / 'island-return-not-generated.png'
            manual.touch()
            with (patch.object(bot, 'ROOT', root), patch.object(bot.time, 'time', return_value=now),
                  contextlib.redirect_stdout(io.StringIO())):
                bot.cleanup_diagnostic_screenshots()
            self.assertEqual(len(list(directory.glob('island-return-20260926-*.png'))), 20)
            self.assertFalse(old.exists())
            self.assertTrue(fixture.exists())
            self.assertTrue(manual.exists())

    def test_cleanup_runs_daily_and_can_be_forced_after_save(self):
        clock = Clock()
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot, 'DIAGNOSTIC_CLEANUP_AT', 0),
              patch.object(bot, 'cleanup_diagnostic_screenshots') as clean):
            bot.maybe_cleanup_diagnostic_screenshots()
            clock.sleep(3600)
            bot.maybe_cleanup_diagnostic_screenshots()
            bot.maybe_cleanup_diagnostic_screenshots(force=True)
            clock.sleep(86400)
            bot.maybe_cleanup_diagnostic_screenshots()
        self.assertEqual(clean.call_count, 3)

    def test_startup_can_leave_a_confirmed_island(self):
        island = Image.open(bot.ROOT / 'artifacts/tree-page.png').convert('L')
        city = Image.open(bot.ROOT / 'artifacts/tree-return-city.png').convert('L')
        with (patch.object(bot, 'EXPECTED_SIZE', (1080, 2340)),
              patch.object(bot, 'screenshot', side_effect=[island, city, city, city]),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'inspect', side_effect=[(False, None, 30)] + [(True, None, 0)] * 3),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot.time, 'sleep'),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(bot.return_to_city('test', None, None, None, None))
        tap.assert_called_once_with('test', 60, 190)

    def test_runtime_error_retries_once_after_ten_seconds(self):
        clock, attempts = Clock(), []
        def operation():
            attempts.append(clock.now)
            if len(attempts) == 1:
                raise RuntimeError('临时画面')
            return 'ok'
        with (patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'screenshot', return_value=Image.new('L', (1, 1))),
              patch.object(bot, 'forced_offline', return_value=False),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertEqual(bot.retry_once('生命结晶', operation, 'test', None, None), 'ok')
        self.assertEqual(attempts, [100, 110])
        self.assertIn('首次异常：临时画面；10 秒后第 1 次重试', output.getvalue())

    def test_three_retries_exhaust_with_task_name(self):
        clock, attempts = Clock(), []
        def operation():
            attempts.append(clock.now)
            raise RuntimeError(f'错误 {len(attempts)}')
        with (patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'screenshot', return_value=Image.new('L', (1, 1))),
              patch.object(bot, 'forced_offline', return_value=False),
              contextlib.redirect_stdout(io.StringIO())):
            with self.assertRaisesRegex(bot.RetriesExhausted, '生命结晶 重试失败：错误 4'):
                bot.retry_once('生命结晶', operation, 'test', None, None)
        self.assertEqual(attempts, [100, 110, 120, 130])

    def test_third_retry_can_succeed(self):
        clock, attempts = Clock(), []
        def operation():
            attempts.append(clock.now)
            if len(attempts) < 4:
                raise RuntimeError('临时画面')
            return 'ok'
        with (patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'screenshot', return_value=Image.new('L', (1, 1))),
              patch.object(bot, 'forced_offline', return_value=False),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(bot.retry_once('生命结晶', operation, 'test', None, None), 'ok')
        self.assertEqual(attempts, [100, 110, 120, 130])

    def test_recovery_leaves_confirmed_island_page(self):
        frame = Image.new('RGB', (1080, 2340))
        with (patch.object(bot, 'EXPECTED_SIZE', (1080, 2340)),
              patch.object(bot, 'color_screenshot', return_value=frame),
              patch.object(bot, 'inspect', side_effect=[(False, None, 30), (True, None, 0)]),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'island_page', return_value=True),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'task_screen', return_value=frame)):
            self.assertIs(bot.recover_task_city('test', None, None, None, None), frame)
        tap.assert_called_once_with('test', 60, 190)

    def test_recovery_rewinds_an_open_drawer_before_retry(self):
        frame = Image.new('RGB', (1080, 2340))
        with (patch.object(bot, 'EXPECTED_SIZE', (1080, 2340)),
              patch.object(bot, 'color_screenshot', return_value=frame),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=True),
              patch.object(bot, 'reset_drawer', return_value=frame) as reset):
            self.assertIs(bot.recover_task_city('test', None, None, None, None), frame)
        reset.assert_called_once()

    def test_batch_close_retry_does_not_repeat_completed_tasks(self):
        frame = Image.new('RGB', (1080, 2340))
        clock, calls = Clock(), []
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'task_page', side_effect=[True, False]),
              patch.object(bot, 'task_tap', side_effect=RuntimeError('关闭失败')) as tap,
              patch.object(bot, 'screenshot', return_value=Image.new('L', (1, 1))),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'recover_task_city', return_value=frame),
              patch.object(bot, 'run', side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds)),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 1, 2, False, {'tree': lambda: calls.append('tree') or frame},
                             *([None] * 4))
        self.assertEqual(calls, ['tree'])
        tap.assert_called_once()

    def test_scheduled_task_retries_only_the_failed_task(self):
        clock, calls = Clock(), []
        def task(name):
            calls.append((name, clock.now))
            if name == 'tree' and len([n for n, _ in calls if n == 'tree']) == 1:
                raise RuntimeError('临时画面')
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'screenshot', return_value=Image.new('L', (1, 1))),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'recover_task_city') as recover,
              patch.object(bot, 'run', side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds)),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 5, 2, False,
                             {name: lambda name=name: task(name) for name in ('donate', 'tree', 'dawn')},
                             *([None] * 4))
        self.assertEqual(calls, [('donate', 100), ('tree', 100), ('tree', 110), ('dawn', 110)])
        recover.assert_called_once()

    def test_exhausted_task_is_skipped_on_later_checks(self):
        clock, calls = Clock(), []
        def task(name):
            calls.append((name, clock.now))
            if name == 'tree':
                raise RuntimeError('页面已变化')
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'screenshot', return_value=Image.new('L', (1, 1))),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'recover_task_city'),
              patch.object(bot, 'run', side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds)),
              contextlib.redirect_stdout(io.StringIO()) as output):
            bot.run_schedule('test', 650, 2, False,
                             {name: lambda name=name: task(name) for name in ('donate', 'tree', 'explore')},
                             *([None] * 4))
        self.assertEqual([when for name, when in calls if name == 'tree'], [100, 110, 120, 130])
        self.assertEqual(len([name for name, _ in calls if name == 'donate']), 2)
        self.assertEqual(len([name for name, _ in calls if name == 'explore']), 2)
        self.assertEqual(output.getvalue().count('⚠️ 生命结晶收集 已连续执行失败3次，后续不再执行这个任务'), 1)

    def test_exhausted_task_stays_skipped_after_reconnect_resets_timers(self):
        clock, calls = Clock(), []
        def failed():
            calls.append('tree')
            raise RuntimeError('页面已变化')
        def monitor(_serial, seconds, *_args, **_kwargs):
            clock.sleep(1 if calls.count('donate') == 1 else seconds)
            return calls.count('donate') == 1
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'screenshot', return_value=Image.new('L', (1, 1))),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'recover_task_city'),
              patch.object(bot, 'run', side_effect=monitor),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 40, 2, False,
                             {'tree': failed, 'donate': lambda: calls.append('donate')},
                             *([None] * 4))
        self.assertEqual(calls.count('tree'), 4)
        self.assertEqual(calls.count('donate'), 2)

    def test_monitor_retries_for_only_the_remaining_wait(self):
        clock, waits = Clock(), []
        timers = {}
        def task():
            bot.record_timer(timers, 'train', 1000)
        def monitor(_serial, seconds, *_args, **_kwargs):
            waits.append(seconds)
            if len(waits) == 1:
                raise RuntimeError('监控截图失败')
            clock.sleep(seconds)
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot.time, 'sleep', clock.sleep),
              patch.object(bot, 'screenshot', return_value=Image.new('L', (1, 1))),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot, 'recover_task_city'),
              patch.object(bot, 'run', side_effect=monitor),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 20, 2, False, {'train': task}, *([None] * 4), timers=timers)
        self.assertEqual(waits, [20, 10])

    def test_recruit_timers_are_read_separately_without_closing_drawer(self):
        frame = Image.new('RGB', (1080, 2340))
        timers = {}
        with (patch.object(bot, 'TASK_DRAWER_SCROLL', 1),
              patch.object(bot, 'task_drawer', return_value=frame),
              patch.object(bot, 'task_tap') as tap,
              patch.object(bot, 'recognize_text', return_value=[
                  ('高级招募', (10, 100, 150, 35)), ('史诗招募', (10, 210, 150, 35))]),
              patch.object(bot, 'countdown', side_effect=[120, 86400]) as read,
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO())):
            for kind in bot.RECRUIT_TYPES:
                bot.recruit_timer('test', frame, {}, timers, kind)
        tap.assert_not_called()
        self.assertEqual(timers, {'recruit_advanced': 220, 'recruit_epic': 86500})
        self.assertEqual([call.args[1] for call in read.call_args_list],
                         [(125, 845, 550, 900), (125, 955, 550, 1010)])

    def test_missing_epic_timer_does_not_overwrite_advanced_timer(self):
        frame = Image.new('RGB', (1080, 2340))
        timers = {'recruit_advanced': 5000}
        with (patch.object(bot, 'recruit_row', return_value=(frame, None)),
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO())):
            bot.recruit_timer('test', frame, {}, timers, 'recruit_epic')
        self.assertEqual(timers, {'recruit_advanced': 5000, 'recruit_epic': 160})

    def test_epic_recruit_requires_its_own_free_button(self):
        frame = Image.new('RGB', (1080, 2340))
        for free in (False, True):
            with (self.subTest(free=free), patch.object(bot, 'TASK_DRAWER_SCROLL', 1),
                  patch.object(bot, 'inspect', return_value=(True, None, 0)),
                  patch.object(bot, 'task_drawer', return_value=frame),
                  patch.object(bot, 'recruit_row', return_value=(frame, 955)),
                  patch.object(bot, 'find_green', return_value=(1, 280, 970)),
                  patch.object(bot, 'task_page', return_value=True),
                  patch.object(bot, 'task_screen', return_value=frame),
                  patch.object(bot, 'green_button', return_value=free),
                  patch.object(bot, 'recognize_text', return_value=[('免费', (0, 0, 50, 30))]),
                  patch.object(bot, 'task_tap') as tap,
                  contextlib.redirect_stdout(io.StringIO())):
                if free:
                    bot.run_city_tasks('test', None, None, {'free-label': frame}, initial_image=frame,
                                       donate=False, recruit_kind='recruit_epic', keep_open=True)
                else:
                    with self.assertRaisesRegex(RuntimeError, '免费招募按钮'):
                        bot.run_city_tasks('test', None, None, {'free-label': frame}, initial_image=frame,
                                           donate=False, recruit_kind='recruit_epic', keep_open=True)
                buttons = [call.args[1:] for call in tap.call_args_list]
                self.assertEqual((280, 2230) in buttons, free)
                self.assertNotIn((280, 1690), buttons)

    def test_epic_cooldown_is_not_a_free_recruit(self):
        free = Image.open(bot.ROOT / 'artifacts/epic-free-sample.png').convert('RGB')
        cooldown = Image.open(bot.ROOT / 'artifacts/feature-recruit.png').convert('RGB')
        with patch.object(bot, 'recognize_text') as ocr:
            self.assertEqual(bot.recruit_button_state(free, {}, 'recruit_epic')[0], 'free')
        ocr.assert_not_called()
        with patch.object(bot, 'recognize_text', return_value=[('下次免费：1天', (0, 0, 50, 30))]):
            self.assertEqual(bot.recruit_button_state(cooldown, {}, 'recruit_epic')[0], 'cooldown')

    def test_epic_cooldown_returns_without_clicking_recruit(self):
        frame = Image.new('RGB', (1080, 2340))
        with (patch.object(bot, 'TASK_DRAWER_SCROLL', 1),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_drawer', return_value=frame),
              patch.object(bot, 'recruit_row', return_value=(frame, 955)),
              patch.object(bot, 'find_green', return_value=(1, 280, 970)),
              patch.object(bot, 'task_page', return_value=True),
              patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'recruit_button_state', return_value=('cooldown', '下次免费')),
              patch.object(bot, 'recruit_timer', return_value=frame) as timer,
              patch.object(bot, 'task_tap') as tap,
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_city_tasks('test', None, None, {'free-label': frame}, initial_image=frame,
                               donate=False, recruit_kind='recruit_epic', keep_open=True)
        self.assertEqual([call.args[1:] for call in tap.call_args_list],
                         [(600, 970), (60, 190)])
        timer.assert_called_once()

    def test_recruit_retry_saves_all_failure_frames(self):
        frame = Image.new('L', bot.EXPECTED_SIZE)
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(bot, 'ROOT', Path(directory)),
              patch.object(bot, 'screenshot', return_value=frame),
              patch.object(bot, 'forced_offline', return_value=False),
              patch.object(bot.time, 'sleep'),
              contextlib.redirect_stdout(io.StringIO())):
            (Path(directory) / 'artifacts').mkdir()
            with self.assertRaisesRegex(RuntimeError, '重试失败'):
                bot.retry_once('史诗招募', lambda: (_ for _ in ()).throw(RuntimeError('识别失败')),
                               'test', None, None)
            self.assertEqual(len(list((Path(directory) / 'artifacts').glob('recruit-failure-*.png'))), 4)

    def test_both_recruit_countdowns_from_saved_drawer(self):
        frame = Image.open(bot.ROOT / 'artifacts/drawer-flow-1.png').convert('RGB')
        timers = {}
        with (patch.object(bot, 'TASK_DRAWER_SCROLL', 1),
              patch.object(bot, 'task_drawer', return_value=frame),
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO())):
            for kind in bot.RECRUIT_TYPES:
                bot.recruit_timer('test', frame, {}, timers, kind)
        self.assertEqual(timers, {'recruit_advanced': 29237, 'recruit_epic': 94813})

    def test_drawer_closes_only_after_all_due_tasks(self):
        frame = Image.new('RGB', (1080, 2340))
        clock, events = Clock(), []
        def task(name):
            events.append(name)
            return frame
        with (patch.object(bot.time, 'monotonic', clock.monotonic),
              patch.object(bot, 'task_page', return_value=True),
              patch.object(bot, 'task_tap', side_effect=lambda *_: events.append('close')),
              patch.object(bot, 'task_screen', return_value=frame),
              patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'run', side_effect=lambda _, seconds, *a, **kw: clock.sleep(seconds)),
              contextlib.redirect_stdout(io.StringIO())):
            bot.run_schedule('test', 1, 2, False,
                             {name: lambda name=name: task(name) for name in ('train', 'donate', 'dawn')},
                             *([None] * 4))
        self.assertEqual(events, ['train', 'donate', 'dawn', 'close'])

    def test_warehouse_timer_is_read_on_building_after_claim(self):
        frame = Image.open(bot.ROOT / 'artifacts/flow-dawn-exit.png').convert('RGB')
        self.assertEqual(bot.warehouse_countdown(frame), 959)
        other_page = Image.open(bot.ROOT / 'artifacts/after-training-back.png').convert('RGB')
        with patch.object(bot, 'countdown') as read:
            self.assertIsNone(bot.warehouse_countdown(other_page))
        read.assert_not_called()

    def test_stamina_locates_warehouse_claims_and_records_its_own_timer(self):
        frame = lambda name: Image.open(bot.ROOT / f'artifacts/{name}.png').convert('RGB')
        away, ready, claimed = map(frame, ('after-training-back', 'stamina-ready', 'stamina-claimed'))
        self.assertIsNone(bot.warehouse_position(away))
        offset = bot.warehouse_position(ready)
        self.assertEqual(offset, (-36, -266))
        self.assertIsNotNone(bot.stamina_target(ready, offset))
        self.assertIsNone(bot.stamina_target(claimed, offset))
        self.assertIsNone(bot.warehouse_countdown(ready, offset=offset))
        self.assertEqual(bot.warehouse_countdown(claimed, offset=offset), 1464)
        timers = {'warehouse': 5000}
        screens = [away, away, claimed, ready, frame('flow-warehouse-can-click'), claimed]
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'reset_drawer', return_value=away) as reset,
              patch.object(bot, 'task_screen', side_effect=screens),
              patch.object(bot, 'game_foreground', return_value=True),
              patch.object(bot, 'adb') as adb, patch.object(bot, 'task_tap') as tap,
              patch.object(bot.time, 'monotonic', return_value=100),
              contextlib.redirect_stdout(io.StringIO()) as output):
            result = bot.run_stamina('test', None, None, None, timers, initial_image=away)
        self.assertIs(result, claimed)
        reset.assert_called_once()
        self.assertEqual(adb.call_count, 2)
        self.assertEqual(tap.call_args_list[-1].args, ('test', 530, 1700))
        self.assertEqual(timers, {'warehouse': 5000, 'stamina': 1564})
        self.assertIn('已领取体力罐头', output.getvalue())
        self.assertIn('体力罐头复查（仓库计时） 倒计时 1464 秒，预计完成时间', output.getvalue())

    def test_stamina_cooldown_unknown_timer_and_modal_guard(self):
        claimed = Image.open(bot.ROOT / 'artifacts/stamina-claimed.png').convert('RGB')
        ready = Image.open(bot.ROOT / 'artifacts/stamina-ready.png').convert('RGB')
        for seconds in (300, None):
            timers = {'warehouse': 5000}
            with (self.subTest(seconds=seconds),
                  patch.object(bot, 'inspect', return_value=(True, None, 0)),
                  patch.object(bot, 'task_page', return_value=False),
                  patch.object(bot, 'task_screen', return_value=claimed),
                  patch.object(bot, 'task_tap') as tap,
                  patch.object(bot, 'warehouse_countdown', return_value=seconds),
                  patch.object(bot.time, 'monotonic', return_value=100),
                  contextlib.redirect_stdout(io.StringIO())):
                bot.run_stamina('test', None, None, None, timers, initial_image=claimed)
            tap.assert_not_called()
            self.assertEqual(timers, {'warehouse': 5000, 'stamina': 400 if seconds else 160})
        with (patch.object(bot, 'inspect', return_value=(True, None, 0)),
              patch.object(bot, 'task_page', return_value=False),
              patch.object(bot, 'task_screen', return_value=claimed),
              patch.object(bot, 'task_tap') as tap):
            with self.assertRaisesRegex(RuntimeError, '未确认罐头领取页面'):
                bot.run_stamina('test', None, None, None, initial_image=ready)
        self.assertNotIn(('test', 530, 1700), [call.args for call in tap.call_args_list])

    def test_island_recognizes_changed_background(self):
        title = Image.open(bot.ROOT / 'assets/emulator-island-title.png').convert('L')
        frame = Image.new('RGB', (1080, 2340), 'blue')
        with patch.object(bot, 'recognize_text', return_value=[('我的海岛', (0, 0, 100, 40))]):
            self.assertTrue(bot.island_page(frame, title))
        with patch.object(bot, 'recognize_text', return_value=[('生命之树', (0, 0, 100, 40))]):
            self.assertFalse(bot.island_page(frame, title))


if __name__ == '__main__':
    unittest.main()
