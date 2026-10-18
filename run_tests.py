"""Run fast logic checks or the complete offline screenshot regression suite."""

import argparse
from pathlib import Path
import sys
import unittest

from platform_support import configure_stdio

ROOT = Path(__file__).resolve().parent
FAST_MODULES = {
    "test_gui_web", "test_emulator_config", "test_emulator_runtime", "test_device_notes", "test_platform_support",
    "test_device_lease", "test_runtime_control", "test_task_status", "test_stamina_identity",
    "test_image_matching", "test_hunting",
    "test_runtime_logs", "test_maintenance",
    "test_alliance_help_clicker", "test_bear_runtime", "test_app_updates",
}


def build_suite(group):
    modules = {path.stem for path in ROOT.glob("test_*.py")}
    selected = modules if group == "all" else modules & FAST_MODULES if group == "fast" else modules - FAST_MODULES
    return unittest.defaultTestLoader.loadTestsFromNames(sorted(selected))


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("fast", "screenshots", "all"), default="fast")
    parser.add_argument("--quiet", action="store_true", help="仅显示最终结果")
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=1 if args.quiet else 2).run(build_suite(args.suite))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
