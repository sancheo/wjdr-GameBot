"""Run a single resource verification on the explicitly selected emulator."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', required=True)
    parser.add_argument('--resource', choices=tuple(bot.GATHER_RESOURCES), required=True)
    parser.add_argument('--level', type=int, choices=range(1, 9), default=8)
    args = parser.parse_args()
    bot.TOWN_TEMPLATE = Image.open(bot.ROOT / 'assets/emulator-town-button.png').convert('L')
    image = bot.color_screenshot(args.device)
    if bot.gather_search_page(image) or bot.gather_back_target(image):
        bot.leave_gather(args.device, image)
    original = bot.task_screen
    count = 0

    def capture(serial):
        nonlocal count
        frame = original(serial)
        count += 1
        frame.save(bot.ROOT / f'artifacts/windows-gather-{args.resource}-{count:02}.png')
        return frame

    bot.task_screen = capture
    help_template = Image.open(bot.ROOT / 'assets/emulator-help-icon.png').convert('L')
    nav_template = Image.open(bot.ROOT / 'assets/emulator-city-nav.png').convert('L')
    result = bot.run_gather(args.device, help_template, nav_template, None, None,
                            resources=(args.resource,), levels={args.resource: args.level})
    result.save(bot.ROOT / f'artifacts/windows-gather-{args.resource}-result.png')
    print(f'VERIFIED {args.resource}: returned to wilderness={bot.town_button(result)}', flush=True)


if __name__ == '__main__':
    main()
