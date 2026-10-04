"""Handle emulator daily tasks, alliance help, and the forced-offline dialog."""

import argparse
import builtins
from datetime import datetime
import io
import re
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from ocr_backend import recognize_text
from platform_support import SUBPROCESS_OPTIONS, configure_stdio, resolve_adb


ROOT = Path(__file__).resolve().parent
ADB = resolve_adb()
HELP_BOX = (748, 2070, 830, 2155)
NAV_BOX = (70, 2195, 150, 2275)
EMULATOR_OFFLINE_TEXT_BOX = (130, 1175, 940, 1250)
EMULATOR_RECONNECT_BOX = (570, 1435, 935, 1530)
OFFLINE_TEXT_BOX = EMULATOR_OFFLINE_TEXT_BOX
RECONNECT_BOX = EMULATOR_RECONNECT_BOX
HELP_COUNT_BOX = (830, 2050, 865, 2085)
EXPECTED_SIZE = (1080, 2340)
HELP_LIMIT = 22
NAV_LIMIT = 20
OFFLINE_LIMIT = 12
RECONNECT_DELAY = 600
PACKAGE = "com.gof.china/"
TASK_BOXES = {
    "drawer-open": (678, 1020, 715, 1170),
    "alliance-title": (135, 150, 255, 225),
    "tech-title": (135, 150, 360, 225),
    "tech-detail": (920, 535, 1005, 625),
    "recruit-title": (885, 265, 1050, 440),
    "free-button": (220, 1670, 350, 1730),
    "reward-title": (460, 590, 630, 720),
}
TRAIN_ROWS = (("shield", 1115), ("spear", 1225), ("archer", 1338))
UNIT_TYPES = {"shield": "盾兵", "spear": "矛兵", "archer": "射手"}
RECRUIT_TYPES = {"recruit_advanced": "高级招募", "recruit_epic": "史诗招募"}
GATHER_RESOURCES = {"meat": "生肉", "wood": "木材", "coal": "煤矿", "iron": "铁矿"}
PET_SKILLS = {
    "pet_companion": ("心灵伴侣", (425, 680)),
    "pet_transport": ("重物搬运", (645, 680)),
    "pet_senses": ("敏锐感官", (865, 680)),
    "pet_gift": ("大地馈赠", (425, 900)),
}
PET_TITLE_BOX = (440, 480, 640, 535)
PET_BUTTON_BOX = (960, 1680, 1030, 1750)
TRAIN_TITLE_BOX = (400, 140, 680, 230)
TRAIN_INTRO_PROMPT_BOX = (200, 2100, 880, 2335)
TRAIN_UNIT_ROW_BOX = (0, 1360, 1080, 1620)
TRAIN_UNIT_ROW_Y = 1480
TRAIN_UNIT_SWIPE_MS = "1200"
TASK_HELP = None
HELP_RETRY_AT = 0.0
TASK_DRAWER_SCROLL = None
DRAWER_SWIPES = 6
DRAWER_SWIPE_START = "1320"
DRAWER_SWIPE_END = "1080"
DRAWER_SWIPE_MS = "800"
DIAGNOSTIC_CLEANUP_AT = 0.0
CHAT_TEMPLATE = None
CHAT_BOX = (140, 150, 245, 230)
RECONNECT_PROMO_CLOSE = None
RECONNECT_WELCOME_CLOSE = None
ACTIVITY_CLOSE = None
TOWN_TEMPLATE = None
TOWN_BOX = (925, 2275, 1020, 2330)
EXPLORE_BOXES = {
    "title": (140, 150, 260, 225),
    "income": (440, 695, 660, 760),
    "reward": (395, 705, 660, 790),
}


def print(*values, **kwargs):
    if any("⚠️" in str(value) for value in values) and sys.stdout.isatty():
        values = ("\033[31m" + " ".join(map(str, values)) + "\033[0m",)
    builtins.print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}]", *values, **kwargs)


def help_changed(before, after, city, match):
    return city and (match[0] > HELP_LIMIT or
                    (HELP_COUNT_BOX is not None and
                     difference(before.crop(HELP_COUNT_BOX), after.crop(HELP_COUNT_BOX)) > 8))


def wait_help_update(serial, before, after, help_template, nav_template, capture,
                     offline_templates=None, allow_wilderness=False):
    """Allow animation/network delay, then defer an unchanged safe city for 30 seconds."""
    global HELP_RETRY_AT
    for attempt in range(5):
        if offline_templates is not None and forced_offline(after, *offline_templates):
            return after, False
        city, match, _ = inspect(after.convert("L"), help_template, nav_template,
                                 allow_wilderness=allow_wilderness)
        if help_changed(before, after, city, match) or chat_page(after):
            return after, True
        if attempt < 4:
            time.sleep(1)
            after = capture(serial)
    if not city:
        raise RuntimeError("互助点击后未能返回主城")
    HELP_RETRY_AT = time.monotonic() + 30
    print("互助图标暂未更新，30 秒后重新检测", flush=True)
    return after, False


def adb(serial, *args, timeout=15):
    return subprocess.run(
        [str(ADB), "-s", serial, *args],
        check=True,
        capture_output=True,
        timeout=timeout,
        **SUBPROCESS_OPTIONS,
    ).stdout


def connected_device():
    result = subprocess.run(
        [str(ADB), "devices"], check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15, **SUBPROCESS_OPTIONS
    ).stdout
    devices = [line.split()[0] for line in result.splitlines()[1:]
               if line.endswith("\tdevice") and emulator_device(line.split()[0])]
    if len(devices) != 1:
        raise RuntimeError(f"需要恰好一台已连接模拟器，当前找到 {len(devices)} 台；可用 --device 指定")
    return devices[0]


def emulator_device(serial):
    return serial.startswith("emulator-") or any(
        adb(serial, "shell", "getprop", prop).strip() == b"1"
        for prop in ("ro.kernel.qemu", "ro.boot.qemu"))


def screenshot(serial):
    return Image.open(io.BytesIO(adb(serial, "exec-out", "screencap", "-p"))).convert("L")


def color_screenshot(serial):
    return Image.open(io.BytesIO(adb(serial, "exec-out", "screencap", "-p"))).convert("RGB")


def difference(left, right):
    return ImageStat.Stat(ImageChops.difference(left, right)).mean[0]


def forced_offline(image, text_template, reconnect_template):
    return reconnect_target(image, text_template, reconnect_template) is not None


def reconnect_target(image, text_template, reconnect_template):
    if image.size != EXPECTED_SIZE:
        raise RuntimeError(f"屏幕尺寸 {image.size} 与已校准尺寸 {EXPECTED_SIZE} 不同")
    if (difference(image.crop(OFFLINE_TEXT_BOX), text_template) <= OFFLINE_LIMIT
            and difference(image.crop(RECONNECT_BOX), reconnect_template) <= OFFLINE_LIMIT):
        return ((RECONNECT_BOX[0] + RECONNECT_BOX[2]) // 2,
                (RECONNECT_BOX[1] + RECONNECT_BOX[3]) // 2)
    return None


def reconnect_popup_target(image):
    candidates = ((RECONNECT_PROMO_CLOSE, range(870, 891, 5), range(495, 526, 5)),
                  (RECONNECT_WELCOME_CLOSE, range(955, 976, 5), range(610, 631, 5)))
    for template, x_range, y_range in candidates:
        if template is None:
            continue
        score, x, y = find_white(image, template, x_range, y_range)
        if score >= 0.9:
            return x + template.width // 2, y + template.height // 2
    if RECONNECT_PROMO_CLOSE is not None:
        # 活动内容会更换；仅在右上区域找到同款白色关闭叉时点击。
        score, x, y = find_white(image, RECONNECT_PROMO_CLOSE,
                                 range(750, 1001, 5), range(300, 901, 5))
        if score >= 0.95:
            return x + RECONNECT_PROMO_CLOSE.width // 2, y + RECONNECT_PROMO_CLOSE.height // 2
    if ACTIVITY_CLOSE is not None:
        score, x, y = find_white(image, ACTIVITY_CLOSE,
                                 range(750, 1001, 5), range(300, 901, 5))
        # Sale/event panels reuse the 70px white X with small changes in
        # outline and shadow. Real variants score around .90; city controls
        # remain below .72 in this calibrated region.
        if score >= 0.88:
            return x + ACTIVITY_CLOSE.width // 2, y + ACTIVITY_CLOSE.height // 2
    return None


def inspect(image, help_template, nav_template, allow_wilderness=False):
    if image.size != EXPECTED_SIZE:
        raise RuntimeError(f"屏幕尺寸 {image.size} 与已校准尺寸 {EXPECTED_SIZE} 不同")
    nav_difference = difference(image.crop(NAV_BOX), nav_template)
    if nav_difference > NAV_LIMIT or (town_button(image) and not allow_wilderness):
        return False, None, nav_difference

    best = (float("inf"), 0, 0)
    for dy in range(-30, 31, 2):
        for dx in range(-30, 31, 2):
            box = tuple(value + (dx if index % 2 == 0 else dy) for index, value in enumerate(HELP_BOX))
            score = difference(image.crop(box), help_template)
            if score < best[0]:
                best = (score, dx, dy)
    return True, best, nav_difference


def game_foreground(serial):
    output = adb(serial, "shell", "dumpsys", "activity", "activities").decode(errors="replace")
    return any(PACKAGE in line for line in output.splitlines()
               if "mResumedActivity:" in line or "topResumedActivity=" in line)


def green_mask(image):
    red, green, blue = image.convert("RGB").split()
    red_gap = ImageChops.subtract(green, red).point(lambda value: 255 if value > 30 else 0)
    blue_gap = ImageChops.subtract(green, blue).point(lambda value: 255 if value > 10 else 0)
    return ImageChops.multiply(red_gap, blue_gap)


def white_mask(image):
    return image.convert("L").point(lambda value: 255 if value > 190 else 0)


def find_mask(image, template, x_range, y_range, mask_function):
    mask = mask_function(image)
    target = mask_function(template)
    target_pixels = ImageStat.Stat(target).sum[0] / 255

    def score_at(x, y):
        crop = mask.crop((x, y, x + target.width, y + target.height))
        pixels = ImageStat.Stat(crop).sum[0] / 255
        common = ImageStat.Stat(ImageChops.multiply(crop, target)).sum[0] / 255
        return 2 * common / (pixels + target_pixels) if pixels + target_pixels else 0

    best = (0, 0, 0)
    for y in y_range:
        for x in x_range:
            value = score_at(x, y)
            if value > best[0]:
                best = (value, x, y)
    if best[0]:
        _, coarse_x, coarse_y = best
        for y in range(max(0, coarse_y - 7), coarse_y + 8):
            for x in range(max(0, coarse_x - 7), coarse_x + 8):
                value = score_at(x, y)
                if value > best[0]:
                    best = (value, x, y)
    return best


def find_green(image, template, x_range, y_range):
    """Locate a green label or thumb; return (score, x, y)."""
    return find_mask(image, template, x_range, y_range, green_mask)


def find_white(image, template, x_range, y_range):
    return find_mask(image, template, x_range, y_range, white_mask)


def task_page(image, templates, name, limit=12):
    if name == "drawer-open":
        # 半透明抽屉下的主城会变；只比较白色收起箭头。
        box = TASK_BOXES[name]
        arrow = (box[0], box[1] + 40, box[2], box[1] + 120)
        current = image.crop(arrow).convert("L").point(lambda value: 255 if value > 230 else 0)
        reference = templates[name].crop((0, 40, box[2] - box[0], 120)).convert("L")
        reference = reference.point(lambda value: 255 if value > 230 else 0)
        return difference(current, reference) <= 10
    return difference(image.crop(TASK_BOXES[name]), templates[name]) <= limit


def task_screen(serial):
    time.sleep(1)
    image = color_screenshot(serial)
    if TASK_HELP is None:
        return image
    help_template, nav_template, drawer_template = TASK_HELP
    wilderness = town_button(image)
    city, match, _ = (inspect(image.convert("L"), help_template, nav_template, allow_wilderness=True)
                      if wilderness else inspect(image.convert("L"), help_template, nav_template))
    if not city or match[0] > HELP_LIMIT or time.monotonic() < HELP_RETRY_AT:
        return image
    if not game_foreground(serial):
        raise RuntimeError("互助出现时游戏不在前台，已停止")
    _, dx, dy = match
    x = (HELP_BOX[0] + HELP_BOX[2]) // 2 + dx
    y = (HELP_BOX[1] + HELP_BOX[3]) // 2 + dy
    adb(serial, "shell", "input", "tap", str(x), str(y))
    time.sleep(1)
    post = color_screenshot(serial)
    was_chat = chat_page(post)
    if was_chat:
        adb(serial, "shell", "input", "tap", "60", "190")
        time.sleep(1)
        post = color_screenshot(serial)
        if not (inspect(post.convert("L"), help_template, nav_template, allow_wilderness=True)
                    if wilderness else inspect(post.convert("L"), help_template, nav_template))[0]:
            raise RuntimeError("误入聊天后未能返回主城，已停止")
        print("互助点击误入聊天，已返回主城", flush=True)
    post_city, post_match, _ = (inspect(post.convert("L"), help_template, nav_template, allow_wilderness=True)
                                if wilderness else inspect(post.convert("L"), help_template, nav_template))
    icon_gone = post_city and post_match[0] > HELP_LIMIT
    count_changed = (HELP_COUNT_BOX is not None and post_city and
                     difference(image.crop(HELP_COUNT_BOX), post.crop(HELP_COUNT_BOX)) > 8)
    confirmed = icon_gone or count_changed
    if not was_chat and not confirmed:
        post, confirmed = wait_help_update(serial, image, post, help_template, nav_template,
                                           color_screenshot, allow_wilderness=wilderness)
        if chat_page(post):
            task_tap(serial, 60, 190)
            time.sleep(1)
            post = color_screenshot(serial)
            was_chat = True
            if not (inspect(post.convert("L"), help_template, nav_template, allow_wilderness=True)
                    if wilderness else inspect(post.convert("L"), help_template, nav_template))[0]:
                raise RuntimeError("误入聊天后未能返回主城")
    if drawer_template and task_page(image, {"drawer-open": drawer_template}, "drawer-open"):
        if not task_page(post, {"drawer-open": drawer_template}, "drawer-open"):
            adb(serial, "shell", "input", "tap", "20", "1100")
            time.sleep(1)
            post = color_screenshot(serial)
            if not task_page(post, {"drawer-open": drawer_template}, "drawer-open"):
                raise RuntimeError("互助后未能恢复主城任务抽屉，已停止")
            for _ in range(TASK_DRAWER_SCROLL or 0):
                adb(serial, "shell", "input", "swipe", "400", DRAWER_SWIPE_START,
                    "400", DRAWER_SWIPE_END, DRAWER_SWIPE_MS)
                time.sleep(.5)
                post = color_screenshot(serial)
                if not task_page(post, {"drawer-open": drawer_template}, "drawer-open"):
                    raise RuntimeError("互助后恢复抽屉滚动位置失败，已停止")
    if not was_chat and confirmed:
        print("任务期间已确认一次联盟互助", flush=True)
    return post


def check_priority_help(serial, help_template, nav_template, text_template, reconnect_template):
    image = screenshot(serial)
    if forced_offline(image, text_template, reconnect_template):
        raise RuntimeError("互助检查前检测到强制下线")
    if not inspect(image, help_template, nav_template)[0]:
        raise RuntimeError("互助检查前未确认主城")
    task_screen(serial)


def chat_page(image):
    return CHAT_TEMPLATE is not None and difference(image.crop(CHAT_BOX).convert("L"), CHAT_TEMPLATE) < 15


def town_button(image):
    return (TOWN_TEMPLATE is not None and
            difference(image.crop(TOWN_BOX).convert("L"), TOWN_TEMPLATE) <= 15)


def return_to_city(serial, help_template, nav_template, text_template, reconnect_template,
                   timeout=60):
    confirmations = 0
    started = time.monotonic()
    deadline = started + timeout
    next_progress = started + 10
    nav_difference = None
    island_template = Image.open(ROOT / "assets/emulator-island-title.png").convert("L")
    while time.monotonic() < deadline:
        image = screenshot(serial)
        if forced_offline(image, text_template, reconnect_template):
            return False
        city, _, nav_difference = inspect(image, help_template, nav_template)
        if city:
            confirmations += 1
            if confirmations >= 3:
                return True
        else:
            confirmations = 0
            target = reconnect_popup_target(image)
            action = "关闭活动弹窗"
            if target is None and pet_page(image):
                target, action = (995, 505), "关闭宠物技能弹窗"
            if target is None and pet_reward_page(image):
                target, action = (540, 2100), "关闭奖励弹窗"
            if target is None and stamina_page(image):
                target, action = (920, 550), "关闭体力罐头页面"
            if target is None and warehouse_reward_timer(image)[0]:
                target, action = (530, 1900), "关闭仓库奖励页"
            if target is None and recruit_hero_exit_page(image):
                target, action = (540, 2100), "退出完整英雄招募展示"
            if target is None and chat_page(image):
                target, action = (60, 190), "退出聊天"
            if target is None and island_template is not None and island_page(image, island_template):
                target, action = (60, 190), "退出海岛"
            if target is None:
                target = treasure_back_target(image)
                action = "退出宠物寻宝页面"
            if target is None:
                target = gather_back_target(image)
                action = "退出资源采集页面"
            if target is None:
                target = intelligence_back_target(image)
                action = "退出情报任务页面"
            if target is None and town_button(image):
                target, action = (960, 2230), "从野外返回城镇"
            if target is not None:
                task_tap(serial, *target)
                print(f"已{action}", flush=True)
        now = time.monotonic()
        if now >= next_progress:
            waited = min(timeout, now - started)
            detail = (f"，底栏差异 {nav_difference:.1f}"
                      if nav_difference is not None else "")
            print(f"正在恢复主城，已等待 {waited:.0f}/{timeout:g} 秒{detail}", flush=True)
            next_progress = now + 10
        remaining = deadline - now
        if remaining > 0:
            time.sleep(min(2, remaining))
    raise RuntimeError(f"{timeout:g} 秒内仍未确认主城，已停止")


def task_tap(serial, x, y):
    if not game_foreground(serial):
        raise RuntimeError("当前前台不是目标游戏，已停止")
    adb(serial, "shell", "input", "tap", str(x), str(y))


def intelligence_page(image):
    template = Image.open(ROOT / "assets/emulator-intel-title.png").convert("L")
    return difference(image.crop((140, 166, 244, 218)).convert("L"), template) <= 15


def intelligence_text_target(image, labels, box):
    for text, (x, y, width, height) in recognize_text(image.crop(box)):
        text = text.replace(" ", "")
        if any(text.startswith(label) for label in labels):
            return round(box[0] + x + width / 2), round(box[1] + y + height / 2)
    return None


def intelligence_back_target(image):
    if intelligence_page(image):
        return 60, 190
    if intelligence_text_target(image, ("出征", "小队设置"), (120, 150, 360, 235)):
        return 60, 190
    if intelligence_text_target(image, ("点击任意位置退出",), (250, 1850, 850, 2260)):
        return 540, 2150
    if intelligence_text_target(image, ("前往查看",), (280, 1500, 800, 1770)):
        return 100, 1900
    return None


def intelligence_stamina(image):
    values = [int(text.strip()) for text, _ in recognize_text(image.crop((40, 275, 125, 312)))
              if re.fullmatch(r"\d{1,4}", text.strip())]
    return values[0] if len(values) == 1 else None


def intelligence_queue_full(image):
    lines = [text.replace(" ", "") for text, _ in recognize_text(image.crop((0, 430, 395, 1250)))]
    if not any("行军" in text for text in lines):
        return False
    fraction = next((re.search(r"(\d+)\s*[/／]\s*(\d+)", text) for text in lines
                     if re.search(r"(\d+)\s*[/／]\s*(\d+)", text)), None)
    if fraction is None or int(fraction[2]) == 0:
        raise RuntimeError("野外行军队列容量未识别，未发起任务")
    return int(fraction[1]) >= int(fraction[2])


def intelligence_completed(image, target):
    x, y = target
    badge = green_mask(image.crop((x + 10, y - 65, x + 65, y - 10)))
    return ImageStat.Stat(badge).sum[0] / 255 > 60


def intelligence_target(image, excluded=()):
    search = image.copy()
    for x, y in excluded:
        search.paste(0, (x - 45, y - 45, x + 45, y + 45))
    for name in ("wolf", "wolf-large", "wolf-medium", "swords", "tent"):
        template = Image.open(ROOT / f"assets/emulator-intel-{name}.png").convert("L")
        for _ in range(20):
            score, x, y = find_white(search, template, range(60, 981, 4), range(450, 1951, 4))
            if score < .94:
                break
            target = x + template.width // 2, y + template.height // 2
            if not intelligence_completed(image, target):
                return target
            search.paste(0, (x - 5, y - 5, x + template.width + 5, y + template.height + 5))
    return None


def intelligence_queue_countdown(image):
    lines = recognize_text(image.crop((0, 430, 395, 1250)))
    bottom = min((y for text, (_, y, _, _) in lines if "增加行军队列" in text.replace(" ", "")), default=820)
    seconds = [parse_countdown(text) for text, (_, y, _, _) in lines if y < bottom]
    return min((value for value in seconds if value is not None), default=None)


def intelligence_wait_queue(serial, image):
    timers = {}
    while intelligence_queue_full(image):
        record_timer(timers, "march_queue", intelligence_queue_countdown(image),
                     "野外行军队列已满，最短队列")
        # 互助仍按原频率检查；队列容量和倒计时只在到期后重新识别。
        while (remaining := timers["march_queue"] - time.monotonic()) > 0:
            time.sleep(min(2, remaining) if TASK_HELP is not None else remaining)
            image = task_screen(serial)
            if not town_button(image):
                raise RuntimeError("等待行军队列时离开野外界面")
    return image


def open_intelligence(serial, image):
    if not town_button(image):
        raise RuntimeError("打开情报前未确认野外界面")
    intelligence_wait_queue(serial, image)
    task_tap(serial, 995, 1715)
    for _ in range(3):
        image = task_screen(serial)
        if intelligence_page(image):
            return image
    raise RuntimeError("未确认灯塔情报界面")


def bounty_queue_entries(image):
    entries = {}
    for text, (_, y, _, _) in recognize_text(image.crop((0, 430, 395, 1250))):
        match = re.search(r"[（(]\s*(\d+)\s*[,，]\s*(\d+)\s*[）)]", text)
        if match:
            entries[tuple(map(int, match.groups()))] = round(430 + y - 20)
    return entries


def bounty_target(image):
    template = Image.open(ROOT / "assets/emulator-intel-bounty.png").convert("L")
    # Search only near orange pins; ice textures can mimic the white head.
    thumbnail = image.crop((60, 450, 980, 1950)).convert("RGB").resize((92, 150))
    centers = []
    for index, (r, g, b) in enumerate(thumbnail.getdata()):
        if r > 200 and 80 < g < 210 and b < 90:
            cx, cy = 65 + index % 92 * 10, 455 + index // 92 * 10
            if all(abs(cx - px) > 70 or abs(cy - py) > 70 for px, py in centers):
                centers.append((cx, cy))
    best = (0, 0, 0)
    for cx, cy in centers:
        match = find_white(image, template, range(max(60, cx - 80), min(981, cx + 41), 4),
                           range(max(450, cy - 100), min(1951, cy + 41), 4))
        score, x, y = match
        tx, ty = x + template.width // 2, y + template.height // 2
        pixels = image.crop((tx - 45, ty - 45, tx + 45, ty + 45)).convert("RGB").getdata()
        if match > best and sum(r > 200 and 80 < g < 210 and b < 90 for r, g, b in pixels) > 400:
            best = match
    score, x, y = best
    return (x + template.width // 2, y + template.height // 2) if score >= .93 else None


def bounty_name(image):
    return next((text.replace(" ", "") for text, _ in
                 recognize_text(image.crop((200, 650, 900, 950)))
                 if text.replace(" ", "").startswith("大师悬赏")), None)


def execute_bounty(serial, image, target, state):
    # Keep this latch through retries/reconnects; only confirmed success clears it.
    state["disabled"] = True
    trip = {}
    try:
        task_tap(serial, *target)
        image = task_screen(serial)
        name = bounty_name(image)
        if name is None:
            raise RuntimeError("未确认大师悬赏任务详情")
        image, dispatched = execute_intelligence(serial, image, bounty_trip=trip)
        if not dispatched:
            state["disabled"] = False
            return image, False
        entries = bounty_queue_entries(image)
        added = [(coord, y) for coord, y in entries.items() if coord not in trip.get("queue_before", {})]
        if len(added) != 1:
            raise RuntimeError("未唯一识别大师悬赏的出征队列")
        coord, row_y = added[0]
        travel = trip.get("travel")
        if travel is None:
            raise RuntimeError("未识别大师悬赏行军时间")
        started = time.monotonic()
        deadline = started + max(120, travel * 2 + 60)
        absent = 0
        print(f"大师悬赏已出征，正在跟踪目标 {coord} 的战斗结果", flush=True)
        while time.monotonic() < deadline:
            image = task_screen(serial)
            if not town_button(image):
                raise RuntimeError("跟踪大师悬赏时未确认野外界面")
            lines = [text.replace(" ", "") for text, _ in
                     recognize_text(image.crop((0, row_y, 395, row_y + 100)))]
            entries = bounty_queue_entries(image)
            if coord in entries:
                row_y = entries[coord]
                absent = 0
            else:
                absent += 1
            if time.monotonic() >= started + travel and (
                    "返回中" in lines or absent >= 3):
                break
            time.sleep(2)
        else:
            raise RuntimeError("大师悬赏战斗结果跟踪超时")
        # Allow the result badge to catch up with the returning army.
        for _ in range(3):
            image = open_intelligence(serial, image)
            remaining = bounty_target(image)
            if remaining is not None and intelligence_completed(image, remaining):
                state["disabled"] = False
                print("大师悬赏已获胜，等待领取奖励", flush=True)
                return image, True
            task_tap(serial, 60, 190)
            image = task_screen(serial)
            time.sleep(2)
        image = open_intelligence(serial, image)
        remaining = bounty_target(image)
        if remaining is None:
            raise RuntimeError("未确认原大师悬赏任务的战斗结果")
        task_tap(serial, *remaining)
        if bounty_name(task_screen(serial)) != name:
            raise RuntimeError("未确认原大师悬赏任务的战斗结果")
        task_tap(serial, 100, 1900)
        image = task_screen(serial)
        print("⚠️ 大师悬赏怪打不过，本次程序运行期间不会再挑战大师悬赏怪", flush=True)
        return image, True
    except Exception:
        print("⚠️ 大师悬赏结果未确认，为避免重复出征，本次运行已暂停大师悬赏", flush=True)
        raise


def execute_intelligence(serial, image, bounty_trip=None):
    target = intelligence_text_target(image, ("前往查看",), (280, 1500, 800, 1770))
    if target is None:
        raise RuntimeError("情报详情未发现前往查看按钮")
    task_tap(serial, *target)
    issued = False
    deadline = time.monotonic() + 120
    last_action = None
    while time.monotonic() < deadline:
        image = task_screen(serial)
        texts = [text.replace(" ", "") for text, _ in recognize_text(image.crop((200, 500, 900, 1800)))]
        if any("体力不足" in text or "补充体力" in text for text in texts):
            print("体力已无法继续执行情报任务", flush=True)
            # Close the refill offer without using cans or buying stamina.
            task_tap(serial, 100, 1900)
            return image, False
        if any(text in ("失败", "战败", "战斗失败") for text in texts):
            raise RuntimeError("情报探险战斗失败，未确认任务完成")
        back = intelligence_back_target(image)
        if back == (540, 2150):
            task_tap(serial, *back)
            return task_screen(serial), True
        action = intelligence_text_target(image, ("出征", "营救", "探险", "战斗"),
                                          (300, 1150, 1050, 2300))
        if action is not None:
            signature = (round(action[0] / 50), round(action[1] / 50))
            if signature == last_action:
                time.sleep(1)
                continue
            if town_button(image) and intelligence_queue_full(image):
                print("野外行军队列已满，跳过当前情报任务", flush=True)
                return image, False
            if bounty_trip is not None:
                if town_button(image):
                    bounty_trip["queue_before"] = bounty_queue_entries(image)
                elif action[1] > 2000:
                    bounty_trip["travel"] = countdown(image, (710, 2100, 930, 2190))
            # Insufficient stamina may open a refill dialog; never confirm it.
            task_tap(serial, *action)
            last_action = signature
            issued = True
            time.sleep(1)
        elif issued and town_button(image):
            return image, True
    raise RuntimeError("情报任务执行后未确认结果")


def enter_wilderness(serial, image, help_template, nav_template, label):
    if inspect(image.convert("L"), help_template, nav_template)[0]:
        drawer = Image.open(ROOT / "assets/emulator-drawer-open.png").convert("RGB")
        if task_page(image, {"drawer-open": drawer}, "drawer-open"):
            task_tap(serial, 695, 1100)
            image = task_screen(serial)
        task_tap(serial, 960, 2230)
        for _ in range(3):
            image = task_screen(serial)
            if town_button(image):
                break
    if not town_button(image):
        raise RuntimeError(f"{label}开始时未确认野外界面")
    return image


def run_intelligence(serial, help_template, nav_template, text_template, reconnect_template,
                     stamina_threshold=0, timers=None, bounty=False, bounty_state=None):
    if stamina_threshold < 0:
        raise ValueError("体力触发阈值必须大于或等于 0")
    image = task_screen(serial)
    if intelligence_page(image):
        task_tap(serial, 60, 190)
        image = task_screen(serial)
    image = enter_wilderness(serial, image, help_template, nav_template, "灯塔情报")
    if intelligence_queue_full(image):
        print("野外行军队列已满，跳过本轮灯塔情报", flush=True)
        record_timer(timers, "intelligence", intelligence_queue_countdown(image),
                     "灯塔情报等待空闲行军队列")
        return image
    if stamina_threshold:
        stamina = intelligence_stamina(image)
        if stamina is None:
            raise RuntimeError("未识别野外头像下的体力值")
        if stamina <= stamina_threshold:
            print(f"体力 {stamina} 未大于触发阈值 {stamina_threshold}，跳过灯塔情报", flush=True)
            return image
        print(f"体力 {stamina} 大于触发阈值 {stamina_threshold}，开始执行灯塔情报", flush=True)
    image = open_intelligence(serial, image)
    if timers is not None:
        record_timer(timers, "intelligence",
                     labelled_countdown(image, (300, 280, 840, 375), ("下次刷新",)),
                     "灯塔情报刷新")
    pending = set()
    bounty_state = bounty_state if bounty_state is not None else {}
    while True:
        pending = {target for target in pending if not intelligence_completed(image, target)}
        claim = intelligence_text_target(image, ("一键领取",), (280, 2000, 800, 2190))
        if claim is not None:
            task_tap(serial, *claim)
            image = task_screen(serial)
            if intelligence_back_target(image) == (540, 2150):
                task_tap(serial, 540, 2150)
                image = task_screen(serial)
            if not intelligence_page(image):
                raise RuntimeError("领取情报奖励后未返回情报界面")
            print("已一键领取情报奖励", flush=True)
        target = intelligence_target(image, pending)
        if target is None:
            if not pending:
                if bounty and not bounty_state.get("disabled"):
                    target = bounty_target(image)
                    if target is not None and not intelligence_completed(image, target):
                        image, completed = execute_bounty(serial, image, target, bounty_state)
                        if completed:
                            continue
                        break
                print("情报界面已无待执行任务图标，结束灯塔情报", flush=True)
                break
            # Own missions are still marching; let their checks/rewards settle.
            task_tap(serial, 60, 190)
            image = task_screen(serial)
            time.sleep(2)
            image = open_intelligence(serial, image)
            continue
        task_tap(serial, *target)
        image, completed = execute_intelligence(serial, task_screen(serial))
        if not completed:
            break
        pending.add(target)
        print(f"已执行情报任务，位置 {target}", flush=True)
        if not town_button(image):
            raise RuntimeError("执行情报任务后未返回野外界面")
        image = open_intelligence(serial, image)
    if intelligence_page(image):
        task_tap(serial, 60, 190)
        image = task_screen(serial)
    if not town_button(image):
        raise RuntimeError("情报结束后未确认野外界面")
    return image


def gather_search_page(image):
    template = Image.open(ROOT / "assets/emulator-gather-search.png").convert("L")
    return difference(image.crop((485, 2200, 605, 2285)).convert("L"), template) <= 15


def gather_level_max(image):
    template = Image.open(ROOT / "assets/emulator-gather-plus-disabled.png").convert("L")
    return difference(image.crop((690, 1960, 770, 2040)).convert("L"), template) <= 15


def gather_full_only(image):
    return ImageStat.Stat(green_mask(image.crop((300, 2110, 355, 2165)))).sum[0] / 255 > 60


def gather_back_target(image):
    if gather_search_page(image):
        return 540, 1550  # Outside both the search panel and resource detail.
    if intelligence_text_target(image, ("采集",), (350, 1200, 730, 1310)):
        return 540, 1550
    if (intelligence_text_target(image, tuple(GATHER_RESOURCES.values()), (120, 150, 360, 235))
            and intelligence_text_target(image, ("出征",), (600, 2180, 1050, 2300))):
        return 60, 190
    return None


def prepare_gather_search(serial, image, resource):
    label = GATHER_RESOURCES[resource]
    for _ in range(4):
        if not gather_search_page(image):
            raise RuntimeError("未确认资源搜索界面")
        target = intelligence_text_target(image, (label,), (0, 1790, 1080, 1880))
        if target is not None and 110 <= target[0] <= 970:
            task_tap(serial, *target)
            image = task_screen(serial)
            break
        start, end = (900, 200) if resource in ("coal", "iron") else (200, 900)
        if not game_foreground(serial):
            raise RuntimeError("当前前台不是目标游戏，已停止")
        adb(serial, "shell", "input", "swipe", str(start), "1780", str(end), "1780", "600")
        image = task_screen(serial)
    else:
        raise RuntimeError(f"搜索栏未找到完整的{label}图标")
    deadline = time.monotonic() + 60
    while not gather_level_max(image):
        if not gather_search_page(image) or time.monotonic() >= deadline:
            raise RuntimeError(f"{label}搜索等级未确认达到上限")
        task_tap(serial, 730, 2000)
        image = task_screen(serial)
    if not gather_full_only(image):
        task_tap(serial, 328, 2135)
        image = task_screen(serial)
    if not gather_search_page(image) or not gather_full_only(image):
        raise RuntimeError("未确认勾选仅搜索资源为满的资源点")
    return image


def run_gather(serial, help_template, nav_template, text_template, reconnect_template,
               resources=tuple(GATHER_RESOURCES)):
    if not resources or any(resource not in GATHER_RESOURCES for resource in resources):
        raise ValueError("至少要勾选一项有效资源")
    image = enter_wilderness(serial, task_screen(serial), help_template, nav_template, "资源采集")
    for resource, label in GATHER_RESOURCES.items():
        if resource not in resources:
            continue
        if not town_button(image):
            raise RuntimeError("采集前未确认野外界面")
        if intelligence_queue_full(image):
            print("野外行军队列已满，跳过剩余资源采集", flush=True)
            break
        task_tap(serial, 65, 1735)
        image = prepare_gather_search(serial, task_screen(serial), resource)
        task_tap(serial, 540, 2240)
        for _ in range(5):
            image = task_screen(serial)
            target = intelligence_text_target(image, ("采集",), (350, 1200, 730, 1310))
            if target is not None:
                break
        else:
            raise RuntimeError(f"搜索{label}后未发现采集按钮")
        # Search may take time; check the current queue again before gathering.
        if intelligence_queue_full(image):
            task_tap(serial, 540, 1550)
            image = task_screen(serial)
            print("野外行军队列已满，跳过剩余资源采集", flush=True)
            break
        task_tap(serial, *target)
        for _ in range(5):
            image = task_screen(serial)
            target = intelligence_text_target(image, ("出征",), (600, 2180, 1050, 2300))
            if target is not None:
                break
        else:
            raise RuntimeError(f"{label}采集未进入出征界面")
        task_tap(serial, *target)
        for _ in range(5):
            image = task_screen(serial)
            if town_button(image) and not gather_search_page(image):
                break
        else:
            raise RuntimeError(f"{label}采集出征后未返回野外界面")
        print(f"已派遣{label}采集队伍", flush=True)
    if not town_button(image):
        raise RuntimeError("采集结束后未确认野外界面")
    return image


def resource_donation_available(image):
    red, _, blue = ImageStat.Stat(image.crop((565, 1780, 610, 1830))).mean
    return blue - red > 60


def red_dot(image, box):
    dot = image.crop(box).convert("RGB")
    return sum(r > 180 and r > g * 1.45 and r > b * 1.45
               for r, g, b in dot.getdata()) > 100


def explore_red_dot(image):
    return red_dot(image, (130, 2180, 160, 2210))


def explore_page(image, templates, name):
    return difference(image.crop(EXPLORE_BOXES[name]).convert("L"), templates[name]) < 15


def green_button(image, box):
    button = image.crop(box).convert("RGB")
    green = sum(g > r + 35 and g > b + 25 and g > 100 for r, g, b in button.getdata())
    return green / (button.width * button.height) > 0.5


def recruit_button_state(image, templates, kind):
    if kind == "recruit_advanced":
        free = task_page(image, templates, "free-button", 15)
        button_detail = f"按钮模板差异 {difference(image.crop(TASK_BOXES['free-button']), templates['free-button']):.1f}"
        hint_box = (60, 1470, 520, 1720)
    else:
        # The epic single-recruit button is green only when it is free. Sample
        # its interior so the rounded border and surrounding panel do not dilute it.
        button_box = (130, 2195, 440, 2265)
        green = green_button(image, button_box)
        free = green
        button_detail = f"单次按钮绿色={green}"
        hint_box = (60, 2070, 520, 2300)
    if free:
        return "free", "已确认免费按钮"
    hints = [text.replace(" ", "") for text, _ in recognize_text(image.crop(hint_box))]
    if any("下次免费" in text for text in hints):
        return "cooldown", "已确认下次免费提示"
    return "unknown", f"未识别免费按钮或冷却提示；{button_detail}；提示OCR={hints}"


def recruit_hero_exit_page(image):
    """Recognize the full-hero result page, which has no normal reward title."""
    texts = [text.replace(" ", "").replace("\n", "")
             for text, _ in recognize_text(image.crop((80, 1550, 1000, 2320)))]
    return "点击任意位置退出" in "".join(texts)


def run_explore(serial, help_template, nav_template, templates):
    image = color_screenshot(serial)
    if not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("探险检查前未确认主城")
    if not explore_red_dot(image):
        print("探险经验：未发现可领取红点，本次跳过；10 分钟后复查", flush=True)
        return image
    print("探险经验：发现可领取红点，开始领取", flush=True)
    task_tap(serial, 95, 2240)
    image = task_screen(serial)
    if not explore_page(image, templates, "title") or not green_button(image, (835, 1675, 1000, 1750)):
        raise RuntimeError("未确认探险经验可领取页面")
    task_tap(serial, 915, 1718)
    image = task_screen(serial)
    if not explore_page(image, templates, "income") or not green_button(image, (350, 1600, 740, 1730)):
        raise RuntimeError("未确认探险挂机收益领取弹窗")
    task_tap(serial, 540, 1660)
    image = task_screen(serial)
    for _ in range(5):
        if explore_page(image, templates, "reward"):
            break
        image = task_screen(serial)
    else:
        raise RuntimeError("未确认探险经验奖励页面")
    task_tap(serial, 540, 2050)
    image = task_screen(serial)
    if not explore_page(image, templates, "title") or green_button(image, (835, 1675, 1000, 1750)):
        raise RuntimeError("探险经验领取后状态未变化")
    task_tap(serial, 60, 190)
    image = task_screen(serial)
    if not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("探险经验领取后未返回主城")
    print("已领取探险经验", flush=True)
    return image


def task_drawer(serial, image, templates, reset_unknown=True):
    global TASK_DRAWER_SCROLL
    if not task_page(image, templates, "drawer-open"):
        task_tap(serial, 20, 1100)
        TASK_DRAWER_SCROLL = 0
        image = task_screen(serial)
    for attempt in range(5):
        if task_page(image, templates, "drawer-open"):
            break
        if attempt < 4:
            image = task_screen(serial)
    else:
        raise RuntimeError("未能确认主城抽屉已打开")
    if TASK_DRAWER_SCROLL is None and reset_unknown:
        # 启动时已打开的抽屉可能停在任意位置，先回到顶部再记录滚动次数。
        TASK_DRAWER_SCROLL = DRAWER_SWIPES
        for _ in range(DRAWER_SWIPES):
            image = swipe_drawer(serial, templates["drawer-open"], reverse=True)
    return image


def swipe_drawer(serial, drawer_template, reverse=False):
    global TASK_DRAWER_SCROLL
    if not game_foreground(serial):
        raise RuntimeError("当前前台不是目标游戏，已停止")
    start, end = ((DRAWER_SWIPE_END, DRAWER_SWIPE_START) if reverse else
                  (DRAWER_SWIPE_START, DRAWER_SWIPE_END))
    adb(serial, "shell", "input", "swipe", "400", start, "400", end, DRAWER_SWIPE_MS)
    TASK_DRAWER_SCROLL = max(0, min(DRAWER_SWIPES,
                                    TASK_DRAWER_SCROLL + (-1 if reverse else 1)))
    image = task_screen(serial)
    if not task_page(image, {"drawer-open": drawer_template}, "drawer-open"):
        raise RuntimeError("滑动后未能确认主城抽屉")
    return image


def reset_drawer(serial, image, templates):
    image = task_drawer(serial, image, templates)
    for _ in range(TASK_DRAWER_SCROLL):
        image = swipe_drawer(serial, templates["drawer-open"], reverse=True)
    return image


def task_label(serial, image, templates, name):
    image = task_drawer(serial, image, templates)
    for attempt in range(DRAWER_SWIPES + 1):
        score, x, y = find_green(image, templates[name], range(240, 321, 4), range(680, 1431, 4))
        if score >= 0.90:
            return image, x, y
        # The donation row remains visible when no donation is available. Stop
        # there so the following recruitment checks can continue downwards from
        # the same drawer position instead of overshooting and scrolling back.
        if name == "donate-label" and any(
                "联盟捐献" in text.replace(" ", "")
                for text, _ in recognize_text(image.crop((240, 650, 550, 1470)))):
            return image, None, None
        if attempt < DRAWER_SWIPES:
            image = swipe_drawer(serial, templates["drawer-open"])
    return image, None, None


def run_city_tasks(serial, help_template, nav_template, templates,
                   initial_image=None, keep_open=False, donate=True, recruit=True, timers=None,
                   recruit_kind="recruit_advanced"):
    image = initial_image if initial_image is not None else color_screenshot(serial)
    if not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("未确认主城，未执行捐献和招募")
    image = task_drawer(serial, image, templates)
    donation_y = None
    if donate:
        image, _, donation_y = task_label(serial, image, templates, "donate-label")
    if donation_y is not None:
        task_tap(serial, 800, 2240)
        image = task_screen(serial)
        if not task_page(image, templates, "alliance-title") or task_page(image, templates, "tech-title"):
            raise RuntimeError("未能确认联盟页面")
        task_tap(serial, 790, 1530)
        image = task_screen(serial)
        if not task_page(image, templates, "tech-title"):
            raise RuntimeError("未能确认联盟科技页面")
        score, x, y = find_green(image, templates["thumb"], range(80, 921, 8), range(600, 2101, 8))
        if score >= 0.80:
            task_tap(serial, x + 90, y + 115)
            image = task_screen(serial)
            if not task_page(image, templates, "tech-detail"):
                raise RuntimeError("未能确认点赞科技详情")
            count_box = (740, 1690, 900, 1745)
            for _ in range(10):
                if not resource_donation_available(image):
                    break
                before = image.crop(count_box)
                if not game_foreground(serial):
                    raise RuntimeError("当前前台不是目标游戏，已停止")
                # 同点滑动等同长按；只按右侧普通资源按钮，左侧按钮消耗钻石。
                adb(serial, "shell", "input", "swipe", "760", "1810", "760", "1810", "3000")
                image = task_screen(serial)
                for _ in range(5):
                    if not task_page(image, templates, "tech-detail"):
                        raise RuntimeError("长按后未能确认科技详情")
                    if not resource_donation_available(image) or difference(before, image.crop(count_box)) >= 3:
                        break
                    image = task_screen(serial)
                if resource_donation_available(image) and difference(before, image.crop(count_box)) < 3:
                    raise RuntimeError("长按后捐献次数未变化，已停止")
            else:
                raise RuntimeError("连续长按后仍可捐献，已停止以避免无限循环")
            print("普通资源已捐献至当前无法继续捐献", flush=True)
            task_tap(serial, 960, 575)
            image = task_screen(serial)
            if not task_page(image, templates, "tech-title"):
                raise RuntimeError("关闭科技详情后未返回联盟科技")
        else:
            print("联盟科技页面没有绿色点赞，跳过捐献", flush=True)
        task_tap(serial, 60, 190)
        image = task_screen(serial)
        if not task_page(image, templates, "alliance-title") or task_page(image, templates, "tech-title"):
            raise RuntimeError("未能从联盟科技返回联盟")
        task_tap(serial, 60, 190)
        image = task_screen(serial)
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("未能从联盟返回主城")
        image = task_drawer(serial, image, templates)
    elif donate:
        print("抽屉里没有可捐献提示，跳过联盟科技", flush=True)

    free_y = None
    if recruit:
        image, row_bottom = recruit_row(serial, image, templates, recruit_kind)
        if row_bottom is not None:
            score, _, label_y = find_green(image, templates["free-label"], range(240, 321, 4),
                                           range(row_bottom, row_bottom + 55, 2))
            if score >= .90:
                free_y = label_y
        if free_y is None:
            record_timer(timers, recruit_kind, countdown(image, (125, row_bottom, 550, row_bottom + 55))
                         if row_bottom is not None else None)
    if free_y is not None:
        task_tap(serial, 600, row_bottom + 15)
        image = task_screen(serial)
        for attempt in range(6):
            if task_page(image, templates, "recruit-title"):
                break
            # 滚动惯性或互助恢复可能移动任务行；仍在抽屉时重新定位，不能复用旧坐标。
            if attempt in (1, 3) and task_page(image, templates, "drawer-open"):
                image, row_bottom = recruit_row(serial, image, templates, recruit_kind)
                if row_bottom is not None:
                    task_tap(serial, 600, row_bottom + 15)
            if attempt < 5:
                image = task_screen(serial)
        else:
            score = difference(image.crop(TASK_BOXES["recruit-title"]), templates["recruit-title"])
            raise RuntimeError(f"未能确认英雄招募页面；标题差异 {score:.1f}")
        for attempt in range(5):
            if task_page(image, templates, "recruit-title"):
                state, detail = recruit_button_state(image, templates, recruit_kind)
            else:
                state, detail = "unknown", "等待招募页面恢复"
            if state != "unknown":
                break
            if attempt < 4:
                image = task_screen(serial)
        else:
            raise RuntimeError(f"招募页面没有确认到免费招募按钮；{detail}")
        if state == "cooldown":
            print(f"{RECRUIT_TYPES[recruit_kind]}已进入冷却，跳过招募", flush=True)
            task_tap(serial, 60, 190)
            image = task_screen(serial)
            image = recruit_timer(serial, image, templates, timers, recruit_kind)
        else:
            task_tap(serial, 280, 1690 if recruit_kind == "recruit_advanced" else 2230)
            image = task_screen(serial)
            result_page = None
            for _ in range(5):
                if task_page(image, templates, "reward-title"):
                    result_page = "reward"
                    break
                if recruit_hero_exit_page(image):
                    result_page = "hero"
                    break
                image = task_screen(serial)
            if result_page is None:
                raise RuntimeError("未能确认免费招募奖励页面")
            suffix = "，招募到完整英雄" if result_page == "hero" else ""
            print(f"已完成一次免费{RECRUIT_TYPES[recruit_kind]}{suffix}", flush=True)
            if result_page == "hero":
                task_tap(serial, 540, 2100)
            else:
                task_tap(serial, 60, 190)
            for _ in range(5):
                image = task_screen(serial)
                if task_page(image, templates, "recruit-title"):
                    break
            else:
                raise RuntimeError("未能从奖励页返回英雄招募")
            task_tap(serial, 60, 190)
            image = task_screen(serial)
            image = recruit_timer(serial, image, templates, timers, recruit_kind)
    elif recruit:
        print(f"抽屉里没有{RECRUIT_TYPES[recruit_kind]}免费提示，跳过招募", flush=True)
    if keep_open:
        return image if task_page(image, templates, "drawer-open") else task_drawer(serial, image, templates)
    if task_page(image, templates, "drawer-open"):
        task_tap(serial, 695, 1100)
        image = task_screen(serial)
    if not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("任务结束后未能确认主城")
    return image


def training_status(image, y):
    row = image.crop((125, y + 5, 550, y + 45)).convert("RGB")
    orange = sum(r > 150 and 70 < g < r * .8 and b < 100 for r, g, b in row.getdata())
    if orange > 30 and any("升级中" in text.replace(" ", "") for text, _ in recognize_text(row)):
        return "upgrading"
    check = image.crop((565, y - 35, 630, y + 35)).convert("RGB")
    green = sum(g > r + 35 and g > b + 10 for r, g, b in check.getdata())
    if green / (check.width * check.height) > 0.3:
        return "completed"
    bar = image.crop((125, y + 5, 495, y + 40)).convert("RGB")
    dark = sum(max(pixel) < 55 for pixel in bar.getdata())
    green = sum(g > r + 35 and g > b + 20 and g > 80 for r, g, b in bar.getdata())
    return "busy" if dark / (bar.width * bar.height) > 0.4 or green / (bar.width * bar.height) > 0.2 else "idle"


def training_page(image, title):
    crop = image.crop(TRAIN_TITLE_BOX)
    # The sky behind the white title changes with the city's weather.
    return (difference(crop.convert("L"), title.convert("L")) <= 3
            or difference(white_mask(crop), white_mask(title)) <= 10
            # Selecting a lower tier changes the name prefix, not the unit type.
            or difference(white_mask(crop.crop((140, 0, 280, 90))),
                          white_mask(title.crop((140, 0, 280, 90)))) <= 10)


def training_upgrade_target(image, arrow):
    # Check left to right. Only the avatar's upper-left marker counts.
    for left in range(0, 1000, 160):
        score, x, _ = find_green(image, arrow, range(left, min(left + 160, 1000), 8),
                                 range(1358, 1391, 6))
        if score >= .9:
            return min(x + 85, 1040), TRAIN_UNIT_ROW_Y
    return None


def highest_training_target(image):
    # Unlocked portraits have white borders; locked portraits are entirely grey.
    # ponytail: calibrated border grouping; use portrait templates if skins change it.
    mask = white_mask(image.crop((0, 1370, 1080, 1530)))
    columns = [0] * mask.width
    for index, value in enumerate(mask.getdata()):
        if value:
            columns[index % mask.width] += 1
    spans = []
    for x, count in enumerate(columns):
        if count < 5:
            continue
        if spans and x - spans[-1][1] <= 22:
            spans[-1][1] = x
        else:
            spans.append([x, x])
    for left, right in reversed(spans):
        if right - left >= 90 and 80 <= (left + right) // 2 <= 1000:
            return (left + right) // 2, TRAIN_UNIT_ROW_Y
    raise RuntimeError("未确认最高可训练等级兵种，已停止")


def scan_training_upgrade(serial, image, title):
    arrow = Image.open(ROOT / "assets/emulator-training-upgrade-arrow.png").convert("RGB")

    def swipe(start, end):
        if not game_foreground(serial):
            raise RuntimeError("当前前台不是目标游戏，已停止")
        adb(serial, "shell", "input", "swipe", str(start), str(TRAIN_UNIT_ROW_Y),
            str(end), str(TRAIN_UNIT_ROW_Y), TRAIN_UNIT_SWIPE_MS)
        frame = task_screen(serial)
        if not training_page(frame, title):
            raise RuntimeError("兵种列表滑动后未确认兵营，已停止")
        return frame

    for _ in range(6):
        before = image
        image = swipe(180, 930)  # Scroll to the lowest tier (content moves right).
        if difference(before.crop(TRAIN_UNIT_ROW_BOX), image.crop(TRAIN_UNIT_ROW_BOX)) <= 2:
            break
    else:
        raise RuntimeError("兵种列表未能滑到最低等级端，已停止")
    for _ in range(16):
        target = training_upgrade_target(image, arrow)
        if target:
            return image, target
        before = image
        image = swipe(900, 540)  # Slow, overlapping scans towards higher tiers.
        if difference(before.crop(TRAIN_UNIT_ROW_BOX), image.crop(TRAIN_UNIT_ROW_BOX)) <= 2:
            task_tap(serial, *highest_training_target(image))
            image = task_screen(serial)
            if not training_page(image, title):
                raise RuntimeError("选择最高等级后未确认兵营，已停止")
            return image, None
    raise RuntimeError("兵种列表未能滑到最高等级端，已停止")


def training_upgrade_dialog(image):
    title = "".join(text.replace(" ", "") for text, _ in
                    recognize_text(image.crop((350, 680, 730, 755))))
    button = "".join(text.replace(" ", "") for text, _ in
                     recognize_text(image.crop((700, 1570, 875, 1655))))
    r, g, b = image.convert("RGB").getpixel((900, 1640))
    return "士兵晋升" in title and "晋升" in button and b > r + 50 and g > r + 30


def training_intro_page(image):
    """Recognize the multi-page new-unit introduction shown on first entry."""
    texts = [text.replace(" ", "").replace("\n", "")
             for text, _ in recognize_text(image.crop(TRAIN_INTRO_PROMPT_BOX))]
    return "点击任意位置继续" in "".join(texts)


def run_training(serial, help_template, nav_template, drawer_template, titles,
                 initial_image=None, keep_open=False, units=None, timers=None, upgrade=False):
    def city(image):
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("训练流程未确认主城，已停止")

    def drawer(image, reset=False):
        city(image)
        templates = {"drawer-open": drawer_template}
        image = reset_drawer(serial, image, templates) if reset else task_drawer(serial, image, templates)
        return image

    image = drawer(initial_image if initial_image is not None else color_screenshot(serial), reset=True)
    units = set(units) if units is not None else {unit for unit, _ in TRAIN_ROWS}
    finishes = []
    for index, (unit, y) in enumerate(TRAIN_ROWS):
        if unit not in units:
            continue
        unit_label = UNIT_TYPES[unit]
        status = training_status(image, y)
        if status == "upgrading":
            print(f"{unit_label}建筑升级中，跳过倒计时记录和自动训练", flush=True)
            continue
        if status == "busy":
            print(f"{unit_label}已在训练中，跳过", flush=True)
            if timers is not None:
                seconds = countdown(image, (125, y + 5, 550, y + 42))
                record_timer(timers, "train", seconds, unit_label)
                finishes.append(timers["train"])
            continue
        task_tap(serial, 608, y)
        image = task_screen(serial)
        city(image)
        if status == "completed":
            task_tap(serial, 530, 1090)
            image = task_screen(serial)
            image = drawer(image)
            if training_status(image, y) != "idle":
                raise RuntimeError(f"{unit_label}已完成队列领取后未变为空闲，已停止")
            task_tap(serial, 608, y)
            image = task_screen(serial)
            city(image)
            print(f"{unit_label}已领取完成的士兵", flush=True)
        task_tap(serial, 725, 1590)
        image = task_screen(serial)
        intro_pages = 0
        while not training_page(image, titles[unit]) and training_intro_page(image):
            if intro_pages >= 12:
                raise RuntimeError(f"{unit_label}新兵种提示连续 12 次未结束，已停止")
            task_tap(serial, 540, 2050)
            image = task_screen(serial)
            intro_pages += 1
        if intro_pages:
            print(f"{unit_label}已关闭新兵种提示，共 {intro_pages} 页", flush=True)
            if not training_page(image, titles[unit]):
                # The introduction returns to the city instead of the training
                # configuration page. Re-enter this barracks through the drawer.
                city(image)
                image = drawer(image)
                task_tap(serial, 608, y)
                image = task_screen(serial)
                city(image)
                task_tap(serial, 725, 1590)
                image = task_screen(serial)
        if not training_page(image, titles[unit]):
            raise RuntimeError(f"未确认{unit_label}训练配置页，已停止")
        upgraded = False
        if upgrade:
            image, target = scan_training_upgrade(serial, image, titles[unit])
            if target is not None:
                task_tap(serial, *target)
                image = task_screen(serial)
                button = Image.open(ROOT / "assets/emulator-training-upgrade-button.png").convert("RGB")
                score, _, _ = find_white(image, button, [928], [1245])
                if not training_page(image, titles[unit]) or score < .9:
                    raise RuntimeError(f"未确认{unit_label}可用晋升入口，已停止")
                task_tap(serial, 975, 1290)
                image = task_screen(serial)
                if not training_upgrade_dialog(image):
                    raise RuntimeError(f"未确认{unit_label}士兵晋升弹窗，已停止")
                task_tap(serial, 775, 1630)  # Blue resource button; yellow spends diamonds.
                image = task_screen(serial)
                if not training_page(image, titles[unit]) or image.getpixel((400, 1900))[1] > 180:
                    raise RuntimeError(f"{unit_label}晋升后未确认队列启动，已停止")
                print(f"{unit_label}已启动兵种升级，晋升至当前最高等级", flush=True)
                upgraded = True
            else:
                print("⚠️ 城内无可升级兵种，改为自动训练新兵，如需升级兵种请将低级兵召回", flush=True)
        r, g, b = image.getpixel((400, 1900))
        if not upgraded and not (g > 180 and g > r + 100 and g > b + 80):
            raise RuntimeError(f"{unit_label}训练配置不在可启动状态，已停止")
        if not upgraded:
            task_tap(serial, 795, 2100)  # 右侧蓝色普通资源训练按钮；左侧黄色按钮消耗钻石
            image = task_screen(serial)
            if (not training_page(image, titles[unit])
                    or image.getpixel((400, 1900))[1] > 180):
                raise RuntimeError(f"{unit_label}训练开始后未确认状态变化，已停止")
            print(f"{unit_label}已启动普通资源训练", flush=True)
        task_tap(serial, 60, 190)
        image = task_screen(serial)
        city(image)
        if index < len(TRAIN_ROWS) - 1 or keep_open or timers is not None:
            image = drawer(image)
        if timers is not None:
            seconds = countdown(image, (125, y + 5, 550, y + 42))
            record_timer(timers, "train", seconds, unit_label)
            finishes.append(timers["train"])
    if finishes:
        timers["train"] = min(finishes)
    elif timers is not None:
        # No training deadline exists while all selected buildings are upgrading.
        timers["train"] = time.monotonic() + 600
    if keep_open:
        return image
    if task_page(image, {"drawer-open": drawer_template}, "drawer-open"):
        task_tap(serial, 695, 1100)
        image = task_screen(serial)
    city(image)
    return image


def tree_amount(image, x, y):
    # Keep the full label/count: a narrow crop can cut off the leading digit.
    for text, _ in recognize_text(green_mask(image).crop((x - 5, y - 10, x + 360, y + 50))):
        amount = parse_tree_amount(text)
        if amount is not None:
            return amount
    return None


def tree_drawer_amount(image, template):
    score, x, y = find_green(image, template, range(180, 241, 4), range(680, 1401, 4))
    amount = tree_amount(image, x, y) if score >= .85 else None
    if amount is not None:
        return amount, y
    for text, (_, row_y, _, _) in recognize_text(image.crop((125, 650, 550, 1470))):
        if text.replace(" ", "").startswith("可收集"):
            amount = parse_tree_amount(text.replace(" ", ""))
            if amount is not None:
                return amount, round(650 + row_y)
    return None, None


def parse_countdown(text):
    match = re.fullmatch(r"\s*(?:(\d+)\s*天\s*)?(\d{1,3})\s*[:：]\s*([0-5]\d)\s*[:：]\s*([0-5]\d)\s*", text)
    if match:
        days, hours, minutes, seconds = (int(value or 0) for value in match.groups())
        return days * 86400 + hours * 3600 + minutes * 60 + seconds
    return None


def countdown(image, box):
    values = [parse_countdown(text) for text, _ in recognize_text(image.crop(box))]
    return next((value for value in values if value is not None), None)


def record_timer(timers, name, seconds, label=None):
    if timers is None:
        return
    label = label or {"train": "练兵", **RECRUIT_TYPES, "warehouse": "仓库补给", "stamina": "体力罐头", "dawn": "晨曦回礼"}.get(name, name)
    delay = max(1, seconds) if seconds is not None else 60
    timers[name] = time.monotonic() + delay
    if seconds is None:
        print(f"{label} 倒计时未识别，60 秒后重新检测", flush=True)
    else:
        finish = datetime.fromtimestamp(time.time() + delay)
        print(f"{label} 倒计时 {seconds} 秒，预计完成时间 {finish:%Y-%m-%d %H:%M:%S}", flush=True)


def pet_page(image):
    with Image.open(ROOT / "assets/emulator-pet-title.png") as template:
        return difference(image.crop(PET_TITLE_BOX).convert("L"), template.convert("L")) < 15


def pet_skill_state(image, label):
    if not pet_page(image):
        raise RuntimeError("未确认宠物技能弹窗")
    names = [text.replace(" ", "") for text, _ in recognize_text(image.crop((80, 1430, 700, 1510)))]
    if not any(text.startswith(label) for text in names):
        raise RuntimeError(f"未确认宠物技能 {label}，识别结果：{names}")
    hints = [text.replace(" ", "") for text, _ in recognize_text(image.crop((300, 1850, 800, 1960)))]
    if "生效中" in hints:
        return "active", None
    for hint in hints:
        if hint.startswith("冷却中"):
            seconds = parse_countdown(re.sub(r"^冷却中[:：]?", "", hint))
            if seconds is None:
                # OCR can turn the prefix punctuation into an extra digit.
                # Read the selected skill's own icon instead of guessing digits.
                target = next((target for skill, target in PET_SKILLS.values() if skill == label), None)
                if target is not None:
                    x, y = target
                    seconds = countdown(image, (x - 100, y + 20, x + 110, y + 110))
            return "cooldown", seconds
    # Only an explicitly labelled, coloured use button is actionable.
    pixels = list(image.crop((350, 1870, 420, 1940)).convert("RGB").getdata())
    enabled = sum((b > r + 40 or g > r + 40) and max(g, b) > 140 for r, g, b in pixels) > len(pixels) / 2
    if "使用" in hints and enabled:
        return "ready", None
    return "unknown", None


def close_pet_page(serial, image, help_template, nav_template):
    if pet_page(image):
        task_tap(serial, 995, 505)
        image = task_screen(serial)
    if not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("宠物技能检查后未返回主城")
    return image


def pet_reward_page(image):
    with Image.open(ROOT / "assets/emulator-dawn-reward-title.png") as title:
        if difference(image.crop((380, 680, 700, 790)).convert("L"), title.convert("L")) >= 15:
            return False
    return any("点击任意位置退出" in text.replace(" ", "")
               for text, _ in recognize_text(image.crop((280, 2130, 800, 2250))))


def run_pet_skill(serial, help_template, nav_template, drawer_template, name, timers,
                  keep_open=False):
    label, target = PET_SKILLS[name]
    image = color_screenshot(serial)
    if not (keep_open and pet_page(image)):
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("宠物技能检查前未确认主城")
        if task_page(image, {"drawer-open": drawer_template}, "drawer-open"):
            task_tap(serial, 695, 1100)
            image = task_screen(serial)
        with Image.open(ROOT / "assets/emulator-pet-button.png") as template:
            if difference(image.crop(PET_BUTTON_BOX).convert("L"), template.convert("L")) > 15:
                raise RuntimeError("未确认宠物技能爪印入口")
        task_tap(serial, 995, 1720)
        image = task_screen(serial)
    for attempt in range(5):
        if pet_page(image):
            break
        if attempt < 4:
            image = task_screen(serial)
    else:
        raise RuntimeError("未打开宠物技能弹窗")
    task_tap(serial, *target)
    image = task_screen(serial)
    state, seconds = pet_skill_state(image, label)
    if state == "ready":
        task_tap(serial, 540, 1910)
        for _ in range(5):
            image = task_screen(serial)
            if not pet_page(image) and pet_reward_page(image):
                task_tap(serial, 540, 2100)
                image = task_screen(serial)
            if pet_page(image):
                state, seconds = pet_skill_state(image, label)
                if state in ("cooldown", "active"):
                    break
        else:
            raise RuntimeError(f"{label} 使用后未确认进入冷却或生效状态")
        print(f"已使用宠物技能：{label}", flush=True)
    elif state == "unknown":
        print(f"{label} 使用状态未识别，本次不点击", flush=True)
    if state == "active":
        if timers is not None:
            timers[name] = time.monotonic() + 600
        print(f"{label} 生效中，不重复使用；10 分钟后复查", flush=True)
    else:
        record_timer(timers, name, seconds, label)
    if not keep_open or state == "unknown":
        image = close_pet_page(serial, image, help_template, nav_template)
    return image


def recruit_row(serial, image, templates, kind):
    image = task_drawer(serial, image, templates)
    for attempt in range(DRAWER_SWIPES + 1):
        texts = recognize_text(image.crop((240, 710, 550, 1430)))
        for text, (_, y, _, height) in texts:
            normalized = text.replace(" ", "")
            if RECRUIT_TYPES[kind] in normalized:
                row_bottom = round(710 + y + height)
                if row_bottom + 55 <= 1430:
                    return image, row_bottom
        normalized_text = "".join(text.replace(" ", "") for text, _ in texts)
        later_labels = (("史诗招募", "仓库补给", "生命之树", "晨曦回礼")
                        if kind == "recruit_advanced" else
                        ("仓库补给", "生命之树", "晨曦回礼"))
        if any(label in normalized_text for label in later_labels):
            return image, None
        if attempt < DRAWER_SWIPES:
            image = swipe_drawer(serial, templates["drawer-open"])
    return image, None


def recruit_timer(serial, image, templates, timers, kind="recruit_advanced"):
    if timers is None:
        return image
    image, row_bottom = recruit_row(serial, image, templates, kind)
    record_timer(timers, kind, countdown(image, (125, row_bottom, 550, row_bottom + 55))
                 if row_bottom is not None else None)
    return image


def treasure_page(image):
    with Image.open(ROOT / "assets/emulator-treasure-title.png") as title:
        return difference(image.crop((140, 165, 345, 225)).convert("L"), title.convert("L")) <= 12


def treasure_remaining(image):
    if not treasure_page(image):
        return None
    text = "".join(text.replace(" ", "") for text, _ in
                   recognize_text(image.crop((280, 270, 800, 390))))
    match = re.search(r"今日剩余寻宝次数[:：]?(\d+)", text)
    return int(match[1]) if match else None


def treasure_back_target(image):
    if intelligence_text_target(image, ("联盟宝藏",), (400, 470, 680, 550)):
        return 1000, 510
    if intelligence_text_target(image, ("寻宝完成",), (350, 680, 730, 810)):
        return 960, 640
    if intelligence_text_target(image, ("寻宝中",), (350, 680, 730, 810)):
        return 960, 680
    if intelligence_text_target(image, ("开始寻宝",), (300, 1700, 780, 1940)):
        return 995, 610
    if intelligence_text_target(image, ("寻宝派遣",), (300, 1700, 780, 1940)):
        return 960, 595
    if treasure_page(image):
        return 60, 190
    return None


def treasure_completed_target(image, near=None):
    if not treasure_page(image):
        return None
    with Image.open(ROOT / "assets/emulator-treasure-completed.png") as badge:
        x_range = (range(0, image.width - badge.width + 1, 8) if near is None else
                   range(max(0, near[0] - badge.width // 2 - 20), near[0] - badge.width // 2 + 21, 4))
        y_range = (range(430, 1851, 8) if near is None else
                   range(max(0, near[1] - badge.height // 2 - 20), near[1] - badge.height // 2 + 21, 4))
        score, x, y = find_green(image, badge, x_range, y_range)
        if score >= .90:
            return x + badge.width // 2, y + badge.height // 2
    return None


def treasure_chest_target(image):
    if not treasure_page(image):
        return None
    gray = image.convert("L")
    # 倒计时位于正在寻宝宝箱下方；遮住其本体，避免点进无派遣按钮的弹窗。
    for text, (x, y, width, _) in recognize_text(image.crop((0, 430, 1080, 2050))):
        if parse_countdown(text) is not None:
            gray.paste(0, (max(0, round(x - 100)), round(430 + y - 270),
                           min(1080, round(x + width + 100)), round(430 + y - 60)))
    small = gray.resize((270, 585))
    # 红色稀有宝箱使用现有 orange 模板，按品质优先而非匹配分数选择。
    for name in ("chest-orange", "chest-purple", "chest"):
        with Image.open(ROOT / f"assets/emulator-treasure-{name}.png") as chest:
            # 只比较宝箱本体；白色外圈旁的雪地和光效会随位置、动画改变。
            target = chest.crop((30, 40, 170, 160)).convert("L")
        thumbnail = target.resize((35, 30))
        _, x, y = min((difference(small.crop((x, y, x + 35, y + 30)), thumbnail), x, y)
                      for y in range(108, 501, 4) for x in range(0, 236, 4))
        match = min((difference(gray.crop((xx, yy, xx + 140, yy + 120)), target), xx, yy)
                    for yy in range(y * 4 - 16, y * 4 + 17)
                    for xx in range(max(0, x * 4 - 16), min(941, x * 4 + 17)))
        score, x, y = match
        if score <= 15:
            return x + 69, y + 51
    return None


def claim_treasure(serial, image):
    target = treasure_completed_target(image)
    if target is None:
        raise RuntimeError("已完成的宠物寻宝未找到打勾宝箱")
    task_tap(serial, *target)
    for _ in range(5):
        image = task_screen(serial)
        if intelligence_text_target(image, ("寻宝完成",), (350, 680, 730, 810)):
            break
    else:
        raise RuntimeError("未确认寻宝完成弹窗")
    task_tap(serial, 540, 1490)
    for _ in range(10):
        image = task_screen(serial)
        if intelligence_text_target(image, ("点击任意位置退出",), (280, 2130, 800, 2250)):
            task_tap(serial, 540, 2100)
            break
    else:
        raise RuntimeError("领取寻宝宝箱后未确认奖励页面")
    for _ in range(5):
        image = task_screen(serial)
        if intelligence_text_target(image, ("寻宝完成",), (350, 680, 730, 810)):
            task_tap(serial, 960, 640)
            continue
        if treasure_page(image) and treasure_completed_target(image, near=target) is None:
            print("已领取宠物寻宝奖励", flush=True)
            return image
    raise RuntimeError("领取寻宝奖励后未确认完成标记消失")


def claim_alliance_treasure(serial, image):
    if not treasure_page(image) or not red_dot(image, (980, 2150, 1020, 2190)):
        return image
    task_tap(serial, 950, 2220)
    for _ in range(5):
        image = task_screen(serial)
        if intelligence_text_target(image, ("联盟宝藏",), (400, 470, 680, 550)):
            target = intelligence_text_target(image, ("一键领取",), (300, 1810, 780, 1980))
            if target is not None:
                break
    else:
        raise RuntimeError("联盟宝藏未确认一键领取按钮")
    task_tap(serial, *target)
    for _ in range(10):
        image = task_screen(serial)
        if intelligence_text_target(image, ("点击任意位置退出",), (280, 2130, 800, 2250)):
            task_tap(serial, 540, 2100)
            break
    else:
        raise RuntimeError("联盟宝藏一键领取后未确认奖励页面")
    for _ in range(5):
        image = task_screen(serial)
        if intelligence_text_target(image, ("联盟宝藏",), (400, 470, 680, 550)):
            task_tap(serial, 1000, 510)
            continue
        if treasure_page(image):
            print("已一键领取联盟宝藏奖励", flush=True)
            return image
    raise RuntimeError("领取联盟宝藏后未返回宠物寻宝页面")


def run_treasure(serial, help_template, nav_template, drawer_template):
    image = color_screenshot(serial)
    if not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("宠物寻宝检查前未确认主城")
    templates = {"drawer-open": drawer_template}
    image = task_drawer(serial, image, templates, reset_unknown=False)
    target = None
    completed = False
    for attempt in range(DRAWER_SWIPES + 2):
        texts = recognize_text(image.crop((240, 710, 550, 1470)))
        row_bottom = next((round(710 + y + height) for text, (_, y, _, height) in texts
                           if text.replace(" ", "") == "宠物寻宝"), None)
        if row_bottom is not None and row_bottom + 55 <= 1470:
            completed = intelligence_text_target(
                image, ("已完成",), (240, row_bottom, 550, row_bottom + 55)) is not None
            if completed or intelligence_text_target(image, ("可寻宝",), (240, row_bottom, 550, row_bottom + 55)):
                target = (605, row_bottom + 15)
            break
        if attempt == 0:
            # 先检查当前抽屉，找不到该行才回到顶部重新向下扫描。
            image = reset_drawer(serial, image, templates)
            continue
        if any(label in text.replace(" ", "") for text, _ in texts
               for label in ("生命之树", "生命结晶", "晨曦回礼")):
            break
        if attempt < DRAWER_SWIPES + 1:
            image = swipe_drawer(serial, drawer_template)
    if target is None:
        print("抽屉里没有宠物可寻宝或已完成提示，跳过宠物寻宝", flush=True)
        return image
    task_tap(serial, *target)
    for _ in range(5):
        image = task_screen(serial)
        remaining = treasure_remaining(image)
        if remaining is not None:
            break
    else:
        raise RuntimeError("未确认宠物寻宝页面或今日剩余寻宝次数")
    image = claim_alliance_treasure(serial, image)
    if treasure_completed_target(image) is not None:
        for _ in range(3):
            image = claim_treasure(serial, image)
            if treasure_completed_target(image) is None:
                break
        else:
            raise RuntimeError("领取三个寻宝宝箱后仍有完成标记")
        remaining = treasure_remaining(image)
        if remaining is None:
            raise RuntimeError("领取寻宝奖励后未识别今日剩余次数")
    if remaining > 0:
        for attempt in range(5):
            target = treasure_chest_target(image)
            if target is not None:
                break
            if attempt < 4:
                image = task_screen(serial)
        else:
            raise RuntimeError("宠物寻宝页面未找到宝箱图标")
        task_tap(serial, *target)
        for label in ("寻宝派遣", "开始寻宝"):
            for _ in range(5):
                image = task_screen(serial)
                target = intelligence_text_target(image, (label,), (300, 1700, 780, 1940))
                if target is not None:
                    break
            else:
                raise RuntimeError(f"宠物寻宝未确认{label}按钮")
            task_tap(serial, *target)
        for _ in range(5):
            image = task_screen(serial)
            after = treasure_remaining(image)
            if after is not None and after < remaining:
                break
            if intelligence_text_target(image, ("寻宝中",), (350, 680, 730, 810)):
                task_tap(serial, 960, 680)
        else:
            raise RuntimeError("开始寻宝后未确认今日剩余次数减少")
        print(f"已开始宠物寻宝，今日剩余次数 {remaining} → {after}", flush=True)
    else:
        print("今日剩余寻宝次数为 0，跳过派遣", flush=True)
    task_tap(serial, 60, 190)
    for _ in range(5):
        image = task_screen(serial)
        if inspect(image.convert("L"), help_template, nav_template)[0]:
            return task_drawer(serial, image, templates)
    raise RuntimeError("宠物寻宝结束后未确认返回主城")


def island_page(image, template):
    # Only match title lettering: the island scenery behind it changes with camera position.
    crop = image.crop((120, 140, 310, 230)).convert("L")
    if difference(crop, template) <= 10:
        return True
    return any("我的海岛" in text.replace(" ", "")
               for text, _ in recognize_text(image.crop((100, 140, 370, 235))))


def crystal_target(image, template, near=None):
    tutorial = Image.open(ROOT / "assets/emulator-crystal-claim-tutorial.png").convert("RGB")
    best = None
    for candidate, limit in ((template, .82), (tutorial, .88)):
        x_range = range(80, 941, 8) if near is None else range(max(0, near[0] - 80), near[0] + 40, 4)
        y_range = range(300, 1901, 8) if near is None else range(max(0, near[1] - 80), near[1] + 40, 4)
        score, x, y = find_green(image, candidate, x_range, y_range)
        if score >= limit and (best is None or score > best[0]):
            best = (score, x + candidate.width // 2, y + candidate.height // 2)
    return best[1:] if best else None


def collect_crystals(serial, image, island_template):
    template = Image.open(ROOT / "assets/emulator-crystal-claim.png").convert("RGB")
    collected = 0
    for _ in range(30):
        target = None
        empty_frames = 0
        for attempt in range(8):
            if island_page(image, island_template):
                target = crystal_target(image, template)
                if target is not None:
                    break
                empty_frames += 1
                if empty_frames >= 3:
                    break
            else:
                empty_frames = 0
            if attempt < 7:
                image = task_screen(serial)
        if target is None:
            if empty_frames < 3:
                raise RuntimeError("海岛页面加载不稳定，未确认结晶领取结果")
            if collected == 0:
                raise RuntimeError("抽屉显示结晶可领取，但海岛未找到领取图标，未确认领取成功")
            print(f"海岛结晶领取完成，共 {collected} 处", flush=True)
            return image
        task_tap(serial, *target)
        absent_frames = 0
        for attempt in range(8):
            image = task_screen(serial)
            if not island_page(image, island_template):
                absent_frames = 0
                continue
            if crystal_target(image, template, near=target) is None:
                absent_frames += 1
            else:
                absent_frames = 0
            if absent_frames >= 2:
                collected += 1
                break
        else:
            raise RuntimeError("海岛结晶点击后领取图标仍未消失")
    raise RuntimeError("海岛连续领取 30 处后仍有图标，请检查识别结果")


def wait_city_after_island(serial, help_template, nav_template):
    confirmations = 0
    for _ in range(8):
        image = task_screen(serial)
        city, _, nav_difference = inspect(image.convert("L"), help_template, nav_template)
        confirmations = confirmations + 1 if city else 0
        if confirmations >= 2:
            return image
    path = ROOT / "artifacts" / f"island-return-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
    image.save(path)
    maybe_cleanup_diagnostic_screenshots(force=True)
    raise RuntimeError(f"领取结晶后返回主城超时；底栏差异 {nav_difference:.1f}；截图 {path}")


def cleanup_diagnostic_screenshots():
    """Keep recent screenshots generated by failed island returns and tasks."""
    directory = ROOT / "artifacts"
    cutoff = time.time() - 30 * 86400
    removed = 0
    for prefix in ("island-return", "recruit-failure", "treasure-failure"):
        names = sorted((path for path in directory.glob(f"{prefix}-*.png")
                       if re.fullmatch(rf"{prefix}-\d{{8}}-\d{{6}}-\d{{6}}\.png", path.name)),
                       reverse=True)
        for index, path in enumerate(names):
            try:
                if index >= 20 or path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
    if removed:
        print(f"已清理 {removed} 张过期故障截图", flush=True)


def maybe_cleanup_diagnostic_screenshots(force=False):
    global DIAGNOSTIC_CLEANUP_AT
    now = time.monotonic()
    if not force and now < DIAGNOSTIC_CLEANUP_AT:
        return
    DIAGNOSTIC_CLEANUP_AT = now + 86400
    try:
        cleanup_diagnostic_screenshots()
    except OSError as exc:
        print(f"清理海岛故障截图失败：{exc}", flush=True)


def parse_tree_amount(text):
    text = re.sub(r"^\s*可收集\s*", "", text).replace("，", ",")
    match = re.match(r"\s*(\d{1,2},\d{3}|\d{1,5})\s*/", text)
    return int(match[1].replace(",", "")) if match else None


def warehouse_position(image):
    """Locate the facade, which remains visible under both reward icons and timers."""
    template = Image.open(ROOT / "assets/emulator-warehouse-front.png").convert("L")
    gray = image.convert("L")
    small = gray.resize((270, 585))
    target = template.resize((40, 30))
    _, x, y = min((difference(small.crop((x, y, x + 40, y + 30)), target), x, y)
                  for y in range(150, 481, 4) for x in range(10, 211, 4))
    score, x, y = min((difference(gray.crop((xx, yy, xx + 160, yy + 120)), template), xx, yy)
                      for yy in range(y * 4 - 16, y * 4 + 17, 2)
                      for xx in range(x * 4 - 16, x * 4 + 17, 2))
    return (x - 260, y - 1330) if score < 20 else None


def warehouse_countdown(image, known_warehouse=False, offset=None):
    if offset is None:
        roof = Image.open(ROOT / "assets/emulator-warehouse-roof.png").convert("L")
        if not known_warehouse and difference(image.crop((470, 1010, 655, 1155)).convert("L"), roof) > 20:
            return None
        offset = (0, 0)
    dx, dy = offset
    box = (395 + dx, 1160 + dy, 700 + dx, 1240 + dy)
    # 绿色时间是罐头的可领取剩余时间，不能当成下次领取倒计时。
    if ImageStat.Stat(green_mask(image.crop(box))).sum[0] > 255 * 30:
        return None
    return countdown(image, box)


def warehouse_reward_timer(image):
    lines = [text.replace(" ", "") for text, _ in
             recognize_text(image.crop((330, 1850, 800, 2020)))]
    if not any("距离下个包裹" in text for text in lines):
        return False, None
    values = [parse_countdown(text) for text in lines]
    return True, next((value for value in values if value is not None), None)


def labelled_countdown(image, box, labels):
    """Read a time only from its label's line or the immediately following line."""
    lines = [text.replace(" ", "") for text, _ in recognize_text(image.crop(box))]
    for index, line in enumerate(lines):
        label = next((label for label in labels if label in line), None)
        if label is None:
            continue
        suffix = line.split(label, 1)[1]
        match = re.search(r"(?:(?:\d+)\s*天\s*)?\d{1,3}\s*[:：]\s*[0-5]\d\s*[:：]\s*[0-5]\d", suffix)
        seconds = parse_countdown(match.group(0)) if match else None
        if seconds is not None:
            return seconds
        if not suffix and index + 1 < len(lines):
            seconds = parse_countdown(lines[index + 1])
            if seconds is not None:
                return seconds
    return None


def stamina_page(image):
    with Image.open(ROOT / "assets/emulator-can-modal-title.png") as title:
        return difference(image.crop((390, 1390, 690, 1480)).convert("L"), title.convert("L")) <= 15


def stamina_countdown(image):
    # The white timer on the warehouse is its package timer. Only a timer
    # explicitly shown on the can page may schedule the can task.
    if not stamina_page(image):
        return None
    return labelled_countdown(image, (300, 1480, 830, 1800),
                              ("剩余时间", "距离下次领取", "下次可领取", "下次领取",
                               "距离下次招待", "下次招待"))


def stamina_target(image, offset=None):
    template = Image.open(ROOT / "assets/emulator-can-icon.png").convert("L")
    if offset is None:
        x_range = range(0, image.width - template.width + 1, 10)
        y_range = range(280, 2050 - template.height + 1, 10)
    else:
        dx, dy = offset
        x_range, y_range = range(430 + dx, 526 + dx, 5), range(930 + dy, 1066 + dy, 5)
    score, x, y = find_white(image, template, x_range, y_range)
    if (score >= .85 and difference(image.crop((x, y, x + template.width, y + template.height)).convert("L"),
                                   template) < 35):
        return x + template.width // 2, y + template.height // 2
    return None


def reward_row(image, template, label):
    match = find_white(image, template, range(240, 351, 5), range(650, 1420, 5))
    if match[0] >= .85:
        return match
    # Dimmed drawer labels vary with the city behind the translucent panel.
    for text, (x, y, _, _) in recognize_text(image.crop((240, 650, 550, 1470))):
        if text.replace(" ", "") == label:
            return 1.0, round(240 + x), round(650 + y)
    return match


def run_stamina(serial, help_template, nav_template, drawer_template, timers=None, initial_image=None):
    def city(image):
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("体力罐头流程未确认主城，已停止")

    image = initial_image if initial_image is not None else color_screenshot(serial)
    stamina_timer_recorded = False
    city(image)
    templates = {"drawer-open": drawer_template}
    if task_page(image, templates, "drawer-open"):
        task_tap(serial, 695, 1100)
        image = task_screen(serial)
        city(image)
    offset = warehouse_position(image)
    if offset is None:
        # 先定位盾兵兵营；向右下拖动地图，视角才会朝左上约 10 点钟移动。
        image = reset_drawer(serial, image, templates)
        city(image)
        task_tap(serial, 608, TRAIN_ROWS[0][1])
        for _ in range(5):
            image = task_screen(serial)
            city(image)
            if not task_page(image, templates, "drawer-open"):
                break
        else:
            raise RuntimeError("体力罐头检查前未能通过盾兵训练定位兵营")
        for _ in range(3):
            if not game_foreground(serial):
                raise RuntimeError("当前前台不是目标游戏，已停止")
            # 起点避开兵营操作按钮；每次移动后确认仓库，找到就停止。
            adb(serial, "shell", "input", "swipe", "200", "650", "800", "1000", "800")
            image = task_screen(serial)
            city(image)
            offset = warehouse_position(image)
            if offset is not None:
                break
        else:
            raise RuntimeError("体力罐头检查前未能定位仓库，未开始检测罐头")
    # 等待补给奖励退出、地图移动和图标切换动画。
    for attempt in range(5):
        city(image)
        target = stamina_target(image, offset)
        if target is not None:
            task_tap(serial, *target)
            button = Image.open(ROOT / "assets/emulator-can-button.png").convert("L")
            for _ in range(5):
                image = task_screen(serial)
                if stamina_page(image):
                    break
            if not stamina_page(image):
                raise RuntimeError("未确认罐头领取页面，已停止")
            available_seconds = stamina_countdown(image)
            if available_seconds is not None:
                record_timer(timers, "stamina", available_seconds)
                stamina_timer_recorded = True
            else:
                print("体力罐头倒计时未识别", flush=True)
            if difference(image.crop((350, 1620, 735, 1760)).convert("L"), button) > 20:
                seconds = stamina_countdown(image)
                if seconds is None:
                    raise RuntimeError("罐头页面未确认领取按钮或下次领取倒计时，已停止")
                record_timer(timers, "stamina", seconds)
                stamina_timer_recorded = True
                task_tap(serial, 920, 550)  # Confirmed can page's close button.
                for _ in range(5):
                    image = task_screen(serial)
                    if inspect(image.convert("L"), help_template, nav_template)[0]:
                        return image
                raise RuntimeError("关闭罐头页面后未确认主城")
            task_tap(serial, 530, 1700)
            for _ in range(5):
                image = task_screen(serial)
                if (inspect(image.convert("L"), help_template, nav_template)[0]
                        and stamina_target(image, warehouse_position(image)) is None):
                    break
            else:
                raise RuntimeError("罐头领取后未确认返回主城，已停止")
            print("已领取体力罐头", flush=True)
            break
        if attempt < 4:
            image = task_screen(serial)
    else:
        print("当前主城画面未发现体力罐头图标", flush=True)
    if not stamina_timer_recorded:
        record_timer(timers, "stamina", None)
    return image


def run_rewards(serial, help_template, nav_template, drawer_template,
                warehouse_template, tree_template, island_template, initial_image=None,
                warehouse=True, tree=True, dawn=True, tree_threshold=1000, timers=None, keep_open=False):
    def city(image):
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("补给流程未确认主城，已停止")

    def bottom_drawer(image, kind):
        city(image)
        image = task_drawer(serial, image, {"drawer-open": drawer_template})
        label = {"warehouse": "仓库补给", "tree": "生命之树", "dawn": "晨曦回礼"}[kind]
        row_template = (warehouse_template if kind == "warehouse" else
                        Image.open(ROOT / "assets/emulator-dawn-row-title.png").convert("RGB")
                        if kind == "dawn" else None)
        # Detect the requested row on each frame. A fixed swipe count can stop
        # above it, or scroll past it when resuming another drawer task.
        for attempt in range(DRAWER_SWIPES + 1):
            texts = recognize_text(image.crop((240, 710, 550, 1400)))
            normalized_text = "".join(text.replace(" ", "") for text, _ in texts)
            if kind == "tree":
                if tree_drawer_amount(image, tree_template)[0] is not None:
                    return image, True
                visible = label in normalized_text
            else:
                visible = reward_row(image, row_template, label)[0] >= .85
            if visible:
                return image, True
            later_labels = (("生命之树", "晨曦回礼") if kind == "warehouse" else
                            ("晨曦回礼",) if kind == "tree" else ())
            if any(later_label in normalized_text for later_label in later_labels):
                return image, False
            if attempt < DRAWER_SWIPES:
                image = swipe_drawer(serial, drawer_template)
        return image, False

    image = initial_image if initial_image is not None else color_screenshot(serial)
    warehouse_timer_recorded = False
    drawer_kind = "warehouse" if warehouse else "tree" if tree else "dawn"
    image, drawer_visible = bottom_drawer(image, drawer_kind)
    score, _, warehouse_y = (reward_row(image, warehouse_template, "仓库补给")
                             if warehouse and drawer_visible else (0, 0, 0))
    if warehouse and score >= 0.85 and training_status(image, warehouse_y + 30) == "completed":
        task_tap(serial, 608, warehouse_y + 30)
        image = task_screen(serial)
        city(image)
        task_tap(serial, 530, 1050)
        for _ in range(5):
            image = task_screen(serial)
            confirmed, reward_seconds = warehouse_reward_timer(image)
            if confirmed:
                break
        else:
            raise RuntimeError("仓库补给未出现已确认的奖励页面，已停止")
        task_tap(serial, 530, 1900)
        for _ in range(5):
            image = task_screen(serial)
            if inspect(image.convert("L"), help_template, nav_template)[0]:
                break
        city(image)
        print("已领取仓库补给", flush=True)
        if timers is not None:
            # Do not reset the independent can deadline every time a package
            # is collected. Missing package OCR is retried on its own schedule.
            record_timer(timers, "warehouse", reward_seconds)
            warehouse_timer_recorded = True
    elif warehouse:
        print("仓库补给尚不可领取", flush=True)

    if warehouse and timers is not None and not warehouse_timer_recorded:
        score, _, row_y = reward_row(image, warehouse_template, "仓库补给")
        record_timer(timers, "warehouse", countdown(image, (125, row_y + 30, 550, row_y + 85))
                     if score >= .85 else None)

    if tree and drawer_kind != "tree":
        image, drawer_visible = bottom_drawer(image, "tree")
        drawer_kind = "tree"
    if tree and not drawer_visible:
        raise RuntimeError("未定位到生命之树任务行，未执行领取")
    amount, y = tree_drawer_amount(image, tree_template) if tree else (None, None)
    if tree and amount is None:
        raise RuntimeError("未识别生命之树可收集数量，未执行领取")
    if tree and amount is not None and amount >= tree_threshold:
        task_tap(serial, 608, y - 5)
        for _ in range(8):
            image = task_screen(serial)
            if island_page(image, island_template):
                break
        else:
            raise RuntimeError("进入生命之树后等待 8 次截图仍未确认海岛页面，已停止")
        image = collect_crystals(serial, image, island_template)
        task_tap(serial, 60, 190)
        image = wait_city_after_island(serial, help_template, nav_template)
        image, drawer_visible = bottom_drawer(image, "tree")
        drawer_kind = "tree"
        if not drawer_visible:
            raise RuntimeError("领取后未重新定位到生命之树任务行")
        remaining, _ = tree_drawer_amount(image, tree_template)
        for _ in range(4):
            if remaining is not None and remaining < amount:
                break
            image = task_screen(serial)
            remaining, _ = tree_drawer_amount(image, tree_template)
        if remaining is None or remaining >= amount:
            raise RuntimeError("返回主城后未确认可收集结晶数量减少，未确认领取成功")
        print(f"生命结晶领取已确认：可收集数量 {amount} → {remaining}", flush=True)
    elif tree:
        status = f"不足 {tree_threshold}" if amount is not None else "未识别"
        print(f"生命之树可收集数{status}，跳过", flush=True)

    if dawn and drawer_kind != "dawn":
        image, drawer_visible = bottom_drawer(image, "dawn")
        drawer_kind = "dawn"
    if dawn:
        dawn_template = Image.open(ROOT / "assets/emulator-dawn-row-title.png").convert("RGB")
        score, _, dawn_y = (reward_row(image, dawn_template, "晨曦回礼")
                            if drawer_visible else (0, 0, 0))
    if dawn and score >= 0.85 and training_status(image, dawn_y + 50) == "completed":
        task_tap(serial, 608, dawn_y + 50)
        image = task_screen(serial)
        title_box = (405, 520, 660, 630)
        title = Image.open(ROOT / "assets/emulator-dawn-title.png").convert("L")
        button_box = (360, 1580, 730, 1730)
        button = Image.open(ROOT / "assets/emulator-dawn-button.png").convert("L")
        if (difference(image.crop(title_box).convert("L"), title) > 15
                or difference(image.crop(button_box).convert("L"), button) > 20):
            raise RuntimeError("未确认晨曦回礼领取页面，已停止")
        task_tap(serial, 530, 1650)
        image = task_screen(serial)
        reward_box = (380, 680, 700, 790)
        reward = Image.open(ROOT / "assets/emulator-dawn-reward-title.png").convert("L")
        for _ in range(5):
            if difference(image.crop(reward_box).convert("L"), reward) < 15:
                break
            image = task_screen(serial)
        else:
            raise RuntimeError("晨曦回礼领取后未确认奖励页面，已停止")
        task_tap(serial, 530, 1900)
        image = task_screen(serial)
        city(image)
        print("已领取晨曦回礼", flush=True)
        if timers is not None:
            print("正在打开任务抽屉，读取晨曦回礼下次领取倒计时", flush=True)
    elif dawn:
        print("晨曦回礼尚不可领取", flush=True)
    if dawn and timers is not None:
        image, drawer_visible = bottom_drawer(image, "dawn")
        score, _, row_y = (reward_row(image, dawn_template, "晨曦回礼")
                           if drawer_visible else (0, 0, 0))
        record_timer(timers, "dawn", countdown(image, (125, row_y + 30, 550, row_y + 100))
                     if score >= .85 else None)

    if keep_open:
        return image
    if task_page(image, {"drawer-open": drawer_template}, "drawer-open"):
        task_tap(serial, 695, 1100)
        image = task_screen(serial)
    city(image)
    return image


def self_test(help_template, nav_template, text_template, reconnect_template):
    for name, should_find in (("emulator-current", True), ("emulator-final", False)):
        image = Image.open(ROOT / f"artifacts/{name}.png").convert("L")
        city, match, nav = inspect(image, help_template, nav_template)
        print(f"{name}: city={city}, help_score={match[0] if match else None}, nav_score={nav:.1f}")
        assert city and (match[0] <= HELP_LIMIT) == should_find
        assert not forced_offline(image, text_template, reconnect_template)
    image = Image.open(ROOT / "artifacts/reconnect-current-emulator.png").convert("L")
    assert forced_offline(image, text_template, reconnect_template)
    assert not inspect(image, help_template, nav_template)[0]
    city, match, _ = inspect(Image.new("L", EXPECTED_SIZE), help_template, nav_template)
    assert not city and match is None
    print("主城和强制下线样本识别正确")


def run(serial, seconds, interval, dry_run, max_clicks,
        help_template, nav_template, text_template, reconnect_template, announce_end=True,
        reconnect=True, help_enabled=True, return_on_reconnect=False, allow_wilderness=False):
    duration = f"{seconds:.1f}".rstrip("0").rstrip(".")
    print(f"设备 {serial}；本轮最多监控 {duration} 秒；"
          f"{'只观察' if dry_run else '允许点击'}；Ctrl-C 可停止", flush=True)
    deadline = time.monotonic() + seconds
    clicks = 0
    not_city = 0
    paused_at = None
    chat_misses = 0
    while time.monotonic() < deadline or paused_at is not None:
        maybe_cleanup_diagnostic_screenshots()
        image = screenshot(serial)
        offline = forced_offline(image, text_template, reconnect_template)
        if offline and not reconnect:
            raise RuntimeError("检测到强制下线，自动重连已关闭")
        if paused_at is not None:
            if not offline:
                # Disappearance can mean a loading screen or a welcome popup;
                # resume tasks only after confirmed city recovery.
                if not return_to_city(serial, help_template, nav_template,
                                      text_template, reconnect_template):
                    deadline += time.monotonic() - paused_at
                    paused_at = time.monotonic()
                    print("恢复过程中再次强制下线，重新等待", flush=True)
                    continue
                deadline += time.monotonic() - paused_at
                paused_at = None
                not_city = 0
                print("强制下线弹窗已消失且已返回主城，取消重连点击", flush=True)
                if return_on_reconnect:
                    return True
                continue
            remaining = paused_at + RECONNECT_DELAY - time.monotonic()
            if remaining > 0:
                print(f"强制下线冷却中，还需 {remaining:.0f} 秒", flush=True)
                time.sleep(min(30, remaining))
                continue
            if not game_foreground(serial):
                raise RuntimeError("游戏不在前台，未点击重新连接")
            target = reconnect_target(screenshot(serial), text_template, reconnect_template)
            if target is None:
                continue
            x, y = target
            adb(serial, "shell", "input", "tap", str(x), str(y))
            print(f"已等待 {RECONNECT_DELAY / 60:g} 分钟并点击重新连接", flush=True)
            not_city = 0
            time.sleep(2)
            recovered = return_to_city(serial, help_template, nav_template,
                                       text_template, reconnect_template)
            deadline += time.monotonic() - paused_at
            paused_at = None
            if not recovered:
                paused_at = time.monotonic()
                print("重连后仍显示强制下线，重新等待", flush=True)
            elif return_on_reconnect:
                return True
            continue
        if offline:
            paused_at = time.monotonic()
            print(f"检测到其他设备登录导致强制下线，暂停其他操作 {RECONNECT_DELAY / 60:g} 分钟", flush=True)
            if dry_run:
                print("只观察模式，不等待或点击重新连接", flush=True)
                return
            continue
        city, match, nav = inspect(image, help_template, nav_template, allow_wilderness=allow_wilderness)
        if not city:
            not_city += 1
            print(f"未确认主城 ({not_city}/5)，底栏差异 {nav:.1f}", flush=True)
            if not_city >= 5:
                raise RuntimeError("连续五次未确认主城，已停止")
        else:
            not_city = 0
            score, dx, dy = match
            if help_enabled and score <= HELP_LIMIT and time.monotonic() >= HELP_RETRY_AT:
                print(f"发现互助，匹配差异 {score:.1f}，偏移 ({dx}, {dy})", flush=True)
                if dry_run:
                    return
                if not game_foreground(serial):
                    raise RuntimeError("当前前台不是目标游戏，已停止")
                x = (HELP_BOX[0] + HELP_BOX[2]) // 2 + dx
                y = (HELP_BOX[1] + HELP_BOX[3]) // 2 + dy
                adb(serial, "shell", "input", "tap", str(x), str(y))
                time.sleep(1)
                post_image = screenshot(serial)
                if chat_page(post_image):
                    adb(serial, "shell", "input", "tap", "60", "190")
                    time.sleep(1)
                    if not inspect(screenshot(serial), help_template, nav_template,
                                   allow_wilderness=allow_wilderness)[0]:
                        raise RuntimeError("误入聊天后未能返回主城，已停止")
                    chat_misses += 1
                    print("互助点击误入聊天，已返回主城", flush=True)
                    if chat_misses >= 3:
                        print("连续三次误入聊天，等待 30 秒后再检查", flush=True)
                        time.sleep(30)
                        chat_misses = 0
                    continue
                if forced_offline(post_image, text_template, reconnect_template):
                    if not reconnect:
                        raise RuntimeError("检测到强制下线，自动重连已关闭")
                    paused_at = time.monotonic()
                    print(f"互助后出现强制下线，暂停其他操作 {RECONNECT_DELAY / 60:g} 分钟", flush=True)
                    continue
                post_city, post_match, _ = inspect(post_image, help_template, nav_template,
                                                  allow_wilderness=allow_wilderness)
                icon_gone = post_city and post_match[0] > HELP_LIMIT
                count_changed = (HELP_COUNT_BOX is not None and post_city and
                                 difference(image.crop(HELP_COUNT_BOX),
                                            post_image.crop(HELP_COUNT_BOX)) > 8)
                if not (icon_gone or count_changed):
                    post_image, confirmed = wait_help_update(
                        serial, image, post_image, help_template, nav_template, screenshot,
                        (text_template, reconnect_template), allow_wilderness=allow_wilderness)
                    if forced_offline(post_image, text_template, reconnect_template):
                        if not reconnect:
                            raise RuntimeError("检测到强制下线，自动重连已关闭")
                        paused_at = time.monotonic()
                        continue
                    if chat_page(post_image):
                        task_tap(serial, 60, 190)
                        time.sleep(1)
                        if not inspect(screenshot(serial), help_template, nav_template,
                                       allow_wilderness=allow_wilderness)[0]:
                            raise RuntimeError("误入聊天后未能返回主城")
                        continue
                    if not confirmed:
                        continue
                clicks += 1
                chat_misses = 0
                print(f"第 {clicks} 次互助已确认", flush=True)
                if clicks >= max_clicks:
                    print("达到本次点击上限，已停止", flush=True)
                    return
        time.sleep(interval)
    if announce_end:
        print(f"限时运行结束，共确认 {clicks} 次互助", flush=True)


def recover_task_city(serial, help_template, nav_template, text_template, reconnect_template):
    """Leave only pages with a confirmed, calibrated back target before retrying."""
    image = color_screenshot(serial)
    drawer = Image.open(ROOT / "assets/emulator-drawer-open.png").convert("RGB")
    island = Image.open(ROOT / "assets/emulator-island-title.png").convert("L")
    pages = {name: Image.open(ROOT / f"assets/emulator-{name}.png").convert("RGB")
             for name in ("tech-detail", "tech-title", "alliance-title", "recruit-title", "reward-title")}
    titles = [Image.open(ROOT / f"assets/emulator-training-{unit}-title.png").convert("L")
              for unit, _ in TRAIN_ROWS]
    dawn = Image.open(ROOT / "assets/emulator-dawn-title.png").convert("L")
    for _ in range(6):
        if inspect(image.convert("L"), help_template, nav_template)[0]:
            return (reset_drawer(serial, image, {"drawer-open": drawer})
                    if task_page(image, {"drawer-open": drawer}, "drawer-open") else image)
        if forced_offline(image.convert("L"), text_template, reconnect_template):
            raise RuntimeError("重试前检测到强制下线")
        if pet_page(image):
            target = (995, 505)
        elif pet_reward_page(image):
            target = (540, 2100)
        elif stamina_page(image):
            target = (920, 550)
        elif warehouse_reward_timer(image)[0]:
            target = (530, 1900)
        elif recruit_hero_exit_page(image):
            target = (540, 2100)
        elif task_page(image, pages, "tech-detail"):
            target = (960, 575)
        elif (target := treasure_back_target(image)) is not None:
            pass
        elif (island_page(image, island)
              or any(task_page(image, pages, name) for name in
                     ("tech-title", "alliance-title", "recruit-title", "reward-title"))
              or any(training_page(image, title) for title in titles)
              or difference(image.crop((405, 520, 660, 630)).convert("L"), dawn) <= 15):
            target = (60, 190)
        else:
            break
        task_tap(serial, *target)
        image = task_screen(serial)
    if not return_to_city(serial, help_template, nav_template, text_template, reconnect_template):
        raise RuntimeError("重试前仍处于强制下线状态")
    image = color_screenshot(serial)
    if task_page(image, {"drawer-open": drawer}, "drawer-open"):
        return reset_drawer(serial, image, {"drawer-open": drawer})
    return image


class RetriesExhausted(RuntimeError):
    pass


class ConsecutiveTaskFailures(RuntimeError):
    pass


def retry_once(label, operation, serial, text_template, reconnect_template, recover=None):
    for attempt in range(4):
        try:
            if attempt:
                if recover is not None:
                    print(f"{label} 第 {attempt} 次重试：正在恢复主城", flush=True)
                    recover()
                    print(f"{label} 第 {attempt} 次重试：主城恢复完成，开始重试", flush=True)
                else:
                    print(f"{label} 第 {attempt} 次重试：开始重试", flush=True)
            return operation()
        except Exception as error:
            # Confirmed forced-offline dialogs retain the existing reconnect policy.
            try:
                offline = serial is not None and forced_offline(
                    screenshot(serial), text_template, reconnect_template)
            except Exception:
                offline = False
            if offline:
                raise
            if label in (*RECRUIT_TYPES.values(), "宠物寻宝"):
                save_task_failure(serial, label, "first" if not attempt else f"retry-{attempt}")
            if attempt == 3:
                raise RetriesExhausted(f"{label} 重试失败：{error}") from error
            retry = attempt + 1
            action = (f"尝试恢复主城并重试（第 {retry} 次）" if recover is not None
                      else f"进行第 {retry} 次重试")
            print(f"{label} {'首次异常' if not attempt else f'第 {attempt} 次重试异常'}：{error}；"
                  f"10 秒后{action}", flush=True)
            time.sleep(10)


def save_task_failure(serial, label, attempt):
    try:
        image = color_screenshot(serial) if label == "宠物寻宝" else screenshot(serial)
        if image.size != EXPECTED_SIZE:
            return
        prefix = "treasure" if label == "宠物寻宝" else "recruit"
        path = ROOT / "artifacts" / f"{prefix}-failure-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
        image.save(path)
        print(f"{label} {attempt} 失败截图：{path}", flush=True)
        maybe_cleanup_diagnostic_screenshots(force=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"{label} 失败截图保存失败：{exc}", flush=True)


def run_schedule(serial, seconds, interval, forever, tasks,
                 help_template, nav_template, text_template, reconnect_template,
                 reconnect=True, help_enabled=True, timers=None):
    global TASK_HELP, HELP_RETRY_AT, TASK_DRAWER_SCROLL
    tasks = tasks if isinstance(tasks, dict) else dict(enumerate(tasks))
    wilderness_tasks = "intelligence" in tasks or "gather" in tasks
    timers = timers if timers is not None else {}
    timers.clear()
    disabled_tasks = set()
    consecutive_failures = 0
    priority_help = help_enabled
    HELP_RETRY_AT = 0.0
    TASK_DRAWER_SCROLL = None
    deadline = float("inf") if forever else time.monotonic() + seconds
    image = None
    while time.monotonic() < deadline:
        maybe_cleanup_diagnostic_screenshots()
        now = time.monotonic()
        next_tasks = min((timers.get(name, now) for name in tasks if name not in disabled_tasks),
                         default=float("inf"))
        try:
            if priority_help:
                drawer = Image.open(ROOT / "assets/emulator-drawer-open.png").convert("RGB")
                TASK_HELP = (help_template, nav_template, drawer)
                try:
                    retry_once("优先互助检查",
                               lambda: check_priority_help(serial, help_template, nav_template,
                                                           text_template, reconnect_template),
                               serial, text_template, reconnect_template,
                               lambda: recover_task_city(serial, help_template, nav_template,
                                                         text_template, reconnect_template))
                    priority_help = False
                finally:
                    TASK_HELP = None
            if now >= next_tasks:
                drawer = Image.open(ROOT / "assets/emulator-drawer-open.png").convert("RGB")
                TASK_HELP = (help_template, nav_template, drawer) if help_enabled else None
                try:
                    for name, task in tasks.items():
                        if name in disabled_tasks:
                            continue
                        if time.monotonic() >= timers.get(name, 0):
                            if (wilderness_tasks and name not in ("intelligence", "gather")
                                    and isinstance(image, Image.Image) and town_button(image)):
                                image = retry_once(
                                    "城内任务前返回主城",
                                    lambda: recover_task_city(serial, help_template, nav_template,
                                                              text_template, reconnect_template),
                                    serial, text_template, reconnect_template)
                            if name not in PET_SKILLS and isinstance(image, Image.Image) and pet_page(image):
                                image = retry_once(
                                    "关闭宠物技能弹窗",
                                    lambda: close_pet_page(serial, color_screenshot(serial), help_template, nav_template),
                                    serial, text_template, reconnect_template,
                                    lambda: recover_task_city(serial, help_template, nav_template,
                                                              text_template, reconnect_template))
                            timers[name] = time.monotonic() + (60 if name in
                                ("train", *RECRUIT_TYPES, *PET_SKILLS, "warehouse", "stamina", "dawn") else 600)
                            label = {"explore": "探险经验", "train": "自动练兵", "donate": "联盟捐献",
                                     "treasure": "宠物寻宝",
                                     **RECRUIT_TYPES, "warehouse": "仓库补给", "stamina": "体力罐头", "tree": "生命结晶收集",
                                     "dawn": "晨曦回礼", "intelligence": "灯塔情报", "gather": "资源采集", **{key: value[0] for key, value in PET_SKILLS.items()}}.get(name, str(name))
                            print(f"开始检查：{label}", flush=True)
                            try:
                                image = retry_once(label, task, serial, text_template, reconnect_template,
                                                   lambda: recover_task_city(serial, help_template, nav_template,
                                                                             text_template, reconnect_template))
                            except RetriesExhausted:
                                disabled_tasks.add(name)
                                consecutive_failures += 1
                                print(f"⚠️ {label} 已连续执行失败3次，后续不再执行这个任务", flush=True)
                                if consecutive_failures >= 3:
                                    message = "⚠️ 当前已出现三个任务连续执行异常因此自动中断脚本"
                                    print(message, flush=True)
                                    raise ConsecutiveTaskFailures(message) from None
                                image = None
                                image = retry_once(
                                    f"{label} 停用后恢复主城",
                                    lambda: recover_task_city(serial, help_template, nav_template,
                                                              text_template, reconnect_template),
                                    serial, text_template, reconnect_template)
                            else:
                                consecutive_failures = 0
                                if name == "intelligence" and "gather" in tasks:
                                    timers["gather"] = 0
                    def finish_drawer():
                        nonlocal image
                        if isinstance(image, Image.Image) and pet_page(image):
                            # A later failed skill may have recovered to the city;
                            # never close a modal using a previous skill's frame.
                            image = close_pet_page(serial, color_screenshot(serial), help_template, nav_template)
                        if isinstance(image, Image.Image) and task_page(image, {"drawer-open": drawer}, "drawer-open"):
                            task_tap(serial, 695, 1100)
                            image = task_screen(serial)
                            if not inspect(image.convert("L"), help_template, nav_template)[0]:
                                raise RuntimeError("任务检查结束后未能确认主城")

                    def recover_drawer():
                        nonlocal image
                        image = recover_task_city(serial, help_template, nav_template,
                                                  text_template, reconnect_template)

                    retry_once("结束本轮任务", finish_drawer, serial, text_template,
                               reconnect_template, recover_drawer)
                finally:
                    TASK_HELP = None
                continue
            wait = min(deadline, next_tasks, now + 600) - now
            if wait > 0:
                monitor_end = time.monotonic() + wait
                recovered = retry_once(
                    "联盟互助监控",
                    lambda: run(serial, max(1, monitor_end - time.monotonic()), interval, False,
                                float("inf"), help_template, nav_template, text_template,
                                reconnect_template, reconnect=reconnect,
                                help_enabled=help_enabled, return_on_reconnect=True,
                                allow_wilderness=wilderness_tasks),
                    serial, text_template, reconnect_template,
                    lambda: recover_task_city(serial, help_template, nav_template,
                                              text_template, reconnect_template))
                if recovered:
                    image = None
                    timers.clear()
                    priority_help = help_enabled
                    HELP_RETRY_AT = 0.0
                    deadline = max(deadline, time.monotonic() + 1)
        except ConsecutiveTaskFailures:
            raise
        except Exception as task_error:
            # 任务可能在两次正常检查之间被强制下线；只对已确认的弹窗恢复。
            try:
                offline = forced_offline(screenshot(serial), text_template, reconnect_template)
            except Exception:
                raise task_error from None
            if not offline:
                raise
            if not reconnect:
                raise RuntimeError("检测到强制下线，自动重连已关闭") from task_error
            print("任务异常后确认强制下线弹窗，等待重连并重试任务", flush=True)
            recovery_started = time.monotonic()
            run(serial, 1, interval, False, float("inf"), help_template,
                nav_template, text_template, reconnect_template, False, reconnect=reconnect,
                help_enabled=help_enabled, return_on_reconnect=True)
            if not inspect(screenshot(serial), help_template, nav_template)[0]:
                raise RuntimeError("重连后未确认主城，未重试日常任务")
            if not forever:
                deadline += time.monotonic() - recovery_started
            timers.clear()
            image = None
            priority_help = help_enabled
            HELP_RETRY_AT = 0.0


def main():
    global CHAT_TEMPLATE, RECONNECT_DELAY, RECONNECT_PROMO_CLOSE, RECONNECT_WELCOME_CLOSE
    global ACTIVITY_CLOSE, TOWN_TEMPLATE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="用点击前后截图验证识别")
    parser.add_argument("--device", help="ADB 序列号；只有一台设备时可省略")
    parser.add_argument("--emulator", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--city-tasks", action="store_true", help="另执行模拟器联盟科技捐献和免费招募")
    parser.add_argument("--train", action="store_true", help="另领取完成的士兵并启动空闲兵营的普通资源训练")
    parser.add_argument("--upgrade", action="store_true", help="自动练兵时优先晋升低级兵种至当前最高等级")
    parser.add_argument("--daily-tasks", action="store_true", help="启动时全量检查；训练、招募、仓库、体力罐头和晨曦按倒计时复查，捐献、宠物寻宝和生命之树每10分钟检查")
    for name, description in (("help", "联盟互助"), ("explore", "探险经验"),
                              ("train", "练兵"),
                              ("shield", "盾兵训练"),
                              ("spear", "矛兵训练"), ("archer", "射手训练"),
                              ("donate", "联盟捐献"), ("recruit", "免费招募"),
                              ("treasure", "宠物寻宝"),
                              ("warehouse", "仓库补给"), ("tree", "生命结晶"),
                              ("dawn", "晨曦回礼"), ("pet", "宠物技能"), ("reconnect", "强制下线自动重连")):
        parser.add_argument(f"--no-{name}", action="store_true", help=f"关闭{description}")
    parser.add_argument("--tree-threshold", type=int, default=1000,
                        help="生命结晶数量达到此值时领取，必须大于 0；默认 1000")
    parser.add_argument("--intelligence", action="store_true", help="日常任务后执行灯塔情报")
    parser.add_argument("--gather", action="store_true", help="灯塔情报后执行资源采集；关闭情报时直接采集")
    parser.add_argument("--resources", nargs="+", choices=tuple(GATHER_RESOURCES),
                        default=tuple(GATHER_RESOURCES), help="采集资源，默认 meat wood coal iron")
    parser.add_argument("--bounty", action="store_true", help="普通情报结束后执行大师悬赏；战败后本次运行停用")
    parser.add_argument("--stamina-threshold", type=int, default=0, help="体力大于此值时执行情报；0 时用到无法执行")
    parser.add_argument("--forever", action="store_true", help="长期运行，联盟互助不设点击上限")
    parser.add_argument("--duration", type=int, default=120, help="本次运行秒数，默认 120")
    parser.add_argument("--interval", type=float, default=2, help="每轮检查间隔秒数")
    parser.add_argument("--reconnect-minutes", type=float, default=10,
                        help="强制下线后等待多少分钟再点击重新连接，默认 10；可设为 1")
    parser.add_argument("--max-clicks", type=int, default=10, help="本次最多点击次数")
    parser.add_argument("--dry-run", action="store_true", help="仅识别，不点击")
    args = parser.parse_args()
    if args.duration <= 0 or args.interval <= 0 or args.max_clicks <= 0 or args.reconnect_minutes <= 0:
        parser.error("时长、间隔、点击上限和重连分钟数都必须大于零")
    if args.tree_threshold <= 0:
        parser.error("生命结晶阈值必须大于 0")
    if args.stamina_threshold < 0:
        parser.error("体力触发阈值必须大于或等于 0")
    RECONNECT_DELAY = args.reconnect_minutes * 60
    if args.forever and args.dry_run:
        parser.error("--forever 不能与 --dry-run 同时使用")
    CHAT_TEMPLATE = Image.open(ROOT / "assets/emulator-chat-title.png").convert("L")
    RECONNECT_PROMO_CLOSE = Image.open(ROOT / "assets/emulator-reconnect-promo-close.png").convert("L")
    RECONNECT_WELCOME_CLOSE = Image.open(ROOT / "assets/emulator-reconnect-welcome-close.png").convert("L")
    ACTIVITY_CLOSE = Image.open(ROOT / "assets/emulator-activity-close.png").convert("L")
    TOWN_TEMPLATE = Image.open(ROOT / "assets/emulator-town-button.png").convert("L")
    help_template = Image.open(ROOT / "assets/emulator-help-icon.png").convert("L")
    nav_template = Image.open(ROOT / "assets/emulator-city-nav.png").convert("L")
    text_template = Image.open(ROOT / "assets/emulator-forced-text.png").convert("L")
    reconnect_template = Image.open(ROOT / "assets/emulator-reconnect.png").convert("L")
    task_templates = ({name: Image.open(ROOT / f"assets/emulator-{name}.png").convert("RGB")
                       for name in (*TASK_BOXES, "donate-label", "free-label", "thumb")}
                      if args.city_tasks or args.daily_tasks else None)
    train_titles = {unit: Image.open(ROOT / f"assets/emulator-training-{unit}-title.png").convert("L")
                    for unit, _ in TRAIN_ROWS} if args.train or args.daily_tasks else None
    try:
        if args.self_test:
            self_test(help_template, nav_template, text_template, reconnect_template)
        else:
            serial = args.device or retry_once("查找设备", connected_device, None,
                                               text_template, reconnect_template)
            if not emulator_device(serial):
                raise RuntimeError("所选 ADB 设备不是模拟器")
            if args.dry_run:
                retry_once("运行监控", lambda: run(
                    serial, args.duration, args.interval, True, args.max_clicks,
                    help_template, nav_template, text_template, reconnect_template,
                    reconnect=not args.no_reconnect, help_enabled=not args.no_help),
                    serial, text_template, reconnect_template)
            else:
                if not retry_once("启动时主城检查", lambda: return_to_city(
                    serial, help_template, nav_template, text_template, reconnect_template),
                    serial, text_template, reconnect_template):
                    print("启动时检测到强制下线，等待重连", flush=True)
                    run(serial, 1, args.interval, False, float("inf"),
                        help_template, nav_template, text_template, reconnect_template,
                        announce_end=False, reconnect=not args.no_reconnect,
                        help_enabled=False, return_on_reconnect=True)
                tasks = {}
                timers = {}
                bounty_state = {}
                drawer_template = Image.open(ROOT / "assets/emulator-drawer-open.png").convert("RGB")
                if args.daily_tasks and not args.no_explore:
                    explore_templates = {name: Image.open(ROOT / f"assets/emulator-explore-{name}.png").convert("L")
                                         for name in EXPLORE_BOXES}
                    tasks["explore"] = lambda: run_explore(serial, help_template, nav_template, explore_templates)
                units = {unit for unit, _ in TRAIN_ROWS if not getattr(args, f"no_{unit}")}
                if (args.daily_tasks or args.train) and not args.no_train and units:
                    tasks["train"] = lambda: run_training(serial, help_template, nav_template, drawer_template,
                                                           train_titles, units=units, timers=timers, keep_open=True,
                                                           upgrade=args.upgrade)
                if args.daily_tasks or args.city_tasks:
                    if not args.no_donate:
                        tasks["donate"] = lambda: run_city_tasks(serial, help_template, nav_template,
                                                                  task_templates, recruit=False, keep_open=True)
                    if not args.no_recruit:
                        for kind in RECRUIT_TYPES:
                            tasks[kind] = lambda kind=kind: run_city_tasks(
                                serial, help_template, nav_template, task_templates, donate=False,
                                timers=timers, recruit_kind=kind, keep_open=True)
                if args.daily_tasks:
                    warehouse = Image.open(ROOT / "assets/emulator-warehouse-title.png").convert("RGB")
                    tree = Image.open(ROOT / "assets/emulator-tree-collect-label.png").convert("RGB")
                    island = Image.open(ROOT / "assets/emulator-island-title.png").convert("L")
                    for name in ("warehouse", "tree", "dawn"):
                        if not getattr(args, f"no_{name}"):
                            tasks[name] = lambda name=name: run_rewards(
                                serial, help_template, nav_template, drawer_template, warehouse, tree, island,
                                warehouse=name == "warehouse", tree=name == "tree", dawn=name == "dawn",
                                tree_threshold=args.tree_threshold, timers=timers, keep_open=True)
                        if name == "warehouse":
                            if not args.no_warehouse:
                                tasks["stamina"] = lambda: run_stamina(
                                    serial, help_template, nav_template, drawer_template, timers)
                            if not args.no_treasure:
                                tasks["treasure"] = lambda: run_treasure(
                                    serial, help_template, nav_template, drawer_template)
                if args.daily_tasks and not args.no_pet:
                    for name in PET_SKILLS:
                        tasks[name] = lambda name=name: run_pet_skill(
                            serial, help_template, nav_template, drawer_template, name, timers, keep_open=True)
                if args.intelligence:
                    tasks["intelligence"] = lambda: run_intelligence(
                        serial, help_template, nav_template, text_template, reconnect_template,
                        args.stamina_threshold, timers=timers,
                        bounty=args.bounty, bounty_state=bounty_state)
                if args.gather:
                    tasks["gather"] = lambda: run_gather(
                        serial, help_template, nav_template, text_template, reconnect_template,
                        resources=args.resources)
                if tasks or args.forever:
                    run_schedule(serial, args.duration, args.interval, args.forever, tasks,
                                 help_template, nav_template, text_template, reconnect_template,
                                 reconnect=not args.no_reconnect,
                                 help_enabled=not args.no_help, timers=timers)
                else:
                    retry_once("运行监控", lambda: run(
                        serial, args.duration, args.interval, False, args.max_clicks,
                        help_template, nav_template, text_template, reconnect_template,
                        reconnect=not args.no_reconnect, help_enabled=not args.no_help),
                        serial, text_template, reconnect_template)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        parser.exit(1, f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 已停止：{exc}\n")
    except KeyboardInterrupt:
        print("已由键盘停止", flush=True)


if __name__ == "__main__":
    configure_stdio()
    main()
