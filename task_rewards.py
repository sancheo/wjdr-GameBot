"""Collect numbered task notifications on confirmed city/wilderness surfaces."""

import re

from PIL import Image

ENTRY_BOX = (15, 1985, 95, 2040)
ENTRY_BADGE = (68, 1930, 125, 1988)
TITLE_BOX = (435, 435, 645, 500)
TAB_BOX = (120, 1900, 990, 2070)


def badge_count(bot, image, box, detected_text=None):
    if not bot.red_dot(image, box):
        return 0
    texts = detected_text or [text for text, _ in bot.recognize_text(image.crop(box))]
    for text in texts:
        text = text.replace(" ", "")
        if re.fullmatch(r"[0-9]+\+?", text) and int(text.rstrip("+")) > 0:
            return int(text.rstrip("+"))
    # Small badge digits can disappear when OCR sees the whole tab row or
    # surrounding tab text. Retry only the actual red badge, never infer a count.
    crop = image.crop(box).convert("RGB")
    points = [(index % crop.width, index // crop.width)
              for index, (red, green, blue) in enumerate(crop.getdata())
              if red > 180 and red > green * 1.45 and red > blue * 1.45]
    if points:
        xs, ys = zip(*points)
        badge = crop.crop((max(0, min(xs) - 3), max(0, min(ys) - 3),
                           min(crop.width, max(xs) + 4), min(crop.height, max(ys) + 4)))
        for text, _ in bot.recognize_text(badge):
            text = text.replace(" ", "")
            if re.fullmatch(r"[0-9]+\+?", text) and int(text.rstrip("+")) > 0:
                return int(text.rstrip("+"))
        # A tightly cropped single "1" can be rejected by the OCR detector.
        # Add a neutral margin; still require an actual positive digit read.
        padded = Image.new("RGB", (badge.width + 20, badge.height + 20), "white")
        padded.paste(badge, (10, 10))
        for text, _ in bot.recognize_text(padded):
            text = text.replace(" ", "")
            if re.fullmatch(r"[0-9]+\+?", text) and int(text.rstrip("+")) > 0:
                return int(text.rstrip("+"))
    return None


def entry(bot, image, help_template, nav_template):
    if image.mode != "RGB":
        return None
    if not bot.inspect(image.convert("L"), help_template, nav_template,
                       allow_wilderness=True)[0]:
        return None
    template = bot.open_template(bot.ROOT / "assets/emulator-task-rewards-entry.png").convert("L")
    if bot.difference(image.crop(ENTRY_BOX).convert("L"), template) > 20:
        return None
    return (65, 1995) if badge_count(bot, image, ENTRY_BADGE) else None


def page(bot, image):
    for name, label in (("daily", "每日任务"), ("growth", "成长任务")):
        template = bot.open_template(bot.ROOT / f"assets/emulator-task-rewards-{name}-title.png").convert("L")
        if bot.difference(image.crop(TITLE_BOX).convert("L"), template) <= 15:
            return label
    # Other task tabs can be unlocked on different accounts.
    for text, _ in bot.recognize_text(image.crop((340, 420, 760, 510))):
        text = text.replace(" ", "")
        if text.endswith("任务"):
            return text
    return None


def tabs(bot, image):
    result = []
    detections = bot.recognize_text(image.crop(TAB_BOX))
    for text, (x, y, width, height) in detections:
        text = text.replace(" ", "")
        if not text.endswith("任务"):
            continue
        x, y = x + TAB_BOX[0], y + TAB_BOX[1]
        box = (round(x + width), round(y + height - 30),
               min(1080, round(x + width + 110)), min(2100, round(y + height + 55)))
        numbers = [value for value, (nx, ny, nw, nh) in detections
                   if (box[0] <= TAB_BOX[0] + nx + nw / 2 <= box[2]
                       and box[1] <= TAB_BOX[1] + ny + nh / 2 <= box[3]
                       and re.fullmatch(r"[0-9]+\+?", value.replace(" ", "")))]
        result.append((text, (round(x + width / 2), round(y + height / 2)),
                       badge_count(bot, image, box, detected_text=numbers)))
    return result


def green_claim(bot, image, labels, box):
    for text, (x, y, width, height) in bot.recognize_text(image.crop(box)):
        if text.replace(" ", "") not in labels:
            continue
        x, y = round(box[0] + x + width / 2), round(box[1] + y + height / 2)
        mask = bot.green_mask(image.crop((x - 85, y - 28, x + 85, y + 28)))
        if sum(value > 0 for value in mask.getdata()) > mask.width * mask.height * .2:
            return x, y
    return None


def stable_tabs(bot, serial, image):
    for _ in range(6):
        available = tabs(bot, image)
        if len(available) >= 2 and all(count is not None for _, _, count in available):
            return image, available
        image = wait_panel(bot, serial)
    raise RuntimeError("任务奖励弹窗未识别到完整的任务标签页")


def wait_panel(bot, serial, on_reward=None):
    for _ in range(8):
        image = bot.task_screen(serial, help_enabled=False)
        if page(bot, image) is not None:
            return image
        # Reward popups may cover the task panel after a claim.
        if (bot.task_page(image, {"reward-title": bot.open_template(
                bot.ROOT / "assets/emulator-reward-title.png").convert("RGB")}, "reward-title")
                or bot.intelligence_text_target(image, ("点击任意位置",), (220, 1850, 900, 2260))):
            if on_reward is not None:
                on_reward()
            bot.task_tap(serial, 540, 2160)
    raise RuntimeError("领取任务奖励后未确认任务弹窗")


def run(bot, serial, help_template, nav_template, initial_image=None):
    image = initial_image if initial_image is not None else bot.task_screen(serial, help_enabled=False)
    drawer = bot.open_template(bot.ROOT / "assets/emulator-drawer-open.png").convert("RGB")
    if bot.task_page(image, {"drawer-open": drawer}, "drawer-open"):
        bot.task_tap(serial, 695, 1100)
        image = bot.task_screen(serial, help_enabled=False)
    if page(bot, image) is None:
        target = entry(bot, image, help_template, nav_template)
        if target is None:
            return image
        bot.task_tap(serial, *target)
        image = wait_panel(bot, serial)
    visited = set()
    for _ in range(10):
        image, available = stable_tabs(bot, serial, image)
        # Always inspect the daily tab first to use its batch claim button.
        pending = sorted((tab for tab in available if tab[0] not in visited
                          and (tab[0] == "每日任务" or tab[2])),
                         key=lambda tab: tab[0] != "每日任务")
        if not pending:
            break
        label, target, _ = pending[0]
        if page(bot, image) != label:
            bot.task_tap(serial, *target)
            image = wait_panel(bot, serial)
            if page(bot, image) != label:
                raise RuntimeError(f"任务奖励未能切换到{label}")
        scrolls = 0
        for _ in range(100):
            bot.check_stop()
            image, available = stable_tabs(bot, serial, image)
            remaining = next((count for name, _, count in available if name == label), 0)
            if not remaining:
                break
            target = (green_claim(bot, image, ("一键领取",), (300, 1720, 780, 1890))
                      if label == "每日任务" else None)
            if target is None and label != "每日任务":
                target = green_claim(bot, image, ("领取",), (740, 580, 1020, 1890))
            if target is not None:
                before = image
                x, y = target
                progress_box = (max(0, x - 140), max(0, y - 70),
                                min(1080, x + 140), min(2340, y + 70))
                bot.task_tap(serial, *target)
                reward_seen = []
                def confirm_reward():
                    reward_seen.append(True)
                image = wait_panel(bot, serial, on_reward=confirm_reward)
                for _ in range(5):
                    image, updated_tabs = stable_tabs(bot, serial, image)
                    updated_count = next((count for name, _, count in updated_tabs if name == label), 0)
                    if (reward_seen or updated_count < remaining
                            or bot.difference(before.crop(progress_box).convert("L"),
                                       image.crop(progress_box).convert("L")) > 2
                            or bot.difference(before.crop(TAB_BOX).convert("L"),
                                              image.crop(TAB_BOX).convert("L")) > .3):
                        break
                    image = wait_panel(bot, serial, on_reward=confirm_reward)
                else:
                    raise RuntimeError(f"{label}领取后画面未更新")
                bot.print(f"已{'一键' if label == '每日任务' else ''}领取{label}奖励", flush=True)
                continue
            if label == "每日任务":
                raise RuntimeError("每日任务仍有奖励角标，但未确认可用的一键领取按钮")
            if scrolls >= 12:
                raise RuntimeError(f"{label}仍有奖励角标，但未找到领取按钮")
            if not bot.game_foreground(serial):
                raise RuntimeError("当前前台不是目标游戏，已停止")
            bot.adb(serial, "shell", "input", "swipe", "550", "1740", "550", "950", "650")
            image = wait_panel(bot, serial)
            scrolls += 1
        else:
            raise RuntimeError(f"{label}领取次数超过安全上限")
        visited.add(label)
    else:
        raise RuntimeError("任务奖励标签页数量超过安全上限")
    bot.task_tap(serial, 1030, 460)
    for _ in range(8):
        image = bot.task_screen(serial, help_enabled=False)
        if bot.inspect(image.convert("L"), help_template, nav_template, allow_wilderness=True)[0]:
            return image
    raise RuntimeError("任务奖励领取结束后未确认主城或野外界面")
