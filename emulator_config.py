"""Store GUI settings separately for each emulator in the program directory."""

import hashlib
import json
import os
from pathlib import Path
import tempfile


def config_path(root, emulator):
    # Emulator names/serials can contain characters forbidden in Windows paths.
    filename = hashlib.sha256(emulator.encode("utf-8")).hexdigest() + ".json"
    return Path(root) / "configs" / filename


def load_config(root, emulator):
    path = config_path(root, emulator)
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except FileNotFoundError:
        return {}
    if (not isinstance(data, dict) or data.get("version") != 1
            or data.get("emulator") != emulator
            or not isinstance(data.get("settings"), dict)):
        raise ValueError("配置文件格式或模拟器标识不正确")
    settings = data["settings"]
    for section in ("options", "entries", "resource_levels", "hunt_levels"):
        if section in settings and not isinstance(settings[section], dict):
            raise ValueError(f"配置文件中的 {section} 格式不正确")
    return settings


def save_config(root, emulator, serial, settings):
    path = config_path(root, emulator)
    settings = dict(settings)
    if isinstance(settings.get("options"), dict):
        # This mode requires an explicit confirmation in each desktop session.
        settings["options"] = {name: value for name, value in settings["options"].items()
                               if name != "help_clicker"}
    data = {"version": 1, "emulator": emulator, "serial": serial, "settings": settings}
    write_json(path, data)
    return path


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            json.dump(data, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
