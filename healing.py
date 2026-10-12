"""Calibrated wilderness hospital flow; every action follows a confirmed frame."""

ICON_BOX = (800, 1955, 866, 2015)
TITLE_ORIGIN = (416, 780)


def page(bot, image):
    template = bot.open_template(bot.ROOT / "assets/emulator-heal-title.png")
    score, x, y = bot.find_white(image, template, range(390, 441, 5), range(550, 1101, 5))
    return (x - TITLE_ORIGIN[0], y - TITLE_ORIGIN[1]) if score >= .94 else None


def button(bot, image, name, offset):
    dx, dy = offset
    origins = {"quick": (150, 1640), "treat": (730, 1640), "help": (675, 1665)}
    x, y = origins[name]
    template = bot.open_template(bot.ROOT / f"assets/emulator-heal-{name}.png")
    score, _, _ = bot.find_white(image, template, [x + dx], [y + dy])
    return score >= .92


def entry(bot, image, help_template, nav_template):
    if (not bot.town_button(image) or page(bot, image) is not None
            or not bot.inspect(image.convert("L"), help_template, nav_template, allow_wilderness=True)[0]):
        return None
    template = bot.open_template(bot.ROOT / "assets/emulator-heal-icon.png").convert("L")
    score, dx, dy = bot.minimum_difference(image.convert("L"), template, ICON_BOX,
                                         range(-24, 25, 2), range(-40, 41, 2))
    return (837 + dx, 1994 + dy) if score <= 18 else None


def run(bot, serial, help_template, nav_template, initial_image=None):
    image = initial_image if initial_image is not None else bot.task_screen(serial, help_enabled=False)
    offset = page(bot, image)
    if offset is None:
        image = bot.enter_wilderness(serial, image, help_template, nav_template, "自动治疗")
        target = entry(bot, image, help_template, nav_template)
        if target is None:
            return image
        bot.task_tap(serial, *target)
        for _ in range(8):
            image = bot.task_screen(serial, help_enabled=False)
            offset = page(bot, image)
            if offset is not None:
                break
        else:
            raise RuntimeError("点击伤兵图标后未确认治疗伤兵弹窗")

    dx, dy = offset
    if button(bot, image, "quick", offset):
        bot.task_tap(serial, 200 + dx, 1695 + dy)
        image = bot.task_screen(serial, help_enabled=False)
        offset = page(bot, image)
        if offset is None or not button(bot, image, "treat", offset):
            raise RuntimeError("快速选择后未确认普通资源治疗按钮")
        dx, dy = offset
        bot.task_tap(serial, 775 + dx, 1695 + dy)
        for _ in range(8):
            image = bot.task_screen(serial, help_enabled=False)
            offset = page(bot, image)
            shortage = bot.resource_more_target(image)
            if shortage is not None:
                bot.task_tap(serial, *shortage)
                image = bot.task_screen(serial, help_enabled=False)
                offset = page(bot, image)
                if offset is None:
                    raise RuntimeError("治疗资源不足弹窗关闭后未确认医院")
                bot.print("治疗普通资源不足，稍后重新检测", flush=True)
                break
            if offset is not None and button(bot, image, "help", offset):
                break
        else:
            raise RuntimeError("点击治疗后未确认联盟互助按钮")

    if offset is not None and button(bot, image, "help", offset):
        dx, dy = offset
        bot.task_tap(serial, 775 + dx, 1695 + dy)
        for _ in range(8):
            image = bot.task_screen(serial, help_enabled=False)
            offset = page(bot, image)
            if offset is None or not button(bot, image, "help", offset):
                break
        else:
            raise RuntimeError("治疗联盟互助请求尚未确认")
        bot.print("伤兵治疗已启动，联盟互助请求已确认", flush=True)

    if offset is not None:
        dx, dy = offset
        bot.task_tap(serial, 899 + dx, 804 + dy)
    for _ in range(8):
        image = bot.task_screen(serial, help_enabled=False)
        if (page(bot, image) is None and bot.town_button(image)
                and bot.inspect(image.convert("L"), help_template, nav_template, allow_wilderness=True)[0]):
            return image
    raise RuntimeError("治疗结束后未确认返回野外界面")
