"""Task names and the structured event schema shared by scheduler and desktop."""

import math

TASK_LABELS = {
    "bear": "自动打熊",
    "help_clicker": "互助连点",
    "task_rewards": "任务奖励",
    "alliance_chests": "联盟宝箱",
    "explore": "探险经验", "train": "自动练兵", "heal": "自动治疗", "donate": "联盟捐献",
    "recruit_advanced": "高级招募", "recruit_epic": "史诗招募", "treasure": "宠物寻宝",
    "warehouse": "仓库补给", "stamina": "体力罐头", "tree": "生命结晶收集",
    "dawn": "晨曦回礼", "intelligence": "灯塔情报", "hunt": "自动打怪", "gather": "资源采集",
    "pet_companion": "心灵伴侣", "pet_transport": "重物搬运",
    "pet_senses": "敏锐感官", "pet_gift": "大地馈赠",
}
TASK_STATES = {"waiting", "running", "retrying", "disabled", "stopped", "failed"}


def task_label(name):
    return TASK_LABELS.get(name, str(name))


def validate_task_event(event):
    if not isinstance(event, dict) or event.get("kind") != "task":
        return None
    name, status = event.get("name"), event.get("status")
    if not isinstance(name, str) or not isinstance(status, str) or name not in TASK_LABELS or status not in TASK_STATES:
        return None
    next_run = event.get("next_run")
    if next_run is not None and (isinstance(next_run, bool) or not isinstance(next_run, (int, float))
                                 or not math.isfinite(next_run) or next_run < 0):
        return None
    reason = event.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 4096:
        return None
    return {"name": name, "label": task_label(name), "status": status,
            "next_run": next_run, "reason": reason}
