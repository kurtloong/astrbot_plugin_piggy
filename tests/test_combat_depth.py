import copy
import unittest

from core.battle import FALLBACK, fighter, validate_entry
from core.raid import (
    POISE_BREAK_DAMAGE,
    POISE_MAX,
    POISE_SECOND_CYCLE_FACTOR,
    ULTIMATE_ROUND,
    _RaidBattle,
    boss_fighter,
)
from core.raid_events import new_config

ROLES = ("输出", "坦克", "治疗", "辅助")
SLOT = "goblin-pig"


def active(name, effects, cd=2):
    return {
        "name": name,
        "text": name,
        "cd": cd,
        "when": None,
        "passive": False,
        "effects": effects,
    }


def make_unit(role, seat):
    entry = copy.deepcopy(FALLBACK)
    entry["role"] = role
    entry["style"] = f"测试{role}"
    data = fighter(
        {"id": f"depth-{seat}", "name": role, "asset": ""},
        validate_entry(f"depth-{role}-{seat}", entry),
        5,
        role,
    )
    data["seat"] = seat
    return data


def battle(seed=31):
    heroes = [make_unit(role, seat) for seat, role in enumerate(ROLES)]
    boss = boss_fighter(
        {"id": SLOT, "name": "韧性 Boss", "asset": ""},
        validate_entry("depth-boss", copy.deepcopy(FALLBACK)),
        1,
        SLOT,
        "韧性 Boss",
    )
    return _RaidBattle(heroes, boss, SLOT, new_config(), seed)


def by_role(fight, role):
    return next(hero for hero in fight.heroes if hero.role == role)


class CombatDepthTests(unittest.TestCase):
    def test_poise_is_queryable_and_basics_do_not_grind_it_down(self):
        fight = battle()
        dps = by_role(fight, "输出")
        self.assertEqual(fight.poise, fight.poise_max)
        fight.apply(dps, fight.boss, dps.actives[0]["effects"], dps.actives[0])
        self.assertEqual(fight.poise, fight.poise_max)

        result = battle().run()
        self.assertIn("poise", result)
        self.assertEqual(result["poise"]["max"], POISE_MAX)
        self.assertEqual(len(result["poise"]["contributions"]), 4)

    def test_each_role_earns_visible_poise_from_real_tactical_value(self):
        # DPS: only a real non-basic skill hit contributes.
        fight = battle()
        dps = by_role(fight, "输出")
        burst = active("测试爆发", [{"type": "damage", "power": 1.5, "true": True}])
        dps.actives.append(burst)
        fight.apply(dps, fight.boss, burst["effects"], burst)
        self.assertGreater(fight.poise_contributions[id(dps)], 0)

        # Tank: both setting up real protection and actually bearing enemy damage matter.
        fight = battle()
        tank = by_role(fight, "坦克")
        shield = active("团队护盾", [{"type": "shield", "pct": 15, "target": "ally_all"}])
        before = fight.poise
        fight.effect(tank, fight.boss, shield["effects"][0], shield)
        after_protection = fight.poise
        self.assertLess(after_protection, before)
        fight.hurt(tank, tank.max_hp * 0.2, attacker=fight.boss)
        self.assertLess(fight.poise, after_protection)
        self.assertTrue(any("坦克" in note[0] for note in fight.notes))

        # Healer: overheal is ignored; restoring missing HP earns value.
        fight = battle()
        healer = by_role(fight, "治疗")
        target = fight.heroes[0]
        target.hp *= 0.5
        heal = active("急救", [{"type": "heal", "pct": 30, "target": "ally_lowest"}])
        fight.effect(healer, fight.boss, heal["effects"][0], heal)
        self.assertGreater(fight.poise_contributions[id(healer)], 0)

        # Support: a landed debuff and a cleanse both have explicit contribution.
        fight = battle()
        support = by_role(fight, "辅助")
        weaken = active("削弱", [{"type": "weaken", "pct": 20, "turns": 2}])
        fight.effect(support, fight.boss, weaken["effects"][0], weaken)
        after_debuff = fight.poise
        victim = fight.heroes[0]
        victim.stun = 1
        cleanse = active("净化", [{"type": "cleanse", "target": "ally_all"}])
        fight.effect(support, fight.boss, cleanse["effects"][0], cleanse)
        self.assertLess(fight.poise, after_debuff)
        self.assertTrue(any("辅助" in note[0] for note in fight.notes))

    def test_break_window_boosts_damage_interrupts_once_then_recovers(self):
        fight = battle()
        fight.round = 3
        support = by_role(fight, "辅助")
        fight._reduce_poise(support, fight.poise_max, "测试协同")
        self.assertTrue(fight.poise_broken)
        self.assertEqual(fight.poise_breaks, 1)
        self.assertAlmostEqual(
            fight.adjust_hit(by_role(fight, "输出"), fight.boss, 100, {}),
            100 * POISE_BREAK_DAMAGE,
        )

        before = fight.boss.hp
        fight.act(fight.boss, fight.heroes[0])
        self.assertEqual(fight.boss.hp, before)
        self.assertFalse(fight._poise_interrupt_pending)
        self.assertTrue(any("行动被破防" in line for line in fight.log))

        fight.round = fight.poise_broken_until
        fight._recover_poise()
        self.assertFalse(fight.poise_broken)
        self.assertEqual(fight.poise, fight.poise_max)

    def test_second_cycle_has_diminishing_returns_and_cannot_chain_lock(self):
        fight = battle()
        support = by_role(fight, "辅助")
        fight.round = 1
        fight._reduce_poise(support, fight.poise_max, "第一次")
        fight.round = fight.poise_broken_until
        fight._recover_poise()

        fight.round += 1
        fight._reduce_poise(support, fight.poise_max, "衰减测试")
        self.assertEqual(
            fight.poise,
            fight.poise_max * (1 - POISE_SECOND_CYCLE_FACTOR),
        )
        fight._reduce_poise(support, fight.poise_max, "第二次")
        self.assertEqual(fight.poise_breaks, 2)
        fight.round = fight.poise_broken_until
        fight._recover_poise()
        self.assertTrue(fight.poise_locked)
        self.assertEqual(fight._reduce_poise(support, 999, "禁止连锁"), 0)
        self.assertEqual(fight.poise, fight.poise_max)

    def test_damage_dealer_holds_burst_then_prioritizes_it_in_break_window(self):
        fight = battle()
        dps = by_role(fight, "输出")
        basic = dps.actives[0]
        small = active("小技能", [{"type": "damage", "power": 1.0}], cd=1)
        burst = active("大爆发", [{"type": "damage", "power": 3.0}], cd=4)
        dps.actives = [basic, small, burst]
        fight.poise = 20
        # Keep the real burst for the imminent window, but allow a small
        # technique to help finish the break instead of making DPS idle.
        self.assertIs(fight.choose(dps, fight.boss), small)

        fight.poise = 0
        fight.poise_broken_until = fight.round + 1
        self.assertIs(fight.choose(dps, fight.boss), burst)

    def test_ultimate_does_not_create_free_poise(self):
        # Safe state chooses the DPS ultimate; its large hit never reduces resilience.
        fight = battle()
        fight.round = ULTIMATE_ROUND
        before = fight.poise
        self.assertTrue(fight._try_ultimate())
        self.assertEqual(fight.ultimate_role, "输出")
        self.assertEqual(fight.poise, before)

        # Tank invulnerability blocks all actual damage, so it earns no fake bearing value.
        fight = battle()
        fight.round = ULTIMATE_ROUND
        fight.boss.base["atk"] = max(hero.max_hp for hero in fight.heroes) * 5
        self.assertTrue(fight._try_ultimate())
        self.assertEqual(fight.ultimate_role, "坦克")
        tank = by_role(fight, "坦克")
        before = fight.poise
        self.assertEqual(fight.hurt(tank, 99999, attacker=fight.boss), 0)
        self.assertEqual(fight.poise, before)


if __name__ == "__main__":
    unittest.main()
