"""Python task backend for the native Windows desktop EXE."""

from collections import deque
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
from emulator_runtime import avd_key as emulator_config_key, device_ready, discover_emulators, resolve_emulator, stop_emulator
from platform_support import SUBPROCESS_OPTIONS, configure_stdio, terminate_task_process
from device_lease import DeviceLease
from runtime_control import EVENT_PREFIX
from task_status import validate_task_event
from hunting import MINIMUM_LEVELS
from runtime_logs import SessionLogs, is_error
from app_updates import UpdateManager


ROOT = Path(__file__).resolve().parent
TASKS = (
    ("help", "自动联盟互助"), ("explore", "自动领取探险经验"),
    ("donate", "自动捐献"), ("recruit", "自动免费招募"),
    ("treasure", "宠物寻宝"), ("warehouse", "自动领取仓库补给"),
    ("tree", "自动收集生命结晶"), ("dawn", "自动收集晨曦回礼"),
    ("pet", "宠物技能"), ("task_rewards", "任务奖励"), ("reconnect", "强制下线后自动重连"),
)
UNITS = (("shield", "盾兵"), ("spear", "矛兵"), ("archer", "射手"))
RESOURCES = (("meat", "生肉"), ("wood", "木材"), ("coal", "煤矿"), ("iron", "铁矿"))
RESOURCE_LEVELS = tuple(f"{level}级矿" for level in range(1, 9))
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
    if image.size != bot.EXPECTED_SIZE:
        return False
    template = bot.open_template(ROOT / "assets/emulator-startup-guide-hand.png").convert("L")
    score, _, _ = bot.minimum_difference(image.convert("L"), template,
                                         (530, 1120, 720, 1360),
                                         range(-24, 25, 4), range(-24, 25, 4))
    return score <= 12


def game_loaded(image):
    """Only confirmed game surfaces end the passive cold-start wait."""
    import help_bot as bot
    if image.size != bot.EXPECTED_SIZE:
        raise RuntimeError(f"游戏截图尺寸不正确：{image.size}")
    if startup_guide_blocked(image):
        return False
    nav = bot.open_template(ROOT / "assets/emulator-city-nav.png").convert("L")
    if bot.difference(image.crop(bot.NAV_BOX), nav) <= bot.NAV_LIMIT:
        return True
    if bot.legendary_gift_close_target(image) is not None:
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
    if image.size != bot.EXPECTED_SIZE:
        return None
    for box, name in (((845, 235, 1045, 326), "story-skip"),
                      ((940, 2210, 1030, 2310), "story-dialog-marker")):
        template = bot.open_template(ROOT / f"assets/emulator-{name}.png").convert("L")
        if bot.difference(image.crop(box).convert("L"), template) > 8:
            return None
    return (950, 280)


def default_config():
    options = {name: True for name, _ in (*TASKS, *UNITS, *RESOURCES)}
    options.update(train=True, upgrade=False, heal=True, intelligence=True, hunt=True,
                   beast=True, polar_beast=True, gather=True, bounty=False,
                   help_clicker=False, bear=False, bear_drive=False, bear_follow=True)
    return {"options": options,
            "entries": {"tree_threshold": "1000", "reconnect_minutes": "10", "stamina_threshold": "0"},
            "resource_levels": {name: RESOURCE_LEVELS[0] for name, _ in RESOURCES},
            "hunt_levels": {"beast": "1级", "polar_beast": "2级"}}


def checked_config(config, *, from_disk=False):
    """Limit bridge data to known settings; retain invalid input for Start validation."""
    result = default_config()
    if not isinstance(config, dict):
        raise ValueError("任务配置格式不正确")
    for section in result:
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
                if value in RESOURCE_LEVELS:
                    result[section][name] = value
                elif not from_disk and not (result["options"]["help_clicker"] or result["options"]["bear"]):
                    raise ValueError("资源采集等级必须选择 1级矿 到 8级矿")
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
    if result["options"]["bear"] and result["options"]["help_clicker"]:
        raise ValueError("自动打熊与互助连点不能同时开启")
    if not (result["options"]["help_clicker"] or result["options"]["bear"]) and not any(result["options"][name] for name, _, _ in MONSTERS):
        if not from_disk:
            raise ValueError("至少要勾选一项打怪目标")
        for name, _, _ in MONSTERS:
            result["options"][name] = True
    return result


def build_command(config, serial):
    if not serial:
        raise ValueError("请先连接并选择一台模拟器")
    options, entries = config["options"], config["entries"]
    if options.get("bear"):
        # The GUI is delivered before the sampled rally flows are wired to the
        # runtime. Never silently start daily tasks for this exclusive mode.
        raise ValueError("自动打熊执行流程尚未接入，当前可保存打熊设置，暂不能启动打熊模式")
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
        resources = [name for name, _ in RESOURCES if options[name]]
        if not resources:
            raise ValueError("至少要勾选一项资源")
        command += ["--gather", "--resources", *resources]
        for name in resources:
            try:
                level = RESOURCE_LEVELS.index(config["resource_levels"][name]) + 1
            except ValueError:
                raise ValueError("资源采集等级必须选择 1级矿 到 8级矿") from None
            command += [f"--{name}-level", str(level)]
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
        self._device_status = "正在查找模拟器…"
        self._process = None
        self._stopping = False
        self._closed = False
        self._logs = deque(maxlen=2000)
        self._seq = 0
        self._notice = None
        self._notice_id = 0
        self._reader = None
        self._controlled_process = False
        self._task_status = {}
        self._task_ready = False
        self._resume_state = "idle"
        self._launches = {}
        self._restarts = {}
        self._inventory_version = 0
        self._launch_monitor = None
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
                    "log_reset": reset, "last_log": self._seq, "update": self._updates.snapshot()}

    def check_updates(self):
        self._updates.check()
        return self.get_state()

    def download_update(self):
        self._updates.download()
        return self.get_state()

    def install_update(self):
        with self._lock:
            if self._closed or self._launches or self._restarts or self._resume_state == "starting":
                raise ValueError("请等待模拟器启动或重启完成后再安装更新")
            plan = self._updates.prepare_install()
            self._updating = True
            return {"install_plan": plan}

    def _append(self, line):
        with self._lock:
            self._seq += 1
            error = is_error(line)
            self._logs.append({"id": self._seq, "text": timestamp_line(line), "error": error})
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
                self._device_status = "正在查找模拟器…"
            threading.Thread(target=self._scan, daemon=True).start()
            return self.get_state()

    def _scan(self):
        with self._lock:
            version = self._inventory_version
        try:
            devices, error = discover_emulators()
        except (OSError, subprocess.SubprocessError) as exc:
            devices, error = [], str(exc)
        with self._lock:
            if self._closed:
                return
            if version != self._inventory_version:
                # Ignore an inventory captured before a launch/restart changed the device.
                self._scanning = False
                self._background_scan = False
                return
            previous, previous_key = self._selected, self._active_key
            if error and not devices:
                # A transient SDK/ADB failure must not discard the user's selection.
                devices = deepcopy(self._devices)
                for device in devices:
                    device["status"] = "unknown"
            for key, restarting in self._restarts.items():
                device = next((item for item in devices if item["id"] == key), None)
                if device is None:
                    device = deepcopy(restarting)
                    devices.append(device)
                device["status"] = "restarting"
            for key, (process, started) in list(self._launches.items()):
                device = next((item for item in devices if item["id"] == key), None)
                if device is None:
                    device = next((deepcopy(item) for item in self._devices if item["id"] == key), None)
                    if device is not None:
                        devices.append(device)
                if device is not None and device["status"] == "running":
                    del self._launches[key]
                elif process.poll() is not None or time.monotonic() - started >= 180:
                    code = process.poll()
                    message = (f"模拟器 {key[4:]} 启动失败（退出码 {code}）" if code is not None
                               else f"模拟器 {key[4:]} 仍未就绪，请检查模拟器窗口后点击刷新")
                    self._append(f"⚠️ {message}")
                    self._notice_id += 1
                    self._notice = {"id": self._notice_id, "message": message}
                    if device is not None:
                        device["status"] = "stopped" if code is not None else "unknown"
                    del self._launches[key]
                elif device is not None:
                    device["status"] = "starting"
            self._devices = devices
            self._keys = {device["id"]: device["key"] for device in devices}
            chosen = next((device["id"] for device in devices if device["id"] == previous), None)
            if chosen is None and previous_key:
                chosen = next((device["id"] for device in devices if device["key"] == previous_key), None)
            if self._process is None:
                preferred = next((device["id"] for device in devices if device["status"] == "running"), "")
                self._choose(chosen or preferred or (devices[0]["id"] if devices else ""))
            count = sum(device["status"] == "running" for device in devices)
            self._device_status = error or (f"共 {len(devices)} 台模拟器，{count} 台已启动" if devices else "未发现模拟器，请检查 Android SDK 与 AVD 配置")
            self._scanning = False
            self._background_scan = False

    def launch_emulator(self, device_id):
        with self._lock:
            if self._updating:
                raise ValueError("正在安装更新，不能启动模拟器")
            if self._closed or (self._scanning and not self._background_scan):
                raise ValueError("请等待模拟器刷新完成，或重新打开窗口")
            device = next((item for item in self._devices if item["id"] == device_id), None)
            if device is None or not device["avd"]:
                raise ValueError("请选择列表中的 Android AVD 模拟器")
            if device["status"] != "stopped" or device_id in self._launches:
                raise ValueError("模拟器已启动、正在启动或状态未确认，请刷新后重试")
            self._launch_emulator(device)
            return self.get_state()

    def _launch_emulator(self, device):
        command = [str(resolve_emulator()), "-avd", device["avd"], "-noaudio", "-no-snapshot-load"]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, **SUBPROCESS_OPTIONS)
        self._launches[device["id"]] = (process, time.monotonic())
        self._inventory_version += 1
        device["status"] = "starting"
        self._device_status = f"正在启动 {device['name']}，就绪后会自动更新状态…"
        self._append(f"正在启动模拟器 {device['name']}（禁用音频，不加载快照）")
        if self._launch_monitor is None or not self._launch_monitor.is_alive():
            self._launch_monitor = threading.Thread(target=self._monitor_launches, daemon=True)
            self._launch_monitor.start()

    def restart_emulator(self, device_id):
        with self._lock:
            if self._updating:
                raise ValueError("正在安装更新，不能重启模拟器")
            if self._closed or (self._scanning and not self._background_scan):
                raise ValueError("请等待模拟器刷新完成，或重新打开窗口")
            if self._process is not None:
                raise ValueError("请先停止任务后再重启模拟器")
            device = next((item for item in self._devices if item["id"] == device_id), None)
            if device is None or not device["avd"] or not device["serial"]:
                raise ValueError("请选择列表中已启动的 Android AVD 模拟器")
            if device["status"] != "running" or device_id in self._restarts or device_id in self._launches:
                raise ValueError("模拟器未就绪或正在重启，请刷新后重试")
            self._restarts[device_id] = deepcopy(device)
            self._inventory_version += 1
            device["status"] = "restarting"
            self._device_status = f"正在重启 {device['name']}，关闭后将自动启动…"
            self._append(f"正在关闭模拟器 {device['name']}，关闭后使用原启动命令重启")
            threading.Thread(target=self._restart_emulator, args=(device_id,), daemon=True).start()
            return self.get_state()

    def _restart_emulator(self, device_id):
        with self._lock:
            original = self._restarts[device_id]
        stopped = False
        try:
            with DeviceLease(original["serial"], original["key"]):
                stop_emulator(original["serial"], original["avd"])
                stopped = True
                with self._lock:
                    if self._closed:
                        return
                    device = next(item for item in self._devices if item["id"] == device_id)
                    device["serial"] = ""
                    self._launch_emulator(device)
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
            with self._lock:
                device = next((item for item in self._devices if item["id"] == device_id), None)
                if device is not None:
                    device["status"] = "stopped" if stopped else "unknown"
                message = f"模拟器 {original['name']} 重启失败：{exc}"
                self._device_status = message
                self._append(f"⚠️ {message}")
                self._notice_id += 1
                self._notice = {"id": self._notice_id, "message": message}
        finally:
            with self._lock:
                self._restarts.pop(device_id, None)
                self._inventory_version += 1

    def _monitor_launches(self):
        while not self._launch_wakeup.wait(2):
            with self._lock:
                if self._closed or not self._launches:
                    self._launch_monitor = None
                    return
            self._begin_scan(background=True)

    def _choose(self, serial):
        key = self._keys.get(serial, f"serial:{serial}") if serial else None
        self._selected = serial
        if key == self._active_key:
            return
        if self._active_key is not None:
            self._sessions[self._active_key] = deepcopy(self._config)
        self._active_key = key
        self._disk_logs.key = key or "desktop"
        if key is not None:
            config = self._sessions.get(key)
            from_disk = config is None
            if config is None:
                try:
                    config = load_config(self._root, key)
                except (OSError, ValueError) as exc:
                    config = {}
                    self._append(f"⚠️ 模拟器配置读取失败，已使用默认配置：{exc}")
            self._config = checked_config(config, from_disk=from_disk)
        self._save_runtime()

    def select_device(self, serial, config):
        with self._lock:
            if self._process is not None or (self._scanning and not self._background_scan):
                raise ValueError("运行或刷新期间不能切换模拟器")
            if serial not in {device["id"] for device in self._devices}:
                raise ValueError("请刷新并选择一台模拟器")
            self._config = checked_config(config)
            self._choose(serial)
            return self.get_state()

    def update_config(self, config):
        with self._lock:
            if self._process is not None:
                raise ValueError("请停止任务后修改配置")
            updated = checked_config(config)
            bear_changed = any(updated["options"][name] != self._config["options"][name]
                               for name in ("bear", "bear_drive", "bear_follow"))
            self._config = updated
            # These controls are available before the bear runtime is connected;
            # persist their settings without requiring a successful task launch.
            if bear_changed and self._active_key:
                device = next((item for item in self._devices if item["id"] == self._selected), None)
                if device:
                    try:
                        save_config(self._root, self._active_key, device["serial"], self._config)
                    except (OSError, ValueError) as exc:
                        self._append(f"⚠️ 模拟器配置保存失败：{exc}")
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
                raise ValueError("请等待模拟器刷新完成")
            device = next((item for item in self._devices if item["id"] == self._selected), None)
            if device is None:
                raise ValueError("请先选择一台模拟器")
            if device["status"] == "stopped":
                raise ValueError(f"模拟器 {device['name']} 未启动，请在下拉列表中点击该模拟器旁的启动按钮")
            if device["status"] == "starting":
                raise ValueError(f"模拟器 {device['name']} 正在启动，请等待启动完成后再启动任务")
            if device["status"] != "running" or not device_ready(device["serial"], avd=bool(device["avd"])):
                raise ValueError(f"模拟器 {device['name']} 未连接或尚未就绪，请刷新模拟器列表")
            if device["avd"] and emulator_config_key(device["serial"]) != device["key"]:
                raise ValueError("模拟器连接标识已变化，请刷新模拟器列表后再启动任务")
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
            elif self._config["options"]["hunt"]:
                levels = "、".join(f"{label} {self._config['hunt_levels'][name]}"
                                  for name, label, _ in MONSTERS if self._config["options"][name])
                self._append(f"自动打怪配置：{levels}")
            if self._config["options"]["gather"] and not self._config["options"]["help_clicker"]:
                levels = "、".join(f"{label} {self._config['resource_levels'][name]}"
                                  for name, label in RESOURCES if self._config["options"][name])
                self._append(f"资源采集配置：{levels}")
            try:
                key = self._keys.get(self._selected, f"serial:{self._selected}")
                path = save_config(self._root, key, device["serial"], self._config)
                self._sessions[key] = deepcopy(self._config)
                self._append(f"模拟器配置已保存：{path}")
            except (OSError, ValueError) as exc:
                self._append(f"⚠️ 模拟器配置保存失败：{exc}")
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
                                  "已进入联盟互助，持续快速点击底部全部帮助区域")):
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
        """Restore the requested AVD and saved task settings for Codex maintenance."""
        try:
            self._resume_state = "starting"
            self._scan()
            with self._lock:
                device = next((item for item in self._devices if item["key"] == key), None)
                if device is None or not device["avd"]:
                    raise ValueError(f"无法恢复模拟器：{key}")
                if not load_config(self._root, key):
                    raise ValueError(f"模拟器没有已保存任务配置：{key}")
                self._choose(device["id"])
                if device["status"] == "stopped":
                    self.launch_emulator(device["id"])
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
            raise RuntimeError("模拟器中未安装无尽冬日国服")
        for attempt in range(3):
            if self._closed:
                return False
            try:
                if attempt:
                    self._append(f"正在重新打开无尽冬日 App（第 {attempt} 次重试）")
                    adb_output("-s", serial, "shell", "am", "force-stop", "com.gof.china")
                self._append(f"正在打开模拟器 {device['name']} 中的无尽冬日 App")
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
        while time.monotonic() < deadline:
            if self._launch_wakeup.wait(2):
                return False
            if bot.game_foreground(serial):
                image = bot.screenshot(serial)
                if startup_guide_blocked(image):
                    raise StartupGuideBlocked("检测到强制兽栏引导，普通任务导航不可用；完成引导后再恢复")
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
    """Line-delimited JSON RPC over anonymous pipes owned by the desktop EXE."""
    api = api or DesktopApi()
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    methods = {name: getattr(api, name) for name in
               ("get_state", "refresh_devices", "select_device", "launch_emulator", "restart_emulator", "update_config", "start", "stop", "retry_task",
                "check_updates", "download_update", "install_update")}
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
        serve_bridge(api)
    else:
        raise SystemExit("请通过 GameBot.exe 或 start_windows.cmd 打开桌面界面")
