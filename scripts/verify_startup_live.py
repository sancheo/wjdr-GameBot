"""Verify startup city recovery on an explicitly selected emulator."""
import argparse
import json
import sys
import time
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
    parser.add_argument("--save-report", action="store_true", help="Explicitly save the JSON verification report")
    parser.add_argument("--tag", default="startup")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    for variable, name in (("CHAT_TEMPLATE", "chat-title"), ("TOWN_TEMPLATE", "town-button"),
                           ("RECONNECT_PROMO_CLOSE", "reconnect-promo-close"),
                           ("RECONNECT_WELCOME_CLOSE", "reconnect-welcome-close"),
                           ("ACTIVITY_CLOSE", "activity-close")):
        setattr(bot, variable, asset(name))
    templates = [asset(name) for name in ("help-icon", "city-nav", "forced-text", "reconnect")]
    prefix = bot.ROOT / f"artifacts/windows-startup-{args.device}-{args.tag}"
    before = bot.color_screenshot(args.device)
    before.save(f"{prefix}-before.png")
    print(f"device={args.device} foreground={bot.game_foreground(args.device)} "
          f"popup_target={bot.reconnect_popup_target(before)}", flush=True)
    started = time.monotonic()
    result = {"device": args.device, "tag": args.tag}
    try:
        result["recovered"] = bot.return_to_city(args.device, *templates, timeout=args.timeout)
        after = bot.color_screenshot(args.device)
        result["city"], _, result["nav_difference"] = bot.inspect(after.convert("L"), *templates[:2])
        if not result["recovered"] or not result["city"]:
            raise RuntimeError("启动恢复未确认主城")
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        bot.color_screenshot(args.device).save(f"{prefix}-after.png")
        if args.save_report:
            Path(f"{prefix}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
