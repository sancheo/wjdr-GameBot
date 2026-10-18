"""Python task backend for the native Windows desktop EXE."""

from collections import deque
from contextlib import redirect_stdout
from copy import deepcopy
import math
import os
import json
from datetime import datetime
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from emulator_config import load_config, save_config, write_json
from device_notes import load_note, save_note
from emulator_runtime import device_ready, discover_emulators
from platform_support import SUBPROCESS_OPTIONS, configure_stdio, terminate_task_process
from device_lease import DeviceLease
from runtime_control import EVENT_PREFIX
from task_status import validate_task_event
from hunting import MINIMUM_LEVELS
from runtime_logs import SessionLogs, is_error, is_retry
from app_updates import UpdateManager


ROOT = Path(__file__).resolve().parent
TASKS = (
    ("help", "自动联盟互助"), ("explore", "自动领取探险经验"),
    ("donate", "自动捐献"), ("alliance_chests", "联盟宝箱"), ("recruit", "自动免费招募"),
    ("treasure", "宠物寻宝"), ("warehouse", "自动领取仓库补给"),
    ("tree", "自动收集生命结晶"), ("dawn", "自动收集晨曦回礼"),
    ("pet", "宠物技能"), ("task_rewards", "任务奖励"), ("reconnect", "强制下线后自动重连"),
)
UNITS = (("shield", "盾兵"), ("spear", "矛兵"), ("archer", "射手"))
RESOURCES = (("meat", "生肉"), ("wood", "木材"), ("coal", "煤矿"), ("iron", "铁矿"))
RESOURCE_LEVELS = tuple(f"{level}级" for level in range(1, 9))
MONSTERS = (("beast", "野兽", 30), ("polar_beast", "冰原巨兽", 6))


def timestamp_line(line):
    return line if re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", line) else f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}"


def exception_reason(lines, code):
    for line in reversed(lines):
        if "已停止：" in line:
            return line.split("已停止：", 1)[1]
    for line in reversed(lines):
        if re.match(r"^(?:\w+\.)*\w*(?:Error|Exception):", line):
            return line
    return f"程序异常退出（退出码 {code}）"


class StartupGuideBlocked(RuntimeError):
    """A mandatory game guide prevents ordinary task navigation."""


def startup_guide_blocked(image):
    import help_bot as bot
    template = bot.open_template(ROOT / "assets/emulator-startup-guide-hand.png").convert("L")
    score, _, _ = bot.minimum_difference(image.convert("L"), template,
                                         (530, 1120, 720, 1360),
                                         range(-24, 25, 4), range(-24, 25, 4))
    if score <= 12:
        return True
    # The running-city guide uses a different hand position and animation
    # from the cold-start beast guide. Match observed poses, not the city.
    for pose in ("first", "last"):
        template = bot.open_template(ROOT / f"assets/emulator-city-guide-hand-{pose}.png").convert("L")
        score, _, _ = bot.minimum_difference(image.convert("L"), template,
                                             (525, 930, 710, 1190),
                                             range(-24, 25, 4), range(-24, 25, 4))
        if score <= 15:
            return True
    return False


def game_loaded(image):
    """Only confirmed game surfaces end the passive cold-start wait."""
    import help_bot as bot
    if startup_guide_blocked(image):
        return False
    nav = bot.open_template(ROOT / "assets/emulator-city-nav.png").convert("L")
    if bot.difference(image.crop(bot.NAV_BOX), nav) <= bot.NAV_LIMIT:
        return True
    if bot.dawn_gift_close_target(image) is not None:
        return True
    if bot.legendary_gift_close_target(image) is not None:
        return True
    if bot.bazaar_close_target(image) is not None:
        return True
    if bot.secret_choice_close_target(image) is not None:
        return True
    if bot.wish_shop_close_target(image) is not None:
        return True
    text = bot.open_template(ROOT / "assets/emulator-forced-text.png").convert("L")
    reconnect = bot.open_template(ROOT / "assets/emulator-reconnect.png").convert("L")
    if bot.forced_offline(image, text, reconnect):
        return True
    if bot.network_reconnect_target(image) is not None:
        return True
    if bot.city_relocation_target(image) is not None:
        return True
    # A maintenance restart can reopen the game on an existing training page.
    # It is already loaded; the task process can safely navigate back to city.
    if bot.training_back_target(image) is not None:
        return True
    for name in ("reconnect-promo-close", "reconnect-welcome-close", "activity-close"):
        template = bot.open_template(ROOT / f"assets/emulator-{name}.png").convert("L")
        # Use the same calibrated activity-X threshold as city recovery.
        # Real sale panels vary in outline/shadow and score below .95.
        limit = .88 if name == "activity-close" else .95
        if bot.find_white(image, template, range(750, 1001, 5), range(300, 901, 5))[0] >= limit:
            return True
    return False


def startup_story_skip_target(image):
    """Require both the observed story skip button and dialogue marker."""
    import help_bot as bot
    for box, name in (((845, 235, 1045, 326), "story-skip"),
                      ((940, 2210, 1030, 2310), "story-dialog-marker")):
        template = bot.open_template(ROOT / f"assets/emulator-{name}.png").convert("L")
        if bot.difference(image.crop(box).convert("L"), template) > 8:
            return None
    return (950, 280)


def default_config():
    options = {name: True for name, _ in (*TASKS, *UNITS, *RESOURCES)}
    options.update(train=True, upgrade=False, heal=True, intelligence=True, hunt=True,
                   beast=True, polar_beast=True, gather=True, gather_full_only=True, bounty=False,
                   help_clicker=False, bear=False, bear_drive=False, bear_follow=False, bear_follow_no_heroes=True)
    return {"options": options,
            "entries": {"tree_threshold": "1000", "reconnect_minutes": "10", "stamina_threshold": "0"},
            "resource_levels": {name: RESOURCE_LEVELS[-1] for name, _ in RESOURCES},
            "gather_steps": [{"resource": name, "level": "8级"} for name, _ in RESOURCES],
            "hunt_levels": {"beast": "1级", "polar_beast": "2级"}}


def checked_config(config, *, from_disk=False):
    """Limit bridge data to known settings; retain invalid input for Start validation."""
    result = default_config()
    if not isinstance(config, dict):
        raise ValueError("任务配置格式不正确")
    for section in result:
        if section == "gather_steps":
            continue
        supplied = config.get(section, {})
        if not isinstance(supplied, dict):
            raise ValueError("任务配置格式不正确")
        for name, value in supplied.items():
            if name not in result[section]:
                continue
            if section == "options" and name == "help_clicker" and from_disk:
                continue
            if section == "options" and isinstance(value, bool):
                result[section][name] = value
            elif section == "resource_levels":
                if from_disk and isinstance(value, str):
                    value = value.removesuffix("矿")
                if value in RESOURCE_LEVELS:
                    result[section][name] = value
                elif not from_disk and not (result["options"]["help_clicker"] or result["options"]["bear"]):
                    raise ValueError("资源采集等级必须选择 1级 到 8级")
            elif section == "hunt_levels":
                maximum = next(limit for kind, _, limit in MONSTERS if kind == name)
                minimum = MINIMUM_LEVELS[name]
                if value in tuple(f"{level}级" for level in range(minimum, maximum + 1)):
                    result[section][name] = value
                elif not from_disk and not (result["options"]["help_clicker"] or result["options"]["bear"]):
                    raise ValueError(f"打怪等级必须选择 {minimum} 到 {maximum} 级")
            elif section == "entries" and isinstance(value, str) and len(value) <= 64:
                if from_disk:
                    try:
                        number = float(value) if name == "reconnect_minutes" else int(value)
                        if not math.isfinite(number) or (number < 0 if name == "stamina_threshold" else number <= 0):
                            continue
                    except (ValueError, OverflowError):
                        continue
                result[section][name] = value.strip()
    if from_disk and not any(result["options"][name] for name, _ in RESOURCES):
        for name, _ in RESOURCES:
            result["options"][name] = True
    if "gather_steps" in config:
        try:
            result["gather_steps"] = checked_gather_steps(config["gather_steps"])
        except ValueError:
            if not from_disk and not (result["options"]["help_clicker"] or result["options"]["bear"]):
                raise
    elif "resource_levels" in config or "options" in config:
        result["gather_steps"] = [
            {"resource": name, "level": result["resource_levels"][name]}
            for name, _ in RESOURCES if result["options"][name]]
        if not result["gather_steps"]:
            result["gather_steps"] = default_config()["gather_steps"]
    if result["options"]["bear"] and result["options"]["help_clicker"]:
        raise ValueError("自动打熊与互助连点不能同时开启")
    if not (result["options"]["help_clicker"] or result["options"]["bear"]) and not any(result["options"][name] for name, _, _ in MONSTERS):
        if not from_disk:
            raise ValueError("至少要勾选一项打怪目标")
        for name, _, _ in MONSTERS:
            result["options"][name] = True
    return result


def checked_gather_steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 6:
        raise ValueError("资源采集必须配置 1 到 6 个子选项")
    result = []
    for step in steps:
        if not isinstance(step, dict) or step.get("resource") not in dict(RESOURCES):
            raise ValueError("资源采集类型必须选择生肉、木材、煤矿或铁矿")
        if step.get("level") not in RESOURCE_LEVELS:
            raise ValueError("资源采集等级必须选择 1级 到 8级")
        result.append({"resource": step["resource"], "level": step["level"]})
    return result


def build_command(config, serial):
    if not serial:
        raise ValueError("请先连接并选择一台安卓设备（模拟器或真机）")
    options, entries = config["options"], config["entries"]
    command = [sys.executable, "-X", "utf8", "-u", str(ROOT / "help_bot.py"),
               "--device", serial, "--forever", "--control"]
    if options["reconnect"]:
        try:
            minutes = float(entries["reconnect_minutes"])
        except ValueError:
            raise ValueError("重连等待时间必须是大于 0 的数字") from None
        if not math.isfinite(minutes) or minutes <= 0:
            raise ValueError("重连等待时间必须大于 0")
        command += ["--reconnect-minutes", str(minutes)]
    else:
        command.append("--no-reconnect")
    if options.get("bear"):
        if options["help_clicker"]:
            raise ValueError("自动打熊与互助连点不能同时开启")
        modes = ["drive"] if options["bear_drive"] else []
        if options["bear_follow_no_heroes"]:
            modes.append("follow_no_heroes")
        elif options["bear_follow"]:
            modes.append("follow")
        if not modes:
            raise ValueError("自动打熊至少要勾选一项")
        return command + ["--bear", "--bear-modes", *modes]
    if options["help_clicker"]:
        command.append("--help-clicker")
        return command
    command.append("--daily-tasks")
    for name, _ in (*TASKS, *UNITS):
        if name != "reconnect" and not options[name]:
            command.append(f"--no-{name.replace('_', '-')}")
    if not options["train"]:
        command.append("--no-train")
    elif options["upgrade"]:
        command.append("--upgrade")
    if options["heal"]:
        command.append("--heal")
    if options["tree"]:
        try:
            value = int(entries["tree_threshold"])
        except ValueError:
            raise ValueError("生命结晶阈值必须是大于 0 的整数") from None
        if value <= 0:
            raise ValueError("生命结晶阈值必须大于 0")
        command += ["--tree-threshold", str(value)]
    if options["intelligence"]:
        try:
            threshold = int(entries["stamina_threshold"])
        except ValueError:
            raise ValueError("体力触发阈值必须是大于或等于 0 的整数") from None
        if threshold < 0:
            raise ValueError("体力触发阈值必须大于或等于 0")
        command += ["--intelligence", "--stamina-threshold", str(threshold)]
        if options["bounty"]:
            command.append("--bounty")
    if options["hunt"]:
        monsters = [name for name, _, _ in MONSTERS if options[name]]
        if not monsters:
            raise ValueError("至少要勾选一项打怪目标")
        command += ["--hunt", "--monsters", *monsters]
        for name, label, maximum in MONSTERS:
            if name not in monsters:
                continue
            value = config["hunt_levels"][name]
            minimum = MINIMUM_LEVELS[name]
            if value not in tuple(f"{level}级" for level in range(minimum, maximum + 1)):
                raise ValueError(f"{label}等级必须选择 {minimum} 到 {maximum} 级")
            command += [f"--{name.replace('_', '-')}-level", value[:-1]]
    if options["gather"]:
        if not options.get("gather_full_only", True):
            command.append("--no-gather-full-only")
        steps = checked_gather_steps(config["gather_steps"])
        command += ["--gather", "--gather-steps",
                    *(f"{step['resource']}:{step['level'][:-1]}" for step in steps)]
    return command


class DesktopApi:
    """A small, thread-safe bridge. No resize events cross into Python."""

    def __init__(self, root=ROOT):
        self._root = Path(root)
        self._lock = threading.RLock()
        self._config = default_config()
        self._sessions = {}
        self._devices = []
        self._keys = {}
        self._selected = ""
        self._active_key = None
        self._scanning = False
        self._background_scan = False
        self._device_status = "正在查找安卓设备…"
        self._process = None
        self._stopping = False
        self._closed = False
        self._logs = deque()
        self._device_logs = {"desktop": self._logs}
        self._seq = 0
        self._notice = None
        self._notice_id = 0
        self._reader = None
        self._controlled_process = False
        self._task_status = {}
        self._task_ready = False
        self._resume_state = "idle"
        self._launch_wakeup = threading.Event()
        self._disk_logs = SessionLogs(self._root, "desktop")
        self._updates = UpdateManager(self._root, enabled="--check-ui" not in sys.argv)
        self._updating = False

    def get_state(self, since=0):
        with self._lock:
            since = since if isinstance(since, int) and since >= 0 else 0
            reset = bool(self._logs and since < self._logs[0]["id"] - 1)
            return {"devices": deepcopy(self._devices), "selected": self._selected,
                    "device_status": self._device_status, "scanning": self._scanning and not self._background_scan,
                    "running": self._process is not None, "stopping": self._stopping,
                    "config": deepcopy(self._config), "notice": deepcopy(self._notice),
                    "task_status": deepcopy(list(self._task_status.values())),
                    "logs": [dict(line) for line in self._logs if reset or line["id"] > since],
                    "log_reset": reset, "log_key": self._active_key or "desktop",
                    "last_log": self._seq, "update": self._updates.snapshot()}

    def open_screenshot(self, path):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("截图路径无效")
        screenshots = (self._root / "artifacts" / "exception_screenshots").resolve()
        image = Path(path)
        if not image.is_absolute():
            image = self._root / image
        image = image.resolve()
        if not image.is_relative_to(screenshots) or image.suffix.lower() != ".png":
            raise ValueError("只能打开异常截图目录中的 PNG 图片")
        if not image.is_file():
            raise ValueError("截图不存在，可能已被自动清理")
        try:
            os.startfile(str(image))
        except OSError as exc:
            raise ValueError(f"无法打开截图：{exc}") from exc
        return True

    def check_updates(self):
        self._updates.check()
        return self.get_state()

    def download_update(self):
        self._updates.download()
        return self.get_state()

    def cancel_update_download(self):
        self._updates.cancel_download()
        return self.get_state()

    def install_update(self):
        with self._lock:
            if self._closed or self._resume_state == "starting":
                raise ValueError("请等待设备任务恢复完成后再安装更新")
            plan = self._updates.prepare_install()
            self._updating = True
            return {"install_plan": plan}

    def _append(self, line):
        with self._lock:
            self._seq += 1
            error = is_error(line)
            self._logs.append({"id": self._seq, "text": timestamp_line(line), "error": error,
                               "warning": is_retry(line)})
            try:
                self._disk_logs.append(line, error)
            except OSError as exc:
                self._notice_id += 1
                self._notice = {"id": self._notice_id, "message": f"本地日志保存失败：{exc}"}

    def _save_runtime(self):
        try:
            pid = getattr(self._process, "pid", None)
            write_json(self._root / "configs" / "runtime" / f"desktop-{os.getpid()}.json",
                       {"version": 1, "bridge_pid": os.getpid(), "emulator": self._active_key,
                        "running": self._process is not None, "closed": self._closed,
                        "task_ready": self._task_ready,
                        "resume_state": self._resume_state,
                        "task_status": list(self._task_status.values()),
                        "task_pid": pid if isinstance(pid, int) else None,
                        "logs": {kind: str(path) for kind, path in self._disk_logs.paths().items()}})
        except OSError as exc:
            self._append(f"⚠️ 运行状态保存失败：{exc}")

    def refresh_devices(self):
        return self._begin_scan()

    def _begin_scan(self, background=False):
        with self._lock:
            if self._closed or self._scanning:
                return self.get_state()
            self._scanning = True
            self._background_scan = background
            if not background:
                self._device_status = "正在查找安卓设备…"
            threading.Thread(target=self._scan, daemon=True).start()
            return self.get_state()

    def _scan(self):
        try:
            devices, error = discover_emulators()
        except (OSError, subprocess.SubprocessError) as exc:
            devices, error = [], str(exc)
        with self._lock:
            if self._closed:
                return
            previous, previous_key = self._selected, self._active_key
            if error and not devices:
                # A transient ADB failure must not discard the user's selection.
                devices = deepcopy(self._devices)
                for device in devices:
                    device["status"] = "unknown"
            for device in devices:
                try:
                    device["note"] = load_note(self._root, device["key"])
                except (OSError, ValueError) as exc:
                    device["note"] = ""
                    self._append(f"⚠️ 设备 {device['name']} 备注读取失败：{exc}")
            self._devices = devices
            self._keys = {device["id"]: device["key"] for device in devices}
            chosen = next((device["id"] for device in devices if device["id"] == previous), None)
            if chosen is None and previous_key:
                chosen = next((device["id"] for device in devices if device["key"] == previous_key), None)
            if self._process is None:
                preferred = next((device["id"] for device in devices if device["status"] == "running"), "")
                self._choose(chosen or preferred or (devices[0]["id"] if devices else ""))
            count = sum(device["status"] == "running" for device in devices)
            summary = (f"共 {len(devices)} 台设备，{count} 台已就绪" if devices
                       else "未发现安卓设备，请连接手机并开启 USB 调试，启动夜神模拟器后刷新")
            hints = []
            if any(device["status"] == "unauthorized" for device in devices):
                hints.append("手机待授权：请解锁手机并允许 USB 调试后刷新")
            if any(device["status"] == "offline" for device in devices):
                hints.append("设备离线：请检查数据线与 USB 调试后刷新")
            self._device_status = "；".join(filter(None, [error, summary, *hints]))
            self._scanning = False
            self._background_scan = False

    def _choose(self, serial):
        key = self._keys.get(serial, f"serial:{serial}") if serial else None
        self._selected = serial
        if key == self._active_key:
            return
        if self._active_key is not None:
            self._sessions[self._active_key] = deepcopy(self._config)
        self._active_key = key
        self._logs = self._device_logs.setdefault(key or "desktop", deque())
        self._disk_logs.key = key or "desktop"
        if key is not None:
            config = self._sessions.get(key)
            from_disk = config is None
            if config is None:
                try:
                    config = load_config(self._root, key)
                except (OSError, ValueError) as exc:
                    config = {}
                    self._append(f"⚠️ 设备配置读取失败，已使用默认配置：{exc}")
            self._config = checked_config(config, from_disk=from_disk)
        self._save_runtime()

    def select_device(self, serial, config):
        with self._lock:
            if self._process is not None or (self._scanning and not self._background_scan):
                raise ValueError("运行或刷新期间不能切换设备")
            if serial not in {device["id"] for device in self._devices}:
                raise ValueError("请刷新并选择一台安卓设备（模拟器或真机）")
            self._config = checked_config(config)
            self._choose(serial)
            return self.get_state()

    def set_device_note(self, device_id, note):
        with self._lock:
            device = next((item for item in self._devices if item["id"] == device_id), None)
            if device is None:
                raise ValueError("设备已不在列表中，请刷新后重试")
            device["note"] = save_note(self._root, device["key"], note)
            return self.get_state()

    def update_config(self, config):
        with self._lock:
            if self._process is not None:
                raise ValueError("请停止任务后修改配置")
            updated = checked_config(config)
            bear_changed = any(updated["options"][name] != self._config["options"][name]
                               for name in ("bear", "bear_drive", "bear_follow", "bear_follow_no_heroes"))
            self._config = updated
            # Save mode preferences as soon as they change, including before Start.
            if bear_changed and self._active_key:
                device = next((item for item in self._devices if item["id"] == self._selected), None)
                if device:
                    try:
                        save_config(self._root, self._active_key, device["serial"], self._config)
                    except (OSError, ValueError) as exc:
                        self._append(f"⚠️ 设备配置保存失败：{exc}")
            return self.get_state()

    def start(self, config):
        with self._lock:
            if self._updating:
                raise ValueError("正在安装更新，不能启动任务")
            if self._closed:
                raise ValueError("窗口已关闭")
            if self._process is not None:
                raise ValueError("任务已在运行")
            if self._scanning and not self._background_scan:
                raise ValueError("请等待设备刷新完成")
            device = next((item for item in self._devices if item["id"] == self._selected), None)
            if device is None:
                raise ValueError("请先选择一台安卓设备（模拟器或真机）")
            if device["status"] == "unauthorized":
                raise ValueError("手机尚未授权，请解锁手机，在“允许 USB 调试”弹窗中确认后刷新设备列表")
            if device["status"] == "offline":
                raise ValueError("设备已离线，请检查数据线、USB 调试和手机连接后刷新设备列表")
            if device["status"] == "stopped":
                raise ValueError(f"模拟器 {device['name']} 未启动，请先打开夜神模拟器后刷新")
            if device["status"] == "starting":
                raise ValueError(f"模拟器 {device['name']} 正在启动，请等待启动完成后再启动任务")
            if device["status"] != "running" or not device_ready(device["serial"]):
                raise ValueError(f"设备 {device['name']} 未连接或尚未就绪，请刷新设备列表")
            # The child holds the actual lease; this early check gives a useful
            # message without launching another process for an occupied device.
            with DeviceLease(device["serial"], device["key"]):
                pass
            self._config = checked_config(config)
            command = build_command(self._config, device["serial"])
            self._disk_logs = SessionLogs(self._root, device["key"])
            self._disk_logs.append(f"启动 GameBot：{device['name']} ({device['serial']})")
            environment = dict(os.environ, GAMEBOT_LOG_OWNER="gui")
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                       errors="replace", bufsize=1, env=environment, **SUBPROCESS_OPTIONS)
            self._process = process
            self._controlled_process = True
            self._task_status = {}
            self._task_ready = False
            self._stopping = False
            self._append("已启动。")
            if self._config["options"]["help_clicker"]:
                self._append("小号模式：只执行互助连点、启动活动跳过和自动重连。")
            elif self._config["options"]["bear"]:
                modes = "、".join(label for name, label in (("drive", "主动开车"), ("follow", "自动跟车"), ("follow_no_heroes", "无英雄跟车"))
                                 if self._config["options"][f"bear_{name}"])
                self._append(f"自动打熊配置：{modes}；其他任务已跳过。")
            elif self._config["options"]["hunt"]:
                levels = "、".join(f"{label} {self._config['hunt_levels'][name]}"
                                  for name, label, _ in MONSTERS if self._config["options"][name])
                self._append(f"自动打怪配置：{levels}")
            if self._config["options"]["gather"] and not (self._config["options"]["help_clicker"] or self._config["options"]["bear"]):
                levels = " → ".join(f"{dict(RESOURCES)[step['resource']]} {step['level']}"
                                    for step in self._config["gather_steps"])
                self._append(f"资源采集配置：{levels}")
            try:
                key = self._keys.get(self._selected, f"serial:{self._selected}")
                path = save_config(self._root, key, device["serial"], self._config)
                self._sessions[key] = deepcopy(self._config)
                self._append(f"设备配置已保存：{path}")
            except (OSError, ValueError) as exc:
                self._append(f"⚠️ 设备配置保存失败：{exc}")
            self._reader = threading.Thread(target=self._read_process, args=(process,), daemon=True)
            self._reader.start()
            self._save_runtime()
            return self.get_state()

    def _read_process(self, process):
        output = deque(maxlen=200)
        try:
            for line in process.stdout:
                line = line.rstrip()
                if line.startswith(EVENT_PREFIX):
                    self._receive_task_event(line[len(EVENT_PREFIX):])
                    continue
                output.append(line)
                self._append(line)
                if line.endswith(("启动时主城检查已通过，开始运行任务",
                                  "已进入联盟互助，持续快速点击底部全部帮助区域",
                                  "自动打熊主城检查已通过，开始运行任务")):
                    with self._lock:
                        self._task_ready = True
                        self._save_runtime()
        except OSError as exc:
            self._append(f"⚠️ 读取任务输出失败：{exc}")
        finally:
            process.stdout.close()
        code = process.wait()
        if process.stdin is not None:
            process.stdin.close()
        with self._lock:
            if self._process is not process:
                return
            stopped = self._stopping
            self._process = None
            self._task_ready = False
            self._controlled_process = False
            self._stopping = False
            reason = exception_reason(list(output), code) if code and not stopped else ""
            for row in self._task_status.values():
                row["retry_requested"] = False
                if row["status"] != "disabled":
                    row["status"] = "failed" if code and not stopped else "stopped"
                    row["next_run"] = None
                    if reason:
                        row["reason"] = reason
            if stopped:
                self._append("已停止。")
            elif code:
                if not any("已停止：" in line for line in output):
                    self._append(f"已停止：{reason}")
                self._notice_id += 1
                self._notice = {"id": self._notice_id, "message": reason}
            else:
                self._append("运行已结束。")
            self._save_runtime()

    def stop(self):
        with self._lock:
            if self._process is not None and not self._stopping:
                self._stopping = True
                process = self._process
                if self._controlled_process:
                    try:
                        self._write_control(process, {"action": "stop"})
                        self._append("正在停止：已取消后续点击，等待当前命令结束。")
                    except (OSError, ValueError):
                        terminate_task_process(process)
                    threading.Thread(target=self._wait_stop, args=(process,), daemon=True).start()
                else:
                    process.terminate()
            return self.get_state()

    def _receive_task_event(self, payload):
        try:
            if len(payload) > 16384:
                raise ValueError("状态消息过大")
            row = validate_task_event(json.loads(payload))
            if row is None:
                raise ValueError("状态消息格式不正确")
            row["retry_requested"] = False
            with self._lock:
                self._task_status[row["name"]] = row
                self._save_runtime()
        except ValueError as exc:
            self._append(f"⚠️ 无法读取任务状态：{exc}")

    @staticmethod
    def _write_control(process, message):
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def retry_task(self, name):
        with self._lock:
            row = self._task_status.get(name) if isinstance(name, str) else None
            if self._process is None or self._stopping or row is None or row["status"] != "disabled":
                raise ValueError("只能在运行期间重试已停用的任务")
            if row.get("retry_requested"):
                raise ValueError("该任务已安排重试，请等待执行")
            self._write_control(self._process, {"action": "retry", "task": name})
            row["retry_requested"] = True
            self._append(f"已请求单项重试：{row['label']}")
            return self.get_state()

    def _wait_stop(self, process):
        try:
            process.wait(timeout=18)
        except subprocess.TimeoutExpired:
            self._append("⚠️ 停止等待超时，正在强制终止任务。")
            try:
                terminate_task_process(process)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            except OSError:
                pass

    def _close(self):
        self._updates.close()
        self.stop()
        with self._lock:
            self._closed = True
            self._launch_wakeup.set()
            process = self._process
            controlled = self._controlled_process
            self._save_runtime()
        if process is not None and controlled:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                terminate_task_process(process)

    def resume_saved(self, key, timeout=240):
        """Restore a connected emulator or phone with its saved task settings."""
        try:
            self._resume_state = "starting"
            self._scan()
            with self._lock:
                device = next((item for item in self._devices if item["key"] == key), None)
                if device is None:
                    raise ValueError(f"无法恢复设备：{key}，请检查连接后重试")
                if device["status"] != "running":
                    raise ValueError("设备未连接或尚未授权，请启动夜神或检查手机 USB 调试后重试")
                if not load_config(self._root, key):
                    raise ValueError(f"设备没有已保存任务配置：{key}")
                self._choose(device["id"])
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._closed:
                    return
                self._scan()
                with self._lock:
                    device = next((item for item in self._devices if item["key"] == key), None)
                    if device and device["status"] == "running":
                        break
                time.sleep(2)
            else:
                raise RuntimeError(f"模拟器启动超时：{key}")
            if not self._open_game(device):
                return
            self._append("无尽冬日 App 已完成加载，正在启动 GameBot 并检查主城")
            self.start(deepcopy(self._config))
            self._resume_state = "complete"
            self._save_runtime()
        except Exception as exc:
            self._resume_state = "failed"
            self._append(f"⚠️ 定时巡检恢复失败：{type(exc).__name__}: {exc}")
            self._save_runtime()
            with self._lock:
                self._notice_id += 1
                self._notice = {"id": self._notice_id, "message": str(exc)}

    def _open_game(self, device):
        from emulator_runtime import adb_output
        serial = device["serial"]
        if not adb_output("-s", serial, "shell", "pm", "path", "com.gof.china").startswith("package:"):
            raise RuntimeError("设备中未安装无尽冬日国服")
        for attempt in range(3):
            if self._closed:
                return False
            try:
                if attempt:
                    # ADB/Android services can briefly reject commands after a
                    # broken capture. Do not exhaust both retries immediately.
                    self._append("等待 Android 服务稳定后重试打开游戏")
                    if self._launch_wakeup.wait(10 * attempt) or self._closed:
                        return False
                    self._append(f"正在重新打开无尽冬日 App（第 {attempt} 次重试）")
                    adb_output("-s", serial, "shell", "am", "force-stop", "com.gof.china")
                self._append(f"正在打开设备 {device['name']} 中的无尽冬日 App")
                reply = adb_output("-s", serial, "shell", "monkey", "-p", "com.gof.china",
                                   "-c", "android.intent.category.LAUNCHER", "1", timeout=30)
                if "Events injected: 1" not in reply:
                    raise RuntimeError(f"游戏启动失败：{reply}")
                for _ in range(60):
                    if self._launch_wakeup.wait(2):
                        return False
                    activity = adb_output("-s", serial, "shell", "dumpsys", "activity", "activities")
                    if any("com.gof.china/" in line for line in activity.splitlines()
                           if "mResumedActivity:" in line or "topResumedActivity=" in line):
                        break
                else:
                    raise RuntimeError("无尽冬日 App 未进入前台，未启动自动任务")
                return self._wait_game_loaded(serial)
            except StartupGuideBlocked:
                raise
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                self._append(f"⚠️ 无尽冬日启动异常：{exc}")
                if attempt == 2:
                    raise

    def _wait_game_loaded(self, serial, timeout=180):
        import help_bot as bot
        deadline = time.monotonic() + timeout
        progress = 0
        story_skipped = False
        capture_failed = False
        while time.monotonic() < deadline:
            if self._launch_wakeup.wait(2):
                return False
            image = None
            try:
                if bot.game_foreground(serial):
                    image = bot.screenshot(serial)
            except (OSError, subprocess.SubprocessError) as exc:
                if not capture_failed:
                    self._append(f"启动画面读取异常，继续等待有效截图：{exc}")
                capture_failed = True
            else:
                capture_failed = False
            if image is not None:
                if startup_guide_blocked(image):
                    raise StartupGuideBlocked("检测到强制游戏引导，普通任务导航不可用；完成引导后再恢复")
                target = startup_story_skip_target(image) if not story_skipped else None
                if target is not None:
                    bot.adb(serial, "shell", "input", "tap", *map(str, target))
                    story_skipped = True
                    self._append("已确认并跳过启动剧情，继续等待主城加载")
                    continue
                if game_loaded(image):
                    return True
            if time.monotonic() >= progress:
                self._append("正在等待无尽冬日加载完成，尚未启动自动任务")
                progress = time.monotonic() + 30
        raise RuntimeError("等待无尽冬日加载超时，未启动自动任务")


def serve_bridge(api=None, input_stream=None, output_stream=None):
    # stdout is a transport, including while startup/background threads run.
    # Keep incidental Python/library diagnostics out of the JSON pipe.
    output_stream = output_stream or sys.stdout
    with redirect_stdout(sys.stderr):
        return _serve_bridge(api, input_stream, output_stream)


def _serve_bridge(api=None, input_stream=None, output_stream=None):
    """Line-delimited JSON RPC over anonymous pipes owned by the desktop EXE."""
    api = api or DesktopApi()
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    methods = {name: getattr(api, name) for name in
               ("get_state", "refresh_devices", "select_device", "set_device_note", "update_config", "start", "stop", "retry_task",
                "open_screenshot", "check_updates", "download_update", "cancel_update_download", "install_update")}
    try:
        for line in input_stream:
            request_id = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("请求格式不正确")
                request_id = request.get("id")
                method = request.get("method")
                if method == "close":
                    break
                if not isinstance(method, str) or method not in methods:
                    raise ValueError("不支持的桌面操作")
                args = request.get("args", [])
                if not isinstance(args, list):
                    raise ValueError("请求参数格式不正确")
                response = {"id": request_id, "result": methods[method](*args)}
            except Exception as exc:
                response = {"id": request_id, "error": str(exc)}
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()
    finally:
        api._close()


if __name__ == "__main__":
    if "--bridge" in sys.argv:
        configure_stdio()
        bridge_output = sys.stdout
        sys.stdout = sys.stderr
        if "--check-ui" in sys.argv:
            # Desktop integration tests never connect to, or operate, an emulator.
            discover_emulators = lambda: ([], "")
        api = DesktopApi()
        if "--check-ui" not in sys.argv:
            api._updates.cleanup_completed()
            cleanup = threading.Timer(15, api._updates.cleanup_completed)
            cleanup.daemon = True
            cleanup.start()
            api.check_updates()
        if os.environ.get("GAMEBOT_RESUME_KEY") and "--check-ui" not in sys.argv:
            threading.Thread(target=api.resume_saved, args=(os.environ["GAMEBOT_RESUME_KEY"],), daemon=True).start()
        serve_bridge(api, output_stream=bridge_output)
    else:
        raise SystemExit("请通过 GameBot.exe 或 start_windows.cmd 打开桌面界面")
