"""Exercise anchored UI adaptation on one explicitly selected, leased device."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import help_bot as bot
from device_lease import DeviceLease
from emulator_runtime import avd_key


def main():
    bot.configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--steps", nargs="+", default=["explore", "train", "donate", "recruit", "warehouse", "treasure", "pet", "heal"],
                        choices=("explore", "train", "donate", "recruit", "warehouse", "treasure", "pet", "heal", "gather",
                                 "stamina", "tree", "dawn", "task_rewards", "intelligence", "hunt", "help", "bear",
                                 "alliance_treasure", "polar_beast", "gather_coal", "gather_iron", "gather_meat",
                                 "train_upgrade"))
    args = parser.parse_args()

    def asset(name, mode="L"):
        return bot.open_template(bot.ROOT / f"assets/emulator-{name}.png").convert(mode)

    for variable, name in (("CHAT_TEMPLATE", "chat-title"), ("TOWN_TEMPLATE", "town-button"),
                           ("RECONNECT_PROMO_CLOSE", "reconnect-promo-close"),
                           ("RECONNECT_WELCOME_CLOSE", "reconnect-welcome-close"), ("ACTIVITY_CLOSE", "activity-close")):
        setattr(bot, variable, asset(name))
    help_icon, nav, offline, reconnect = [asset(name) for name in ("help-icon", "city-nav", "forced-text", "reconnect")]
    drawer = asset("drawer-open", "RGB")
    templates = {name: asset(name, "RGB") for name in (*bot.TASK_BOXES, "donate-label", "free-label", "thumb")}
    titles = {unit: asset(f"training-{unit}-title") for unit in bot.UNIT_TYPES}
    timers = {}
    serial = args.device

    def alliance_treasure():
        # Inspect shared rewards even when the own-pet drawer row has no
        # ready hint. Navigate only through a freshly observed task label.
        image = bot.reset_drawer(serial, bot.color_screenshot(serial), templates)
        box = (240, 710, 550, 1590)
        for attempt in range(bot.DRAWER_SWIPES + 1):
            rows = bot.recognize_text(image.crop(box))
            bottom = next((round(box[1] + y + height) for text, (_, y, _, height) in rows
                           if text.replace(" ", "") == "宠物寻宝"), None)
            if bottom is not None and bottom + 15 < box[3]:
                bot.task_tap(serial, 605, bottom + 15)
                break
            if attempt < bot.DRAWER_SWIPES:
                image = bot.swipe_drawer(serial, drawer)
        else:
            raise RuntimeError("专项验证未定位到宠物寻宝任务行")
        for _ in range(6):
            image = bot.task_screen(serial)
            if bot.treasure_page(image):
                break
        else:
            raise RuntimeError("专项验证未确认宠物寻宝页面")
        notified = bot.red_dot(image, (980, 2150, 1020, 2190))
        image = bot.claim_alliance_treasure(serial, image)
        print(f"联盟宝藏专项检查：领取提示={notified}", flush=True)
        return bot.leave_treasure(serial, help_icon, nav, templates)

    operations = {
        "explore": lambda: bot.run_explore(serial, help_icon, nav, {name: asset(f"explore-{name}") for name in bot.EXPLORE_BOXES}, timers),
        "train": lambda: bot.run_training(serial, help_icon, nav, drawer, titles, timers=timers),
        "train_upgrade": lambda: bot.run_training(serial, help_icon, nav, drawer, titles, timers=timers, upgrade=True),
        "donate": lambda: bot.run_city_tasks(serial, help_icon, nav, templates, recruit=False),
        "recruit": lambda: [bot.run_city_tasks(serial, help_icon, nav, templates, donate=False, recruit_kind=kind, timers=timers)
                            for kind in bot.RECRUIT_TYPES],
        "warehouse": lambda: bot.run_rewards(serial, help_icon, nav, drawer, asset("warehouse-title", "RGB"),
                                              asset("tree-collect-label", "RGB"), asset("island-title"),
                                              warehouse=True, tree=False, dawn=False, timers=timers),
        "treasure": lambda: bot.run_treasure(serial, help_icon, nav, drawer),
        "alliance_treasure": alliance_treasure,
        "pet": lambda: [bot.run_pet_skill(serial, help_icon, nav, drawer, name, timers) for name in bot.PET_SKILLS],
        "heal": lambda: bot.run_healing(serial, help_icon, nav),
        "gather": lambda: bot.run_gather(serial, help_icon, nav, offline, reconnect,
                                         resources=("wood",), levels={"wood": 7}),
        **{f"gather_{resource}": (lambda resource=resource, level=level: bot.run_gather(
            serial, help_icon, nav, offline, reconnect, resources=(resource,), levels={resource: level}))
           for resource, level in (("coal", 7), ("iron", 1), ("meat", 7))},
        "stamina": lambda: bot.run_stamina(serial, help_icon, nav, drawer, timers),
        "tree": lambda: bot.run_rewards(serial, help_icon, nav, drawer, asset("warehouse-title", "RGB"),
                                         asset("tree-collect-label", "RGB"), asset("island-title"),
                                         warehouse=False, tree=True, dawn=False, timers=timers),
        "dawn": lambda: bot.run_rewards(serial, help_icon, nav, drawer, asset("warehouse-title", "RGB"),
                                         asset("tree-collect-label", "RGB"), asset("island-title"),
                                         warehouse=False, tree=False, dawn=True, timers=timers),
        "task_rewards": lambda: bot.task_rewards.run(bot, serial, help_icon, nav),
        "intelligence": lambda: bot.run_intelligence(serial, help_icon, nav, offline, reconnect,
                                                       timers=timers, bounty=True, bounty_state={}),
        "hunt": lambda: bot.run_hunt(serial, help_icon, nav, monsters=("beast", "polar_beast"),
                                      levels={"beast": 16, "polar_beast": 2}, timers=timers),
        "polar_beast": lambda: bot.run_hunt(serial, help_icon, nav, monsters=("polar_beast",),
                                             levels={"polar_beast": 2}, timers=timers),
        "help": lambda: bot.run(serial, 25, 2, False, 3, help_icon, nav, offline, reconnect),
        "bear": lambda: [bot.bear_hunting.run(bot, serial, help_icon, nav, offline, reconnect,
                                              modes=(mode,), forever=False, seconds=1) for mode in ("drive", "follow")],
    }
    with DeviceLease(serial, avd_key(serial)):
        for stage in args.steps:
            print(f"START {serial} {stage}", flush=True)
            try:
                if not bot.return_to_city(serial, help_icon, nav, offline, reconnect):
                    raise RuntimeError("设备处于强制下线等待，未执行验证")
                operations[stage]()
                if not bot.return_to_city(serial, help_icon, nav, offline, reconnect):
                    raise RuntimeError("任务后未返回主城")
                print(f"VERIFIED {serial} {stage}", flush=True)
            except Exception:
                print(bot.save_task_failure(serial, "屏幕适配", stage), flush=True)
                raise


if __name__ == "__main__":
    main()
