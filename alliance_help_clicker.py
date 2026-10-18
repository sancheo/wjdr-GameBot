"""Exclusive alliance-help mode: keep tapping the fixed help-all area."""

CITY_ALLIANCE = (800, 2240)
ALLIANCE_HELP = (800, 1740)
HELP_ALL = (540, 2230)
HEALTH_CHECK_SECONDS = 2
HELP_TITLE_BOX = (135, 150, 360, 225)
HELP_ENTRY_BOX = (775, 1705, 965, 1780)


class ReconnectRequired(Exception):
    """Stop navigation taps as soon as a reconnect dialog is visible."""


def _interrupted(bot, image, text_template, reconnect_template):
    return (bot.forced_offline(image, text_template, reconnect_template)
            or bot.network_reconnect_target(image) is not None)


def help_page(bot, image):
    """Identify the page by its heading, without inspecting the help-all button."""
    with bot.open_template(bot.ROOT / "assets/emulator-alliance-help-title.png") as template:
        difference = bot.difference(image.crop(HELP_TITLE_BOX).convert("L"),
                                    template.convert("L"))
        return difference <= 15 or (difference <= 35 and bot.header_text(image, "联盟互助"))


def _wait_page(bot, serial, predicate, text_template, reconnect_template):
    deadline = bot.time.monotonic() + 30
    while bot.time.monotonic() < deadline:
        bot.check_stop()
        image = bot.screenshot(serial)
        if _interrupted(bot, image, text_template, reconnect_template):
            raise ReconnectRequired()
        if predicate(image):
            # The heading can load before the menu/button layout settles.
            bot.time.sleep(1)
            image = bot.screenshot(serial)
            if _interrupted(bot, image, text_template, reconnect_template):
                raise ReconnectRequired()
            if predicate(image):
                return image
        bot.time.sleep(1)
    raise RuntimeError("30 秒内未进入联盟互助导航页面")


def enter(bot, serial, text_template, reconnect_template):
    alliance = {"alliance-title": bot.open_template(
        bot.ROOT / "assets/emulator-alliance-title.png").convert("L")}
    with bot.open_template(bot.ROOT / "assets/emulator-alliance-help-entry.png") as template:
        entry_template = template.convert("L")

    def alliance_ready(image):
        return (bot.task_page(image, alliance, "alliance-title")
                and bot.difference(image.crop(HELP_ENTRY_BOX).convert("L"), entry_template) <= 15)

    bot.task_tap(serial, *CITY_ALLIANCE)
    _wait_page(bot, serial, alliance_ready,
               text_template, reconnect_template)
    bot.task_tap(serial, *ALLIANCE_HELP)
    _wait_page(bot, serial, lambda image: help_page(bot, image),
               text_template, reconnect_template)
    bot.print("已进入联盟互助，持续快速点击底部全部帮助区域", flush=True)


def run(bot, serial, help_template, nav_template, text_template,
        reconnect_template, reconnect=True, dry_run=False):
    """Run until cancelled; ordinary task timers and click limits never apply."""
    previous = bot.TASK_HELP, bot.WILDERNESS_HEALING
    bot.TASK_HELP = bot.WILDERNESS_HEALING = None
    bot.retryable_tasks(())

    def report(status, reason=""):
        bot.emit_task_event(kind="task", name="help_clicker", status=status,
                            reason=reason, next_run=None)

    def recover_city():
        image = bot.screenshot(serial)
        if (not _interrupted(bot, image, text_template, reconnect_template)
                and bot.inspect(image.convert("L"), help_template, nav_template,
                                allow_wilderness=True)[0]):
            return
        if not bot.return_to_city(serial, help_template, nav_template,
                                  text_template, reconnect_template):
            report("retrying", "强制下线，等待自动重连")
            bot.run(serial, 1, 2, False, float("inf"), help_template,
                    nav_template, text_template, reconnect_template,
                    announce_end=False, reconnect=reconnect, help_enabled=False,
                    return_on_reconnect=True)

    try:
        if dry_run:
            image = bot.screenshot(serial)
            bot.print(f"互助连点只观察模式；当前联盟互助页面：{help_page(bot, image)}；"
                      f"固定点击区域：{HELP_ALL}", flush=True)
            return
        bot.print("小号模式互助连点已启用，其他任务已跳过；停止程序可结束连点", flush=True)
        report("running")
        entered = False
        next_check = 0
        while True:
            bot.check_stop()
            try:
                if not entered:
                    recover_city()
                    enter(bot, serial, text_template, reconnect_template)
                    entered = True
                    next_check = 0
                    report("running")
                now = bot.time.monotonic()
                if now >= next_check:
                    image = bot.screenshot(serial)
                    if _interrupted(bot, image, text_template, reconnect_template):
                        raise ReconnectRequired()
                    if not bot.game_foreground(serial):
                        raise RuntimeError("当前前台不是目标游戏，互助连点已停止")
                    next_check = bot.time.monotonic() + HEALTH_CHECK_SECONDS
                # Do not inspect the button: a temporarily empty help list must
                # still be tapped, so new requests are helped immediately.
                bot.adb(serial, "shell", "input", "tap", *(str(value) for value in HELP_ALL))
            except ReconnectRequired:
                report("retrying", "连接中断，恢复主城后继续互助连点")
                entered = False
    finally:
        bot.TASK_HELP, bot.WILDERNESS_HEALING = previous
