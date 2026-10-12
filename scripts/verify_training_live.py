"""Verify barracks recovery and one training pass on a selected emulator."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--recover-only", action="store_true")
    args = parser.parse_args()

    def asset(name, mode="L"):
        with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as image:
            return image.convert(mode)

    bot.CHAT_TEMPLATE = asset("chat-title")
    bot.RECONNECT_PROMO_CLOSE = asset("reconnect-promo-close")
    bot.RECONNECT_WELCOME_CLOSE = asset("reconnect-welcome-close")
    bot.ACTIVITY_CLOSE = asset("activity-close")
    bot.TOWN_TEMPLATE = asset("town-button")
    help_icon, nav = asset("help-icon"), asset("city-nav")
    offline, reconnect = asset("forced-text"), asset("reconnect")
    drawer = asset("drawer-open", "RGB")
    original = bot.task_screen
    count = 0

    def capture(serial):
        nonlocal count
        image = original(serial)
        count += 1
        image.save(bot.ROOT / f"artifacts/windows-training-{args.device}-{count:02}.png")
        target = bot.resource_more_target(image)
        if target:
            image.save(bot.ROOT / "artifacts/training-resource-shortage-live.png")
        print(f"frame={count} resource_shortage={target is not None}", flush=True)
        return image

    bot.task_screen = capture
    bot.color_screenshot(args.device).save(bot.ROOT / f"artifacts/windows-training-{args.device}-before.png")
    if not bot.return_to_city(args.device, help_icon, nav, offline, reconnect):
        raise RuntimeError("强制下线，未执行练兵验证")
    timers = {}
    if not args.recover_only:
        titles = {unit: asset(f"training-{unit}-title") for unit in bot.UNIT_TYPES}
        image = bot.run_training(args.device, help_icon, nav, drawer, titles, timers=timers)
    else:
        image = bot.color_screenshot(args.device)
    city = bot.inspect(image.convert("L"), help_icon, nav)[0]
    image.save(bot.ROOT / f"artifacts/windows-training-{args.device}-result.png")
    if not city:
        raise RuntimeError("练兵验证结束后未确认主城")
    print(f"VERIFIED device={args.device} city={city} timers={timers}", flush=True)


if __name__ == "__main__":
    main()
