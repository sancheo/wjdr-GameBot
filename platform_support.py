"""Host-specific process and Android SDK configuration."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
SUBPROCESS_OPTIONS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def resolve_adb():
    """Prefer an explicit override, then bundled tools, SDK and PATH."""
    if os.environ.get("GAMEBOT_ADB"):
        return Path(os.environ["GAMEBOT_ADB"]).expanduser()
    name = "adb.exe" if sys.platform == "win32" else "adb"
    bundled = ROOT / ".tools" / "platform-tools" / name
    candidates = [bundled]
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "platform-tools" / name)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk" / "platform-tools" / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(shutil.which(name) or bundled)


def configure_stdio():
    # Windows redirected streams otherwise use the locale encoding (often GBK),
    # which cannot encode warning emoji. Match the GUI pipe's explicit UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
