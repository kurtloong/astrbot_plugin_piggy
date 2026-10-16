import copy
import json
import random
import re
import unittest
from pathlib import Path

from core.battle import (
    FALLBACK,
    MAX_ROUNDS,
    SKILL_SLOTS,
    _Battle,
    describe_skill,
    entry_for,
    fighter,
    simulate,
    validate_battle,
    validate_entry,
)
from core.config import PiggyError
from core.rarity import MYTHIC_PIGS
from tools.boss_kits import BOSS_KITS, apply_boss_kits

RESOURCES = Path(__file__).resolve().parents[1] / "resources"


def editable_fallback():
    """Return the legacy fixture without an explicit engine tied to old names."""
    entry = copy.deepcopy(FALLBACK)
    entry.pop("engine", None)
    return entry


class BattleDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pigs = {p["id"]: p for p in json.loads((RESOURCES / "pigs.json").read_text("utf-8"))}
        cls.raw = json.loads((RESOURCES / "battle.json").read_text("utf-8"))
        cls.data = validate_battle(cls.raw, cls.pigs)

    def test_every_bundled_pig_has_five_natural_skills(self):
        self.assertEqual(set(self.data), set(self.pigs))
        for pig_id, entry in self.data.items():
            names = [skill["name"] for skill in entry["skills"]]
            self.assertEqual(len(names), SKILL_SLOTS, pig_id)
            self.assertEqual(len(set(names)), SKILL_SLOTS, pig_id)
            for skill in entry["skills"]:
                self.assertTrue(describe_skill(skill))
                if self.pigs[pig_id].get("enabled", True):
                    self.assertIsNone(
                        re.search(r"·-?[a-z0-9_]{2,}$", skill["name"], re.IGNORECASE),
                        (pig_id, skill["name"]),
                    )

    def test_every_enabled_pig_declares_a_linked_core_engine(self):
        engine_names = {}
        skeletons = {}
        for pig_id, pig in self.pigs.items():
            if not pig.get("enabled", True):
                continue
            raw = self.raw["pigs"][pig_id]
            self.assertIn("engine", raw, pig_id)
            engine = self.data[pig_id]["engine"]
            self.assertGreaterEqual(len(engine["skills"]), 3, pig_id)
            names = {skill["name"] for skill in self.data[pig_id]["skills"]}
            self.assertTrue(set(engine["skills"]) <= names, pig_id)
            self.assertNotIn(engine["name"], engine_names, (engine_names.get(engine["name"]), pig_id))
            engine_names[engine["name"]] = pig_id

            # Numeric tuning does not make two kits meaningfully different.  Treat
            # the trigger/effect/target/stat layout as the mechanical skeleton and
            # require a unique one for every enabled pig.
            skeleton = tuple(
                (
                    skill.get("when"),
                    bool(skill.get("passive")),
                    tuple(
                        (effect["type"], effect.get("target", "self"), effect.get("stat"))
                        for effect in skill["effects"]
                    ),
                )
                for skill in self.data[pig_id]["skills"]
            )
            self.assertNotIn(skeleton, skeletons, (skeletons.get(skeleton), pig_id))
            skeletons[skeleton] = pig_id

    def test_normalized_entries_validate_again_for_backup_restore(self):
        for pig_id, entry in self.data.items():
            self.assertEqual(validate_entry(pig_id, json.loads(json.dumps(entry))), entry)

    def test_boss_maintenance_keeps_ai_authored_engines_and_skills(self):
        pigs = copy.deepcopy(self.raw["pigs"])
        before = {
            pig_id: (copy.deepcopy(pigs[pig_id]["engine"]), copy.deepcopy(pigs[pig_id]["skills"]))
            for pig_id in BOSS_KITS
        }
        apply_boss_kits(pigs)
        for pig_id, (engine, skills) in before.items():
            self.assertEqual(pigs[pig_id]["engine"], engine, pig_id)
            self.assertEqual(pigs[pig_id]["skills"], skills, pig_id)

    def test_entries_expose_searchable_role_and_mechanic_tags(self):
        for pig_id, entry in self.data.items():
            self.assertTrue(2 <= len(entry["tags"]) <= 4, pig_id)
            self.assertEqual(entry["tags"][0], entry["role"], pig_id)
            self.assertEqual(len(entry["tags"]), len(set(entry["tags"])), pig_id)

        tagged = editable_fallback()
        tagged["role"] = "坦克"
        tagged["tags"] = ["坦克", "自回", "反击"]
        self.assertEqual(validate_entry("tagged", tagged)["tags"], tagged["tags"])
        tagged["tags"] = ["输出", "自回"]
        with self.assertRaises(PiggyError):
            validate_entry("bad-tags", tagged)

    def test_every_healer_has_a_low_power_zero_cooldown_healing_basic(self):
        allowed = {"heal", "mass_heal", "shield", "regen"}
        for pig_id, entry in self.data.items():
            if entry["role"] != "治疗":
                continue
            basics = [
                skill
                for skill in entry["skills"][1:]
                if not skill["passive"]
                and not skill["cd"]
                and not skill["when"]
                and {effect["type"] for effect in skill["effects"]} <= allowed
            ]
            self.assertEqual(len(basics), 1, pig_id)
            effect = basics[0]["effects"][0]
            if effect["type"] == "mass_heal":
                self.assertLessEqual(effect["pct"], 8, pig_id)
            elif effect["type"] == "regen":
                self.assertLessEqual(effect["pct"], 4, pig_id)
            else:
                self.assertLessEqual(effect["pct"], 12, pig_id)

    def test_level_scales_stats_and_unlocks_one_skill_per_level(self):
        pig, entry = self.pigs["pig-human"], self.data["pig-human"]
        units = {level: fighter(pig, entry, level) for level in (1, 3, 5, 9)}
        self.assertEqual([len(units[n]["skills"]) for n in (1, 3, 5, 9)], [1, 3, 5, 5])
        self.assertEqual(len(units[3]["locked"]), 2)
        self.assertGreater(units[9]["stats"]["hp"], units[5]["stats"]["hp"])
        self.assertGreater(units[5]["stats"]["atk"], units[1]["stats"]["atk"])
        self.assertEqual(units[9]["stats"]["crit"], units[1]["stats"]["crit"])

    def test_rarity_scales_base_growth_and_skill_numbers(self):
        pig, entry = self.pigs["pig-human"], self.data["pig-human"]
        common = fighter(pig, entry, 5, rarity="普通")
        mythic = fighter(pig, entry, 5, rarity="神话")
        self.assertGreater(mythic["stats"]["hp"], common["stats"]["hp"])
        self.assertGreater(mythic["stats"]["atk"], common["stats"]["atk"])
        common_power = common["skills"][0]["effects"][0]["power"]
        mythic_power = mythic["skills"][0]["effects"][0]["power"]
        self.assertGreater(mythic_power, common_power)
        self.assertEqual(mythic["rarity"], "神话")
        self.assertEqual(len(MYTHIC_PIGS), 4)

    def test_personality_is_a_sixth_combat_skill(self):
        pig, entry = self.pigs["pig-human"], self.data["pig-human"]
        unit = fighter(pig, entry, 5, personality="critical_overload")
        self.assertEqual(len(unit["skills"]), 6)
        self.assertEqual(unit["personality_name"], "会心过载")
        self.assertTrue(unit["skills"][-1]["passive"])

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

    def test_each_role_pool_has_multiple_functional_combat_profiles(self):
        # MMO roles intentionally do not share a cross-role 1v1 win-rate target.
        # Keep a compact same-role smoke sample to catch dead kits, deterministic
        # seat bias and a role collapsing into one cloned stat/skill profile.
        rng = random.Random(2026)
        for role in ("坦克", "输出", "治疗", "辅助"):
            pool = [pig_id for pig_id, entry in self.data.items() if entry["role"] == role]
            sample = rng.sample(pool, min(24, len(pool)))
            profiles = {
                (
                    tuple(entry["stats"].values()),
                    tuple(effect["type"] for skill in entry["skills"] for effect in skill["effects"]),
                )
                for pig_id in sample
                for entry in (self.data[pig_id],)
            }
            self.assertGreaterEqual(len(profiles), max(4, len(sample) // 2), role)
            winners = set()
            for index, (left, right) in enumerate(zip(sample, sample[1:] + sample[:1])):
                for swap in (False, True):
                    ids = (right, left) if swap else (left, right)
                    pair = [fighter(self.pigs[p], self.data[p], 5, p) for p in ids]
                    result = simulate(*pair, 20_260 + index * 2 + swap)
                    winners.add(pair[result["winner"]]["pig_id"])
                    self.assertLessEqual(result["rounds"], MAX_ROUNDS)
            self.assertGreaterEqual(len(winners), max(3, len(sample) // 5), role)

    def test_revive_and_copy_mechanics(self):
        base = editable_fallback()
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
        missing = editable_fallback()
        missing["skills"].pop()
        broken.append(missing)
        passive_first = editable_fallback()
        passive_first["skills"][0]["passive"] = True
        broken.append(passive_first)
        unknown = editable_fallback()
        unknown["skills"][2]["effects"] = [{"type": "nuke"}]
        broken.append(unknown)
        passive_damage = editable_fallback()
        passive_damage["skills"][1]["effects"] = [{"type": "damage", "power": 1}]
        broken.append(passive_damage)
        stats = editable_fallback()
        stats["stats"]["hp"] = 9999
        broken.append(stats)
        bad_role = editable_fallback()
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
        bare = editable_fallback()
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
            pack = editable_fallback()
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
        pack = editable_fallback()
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
        bomber = editable_fallback()
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
        healer = editable_fallback()
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

    def test_turn_chains_two_distinct_skills_with_immediate_energy_and_cooldown(self):
        pack = editable_fallback()
        pack["skills"][1] = {
            "name": "能量终结",
            "text": "普攻攒能后立刻衔接。",
            "cd": 2,
            "cost": 8,
            "effects": [{"type": "damage", "power": 2.0, "true": True, "sure": True}],
        }
        for index in range(2, 5):
            pack["skills"][index]["when"] = "on_dodge"
        pig = {"id": "chain", "name": "连招猪"}
        data = fighter(pig, validate_entry("chain", pack), 5, "连招猪")
        foe = fighter(pig, validate_entry("foe", FALLBACK), 1, "木桩")
        fight = _Battle(data, foe, 3)
        unit, enemy = fight.units

        actions = fight.plan_actions(unit, enemy)
        self.assertEqual([skill["name"] for skill in actions], ["猪突猛进", "能量终结"])
        fight.act(unit, enemy)
        self.assertEqual(unit.energy, 0)
        self.assertEqual(unit.cooldowns["能量终结"], 3)
        self.assertEqual(unit.used_skills, {"猪突猛进", "能量终结"})
        self.assertTrue(any("展开技能链" in line for line in fight.log))
        self.assertGreater(enemy.hp, enemy.max_hp - unit.stat("atk") * 3)

    def test_zero_cooldown_healing_basic_is_smart_and_once_per_turn(self):
        pack = editable_fallback()
        pack["role"] = "治疗"
        pack["skills"][1] = {
            "name": "护理普攻",
            "text": "有伤才治疗。",
            "effects": [{"type": "heal", "pct": 20}],
        }
        for index in range(2, 5):
            pack["skills"][index]["when"] = "on_dodge"
        pig = {"id": "healer", "name": "治疗猪"}
        data = fighter(pig, validate_entry("healer", pack), 5, "治疗猪")
        foe = fighter(pig, validate_entry("foe2", FALLBACK), 1, "木桩")
        fight = _Battle(data, foe, 4)
        unit, enemy = fight.units

        self.assertEqual(fight.plan_actions(unit, enemy), [unit.actives[0]])
        unit.hp = unit.max_hp / 2
        actions = fight.plan_actions(unit, enemy)
        self.assertEqual([s["name"] for s in actions], ["猪突猛进", "护理普攻"])
        before = unit.hp
        fight.act(unit, enemy)
        self.assertGreater(unit.hp, before)
        self.assertEqual(len(unit.used_skills), 2)
        # A second action in the same round cannot repeat either zero-CD basic.
        fight.act(unit, enemy)
        self.assertEqual(len(unit.used_skills), 2)

    def test_chain_flags_do_not_replay_a_stale_reaction_on_the_second_link(self):
        pig = {"id": "react", "name": "反应猪"}
        data = fighter(pig, validate_entry("react", FALLBACK), 5, "反应猪")
        basic = data["skills"][0]
        data["skills"] = [
            basic,
            {
                "name": "第二击",
                "text": "不会暴击。",
                "cd": 0,
                "when": None,
                "passive": False,
                "effects": [{"type": "damage", "power": 1.0, "sure": True}],
            },
            {
                "name": "暴击反应甲",
                "text": "第一次暴击触发。",
                "cd": 0,
                "when": "on_crit",
                "passive": False,
                "effects": [{"type": "heal", "pct": 5}],
            },
            {
                "name": "暴击反应乙",
                "text": "不能被旧暴击标志误触发。",
                "cd": 0,
                "when": "on_crit",
                "passive": False,
                "effects": [{"type": "heal", "pct": 5}],
            },
        ]
        foe = fighter(pig, validate_entry("react-foe", FALLBACK), 1, "木桩")
        fight = _Battle(data, foe, 2)
        rolls = iter((True, False))
        fight.crit_roll = lambda unit, enemy: next(rolls)

        fight.act(*fight.units)
        joined = "\n".join(fight.log)
        self.assertIn("暴击反应甲", joined)
        self.assertNotIn("暴击反应乙", joined)

    def test_chain_output_scale_covers_utility_and_stored_damage(self):
        pig = {"id": "scale", "name": "缩放猪"}
        a = fighter(pig, validate_entry("scale-a", FALLBACK), 5, "缩放猪")
        b = fighter(pig, validate_entry("scale-b", FALLBACK), 5, "木桩")
        fight = _Battle(a, b, 1)
        unit, enemy = fight.units
        fight._cast_output_scale = 0.5

        fight.effect(unit, enemy, {"type": "buff", "stat": "atk", "pct": 20}, {})
        fight.effect(unit, enemy, {"type": "mitigate", "pct": 30}, {})
        self.assertEqual(unit.buffs[-1]["pct"], 10)
        self.assertEqual(unit.mitigates[-1]["pct"], 15)

        enemy.dots.append({"pct": 10, "turns": 2, "label": "测试毒", "stacks": 1})
        expected = fight.dot_amount(enemy, enemy.dots[0])
        before = enemy.hp
        fight.effect(unit, enemy, {"type": "detonate"}, {})
        self.assertAlmostEqual(before - enemy.hp, expected)

        fight._cast_output_scale = 1.0
        enemy.last = {
            "name": "护盾样本",
            "text": "用于验证复制缩放。",
            "cd": 0,
            "when": None,
            "passive": False,
            "effects": [{"type": "shield", "pct": 20}],
        }
        unit.shield = 0
        fight.effect(unit, enemy, {"type": "copy", "pct": 50}, {})
        self.assertAlmostEqual(unit.shield, unit.max_hp * 0.10)


if __name__ == "__main__":
    unittest.main()
