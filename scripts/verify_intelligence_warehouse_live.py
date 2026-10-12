"""Verify warehouse claims and lighthouse navigation on a selected emulator."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot


def asset(name, mode="L"):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as im:
        return im.convert(mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--save-report", action="store_true", help="Explicitly save the JSON verification report")
    parser.add_argument("--tag", default="fixed")
    parser.add_argument("--phase", choices=("warehouse", "intelligence"), required=True)
    args = parser.parse_args()
    bot.configure_stdio()
    bot.TOWN_TEMPLATE = asset("town-button")
    help_template, nav = asset("help-icon"), asset("city-nav")
    offline, reconnect = asset("forced-text"), asset("reconnect")
    drawer = asset("drawer-open", "RGB")
    prefix = bot.ROOT / f"artifacts/5556-{args.phase}-{args.tag}"
    result = {"device": args.device, "phase": args.phase, "tag": args.tag}
    original_screen = bot.task_screen
    count = 0

    def capture(serial, **kwargs):
        nonlocal count
        im = original_screen(serial, **kwargs)
        count += 1
        im.save(f"{prefix}-{count:02}.png")
        return im

    bot.task_screen = capture
    started = time.monotonic()
    try:
        result["initial_recovery"] = bot.return_to_city(args.device, help_template, nav, offline, reconnect)
        timers = {}
        if args.phase == "warehouse":
            # The diagnostic exploration may have selected the wilderness
            # drawer tab. Put the verified drawer back on its town tab.
            image = bot.task_drawer(args.device, bot.color_screenshot(args.device), {"drawer-open": drawer})
            bot.task_tap(args.device, 173, 683)
            image = bot.task_screen(args.device)
            image = bot.run_rewards(args.device, help_template, nav, drawer,
                                    asset("warehouse-title", "RGB"), None, None,
                                    initial_image=image, tree=False, dawn=False, timers=timers)
        else:
            image = bot.run_intelligence(args.device, help_template, nav, offline, reconnect, timers=timers)
            result["task_ended_in_wilderness"] = bot.town_button(image)
            result["queue_full"] = bot.intelligence_queue_full(image)
            # Navigation can be verified even with full gathering queues;
            # this does not send troops or recall existing gathering armies.
            result["entry_target"] = bot.intelligence_entry_target(image)
            image = bot.navigate_intelligence(args.device, image)
            result["intelligence_page"] = bot.intelligence_page(image)
            image.save(f"{prefix}-page.png")
            recovery_start = time.monotonic()
            result["recovery_from_intelligence"] = bot.return_to_city(args.device, help_template, nav, offline, reconnect)
            result["recovery_seconds"] = round(time.monotonic() - recovery_start, 2)
            image = bot.color_screenshot(args.device)
        result["city"] = bot.inspect(image.convert("L"), help_template, nav)[0]
        result["timers_seconds"] = {name: round(deadline - time.monotonic()) for name, deadline in timers.items()}
        image.save(f"{prefix}-result.png")
    except Exception as error:
        result["error"] = str(error)
        bot.color_screenshot(args.device).save(f"{prefix}-failure.png")
        raise
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        if args.save_report:
            Path(f"{prefix}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
