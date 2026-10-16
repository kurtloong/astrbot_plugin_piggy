import copy
import unittest

from core.battle import FALLBACK, fighter, validate_entry
from core.raid import ULTIMATE_ROUND, _RaidBattle, boss_fighter
from core.raid_events import new_config

ROLES = ("输出", "坦克", "治疗", "辅助")
SLOT = "goblin-pig"


def combatant(role, seat):
    entry = copy.deepcopy(FALLBACK)
    entry["role"] = role
    entry["style"] = f"测试{role}"
    data = fighter(
        {"id": f"hero-{seat}", "name": role, "asset": ""},
        validate_entry(f"ultimate-{role}-{seat}", entry),
        5,
        role,
    )
    data["seat"] = seat
    return data


def make_battle(roles=ROLES, seed=7):
    heroes = [combatant(role, seat) for seat, role in enumerate(roles)]
    boss = boss_fighter(
        {"id": SLOT, "name": "测试 Boss", "asset": ""},
        validate_entry("ultimate-boss", copy.deepcopy(FALLBACK)),
        1,
        SLOT,
        "测试 Boss",
    )
    return _RaidBattle(heroes, boss, SLOT, new_config(), seed)


def ready(fight):
    fight.round = ULTIMATE_ROUND
    return fight


class UltimateTests(unittest.TestCase):
    def test_only_exact_four_role_roster_is_eligible(self):
        self.assertTrue(make_battle().ultimate_eligible)
        self.assertFalse(make_battle(("输出",)).ultimate_eligible)
        self.assertFalse(make_battle(("输出", "输出", "治疗", "辅助")).ultimate_eligible)
        self.assertFalse(make_battle(("输出", "坦克", "治疗")).ultimate_eligible)

    def test_round_gate_and_once_per_boss(self):
        fight = make_battle()
        fight.round = ULTIMATE_ROUND - 1
        self.assertFalse(fight._try_ultimate())
        self.assertFalse(fight.ultimate_used)

        fight.round = ULTIMATE_ROUND
        self.assertTrue(fight._try_ultimate())
        first_hp = fight.boss.hp
        self.assertEqual(fight.ultimate_role, "输出")
        self.assertFalse(fight._try_ultimate())
        self.assertEqual(fight.boss.hp, first_hp)

    def test_damage_is_large_capped_and_replayed(self):
        fight = ready(make_battle())
        dps = next(hero for hero in fight.heroes if hero.role == "输出")
        before = fight.boss.hp
        self.assertTrue(fight._try_ultimate())
        dealt = before - fight.boss.hp
        self.assertGreater(dealt, dps.stat("atk") * 2)
        self.assertLessEqual(dealt, fight.boss.max_hp * 0.22 + 0.01)
        self.assertTrue(any(cue["t"] == "ultimate" for cue in fight.timeline[-1]["cues"]))

    def test_healer_answers_critical_party_health(self):
        fight = ready(make_battle())
        for hero in fight.heroes:
            hero.hp = hero.max_hp * 0.20
        before = [hero.hp for hero in fight.heroes]

        self.assertTrue(fight._try_ultimate())
        self.assertEqual(fight.ultimate_role, "治疗")
        for hero, old in zip(fight.heroes, before):
            self.assertGreater(hero.hp, old)
            self.assertLessEqual(hero.hp, hero.max_hp)

    def test_tank_grants_exactly_one_round_of_real_invulnerability(self):
        fight = ready(make_battle())
        fight.boss.base["atk"] = max(hero.max_hp for hero in fight.heroes) * 5

        self.assertTrue(fight._try_ultimate())
        self.assertEqual(fight.ultimate_role, "坦克")
        target = fight.heroes[0]
        before = target.hp
        self.assertEqual(fight.hurt(target, 99999, attacker=fight.boss), 0)
        self.assertEqual(target.hp, before)

        fight.round += 1
        self.assertGreater(fight.hurt(target, 10, attacker=fight.boss), 0)
        self.assertLess(target.hp, before)

    def test_support_refreshes_all_survivors_and_cleanses_every_status_family(self):
        fight = ready(make_battle())
        affected = fight.heroes[0]
        affected.away = 1  # Off-field survivors still receive the team-wide reset.
        affected.cooldowns["测试技能"] = 4
        affected.dots.append({"pct": 5, "turns": 3, "label": "毒"})
        affected.buffs.append({"stat": "atk", "pct": -20, "turns": 3})
        affected.stun = affected.silence = affected.root = 2
        affected.weaken.append({"pct": 10, "turns": 2})
        affected.expose.append({"pct": 10, "turns": 2})
        affected.corrupting.append({"pct": 10, "turns": 2})
        affected.delay_damages.append({"pct": 5, "turns": 2, "label": "延迟"})
        affected.marks.append({"pct": 10, "turns": 2})
        affected.vuln, affected.chill = 30, 2
        affected.glitch = affected.possessed = affected.lured = True
        affected.marked = affected.blind = True
        affected.zeroed.add("dodge")

        self.assertTrue(fight._try_ultimate())
        self.assertEqual(fight.ultimate_role, "辅助")
        self.assertFalse(affected.cooldowns)
        self.assertFalse(affected.dots)
        self.assertFalse([b for b in affected.buffs if b["pct"] < 0])
        self.assertEqual((affected.stun, affected.silence, affected.root), (0, 0, 0))
        self.assertFalse(affected.weaken)
        self.assertFalse(affected.expose)
        self.assertFalse(affected.corrupting)
        self.assertFalse(affected.delay_damages)
        self.assertFalse(affected.marks)
        self.assertEqual((affected.vuln, affected.chill), (0, 0))
        self.assertFalse(affected.glitch)
        self.assertFalse(affected.possessed)
        self.assertFalse(affected.lured)
        self.assertFalse(affected.marked)
        self.assertFalse(affected.blind)
        self.assertFalse(affected.zeroed)

    def test_dead_or_away_caster_defers_without_spending_charge(self):
        for state in ("dead", "away"):
            with self.subTest(state=state):
                fight = ready(make_battle())
                dps = next(hero for hero in fight.heroes if hero.role == "输出")
                if state == "dead":
                    dps.hp = 0
                else:
                    dps.away = 1
                self.assertFalse(fight._try_ultimate())
                self.assertFalse(fight.ultimate_used)

    def test_decision_is_deterministic_for_identical_state(self):
        choices = []
        for seed in range(12):
            fight = ready(make_battle(seed=seed))
            target = fight.heroes[1]
            target.cooldowns["长冷却"] = 4
            target.glitch = True
            self.assertTrue(fight._try_ultimate())
            choices.append(fight.ultimate_role)
        self.assertEqual(set(choices), {"辅助"})


if __name__ == "__main__":
    unittest.main()
