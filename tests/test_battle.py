import copy
import json
import random
import unittest
from pathlib import Path

from core.battle import (
    FALLBACK,
    MAX_ROUNDS,
    SKILL_SLOTS,
    describe_skill,
    entry_for,
    fighter,
    simulate,
    validate_battle,
    validate_entry,
)
from core.config import PiggyError

RESOURCES = Path(__file__).resolve().parents[1] / "resources"


class BattleDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pigs = {p["id"]: p for p in json.loads((RESOURCES / "pigs.json").read_text("utf-8"))}
        cls.raw = json.loads((RESOURCES / "battle.json").read_text("utf-8"))
        cls.data = validate_battle(cls.raw, cls.pigs)

    def test_every_bundled_pig_has_five_unique_skills(self):
        self.assertEqual(set(self.data), set(self.pigs))
        names = [s["name"] for entry in self.data.values() for s in entry["skills"]]
        self.assertEqual(len(names), len(self.pigs) * SKILL_SLOTS)
        self.assertEqual(len(set(names)), len(names))
        for entry in self.data.values():
            for skill in entry["skills"]:
                self.assertTrue(describe_skill(skill))

    def test_normalized_entries_validate_again_for_backup_restore(self):
        for pig_id, entry in self.data.items():
            self.assertEqual(validate_entry(pig_id, json.loads(json.dumps(entry))), entry)

    def test_level_scales_stats_and_unlocks_one_skill_per_level(self):
        pig, entry = self.pigs["pig-human"], self.data["pig-human"]
        units = {level: fighter(pig, entry, level) for level in (1, 3, 5, 9)}
        self.assertEqual([len(units[n]["skills"]) for n in (1, 3, 5, 9)], [1, 3, 5, 5])
        self.assertEqual(len(units[3]["locked"]), 2)
        self.assertGreater(units[9]["stats"]["hp"], units[5]["stats"]["hp"])
        self.assertGreater(units[5]["stats"]["atk"], units[1]["stats"]["atk"])
        self.assertEqual(units[9]["stats"]["crit"], units[1]["stats"]["crit"])

    def test_simulation_is_reproducible_and_always_ends(self):
        ids = list(self.data)
        for index, (a, b) in enumerate(zip(ids, reversed(ids))):
            level = index % 7 + 1
            first = fighter(self.pigs[a], self.data[a], level, "红方")
            second = fighter(self.pigs[b], self.data[b], level + 1, "蓝方")
            result = simulate(first, second, index)
            self.assertIn(result["winner"], (0, 1))
            self.assertLessEqual(result["rounds"], MAX_ROUNDS)
            self.assertEqual(result, simulate(first, second, index))
            self.assertTrue(result["log"])

    def test_same_level_matchups_stay_reasonably_balanced(self):
        # Each pig meets a fixed sample of opponents from both sides; all pairs would be too slow.
        ids = sorted(self.data)
        rng = random.Random(2026)
        opponents = {p: rng.sample([o for o in ids if o != p], 30) for p in ids}
        for level in (1, 5):
            seed = 0
            for pig_id in ids:
                won = 0
                for other in opponents[pig_id]:
                    for swap in (False, True):
                        seed += 1
                        pair = [
                            fighter(self.pigs[p], self.data[p], level, p)
                            for p in ((other, pig_id) if swap else (pig_id, other))
                        ]
                        won += pair[simulate(*pair, seed)["winner"]]["pig_id"] == pig_id
                rate = won / 60
                self.assertTrue(0.15 < rate < 0.85, (level, pig_id, rate))

    def test_revive_and_copy_mechanics(self):
        base = copy.deepcopy(FALLBACK)
        undead = copy.deepcopy(base)
        undead["skills"][1] = {
            "name": "复活",
            "text": "测试",
            "passive": True,
            "effects": [{"type": "revive", "pct": 50}],
        }
        mimic = copy.deepcopy(base)
        mimic["skills"][2] = {
            "name": "复读",
            "text": "测试",
            "cd": 1,
            "effects": [{"type": "copy"}],
        }
        pig = {"id": "test", "name": "测试猪"}
        a = fighter(pig, validate_entry("a", undead), 5, "不死猪")
        b = fighter(pig, validate_entry("b", mimic), 5, "复读猪")
        logs = ["\n".join(simulate(a, b, seed)["log"]) for seed in range(40)]
        self.assertTrue(any("又站了起来" in log for log in logs))
        self.assertTrue(any("复制了" in log for log in logs))

    def test_invalid_battle_data_is_rejected_with_actionable_errors(self):
        broken = []
        missing = copy.deepcopy(FALLBACK)
        missing["skills"].pop()
        broken.append(missing)
        passive_first = copy.deepcopy(FALLBACK)
        passive_first["skills"][0]["passive"] = True
        broken.append(passive_first)
        unknown = copy.deepcopy(FALLBACK)
        unknown["skills"][2]["effects"] = [{"type": "nuke"}]
        broken.append(unknown)
        passive_damage = copy.deepcopy(FALLBACK)
        passive_damage["skills"][1]["effects"] = [{"type": "damage", "power": 1}]
        broken.append(passive_damage)
        stats = copy.deepcopy(FALLBACK)
        stats["stats"]["hp"] = 9999
        broken.append(stats)
        bad_role = copy.deepcopy(FALLBACK)
        bad_role["role"] = "法师"
        broken.append(bad_role)
        for entry in broken:
            with self.assertRaises(PiggyError):
                validate_entry("bad", entry)
        with self.assertRaises(PiggyError):
            validate_battle({"pigs": {"ghost": FALLBACK}}, ["pig"])
        self.assertEqual(entry_for("")["skills"][0]["name"], FALLBACK["skills"][0]["name"])

    def test_role_defaults_and_new_effect_primitives(self):
        pig = {"id": "test", "name": "测试猪"}
        bare = copy.deepcopy(FALLBACK)
        bare.pop("role", None)
        entry = validate_entry("bare", bare)
        self.assertEqual(entry["role"], "输出")
        self.assertEqual(fighter(pig, entry, 5)["role"], "输出")

        cases = [
            ("mitigate", {"type": "mitigate", "pct": 40, "turns": 3}),
            ("taunt", {"type": "taunt", "turns": 2}),
            ("share", {"type": "share", "pct": 40, "turns": 2}),
            ("rewind", {"type": "snapshot"}),
            ("charge", {"type": "charge", "turns": 1, "power": 2.0}),
            ("mark", {"type": "mark", "pct": 20, "turns": 3}),
            ("silence", {"type": "silence", "turns": 1}),
            ("weaken", {"type": "weaken", "pct": 30, "turns": 2}),
            ("expose", {"type": "expose", "pct": 25, "turns": 2}),
            ("triage", {"type": "triage", "pct": 25, "bonus": 10, "threshold": 40}),
            ("mass_heal", {"type": "mass_heal", "pct": 15}),
            ("delay_damage", {"type": "delay_damage", "pct": 10, "turns": 1, "label": "炸弹"}),
            ("immunity", {"type": "immunity", "kind": "stun", "turns": 2}),
            ("combo", {"type": "combo", "gain": 2, "max": 5}),
            ("energy", {"type": "energy", "gain": 20}),
        ]
        for name, effect in cases:
            pack = copy.deepcopy(FALLBACK)
            pack["role"] = "坦克"
            pack["skills"][2] = {
                "name": f"测{name}",
                "text": "测试",
                "cd": 1,
                "effects": [effect] if effect["type"] != "snapshot" else [effect, {"type": "rewind", "turns": 1}],
            }
            validated = validate_entry(name, pack)
            self.assertTrue(describe_skill(validated["skills"][2]))
            result = simulate(
                fighter(pig, validated, 5, "甲"),
                fighter(pig, validate_entry("fb", FALLBACK), 5, "乙"),
                hash(name) % 10_000,
            )
            self.assertIn(result["winner"], (0, 1))

    def test_pick_resolves_random_option_bundles(self):
        pig = {"id": "curator-pig", "name": "猪理人"}
        pack = copy.deepcopy(FALLBACK)
        pack["role"] = "辅助"
        pack["skills"][2] = {
            "name": "今日菜单·测试",
            "text": "随机做一道菜。",
            "cd": 1,
            "effects": [
                {
                    "type": "pick",
                    "options": [
                        {
                            "text": "红烧",
                            "weight": 1,
                            "effects": [{"type": "buff", "stat": "atk", "pct": 20, "turns": 2}],
                        },
                        {
                            "text": "清汤",
                            "weight": 1,
                            "effects": [{"type": "heal", "pct": 15}],
                        },
                    ],
                }
            ],
        }
        entry = validate_entry("pick-test", pack)
        logs = "\n".join(
            line
            for seed in range(20)
            for line in simulate(
                fighter(pig, entry, 5, "厨"),
                fighter(pig, validate_entry("fb", FALLBACK), 5, "客"),
                seed,
            )["log"]
        )
        self.assertTrue("抽到" in logs)
        self.assertTrue("红烧" in logs or "清汤" in logs)

    def test_detonate_and_ally_target_degrade_in_duel(self):
        pig = {"id": "test", "name": "测试猪"}
        bomber = copy.deepcopy(FALLBACK)
        bomber["role"] = "输出"
        bomber["skills"][2] = {
            "name": "挂毒",
            "text": "测试",
            "cd": 1,
            "effects": [{"type": "dot", "pct": 8, "turns": 3, "label": "毒", "stacks": 2}],
        }
        bomber["skills"][3] = {
            "name": "引爆",
            "text": "测试",
            "cd": 2,
            "effects": [{"type": "detonate"}],
        }
        healer = copy.deepcopy(FALLBACK)
        healer["role"] = "治疗"
        healer["skills"][2] = {
            "name": "群奶",
            "text": "测试",
            "cd": 2,
            "effects": [{"type": "heal", "pct": 20, "target": "ally_all"}],
        }
        a = fighter(pig, validate_entry("bomber", bomber), 5, "爆")
        b = fighter(pig, validate_entry("healer", healer), 5, "奶")
        logs = "\n".join(simulate(a, b, 7)["log"])
        self.assertTrue("毒" in logs or "引爆" in logs or "回复" in logs)


if __name__ == "__main__":
    unittest.main()
