"""Map Android frames and input through the same anchored reference canvas.

Unity keeps UI sizes proportional to width, but anchors headers, dialogs and
navigation to different edges of its safe area. Resizing the entire screenshot
would stretch templates and cannot model those independent offsets.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import threading

import cv2
import numpy as np
from PIL import Image

from image_matching import open_template

REFERENCE_SIZE = (1080, 2340)
REFERENCE_TOP = 136
ROOT = Path(__file__).resolve().parent

# Small, stable UI landmarks. Coordinates remain in the existing sample space.
# Matching also identifies whether the foreground is a centered modal or a page.
LANDMARKS = (
    ("secret-choice-title", (345, 230, 840, 320), "page"),
    ("secret-choice-close", (965, 230, 1045, 315), "page"),
    ("drawer-open", (678, 1020, 715, 1170), "drawer"),
    ("bazaar-title", (350, 300, 720, 380), "page"),
    ("bazaar-close", (985, 165, 1050, 235), "page"),
    ("city-nav", (70, 2195, 150, 2275), "edge"),
    ("town-button", (925, 2275, 1020, 2330), "edge"),
    ("help-icon", (748, 2070, 830, 2155), "edge"),
    ("pet-button", (960, 1680, 1030, 1750), "edge"),
    ("task-rewards-entry", (15, 1985, 95, 2040), "edge"),
    ("intel-entry", (956, 1674, 1036, 1754), "edge"),
    ("alliance-title", (135, 150, 255, 225), "page"),
    ("alliance-help-title", (135, 150, 360, 225), "page"),
    ("alliance-help-entry", (775, 1705, 965, 1780), "edge"),
    ("tech-title", (135, 150, 360, 225), "page"),
    ("chat-title", (140, 150, 245, 230), "page"),
    ("intel-title", (140, 166, 244, 218), "bottom-page"),
    ("intel-detail-go", (425, 1630, 660, 1715), "modal"),
    ("explore-title", (140, 150, 260, 225), "bottom-page"),
    ("training-shield-title", (400, 140, 680, 230), "page"),
    ("training-spear-title", (400, 140, 680, 230), "page"),
    ("training-archer-title", (400, 140, 680, 230), "page"),
    ("treasure-title", (140, 165, 345, 225), "page"),
    ("bear-war-title", (140, 165, 340, 225), "page"),
    ("gather-search", (485, 2200, 605, 2285), "bottom-page"),
    ("recruit-title", (885, 265, 1050, 440), "bottom-page"),
    ("pet-title", (440, 480, 640, 535), "modal"),
    ("treasure-alliance-title", (400, 470, 680, 550), "modal"),
    ("treasure-completed-title", (410, 680, 670, 810), "modal"),
    ("treasure-dispatch-action", (430, 1810, 650, 1885), "modal"),
    ("treasure-dispatch-title", (430, 580, 650, 640), "modal"),
    ("treasure-active-title", (460, 725, 620, 790), "modal"),
    ("can-modal-title", (390, 1390, 690, 1480), "modal"),
    ("heal-title", (416, 780, 615, 835), "modal"),
    ("training-upgrade-title", (350, 680, 730, 755), "modal"),
    ("bear-hero-title", (438, 477, 642, 539), "modal"),
    ("bear-rally-title", (435, 850, 655, 914), "modal"),
    ("explore-income", (440, 695, 660, 760), "modal"),
    ("explore-reward", (395, 705, 660, 790), "modal"),
    ("tech-detail", (920, 535, 1005, 625), "modal"),
    ("dawn-title", (405, 520, 660, 630), "modal"),
    ("dawn-reward-title", (380, 680, 700, 790), "modal"),
    ("island-title", (120, 140, 310, 230), "page"),
    ("dawn-gift-title", (235, 430, 530, 505), "modal"),
    ("dawn-gift-close", (879, 437, 949, 507), "modal"),
    ("forced-text", (130, 1175, 940, 1250), "modal"),
    ("reconnect", (570, 1435, 935, 1530), "modal"),
    ("network-title", (465, 925, 615, 980), "modal"),
    ("relocation-title", (490, 925, 590, 980), "modal"),
    ("task-rewards-daily-title", (435, 435, 645, 500), "modal"),
    ("task-rewards-growth-title", (435, 435, 645, 500), "modal"),
    ("reward-title", (460, 590, 630, 720), "modal"),
)


@dataclass(frozen=True)
class Geometry:
    size: tuple
    # Native viewport, excluding full-width black system/letterbox bands.
    viewport: tuple
    source_y: tuple
    target_y: tuple

    @property
    def scale(self):
        return (self.viewport[2] - self.viewport[0]) / REFERENCE_SIZE[0]

    def point(self, x, y):
        px = self.viewport[0] + float(x) * self.scale
        py = float(np.interp(float(y), self.target_y, self.source_y))
        return (max(0, min(self.size[0] - 1, round(px))),
                max(0, min(self.size[1] - 1, round(py))))

    def logical_point(self, x, y):
        """Inverse input mapping for targets detected on the undistorted scene."""
        return (round((x - self.viewport[0]) / self.scale),
                round(float(np.interp(float(y), self.source_y, self.target_y))))

    def normalize(self, image):
        if self.size == REFERENCE_SIZE and self.source_y == self.target_y:
            return image
        xs = np.arange(REFERENCE_SIZE[0], dtype=np.float32) * self.scale + self.viewport[0]
        ys = np.interp(np.arange(REFERENCE_SIZE[1]), self.target_y, self.source_y).astype(np.float32)
        result = cv2.remap(np.asarray(image), np.broadcast_to(xs, (2340, 1080)),
                           np.broadcast_to(ys[:, None], (2340, 1080)),
                           cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        normalized = Image.fromarray(result)
        normalized.info.update(image.info)
        normalized.info["screen_geometry"] = self
        normalized.info["native_frame"] = image
        return normalized


def viewport(image):
    """Only remove continuous, near-black *full-width* edge bands.

    A dark scene or loading screen is not evidence of a different safe area.
    Ambiguous frames reuse the last geometry, rather than recalibrating on them.
    """
    pixels = np.asarray(image.convert("RGB"))
    rows = pixels.max(axis=(1, 2)) > 8
    visible = np.flatnonzero(rows)
    if len(visible) < image.height * .6:
        return None
    top, bottom = int(visible[0]), int(visible[-1]) + 1
    if top > image.height * .15 or image.height - bottom > image.height * .15:
        return None
    return 0, top, image.width, bottom


@lru_cache(maxsize=128)
def _landmark(name, box):
    path = ROOT / f"assets/emulator-{name}.png"
    if not path.is_file():
        return None
    template = np.asarray(open_template(path).convert("L"))
    return template if template.shape == (box[3] - box[1], box[2] - box[0]) else None


def _matches(image, top_shift, bottom_shift):
    pixels = np.asarray(image.convert("L"))
    height, width = pixels.shape
    matches = []
    for name, box, kind in LANDMARKS:
        target = _landmark(name, box)
        if target is None or float(target.std()) < 12:
            continue
        left, right = max(0, box[0] - 4), min(width, box[2] + 4)
        top = max(0, int(box[1] + min(top_shift, bottom_shift)) - 24)
        bottom = min(height, int(box[3] + max(top_shift, bottom_shift)) + 25)
        crop = pixels[top:bottom, left:right]
        if crop.shape[0] < target.shape[0] or crop.shape[1] < target.shape[1]:
            continue
        if name in {"training-shield-title", "training-spear-title", "training-archer-title"}:
            # Tiers change the name prefix. The unit suffix still anchors the
            # same training layout before that tier has its own captured title.
            crop, target = crop[:, 140:], target[:, 140:]
        if kind == "drawer":
            # The panel is translucent: buildings and timers behind the handle
            # change every frame. Its white arrow is the stable landmark.
            crop = np.where(crop > 230, 255, 0).astype(np.uint8)
            target = np.where(target > 230, 255, 0).astype(np.uint8)
        scores = cv2.matchTemplate(crop, target, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, (x, y) = cv2.minMaxLoc(scores)
        actual = crop[y:y + target.shape[0], x:x + target.shape[1]]
        difference = float(np.abs(actual.astype(np.int16) - target).mean())
        # Task titles can retain their exact lettering while the blue backing
        # changes by account. Only accept that weaker correlation at the
        # independently predicted center anchor, never elsewhere on the frame.
        centered_task = (name in {"task-rewards-daily-title", "task-rewards-growth-title"}
                         and confidence >= .72
                         and abs(top + y - box[1] - (top_shift + bottom_shift) / 2) <= 3)
        if (confidence >= .85 or centered_task) and difference <= 24:
            matches.append((confidence, box, top + y - box[1], kind))
    return matches


def detect(image, previous=None):
    if image.width >= image.height:
        raise RuntimeError("屏幕适配需要竖屏手机画面，请关闭横屏或分屏后重试")
    bounds = viewport(image)
    if bounds is None:
        if previous is not None and previous.size == image.size:
            return previous
        bounds = (0, round(REFERENCE_TOP * image.width / 1080), image.width, image.height)
    if image.size == REFERENCE_SIZE and bounds[1] == REFERENCE_TOP and bounds[3] == 2340:
        return Geometry(image.size, bounds, (0., 2340.), (0., 2340.))
    scale = image.width / 1080
    top_shift = bounds[1] / scale - REFERENCE_TOP
    bottom_shift = bounds[3] / scale - 2340
    center_shift = (top_shift + bottom_shift) / 2
    small = image.resize((1080, round(image.height / scale)), Image.Resampling.LANCZOS)
    matches = _matches(small, top_shift, bottom_shift)
    # Wilderness puts intelligence and pets in different rows even though the
    # historical city pet and intelligence anchors share one logical row. Both
    # can match the native frame, but together they fold the vertical map.
    town_box = (925, 2275, 1020, 2330)
    pet_box = (960, 1680, 1030, 1750)
    if any(match[1] == town_box for match in matches):
        matches = [match for match in matches if match[1] != pet_box]
    modal = [m for m in matches if m[3] == "modal"]
    drawer = any(m[3] == "drawer" for m in matches)
    page = any(m[3] == "page" for m in matches)
    bottom_page = any(m[3] == "bottom-page" for m in matches)
    training_page = any(m[1] == (400, 140, 680, 230) and m[3] == "page" for m in matches)
    bottom_page = bottom_page or training_page
    if modal:
        # Centered foreground panels must also obscure the background navigation
        # in the normalized view; do not stitch its city controls back on top.
        # A panel may grow with account content (for example the pre-stage-200
        # exploration hint). Its title then moves within the centered panel;
        # that movement is not a change to the screen's coordinate system.
        shift = center_shift
        knots = [(0., shift), (2340., shift)]
    else:
        knots = ([(0., top_shift), (350., top_shift), (650., center_shift),
                  (1650., center_shift), (1970., bottom_shift), (2340., bottom_shift)] if drawer else
                 [(0., top_shift), (350., top_shift), (650., bottom_shift),
                  (2340., bottom_shift)] if bottom_page else
                 [(0., top_shift), (1750., top_shift),
                  (2110., bottom_shift), (2340., bottom_shift)] if page else
                 [(0., top_shift), (550., top_shift), (850., center_shift),
                  (1490., center_shift), (1790., bottom_shift), (2340., bottom_shift)])
        # A verified landmark supplies a rigid band: preserve its dimensions,
        # interpolate only the spaces between independently anchored controls.
        for confidence, box, shift, _ in sorted(matches):
            # The drawer is a single centered panel. City buttons behind it
            # must not stretch its last rows to their bottom-anchored positions.
            if drawer and 450 < box[1] < 1900 and _ != "drawer":
                continue
            start, end = max(0., box[1] - 12.), min(2340., box[3] + 12.)
            knots = [(y, dy) for y, dy in knots if not start <= y <= end]
            knots.extend(((start, shift), (end, shift)))
    knots.sort()
    target = tuple(y for y, _ in knots)
    source = tuple((y + dy) * scale for y, dy in knots)
    if any(b <= a for a, b in zip(source, source[1:])):
        # Conflicting matches must never produce a folded image/input map.
        target = (0., 550., 1790., 2340.)
        source = tuple((y + dy) * scale for y, dy in
                       zip(target, (top_shift, top_shift, bottom_shift, bottom_shift)))
    return Geometry(image.size, bounds, source, target)


class ScreenAdapter:
    """Per-device, process-local geometry; no phone settings are changed."""
    def __init__(self):
        self._geometries = {}
        self._lock = threading.RLock()

    def frame(self, serial, image):
        with self._lock:
            geometry = detect(image, self._geometries.get(serial))
            self._geometries[serial] = geometry
        return geometry.normalize(image)

    def geometry(self, serial):
        with self._lock:
            return self._geometries.get(serial)

    def input(self, serial, args):
        """Cover every tap/swipe call, including modules calling adb directly."""
        if len(args) < 5 or tuple(args[:3]) not in (("shell", "input", "tap"),
                                                  ("shell", "input", "swipe")):
            return args
        geometry = self.geometry(serial)
        if geometry is None:
            raise RuntimeError("尚未读取设备截图，无法确认点击坐标，请重新启动任务")
        mapped = list(args)
        for index in ((3,) if args[2] == "tap" else (3, 5)):
            mapped[index:index + 2] = map(str, geometry.point(args[index], args[index + 1]))
        return tuple(mapped)


adapter = ScreenAdapter()


def scene_view(image):
    """Return an unwarped world view and conversions to the UI canvas.

    Anchored HUD controls need piecewise normalization. World sprites do not:
    a warehouse or crystal crossing a HUD interpolation band must retain its
    original proportions. This view is only for world-object recognition.
    """
    geometry = image.info.get("screen_geometry")
    native = image.info.get("native_frame")
    if geometry is None or native is None or geometry.size == REFERENCE_SIZE:
        return None
    height = round(native.height / geometry.scale)
    padding = (REFERENCE_SIZE[1] - height) // 2
    scene = Image.new("RGB", REFERENCE_SIZE)
    scene.paste(native.resize((1080, height), Image.Resampling.LANCZOS), (0, padding))

    def to_logical(x, y):
        return geometry.logical_point(geometry.viewport[0] + x * geometry.scale,
                                      (y - padding) * geometry.scale)

    def from_logical(x, y):
        nx, ny = geometry.point(x, y)
        return (round((nx - geometry.viewport[0]) / geometry.scale),
                round(ny / geometry.scale + padding))

    return scene, to_logical, from_logical
