"""Verify one free recruitment of each kind and recovery on a selected emulator."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot


def asset(name, mode="RGB"):
    with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as image:
        return image.convert(mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    args = parser.parse_args()
    bot.configure_stdio()
    help_icon, nav = asset("help-icon", "L"), asset("city-nav", "L")
    offline, reconnect = asset("forced-text", "L"), asset("reconnect", "L")
    bot.TOWN_TEMPLATE = asset("town-button", "L")
    templates = {name: asset(name) for name in (*bot.TASK_BOXES, "free-label")}
    prefix = bot.ROOT / f"artifacts/recruit-{args.device}"
    # Samples are regression fixtures, not a persistent verification report.
    capture = bot.task_screen
    seen = set()

    def screen(serial, **kwargs):
        image = capture(serial, **kwargs)
        if bot.task_page(image, templates, "recruit-title"):
            state = tuple(bot.recruit_button_state(image, templates, kind)[0] for kind in bot.RECRUIT_TYPES)
            if state not in seen:
                image.save(f"{prefix}-{'-'.join(state)}.png")
                seen.add(state)
                print(f"招募页状态：{state}", flush=True)
        return image

    bot.task_screen = screen
    if not bot.return_to_city(args.device, help_icon, nav, offline, reconnect):
        raise RuntimeError("强制下线，无法验证")
    timers = {}
    image = bot.color_screenshot(args.device)
    for kind, label in bot.RECRUIT_TYPES.items():
        print(f"实机检查：{label}", flush=True)
        image = bot.run_city_tasks(args.device, help_icon, nav, templates, initial_image=image,
                                   donate=False, recruit_kind=kind, keep_open=True, timers=timers)
        image.save(f"{prefix}-{kind}-drawer.png")
        print(f"{label} 下次检查：{round(timers[kind] - time.monotonic())} 秒后", flush=True)
    if not bot.inspect(image.convert("L"), help_icon, nav)[0]:
        raise RuntimeError("招募结束未确认主城")
    # Verify startup recovery from a real recruitment page too.
    image, bottom = bot.recruit_row(args.device, image, templates, "recruit_epic")
    if bottom is None:
        raise RuntimeError("恢复验证未定位到史诗招募任务行")
    bot.task_tap(args.device, 600, bottom + 15)
    for _ in range(6):
        image = screen(args.device)
        if bot.task_page(image, templates, "recruit-title"):
            break
    else:
        raise RuntimeError("恢复验证未进入招募页")
    if not bot.return_to_city(args.device, help_icon, nav, offline, reconnect):
        raise RuntimeError("恢复验证遇到强制下线")
    print("高级、史诗招募检查完成；招募页面返回主城验证通过", flush=True)


if __name__ == "__main__":
    main()
