"""Round-timed four-player mythic dungeons with trash and random ADD events."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .battle import entry_for, fighter, level_for
from .raid import DUNGEONS, boss_fighter, simulate_raid

MIN_LEVEL = 5
START_KEY_LEVEL = 2
DEATH_PENALTY = 3
OVERTIME_GRACE = 30
ADD_RUN_CHANCE = 30.0
ADD_DOUBLE_CHANCE = 30.0

MYTHIC_LIMITS = {
    1: 80,
    2: 74,
    3: 77,
    4: 68,
    5: 74,
    6: 78,
    7: 72,
}

TRASH_COUNT = 6
TRASH_ROLES = ("坦克", "输出", "治疗")
AFFIXES = {
    "fortified": {"name": "强韧", "description": "副本小怪与 ADD 生命提高 12%，攻击提高 8%。"},
    "tyrannical": {"name": "残暴", "description": "Boss 生命提高 12%，攻击提高 8%。"},
    "volcanic": {"name": "火山", "description": "每 5 回合在随机玩家小猪脚下喷发。"},
    "grievous": {"name": "重伤", "description": "回合结束时，低于 50% 生命的小猪受到额外伤害。"},
}
ROOKIE_BLESSING = {
    2: {"hp": 50, "atk": 50, "def": 35},
    3: {"hp": 35, "atk": 35, "def": 25},
    4: {"hp": 20, "atk": 20, "def": 15},
}
RUN_REWARD_DRAWS = ((17, 3), (12, 2), (7, 1))
WEEKLY_REWARD_MILESTONES = (
    {"id": "rare", "key_level": 2, "name": "稀有小猪", "rarity": "稀有", "pulls": 1},
    {"id": "epic", "key_level": 7, "name": "史诗小猪", "rarity": "史诗", "pulls": 1},
    {"id": "mythic", "key_level": 12, "name": "神话小猪", "rarity": "神话", "pulls": 1},
    {"id": "ten-pull", "key_level": 17, "name": "十连抽", "rarity": None, "pulls": 10},
)
THEME_KEYWORDS = {
    1: ("雪", "冰", "冻", "寒", "冷", "冬", "珠峰"),
    2: ("机械", "赛博", "机器", "电脑", "智能", "代码", "程序", "404", "键盘", "科技", "安卓", "电"),
    3: ("神", "佛", "天堂", "圣", "智慧", "王", "冠", "祭", "仙"),
    4: ("僵尸", "骷髅", "灵魂", "幽灵", "鬼", "墓", "尸", "死"),
    5: ("深海", "海", "鱼", "水", "寿司", "鲨", "珊瑚", "潜", "鲸", "河"),
    6: ("星", "外星", "宇宙", "飞碟", "行星", "火箭", "月", "云"),
    7: ("火", "熔岩", "烤", "红烧", "爆破", "炸", "炉", "燃", "烟"),
}
THEME_ROSTERS = {
    1: {
        "坦克": ("pighub0131", "pighub0396"),
        "输出": ("snow-pig", "frozen-pig", "pighub0310", "pighub0897"),
        "治疗": ("pighub0476", "pighub0500", "pighub0560"),
    },
    2: {
        "坦克": ("pighub0192", "tank_pig"),
        "输出": ("mechanical-pig", "cyberpunk-pig", "pighub0300"),
        "治疗": ("pighub0091", "pighub0582"),
    },
    3: {
        "坦克": ("pighub0332", "pighub0555", "pighub0663"),
        "输出": ("pighub0546", "pighub0908"),
        "治疗": ("heaven-pig", "pighub0590", "pighub0787", "pighub0821"),
    },
    4: {
        "坦克": ("skeleton-pig", "pighub0211"),
        "输出": ("zombie-pig", "soul-pig"),
        "治疗": ("pighub0364", "pighub0395", "pighub0891"),
    },
    5: {
        "坦克": ("pighub0007", "pighub0830"),
        "输出": ("salmon-sushi-pig", "pighub0233", "pighub0524"),
        "治疗": ("pighub0726", "pighub0811", "pighub0929"),
    },
    6: {
        "坦克": ("pighub0129", "pighub0204", "pighub0336"),
        "输出": ("alien-pig", "pighub0845", "pighub0872"),
        "治疗": ("pighub0124", "pighub0263", "pighub0558"),
    },
    7: {
        "坦克": ("roasted-pig", "pighub0807"),
        "输出": ("explosive-pig", "suckling-pig", "pighub0645", "pighub0793"),
        "治疗": ("pighub0308", "pighub0315", "pighub0321"),
    },
}

FORCES = (15, 15, 17, 18, 17, 18)
TRASH_AFTER = (2, 5, 8)


def dungeon_info(key: int) -> dict:
    return next(item for item in DUNGEONS if item["key"] == key)


def enemy_scale(key_level: int) -> tuple[float, float]:
    extra = max(0, key_level - START_KEY_LEVEL)
    return 1.043**extra, 1.032**extra


def affixes_for(dungeon_key: int, key_level: int, seed: int) -> list[dict]:
    """Fixed thresholds with a deterministic primary affix for reproducible runs."""
    keys = ["fortified" if (seed + dungeon_key) % 2 == 0 else "tyrannical"]
    if key_level >= 7:
        keys.append("volcanic")
    if key_level >= 12:
        keys.append("grievous")
    return [{"id": key, **AFFIXES[key]} for key in keys]


def rookie_blessing(levels: list[int], key_level: int) -> dict | None:
    """A fixed low-key on-ramp for parties made entirely of Lv5–9 pigs."""
    bonus = ROOKIE_BLESSING.get(key_level)
    if not bonus or not levels or min(levels) < MIN_LEVEL or max(levels) > 9:
        return None
    return {
        "name": "新锐祝福",
        "description": (
            f"全队均为 Lv5–9：生命与攻击提高 {bonus['hp']}% / {bonus['atk']}%，"
            f"防御提高 {bonus['def']}%；+5 起失效。"
        ),
        **bonus,
    }


def run_reward_draws(key_level: int, timed: bool) -> int:
    """Daily bonus draws earned by a timed run; low keys award only the entered pig."""
    if not timed:
        return 0
    return next((draws for threshold, draws in RUN_REWARD_DRAWS if key_level >= threshold), 0)


def weekly_reward_milestones(key_level: int, timed: bool) -> list[dict]:
    """Weekly cumulative milestones reached by this timed key level."""
    if not timed:
        return []
    return [dict(item) for item in WEEKLY_REWARD_MILESTONES if key_level >= item["key_level"]]


def timed_upgrade(rounds: int, limit: int, completed: bool) -> int:
    if not completed or rounds > limit:
        return -1
    remaining = (limit - rounds) / limit
    if remaining >= 0.30:
        return 3
    if remaining >= 0.15:
        return 2
    return 1


def score_for(key_level: int, upgrade: int, completed: bool) -> int:
    if not completed:
        return 0
    return key_level * 100 + {3: 50, 2: 35, 1: 20}.get(upgrade, 5)


def _dungeon_trash_roster(catalog: dict[str, dict], dungeon_key: int) -> list[list[dict]]:
    """Build six themed squads, each with tank, damage and healer pigs."""
    all_bosses = {slot for item in DUNGEONS for slot in item["bosses"]}
    pool = sorted(
        (pig for pig in catalog.values() if pig.get("enabled", 1) and pig["id"] not in all_bosses),
        key=lambda pig: pig["id"],
    )
    if len(pool) < len(TRASH_ROLES):
        pool = sorted((pig for pig in catalog.values() if pig.get("enabled", 1)), key=lambda pig: pig["id"])
    if not pool:
        raise ValueError("Pig catalog has no entries for dungeon trash")
    keywords = THEME_KEYWORDS[dungeon_key]
    by_role = {}
    for role in TRASH_ROLES:
        role_pool = [pig for pig in pool if entry_for(pig.get("battle")).get("role", "输出") == role]
        curated = [
            catalog[pig_id]
            for pig_id in THEME_ROSTERS[dungeon_key][role]
            if pig_id in catalog and catalog[pig_id].get("enabled", 1)
        ]
        themed = curated or [pig for pig in role_pool if any(word in pig["name"] for word in keywords)]
        by_role[role] = themed or role_pool
        if not by_role[role]:
            raise ValueError(f"Pig catalog has no {role} entries for dungeon trash")
    offset = (dungeon_key - 1) * 2
    return [
        [by_role[role][(offset + wave) % len(by_role[role])] for role in TRASH_ROLES]
        for wave in range(TRASH_COUNT)
    ]


def _trash_enemy(template: dict, key_level: int, index: int, affix_ids: set[str]) -> dict:
    unit = fighter(template, entry_for(template.get("battle")), 3, template["name"], rarity="普通")
    hp_scale, atk_scale = enemy_scale(key_level)
    # Three real units share one trash-pack budget.  Their combined health is
    # comparable to the former aggregate pack, while their three skill turns
    # are intentionally softened so Lv5 remains a valid entry bracket.
    unit["stats"]["hp"] = max(1, round(unit["stats"]["hp"] * (0.48 + index * 0.02) * hp_scale))
    unit["stats"]["atk"] = max(1, round(unit["stats"]["atk"] * 0.30 * atk_scale))
    if "fortified" in affix_ids:
        unit["stats"]["hp"] = round(unit["stats"]["hp"] * 1.12)
        unit["stats"]["atk"] = round(unit["stats"]["atk"] * 1.08)
    unit["start_hp"] = unit["stats"]["hp"]
    unit["seat"] = None
    unit["mods"] = {}
    unit["dot_basis"] = unit["stats"]["hp"]
    return unit


def _add_enemy(
    pig: dict, key_level: int, label: str | None = None, affix_ids: set[str] | None = None
) -> dict:
    """Build a fixed-scaling ADD from a real pig-pen catalog entry."""
    unit = fighter(pig, entry_for(pig.get("battle")), 3, label or pig["name"], rarity="普通")
    hp_scale, atk_scale = enemy_scale(key_level)
    # An ADD is a genuine extra enemy, deliberately close to a full encounter:
    # one occurrence should consume most of a healthy timer's spare rounds.
    unit["stats"]["hp"] = max(1, round(unit["stats"]["hp"] * 2.1 * hp_scale))
    unit["stats"]["atk"] = max(1, round(unit["stats"]["atk"] * 0.8 * atk_scale))
    if "fortified" in (affix_ids or set()):
        unit["stats"]["hp"] = round(unit["stats"]["hp"] * 1.12)
        unit["stats"]["atk"] = round(unit["stats"]["atk"] * 1.08)
    unit["dot_basis"] = unit["stats"]["hp"]
    unit["start_hp"] = unit["stats"]["hp"]
    unit["seat"] = None
    unit["mods"] = {}
    return unit


def _boss_enemy(
    pig: dict, stage: int, slot: str, key_level: int, affix_ids: set[str]
) -> dict:
    unit = boss_fighter(
        pig,
        entry_for(pig.get("battle")),
        stage,
        slot,
        pig["name"],
        # Mythic difficulty is fixed by dungeon and key level.  It must not
        # silently compensate for the party's level bracket.
        party_levels=None,
    )
    hp_scale, atk_scale = enemy_scale(key_level)
    unit["stats"]["hp"] = max(1, round(unit["stats"]["hp"] * hp_scale))
    unit["stats"]["atk"] = max(1, round(unit["stats"]["atk"] * atk_scale))
    if "tyrannical" in affix_ids:
        unit["stats"]["hp"] = round(unit["stats"]["hp"] * 1.12)
        unit["stats"]["atk"] = round(unit["stats"]["atk"] * 1.08)
    unit["dot_basis"] = unit["stats"]["hp"]
    return unit


@dataclass
class _State:
    hp: float = 1.0
    final_hp: float = 1.0
    min_hp: float = 1.0
    deaths: int = 0


def _add_schedule(seed: int, chance: float = ADD_RUN_CHANCE) -> set[int]:
    """Pick zero, one, or two distinct route phases for this entire run."""
    rng = random.Random(seed ^ 0xADD5EED)
    if chance <= 0 or rng.random() * 100 >= min(100.0, chance):
        return set()
    count = 2 if rng.random() * 100 < ADD_DOUBLE_CHANCE else 1
    return set(rng.sample(range(1, 10), count))


def simulate_mythic(
    members: list[dict],
    catalog: dict[str, dict],
    dungeon_key: int,
    key_level: int,
    seed: int,
    *,
    level_cap: int = 20,
    add_chance: float = ADD_RUN_CHANCE,
) -> dict:
    """Run all six trash packs and three bosses and return a serialisable report."""
    if len(members) != 4:
        raise ValueError("A mythic dungeon requires exactly four members")
    info = dungeon_info(dungeon_key)
    levels = [level_for(member["count"], level_cap) for member in members]
    if min(levels) < MIN_LEVEL:
        raise ValueError("Every mythic-dungeon pig must be at least level 5")

    states = {member["seat"]: _State() for member in members}
    totals = {
        member["seat"]: {"dealt": 0, "healed": 0, "taken": 0}
        for member in members
    }
    phases = []
    total_rounds = 0
    forces = 0
    trash_index = 0
    boss_index = 0
    completed = True
    limit = MYTHIC_LIMITS[dungeon_key]
    affixes = affixes_for(dungeon_key, key_level, seed)
    affix_ids = {affix["id"] for affix in affixes}
    blessing = rookie_blessing(levels, key_level)
    add_schedule = _add_schedule(seed, add_chance)
    trash_roster = _dungeon_trash_roster(catalog, dungeon_key)
    add_rng = random.Random(seed ^ 0xA11D)
    add_squads = {phase: add_rng.choice(trash_roster) for phase in sorted(add_schedule)}

    base_sequence = list(enumerate(("trash", "trash", "boss") * 3, 1))
    sequence = []
    for base_index, kind in base_sequence:
        sequence.append((kind, base_index, False))
        if base_index in add_schedule and kind == "trash":
            sequence.append(("add", base_index, True))
    for phase_index, (kind, base_index, extra_wave) in enumerate(sequence, 1):
        heroes = []
        for member, level in zip(members, levels):
            hero = fighter(
                member["pig"],
                entry_for(member["pig"].get("battle")),
                level,
                member["label"],
                personality=member.get("personality"),
            )
            if blessing:
                for stat in ("hp", "atk", "def"):
                    hero["stats"][stat] = max(
                        1,
                        round(hero["stats"][stat] * (1 + blessing[stat] / 100)),
                    )
            hero.update(
                seat=member["seat"],
                start_hp=max(1, round(hero["stats"]["hp"] * states[member["seat"]].hp)),
                mods={},
            )
            heroes.append(hero)

        if kind == "trash":
            squad = trash_roster[trash_index]
            enemy_units = [
                _trash_enemy(pig, key_level, trash_index, affix_ids) for pig in squad
            ]
            pig, enemy = squad[0], enemy_units[0]
            starting_foes = enemy_units[1:]
            name = "、".join(pig["name"] for pig in squad)
            slot = "mythic-trash"
            stage = trash_index // 2 + 1
            force = FORCES[trash_index]
            trash_index += 1
        elif kind == "boss":
            slot = info["bosses"][boss_index]
            stage = boss_index + 1
            pig = catalog[slot]
            squad = [pig]
            enemy = _boss_enemy(pig, stage, slot, key_level, affix_ids)
            starting_foes = []
            name = enemy["label"]
            force = 0
            boss_index += 1
        else:
            squad = add_squads[base_index]
            enemy_units = [
                _add_enemy(pig, key_level, f"ADD小怪·{pig['name']}", affix_ids)
                for pig in squad
            ]
            pig, enemy = squad[0], enemy_units[0]
            starting_foes = enemy_units[1:]
            name = "、".join(pig["name"] for pig in squad)
            slot = "mythic-trash"
            stage = (base_index - 1) // 3 + 1
            force = 0

        scheduled_here = base_index in add_schedule and not extra_wave
        add_units = []
        if scheduled_here and kind == "boss":
            add_units = [
                _add_enemy(pig, key_level, f"ADD小怪·{pig['name']}", affix_ids)
                for pig in add_squads[base_index]
            ]
        scheduled_squad = add_squads.get(base_index, []) if scheduled_here else []
        add_pig_rows = [
            {"id": pig["id"], "name": pig["name"], "asset": pig.get("asset", "")}
            for pig in scheduled_squad
        ]
        cfg = {
            "team_mods": {},
            "boss_mods": {},
            "unit_mods": {},
            "starting_foes": starting_foes,
            "add_names": (("、".join(pig["name"] for pig in scheduled_squad),) if scheduled_here else ()),
            "add_budget": 1 if scheduled_here else 0,
            "add_trigger_action": 1,
            "add_wave_hp": (
                sum(
                    _add_enemy(pig, key_level, affix_ids=affix_ids)["stats"]["hp"]
                    for pig in scheduled_squad
                )
                if scheduled_here else 0
            ),
            "add_units": add_units,
            "add_pigs": add_pig_rows,
            "mythic_affixes": [affix["id"] for affix in affixes],
            "mythic_affix_details": affixes,
            "mythic_blessing": blessing,
            "require_all_foes": kind != "boss" or bool(add_units),
            "disable_ultimate": kind != "boss",
        }
        result = simulate_raid(heroes, enemy, slot, cfg, seed + phase_index * 7919)
        total_rounds += result["rounds"]
        phase_deaths = 0
        for state_row in result["heroes"]:
            if state_row["seat"] is None:
                continue
            seat = state_row["seat"]
            for field in ("dealt", "healed", "taken"):
                totals[seat][field] += state_row.get(field, 0)
            falls = state_row.get("falls", 0 if state_row["alive"] else 1)
            states[seat].deaths += falls
            phase_deaths += falls
            states[seat].final_hp = (
                state_row["hp"] / max(1, state_row["max_hp"]) if state_row["alive"] else 0.0
            )
            states[seat].min_hp = min(
                states[seat].min_hp,
                state_row.get("min_hp", state_row["hp"]) / max(1, state_row["max_hp"]),
            )
            if state_row["alive"]:
                ratio = states[seat].final_hp
                states[seat].hp = min(1.0, ratio + (0.15 if kind == "boss" else 0.08))
            else:
                states[seat].hp = 0.25
        if result["won"]:
            forces += force
        else:
            completed = False
        phases.append(
            {
                "index": phase_index,
                "kind": kind,
                "name": name,
                "stage": stage,
                "rounds": result["rounds"],
                "won": result["won"],
                "force": force if result["won"] else 0,
                "deaths": phase_deaths,
                "enemy": {
                    "id": pig["id"],
                    "name": pig["name"],
                    "asset": pig.get("asset", ""),
                },
                "enemies": [
                    {"id": enemy_pig["id"], "name": enemy_pig["name"], "asset": enemy_pig.get("asset", "")}
                    for enemy_pig in squad
                ],
                "adds": result.get("add_events", []),
                "log": result["log"],
            }
        )
        if not completed or total_rounds > limit + OVERTIME_GRACE:
            completed = False
            break

    deaths = sum(state.deaths for state in states.values())
    adjusted_rounds = total_rounds + deaths * DEATH_PENALTY
    upgrade = timed_upgrade(adjusted_rounds, limit, completed and forces == 100)
    return {
        "dungeon": info,
        "key_level": key_level,
        "affixes": affixes,
        "blessing": blessing,
        "limit": limit,
        "combat_rounds": total_rounds,
        "death_penalty": deaths * DEATH_PENALTY,
        "rounds": adjusted_rounds,
        "deaths": deaths,
        "forces": forces,
        "completed": completed and forces == 100,
        "timed": completed and forces == 100 and adjusted_rounds <= limit,
        "upgrade": upgrade,
        "score": score_for(key_level, upgrade, completed and forces == 100),
        "add_count": sum(len(phase["adds"]) for phase in phases),
        "phases": phases,
        "members": [
            {
                "seat": member["seat"],
                "user": member["user"],
                "pig": {k: v for k, v in member["pig"].items() if k != "battle"},
                "level": level,
                "role": entry_for(member["pig"].get("battle")).get("role", "输出"),
                "style": entry_for(member["pig"].get("battle")).get("style", ""),
                "deaths": states[member["seat"]].deaths,
                "hp_ratio": states[member["seat"]].final_hp,
                "min_hp_ratio": states[member["seat"]].min_hp,
                **totals[member["seat"]],
            }
            for member, level in zip(members, levels)
        ],
    }
