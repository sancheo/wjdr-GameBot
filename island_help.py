"""Visit only badged islands, tend their trees, and like unliked islands."""

import cv2
import numpy as np

from screen_geometry import scene_view


def view(image):
    scene = scene_view(image)
    return (scene[0], scene[1]) if scene else (image, lambda x, y: (x, y))


def features(image, name):
    if name == 'visitor':
        return cv2.Canny(np.asarray(image.convert('L')), 70, 140)
    pixels = np.asarray(image.convert('RGB')).astype(np.int16)
    if name == 'heart-red':
        r, g, b = pixels.transpose(2, 0, 1)
        return ((r > 180) & (r > g * 1.45) & (r > b * 1.45)).astype(np.uint8) * 255
    if name in ('heart-white', 'trophy', 'title', 'visit'):
        return (pixels.min(axis=2) > 190).astype(np.uint8) * 255
    return pixels.astype(np.uint8)


def locate(bot, image, name, box, threshold=.90, feature_name=None):
    scene, to_logical = view(image)
    template = bot.open_template(bot.ROOT / f'assets/emulator-island-help-{name}.png').convert('RGB')
    feature_name = feature_name or name
    result = cv2.matchTemplate(features(scene.crop(box), feature_name), features(template, feature_name),
                               cv2.TM_CCOEFF_NORMED)
    _, score, _, point = cv2.minMaxLoc(result)
    if score < threshold:
        return None
    return to_logical(round(box[0] + point[0] + template.width / 2),
                      round(box[1] + point[1] + template.height / 2))


def ranking(bot, image):
    return locate(bot, image, 'title', (100, 140, 470, 390)) is not None


def trophy(bot, image):
    return locate(bot, image, 'trophy', (930, 220, 1080, 500))


def visitor(bot, image):
    # The warning control can have a grey background. Its white symbol stays
    # unchanged, while Canny also includes the changing background edges.
    return (trophy(bot, image) is not None
            and (locate(bot, image, 'visitor', (930, 1350, 1080, 2000), .78) is not None
                 or locate(bot, image, 'visitor', (930, 1350, 1080, 2000), .90,
                           feature_name='heart-white') is not None))


def trophy_badge(bot, image):
    target = trophy(bot, image)
    if target is None:
        raise RuntimeError('海岛页面未确认奖杯图标，未判断红点')
    scene, _ = view(image)
    # Locate the trophy again in scene coordinates to inspect its top-right corner.
    transform = scene_view(image)
    x, y = transform[2](*target) if transform else target
    return badge(scene, (x + 20, y - 65, x + 65, y - 15))


def badge(image, box):
    """A small bright red circle, excluding the podium characters' clothing."""
    pixels = np.asarray(image.crop(box).convert('RGB'))
    r, g, b = pixels.transpose(2, 0, 1)
    mask = ((r > 220) & (g < 95) & (b < 110)).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    return any(8 <= w <= 32 and 8 <= h <= 32 and .65 <= w / h <= 1.5
               and 70 <= area <= 900 and area / (w * h) >= .55
               for x, y, w, h, area in stats[1:])


def visit_target(bot, image):
    """Require a white person/eye icon and a red badge belonging to that icon."""
    scene, to_logical = view(image)
    template = bot.open_template(bot.ROOT / 'assets/emulator-island-help-visit.png').convert('RGB')
    # Include podium entries and the scrollable list, excluding the pinned self row.
    box = (250, 600, 1040, 1910)
    pixels = features(scene.crop(box), 'visit')
    candidates = []
    # The podium uses smaller versions of the same person/eye control.
    for scale in (.85, .9, 1):
        resized = template.resize((round(template.width * scale), round(template.height * scale)))
        scores = cv2.matchTemplate(pixels, features(resized, 'visit'), cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(scores >= .88)
        for y, x in zip(ys, xs):
            candidates.append((float(scores[y, x]), int(x + box[0]), int(y + box[1]),
                               resized.width, resized.height, scale))
    found = []
    for score, left, top, width, height, scale in sorted(candidates, reverse=True):
        center = left + width // 2, top + height // 2
        if any(abs(center[0] - x) < 20 and abs(center[1] - y) < 20 for x, y in found):
            continue
        badge_box = tuple(round(value) for value in
                          (left + 40 * scale, top - 27 * scale, left + 90 * scale, top + 18 * scale))
        if badge(scene, badge_box):
            found.append(center)
    return to_logical(*min(found, key=lambda point: point[1])) if found else None


def tend_target(bot, image):
    return locate(bot, image, 'tend', (100, 450, 920, 1650), .88)


def heart(bot, image):
    for state in ('white', 'red'):
        target = locate(bot, image, f'heart-{state}', (930, 1050, 1080, 1750), .86 if state == 'white' else .92)
        if target is not None:
            return state, target
    # Snow behind the control merges with its white fill. The dark outline
    # still distinguishes the unliked heart from the differently shaded red one.
    target = locate(bot, image, 'heart-white', (930, 1050, 1080, 1750), .85,
                    feature_name='visitor')
    if target is not None:
        return 'white', target
    return None, None


def wait(bot, serial, predicate, image=None):
    for _ in range(12):
        if image is not None and predicate(image):
            return image
        image = bot.task_screen(serial, help_enabled=False)
    if predicate(image):
        return image
    raise RuntimeError('海岛互助操作后未确认目标状态')


def find_visit(bot, serial, image):
    previous = None
    unchanged = 0
    for _ in range(100):
        bot.check_stop()
        if not ranking(bot, image):
            raise RuntimeError('查找互助红点时已离开海岛繁荣度')
        target = visit_target(bot, image)
        if target is not None:
            return image, target
        scene, _ = view(image)
        content = scene.crop((80, 1190, 1020, 1900)).convert('L')
        unchanged = unchanged + 1 if previous is not None and bot.difference(content, previous) < 1 else 0
        if unchanged >= 2:
            # A stale trophy badge must not trap the rest of the daily tasks.
            print('海岛繁荣度已检查至列表底部，未找到可互助红点', flush=True)
            return image, None
        previous = content
        _, to_logical = view(image)
        start, end = to_logical(580, 1850), to_logical(580, 1370)
        bot.adb(serial, 'shell', 'input', 'swipe', *map(str, (*start, *end)), '1400')
        image = bot.task_screen(serial, help_enabled=False)
    raise RuntimeError('海岛繁荣度扫描超过 100 页，未确认列表底部')


def home(bot, serial, image, island_template):
    if ranking(bot, image) or visitor(bot, image):
        bot.task_tap(serial, 60, 190)
        image = wait(bot, serial, lambda im: trophy(bot, im) is not None and not visitor(bot, im))
    if not bot.island_page(image, island_template):
        raise RuntimeError('互助结束后未确认返回我的海岛')
    return image


def run(bot, serial, image, island_template):
    completed = 0
    for _ in range(40):
        image = wait(bot, serial, lambda im: trophy(bot, im) is not None, image)
        # A retry can resume on the already opened, still untended island.
        if not (visitor(bot, image) and tend_target(bot, image) is not None):
            # Confirm absence on a fresh frame so a loading badge is not skipped.
            if not trophy_badge(bot, image):
                image = bot.task_screen(serial, help_enabled=False)
                if trophy(bot, image) is None:
                    continue
                if not trophy_badge(bot, image):
                    print(f'海岛奖杯无红点，互助结束，本次打理 {completed} 处', flush=True)
                    return home(bot, serial, image, island_template)
            bot.task_tap(serial, *trophy(bot, image))
            image = wait(bot, serial, lambda im: ranking(bot, im))
            image, target = find_visit(bot, serial, image)
            if target is None:
                return home(bot, serial, image, island_template)
            bot.task_tap(serial, *target)
            image = wait(bot, serial, lambda im: visitor(bot, im) and tend_target(bot, im) is not None)
        target = tend_target(bot, image)
        bot.task_tap(serial, *target)
        absent = 0
        for _ in range(12):
            image = bot.task_screen(serial, help_enabled=False)
            absent = absent + 1 if visitor(bot, image) and tend_target(bot, image) is None else 0
            if absent >= 2:
                break
        else:
            raise RuntimeError('点击海岛互助后树上图标仍未消失，未确认打理成功')
        completed += 1
        image = wait(bot, serial, lambda im: heart(bot, im)[0] is not None, image)
        state, target = heart(bot, image)
        if state == 'white':
            bot.task_tap(serial, *target)
            image = wait(bot, serial, lambda im: visitor(bot, im) and heart(bot, im)[0] == 'red')
            print('已确认海岛爱心由白色变为红色', flush=True)
        print(f'已确认第 {completed} 次海岛互助打理', flush=True)
    raise RuntimeError('海岛互助连续 40 次后仍有红点，请检查识别结果')
