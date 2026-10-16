import json
import unittest
from pathlib import Path

from core.battle import FALLBACK, fighter, simulate, validate_battle, validate_entry
from core.raid import DUNGEONS, boss_fighter, simulate_raid
from core.raid_events import new_config

RESOURCES = Path(__file__).resolve().parents[1] / "resources"
REWORKED_SUPPORTS = {
    "pighub0087",  # 受击转护盾/能量，复制后返还能量
    "pighub0210",  # 留言分别提供减攻、易伤、补能或群盾
    "pighub0323",  # 火锅层数提供减攻、易伤与收锅补能
    "pighub0444",  # 缓存制造攻防窗口，清算后补能提速
}


def flatten_effects(effects):
    for effect in effects:
        yield effect
        if effect["type"] == "pick":
            for option in effect["options"]:
                yield from flatten_effects(option["effects"])


def is_team_value(effect):
    """Recognize mechanics that change a teammate's or the whole party's decision."""
    ally_targeted = {
        "buff",
        "cleanse",
        "cleanse_heal",
        "delay_heal",
        "energy",
        "evade",
        "guard",
        "heal",
        "hot_boost",
        "immunity",
        "leech",
        "life_link",
        "regen",
        "sacrifice",
        "shield",
    }
    party_wide = {"aura", "mass_heal", "redirect", "share", "taunt"}
    enemy_setup = {
        "corrupting",
        "dispel",
        "expose",
        "mark",
        "purge",
        "root",
        "silence",
        "stun",
        "transfer",
        "weaken",
    }
    return (
        effect["type"] in party_wide | enemy_setup
        or effect["type"] in ally_targeted
        and effect.get("target", "self").startswith("ally_")
    )


class SupportValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pigs = {
            pig["id"]: pig
            for pig in json.loads((RESOURCES / "pigs.json").read_text("utf-8"))
        }
        cls.raw = json.loads((RESOURCES / "battle.json").read_text("utf-8"))
        cls.data = validate_battle(cls.raw, cls.pigs)
        cls.supports = {
            pig_id: entry
            for pig_id, entry in cls.data.items()
            if entry["role"] == "辅助"
        }

    def test_every_support_validates_and_has_real_team_value(self):
        self.assertGreaterEqual(len(self.supports), 4)
        for pig_id, entry in self.supports.items():
            self.assertEqual(validate_entry(pig_id, entry), entry)
            effects = flatten_effects(
                [effect for skill in entry["skills"] for effect in skill["effects"]]
            )
            self.assertTrue(any(map(is_team_value, effects)), pig_id)

    def test_reworked_supports_have_distinct_tempo_packages(self):
        self.assertTrue(REWORKED_SUPPORTS <= self.supports.keys())
        expected = {
            "pighub0087": {"copy", "energy", "shield"},
            "pighub0210": {"weaken", "expose", "energy", "shield"},
            "pighub0323": {"dot", "weaken", "expose", "energy"},
            "pighub0444": {"delay_damage", "weaken", "expose", "energy", "buff"},
        }
        signatures = set()
        for pig_id, required in expected.items():
            effects = list(
                flatten_effects(
                    [
                        effect
                        for skill in self.supports[pig_id]["skills"]
                        for effect in skill["effects"]
                    ]
                )
            )
            kinds = {effect["type"] for effect in effects}
            self.assertTrue(required <= kinds, (pig_id, required - kinds))
            signature = tuple(
                (effect["type"], effect.get("target", "self")) for effect in effects
            )
            self.assertNotIn(signature, signatures, pig_id)
            signatures.add(signature)

    def test_reworked_supports_run_in_auto_battle(self):
        fallback = validate_entry("fallback", FALLBACK)
        enemy = fighter(
            {"id": "training-dummy", "name": "训练假人", "asset": ""},
            fallback,
            5,
            "训练假人",
        )
        for index, pig_id in enumerate(sorted(REWORKED_SUPPORTS)):
            support = fighter(
                self.pigs[pig_id], self.supports[pig_id], 5, self.pigs[pig_id]["name"]
            )
            result = simulate(support, enemy, seed=8_700 + index)
            self.assertIn(result["winner"], (0, 1), pig_id)
            self.assertTrue(result["log"], pig_id)

    def test_reworked_support_runs_in_four_role_raid(self):
        fallback = validate_entry("fallback", FALLBACK)
        support_id = "pighub0444"
        heroes = []
        for seat, role in enumerate(("辅助", "输出", "坦克", "治疗")):
            entry = self.supports[support_id] if role == "辅助" else fallback
            pig = self.pigs[support_id] if role == "辅助" else {
                "id": f"test-{role}",
                "name": f"测试{role}",
                "asset": "",
            }
            hero = fighter(pig, entry, 5, pig["name"])
            hero["role"] = role
            hero["seat"] = seat
            heroes.append(hero)

        slot_id = DUNGEONS[0]["bosses"][0]
        boss = boss_fighter(
            {"id": slot_id, "name": "测试首领", "asset": ""},
            fallback,
            1,
            slot_id,
            "测试首领",
        )
        result = simulate_raid(heroes, boss, slot_id, new_config(), seed=44_400)
        self.assertTrue(result["log"])
        self.assertGreaterEqual(result["rounds"], 1)
        self.assertEqual(len(result["heroes"]), 4)


if __name__ == "__main__":
    unittest.main()
