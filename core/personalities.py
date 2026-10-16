"""Personality talents earned by completing a full three-boss raid."""

PERSONALITY_CHANCE = 0.25


def _skill(name, text, effects, *, passive=False, when=None, cd=0):
    return {
        "name": name,
        "text": text,
        "cd": cd,
        "when": when,
        "passive": passive,
        "effects": effects,
    }


PERSONALITIES = {
    "combo_fever": {
        "name": "连击狂热",
        "roles": ("输出", "辅助"),
        "skill": _skill("性格·连击狂热", "暴击后立刻补上一击。", [{"type": "damage", "power": 0.65, "sure": True}], when="on_crit", cd=1),
    },
    "hunter": {
        "name": "穷追猛打",
        "roles": ("输出", "坦克"),
        "skill": _skill("性格·穷追猛打", "敌人虚弱时追加斩杀攻击。", [{"type": "damage", "power": 0.9, "execute": 35}], when="enemy_low", cd=2),
    },
    "critical_overload": {
        "name": "会心过载",
        "roles": ("输出", "辅助"),
        "skill": _skill("性格·会心过载", "暴击伤害倍率由 1.6 倍变为 2 倍。", [{"type": "crit_amp", "multiplier": 2.0}], passive=True),
    },
    "bristled": {
        "name": "荆棘脾气",
        "roles": ("坦克", "辅助"),
        "skill": _skill("性格·荆棘脾气", "受到直接伤害时反伤。", [{"type": "thorns", "pct": 18, "turns": 99}], passive=True),
    },
    "guardian": {
        "name": "护主天性",
        "roles": ("坦克", "辅助", "治疗"),
        "skill": _skill("性格·护主天性", "队友受伤后为最低血队友补盾。", [{"type": "shield", "pct": 10, "target": "ally_lowest"}], when="on_ally_hurt", cd=2),
    },
    "grateful": {
        "name": "知恩图报",
        "roles": ("治疗", "辅助", "坦克"),
        "skill": _skill("性格·知恩图报", "队友受伤后治疗最低血队友。", [{"type": "heal", "pct": 11, "target": "ally_lowest"}], when="on_ally_hurt", cd=2),
    },
    "second_wind": {
        "name": "绝处逢生",
        "roles": ("坦克", "治疗", "输出"),
        "skill": _skill("性格·绝处逢生", "濒危时自疗并净化。", [{"type": "heal", "pct": 24, "target": "self"}, {"type": "cleanse", "target": "self"}], when="low_hp", cd=4),
    },
    "unyielding": {
        "name": "不倒翁",
        "roles": ("坦克", "治疗"),
        "skill": _skill("性格·不倒翁", "首次倒下时以 25% 生命复起。", [{"type": "revive", "pct": 25}], passive=True),
    },
    "riposte": {
        "name": "见招拆招",
        "roles": ("坦克", "输出", "辅助"),
        "skill": _skill("性格·见招拆招", "闪避后发动必中反击。", [{"type": "damage", "power": 0.8, "sure": True}], when="on_dodge", cd=1),
    },
    "warm_current": {
        "name": "暖流体质",
        "roles": ("治疗", "辅助"),
        "skill": _skill("性格·暖流体质", "每回合为全队恢复少量生命。", [{"type": "aura", "heal_pct": 3}], passive=True),
    },
    "echo": {
        "name": "蓄势余震",
        "roles": ("输出", "坦克"),
        "skill": _skill("性格·蓄势余震", "蓄力完成后追加一次余震。", [{"type": "damage", "power": 0.75, "sure": True}], when="after_charge", cd=1),
    },
    "shield_hunter": {
        "name": "破盾追猎",
        "roles": ("输出", "辅助", "治疗"),
        "skill": _skill("性格·破盾追猎", "自身护盾被击碎时立刻反击。", [{"type": "damage", "power": 0.7, "sure": True}], when="on_shield_break", cd=2),
    },
}


def personality_for(personality_id: str | None) -> dict | None:
    return PERSONALITIES.get(personality_id or "")


def choices_for(role: str) -> list[str]:
    return [key for key, value in PERSONALITIES.items() if role in value["roles"]]
