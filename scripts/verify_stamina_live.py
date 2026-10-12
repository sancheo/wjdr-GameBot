"""Verify only the stamina task on an explicitly selected emulator."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', required=True)
    parser.add_argument('--tag', default='check')
    parser.add_argument('--start-at-research', action='store_true',
                        help='Navigate to research first to verify warehouse recovery')
    args = parser.parse_args()
    bot.TOWN_TEMPLATE = Image.open(bot.ROOT / 'assets/emulator-town-button.png').convert('L')
    help_template = Image.open(bot.ROOT / 'assets/emulator-help-icon.png').convert('L')
    nav_template = Image.open(bot.ROOT / 'assets/emulator-city-nav.png').convert('L')
    drawer_template = Image.open(bot.ROOT / 'assets/emulator-drawer-open.png').convert('RGB')
    original = bot.task_screen
    count = 0

    def capture(serial, **kwargs):
        nonlocal count
        image = original(serial, **kwargs)
        count += 1
        image.save(bot.ROOT / f'artifacts/windows-stamina-{args.tag}-{count:02}.png')
        print(f'frame={count} warehouse={bot.warehouse_position(image)}', flush=True)
        return image

    bot.task_screen = capture
    timers = {}
    try:
        if args.start_at_research:
            image = bot.reset_drawer(args.device, bot.color_screenshot(args.device),
                                     {'drawer-open': drawer_template})
            for attempt in range(bot.DRAWER_SWIPES + 1):
                target = bot.research_drawer_target(image)
                if target is not None:
                    break
                if attempt == bot.DRAWER_SWIPES:
                    raise RuntimeError('Verification could not locate research arrow')
                image = bot.swipe_drawer(args.device, drawer_template)
            bot.task_tap(args.device, *target)
            image = bot.task_screen(args.device, help_enabled=False)
            image = bot.stamina_map_screen(args.device, help_template, nav_template, image)
            image.save(bot.ROOT / f'artifacts/windows-stamina-{args.tag}-start.png')
        image = bot.run_stamina(args.device, help_template, nav_template, drawer_template, timers)
    except Exception:
        bot.color_screenshot(args.device).save(bot.ROOT / f'artifacts/windows-stamina-{args.tag}-failure.png')
        raise
    image.save(bot.ROOT / f'artifacts/windows-stamina-{args.tag}-result.png')
    print(f'VERIFIED city={bot.inspect(image.convert("L"), help_template, nav_template)[0]} timers={timers}', flush=True)


if __name__ == '__main__':
    main()
