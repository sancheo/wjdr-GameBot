"""Rebuild GUI crops from the supplied design PNG (path passed as first argument)."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/ui'
OUT.mkdir(exist_ok=True)
source = Image.open(sys.argv[1]).convert('RGBA')
# Coordinates are in the original 1536 × 1024 design, before display scaling.
badges = {
    'monitor': (68, 126, 128, 186), 'gear': (42, 282, 97, 337),
    'calendar': (65, 360, 114, 409), 'people': (560, 360, 609, 409),
    'diamond': (1041, 360, 1090, 409), 'terminal': (67, 784, 105, 819),
}
for name, box in badges.items():
    crop = source.crop(box)
    mask = Image.new('L', (crop.width * 4, crop.height * 4))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1),
                                          radius=(10 if name == 'terminal' else 13) * 4, fill=255)
    crop.putalpha(mask.resize(crop.size, Image.Resampling.LANCZOS))
    crop.save(OUT / f'{name}.png')

# Remove the pale background from small glyph crops so they blend into any panel.
glyphs = {
    'refresh': ((743, 171, 771, 197), (45, 109, 255)),
    'clock': ((1241, 299, 1265, 323), (102, 121, 157)),
    'info': ((1326, 789, 1351, 814), (111, 126, 168)),
    'document': ((746, 850, 792, 898), (177, 188, 210)),
    'chevron': ((662, 178, 681, 191), (45, 109, 255)),
    'stepper': ((1438, 523, 1459, 551), (80, 100, 145)),
}
for name, (box, ink) in glyphs.items():
    crop = source.crop(box)
    bg = crop.getpixel((0, 0))[0]
    pixels = []
    for r, g, b, _ in crop.getdata():
        alpha = max(0, min(255, round((bg - r) / max(1, bg - ink[0]) * 255)))
        pixels.append((*ink, alpha))
    crop.putdata(pixels)
    crop.save(OUT / f'{name}.png')

play = source.crop((1350, 171, 1370, 193))
play.putdata([(255, 255, 255, max(0, min(255, round((r - 48) / 207 * 255))))
              for r, g, b, a in play.getdata()])
play.save(OUT / 'play.png')

logo = Image.open(ROOT / 'assets/gamebot-logo.webp').convert('RGBA')
logo.save(ROOT / 'assets/gamebot-icon.png')
# The visible macOS icon occupies 80% of its square canvas, like standard Dock icons.
dock = Image.new('RGBA', (512, 512))
dock.alpha_composite(logo.resize((410, 410), Image.Resampling.LANCZOS), (51, 51))
dock.save(ROOT / 'assets/gamebot-dock.png')
