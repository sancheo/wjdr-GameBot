"""Verify island help on an explicitly selected, leased Nox account."""
import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import help_bot as bot
import island_help
from device_lease import DeviceLease
from emulator_runtime import nox_endpoints


def main():
    bot.configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--current-island', action='store_true')
    parser.add_argument('--samples', action='store_true')
    args = parser.parse_args()
    if args.device not in nox_endpoints():
        parser.error('实测仅支持夜神模拟器')
    asset = lambda name: bot.open_template(bot.ROOT / f'assets/emulator-{name}.png')
    help_icon, nav, island = [asset(name).convert('L') for name in
                              ('help-icon', 'city-nav', 'island-title')]
    bot.TOWN_TEMPLATE, bot.CHAT_TEMPLATE = asset('town-button').convert('L'), asset('chat-title').convert('L')
    capture = bot.task_screen
    directory = bot.ROOT / 'tests/fixtures/nox' / datetime.now().strftime('island-help-live-%Y%m%d-%H%M%S')
    count = 0

    def screen(serial, **kwargs):
        nonlocal count
        image = capture(serial, **kwargs)
        count += 1
        if args.samples:
            directory.mkdir(parents=True, exist_ok=True)
            image.save(directory / f'{count:03}-logical.png')
            image.info['native_frame'].save(directory / f'{count:03}-native.png')
            island_help.view(image)[0].save(directory / f'{count:03}.png')
        return image

    bot.task_screen = screen
    with DeviceLease(args.device):
        if not bot.game_foreground(args.device):
            raise RuntimeError('目标游戏不在前台')
        if args.current_island:
            image = screen(args.device)
            if not (bot.island_page(image, island) or island_help.visitor(bot, image)):
                raise RuntimeError('当前页面不是海岛')
            image = island_help.run(bot, args.device, image, island)
            assert bot.island_page(image, island)
            print(f'奖杯剩余红点：{island_help.trophy_badge(bot, image)}', flush=True)
            bot.task_tap(args.device, 60, 190)
            image = bot.wait_city_after_island(args.device, help_icon, nav)
        else:
            image = bot.run_rewards(args.device, help_icon, nav, asset('drawer-open'),
                                    asset('warehouse-title'), asset('tree-collect-label'), island,
                                    warehouse=False, dawn=False, tree_threshold=1)
        assert bot.inspect(image.convert('L'), help_icon, nav)[0], '未返回主城，后续任务不可继续'
        print(f'VERIFIED device={args.device} city=True frames={count}', flush=True)
        if args.samples:
            print(f'Samples: {directory}', flush=True)


if __name__ == '__main__':
    main()
