"""Per-emulator session logs with seven-day retention and console capture."""

from contextlib import contextmanager
from datetime import datetime
import hashlib
from pathlib import Path
import re
import sys
import threading
import time

RETENTION_SECONDS = 7 * 86400
LOG_NAME = re.compile(r"^\d{8}-\d{6}-\d{6}-(?:runtime|exception)\.log$")
EXCEPTION_LINE = re.compile(r"(?:^|\s)(?:\w+\.)*\w*(?:Error|Exception):")


def is_error(line):
    return bool("\u26a0" in line or "已停止：" in line or "任务异常：" in line
                or re.search(r"异常[:：]|失败[:：]", line)
                or "Traceback (most recent call last):" in line or EXCEPTION_LINE.search(line))


def emulator_directory(key):
    name = key.removeprefix("avd:").removeprefix("serial:")
    name = re.sub(r"[^\w-]", "_", name).strip("_")[:80] or "unknown"
    return name + "-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def safe_directory(path, root):
    return (not path.is_symlink() and not getattr(path, "is_junction", lambda: False)()
            and path.resolve().is_relative_to(root.resolve()))


def cleanup_logs(root, now=None):
    root = Path(root) / "logs"
    if not root.exists() or not safe_directory(root, Path(root).parent):
        return
    cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
    for device in root.iterdir():
        if not device.is_dir() or not safe_directory(device, root):
            continue
        for day in device.iterdir():
            if (not day.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name)
                    or not safe_directory(day, root)):
                continue
            for path in day.iterdir():
                if not LOG_NAME.fullmatch(path.name) or not safe_directory(path, root):
                    continue
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                except FileNotFoundError:
                    pass
            _remove_empty(day)
        _remove_empty(device)


def _remove_empty(path):
    try:
        path.rmdir()
    except OSError:
        pass


class SessionLogs:
    def __init__(self, root, key, started=None):
        self.root = Path(root)
        self.key = key
        self.started = started or datetime.now()
        self.session = self.started.strftime("%Y%m%d-%H%M%S-%f")
        self._lock = threading.RLock()
        self._cleanup_at = 0
        self._traceback = False

    def paths(self, at=None):
        day = (at or datetime.now()).strftime("%Y-%m-%d")
        folder = self.root / "logs" / emulator_directory(self.key) / day
        return {kind: folder / f"{self.session}-{kind}.log" for kind in ("runtime", "exception")}

    def append(self, line, error=False):
        if line.startswith("@gamebot-state "):
            return
        with self._lock:
            if time.monotonic() >= self._cleanup_at:
                cleanup_logs(self.root)
                self._cleanup_at = time.monotonic() + 3600
            self._traceback = self._traceback or "Traceback (most recent call last):" in line
            error = error or self._traceback or is_error(line)
            text = line if re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", line) else f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}"
            paths = self.paths()
            if (not safe_directory(self.root / "logs", self.root)
                    or not safe_directory(paths["runtime"].parent, self.root)):
                raise OSError("Log directory must stay inside the project logs directory")
            paths["runtime"].parent.mkdir(parents=True, exist_ok=True)
            if not safe_directory(paths["runtime"].parent, self.root / "logs"):
                raise OSError("Log directory must stay inside the project logs directory")
            for kind, path in paths.items():
                if not safe_directory(path, self.root / "logs"):
                    raise OSError("Log file must stay inside the project logs directory")
                with path.open("a", encoding="utf-8") as target:
                    if kind == "runtime" or error:
                        target.write(text + "\n")
            if EXCEPTION_LINE.search(line):
                self._traceback = False


_console_logs = None


def bind_console_emulator(key):
    if _console_logs is not None:
        _console_logs.key = key


class _ConsoleTee:
    def __init__(self, stream, logs, error=False):
        self.stream, self.logs, self.error = stream, logs, error
        self.pending = ""

    def write(self, text):
        result = self.stream.write(text)
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self.logs.append(line.rstrip("\r"), self.error)
        return result

    def flush(self):
        self.stream.flush()
        if self.pending:
            line, self.pending = self.pending, ""
            self.logs.append(line, self.error)

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextmanager
def capture_console(root, key):
    global _console_logs
    original = sys.stdout, sys.stderr
    previous = _console_logs
    logs = _console_logs = SessionLogs(root, key)
    sys.stdout, sys.stderr = _ConsoleTee(original[0], logs), _ConsoleTee(original[1], logs, True)
    try:
        yield logs
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            sys.stdout, sys.stderr = original
            _console_logs = previous
