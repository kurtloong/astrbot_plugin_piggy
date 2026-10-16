from __future__ import annotations

import json
import random
from collections import deque
from copy import deepcopy
from pathlib import Path

from .config import PiggyError
from .personalities import personality_for
from .rarity import rarity_info

STATS = ("hp", "atk", "def", "spd", "crit", "dodge")
STAT_NAMES = {
    "hp": "生命",
    "atk": "攻击",
    "def": "防御",
    "spd": "速度",
    "crit": "暴击",
    "dodge": "闪避",
}
STAT_LIMITS = {
    "hp": (40, 250),
    "atk": (5, 50),
    "def": (0, 40),
    "spd": (1, 30),
    "crit": (0, 50),
    "dodge": (0, 40),
}
ROLES = ("坦克", "输出", "治疗", "辅助")
MECHANISM_TAGS = (
    "直伤",
    "自回",
    "护盾",
    "承伤",
    "反击",
    "连击",
    "爆发",
    "持续伤害",
    "吸血",
    "斩杀",
    "蓄力",
    "多段",
    "标记",
    "属性转伤",
    "随机",
    "单疗",
    "群疗",
    "持续治疗",
    "净化",
    "驱散",
    "抗控",
    "状态转移",
    "控制",
    "减益",
    "团队增益",
    "能量",
    "复活",
    "回溯",
    "分摊",
    "守护",
    "战坦",
    "铁壁",
    "治疗坦",
    "减益坦",
)
TAGS = (*ROLES, *MECHANISM_TAGS)
# Level only scales the four core stats; crit and dodge are percentages.
GROWN = ("hp", "atk", "def", "spd")
GROWTH = 0.08
SKILL_SLOTS = 5
MAX_ROUNDS = 20
CRIT_MULTIPLIER = 1.6
EXECUTE_MULTIPLIER = 1.8
FURY_ROUND = 8
FURY_STEP = 0.15
HP_HISTORY = 6
ENERGY_MAX = 100
BASIC_ENERGY_GAIN = 8
# A normal turn has enough tempo for one basic (1) plus one technique (2).
# This produces a readable opener -> finisher chain without allowing two large
# cooldown skills to be dumped together.
ACTION_BUDGET = 3
BASIC_ACTION_COST = 1
SKILL_ACTION_COST = 2
MAX_SKILLS_PER_TURN = 2
CHAIN_BASIC_SCALE = 0.60
CHAIN_SKILL_SCALE = 0.85
TRIGGER_OUTPUT_SCALE = 0.70
TARGET_KEYS = (
    "self",
    "enemy",
    "ally_lowest",
    "ally_random",
    "ally_all",
    "ally_missing",
    "ally_tank",
)
WHEN = {
    "hurt": "自身生命低于 70% 时",
    "low_hp": "自身生命低于 40% 时",
    "enemy_low": "对手生命低于 35% 时",
    "on_crit": "暴击后",
    "on_dodge": "闪避后",
    "on_ally_dodge": "队友闪避后",
    "on_kill": "击杀后",
    "on_ally_hurt": "队友受伤后",
    "on_shield_break": "护盾破碎后",
    "after_charge": "蓄力完成后",
}
PASSIVE_EFFECTS = {
    "buff",
    "shield",
    "thorns",
    "regen",
    "revive",
    "evade",
    "mitigate",
    "taunt",
    "aura",
    "last_stand",
    "immunity",
    "mark",
    "hot_boost",
    "redirect",
    "crit_amp",
}

_num = (int, float)
_DAMAGE_FIELDS = {
    "power": (_num, 0.1, 5),
    "power_max": (_num, 0.1, 6),
    "hits": (int, 1, 6),
    "pierce": (_num, 0, 100),
    "drain": (_num, 0, 100),
    "execute": (_num, 1, 99),
    "true": (bool,),
    "sure": (bool,),
    "scale": (str, {"atk", "def", "spd", "hp"}),
    "overflow": (bool,),
    "threshold": (_num, 1, 100),
    "cleave": (_num, 1, 100),
    "combo_bonus": (_num, 1, 50),
}
_EFFECT_FIELDS = {
    "damage": dict(_DAMAGE_FIELDS),
    "heal": {
        "pct": (_num, 1, 80),
        "target": (str, set(TARGET_KEYS)),
        "overheal_shield": (bool,),
        "crit_heal": (bool,),
    },
    "shield": {"pct": (_num, 1, 80), "target": (str, set(TARGET_KEYS))},
    "stun": {
        "turns": (int, 1, 3),
        "label": (str, 1, 8),
        "target": (str, {"enemy", "self"}),
    },
    "dot": {
        "pct": (_num, 1, 20),
        "turns": (int, 1, 6),
        "label": (str, 1, 8),
        "stacks": (int, 1, 9),
        "max_stacks": (int, 1, 9),
        "spread": (bool,),
    },
    "buff": {
        "stat": (str, {"atk", "def", "spd", "crit", "dodge"}),
        "pct": (_num, -80, 150),
        "turns": (int, 1, 99),
        "target": (str, set(TARGET_KEYS)),
    },
    "thorns": {"pct": (_num, 1, 100), "turns": (int, 1, 99)},
    "crit_amp": {"multiplier": (_num, 1.61, 3)},
    "regen": {
        "pct": (_num, 1, 20),
        "turns": (int, 1, 99),
        "target": (str, set(TARGET_KEYS)),
        "stacks": (int, 1, 9),
    },
    "evade": {"count": (int, 1, 3), "target": (str, set(TARGET_KEYS))},
    "cleanse": {"target": (str, set(TARGET_KEYS))},
    "dispel": {},
    "copy": {"pct": (_num, 50, 200)},
    "recoil": {"pct": (_num, 1, 60)},
    "revive": {"pct": (_num, 1, 100)},
    # Team / tank
    "taunt": {"turns": (int, 1, 99)},
    "redirect": {"turns": (int, 1, 99)},
    "guard": {"pct": (_num, 10, 80), "turns": (int, 1, 99), "target": (str, set(TARGET_KEYS))},
    "share": {"pct": (_num, 10, 80), "turns": (int, 1, 99)},
    "aura": {
        "stat": (str, {"atk", "def", "spd", "crit", "dodge"}),
        "pct": (_num, -40, 80),
        "heal_pct": (_num, 1, 15),
    },
    "mitigate": {"pct": (_num, 5, 80), "turns": (int, 1, 99)},
    "block": {"count": (int, 1, 4), "pct": (_num, 20, 100)},
    "absorb": {"pct": (_num, 5, 80), "break_power": (_num, 0.1, 3)},
    "reflect": {
        "pct": (_num, 5, 100),
        "turns": (int, 1, 99),
        "stun_turns": (int, 1, 2),
        "dot_pct": (_num, 1, 15),
    },
    "fortify": {"pct": (_num, 5, 50)},
    "last_stand": {"threshold": (_num, 5, 40), "pct": (_num, 20, 80), "turns": (int, 1, 99)},
    "sacrifice": {
        "pct": (_num, 5, 40),
        "shield_pct": (_num, 5, 60),
        "heal_pct": (_num, 5, 60),
        "mitigate_pct": (_num, 10, 60),
        "target": (str, set(TARGET_KEYS)),
    },
    # Time
    "rewind": {"turns": (int, 1, 5)},
    "snapshot": {},
    "delay_heal": {"pct": (_num, 5, 60), "turns": (int, 1, 4), "target": (str, set(TARGET_KEYS))},
    "delay_damage": {"pct": (_num, 5, 40), "turns": (int, 1, 4), "label": (str, 1, 8)},
    "charge": {
        **_DAMAGE_FIELDS,
        "turns": (int, 1, 4),
        "interrupt": (bool,),
    },
    "channel": {
        "turns": (int, 1, 4),
        "power": (_num, 0.1, 2),
        "heal_pct": (_num, 1, 25),
    },
    # Offense
    "mark": {
        "pct": (_num, 5, 50),
        "turns": (int, 1, 99),
        "stacks": (int, 1, 9),
        "max_stacks": (int, 1, 9),
        "burst_power": (_num, 0.5, 4),
    },
    "detonate": {},
    "combo": {"gain": (int, 1, 3), "max": (int, 1, 9)},
    "leech": {"pct": (_num, 5, 80), "target": (str, set(TARGET_KEYS)), "delay": (int, 0, 3)},
    "hot_boost": {
        "pct": (_num, 10, 100),
        "turns": (int, 1, 99),
        "target": (str, set(TARGET_KEYS)),
    },
    "corrupting": {"pct": (_num, 10, 80), "turns": (int, 1, 99)},
    "bleed_burst": {"stacks": (int, 2, 9)},
    # Control / support
    "silence": {"turns": (int, 1, 3)},
    "root": {"turns": (int, 1, 3)},
    "weaken": {"pct": (_num, 5, 60), "turns": (int, 1, 99)},
    "expose": {"pct": (_num, 5, 60), "turns": (int, 1, 99)},
    "purge": {"mode": (str, {"buffs", "shield", "all"})},
    "transfer": {"mode": (str, {"debuff", "buff"})},
    "immunity": {
        "kind": (str, {"stun", "dot", "weaken", "all"}),
        "turns": (int, 1, 99),
    },
    # Heal specialists
    "mass_heal": {"pct": (_num, 5, 50)},
    "triage": {"pct": (_num, 8, 60), "bonus": (_num, 5, 40), "threshold": (_num, 20, 60)},
    "cleanse_heal": {"pct": (_num, 5, 50), "target": (str, set(TARGET_KEYS))},
    "life_link": {
        "pct": (_num, 5, 50),
        "self_pct": (_num, -40, 40),
        "target": (str, set(TARGET_KEYS)),
    },
    "energy": {"gain": (int, 1, 100), "target": (str, set(TARGET_KEYS))},
    # Randomly resolve one option bundle (e.g. cook a random dish with different buffs).
    "pick": {},
}
_REQUIRED = {
    "damage": ("power",),
    "heal": ("pct",),
    "shield": ("pct",),
    "dot": ("pct", "turns"),
    "buff": ("stat", "pct"),
    "thorns": ("pct",),
    "crit_amp": ("multiplier",),
    "regen": ("pct",),
    "recoil": ("pct",),
    "revive": ("pct",),
    "taunt": ("turns",),
    "redirect": ("turns",),
    "guard": ("pct", "turns"),
    "share": ("pct", "turns"),
    "aura": (),
    "mitigate": ("pct",),
    "block": ("count",),
    "absorb": ("pct",),
    "reflect": ("pct",),
    "fortify": ("pct",),
    "last_stand": ("threshold", "pct"),
    "sacrifice": ("pct",),
    "rewind": (),
    "delay_heal": ("pct", "turns"),
    "delay_damage": ("pct", "turns"),
    "charge": ("turns", "power"),
    "channel": ("turns",),
    "mark": ("pct",),
    "combo": (),
    "leech": ("pct",),
    "hot_boost": ("pct",),
    "corrupting": ("pct",),
    "bleed_burst": ("stacks",),
    "silence": ("turns",),
    "root": ("turns",),
    "weaken": ("pct", "turns"),
    "expose": ("pct", "turns"),
    "purge": (),
    "transfer": ("mode",),
    "immunity": ("kind",),
    "mass_heal": ("pct",),
    "triage": ("pct",),
    "cleanse_heal": ("pct",),
    "life_link": ("pct",),
    "energy": ("gain",),
    "pick": ("options",),
}

FALLBACK = {
    "role": "输出",
    "style": "均衡",
    "engine": {
        "name": "稳扎稳打",
        "loop": "用普攻维持输出，穿插连击与自疗，等待全力一拱完成收尾。",
        "payoff": "连击压低血线后以穿甲重击兑现。",
        "weakness": "没有鲜明的团队协同，爆发技能进入冷却后威胁较低。",
        "skills": ["猪突猛进", "连环拱", "全力一拱"],
    },
    "stats": {"hp": 105, "atk": 19, "def": 10, "spd": 10, "crit": 5, "dodge": 5},
    "skills": [
        {
            "name": "猪突猛进",
            "text": "低头就是一拱。",
            "effects": [{"type": "damage", "power": 1.0}],
        },
        {
            "name": "皮糙肉厚",
            "text": "猪圈里摸爬滚打练出来的厚皮。",
            "passive": True,
            "effects": [{"type": "buff", "stat": "def", "pct": 15}],
        },
        {
            "name": "连环拱",
            "text": "左拱一下右拱一下。",
            "cd": 2,
            "effects": [{"type": "damage", "power": 0.6, "hits": 2}],
        },
        {
            "name": "哼哼回血",
            "text": "躺下哼两声，元气回来了。",
            "cd": 4,
            "when": "hurt",
            "effects": [{"type": "heal", "pct": 22}],
        },
        {
            "name": "全力一拱",
            "text": "把全身的膘都压上去。",
            "cd": 4,
            "effects": [{"type": "damage", "power": 2.0, "pierce": 30}],
        },
    ],
}


def _check(value, spec, where: str):
    kind = spec[0]
    if kind is bool:
        ok = type(value) is bool
    elif kind is int:
        ok = type(value) is int and spec[1] <= value <= spec[2]
    elif kind is _num:
        ok = type(value) in (int, float) and spec[1] <= value <= spec[2]
    elif isinstance(spec[1], set):
        ok = isinstance(value, str) and value in spec[1]
    else:
        ok = isinstance(value, str) and spec[1] <= len(value.strip()) <= spec[2]
    if not ok:
        raise PiggyError(f"战斗数据 {where} 取值不合法。")


def _effects(items, where: str, passive: bool) -> list[dict]:
    if not isinstance(items, list) or not 1 <= len(items) <= 6:
        raise PiggyError(f"战斗数据 {where} 需要 1–6 个效果。")
    result = []
    for index, effect in enumerate(items):
        spot = f"{where} 第 {index + 1} 个效果"
        if not isinstance(effect, dict) or effect.get("type") not in _EFFECT_FIELDS:
            raise PiggyError(f"战斗数据 {spot} 类型不合法。")
        kind = effect["type"]
        if passive and kind not in PASSIVE_EFFECTS:
            raise PiggyError(f"战斗数据 {spot}：被动技能不能使用 {kind}。")
        if not passive and kind == "revive":
            raise PiggyError(f"战斗数据 {spot}：revive 只能用于被动技能。")
        if kind == "pick":
            if passive:
                raise PiggyError(f"战斗数据 {spot}：被动技能不能使用 pick。")
            allowed = {"type", "chance", "options"}
            unknown = set(effect) - allowed
            if unknown:
                raise PiggyError(f"战斗数据 {spot} 含未知字段 {', '.join(sorted(unknown))}。")
            if "chance" in effect:
                _check(effect["chance"], (int, 1, 100), spot)
            options = effect.get("options")
            if not isinstance(options, list) or not 2 <= len(options) <= 8:
                raise PiggyError(f"战斗数据 {spot}：pick 需要 2–8 个选项。")
            normalized_opts = []
            for oi, opt in enumerate(options):
                ospot = f"{spot} 选项 {oi + 1}"
                if not isinstance(opt, dict):
                    raise PiggyError(f"战斗数据 {ospot} 必须为对象。")
                bad = set(opt) - {"weight", "text", "effects"}
                if bad:
                    raise PiggyError(f"战斗数据 {ospot} 含未知字段 {', '.join(sorted(bad))}。")
                if "weight" in opt:
                    _check(opt["weight"], (int, 1, 100), f"{ospot} 权重")
                if "text" in opt:
                    _check(opt["text"], (str, 1, 24), f"{ospot} 文案")
                nested = _effects(opt.get("effects"), ospot, False)
                if any(e["type"] == "pick" for e in nested):
                    raise PiggyError(f"战斗数据 {ospot}：pick 不能嵌套 pick。")
                item = {"effects": nested}
                if "weight" in opt:
                    item["weight"] = opt["weight"]
                if "text" in opt:
                    item["text"] = opt["text"].strip()
                normalized_opts.append(item)
            packed = {"type": "pick", "options": normalized_opts}
            if "chance" in effect:
                packed["chance"] = effect["chance"]
            result.append(packed)
            continue
        fields = _EFFECT_FIELDS[kind]
        for key, value in effect.items():
            if key == "type":
                continue
            if key == "chance":
                _check(value, (int, 1, 100), spot)
            elif key in fields:
                _check(value, fields[key], spot)
            else:
                raise PiggyError(f"战斗数据 {spot} 含未知字段 {key}。")
        for key in _REQUIRED.get(kind, ()):
            if key not in effect:
                raise PiggyError(f"战斗数据 {spot} 缺少 {key}。")
        if kind in ("damage", "charge") and effect.get("power_max", effect.get("power", 0)) < effect.get(
            "power", 0
        ):
            raise PiggyError(f"战斗数据 {spot} 的 power_max 不能小于 power。")
        if kind == "aura" and "stat" not in effect and "heal_pct" not in effect:
            raise PiggyError(f"战斗数据 {spot}：aura 需要 stat 或 heal_pct。")
        if kind == "channel" and "power" not in effect and "heal_pct" not in effect:
            raise PiggyError(f"战斗数据 {spot}：channel 需要 power 或 heal_pct。")
        result.append(dict(effect))
    return result


def validate_entry(pig_id: str, entry) -> dict:
    where = pig_id
    if not isinstance(entry, dict):
        raise PiggyError(f"战斗数据 {where} 必须为对象。")
    unknown = set(entry) - {"role", "style", "engine", "tags", "stats", "skills"}
    if unknown:
        raise PiggyError(f"战斗数据 {where} 含未知字段 {', '.join(sorted(unknown))}。")
    role = entry.get("role", "输出")
    if role not in ROLES:
        raise PiggyError(f"战斗数据 {where} 的职业必须是 {'/'.join(ROLES)}。")
    _check(entry.get("style"), (str, 1, 8), f"{where} 流派")
    engine = entry.get("engine")
    if engine is None:
        engine = {
            "name": entry["style"],
            "loop": "围绕现有技能按战况循环施放。",
            "payoff": "在关键技能就绪时完成主要作用。",
            "weakness": "缺少显式核心引擎说明，需由管理员补充。",
            "skills": [skill.get("name", "") for skill in entry.get("skills", [])[:3]],
        }
    if not isinstance(engine, dict) or set(engine) != {
        "name",
        "loop",
        "payoff",
        "weakness",
        "skills",
    }:
        raise PiggyError(
            f"战斗数据 {where} 的核心引擎必须包含 name/loop/payoff/weakness/skills。"
        )
    for key, limits in {
        "name": (2, 12),
        "loop": (8, 100),
        "payoff": (4, 80),
        "weakness": (4, 80),
    }.items():
        _check(engine.get(key), (str, *limits), f"{where} 核心引擎 {key}")
    if (
        not isinstance(engine["skills"], list)
        or not 3 <= len(engine["skills"]) <= SKILL_SLOTS
        or any(not isinstance(name, str) for name in engine["skills"])
        or len(set(engine["skills"])) != len(engine["skills"])
    ):
        raise PiggyError(f"战斗数据 {where} 的核心引擎必须关联 3–5 个不重复技能。")
    tags = entry.get("tags")
    if tags is not None:
        if (
            not isinstance(tags, list)
            or not 2 <= len(tags) <= 4
            or any(not isinstance(tag, str) or tag not in TAGS for tag in tags)
            or len(set(tags)) != len(tags)
            or tags[0] != role
        ):
            raise PiggyError(
                f"战斗数据 {where} 标签必须是 2–4 个不重复的受控标签，且首项为职业。"
            )
    stats = entry.get("stats")
    if not isinstance(stats, dict) or set(stats) != set(STATS):
        raise PiggyError(f"战斗数据 {where} 的属性必须包含 {', '.join(STATS)}。")
    for key in STATS:
        _check(stats[key], (int, *STAT_LIMITS[key]), f"{where} 属性 {key}")
    skills = entry.get("skills")
    if not isinstance(skills, list) or len(skills) != SKILL_SLOTS:
        raise PiggyError(f"战斗数据 {where} 必须恰好有 {SKILL_SLOTS} 个技能。")
    names = set()
    normalized = []
    for index, skill in enumerate(skills):
        spot = f"{where} 技能 {index + 1}"
        if not isinstance(skill, dict):
            raise PiggyError(f"战斗数据 {spot} 必须为对象。")
        unknown = set(skill) - {
            "name",
            "text",
            "cd",
            "when",
            "passive",
            "fail",
            "effects",
            "cost",
            "gain",
        }
        if unknown:
            raise PiggyError(f"战斗数据 {spot} 含未知字段 {', '.join(sorted(unknown))}。")
        _check(skill.get("name"), (str, 1, 16), f"{spot} 名称")
        _check(skill.get("text"), (str, 1, 80), f"{spot} 文案")
        if skill["name"] in names:
            raise PiggyError(f"战斗数据 {where} 技能名重复：{skill['name']}")
        names.add(skill["name"])
        passive = skill.get("passive", False)
        _check(passive, (bool,), spot)
        _check(skill.get("cd", 0), (int, 0, 9), f"{spot} 冷却")
        when = skill.get("when")
        if when is not None and when not in WHEN:
            raise PiggyError(f"战斗数据 {spot} 的触发条件不合法。")
        if passive and (skill.get("cd") or when or "fail" in skill or "cost" in skill):
            raise PiggyError(f"战斗数据 {spot}：被动技能不能设置冷却、条件、失败或消耗。")
        item = {
            "name": skill["name"].strip(),
            "text": skill["text"].strip(),
            "cd": skill.get("cd", 0),
            "when": when,
            "passive": passive,
            "effects": _effects(skill.get("effects"), spot, passive),
        }
        if "cost" in skill:
            _check(skill["cost"], (int, 1, 100), f"{spot} 能量消耗")
            item["cost"] = skill["cost"]
        if "gain" in skill:
            _check(skill["gain"], (int, 1, 100), f"{spot} 能量回复")
            item["gain"] = skill["gain"]
        fail = skill.get("fail")
        if fail is not None:
            if not isinstance(fail, dict) or set(fail) - {"chance", "text", "effects"}:
                raise PiggyError(f"战斗数据 {spot} 的失败设置不合法。")
            _check(fail.get("chance"), (int, 1, 99), f"{spot} 失败概率")
            _check(fail.get("text"), (str, 1, 80), f"{spot} 失败文案")
            item["fail"] = {
                "chance": fail["chance"],
                "text": fail["text"].strip(),
                "effects": _effects(fail["effects"], f"{spot} 失败", False)
                if fail.get("effects")
                else [],
            }
        normalized.append(item)
    skill_names = {skill["name"] for skill in normalized}
    if any(name not in skill_names for name in engine["skills"]):
        raise PiggyError(f"战斗数据 {where} 的核心引擎关联了不存在的技能。")
    first = normalized[0]
    if (
        first["passive"]
        or first["cd"]
        or first["when"]
        or "fail" in first
        or "cost" in first
        or not any(e["type"] == "damage" for e in first["effects"])
    ):
        raise PiggyError(f"战斗数据 {where} 的第 1 个技能必须是无冷却、无条件的攻击技能。")
    return {
        "role": role,
        "style": entry["style"].strip(),
        "engine": {
            **{key: engine[key].strip() for key in ("name", "loop", "payoff", "weakness")},
            "skills": list(engine["skills"]),
        },
        "tags": list(tags) if tags is not None else _tags_from_skills(role, normalized),
        "stats": dict(stats),
        "skills": normalized,
    }


def _tags_from_skills(role: str, skills: list[dict]) -> list[str]:
    """Compatibility classifier for administrator entries created before tags existed.

    Bundled entries are curated separately.  This path deliberately uses only
    mechanics that are really present, so an old custom pig stays searchable
    without acquiring a fictional capability.
    """
    def unpack(effects):
        for effect in effects:
            yield effect
            if effect["type"] == "pick":
                for option in effect["options"]:
                    yield from unpack(option["effects"])

    effects = [effect for skill in skills for effect in unpack(skill["effects"])]
    kinds = {effect["type"] for effect in effects}
    tags = [role]
    groups = (
        ("self_heal", "自回"),
        ("shield", "护盾"),
        ("tank", "承伤"),
        ("counter", "反击"),
        ("combo", "连击"),
        ("burst", "爆发"),
        ("dot", "持续伤害"),
        ("leech", "吸血"),
        ("execute", "斩杀"),
        ("charge", "蓄力"),
        ("multi_hit", "多段"),
        ("mark", "标记"),
        ("scale", "属性转伤"),
        ("pick", "随机"),
        ("mass_heal", "群疗"),
        ("heal", "单疗"),
        ("regen", "持续治疗"),
        ("cleanse", "净化"),
        ("dispel", "驱散"),
        ("immunity", "抗控"),
        ("transfer", "状态转移"),
        ("control", "控制"),
        ("debuff", "减益"),
        ("team_buff", "团队增益"),
        ("energy", "能量"),
        ("revive", "复活"),
        ("rewind", "回溯"),
        ("share", "分摊"),
        ("guard", "守护"),
    )
    present = {
        "self_heal": bool(kinds & {"drain", "leech"}),
        "shield": "shield" in kinds,
        "tank": bool(kinds & {"taunt", "redirect", "guard", "share", "mitigate", "block", "absorb", "fortify", "last_stand", "sacrifice"}),
        "counter": bool(kinds & {"thorns", "reflect", "recoil"}),
        "combo": "combo" in kinds,
        "burst": bool(kinds & {"detonate", "delay_damage", "charge"}),
        "dot": bool(kinds & {"dot", "corrupting", "bleed_burst"}),
        "leech": "leech" in kinds,
        "execute": any(effect.get("execute") for effect in effects),
        "charge": "charge" in kinds,
        "multi_hit": any(effect.get("hits", 1) > 1 for effect in effects),
        "mark": "mark" in kinds,
        "scale": any(effect.get("scale", "atk") != "atk" for effect in effects),
        "pick": "pick" in kinds,
        "mass_heal": "mass_heal" in kinds,
        "heal": bool(kinds & {"heal", "triage", "cleanse_heal", "delay_heal"}),
        "regen": "regen" in kinds,
        "cleanse": bool(kinds & {"cleanse", "cleanse_heal", "purge"}),
        "dispel": bool(kinds & {"dispel", "purge"}),
        "immunity": "immunity" in kinds,
        "transfer": "transfer" in kinds,
        "control": bool(kinds & {"stun", "silence", "root"}),
        "debuff": bool(kinds & {"weaken", "expose", "corrupting", "silence"}),
        "team_buff": bool(kinds & {"aura", "hot_boost"}),
        "energy": "energy" in kinds,
        "revive": "revive" in kinds,
        "rewind": bool(kinds & {"rewind", "snapshot"}),
        "share": "share" in kinds,
        "guard": bool(kinds & {"guard", "redirect"}),
    }
    if role == "坦克":
        tank_tags = []
        for condition, label in (
            (present["rewind"], "回溯"),
            (present["leech"], "吸血"),
            (present["share"], "分摊"),
            (present["guard"], "守护"),
            (present["team_buff"], "团队增益"),
            (present["cleanse"] or present["dispel"], "净化"),
            (present["debuff"] or present["mark"], "减益坦"),
            ((present["heal"] or present["regen"]) and (present["share"] or present["guard"]), "治疗坦"),
            (present["self_heal"] or present["heal"] or present["regen"], "自回"),
            (present["counter"], "反击"),
            (present["burst"] or present["multi_hit"] or present["execute"], "战坦"),
            (present["shield"], "护盾"),
            (present["tank"], "铁壁"),
            (present["control"], "控制"),
        ):
            if condition and label not in tank_tags:
                tank_tags.append(label)
            if len(tank_tags) == 3:
                break
        return [role, *(tank_tags or ["承伤"])]
    for key, label in groups:
        if present[key] and label not in tags:
            tags.append(label)
        if len(tags) == 4:
            break
    if len(tags) == 1:
        tags.append("直伤")
    return tags


def validate_battle(data, pig_ids) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("pigs"), dict):
        raise PiggyError("战斗数据必须是包含 pigs 对象的 JSON。")
    unknown = set(data["pigs"]) - set(pig_ids)
    if unknown:
        raise PiggyError(f"战斗数据含有猪库中不存在的小猪：{', '.join(sorted(unknown)[:5])}")
    return {pig_id: validate_entry(pig_id, entry) for pig_id, entry in data["pigs"].items()}


def load_battle(path: Path, pig_ids) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise PiggyError("战斗数据 battle.json 无法读取，原有数据已保留。") from exc
    return validate_battle(data, pig_ids)


def entry_for(raw: str | None) -> dict:
    """Pigs without curated data still fight with a balanced template."""
    return json.loads(raw) if raw else validate_entry("fallback", FALLBACK)


def level_for(count: int, cap: int) -> int:
    return max(1, min(count, cap))


def _scale_effect(effect: dict, factor: float) -> dict:
    effect = deepcopy(effect)
    potency = {
        "damage": ("power", "power_max"), "heal": ("pct",), "shield": ("pct",),
        "dot": ("pct",), "thorns": ("pct",), "regen": ("pct",),
        "guard": ("pct",), "mitigate": ("pct",), "reflect": ("pct", "dot_pct"),
        "delay_heal": ("pct",), "delay_damage": ("pct",),
        "charge": ("power", "power_max"), "channel": ("power", "heal_pct"),
        "mark": ("pct", "burst_power"), "leech": ("pct",),
        "mass_heal": ("pct",), "triage": ("pct", "bonus"),
        "cleanse_heal": ("pct",), "life_link": ("pct",),
        "weaken": ("pct",), "expose": ("pct",), "hot_boost": ("pct",),
    }
    for key in potency.get(effect.get("type"), ()):
        if key in effect:
            effect[key] = round(effect[key] * factor, 2)
    if effect.get("type") == "pick":
        for option in effect.get("options", []):
            option["effects"] = [_scale_effect(item, factor) for item in option["effects"]]
    return effect


def _scale_skills(skills: list[dict], factor: float) -> list[dict]:
    result = deepcopy(skills)
    if factor == 1:
        return result
    for skill in result:
        skill["effects"] = [_scale_effect(effect, factor) for effect in skill["effects"]]
        if "fail" in skill:
            skill["fail"]["effects"] = [
                _scale_effect(effect, factor) for effect in skill["fail"]["effects"]
            ]
    return result


def fighter(
    pig: dict,
    entry: dict,
    level: int,
    label: str = "",
    *,
    rarity: str | None = None,
    personality: str | None = None,
) -> dict:
    rarity, rare = rarity_info(pig["id"], rarity)
    scale = rare["base"] * (1 + rare["growth"] * (level - 1))
    stats = {
        key: round(value * scale) if key in GROWN else value
        for key, value in entry["stats"].items()
    }
    unlocked = _scale_skills(entry["skills"][: min(level, SKILL_SLOTS)], rare["skill"])
    nature = personality_for(personality)
    if nature:
        unlocked.append(deepcopy(nature["skill"]))
    return {
        "pig_id": pig["id"],
        "name": pig["name"],
        "asset": pig.get("asset", ""),
        "label": label or pig["name"],
        "role": entry.get("role", "输出"),
        "style": entry["style"],
        "engine": entry.get("engine", FALLBACK["engine"]),
        "tags": entry.get("tags", [entry.get("role", "输出")]),
        "rarity": rarity,
        "rarity_rank": rare["rank"],
        "personality": personality,
        "personality_name": nature["name"] if nature else "",
        "level": level,
        "stats": stats,
        "skills": unlocked,
        "locked": entry["skills"][min(level, SKILL_SLOTS) :],
    }


def _target_label(target: str | None) -> str:
    return {
        None: "自身",
        "self": "自身",
        "enemy": "敌方",
        "ally_lowest": "生命最低队友",
        "ally_random": "随机队友",
        "ally_all": "全体队友",
        "ally_missing": "缺血最多队友",
        "ally_tank": "坦克队友",
    }.get(target or "self", "自身")


def _duration_text(effect: dict, default: int, passive: bool) -> str:
    if "turns" in effect:
        return f"，持续 {effect['turns']} 回合"
    return "，常驻" if passive else f"，持续 {default} 回合"


def _chance_text(effect: dict, text: str) -> str:
    chance = effect.get("chance", 100)
    return f"{chance}% 概率触发：{text}" if chance < 100 else text


def describe_effect(effect: dict, *, passive: bool = False) -> str:
    kind = effect["type"]
    if kind == "damage":
        power = f"{effect['power']:g}"
        if "power_max" in effect:
            power += f"~{effect['power_max']:g}"
        base = {"atk": "攻击", "def": "防御", "spd": "速度", "hp": "生命"}[
            effect.get("scale", "atk")
        ]
        text = f"对敌方造成 {power} 倍{base}伤害"
        if effect.get("hits", 1) > 1:
            text = f"对敌方攻击 {effect['hits']} 段，每段造成 {power} 倍{base}伤害"
        extras = []
        if effect.get("true"):
            extras.append("真实伤害")
        elif effect.get("pierce"):
            extras.append(f"无视 {effect['pierce']:g}% 防御")
        if effect.get("sure"):
            extras.append("必中")
        if effect.get("drain"):
            extras.append(f"按伤害的 {effect['drain']:g}% 回复自身生命")
        if effect.get("execute"):
            extras.append(f"敌方生命不高于 {effect['execute']:g}% 时伤害提高")
        if effect.get("threshold"):
            extras.append(f"按敌方已损生命额外提高伤害（系数 {effect['threshold']:g}%）")
        if effect.get("cleave"):
            extras.append(f"向其他敌人溅射 {effect['cleave']:g}%")
        if effect.get("overflow"):
            extras.append("击杀后的溢出伤害转向其他敌人")
        if effect.get("combo_bonus"):
            extras.append(f"消耗全部连击，每层增伤 {effect['combo_bonus']:g}%")
        if extras:
            text += "（" + "；".join(extras) + "）"
    elif kind == "heal":
        who = _target_label(effect.get("target"))
        text = f"为{who}回复 {effect['pct']:g}% 最大生命"
        if effect.get("overheal_shield"):
            text += "（溢出治疗转为护盾）"
        if effect.get("crit_heal"):
            text += "（可暴击）"
    elif kind == "shield":
        who = _target_label(effect.get("target"))
        text = f"为{who}施加相当于其 {effect['pct']:g}% 最大生命的护盾"
    elif kind == "stun":
        who = "自身" if effect.get("target") == "self" else "敌方"
        text = f"使{who}陷入{effect.get('label', '眩晕')}，持续 {effect.get('turns', 1)} 回合"
    elif kind == "dot":
        text = (
            f"使敌方获得{effect.get('label', '持续伤害')}：每回合损失 {effect['pct']:g}% 最大生命，"
            f"持续 {effect['turns']} 回合"
        )
        if effect.get("stacks", 1) > 1:
            text += f"，一次叠加 {effect['stacks']} 层"
        if effect.get("max_stacks"):
            text += f"，最多 {effect['max_stacks']} 层"
        if effect.get("spread"):
            text += "，并以半数效果扩散给一名其他敌人"
    elif kind == "buff":
        who = _target_label(effect.get("target"))
        sign = "+" if effect["pct"] >= 0 else ""
        unit = "%" if effect["stat"] in ("atk", "def", "spd") else " 点"
        text = (
            f"使{who}{STAT_NAMES[effect['stat']]} {sign}{effect['pct']:g}{unit}"
            + _duration_text(effect, 2, passive)
        )
    elif kind == "thorns":
        text = f"自身反弹 {effect['pct']:g}% 受到的伤害" + _duration_text(effect, 2, passive)
    elif kind == "crit_amp":
        text = f"使自身暴击伤害倍率提高到 {effect['multiplier']:g} 倍"
    elif kind == "regen":
        who = _target_label(effect.get("target"))
        text = f"使{who}每回合回复 {effect['pct']:g}% 最大生命" + _duration_text(effect, 3, passive)
        if effect.get("stacks", 1) > 1:
            text += f"，效果按 {effect['stacks']} 层结算"
    elif kind == "evade":
        text = (
            f"使{_target_label(effect.get('target'))}完全躲开接下来 {effect.get('count', 1)} 次攻击"
        )
    elif kind == "cleanse":
        text = f"清除{_target_label(effect.get('target'))}的所有可净化负面状态"
    elif kind == "dispel":
        text = "驱散敌方的增益与护盾"
    elif kind == "copy":
        text = "复制敌方上一次施放的技能"
        if effect.get("pct", 100) != 100:
            text += f"（效果 {effect['pct']:g}%）"
    elif kind == "recoil":
        text = f"自身损失 {effect['pct']:g}% 最大生命"
    elif kind == "revive":
        text = f"首次倒下时以 {effect['pct']:g}% 最大生命复活"
    elif kind == "taunt":
        text = f"自身嘲讽敌方，持续 {effect['turns']} 回合"
    elif kind == "redirect":
        text = f"自身强制吸引单体攻击，持续 {effect['turns']} 回合"
    elif kind == "guard":
        text = (
            f"守护{_target_label(effect.get('target') or 'ally_lowest')}，由自身承受其 "
            f"{effect['pct']:g}% 伤害，持续 {effect['turns']} 回合"
        )
    elif kind == "share":
        text = f"链接全体队友并分摊 {effect['pct']:g}% 伤害，持续 {effect['turns']} 回合"
    elif kind == "aura":
        parts = []
        if "stat" in effect:
            parts.append(f"全体队友{STAT_NAMES[effect['stat']]} +{effect['pct']:g}%")
        if "heal_pct" in effect:
            parts.append(f"全体队友每回合回复 {effect['heal_pct']:g}% 最大生命")
        text = "展开常驻光环：" + "；".join(parts)
    elif kind == "mitigate":
        text = f"自身受到的伤害降低 {effect['pct']:g}%" + _duration_text(effect, 2, passive)
    elif kind == "block":
        text = f"自身格挡接下来 {effect['count']} 次伤害（每次减免 {effect.get('pct', 100):g}%）"
    elif kind == "absorb":
        text = f"自身获得相当于 {effect['pct']:g}% 最大生命的吸收盾"
        if effect.get("break_power"):
            text += f"，护盾破碎时对敌方造成 {effect['break_power']:g} 倍攻击伤害"
    elif kind == "reflect":
        text = f"自身反射 {effect['pct']:g}% 受到的伤害" + _duration_text(effect, 2, passive)
        if effect.get("stun_turns"):
            text += f"，反射成功时眩晕攻击者 {effect['stun_turns']} 回合"
        if effect.get("dot_pct"):
            text += f"，并施加每回合 {effect['dot_pct']:g}% 最大生命的反噬（2 回合）"
    elif kind == "fortify":
        text = f"将自身现有护盾与防御按 {effect['pct']:g}% 固化为额外护盾"
    elif kind == "last_stand":
        text = (
            f"自身生命首次降至 {effect['threshold']:g}% 以下时，受到的伤害降低 "
            f"{effect['pct']:g}%" + _duration_text(effect, 1, False)
        )
    elif kind == "sacrifice":
        parts = [f"自身损失 {effect['pct']:g}% 最大生命"]
        target = _target_label(effect.get("target") or "ally_lowest")
        if effect.get("shield_pct"):
            parts.append(f"为{target}施加 {effect['shield_pct']:g}% 最大生命的护盾")
        if effect.get("heal_pct"):
            parts.append(f"为{target}回复 {effect['heal_pct']:g}% 最大生命")
        if effect.get("mitigate_pct"):
            parts.append(f"使自身获得 {effect['mitigate_pct']:g}% 减伤（2 回合）")
        text = "，".join(parts)
    elif kind == "rewind":
        text = f"将自身生命回溯至 {effect.get('turns', 1)} 回合前（若有快照则优先回到快照）"
    elif kind == "snapshot":
        text = "记录自身当前生命快照"
    elif kind == "delay_heal":
        text = (
            f"{effect['turns']} 回合后为{_target_label(effect.get('target'))}回复 "
            f"{effect['pct']:g}% 最大生命"
        )
    elif kind == "delay_damage":
        text = f"{effect['turns']} 回合后使敌方损失 {effect['pct']:g}% 最大生命"
        if effect.get("label"):
            text += f"（{effect['label']}）"
    elif kind == "charge":
        power = f"{effect['power']:g}"
        if "power_max" in effect:
            power += f"~{effect['power_max']:g}"
        base = {"atk": "攻击", "def": "防御", "spd": "速度", "hp": "生命"}[
            effect.get("scale", "atk")
        ]
        text = f"蓄力 {effect['turns']} 回合后对敌方造成 {power} 倍{base}伤害"
        if effect.get("interrupt"):
            text += "（可被打断）"
    elif kind == "channel":
        text = f"引导 {effect['turns']} 回合"
        if effect.get("power"):
            text += f"，每回合对敌方造成 {effect['power']:g} 倍攻击伤害"
        if effect.get("heal_pct"):
            text += f"，每回合为自身回复 {effect['heal_pct']:g}% 最大生命"
    elif kind == "mark":
        text = f"标记敌方，使其受到的伤害 +{effect['pct']:g}%，持续 {effect.get('turns', 3)} 回合"
        if effect.get("stacks", 1) > 1:
            text += f"，一次叠加 {effect['stacks']} 层"
        if effect.get("max_stacks"):
            text += f"，最多 {effect['max_stacks']} 层"
        if effect.get("burst_power") and effect.get("max_stacks"):
            text += f"，满层时引爆 {effect['burst_power']:g} 倍攻击伤害"
    elif kind == "detonate":
        text = "立即结算并清除敌方的全部持续伤害与延迟伤害"
    elif kind == "combo":
        text = f"自身获得 {effect.get('gain', 1)} 点连击（最多 {effect.get('max', 5)} 点）"
    elif kind == "leech":
        text = (
            f"将本技能已造成伤害的 {effect['pct']:g}% 转化为"
            f"{_target_label(effect.get('target'))}的治疗"
        )
        if effect.get("delay"):
            text += f"，延迟 {effect['delay']} 回合生效"
    elif kind == "hot_boost":
        text = (
            f"使{_target_label(effect.get('target'))}受到的持续治疗增强 {effect['pct']:g}%"
            + _duration_text(effect, 3, passive)
        )
    elif kind == "corrupting":
        text = f"使敌方受到的治疗降低 {effect['pct']:g}%，持续 {effect['turns']} 回合"
    elif kind == "bleed_burst":
        text = f"若敌方任一持续伤害达到 {effect['stacks']} 层，立即结算并清除该效果"
    elif kind == "silence":
        text = f"沉默敌方，持续 {effect['turns']} 回合"
    elif kind == "root":
        text = f"禁锢敌方，持续 {effect['turns']} 回合（期间无法闪避）"
    elif kind == "weaken":
        text = f"使敌方造成的伤害降低 {effect['pct']:g}%，持续 {effect['turns']} 回合"
    elif kind == "expose":
        text = f"使敌方受到的伤害提高 {effect['pct']:g}%，持续 {effect['turns']} 回合"
    elif kind == "purge":
        mode = {"buffs": "增益", "shield": "护盾", "all": "增益与护盾"}.get(
            effect.get("mode", "all"), "增益与护盾"
        )
        text = f"驱散敌方的{mode}"
    elif kind == "transfer":
        text = (
            "将自身一个负面状态转移给敌方"
            if effect["mode"] == "debuff"
            else "偷取敌方一个增益（没有增益时改为偷取一半护盾）"
        )
    elif kind == "immunity":
        # Keep the player-facing promise aligned with the checks made by
        # ``_Unit.immune_to``.  ``all`` is the union of the three supported
        # immunity families; it does not cover silence, root or expose.
        names = {
            "stun": "眩晕",
            "dot": "持续伤害",
            "weaken": "虚弱",
            "all": "眩晕、持续伤害与虚弱",
        }
        text = f"使自身免疫{names.get(effect['kind'], effect['kind'])}" + _duration_text(
            effect, 2, passive
        )
    elif kind == "mass_heal":
        text = f"为全体队友各回复 {effect['pct']:g}% 最大生命"
    elif kind == "triage":
        text = f"为生命最低队友回复 {effect['pct']:g}% 最大生命"
        if effect.get("bonus"):
            text += (
                f"；其生命不高于 {effect.get('threshold', 40):g}% 时，额外回复 "
                f"{effect['bonus']:g}% 最大生命"
            )
    elif kind == "cleanse_heal":
        text = (
            f"清除{_target_label(effect.get('target') or 'ally_lowest')}的所有可净化负面状态，"
            f"并回复 {effect['pct']:g}% 最大生命"
        )
    elif kind == "life_link":
        target = _target_label(effect.get("target") or "ally_lowest")
        parts = [f"为{target}回复 {effect['pct']:g}% 最大生命"]
        if effect.get("self_pct", 0) > 0:
            parts.append(f"自身回复 {effect['self_pct']:g}% 最大生命")
        elif effect.get("self_pct", 0) < 0:
            parts.append(f"自身损失 {abs(effect['self_pct']):g}% 最大生命")
        text = "生命链接：" + "，".join(parts)
    elif kind == "energy":
        text = f"使{_target_label(effect.get('target'))}获得 {effect['gain']} 点能量"
    elif kind == "pick":
        options = []
        for index, option in enumerate(effect.get("options", []), 1):
            label = option.get("text") or f"选项 {index}"
            weight = f"，权重 {option['weight']}" if "weight" in option else ""
            effects = "；".join(describe_effect(item) for item in option.get("effects", []))
            options.append(f"{label}{weight}（{effects}）")
        text = "随机选择一项：" + " / ".join(options)
    else:
        text = kind
    return _chance_text(effect, text)


def describe_skill(skill: dict) -> str:
    passive = skill["passive"]
    parts = ["被动（开战生效）" if passive else "主动"]
    if skill["when"]:
        parts.append(f"触发：{WHEN[skill['when']]}")
    parts.append(
        "效果：" + "；然后".join(describe_effect(e, passive=passive) for e in skill["effects"])
    )
    if not passive:
        parts.append(f"冷却：{skill['cd']} 回合" if skill["cd"] else "冷却：无")
    energy = []
    if skill.get("cost"):
        energy.append(f"消耗 {skill['cost']}")
    if skill.get("gain"):
        energy.append(f"获得 {skill['gain']}")
    if energy:
        parts.append("能量：" + "，".join(energy))
    if "fail" in skill:
        failure = f"失败：{skill['fail']['chance']}% 概率，{skill['fail']['text']}"
        if skill["fail"]["effects"]:
            failure += (
                "（"
                + "；然后".join(describe_effect(effect) for effect in skill["fail"]["effects"])
                + "）"
            )
        parts.append(failure)
    return " · ".join(parts)


class _Unit:
    def __init__(self, data: dict):
        self.data = data
        self.label = data["label"]
        self.base = data["stats"]
        self.max_hp = data["stats"]["hp"]
        self.hp = float(data.get("start_hp", self.max_hp))
        self.buffs = []
        self.dots = []
        self.regens = []
        self.thorns = []
        self.reflects = []
        self.shield = 0.0
        self.stun = 0
        self.stun_label = ""
        self.evade = 0
        self.revive = None
        self.cooldowns = {}
        self.last = None
        self.actives = [s for s in data["skills"] if not s["passive"]]
        self.mitigates = []
        self.block_count = 0
        self.block_pct = 100
        self.absorb_break = 0.0
        self.taunt = 0
        self.redirect = 0
        self.guards = []  # {ward, pct, turns} on caster
        self.share = None  # {pct, turns, members}
        self.auras = []
        self.hp_history = deque([self.hp], maxlen=HP_HISTORY)
        self.snapshot = None
        self.delay_heals = []
        self.delay_damages = []
        self.charge = None
        self.channel = None
        self.marks = []
        self.combo = 0
        self.combo_max = 5
        self.crit_multiplier = CRIT_MULTIPLIER
        self.silence = 0
        self.root = 0
        self.weaken = []
        self.expose = []
        self.immunity = {}  # kind -> turns
        self.hot_boost = []
        self.corrupting = []
        self.last_stand = None
        self.last_stand_used = False
        self.lock_hp = 0
        self.pending_leech = []
        self.energy = 0
        self.uses_energy = any("cost" in s or "gain" in s for s in data["skills"])
        self.used_skills = set()
        self.flags = {"crit": False, "dodged": False, "killed": False}
        self.side = data.get("side", 0)

    def stat(self, key: str) -> float:
        bonus = sum(b["pct"] for b in self.buffs if b["stat"] == key)
        if key in ("crit", "dodge"):
            value = max(0.0, min(75.0, self.base[key] + bonus))
            if key == "dodge" and self.root:
                return 0.0
            return value
        return max(1.0 if key != "def" else 0.0, self.base[key] * (1 + max(-80, bonus) / 100))

    @property
    def ratio(self) -> float:
        return max(0.0, self.hp) / self.max_hp

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def role(self) -> str:
        return self.data.get("role", "输出")

    def record_hp(self):
        self.hp_history.append(self.hp)

    def immune_to(self, kind: str) -> bool:
        if self.immunity.get("all", 0) > 0:
            return True
        return self.immunity.get(kind, 0) > 0


class _Battle:
    def __init__(self, a: dict, b: dict, seed: int):
        self.rng = random.Random(seed)
        self.units = (_Unit(a), _Unit(b))
        self.log = []
        self.round = 0
        self._copy_scale = 1.0
        self._cast_output_scale = 1.0
        self._leech_pool = 0.0
        self.void_note = ""
        self._in_trigger = False
        self._turn_triggered = set()

    def say(self, text: str):
        self.log.append(f"R{self.round} {text}" if self.round else text)

    def allies_of(self, unit: _Unit) -> list[_Unit]:
        """1v1: only self. Raid overrides to return party members."""
        return [unit]

    def foes_of(self, unit: _Unit) -> list[_Unit]:
        return [self.other(unit)]

    def resolve_targets(self, unit: _Unit, enemy: _Unit, spec: str | None) -> list[_Unit]:
        spec = spec or "self"
        allies = [u for u in self.allies_of(unit) if u.alive]
        if not allies:
            allies = [unit] if unit.alive else []
        if spec == "self":
            return [unit] if unit.alive else []
        if spec == "enemy":
            return [enemy] if enemy and enemy.alive else []
        if spec == "ally_all":
            return allies
        if spec == "ally_random":
            return [self.rng.choice(allies)] if allies else []
        if spec == "ally_lowest":
            return [min(allies, key=lambda u: u.ratio)] if allies else []
        if spec == "ally_missing":
            return [max(allies, key=lambda u: u.max_hp - u.hp)] if allies else []
        if spec == "ally_tank":
            tanks = [u for u in allies if u.data.get("role") == "坦克"]
            return [min(tanks or allies, key=lambda u: u.ratio)] if allies else []
        return [unit] if unit.alive else []

    def run(self) -> dict:
        for unit in self.units:
            for skill in unit.data["skills"]:
                if skill["passive"]:
                    for effect in skill["effects"]:
                        self.apply_passive(unit, effect)
                    self.say(f"{unit.label} 被动「{skill['name']}」生效")
        winner = None
        for self.round in range(1, MAX_ROUNDS + 1):
            if self.round == FURY_ROUND + 1:
                self.say("围观的猪开始起哄，之后每回合伤害都会更高！")
            a, b = self.units
            speeds = (a.stat("spd"), b.stat("spd"))
            if speeds[0] == speeds[1]:
                first = self.rng.randrange(2)
            else:
                first = 0 if speeds[0] > speeds[1] else 1
            for index in (first, 1 - first):
                self.turn(self.units[index], self.units[1 - index])
                winner = self.decided()
                if winner is not None:
                    break
            if winner is not None:
                break
        else:
            self.round = 0
            a, b = self.units
            if a.ratio == b.ratio:
                winner = self.rng.randrange(2)
            else:
                winner = 0 if a.ratio > b.ratio else 1
            self.say(
                f"{MAX_ROUNDS} 回合未分胜负，按剩余生命判定："
                f"{a.label} {a.ratio:.0%} / {b.label} {b.ratio:.0%}"
            )
        return {
            "winner": winner,
            "rounds": self.round or MAX_ROUNDS,
            "log": self.log,
            "hp": [round(max(0, u.hp)) for u in self.units],
            "max_hp": [u.max_hp for u in self.units],
        }

    def decided(self):
        a, b = self.units
        if not a.alive and not b.alive:
            return 0 if a.hp >= b.hp else 1
        if not a.alive:
            return 1
        if not b.alive:
            return 0
        return None

    def apply_passive(self, unit: _Unit, effect: dict):
        kind = effect["type"]
        if kind == "buff":
            spec = effect.get("target", "self")
            targets = (
                [self.other(unit)]
                if spec == "enemy"
                else self.resolve_targets(unit, self.other(unit), spec)
            )
            for target in targets:
                if target.alive:
                    target.buffs.append(
                        {
                            "stat": effect["stat"],
                            "pct": effect["pct"],
                            "turns": effect.get("turns", 99),
                        }
                    )
        elif kind == "shield":
            for target in self.resolve_targets(unit, self.other(unit), effect.get("target", "self")):
                target.shield += target.max_hp * effect["pct"] / 100
        elif kind == "thorns":
            unit.thorns.append({"pct": effect["pct"], "turns": effect.get("turns", 99)})
        elif kind == "crit_amp":
            unit.crit_multiplier = max(unit.crit_multiplier, effect["multiplier"])
        elif kind == "regen":
            for target in self.resolve_targets(unit, self.other(unit), effect.get("target", "self")):
                target.regens.append(
                    {"pct": effect["pct"], "turns": effect.get("turns", 99), "source": unit}
                )
        elif kind == "evade":
            for target in self.resolve_targets(unit, self.other(unit), effect.get("target", "self")):
                target.evade += effect.get("count", 1)
        elif kind == "revive":
            unit.revive = effect["pct"]
        elif kind == "mitigate":
            unit.mitigates.append({"pct": effect["pct"], "turns": effect.get("turns", 99)})
        elif kind == "taunt":
            unit.taunt = max(unit.taunt, effect["turns"])
        elif kind == "redirect":
            unit.redirect = max(unit.redirect, effect["turns"])
        elif kind == "aura":
            unit.auras.append({**effect, "source": unit})
        elif kind == "last_stand":
            unit.last_stand = dict(effect)
        elif kind == "immunity":
            unit.immunity[effect["kind"]] = max(
                unit.immunity.get(effect["kind"], 0), effect.get("turns", 99)
            )
        elif kind == "mark":
            enemy = self.other(unit)
            self._apply_mark(enemy, effect)
        elif kind == "hot_boost":
            unit.hot_boost.append({"pct": effect["pct"], "turns": effect.get("turns", 99)})

    def fury(self) -> float:
        """Long fights escalate so sustain-heavy pigs cannot stall forever."""
        return 1 + FURY_STEP * max(0, self.round - FURY_ROUND)

    def other(self, unit: _Unit) -> _Unit:
        return self.units[1] if unit is self.units[0] else self.units[0]

    def _mitigation(self, unit: _Unit) -> float:
        return min(80.0, sum(m["pct"] for m in unit.mitigates))

    def _expose_bonus(self, unit: _Unit) -> float:
        return sum(e["pct"] for e in unit.expose)

    def _weaken_factor(self, unit: _Unit) -> float:
        cut = min(80.0, sum(w["pct"] for w in unit.weaken))
        return 1 - cut / 100

    def _mark_bonus(self, unit: _Unit) -> float:
        return sum(m["pct"] for m in unit.marks)

    def hurt(self, unit: _Unit, amount: float, source: str = "", attacker: _Unit | None = None) -> float:
        if amount <= 0 or not unit.alive:
            return 0.0
        # Guard transfer: damage taken by ward goes partially to guardian.
        # Do not re-enter on transferred hits — mutual/cyclic guards would recurse forever.
        if source != "guard":
            for ally in self.allies_of(unit):
                for guard in list(ally.guards):
                    if guard["ward"] is unit and ally.alive and ally is not unit:
                        share = amount * guard["pct"] / 100
                        amount -= share
                        self.hurt(ally, share, source="guard", attacker=attacker)
        # Share link
        if unit.share and unit.share["turns"] > 0:
            members = [m for m in unit.share["members"] if m.alive]
            if len(members) > 1:
                portion = amount * unit.share["pct"] / 100
                amount -= portion
                each = portion / len(members)
                for member in members:
                    if member is not unit:
                        self._raw_hurt(member, each, attacker=attacker)
            else:
                # 1v1 / alone: partial mitigation
                amount *= 1 - unit.share["pct"] / 200
        return self._raw_hurt(unit, amount, attacker=attacker)

    def _raw_hurt(self, unit: _Unit, amount: float, attacker: _Unit | None = None) -> float:
        if amount <= 0 or not unit.alive:
            return 0.0
        amount *= 1 + self._expose_bonus(unit) / 100
        amount *= 1 - self._mitigation(unit) / 100
        if unit.block_count > 0:
            unit.block_count -= 1
            amount *= 1 - unit.block_pct / 100
        before_shield = unit.shield
        absorbed = min(unit.shield, amount)
        unit.shield -= absorbed
        amount -= absorbed
        shield_broke = before_shield > 0 and unit.shield <= 0 and absorbed > 0
        if shield_broke:
            if unit.absorb_break and attacker and attacker.alive:
                break_raw = unit.stat("atk") * unit.absorb_break * self.fury()
                self._raw_hurt(attacker, max(1.0, break_raw))
                unit.absorb_break = 0.0
        unit.hp -= amount
        if unit.lock_hp and unit.hp < unit.max_hp * unit.lock_hp / 100:
            unit.hp = unit.max_hp * unit.lock_hp / 100
        if (
            unit.last_stand
            and not unit.last_stand_used
            and unit.hp > 0
            and unit.ratio * 100 <= unit.last_stand["threshold"]
        ):
            unit.last_stand_used = True
            unit.mitigates.append(
                {"pct": unit.last_stand["pct"], "turns": unit.last_stand.get("turns", 1) + 1}
            )
            unit.lock_hp = 1
            unit.lock_hp_turns = 1
            self.say(f"{unit.label} 进入绝境坚守！")
        self.check_revive(unit)
        dealt = amount + absorbed
        event_enemy = self._event_enemy(unit, attacker)
        if event_enemy is not None:
            if shield_broke:
                self._try_triggers(unit, event_enemy, "on_shield_break")
            if dealt > 0:
                for ally in self.allies_of(unit):
                    if ally is not unit:
                        self._try_triggers(ally, event_enemy, "on_ally_hurt")
        return dealt

    def check_revive(self, unit: _Unit):
        if unit.hp <= 0 and unit.snapshot is not None and unit.revive is None:
            # Prefer explicit revive; snapshot alone does not revive.
            pass
        if unit.hp <= 0 and unit.revive:
            unit.hp = unit.max_hp * unit.revive / 100
            unit.revive = None
            unit.dots.clear()
            self.say(f"{unit.label} 倒下后又站了起来！恢复 {round(unit.hp)} 生命")

    def heal(self, unit: _Unit, amount: float, source: _Unit | None = None) -> int:
        if amount <= 0 or not unit.alive:
            return 0
        boost = sum(h["pct"] for h in unit.hot_boost)
        amount *= 1 + boost / 100
        corrupt = sum(c["pct"] for c in unit.corrupting)
        if corrupt:
            converted = amount * corrupt / 100
            amount -= converted
            self._raw_hurt(unit, converted)
        before = unit.hp
        unit.hp = min(unit.max_hp, unit.hp + amount)
        return round(unit.hp - before)

    def heal_with_options(
        self, unit: _Unit, amount: float, effect: dict, source: _Unit | None = None
    ) -> tuple[int, int]:
        if effect.get("crit_heal") and self.rng.random() * 100 < unit.stat("crit"):
            amount *= CRIT_MULTIPLIER
        before = unit.hp
        healed = self.heal(unit, amount, source=source)
        over = 0
        if effect.get("overheal_shield"):
            overflow = before + amount - unit.max_hp
            if overflow > 0:
                unit.shield += overflow
                over = round(overflow)
        return healed, over

    def turn(self, unit: _Unit, enemy: _Unit):
        unit.flags = {k: False for k in unit.flags}
        unit.used_skills.clear()
        self._turn_triggered.clear()
        unit.record_hp()
        self._tick_delays(unit, enemy)
        self._tick_auras(unit)
        self._tick_charge_channel(unit, enemy)
        for dot in list(unit.dots):
            if unit.immune_to("dot"):
                break
            stacks = max(1, dot.get("stacks", 1))
            damage = self.hurt(unit, self.dot_amount(unit, dot) * stacks)
            self.say(f"{unit.label} 受到{dot['label']}影响，损失 {round(damage)} 生命")
            dot["turns"] -= 1
            if dot["turns"] <= 0 and dot in unit.dots:
                unit.dots.remove(dot)
            if not unit.alive:
                return
        for regen in list(unit.regens):
            stacks = max(1, regen.get("stacks", 1))
            healed = self.heal(
                unit,
                unit.max_hp * regen["pct"] / 100 * stacks,
                source=regen.get("source"),
            )
            regen["turns"] -= 1
            if regen["turns"] <= 0:
                unit.regens.remove(regen)
            if healed:
                self.say(f"{unit.label} 回复 {healed} 生命")
        for leech in list(unit.pending_leech):
            leech["turns"] -= 1
            if leech["turns"] <= 0:
                healed = self.heal(
                    leech["target"], leech["amount"], source=leech.get("source", unit)
                )
                if healed:
                    self.say(f"{leech['target'].label} 吸收 {healed} 生命")
                unit.pending_leech.remove(leech)
        for name in list(unit.cooldowns):
            unit.cooldowns[name] = max(0, unit.cooldowns[name] - 1)
        if unit.stun:
            if unit.charge and unit.charge.get("interrupt"):
                unit.charge = None
                self.say(f"{unit.label} 的蓄力被打断了")
            unit.stun -= 1
            self.say(f"{unit.label} 处于{unit.stun_label}状态，这回合动不了")
        elif unit.channel:
            pass  # channel ticks in _tick_charge_channel; skip normal act
        else:
            self.act(unit, enemy)
        self._decay_status(unit)

    def _tick_delays(self, unit: _Unit, enemy: _Unit):
        for item in list(unit.delay_heals):
            item["turns"] -= 1
            if item["turns"] <= 0:
                healed, _ = self.heal_with_options(
                    item["target"],
                    item["target"].max_hp * item["pct"] / 100,
                    item.get("opts", {}),
                    source=item.get("source", unit),
                )
                if healed:
                    self.say(f"{item['target'].label} 延迟回复 {healed} 生命")
                unit.delay_heals.remove(item)
        for item in list(unit.delay_damages):
            item["turns"] -= 1
            if item["turns"] <= 0:
                # Keep accounting ownership separate from combat semantics.
                # Supplying the planter as ``attacker`` here would change
                # shield-break retaliation and reactive target selection.
                previous_source = getattr(self, "_stat_source", None)
                self._stat_source = item.get("source")
                try:
                    lost = self.hurt(unit, unit.max_hp * item["pct"] / 100)
                finally:
                    self._stat_source = previous_source
                self.say(f"{unit.label} 遭受{item['label']}，损失 {round(lost)} 生命")
                # Lethal delayed damage may clear the unit's statuses inside
                # ``hurt``.  Only remove the timer when it still exists.
                if item in unit.delay_damages:
                    unit.delay_damages.remove(item)

    def _tick_auras(self, unit: _Unit):
        for aura in unit.auras:
            for ally in self.allies_of(unit):
                if not ally.alive:
                    continue
                if "stat" in aura:
                    ally.buffs.append(
                        {"stat": aura["stat"], "pct": aura["pct"], "turns": 1}
                    )
                if "heal_pct" in aura:
                    healed = self.heal(
                        ally,
                        ally.max_hp * aura["heal_pct"] / 100,
                        source=aura.get("source", unit),
                    )
                    if healed:
                        self.say(f"{ally.label} 受到光环治疗 {healed}")

    def _tick_charge_channel(self, unit: _Unit, enemy: _Unit):
        if unit.charge:
            unit.charge["turns"] -= 1
            if unit.charge["turns"] <= 0:
                effect = {k: v for k, v in unit.charge.items() if k not in ("turns", "interrupt")}
                effect["type"] = "damage"
                note = self.damage(unit, enemy, effect)
                self.say(f"{unit.label} 蓄力爆发，{note}")
                unit.charge = None
                self._try_triggers(unit, enemy, "after_charge")
        if unit.channel:
            unit.channel["turns"] -= 1
            if unit.channel.get("power"):
                note = self.damage(
                    unit, enemy, {"type": "damage", "power": unit.channel["power"]}
                )
                self.say(f"{unit.label} 引导中，{note}")
            if unit.channel.get("heal_pct"):
                for ally in self.resolve_targets(unit, enemy, "ally_lowest"):
                    healed = self.heal(
                        ally,
                        ally.max_hp * unit.channel["heal_pct"] / 100,
                        source=unit.channel.get("source", unit),
                    )
                    if healed:
                        self.say(f"{unit.label} 引导治疗 {ally.label} {healed}")
            if unit.channel["turns"] <= 0:
                unit.channel = None

    def _decay_status(self, unit: _Unit):
        for group in (
            unit.buffs,
            unit.thorns,
            unit.reflects,
            unit.mitigates,
            unit.weaken,
            unit.expose,
            unit.marks,
            unit.hot_boost,
            unit.corrupting,
        ):
            for item in list(group):
                item["turns"] -= 1
                if item["turns"] <= 0:
                    group.remove(item)
        for guard in list(unit.guards):
            guard["turns"] -= 1
            if guard["turns"] <= 0:
                unit.guards.remove(guard)
        if unit.share:
            unit.share["turns"] -= 1
            if unit.share["turns"] <= 0:
                for member in unit.share["members"]:
                    if member.share is unit.share:
                        member.share = None
        for kind in list(unit.immunity):
            unit.immunity[kind] -= 1
            if unit.immunity[kind] <= 0:
                del unit.immunity[kind]
        if unit.taunt:
            unit.taunt -= 1
        if unit.redirect:
            unit.redirect -= 1
        if unit.silence:
            unit.silence -= 1
        if unit.root:
            unit.root -= 1
        if getattr(unit, "lock_hp_turns", 0):
            unit.lock_hp_turns -= 1
            if unit.lock_hp_turns <= 0:
                unit.lock_hp = 0

    def action_budget(self, unit: _Unit) -> int:
        return ACTION_BUDGET

    def max_skills_per_turn(self, unit: _Unit) -> int:
        return MAX_SKILLS_PER_TURN

    def action_target(self, unit: _Unit, enemy: _Unit) -> _Unit | None:
        if enemy is not None and enemy.alive:
            return enemy
        return next((foe for foe in self.foes_of(unit) if foe.alive), None)

    def action_cost(self, unit: _Unit, skill: dict) -> int:
        return BASIC_ACTION_COST if self._is_basic_action(unit, skill) else SKILL_ACTION_COST

    def _is_healing_basic(self, unit: _Unit, skill: dict) -> bool:
        if unit.role != "治疗" or skill is unit.actives[0] or skill.get("cd") or skill.get("when"):
            return False
        kinds = {effect["type"] for effect in skill["effects"]}
        return bool(kinds) and kinds <= {"heal", "mass_heal", "shield", "regen"}

    def _is_basic_action(self, unit: _Unit, skill: dict) -> bool:
        return skill is unit.actives[0] or self._is_healing_basic(unit, skill)

    def _preview_targets(self, unit: _Unit, enemy: _Unit, spec: str | None) -> list[_Unit]:
        """Resolve utility targets without consuming RNG during planning."""
        if spec == "ally_random":
            return [ally for ally in self.allies_of(unit) if ally.alive]
        return self.resolve_targets(unit, enemy, spec)

    def _has_removable_benefit(self, enemy: _Unit | None, mode: str = "all") -> bool:
        buffs = bool(
            enemy
            and (
                enemy.evade
                or enemy.thorns
                or enemy.reflects
                or enemy.mitigates
                or enemy.hot_boost
                or any(buff.get("pct", 0) > 0 for buff in enemy.buffs)
            )
        )
        shield = bool(enemy and enemy.shield)
        return bool(
            enemy
            and ((mode in {"all", "buffs"} and buffs) or (mode in {"all", "shield"} and shield))
        )

    def _effect_useful(self, unit: _Unit, enemy: _Unit, effect: dict) -> bool:
        """Whether an effect can materially change the current battle state."""
        kind = effect["type"]
        spec = effect.get("target", "self")
        targets = self._preview_targets(unit, enemy, spec)
        if kind in {"damage", "charge", "channel", "recoil", "combo"}:
            return enemy is not None and enemy.alive
        if kind == "dot":
            if enemy is None or not enemy.alive:
                return False
            label = effect.get("label", "持续伤害")
            current = next((dot for dot in enemy.dots if dot.get("label") == label), None)
            if current is None:
                return True
            return current.get("stacks", 1) < effect.get("max_stacks", 9) or current.get(
                "turns", 0
            ) < effect.get("turns", 1)
        if kind == "heal":
            return any(target.hp < target.max_hp * 0.97 for target in targets)
        if kind == "mass_heal":
            allies = [ally for ally in self.allies_of(unit) if ally.alive]
            missing = sum(max(0.0, ally.max_hp - ally.hp) for ally in allies)
            total = sum(ally.max_hp for ally in allies) or 1
            return missing / total >= 0.06 or any(ally.ratio < 0.75 for ally in allies)
        if kind == "triage":
            allies = [ally for ally in self.allies_of(unit) if ally.alive]
            return bool(allies) and min(ally.ratio for ally in allies) < 0.92
        if kind in {"cleanse", "cleanse_heal"}:
            return any(self._cleanse_urgency(target) > 0 for target in targets) or (
                kind == "cleanse_heal" and any(target.ratio < 0.92 for target in targets)
            )
        if kind == "shield":
            desired = effect["pct"] / 100
            return any(
                target.shield / target.max_hp < min(0.18, desired * 0.7)
                and (target.ratio < 0.95 or target.role == "坦克")
                for target in targets
            )
        if kind == "regen":
            return any(target.ratio < 0.9 and not target.regens for target in targets)
        if kind == "evade":
            return any(target.evade < effect.get("count", 1) for target in targets)
        if kind == "buff":
            stat, pct = effect["stat"], effect["pct"]
            if pct < 0:
                return enemy is not None and not any(
                    buff["stat"] == stat and buff["pct"] <= pct and buff["turns"] > 1
                    for buff in enemy.buffs
                )
            return any(
                not any(
                    buff["stat"] == stat and buff["pct"] >= pct and buff["turns"] > 1
                    for buff in target.buffs
                )
                for target in targets
            )
        if kind in {"dispel", "purge"}:
            return self._has_removable_benefit(enemy, effect.get("mode", "all"))
        if kind == "stun":
            target = unit if effect.get("target") == "self" else enemy
            return bool(
                target
                and not target.immune_to("stun")
                and target.stun < effect.get("turns", 1)
            )
        if kind == "silence":
            return bool(enemy and enemy.silence < effect["turns"])
        if kind == "root":
            return bool(enemy and enemy.root < effect["turns"])
        if kind == "weaken":
            return bool(enemy and not enemy.weaken)
        if kind == "expose":
            return bool(enemy and not enemy.expose)
        if kind == "corrupting":
            return bool(enemy and not enemy.corrupting)
        if kind == "mark":
            current = sum(mark.get("stacks", 1) for mark in enemy.marks) if enemy else 0
            return bool(enemy and current < effect.get("max_stacks", 9))
        if kind in {"taunt", "redirect"}:
            return getattr(unit, kind) < effect["turns"]
        if kind == "guard":
            wards = self._preview_targets(unit, enemy, effect.get("target", "ally_lowest"))
            return any(
                ward is not unit
                and not any(guard["ward"] is ward and guard["turns"] > 1 for guard in unit.guards)
                for ward in wards
            )
        if kind == "share":
            return any(ally.share is None for ally in self.allies_of(unit) if ally.alive)
        if kind == "mitigate":
            current = sum(item["pct"] for item in unit.mitigates)
            return current < effect["pct"] * 0.7
        if kind == "block":
            return unit.block_count < effect["count"]
        if kind == "absorb":
            return unit.shield < unit.max_hp * effect["pct"] / 100 * 0.7
        if kind in {"thorns", "reflect"}:
            active = unit.thorns if kind == "thorns" else unit.reflects
            return not any(item["turns"] > 1 for item in active)
        if kind == "immunity":
            return unit.immunity.get(effect["kind"], 0) <= 1
        if kind == "aura":
            return not any(
                aura.get("stat") == effect.get("stat")
                and aura.get("heal_pct") == effect.get("heal_pct")
                for aura in unit.auras
            )
        if kind == "last_stand":
            return not unit.last_stand and not unit.last_stand_used
        if kind == "snapshot":
            return unit.snapshot is None
        if kind == "rewind":
            recoverable = max(max(unit.hp_history, default=unit.hp), unit.snapshot or 0) - unit.hp
            return recoverable > unit.max_hp * 0.05
        if kind == "delay_heal":
            return any(target.ratio < 0.85 for target in targets) and not unit.delay_heals
        if kind == "delay_damage":
            label = effect.get("label", "延时炸弹")
            return bool(
                enemy
                and not any(item.get("label", "延时炸弹") == label for item in enemy.delay_damages)
            )
        if kind == "detonate":
            return bool(enemy and (enemy.dots or enemy.delay_damages))
        if kind == "bleed_burst":
            return bool(
                enemy
                and any(dot.get("stacks", 1) >= effect["stacks"] for dot in enemy.dots)
            )
        if kind == "leech":
            return any(item["type"] == "damage" for item in getattr(self, "_planning_effects", ()))
        if kind == "hot_boost":
            return any(target.ratio < 0.9 and not target.hot_boost for target in targets)
        if kind == "transfer":
            if effect["mode"] == "debuff":
                return bool(unit.dots or any(buff.get("pct", 0) < 0 for buff in unit.buffs))
            return self._has_removable_benefit(enemy)
        if kind == "energy":
            return any(target.uses_energy and target.energy < ENERGY_MAX for target in targets)
        if kind == "crit_amp":
            return unit.crit_multiplier < effect["multiplier"]
        if kind == "life_link":
            return any(target.ratio < 0.92 for target in targets)
        if kind == "sacrifice":
            wards = self._preview_targets(unit, enemy, effect.get("target", "ally_lowest"))
            return unit.ratio > 0.35 and any(target.ratio < 0.75 for target in wards)
        if kind == "fortify":
            return unit.shield > 0 or unit.stat("def") > 0
        if kind == "copy":
            return bool(enemy and enemy.last)
        if kind == "pick":
            return any(
                self._effect_useful(unit, enemy, nested)
                for option in effect.get("options", [])
                for nested in option.get("effects", [])
            )
        return True

    def _skill_useful(self, unit: _Unit, enemy: _Unit, skill: dict) -> bool:
        self._planning_effects = skill["effects"]
        try:
            return any(self._effect_useful(unit, enemy, effect) for effect in skill["effects"])
        finally:
            self._planning_effects = ()

    def _healing_basic_needed(self, unit: _Unit, enemy: _Unit, skill: dict) -> bool:
        allies = [ally for ally in self.allies_of(unit) if ally.alive]
        for effect in skill["effects"]:
            kind = effect["type"]
            if kind == "mass_heal":
                targets = allies
            else:
                targets = self.resolve_targets(unit, enemy, effect.get("target", "self"))
            targets = targets or allies
            if kind in {"heal", "mass_heal"} and any(target.hp < target.max_hp for target in targets):
                return True
            if kind == "shield" and any(target.shield < target.max_hp * 0.15 for target in targets):
                return True
            if kind == "regen" and any(
                target.hp < target.max_hp and not target.regens for target in targets
            ):
                return True
        return False

    def _can_choose(self, unit: _Unit, enemy: _Unit, skill: dict, energy: int | None = None) -> bool:
        if skill["name"] in unit.used_skills:
            return False
        if unit.cooldowns.get(skill["name"], 0):
            return False
        if skill.get("cost") and (unit.energy if energy is None else energy) < skill["cost"]:
            return False
        when = skill["when"]
        if when == "hurt" and unit.ratio >= 0.7:
            return False
        if when == "low_hp" and unit.ratio >= 0.4:
            return False
        if when == "enemy_low" and (enemy is None or enemy.ratio >= 0.35):
            return False
        if when in (
            "on_crit",
            "on_dodge",
            "on_ally_dodge",
            "on_kill",
            "on_ally_hurt",
            "on_shield_break",
            "after_charge",
        ):
            return False
        if any(e["type"] == "copy" for e in skill["effects"]) and (enemy is None or not enemy.last):
            return False
        if self._is_healing_basic(unit, skill) and not self._healing_basic_needed(
            unit, enemy, skill
        ):
            return False
        if not self._skill_useful(unit, enemy, skill):
            return False
        return True

    def _skill_score(self, unit: _Unit, enemy: _Unit, skill: dict) -> float:
        """Small deterministic utility model used by auto battle.

        Conditions and current combat state dominate raw slot order, so a pig
        uses an emergency heal, cleanse, setup or execute for a reason instead
        of randomly emptying every ready cooldown.
        """
        score = 10.0 + unit.actives.index(skill)
        when = skill.get("when")
        if when:
            score += 45
        kinds = {effect["type"] for effect in skill["effects"]}
        allies = [ally for ally in self.allies_of(unit) if ally.alive]
        lowest = min((ally.ratio for ally in allies), default=unit.ratio)
        missing = sum(max(0.0, 1 - ally.ratio) for ally in allies)
        if kinds & {
            "heal",
            "mass_heal",
            "triage",
            "cleanse_heal",
            "life_link",
            "regen",
        }:
            score += min(75, missing * 28) + (1 - lowest) * 65
            if lowest < 0.4:
                score += 55
            if unit.role == "治疗":
                score += 18
        cleanse_effects = [
            effect for effect in skill["effects"] if effect["type"] in {"cleanse", "cleanse_heal"}
        ]
        if cleanse_effects:
            # Value only statuses this skill can actually reach.  The previous
            # party-wide boolean made a self-cleanse look useful when another
            # pig was poisoned, and +55 was too small to answer dangerous raid
            # mechanics before another attractive cooldown.
            urgency = max(
                (
                    sum(
                        self._cleanse_urgency(target)
                        for target in self.resolve_targets(
                            unit, enemy, effect.get("target", "self")
                        )
                    )
                    for effect in cleanse_effects
                ),
                default=0.0,
            )
            score += 90 + min(120, urgency * 2) if urgency else -40
        if kinds & {"shield", "guard", "share", "mitigate", "block", "absorb", "immunity"}:
            score += (1 - lowest) * 35
            if lowest < 0.4:
                score += 30
            if unit.role == "坦克":
                score += 18
            protected = [
                target
                for effect in skill["effects"]
                if effect["type"] in {"shield", "guard"}
                for target in self._preview_targets(
                    unit, enemy, effect.get("target", "ally_lowest")
                )
            ]
            score += max((1 - target.ratio) * 70 for target in protected) if protected else 0
        if "rewind" in kinds:
            history_best = max(unit.hp_history, default=unit.hp)
            recoverable = max(history_best, unit.snapshot or 0) - unit.hp
            score += max(0, recoverable) / unit.max_hp * 100
            if unit.ratio < 0.4:
                score += 35
        if kinds & {"leech", "hot_boost"}:
            score += (1 - unit.ratio) * 45
        if kinds & {"buff", "aura"} and any(
            effect.get("target") == "ally_all" or effect["type"] == "aura"
            for effect in skill["effects"]
        ):
            score += 30
            if unit.role == "辅助" and any(ally.role == "输出" for ally in allies):
                score += 24
        if enemy is not None:
            if kinds & {"dispel", "purge"}:
                removable = self._has_removable_benefit(enemy)
                score += 55 if removable else -25
            if kinds & {"damage", "bleed_burst", "detonate"}:
                score += (1 - enemy.ratio) * 25
                window, _ = self._offense_window_state(enemy)
                if window:
                    score += 45 + self._burst_value(skill) * 12
            if any(effect.get("execute") for effect in skill["effects"]):
                score += (1 - enemy.ratio) * 60
            if "detonate" in kinds:
                score += 40 if enemy.dots or enemy.delay_damages else -35
            if kinds & {"stun", "silence", "root"}:
                score += 30
            if kinds & {"weaken", "expose"}:
                score += 28
                if unit.role == "辅助":
                    score += 22
            if kinds & {"mark", "dot", "corrupting"}:
                score += 12
                if unit.role == "辅助" and kinds & {"mark", "corrupting"}:
                    score += 15
        return score

    def _burst_value(self, skill: dict) -> float:
        value = 0.0
        for effect in skill["effects"]:
            if effect["type"] in {"damage", "charge"}:
                power = effect.get("power_max", effect.get("power", 0))
                value += power * effect.get("hits", 1)
                value += 1.0 if effect.get("execute") or effect.get("combo_bonus") else 0.0
            elif effect["type"] in {"detonate", "bleed_burst"}:
                value += 2.5
        return value

    def _offense_window_state(self, enemy: _Unit | None) -> tuple[bool, int | None]:
        """Read generic and raid-provided burst-window hints without coupling engines."""
        if enemy is None:
            return False, None
        active = bool(
            enemy.expose
            or enemy.marks
            or any(buff.get("stat") == "def" and buff.get("pct", 0) < 0 for buff in enemy.buffs)
            or getattr(enemy, "mods", {}).get("def", 0) < 0
            or getattr(enemy, "vuln", 0)
            or getattr(enemy, "break_window", False)
            or getattr(enemy, "broken", False)
            or getattr(enemy, "staggered", False)
            or getattr(enemy, "vulnerable", False)
            or getattr(self, "poise_broken", False)
        )
        eta = None
        sources = (enemy, getattr(self, "mech", None))
        for source in sources:
            if source is None:
                continue
            for name in ("break_in", "break_eta", "turns_to_break", "turns_until_break"):
                value = getattr(source, name, None)
                if type(value) is int and value >= 0:
                    eta = value if eta is None else min(eta, value)
        poise = getattr(self, "poise", None)
        poise_max = getattr(self, "poise_max", None)
        if (
            not active
            and isinstance(poise, (int, float))
            and isinstance(poise_max, (int, float))
            and poise_max > 0
            and poise <= poise_max * 0.25
            and not getattr(self, "poise_locked", False)
        ):
            eta = 1 if eta is None else min(eta, 1)
        return active, eta

    def _hold_for_window(self, unit: _Unit, enemy: _Unit, skill: dict) -> bool:
        if unit.role != "输出" or skill.get("when") or not skill.get("cd"):
            return False
        active, eta = self._offense_window_state(enemy)
        if active or eta != 1 or enemy.ratio < 0.25:
            return False
        return self._burst_value(skill) >= 1.8

    def _cleanse_urgency(self, unit: _Unit) -> float:
        """Estimate how costly it is to leave one ally's removable debuffs up."""
        urgency = 0.0
        for dot in unit.dots:
            stacks = max(1, dot.get("stacks", 1))
            remaining = min(3, max(1, dot.get("turns", 1)))
            urgency += min(60, dot.get("pct", 0) * stacks * remaining)
        urgency += 55 * bool(unit.stun)
        urgency += 35 * bool(unit.silence)
        urgency += 25 * bool(unit.root)
        urgency += 18 * len(unit.weaken)
        urgency += 18 * len(unit.expose)
        urgency += 30 * len(unit.corrupting)
        urgency += 30 * len(unit.delay_damages)
        urgency += 15 * len(unit.marks)
        urgency += 12 * sum(1 for buff in unit.buffs if buff.get("pct", 0) < 0)

        # Raid mechanics keep several cleanseable states outside the generic
        # status containers.  getattr keeps the base 1v1 engine independent.
        urgency += min(45, float(getattr(unit, "vuln", 0)))
        urgency += min(35, float(getattr(unit, "chill", 0)) * 10)
        urgency += 45 * bool(getattr(unit, "glitch", False))
        urgency += 65 * bool(getattr(unit, "possessed", False))
        urgency += 45 * bool(getattr(unit, "lured", False))
        urgency += 45 * bool(getattr(unit, "marked", False))
        urgency += 40 * bool(getattr(unit, "blind", False))
        return urgency

    def plan_actions(self, unit: _Unit, enemy: _Unit) -> list[dict]:
        basic = unit.actives[0]
        if unit.silence or self.max_skills_per_turn(unit) <= 1:
            return [self.choose(unit, enemy)]

        chosen = self.choose(unit, enemy)
        if chosen is basic:
            return [basic]
        budget = self.action_budget(unit)
        if self.action_cost(unit, chosen) + self.action_cost(unit, basic) > budget:
            return [chosen]

        kinds = {effect["type"] for effect in chosen["effects"]}
        # Setup/control and emergency protection should happen before the basic
        # follow-up; direct damage/healing reads more clearly as the finisher.
        setup = bool(
            kinds
            & {
                "buff",
                "dot",
                "mark",
                "weaken",
                "expose",
                "corrupting",
                "stun",
                "silence",
                "root",
                "dispel",
                "purge",
                "cleanse",
                "cleanse_heal",
                "transfer",
                "shield",
                "guard",
                "share",
                "mitigate",
                "block",
                "absorb",
                "immunity",
            }
        )
        defensive = kinds & {
            "heal",
            "mass_heal",
            "triage",
            "cleanse_heal",
            "life_link",
            "regen",
            "rewind",
            "shield",
            "guard",
            "share",
            "mitigate",
            "block",
            "absorb",
            "immunity",
        }
        allies = [ally for ally in self.allies_of(unit) if ally.alive]
        if defensive and min((ally.ratio for ally in allies), default=unit.ratio) < 0.4:
            setup = True
        if chosen.get("cost") and unit.energy < chosen["cost"]:
            projected = unit.energy + (BASIC_ENERGY_GAIN if unit.uses_energy else 0)
            if projected < chosen["cost"]:
                return [basic]
            setup = False  # the opener must generate the missing energy
        return [chosen, basic] if setup else [basic, chosen]

    def act(self, unit: _Unit, enemy: _Unit):
        actions = self.plan_actions(unit, enemy)
        if len(actions) > 1:
            self.say(f"{unit.label} 展开技能链：" + " → ".join(s["name"] for s in actions))
        for skill in actions:
            target = self.action_target(unit, enemy)
            if target is None or not unit.alive:
                break
            # Resource/cooldown changes happen immediately, so later links can
            # neither reuse a skill nor spend energy that is no longer there.
            if not self._can_choose(unit, target, skill):
                continue
            self._cast_output_scale = (
                CHAIN_BASIC_SCALE if self._is_basic_action(unit, skill) else CHAIN_SKILL_SCALE
            ) if len(actions) > 1 else 1.0
            # These flags describe one cast, not the whole chained turn.  A
            # crit on link one must not make a non-crit link two fire another
            # on_crit reaction from stale state.
            unit.flags = {key: False for key in unit.flags}
            try:
                self.cast(unit, target, skill)
            finally:
                self._cast_output_scale = 1.0
            if unit.charge or unit.channel:
                break

    def choose(self, unit: _Unit, enemy: _Unit) -> dict:
        basic = unit.actives[0]
        if unit.silence:
            return basic
        projected = unit.energy
        if self.max_skills_per_turn(unit) > 1 and unit.uses_energy:
            projected += BASIC_ENERGY_GAIN
        ready = [
            skill
            for skill in unit.actives[1:]
            if self._can_choose(unit, enemy, skill, energy=projected)
        ]
        if not ready:
            return basic
        available = [skill for skill in ready if not self._hold_for_window(unit, enemy, skill)]
        if not available:
            return basic
        return max(available, key=lambda skill: self._skill_score(unit, enemy, skill))

    def _try_triggers(self, unit: _Unit, enemy: _Unit, when: str):
        if self._in_trigger or not unit.alive:
            return
        for skill in unit.actives[1:]:
            if skill.get("when") != when:
                continue
            if unit.cooldowns.get(skill["name"], 0):
                continue
            if skill.get("cost") and unit.energy < skill["cost"]:
                continue
            trigger_key = (id(unit), skill["name"])
            if trigger_key in self._turn_triggered:
                continue
            self._turn_triggered.add(trigger_key)
            self._in_trigger = True
            previous_scale = self._cast_output_scale
            self._cast_output_scale = TRIGGER_OUTPUT_SCALE
            try:
                self.cast(unit, enemy, skill)
            finally:
                self._cast_output_scale = previous_scale
                self._in_trigger = False
            break

    def _event_enemy(self, unit: _Unit, attacker: _Unit | None = None) -> _Unit | None:
        if attacker is not None and attacker.alive:
            return attacker
        return next((foe for foe in self.foes_of(unit) if foe.alive), None)

    def _dodge_reactions(self, defender: _Unit, attacker: _Unit):
        """Dispatch dodge reactions to the pig that dodged and its teammates.

        Dodge used to set a flag on the attacker, which made ``on_dodge`` fire
        for the wrong side.  Party support also needs a distinct ally event so
        it can protect the tank instead of hoping the boss targets the support.
        """
        self._try_triggers(defender, attacker, "on_dodge")
        for ally in self.allies_of(defender):
            if ally is not defender:
                self._try_triggers(ally, attacker, "on_ally_dodge")

    def cast(self, unit: _Unit, enemy: _Unit, skill: dict, copied: bool = False):
        if not copied:
            unit.used_skills.add(skill["name"])
            unit.cooldowns[skill["name"]] = skill["cd"] + 1 if skill["cd"] else 0
            unit.last = skill
            if skill.get("cost"):
                unit.energy = max(0, unit.energy - skill["cost"])
            gain = skill.get("gain")
            if gain:
                unit.energy = min(ENERGY_MAX, unit.energy + gain)
            elif skill is unit.actives[0] and unit.uses_energy:
                unit.energy = min(ENERGY_MAX, unit.energy + BASIC_ENERGY_GAIN)
            if skill is unit.actives[0]:
                unit.combo = min(unit.combo_max, unit.combo + 1)
        fail = skill.get("fail")
        if fail and self.rng.randrange(100) < fail["chance"]:
            self.say(f"{unit.label}「{skill['name']}」失手了：{fail['text']}")
            self.apply(unit, enemy, fail["effects"], skill)
            return
        self.apply(unit, enemy, skill["effects"], skill)

    def apply(self, unit: _Unit, enemy: _Unit, effects: list, skill: dict):
        notes = []
        self._leech_pool = 0.0
        for effect in effects:
            if not unit.alive:
                break
            if self.rng.randrange(100) >= effect.get("chance", 100):
                continue
            note = self.effect(unit, enemy, effect, skill)
            if note:
                notes.append(note)
        self.say(
            f"{unit.label}「{skill['name']}」" + ("，".join(notes) if notes else "，但什么也没发生")
        )
        self.announce_falls(unit, enemy)
        if unit.flags["crit"]:
            self._try_triggers(unit, enemy, "on_crit")
        if unit.flags["killed"]:
            self._try_triggers(unit, enemy, "on_kill")

    def announce_falls(self, unit: _Unit, enemy: _Unit):
        for target in (enemy, unit):
            if not target.alive:
                self.say(f"{target.label} 倒下了")

    def dot_amount(self, unit: _Unit, dot: dict) -> float:
        return unit.max_hp * dot["pct"] / 100

    def crit_roll(self, unit: _Unit, enemy: _Unit) -> bool:
        return self.rng.random() * 100 < unit.stat("crit")

    def pierce(self, unit: _Unit, enemy: _Unit, effect: dict) -> float:
        return effect.get("pierce", 0)

    def adjust_hit(self, unit: _Unit, enemy: _Unit, raw: float, effect: dict) -> float:
        """Return 0 to void the hit entirely."""
        return raw

    def after_hit(self, unit: _Unit, enemy: _Unit, dealt: float):
        pass

    def _apply_mark(self, target: _Unit, effect: dict):
        stacks = effect.get("stacks", 1)
        max_stacks = effect.get("max_stacks", 9)
        existing = next((m for m in target.marks), None)
        if existing:
            existing["stacks"] = min(max_stacks, existing.get("stacks", 1) + stacks)
            existing["pct"] = max(existing["pct"], effect["pct"])
            existing["turns"] = max(existing["turns"], effect.get("turns", 3))
            if existing["stacks"] >= max_stacks and effect.get("burst_power"):
                return effect["burst_power"]
        else:
            target.marks.append(
                {
                    "pct": effect["pct"],
                    "turns": effect.get("turns", 3),
                    "stacks": stacks,
                    "max_stacks": max_stacks,
                    "burst_power": effect.get("burst_power"),
                }
            )
        return None

    def effect(self, unit: _Unit, enemy: _Unit, effect: dict, skill: dict) -> str:
        kind = effect["type"]
        if kind == "damage":
            return self.damage(unit, enemy, effect)
        if kind == "heal":
            return self._do_heal(unit, enemy, effect)
        if kind == "shield":
            notes = []
            for target in self.resolve_targets(unit, enemy, effect.get("target", "self")):
                amount = target.max_hp * effect["pct"] / 100 * self._cast_output_scale
                target.shield += amount
                notes.append(f"{target.label}获得{round(amount)}护盾")
            return "，".join(notes) or "获得护盾"
        if kind == "stun":
            if enemy.immune_to("stun") and effect.get("target") != "self":
                return f"{enemy.label}免疫控制"
            target = unit if effect.get("target") == "self" else enemy
            label = effect.get("label", "眩晕")
            target.stun = max(target.stun, effect.get("turns", 1))
            target.stun_label = label
            return f"{'自己' if target is unit else target.label}陷入{label}"
        if kind == "dot":
            if enemy.immune_to("dot"):
                return f"{enemy.label}免疫持续伤害"
            pct = effect["pct"] * self._cast_output_scale
            stacks = effect.get("stacks", 1)
            max_stacks = effect.get("max_stacks", 9)
            label = effect.get("label", "持续伤害")
            existing = next((d for d in enemy.dots if d["label"] == label), None)
            if existing:
                existing["stacks"] = min(max_stacks, existing.get("stacks", 1) + stacks)
                existing["turns"] = max(existing["turns"], effect["turns"])
                existing["pct"] = max(existing["pct"], pct)
            else:
                enemy.dots.append(
                    {
                        "pct": pct,
                        "turns": effect["turns"],
                        "label": label,
                        "stacks": stacks,
                        "max_stacks": max_stacks,
                    }
                )
            if effect.get("spread"):
                others = [f for f in self.foes_of(unit) if f is not enemy and f.alive]
                if others:
                    other = self.rng.choice(others)
                    other.dots.append(
                        {
                            "pct": pct * 0.5,
                            "turns": effect["turns"],
                            "label": label,
                            "stacks": 1,
                        }
                    )
            return f"{enemy.label}陷入{label}"
        if kind == "buff":
            targets = self.resolve_targets(unit, enemy, effect.get("target", "self"))
            if effect.get("target") == "enemy":
                targets = [enemy] if enemy.alive else []
            notes = []
            pct = effect["pct"] * self._cast_output_scale
            for target in targets:
                turns = effect.get("turns", 2) + (target is unit)
                target.buffs.append({"stat": effect["stat"], "pct": pct, "turns": turns})
                sign = "+" if pct >= 0 else ""
                unit_text = "%" if effect["stat"] in ("atk", "def", "spd") else ""
                who = target.label if target is not unit else ""
                notes.append(f"{who}{STAT_NAMES[effect['stat']]}{sign}{pct:g}{unit_text}")
            return "，".join(notes)
        if kind == "thorns":
            pct = effect["pct"] * self._cast_output_scale
            unit.thorns.append({"pct": pct, "turns": effect.get("turns", 2) + 1})
            return f"进入反弹状态 {pct:g}%"
        if kind == "regen":
            for target in self.resolve_targets(unit, enemy, effect.get("target", "self")):
                target.regens.append(
                    {
                        "pct": effect["pct"] * self._cast_output_scale,
                        "turns": effect.get("turns", 3),
                        "stacks": effect.get("stacks", 1),
                        "source": unit,
                    }
                )
            return "开始持续回复"
        if kind == "evade":
            targets = self.resolve_targets(unit, enemy, effect.get("target", "self"))
            for target in targets:
                target.evade += effect.get("count", 1)
            return "、".join(target.label for target in targets) + "准备躲开下一次攻击"
        if kind == "cleanse":
            for target in self.resolve_targets(unit, enemy, effect.get("target", "self")):
                self._cleanse(target)
            return "清除了负面状态"
        if kind == "dispel":
            return self._purge(enemy, "all")
        if kind == "copy":
            source = enemy.last
            if not source or any(e["type"] == "copy" for e in source["effects"]):
                return "没找到能复制的技能"
            # Fold copy efficiency into the same output scale used by chains
            # and reactions.  This covers shields, DoTs, buffs and stored
            # charge/channel values too; the old _copy_scale path only reached
            # immediate damage and direct healing.
            previous_scale = self._cast_output_scale
            self._cast_output_scale *= effect.get("pct", 100) / 100
            self.say(f"{unit.label} 复制了「{source['name']}」！")
            try:
                self.cast(unit, enemy, source, copied=True)
            finally:
                self._cast_output_scale = previous_scale
            return ""
        if kind == "recoil":
            lost = self.hurt(unit, unit.max_hp * effect["pct"] / 100)
            return f"自己也损失 {round(lost)} 生命"
        if kind == "taunt":
            unit.taunt = max(unit.taunt, effect["turns"])
            return f"嘲讽 {effect['turns']} 回合"
        if kind == "redirect":
            unit.redirect = max(unit.redirect, effect["turns"])
            return f"吸引火力 {effect['turns']} 回合"
        if kind == "guard":
            wards = self.resolve_targets(unit, enemy, effect.get("target", "ally_lowest"))
            wards = [w for w in wards if w is not unit] or self.resolve_targets(unit, enemy, "self")
            for ward in wards[:1]:
                unit.guards.append(
                    {
                        "ward": ward,
                        "pct": effect["pct"] * self._cast_output_scale,
                        "turns": effect["turns"] + 1,
                    }
                )
                return f"守护 {ward.label}"
            return "守护落空"
        if kind == "share":
            members = [u for u in self.allies_of(unit) if u.alive]
            pct = effect["pct"] * self._cast_output_scale
            link = {"pct": pct, "turns": effect["turns"] + 1, "members": members}
            for member in members:
                member.share = link
            if len(members) == 1:
                return f"自我分摊减伤 {effect['pct']:g}%"
            return f"与队友灵魂链接 {effect['pct']:g}%"
        if kind == "aura":
            aura = dict(effect)
            for key in ("pct", "heal_pct"):
                if key in aura:
                    aura[key] *= self._cast_output_scale
            aura["source"] = unit
            unit.auras.append(aura)
            return "展开光环"
        if kind == "mitigate":
            pct = effect["pct"] * self._cast_output_scale
            unit.mitigates.append({"pct": pct, "turns": effect.get("turns", 2) + 1})
            return f"减伤 {pct:g}%"
        if kind == "block":
            unit.block_count += effect["count"]
            unit.block_pct = effect.get("pct", 100) * self._cast_output_scale
            return f"格挡 {effect['count']} 次"
        if kind == "absorb":
            amount = unit.max_hp * effect["pct"] / 100 * self._cast_output_scale
            unit.shield += amount
            unit.absorb_break = effect.get("break_power", 0)
            return f"吸收盾 {round(amount)}"
        if kind == "reflect":
            pct = effect["pct"] * self._cast_output_scale
            unit.reflects.append(
                {
                    "pct": pct,
                    "turns": effect.get("turns", 2) + 1,
                    "stun_turns": effect.get("stun_turns"),
                    "dot_pct": effect.get("dot_pct", 0) * self._cast_output_scale or None,
                }
            )
            return f"反射 {pct:g}%"
        if kind == "fortify":
            pool = (
                unit.shield * effect["pct"] / 100 + unit.stat("def") * effect["pct"] / 10
            ) * self._cast_output_scale
            unit.shield += pool
            return f"固化护盾 {round(pool)}"
        if kind == "last_stand":
            unit.last_stand = dict(effect)
            unit.last_stand["pct"] *= self._cast_output_scale
            return "预备绝境坚守"
        if kind == "sacrifice":
            lost = self.hurt(unit, unit.max_hp * effect["pct"] / 100)
            notes = [f"自损 {round(lost)}"]
            for target in self.resolve_targets(unit, enemy, effect.get("target", "ally_lowest")):
                if effect.get("shield_pct"):
                    target.shield += (
                        target.max_hp * effect["shield_pct"] / 100 * self._cast_output_scale
                    )
                    notes.append(f"{target.label}获盾")
                if effect.get("heal_pct"):
                    healed = self.heal(
                        target,
                        target.max_hp * effect["heal_pct"] / 100 * self._cast_output_scale,
                    )
                    notes.append(f"治疗{healed}")
            if effect.get("mitigate_pct"):
                unit.mitigates.append(
                    {"pct": effect["mitigate_pct"] * self._cast_output_scale, "turns": 3}
                )
                notes.append("自身减伤")
            return "，".join(notes)
        if kind == "rewind":
            steps = effect.get("turns", 1)
            if unit.snapshot is not None:
                unit.hp = min(
                    unit.max_hp, unit.hp + max(0, unit.snapshot - unit.hp) * self._cast_output_scale
                )
                unit.snapshot = None
                return f"回溯至快照 {round(unit.hp)}"
            if len(unit.hp_history) > steps:
                past = unit.hp_history[-1 - steps]
            elif unit.hp_history:
                past = unit.hp_history[0]
            else:
                past = unit.hp
            unit.hp = min(unit.max_hp, unit.hp + max(0, past - unit.hp) * self._cast_output_scale)
            return f"时光倒流至 {round(unit.hp)}"
        if kind == "snapshot":
            unit.snapshot = unit.hp
            return f"记录快照 {round(unit.hp)}"
        if kind == "delay_heal":
            for target in self.resolve_targets(unit, enemy, effect.get("target", "self")):
                unit.delay_heals.append(
                    {
                        "target": target,
                        "pct": effect["pct"] * self._cast_output_scale,
                        "turns": effect["turns"],
                        "opts": {
                            "overheal_shield": effect.get("overheal_shield", False),
                            "crit_heal": effect.get("crit_heal", False),
                        },
                        "source": unit,
                    }
                )
            return f"延迟治疗 {effect['turns']} 回合"
        if kind == "delay_damage":
            enemy.delay_damages.append(
                {
                    "pct": effect["pct"] * self._cast_output_scale,
                    "turns": effect["turns"],
                    "label": effect.get("label", "延时炸弹"),
                    "source": unit,
                }
            )
            return f"给{enemy.label}安装延时炸弹"
        if kind == "charge":
            unit.charge = {
                k: v for k, v in effect.items() if k not in ("type", "chance")
            }
            unit.charge["power"] *= self._cast_output_scale
            if "power_max" in unit.charge:
                unit.charge["power_max"] *= self._cast_output_scale
            return f"开始蓄力 {effect['turns']} 回合"
        if kind == "channel":
            unit.channel = {
                "turns": effect["turns"],
                "power": effect.get("power", 0) * self._cast_output_scale,
                "heal_pct": effect.get("heal_pct", 0) * self._cast_output_scale,
                "source": unit,
            }
            return f"开始引导 {effect['turns']} 回合"
        if kind == "mark":
            marked = dict(effect)
            marked["pct"] *= self._cast_output_scale
            burst = self._apply_mark(enemy, marked)
            if burst:
                note = self.damage(unit, enemy, {"type": "damage", "power": burst})
                enemy.marks.clear()
                return f"标记引爆，{note}"
            return f"标记{enemy.label}"
        if kind == "detonate":
            total = 0.0
            for dot in list(enemy.dots):
                stacks = max(1, dot.get("stacks", 1))
                total += self.dot_amount(enemy, dot) * stacks * max(1, dot["turns"])
                enemy.dots.remove(dot)
            for bomb in list(enemy.delay_damages):
                total += enemy.max_hp * bomb["pct"] / 100
                enemy.delay_damages.remove(bomb)
            if total <= 0:
                return "没有可引爆的效果"
            dealt = self.hurt(enemy, total * self._cast_output_scale)
            return f"引爆造成 {round(dealt)} 伤害"
        if kind == "combo":
            unit.combo = min(effect.get("max", unit.combo_max), unit.combo + effect.get("gain", 1))
            unit.combo_max = effect.get("max", unit.combo_max)
            return f"连击 {unit.combo}"
        if kind == "leech":
            # Applies to damage already dealt this skill via _leech_pool, or arms drain.
            amount = self._leech_pool * effect["pct"] / 100
            if amount <= 0:
                return "吸血落空"
            delay = effect.get("delay", 0)
            targets = self.resolve_targets(unit, enemy, effect.get("target", "self"))
            target = targets[0] if targets else unit
            if delay:
                unit.pending_leech.append(
                    {"target": target, "amount": amount, "turns": delay, "source": unit}
                )
                return f"延迟吸血 {round(amount)}"
            healed = self.heal(target, amount)
            return f"吸血 {healed}"
        if kind == "hot_boost":
            for target in self.resolve_targets(unit, enemy, effect.get("target", "self")):
                target.hot_boost.append(
                    {
                        "pct": effect["pct"] * self._cast_output_scale,
                        "turns": effect.get("turns", 3) + 1,
                    }
                )
            return f"持续治疗增强 {effect['pct'] * self._cast_output_scale:g}%"
        if kind == "corrupting":
            enemy.corrupting.append(
                {"pct": effect["pct"] * self._cast_output_scale, "turns": effect["turns"] + 1}
            )
            return f"{enemy.label}陷入反治疗"
        if kind == "bleed_burst":
            need = effect["stacks"]
            for dot in list(enemy.dots):
                if dot.get("stacks", 1) >= need:
                    # Long-lived mechanic DoTs use a high sentinel duration so
                    # they persist until cleansed.  They may explicitly cap
                    # how many ticks a detonation settles via burst_turns.
                    turns = dot.get("burst_turns", dot["turns"])
                    total = self.dot_amount(enemy, dot) * dot["stacks"] * max(1, turns)
                    enemy.dots.remove(dot)
                    dealt = self.hurt(enemy, total * self._cast_output_scale)
                    return f"层数爆发 {round(dealt)} 伤害"
            return "层数不足"
        if kind == "silence":
            enemy.silence = max(enemy.silence, effect["turns"])
            return f"沉默{enemy.label}"
        if kind == "root":
            enemy.root = max(enemy.root, effect["turns"])
            enemy.evade = 0
            return f"禁锢{enemy.label}"
        if kind == "weaken":
            if enemy.immune_to("weaken"):
                return f"{enemy.label}免疫虚弱"
            enemy.weaken.append(
                {"pct": effect["pct"] * self._cast_output_scale, "turns": effect["turns"] + 1}
            )
            return f"{enemy.label}造成伤害降低"
        if kind == "expose":
            enemy.expose.append(
                {"pct": effect["pct"] * self._cast_output_scale, "turns": effect["turns"] + 1}
            )
            return f"{enemy.label}易伤"
        if kind == "purge":
            return self._purge(enemy, effect.get("mode", "all"))
        if kind == "transfer":
            if effect["mode"] == "debuff":
                if unit.dots:
                    enemy.dots.append(unit.dots.pop(0))
                    return "转移持续伤害"
                negs = [b for b in unit.buffs if b["pct"] < 0]
                if negs:
                    enemy.buffs.append(negs[0])
                    unit.buffs.remove(negs[0])
                    return "转移减益"
                return "没有可转移的负面"
            buffs = [b for b in enemy.buffs if b["pct"] > 0]
            if buffs:
                unit.buffs.append(buffs[0])
                enemy.buffs.remove(buffs[0])
                return "偷取增益"
            if enemy.shield > 0:
                stolen = enemy.shield * 0.5
                enemy.shield -= stolen
                unit.shield += stolen
                return "偷取护盾"
            return "没有可偷取的增益"
        if kind == "immunity":
            unit.immunity[effect["kind"]] = max(
                unit.immunity.get(effect["kind"], 0), effect.get("turns", 2)
            )
            return f"免疫{effect['kind']}"
        if kind == "mass_heal":
            notes = []
            for ally in self.allies_of(unit):
                if ally.alive:
                    healed = self.heal(
                        ally, ally.max_hp * effect["pct"] / 100 * self._cast_output_scale
                    )
                    notes.append(f"{ally.label}+{healed}")
            return "群体治疗 " + "，".join(notes) if notes else "群体治疗落空"
        if kind == "triage":
            targets = self.resolve_targets(unit, enemy, "ally_lowest")
            if not targets:
                return "没有治疗目标"
            target = targets[0]
            pct = effect["pct"]
            if target.ratio * 100 <= effect.get("threshold", 40):
                pct += effect.get("bonus", 0)
            healed = self.heal(target, target.max_hp * pct / 100 * self._cast_output_scale)
            return f"急救{target.label} {healed}"
        if kind == "cleanse_heal":
            notes = []
            for target in self.resolve_targets(unit, enemy, effect.get("target", "ally_lowest")):
                self._cleanse(target)
                healed = self.heal(
                    target, target.max_hp * effect["pct"] / 100 * self._cast_output_scale
                )
                notes.append(f"{target.label}+{healed}")
            return "净化治疗 " + "，".join(notes)
        if kind == "life_link":
            notes = []
            if effect.get("self_pct", 0) < 0:
                lost = self.hurt(unit, unit.max_hp * abs(effect["self_pct"]) / 100)
                notes.append(f"自损{round(lost)}")
            elif effect.get("self_pct", 0) > 0:
                healed = self.heal(
                    unit, unit.max_hp * effect["self_pct"] / 100 * self._cast_output_scale
                )
                notes.append(f"自疗{healed}")
            for target in self.resolve_targets(unit, enemy, effect.get("target", "ally_lowest")):
                healed = self.heal(
                    target, target.max_hp * effect["pct"] / 100 * self._cast_output_scale
                )
                notes.append(f"{target.label}+{healed}")
            return "生命链接 " + "，".join(notes)
        if kind == "energy":
            for target in self.resolve_targets(unit, enemy, effect.get("target", "self")):
                target.energy = min(ENERGY_MAX, target.energy + effect["gain"])
                target.uses_energy = True
            return f"回复能量 {effect['gain']}"
        if kind == "pick":
            options = effect["options"]
            weights = [opt.get("weight", 1) for opt in options]
            chosen = self.rng.choices(options, weights=weights, k=1)[0]
            label = chosen.get("text") or "随机效果"
            notes = []
            for nested in chosen["effects"]:
                if not unit.alive:
                    break
                if self.rng.randrange(100) >= nested.get("chance", 100):
                    continue
                note = self.effect(unit, enemy, nested, skill)
                if note:
                    notes.append(note)
            body = "，".join(notes) if notes else "什么也没发生"
            return f"抽到「{label}」：{body}"
        return ""

    def _do_heal(self, unit: _Unit, enemy: _Unit, effect: dict) -> str:
        notes = []
        scale = self._copy_scale * self._cast_output_scale
        for target in self.resolve_targets(unit, enemy, effect.get("target", "self")):
            amount = target.max_hp * effect["pct"] / 100 * scale
            healed, over = self.heal_with_options(target, amount, effect)
            bit = f"{target.label}回复{healed}" if target is not unit else f"回复 {healed} 生命"
            if over:
                bit += f"（溢盾{over}）"
            notes.append(bit)
        return "，".join(notes) or "回复 0 生命"

    def _cleanse(self, unit: _Unit):
        unit.dots.clear()
        unit.buffs = [b for b in unit.buffs if b["pct"] >= 0]
        unit.stun = 0
        unit.silence = 0
        unit.root = 0
        unit.weaken.clear()
        unit.expose.clear()
        unit.corrupting.clear()
        unit.delay_damages.clear()
        unit.marks.clear()

    def _purge(self, enemy: _Unit, mode: str) -> str:
        mode = mode or "all"
        if mode in ("buffs", "all"):
            enemy.buffs = [b for b in enemy.buffs if b["pct"] < 0]
            enemy.thorns.clear()
            enemy.reflects.clear()
            enemy.evade = 0
            enemy.mitigates.clear()
            enemy.hot_boost.clear()
        if mode in ("shield", "all"):
            enemy.shield = 0
        return f"驱散了{enemy.label}的增益"

    def damage(self, unit: _Unit, enemy: _Unit, effect: dict) -> str:
        true = effect.get("true", False)
        if enemy.evade and not true:
            enemy.evade -= 1
            self._dodge_reactions(enemy, unit)
            return f"被{enemy.label}完全躲开"
        scale = effect.get("scale", "atk")
        if scale == "atk":
            base = unit.stat("atk")
        elif scale == "hp":
            base = unit.max_hp * 0.18
        else:
            base = unit.stat(scale) * 1.6
        hits = effect.get("hits", 1)
        total, crits, misses, voids = 0.0, 0, 0, 0
        self.void_note = ""
        combo_spent = 0
        if effect.get("combo_bonus") and unit.combo:
            combo_spent = unit.combo
            unit.combo = 0
        for _ in range(hits):
            if not enemy.alive:
                break
            if not (true or effect.get("sure")) and self.rng.random() * 100 < enemy.stat("dodge"):
                misses += 1
                self._dodge_reactions(enemy, unit)
                continue
            power = effect["power"]
            if "power_max" in effect:
                power = self.rng.uniform(power, effect["power_max"])
            raw = base * power * self.rng.uniform(0.9, 1.1) * self.fury()
            raw *= self._weaken_factor(unit) * self._copy_scale * self._cast_output_scale
            if effect.get("execute") and enemy.ratio * 100 <= effect["execute"]:
                raw *= EXECUTE_MULTIPLIER
            if effect.get("threshold"):
                missing = 1 - enemy.ratio
                raw *= 1 + missing * (effect["threshold"] / 100)
            if combo_spent and effect.get("combo_bonus"):
                raw *= 1 + combo_spent * effect["combo_bonus"] / 100
            raw *= 1 + self._mark_bonus(enemy) / 100
            if self.crit_roll(unit, enemy):
                raw *= unit.crit_multiplier
                crits += 1
                unit.flags["crit"] = True
            if not true:
                defense = enemy.stat("def") * (1 - min(100, self.pierce(unit, enemy, effect)) / 100)
                raw *= 60 / (60 + defense)
            raw = self.adjust_hit(unit, enemy, raw, effect)
            if raw <= 0:
                voids += 1
                continue
            before_hp = enemy.hp
            dealt = self.hurt(enemy, max(1.0, raw), attacker=unit)
            total += dealt
            self.after_hit(unit, enemy, dealt)
            for thorn in enemy.thorns:
                self.hurt(unit, dealt * thorn["pct"] / 100, attacker=enemy)
            for reflect in enemy.reflects:
                bounced = self.hurt(unit, dealt * reflect["pct"] / 100, attacker=enemy)
                if reflect.get("stun_turns") and bounced > 0 and not unit.immune_to("stun"):
                    unit.stun = max(unit.stun, reflect["stun_turns"])
                    unit.stun_label = "反震"
                if reflect.get("dot_pct") and bounced > 0 and not unit.immune_to("dot"):
                    unit.dots.append(
                        {
                            "pct": reflect["dot_pct"],
                            "turns": 2,
                            "label": "反噬",
                            "stacks": 1,
                        }
                    )
            if effect.get("overflow") and before_hp > 0 and not enemy.alive:
                overflow = max(0.0, raw - before_hp)
                others = [f for f in self.foes_of(unit) if f.alive]
                if overflow > 0 and others:
                    self.hurt(others[0], overflow, attacker=unit)
            if effect.get("cleave"):
                for foe in self.foes_of(unit):
                    if foe is not enemy and foe.alive:
                        self.hurt(foe, dealt * effect["cleave"] / 100, attacker=unit)
        if not enemy.alive and total > 0:
            unit.flags["killed"] = True
        self._leech_pool += total
        if misses == hits:
            return f"被{enemy.label}闪开了"
        if misses + voids == hits:
            return f"打了个空（{self.void_note or enemy.label + '化解了攻击'}）"
        text = f"造成 {round(total)} 伤害"
        if hits > 1:
            text = f"{hits - misses - voids} 段命中，共{text}"
        if crits:
            text += "（暴击）"
        if effect.get("drain"):
            healed = self.heal(unit, total * effect["drain"] / 100)
            if healed:
                text += f"，吸血 {healed}"
        if (enemy.thorns or enemy.reflects) and total:
            text += "，被反弹了一部分"
        return text


def simulate(a: dict, b: dict, seed: int) -> dict:
    return _Battle(a, b, seed).run()
