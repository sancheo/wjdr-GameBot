"""Compare native kernels to the historical PIL scores and tie-breaking."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageChops, ImageStat

import image_matching as matching


def historical_mask(image, template, xs, ys, mask_function):
    mask, target = mask_function(image), mask_function(template)
    target_count = ImageStat.Stat(target).sum[0] / 255

    def at(x, y):
        crop = mask.crop((x, y, x + target.width, y + target.height))
        count = ImageStat.Stat(crop).sum[0] / 255
        common = ImageStat.Stat(ImageChops.multiply(crop, target)).sum[0] / 255
        return 2 * common / (count + target_count) if count + target_count else 0

    best = (0, 0, 0)
    for y in ys:
        for x in xs:
            score = at(x, y)
            if score > best[0]:
                best = score, x, y
    if best[0]:
        _, x, y = best
        for candidate_y in range(max(0, y - 7), y + 8):
            for candidate_x in range(max(0, x - 7), x + 8):
                score = at(candidate_x, candidate_y)
                if score > best[0]:
                    best = score, candidate_x, candidate_y
    return best


class ImageMatchingTest(unittest.TestCase):
    def test_mask_scores_and_coordinates_match_pil_including_padding_and_ties(self):
        random = np.random.default_rng(42)
        threshold = lambda image: image.convert("L").point(lambda value: 255 if value > 190 else 0)
        for iteration in range(6):
            image = Image.fromarray(random.integers(0, 256, (55, 65), dtype=np.uint8))
            template = image.crop((17, 21, 28, 30))
            if iteration == 0:
                image, template = Image.new("L", image.size), Image.new("L", template.size)
            xs = list(range(-2, 61, 5))
            ys = list(range(-1, 51, 5))
            if iteration % 2:
                xs.reverse()
                ys.reverse()
            expected = historical_mask(image, template, xs, ys, threshold)
            actual = matching.find_mask(image, template, xs, ys, threshold)
            self.assertEqual(actual, expected)

    def test_absolute_difference_uses_same_score_and_first_minimum(self):
        random = np.random.default_rng(7)
        for mode in ("L", "RGB"):
            shape = (55, 65) if mode == "L" else (55, 65, 3)
            image = Image.fromarray(random.integers(0, 256, shape, dtype=np.uint8))
            box, xs, ys = (15, 15, 28, 27), list(range(-4, 5, 2)), list(range(-4, 5, 2))
            template = image.crop((17, 13, 30, 25))
            expected = (float("inf"), 0, 0)
            for y in ys:
                for x in xs:
                    crop = image.crop((box[0] + x, box[1] + y, box[2] + x, box[3] + y))
                    score = ImageStat.Stat(ImageChops.difference(crop, template)).mean[0]
                    if score < expected[0]:
                        expected = score, x, y
            self.assertEqual(matching.minimum_difference(image, template, box, xs, ys), expected)
        image = Image.new("L", (50, 50))
        self.assertEqual(matching.minimum_difference(image, Image.new("L", (10, 10)),
                                                     (10, 10, 20, 20), [-2, 0, 2], [-2, 0, 2]), (0, -2, -2))

    def test_cached_template_is_decoded_once_and_returned_images_are_independent(self):
        matching._template.cache_clear()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "template.png"
            Image.new("L", (10, 10), 123).save(path)
            with patch.object(matching.Image, "open", wraps=Image.open) as opened:
                with matching.open_template(path) as image:
                    image.putpixel((0, 0), 255)
                with matching.open_template(path) as image:
                    self.assertEqual(image.getpixel((0, 0)), 123)
            self.assertEqual(opened.call_count, 1)
        matching._template.cache_clear()
