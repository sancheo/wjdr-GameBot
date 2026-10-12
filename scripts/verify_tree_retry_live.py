"""Verify crystal OCR and, optionally, exhausted retries on an explicit device."""
import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot


def asset(name, mode="L"):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as image:
        return image.convert(mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--save-report", action="store_true", help="Explicitly save the JSON verification report")
    parser.add_argument("--force-ocr-failure", action="store_true")
    args = parser.parse_args()
    bot.configure_stdio()
    for variable, name in (("CHAT_TEMPLATE", "chat-title"), ("TOWN_TEMPLATE", "town-button"),
                           ("RECONNECT_PROMO_CLOSE", "reconnect-promo-close"),
                           ("RECONNECT_WELCOME_CLOSE", "reconnect-welcome-close"),
                           ("ACTIVITY_CLOSE", "activity-close")):
        setattr(bot, variable, asset(name))
    help_icon, nav, offline, reconnect = [asset(name) for name in
                                        ("help-icon", "city-nav", "forced-text", "reconnect")]
    drawer, warehouse, tree = [asset(name, "RGB") for name in
                               ("drawer-open", "warehouse-title", "tree-collect-label")]
    island = asset("island-title")
    prefix = bot.ROOT / f"artifacts/tree-retry-{args.device}"
    result = {"device": args.device, "forced_ocr_failure": args.force_ocr_failure,
              "attempts": 0, "captures": [], "following_task_executed": False}
    bot.recover_task_city(args.device, help_icon, nav, offline, reconnect)

    def operation():
        result["attempts"] += 1
        return bot.run_rewards(args.device, help_icon, nav, drawer, warehouse, tree, island,
                               warehouse=False, dawn=False, tree_threshold=1000, keep_open=True)

    def following_task():
        result["following_task_executed"] = True

    save = bot.save_task_failure

    def capture(serial, label, attempt):
        assert result["attempts"] == 4, "Captured before third retry failed"
        diagnostic = save(serial, label, attempt)
        result["captures"].append(diagnostic)
        return diagnostic

    try:
        if args.force_ocr_failure:
            # Only quantity recognition is injected; taps, recovery, delays,
            # screenshots and scheduling use the connected emulator.
            with (patch.object(bot, "tree_drawer_amount", return_value=(None, None)),
                  patch.object(bot, "save_task_failure", side_effect=capture)):
                bot.run_schedule(args.device, 1, 2, False,
                                 {"tree": operation, "following": following_task},
                                 help_icon, nav, offline, reconnect, help_enabled=False)
            assert result["attempts"] == 4
            assert len(result["captures"]) == 1
            assert result["captures"][0].startswith("失败截图：")
            assert result["following_task_executed"]
        else:
            frame = operation()
            result["amount"], result["row_y"] = bot.tree_drawer_amount(frame, tree)
            frame.save(f"{prefix}-drawer.png")
            assert result["amount"] is not None
        frame = bot.recover_task_city(args.device, help_icon, nav, offline, reconnect)
        result["city"] = bot.inspect(frame.convert("L"), help_icon, nav)[0]
        frame.save(f"{prefix}-result.png")
        assert result["city"]
        print("VERIFIED", json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        if args.save_report:
            Path(f"{prefix}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
