#!/usr/bin/env python3
"""Reclassify support-heavy generated pigs into real tank and healer kits."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.battle import validate_battle, validate_entry  # noqa: E402

BATTLE = ROOT / "resources" / "battle.json"
PIGS = ROOT / "resources" / "pigs.json"
HISTORY = ROOT / "resources" / "battle_history.json"
TARGET_PER_ROLE = 244
PROTECTED_SUPPORTS = {
    "pighub0087",
    "pighub0210",
    "pighub0323",
    "pighub0444",
}
HEAL_TAG_WEIGHT = {
    "群疗": 5,
    "单疗": 4,
    "持续治疗": 4,
    "复活": 3,
    "净化": 2,
    "护盾": 1,
    "回溯": 1,
}
TANK_TAG_WEIGHT = {
    "铁壁": 5,
    "承伤": 5,
    "守护": 5,
    "分摊": 5,
    "护盾": 3,
    "反击": 2,
    "自回": 2,
    "抗控": 2,
    "回溯": 2,
    "控制": 1,
}
HEAL_EFFECTS = {"heal", "mass_heal", "regen", "cleanse_heal", "triage", "delay_heal"}


def score(entry: dict, weights: dict[str, int]) -> int:
    return sum(weights.get(tag, 0) for tag in entry.get("tags", ()))


def digest(entry: dict) -> str:
    text = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def effects(skill: dict):
    for effect in skill["effects"]:
        yield effect
        if effect["type"] == "pick":
            for option in effect["options"]:
                for nested in option["effects"]:
                    yield nested


def stats_for(pig_id: str, role: str) -> dict[str, int]:
    seed = int(hashlib.sha256(f"{pig_id}:{role}".encode()).hexdigest(), 16)
    if role == "坦克":
        return {
            "hp": 188 + seed % 29,
            "atk": 12 + seed // 29 % 5,
            "def": 27 + seed // 145 % 9,
            "spd": 5 + seed // 1305 % 4,
            "crit": 3 + seed // 5220 % 4,
            "dodge": 3 + seed // 20880 % 5,
        }
    return {
        "hp": 132 + seed % 15,
        "atk": 10 + seed // 15 % 4,
        "def": 11 + seed // 60 % 5,
        "spd": 9 + seed // 300 % 5,
        "crit": 4 + seed // 1500 % 5,
        "dodge": 6 + seed // 7500 % 6,
    }


def healing_basic(entry: dict) -> int:
    """Turn the most thematic existing healing skill into the healer's basic heal."""
    candidates = []
    for index, skill in enumerate(entry["skills"][1:], 1):
        kinds = {effect["type"] for effect in effects(skill)}
        candidates.append((len(kinds & HEAL_EFFECTS), index))
    _, index = max(candidates)
    tags = set(entry.get("tags", ()))
    if "群疗" in tags:
        effect = {"type": "mass_heal", "pct": 8}
        text = "用稳定的群体照护维持全队血线。"
    elif "持续治疗" in tags:
        effect = {"type": "regen", "pct": 4, "turns": 2, "target": "ally_lowest"}
        text = "为伤势最重的队友持续恢复生命。"
    elif "护盾" in tags and "单疗" not in tags:
        effect = {"type": "shield", "pct": 10, "target": "ally_lowest"}
        text = "为伤势最重的队友补上一层保护。"
    else:
        effect = {"type": "heal", "pct": 12, "target": "ally_lowest"}
        text = "立即治疗伤势最重的队友。"
    entry["skills"][index] = {
        "name": entry["skills"][index]["name"],
        "text": text,
        "effects": [effect],
    }
    return index


def convert(pig_id: str, entry: dict, role: str) -> dict:
    entry = json.loads(json.dumps(entry, ensure_ascii=False))
    entry["role"] = role
    entry["style"] = ("前排·" if role == "坦克" else "医护·") + entry["style"]
    entry["style"] = entry["style"][:16]
    entry["stats"] = stats_for(pig_id, role)
    if role == "治疗":
        healing_basic(entry)
        entry["engine"].update(
            name=f"{entry['style']}循环"[:16],
            loop="先用无冷却治疗稳定血线，再按队伍状态衔接原有功能技能。",
            payoff="在维持治疗职责的同时保留原有机制特色。",
            weakness="治疗基础技能偏稳健，需要正确安排高价值技能的冷却。",
        )
    else:
        entry["engine"].update(
            name=f"{entry['style']}循环"[:16],
            loop="依靠原有守护、承伤或控制技能接住火力，并为队友创造输出窗口。",
            payoff="把原本的保护型辅助机制转化为稳定前排能力。",
            weakness="需要及时开启防御技能，冷却空档仍可能被集中突破。",
        )
    probe = dict(entry)
    probe.pop("tags", None)
    entry["tags"] = validate_entry(pig_id, probe)["tags"]
    return entry


def main() -> None:
    payload = json.loads(BATTLE.read_text("utf-8"))
    entries = payload["pigs"]
    counts = Counter(entry["role"] for entry in entries.values())
    candidates = [
        (pig_id, entry)
        for pig_id, entry in entries.items()
        if entry["role"] == "辅助"
        and pig_id.startswith("pighub")
        and pig_id not in PROTECTED_SUPPORTS
    ]

    healer_needed = max(0, TARGET_PER_ROLE - counts["治疗"])
    tank_needed = max(0, TARGET_PER_ROLE - counts["坦克"])
    healers = sorted(
        candidates,
        key=lambda item: (
            -(score(item[1], HEAL_TAG_WEIGHT) - score(item[1], TANK_TAG_WEIGHT) * 0.25),
            -score(item[1], HEAL_TAG_WEIGHT),
            item[0],
        ),
    )[:healer_needed]
    healer_ids = {pig_id for pig_id, _ in healers}
    tanks = sorted(
        (item for item in candidates if item[0] not in healer_ids),
        key=lambda item: (
            -(score(item[1], TANK_TAG_WEIGHT) - score(item[1], HEAL_TAG_WEIGHT) * 0.25),
            -score(item[1], TANK_TAG_WEIGHT),
            item[0],
        ),
    )[:tank_needed]

    if len(healers) != healer_needed or len(tanks) != tank_needed:
        raise RuntimeError("Not enough generated support pigs to rebalance all roles")
    history = json.loads(HISTORY.read_text("utf-8")) if HISTORY.exists() else {}
    for pig_id, entry in [*healers, *tanks]:
        old_digest = digest(entry)
        previous = history.setdefault(pig_id, [])
        if old_digest not in previous:
            previous.append(old_digest)
    for pig_id, entry in healers:
        entries[pig_id] = convert(pig_id, entry, "治疗")
    for pig_id, entry in tanks:
        entries[pig_id] = convert(pig_id, entry, "坦克")

    pig_ids = {pig["id"] for pig in json.loads(PIGS.read_text("utf-8"))}
    validate_battle(payload, pig_ids)
    BATTLE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(dict(Counter(entry["role"] for entry in entries.values())))


if __name__ == "__main__":
    main()
