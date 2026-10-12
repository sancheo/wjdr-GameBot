"""Daily scheduling, with an injected runtime for emulator operations."""

def run_schedule(bot, serial, seconds, interval, forever, tasks,
                 help_template, nav_template, text_template, reconnect_template,
                 reconnect=True, help_enabled=True, timers=None):
    tasks = tasks if isinstance(tasks, dict) else dict(enumerate(tasks))
    # Keep field priorities stable even when callers supply a different order.
    field_order = ('intelligence', 'hunt', 'gather', 'heal')
    tasks = {**{name: task for name, task in tasks.items() if name not in field_order},
             **{name: tasks[name] for name in field_order if name in tasks}}
    wilderness_tasks = any(name in tasks for name in field_order) or 'task_rewards' in tasks
    timers = timers if timers is not None else {}
    timers.clear()
    disabled_tasks = set()
    bot.retryable_tasks(disabled_tasks)

    def report(name, status, reason=''):
        next_run = None
        if status == 'waiting':
            next_run = bot.time.time() + max(0, timers.get(name, 0) - bot.time.monotonic())
        bot.emit_task_event(kind='task', name=name, status=status, reason=reason, next_run=next_run)

    for name in tasks:
        report(name, 'waiting')
    consecutive_failures = 0
    priority_help = help_enabled
    bot.HELP_RETRY_AT = 0.0
    bot.TASK_DRAWER_SCROLL = None
    deadline = float('inf') if forever else bot.time.monotonic() + seconds
    image = None
    recruit_templates = ({'recruit-title': bot.open_template(bot.ROOT / 'assets/emulator-recruit-title.png').convert('RGB')}
                         if any(name in tasks for name in bot.RECRUIT_TYPES) else {})

    def on_recruit_page():
        return (bool(recruit_templates) and isinstance(image, bot.Image.Image)
                and bot.task_page(image, recruit_templates, 'recruit-title'))

    def rewards_pending(frame):
        if ('task_rewards' in tasks and 'task_rewards' not in disabled_tasks
                and bot.task_rewards.entry(bot, frame, help_template, nav_template) is not None):
            timers['task_rewards'] = 0
            return True
        return False

    while bot.time.monotonic() < deadline:
        bot.check_stop()
        requested = bot.take_retries() & disabled_tasks
        for name in requested:
            disabled_tasks.remove(name)
            timers[name] = 0
            report(name, 'waiting')
            bot.print(f'已安排单项重试：{bot.task_display_name(name)}', flush=True)
        if requested:
            consecutive_failures = 0
            image = None
        bot.retryable_tasks(disabled_tasks)
        bot.maybe_cleanup_diagnostic_screenshots()
        now = bot.time.monotonic()
        next_tasks = min((timers.get(name, now) for name in tasks if name not in disabled_tasks),
                         default=float('inf'))
        try:
            if priority_help:
                drawer = bot.open_template(bot.ROOT / 'assets/emulator-drawer-open.png').convert('RGB')
                bot.TASK_HELP = (help_template, nav_template, drawer)
                try:
                    bot.retry_once(
                        '优先互助检查',
                        lambda: bot.check_priority_help(serial, help_template, nav_template,
                                                        text_template, reconnect_template),
                        serial, text_template, reconnect_template,
                        lambda: bot.recover_task_city(serial, help_template, nav_template,
                                                      text_template, reconnect_template))
                    priority_help = False
                finally:
                    bot.TASK_HELP = None
            if now >= next_tasks:
                drawer = bot.open_template(bot.ROOT / 'assets/emulator-drawer-open.png').convert('RGB')
                bot.TASK_HELP = (help_template, nav_template, drawer) if help_enabled else None
                try:
                    for name, task in tasks.items():
                        if name in disabled_tasks:
                            continue
                        if bot.time.monotonic() >= timers.get(name, 0):
                            report(name, 'running')
                            if name not in bot.RECRUIT_TYPES and on_recruit_page():
                                image = bot.retry_once(
                                    '招募结束后返回主城',
                                    lambda: bot.recover_task_city(serial, help_template, nav_template,
                                                                  text_template, reconnect_template),
                                    serial, text_template, reconnect_template)
                            if (wilderness_tasks and name not in (*field_order, 'task_rewards')
                                    and isinstance(image, bot.Image.Image) and bot.town_button(image)):
                                image = bot.retry_once(
                                    '城内任务前返回主城',
                                    lambda: bot.recover_task_city(serial, help_template, nav_template,
                                                                  text_template, reconnect_template),
                                    serial, text_template, reconnect_template)
                            if name not in bot.PET_SKILLS and isinstance(image, bot.Image.Image) and bot.pet_page(image):
                                image = bot.retry_once(
                                    '关闭宠物技能弹窗',
                                    lambda: bot.close_pet_page(serial, bot.color_screenshot(serial),
                                                               help_template, nav_template),
                                    serial, text_template, reconnect_template,
                                    lambda: bot.recover_task_city(serial, help_template, nav_template,
                                                                  text_template, reconnect_template))
                            delay = (15 if name == 'heal' else 60 if name == 'hunt' else bot.EXPLORE_INTERVAL if name == 'explore' else
                                     60 if name in ('task_rewards', 'train', *bot.RECRUIT_TYPES, *bot.PET_SKILLS,
                                                   'warehouse', 'stamina', 'dawn') else 600)
                            timers[name] = bot.time.monotonic() + delay
                            label = bot.task_display_name(name)
                            bot.print(f'开始检查：{label}', flush=True)
                            try:
                                image = bot.retry_once(label, task, serial, text_template, reconnect_template,
                                                       lambda: bot.recover_task_city(serial, help_template, nav_template, text_template, reconnect_template),
                                                       on_retry=lambda attempt, reason: report(name, 'retrying', f'第 {attempt} 次重试：{reason}'))
                            except bot.RetriesExhausted as error:
                                disabled_tasks.add(name)
                                report(name, 'disabled', str(error))
                                bot.retryable_tasks(disabled_tasks)
                                consecutive_failures += 1
                                diagnostic = bot.exception_screenshot(serial, error)
                                bot.print(f'⚠️ {label} 连续3次重试失败，本次程序运行期间跳过该任务；原因：{error}；{diagnostic}', flush=True)
                                if consecutive_failures >= 3:
                                    message = '⚠️ 当前已出现三个任务连续执行异常因此自动中断脚本'
                                    bot.print(f'{message}；{diagnostic}', flush=True)
                                    stopped = bot.ConsecutiveTaskFailures(message)
                                    stopped.diagnostic_screenshot = diagnostic
                                    raise stopped from None
                                image = None
                                image = bot.retry_once(
                                    f'{label} 停用后恢复主城',
                                    lambda: bot.recover_task_city(serial, help_template, nav_template,
                                                                  text_template, reconnect_template),
                                    serial, text_template, reconnect_template)
                            else:
                                consecutive_failures = 0
                                report(name, 'waiting')
                                if name == 'intelligence' and 'gather' in tasks:
                                    timers['gather'] = 0
                                if name == 'intelligence' and 'hunt' in tasks:
                                    # Intelligence may release ordinary hunt waits,
                                    # but must not restart a stamina check early.
                                    timers['hunt'] = timers.get(bot.hunting.STAMINA_RECHECK_TIMER, 0)
                                if name in ('intelligence', 'hunt', 'gather') and 'heal' in tasks:
                                    timers['heal'] = 0
                                if (name != 'task_rewards' and isinstance(image, bot.Image.Image)
                                        and 'task_rewards' in tasks):
                                    rewards_pending(image)

                    def finish_drawer():
                        nonlocal image
                        if on_recruit_page():
                            image = bot.recover_task_city(serial, help_template, nav_template,
                                                          text_template, reconnect_template)
                        if isinstance(image, bot.Image.Image) and bot.pet_page(image):
                            # A failed later skill may have recovered the city;
                            # inspect a fresh frame before closing a modal.
                            image = bot.close_pet_page(serial, bot.color_screenshot(serial), help_template, nav_template)
                        if isinstance(image, bot.Image.Image) and bot.task_page(image, {'drawer-open': drawer}, 'drawer-open'):
                            bot.task_tap(serial, 695, 1100)
                            image = bot.task_screen(serial)
                            if not bot.inspect(image.convert('L'), help_template, nav_template)[0]:
                                raise RuntimeError('任务检查结束后未能确认主城')

                    def recover_drawer():
                        nonlocal image
                        image = bot.recover_task_city(serial, help_template, nav_template, text_template, reconnect_template)
                    bot.retry_once('结束本轮任务', finish_drawer, serial, text_template, reconnect_template, recover_drawer)
                finally:
                    bot.TASK_HELP = None
                continue
            wait = min(deadline, next_tasks, now + 600) - now
            if wait > 0:
                monitor_end = bot.time.monotonic() + wait
                recovered = bot.retry_once(
                    '联盟互助监控',
                    lambda: bot.run(serial, max(1, monitor_end - bot.time.monotonic()), interval,
                                    False, float('inf'), help_template, nav_template, text_template,
                                    reconnect_template, reconnect=reconnect, help_enabled=help_enabled,
                                    return_on_reconnect=True, allow_wilderness=wilderness_tasks),
                    serial, text_template, reconnect_template,
                    lambda: bot.recover_task_city(serial, help_template, nav_template,
                                                  text_template, reconnect_template))
                if recovered:
                    image = None
                    bot.reset_timers_after_reconnect(timers)
                    for name in tasks:
                        if name not in disabled_tasks:
                            report(name, 'waiting')
                    priority_help = help_enabled
                    bot.HELP_RETRY_AT = 0.0
                    deadline = max(deadline, bot.time.monotonic() + 1)
        except bot.ConsecutiveTaskFailures:
            raise
        except Exception as task_error:
            diagnostic = bot.exception_screenshot(serial, task_error)
            bot.print(f'任务异常：{task_error}；{diagnostic}', flush=True)
            try:
                offline = bot.forced_offline(bot.screenshot(serial), text_template, reconnect_template)
            except Exception:
                raise task_error from None
            if not offline:
                raise
            if not reconnect:
                raise RuntimeError('检测到强制下线，自动重连已关闭') from task_error
            bot.print('任务异常后确认强制下线弹窗，等待重连并重试任务', flush=True)
            recovery_started = bot.time.monotonic()
            bot.run(serial, 1, interval, False, float('inf'), help_template, nav_template, text_template, reconnect_template, False, reconnect=reconnect, help_enabled=help_enabled, return_on_reconnect=True)
            if not bot.inspect(bot.screenshot(serial), help_template, nav_template)[0]:
                raise RuntimeError('重连后未确认主城，未重试日常任务')
            if not forever:
                deadline += bot.time.monotonic() - recovery_started
            bot.reset_timers_after_reconnect(timers)
            for name in tasks:
                if name not in disabled_tasks:
                    report(name, 'waiting')
            image = None
            priority_help = help_enabled
            bot.HELP_RETRY_AT = 0.0
