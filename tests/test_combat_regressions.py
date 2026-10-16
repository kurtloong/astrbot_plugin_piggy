import copy
import unittest
from unittest.mock import patch

from core.battle import FALLBACK, _Battle, fighter, validate_entry
from core.raid import ULTIMATE_ROUND, _RaidBattle, boss_fighter
from core.raid_events import new_config

ROLES = ("输出", "坦克", "治疗", "辅助")


def combatant(role: str, seat: int) -> dict:
    entry = copy.deepcopy(FALLBACK)
    entry["role"] = role
    entry["style"] = f"测试{role}"
    data = fighter(
        {"id": f"regression-{role}-{seat}", "name": role, "asset": ""},
        validate_entry(f"regression-{role}-{seat}", entry),
        5,
        role,
    )
    data["seat"] = seat
    return data


def raid(slot: str = "goblin-pig", seed: int = 7) -> _RaidBattle:
    heroes = [combatant(role, seat) for seat, role in enumerate(ROLES)]
    boss = boss_fighter(
        {"id": slot, "name": "测试 Boss", "asset": ""},
        validate_entry(f"regression-boss-{slot}", copy.deepcopy(FALLBACK)),
        1,
        slot,
        "测试 Boss",
    )
    return _RaidBattle(heroes, boss, slot, new_config(), seed)


def duel() -> _Battle:
    entry = validate_entry("regression-duel", copy.deepcopy(FALLBACK))
    left = fighter({"id": "left", "name": "left"}, entry, 5, "left")
    right = fighter({"id": "right", "name": "right"}, entry, 5, "right")
    return _Battle(left, right, 3)


class CombatRegressionTests(unittest.TestCase):
    def test_round_start_mutual_ko_is_decided_before_field_event(self):
        fight = raid()

        def mutual_ko(battle):
            battle.boss.hp = 0
            for hero in battle.heroes:
                hero.hp = 0

        fight.mech.round_start = mutual_ko
        with patch("core.raid.field_event") as event:
            result = fight.run()

        event.assert_not_called()
        self.assertEqual(result["winner"], 0)
        self.assertEqual(result["rounds"], 1)

    def test_lethal_hit_triggers_last_stand_and_keeps_one_percent_hp(self):
        fight = duel()
        unit = fight.units[0]
        unit.last_stand = {"threshold": 30, "pct": 80, "turns": 1}

        dealt = fight.hurt(unit, unit.max_hp * 2)

        self.assertTrue(unit.alive)
        self.assertTrue(unit.last_stand_used)
        self.assertEqual(unit.hp, unit.max_hp * 0.01)
        self.assertAlmostEqual(dealt, unit.max_hp * 0.99)
        self.assertEqual(unit.lock_hp, 1)

    def test_lethal_hit_still_runs_mandatory_boss_threshold_recovery(self):
        fight = raid("error-404-pig")
        fight.boss.hp = fight.boss.max_hp * 0.41

        fight.hurt(fight.boss, fight.boss.max_hp, attacker=fight.heroes[0])

        self.assertTrue(fight.boss.alive)
        self.assertTrue(fight.mech.rolled)
        self.assertEqual(fight.boss.hp, fight.boss.max_hp * 0.60)
        self.assertTrue(any("缓存回滚" in item[0] for item in fight.notes))

    def test_tank_ultimate_blocks_boss_on_hit_control_as_well_as_damage(self):
        fight = raid("frozen-pig")
        fight.round = ULTIMATE_ROUND
        fight.boss.base["atk"] = max(hero.max_hp for hero in fight.heroes) * 5
        self.assertTrue(fight._try_ultimate())
        self.assertEqual(fight.ultimate_role, "坦克")
        target = fight.heroes[0]
        target.chill = 2

        dealt = fight.hurt(target, target.max_hp, attacker=fight.boss)
        fight.after_hit(fight.boss, target, dealt)

        self.assertEqual(dealt, 0)
        self.assertEqual(target.chill, 2)
        self.assertEqual(target.stun, 0)

    def test_heal_ban_excludes_healer_ultimate_instead_of_spending_it_for_zero(self):
        fight = raid("pighub0007")
        fight.round = ULTIMATE_ROUND
        fight.mech.decree = "禁疗令"
        fight.mech.until = fight.round
        for hero in fight.heroes:
            hero.hp = hero.max_hp * 0.20

        self.assertTrue(fight._try_ultimate())
        self.assertTrue(fight.ultimate_used)
        self.assertNotEqual(fight.ultimate_role, "治疗")

    def test_dot_revive_skips_the_rest_of_the_current_turn(self):
        fight = duel()
        unit, enemy = fight.units
        fight.round = 1
        unit.hp = 1
        unit.revive = 50
        unit.dots.append({"pct": 20, "turns": 2, "label": "毒", "stacks": 1})
        before = enemy.hp

        fight.turn(unit, enemy)

        self.assertEqual(unit.hp, unit.max_hp * 0.50)
        self.assertEqual(enemy.hp, before)
        self.assertTrue(any("又站了起来" in line for line in fight.log))

        fight.round = 2
        fight.turn(unit, enemy)
        self.assertLess(enemy.hp, before)


if __name__ == "__main__":
    unittest.main()
