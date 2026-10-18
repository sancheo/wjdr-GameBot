"""Capture real Nox frames; template crops require explicit, reviewed boxes."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import help_bot as bot
from device_lease import DeviceLease
from emulator_runtime import avd_key, nox_endpoints
from image_matching import _template
from screen_geometry import _landmark


def main():
    bot.configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--device", required=True)
    parser.add_argument("--tap", nargs=2, type=int)
    parser.add_argument("--swipe", nargs=5, type=int)
    parser.add_argument("--back", action="store_true")
    parser.add_argument("--crop", action="append", default=[],
                        help="template-name:left,top,right,bottom in normalized coordinates")
    parser.add_argument("--ocr", action="store_true")
    args = parser.parse_args()
    if not args.name.replace("-", "").replace("_", "").isalnum():
        parser.error("Use a simple sample name")
    root = bot.ROOT / "tests/fixtures/nox"
    if any((root / f"{args.name}{suffix}.png").exists() for suffix in ("", "-native")):
        parser.error("Use a new sample name so existing crop provenance stays valid")
    if args.device not in nox_endpoints():
        parser.error("Samples must come from an installed Nox instance")
    with DeviceLease(args.device, avd_key(args.device)):
        if not bot.game_foreground(args.device):
            raise RuntimeError("Target game is not in the foreground")
        image = bot.color_screenshot(args.device)
        if args.tap or args.swipe or args.back:
            if not bot.game_foreground(args.device):
                raise RuntimeError("Target game is not in the foreground")
            if args.tap:
                bot.task_tap(args.device, *args.tap)
            elif args.swipe:
                bot.adb(args.device, "shell", "input", "swipe", *map(str, args.swipe))
            else:
                bot.adb(args.device, "shell", "input", "keyevent", "4")
            image = bot.task_screen(args.device, help_enabled=False)
        root.mkdir(parents=True, exist_ok=True)
        native = image.info["native_frame"]
        native.save(root / f"{args.name}-native.png")
        image.save(root / f"{args.name}.png")
        manifest = bot.ROOT / "assets/nox-samples.json"
        samples = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
        model = bot.adb(args.device, "shell", "getprop", "ro.product.model").decode().strip()
        for specification in args.crop:
            name, coordinates = specification.split(":", 1)
            path = bot.ROOT / "assets" / f"emulator-{name}.png"
            if not path.is_file() or Path(name).name != name:
                raise ValueError(f"Unknown template: {name}")
            box = tuple(map(int, coordinates.split(",")))
            if (len(box) != 4 or not (0 <= box[0] < box[2] <= image.width
                                      and 0 <= box[1] < box[3] <= image.height)):
                raise ValueError(f"Invalid crop box: {box}")
            crop = image.crop(box)
            with Image.open(path) as reference:
                if crop.size != reference.size:
                    raise ValueError(f"{name}: expected {reference.size}, got {crop.size}")
            crop.save(path)
            samples[name] = {"native": f"tests/fixtures/nox/{args.name}-native.png",
                             "normalized": f"tests/fixtures/nox/{args.name}.png",
                             "box": box, "device": args.device, "model": model}
            manifest.write_text(json.dumps(samples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _template.cache_clear()
            _landmark.cache_clear()
            print(f"Captured {name} from {args.name}: {box}")
        print(f"Sample: {root / (args.name + '.png')}")
        if args.ocr:
            print(bot.recognize_text(image))


if __name__ == "__main__":
    main()
