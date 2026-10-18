"""Android device inventory and readiness checks for the desktop interface."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET

from platform_support import SUBPROCESS_OPTIONS, resolve_adb


ADB = resolve_adb()


def adb_output(*args, timeout=4):
    return subprocess.run([str(ADB), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          **SUBPROCESS_OPTIONS).stdout.strip()


def device_ready(serial):
    try:
        return adb_output("-s", serial, "get-state") == "device"
    except (OSError, subprocess.SubprocessError):
        return False


def avd_key(serial):
    """Keep task/config identity compatible with callers and existing AVDs."""
    if serial.startswith("emulator-"):
        try:
            name = adb_output("-s", serial, "emu", "avd", "name").splitlines()[0].strip()
            if name and name != "OK" and not name.startswith("KO:"):
                return f"avd:{name}"
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
    return f"serial:{serial}"


def nox_endpoints():
    """Read ADB forwarding ports from installed Nox instances."""
    if sys.platform != "win32":
        return {}
    import winreg
    folders = set()
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for branch in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                       r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                with winreg.OpenKey(hive, branch) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                                name = winreg.QueryValueEx(entry, "DisplayName")[0]
                                if "nox" not in name.lower() and "夜神" not in name:
                                    continue
                                icon = winreg.QueryValueEx(entry, "DisplayIcon")[0]
                                folders.add(Path(icon.split(",", 1)[0].strip('"')).parent)
                        except OSError:
                            continue
            except OSError:
                continue
    endpoints = {}
    for folder in folders:
        for config in (folder / "BignoxVMS").glob("*/*.vbox"):
            try:
                for node in ET.parse(config).iter():
                    if (node.tag.rsplit("}", 1)[-1] == "Forwarding"
                            and node.get("guestport") == "5555"
                            and node.get("proto") == "1"):
                        port = int(node.get("hostport", "0"))
                        if 0 < port < 65536:
                            endpoints[f"127.0.0.1:{port}"] = f"夜神模拟器 ({config.stem})"
            except (OSError, ET.ParseError, ValueError):
                continue
    return endpoints


def connect_nox(endpoints):
    reachable = set()
    for serial in endpoints:
        try:
            with socket.create_connection(("127.0.0.1", int(serial.rsplit(":", 1)[1])), timeout=0.2):
                pass
            reachable.add(serial)
            adb_output("connect", serial, timeout=2)
        except (OSError, subprocess.SubprocessError):
            continue
    return reachable


def discover_emulators():
    """Discover connected Nox instances and Android phones."""
    devices = {}
    errors = []
    nox = nox_endpoints()
    reachable_nox = connect_nox(nox)
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
        if connection not in {"device", "offline", "unauthorized"}:
            continue
        if serial.startswith("emulator-"):
            continue
        is_emulator = serial in nox
        if not is_emulator and connection == "device":
            try:
                is_emulator = any(adb_output("-s", serial, "shell", "getprop", prop) == "1"
                                  for prop in ("ro.kernel.qemu", "ro.boot.qemu"))
            except (OSError, subprocess.SubprocessError):
                pass
        key = f"serial:{serial}"
        model = next((item[6:].replace("_", " ") for item in parts[2:]
                      if item.startswith("model:")), "模拟器" if is_emulator else "安卓设备")
        name = model
        if serial in nox:
            name = nox[serial]
        status = {"device": "running", "offline": "offline", "unauthorized": "unauthorized"}[connection]
        # ADB keeps offline TCP transports after Nox exits. They do not
        # indicate startup; a closed forwarding port means it is stopped.
        if serial in nox and connection == "offline" and serial not in reachable_nox:
            status = "stopped"
        devices[key] = {"id": key, "key": key, "name": name,
                        "avd": "",
                        "serial": serial, "status": status,
                        "kind": "emulator" if is_emulator else "physical"}
    return list(devices.values()), "；".join(errors)
