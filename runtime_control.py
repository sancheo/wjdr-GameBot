"""Cooperative cancellation, task events and a narrow stdin control protocol."""

import json
import threading
import time as system_time

EVENT_PREFIX = "@gamebot-state "


class StopRequested(BaseException):
    """Never turn a deliberate stop into task retries or failure screenshots."""


class RuntimeControl:
    def __init__(self):
        self.stopped = threading.Event()
        self.wakeup = threading.Event()
        self._lock = threading.Lock()
        self._retries = set()
        self._retryable = set()

    def check(self):
        if self.stopped.is_set():
            raise StopRequested()

    def stop(self):
        self.stopped.set()
        self.wakeup.set()

    def set_retryable(self, names):
        with self._lock:
            self._retryable = set(names)

    def request_retry(self, name):
        with self._lock:
            if name not in self._retryable:
                return False
            self._retries.add(name)
        self.wakeup.set()
        return True

    def take_retries(self):
        with self._lock:
            result, self._retries = self._retries, set()
            self.wakeup.clear()
            if self.stopped.is_set():
                self.wakeup.set()
            return result

    def listen(self, source):
        try:
            for line in source:
                if len(line) > 4096:
                    continue
                try:
                    command = json.loads(line)
                    if not isinstance(command, dict):
                        continue
                    if command.get("action") == "stop":
                        self.stop()
                        return
                    if command.get("action") == "retry" and isinstance(command.get("task"), str):
                        self.request_retry(command["task"])
                except ValueError:
                    continue
        finally:
            # Closing/crashing the parent must not leave a task clicking forever.
            self.stop()


_active = None
_output_lock = threading.Lock()


def activate(control):
    global _active
    _active = control


def check_stop():
    if _active is not None:
        _active.check()


def retryable_tasks(names):
    if _active is not None:
        _active.set_retryable(names)


def take_retries():
    return _active.take_retries() if _active is not None else set()


def retry_pending():
    return _active is not None and _active.wakeup.is_set() and not _active.stopped.is_set()


def emit_task_event(**event):
    if _active is None:
        return
    import sys
    with _output_lock:
        sys.stdout.write(EVENT_PREFIX + json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        sys.stdout.flush()


class RuntimeClock:
    monotonic = staticmethod(system_time.monotonic)
    time = staticmethod(system_time.time)

    def sleep(self, seconds):
        if _active is None:
            system_time.sleep(seconds)
            return
        _active.check()
        _active.stopped.wait(seconds)
        _active.check()


clock = RuntimeClock()
