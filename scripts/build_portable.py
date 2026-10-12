"""Build an offline Windows x64 distribution from the installed project venv.

The private Python has an isolated ._pth file; no venv or machine Python is
required on the target. Supply an official x64 WebView2 Fixed Version folder.
"""

import argparse
from datetime import datetime
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_version import VERSION, REPOSITORY, UPDATE_PROTOCOL
from app_updates import version_tuple
VC_DLLS = (
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll", "concrt140.dll",
    "vcruntime140.dll", "vcruntime140_1.dll",
)
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def check_x64(path, allow_x86=False):
    with Path(path).open("rb") as source:
        if source.read(2) != b"MZ":
            raise ValueError(f"Not a Windows binary: {path}")
        source.seek(0x3C)
        offset = struct.unpack("<I", source.read(4))[0]
        source.seek(offset)
        signature = source.read(6)
        if signature != b"PE\0\0\x64\x86" and not (allow_x86 and signature == b"PE\0\0\x4c\x01"):
            raise ValueError(f"An x64 binary is required: {path}")


def run(command, **kwargs):
    print("Running:", command[0], flush=True)
    kwargs.setdefault("stdout", subprocess.PIPE)
    result = subprocess.run([str(arg) for arg in command], stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command[0]}\n{result.stderr}")


def verify(package):
    # Verify relocation and ignore host Python configuration/PATH. UI self-check
    # uses a fake empty device inventory, so this never operates a game or AVD.
    environment = dict(os.environ, PYTHONHOME="Z:/missing-python",
                       PYTHONPATH="Z:/missing-packages", PYTHONIOENCODING="utf-8",
                       PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                       WEBVIEW2_BROWSER_EXECUTABLE_FOLDER=str(package / "runtime/webview2"))
    python = package / "runtime/python/python.exe"
    code = """
import sys
import ctypes
from pathlib import Path
import cv2, numpy, onnxruntime
from PIL import Image, ImageDraw, ImageFont
from ocr_backend import recognize_text
from platform_support import resolve_adb
from gui_web import build_command
root = Path(sys.argv[1]).resolve()
assert Path(sys.prefix).resolve() == root / 'runtime/python', sys.prefix
assert sys.flags.isolated and sys.flags.ignore_environment
assert all(Path(p).resolve().is_relative_to(root) for p in sys.path if p), sys.path
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel.GetModuleHandleW.restype = ctypes.c_void_p
kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
for name in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll'):
    buffer = ctypes.create_unicode_buffer(32768)
    handle = kernel.GetModuleHandleW(name)
    assert handle and kernel.GetModuleFileNameW(handle, buffer, len(buffer)), name
    assert Path(buffer.value).resolve().is_relative_to(root), (name, buffer.value)
image = Image.new('RGB', (500, 70), 'white')
ImageDraw.Draw(image).text((10, 10), 'GameBot 12:34:56', fill='black', font=ImageFont.truetype('arial.ttf', 32))
text = ''.join(t for t, _ in recognize_text(image)).replace(' ', '')
assert 'GameBot' in text and '12:34:56' in text, text
assert resolve_adb().resolve() == root / '.tools/platform-tools/adb.exe'
assert (root / 'assets/desktop.html').is_file()
print('Private Python, native dependencies, OCR models and assets: OK', flush=True)
"""
    with tempfile.TemporaryDirectory(prefix="GameBot-check-") as unrelated:
        # ADB version may initialize its user directory; keep validation state
        # temporary instead of touching the developer's Android configuration.
        environment["ANDROID_USER_HOME"] = unrelated
        run([python, "-X", "utf8", "-c", code, package], cwd=unrelated,
            env=environment, timeout=120)
        run([python, "-X", "utf8", package / "help_bot.py", "--help"],
            cwd=unrelated, env=environment, stdout=subprocess.DEVNULL, timeout=30)
        run([package / ".tools/platform-tools/adb.exe", "version"],
            cwd=unrelated, env=environment, timeout=15)
        result = subprocess.run([str(package / "GameBot.exe"), "--check-ui"],
                                cwd=unrelated, env=environment, capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        print(result.stdout, end="", flush=True)
        if result.returncode:
            raise RuntimeError(f"Desktop self-check failed: {result.stderr}")
        if "Desktop EXE checks passed:" not in result.stdout:
            raise RuntimeError(f"Desktop self-check did not confirm success: {result.stdout}")


def build(browser, vc_directory, headless_wheel=None, version=VERSION):
    version = '.'.join(map(str, version_tuple(version)))
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Build with the Windows x64 project venv Python.")
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        raise RuntimeError("Use .venv/Scripts/python.exe to preserve the project dependencies.")
    browser = browser.resolve()
    check_x64(browser / "msedgewebview2.exe")
    base = Path(sys.base_prefix)
    check_x64(base / "python.exe")
    sdk = ROOT / ".venv/Lib/site-packages/webview/lib"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    adb = ROOT / ".tools/platform-tools"
    # Official ADB is also distributed as x86 and works on x64 Windows.
    check_x64(adb / "adb.exe", allow_x86=True)
    for name in VC_DLLS:
        check_x64(vc_directory / name)
    packages = ROOT / ".venv/Lib/site-packages"
    if not list(packages.glob("opencv_python_headless-*.dist-info")) and headless_wheel is None:
        raise RuntimeError("The venv has GUI OpenCV. Supply --opencv-headless-wheel with a matching x64 wheel, "
                           "or install requirements.txt in a clean venv. GUI OpenCV requires Windows media components.")
    if headless_wheel:
        headless_wheel = headless_wheel.resolve()
        if not headless_wheel.name.endswith("win_amd64.whl") or not headless_wheel.name.startswith("opencv_python_headless-"):
            raise ValueError("Supply an official opencv-python-headless Windows x64 wheel.")
        with zipfile.ZipFile(headless_wheel) as wheel:
            metadata = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
            wheel_version = next(line.split(": ", 1)[1] for line in wheel.read(metadata).decode().splitlines()
                                 if line.startswith("Version: "))
        installed_version = importlib.metadata.version("opencv-python")
        if wheel_version != installed_version:
            raise ValueError(f"Headless wheel must match installed OpenCV {installed_version}.")
    # Every build gets its own destination; never overwrite an existing release.
    release_name = "GameBot-Windows-x64-v" + version + "-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = ROOT / "dist" / release_name
    destination.mkdir(parents=True, exist_ok=False)
    python = destination / "runtime/python"
    python.mkdir(parents=True)
    for pattern in ("python*.exe", "python*.dll", "vcruntime*.dll", "LICENSE*"):
        for source in base.glob(pattern):
            shutil.copy2(source, python / source.name)
    shutil.copytree(base / "DLLs", python / "DLLs", ignore=IGNORED)
    shutil.copytree(base / "Lib", python / "Lib", ignore=shutil.ignore_patterns(
        "site-packages", "__pycache__", "*.pyc", "test", "tests", "idlelib", "tkinter", "ensurepip"))
    excluded = ["__pycache__", "*.pyc", "pip", "pip-*.dist-info", ".DS_Store"]
    if headless_wheel:
        excluded.extend(["cv2", "opencv_python-*.dist-info", "opencv_python_headless-*.dist-info"])
    shutil.copytree(packages, python / "Lib/site-packages", ignore=shutil.ignore_patterns(*excluded))
    if headless_wheel:
        with zipfile.ZipFile(headless_wheel) as wheel:
            wheel.extractall(python / "Lib/site-packages")
    # Explicit paths are relative to this file, and ignore registry/user/env paths.
    (python / f"python{sys.version_info.major}{sys.version_info.minor}._pth").write_text(
        ".\nDLLs\nLib\nLib\\site-packages\n..\\..\nimport site\n", encoding="utf-8")
    for name in VC_DLLS:
        shutil.copy2(vc_directory / name, python / name)
    shutil.copytree(browser, destination / "runtime/webview2", ignore=IGNORED)
    shutil.copytree(sdk, destination / "runtime/webview-sdk", ignore=IGNORED)
    shutil.copytree(ROOT / "assets", destination / "assets", ignore=IGNORED)
    tools = destination / ".tools/platform-tools"
    tools.mkdir(parents=True)
    for name in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "NOTICE.txt", "source.properties"):
        shutil.copy2(adb / name, tools / name)
    for source in ROOT.glob("*.py"):
        if not source.name.startswith("test_") and source.name != "run_tests.py":
            shutil.copy2(source, destination / source.name)
    (destination / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/stop_desktop.ps1", destination / "scripts/stop_desktop.ps1")
    shutil.copy2(ROOT / "README.md", destination / "README.md")
    (destination / "release.json").write_text(json.dumps({"version": version, "repository": REPOSITORY,
        "platform": "windows-x64", "update_protocol": UPDATE_PROTOCOL}, indent=2) + "\n", encoding="utf-8")
    versions = sorted(f"{dist.metadata['Name']}=={dist.version}" for dist in
                      importlib.metadata.distributions(path=[str(python / "Lib/site-packages")]))
    (destination / "requirements-bundled.txt").write_text("\n".join(versions) + "\n", encoding="utf-8")
    run([compiler, "/nologo", "/target:winexe", "/platform:x64", "/optimize+", "/codepage:65001",
         f"/out:{destination / 'GameBot.exe'}", f"/win32icon:{ROOT / 'assets/gamebot-icon.ico'}",
         "/reference:System.Drawing.dll", "/reference:System.Windows.Forms.dll",
         "/reference:System.Web.Extensions.dll", f"/reference:{sdk / 'Microsoft.Web.WebView2.Core.dll'}",
         f"/reference:{sdk / 'Microsoft.Web.WebView2.WinForms.dll'}", ROOT / "desktop/GameBot.cs"])
    run([compiler, "/nologo", "/target:winexe", "/platform:x64", "/optimize+", "/codepage:65001",
         f"/out:{destination / 'GameBot.Updater.exe'}", "/reference:System.Drawing.dll",
         "/reference:System.Windows.Forms.dll", "/reference:System.Web.Extensions.dll", "/reference:System.Core.dll",
         ROOT / "desktop/Updater.cs"])
    return finish_build(destination)


def finish_build(destination):
    destination = destination.resolve()
    if destination.parent != (ROOT / "dist").resolve() or not destination.name.startswith("GameBot-Windows-x64-"):
        raise ValueError("Only a generated release directory directly inside dist can be archived.")
    release_name = destination.name
    verify(destination)
    # These are self-check output inside a freshly created build directory.
    # Resolve every delete target and restrict it to this build before removing it.
    for name in (".desktop-cache", "configs", "logs", "artifacts", "__pycache__"):
        state = (destination / name).resolve()
        if not state.is_relative_to(destination.resolve()) or state == destination.resolve():
            raise RuntimeError(f"Unsafe build cleanup target: {state}")
        def missing_is_ok(function, path, error):
            if not isinstance(error, FileNotFoundError):
                raise error
        for attempt in range(6):
            try:
                if state.exists():
                    shutil.rmtree(state, onexc=missing_is_ok)
                break
            except PermissionError:
                # WebView2 can finish writing cache files shortly after EXE exit.
                if attempt == 5:
                    raise
                time.sleep(1)
    version = json.loads((destination / "release.json").read_text(encoding="utf-8"))["version"]
    version_tuple(version)
    archive = destination.parent / f"GameBot-Windows-x64-v{version}.zip"
    print(f"Creating archive: {archive}", flush=True)
    # Include hidden .tools explicitly. Exclude all state created by the self-check.
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for source in sorted(destination.rglob("*")):
            relative = source.relative_to(destination)
            if (source.is_file() and relative.parts[0] not in
                    {".desktop-cache", "configs", "logs", "artifacts"}
                    and "__pycache__" not in relative.parts
                    and source.suffix != ".pyc"):
                output.write(source, Path(release_name) / relative)
    with zipfile.ZipFile(archive) as output:
        if {Path(item.filename).parts[0] for item in output.infolist()} != {release_name}:
            raise RuntimeError("The archive must contain exactly one correctly named release directory.")
    with archive.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest + '  ' + archive.name + '\n', encoding='ascii')
    print(f"Built: {archive}\nSize: {archive.stat().st_size / 1024**2:.1f} MiB", flush=True)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--webview-runtime", type=Path,
                        help="Official extracted x64 Fixed Version folder containing msedgewebview2.exe")
    operation.add_argument("--verify", type=Path, help="Verify an extracted portable package without building")
    operation.add_argument("--finish-build", type=Path, help="Verify and archive an existing generated build in dist")
    parser.add_argument("--vc-runtime", type=Path,
                        default=Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32",
                        help="x64 Microsoft VC runtime DLL directory (default: System32)")
    parser.add_argument("--opencv-headless-wheel", type=Path,
                        help="Matching official headless x64 wheel when the source venv has GUI OpenCV")
    parser.add_argument("--version", default=VERSION, help="Release version, e.g. 1.0.1 (must match the GitHub tag)")
    args = parser.parse_args()
    if args.verify:
        verify(args.verify.resolve())
    elif args.finish_build:
        finish_build(args.finish_build)
    else:
        build(args.webview_runtime, args.vc_runtime, args.opencv_headless_wheel, args.version)
