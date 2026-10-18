"""Collect alliance gifts, loot and personal activity rewards on verified pages."""

import task_rewards
import cv2
import numpy as np


BOXES = {
    'title': (140, 160, 340, 225),
    'incentive-title': (140, 160, 340, 225),
    'loot-selected': (180, 710, 390, 765),
    'gifts-selected': (710, 710, 885, 765),
    'incentive-icon': (610, 2130, 670, 2190),
    'incentive-open': (280, 510, 780, 1000),
}
BADGES = {'entry': (985, 1040, 1055, 1090),
          'loot': (475, 665, 540, 710), 'gifts': (990, 665, 1040, 710)}
PERSONAL_BADGE = (1020, 1455, 1060, 1500)


def matches(bot, image, name):
    if name == 'incentive-icon':
        # The notification bubble pulses and shifts while the menu settles.
        region = np.asarray(image.crop((560, 2080, 720, 2250)).convert('L'))
        for variant in ('incentive-icon', 'incentive-icon-weekly'):
            template = bot.open_template(bot.ROOT / f'assets/emulator-alliance-chests-{variant}.png').convert('L')
            for scale in (.75, .8, .85, .9, .95, 1, 1.05, 1.1):
                resized = template.resize((round(template.width * scale), round(template.height * scale)))
                if cv2.minMaxLoc(cv2.matchTemplate(region, np.asarray(resized), cv2.TM_CCOEFF_NORMED))[1] >= .85:
                    return True
        return False
    template = bot.open_template(bot.ROOT / f'assets/emulator-alliance-chests-{name}.png').convert('L')
    return bot.minimum_difference(image.convert('L'), template, BOXES[name],
                                  range(-2, 3), range(-2, 3))[0] <= 12


def page(bot, image):
    if matches(bot, image, 'title'):
        return 'chests'
    if matches(bot, image, 'incentive-title'):
        return 'incentive'
    return None


def alliance(bot, image):
    templates = {name: bot.open_template(bot.ROOT / f'assets/emulator-{name}.png').convert('RGB')
                 for name in ('alliance-title', 'tech-title')}
    return (page(bot, image) is None and bot.task_page(image, templates, 'alliance-title')
            and not bot.task_page(image, templates, 'tech-title'))


def back_target(bot, image):
    return (60, 190) if page(bot, image) is not None else None


def wait(bot, serial, predicate, image=None):
    for _ in range(8):
        if image is None:
            image = bot.task_screen(serial, help_enabled=False)
        if predicate(image):
            return image
        # Claims can show a rewards overlay above either page.
        reward = bot.open_template(bot.ROOT / 'assets/emulator-reward-title.png').convert('RGB')
        if (bot.task_page(image, {'reward-title': reward}, 'reward-title')
                or bot.intelligence_text_target(image, ('点击任意位置退出', '点击任意位置'),
                                                 (220, 1850, 900, 2280))):
            bot.task_tap(serial, 540, 2110)
        image = None
    raise RuntimeError('联盟宝箱领取后未确认目标页面')


def selected(bot, image):
    if page(bot, image) != 'chests':
        return None
    for tab in ('loot', 'gifts'):
        if matches(bot, image, f'{tab}-selected'):
            return tab
    return None


def claim_tab(bot, serial, image, tab):
    label = '战利品宝箱' if tab == 'loot' else '盟友赠礼'
    for _ in range(120):
        bot.check_stop()
        image = wait(bot, serial, lambda im: selected(bot, im) == tab, image)
        target = task_rewards.green_claim(
            bot, image, ('一键领取',) if tab == 'loot' else ('领取',),
            (280, 2100, 800, 2340) if tab == 'loot' else (790, 960, 1030, 2130))
        if target is None:
            remaining = task_rewards.badge_count(bot, image, BADGES[tab])
            if remaining is None:
                raise RuntimeError(f'{label}剩余红点数字未识别')
            if remaining:
                raise RuntimeError(f'{label}仍有奖励角标，但未找到领取按钮')
            return image
        before = image
        bot.task_tap(serial, *target)
        for _ in range(8):
            image = wait(bot, serial, lambda im: selected(bot, im) == tab)
            # Countdown text and animated chests do not prove a successful claim.
            if (bot.difference(before.crop(BADGES[tab]), image.crop(BADGES[tab])) > 1
                    or task_rewards.green_claim(
                        bot, image, ('一键领取',) if tab == 'loot' else ('领取',),
                        (280, 2100, 800, 2340) if tab == 'loot' else
                        (790, max(950, target[1] - 40), 1030, target[1] + 40)) is None):
                break
        else:
            raise RuntimeError(f'{label}领取后未确认奖励状态变化')
        bot.print(f'已领取{label}', flush=True)
    raise RuntimeError(f'{label}领取次数超出安全上限')


def collect_on_alliance(bot, serial, image):
    image = wait(bot, serial, lambda im: alliance(bot, im), image)
    count = task_rewards.badge_count(bot, image, BADGES['entry'])
    if count is None:
        raise RuntimeError('联盟宝箱红点数字未识别')
    if count:
        bot.task_tap(serial, 820, 1135)
        image = wait(bot, serial, lambda im: selected(bot, im) is not None)
        tab = selected(bot, image)
        image = claim_tab(bot, serial, image, tab)
        other = 'gifts' if tab == 'loot' else 'loot'
        count = task_rewards.badge_count(bot, image, BADGES[other])
        if count is None:
            raise RuntimeError('联盟宝箱另一标签页红点数字未识别')
        if count:
            bot.task_tap(serial, 800 if other == 'gifts' else 290, 735)
            image = wait(bot, serial, lambda im: selected(bot, im) == other)
            image = claim_tab(bot, serial, image, other)
        bot.task_tap(serial, 60, 190)
        image = wait(bot, serial, lambda im: alliance(bot, im))
    if matches(bot, image, 'incentive-icon'):
        bot.task_tap(serial, 540, 2230)
        image = wait(bot, serial, lambda im: page(bot, im) == 'incentive')
        if matches(bot, image, 'incentive-open'):
            bot.task_tap(serial, 540, 950)
            for _ in range(8):
                image = wait(bot, serial, lambda im: page(bot, im) == 'incentive')
                if not matches(bot, image, 'incentive-open'):
                    break
            else:
                raise RuntimeError('联盟活跃激励宝箱领取后仍处于打开状态')
            bot.print('已领取联盟活跃激励每周宝箱', flush=True)
        if bot.red_dot(image, PERSONAL_BADGE):
            bot.task_tap(serial, 540, 1650)
            for _ in range(8):
                image = wait(bot, serial, lambda im: page(bot, im) == 'incentive')
                if not bot.red_dot(image, PERSONAL_BADGE):
                    break
            else:
                raise RuntimeError('个人活跃激励宝箱领取后红点未消失')
            bot.print('已领取个人活跃激励宝箱', flush=True)
        bot.task_tap(serial, 60, 190)
        image = wait(bot, serial, lambda im: alliance(bot, im))
    return image


def run(bot, serial, help_template, nav_template, initial_image=None, timers=None):
    image = initial_image if initial_image is not None else bot.task_screen(serial, help_enabled=False)
    if not alliance(bot, image):
        if not bot.inspect(image.convert('L'), help_template, nav_template)[0]:
            raise RuntimeError('未确认主城，未进入联盟领取宝箱')
        drawer = bot.open_template(bot.ROOT / 'assets/emulator-drawer-open.png').convert('RGB')
        if bot.task_page(image, {'drawer-open': drawer}, 'drawer-open'):
            bot.task_tap(serial, 695, 1100)
            image = bot.task_screen(serial, help_enabled=False)
        bot.task_tap(serial, 800, 2240)
        image = wait(bot, serial, lambda im: alliance(bot, im))
    image = collect_on_alliance(bot, serial, image)
    bot.task_tap(serial, 60, 190)
    image = wait(bot, serial, lambda im: bot.inspect(im.convert('L'), help_template, nav_template)[0])
    if timers is not None:
        timers['alliance_chests'] = bot.time.monotonic() + 600
    return image
