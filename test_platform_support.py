"""Cross-platform boundaries and Windows OCR compatibility checks."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw, ImageFont

import ocr_backend
import platform_support as host


class PlatformTest(unittest.TestCase):
    def test_adb_keeps_mac_binary_and_prefers_windows_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / ".tools" / "platform-tools"
            tools.mkdir(parents=True)
            (tools / "adb").touch()
            (tools / "adb.exe").touch()
            with patch.object(host, "ROOT", root), patch.dict(os.environ, {}, clear=True):
                for platform, name in (("win32", "adb.exe"), ("darwin", "adb")):
                    with patch.object(host.sys, "platform", platform):
                        self.assertEqual(host.resolve_adb(), tools / name)

    def test_adb_override_sdk_and_path_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = root / "Android SDK"
            (sdk / "platform-tools").mkdir(parents=True)
            executable = sdk / "platform-tools" / "adb.exe"
            executable.touch()
            with (patch.object(host, "ROOT", root), patch.object(host.sys, "platform", "win32"),
                  patch.dict(os.environ, {"ANDROID_HOME": str(sdk)}, clear=True)):
                self.assertEqual(host.resolve_adb(), executable)
                with patch.dict(os.environ, {"GAMEBOT_ADB": str(root / "override.exe")}):
                    self.assertEqual(host.resolve_adb(), root / "override.exe")
                executable.unlink()
                with patch.object(host.shutil, "which", return_value=str(executable)):
                    self.assertEqual(host.resolve_adb(), executable)

    def test_unicode_child_output_survives_windows_legacy_encoding(self):
        environment = {**os.environ, "PYTHONIOENCODING": "gbk"}
        result = subprocess.run(
            [sys.executable, "-c", "from platform_support import configure_stdio; "
             "configure_stdio(); print('\u26a0\ufe0f 中文日志')"],
            cwd=host.ROOT, env=environment, capture_output=True, check=True,
            encoding="utf-8", **host.SUBPROCESS_OPTIONS)
        self.assertEqual(result.stdout.strip(), "⚠️ 中文日志")

    def test_platform_selects_ocr_without_importing_other_backend(self):
        image = Image.new("RGB", (100, 30))
        with (patch.object(ocr_backend, "recognize_windows", return_value=[]) as windows,
              patch.object(ocr_backend, "recognize_vision", return_value=[]) as vision):
            with patch.object(ocr_backend.sys, "platform", "win32"):
                ocr_backend.recognize_text(image)
                windows.assert_called_once()
                vision.assert_not_called()
            with patch.object(ocr_backend.sys, "platform", "darwin"):
                ocr_backend.recognize_text(image)
                vision.assert_called_once()

    def test_ocr_boxes_map_to_original_image_and_merge_day_prefix(self):
        engine = Mock(return_value=([
            ([[20, 10], [60, 10], [60, 50], [20, 50]], "1天", .99),
            ([[58, 12], [220, 12], [220, 50], [58, 50]], "02:18:33", .99),
            ([[20, 80], [80, 80], [80, 110], [20, 110]], "下一行", .99),
        ], None))
        with patch.object(ocr_backend, "windows_engine", return_value=engine):
            result = ocr_backend.recognize_windows(Image.new("L", (150, 60)))
        self.assertEqual(result, [("1天02:18:33", (10, 5, 100, 20)),
                                  ("下一行", (10, 40, 30, 15))])

    def test_ocr_does_not_merge_distant_columns_or_different_rows(self):
        words = [("左侧", (0, 0, 40, 20)), ("右侧", (100, 0, 40, 20)),
                 ("下一行", (0, 40, 40, 20))]
        self.assertEqual(ocr_backend.merge_text_lines(words), words)

    def test_empty_ocr_and_missing_dependency(self):
        with patch.object(ocr_backend, "windows_engine", return_value=Mock(return_value=(None, None))):
            self.assertEqual(ocr_backend.recognize_windows(Image.new("RGB", (50, 30))), [])
        with patch.dict(sys.modules, {"rapidocr_onnxruntime": None}):
            with self.assertRaisesRegex(RuntimeError, "requirements.txt"):
                ocr_backend.windows_engine.__wrapped__()

    @unittest.skipUnless(sys.platform == "win32", "Windows OCR integration")
    def test_real_english_and_countdown_ocr(self):
        image = Image.new("RGB", (500, 70), "white")
        ImageDraw.Draw(image).text((10, 10), "GameBot 12:34:56", fill="black",
                                   font=ImageFont.truetype("arial.ttf", 32))
        text = "".join(text for text, _ in ocr_backend.recognize_text(image)).replace(" ", "")
        self.assertIn("GameBot", text)
        self.assertIn("12:34:56", text)


if __name__ == "__main__":
    unittest.main()
