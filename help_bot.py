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


ROOT = Path(__file__).resolve().parent
ADB = ROOT / ".tools/platform-tools/adb"
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
RECRUIT_TYPES = {"recruit_advanced": "高级招募", "recruit_epic": "史诗招募"}
PET_SKILLS = {
    "pet_companion": ("心灵伴侣", (425, 680)),
    "pet_transport": ("重物搬运", (645, 680)),
    "pet_senses": ("敏锐感官", (865, 680)),
    "pet_gift": ("大地馈赠", (425, 900)),
}
PET_TITLE_BOX = (440, 480, 640, 535)
PET_BUTTON_BOX = (960, 1680, 1030, 1750)
TRAIN_TITLE_BOX = (400, 140, 680, 230)
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


def wait_help_update(serial, before, after, help_template, nav_template, capture, offline_templates=None):
    """Allow animation/network delay, then defer an unchanged safe city for 30 seconds."""
    global HELP_RETRY_AT
    for attempt in range(5):
        if offline_templates is not None and forced_offline(after, *offline_templates):
            return after, False
        city, match, _ = inspect(after.convert("L"), help_template, nav_template)
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
    ).stdout


def connected_device():
    result = subprocess.run(
        [str(ADB), "devices"], check=True, capture_output=True, text=True, timeout=15
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
        if score >= 0.95:
            return x + ACTIVITY_CLOSE.width // 2, y + ACTIVITY_CLOSE.height // 2
    return None


def inspect(image, help_template, nav_template):
    if image.size != EXPECTED_SIZE:
        raise RuntimeError(f"屏幕尺寸 {image.size} 与已校准尺寸 {EXPECTED_SIZE} 不同")
    nav_difference = difference(image.crop(NAV_BOX), nav_template)
    if nav_difference > NAV_LIMIT or town_button(image):
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
    city, match, _ = inspect(image.convert("L"), help_template, nav_template)
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
        if not inspect(post.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("误入聊天后未能返回主城，已停止")
        print("互助点击误入聊天，已返回主城", flush=True)
    post_city, post_match, _ = inspect(post.convert("L"), help_template, nav_template)
    icon_gone = post_city and post_match[0] > HELP_LIMIT
    count_changed = (HELP_COUNT_BOX is not None and post_city and
                     difference(image.crop(HELP_COUNT_BOX), post.crop(HELP_COUNT_BOX)) > 8)
    confirmed = icon_gone or count_changed
    if not was_chat and not confirmed:
        post, confirmed = wait_help_update(serial, image, post, help_template, nav_template,
                                           color_screenshot)
        if chat_page(post):
            task_tap(serial, 60, 190)
            time.sleep(1)
            post = color_screenshot(serial)
            was_chat = True
            if not inspect(post.convert("L"), help_template, nav_template)[0]:
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


def return_to_city(serial, help_template, nav_template, text_template, reconnect_template):
    confirmations = 0
    island_template = Image.open(ROOT / "assets/emulator-island-title.png").convert("L")
    for _ in range(30):
        image = screenshot(serial)
        if forced_offline(image, text_template, reconnect_template):
            return False
        if inspect(image, help_template, nav_template)[0]:
            confirmations += 1
            if confirmations >= 3:
                return True
        else:
            confirmations = 0
            target = reconnect_popup_target(image)
            action = "关闭活动弹窗"
            if target is None and pet_page(image):
                target, action = (995, 505), "关闭宠物技能弹窗"
            if target is None and chat_page(image):
                target, action = (60, 190), "退出聊天"
            if target is None and island_template is not None and island_page(image, island_template):
                target, action = (60, 190), "退出海岛"
            if target is None and town_button(image):
                target, action = (960, 2230), "从野外返回城镇"
            if target is not None:
                task_tap(serial, *target)
                print(f"已{action}", flush=True)
        time.sleep(2)
    raise RuntimeError("60 秒仍未确认主城，已停止")


def task_tap(serial, x, y):
    if not game_foreground(serial):
        raise RuntimeError("当前前台不是目标游戏，已停止")
    adb(serial, "shell", "input", "tap", str(x), str(y))


def resource_donation_available(image):
    red, _, blue = ImageStat.Stat(image.crop((565, 1780, 610, 1830))).mean
    return blue - red > 60


def explore_red_dot(image):
    dot = image.crop((130, 2180, 160, 2210)).convert("RGB")
    return sum(r > 180 and r > g * 1.45 and r > b * 1.45
               for r, g, b in dot.getdata()) > 100


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


def task_drawer(serial, image, templates):
    global TASK_DRAWER_SCROLL
    if not task_page(image, templates, "drawer-open"):
        task_tap(serial, 20, 1100)
        TASK_DRAWER_SCROLL = 0
        image = task_screen(serial)
    if not task_page(image, templates, "drawer-open"):
        raise RuntimeError("未能确认主城抽屉已打开")
    if TASK_DRAWER_SCROLL is None:
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
    TASK_DRAWER_SCROLL = max(0, TASK_DRAWER_SCROLL + (-1 if reverse else 1))
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
    image = reset_drawer(serial, image, templates)
    for attempt in range(DRAWER_SWIPES + 1):
        score, x, y = find_green(image, templates[name], range(240, 321, 4), range(680, 1431, 4))
        if score >= 0.90:
            return image, x, y
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
            for _ in range(5):
                if task_page(image, templates, "reward-title"):
                    break
                image = task_screen(serial)
            if not task_page(image, templates, "reward-title"):
                raise RuntimeError("未能确认免费招募奖励页面")
            print(f"已完成一次免费{RECRUIT_TYPES[recruit_kind]}", flush=True)
            task_tap(serial, 60, 190)
            image = task_screen(serial)
            if not task_page(image, templates, "recruit-title"):
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


def run_training(serial, help_template, nav_template, drawer_template, titles,
                 initial_image=None, keep_open=False, units=None, timers=None):
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
        status = training_status(image, y)
        if status == "upgrading":
            print(f"{unit} 建筑升级中，跳过倒计时记录和自动训练", flush=True)
            continue
        if status == "busy":
            print(f"{unit} 已在训练中，跳过", flush=True)
            if timers is not None:
                seconds = countdown(image, (125, y + 5, 550, y + 42))
                record_timer(timers, "train", seconds, {"shield": "盾兵", "spear": "矛兵", "archer": "射手"}[unit])
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
                raise RuntimeError(f"{unit} 已完成队列领取后未变为空闲，已停止")
            task_tap(serial, 608, y)
            image = task_screen(serial)
            city(image)
            print(f"{unit} 已领取完成的士兵", flush=True)
        task_tap(serial, 725, 1590)
        image = task_screen(serial)
        if difference(image.convert("L").crop(TRAIN_TITLE_BOX), titles[unit]) > 3:
            raise RuntimeError(f"未确认 {unit} 训练配置页，已停止")
        r, g, b = image.getpixel((400, 1900))
        if not (g > 180 and g > r + 100 and g > b + 80):
            raise RuntimeError(f"{unit} 训练配置不在可启动状态，已停止")
        task_tap(serial, 795, 2100)  # 右侧蓝色普通资源训练按钮；左侧黄色按钮消耗钻石
        image = task_screen(serial)
        if (difference(image.convert("L").crop(TRAIN_TITLE_BOX), titles[unit]) > 3
                or image.getpixel((400, 1900))[1] > 180):
            raise RuntimeError(f"{unit} 训练开始后未确认状态变化，已停止")
        print(f"{unit} 已启动普通资源训练", flush=True)
        task_tap(serial, 60, 190)
        image = task_screen(serial)
        city(image)
        if index < len(TRAIN_ROWS) - 1 or keep_open or timers is not None:
            image = drawer(image)
        if timers is not None:
            seconds = countdown(image, (125, y + 5, 550, y + 42))
            record_timer(timers, "train", seconds, {"shield": "盾兵", "spear": "矛兵", "archer": "射手"}[unit])
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


def recognize_text(image):
    import objc
    from Foundation import NSData

    data = io.BytesIO()
    image.convert("RGB").resize((image.width * 2, image.height * 2)).save(data, format="PNG")
    raw = data.getvalue()
    objc.loadBundle("Vision", globals(), bundle_path="/System/Library/Frameworks/Vision.framework")
    request = objc.lookUpClass("VNRecognizeTextRequest").alloc().init()
    request.setRecognitionLevel_(0)
    request.setRecognitionLanguages_(["zh-Hans", "en-US"])
    request.setUsesLanguageCorrection_(False)
    handler = objc.lookUpClass("VNImageRequestHandler").alloc().initWithData_options_(
        NSData.dataWithBytes_length_(raw, len(raw)), {})
    if not handler.performRequests_error_([request], None):
        return []
    results = []
    for result in request.results() or []:
        box = result.boundingBox()
        results.append((str(result.topCandidates_(1)[0].string()),
                        (box.origin.x * image.width,
                         (1 - box.origin.y - box.size.height) * image.height,
                         box.size.width * image.width, box.size.height * image.height)))
    return results


def tree_amount(image, x, y):
    for text, _ in recognize_text(green_mask(image).crop((x + 85, y - 5, x + 235, y + 40))):
        amount = parse_tree_amount(text)
        if amount is not None:
            return amount
    return None


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
    for hint in hints:
        if hint.startswith("冷却中"):
            return "cooldown", parse_countdown(re.sub(r"^冷却中[:：]?", "", hint))
    # Only an explicitly labelled, coloured use button is actionable.
    pixels = list(image.crop((350, 1870, 420, 1940)).convert("RGB").getdata())
    enabled = sum((b > r + 40 or g > r + 40) and max(g, b) > 140 for r, g, b in pixels) > len(pixels) / 2
    if "使用" in hints and enabled:
        return "ready", None
    return "unknown", None


def run_pet_skill(serial, help_template, nav_template, drawer_template, name, timers):
    label, target = PET_SKILLS[name]
    image = color_screenshot(serial)
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
    if not pet_page(image):
        raise RuntimeError("未打开宠物技能弹窗")
    task_tap(serial, *target)
    image = task_screen(serial)
    state, seconds = pet_skill_state(image, label)
    if state == "ready":
        task_tap(serial, 540, 1910)
        for _ in range(5):
            image = task_screen(serial)
            if pet_page(image):
                state, seconds = pet_skill_state(image, label)
                if state == "cooldown":
                    break
        else:
            raise RuntimeError(f"{label} 使用后未确认进入冷却")
        print(f"已使用宠物技能：{label}", flush=True)
    elif state == "unknown":
        print(f"{label} 使用状态未识别，本次不点击", flush=True)
    record_timer(timers, name, seconds, label)
    task_tap(serial, 995, 505)
    image = task_screen(serial)
    if not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("宠物技能检查后未返回主城")
    return image


def recruit_row(serial, image, templates, kind):
    image = task_drawer(serial, image, templates)
    if TASK_DRAWER_SCROLL > 1:
        image = reset_drawer(serial, image, templates)
    for attempt in range(DRAWER_SWIPES + 1):
        for text, (_, y, _, height) in recognize_text(image.crop((240, 710, 530, 1430))):
            if RECRUIT_TYPES[kind] in text.replace(" ", ""):
                row_bottom = round(710 + y + height)
                if row_bottom + 55 <= 1430:
                    return image, row_bottom
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
        target = crystal_target(image, template)
        if target is None:
            print(f"海岛结晶领取完成，共 {collected} 处", flush=True)
            return image
        task_tap(serial, *target)
        for attempt in range(8):
            image = task_screen(serial)
            if not island_page(image, island_template):
                continue
            if crystal_target(image, template, near=target) is None:
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
    """Keep recent screenshots generated by failed island returns and recruitment."""
    directory = ROOT / "artifacts"
    cutoff = time.time() - 30 * 86400
    removed = 0
    for prefix in ("island-return", "recruit-failure"):
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


def stamina_target(image, offset):
    dx, dy = offset
    template = Image.open(ROOT / "assets/emulator-can-icon.png").convert("L")
    score, x, y = find_white(image, template, range(430 + dx, 526 + dx, 5), range(930 + dy, 1066 + dy, 5))
    if (score >= .85 and difference(image.crop((x, y, x + template.width, y + template.height)).convert("L"),
                                   template) < 35):
        return x + template.width // 2, y + template.height // 2
    return None


def run_stamina(serial, help_template, nav_template, drawer_template, timers=None, initial_image=None):
    def city(image):
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("体力罐头流程未确认主城，已停止")

    image = initial_image if initial_image is not None else color_screenshot(serial)
    city(image)
    templates = {"drawer-open": drawer_template}
    if task_page(image, templates, "drawer-open"):
        task_tap(serial, 695, 1100)
        image = task_screen(serial)
        city(image)
    offset = warehouse_position(image)
    if offset is None:
        # ponytail: 路径按当前主城布局校准；建筑移动后需重新校准这两次滑动。
        image = reset_drawer(serial, image, templates)
        task_tap(serial, 608, TRAIN_ROWS[0][1])
        image = task_screen(serial)
        city(image)
        for start_x, start_y, end_x, end_y in ((200, 1400, 850, 1400), (250, 800, 850, 1450)):
            if not game_foreground(serial):
                raise RuntimeError("当前前台不是目标游戏，已停止")
            adb(serial, "shell", "input", "swipe", *map(str, (start_x, start_y, end_x, end_y)), "1000")
            image = task_screen(serial)
            city(image)
        offset = warehouse_position(image)
        if offset is None:
            raise RuntimeError("滑动主城后未找到仓库，未点击体力罐头")
    # 等待补给奖励退出、地图移动和图标切换动画。
    for attempt in range(5):
        city(image)
        target = stamina_target(image, offset)
        if target is not None:
            task_tap(serial, *target)
            image = task_screen(serial)
            modal = Image.open(ROOT / "assets/emulator-can-modal-title.png").convert("L")
            button = Image.open(ROOT / "assets/emulator-can-button.png").convert("L")
            if (difference(image.crop((390, 1390, 690, 1480)).convert("L"), modal) > 15
                    or difference(image.crop((350, 1620, 735, 1760)).convert("L"), button) > 20):
                raise RuntimeError("未确认罐头领取页面，已停止")
            task_tap(serial, 530, 1700)
            for _ in range(5):
                image = task_screen(serial)
                if (inspect(image.convert("L"), help_template, nav_template)[0]
                        and stamina_target(image, offset) is None):
                    break
            else:
                raise RuntimeError("罐头领取后未确认返回主城，已停止")
            print("已领取体力罐头", flush=True)
            break
        if attempt < 4:
            image = task_screen(serial)
    else:
        print("体力罐头尚不可领取", flush=True)
    record_timer(timers, "stamina", warehouse_countdown(image, offset=offset), label="体力罐头复查（仓库计时）")
    return image


def run_rewards(serial, help_template, nav_template, drawer_template,
                warehouse_template, tree_template, island_template, initial_image=None,
                warehouse=True, tree=True, dawn=True, tree_threshold=1000, timers=None, keep_open=False):
    def city(image):
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("补给流程未确认主城，已停止")

    def bottom_drawer(image):
        city(image)
        image = task_drawer(serial, image, {"drawer-open": drawer_template})
        while TASK_DRAWER_SCROLL < DRAWER_SWIPES:
            image = swipe_drawer(serial, drawer_template)
        return image

    image = initial_image if initial_image is not None else color_screenshot(serial)
    warehouse_timer_recorded = False
    if warehouse and timers is not None:
        seconds = warehouse_countdown(image)
        if seconds is not None:
            record_timer(timers, "warehouse", seconds)
            warehouse_timer_recorded = True
    image = bottom_drawer(image)
    score, _, warehouse_y = (find_white(image, warehouse_template,
                                       range(240, 351, 5), range(650, 1420, 5))
                             if warehouse else (0, 0, 0))
    if warehouse and score >= 0.85 and training_status(image, warehouse_y + 30) == "completed":
        task_tap(serial, 608, warehouse_y + 30)
        image = task_screen(serial)
        city(image)
        task_tap(serial, 530, 1050)
        image = task_screen(serial)
        if inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("仓库补给未出现奖励页面，已停止")
        task_tap(serial, 530, 1900)
        image = task_screen(serial)
        city(image)
        print("已领取仓库补给", flush=True)
        if timers is not None:
            timers["stamina"] = 0  # 补给领取后立即安排罐头检查，独立记录下一次时间。
            record_timer(timers, "warehouse", warehouse_countdown(image, known_warehouse=True))
            warehouse_timer_recorded = True
        image = bottom_drawer(image)
    elif warehouse:
        print("仓库补给尚不可领取", flush=True)

    if warehouse and timers is not None and not warehouse_timer_recorded:
        score, _, row_y = find_white(image, warehouse_template, range(240, 351, 5), range(650, 1420, 5))
        record_timer(timers, "warehouse", countdown(image, (125, row_y + 30, 550, row_y + 85))
                     if score >= .85 else None)

    score, x, y = (find_green(image, tree_template, range(180, 241, 4), range(680, 1401, 4))
                   if tree else (0, 0, 0))
    amount = tree_amount(image, x, y) if tree and score >= 0.85 else None
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
        image = bottom_drawer(image)
    elif tree:
        status = f"不足 {tree_threshold}" if amount is not None else "未识别"
        print(f"生命之树可收集数{status}，跳过", flush=True)

    if dawn:
        dawn_template = Image.open(ROOT / "assets/emulator-dawn-row-title.png").convert("RGB")
        score, _, dawn_y = find_white(image, dawn_template, range(240, 351, 5), range(650, 1420, 5))
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
    elif dawn:
        print("晨曦回礼尚不可领取", flush=True)
    if dawn and timers is not None:
        image = bottom_drawer(image)
        score, _, row_y = find_white(image, dawn_template, range(240, 351, 5), range(650, 1420, 5))
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
        reconnect=True, help_enabled=True, return_on_reconnect=False):
    print(f"设备 {serial}；运行 {seconds} 秒；{'只观察' if dry_run else '允许点击'}；Ctrl-C 可停止", flush=True)
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
                deadline += time.monotonic() - paused_at
                paused_at = None
                not_city = 0
                print("强制下线弹窗已消失，取消重连点击", flush=True)
                if return_on_reconnect and return_to_city(serial, help_template, nav_template,
                                                         text_template, reconnect_template):
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
            deadline += time.monotonic() - paused_at
            paused_at = None
            not_city = 0
            time.sleep(2)
            if not return_to_city(serial, help_template, nav_template,
                                  text_template, reconnect_template):
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
        city, match, nav = inspect(image, help_template, nav_template)
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
                    if not inspect(screenshot(serial), help_template, nav_template)[0]:
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
                post_city, post_match, _ = inspect(post_image, help_template, nav_template)
                icon_gone = post_city and post_match[0] > HELP_LIMIT
                count_changed = (HELP_COUNT_BOX is not None and post_city and
                                 difference(image.crop(HELP_COUNT_BOX),
                                            post_image.crop(HELP_COUNT_BOX)) > 8)
                if not (icon_gone or count_changed):
                    post_image, confirmed = wait_help_update(
                        serial, image, post_image, help_template, nav_template, screenshot,
                        (text_template, reconnect_template))
                    if forced_offline(post_image, text_template, reconnect_template):
                        if not reconnect:
                            raise RuntimeError("检测到强制下线，自动重连已关闭")
                        paused_at = time.monotonic()
                        continue
                    if chat_page(post_image):
                        task_tap(serial, 60, 190)
                        time.sleep(1)
                        if not inspect(screenshot(serial), help_template, nav_template)[0]:
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
        elif task_page(image, pages, "tech-detail"):
            target = (960, 575)
        elif (island_page(image, island)
              or any(task_page(image, pages, name) for name in
                     ("tech-title", "alliance-title", "recruit-title", "reward-title"))
              or any(difference(image.crop(TRAIN_TITLE_BOX).convert("L"), title) <= 3
                     for title in titles)
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
            if attempt and recover is not None:
                recover()
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
            if label in RECRUIT_TYPES.values():
                save_recruit_failure(serial, label, "first" if not attempt else f"retry-{attempt}")
            if attempt == 3:
                raise RetriesExhausted(f"{label} 重试失败：{error}") from error
            print(f"{label} {'首次异常' if not attempt else f'第 {attempt} 次重试异常'}：{error}；"
                  f"10 秒后第 {attempt + 1} 次重试", flush=True)
            time.sleep(10)


def save_recruit_failure(serial, label, attempt):
    try:
        image = screenshot(serial)
        if image.size != EXPECTED_SIZE:
            return
        path = ROOT / "artifacts" / f"recruit-failure-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
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
    timers = timers if timers is not None else {}
    timers.clear()
    disabled_tasks = set()
    consecutive_failures = 0
    priority_help = help_enabled
    HELP_RETRY_AT = 0.0
    TASK_DRAWER_SCROLL = None
    deadline = float("inf") if forever else time.monotonic() + seconds
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
                    image = None
                    for name, task in tasks.items():
                        if name in disabled_tasks:
                            continue
                        if time.monotonic() >= timers.get(name, 0):
                            timers[name] = time.monotonic() + (60 if name in
                                ("train", *RECRUIT_TYPES, *PET_SKILLS, "warehouse", "stamina", "dawn") else 600)
                            label = {"explore": "探险经验", "train": "自动练兵", "donate": "联盟捐献",
                                     **RECRUIT_TYPES, "warehouse": "仓库补给", "stamina": "体力罐头", "tree": "生命结晶收集",
                                     "dawn": "晨曦回礼", **{key: value[0] for key, value in PET_SKILLS.items()}}.get(name, str(name))
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
                            else:
                                consecutive_failures = 0
                    def finish_drawer():
                        nonlocal image
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
                                help_enabled=help_enabled, return_on_reconnect=True),
                    serial, text_template, reconnect_template,
                    lambda: recover_task_city(serial, help_template, nav_template,
                                              text_template, reconnect_template))
                if recovered:
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
                help_enabled=help_enabled)
            if not inspect(screenshot(serial), help_template, nav_template)[0]:
                raise RuntimeError("重连后未确认主城，未重试日常任务")
            if not forever:
                deadline += time.monotonic() - recovery_started
            timers.clear()
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
    parser.add_argument("--daily-tasks", action="store_true", help="启动时全量检查；训练、招募、仓库、体力罐头和晨曦按倒计时复查，捐献和生命之树每10分钟检查")
    for name, description in (("help", "联盟互助"), ("explore", "探险经验"),
                              ("train", "练兵"),
                              ("shield", "盾兵训练"),
                              ("spear", "矛兵训练"), ("archer", "射手训练"),
                              ("donate", "联盟捐献"), ("recruit", "免费招募"),
                              ("warehouse", "仓库补给"), ("tree", "生命结晶"),
                              ("dawn", "晨曦回礼"), ("pet", "宠物技能"), ("reconnect", "强制下线自动重连")):
        parser.add_argument(f"--no-{name}", action="store_true", help=f"关闭{description}")
    parser.add_argument("--tree-threshold", type=int, default=1000,
                        help="生命结晶数量达到此值时领取，必须大于 0；默认 1000")
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
                tasks = {}
                timers = {}
                drawer_template = Image.open(ROOT / "assets/emulator-drawer-open.png").convert("RGB")
                if args.daily_tasks and not args.no_explore:
                    explore_templates = {name: Image.open(ROOT / f"assets/emulator-explore-{name}.png").convert("L")
                                         for name in EXPLORE_BOXES}
                    tasks["explore"] = lambda: run_explore(serial, help_template, nav_template, explore_templates)
                units = {unit for unit, _ in TRAIN_ROWS if not getattr(args, f"no_{unit}")}
                if (args.daily_tasks or args.train) and not args.no_train and units:
                    tasks["train"] = lambda: run_training(serial, help_template, nav_template, drawer_template,
                                                           train_titles, units=units, timers=timers, keep_open=True)
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
                                tasks["stamina"] = lambda: run_stamina(
                                    serial, help_template, nav_template, drawer_template, timers)
                if args.daily_tasks and not args.no_pet:
                    for name in PET_SKILLS:
                        tasks[name] = lambda name=name: run_pet_skill(
                            serial, help_template, nav_template, drawer_template, name, timers)
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
    main()
