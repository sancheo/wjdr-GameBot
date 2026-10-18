"""Collect one explicitly requested rally/hero flow on a leased emulator."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import help_bot as bot
import bear_hunting as bear
from device_lease import DeviceLease
from emulator_runtime import avd_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--flow", choices=("drive", "follow"), default="drive",
                        help="Dispatch as a driver (keep heroes) or follower (validate heroes)")
    parser.add_argument("--case", choices=("existing", "wrong-first", "wrong-rear", "wrong-rear-third"),
                        default="existing")
    parser.add_argument("--dispatch", action="store_true", help="Actually issue one rally using the selected flow")
    args = parser.parse_args()
    bot.configure_stdio()
    original = bot.task_screen
    prefix = bot.ROOT / f"artifacts/bear-{args.flow}-{args.device}-{args.case}"
    counter = 0

    def capture(serial, **kwargs):
        nonlocal counter
        image = original(serial, **kwargs)
        image.save(f"{prefix}-{counter:03}.png")
        counter += 1
        return image

    bot.task_screen = capture
    bot.TOWN_TEMPLATE = bot.open_template(bot.ROOT / "assets/emulator-town-button.png").convert("L")
    with DeviceLease(args.device, avd_key(args.device)):
        image = capture(args.device)
        if args.flow == "follow" and args.dispatch and args.case == "existing" and bear.war_page(bot, image):
            image, issued = bear.follow_rally(bot, args.device, image)
            print("Rally joined:", issued, flush=True)
            if not issued:
                raise RuntimeError("当前未能加入熊队伍，检查可加入队伍、体力和士兵状态")
            return
        if bear.rally_page(bot, image):
            bot.task_tap(args.device, 540, 1538)
            image = bear.wait_for(bot, args.device, lambda frame: bear.squad_page(bot, frame), "未打开出征界面")
        if not bear.squad_page(bot, image):
            raise RuntimeError("请先进入发起集结弹窗或出征准备界面")
        if args.case == "wrong-first":
            if bear.slots(bot, image)[0]:
                image = bear.remove(bot, args.device, image, 0)
            dy, _ = bear.squad_layout(bot, image)
            bot.task_tap(args.device, 254, 705 + dy)
            image = bear.wait_for(bot, args.device, lambda frame: bear.selection_page(bot, frame), "未打开英雄选择")
            image = bear.fill_selection(bot, args.device, image, need_first=False)
            dy, _ = bear.squad_layout(bot, image)
            if bear.hero_name(bot, image.crop(bear.shifted(bear.FACE_BOX, dy=dy))) is not None:
                raise RuntimeError("未构造首英雄不符合条件的采样场景")
        elif args.case in ("wrong-rear", "wrong-rear-third"):
            slot = 2 if args.case == "wrong-rear-third" else 1
            image = bear.prepare_squad(bot, args.device, image, flow=args.flow)
            if bear.slots(bot, image)[slot]:
                image = bear.remove(bot, args.device, image, slot)
            dy, _ = bear.squad_layout(bot, image)
            bot.task_tap(args.device, 254 + bear.SLOT_OFFSETS[slot], 705 + dy)
            image = bear.wait_for(bot, args.device, lambda frame: bear.selection_page(bot, frame), "未打开后排英雄选择")
            image, found = bear.choose(bot, args.device, image, first=True)
            if not found:
                raise RuntimeError("当前没有可放入后排的打熊英雄，未构造后排排除场景")
            bot.task_tap(args.device, 770, 1780)
            image = bear.wait_for(bot, args.device,
                                  lambda frame: bear.selection_page(bot, frame) and bear.slots(bot, frame, True)[slot],
                                  "未确认后排槽指派完成")
            image = bear.close_selection(bot, args.device, image)
        image.save(f"{prefix}-before.png")
        image = bear.prepare_squad(bot, args.device, image, flow=args.flow)
        image.save(f"{prefix}-prepared.png")
        dy, occupied = bear.squad_layout(bot, image)
        if args.flow == "follow":
            names = [bear.hero_name(bot, image.crop(bear.shifted(bear.FACE_BOX, dx, dy))) if occupied[i] else None
                     for i, dx in enumerate(bear.SLOT_OFFSETS)]
            print("Validated follower squad:", occupied, names, flush=True)
        else:
            print("Driver squad kept:", occupied, flush=True)
        if args.dispatch:
            image, issued = bear.dispatch(bot, args.device, image, flow=args.flow)
            image.save(f"{prefix}-dispatched.png")
            print("Rally dispatched:", issued, flush=True)
            if not issued:
                raise RuntimeError("本次未能出征，检查体力或士兵状态")


if __name__ == "__main__":
    main()
