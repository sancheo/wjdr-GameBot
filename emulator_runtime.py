"""Android AVD inventory and readiness checks for the desktop interface."""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from platform_support import SUBPROCESS_OPTIONS, resolve_adb


ADB = resolve_adb()


def resolve_emulator():
    name = "emulator.exe" if sys.platform == "win32" else "emulator"
    candidates = []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "emulator" / name)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk" / "emulator" / name)
    candidates.append(ADB.parent.parent / "emulator" / name)
    return next((path for path in candidates if path.is_file()), Path(shutil.which(name) or candidates[-1]))


def adb_output(*args, timeout=4):
    return subprocess.run([str(ADB), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          **SUBPROCESS_OPTIONS).stdout.strip()


def avd_key(serial):
    if serial.startswith("emulator-"):
        try:
            name = adb_output("-s", serial, "emu", "avd", "name").splitlines()[0].strip()
            if name and name != "OK" and not name.startswith("KO:"):
                return f"avd:{name}"
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
    return f"serial:{serial}"


def device_ready(serial, avd=False):
    try:
        return (adb_output("-s", serial, "get-state") == "device"
                and (not avd or adb_output("-s", serial, "shell", "getprop", "sys.boot_completed") == "1"))
    except (OSError, subprocess.SubprocessError):
        return False


def emulator_console_open(port):
    """Check Windows listeners without a TCP probe that can time out after shutdown."""
    if sys.platform == "win32":
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "netstat.exe"
        try:
            output = subprocess.run([str(executable), "-ano", "-p", "tcp"], check=True,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=4, **SUBPROCESS_OPTIONS).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"无法确认模拟器控制台端口是否关闭：{exc}") from exc
        for line in output.splitlines():
            parts = line.split()
            if (len(parts) >= 4 and parts[0] == "TCP" and parts[3] == "LISTENING"
                    and parts[1].rsplit(":", 1)[-1] == str(port)):
                return True
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except (ConnectionRefusedError, ConnectionResetError):
        return False
    except TimeoutError:
        # An unanswered probe does not prove that the emulator has exited.
        return True


def stop_emulator(serial, avd, timeout=30):
    """Close only the requested AVD, then wait for its transport and console to exit."""
    if not serial.startswith("emulator-") or not serial[9:].isdigit() or avd_key(serial) != f"avd:{avd}":
        raise ValueError("模拟器连接标识已变化，请刷新模拟器列表后再重启")
    reply = adb_output("-s", serial, "emu", "kill")
    if any(line.startswith("KO:") for line in reply.splitlines()):
        raise RuntimeError(f"关闭模拟器失败：{reply}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        output = adb_output("devices")
        connected = any(line.split() and line.split()[0] == serial for line in output.splitlines()[1:])
        if not connected and not emulator_console_open(int(serial[9:])):
            return
        time.sleep(0.5)
    raise RuntimeError("等待模拟器关闭超时，未重新启动，请检查模拟器窗口后刷新")


def discover_emulators():
    """Merge installed AVDs with connected emulators, retaining partial results."""
    devices = {}
    errors = []
    try:
        output = subprocess.run([str(resolve_emulator()), "-list-avds"], check=True,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=15, **SUBPROCESS_OPTIONS).stdout
        for name in output.splitlines():
            name = name.strip()
            if name:
                key = f"avd:{name}"
                devices[key] = {"id": key, "key": key, "name": name, "avd": name,
                                "serial": "", "status": "stopped"}
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"读取 AVD 列表失败：{exc}")
    try:
        output = subprocess.run([str(ADB), "devices", "-l"], check=True, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=15,
                                **SUBPROCESS_OPTIONS).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        for device in devices.values():
            device["status"] = "unknown"
        return list(devices.values()), "；".join([*errors, f"读取连接状态失败：{exc}"])
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, connection = parts[:2]
        avd = serial.startswith("emulator-")
        if not avd:
            if connection != "device":
                continue
            try:
                if not any(adb_output("-s", serial, "shell", "getprop", prop) == "1"
                           for prop in ("ro.kernel.qemu", "ro.boot.qemu")):
                    continue
            except (OSError, subprocess.SubprocessError):
                continue
        key = avd_key(serial)
        model = next((item[6:].replace("_", " ") for item in parts[2:]
                      if item.startswith("model:")), "模拟器")
        name = key[4:] if key.startswith("avd:") else model
        ready = connection == "device" and (not avd or device_ready(serial, avd=True))
        devices[key] = {"id": key, "key": key, "name": name,
                        "avd": name if key.startswith("avd:") else "",
                        "serial": serial, "status": "running" if ready else "starting"}
    # An offline ADB transport cannot always reveal its AVD name. Do not offer
    # duplicate launches until these anonymous emulators finish connecting.
    if any(item["status"] == "starting" and not item["avd"] for item in devices.values()):
        for item in devices.values():
            if item["status"] == "stopped":
                item["status"] = "unknown"
        errors.append("有模拟器正在连接，待连接完成后刷新以确认 AVD 状态")
    return list(devices.values()), "；".join(errors)
