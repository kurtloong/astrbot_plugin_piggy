import unittest

from core.battle import _Battle, _Unit


def skill(name="普攻", effects=None):
    return {
        "name": name,
        "text": "测试",
        "cd": 0,
        "when": None,
        "passive": False,
        "effects": effects or [{"type": "damage", "power": 1.0}],
    }


def unit_data(label):
    return {
        "pig_id": label,
        "name": label,
        "label": label,
        "role": "输出",
        "style": "测试",
        "stats": {"hp": 100, "atk": 20, "def": 10, "spd": 10, "crit": 0, "dodge": 0},
        "skills": [skill()],
    }


class PartyBattle(_Battle):
    def __init__(self):
        super().__init__(unit_data("施法者"), unit_data("敌人"), 7)
        self.ally = _Unit(unit_data("队友"))
        self.heroes = [self.units[0], self.ally]

    def allies_of(self, unit):
        return self.heroes if unit in self.heroes else [unit]


class EffectIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.fight = _Battle(unit_data("甲"), unit_data("乙"), 7)
        self.caster, self.target = self.fight.units

    def apply(self, effect):
        return self.fight.effect(self.caster, self.target, effect, skill())

    def test_all_immunity_blocks_every_extended_hostile_status(self):
        self.target.immunity["all"] = 3
        effects = [
            {"type": "root", "turns": 2},
            {"type": "silence", "turns": 2},
            {"type": "expose", "pct": 20, "turns": 2},
            {"type": "corrupting", "pct": 30, "turns": 2},
            {"type": "delay_damage", "pct": 20, "turns": 2},
        ]
        for effect in effects:
            self.apply(effect)

        self.assertEqual(self.target.root, 0)
        self.assertEqual(self.target.silence, 0)
        self.assertEqual(self.target.expose, [])
        self.assertEqual(self.target.corrupting, [])
        self.assertEqual(self.target.delay_damages, [])

    def test_specialist_immunities_cover_related_extended_statuses(self):
        self.target.immunity = {"stun": 3, "weaken": 3, "dot": 3}
        for effect in (
            {"type": "root", "turns": 2},
            {"type": "silence", "turns": 2},
            {"type": "expose", "pct": 20, "turns": 2},
            {"type": "corrupting", "pct": 30, "turns": 2},
            {"type": "delay_damage", "pct": 20, "turns": 2},
        ):
            self.apply(effect)
        self.assertEqual(
            (self.target.root, self.target.silence, self.target.expose,
             self.target.corrupting, self.target.delay_damages),
            (0, 0, [], [], []),
        )

    def test_ai_rejects_extended_statuses_blocked_by_immunity(self):
        self.target.immunity = {"stun": 3, "weaken": 3, "dot": 3}
        effects = (
            {"type": "dot", "pct": 5, "turns": 2},
            {"type": "root", "turns": 2},
            {"type": "silence", "turns": 2},
            {"type": "weaken", "pct": 20, "turns": 2},
            {"type": "expose", "pct": 20, "turns": 2},
            {"type": "corrupting", "pct": 30, "turns": 2},
            {"type": "delay_damage", "pct": 20, "turns": 2},
        )
        for effect in effects:
            with self.subTest(effect=effect["type"]):
                self.assertFalse(self.fight._effect_useful(self.caster, self.target, effect))

    def test_transfer_moves_all_cleanseable_debuffs_with_remaining_duration(self):
        self.caster.dots = [{"pct": 4, "turns": 2, "label": "毒", "source": self.target}]
        self.caster.delay_damages = [
            {"pct": 12, "turns": 3, "label": "炸弹", "source": self.target}
        ]
        self.caster.buffs = [
            {"stat": "atk", "pct": -15, "turns": 4},
            {"stat": "def", "pct": 10, "turns": 4},
        ]
        self.caster.weaken = [{"pct": 10, "turns": 2}]
        self.caster.expose = [{"pct": 20, "turns": 3}]
        self.caster.corrupting = [{"pct": 30, "turns": 4}]
        self.caster.marks = [{"pct": 8, "turns": 5, "stacks": 2}]
        self.caster.stun, self.caster.stun_label = 2, "冻结"
        self.caster.silence = 3
        self.caster.root = 4

        self.apply({"type": "transfer", "mode": "debuff"})

        self.assertEqual(self.caster.dots, [])
        self.assertEqual(self.caster.delay_damages, [])
        self.assertEqual([b["pct"] for b in self.caster.buffs], [10])
        self.assertEqual(self.caster.weaken, [])
        self.assertEqual(self.caster.expose, [])
        self.assertEqual(self.caster.corrupting, [])
        self.assertEqual(self.caster.marks, [])
        self.assertEqual((self.caster.stun, self.caster.silence, self.caster.root), (0, 0, 0))
        self.assertEqual(self.target.dots[0]["turns"], 2)
        self.assertIs(self.target.dots[0]["source"], self.caster)
        self.assertEqual(self.target.delay_damages[0]["turns"], 3)
        self.assertIs(self.target.delay_damages[0]["source"], self.caster)
        self.assertEqual(self.target.weaken[0]["turns"], 2)
        self.assertEqual(self.target.expose[0]["turns"], 3)
        self.assertEqual(self.target.corrupting[0]["turns"], 4)
        self.assertEqual(self.target.marks[0]["turns"], 5)
        self.assertEqual((self.target.stun, self.target.silence, self.target.root), (2, 3, 4))
        self.assertEqual(self.target.stun_label, "冻结")

    def test_purge_modes_separate_buffs_from_shield_payload(self):
        target = self.target
        target.buffs = [
            {"stat": "atk", "pct": 20, "turns": 3},
            {"stat": "atk", "pct": -10, "turns": 3},
        ]
        target.block_count, target.block_pct = 2, 60
        target.immunity = {"all": 2}
        target.regens = [{"pct": 5, "turns": 3}]
        target.auras = [{"stat": "atk", "pct": 10, "turns": 3}]
        target.shield, target.absorb_break = 40, 1.5

        self.assertTrue(self.fight._has_removable_benefit(target, "buffs"))
        self.assertTrue(self.fight._has_removable_benefit(target, "shield"))
        self.fight._purge(target, "buffs")
        self.assertEqual([b["pct"] for b in target.buffs], [-10])
        self.assertEqual(target.block_count, 0)
        self.assertEqual(target.block_pct, 100)
        self.assertEqual(target.immunity, {})
        self.assertEqual(target.regens, [])
        self.assertEqual(target.auras, [])
        self.assertEqual((target.shield, target.absorb_break), (40, 1.5))

        self.fight._purge(target, "shield")
        self.assertEqual((target.shield, target.absorb_break), (0, 0.0))

    def test_dispel_all_clears_buffs_and_absorb_shield_payload(self):
        self.target.block_count = 2
        self.target.immunity = {"all": 2}
        self.target.regens = [{"pct": 5, "turns": 3}]
        self.target.auras = [{"stat": "atk", "pct": 10, "turns": 3}]
        self.target.shield, self.target.absorb_break = 40, 2

        self.apply({"type": "dispel"})

        self.assertEqual(self.target.block_count, 0)
        self.assertEqual(self.target.immunity, {})
        self.assertEqual(self.target.regens, [])
        self.assertEqual(self.target.auras, [])
        self.assertEqual((self.target.shield, self.target.absorb_break), (0, 0.0))

    def test_delayed_heal_resolves_after_caster_dies(self):
        fight = PartyBattle()
        caster, ally = fight.heroes
        ally.hp = 40
        fight.effect(
            caster,
            fight.units[1],
            {"type": "delay_heal", "pct": 25, "turns": 1, "target": "ally_lowest"},
            skill(),
        )
        self.assertEqual(caster.delay_heals, [])
        self.assertEqual(len(ally.delay_heals), 1)
        caster.hp = 0

        fight._tick_delays(ally, fight.units[1])

        self.assertEqual(ally.hp, 65)
        self.assertEqual(ally.delay_heals, [])

    def test_corrupted_heal_does_not_create_phantom_overheal_shield(self):
        self.caster.hp = 90
        self.caster.corrupting = [{"pct": 50, "turns": 2}]
        healed, shield = self.fight.heal_with_options(
            self.caster, 20, {"overheal_shield": True}, source=self.caster
        )
        self.assertEqual((healed, shield), (10, 0))
        self.assertEqual((self.caster.hp, self.caster.shield), (90, 0))


if __name__ == "__main__":
    unittest.main()
