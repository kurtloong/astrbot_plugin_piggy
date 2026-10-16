import random
import unittest

from core.battle import _Battle, _Unit


def skill(name, effects, *, cd=0, role_trigger=None, cost=None):
    item = {
        "name": name,
        "text": "战术测试",
        "cd": cd,
        "when": role_trigger,
        "passive": False,
        "effects": effects,
    }
    if cost is not None:
        item["cost"] = cost
    return item


def unit_data(label, role, skills):
    return {
        "pig_id": label,
        "name": label,
        "label": label,
        "role": role,
        "style": "测试",
        "stats": {"hp": 100, "atk": 20, "def": 10, "spd": 10, "crit": 0, "dodge": 0},
        "skills": skills,
    }


BASIC = skill("普攻", [{"type": "damage", "power": 1.0}])


class PartyBattle(_Battle):
    def __init__(self, heroes):
        boss = unit_data("木桩", "输出", [BASIC])
        super().__init__(heroes[0], boss, 7)
        self.heroes = [self.units[0], *[_Unit(data) for data in heroes[1:]]]
        self.boss = self.units[1]
        self.rng = random.Random(7)

    def allies_of(self, unit):
        return self.heroes if unit in self.heroes else [self.boss]

    def foes_of(self, unit):
        return [self.boss] if unit in self.heroes else [hero for hero in self.heroes if hero.alive]


class TacticalAITests(unittest.TestCase):
    def test_cleanse_and_dispel_are_not_cast_without_a_real_target(self):
        support = unit_data(
            "辅助",
            "辅助",
            [
                BASIC,
                skill("团队净化", [{"type": "cleanse", "target": "ally_all"}], cd=3),
                skill("驱散", [{"type": "dispel"}], cd=3),
                skill("压制", [{"type": "damage", "power": 1.2}], cd=1),
            ],
        )
        ally = unit_data("队友", "输出", [BASIC])
        fight = PartyBattle([support, ally])
        caster, teammate = fight.heroes

        self.assertEqual(fight.choose(caster, fight.boss)["name"], "压制")
        teammate.dots.append({"label": "毒", "pct": 5, "turns": 3, "stacks": 1})
        self.assertEqual(fight.choose(caster, fight.boss)["name"], "团队净化")

        teammate.dots.clear()
        fight.boss.shield = 30
        self.assertEqual(fight.choose(caster, fight.boss)["name"], "驱散")

    def test_tank_does_not_repeat_shield_but_protects_a_wounded_ally(self):
        tank = unit_data(
            "坦克",
            "坦克",
            [
                BASIC,
                skill(
                    "护住后排",
                    [{"type": "shield", "pct": 30, "target": "ally_lowest"}],
                    cd=3,
                ),
                skill("反击", [{"type": "damage", "power": 1.3}], cd=2),
            ],
        )
        ally = unit_data("后排", "输出", [BASIC])
        fight = PartyBattle([tank, ally])
        caster, teammate = fight.heroes
        teammate.hp = teammate.max_hp * 0.9
        teammate.shield = teammate.max_hp * 0.25
        self.assertEqual(fight.choose(caster, fight.boss)["name"], "反击")

        teammate.shield = 0
        teammate.hp = teammate.max_hp * 0.3
        self.assertEqual(fight.choose(caster, fight.boss)["name"], "护住后排")

    def test_healer_prioritizes_the_dangerous_health_line_without_overhealing(self):
        healer = unit_data(
            "治疗",
            "治疗",
            [
                BASIC,
                skill("急救", [{"type": "triage", "pct": 35, "bonus": 15, "threshold": 40}], cd=2),
                skill("圣光打击", [{"type": "damage", "power": 1.5}], cd=2),
            ],
        )
        ally = unit_data("伤员", "输出", [BASIC])
        fight = PartyBattle([healer, ally])
        caster, teammate = fight.heroes
        teammate.hp = 28
        self.assertEqual(fight.choose(caster, fight.boss)["name"], "急救")

        teammate.hp = teammate.max_hp
        self.assertEqual(fight.choose(caster, fight.boss)["name"], "圣光打击")

    def test_full_health_group_heal_and_redundant_control_are_saved(self):
        healer = unit_data(
            "治疗",
            "治疗",
            [
                BASIC,
                skill("群体治疗", [{"type": "mass_heal", "pct": 25}], cd=3),
                skill("镇静", [{"type": "stun", "turns": 2}], cd=3),
                skill("轻击", [{"type": "damage", "power": 1.1}], cd=1),
            ],
        )
        ally = unit_data("满血队友", "辅助", [BASIC])
        fight = PartyBattle([healer, ally])
        caster = fight.heroes[0]
        fight.boss.stun = 2
        self.assertEqual(fight.choose(caster, fight.boss)["name"], "轻击")

        fight.heroes[1].hp = 25
        self.assertEqual(fight.choose(caster, fight.boss)["name"], "群体治疗")

    def test_distinct_delayed_attacks_remain_usable_but_energy_is_not_wasted(self):
        support = unit_data(
            "辅助",
            "辅助",
            [
                BASIC,
                skill(
                    "短缓存",
                    [{"type": "delay_damage", "pct": 8, "turns": 1, "label": "短缓存"}],
                    cd=2,
                ),
                skill(
                    "长缓存",
                    [{"type": "delay_damage", "pct": 12, "turns": 3, "label": "长缓存"}],
                    cd=3,
                ),
                skill("空补能", [{"type": "energy", "gain": 20, "target": "ally_all"}], cd=4),
            ],
        )
        ally = unit_data("无能量队友", "坦克", [BASIC])
        fight = PartyBattle([support, ally])
        caster = fight.heroes[0]
        fight.boss.delay_damages.append({"pct": 8, "turns": 1, "label": "短缓存"})

        self.assertEqual(fight.choose(caster, fight.boss)["name"], "长缓存")

    def test_dps_spends_burst_in_an_active_window_and_holds_one_round_before_it(self):
        dps = unit_data(
            "输出",
            "输出",
            [
                BASIC,
                skill("终结炮", [{"type": "damage", "power": 2.8}], cd=4),
            ],
        )
        fight = PartyBattle([dps])
        caster = fight.heroes[0]
        fight.boss.break_in = 1
        self.assertIs(fight.choose(caster, fight.boss), caster.actives[0])

        fight.boss.break_in = 0
        fight.boss.break_window = True
        self.assertEqual(fight.choose(caster, fight.boss)["name"], "终结炮")

    def test_skill_chain_applies_setup_before_damage_and_emergency_defense_before_attack(self):
        support = unit_data(
            "辅助",
            "辅助",
            [BASIC, skill("破绽", [{"type": "expose", "pct": 25, "turns": 2}], cd=2)],
        )
        fight = PartyBattle([support])
        self.assertEqual(
            [item["name"] for item in fight.plan_actions(fight.heroes[0], fight.boss)],
            ["破绽", "普攻"],
        )

        tank = unit_data(
            "坦克",
            "坦克",
            [
                BASIC,
                skill("紧急护盾", [{"type": "shield", "pct": 30, "target": "ally_lowest"}], cd=2),
            ],
        )
        ally = unit_data("濒危队友", "治疗", [BASIC])
        fight = PartyBattle([tank, ally])
        fight.heroes[1].hp = 20
        self.assertEqual(
            [item["name"] for item in fight.plan_actions(fight.heroes[0], fight.boss)],
            ["紧急护盾", "普攻"],
        )


if __name__ == "__main__":
    unittest.main()
