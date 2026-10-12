"""Run ordinary intelligence on one explicitly selected emulator, retaining evidence."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--save-report", action="store_true", help="Explicitly save the JSON verification report")
    args = parser.parse_args()
    bot.configure_stdio()
    prefix = bot.ROOT / "artifacts/intel-shape-live"
    result = {"device": args.device, "scans": [], "executions": [], "bounty_enabled": False}
    start = time.monotonic()
    original_target, original_execute = bot.intelligence_target, bot.execute_intelligence

    def scan(image, excluded=()):
        before = time.perf_counter()
        target = original_target(image, excluded)
        elapsed = time.perf_counter() - before
        result["scans"].append({"target": target, "pending": list(excluded), "seconds": round(elapsed, 3)})
        image.save(f"{prefix}-map-{len(result['scans']):02}.png")
        print(f"外形扫描 {elapsed:.3f} 秒，下一任务 {target}，已派遣 {len(excluded)}", flush=True)
        return target

    def execute(serial, image, bounty_trip=None):
        assert bounty_trip is None, "This verifier never challenges master bounties"
        assert bot.bounty_name(image) is None, "Master bounty detected in ordinary flow"
        image.save(f"{prefix}-detail-{len(result['executions']) + 1:02}.png")
        before = time.monotonic()
        image, dispatched = original_execute(serial, image)
        result["executions"].append({"dispatched": dispatched, "seconds": round(time.monotonic() - before, 2)})
        return image, dispatched

    def asset(name):
        return Image.open(bot.ROOT / f"assets/emulator-{name}.png").convert("L")

    bot.TOWN_TEMPLATE = asset("town-button")
    bot.intelligence_target, bot.execute_intelligence = scan, execute
    try:
        bot.color_screenshot(args.device).save(f"{prefix}-before.png")
        timers = {}
        image = bot.run_intelligence(args.device, asset("help-icon"), asset("city-nav"),
                                     asset("forced-text"), asset("reconnect"),
                                     timers=timers, bounty=False)
        result["ended_in_wilderness"] = bot.town_button(image)
        # Inspect the final map even if all queues are occupied; no dispatch here.
        image = bot.navigate_intelligence(args.device, image)
        image.save(f"{prefix}-final-map.png")
        result["remaining_ordinary"] = original_target(image)
        result["remaining_bounty"] = bot.bounty_target(image)
        result["refresh_seconds"] = {key: round(value - time.monotonic()) for key, value in timers.items()}
        bot.task_tap(args.device, 60, 190)
        image = bot.task_screen(args.device)
        image.save(f"{prefix}-result.png")
    except Exception as exc:
        result["error"] = str(exc)
        bot.color_screenshot(args.device).save(f"{prefix}-failure.png")
        raise
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - start, 2)
        if args.save_report:
            Path(f"{prefix}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
