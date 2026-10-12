"""Persist can refresh deadlines independently for each emulator."""

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

_identities = {}


def set_device_identity(serial, identity):
    _identities[serial] = identity


def clear_device_identity(serial):
    _identities.pop(serial, None)


def state_path(root, serial):
    name = hashlib.sha256(serial.encode("utf-8")).hexdigest()
    return Path(root) / "configs" / "stamina" / f"{name}.json"


def load_refresh(root, serial):
    key = _identities.get(serial, serial)
    try:
        return _read_refresh(state_path(root, key), key)
    except FileNotFoundError:
        if key == serial:
            return None
    try:
        deadline = _read_refresh(state_path(root, serial), serial)
    except FileNotFoundError:
        return None
    # Migrate the old serial file only after validating it, without altering the deadline.
    _write_refresh(root, key, serial, deadline, 0, deadline)
    return deadline


def _read_refresh(path, key):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") not in (1, 2) or data.get("device") != key:
        raise ValueError("罐头时间文件格式或模拟器标识不正确")
    deadline = data.get("refresh_at")
    if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline) or deadline <= 0):
        raise ValueError("罐头刷新时间无效")
    return deadline


def save_refresh(root, serial, observed_at, seconds):
    deadline = observed_at + max(1, seconds)
    return _write_refresh(root, _identities.get(serial, serial), serial, observed_at, seconds, deadline)


def _write_refresh(root, key, serial, observed_at, seconds, deadline):
    path = state_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            json.dump({"version": 2, "device": key, "serial": serial, "observed_at": observed_at,
                       "remaining_seconds": seconds, "refresh_at": deadline},
                      target, ensure_ascii=False, indent=2, allow_nan=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return deadline
