"""Verify bounty on an explicit emulator and retain the dispatch/result evidence."""
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
    parser.add_argument('--device', required=True)
    parser.add_argument('--save-report', action='store_true', help='Explicitly save the JSON verification report')
    parser.add_argument('--tag', default='live')
    parser.add_argument('--only-bounty', action='store_true')
    parser.add_argument('--resume-detail', action='store_true')
    parser.add_argument('--target', nargs=2, type=int,
                        help='Observed exposed bounty center, for overlapping pins')
    args = parser.parse_args()
    bot.configure_stdio()
    prefix = bot.ROOT / f'artifacts/bounty-{args.device}-{args.tag}'
    result = {'device': args.device, 'state': {}, 'screens': [], 'dispatches': []}
    original_screen, original_execute = bot.task_screen, bot.execute_intelligence
    stage = 'ordinary'
    started = time.monotonic()

    def screen(serial, *a, **kw):
        image = original_screen(serial, *a, **kw)
        if stage == 'bounty':
            path = Path(f'{prefix}-{len(result["screens"]):03}.png')
            image.save(path)
            result['screens'].append(str(path))
        return image

    def execute(serial, image, bounty_trip=None):
        nonlocal stage
        if bounty_trip is not None:
            stage = 'bounty'
            image.save(f'{prefix}-detail.png')
        image, dispatched = original_execute(serial, image, bounty_trip=bounty_trip)
        if bounty_trip is not None:
            result['dispatches'].append({'dispatched': dispatched, **{
                key: repr(value) if isinstance(value, dict) else value
                for key, value in bounty_trip.items()}})
            print(f'悬赏出征信息：{bounty_trip}', flush=True)
        return image, dispatched

    def asset(name):
        return Image.open(bot.ROOT / f'assets/emulator-{name}.png').convert('L')

    bot.TOWN_TEMPLATE = asset('town-button')
    bot.task_screen, bot.execute_intelligence = screen, execute
    try:
        bot.color_screenshot(args.device).save(f'{prefix}-before.png')
        if args.only_bounty:
            image = bot.task_screen(args.device)
            if not bot.intelligence_page(image):
                image = bot.open_intelligence(args.device, image)
            target = tuple(args.target) if args.target else bot.bounty_target(image)
            if target is None:
                raise RuntimeError('悬赏图标尚被遮挡，无法执行专项验证')
            stage = 'bounty'
            image, completed = bot.execute_bounty(args.device, image, target, result['state'])
            result['completed'] = completed
        else:
            if args.resume_detail:
                image = bot.task_screen(args.device)
                if bot.bounty_name(image) is not None:
                    raise RuntimeError('恢复普通详情时发现大师悬赏，未执行')
                image, _ = bot.execute_intelligence(args.device, image)
            image = bot.retry_once('悬赏实测', lambda: bot.run_intelligence(
                args.device, asset('help-icon'), asset('city-nav'), asset('forced-text'),
                asset('reconnect'), bounty=True, bounty_state=result['state']),
                args.device, asset('forced-text'), asset('reconnect'),
                lambda: bot.recover_task_city(args.device, asset('help-icon'),
                    asset('city-nav'), asset('forced-text'), asset('reconnect')))
        image.save(f'{prefix}-result.png')
        result['ended_in_wilderness'] = bot.town_button(image)
        if result['state'].get('diagnostic_screenshot'):
            raise RuntimeError('悬赏结果仍未确认，请检查本次异常截图')
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        bot.color_screenshot(args.device).save(f'{prefix}-failure.png')
        raise
    finally:
        result['elapsed_seconds'] = round(time.monotonic() - started, 2)
        if args.save_report:
            Path(f'{prefix}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2,
                default=str), encoding='utf-8')
        print(result, flush=True)


if __name__ == '__main__':
    main()
