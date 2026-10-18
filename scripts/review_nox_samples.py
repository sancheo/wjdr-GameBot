"""Preview candidate crops from retained Nox frames; apply only reviewed names.

Existing samples are never overwritten. No verification report is persisted.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from PIL import Image, ImageDraw
import help_bot as bot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--apply", nargs="+", default=[])
    parser.add_argument("--source-device", help="Verified device that supplied these retained frames")
    parser.add_argument("--source-model")
    args = parser.parse_args()
    if args.apply and not (args.source_device and args.source_model):
        parser.error("Applying retained frames requires their verified device and model")
    directory = args.directory.resolve()
    directory.relative_to((bot.ROOT / "tests/fixtures/nox").resolve())
    manifest = bot.ROOT / "assets/nox-samples.json"
    samples = json.loads(manifest.read_text(encoding="utf-8"))
    frames = []
    for path in directory.glob("*.png"):
        native = path.with_name(path.stem + "-native.png")
        if not native.is_file():
            continue
        with Image.open(path) as image:
            if image.size != bot.EXPECTED_SIZE:
                continue
            pixels = np.asarray(image.convert("L"))
        frames.append((path, pixels, cv2.resize(pixels, (270, 585), interpolation=cv2.INTER_AREA)))
    candidates = {}
    for path in sorted((bot.ROOT / "assets").glob("emulator-*.png")):
        name = path.stem.removeprefix("emulator-")
        if name in samples:
            continue
        with Image.open(path) as reference:
            target = np.asarray(reference.convert("L"))
            coarse = cv2.resize(target, (max(1, round(reference.width / 4)),
                                        max(1, round(reference.height / 4))), interpolation=cv2.INTER_AREA)
        if target.std() < 12:
            continue
        best = None
        for source, pixels, small in frames:
            _, score, _, (cx, cy) = cv2.minMaxLoc(cv2.matchTemplate(small, coarse, cv2.TM_CCOEFF_NORMED))
            if score < .72:
                continue
            left, top = max(0, cx * 4 - 12), max(0, cy * 4 - 12)
            region = pixels[top:min(2340, cy * 4 + target.shape[0] + 16),
                            left:min(1080, cx * 4 + target.shape[1] + 16)]
            if region.shape[0] < target.shape[0] or region.shape[1] < target.shape[1]:
                continue
            _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(region, target, cv2.TM_CCOEFF_NORMED))
            x, y = x + left, y + top
            box = (x, y, x + target.shape[1], y + target.shape[0])
            error = np.abs(pixels[y:box[3], x:box[2]].astype(float) - target).mean()
            if score >= .88 and error <= 22 and (best is None or score > best[0]):
                best = score, error, source, box
        if best:
            candidates[name] = best
            print(f"{name}: correlation={best[0]:.3f} difference={best[1]:.1f} {best[2].name} box={best[3]}", flush=True)
    if candidates:
        sheet = Image.new("RGB", (800, 150 * len(candidates)), "#dddddd")
        draw = ImageDraw.Draw(sheet)
        for row, (name, (_, _, source, box)) in enumerate(candidates.items()):
            draw.text((8, row * 150 + 4), name + " (old / Nox)", fill="black")
            with Image.open(bot.ROOT / f"assets/emulator-{name}.png") as old, Image.open(source) as frame:
                for column, crop in enumerate((old.convert("RGB"), frame.crop(box).convert("RGB"))):
                    crop.thumbnail((380, 115))
                    sheet.paste(crop, (8 + column * 400, row * 150 + 26))
        preview = bot.ROOT / "artifacts/nox-crop-review.png"
        sheet.save(preview)
        print(f"Preview: {preview}")
    if set(args.apply) - candidates.keys():
        parser.error("Some requested names have no candidate")
    for name in args.apply:
        _, _, source, box = candidates[name]
        with Image.open(source) as frame:
            frame.crop(box).convert("RGB").save(bot.ROOT / f"assets/emulator-{name}.png")
        samples[name] = {"native": source.with_name(source.stem + "-native.png").relative_to(bot.ROOT).as_posix(),
                         "normalized": source.relative_to(bot.ROOT).as_posix(), "box": box,
                         "device": args.source_device, "model": args.source_model}
        print(f"Applied reviewed Nox crop: {name}")
    if args.apply:
        manifest.write_text(json.dumps(samples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
