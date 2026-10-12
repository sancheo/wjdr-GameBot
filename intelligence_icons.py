"""Detect ordinary lighthouse map pins by outline, independent of their artwork."""
from functools import lru_cache

import cv2
import numpy as np


# Search the task map only, leaving the title, currency and reward buttons out.
MAP_BOX = (60, 450, 1020, 1950)


@lru_cache(maxsize=8)
def pin_outline(radius):
    """Generate a semicircular head and tapered point; no game samples required."""
    angles = np.linspace(np.pi, 2 * np.pi, 150)
    upper = np.column_stack((radius * np.cos(angles), radius * np.sin(angles)))
    t = np.linspace(0, 1, 100)[:, None]
    controls = np.array([[radius, 0], [radius, radius * .6],
                         [radius * .6, radius * 1.05], [0, radius * 1.5]])
    right = ((1 - t)**3 * controls[0] + 3 * (1 - t)**2 * t * controls[1]
             + 3 * (1 - t) * t**2 * controls[2] + t**3 * controls[3])
    points = np.concatenate((upper, right, right[::-1] * [-1, 1]))
    offset = radius + 9
    points = np.round(points + offset).astype(np.int32)
    shape = (round(radius * 2.5) + 19, radius * 2 + 19)
    outline = np.zeros(shape, np.float32)
    cv2.polylines(outline, [points], True, 1, 1)
    filled = np.zeros(shape, np.uint8)
    cv2.fillPoly(filled, [points], 1)
    # An outer band rejects dark buildings/terrain that fill the whole shape.
    outside = (cv2.dilate(filled, np.ones((15, 15), np.uint8))
               - cv2.dilate(filled, np.ones((9, 9), np.uint8))).astype(bool)
    return outline, outside, offset


def ordinary_pins(image):
    """Return clickable circle centers; flame-shaped master bounties do not fit.

    Only the dark outer border is scored. Interior color and glyphs are unused.
    Small gaps allow gold sparkles, completion badges and partial overlap. A
    mostly hidden pin becomes detectable after the foreground task is claimed.
    """
    gray = np.asarray(image.convert("L").crop(MAP_BOX))
    dark = (gray < 115).astype(np.uint8)
    near_border = cv2.dilate(
        dark, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))).astype(np.float32)
    border_gray = cv2.erode(gray, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    candidates = []
    for radius in (48, 50, 52, 54):
        outline, outside, offset = pin_outline(radius)
        angles = np.linspace(np.pi, 2 * np.pi, 80)
        head_x = np.rint(offset + radius * np.cos(angles)).astype(int)
        head_y = np.rint(offset + radius * np.sin(angles)).astype(int)
        outer_x = np.rint(offset + (radius + 9) * np.cos(angles)).astype(int)
        outer_y = np.rint(offset + (radius + 9) * np.sin(angles)).astype(int)
        scores = cv2.matchTemplate(near_border, outline, cv2.TM_CCORR) / outline.sum()
        for _ in range(40):
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if score < .87:
                break
            crop = dark[y:y + outline.shape[0], x:x + outline.shape[1]]
            near_crop = near_border[y:y + outline.shape[0], x:x + outline.shape[1]]
            tip = (outline > 0) & (np.arange(outline.shape[0])[:, None] > offset + radius * .8)
            # A circle alone can fit most of the head; require the cone as well.
            # A dim refresh banner can contain a whole dark pin-shaped area.
            # Require a real dark rim with lighter terrain just outside its
            # head; allow missing segments for badges and overlapping pins.
            contrast = (gray[y + outer_y, x + outer_x].astype(np.int16)
                        - border_gray[y + head_y, x + head_x].astype(np.int16))
            if (crop[outside].mean() < .4 and near_crop[tip].mean() >= .75
                    and (contrast > 25).mean() >= .65):
                candidates.append((score, x + offset + MAP_BOX[0],
                                   y + offset + MAP_BOX[1]))
            scores[max(0, y - 35):y + 36, max(0, x - 35):x + 36] = 0
    selected = []
    for score, x, y in sorted(candidates, reverse=True):
        if all(abs(x - px) > 45 or abs(y - py) > 55 for px, py in selected):
            selected.append((x, y))
    # Consistent spatial order rather than a glyph-dependent preference.
    return sorted(selected, key=lambda point: (point[1], point[0]))


@lru_cache(maxsize=1)
def completion_badge_ring():
    yy, xx = np.ogrid[-23:24, -23:24]
    distance = xx * xx + yy * yy
    return ((distance >= 17**2) & (distance <= 21**2)).astype(np.float32)


def completed_pin(image, target):
    """A green tick in a white circular badge, rather than a green pin body."""
    x, y = target
    pixels = np.asarray(image.convert("RGB").crop((x - 5, y - 85, x + 80, y + 5)), dtype=np.int16)
    white = (pixels.min(axis=2) > 195).astype(np.float32)
    green = ((pixels[:, :, 1] - pixels[:, :, 0] > 30)
             & (pixels[:, :, 1] - pixels[:, :, 2] > 10))
    ring = completion_badge_ring()
    scores = cv2.matchTemplate(white, ring, cv2.TM_CCORR) / ring.sum()
    for _ in range(8):
        _, score, _, (bx, by) = cv2.minMaxLoc(scores)
        if score < .7:
            return False
        # The green check must occupy the badge's center, with a white surround.
        if green[by + 10:by + 37, bx + 10:bx + 37].sum() > 60:
            return True
        scores[max(0, by - 5):by + 6, max(0, bx - 5):bx + 6] = 0
    return False
