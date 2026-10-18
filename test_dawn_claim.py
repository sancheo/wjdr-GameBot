"""Real-page regressions for dawn rewards over changing city backgrounds."""
import unittest
from PIL import Image
import help_bot as bot


class DawnClaimTest(unittest.TestCase):
    def frame(self):
        with Image.open(bot.ROOT / "tests/fixtures/dawn-claim-background-1014.png") as image:
            return image.convert("RGB")

    def test_real_title_background_and_existing_page(self):
        for mode in ("RGB", "L"):
            self.assertTrue(bot.dawn_claim_page(self.frame().convert(mode)))
        with Image.open(bot.ROOT / "artifacts/flow-dawn-entry.png") as image:
            self.assertTrue(bot.dawn_claim_page(image))

    def test_both_title_and_claim_button_are_required(self):
        for box in ((405, 520, 660, 630), (360, 1580, 730, 1730)):
            image = self.frame()
            image.paste(0, box)
            self.assertFalse(bot.dawn_claim_page(image))

    def test_other_panels_and_wrong_size_are_rejected(self):
        for name in ("stamina-ready", "feature-tech", "pet-reward-overlay", "flow-dawn-exit"):
            with Image.open(bot.ROOT / f"artifacts/{name}.png") as image:
                self.assertFalse(bot.dawn_claim_page(image))
        self.assertFalse(bot.dawn_claim_page(Image.new("L", (540, 1170))))


if __name__ == "__main__":
    unittest.main()
