"""Search calibrated monster categories, confirm levels, and dispatch once per kind."""

import re

MONSTERS = {"beast": ("野兽", 30), "polar_beast": ("冰原巨兽", 6)}
MINIMUM_LEVELS = {"beast": 1, "polar_beast": 2}
STAMINA_COSTS = {"beast": 10, "polar_beast": 25}
STAMINA_RECHECK_SECONDS = 60
STAMINA_RECHECK_TIMER = "hunt_stamina"


def level(bot, image, maximum):
    values = [int(text.strip()) for text, _ in
              bot.recognize_text(image.crop((795, 1960, 1015, 2045)))
              if re.fullmatch(r"\d{1,2}", text.strip()) and 1 <= int(text.strip()) <= maximum]
    return values[0] if len(values) == 1 else None


def challenge_limit(bot, image):
    text = "".join(re.sub(r"\s+", "", value) for value, _ in
                   bot.recognize_text(image.crop((250, 2070, 830, 2190))))
    match = re.search(r"最高可挑战(\d{1,2})级野兽", text)
    return int(match[1]) if match and 1 <= int(match[1]) <= 30 else None


def set_level(bot, serial, image, target, maximum):
    # Each click is followed by a fresh numeric read, including delayed frames.
    for _ in range(maximum + 10):
        if not bot.gather_search_page(image):
            raise RuntimeError("调整打怪等级时未确认搜索界面")
        current = level(bot, image, maximum)
        if current == target:
            return image
        if current is not None:
            if current > target and minimum_level(bot, image):
                raise RuntimeError(f"当前游戏最低可搜索 {current} 级，无法设置 {target} 级")
            bot.task_tap(serial, 730 if current < target else 105, 2000)
        image = bot.task_screen(serial)
    raise RuntimeError(f"打怪搜索等级未确认达到 {target} 级")


def minimum_level(bot, image):
    template = bot.open_template(bot.ROOT / "assets/emulator-hunt-minus-disabled.png").convert("L")
    return bot.difference(image.crop((65, 1960, 145, 2040)).convert("L"), template) <= 15


def prepare(bot, serial, image, kind, configured_level):
    label, maximum = MONSTERS[kind]
    for _ in range(5):
        if not bot.gather_search_page(image):
            raise RuntimeError("未确认打怪搜索界面")
        target = bot.intelligence_text_target(image, (label,), (0, 1790, 1080, 1880))
        if target is not None and 110 <= target[0] <= 970:
            bot.task_tap(serial, *target)
            image = bot.task_screen(serial)
            break
        if not bot.game_foreground(serial):
            raise RuntimeError("当前前台不是目标游戏，已停止")
        bot.adb(serial, "shell", "input", "swipe", "200", "1780", "900", "1780", "600")
        image = bot.task_screen(serial)
    else:
        raise RuntimeError(f"搜索栏未找到完整的{label}图标")
    target_level = configured_level
    if kind == "beast":
        for _ in range(5):
            limit = challenge_limit(bot, image)
            if limit is not None:
                target_level = min(configured_level, limit)
                break
            image = bot.task_screen(serial)
        else:
            raise RuntimeError("未识别搜索按钮上方的最高可挑战野兽等级")
        if target_level != configured_level:
            bot.print(f"野兽配置 {configured_level} 级超过最高可挑战 {limit} 级，本次挑战 {target_level} 级", flush=True)
    return set_level(bot, serial, image, target_level, maximum), target_level


def action(bot, image, kind):
    labels = ("攻击",) if kind == "beast" else ("集结", "攻击")
    return button_target(bot, image, labels, (280, 850, 850, 1630))


def button_target(bot, image, labels, box):
    for text, (x, y, width, height) in bot.recognize_text(image.crop(box)):
        if re.sub(r"\s+", "", text) in labels:
            return round(box[0] + x + width / 2), round(box[1] + y + height / 2)
    return None


def notice(bot, image):
    text = "".join(re.sub(r"\s+", "", value) for value, _ in
                   bot.recognize_text(image.crop((80, 650, 1000, 1650))))
    if bot.stamina_refill_target(image) is not None or "体力不足" in text or "补充体力" in text:
        return "体力不足"
    if "附近没有发现条件相符的目标" in text:
        return "没有符合条件的目标"
    if bot.gather_notice(image) == "no_soldiers":
        return "士兵不足"
    return None


def leave(bot, serial, image):
    for _ in range(8):
        if (not bot.gather_search_page(image) and bot.town_button(image)
                and button_target(bot, image, ("攻击", "集结", "发起集结", "出征"),
                                  (280, 850, 1050, 2300)) is None):
            return image
        bot.task_back(serial)
        image = bot.task_screen(serial)
    raise RuntimeError("打怪结束后未确认野外主界面")


def defer_for_stamina(bot, timers):
    # Start the interval after checking/unwinding, since dispatch can take
    # longer than the scheduler's default hunt interval.
    if timers is not None:
        bot.record_timer(timers, "hunt", STAMINA_RECHECK_SECONDS, "自动打怪体力复查")
        timers[STAMINA_RECHECK_TIMER] = timers["hunt"]


def check_stamina(bot, serial, image, kind, timers):
    for attempt in range(3):
        stamina = bot.intelligence_stamina(image)
        if stamina is not None:
            break
        if attempt < 2:
            image = bot.task_screen(serial)
    required = STAMINA_COSTS[kind]
    if stamina is None:
        bot.print("未识别野外头像下的体力值，等待复查体力", flush=True)
    elif stamina < required:
        bot.print(f"当前体力 {stamina}，{MONSTERS[kind][0]}需要体力 {required}，等待体力恢复", flush=True)
    else:
        return image, True
    defer_for_stamina(bot, timers)
    return image, False


def wait_for_stamina(bot, serial, image, timers=None):
    bot.print("体力已无法继续自动打怪，等待体力恢复", flush=True)
    close = bot.stamina_refill_target(image)
    if close is not None:
        bot.task_tap(serial, *close)
    else:
        bot.task_back(serial)
    image = leave(bot, serial, bot.task_screen(serial))
    defer_for_stamina(bot, timers)
    return image, False


def wait_for_soldiers(bot, serial, image):
    bot.print("暂无可出征士兵，跳过本轮打怪，等待士兵返回或训练完成", flush=True)
    return leave(bot, serial, image), False


def dispatch(bot, serial, image, kind, timers=None):
    label = MONSTERS[kind][0]
    # The ice-beast detail covers the queue fraction. Check on the search
    # drawer immediately before Search, while the complete header is visible.
    if bot.intelligence_queue_full(image):
        bot.print("野外行军队列已满，暂停自动打怪", flush=True)
        return leave(bot, serial, image), False
    bot.task_tap(serial, 540, 2240)
    for _ in range(8):
        image = bot.task_screen(serial)
        reason = notice(bot, image)
        if reason == "体力不足":
            return wait_for_stamina(bot, serial, image, timers)
        if reason == "士兵不足":
            return wait_for_soldiers(bot, serial, image)
        if reason:
            raise RuntimeError(f"搜索{label}异常：{reason}")
        target = action(bot, image, kind)
        if target is not None:
            break
    else:
        raise RuntimeError(f"搜索{label}后未发现{'攻击' if kind == 'beast' else '集结或攻击'}按钮")
    bot.task_tap(serial, *target)
    rally_confirmed = False
    for _ in range(10):
        image = bot.task_screen(serial)
        reason = notice(bot, image)
        if reason == "体力不足":
            return wait_for_stamina(bot, serial, image, timers)
        if reason == "士兵不足":
            return wait_for_soldiers(bot, serial, image)
        if reason:
            raise RuntimeError(f"{label}出征异常：{reason}")
        target = bot.intelligence_text_target(image, ("出征",), (600, 2180, 1050, 2300))
        if target is not None:
            break
        if kind == "polar_beast" and not rally_confirmed:
            # Ice beasts use a rally-duration modal before the squad screen.
            target = button_target(bot, image, ("发起集结",), (280, 1450, 850, 1800))
            if target is not None:
                bot.task_tap(serial, *target)
                rally_confirmed = True
    else:
        raise RuntimeError(f"{label}未进入出征界面")
    bot.task_tap(serial, *target)
    for _ in range(8):
        image = bot.task_screen(serial)
        reason = notice(bot, image)
        if reason == "体力不足":
            return wait_for_stamina(bot, serial, image, timers)
        if reason == "士兵不足":
            return wait_for_soldiers(bot, serial, image)
        if reason:
            raise RuntimeError(f"{label}出征异常：{reason}")
        if bot.town_button(image) and not bot.gather_search_page(image):
            return leave(bot, serial, image), True
        # Some clients keep the search drawer after a successful dispatch.
        if bot.gather_search_page(image) and action(bot, image, kind) is None:
            return leave(bot, serial, image), True
    raise RuntimeError(f"{label}出征后未返回野外界面")


def run(bot, serial, help_template, nav_template, monsters=tuple(MONSTERS), levels=None, timers=None):
    if not monsters or any(kind not in MONSTERS for kind in monsters):
        raise ValueError("至少要勾选一项有效打怪目标")
    levels = {kind: (levels or {}).get(kind, 1 if kind == "beast" else 2) for kind in monsters}
    for kind, value in levels.items():
        if type(value) is not int or not MINIMUM_LEVELS[kind] <= value <= MONSTERS[kind][1]:
            raise ValueError(f"{MONSTERS[kind][0]}等级不在有效范围内")
    if timers is not None:
        timers.pop(STAMINA_RECHECK_TIMER, None)
    image = bot.enter_wilderness(serial, bot.task_screen(serial), help_template, nav_template, "自动打怪")
    for kind, (label, _) in MONSTERS.items():
        if kind not in monsters:
            continue
        image = bot.task_screen(serial)
        if not bot.town_button(image):
            raise RuntimeError("打怪前未确认野外界面")
        if bot.intelligence_queue_full(image):
            bot.print("野外行军队列已满，跳过本轮剩余打怪目标", flush=True)
            if timers is not None:
                bot.record_timer(timers, "hunt", bot.intelligence_queue_countdown(image), "自动打怪等待行军队列")
            break
        image, enough_stamina = check_stamina(bot, serial, image, kind, timers)
        if not enough_stamina:
            break
        bot.task_tap(serial, 65, 1735)
        image, selected = prepare(bot, serial, bot.task_screen(serial), kind, levels[kind])
        bot.print(f"搜索{label}：{selected}级", flush=True)
        image, issued = dispatch(bot, serial, image, kind, timers)
        if not issued:
            break
        bot.print(f"已派遣{label} {selected} 级打怪队伍", flush=True)
    return image
