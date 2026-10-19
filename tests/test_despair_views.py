import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.config import Settings
from core.despair_rendering import (
    MAX_PHASE_LOG_LINES,
    PHASE_CARD_GAP,
    _defeat_log_lines,
    _phase_log_lines,
    stack_despair_phase_cards,
)
from core.despair_views import despair_battle_message
from core.rendering import Card


class DespairViewTests(unittest.IsolatedAsyncioTestCase):
    def test_phase_cards_are_stacked_into_one_valid_png(self):
        cards = []
        for size, color in [((1080, 120), "red"), ((900, 80), "blue")]:
            with Image.new("RGB", size, color) as image, BytesIO() as output:
                image.save(output, "PNG")
                cards.append(Card(output.getvalue(), *size))

        combined = stack_despair_phase_cards(cards)

        self.assertEqual(combined.width, 1080)
        self.assertEqual(combined.height, 200 + PHASE_CARD_GAP)
        with Image.open(BytesIO(combined.data)) as image:
            self.assertEqual(image.size, (combined.width, combined.height))

    def test_defeat_summary_keeps_the_lethal_boss_action(self):
        class Measure:
            @staticmethod
            def wrap(text, size, width):
                return [text]

        lines = _defeat_log_lines(
            Measure(),
            {
                "log": [
                    "R69 玩家施放技能。",
                    "R70 万相猪皇发动软狂暴「绝境威压」，甲受到 80 伤害并倒下；乙受到 90 伤害并倒下。",
                    "R70 甲 倒下。",
                    "R70 乙 倒下。",
                ]
            },
        )

        self.assertTrue(any("绝境威压" in line and "并倒下" in line for line, _ in lines))
        self.assertTrue(any("甲 倒下" in line for line, _ in lines))
        self.assertTrue(any("乙 倒下" in line for line, _ in lines))

    def test_oversized_phase_log_keeps_every_boss_action_and_its_context(self):
        class Measure:
            @staticmethod
            def wrap(text, size, width):
                return [text]

        log = []
        for round_number in range(1, 151):
            log.extend(
                [
                    f"R{round_number} 玩家施放「普通技能」，造成完整效果。",
                    f"R{round_number} 万相猪皇施放「阶段招式」，造成完整效果。",
                    f"R{round_number} 持续治疗生效。",
                ]
            )

        lines = _phase_log_lines(Measure(), {"log": log}, MAX_PHASE_LOG_LINES)

        sources = [source for _, source in lines]
        self.assertIn("Boss 每次技能、机制及其前后记录完整保留", lines[0][0])
        for round_number in range(1, 151):
            self.assertIn(
                f"R{round_number} 万相猪皇施放「阶段招式」，造成完整效果。",
                sources,
            )
            self.assertIn(f"R{round_number} 玩家施放「普通技能」，造成完整效果。", sources)
            self.assertIn(f"R{round_number} 持续治疗生效。", sources)

    def test_compressed_phase_log_keeps_two_player_actions_per_round(self):
        class Measure:
            @staticmethod
            def wrap(text, size, width):
                return [text]

        log = []
        for round_number in range(1, 4):
            log.extend(
                [
                    f"R{round_number} 甲施放「技能甲」，造成伤害。",
                    f"R{round_number} 乙施放「技能乙」，获得护盾。",
                    f"R{round_number} 丙施放「技能丙」，造成伤害。",
                ]
            )
        log.extend(f"额外记录 {index}" for index in range(30))

        lines = _phase_log_lines(Measure(), {"log": log}, 12)
        sources = {source for _, source in lines}

        for round_number in range(1, 4):
            self.assertIn(f"R{round_number} 甲施放「技能甲」，造成伤害。", sources)
            self.assertIn(f"R{round_number} 乙施放「技能乙」，获得护盾。", sources)

    async def test_battle_message_builds_one_long_card_with_catalog_boss_art(self):
        reports = [
            {
                "phase": phase,
                "name": f"阶段{phase}",
                "start_round": phase,
                "end_round": phase,
                "boss_hp_start": 600 - phase * 100,
                "boss_hp_end": 500 - phase * 100,
                "heroes": [],
                "actions": [],
                "log": [f"阶段{phase}细节"],
            }
            for phase in range(1, 6)
        ]
        fighters = [
            {
                "user": {"open_id": f"user-{index}", "nickname": f"玩家{index}"},
                "pig": {"name": f"猪{index}", "asset": f"pig-{index}.webp"},
                "level": 10,
                "role": "输出",
            }
            for index in range(8)
        ]
        battle = {
            "id": 7,
            "week": "2026-W42",
            "won": True,
            "fighters": fighters,
            "boss_pig": {"id": "pighub0253", "name": "城堡猪王", "asset": "castle.webp"},
            "result": {
                "boss": {"max_hp": 600, "hp": 0, "phase": 5},
                "phase_reports": reports,
            },
            "rewards": [],
        }

        phase_cards = [Card(f"card-{phase}".encode(), 1080, 1200) for phase in range(1, 6)]
        combined = Card(b"combined", 1080, 6000)
        with (
            patch(
                "core.despair_views.render_despair_phase",
                side_effect=phase_cards,
            ) as render,
            patch(
                "core.despair_views.stack_despair_phase_cards",
                return_value=combined,
            ) as stack,
        ):
            message = await despair_battle_message(Settings(), Path("/tmp"), battle)
            self.assertEqual(render.call_count, 0)
            resolved = await message.resolve()

        self.assertTrue(resolved.local)
        self.assertEqual(resolved.images, (b"combined",))
        stack.assert_called_once_with(phase_cards)
        self.assertTrue(
            all(call.args[1]["boss_asset"] == "castle.webp" for call in render.call_args_list)
        )
        self.assertEqual(len(render.call_args_list[-1].args[1]["overall_stats"]), 8)
        self.assertEqual(
            [call.args[1]["report"]["phase"] for call in render.call_args_list],
            list(range(1, 6)),
        )
        self.assertTrue(
            all(call.args[1]["report"]["mechanics"] for call in render.call_args_list)
        )

    async def test_early_defeat_is_included_in_the_single_long_card(self):
        reports = [
            {
                "phase": phase,
                "name": f"阶段{phase}",
                "start_round": phase,
                "end_round": phase,
                "heroes": [],
                "log": [],
            }
            for phase in range(1, 4)
        ]
        battle = {
            "id": 8,
            "won": False,
            "test": True,
            "fighters": [],
            "raid": {"leader_user": {"open_id": "tester", "nickname": "测试者"}},
            "result": {
                "timeout": False,
                "boss": {"max_hp": 600, "hp": 250, "phase": 3},
                "phase_reports": reports,
            },
        }

        combined = Card(b"combined", 1080, 3600)
        with (
            patch(
                "core.despair_views.render_despair_phase",
                return_value=Card(b"card", 1080, 1200),
            ) as render,
            patch(
                "core.despair_views.stack_despair_phase_cards",
                return_value=combined,
            ) as stack,
        ):
            message = await despair_battle_message(Settings(), Path("/tmp"), battle)
            self.assertEqual(render.call_count, 0)
            resolved = await message.resolve()

        self.assertEqual(resolved.images, (b"combined",))
        self.assertEqual(render.call_count, 3)
        self.assertEqual(len(stack.call_args.args[0]), 3)
        final_data = render.call_args_list[-1].args[1]
        self.assertTrue(final_data["final"])
        self.assertFalse(final_data["won"])
