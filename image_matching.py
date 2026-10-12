"""Cached templates and exact image scores, evaluated in native array kernels."""

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


@lru_cache(maxsize=256)
def _template(path):
    with Image.open(path) as image:
        return image.copy()


def open_template(path):
    # A caller may use a context manager or mutate the image; keep the cache safe.
    return _template(str(Path(path).resolve())).copy()


def minimum_difference(image, template, box, x_offsets, y_offsets):
    xs, ys = list(x_offsets), list(y_offsets)
    region = image.crop((box[0] + min(xs), box[1] + min(ys),
                         box[2] + max(xs), box[3] + max(ys)))
    pixels, target = np.asarray(region), np.asarray(template)
    if pixels.ndim == 3:
        pixels = pixels[:, :, 0]
    if target.ndim == 3:
        target = target[:, :, 0]
    windows = np.lib.stride_tricks.sliding_window_view(pixels, target.shape)
    best = (float("inf"), 0, 0)
    # One row at a time bounds temporary memory even on large templates.
    for dy in ys:
        candidates = windows[dy - min(ys), np.asarray(xs) - min(xs)]
        scores = np.abs(candidates.astype(np.int16) - target.astype(np.int16)).mean(axis=(1, 2))
        index = int(np.argmin(scores))
        if scores[index] < best[0]:
            best = float(scores[index]), xs[index], dy
    return best


def find_mask(image, template, x_range, y_range, mask_function):
    xs, ys = list(x_range), list(y_range)
    if not xs or not ys:
        return 0, 0, 0
    target = np.asarray(mask_function(template), dtype=np.float32) / 255
    left, top = min(xs) - 7, min(ys) - 7
    right, bottom = max(xs) + 7 + template.width, max(ys) + 7 + template.height
    mask = mask_function(image).crop((left, top, right, bottom))
    pixels = np.asarray(mask, dtype=np.float32) / 255
    common = np.rint(cv2.matchTemplate(pixels, target, cv2.TM_CCORR)).astype(np.float64)
    integral = cv2.integral(pixels, sdepth=cv2.CV_64F)
    height, width = target.shape
    counts = (integral[height:, width:] - integral[:-height, width:]
              - integral[height:, :-width] + integral[:-height, :-width])
    denominator = counts + float(target.sum())
    scores = np.divide(2 * common, denominator, out=np.zeros_like(common), where=denominator != 0)
    coarse = scores[np.asarray(ys)[:, None] - top, np.asarray(xs)[None, :] - left]
    y_index, x_index = np.unravel_index(int(np.argmax(coarse)), coarse.shape)
    best = float(coarse[y_index, x_index]), xs[x_index], ys[y_index]
    if best[0] <= 0:
        return 0, 0, 0
    _, x, y = best
    for candidate_y in range(max(0, y - 7), y + 8):
        for candidate_x in range(max(0, x - 7), x + 8):
            score = float(scores[candidate_y - top, candidate_x - left])
            if score > best[0]:
                best = score, candidate_x, candidate_y
    return best
