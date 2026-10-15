"""Boss-fight win-rate replays: Lv5 tank / DPS / healer / support vs every dungeon."""

from __future__ import annotations

import json
import random
import unittest
from collections import defaultdict
from pathlib import Path

from core.battle import fighter, validate_entry
from core.raid import DUNGEONS, REST_HEAL, REST_REVIVE_HP, _RaidBattle, boss_fighter
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
        # Baseline party should clear most first bosses sometimes; finals may be near-zero.
        self.assertGreater(sum(stage1_rates) / len(stage1_rates), 0.05)
        # These three dungeons previously sat at pathological 2% / 5% / 90%
        # clear rates.  Keep distinct identities, but reject another hard wall or
        # near-free clear after future skill-data changes.
        for name in ("机械猪厂", "星际猪港", "熔岩猪窑"):
            rate = dungeon_clears[name] / SEEDS
            self.assertGreaterEqual(rate, 0.10, (name, rate))
            self.assertLessEqual(rate, 0.65, (name, rate))
        # Fresh-party difficulty should still move toward a harder final boss.
        for item in (DUNGEONS[1], DUNGEONS[5], DUNGEONS[6]):
            first = stage_wins[(item["name"], 1, item["bosses"][0])]
            final = stage_wins[(item["name"], 3, item["bosses"][2])]
            self.assertGreaterEqual(first, final, item["name"])


if __name__ == "__main__":
    unittest.main()
