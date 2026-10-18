"""Device notes stored separately from task configurations."""

import hashlib
import json
from pathlib import Path

from emulator_config import write_json


def note_path(root, key):
    return Path(root) / "device_notes" / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")


def load_note(root, key):
    try:
        data = json.loads(note_path(root, key).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    if (not isinstance(data, dict) or data.get("version") != 1 or data.get("key") != key
            or not isinstance(data.get("note"), str)):
        raise ValueError("设备备注格式不正确")
    return data["note"]


def save_note(root, key, note):
    if not isinstance(note, str) or len(note) > 80 or any(char in note for char in "\r\n\x00"):
        raise ValueError("设备备注限 80 个字符，请输入单行文字")
    note = note.strip()
    write_json(note_path(root, key), {"version": 1, "key": key, "note": note})
    return note
