"""Verify the reported daily-task failures on one explicitly selected emulator."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot


def asset(name, mode="L"):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as image:
        return image.convert(mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--steps", nargs="+", choices=("recover", "stamina", "training", "donation", "crystals", "treasure", "monitor"),
                        default=["recover", "stamina", "training", "donation", "crystals", "treasure", "monitor"])
    args = parser.parse_args()
    for variable, name in (("CHAT_TEMPLATE", "chat-title"), ("TOWN_TEMPLATE", "town-button"),
                           ("RECONNECT_PROMO_CLOSE", "reconnect-promo-close"),
                           ("RECONNECT_WELCOME_CLOSE", "reconnect-welcome-close"), ("ACTIVITY_CLOSE", "activity-close")):
        setattr(bot, variable, asset(name))
    help_icon, nav, offline, reconnect = [asset(name) for name in ("help-icon", "city-nav", "forced-text", "reconnect")]
    drawer = asset("drawer-open", "RGB")
    templates = {name: asset(name, "RGB") for name in (*bot.TASK_BOXES, "donate-label", "free-label", "thumb")}
    titles = {unit: asset(f"training-{unit}-title") for unit in bot.UNIT_TYPES}
    bot.TASK_HELP = help_icon, nav, drawer
    original = bot.task_screen
    count = 0
    stage = "initial"

    def capture(serial, **kwargs):
        nonlocal count
        image = original(serial, **kwargs)
        count += 1
        image.save(bot.ROOT / f"artifacts/windows-runtime-{args.device}-{stage}-{count:03}.png")
        print(f"frame={count} stage={stage}", flush=True)
        return image

    bot.task_screen = capture
    timers = {}
    operations = {
        "recover": lambda: bot.recover_task_city(args.device, help_icon, nav, offline, reconnect),
        "stamina": lambda: bot.run_stamina(args.device, help_icon, nav, drawer, timers),
        "training": lambda: bot.run_training(args.device, help_icon, nav, drawer, titles, timers=timers, upgrade=True),
        "donation": lambda: bot.run_city_tasks(args.device, help_icon, nav, templates, recruit=False),
        "crystals": lambda: bot.run_rewards(args.device, help_icon, nav, drawer, asset("warehouse-title", "RGB"),
                                            asset("tree-collect-label", "RGB"), asset("island-title"),
                                            warehouse=False, tree=True, dawn=False, tree_threshold=1000, timers=timers),
        "treasure": lambda: bot.run_treasure(args.device, help_icon, nav, drawer),
        "monitor": lambda: bot.run(args.device, 35, 2, False, float("inf"), help_icon, nav, offline, reconnect),
    }
    for stage in args.steps:
        print(f"START device={args.device} stage={stage}", flush=True)
        bot.recover_task_city(args.device, help_icon, nav, offline, reconnect)
        try:
            operations[stage]()
        except Exception:
            bot.color_screenshot(args.device).save(bot.ROOT / f"artifacts/windows-runtime-{args.device}-{stage}-failure.png")
            raise
        image = bot.color_screenshot(args.device)
        image.save(bot.ROOT / f"artifacts/windows-runtime-{args.device}-{stage}-result.png")
        if not bot.inspect(image.convert("L"), help_icon, nav)[0]:
            raise RuntimeError(f"{stage} 验证结束后未确认主城")
        print(f"VERIFIED device={args.device} stage={stage} city=True timers={timers}", flush=True)


if __name__ == "__main__":
    main()
