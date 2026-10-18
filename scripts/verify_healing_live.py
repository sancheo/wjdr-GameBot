"""Check the hospital flow and subsequent task continuation on one leased emulator."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import help_bot as bot
import healing
from device_lease import DeviceLease
from emulator_runtime import avd_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', required=True)
    parser.add_argument('--samples', action='store_true', help='保存流程 PNG 样本，不写报告')
    args = parser.parse_args()
    def asset(name):
        return bot.open_template(bot.ROOT / f'assets/emulator-{name}.png').convert('L')
    bot.TOWN_TEMPLATE = asset('town-button')
    bot.CHAT_TEMPLATE = asset('chat-title')
    help_icon, nav = asset('help-icon'), asset('city-nav')
    offline, reconnect = asset('forced-text'), asset('reconnect')
    original = bot.task_screen
    count = 0
    def capture(serial, **kwargs):
        nonlocal count
        image = original(serial, **kwargs)
        if args.samples:
            count += 1
            image.save(bot.ROOT / f'artifacts/heal-live-{serial}-{count:02}.png')
        return image
    bot.task_screen = capture
    calls = []
    def heal():
        image = bot.run_healing(args.device, help_icon, nav)
        calls.append('heal')
        return image
    def following():
        image = bot.color_screenshot(args.device)
        if (healing.page(bot, image) is not None or not bot.town_button(image)
                or not bot.inspect(image.convert('L'), help_icon, nav, allow_wilderness=True)[0]):
            raise RuntimeError('治疗后未确认后续任务可在野外继续')
        calls.append('following')
        return image
    with DeviceLease(args.device, avd_key(args.device)):
        # The scheduler prioritizes gather before heal. Finish the hospital
        # first so the continuation probe never runs on an open hospital.
        heal()
        bot.run_schedule(args.device, 20, 2, False, {'heal': heal, 'gather': following},
                         help_icon, nav, offline, reconnect, help_enabled=False)
    if calls[:2] != ['heal', 'following']:
        raise RuntimeError(f'后续任务未执行：{calls}')
    print(f'VERIFIED device={args.device} wilderness=True continuation={calls}', flush=True)


if __name__ == '__main__':
    main()
