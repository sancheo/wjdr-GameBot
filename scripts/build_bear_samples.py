"""Extract calibrated bear/hero templates from emulator-5554 sample screenshots.

These are 1080 x 2340 screenshots. Hero names were checked on their detail
pages. Selection icons include level/stars; face crops exclude those overlays.
Squad samples are from resource dispatch preparation, not a live bear dispatch.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "artifacts"
ASSETS = ROOT / "assets"
HEROES = {
    "jessie": ("杰西", "bear-5554-38-selection-reset-stable.png", (128, 1187, 298, 1357),
               "bear-5554-37-squad-jessie-valid.png"),
    "jasser": ("杰塞尔", "bear-5554-39-jasser-focus-confirmed.png", (343, 1404, 513, 1574),
               "bear-5554-44-squad-jasser-valid.png"),
    "seoyoon": ("书允", "bear-5554-25-selection-lower.png", (559, 1237, 729, 1407),
                "bear-5554-31-squad-seoyoon-valid.png"),
}
CONTROLS = {
    "entry": ("bear-5554-02-wilderness.png", (791, 1950, 880, 2020)),
    "war-entry": ("bear-5554-02-wilderness.png", (947, 912, 1027, 990)),
    "war-title": ("bear-5554-00-initial.png", (140, 165, 340, 225)),
    "join": ("bear-5554-00-initial.png", (918, 675, 1008, 770)),
    "target": ("bear-5554-00-initial.png", (380, 867, 475, 960)),
    "rally": ("bear-5554-04-rally-target.png", (556, 1758, 829, 1838)),
    "hero-title": ("bear-5554-38-selection-reset-stable.png", (438, 477, 642, 539)),
    "hero-assign": ("bear-5554-38-selection-reset-stable.png", (580, 1738, 953, 1818)),
    "hero-close": ("bear-5554-38-selection-reset-stable.png", (930, 477, 989, 536)),
    "hero-minus": ("bear-5554-47-squad-reopened.png", (322, 495, 390, 561)),
    "hero-plus": ("bear-5554-14-remove-first.png", (213, 665, 298, 748)),
    "dispatch": ("bear-5554-50-no-heroes.png", (635, 2190, 1006, 2288)),
    "hero-focus": ("bear-5554-38-selection-reset-stable.png", (100, 1160, 325, 1385)),
    "hero-card": ("bear-5554-38-selection-reset-stable.png", (128, 1187, 298, 1357)),
    "rally-title": ("bear-drive-5554-00-current.png", (435, 850, 655, 914)),
    "rally-start": ("bear-drive-5554-00-current.png", (345, 1490, 735, 1570)),
    "hero-plus-disabled": ("bear-5554-14-remove-first.png", (497, 665, 582, 748)),
    "hero-selection-plus": ("bear-5554-15-hero-selection.png", (213, 759, 298, 842)),
    "hero-selection-plus-disabled": ("bear-5554-15-hero-selection.png", (497, 759, 582, 842)),
}


def source(name):
    with Image.open(SAMPLES / name) as image:
        if image.size != (1080, 2340):
            raise ValueError(f"Unexpected screenshot dimensions: {name}: {image.size}")
        return image.convert("RGB")


def main():
    preview = Image.new("RGB", (630, 490), "#edf3fa")
    draw = ImageDraw.Draw(preview)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 24)
    for index, (name, (label, filename, box, squad)) in enumerate(HEROES.items()):
        icon = source(filename).crop(box)
        icon.save(ASSETS / f"emulator-bear-hero-{name}-select.png")
        # Relative face box deliberately excludes the level and star overlays.
        icon.crop((14, 50, 156, 126)).save(ASSETS / f"emulator-bear-hero-{name}-select-face.png")
        face = source(squad).crop((170, 557, 340, 750))
        face.save(ASSETS / f"emulator-bear-hero-{name}-squad-face.png")
        x = 20 + index * 210
        draw.text((x, 12), label, font=font, fill="#123250")
        preview.paste(icon, (x, 60))
        preview.paste(face, (x, 260))
    preview.save(SAMPLES / "bear-5554-hero-samples.png")
    for name, (filename, box) in CONTROLS.items():
        source(filename).crop(box).save(ASSETS / f"emulator-bear-{name}.png")
    follow = (
        ("巨熊：绿色加号", "bear-5554-00-initial.png", (350, 650, 1030, 960)),
        ("巨熊：灰色加号", "bear-5554-00-initial.png", (350, 1260, 1030, 1570)),
        ("非熊：绿色加号", "bear-follow-5554-03-current.png", (350, 1120, 1030, 1430)),
        ("首位错误：需清空重选", "bear-follow-emulator-5554-wrong-first-before.png", (120, 470, 980, 930)),
        ("后排杰塞尔：需移除", "bear-follow-emulator-5554-wrong-rear-before.png", (120, 470, 980, 930)),
        ("调整后：杰西领队", "bear-follow-emulator-5554-wrong-first-prepared.png", (120, 470, 980, 930)),
    )
    # Keep the initial hero extraction usable when follow samples are not present.
    if all((SAMPLES / filename).exists() for _, filename, _ in follow):
        sheet = Image.new("RGB", (1080, 570), "#edf3fa")
        draw = ImageDraw.Draw(sheet)
        for index, (label, filename, box) in enumerate(follow):
            x, y = index % 3 * 360 + 10, index // 3 * 285 + 10
            draw.text((x, y), label, font=font, fill="#123250")
            panel = source(filename).crop(box)
            panel.thumbnail((340, 240))
            sheet.paste(panel, (x, y + 42))
        sheet.save(SAMPLES / "bear-follow-5554-samples.png")
    print(f"Extracted {len(HEROES) * 3 + len(CONTROLS)} templates and hero preview")


if __name__ == "__main__":
    main()
