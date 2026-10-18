"""Handle emulator daily tasks, alliance help, and the forced-offline dialog."""

import argparse
import builtins
from datetime import datetime
import io
import json
import os
import re
import subprocess
import sys
import math
import threading
import task_scheduler
from gather_return import GatherTask
import task_rewards
import alliance_chests
import island_help
import alliance_help_clicker
import bear_hunting
import healing
import hunting
from runtime_control import (clock as time, RuntimeControl, StopRequested, activate,
                             check_stop, emit_task_event, retryable_tasks, take_retries, retry_pending)
from device_lease import DeviceLease
from emulator_runtime import avd_key
from image_matching import open_template, minimum_difference, find_mask as matched_mask
from screen_geometry import adapter as screen_adapter, scene_view
from task_status import task_label as task_display_name
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from ocr_backend import recognize_text
from intelligence_icons import ordinary_pins, completed_pin
from platform_support import SUBPROCESS_OPTIONS, configure_stdio, resolve_adb
from runtime_logs import bind_console_emulator, capture_console
from stamina_state import load_refresh, save_refresh


ROOT = Path(__file__).resolve().parent
ADB = resolve_adb()
HELP_BOX = (748, 2070, 830, 2155)
NAV_BOX = (70, 2195, 150, 2275)
EMULATOR_OFFLINE_TEXT_BOX = (130, 1175, 940, 1250)
EMULATOR_RECONNECT_BOX = (570, 1435, 935, 1530)
OFFLINE_TEXT_BOX = EMULATOR_OFFLINE_TEXT_BOX
RECONNECT_BOX = EMULATOR_RECONNECT_BOX
HELP_COUNT_BOX = (830, 2050, 865, 2085)
# Logical canvas used by templates, OCR, navigation, taps and swipes.
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
WILDERNESS_HEALING = None
HEALING_ACTIVE = False
HELP_RETRY_AT = 0.0
TASK_DRAWER_SCROLL = None
DRAWER_SWIPES = 6
DRAWER_SWIPE_START = "1320"
DRAWER_SWIPE_END = "1080"
DRAWER_SWIPE_MS = "800"
DIAGNOSTIC_CLEANUP_AT = 0.0
DIAGNOSTIC_SCREENSHOT_DIR = "exception_screenshots"
DIAGNOSTIC_RETENTION_SECONDS = 7 * 86400
DIAGNOSTIC_FILENAME = re.compile(
    r"(?:island-return|recruit-failure|treasure-failure|exception)-\d{8}-\d{6}-\d{6}\.png")
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
EXPLORE_INTERVAL = 8 * 3600 + 59 * 60


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
    check_stop()
    if args[:3] in (("shell", "input", "tap"), ("shell", "input", "swipe")):
        if screen_adapter.geometry(serial) is None:
            color_screenshot(serial)
        args = screen_adapter.input(serial, args)
    result = subprocess.run(
        [str(ADB), "-s", serial, *args],
        check=True,
        capture_output=True,
        timeout=timeout,
        **SUBPROCESS_OPTIONS,
    ).stdout
    check_stop()
    return result


def connected_device():
    result = subprocess.run(
        [str(ADB), "devices"], check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15, **SUBPROCESS_OPTIONS
    ).stdout
    devices = [line.split()[0] for line in result.splitlines()[1:]
               if len(line.split()) >= 2 and line.split()[1] == "device"]
    if len(devices) != 1:
        raise RuntimeError(f"需要恰好一台已连接安卓设备，当前找到 {len(devices)} 台；可用 --device 指定")
    return devices[0]


def screenshot(serial):
    return color_screenshot(serial).convert("L")


def color_screenshot(serial):
    native = Image.open(io.BytesIO(adb(serial, "exec-out", "screencap", "-p"))).convert("RGB")
    previous = screen_adapter.geometry(serial)
    image = screen_adapter.frame(serial, native)
    geometry = screen_adapter.geometry(serial)
    if previous is None or (previous.size, previous.viewport) != (geometry.size, geometry.viewport):
        print(f"屏幕适配：{native.width}×{native.height} → 1080×2340；"
              f"游戏区域 {geometry.viewport}；截图与操作坐标已统一", file=sys.stderr, flush=True)
    return image


def difference(left, right):
    return ImageStat.Stat(ImageChops.difference(left, right)).mean[0]


def forced_offline(image, text_template, reconnect_template):
    return reconnect_target(image, text_template, reconnect_template) is not None


def reconnect_target(image, text_template, reconnect_template):
    if (difference(image.crop(OFFLINE_TEXT_BOX), text_template) <= OFFLINE_LIMIT
            and difference(image.crop(RECONNECT_BOX), reconnect_template) <= OFFLINE_LIMIT):
        return ((RECONNECT_BOX[0] + RECONNECT_BOX[2]) // 2,
                (RECONNECT_BOX[1] + RECONNECT_BOX[3]) // 2)
    return None


def city_relocation_target(image):
    """Only acknowledge the observed zero-defense automatic relocation notice."""
    gray = image.convert("L")
    for name, box in (("relocation-title", (490, 925, 590, 980)),
                      ("relocation-message", (130, 1130, 940, 1240)),
                      ("relocation-confirm", (440, 1430, 640, 1500))):
        with open_template(ROOT / f"assets/emulator-{name}.png") as template:
            if difference(gray.crop(box), template.convert("L")) > 12:
                return None
    return 540, 1465


def network_reconnect_target(image):
    """Require the network title, explanation and reconnect button together."""
    gray = image.convert("L")
    for name, box in (("network-title", (465, 925, 615, 980)),
                      ("network-message", (90, 1180, 960, 1230)),
                      ("network-reconnect", (675, 1435, 885, 1500))):
        with open_template(ROOT / f"assets/emulator-{name}.png") as template:
            if difference(gray.crop(box), template.convert("L")) > 12:
                return None
    return 780, 1480


def reconnect_popup_target(image):
    target = secret_choice_close_target(image)
    if target is not None:
        return target
    target = bazaar_close_target(image)
    if target is not None:
        return target
    target = dawn_gift_close_target(image)
    if target is not None:
        return target
    target = legendary_gift_close_target(image)
    if target is not None:
        return target
    target = wish_shop_close_target(image)
    if target is not None:
        return target
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


def secret_choice_close_target(image):
    """Require both the observed Nox offer title and its gold exit control."""
    gray = image.convert("L")
    for name, box in (("secret-choice-title", (345, 230, 840, 320)),
                      ("secret-choice-close", (965, 230, 1045, 315))):
        with open_template(ROOT / f"assets/emulator-{name}.png") as template:
            if difference(gray.crop(box), template.convert("L")) > 15:
                return None
    return 1005, 272


def bazaar_close_target(image):
    """Close the observed bazaar only when its heading and top-right X match."""
    import cv2
    import numpy as np
    gray = image.convert("L")
    for name, box in (("bazaar-title", (350, 300, 720, 380)),
                      ("bazaar-close", (985, 165, 1050, 235))):
        with open_template(ROOT / f"assets/emulator-{name}.png") as template:
            target = template.convert("L")
            crop = gray.crop(box)
            delta = difference(crop, target)
            # The heading's backing varies with the emulator renderer; still
            # require its lettering and the independent, strict close control.
            heading_match = (name == "bazaar-title" and delta <= 24 and
                             cv2.matchTemplate(np.asarray(crop), np.asarray(target),
                                               cv2.TM_CCOEFF_NORMED)[0, 0] >= .88)
            if delta > 12 and not heading_match:
                return None
    return 1018, 200


def wish_shop_close_target(image):
    """Require the observed wish-shop heading and its specific close X."""
    gray = image.convert("L")
    for name, box in (("wish-shop-title", (125, 500, 350, 565)),
                      ("wish-shop-close", (879, 505, 949, 575))):
        with open_template(ROOT / f"assets/emulator-{name}.png") as template:
            if difference(gray.crop(box), template.convert("L")) > 12:
                return None
    return 914, 540


def dawn_gift_close_target(image):
    """Require the observed dawn-gift heading and its purple-backed close X."""
    gray = image.convert("L")
    with open_template(ROOT / "assets/emulator-dawn-gift-close.png") as template:
        if difference(gray.crop((879, 437, 949, 507)), template.convert("L")) > 12:
            return None
    # The level prefix changes the heading's width; only match the gift name
    # with the observed small horizontal shift between level 19 and level 30.
    with open_template(ROOT / "assets/emulator-dawn-gift-title.png") as template:
        score, _, _ = minimum_difference(gray, template.convert("L"),
                                         (235, 430, 530, 505), range(-12, 13), (0,))
        if score > 10 and not any("不灭曙光礼包" in text.replace(" ", "") for text, _ in
                                  recognize_text(image.crop((110, 410, 650, 520)))):
            return None
    return 914, 472


def legendary_gift_close_target(image):
    """Confirm both the gift title and its gold-backed close button."""
    # The gold background is bright enough to enter white_mask(), so the
    # generic white-X templates cannot reliably recognize this sale panel.
    gray = image.convert("L")
    for name, box in (("legendary-gift-title", (130, 430, 440, 505)),
                      ("legendary-gift-close", (879, 437, 949, 507))):
        with open_template(ROOT / f"assets/emulator-{name}.png") as template:
            if difference(gray.crop(box), template.convert("L")) > 15:
                return None
    return 914, 472


def inspect(image, help_template, nav_template, allow_wilderness=False):
    check_game_guide(image)
    nav_difference = difference(image.crop(NAV_BOX), nav_template)
    if (nav_difference > NAV_LIMIT or (town_button(image) and not allow_wilderness)
            or island_page(image, open_template(ROOT / "assets/emulator-island-title.png").convert("L"),
                           use_ocr=False)):
        return False, None, nav_difference

    best = minimum_difference(image, help_template, HELP_BOX, range(-30, 31, 2), range(-30, 31, 2))
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
    return matched_mask(image, template, x_range, y_range, mask_function)


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
    if name == "recruit-title":
        # Snow and the blue background vary between accounts and animation frames.
        # Match the white title text rather than raising the whole-image tolerance.
        box = TASK_BOXES[name]
        return find_white(image, templates[name], [box[0]], [box[1]])[0] >= .90
    if name in ("alliance-title", "tech-title"):
        # The observed header is displaced by one pixel after rendering.
        # Keep the same difference limit and search only its immediate area.
        score = minimum_difference(image.convert("L"), templates[name].convert("L"),
                                   TASK_BOXES[name], range(-2, 3), range(-2, 3))[0]
        return score <= limit or (name == "tech-title" and score <= 20
                                  and header_text(image, "联盟科技"))
    return difference(image.crop(TASK_BOXES[name]), templates[name]) <= limit


def task_screen(serial, help_enabled=True):
    global TASK_DRAWER_SCROLL
    time.sleep(1)
    image = color_screenshot(serial)
    check_game_guide(image)
    # Notifications and timed offers may appear a frame after the last tap.
    # Dismiss only identified interruptions before handing a frame to a task.
    for _ in range(5):
        target = (social_back_target(image) or secret_choice_close_target(image)
                  or bazaar_close_target(image))
        if target is None:
            break
        task_tap(serial, *target)
        time.sleep(1)
        image = color_screenshot(serial)
        check_game_guide(image)
    image = check_wilderness_healing(serial, image)
    if TASK_HELP is None or not help_enabled:
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
            expected_scroll = TASK_DRAWER_SCROLL or 0
            post = city_drawer_tab(serial, post, drawer_template)
            for _ in range(expected_scroll):
                adb(serial, "shell", "input", "swipe", "400", DRAWER_SWIPE_START,
                    "400", DRAWER_SWIPE_END, DRAWER_SWIPE_MS)
                time.sleep(.5)
                post = color_screenshot(serial)
                if not task_page(post, {"drawer-open": drawer_template}, "drawer-open"):
                    raise RuntimeError("互助后恢复抽屉滚动位置失败，已停止")
            TASK_DRAWER_SCROLL = expected_scroll
    if not was_chat and confirmed:
        print("任务期间已确认一次联盟互助", flush=True)
    return check_wilderness_healing(serial, post)


def check_priority_help(serial, help_template, nav_template, text_template, reconnect_template):
    image = screenshot(serial)
    if forced_offline(image, text_template, reconnect_template):
        raise RuntimeError("互助检查前检测到强制下线")
    if not inspect(image, help_template, nav_template)[0]:
        raise RuntimeError("互助检查前未确认主城")
    task_screen(serial)


def chat_page(image):
    template = CHAT_TEMPLATE
    if template is None:
        with open_template(ROOT / "assets/emulator-chat-title.png") as asset:
            template = asset.convert("L")
    # The observed chat header renders one pixel lower on some frames.
    # Keep the existing threshold and search only the immediate title area.
    return minimum_difference(image.convert("L"), template, CHAT_BOX,
                              range(-2, 3), range(-2, 3))[0] < 15


def social_back_target(image):
    """Recognize the acknowledgement popup, its underlying profile, and chat."""
    gray = image.convert("L")
    for name, box in (("popularity-title", (360, 780, 725, 860)),
                      ("popularity-button", (445, 1510, 645, 1565))):
        with open_template(ROOT / f"assets/emulator-{name}.png") as template:
            if difference(gray.crop(box), template.convert("L")) > 15:
                break
    else:
        return 540, 1540
    with open_template(ROOT / "assets/emulator-profile-title.png") as template:
        if difference(gray.crop((140, 150, 350, 230)), template.convert("L")) < 15:
            return 60, 190
    if chat_page(image):
        return 60, 190
    return None


def alliance_back_target(image):
    if (target := alliance_chests.back_target(sys.modules[__name__], image)) is not None:
        return target
    gray = image.convert("L")
    # First visits to the stronghold screen open a rules modal. Its dimmed
    # underlying title cannot identify the screen until the modal is closed.
    for name, box in (("stronghold-rules-title", (370, 585, 710, 650)),
                      ("stronghold-rules-close", (930, 590, 990, 655))):
        with open_template(ROOT / f"assets/emulator-{name}.png") as template:
            if difference(gray.crop(box), template.convert("L")) > 15:
                break
    else:
        return 960, 620
    with open_template(ROOT / "assets/emulator-stronghold-title.png") as template:
        if difference(gray.crop((135, 150, 370, 225)), template.convert("L")) <= 15:
            return 60, 190
    if alliance_help_clicker.help_page(sys.modules[__name__], gray):
        return 60, 190
    templates = {name: open_template(ROOT / f"assets/emulator-{name}.png").convert("L")
                 for name in ("tech-detail", "tech-title", "alliance-title")}
    if task_page(gray, templates, "tech-detail"):
        return 960, 575
    if any(task_page(gray, templates, name) for name in ("tech-title", "alliance-title")):
        return 60, 190
    return None


def recruit_back_target(image):
    templates = {name: open_template(ROOT / f"assets/emulator-{name}.png").convert("L")
                 for name in ("recruit-title", "reward-title")}
    if any(task_page(image.convert("L"), templates, name) for name in templates):
        return 60, 190
    return None


def town_button(image):
    return (TOWN_TEMPLATE is not None and
            difference(image.crop(TOWN_BOX).convert("L"), TOWN_TEMPLATE) <= 15)


def healing_back_target(image):
    offset = healing.page(sys.modules[__name__], image)
    return (899 + offset[0], 804 + offset[1]) if offset is not None else None


def run_healing(serial, help_template, nav_template, initial_image=None):
    global HEALING_ACTIVE
    previous = HEALING_ACTIVE
    HEALING_ACTIVE = True
    try:
        return healing.run(sys.modules[__name__], serial, help_template, nav_template, initial_image)
    finally:
        HEALING_ACTIVE = previous


def check_wilderness_healing(serial, image):
    """Inspect every task return to the field; hospital frames cannot recurse."""
    if WILDERNESS_HEALING is None or HEALING_ACTIVE:
        return image
    help_template, nav_template = WILDERNESS_HEALING
    if healing.entry(sys.modules[__name__], image, help_template, nav_template) is None:
        return image
    if gather_search_page(image) or intelligence_page(image):
        return image
    if hunting.button_target(sys.modules[__name__], image,
                             ("攻击", "集结", "发起集结", "采集", "出征", "前往查看",
                              "营救", "探险", "战斗"),
                             (280, 500, 1050, 2300)) is not None:
        return image
    print("返回野外主界面，检测到伤兵，立即执行自动治疗", flush=True)
    return run_healing(serial, help_template, nav_template, image)


def run_hunt(serial, help_template, nav_template, monsters=tuple(hunting.MONSTERS), levels=None, timers=None):
    return hunting.run(sys.modules[__name__], serial, help_template, nav_template, monsters, levels, timers)


def check_game_guide(image):
    from gui_web import StartupGuideBlocked, startup_guide_blocked
    if startup_guide_blocked(image):
        raise StartupGuideBlocked("检测到强制游戏引导，普通任务导航不可用；完成引导后再恢复")


def return_to_city(serial, help_template, nav_template, text_template, reconnect_template,
                   timeout=60, _reopen_frozen=True):
    confirmations = 0
    started = time.monotonic()
    deadline = started + timeout
    next_progress = started + 10
    nav_difference = None
    navigation_target = None
    navigation_pending_until = 0
    unknown_frames = 0
    back_attempts = 0
    network_attempts = 0
    network_next_try = 0
    relocation_attempts = 0
    relocation_next_try = 0
    frozen_navigation = None
    town_attempts = 0
    frozen_tech = None
    frozen_tech_unchanged = True
    tech_back_attempts = 0
    tech_first_back = None
    island_template = open_template(ROOT / "assets/emulator-island-title.png").convert("L")
    while time.monotonic() < deadline:
        image = screenshot(serial)
        check_game_guide(image)
        if frozen_tech is not None and difference(image, frozen_tech) >= .1:
            frozen_tech_unchanged = False
        if forced_offline(image, text_template, reconnect_template):
            return False
        relocation_target = city_relocation_target(image)
        if relocation_target is not None:
            confirmations = 0
            if relocation_attempts < 3 and time.monotonic() >= relocation_next_try:
                task_tap(serial, *relocation_target)
                relocation_attempts += 1
                relocation_next_try = time.monotonic() + 10
                print("已确认城防归零后的系统自动迁城提示，等待主城恢复", flush=True)
            time.sleep(min(2, max(0, deadline - time.monotonic())))
            continue
        network_target = network_reconnect_target(image)
        if network_target is not None:
            confirmations = 0
            if network_attempts < 3 and time.monotonic() >= network_next_try:
                task_tap(serial, *network_target)
                network_attempts += 1
                network_next_try = time.monotonic() + 10
                print(f"检测到无网络弹窗，已点击重新连接（{network_attempts}/3），等待主城确认", flush=True)
            time.sleep(min(2, max(0, deadline - time.monotonic())))
            continue
        bazaar_target = secret_choice_close_target(image) or bazaar_close_target(image)
        if bazaar_target is not None:
            task_tap(serial, *bazaar_target)
            confirmations = 0
            print("已确认关闭礼包或万象杂货铺，等待主城恢复", flush=True)
            time.sleep(min(2, max(0, deadline - time.monotonic())))
            continue
        if gather_search_page(image):
            leave_gather(serial, image)
            confirmations = 0
            print("已确认关闭资源搜索页面并返回野外", flush=True)
            continue
        city, _, nav_difference = inspect(image, help_template, nav_template)
        if city:
            unknown_frames = 0
            confirmations += 1
            if confirmations >= 3:
                return True
        else:
            confirmations = 0
            target = treasure_dispatch_close(image)
            action = "关闭寻宝派遣弹窗"
            if target is None:
                target = social_back_target(image)
                action = "关闭聊天或领主点赞页面"
            if target is None and intelligence_page(image):
                target, action = (60, 190), "退出情报任务页面"
            if target is None and treasure_page(image):
                target, action = (60, 190), "退出宠物寻宝页面"
            if target is None:
                target = healing_back_target(image)
                action = "关闭伤兵治疗弹窗"
            if target is None and town_button(image):
                target, action = (960, 2230), "从野外返回城镇"
            map_navigation = target in ((60, 190), (960, 2230)) and action in (
                "退出情报任务页面", "退出宠物寻宝页面", "从野外返回城镇")
            if (time.monotonic() < navigation_pending_until
                    and (target is None or target == navigation_target)):
                # The known navigation is still animating. Repeated taps can
                # toggle back; unrelated popup OCR can consume most of the
                # recovery deadline on the blank transition frame.
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(2, remaining))
                continue
            if target is None:
                target = alliance_back_target(image)
                action = "关闭联盟弹窗或退出联盟页面"
            if target is None:
                target = recruit_back_target(image)
                action = "退出英雄招募或奖励页面"
            if target is None:
                target = explore_back_target(image)
                action = "退出探险经验页面"
            if target is None:
                target = resource_more_target(image)
                action = "关闭资源不足弹窗"
            if target is None:
                target = training_back_target(image)
                action = "退出兵营训练页面"
            if target is None:
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
            if target is None and (island_help.ranking(sys.modules[__name__], image)
                                   or island_help.visitor(sys.modules[__name__], image)):
                target, action = (60, 190), "退出海岛互助页面"
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
            if target is None and task_rewards.page(sys.modules[__name__], image) is not None:
                target, action = (1030, 460), "关闭任务奖励弹窗"
            if target is not None:
                unknown_frames = 0
                task_tap(serial, *target)
                if (action == "关闭联盟弹窗或退出联盟页面" and target == (60, 190)
                        and task_page(image, {"tech-title": open_template(
                            ROOT / "assets/emulator-tech-title.png").convert("L")}, "tech-title")):
                    if frozen_tech is None:
                        frozen_tech = image.copy()
                        tech_first_back = time.monotonic()
                    tech_back_attempts += 1
                if action == "从野外返回城镇":
                    town_attempts += 1
                    if frozen_navigation is None:
                        frozen_navigation = image.copy()
                if map_navigation:
                    navigation_target = target
                    navigation_pending_until = time.monotonic() + 20
                print(f"已{action}", flush=True)
            else:
                unknown_frames += 1
                if unknown_frames >= 2 and back_attempts < 3:
                    # Give unknown loading frames time to settle before using
                    # Android's physical Back key. Each press still requires
                    # the game in the foreground and a fresh city check.
                    task_back(serial)
                    back_attempts += 1
                    unknown_frames = 0
                    navigation_target = None
                    navigation_pending_until = time.monotonic() + 6
                    print(f"未识别到可用退出按钮，已发送 Android 返回键（{back_attempts}/3）", flush=True)
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
    # A frozen map can still match the navigation templates. Require an
    # unchanged complete frame after multiple spaced town taps before reopening.
    if (_reopen_frozen and town_attempts >= 2 and frozen_navigation is not None
            and town_button(image) and difference(image, frozen_navigation) < .1):
        check_stop()
        if not game_foreground(serial):
            raise RuntimeError("当前前台不是目标游戏，未重新打开")
        print("野外返回城镇多次未生效且画面冻结，重新打开无尽冬日一次", flush=True)
        return reopen_frozen_game(serial, help_template, nav_template, text_template, reconnect_template)
    # The October 16 technology tree ignored every calibrated Back tap for
    # ten minutes. Only reopen after the full navigation timeout, repeated
    # known exits, and continuously unchanged full frames. A page that changed
    # and later returned to the same frame is not evidence of a freeze.
    if (_reopen_frozen and tech_back_attempts >= 2 and frozen_tech_unchanged
            and tech_first_back is not None and time.monotonic() - tech_first_back >= 15):
        check_stop()
        if not game_foreground(serial):
            raise RuntimeError("当前前台不是目标游戏，未重新打开")
        print("联盟科技返回多次未生效且画面冻结，重新打开无尽冬日一次", flush=True)
        return reopen_frozen_game(serial, help_template, nav_template, text_template, reconnect_template)
    raise RuntimeError(f"{timeout:g} 秒内仍未确认主城，已停止")


def reopen_frozen_game(serial, help_template, nav_template, text_template, reconnect_template):
    check_stop()
    if not game_foreground(serial):
        raise RuntimeError("当前前台不是目标游戏，未重新打开")
    from gui_web import game_loaded
    # A cold launch can exit before reaching a game surface. Ordinary task
    # retries only navigate and cannot recover from Android's launcher.
    for attempt in range(3):
        check_stop()
        if attempt:
            print(f"冻屏恢复启动未完成，重新打开无尽冬日（第 {attempt} 次重试）", flush=True)
        adb(serial, "shell", "am", "force-stop", PACKAGE.rstrip("/"))
        adb(serial, "shell", "monkey", "-p", PACKAGE.rstrip("/"),
            "-c", "android.intent.category.LAUNCHER", "1")
        loading_deadline = time.monotonic() + 180
        while time.monotonic() < loading_deadline:
            check_stop()
            if game_foreground(serial):
                image = screenshot(serial)
                check_game_guide(image)
                if game_loaded(image):
                    break
            time.sleep(2)
        else:
            if attempt == 2:
                raise RuntimeError("重新打开后未确认游戏进入前台并加载完成（3 次启动失败）")
            continue
        # Once loaded, city/forced-offline failures must not trigger relaunches.
        if not return_to_city(serial, help_template, nav_template, text_template,
                              reconnect_template, timeout=180, _reopen_frozen=False):
            raise RuntimeError("重新打开后检测到强制下线，未恢复任务")
        return True


def task_tap(serial, x, y):
    if not game_foreground(serial):
        raise RuntimeError("当前前台不是目标游戏，已停止")
    adb(serial, "shell", "input", "tap", str(x), str(y))


def task_back(serial):
    if not game_foreground(serial):
        raise RuntimeError("当前前台不是目标游戏，已停止")
    adb(serial, "shell", "input", "keyevent", "4")


def intelligence_page(image):
    template = open_template(ROOT / "assets/emulator-intel-title.png").convert("L")
    if difference(image.crop((140, 166, 244, 218)).convert("L"), template) <= 15:
        return True
    # The lower-level lighthouse title sits five pixels higher. Match the
    # lettering locally without depending on the map background/animation.
    score = find_white(image, template, range(130, 151, 2), range(150, 181, 2))[0]
    return score >= .94 or (score >= .85 and header_text(image, "情报"))


def header_text(image, label):
    """Read only a near-matching page's fixed top-left title."""
    return any(re.sub(r"\s+", "", text) == label for text, _ in
               recognize_text(image.crop((120, 140, 380, 235))))


def intelligence_entry_target(image):
    """Locate the round intelligence icon above mail, with or without pets."""
    if not town_button(image):
        return None
    template = open_template(ROOT / "assets/emulator-intel-entry.png").convert("L")
    score, x, y = find_white(image, template, range(940, 981, 5), range(1620, 1950, 5))
    if score >= .94:
        return x + template.width // 2, y + template.height // 2
    return None


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


def intelligence_refresh_countdown(image):
    # Maps with available tasks show the refresh timer at the top; the
    # exhausted map on emulator-5556 shows it in a banner at the center.
    for box in ((300, 280, 840, 375), (200, 1080, 900, 1300)):
        seconds = labelled_countdown(image, box, ("下次刷新",))
        if seconds is not None:
            return seconds
    return None


class QueueCapacityUnavailable(RuntimeError):
    pass


def intelligence_queue_full(image):
    lines = [text.replace(" ", "") for text, _ in recognize_text(image.crop((0, 430, 395, 1250)))]
    if not any("行军" in text for text in lines):
        return False
    fraction = next((re.search(r"(\d+)\s*[/／]\s*(\d+)", text) for text in lines
                     if re.search(r"(\d+)\s*[/／]\s*(\d+)", text)), None)
    if fraction is None or int(fraction[2]) == 0:
        raise QueueCapacityUnavailable("野外行军队列容量未识别，未发起任务")
    return int(fraction[1]) >= int(fraction[2])


def intelligence_completed(image, target):
    return completed_pin(image, target)


def intelligence_target(image, excluded=()):
    for target in ordinary_pins(image):
        # Animation and small scale changes can move a center a few pixels.
        if any(abs(target[0] - x) <= 40 and abs(target[1] - y) <= 45 for x, y in excluded):
            continue
        if not intelligence_completed(image, target):
            return target
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
    image = intelligence_wait_queue(serial, image)
    return navigate_intelligence(serial, image)


def navigate_intelligence(serial, image):
    """Open only the confirmed icon and wait through the page transition."""
    target = intelligence_entry_target(image)
    if target is None:
        raise RuntimeError("野外界面未定位到灯塔情报入口，未点击地图")
    task_tap(serial, *target)
    for _ in range(30):
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
    template = open_template(ROOT / "assets/emulator-intel-bounty.png").convert("L")
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
    return (x + template.width // 2, y + template.height // 2) if score >= .95 else None


def bounty_name(image):
    # Early lighthouse levels use the same bounty pin/dispatch flow but label
    # the mission 新手悬赏. Keep the full name for result identity checks.
    return next((text.replace(" ", "") for text, _ in
                 recognize_text(image.crop((200, 650, 900, 950)))
                 if text.replace(" ", "").startswith(("大师悬赏", "新手悬赏"))), None)


def wait_intelligence_detail(serial, image, target):
    """Wait for the modal; map/reward animations can swallow the first tap."""
    for attempt in range(2):
        for _ in range(5):
            if intelligence_text_target(image, ("前往查看",), (280, 1500, 800, 1770)):
                return image
            if intelligence_page(image) and intelligence_completed(image, target):
                return image
            image = task_screen(serial)
        # Retry only opening the same still-visible map pin, never dispatch.
        if attempt == 0 and intelligence_page(image):
            task_tap(serial, *target)
        else:
            break
    raise RuntimeError("点击情报图标后未确认任务详情")


def execute_bounty(serial, image, target, state):
    # Keep this latch through retries/reconnects; only confirmed success clears it.
    state["disabled"] = True
    state["outcome"] = "pending"
    trip = {}
    stage, name, coord = "任务详情", None, None
    try:
        task_tap(serial, *target)
        image = wait_intelligence_detail(serial, task_screen(serial), target)
        if intelligence_page(image) and intelligence_completed(image, target):
            state["disabled"] = False
            state["outcome"] = "won"
            return image, True
        name = bounty_name(image)
        if name is None:
            raise RuntimeError("未确认大师悬赏任务详情")
        stage = "出征"
        image, dispatched = execute_intelligence(serial, image, bounty_trip=trip)
        if not dispatched:
            state["disabled"] = False
            state["outcome"] = "not_dispatched"
            return image, False
        stage = "识别出征队列"
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
        stage = "跟踪战斗"
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
        stage = "确认战斗结果"
        for _ in range(3):
            image = open_intelligence(serial, image)
            remaining = bounty_target(image)
            if remaining is not None and intelligence_completed(image, remaining):
                state["disabled"] = False
                state["outcome"] = "won"
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
        if bounty_name(wait_intelligence_detail(serial, task_screen(serial), remaining)) != name:
            raise RuntimeError("未确认原大师悬赏任务的战斗结果")
        task_tap(serial, 100, 1900)
        image = task_screen(serial)
        state["outcome"] = "defeat"
        print("⚠️ 大师悬赏怪打不过，本次程序运行期间不会再挑战大师悬赏怪", flush=True)
        return image, True
    except Exception as error:
        state["outcome"] = "unconfirmed"
        diagnostic = save_task_failure(serial, "大师悬赏", stage, context={
            "reason": f"{type(error).__name__}: {error}", "stage": stage,
            "name": name, "target": target, "coordinate": coord,
            "trip": {key: repr(value) if isinstance(value, dict) else value
                     for key, value in trip.items()},
        })
        error.diagnostic_screenshot = diagnostic
        state["diagnostic_screenshot"] = diagnostic
        print("⚠️ 大师悬赏结果未确认，为避免重复出征，本次运行已暂停大师悬赏；"
              f"{stage}：{error}；{diagnostic}", flush=True)
        raise


def stamina_refill_target(image):
    """Identify the stamina offer, excluding other Get More resource dialogs."""
    close = resource_more_target(image)
    if close is None:
        return None
    texts = [re.sub(r"\s+", "", text) for text, _ in recognize_text(
        image.crop((200, close[1] + 80, 950, close[1] + 290)))]
    if "领主体力" in texts and any(re.fullmatch(r"\d{1,3}/200", text) for text in texts):
        return close
    return None


def leave_intelligence_stamina(serial, image):
    """Unwind the squad/detail after declining stamina, without another dispatch."""
    for _ in range(8):
        if town_button(image) or intelligence_page(image):
            return image
        back = intelligence_back_target(image)
        if back is None:
            raise RuntimeError("体力不足后未识别可退出的情报页面")
        task_tap(serial, *back)
        image = task_screen(serial)
    raise RuntimeError("体力不足后未返回情报或野外界面")


def execute_intelligence(serial, image, bounty_trip=None):
    if bounty_trip is None and bounty_name(image) is not None:
        raise RuntimeError("普通情报流程识别到大师悬赏详情，未执行出征")
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
        refill = stamina_refill_target(image)
        if refill is not None or any("体力不足" in text or "补充体力" in text for text in texts):
            print("体力已无法继续执行情报任务，等待体力恢复", flush=True)
            # Close the refill offer without using cans or buying stamina.
            task_tap(serial, *(refill or (100, 1900)))
            return leave_intelligence_stamina(serial, task_screen(serial)), False
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
            # Monster cards cover the march drawer's capacity. The unobscured
            # queue was checked before opening the intelligence map.
            monster_card = any("推荐实力" in text for text in texts)
            if town_button(image) and not monster_card and intelligence_queue_full(image):
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
    switched = False
    # A city/map switch first shows a full-screen white transition. Do not
    # treat that animation as a failed task or tap again while it is loading.
    for _ in range(30):
        interruption = secret_choice_close_target(image) or bazaar_close_target(image)
        if interruption is not None:
            task_tap(serial, *interruption)
            image = task_screen(serial)
            switched = False
            continue
        if town_button(image):
            return check_wilderness_healing(serial, image)
        if not switched and inspect(image.convert("L"), help_template, nav_template)[0]:
            drawer = open_template(ROOT / "assets/emulator-drawer-open.png").convert("RGB")
            if task_page(image, {"drawer-open": drawer}, "drawer-open"):
                task_tap(serial, 695, 1100)
                image = task_screen(serial)
            task_tap(serial, 960, 2230)
            switched = True
        image = task_screen(serial)
    if town_button(image):
        return check_wilderness_healing(serial, image)
    raise RuntimeError(f"{label}开始时未确认野外界面")


def claim_intelligence_rewards(serial, target):
    """Let the delayed reward page settle before scanning another map pin."""
    task_tap(serial, *target)
    confirmations, closed = 0, False
    for _ in range(10):
        image = task_screen(serial)
        if intelligence_page(image):
            # A still-visible claim button is the pre-claim frame. Once the
            # reward has closed, a new button can belong to another march.
            if closed or intelligence_text_target(image, ("一键领取",), (280, 2000, 800, 2190)) is None:
                confirmations += 1
                if confirmations >= 2:
                    return image
            else:
                confirmations = 0
        else:
            confirmations = 0
            if not closed and intelligence_back_target(image) == (540, 2150):
                task_tap(serial, 540, 2150)
                closed = True
    raise RuntimeError("领取情报奖励后未确认奖励页关闭并返回情报界面")


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
                     intelligence_refresh_countdown(image),
                     "灯塔情报刷新")
    pending = set()
    bounty_state = bounty_state if bounty_state is not None else {}
    while True:
        pending = {target for target in pending if not intelligence_completed(image, target)}
        claim = intelligence_text_target(image, ("一键领取",), (280, 2000, 800, 2190))
        if claim is not None:
            image = claim_intelligence_rewards(serial, claim)
            print("已一键领取情报奖励", flush=True)
        target = intelligence_target(image, pending)
        if target is None and pending and not ordinary_pins(image):
            # Claiming removes pins and can recenter the map before their
            # completion badge was observed at the original coordinate.
            # Two empty map frames confirm that no own march remains visible.
            image = task_screen(serial)
            if not intelligence_page(image):
                raise RuntimeError("确认情报任务收尾时离开情报界面")
            if not ordinary_pins(image):
                pending.clear()
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
            # Own missions may march for minutes. Yield to other scheduled
            # tasks instead of repeatedly leaving/reopening this map forever.
            # The next check still verifies completion and claims its reward.
            record_timer(timers, "intelligence", 60, "情报任务结果复查")
            print("情报任务仍在进行，60 秒后复查结果，先继续其他任务", flush=True)
            break
        task_tap(serial, *target)
        image = wait_intelligence_detail(serial, task_screen(serial), target)
        if intelligence_page(image) and intelligence_completed(image, target):
            continue
        image, completed = execute_intelligence(serial, image)
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
    template = open_template(ROOT / "assets/emulator-gather-search.png").convert("L")
    box = (485, 2200, 605, 2285)
    gray = image.convert("L")
    score = minimum_difference(gray, template, box, range(-2, 3), range(-2, 3))[0]
    if score <= 15:
        return True
    # The phone's button backing varies slightly after an empty search. Keep
    # the colour/shape score bounded and independently verify the white glyphs.
    return score <= 20 and minimum_difference(
        white_mask(gray), white_mask(template), box, range(-2, 3), range(-2, 3))[0] <= 15


def gather_level_max(image):
    template = open_template(ROOT / "assets/emulator-gather-plus-disabled.png").convert("L")
    return difference(image.crop((690, 1960, 770, 2040)).convert("L"), template) <= 15


def gather_level(image):
    """Read the numeric value to the right of the search level buttons."""
    values = [int(text.strip()) for text, _ in
              recognize_text(image.crop((795, 1960, 1015, 2045)))
              if re.fullmatch(r"[1-8]", text.strip())]
    return values[0] if len(values) == 1 else None


def set_gather_level(serial, image, level):
    for _ in range(10):
        if not gather_search_page(image):
            raise RuntimeError("调整等级时未确认资源搜索界面")
        current = gather_level(image)
        if current is None:
            # Re-read an animation frame before considering another click.
            image = task_screen(serial)
            continue
        if current == level:
            return image
        task_tap(serial, 730 if current < level else 105, 2000)
        image = task_screen(serial)
    raise RuntimeError(f"资源搜索等级未确认达到 {level} 级")


def gather_notice(image):
    texts = [re.sub(r"\s+", "", text) for text, _ in
             recognize_text(image.crop((80, 650, 1000, 2050)))]
    if "附近没有发现条件相符的目标" in "".join(texts):
        return "no_target"
    for text in texts:
        if (("士兵" in text or "兵力" in text or "部队" in text)
                and any(word in text for word in ("不足", "不够", "没有", "无可用", "暂无", "无可出征"))):
            return "no_soldiers"
    return None


def leave_gather(serial, image):
    """Close gathering panels and leave the scheduler on the wilderness map."""
    for _ in range(5):
        if gather_search_page(image):
            # A map tap leaves the search drawer open and can select another
            # mine. BACK dismisses the detail first, then the search drawer.
            task_back(serial)
        else:
            target = gather_back_target(image)
            if target is not None:
                task_tap(serial, *target)
            elif town_button(image):
                return image
            else:
                raise RuntimeError("跳过采集时未识别可退出的采集页面")
        image = task_screen(serial)
    if not gather_search_page(image) and town_button(image) and gather_back_target(image) is None:
        return image
    raise RuntimeError("跳过采集后未确认野外界面")


def gather_action_target(image):
    # "采集速度" opens buffs. Only the complete button label is an action.
    return hunting.button_target(sys.modules[__name__], image, ("采集",), (350, 1100, 730, 1600))


def search_gather_target(serial, image, resource, level):
    label = GATHER_RESOURCES[resource]
    while True:
        # Check a fresh, unobscured frame before every search, including
        # lower-level retries. Resource cards can cover the queue capacity,
        # so dispatch uses this check without closing and searching again.
        image = task_screen(serial)
        if intelligence_queue_full(image):
            return image, None, "queue_full"
        print(f"搜索{label}：{level}级", flush=True)
        task_tap(serial, 540, 2240)
        for _ in range(5):
            image = task_screen(serial)
            notice = gather_notice(image)
            if notice == "no_soldiers":
                return image, None, notice
            if notice == "no_target":
                break
            target = gather_action_target(image)
            if target is not None:
                return image, target, None
        else:
            raise RuntimeError(f"搜索{label}后未发现采集按钮或无目标提示")
        if level == 1:
            print(f"{label}1级仍无符合条件的目标，跳过此资源", flush=True)
            return image, None, "no_target"
        level -= 1
        print(f"附近没有发现条件相符的目标，{label}降至{level}级重试", flush=True)
        image = set_gather_level(serial, image, level)


def gather_full_only(image):
    return ImageStat.Stat(green_mask(image.crop((300, 2110, 355, 2165)))).sum[0] / 255 > 60


def gather_back_target(image):
    if gather_search_page(image):
        return None  # Search drawers must be closed with task_back.
    if gather_action_target(image):
        return 540, 1550
    if intelligence_text_target(image, (*GATHER_RESOURCES.values(), "城镇增益"), (120, 150, 360, 235)):
        return 60, 190
    return None


def prepare_gather_search(serial, image, resource, level=1, full_only=True):
    label = GATHER_RESOURCES[resource]
    for attempt in range(4):
        if not gather_search_page(image):
            raise RuntimeError("未确认资源搜索界面")
        target = intelligence_text_target(image, (label,), (0, 1790, 1080, 1880))
        if target is not None and 110 <= target[0] <= 970:
            task_tap(serial, *target)
            image = task_screen(serial)
            break
        # Monster searches leave this shared carousel at its left end. Resource
        # searches must move toward the requested category, even after hunting.
        categories = ("失控的雪怪", "野兽", "冰原巨兽", "大型养殖场", *GATHER_RESOURCES.values())
        desired = categories.index(label)
        visible = [categories.index(text.replace(" ", "")) for text, _ in
                   recognize_text(image.crop((0, 1790, 1080, 1880)))
                   if text.replace(" ", "") in categories]
        if target is not None:
            left = target[0] > 970
        elif visible:
            left = desired > min(visible)
        else:
            # Unknown text must not cause four identical swipes at an endpoint.
            left = attempt < 2
        start, end = (900, 200) if left else (200, 900)
        if not game_foreground(serial):
            raise RuntimeError("当前前台不是目标游戏，已停止")
        adb(serial, "shell", "input", "swipe", str(start), "1780", str(end), "1780", "600")
        image = task_screen(serial)
    else:
        raise RuntimeError(f"搜索栏未找到完整的{label}图标")
    image = set_gather_level(serial, image, level)
    if gather_full_only(image) != full_only:
        task_tap(serial, 328, 2135)
        image = task_screen(serial)
    if not gather_search_page(image) or gather_full_only(image) != full_only:
        raise RuntimeError("未确认仅搜索资源为满的资源点选项与配置一致")
    return image


def parse_gather_step(value):
    import argparse
    resource, separator, level = value.partition(":")
    if resource not in GATHER_RESOURCES or not separator or level not in tuple(str(i) for i in range(1, 9)):
        raise argparse.ArgumentTypeError("采集子选项必须为资源:等级，例如 wood:7")
    return resource, int(level)


def run_gather(serial, help_template, nav_template, text_template, reconnect_template,
               resources=tuple(GATHER_RESOURCES), levels=None, steps=None, full_only=True,
               return_monitor=None):
    if steps is None:
        if not resources or any(resource not in GATHER_RESOURCES for resource in resources):
            raise ValueError("至少要选择一项有效资源")
        steps = [(resource, (levels or {}).get(resource, 1)) for resource in resources]
    if not 1 <= len(steps) <= 6 or any(resource not in GATHER_RESOURCES for resource, _ in steps):
        raise ValueError("资源采集必须配置 1 到 6 个有效子选项")
    if any(type(level) is not int or not 1 <= level <= 8 for _, level in steps):
        raise ValueError("资源采集等级必须是 1 到 8 的整数")
    image = enter_wilderness(serial, task_screen(serial), help_template, nav_template, "资源采集")
    for resource, level in steps:
        label = GATHER_RESOURCES[resource]
        image = task_screen(serial)
        if not town_button(image):
            raise RuntimeError("采集前未确认野外界面")
        if intelligence_queue_full(image):
            print("野外行军队列已满，跳过剩余资源采集", flush=True)
            break
        before_dispatch = image
        task_tap(serial, 65, 1735)
        image = prepare_gather_search(serial, task_screen(serial), resource, level, full_only=full_only)
        image, target, notice = search_gather_target(serial, image, resource, level)
        if target is None:
            image = leave_gather(serial, image)
            if notice == "queue_full":
                print("野外行军队列已满，跳过剩余资源采集", flush=True)
                break
            if notice == "no_soldiers":
                print("士兵不足，跳过本轮采集，在野外等待其他自动任务", flush=True)
                break
            continue
        task_tap(serial, *target)
        for _ in range(5):
            image = task_screen(serial)
            if gather_notice(image) == "no_soldiers":
                print("士兵不足，跳过本轮采集，在野外等待其他自动任务", flush=True)
                return leave_gather(serial, image)
            target = intelligence_text_target(image, ("出征",), (600, 2180, 1050, 2300))
            if target is not None:
                break
        else:
            raise RuntimeError(f"{label}采集未进入出征界面")
        task_tap(serial, *target)
        for _ in range(5):
            image = task_screen(serial)
            if gather_notice(image) == "no_soldiers":
                print("士兵不足，跳过本轮采集，在野外等待其他自动任务", flush=True)
                return leave_gather(serial, image)
            if town_button(image) and not gather_search_page(image):
                break
        else:
            raise RuntimeError(f"{label}采集出征后未返回野外界面")
        print(f"已派遣{label}采集队伍", flush=True)
        if return_monitor is not None:
            return_monitor.dispatched(before_dispatch, image)
    if not town_button(image):
        raise RuntimeError("采集结束后未确认野外界面")
    return image


def resource_donation_available(image):
    red, _, blue = ImageStat.Stat(image.crop((565, 1780, 610, 1830))).mean
    return blue - red > 60


def tech_list_ready(image, templates):
    if not task_page(image, templates, "tech-title"):
        return False
    # The title renders before the server populates the technology tree.
    return ImageStat.Stat(white_mask(image.crop((50, 650, 1030, 2200)))).sum[0] > 255 * 400


def tech_detail_ready(image, templates):
    return (task_page(image, templates, "tech-detail")
            and ImageStat.Stat(image.crop((740, 1690, 900, 1745)).convert("L")).stddev[0] > 8)


def alliance_tech_target(image, templates):
    """Locate the actual technology button after the alliance menu loads."""
    if (not task_page(image, templates, "alliance-title")
            or task_page(image, templates, "tech-title")):
        return None
    box = (520, 850, 1040, 2000)
    targets = [(round(box[0] + x + width / 2), round(box[1] + y + height / 2))
               for text, (x, y, width, height) in recognize_text(image.crop(box))
               if re.sub(r"\s+", "", text) == "联盟科技"]
    return targets[0] if len(targets) == 1 else None


def wait_alliance_tech(serial, image, templates):
    # The header appears before the menu, whose buttons can still move while
    # animating. Require the labelled button to stay in place across two frames.
    previous = None
    for attempt in range(8):
        target = alliance_tech_target(image, templates)
        if (target is not None and previous is not None
                and max(abs(a - b) for a, b in zip(target, previous)) <= 8):
            return image, target
        previous = target
        if attempt < 7:
            image = task_screen(serial)
    raise RuntimeError("联盟科技入口未加载或位置仍不稳定，未点击")


def red_dot(image, box):
    dot = image.crop(box).convert("RGB")
    return sum(r > 180 and r > g * 1.45 and r > b * 1.45
               for r, g, b in dot.getdata()) > 100


def explore_page_box(image, templates, name):
    box = EXPLORE_BOXES[name]
    if difference(image.crop(box).convert("L"), templates[name]) < 15:
        return box
    if name == "title":
        score, dx, dy = minimum_difference(image.convert("L"), templates[name], box,
                                           range(-2, 3), range(-2, 3))
        if score < 15:
            return box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy
    if name == "income":
        # The pre-200-stage equipment hint makes this modal taller, moving
        # its title/close button up and its claim button down.
        score, x, y = find_white(image, templates[name], range(box[0] - 4, box[0] + 5, 4),
                                 range(box[1] - 70, box[1] + 71, 4))
        if score >= .95:
            return x, y, x + templates[name].width, y + templates[name].height
        score, dx, dy = minimum_difference(image.convert("L"), templates[name], box,
                                           range(-4, 5), range(-70, 71))
        if score < 15:
            return box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy
        if score < 20:
            candidate = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
            if any(text.replace(" ", "") == "挂机收益" for text, _ in
                   recognize_text(image.crop(candidate))):
                return candidate
    return None


def explore_page(image, templates, name):
    return explore_page_box(image, templates, name) is not None


def explore_back_target(image):
    templates = {name: open_template(ROOT / f"assets/emulator-explore-{name}.png").convert("L")
                 for name in EXPLORE_BOXES}
    if (box := explore_page_box(image, templates, "income")) is not None:
        return 945, 720 + box[1] - EXPLORE_BOXES["income"][1]
    if explore_page(image, templates, "reward"):
        return 540, 2050
    if explore_page(image, templates, "title"):
        return 60, 190
    return None


def explore_claim_state(image, templates):
    if not explore_page(image, templates, "title"):
        return None
    box = (835, 1675, 1000, 1750)
    if green_button(image, box):
        return "ready"
    with open_template(ROOT / "assets/emulator-explore-idle-button.png") as idle:
        if difference(image.crop(box).convert("L"), idle.convert("L")) < 12:
            return "empty"
    return None


def explore_cooldown(image, templates):
    """Recognize the sampled rejection toast before reading its remaining time."""
    if not (explore_page(image, templates, "title") or explore_page(image, templates, "income")):
        return None
    with open_template(ROOT / "assets/emulator-explore-cooldown-hint.png") as hint:
        score, _, _ = find_white(image, hint, range(120, 141, 4), range(800, 831, 4))
    if score < .9:
        return None
    text = "".join(line.replace(" ", "") for line, _ in
                   recognize_text(image.crop((80, 790, 1000, 940))))
    match = re.search(r"\d{1,3}\s*[:：]\s*[0-5]\d\s*[:：]\s*[0-5]\d", text)
    if match is None:
        raise RuntimeError("已识别探险领取冷却提示，但未识别剩余倒计时")
    return parse_countdown(match.group(0))


def green_button(image, box):
    button = image.crop(box).convert("RGB")
    green = sum(g > r + 35 and g > b + 25 and g > 100 for r, g, b in button.getdata())
    return green / (button.width * button.height) > 0.5


def recruit_button_state(image, templates, kind):
    if kind == "recruit_advanced":
        free = task_page(image, templates, "free-button", 15)
        button_detail = f"按钮模板差异 {difference(image.crop(TASK_BOXES['free-button']), templates['free-button']):.1f}"
        if not free and green_button(image, (130, 1655, 440, 1725)):
            # Different accounts have slightly different text antialiasing.
            # Read only the button: "今日免费招募剩余" above it is not proof.
            labels = [text.replace(" ", "") for text, _ in
                      recognize_text(image.crop((130, 1630, 440, 1740)))]
            free = "免费" in labels
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
    text = "".join(texts)
    return ("点击任意位置退出" in text
            or ("您已拥有该英雄" in text and "自动转换为" in text))


def recruit_result_exit_target(image):
    # Centered Nox results put the old header-back coordinate in black padding.
    # Prefer the independently confirmed exit hint without touching recruit-again.
    return (540, 2100) if recruit_hero_exit_page(image) else (60, 190)


def run_explore(serial, help_template, nav_template, templates, timers=None):
    def wait_for(image, confirmed, error):
        for attempt in range(5):
            if confirmed(image):
                return image
            if attempt < 4:
                image = task_screen(serial)
        raise RuntimeError(error)

    def leave(image):
        if not explore_page(image, templates, "title"):
            raise RuntimeError("退出探险前未确认探险页面")
        task_tap(serial, 60, 190)
        return wait_for(task_screen(serial),
                        lambda frame: inspect(frame.convert("L"), help_template, nav_template)[0],
                        "探险经验领取后未返回主城")

    remaining = None
    empty_responses = 0
    entry_empty = False
    cooldown_unknown = False

    def claim_response(frame, expected):
        nonlocal remaining, empty_responses, cooldown_unknown
        if explore_page(frame, templates, expected):
            return expected != "income" or green_button(frame, (350, 1600, 740, 1730))
        remaining = explore_cooldown(frame, templates)
        if remaining is not None:
            return True
        # A short rejection toast can disappear before ADB captures it. Only
        # defer if every response still confirms the sampled disabled button.
        if expected == "income" and entry_empty and explore_claim_state(frame, templates) == "empty":
            empty_responses += 1
            if empty_responses == 5:
                remaining = 599
                cooldown_unknown = True
                return True
        else:
            empty_responses = 0
        return False

    def defer(image):
        record_timer(timers, "explore", remaining + 1, "探险经验")
        if cooldown_unknown:
            print("探险经验：已确认领取按钮不可用，冷却提示已消失，10 分钟后重新检查", flush=True)
        else:
            print(f"探险经验：尚未到领取时间，{remaining + 1} 秒后再领取", flush=True)
        if (target := explore_back_target(image)) is not None and explore_page(image, templates, "income"):
            task_tap(serial, *target)
            image = wait_for(task_screen(serial), lambda frame: explore_page(frame, templates, "title"),
                             "关闭探险挂机收益后未确认探险页面")
        return leave(image)

    image = color_screenshot(serial)
    if not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("探险检查前未确认主城")
    print("探险经验：进入探险界面领取", flush=True)
    task_tap(serial, 95, 2240)
    image = wait_for(task_screen(serial), lambda frame: explore_claim_state(frame, templates) is not None,
                     "未确认探险经验可领取页面")
    entry_empty = explore_claim_state(image, templates) == "empty"
    task_tap(serial, 915, 1718)
    image = wait_for(task_screen(serial),
                     lambda frame: claim_response(frame, "income"),
                     "未确认探险挂机收益领取弹窗")
    if remaining is not None:
        return defer(image)
    task_tap(serial, 540, 1660)
    claimed_at = time.monotonic()
    image = wait_for(task_screen(serial), lambda frame: claim_response(frame, "reward"),
                     "未确认探险经验奖励页面")
    if remaining is not None:
        return defer(image)
    task_tap(serial, 540, 2050)
    image = wait_for(task_screen(serial), lambda frame: explore_claim_state(frame, templates) == "empty",
                     "探险经验领取后状态未变化")
    image = leave(image)
    if timers is not None:
        timers["explore"] = claimed_at + EXPLORE_INTERVAL
    print("已领取探险经验，8 小时 59 分后再次领取", flush=True)
    return image


def city_drawer_tab(serial, image, drawer_template):
    """A visible collapse handle confirms either tab, not the city task list."""
    global TASK_DRAWER_SCROLL

    def tab_brightness(frame):
        left = ImageStat.Stat(frame.crop((60, 650, 110, 705)).convert("RGB")).mean
        right = ImageStat.Stat(frame.crop((390, 650, 440, 705)).convert("RGB")).mean
        return left, right

    def wilderness(frame):
        left, right = tab_brightness(frame)
        if right[1] <= left[1] + 35 or right[2] <= left[2] + 35:
            return False
        labels = {text.replace(" ", "") for text, _ in
                  recognize_text(frame.crop((100, 640, 570, 720)))}
        return {"城镇", "野外"} <= labels

    if not wilderness(image):
        return image
    task_tap(serial, 180, 680)
    TASK_DRAWER_SCROLL = 0
    for _ in range(5):
        image = task_screen(serial, help_enabled=False)
        left, right = tab_brightness(image)
        if (task_page(image, {"drawer-open": drawer_template}, "drawer-open")
                and left[1] > right[1] + 35 and left[2] > right[2] + 35):
            print("主城抽屉已从野外切换到城镇任务列表", flush=True)
            return image
    TASK_DRAWER_SCROLL = None
    raise RuntimeError("未能确认主城抽屉已切换到城镇标签")


def task_drawer(serial, image, templates, reset_unknown=True, _reopen_frozen=True):
    global TASK_DRAWER_SCROLL
    check_game_guide(image)
    frozen = image.copy() if closed_city_drawer(image, templates["drawer-open"]) else None
    started = time.monotonic()
    if not task_page(image, templates, "drawer-open"):
        task_tap(serial, 20, 1100)
        TASK_DRAWER_SCROLL = 0
        image = task_screen(serial)
    for attempt in range(5):
        if frozen is not None and difference(image, frozen) >= .1:
            frozen = None
        if task_page(image, templates, "drawer-open"):
            break
        if attempt in (1, 3) and closed_city_drawer(image, templates["drawer-open"]):
            # An input may be ignored while the preceding drawer animation
            # finishes. Retry only when the closed city handle is still visible.
            task_tap(serial, 20, 1100)
            TASK_DRAWER_SCROLL = 0
        if attempt < 4:
            image = task_screen(serial)
    else:
        # A frozen city still passes the navigation check. Only restart after
        # three open taps and at least 15 seconds of unchanged closed-city frames.
        if _reopen_frozen and frozen is not None:
            while time.monotonic() - started < 15:
                time.sleep(1)
                image = task_screen(serial, help_enabled=False)
                if task_page(image, templates, "drawer-open"):
                    return city_drawer_tab(serial, image, templates["drawer-open"])
                if difference(image, frozen) >= .1 or not closed_city_drawer(image, templates["drawer-open"]):
                    frozen = None
                    break
            if frozen is not None:
                print("主城抽屉多次点击未生效且画面冻结，重新打开无尽冬日一次", flush=True)
                assets = [open_template(ROOT / f"assets/emulator-{name}.png").convert("L")
                          for name in ("help-icon", "city-nav", "forced-text", "reconnect")]
                TASK_DRAWER_SCROLL = None
                reopen_frozen_game(serial, *assets)
                return task_drawer(serial, color_screenshot(serial), templates,
                                   reset_unknown=reset_unknown, _reopen_frozen=False)
        raise RuntimeError("未能确认主城抽屉已打开")
    image = city_drawer_tab(serial, image, templates["drawer-open"])
    if TASK_DRAWER_SCROLL is None and reset_unknown:
        # 启动时已打开的抽屉可能停在任意位置，先回到顶部再记录滚动次数。
        TASK_DRAWER_SCROLL = DRAWER_SWIPES
        for _ in range(DRAWER_SWIPES):
            image = swipe_drawer(serial, templates["drawer-open"], reverse=True)
    return image


def closed_city_drawer(image, drawer_template):
    """Require the closed handle and city navigation before reopening a drawer."""
    box = TASK_BOXES["drawer-open"]
    arrow = image.crop((0, box[1] + 40, box[2] - box[0], box[1] + 120)).convert("L")
    arrow = arrow.point(lambda value: 255 if value > 230 else 0)
    reference = drawer_template.crop((0, 40, box[2] - box[0], 120)).convert("L")
    reference = reference.point(lambda value: 255 if value > 230 else 0)
    reference = reference.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if difference(arrow, reference) > 10:
        return False
    with (open_template(ROOT / "assets/emulator-city-nav.png") as nav,
          open_template(ROOT / "assets/emulator-town-button.png") as town):
        return (difference(image.crop(NAV_BOX).convert("L"), nav.convert("L")) <= NAV_LIMIT
                and difference(image.crop(TOWN_BOX).convert("L"), town.convert("L")) > 15)


def swipe_drawer(serial, drawer_template, reverse=False, recover_closed=True):
    global TASK_DRAWER_SCROLL
    if not game_foreground(serial):
        raise RuntimeError("当前前台不是目标游戏，已停止")
    start, end = ((DRAWER_SWIPE_END, DRAWER_SWIPE_START) if reverse else
                  (DRAWER_SWIPE_START, DRAWER_SWIPE_END))
    adb(serial, "shell", "input", "swipe", "400", start, "400", end, DRAWER_SWIPE_MS)
    TASK_DRAWER_SCROLL = max(0, min(DRAWER_SWIPES,
                                    TASK_DRAWER_SCROLL + (-1 if reverse else 1)))
    expected_scroll = TASK_DRAWER_SCROLL
    image = task_screen(serial)
    for attempt in range(5):
        if task_page(image, {"drawer-open": drawer_template}, "drawer-open"):
            return city_drawer_tab(serial, image, drawer_template)
        if attempt < 4:
            image = task_screen(serial, help_enabled=False)
    # Reopening resets the real list to the top. Replay only the confirmed
    # scroll count, and permit one recovery so a persistently closing drawer
    # cannot recurse or leave later tasks with a stale position.
    TASK_DRAWER_SCROLL = None
    if recover_closed and closed_city_drawer(image, drawer_template):
        try:
            image = task_drawer(serial, image, {"drawer-open": drawer_template}, reset_unknown=False)
            for _ in range(expected_scroll):
                image = swipe_drawer(serial, drawer_template, recover_closed=False)
        except Exception:
            TASK_DRAWER_SCROLL = None
            raise
        print("主城抽屉滑动后关闭，已重新打开并恢复滚动位置", flush=True)
        return image
    raise RuntimeError("滑动后未能确认主城抽屉")


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
                   recruit_kind="recruit_advanced", collect_alliance_chests=False):
    def wait_page(image, predicate):
        for attempt in range(6):
            if predicate(image):
                return image
            if attempt < 5:
                image = task_screen(serial)
        return image

    def alliance(image):
        return task_page(image, templates, "alliance-title") and not task_page(image, templates, "tech-title")

    image = initial_image if initial_image is not None else color_screenshot(serial)
    on_recruit_page = recruit and not donate and task_page(image, templates, "recruit-title")
    if not on_recruit_page and not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("未确认主城，未执行捐献和招募")
    if not on_recruit_page:
        image = task_drawer(serial, image, templates)
    donation_y = None
    if donate:
        image, _, donation_y = task_label(serial, image, templates, "donate-label")
    if donation_y is not None:
        task_tap(serial, 800, 2240)
        image = wait_page(task_screen(serial), alliance)
        if not task_page(image, templates, "alliance-title") or task_page(image, templates, "tech-title"):
            raise RuntimeError("未能确认联盟页面")
        image, target = wait_alliance_tech(serial, image, templates)
        print(f"已确认联盟科技入口：{target}", flush=True)
        task_tap(serial, *target)
        image = wait_page(task_screen(serial), lambda im: tech_list_ready(im, templates))
        if not tech_list_ready(image, templates):
            raise RuntimeError("未能确认联盟科技页面")
        score, x, y = find_green(image, templates["thumb"], range(80, 921, 8), range(600, 2101, 8))
        if score >= 0.80:
            task_tap(serial, x + 90, y + 115)
            image = wait_page(task_screen(serial), lambda im: tech_detail_ready(im, templates))
            if not tech_detail_ready(image, templates):
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
                    image = wait_page(image, lambda im: task_page(im, templates, "tech-detail"))
                    if not task_page(image, templates, "tech-detail"):
                        raise RuntimeError("长按后未能确认科技详情")
                    if not resource_donation_available(image) or difference(before, image.crop(count_box)) >= 3:
                        break
                    image = task_screen(serial)
                if resource_donation_available(image) and difference(before, image.crop(count_box)) < 3:
                    # Some emulator builds fail to repeat a held button. Try one
                    # ordinary resource tap, then require an actual count change.
                    task_tap(serial, 760, 1810)
                    image = task_screen(serial)
                    for attempt in range(5):
                        image = wait_page(image, lambda im: task_page(im, templates, "tech-detail"))
                        if not task_page(image, templates, "tech-detail"):
                            raise RuntimeError("单次捐献后未能确认科技详情")
                        if not resource_donation_available(image) or difference(before, image.crop(count_box)) >= 3:
                            break
                        if attempt < 4:
                            image = task_screen(serial)
                    else:
                        raise RuntimeError("长按及单次捐献后次数均未变化，已停止")
            else:
                raise RuntimeError("连续长按后仍可捐献，已停止以避免无限循环")
            print("普通资源已捐献至当前无法继续捐献", flush=True)
            task_tap(serial, 960, 575)
            image = wait_page(task_screen(serial), lambda im: task_page(im, templates, "tech-title")
                              and not task_page(im, templates, "tech-detail"))
            if not task_page(image, templates, "tech-title"):
                raise RuntimeError("关闭科技详情后未返回联盟科技")
        else:
            print("联盟科技页面没有绿色点赞，跳过捐献", flush=True)
        task_tap(serial, 60, 190)
        image = wait_page(task_screen(serial), alliance)
        if not task_page(image, templates, "alliance-title") or task_page(image, templates, "tech-title"):
            raise RuntimeError("未能从联盟科技返回联盟")
        if collect_alliance_chests:
            image = alliance_chests.collect_on_alliance(sys.modules[__name__], serial, image)
            if timers is not None:
                timers['alliance_chests'] = time.monotonic() + 600
        task_tap(serial, 60, 190)
        image = wait_page(task_screen(serial), lambda im: inspect(im.convert("L"), help_template, nav_template)[0])
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("未能从联盟返回主城")
        image = task_drawer(serial, image, templates)
    elif donate:
        print("抽屉里没有可捐献提示，跳过联盟科技", flush=True)

    free_y = None
    if on_recruit_page:
        free_y = 0  # 已在招募页，由实际按钮状态决定是否免费。
    elif recruit:
        image, row_bottom = recruit_row(serial, image, templates, recruit_kind)
        if row_bottom is not None:
            score, _, label_y = find_green(image, templates["free-label"], range(240, 321, 4),
                                           range(row_bottom, row_bottom + 55, 2))
            if score >= .90:
                free_y = label_y
            elif any("免费招募" in text.replace(" ", "") for text, _ in
                     recognize_text(image.crop((240, row_bottom, 550, row_bottom + 55)))):
                free_y = row_bottom
        if free_y is None:
            remaining = (countdown(image, (125, row_bottom, 550, row_bottom + 55))
                         if row_bottom is not None else None)
            if row_bottom is not None and remaining is None:
                # A missing drawer hint is not evidence of a cooldown. The
                # recruitment page independently proves free/cooldown before
                # any recruitment tap, including when drawer text is clipped.
                free_y = row_bottom
                print(f"{RECRUIT_TYPES[recruit_kind]}抽屉状态未识别，进入招募页核实", flush=True)
            else:
                record_timer(timers, recruit_kind, remaining)
    if free_y is not None:
        if not on_recruit_page:
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
                task_tap(serial, *recruit_result_exit_target(image))
            reward_closed = result_page == "reward"
            for _ in range(5):
                image = task_screen(serial)
                if task_page(image, templates, "recruit-title"):
                    break
                # 已拥有的完整英雄会先回到奖励列表，再返回招募页。
                if not reward_closed and task_page(image, templates, "reward-title"):
                    task_tap(serial, *recruit_result_exit_target(image))
                    reward_closed = True
            else:
                raise RuntimeError("未能从奖励页返回英雄招募")
            if (keep_open and recruit_kind == "recruit_advanced"
                    and recruit_button_state(image, templates, "recruit_epic")[0] == "free"):
                record_timer(timers, recruit_kind, countdown(image, (60, 1470, 520, 1720)))
                if timers is not None:
                    timers["recruit_epic"] = 0
                print("史诗招募也可免费，保留招募页面继续执行", flush=True)
                return image
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
            or minimum_difference(white_mask(image),
                                  white_mask(title.crop((140, 0, 280, 90))),
                                  (540, 140, 680, 230), range(-2, 3), range(-2, 3))[0] <= 10)


def resource_more_target(image):
    """Match both the resource panel title and its X, never purchase controls."""
    with open_template(ROOT / "assets/emulator-resource-more-title.png") as title:
        score, _, y = find_white(image, title, [430], range(300, 1001, 10))
    if score < .9:
        return None
    with open_template(ROOT / "assets/emulator-resource-more-close.png") as close:
        score, x, close_y = find_white(image, close, [972], [y + 5])
        if score >= .9:
            return x + close.width // 2, close_y + close.height // 2
    return None


def training_back_target(image):
    with open_template(ROOT / "assets/emulator-resource-more-close.png") as close:
        score, x, y = find_white(image, close, [968], [690])
        if score >= .9:
            # Startup recovery captures greyscale frames; the blue-button colour
            # check used to confirm a promotion action cannot recognize them.
            with open_template(ROOT / "assets/emulator-training-upgrade-title.png") as title:
                title_score, _, _ = find_white(image, title, [350], [680])
            if title_score >= .9:
                return x + close.width // 2, y + close.height // 2
    for unit in UNIT_TYPES:
        with open_template(ROOT / f"assets/emulator-training-{unit}-title.png") as title:
            if training_page(image, title):
                return 60, 190
    return None


def wait_training_entry(serial, image, title):
    for attempt in range(5):
        if training_page(image, title) or training_intro_page(image):
            break
        if attempt < 4:
            image = task_screen(serial)
    return image


def wait_training_result(serial, image, title):
    """Wait for a queue or resource dialog without pressing Train again."""
    for attempt in range(5):
        if (resource_more_target(image) is not None
                or (training_page(image, title) and image.getpixel((400, 1900))[1] <= 180)):
            break
        if attempt < 4:
            image = task_screen(serial)
    return image


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
    arrow = open_template(ROOT / "assets/emulator-training-upgrade-arrow.png").convert("RGB")

    def swipe(start, end):
        if not game_foreground(serial):
            raise RuntimeError("当前前台不是目标游戏，已停止")
        adb(serial, "shell", "input", "swipe", str(start), str(TRAIN_UNIT_ROW_Y),
            str(end), str(TRAIN_UNIT_ROW_Y), TRAIN_UNIT_SWIPE_MS)
        frame = task_screen(serial)
        if not training_page(frame, title):
            raise RuntimeError("兵种列表滑动后未确认兵营，已停止")
        return frame

    for _ in range(12):
        before = image
        image = swipe(180, 930)  # Scroll to the lowest tier (content moves right).
        if training_row_stable(before, image):
            break
    else:
        raise RuntimeError("兵种列表未能滑到最低等级端，已停止")
    for _ in range(16):
        target = training_upgrade_target(image, arrow)
        if target:
            return image, target
        before = image
        image = swipe(900, 540)  # Slow, overlapping scans towards higher tiers.
        if training_row_stable(before, image):
            task_tap(serial, *highest_training_target(image))
            image = task_screen(serial)
            if not training_page(image, title):
                raise RuntimeError("选择最高等级后未确认兵营，已停止")
            return image, None
    raise RuntimeError("兵种列表未能滑到最高等级端，已停止")


def training_row_stable(before, after):
    if difference(before.crop(TRAIN_UNIT_ROW_BOX), after.crop(TRAIN_UNIT_ROW_BOX)) <= 2:
        return True
    # Character animation and selection glow affect the whole portrait row.
    # Tier badges and lower white borders remain fixed at a scroll endpoint.
    box = (0, 1500, 1080, 1560)
    left = white_mask(before.crop(box)).resize((270, 15))
    right = white_mask(after.crop(box)).resize((270, 15))
    return difference(left, right) <= 5


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

    def wait_city(image):
        for attempt in range(5):
            if inspect(image.convert("L"), help_template, nav_template)[0]:
                return image
            if attempt < 4:
                image = task_screen(serial)
        city(image)

    image = drawer(initial_image if initial_image is not None else color_screenshot(serial), reset=True)
    units = set(units) if units is not None else {unit for unit, _ in TRAIN_ROWS}
    finishes = []

    def skip_resource_shortage(image, unit):
        target = resource_more_target(image)
        if target is None:
            return None
        task_tap(serial, *target)
        image = task_screen(serial)
        for attempt in range(5):
            if resource_more_target(image) is None:
                break
            if attempt < 4:
                image = task_screen(serial)
        # A failed promotion returns to its confirmation dialog first.
        if not training_page(image, titles[unit]):
            target = training_back_target(image)
            if target is None or target == (60, 190):
                raise RuntimeError("资源不足后未确认晋升弹窗关闭入口，已停止")
            task_tap(serial, *target)
            image = wait_training_entry(serial, task_screen(serial), titles[unit])
        if not training_page(image, titles[unit]):
            raise RuntimeError("关闭资源不足弹窗后未确认训练配置页，已停止")
        task_tap(serial, 60, 190)
        image = drawer(wait_city(task_screen(serial)))
        if timers is not None:
            finishes.append(time.monotonic() + 600)
        print(f"{UNIT_TYPES[unit]}普通资源不足，跳过本次练兵，已安排稍后重新检测", flush=True)
        return image

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
        image = wait_city(task_screen(serial))
        if status == "completed":
            task_tap(serial, 530, 1090)
            image = task_screen(serial)
            image = drawer(image)
            if training_status(image, y) != "idle":
                raise RuntimeError(f"{unit_label}已完成队列领取后未变为空闲，已停止")
            task_tap(serial, 608, y)
            image = wait_city(task_screen(serial))
            print(f"{unit_label}已领取完成的士兵", flush=True)
        task_tap(serial, 725, 1590)
        image = wait_training_entry(serial, task_screen(serial), titles[unit])
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
                image = wait_city(task_screen(serial))
                task_tap(serial, 725, 1590)
                image = wait_training_entry(serial, task_screen(serial), titles[unit])
        if not training_page(image, titles[unit]):
            raise RuntimeError(f"未确认{unit_label}训练配置页，已停止")
        upgraded = False
        if upgrade:
            image, target = scan_training_upgrade(serial, image, titles[unit])
            if target is not None:
                task_tap(serial, *target)
                image = task_screen(serial)
                button = open_template(ROOT / "assets/emulator-training-upgrade-button.png").convert("RGB")
                score, _, _ = find_white(image, button, [928], [1245])
                if not training_page(image, titles[unit]) or score < .9:
                    raise RuntimeError(f"未确认{unit_label}可用晋升入口，已停止")
                task_tap(serial, 975, 1290)
                image = task_screen(serial)
                if not training_upgrade_dialog(image):
                    raise RuntimeError(f"未确认{unit_label}士兵晋升弹窗，已停止")
                task_tap(serial, 775, 1630)  # Blue resource button; yellow spends diamonds.
                image = wait_training_result(serial, task_screen(serial), titles[unit])
                skipped = skip_resource_shortage(image, unit)
                if skipped is not None:
                    image = skipped
                    continue
                if not training_page(image, titles[unit]) or image.getpixel((400, 1900))[1] > 180:
                    raise RuntimeError(f"{unit_label}晋升后未确认队列启动，已停止")
                print(f"{unit_label}已启动兵种升级，晋升至当前最高等级", flush=True)
                upgraded = True
            else:
                print(f"{unit_label}城内暂无可晋升的低级兵，自动训练最高可用等级新兵", flush=True)
        r, g, b = image.getpixel((400, 1900))
        if not upgraded and not (g > 180 and g > r + 100 and g > b + 80):
            raise RuntimeError(f"{unit_label}训练配置不在可启动状态，已停止")
        if not upgraded:
            task_tap(serial, 795, 2100)  # 右侧蓝色普通资源训练按钮；左侧黄色按钮消耗钻石
            image = wait_training_result(serial, task_screen(serial), titles[unit])
            skipped = skip_resource_shortage(image, unit)
            if skipped is not None:
                image = skipped
                continue
            if (not training_page(image, titles[unit])
                    or image.getpixel((400, 1900))[1] > 180):
                raise RuntimeError(f"{unit_label}训练开始后未确认状态变化，已停止")
            print(f"{unit_label}已启动普通资源训练", flush=True)
        task_tap(serial, 60, 190)
        image = wait_city(task_screen(serial))
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
    # emulator-5554 can place the count at y=1553, below its tree title.
    # Include the complete count in both template search and OCR fallback.
    score, x, y = find_green(image, template, range(180, 241, 4), range(680, 1561, 4))
    amount = tree_amount(image, x, y) if score >= .85 else None
    if amount is not None:
        return amount, y
    for text, (_, row_y, _, _) in recognize_text(image.crop((125, 650, 550, 1600))):
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
    lines = [text.strip() for text, _ in recognize_text(image.crop(box))]
    # Pet icons put days above the clock; treating the clock alone loses days.
    for index, line in enumerate(lines[:-1]):
        if re.fullmatch(r"\d+\s*天", line):
            seconds = parse_countdown(line + lines[index + 1])
            if seconds is not None:
                return seconds
    values = [parse_countdown(text) for text in lines]
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
    with open_template(ROOT / "assets/emulator-pet-title.png") as template:
        score, _, _ = minimum_difference(image.convert("L"), template.convert("L"),
                                         PET_TITLE_BOX, range(-2, 3), range(-2, 3))
        return score < 15 or (score <= 30 and any(
            text.replace(" ", "") == "宠物技能" for text, _ in
            recognize_text(image.crop((400, 455, 680, 550)))))


def pet_skill_available(image, target):
    """A locked/unreleased slot is plain panel background, not a skill tile."""
    x, y = target
    pixels = list(image.convert("RGB").crop((x - 65, y - 65, x + 65, y + 65)).getdata())
    icon = sum(max(pixel) - min(pixel) > 40 or max(pixel) < 130 for pixel in pixels)
    return icon > len(pixels) * .2


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
            # Prefix punctuation can become a valid extra digit (e.g. 1 day
            # becomes 11 days). Independently read the selected icon too.
            target = next((target for skill, target in PET_SKILLS.values() if skill == label), None)
            if target is not None:
                x, y = target
                icon_seconds = countdown(image, (x - 100, y + 20, x + 110, y + 110))
                if icon_seconds is not None:
                    seconds = icon_seconds
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
    with open_template(ROOT / "assets/emulator-dawn-reward-title.png") as title:
        if difference(image.crop((380, 680, 700, 790)).convert("L"), title.convert("L")) >= 15:
            return False
    return any("点击任意位置退出" in text.replace(" ", "")
               for text, _ in recognize_text(image.crop((250, 1850, 850, 2280))))


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
        with open_template(ROOT / "assets/emulator-pet-button.png") as template:
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
    if not pet_skill_available(image, target):
        print(f"{label} 当前未显示技能图标，跳过空位，10 分钟后复查", flush=True)
        if timers is not None:
            timers[name] = time.monotonic() + 600
        return image if keep_open else close_pet_page(serial, image, help_template, nav_template)
    task_tap(serial, *target)
    image = task_screen(serial)
    state, seconds = pet_skill_state(image, label)
    if state == "ready":
        task_tap(serial, 540, 1910)
        if name == "pet_transport":
            # Transport stays active after use; continue the batch without
            # reading its post-use state or countdown.
            image = task_screen(serial)
            if timers is not None:
                timers[name] = float("inf")
            print(f"已使用宠物技能：{label}", flush=True)
            return image if keep_open else close_pet_page(serial, image, help_template, nav_template)
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
            timers[name] = float("inf")
        print(f"{label} 生效中，不重复使用，继续下一个技能", flush=True)
    else:
        record_timer(timers, name, seconds, label)
    if not keep_open or state == "unknown":
        image = close_pet_page(serial, image, help_template, nav_template)
    return image


def recruit_row(serial, image, templates, kind):
    image = task_drawer(serial, image, templates)
    for scan in range(2):
        saw_later_row = False
        for attempt in range(DRAWER_SWIPES + 1):
            texts = recognize_text(image.crop((240, 710, 550, 1590)))
            for text, (_, y, _, height) in texts:
                normalized = text.replace(" ", "")
                if RECRUIT_TYPES[kind] in normalized:
                    row_bottom = round(710 + y + height)
                    if row_bottom + 55 <= 1590:
                        return image, row_bottom
            normalized_text = "".join(text.replace(" ", "") for text, _ in texts)
            later_labels = (("史诗招募", "仓库补给", "生命之树", "晨曦回礼")
                            if kind == "recruit_advanced" else
                            ("仓库补给", "生命之树", "晨曦回礼"))
            if any(label in normalized_text for label in later_labels):
                saw_later_row = True
                break
            if attempt < DRAWER_SWIPES:
                image = swipe_drawer(serial, templates["drawer-open"])
        # A previous task may have left the drawer below this recruitment row.
        # Search the current view first; reset only after it has been missed.
        if scan == 0 and saw_later_row and TASK_DRAWER_SCROLL:
            image = reset_drawer(serial, image, templates)
        else:
            break
    return image, None


def recruit_timer(serial, image, templates, timers, kind="recruit_advanced"):
    if timers is None:
        return image
    image, row_bottom = recruit_row(serial, image, templates, kind)
    record_timer(timers, kind, countdown(image, (125, row_bottom, 550, row_bottom + 55))
                 if row_bottom is not None else None)
    return image


def treasure_page(image):
    with open_template(ROOT / "assets/emulator-treasure-title.png") as title:
        # Account variants use a different blue header and slightly shifted text.
        # Match the white title lettering, as for recruit and intelligence pages.
        score = find_white(image, title, [140], [165])[0]
        return score >= .90 or (score >= .80 and header_text(image, "宠物寻宝"))


def treasure_remaining(image):
    if not treasure_page(image):
        return None
    # OCR 可能重复返回末尾数字；拼接所有检测框会把 3 变成 33。
    # 后端已合并同一行的相邻文字，只读取带次数标签的完整行。
    for text, _ in recognize_text(image.crop((280, 270, 800, 390))):
        match = re.fullmatch(r"今日剩余寻宝次数[:：]?(\d+)", text.replace(" ", "").strip())
        if match:
            return int(match[1])
        if re.fullmatch(r"今日剩余寻宝次数[:：]?[OoＯ]", text.replace(" ", "").strip()):
            # The full line can read the zero as O. Require a separate numeric
            # read of its fixed count location instead of substituting a value.
            values = [int(value.strip()) for value, _ in
                      recognize_text(image.crop((680, 285, 755, 365)))
                      if re.fullmatch(r"\d", value.strip())]
            return values[0] if len(values) == 1 else None
    return None


def treasure_dispatch_close(image):
    """Locate the dispatch header and its close icon as the pet list resizes."""
    with open_template(ROOT / "assets/emulator-treasure-dispatch-title.png") as title:
        score, _, y = find_white(image, title, range(420, 441, 4), range(350, 901, 8))
    if score < .90:
        return None
    with open_template(ROOT / "assets/emulator-treasure-dispatch-close.png") as close:
        score, x, y = find_white(image, close, range(960, 981, 4), range(y, y + 11, 2))
        if score >= .94:
            return x + close.width // 2, y + close.height // 2
    return None


def treasure_action_target(image, label):
    # A short pet list moves Start above the old y=1700 crop; taller lists can
    # move it below y=1940. Keep the detail page's separate dispatch button crop.
    box = (300, 1450, 780, 2120) if label == "开始寻宝" else (300, 1700, 780, 1940)
    return intelligence_text_target(image, (label,), box)


def treasure_back_target(image):
    close = treasure_dispatch_close(image)
    if close is not None:
        return close
    if treasure_page(image):
        return 60, 190
    if intelligence_text_target(image, ("联盟宝藏",), (400, 470, 680, 550)):
        return 1000, 510
    if intelligence_text_target(image, ("寻宝完成",), (350, 680, 730, 810)):
        return 960, 640
    if intelligence_text_target(image, ("寻宝中",), (350, 680, 730, 810)):
        return 960, 680
    if intelligence_text_target(image, ("寻宝派遣",), (300, 1700, 780, 1940)):
        return 960, 595
    return None


def treasure_completed_target(image, near=None):
    if not treasure_page(image):
        return None
    with open_template(ROOT / "assets/emulator-treasure-completed.png") as badge:
        x_range = (range(0, image.width - badge.width + 1, 8) if near is None else
                   range(max(0, near[0] - badge.width // 2 - 20), near[0] - badge.width // 2 + 21, 4))
        y_range = (range(430, 1851, 8) if near is None else
                   range(max(0, near[1] - badge.height // 2 - 20), near[1] - badge.height // 2 + 21, 4))
        score, x, y = find_green(image, badge, x_range, y_range)
        if score >= .90:
            return x + badge.width // 2, y + badge.height // 2
    return None


def treasure_chest_target(image, excluded=()):
    if not treasure_page(image):
        return None
    gray = image.convert("L")
    for x, y in excluded:
        gray.paste(0, (max(0, x - 100), max(0, y - 95), min(1080, x + 105), min(2340, y + 110)))
    # 倒计时位于正在寻宝宝箱下方；遮住其本体，避免点进无派遣按钮的弹窗。
    for text, (x, y, width, _) in recognize_text(image.crop((0, 430, 1080, 2050))):
        if parse_countdown(text) is not None:
            gray.paste(0, (max(0, round(x - 100)), round(430 + y - 270),
                           min(1080, round(x + width + 100)), round(430 + y - 60)))
    small = gray.resize((270, 585))
    # 红色稀有宝箱使用现有 orange 模板，按品质优先而非匹配分数选择。
    for name in ("chest-orange", "chest-purple", "chest"):
        with open_template(ROOT / f"assets/emulator-treasure-{name}.png") as chest:
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


def treasure_share_target(image):
    """Match the sampled alliance treasure share button inside the claim dialog."""
    with open_template(ROOT / "assets/emulator-treasure-share.png") as button:
        score, dx, dy = minimum_difference(
            image.convert("L"), button.convert("L"), (810, 1720, 895, 1810),
            range(-20, 21), range(-160, 161))
        if score <= 12:
            return 852 + dx, 1765 + dy
    return None


def share_treasure(serial, image):
    """Share a claimed alliance chest before closing its completion dialog."""
    share = treasure_share_target(image)
    if share is None:
        return image
    task_tap(serial, *share)
    for _ in range(5):
        image = task_screen(serial)
        if (intelligence_text_target(image, ("寻宝完成",), (350, 680, 730, 810))
                and treasure_share_target(image) is None):
            print("已分享宠物寻宝宝藏", flush=True)
            return image
    raise RuntimeError("宝藏分享后未确认分享按钮消失")


def treasure_pets_unavailable(image):
    if treasure_dispatch_close(image) is None:
        return False
    texts = "".join(re.sub(r"\s+", "", text) for text, _ in
                    recognize_text(image.crop((70, 800, 1010, 2120))))
    # Six visible pets are all blocked; do not treat a selected weak pet as
    # evidence that the other pets cannot go, or retry an impossible mission.
    return ("无法进行本次寻宝" in texts
            and len(re.findall("实力不足|寻宝中", texts)) >= 6)


def close_treasure_to_map(serial, image):
    for _ in range(5):
        if treasure_page(image):
            return image
        target = treasure_back_target(image)
        if target is None:
            raise RuntimeError("无法确认寻宝弹窗退出按钮")
        task_tap(serial, *target)
        image = task_screen(serial)
    raise RuntimeError("关闭寻宝弹窗后未确认寻宝地图")


def dispatch_treasure(serial, target):
    task_tap(serial, *target)
    for label in ("寻宝派遣", "开始寻宝"):
        for _ in range(5):
            image = task_screen(serial)
            if stamina_refill_target(image) is not None:
                return leave_treasure_stamina(serial, image), "stamina"
            if label == "寻宝派遣" and intelligence_text_target(
                    image, ("寻宝中",), (350, 680, 730, 810)) is not None:
                print("该藏宝点正在寻宝，关闭详情并检查其他藏宝点", flush=True)
                return close_treasure_to_map(serial, image), "unavailable"
            if label == "开始寻宝" and treasure_pets_unavailable(image):
                print("该藏宝点的宠物实力不足或宠物忙碌，尝试其他藏宝点", flush=True)
                return close_treasure_to_map(serial, image), "unavailable"
            action = treasure_action_target(image, label)
            if action is not None:
                break
        else:
            raise RuntimeError(f"宠物寻宝未确认{label}按钮")
        task_tap(serial, *action)
    return image, None


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
        if intelligence_text_target(image, ("点击任意位置退出",), (250, 1850, 850, 2280)):
            task_tap(serial, 540, 2100)
            break
    else:
        raise RuntimeError("领取寻宝宝箱后未确认奖励页面")
    for _ in range(5):
        image = task_screen(serial)
        if intelligence_text_target(image, ("寻宝完成",), (350, 680, 730, 810)):
            image = share_treasure(serial, image)
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
    switched_tab = False
    for _ in range(5):
        image = task_screen(serial)
        if intelligence_text_target(image, ("联盟宝藏",), (400, 470, 680, 550)):
            # 保留按钮两侧背景，避免 Windows OCR 把开头的“一”漏掉。
            target = intelligence_text_target(image, ("一键领取",), (250, 1790, 830, 1990))
            if target is not None:
                break
            # Newly earned personal chests open "我的分享" by default. The
            # notification can mean an unshared chest, not claimable rewards.
            if not switched_tab:
                tab = intelligence_text_target(image, ("盟友分享",), (550, 570, 1000, 700))
                if tab is not None:
                    task_tap(serial, *tab)
                    switched_tab = True
    else:
        raise RuntimeError("联盟宝藏未确认一键领取按钮")
    task_tap(serial, *target)
    for _ in range(10):
        image = task_screen(serial)
        if intelligence_text_target(image, ("点击任意位置退出",), (250, 1850, 850, 2280)):
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


def leave_treasure(serial, help_template, nav_template, templates):
    task_tap(serial, 60, 190)
    for _ in range(5):
        image = task_screen(serial)
        if inspect(image.convert("L"), help_template, nav_template)[0]:
            return task_drawer(serial, image, templates)
    raise RuntimeError("宠物寻宝结束后未确认返回主城")


def leave_treasure_stamina(serial, image):
    close = stamina_refill_target(image)
    if close is None:
        raise RuntimeError("未确认寻宝体力不足弹窗")
    task_tap(serial, *close)
    for _ in range(5):
        image = task_screen(serial)
        if treasure_page(image):
            print("体力不足，跳过本轮宠物寻宝，等待体力恢复", flush=True)
            return image
        target = treasure_back_target(image)
        if target is not None:
            task_tap(serial, *target)
    raise RuntimeError("关闭体力弹窗后未返回宠物寻宝页面")


def run_treasure(serial, help_template, nav_template, drawer_template):
    image = color_screenshot(serial)
    if not inspect(image.convert("L"), help_template, nav_template)[0]:
        raise RuntimeError("宠物寻宝检查前未确认主城")
    templates = {"drawer-open": drawer_template}
    image = task_drawer(serial, image, templates, reset_unknown=False)
    target = None
    completed = False
    own_ready = False
    # 低等级账号的寻宝行位于抽屉底部，状态文字可延伸到 y=1570。
    # 原来的 y=1470 边界会裁掉整行，即使列表已滚到底也无法识别。
    drawer_box = (240, 710, 550, 1590)
    for attempt in range(DRAWER_SWIPES + 2):
        texts = recognize_text(image.crop(drawer_box))
        row_bottom = next((round(drawer_box[1] + y + height) for text, (_, y, _, height) in texts
                           if text.replace(" ", "") == "宠物寻宝"), None)
        if row_bottom is not None and row_bottom + 55 <= drawer_box[3]:
            completed = intelligence_text_target(
                image, ("已完成",), (240, row_bottom, 550, row_bottom + 55)) is not None
            own_ready = completed or intelligence_text_target(
                image, ("可寻宝",), (240, row_bottom, 550, row_bottom + 55)) is not None
            # Shared alliance rewards can be ready while our pets are busy.
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
        print("抽屉里没有宠物寻宝任务行，跳过宠物寻宝", flush=True)
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
    if not own_ready and treasure_completed_target(image) is None:
        print("已检查联盟宝藏；自身宠物暂无可寻宝或已完成提示，跳过派遣", flush=True)
        return leave_treasure(serial, help_template, nav_template, templates)
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
        rejected = []
        for candidate in range(6):
            for attempt in range(5):
                target = (treasure_chest_target(image, rejected) if rejected
                          else treasure_chest_target(image))
                if target is not None:
                    break
                if attempt < 4:
                    image = task_screen(serial)
            else:
                if rejected:
                    print("当前没有其他符合宠物实力条件的藏宝点，稍后重新检查", flush=True)
                    return leave_treasure(serial, help_template, nav_template, templates)
                raise RuntimeError("宠物寻宝页面未找到宝箱图标")
            image, notice = dispatch_treasure(serial, target)
            if notice == "stamina":
                return leave_treasure(serial, help_template, nav_template, templates)
            if notice == "unavailable":
                rejected.append(target)
                continue
            for _ in range(5):
                image = task_screen(serial)
                if stamina_refill_target(image) is not None:
                    leave_treasure_stamina(serial, image)
                    return leave_treasure(serial, help_template, nav_template, templates)
                after = treasure_remaining(image)
                if after is not None and after < remaining:
                    break
                if intelligence_text_target(image, ("寻宝中",), (350, 680, 730, 810)):
                    task_tap(serial, 960, 680)
            else:
                raise RuntimeError("开始寻宝后未确认今日剩余次数减少")
            print(f"已开始宠物寻宝，今日剩余次数 {remaining} → {after}", flush=True)
            break
        else:
            print("已检查六个藏宝点，当前均无可派遣宠物，稍后重新检查", flush=True)
    else:
        print("今日剩余寻宝次数为 0，跳过派遣", flush=True)
    return leave_treasure(serial, help_template, nav_template, templates)


def island_page(image, template, *, use_ocr=True):
    # Only match title lettering: the island scenery behind it changes with camera position.
    crop = image.crop((120, 140, 310, 230)).convert("L")
    if (difference(crop, template) <= 10
            or difference(white_mask(crop), white_mask(template)) <= 10):
        return True
    if not use_ocr:
        return False
    return any("我的海岛" in text.replace(" ", "")
               for text, _ in recognize_text(image.crop((100, 140, 370, 235))))


def crystal_target(image, template, near=None):
    scene = scene_view(image)
    if scene is not None:
        unwarped, to_logical, from_logical = scene
        target = crystal_target(unwarped, template, near=from_logical(*near) if near else None)
        return to_logical(*target) if target is not None else None
    tutorial = open_template(ROOT / "assets/emulator-crystal-claim-tutorial.png").convert("RGB")
    nox_tutorial = open_template(ROOT / "assets/emulator-crystal-claim-nox-tutorial.png").convert("RGB")
    best = None
    for candidate, limit in ((template, .82), (tutorial, .88), (nox_tutorial, .90)):
        x_range = range(80, 941, 8) if near is None else range(max(0, near[0] - 80), near[0] + 40, 4)
        y_range = range(300, 1901, 8) if near is None else range(max(0, near[1] - 80), near[1] + 40, 4)
        # Ponds and foliage can match the small green silhouette after a claim.
        # Require the sampled icon colours/white backing as well; otherwise a
        # successful collection keeps clicking unrelated island decorations.
        reference = candidate.convert("RGB")
        colour_limit = 30 if candidate is nox_tutorial else 55
        def matches_colours(x, y):
            crop = image.crop((x, y, x + candidate.width, y + candidate.height)).convert("RGB")
            channels = ImageStat.Stat(ImageChops.difference(crop, reference)).mean
            return sum(channels) / len(channels) <= colour_limit
        score, x, y = matched_mask(image, candidate, x_range, y_range, green_mask,
                                   minimum_score=limit, candidate_filter=matches_colours)
        if score >= limit and (best is None or score > best[0]):
            best = (score, x + candidate.width // 2, y + candidate.height // 2)
    return best[1:] if best else None


def selected_tree_collect_target(image):
    """The small Nox account's tutorial selects the tree and exposes its action menu."""
    pixels = image.crop((760, 1340, 850, 1430)).convert("RGB")
    colours = list(pixels.getdata())
    if sum(b > r + 50 and b > g + 15 for r, g, b in colours) < len(colours) * .2:
        return None
    if not any("生命之树" in text.replace(" ", "") for text, _ in
               recognize_text(image.crop((300, 850, 750, 1050)))):
        return None
    for text, (x, y, width, height) in recognize_text(image.crop((700, 1320, 1000, 1610))):
        if text.replace(" ", "") == "收集":
            return round(700 + x + width / 2), round(1320 + y - 65)
    return None


def collect_crystals(serial, image, island_template):
    template = open_template(ROOT / "assets/emulator-crystal-claim.png").convert("RGB")
    collected = 0
    for _ in range(30):
        target = None
        empty_frames = 0
        for attempt in range(8):
            if island_page(image, island_template):
                target = crystal_target(image, template) or selected_tree_collect_target(image)
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
        for click_attempt in range(3):
            task_tap(serial, *target)
            absent_frames = 0
            for attempt in range(8):
                image = task_screen(serial)
                if not island_page(image, island_template):
                    absent_frames = 0
                    continue
                remaining_target = (crystal_target(image, template, near=target)
                                    or selected_tree_collect_target(image))
                if remaining_target is None:
                    absent_frames += 1
                else:
                    absent_frames = 0
                    target = remaining_target
                if absent_frames >= 2:
                    collected += 1
                    break
            if absent_frames >= 2:
                break
        else:
            raise RuntimeError("海岛结晶点击后领取图标仍未消失")
    raise RuntimeError("海岛连续领取 30 处后仍有图标，请检查识别结果")


def wait_city_after_island(serial, help_template, nav_template):
    confirmations = 0
    # Returning from the island can leave a blank city HUD for over 8 seconds.
    # Keep waiting for the real navigation bar and two consecutive city frames.
    for _ in range(30):
        image = task_screen(serial)
        city, _, nav_difference = inspect(image.convert("L"), help_template, nav_template)
        confirmations = confirmations + 1 if city else 0
        if confirmations >= 2:
            return image
    error = RuntimeError(f"领取结晶后返回主城超时；底栏差异 {nav_difference:.1f}")
    error.diagnostic_key = "领取结晶后返回主城超时"
    raise error


def cleanup_diagnostic_screenshots():
    """Expire generated screenshots after seven days, including the legacy layout."""
    artifacts = ROOT / "artifacts"
    directory = artifacts / DIAGNOSTIC_SCREENSHOT_DIR
    cutoff = time.time() - DIAGNOSTIC_RETENTION_SECONDS
    removed = 0
    paths = list(artifacts.glob("*.png"))
    date_directories = []
    device_directories = []
    for device_directory in directory.glob("*"):
        if not device_directory.is_dir() or device_directory.is_symlink():
            continue
        device_directories.append(device_directory)
        for date_directory in device_directory.glob("*"):
            if (date_directory.is_dir() and not date_directory.is_symlink()
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_directory.name)):
                date_directories.append(date_directory)
                paths.extend(date_directory.glob("*.png"))
    for path in paths:
        if path.is_symlink() or not DIAGNOSTIC_FILENAME.fullmatch(path.name):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                # Bounty diagnostics keep structured context beside the PNG.
                path.with_suffix(".json").unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            continue
    # Remove empty date/device folders; fixtures and folders with other files stay.
    for folder in date_directories + device_directories:
        try:
            if not any(folder.iterdir()):
                folder.rmdir()
        except FileNotFoundError:
            # Another emulator process may have cleaned the same empty folder.
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
        print(f"清理故障截图失败：{exc}", flush=True)


def parse_tree_amount(text):
    text = re.sub(r"^\s*可收集\s*", "", text).replace("，", ",")
    match = re.match(r"\s*(\d{1,2},\d{3}|\d{1,5})\s*/", text)
    return int(match[1].replace(",", "")) if match else None


def warehouse_position(image):
    """Locate the facade, which remains visible under both reward icons and timers."""
    scene = scene_view(image)
    if scene is not None:
        unwarped, to_logical, _ = scene
        offset = warehouse_position(unwarped)
        if offset is None:
            return None
        x, y = to_logical(260 + offset[0], 1330 + offset[1])
        return x - 260, y - 1330
    template = open_template(ROOT / "assets/emulator-warehouse-front.png").convert("L")
    gray = image.convert("L")
    small = gray.resize((270, 585))
    target = template.resize((40, 30))

    def matcher(reference):
        stats = ImageStat.Stat(reference)
        mean, variance = stats.mean[0], stats.var[0]
        pixels = reference.width * reference.height

        def correlation(crop):
            current = ImageStat.Stat(crop)
            if current.var[0] < 1 or variance < 1:
                return 0
            # Squared absolute difference gives the dot product without a
            # NumPy/OpenCV dependency, and NCC ignores lighting gain/offset.
            squared_difference = ImageStat.Stat(ImageChops.difference(crop, reference)).sum2[0]
            product = (current.sum2[0] + stats.sum2[0] - squared_difference) / (2 * pixels)
            return (product - current.mean[0] * mean) / (current.var[0] * variance) ** .5

        return correlation

    coarse = matcher(target)
    _, x, y = max((coarse(small.crop((x, y, x + 40, y + 30))), x, y)
                  for y in range(150, 481, 4) for x in range(10, 211, 4))
    precise = matcher(template)
    score, x, y = max((precise(gray.crop((xx, yy, xx + 160, yy + 120))), xx, yy)
                      for yy in range(y * 4 - 32, y * 4 + 33, 2)
                      for xx in range(x * 4 - 32, x * 4 + 33, 2))
    score, x, y = max((precise(gray.crop((xx, yy, xx + 160, yy + 120))), xx, yy)
                      for yy in range(y - 2, y + 3) for xx in range(x - 2, x + 3))
    facade = (x - 260, y - 1330) if score >= .78 else None
    # Upgrading adds scaffolding across the facade. The distinctive roof vent
    # stays exposed; use its calibrated position with a stricter correlation.
    import cv2
    import numpy as np
    vent = open_template(ROOT / "assets/emulator-warehouse-roof-vent.png").convert("L")
    pixels = np.asarray(gray.crop((40, 350, 1040, 2050)))
    # Both crops come from midday-world-scene.png: the vent is 244px right
    # and 71px above the facade origin. Keep that shared calibration at every
    # scale, and use one anchor whether scaffolding hides the facade or not.
    candidates = []
    for factor in (.9, 1., 1.04, 1.1, 1.14):
        scaled = vent.resize((round(vent.width * factor), round(vent.height * factor)), Image.Resampling.LANCZOS)
        scores = cv2.matchTemplate(pixels, np.asarray(scaled), cv2.TM_CCOEFF_NORMED)
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        candidates.append((score, factor, x, y))
    score, factor, x, y = max(candidates)
    if score >= .85:
        return (round(x + 40 - 244 * factor - 260),
                round(y + 350 + 71 * factor - 1330))
    return facade


def stamina_warehouse_visible(offset):
    """The whole can search area must avoid city HUD and right-side activities."""
    if offset is None:
        return False
    dx, dy = offset
    return 80 <= 430 + dx and 636 + dx <= 900 and 350 <= 930 + dy and 1211 + dy <= 1800


def research_drawer_target(image):
    """Locate the research row's arrow independently of construction queues."""
    for text, (_, y, _, height) in recognize_text(image.crop((20, 720, 570, 1500))):
        if text.replace(" ", "") == "科技研究":
            arrow_y = round(720 + y + height + 80)
            if arrow_y <= 1540:
                return 608, arrow_y
    return None


def stamina_map_stationary(before, after):
    """Separate local guide/snow/effect animation from camera displacement."""
    import cv2
    import numpy as np
    left = np.asarray(before, dtype=np.float32)
    right = np.asarray(after, dtype=np.float32)
    # Flat or unrelated frames cannot establish a stationary textured map.
    if min(float(left.std()), float(right.std())) < 10:
        return False
    (dx, dy), response = cv2.phaseCorrelate(left, right)
    # Samples are downscaled fivefold: .2px corresponds to one screen pixel.
    return response >= .8 and max(abs(dx), abs(dy)) <= .2


def stamina_map_screen(serial, help_template, nav_template, initial_image):
    """Wait for camera motion to stop before locating or dragging the map again."""
    image = initial_image
    stationary_pairs = 0
    for _ in range(5):
        before = image.crop((80, 350, 900, 1800)).convert("L").resize((164, 290))
        image = task_screen(serial, help_enabled=False)
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("体力罐头地图移动后未确认主城")
        after = image.crop((80, 350, 900, 1800)).convert("L").resize((164, 290))
        if difference(before, after) <= 3:
            return image
        stationary_pairs = stationary_pairs + 1 if stamina_map_stationary(before, after) else 0
        if stationary_pairs >= 2:
            return image
    raise RuntimeError("体力罐头地图移动未稳定，未开始检测罐头")


def warehouse_countdown(image, known_warehouse=False, offset=None):
    if offset is None:
        roof = open_template(ROOT / "assets/emulator-warehouse-roof.png").convert("L")
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
             recognize_text(image.crop((330, 1850, 800, 2180)))]
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
    with open_template(ROOT / "assets/emulator-can-modal-title.png") as title:
        if difference(image.crop((390, 1390, 690, 1480)).convert("L"), title.convert("L")) <= 15:
            return True
    return hunting.button_target(sys.modules[__name__], image, ("丰盛的招待",),
                                 (340, 1280, 760, 1540)) is not None


def stamina_countdown(image):
    # The white timer on the warehouse is its package timer. Only a timer
    # explicitly shown on the can page may schedule the can task.
    if not stamina_page(image):
        return None
    return labelled_countdown(image, (300, 1480, 830, 1800),
                              ("剩余时间", "距离下次领取", "下次可领取", "下次领取",
                               "距离下次招待", "下次招待"))


def stamina_target(image, offset=None):
    scene = scene_view(image)
    if scene is not None:
        unwarped, to_logical, from_logical = scene
        if offset is not None:
            x, y = from_logical(260 + offset[0], 1330 + offset[1])
            offset = x - 260, y - 1330
        target = stamina_target(unwarped, offset)
        return to_logical(*target) if target is not None else None
    template = open_template(ROOT / "assets/emulator-can-icon.png").convert("L")
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
    import cv2
    import numpy as np
    # Some historical city views were zoomed ten percent closer. Match that
    # render size without weakening the confidence or adding non-Nox assets.
    scaled = template.resize((round(template.width * 1.1), round(template.height * 1.1)), Image.Resampling.LANCZOS)
    left, top = max(0, min(x_range)), max(0, min(y_range))
    right = min(image.width, max(x_range) + scaled.width + 1)
    bottom = min(image.height, max(y_range) + scaled.height + 1)
    region = np.asarray(image.crop((left, top, right, bottom)).convert("L"))
    if region.shape[0] >= scaled.height and region.shape[1] >= scaled.width:
        scores = cv2.matchTemplate(region, np.asarray(scaled), cv2.TM_CCOEFF_NORMED)
        _, confidence, _, (x, y) = cv2.minMaxLoc(scores)
        if confidence >= .93:
            return left + x + scaled.width // 2, top + y + scaled.height // 2
    return None


def reward_row(image, template, label):
    # Accounts with fewer unlocked tasks end with the warehouse at y=1492.
    # Scrolling cannot lift that last row above the old 1470px OCR cutoff.
    match = find_white(image, template, range(240, 351, 5), range(730, 1550, 5))
    if match[0] >= .85:
        return match
    # Dimmed drawer labels vary with the city behind the translucent panel.
    for text, (x, y, _, _) in recognize_text(image.crop((240, 730, 550, 1590))):
        if text.replace(" ", "") == label:
            return 1.0, round(240 + x), round(730 + y)
    return match


def run_stamina(serial, help_template, nav_template, drawer_template, timers=None, initial_image=None):
    # Drawer helpers also sample with help enabled. Suspend help for the whole
    # navigation/detection transaction so a chat return cannot move the camera.
    global TASK_HELP
    previous_help = TASK_HELP
    TASK_HELP = None
    try:
        return _run_stamina(serial, help_template, nav_template, drawer_template, timers, initial_image)
    finally:
        TASK_HELP = previous_help


def _run_stamina(serial, help_template, nav_template, drawer_template, timers=None, initial_image=None):
    try:
        saved_refresh = load_refresh(ROOT, serial)
    except (OSError, ValueError) as exc:
        print(f"体力罐头本地时间读取失败，按原逻辑检测：{exc}", flush=True)
        saved_refresh = None
    if saved_refresh is not None:
        finish = datetime.fromtimestamp(saved_refresh)
        print(f"体力罐头本地刷新时间 {finish:%Y-%m-%d %H:%M:%S}，先检查实际界面", flush=True)

    def remember(seconds, observed_at):
        # Commit before claiming so an interrupted process can resume the wait.
        refresh = save_refresh(ROOT, serial, observed_at, seconds)
        if timers is not None:
            timers["stamina"] = time.monotonic() + max(1, refresh - time.time())
        print(f"体力罐头剩余时间 {seconds} 秒，本地刷新时间已保存："
              f"{datetime.fromtimestamp(refresh):%Y-%m-%d %H:%M:%S}", flush=True)

    def city(image):
        if not inspect(image.convert("L"), help_template, nav_template)[0]:
            raise RuntimeError("体力罐头流程未确认主城，已停止")

    def position(image, offset):
        # Bring the entire can search area clear of activity buttons and HUD.
        for _ in range(3):
            if stamina_warehouse_visible(offset) or offset is None:
                break
            if not game_foreground(serial):
                raise RuntimeError("当前前台不是目标游戏，已停止")
            dx, dy = offset
            move_x = max(-300, min(300, round(-dx / 1.5)))
            move_y = max(-250, min(250, round((-200 - dy) / 1.5)))
            adb(serial, "shell", "input", "swipe", "500", "950",
                str(500 + move_x), str(950 + move_y), "1500")
            image = stamina_map_screen(serial, help_template, nav_template, image)
            offset = warehouse_position(image)
        return image, offset

    image = initial_image if initial_image is not None else color_screenshot(serial)
    stamina_timer_recorded = False
    city(image)
    templates = {"drawer-open": drawer_template}
    if task_page(image, templates, "drawer-open"):
        task_tap(serial, 695, 1100)
        image = task_screen(serial, help_enabled=False)
        city(image)
    offset = warehouse_position(image)
    if offset is None:
        # Locate the research building from its drawer arrow before moving
        # the view towards the warehouse at about ten o'clock.
        image = reset_drawer(serial, image, templates)
        city(image)
        for attempt in range(DRAWER_SWIPES + 1):
            target = research_drawer_target(image)
            if target is not None:
                break
            if attempt < DRAWER_SWIPES:
                image = swipe_drawer(serial, drawer_template)
                city(image)
        else:
            raise RuntimeError("体力罐头检查前未能定位科技研究右侧箭头")
        task_tap(serial, *target)
        for _ in range(5):
            image = task_screen(serial, help_enabled=False)
            city(image)
            if not task_page(image, templates, "drawer-open"):
                break
        else:
            raise RuntimeError("体力罐头检查前未能通过科技研究定位科研所")
        image = stamina_map_screen(serial, help_template, nav_template, image)
        offset = warehouse_position(image)
        for _ in range(5):
            if offset is not None:
                break
            if not game_foreground(serial):
                raise RuntimeError("当前前台不是目标游戏，已停止")
            # Short, slow drags avoid the long fling that skips the warehouse.
            adb(serial, "shell", "input", "swipe", "300", "700", "600", "875", "1500")
            image = stamina_map_screen(serial, help_template, nav_template, image)
            offset = warehouse_position(image)
        if offset is None:
            raise RuntimeError("体力罐头检查前未能定位仓库，未开始检测罐头")
    image, offset = position(image, offset)
    if offset is None:
        raise RuntimeError("体力罐头调整视角后未能确认仓库，未开始检测罐头")
    if not stamina_warehouse_visible(offset):
        raise RuntimeError("体力罐头仓库上方区域被遮挡，未开始检测罐头")
    # 等待补给奖励退出、地图移动和图标切换动画。
    for attempt in range(5):
        city(image)
        target = stamina_target(image, offset)
        if target is not None:
            task_tap(serial, *target)
            button = open_template(ROOT / "assets/emulator-can-button.png").convert("L")
            for _ in range(5):
                image = task_screen(serial, help_enabled=False)
                observed_at = time.time()
                if stamina_page(image):
                    break
            if not stamina_page(image):
                raise RuntimeError("未确认罐头领取页面，已停止")
            available_seconds = stamina_countdown(image)
            if available_seconds is None:
                raise RuntimeError("体力罐头剩余时间未识别，未领取，等待重试")
            remember(available_seconds, observed_at)
            stamina_timer_recorded = True
            if difference(image.crop((350, 1620, 735, 1760)).convert("L"), button) > 20:
                task_tap(serial, 920, 550)  # Confirmed can page's close button.
                for _ in range(5):
                    image = task_screen(serial, help_enabled=False)
                    if inspect(image.convert("L"), help_template, nav_template)[0]:
                        return image
                raise RuntimeError("关闭罐头页面后未确认主城")
            task_tap(serial, 530, 1700)
            for _ in range(5):
                image = task_screen(serial, help_enabled=False)
                if (inspect(image.convert("L"), help_template, nav_template)[0]
                        and stamina_target(image, warehouse_position(image)) is None):
                    break
            else:
                raise RuntimeError("罐头领取后未确认返回主城，已停止")
            print("已领取体力罐头", flush=True)
            break
        if attempt < 4:
            image = task_screen(serial, help_enabled=False)
            city(image)
            offset = warehouse_position(image)
            if not stamina_warehouse_visible(offset):
                # Snow/lighting or a late camera animation can hide the
                # facade in one frame. Reconfirm a settled map before failing.
                image = stamina_map_screen(serial, help_template, nav_template, image)
                offset = warehouse_position(image)
                image, offset = position(image, offset)
                if not stamina_warehouse_visible(offset):
                    raise RuntimeError("体力罐头检测期间仓库视角发生变化，未完成检测")
    else:
        print("当前主城画面未发现体力罐头图标", flush=True)
        remaining = saved_refresh - time.time() if saved_refresh is not None else 0
        if remaining > 0 and timers is not None:
            timers["stamina"] = time.monotonic() + remaining
            stamina_timer_recorded = True
            print(f"体力罐头尚未刷新，等待本地记录时间："
                  f"{datetime.fromtimestamp(saved_refresh):%Y-%m-%d %H:%M:%S}", flush=True)
    if not stamina_timer_recorded:
        record_timer(timers, "stamina", None)
    return image


def dawn_claim_page(image):
    title = open_template(ROOT / "assets/emulator-dawn-title.png").convert("L")
    crop = image.crop((405, 520, 660, 630)).convert("L")
    button = open_template(ROOT / "assets/emulator-dawn-button.png").convert("L")
    # The translucent title background changes with the city behind the panel.
    title_matches = (difference(crop, title) <= 15
                     or difference(white_mask(crop), white_mask(title)) <= 10)
    return title_matches and difference(
        image.crop((360, 1580, 730, 1730)).convert("L"), button) <= 20


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
                        open_template(ROOT / "assets/emulator-dawn-row-title.png").convert("RGB")
                        if kind == "dawn" else None)
        # Detect the requested row on each frame. A fixed swipe count can stop
        # above it, or scroll past it when resuming another drawer task.
        for attempt in range(DRAWER_SWIPES + 1):
            texts = recognize_text(image.crop((240, 730, 550, 1590)))
            normalized_text = "".join(text.replace(" ", "") for text, _ in texts)
            if kind == "tree":
                if tree_drawer_amount(image, tree_template)[0] is not None:
                    return image, True
                visible = label in normalized_text
            else:
                visible = reward_row(image, row_template, label)[0] >= .85
            # The tree title may be visible while its count is clipped by the
            # drawer's bottom edge. Scroll until the count can be read too.
            if visible and (kind != "tree" or attempt == DRAWER_SWIPES):
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
    elif warehouse and score >= .85:
        print("仓库补给尚不可领取", flush=True)
    elif warehouse:
        print("未定位到仓库补给任务行，稍后重新检测", flush=True)

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
        image = island_help.run(sys.modules[__name__], serial, image, island_template)
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
        print(f"生命之树可收集数量 {amount}，未达到阈值 {tree_threshold}，跳过", flush=True)

    if dawn and drawer_kind != "dawn":
        image, drawer_visible = bottom_drawer(image, "dawn")
        drawer_kind = "dawn"
    if dawn:
        dawn_template = open_template(ROOT / "assets/emulator-dawn-row-title.png").convert("RGB")
        score, _, dawn_y = (reward_row(image, dawn_template, "晨曦回礼")
                            if drawer_visible else (0, 0, 0))
    if dawn and score >= 0.85 and training_status(image, dawn_y + 50) == "completed":
        task_tap(serial, 608, dawn_y + 50)
        image = task_screen(serial)
        if not dawn_claim_page(image):
            raise RuntimeError("未确认晨曦回礼领取页面，已停止")
        task_tap(serial, 530, 1650)
        image = task_screen(serial)
        reward_box = (380, 680, 700, 790)
        reward = open_template(ROOT / "assets/emulator-dawn-reward-title.png").convert("L")
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
        check_stop()
        if retry_pending() and paused_at is None:
            return False
        maybe_cleanup_diagnostic_screenshots()
        image = screenshot(serial)
        offline = forced_offline(image, text_template, reconnect_template)
        if not offline and paused_at is None and city_relocation_target(image) is not None:
            if dry_run:
                print("只观察模式，检测到系统自动迁城提示", flush=True)
                return
            if return_to_city(serial, help_template, nav_template, text_template, reconnect_template):
                if return_on_reconnect:
                    return True
            continue
        if not offline and paused_at is None and network_reconnect_target(image) is not None:
            if dry_run:
                print("只观察模式，检测到无网络弹窗", flush=True)
                return
            if not return_to_city(serial, help_template, nav_template, text_template, reconnect_template):
                continue
            if return_on_reconnect:
                return True
            continue
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
            target = social_back_target(image) if not dry_run else None
            if target is not None:
                task_tap(serial, *target)
                not_city = 0
                print("监控期间已关闭聊天或领主点赞页面，继续确认主城", flush=True)
                time.sleep(interval)
                continue
            not_city += 1
            print(f"未确认主城 ({not_city}/5)，底栏差异 {nav:.1f}", flush=True)
            if not_city >= 5:
                raise RuntimeError("连续五次未确认主城，已停止")
        else:
            not_city = 0
            if not dry_run and allow_wilderness and WILDERNESS_HEALING is not None:
                image = check_wilderness_healing(serial, color_screenshot(serial)).convert("L")
                city, match, nav = inspect(image, help_template, nav_template, allow_wilderness=True)
                # Healing can return a fresh popup/loading frame after the
                # initial city check. Reinspect it on the next monitor pass.
                if not city:
                    time.sleep(interval)
                    continue
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
    drawer = open_template(ROOT / "assets/emulator-drawer-open.png").convert("RGB")
    island = open_template(ROOT / "assets/emulator-island-title.png").convert("L")
    pages = {name: open_template(ROOT / f"assets/emulator-{name}.png").convert("RGB")
             for name in ("tech-detail", "tech-title", "alliance-title", "recruit-title", "reward-title")}
    titles = [open_template(ROOT / f"assets/emulator-training-{unit}-title.png").convert("L")
              for unit, _ in TRAIN_ROWS]
    dawn = open_template(ROOT / "assets/emulator-dawn-title.png").convert("L")
    for _ in range(6):
        if (network_reconnect_target(image) is not None
                or city_relocation_target(image) is not None):
            break
        if gather_search_page(image):
            image = leave_gather(serial, image)
        if inspect(image.convert("L"), help_template, nav_template)[0]:
            return (reset_drawer(serial, image, {"drawer-open": drawer})
                    if task_page(image, {"drawer-open": drawer}, "drawer-open") else image)
        if forced_offline(image.convert("L"), text_template, reconnect_template):
            raise RuntimeError("重试前检测到强制下线")
        if island_page(image, island, use_ocr=False):
            target = (60, 190)
        elif (target := treasure_dispatch_close(image)) is not None:
            pass
        elif (target := social_back_target(image)) is not None:
            pass
        elif intelligence_page(image):
            target = (60, 190)
        elif treasure_page(image):
            target = (60, 190)
        elif (target := healing_back_target(image)) is not None:
            pass
        elif town_button(image):
            target = (960, 2230)
        elif (target := alliance_back_target(image)) is not None:
            pass
        elif (target := explore_back_target(image)) is not None:
            pass
        elif (target := resource_more_target(image)) is not None:
            pass
        elif (target := training_back_target(image)) is not None:
            pass
        elif pet_page(image):
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
            if task_rewards.page(sys.modules[__name__], image) is None:
                break
            target = (1030, 460)
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


def retry_once(label, operation, serial, text_template, reconnect_template, recover=None, on_retry=None):
    from gui_web import StartupGuideBlocked
    for attempt in range(4):
        check_stop()
        try:
            if attempt:
                if recover is not None:
                    print(f"{label} 第 {attempt} 次重试：正在恢复主城", flush=True)
                    recover()
                    print(f"{label} 第 {attempt} 次重试：主城恢复完成，开始重试", flush=True)
                else:
                    print(f"{label} 第 {attempt} 次重试：开始重试", flush=True)
            return operation()
        except StartupGuideBlocked:
            # A mandatory guide cannot be recovered by ordinary city taps.
            # Preserve settings and stop the affected runtime immediately.
            raise
        except Exception as error:
            stage = "首次异常" if not attempt else f"第 {attempt} 次重试异常"
            # Confirmed forced-offline dialogs retain the existing reconnect policy.
            try:
                offline = serial is not None and forced_offline(
                    screenshot(serial), text_template, reconnect_template)
            except Exception:
                offline = False
            if offline:
                print(f"{label} {stage}：{error}；检测到强制下线", flush=True)
                raise
            if attempt == 3:
                diagnostic = save_task_failure(serial, label, "retry-3")
                error.diagnostic_screenshot = diagnostic
                # The caller reports the final outcome (stop or skip) once.
                exhausted = RetriesExhausted(f"{label} 重试失败：{error}；连续3次重试失败")
                exhausted.diagnostic_screenshot = diagnostic
                raise exhausted from error
            retry = attempt + 1
            if on_retry is not None:
                on_retry(retry, str(error))
            action = (f"尝试恢复主城并重试（第 {retry} 次）" if recover is not None
                      else f"进行第 {retry} 次重试")
            print(f"{label} {stage}：{error}；10 秒后{action}", flush=True)
            time.sleep(10)


def save_task_failure(serial, label, attempt, context=None):
    """Return a screenshot path or failure reason for the original exception log."""
    if serial is None:
        return "失败截图未保存：尚未确定模拟器"
    try:
        # Capture before recovery, including unexpected resolutions for diagnosis.
        image = color_screenshot(serial)
        prefix = ("treasure-failure" if label == "宠物寻宝" else
                  "recruit-failure" if label in RECRUIT_TYPES.values() else "exception")
        captured_at = datetime.now()
        # ADB network serials contain colons, which are invalid in Windows folders.
        device_directory = re.sub(r'[^\w-]', '_', str(serial)) or "unknown-device"
        path = (ROOT / "artifacts" / DIAGNOSTIC_SCREENSHOT_DIR / device_directory
                / f"{captured_at:%Y-%m-%d}"
                / f"{prefix}-{captured_at:%Y%m%d-%H%M%S-%f}.png").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        image.info.get("native_frame", image).save(path)
    except Exception as exc:
        # Diagnostics must never mask an ADB, image-decoding or task exception.
        return f"失败截图保存失败：{type(exc).__name__}: {exc}"
    diagnostic = f"失败截图：{path}"
    if context is not None:
        try:
            metadata = {"device": serial, "task": label, "stage": attempt,
                        "captured_at": captured_at.isoformat(), **context}
            metadata_path = path.with_suffix(".json")
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
            diagnostic += f"；异常信息：{metadata_path}"
        except Exception as exc:
            diagnostic += f"；异常信息保存失败：{type(exc).__name__}: {exc}"
    try:
        maybe_cleanup_diagnostic_screenshots(force=True)
    except Exception as exc:
        return f"{diagnostic}；故障截图清理失败：{type(exc).__name__}: {exc}"
    return diagnostic


def exception_screenshot(serial, error):
    """Reuse exhausted retries' evidence without counting or capturing again."""
    return getattr(error, "diagnostic_screenshot", "")


def reset_timers_after_reconnect(timers):
    # Exploration income keeps accumulating while disconnected.
    for name in tuple(timers):
        if name != "explore":
            del timers[name]


def run_schedule(serial, seconds, interval, forever, tasks,
                 help_template, nav_template, text_template, reconnect_template,
                 reconnect=True, help_enabled=True, timers=None):
    return task_scheduler.run_schedule(
        sys.modules[__name__], serial, seconds, interval, forever, tasks,
        help_template, nav_template, text_template, reconnect_template,
        reconnect=reconnect, help_enabled=help_enabled, timers=timers)


def run_help_clicker(serial, help_template, nav_template, text_template, reconnect_template,
                     reconnect=True, dry_run=False):
    return alliance_help_clicker.run(
        sys.modules[__name__], serial, help_template, nav_template, text_template,
        reconnect_template, reconnect=reconnect, dry_run=dry_run)


def run_bear(serial, help_template, nav_template, text_template, reconnect_template,
             modes=("follow",), reconnect=True, dry_run=False, forever=True, seconds=120, interval=2):
    return bear_hunting.run(
        sys.modules[__name__], serial, help_template, nav_template, text_template,
        reconnect_template, modes=modes, reconnect=reconnect, dry_run=dry_run,
        forever=forever, seconds=seconds, interval=interval)


def main():
    global CHAT_TEMPLATE, RECONNECT_DELAY, RECONNECT_PROMO_CLOSE, RECONNECT_WELCOME_CLOSE
    global ACTIVITY_CLOSE, TOWN_TEMPLATE, WILDERNESS_HEALING
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="用点击前后截图验证识别")
    parser.add_argument("--device", help="ADB 序列号；只有一台设备时可省略")
    parser.add_argument("--emulator", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--control", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--help-clicker", action="store_true",
                        help="小号模式：仅进入联盟互助并持续快速点击全部帮助区域，其他任务全部跳过")
    parser.add_argument("--bear", action="store_true", help="仅执行自动打熊，其他任务全部跳过")
    parser.add_argument("--bear-modes", nargs="+", choices=("drive", "follow", "follow_no_heroes"), default=("follow_no_heroes",),
                        help="打熊方式：drive 主动开车，follow 自动跟车，follow_no_heroes 无英雄跟车，可同时选择")
    parser.add_argument("--city-tasks", action="store_true", help="另执行模拟器联盟科技捐献和免费招募")
    parser.add_argument("--train", action="store_true", help="另领取完成的士兵并启动空闲兵营的普通资源训练")
    parser.add_argument("--upgrade", action="store_true", help="自动练兵时优先晋升低级兵种至当前最高等级")
    parser.add_argument("--heal", action="store_true", help="野外检测伤兵图标并快速选择治疗，然后请求联盟互助")
    parser.add_argument("--daily-tasks", action="store_true", help="启动时全量检查；探险经验领取后每8小时59分领取，训练、招募、仓库、体力罐头和晨曦按倒计时复查，捐献、宠物寻宝和生命之树每10分钟检查")
    for name, description in (("help", "联盟互助"), ("explore", "探险经验"),
                              ("train", "练兵"),
                              ("shield", "盾兵训练"),
                              ("spear", "矛兵训练"), ("archer", "射手训练"),
                              ("donate", "联盟捐献"), ("alliance_chests", "联盟宝箱"), ("recruit", "免费招募"),
                              ("treasure", "宠物寻宝"),
                              ("warehouse", "仓库补给"), ("tree", "生命结晶"),
                              ("dawn", "晨曦回礼"), ("pet", "宠物技能"), ("task_rewards", "任务奖励"),
                              ("reconnect", "强制下线自动重连")):
        parser.add_argument(f"--no-{name.replace('_', '-')}", action="store_true", help=f"关闭{description}")
    parser.add_argument("--tree-threshold", type=int, default=1000,
                        help="生命结晶数量达到此值时领取，必须大于 0；默认 1000")
    parser.add_argument("--intelligence", action="store_true", help="日常任务后执行灯塔情报")
    parser.add_argument("--hunt", action="store_true", help="灯塔情报后自动打怪；关闭情报时优先打怪")
    parser.add_argument("--monsters", nargs="+", choices=tuple(hunting.MONSTERS),
                        default=tuple(hunting.MONSTERS), help="按野兽、冰原巨兽顺序挑战")
    for kind, (label, maximum) in hunting.MONSTERS.items():
        parser.add_argument(f"--{kind.replace('_', '-')}-level", type=int, choices=range(hunting.MINIMUM_LEVELS[kind], maximum + 1),
                            default=1 if kind == "beast" else 2, help=f"{label}挑战等级")
    parser.add_argument("--gather", action="store_true", help="启动时执行资源采集，采集队伍返回后按配置顺序重新采集")
    parser.add_argument("--no-gather-full-only", action="store_true",
                        help="采集搜索时取消仅搜索资源为满的资源点，默认勾选")
    parser.add_argument("--gather-steps", nargs="+", type=parse_gather_step,
                        help="按顺序采集，例如 wood:7 iron:8，允许重复资源，最多六项")
    parser.add_argument("--resources", nargs="+", choices=tuple(GATHER_RESOURCES),
                        default=tuple(GATHER_RESOURCES), help="采集资源，默认 meat wood coal iron")
    for resource, label in GATHER_RESOURCES.items():
        parser.add_argument(f"--{resource}-level", type=int, choices=range(1, 9), default=1,
                            help=f"{label}采集搜索等级，1 到 8，默认 1")
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
    if args.bear and args.help_clicker:
        parser.error("自动打熊与互助连点不能同时开启")
    if (args.duration <= 0 or args.max_clicks <= 0 or not math.isfinite(args.interval)
            or not math.isfinite(args.reconnect_minutes) or args.interval <= 0 or args.reconnect_minutes <= 0):
        parser.error("时长、间隔、点击上限和重连分钟数都必须大于零")
    if args.tree_threshold <= 0:
        parser.error("生命结晶阈值必须大于 0")
    if args.stamina_threshold < 0:
        parser.error("体力触发阈值必须大于或等于 0")
    if args.forever and args.dry_run:
        parser.error("--forever 不能与 --dry-run 同时使用")
    previous_runtime = {name: globals()[name] for name in (
        "CHAT_TEMPLATE", "RECONNECT_DELAY", "RECONNECT_PROMO_CLOSE", "RECONNECT_WELCOME_CLOSE",
        "ACTIVITY_CLOSE", "TOWN_TEMPLATE", "TASK_HELP", "HELP_RETRY_AT", "TASK_DRAWER_SCROLL",
        "WILDERNESS_HEALING", "HEALING_ACTIVE")}
    RECONNECT_DELAY = args.reconnect_minutes * 60
    serial = args.device
    lease = None
    control = RuntimeControl() if args.control else None
    if control is not None:
        activate(control)
        threading.Thread(target=control.listen, args=(sys.stdin,), daemon=True).start()
    try:
        check_stop()
        maybe_cleanup_diagnostic_screenshots(force=True)
        CHAT_TEMPLATE = open_template(ROOT / "assets/emulator-chat-title.png").convert("L")
        RECONNECT_PROMO_CLOSE = open_template(ROOT / "assets/emulator-reconnect-promo-close.png").convert("L")
        RECONNECT_WELCOME_CLOSE = open_template(ROOT / "assets/emulator-reconnect-welcome-close.png").convert("L")
        ACTIVITY_CLOSE = open_template(ROOT / "assets/emulator-activity-close.png").convert("L")
        TOWN_TEMPLATE = open_template(ROOT / "assets/emulator-town-button.png").convert("L")
        help_template = open_template(ROOT / "assets/emulator-help-icon.png").convert("L")
        nav_template = open_template(ROOT / "assets/emulator-city-nav.png").convert("L")
        WILDERNESS_HEALING = ((help_template, nav_template)
                              if args.heal and not args.dry_run and not (args.help_clicker or args.bear) else None)
        text_template = open_template(ROOT / "assets/emulator-forced-text.png").convert("L")
        reconnect_template = open_template(ROOT / "assets/emulator-reconnect.png").convert("L")
        task_templates = ({name: open_template(ROOT / f"assets/emulator-{name}.png").convert("RGB")
                           for name in (*TASK_BOXES, "donate-label", "free-label", "thumb")}
                          if (args.city_tasks or args.daily_tasks) and not (args.help_clicker or args.bear) else None)
        train_titles = {unit: open_template(ROOT / f"assets/emulator-training-{unit}-title.png").convert("L")
                        for unit, _ in TRAIN_ROWS} if (args.train or args.daily_tasks) and not (args.help_clicker or args.bear) else None
        if args.self_test:
            self_test(help_template, nav_template, text_template, reconnect_template)
        else:
            serial = args.device or retry_once("查找设备", connected_device, None,
                                               text_template, reconnect_template)
            bind_console_emulator(avd_key(serial))
            if adb(serial, "get-state").strip() != b"device":
                raise RuntimeError("所选安卓设备未连接或未授权，请检查 USB 调试并刷新设备列表")
            if not args.dry_run:
                identity = avd_key(serial)
                lease = DeviceLease(serial, identity).acquire()
                import stamina_state
                stamina_state.set_device_identity(serial, identity)
            if args.bear:
                run_bear(serial, help_template, nav_template, text_template,
                         reconnect_template, modes=args.bear_modes, reconnect=not args.no_reconnect,
                         dry_run=args.dry_run, forever=args.forever, seconds=args.duration, interval=args.interval)
            elif args.help_clicker:
                run_help_clicker(serial, help_template, nav_template, text_template,
                                 reconnect_template, reconnect=not args.no_reconnect,
                                 dry_run=args.dry_run)
            elif args.dry_run:
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
                drawer_template = open_template(ROOT / "assets/emulator-drawer-open.png").convert("RGB")
                if args.daily_tasks and not args.no_explore:
                    explore_templates = {name: open_template(ROOT / f"assets/emulator-explore-{name}.png").convert("L")
                                         for name in EXPLORE_BOXES}
                    tasks["explore"] = lambda: run_explore(serial, help_template, nav_template, explore_templates, timers)
                units = {unit for unit, _ in TRAIN_ROWS if not getattr(args, f"no_{unit}")}
                if (args.daily_tasks or args.train) and not args.no_train and units:
                    tasks["train"] = lambda: run_training(serial, help_template, nav_template, drawer_template,
                                                           train_titles, units=units, timers=timers, keep_open=True,
                                                           upgrade=args.upgrade)
                if args.daily_tasks or args.city_tasks:
                    if not args.no_donate:
                        tasks["donate"] = lambda: run_city_tasks(serial, help_template, nav_template,
                                                                  task_templates, recruit=False, keep_open=True,
                                                                  timers=timers,
                                                                  collect_alliance_chests=args.daily_tasks and not args.no_alliance_chests)
                    if args.daily_tasks and not args.no_alliance_chests:
                        tasks['alliance_chests'] = lambda: alliance_chests.run(
                            sys.modules[__name__], serial, help_template, nav_template, timers=timers)
                    if not args.no_recruit:
                        for kind in RECRUIT_TYPES:
                            tasks[kind] = lambda kind=kind: run_city_tasks(
                                serial, help_template, nav_template, task_templates, donate=False,
                                timers=timers, recruit_kind=kind, keep_open=True)
                if args.daily_tasks:
                    warehouse = open_template(ROOT / "assets/emulator-warehouse-title.png").convert("RGB")
                    tree = open_template(ROOT / "assets/emulator-tree-collect-label.png").convert("RGB")
                    island = open_template(ROOT / "assets/emulator-island-title.png").convert("L")
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
                if args.daily_tasks and not args.no_task_rewards:
                    tasks["task_rewards"] = lambda: task_rewards.run(
                        sys.modules[__name__], serial, help_template, nav_template)
                if args.intelligence:
                    tasks["intelligence"] = lambda: run_intelligence(
                        serial, help_template, nav_template, text_template, reconnect_template,
                        args.stamina_threshold, timers=timers,
                        bounty=args.bounty, bounty_state=bounty_state)
                if args.hunt:
                    tasks["hunt"] = lambda: run_hunt(
                        serial, help_template, nav_template, monsters=args.monsters,
                        levels={kind: getattr(args, f"{kind}_level") for kind in args.monsters}, timers=timers)
                if args.gather:
                    tasks["gather"] = GatherTask(
                        sys.modules[__name__], serial,
                        (help_template, nav_template, text_template, reconnect_template),
                        **({"steps": args.gather_steps} if args.gather_steps is not None else {}),
                        full_only=not args.no_gather_full_only,
                        resources=args.resources,
                        levels={resource: getattr(args, f"{resource}_level") for resource in args.resources})
                if args.heal:
                    tasks["heal"] = lambda: run_healing(serial, help_template, nav_template)
                print("启动时主城检查已通过，开始运行任务", flush=True)
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
    except KeyboardInterrupt:
        print("已由键盘停止", flush=True)
    except StopRequested:
        print("任务已停止，后续点击已取消。", flush=True)
    except Exception as exc:
        diagnostic = exception_screenshot(serial, exc)
        parser.exit(1, f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 已停止：{type(exc).__name__}: {exc}；{diagnostic}\n")
    finally:
        if lease is not None:
            lease.release()
            stamina_state.clear_device_identity(serial)
        if control is not None:
            control.stop()
            activate(None)
        globals().update(previous_runtime)
if __name__ == "__main__":
    configure_stdio()
    if os.environ.get("GAMEBOT_LOG_OWNER") == "gui" or "--self-test" in sys.argv:
        main()
    else:
        log_parser = argparse.ArgumentParser(add_help=False)
        log_parser.add_argument("--device")
        log_args, _ = log_parser.parse_known_args()
        key = avd_key(log_args.device) if log_args.device else "unknown"
        with capture_console(ROOT, key):
            main()
