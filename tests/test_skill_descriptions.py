import unittest

from core.battle import describe_effect, describe_skill


class SkillDescriptionTests(unittest.TestCase):
    def test_effect_names_the_target_value_and_duration(self):
        self.assertEqual(
            describe_effect({"type": "heal", "pct": 24, "target": "ally_lowest"}),
            "为生命最低队友回复 24% 最大生命",
        )
        self.assertEqual(
            describe_effect(
                {
                    "type": "buff",
                    "stat": "def",
                    "pct": 30,
                    "turns": 2,
                    "target": "ally_all",
                }
            ),
            "使全体队友防御 +30%，持续 2 回合",
        )

    def test_probability_and_multi_hit_damage_are_unambiguous(self):
        self.assertEqual(
            describe_effect(
                {
                    "type": "damage",
                    "power": 0.7,
                    "hits": 3,
                    "pierce": 25,
                    "chance": 40,
                }
            ),
            "40% 概率触发：对敌方攻击 3 段，每段造成 0.7 倍攻击伤害（无视 25% 防御）",
        )

    def test_passive_omitted_duration_is_described_as_persistent(self):
        skill = {
            "name": "疗愈光环",
            "text": "测试",
            "passive": True,
            "when": None,
            "cd": 0,
            "effects": [
                {"type": "regen", "pct": 3, "target": "ally_all"},
                {"type": "immunity", "kind": "stun"},
            ],
        }
        self.assertEqual(
            describe_skill(skill),
            "被动（开战生效） · 效果：使全体队友每回合回复 3% 最大生命，常驻；然后使自身免疫眩晕，常驻",
        )

    def test_active_skill_lists_trigger_effect_cooldown_energy_and_failure(self):
        skill = {
            "name": "紧急支援",
            "text": "测试",
            "passive": False,
            "when": "on_ally_hurt",
            "cd": 3,
            "cost": 40,
            "gain": 10,
            "effects": [
                {"type": "cleanse", "target": "ally_all"},
                {"type": "shield", "pct": 18, "target": "ally_lowest"},
            ],
            "fail": {
                "chance": 20,
                "text": "支援中断",
                "effects": [{"type": "recoil", "pct": 8}],
            },
        }
        self.assertEqual(
            describe_skill(skill),
            "主动 · 触发：队友受伤后 · 效果：清除全体队友的所有可净化负面状态；然后为生命最低队友施加相当于其 18% 最大生命的护盾 · 冷却：3 回合 · 能量：消耗 40，获得 10 · 失败：20% 概率，支援中断（自身损失 8% 最大生命）",
        )

    def test_random_bundle_explains_each_option_and_weight(self):
        effect = {
            "type": "pick",
            "options": [
                {
                    "text": "红烧",
                    "weight": 2,
                    "effects": [
                        {
                            "type": "buff",
                            "stat": "atk",
                            "pct": 20,
                            "turns": 2,
                            "target": "ally_all",
                        }
                    ],
                },
                {
                    "text": "清汤",
                    "weight": 1,
                    "effects": [{"type": "heal", "pct": 15, "target": "ally_lowest"}],
                },
            ],
        }
        self.assertEqual(
            describe_effect(effect),
            "随机选择一项：红烧，权重 2（使全体队友攻击 +20%，持续 2 回合） / 清汤，权重 1（为生命最低队友回复 15% 最大生命）",
        )


if __name__ == "__main__":
    unittest.main()
