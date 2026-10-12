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

logo = Image.open(ROOT / 'assets/gamebot-logo.webp').convert('RGBA')
logo.save(ROOT / 'assets/gamebot-icon.png')
logo.save(ROOT / 'assets/gamebot-icon.ico', format='ICO',
          sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
