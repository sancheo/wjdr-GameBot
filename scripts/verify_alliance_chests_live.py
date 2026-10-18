"""Run alliance chest collection on one leased Nox instance, optionally keeping PNG samples."""
import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import help_bot as bot
import alliance_chests
from device_lease import DeviceLease
from emulator_runtime import avd_key, nox_endpoints


def main():
    bot.configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--samples', action='store_true')
    parser.add_argument('--donate', action='store_true', help='先捐献，再在返回主城前领取联盟宝箱')
    args = parser.parse_args()
    if args.device not in nox_endpoints():
        parser.error('实测和采样仅支持已安装的夜神模拟器')
    def asset(name):
        return bot.open_template(bot.ROOT / f'assets/emulator-{name}.png').convert('L')
    help_icon, nav = asset('help-icon'), asset('city-nav')
    bot.TOWN_TEMPLATE, bot.CHAT_TEMPLATE = asset('town-button'), asset('chat-title')
    capture = bot.task_screen
    directory = bot.ROOT / 'tests/fixtures/nox' / datetime.now().strftime('alliance-chests-live-%Y%m%d-%H%M%S')
    count = 0
    def screen(serial, **kwargs):
        nonlocal count
        image = capture(serial, **kwargs)
        if args.samples:
            directory.mkdir(parents=True, exist_ok=True)
            count += 1
            image.save(directory / f'{count:03}.png')
            image.info['native_frame'].save(directory / f'{count:03}-native.png')
        return image
    bot.task_screen = screen
    with DeviceLease(args.device, avd_key(args.device)):
        if not bot.game_foreground(args.device):
            raise RuntimeError('目标游戏不在前台')
        if args.donate:
            timers = {}
            templates = {name: bot.open_template(bot.ROOT / f'assets/emulator-{name}.png').convert('RGB')
                         for name in (*bot.TASK_BOXES, 'donate-label', 'free-label', 'thumb')}
            image = bot.run_city_tasks(args.device, help_icon, nav, templates,
                                       recruit=False, collect_alliance_chests=True, timers=timers)
            if 'alliance_chests' not in timers:
                image = alliance_chests.run(bot, args.device, help_icon, nav, initial_image=image)
        else:
            image = alliance_chests.run(bot, args.device, help_icon, nav)
        if not bot.inspect(image.convert('L'), help_icon, nav)[0]:
            raise RuntimeError('领取后未返回主城')
        # A following task can consume the resulting city frame.
        print(f'VERIFIED device={args.device} city=True samples={count}', flush=True)
        if args.samples:
            print(f'Samples: {directory}', flush=True)


if __name__ == '__main__':
    main()
