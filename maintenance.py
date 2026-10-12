"""Shutdown, log inspection and desktop restoration for the Codex heartbeat."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from device_lease import DeviceLease
from emulator_config import load_config, write_json
from emulator_runtime import discover_emulators, stop_emulator
from platform_support import SUBPROCESS_OPTIONS, configure_stdio
from runtime_logs import cleanup_logs, emulator_directory, LOG_NAME, safe_directory

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "configs" / "maintenance.json"


def saved_keys(root=ROOT):
    keys = []
    for path in (Path(root) / "configs").glob("*.json"):
        if len(path.stem) != 64:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        key = data.get("emulator") if isinstance(data, dict) else None
        if not isinstance(key, str) or not key.startswith("avd:"):
            continue
        if not load_config(root, key):
            raise ValueError(f"Missing saved configuration: {key}")
        keys.append(key)
    return sorted(set(keys))


def process_inventory():
    command = ("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
               "@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,"
               "Name,ExecutablePath,CommandLine) | ConvertTo-Json -Compress")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            check=True, capture_output=True, text=True, encoding="utf-8",
                            timeout=20, **SUBPROCESS_OPTIONS)
    rows = json.loads(result.stdout or "[]")
    return rows if isinstance(rows, list) else [rows]


def desktop_states(root=ROOT, processes=None):
    processes = process_inventory() if processes is None else processes
    live = {row["ProcessId"]: row for row in processes}
    result = []
    for path in (Path(root) / "configs" / "runtime").glob("desktop-*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        process = live.get(data.get("bridge_pid"), {})
        command = process.get("CommandLine") or ""
        ancestor = process
        seen = set()
        owned = False
        desktop_pid = None
        while ancestor and ancestor.get("ProcessId") not in seen:
            seen.add(ancestor.get("ProcessId"))
            executable = ancestor.get("ExecutablePath") or ""
            if executable and Path(executable).parent == Path(root):
                owned = Path(executable).name.startswith("GameBot")
                if owned:
                    desktop_pid = ancestor["ProcessId"]
                    break
            ancestor = live.get(ancestor.get("ParentProcessId"))
        if owned and not data.get("closed") and "gui_web.py --bridge" in command:
            result.append(dict(data, desktop_pid=desktop_pid))
    return result


def latest_logs(key, root=ROOT):
    directory = Path(root) / "logs" / emulator_directory(key)
    result = {}
    if (not safe_directory(Path(root) / "logs", Path(root))
            or not safe_directory(directory, Path(root) / "logs")):
        return result
    runtime = [path for path in directory.glob("*/*-runtime.log")
               if LOG_NAME.fullmatch(path.name) and safe_directory(path, Path(root) / "logs")]
    if runtime:
        latest = max(runtime, key=lambda path: (path.parent.name, path.name))
        result["runtime"] = str(latest)
        result["exception"] = str(latest.with_name(latest.name.replace("-runtime.log", "-exception.log")))
    return result


def inspection(keys, root=ROOT):
    result = []
    for key in keys:
        paths = latest_logs(key, root)
        row = {"emulator": key, "logs": paths, "exception_tail": "", "runtime_tail": ""}
        for kind, path in paths.items():
            try:
                with Path(path).open("rb") as source:
                    source.seek(max(0, Path(path).stat().st_size - 48000))
                    row[kind + "_tail"] = source.read().decode("utf-8", errors="replace")
            except FileNotFoundError:
                pass
        result.append(row)
    return result


def prepare():
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        if state.get("phase") in ("prepared", "resuming"):
            state["phase"] = "stopping"
            write_json(STATE, state)
        if state.get("phase") not in ("stopping", "complete"):
            raise RuntimeError("Previous maintenance has not finished; check status and resume it first")
    else:
        state = {"phase": "complete"}
    devices, error = discover_emulators()
    if error:
        raise RuntimeError(error)
    connected = [row for row in devices if row["serial"]]
    if any(not row["avd"] for row in connected):
        raise RuntimeError("Cannot restart a non-AVD emulator automatically; configure its launcher first")
    if state["phase"] != "stopping":
        keys = saved_keys()
        active_keys = {row["emulator"] for row in desktop_states() if row.get("emulator")}
        running_keys = {row["key"] for row in connected}
        targets = sorted((active_keys | running_keys) & set(keys)) or keys
        if not targets:
            raise RuntimeError("No saved AVD task configuration to restore")
        state = {"version": 1, "phase": "stopping", "started": datetime.now().isoformat(),
                 "targets": targets, "devices": connected}
        write_json(STATE, state)
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(ROOT / "scripts" / "stop_desktop.ps1"), "-ProjectRoot", str(ROOT)],
                   check=True, timeout=180, **SUBPROCESS_OPTIONS)
    for row in connected:
        with DeviceLease(row["serial"], row["key"]):
            stop_emulator(row["serial"], row["avd"])
    remaining, error = discover_emulators()
    if error or any(row["serial"] for row in remaining):
        raise RuntimeError(error or "Some emulators are still connected")
    if any(row.get("ExecutablePath") and Path(row["ExecutablePath"]).parent == ROOT
           and row["Name"].startswith("GameBot") for row in process_inventory()):
        raise RuntimeError("Some GameBot desktop windows are still running")
    cleanup_logs(ROOT)
    state["phase"] = "prepared"
    write_json(STATE, state)
    return {"phase": "prepared", "targets": state["targets"], "logs": inspection(state["targets"])}


def resume():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if state["phase"] not in ("prepared", "resuming"):
        raise RuntimeError("Run prepare before resume")
    executables = [path for path in (ROOT / "GameBot.exe", ROOT / "GameBot.next.exe") if path.is_file()]
    if not executables:
        raise RuntimeError("Build GameBot.exe before resume")
    executable = max(executables, key=lambda path: path.stat().st_mtime)
    state["phase"] = "resuming"
    write_json(STATE, state)
    active = {row["emulator"]: row for row in desktop_states()}
    launches = state.setdefault("launches", {})
    live = {row["ProcessId"] for row in process_inventory()}
    for key in state["targets"]:
        if not load_config(ROOT, key):
            raise RuntimeError(f"Missing saved task configuration: {key}")
        if key in active and active[key].get("resume_state") == "failed":
            subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(ROOT / "scripts" / "stop_desktop.ps1"), "-ProjectRoot", str(ROOT),
                            "-DesktopPid", str(active[key]["desktop_pid"])],
                           check=True, timeout=40, **SUBPROCESS_OPTIONS)
        elif key in active or launches.get(key) in live:
            continue
        process = subprocess.Popen([str(executable)], cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=dict(os.environ, GAMEBOT_RESUME_KEY=key), **SUBPROCESS_OPTIONS)
        launches[key] = process.pid
        write_json(STATE, state)
    return {"phase": "resuming", "targets": state["targets"], "launches": launches,
            "next": "Run maintenance.py status until every target passes the city check and has a live task PID"}


def status():
    processes = process_inventory()
    live = {row["ProcessId"] for row in processes}
    desktops = desktop_states(processes=processes)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    targets = state.get("targets", saved_keys())
    devices, error = discover_emulators()
    ready_devices = {row["key"] for row in devices if row["status"] == "running"}
    running = {row["emulator"] for row in desktops if row.get("running") and row.get("task_pid") in live}
    ready = {row["emulator"] for row in desktops if row.get("task_ready") and row["emulator"] in running}
    complete = bool(targets) and not error and set(targets) <= (ready & ready_devices)
    if state.get("phase") == "resuming" and complete:
        state["phase"] = "complete"
        write_json(STATE, state)
    return {"phase": state.get("phase", "idle"), "complete": complete, "targets": targets,
            "running": sorted(running), "ready": sorted(ready), "desktops": desktops, "devices": devices,
            "device_error": error, "logs": inspection(targets)}


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "inspect", "resume", "status"))
    action = parser.parse_args().action
    if sys.platform != "win32":
        parser.error("Desktop maintenance is only available on Windows")
    with DeviceLease("codex-maintenance", str(ROOT.resolve())):
        result = inspection(saved_keys()) if action == "inspect" else globals()[action]()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
