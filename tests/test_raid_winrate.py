"""Boss-fight win-rate replays: Lv5 tank / DPS / healer / support vs every dungeon."""

from __future__ import annotations

import json
import random
import unittest
from collections import defaultdict
from pathlib import Path

from core.battle import fighter, validate_entry
from core.raid import (
    DUNGEONS,
    REST_HEAL,
    REST_REVIVE_HP,
    STAGE_WIN_RATE_TARGETS,
    _RaidBattle,
    boss_fighter,
    mechanics_for,
)
from core.raid_events import new_config

ROOT = Path(__file__).resolve().parents[1]
BATTLE = json.loads((ROOT / "resources" / "battle.json").read_text())["pigs"]
BOSS_IDS = [slot for item in DUNGEONS for slot in item["bosses"]]

# Fixed iconic pigs — one of each role, always tested at level 5.
PARTY = (
    ("black-pig", "坦克"),
    ("pig", "输出"),
    ("heaven-pig", "治疗"),
    ("human", "辅助"),
)
LEVEL = 5
SEEDS = 40
COUNTER_SEEDS = 120
ALL_COUNTER_SEEDS = 40
MYTHIC_SEEDS = 50
MYTHIC_PARTY = ("big-lazy-pig", "wild-boar", "pig-bun", "pig-stamp")
ROLES = ("坦克", "输出", "治疗", "辅助")


def entry(pig_id: str) -> dict:
    return validate_entry(pig_id, BATTLE[pig_id])


def make_party(level: int = LEVEL) -> list[dict]:
    heroes = []
    for seat, (pig_id, role) in enumerate(PARTY):
        data = entry(pig_id)
        assert data["role"] == role, f"{pig_id} role drifted to {data['role']}"
        hero = fighter({"id": pig_id, "name": pig_id, "asset": ""}, data, level, pig_id)
        hero["seat"] = seat
        heroes.append(hero)
    return heroes


def make_zombie_party(healer_id: str) -> list[dict]:
    """Keep rarity and the other three seats fixed so only counterplay changes."""
    party = (
        ("black-pig", "坦克"),
        ("pig", "输出"),
        (healer_id, "治疗"),
        ("human", "辅助"),
    )
    heroes = []
    for seat, (pig_id, role) in enumerate(party):
        data = entry(pig_id)
        assert data["role"] == role, f"{pig_id} role drifted to {data['role']}"
        hero = fighter(
            {"id": pig_id, "name": pig_id, "asset": ""},
            data,
            LEVEL,
            pig_id,
            rarity="普通",
        )
        hero["seat"] = seat
        heroes.append(hero)
    return heroes


def make_fixed_party(pig_ids: tuple[str, ...], rarity: str) -> list[dict]:
    heroes = []
    for seat, pig_id in enumerate(pig_ids):
        data = entry(pig_id)
        hero = fighter(
            {"id": pig_id, "name": pig_id, "asset": ""},
            data,
            LEVEL,
            pig_id,
            rarity=rarity,
        )
        hero["seat"] = seat
        heroes.append(hero)
    return heroes


def strongest_party_without(tags: tuple[str, ...]) -> tuple[str, ...]:
    """A deterministic, high-stat ordinary party that lacks all requested tags."""

    def score(data: dict) -> float:
        stats = data["stats"]
        return stats["hp"] + stats["atk"] * 5 + stats["def"] * 3 + stats["spd"] * 2

    blocked = set(tags)
    result = []
    for role in ROLES:
        candidates = [
            (score(data), pig_id)
            for pig_id, data in BATTLE.items()
            if data.get("role") == role and not blocked.intersection(data.get("tags", ()))
        ]
        result.append(max(candidates)[1])
    return tuple(result)


def make_boss(slot: str, stage: int) -> dict:
    return boss_fighter(
        {"id": slot, "name": slot, "asset": ""},
        entry(slot),
        stage,
        slot,
        slot,
    )


def simulate_with_hp(heroes: list[dict], slot: str, stage: int, seed: int) -> dict:
    """Run a raid fight, honouring optional `_start_hp` carried between stages."""
    battle = _RaidBattle(heroes, make_boss(slot, stage), slot, new_config(), seed)
    for unit, hero in zip(battle.heroes, heroes):
        if "_start_hp" in hero:
            unit.hp = min(float(hero["_start_hp"]), unit.max_hp)
    return battle.run()


def survivors(heroes: list[dict], result: dict, rng=None) -> list[dict]:
    """Carry survivors with rest heal; randomly revive one fallen pig like continue_raid."""
    rng = rng or random.Random(0)
    by_seat = {row["seat"]: row for row in result["heroes"]}
    next_heroes = []
    fallen = []
    for hero in heroes:
        row = by_seat[hero["seat"]]
        if row["alive"]:
            carried = dict(hero)
            carried["stats"] = dict(hero["stats"])
            carried["_start_hp"] = min(row["max_hp"], row["hp"] + round(row["max_hp"] * REST_HEAL))
            next_heroes.append(carried)
        else:
            fallen.append(hero)
    if fallen:
        pick = rng.choice(fallen)
        carried = dict(pick)
        carried["stats"] = dict(pick["stats"])
        max_hp = by_seat[pick["seat"]]["max_hp"]
        carried["_start_hp"] = max(1, round(max_hp * REST_REVIVE_HP))
        next_heroes.append(carried)
        next_heroes.sort(key=lambda h: h["seat"])
    return next_heroes


class BossKitTests(unittest.TestCase):
    def test_every_boss_has_tank_buster_and_aoe(self):
        for pig_id in BOSS_IDS:
            data = entry(pig_id)
            powers, cleaves = [], []
            for skill in data["skills"]:
                for effect in skill["effects"]:
                    self._collect(effect, powers, cleaves)
            self.assertGreaterEqual(max(powers), 2.5, pig_id)
            self.assertTrue(cleaves, pig_id)

    def _collect(self, effect, powers, cleaves):
        if effect["type"] == "damage":
            powers.append(effect.get("power", 0))
            if effect.get("cleave"):
                cleaves.append(effect["cleave"])
        elif effect["type"] == "pick":
            for opt in effect["options"]:
                for nested in opt["effects"]:
                    self._collect(nested, powers, cleaves)


class RaidWinrateTests(unittest.TestCase):
    """Replay many seeds; print rates. Soft floor so CI catches total collapse only."""

    def test_stage_and_dungeon_win_rates_level_5_party(self):
        stage_wins = defaultdict(int)
        dungeon_clears = defaultdict(int)

        # Independent stage fights (fresh party each time).
        for item in DUNGEONS:
            for stage, slot in enumerate(item["bosses"], 1):
                key = (item["name"], stage, slot)
                for seed in range(SEEDS):
                    result = simulate_with_hp(make_party(), slot, stage, seed * 1009 + stage * 17)
                    if result["won"]:
                        stage_wins[key] += 1

        # Chained dungeon clears (rest heal between stages).
        for item in DUNGEONS:
            for seed in range(SEEDS):
                heroes = make_party()
                cleared = True
                for stage, slot in enumerate(item["bosses"], 1):
                    result = simulate_with_hp(heroes, slot, stage, seed * 7919 + stage)
                    if not result["won"]:
                        cleared = False
                        break
                    heroes = survivors(heroes, result, rng=random.Random(seed))
                    if not heroes:
                        cleared = False
                        break
                if cleared:
                    dungeon_clears[item["name"]] += 1

        lines = ["", "=== Lv5 四职业 副本胜率 (坦克/输出/治疗/辅助) ==="]
        stage1_rates = []
        for item in DUNGEONS:
            parts = []
            for stage, slot in enumerate(item["bosses"], 1):
                key = (item["name"], stage, slot)
                rate = stage_wins[key] / SEEDS
                if stage == 1:
                    stage1_rates.append(rate)
                parts.append(f"G{stage} {slot} {rate:.0%}")
            clear = dungeon_clears[item["name"]] / SEEDS
            lines.append(
                f"{item['name']}: {' | '.join(parts)} | 通关 {clear:.0%} "
                f"({dungeon_clears[item['name']]}/{SEEDS})"
            )
        print("\n".join(lines))
        self.assertEqual(STAGE_WIN_RATE_TARGETS, (0.90, 0.80, 0.60))
        for item in DUNGEONS:
            for stage, slot in enumerate(item["bosses"], 1):
                actual = stage_wins[(item["name"], stage, slot)] / SEEDS
                target = STAGE_WIN_RATE_TARGETS[stage - 1]
                self.assertAlmostEqual(actual, target, delta=0.15, msg=slot)
        # Baseline party should clear most first bosses sometimes; finals may be near-zero.
        self.assertGreater(sum(stage1_rates) / len(stage1_rates), 0.05)
        # Keep distinct identities, but reject another hard wall or guaranteed
        # clear after the intended difficulty reduction.
        for name in ("机械猪厂", "星际猪港", "熔岩猪窑"):
            rate = dungeon_clears[name] / SEEDS
            self.assertGreaterEqual(rate, 0.05, (name, rate))
            self.assertLessEqual(rate, 0.90, (name, rate))

    def test_zombie_dedicated_cleanse_still_beats_the_once_per_fight_ultimate(self):
        def win_rate(healer_id: str) -> float:
            wins = 0
            for seed in range(COUNTER_SEEDS):
                result = simulate_with_hp(
                    make_zombie_party(healer_id), "zombie-pig", 1, seed * 1009 + 17
                )
                wins += bool(result["won"])
            return wins / COUNTER_SEEDS

        cleanse_rate = win_rate("antibacterial-pig")
        no_cleanse_rate = win_rate("pig-bun")
        print(
            f"\n=== 僵尸猪构筑检查 ===\n专职净化 {cleanse_rate:.1%} | 无净化 {no_cleanse_rate:.1%}"
        )
        self.assertGreaterEqual(cleanse_rate, 0.80)
        # A balanced four-role party now has one support ultimate cleanse from
        # round five onward, so the non-cleanse healer is no longer a party
        # with zero answers.  Dedicated cleanse should nevertheless remain
        # the safer, repeatable counter to a 99-turn stacking poison.
        self.assertLess(no_cleanse_rate, 0.95)
        self.assertGreaterEqual(cleanse_rate - no_cleanse_rate, 0.08)

    def test_every_boss_punishes_an_ordinary_party_missing_its_core_function(self):
        rates = {}
        for item in DUNGEONS:
            for stage, slot in enumerate(item["bosses"], 1):
                tags = mechanics_for(slot).CORE_TAGS
                pig_ids = strongest_party_without(tags)
                wins = 0
                for seed in range(ALL_COUNTER_SEEDS):
                    result = simulate_with_hp(
                        make_fixed_party(pig_ids, "普通"), slot, stage, seed * 1009 + stage * 17
                    )
                    wins += bool(result["won"])
                rates[slot] = wins / ALL_COUNTER_SEEDS
        # Exact four-role parties have a once-per-boss ultimate answer even
        # when no normal skill carries the requested tag: support supplies one
        # cleanse for Zombie/Frozen, while DPS supplies one reliable burst for
        # Error404. Dedicated counters remain safer and repeatable.
        ultimate_counters = {"zombie-pig", "frozen-pig", "error-404-pig"}
        failures = {
            slot: rate
            for slot, rate in rates.items()
            if slot not in ultimate_counters and rate >= 0.50
        }
        self.assertFalse(failures, failures)

    def test_all_mythic_reference_party_remains_well_above_the_ordinary_benchmark(self):
        clears = 0
        attempts = 0
        for item in DUNGEONS:
            for seed in range(MYTHIC_SEEDS):
                attempts += 1
                heroes = make_fixed_party(MYTHIC_PARTY, "神话")
                cleared = True
                for stage, slot in enumerate(item["bosses"], 1):
                    result = simulate_with_hp(heroes, slot, stage, seed * 7919 + stage)
                    if not result["won"]:
                        cleared = False
                        break
                    heroes = survivors(heroes, result, random.Random(seed))
                clears += cleared
        rate = clears / attempts
        print(f"\n=== 神话基准队完整副本 ===\n{clears}/{attempts} = {rate:.1%}")
        self.assertGreaterEqual(rate, 0.74)
        self.assertLessEqual(rate, 0.84)


if __name__ == "__main__":
    unittest.main()
