"""Bear rallies and calibrated hero selection on 1080 x 2340 emulator screens."""

import cv2
import numpy as np
import re

HEROES = ("jessie", "jasser", "seoyoon")
HERO_LABELS = dict(zip(HEROES, ("杰西", "杰塞尔", "书允")))
SLOT_OFFSETS = (0, 284, 568)
FACE_BOX = (170, 557, 340, 750)
MINUS_BOX = (322, 495, 390, 561)
PLUS_BOX = (213, 665, 298, 748)
SELECTION_OFFSET = 94
GRID_BOX = (90, 1140, 970, 1674)


def war_page(bot, image):
    return matches(bot, image, "war-title", (140, 165, 340, 225))


def bear_join_targets(bot, image):
    """Bind each enabled green plus to the target label in its own rally row."""
    if not war_page(bot, image):
        return []
    template = np.asarray(asset(bot, "join"))
    pixels = np.asarray(image.crop((900, 350, 1030, 2090)))
    scores = cv2.matchTemplate(pixels, template, cv2.TM_SQDIFF_NORMED)
    scores = np.nan_to_num(scores, nan=1, posinf=1, neginf=1)
    targets = []
    for _ in range(4):
        score, _, (x, y), _ = cv2.minMaxLoc(scores)
        # Real green samples score below .003; disabled grey buttons exceed .18.
        if score > .035:
            break
        cx, cy = 900 + x + template.shape[1] // 2, 350 + y + template.shape[0] // 2
        scores[max(0, y - 100):y + 101] = 1
        if cy + 140 > 2090:
            continue
        labels = [re.sub(r"\s+", "", text) for text, _ in
                  bot.recognize_text(image.crop((430, cy + 60, 900, cy + 140)))]
        if any(re.fullmatch(r"(?:等级\d+)?变异巨熊", label) for label in labels):
            targets.append((cx, cy))
    return sorted(targets, key=lambda point: point[1])


def bear_squad_target(bot, image):
    """Recheck the opened target so a rally-list update cannot change the victim."""
    if not squad_page(bot, image):
        return False
    labels = [re.sub(r"\s+", "", text) for text, _ in
              bot.recognize_text(image.crop((120, 160, 750, 235)))]
    return any(re.fullmatch(r"目标[:：]变异巨熊", label) for label in labels)


def shifted(box, dx=0, dy=0):
    return box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy


def asset(bot, name):
    return bot.open_template(bot.ROOT / f"assets/emulator-bear-{name}.png").convert("RGB")


def distance(left, right):
    return float(np.abs(np.asarray(left.convert("RGB"), dtype=np.int16)
                        - np.asarray(right.convert("RGB"), dtype=np.int16)).mean())


def matches(bot, image, name, box, limit=16):
    return image.size == (1080, 2340) and distance(image.crop(box), asset(bot, name)) <= limit


def hero_name(bot, face, mode="squad"):
    scores = [(distance(face, asset(bot, f"hero-{hero}-{mode}-face")), hero) for hero in HEROES]
    score, hero = min(scores)
    return hero if score <= 20 else None


def selection_page(bot, image):
    return matches(bot, image, "hero-title", (438, 477, 642, 539))


def dispatch_target(bot, image):
    return bot.intelligence_text_target(image, ("出征",), (600, 2180, 1050, 2300))


def slots(bot, image, selection=False):
    """Return empty/occupied only when each slot has an observed visual marker."""
    dy = SELECTION_OFFSET if selection else 0
    result = []
    for dx in SLOT_OFFSETS:
        names = ("hero-selection-plus", "hero-selection-plus-disabled") if selection else (
            "hero-plus", "hero-plus-disabled")
        if any(matches(bot, image, name, shifted(PLUS_BOX, dx, dy)) for name in names):
            result.append(False)
        elif selection:
            # Assignment toasts cross the plus sign, but not the upper portrait.
            # An obscured empty slot must be retried, never counted as occupied.
            pixels = np.asarray(image.crop(shifted((170, 557, 340, 625), dx, dy)), dtype=np.float32)
            if float(pixels.std(axis=(0, 1)).mean()) < 24:
                raise RuntimeError("英雄选择槽尚未完成加载")
            result.append(True)
        elif matches(bot, image, "hero-minus", shifted(MINUS_BOX, dx)):
            result.append(True)
        else:
            raise RuntimeError("未确认出征界面的英雄槽")
    return result


def squad_page(bot, image):
    if image.size != (1080, 2340) or selection_page(bot, image):
        return False
    try:
        slots(bot, image)
    except RuntimeError:
        return False
    return dispatch_target(bot, image) is not None


def wait_for(bot, serial, predicate, message, attempts=8):
    for _ in range(attempts):
        image = bot.task_screen(serial)
        try:
            if predicate(image):
                return image
        except RuntimeError:
            # Slot markers can be obscured by short assignment/removal toasts.
            continue
    raise RuntimeError(message)


def cards(bot, image):
    """Locate complete white avatar frames, regardless of roster order or scroll."""
    template = np.asarray(asset(bot, "hero-card").convert("L"))
    mask = np.zeros(template.shape, dtype=np.uint8)
    mask[5:10, 30:-30] = mask[-10:-5, 30:-30] = 255
    mask[30:-30, 5:10] = mask[30:-30, -10:-5] = 255
    pixels = np.asarray(image.crop(GRID_BOX).convert("L"))
    scores = cv2.matchTemplate(pixels, template, cv2.TM_SQDIFF_NORMED, mask=mask)
    scores = np.nan_to_num(scores, nan=1, posinf=1, neginf=1)
    found = []
    for _ in range(12):
        score, _, (x, y), _ = cv2.minMaxLoc(scores)
        if score > .015:
            break
        found.append((GRID_BOX[0] + x, GRID_BOX[1] + y))
        scores[max(0, y - 100):y + 101, max(0, x - 100):x + 101] = 1
    return sorted(found, key=lambda xy: (xy[1], xy[0]))


def card_hero(bot, image, position):
    x, y = position
    return hero_name(bot, image.crop((x + 14, y + 50, x + 156, y + 126)), "select")


def blue(pixels):
    values = pixels.astype(np.int16)
    return ((values[:, :, 0] < 150) & (values[:, :, 1] > 145)
            & (values[:, :, 2] > 200) & (values[:, :, 2] > values[:, :, 1] + 15)).astype(np.uint8) * 255


def focused_card(bot, image):
    template = blue(np.asarray(asset(bot, "hero-focus")))
    mask = np.zeros(template.shape, dtype=np.uint8)
    for x in (0, 187):
        for y in (0, 187):
            mask[y:y + 38, x:x + 38] = 255
    found = []
    for position in cards(bot, image):
        x, y = position
        region = blue(np.asarray(image.crop((x - 32, y - 31, x + 201, y + 202))))
        scores = cv2.matchTemplate(region, template, cv2.TM_CCORR_NORMED, mask=mask)
        scores = np.nan_to_num(scores, nan=0, posinf=0, neginf=0)
        score = float(scores.max())
        if score >= .60:
            found.append((score, position))
    found.sort(reverse=True)
    if not found or (len(found) > 1 and found[0][0] - found[1][0] < .10):
        return None
    return found[0][1]


def scroll_roster(bot, serial, image, down=True):
    if not selection_page(bot, image):
        raise RuntimeError("滚动英雄前未确认英雄选择界面")
    bot.adb(serial, "shell", "input", "swipe", "640", "1540" if down else "1220",
            "640", "1220" if down else "1540", "450")
    return bot.task_screen(serial)


def choose(bot, serial, image, first):
    """Keep an allowed auto-selected candidate; only change a forbidden one."""
    for scroll in range(7):
        if not selection_page(bot, image):
            raise RuntimeError("选择英雄时离开英雄选择界面")
        focused = focused_card(bot, image)
        if focused is not None and ((card_hero(bot, image, focused) is not None) == first):
            return image, True
        candidates = [point for point in cards(bot, image)
                      if (card_hero(bot, image, point) is not None) == first]
        if candidates:
            expected = candidates[0]
            bot.task_tap(serial, expected[0] + 85, expected[1] + 85)
            def selected(frame):
                if not selection_page(bot, frame):
                    return False
                point = focused_card(bot, frame)
                return point is not None and max(abs(point[i] - expected[i]) for i in (0, 1)) <= 3
            image = wait_for(bot, serial,
                             selected,
                             "未确认候选英雄已选中")
            return image, True
        # Begin at the top even if the previous assignment retained a scroll.
        previous = image.crop(GRID_BOX)
        image = scroll_roster(bot, serial, image, down=scroll > 0)
        if scroll > 0 and distance(previous, image.crop(GRID_BOX)) < 2:
            return image, False
    return image, False


def close_selection(bot, serial, image):
    if not selection_page(bot, image):
        raise RuntimeError("关闭英雄选择前未确认界面")
    bot.task_tap(serial, 960, 507)
    return wait_for(bot, serial, lambda frame: squad_page(bot, frame), "关闭英雄选择后未确认出征界面")


def fill_selection(bot, serial, image, need_first):
    for _ in range(3):
        occupied = slots(bot, image, selection=True)
        if all(occupied):
            break
        slot = occupied.index(False)
        image, found = choose(bot, serial, image, first=need_first and slot == 0)
        if not found:
            bot.print("未找到杰西、杰塞尔、书允，保持无英雄出征" if need_first and slot == 0
                      else "没有可用的名单外英雄，保留空英雄槽", flush=True)
            break
        focused = focused_card(bot, image)
        selected = card_hero(bot, image, focused)
        if (selected is not None) != (need_first and slot == 0):
            raise RuntimeError("指派前候选英雄不符合打熊阵容规则")
        bot.task_tap(serial, 770, 1780)
        image = wait_for(bot, serial,
                         lambda frame: selection_page(bot, frame)
                         and sum(slots(bot, frame, selection=True)) == sum(occupied) + 1
                         and slots(bot, frame, selection=True)[slot],
                         "指派英雄后未确认英雄槽更新")
    return close_selection(bot, serial, image)


def remove(bot, serial, image, slot):
    if not squad_page(bot, image) or not slots(bot, image)[slot]:
        raise RuntimeError("移除英雄前未确认出征界面和目标减号")
    bot.task_tap(serial, 356 + SLOT_OFFSETS[slot], 528)
    return wait_for(bot, serial,
                    lambda frame: squad_page(bot, frame) and not slots(bot, frame)[slot]
                    and (slot != 0 or not any(slots(bot, frame))),
                    "点击减号后未确认英雄已移除")


def prepare_squad(bot, serial, image):
    if not squad_page(bot, image):
        raise RuntimeError("配置打熊阵容前未确认出征界面")
    occupied = slots(bot, image)
    first = hero_name(bot, image.crop(FACE_BOX)) if occupied[0] else None
    if first is None:
        for slot in range(3):
            if slots(bot, image)[slot]:
                image = remove(bot, serial, image, slot)
        bot.task_tap(serial, 254, 705)
        image = wait_for(bot, serial, lambda frame: selection_page(bot, frame), "未打开英雄选择界面")
        image = fill_selection(bot, serial, image, need_first=True)
    else:
        forbidden = [slot for slot in (1, 2) if occupied[slot]
                     and hero_name(bot, image.crop(shifted(FACE_BOX, SLOT_OFFSETS[slot]))) is not None]
        for slot in reversed(forbidden):
            image = remove(bot, serial, image, slot)
        if forbidden:
            slot = min(forbidden)
            bot.task_tap(serial, 254 + SLOT_OFFSETS[slot], 705)
            image = wait_for(bot, serial, lambda frame: selection_page(bot, frame), "未打开后排英雄选择界面")
            image = fill_selection(bot, serial, image, need_first=False)
    occupied = slots(bot, image)
    if any(occupied):
        if not occupied[0] or hero_name(bot, image.crop(FACE_BOX)) is None:
            raise RuntimeError("出征前首英雄未通过打熊名单检查")
        if any(occupied[slot] and hero_name(bot, image.crop(shifted(FACE_BOX, SLOT_OFFSETS[slot])))
               is not None for slot in (1, 2)):
            raise RuntimeError("出征前后两位英雄仍包含打熊首英雄")
    return image


def rally_page(bot, image):
    return matches(bot, image, "rally-title", (435, 850, 655, 914)) and matches(
        bot, image, "rally-start", (345, 1490, 735, 1570))


def dispatch(bot, serial, image):
    image = prepare_squad(bot, serial, image)
    target = dispatch_target(bot, image)
    if target is None:
        raise RuntimeError("未确认打熊出征按钮")
    bot.task_tap(serial, *target)
    for _ in range(10):
        image = bot.task_screen(serial)
        reason = bot.hunting.notice(bot, image)
        if reason in ("体力不足", "士兵不足"):
            bot.print(f"打熊暂不能出征：{reason}", flush=True)
            return bot.hunting.leave(bot, serial, image), False
        if bot.town_button(image) and not bot.gather_search_page(image) and not squad_page(bot, image):
            return image, True
    raise RuntimeError("点击出征后未确认返回野外，需检查当前行军队列")


def start_rally(bot, serial, image):
    if not rally_page(bot, image):
        raise RuntimeError("未确认发起集结弹窗")
    bot.task_tap(serial, 540, 1538)
    image = wait_for(bot, serial, lambda frame: squad_page(bot, frame), "发起集结后未进入出征界面")
    return dispatch(bot, serial, image)
