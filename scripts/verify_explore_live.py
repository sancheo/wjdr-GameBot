"""Verify only exploration income and recovery on a selected emulator."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot


def asset(name):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as image:
        return image.convert("L")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    args = parser.parse_args()
    for variable, name in (("CHAT_TEMPLATE", "chat-title"), ("TOWN_TEMPLATE", "town-button"),
                           ("RECONNECT_PROMO_CLOSE", "reconnect-promo-close"),
                           ("RECONNECT_WELCOME_CLOSE", "reconnect-welcome-close"), ("ACTIVITY_CLOSE", "activity-close")):
        setattr(bot, variable, asset(name))
    help_icon, nav, offline, reconnect = [asset(name) for name in
                                        ("help-icon", "city-nav", "forced-text", "reconnect")]
    templates = {name: asset(f"explore-{name}") for name in bot.EXPLORE_BOXES}
    original = bot.task_screen
    count = 0

    def capture(serial, **kwargs):
        nonlocal count
        image = original(serial, **kwargs)
        count += 1
        image.save(bot.ROOT / f"artifacts/windows-explore-{serial}-{count:02}.png")
        print(f"frame={count} claim_state={bot.explore_claim_state(image, templates)} "
              f"back_target={bot.explore_back_target(image)}", flush=True)
        return image

    bot.task_screen = capture
    if not bot.return_to_city(args.device, help_icon, nav, offline, reconnect):
        raise RuntimeError("强制下线，未执行探险经验验证")
    # Enter only the exploration overview, then test its calibrated exit.
    bot.task_tap(args.device, 95, 2240)
    for _ in range(5):
        image = capture(args.device)
        if bot.explore_page(image, templates, "title"):
            break
    else:
        raise RuntimeError("恢复验证前未确认探险页")
    bot.recover_task_city(args.device, help_icon, nav, offline, reconnect)
    print(f"VERIFIED device={args.device} explore_recovery=True", flush=True)
    try:
        image = bot.run_explore(args.device, help_icon, nav, templates)
    except Exception:
        bot.color_screenshot(args.device).save(bot.ROOT / f"artifacts/windows-explore-{args.device}-failure.png")
        raise
    city = bot.inspect(image.convert("L"), help_icon, nav)[0]
    image.save(bot.ROOT / f"artifacts/windows-explore-{args.device}-result.png")
    if not city:
        raise RuntimeError("探险验证结束后未确认主城")
    print(f"VERIFIED device={args.device} city={city}", flush=True)


if __name__ == "__main__":
    main()
