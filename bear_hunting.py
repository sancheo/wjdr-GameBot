"""Bear rallies and hero selection using existing screen assets and coordinates."""

import cv2
import numpy as np
import re

from runtime_control import StopRequested

HEROES = ("jessie", "jasser", "seoyoon")
HERO_LABELS = dict(zip(HEROES, ("杰西", "杰塞尔", "书允")))
SLOT_OFFSETS = (0, 284, 568)
FACE_BOX = (170, 557, 340, 750)
MINUS_BOX = (322, 495, 390, 561)
PLUS_BOX = (213, 665, 298, 748)
SELECTION_OFFSET = 94
GRID_BOX = (90, 1140, 970, 1674)


def war_page(bot, image):
    return (matches(bot, image, "war-title", (140, 165, 340, 225))
            or any(re.sub(r"\s+", "", text) == "联盟战争" for text, _ in
                   bot.recognize_text(image.crop((120, 150, 400, 245)))))


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


def bear_squad_target(bot, image, *, verified_entry=False):
    """Recheck the opened target so a rally-list update cannot change the victim."""
    if not squad_page(bot, image):
        return False
    labels = [re.sub(r"\s+", "", text) for text, _ in
              bot.recognize_text(image.crop((120, 160, 750, 235)))]
    if any(re.fullmatch(r"目标[:：](?:等级\d+)?变异巨熊", label) for label in labels):
        return True
    # Bear rallies on 5556 use the generic dispatch title. Accept it only
    # immediately after our verified bear-list/detail entry, never standalone.
    return verified_entry and labels == ["出征"]


def shifted(box, dx=0, dy=0):
    return box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy


def asset(bot, name):
    return bot.open_template(bot.ROOT / f"assets/emulator-bear-{name}.png").convert("RGB")


def distance(left, right):
    return float(np.abs(np.asarray(left.convert("RGB"), dtype=np.int16)
                        - np.asarray(right.convert("RGB"), dtype=np.int16)).mean())


def matches(bot, image, name, box, limit=16):
    return distance(image.crop(box), asset(bot, name)) <= limit


def hero_name(bot, face, mode="squad"):
    scores = [(distance(face, asset(bot, f"hero-{hero}-{mode}-face")), hero) for hero in HEROES]
    score, hero = min(scores)
    return hero if score <= 20 else None


def selection_page(bot, image):
    return matches(bot, image, "hero-title", (438, 477, 642, 539))


def dispatch_target(bot, image):
    target = bot.intelligence_text_target(image, ("出征",), (600, 2180, 1050, 2300))
    if target is not None:
        return target
    # Short screens anchor the footer independently of the hero panel.
    from screen_geometry import scene_view
    view = scene_view(image)
    if view is not None:
        frame, to_logical, _ = view
        target = bot.intelligence_text_target(frame, ("出征",), (600, 1850, 1050, 2250))
        if target is not None:
            return to_logical(*target)
    return None


def slot_states(bot, image, selection=False, dy=0):
    """Return empty/occupied only when each slot has an observed visual marker."""
    dy = SELECTION_OFFSET if selection else dy
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
        elif matches(bot, image, "hero-minus", shifted(MINUS_BOX, dx, dy)):
            result.append(True)
        else:
            raise RuntimeError("未确认出征界面的英雄槽")
    return result


def squad_layout(bot, image):
    # The war-bonus row shifts portraits by 91-92px, depending on rounding.
    for dy in (0, 91, 92):
        try:
            return dy, slot_states(bot, image, dy=dy)
        except RuntimeError:
            continue
    raise RuntimeError("未确认出征界面的英雄槽")


def slots(bot, image, selection=False):
    return slot_states(bot, image, selection=True) if selection else squad_layout(bot, image)[1]


def adapted_hero_controls(bot, image):
    """Locate intact hero markers on short emulator screens before mapping taps."""
    from screen_geometry import scene_view
    view = scene_view(image)
    if view is None:
        return None
    frame, to_logical, _ = view
    controls = []
    for dx in SLOT_OFFSETS:
        found = []
        for occupied, name, box in ((True, "hero-minus", (310 + dx, 440, 405 + dx, 850)),
                                   (False, "hero-plus", (195 + dx, 650, 315 + dx, 1100)),
                                   (False, "hero-plus-disabled", (195 + dx, 650, 315 + dx, 1100))):
            template = np.asarray(asset(bot, name))
            scores = cv2.matchTemplate(np.asarray(frame.crop(box)), template, cv2.TM_SQDIFF_NORMED)
            score, _, (x, y), _ = cv2.minMaxLoc(scores)
            if score < .035:
                found.append((score, occupied, to_logical(box[0] + x + template.shape[1] // 2,
                                                        box[1] + y + template.shape[0] // 2)))
        if not found:
            return None
        _, occupied, point = min(found)
        controls.append((occupied, point))
    return controls


def squad_page(bot, image):
    if selection_page(bot, image):
        return False
    try:
        slots(bot, image)
    except RuntimeError:
        if adapted_hero_controls(bot, image) is None:
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
    dy, _ = squad_layout(bot, image)
    bot.task_tap(serial, 356 + SLOT_OFFSETS[slot], 528 + dy)
    return wait_for(bot, serial,
                    lambda frame: squad_page(bot, frame) and not slots(bot, frame)[slot]
                    and (slot != 0 or not any(slots(bot, frame))),
                    "点击减号后未确认英雄已移除")


def prepare_squad(bot, serial, image, *, flow="follow"):
    """Keep driver heroes, validate follower heroes, or explicitly clear all heroes."""
    if flow not in ("drive", "follow", "follow_no_heroes"):
        raise ValueError("打熊模式必须是 drive、follow 或 follow_no_heroes")
    if flow == "follow_no_heroes" and dispatch_target(bot, image) is not None:
        controls = adapted_hero_controls(bot, image)
        if controls is not None:
            for slot in range(3):
                if controls[slot][0]:
                    bot.task_tap(serial, *controls[slot][1])
                    image = wait_for(bot, serial,
                                     lambda frame: (adapted_hero_controls(bot, frame) is not None
                                         and not any(state for state, _ in adapted_hero_controls(bot, frame)))
                                     if slot == 0 else (adapted_hero_controls(bot, frame) is not None
                                         and not adapted_hero_controls(bot, frame)[slot][0]),
                                     "点击减号后未确认英雄已移除")
                    controls = adapted_hero_controls(bot, image)
            if any(state for state, _ in controls):
                raise RuntimeError("无英雄跟车出征前仍有英雄")
            return image
    if not squad_page(bot, image):
        raise RuntimeError("配置打熊阵容前未确认出征界面")
    if flow == "drive":
        return image
    if flow == "follow_no_heroes":
        for slot in range(3):
            if slots(bot, image)[slot]:
                image = remove(bot, serial, image, slot)
        if any(slots(bot, image)):
            raise RuntimeError("无英雄跟车出征前仍有英雄")
        return image
    dy, occupied = squad_layout(bot, image)
    first = hero_name(bot, image.crop(shifted(FACE_BOX, dy=dy))) if occupied[0] else None
    if first is None:
        for slot in range(3):
            if slots(bot, image)[slot]:
                image = remove(bot, serial, image, slot)
        dy, _ = squad_layout(bot, image)
        bot.task_tap(serial, 254, 705 + dy)
        image = wait_for(bot, serial, lambda frame: selection_page(bot, frame), "未打开英雄选择界面")
        image = fill_selection(bot, serial, image, need_first=True)
    else:
        forbidden = [slot for slot in (1, 2) if occupied[slot]
                     and hero_name(bot, image.crop(shifted(FACE_BOX, SLOT_OFFSETS[slot], dy))) is not None]
        for slot in reversed(forbidden):
            image = remove(bot, serial, image, slot)
        if forbidden:
            slot = min(forbidden)
            dy, _ = squad_layout(bot, image)
            bot.task_tap(serial, 254 + SLOT_OFFSETS[slot], 705 + dy)
            image = wait_for(bot, serial, lambda frame: selection_page(bot, frame), "未打开后排英雄选择界面")
            image = fill_selection(bot, serial, image, need_first=False)
    dy, occupied = squad_layout(bot, image)
    if any(occupied):
        if not occupied[0] or hero_name(bot, image.crop(shifted(FACE_BOX, dy=dy))) is None:
            raise RuntimeError("出征前首英雄未通过打熊名单检查")
        if any(occupied[slot] and hero_name(bot, image.crop(shifted(FACE_BOX, SLOT_OFFSETS[slot], dy)))
               is not None for slot in (1, 2)):
            raise RuntimeError("出征前后两位英雄仍包含打熊首英雄")
    return image


def rally_page(bot, image):
    if matches(bot, image, "rally-title", (435, 850, 655, 914)) and matches(
            bot, image, "rally-start", (345, 1490, 735, 1570)):
        return True
    return (bot.intelligence_text_target(image, ("发起集结",), (350, 800, 760, 990)) is not None
            and bot.intelligence_text_target(image, ("发起集结",), (300, 1400, 780, 1620)) is not None)


def troop_limit_confirmation(bot, image):
    text = "".join(re.sub(r"\s+", "", value) for value, _ in
                   bot.recognize_text(image.crop((180, 1080, 920, 1280))))
    if "士兵超过出征上限" not in text:
        return None
    target = bot.intelligence_text_target(image, ("确定",), (300, 1350, 780, 1620))
    if target is None:
        raise RuntimeError("出征上限弹窗未确认确定按钮")
    return target


def leave_over_limit_rally(bot, serial, image, target):
    bot.task_tap(serial, *target)
    wait_for(bot, serial, lambda frame: squad_page(bot, frame), "确认出征上限后未返回编队界面")
    bot.task_back(serial)
    image = wait_for(bot, serial, lambda frame: war_page(bot, frame), "退出超限队伍后未返回联盟战争列表")
    bot.print("士兵超过出征上限，已退出当前队伍，尝试另一只熊队伍", flush=True)
    return image


def wait_for_join(bot, serial):
    """A full/departed rally can reject Join while leaving the list visible."""
    list_frames = 0
    for _ in range(8):
        image = bot.task_screen(serial)
        if squad_page(bot, image):
            return image, True
        list_frames = list_frames + 1 if war_page(bot, image) else 0
        if list_frames >= 3:
            return image, False
    # A rally can disappear while Join is opening; the game may return to
    # the target detail or wilderness instead of retaining the war list.
    if bot.town_button(image):
        if bear_detail_target(bot, image) is not None:
            bot.task_back(serial)
            image = wait_for(bot, serial, lambda frame: bot.town_button(frame)
                             and bear_detail_target(bot, frame) is None,
                             "退出失效熊队伍后未返回野外")
        bot.print("加入熊队伍后已返回野外，稍后重新检查队伍", flush=True)
        return image, False
    error = RuntimeError("加入熊队伍后未进入编队界面")
    error.diagnostic_screenshot = bot.save_task_failure(serial, "自动打熊", "加入队伍")
    raise error


def follow_rally(bot, serial, image, *, max_attempts=4, flow="follow"):
    """Try another leader after an over-limit rejection, without changing troops."""
    skipped_leaders = set()
    for attempt in range(max_attempts):
        if not war_page(bot, image):
            raise RuntimeError("自动上车前未确认联盟战争列表")
        candidate = None
        for point in bear_join_targets(bot, image):
            # Leader captions move with their row when the list refreshes.
            leader = "".join(re.sub(r"\s+", "", text) for text, _ in
                             bot.recognize_text(image.crop((430, point[1] - 210, 900, point[1] - 135))))
            if leader and leader not in skipped_leaders:
                candidate = point, leader
                break
        if candidate is None:
            if attempt + 1 < max_attempts:
                bot.adb(serial, "shell", "input", "swipe", "550", "1840", "550", "740", "450")
                image = bot.task_screen(serial)
                continue
            bot.print("当前没有其他可加入的熊队伍", flush=True)
            return image, False
        point, leader = candidate
        skipped_leaders.add(leader)
        bot.print(f"自动上车：{leader}", flush=True)
        bot.task_tap(serial, *point)
        image, entered = wait_for_join(bot, serial)
        if not entered:
            if not war_page(bot, image):
                return image, False
            bot.print("熊队伍已满、已出发或加入未生效，重新检查其他队伍", flush=True)
            continue
        if not bear_squad_target(bot, image, verified_entry=True):
            # A refreshed list can move another target under the tapped plus.
            bot.task_back(serial)
            image = wait_for(bot, serial, lambda frame: war_page(bot, frame), "目标变化后未返回联盟战争列表")
            continue
        image, issued = dispatch(bot, serial, image, flow=flow)
        if issued or not war_page(bot, image):
            return image, issued
    return image, False


def dispatch(bot, serial, image, *, flow="follow"):
    image = prepare_squad(bot, serial, image, flow=flow)
    target = dispatch_target(bot, image)
    if target is None:
        raise RuntimeError("未确认打熊出征按钮")
    bot.task_tap(serial, *target)
    for _ in range(10):
        image = bot.task_screen(serial)
        if flow != "drive":
            confirmation = troop_limit_confirmation(bot, image)
            if confirmation is not None:
                return leave_over_limit_rally(bot, serial, image, confirmation), False
        reason = bot.hunting.notice(bot, image)
        if reason in ("体力不足", "士兵不足"):
            bot.print(f"打熊暂不能出征：{reason}", flush=True)
            return bot.hunting.leave(bot, serial, image), False
        if flow != "drive" and war_page(bot, image):
            return image, True
        if bot.town_button(image) and not bot.gather_search_page(image) and not squad_page(bot, image):
            return image, True
    raise RuntimeError("点击出征后未确认返回野外或联盟战争列表，需检查当前行军队列")


def start_rally(bot, serial, image):
    if not rally_page(bot, image):
        raise RuntimeError("未确认发起集结弹窗")
    bot.task_tap(serial, 540, 1538)
    image = wait_for(bot, serial, lambda frame: squad_page(bot, frame), "发起集结后未进入出征界面")
    if not bear_squad_target(bot, image, verified_entry=True):
        raise RuntimeError("主动开车未确认出征目标为变异巨熊")
    return dispatch(bot, serial, image, flow="drive")


def icon_target(bot, image, name, box, *, adapt=True):
    """Find sampled event shortcuts within their observed sidebar area."""
    template = np.asarray(asset(bot, name))
    options = {}
    if name == "war-entry":
        # The sidebar badge and map behind the flag change between frames.
        # Compare the flag's center rather than those variable border pixels.
        mask = np.zeros(template.shape[:2], dtype=np.uint8)
        mask[20:65, 20:65] = 255
        options["mask"] = mask
    scores = cv2.matchTemplate(np.asarray(image.convert("RGB").crop(box)), template,
                               cv2.TM_SQDIFF_NORMED, **options)
    scores = np.nan_to_num(scores, nan=1, posinf=1, neginf=1)
    score, _, (x, y), _ = cv2.minMaxLoc(scores)
    if score > .035:
        if adapt:
            from screen_geometry import scene_view
            view = scene_view(image)
            if view is not None:
                frame, to_logical, _ = view
                point = icon_target(bot, frame, name, box, adapt=False)
                if point is not None:
                    return to_logical(*point)
        return None
    return box[0] + x + template.shape[1] // 2, box[1] + y + template.shape[0] // 2


def bear_detail_target(bot, image):
    target = icon_target(bot, image, "rally", (150, 1200, 950, 2100))
    if target is None:
        return None
    labels = [re.sub(r"\s+", "", text) for text, _ in
              bot.recognize_text(image.crop((200, 1200, 900, 1700)))]
    return target if any(re.fullmatch(r"(?:等级\d+)?变异巨熊", label) for label in labels) else None


def drive_rally(bot, serial, image):
    if not bot.town_button(image):
        raise RuntimeError("主动开车前未确认野外界面")
    target = icon_target(bot, image, "entry", (750, 1500, 930, 2110))
    if target is None:
        return image, False
    bot.task_tap(serial, *target)
    # The event shortcut centers the alliance trap before its detail is opened.
    image = wait_for(bot, serial, lambda frame: bot.town_button(frame), "定位巨熊后未返回野外")
    image = bot.task_screen(serial)
    if bear_detail_target(bot, image) is None:
        bot.task_tap(serial, 540, 1030)
        image = wait_for(bot, serial, lambda frame: bear_detail_target(bot, frame) is not None,
                         "定位巨熊后未确认变异巨熊的集结按钮")
    bot.task_tap(serial, *bear_detail_target(bot, image))
    image = wait_for(bot, serial, lambda frame: rally_page(bot, frame), "点击巨熊集结后未确认发起集结弹窗")
    return start_rally(bot, serial, image)


class ReconnectRequired(Exception):
    """Interrupt a bear flow before further navigation or dispatch taps."""


class _ConnectedBot:
    def __init__(self, bot, text_template, reconnect_template):
        self.bot = bot
        self.text_template = text_template
        self.reconnect_template = reconnect_template

    def __getattr__(self, name):
        return getattr(self.bot, name)

    def task_screen(self, serial):
        image = self.bot.task_screen(serial)
        # Reconnect templates are grayscale; hero recognition needs the RGB frame.
        if (self.bot.forced_offline(image.convert("L"), self.text_template, self.reconnect_template)
                or self.bot.network_reconnect_target(image) is not None):
            raise ReconnectRequired()
        if not self.bot.game_foreground(serial):
            raise RuntimeError("当前前台不是目标游戏，自动打熊已停止")
        return image


def leave_bear_pages(bot, serial):
    """Unwind known bear pages before the shared city recovery's slower fallback."""
    for _ in range(6):
        image = bot.task_screen(serial)
        if selection_page(bot, image):
            bot.task_tap(serial, 960, 507)
        elif squad_page(bot, image) or war_page(bot, image) or rally_page(bot, image):
            bot.task_back(serial)
        else:
            return image
    raise RuntimeError("退出打熊页面后未确认返回主城或野外")


def run(bot, serial, help_template, nav_template, text_template, reconnect_template,
        *, modes=("follow",), reconnect=True, dry_run=False, forever=True, seconds=120, interval=2):
    """Exclusive, cancellable bear loop using the shared lease/control/reconnect runtime."""
    if not modes or any(mode not in ("drive", "follow", "follow_no_heroes") for mode in modes):
        raise ValueError("自动打熊至少要选择主动开车或自动跟车")
    backend = bot
    previous = bot.TASK_HELP, bot.WILDERNESS_HEALING
    bot.TASK_HELP = bot.WILDERNESS_HEALING = None
    bot.retryable_tasks(())
    bot = _ConnectedBot(bot, text_template, reconnect_template)

    def report(status, reason="", delay=None):
        bot.emit_task_event(kind="task", name="bear", status=status, reason=reason,
                            next_run=bot.time.time() + delay if delay is not None else None)

    try:
        bot.check_stop()
        if dry_run:
            image = backend.color_screenshot(serial)
            bot.print(f"自动打熊只观察模式；联盟战争页面：{war_page(bot, image)}；"
                      f"打熊方式：{'、'.join(modes)}", flush=True)
            return
        bot.print("自动打熊已启用，其他任务已跳过；停止程序可结束打熊", flush=True)
        deadline = bot.time.monotonic() + seconds
        entered, turn = False, 0
        while forever or bot.time.monotonic() < deadline:
            bot.check_stop()
            try:
                if not entered:
                    report("running", "检查主城并准备打熊")
                    leave_bear_pages(bot, serial)
                    if not bot.return_to_city(serial, help_template, nav_template, text_template, reconnect_template):
                        report("retrying", "强制下线，等待自动重连")
                        bot.run(serial, 1, 2, False, float("inf"), help_template, nav_template,
                                text_template, reconnect_template, announce_end=False,
                                reconnect=reconnect, help_enabled=False, return_on_reconnect=True)
                    bot.print("自动打熊主城检查已通过，开始运行任务", flush=True)
                    entered = True
                image = bot.task_screen(serial)
                if war_page(bot, image):
                    bot.task_back(serial)
                    image = wait_for(bot, serial, lambda frame: bot.town_button(frame), "退出联盟战争后未确认野外")
                image = bot.enter_wilderness(serial, image, help_template, nav_template, "自动打熊")
                # Recheck through the guarded reader after shared navigation.
                image = bot.task_screen(serial)
                if not bot.town_button(image):
                    raise RuntimeError("自动打熊前未确认野外界面")
                delay = max(2, interval)
                reason = "野外行军队列已满，等待空闲队列"
                if bot.intelligence_queue_full(image):
                    delay = max(10, delay)
                else:
                    mode = modes[turn % len(modes)]
                    turn += 1
                    report("running", "主动开车" if mode == "drive" else ("无英雄跟车" if mode == "follow_no_heroes" else "自动跟车"))
                    if mode == "drive":
                        image, issued = drive_rally(bot, serial, image)
                        reason = "本轮开车已出征" if issued else "暂无可用的打熊入口，等待活动或队伍"
                    else:
                        target = icon_target(bot, image, "war-entry", (920, 400, 1080, 1700))
                        issued = False
                        if target is not None:
                            bot.task_tap(serial, *target)
                            image = wait_for(bot, serial, lambda frame: war_page(bot, frame), "未进入联盟战争列表")
                            image, issued = follow_rally(bot, serial, image, **({"flow": mode} if mode == "follow_no_heroes" else {}))
                        reason = "本轮跟车已出征" if issued else "暂无可加入的巨熊队伍，等待复查"
                    if issued:
                        bot.print(reason, flush=True)
                report("waiting", reason, delay)
                bot.time.sleep(delay)
            except ReconnectRequired:
                report("retrying", "连接中断，恢复主城后继续打熊")
                entered = False
        report("stopped", "本次打熊运行时间已结束")
    except (StopRequested, KeyboardInterrupt):
        report("stopped", "自动打熊已停止")
        raise
    except Exception as exc:
        report("failed", str(exc))
        raise
    finally:
        backend.TASK_HELP, backend.WILDERNESS_HEALING = previous
