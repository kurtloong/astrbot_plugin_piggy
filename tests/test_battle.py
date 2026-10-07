import copy
import itertools
import json
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
        ids = sorted(self.data)
        for level in (1, 5):
            wins = dict.fromkeys(ids, 0)
            seed = 0
            for a, b in itertools.combinations(ids, 2):
                for swap in (False, True):
                    seed += 1
                    pair = [
                        fighter(self.pigs[p], self.data[p], level, p)
                        for p in ((b, a) if swap else (a, b))
                    ]
                    wins[pair[simulate(*pair, seed)["winner"]]["pig_id"]] += 1
            games = 2 * (len(ids) - 1)
            for pig_id, won in wins.items():
                self.assertTrue(0.2 < won / games < 0.8, (level, pig_id, won / games))

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
        for entry in broken:
            with self.assertRaises(PiggyError):
                validate_entry("bad", entry)
        with self.assertRaises(PiggyError):
            validate_battle({"pigs": {"ghost": FALLBACK}}, ["pig"])
        self.assertEqual(entry_for("")["skills"][0]["name"], FALLBACK["skills"][0]["name"])


if __name__ == "__main__":
    unittest.main()
