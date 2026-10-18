import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import Settings
from core.despair_views import despair_battle_message
from core.rendering import Card


class DespairViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_battle_message_builds_one_card_per_phase_with_catalog_boss_art(self):
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

        with patch(
            "core.despair_views.render_despair_phase",
            side_effect=[Card(f"card-{phase}".encode(), 1080, 1200) for phase in range(1, 6)],
        ) as render:
            messages = await despair_battle_message(Settings(), Path("/tmp"), battle)

        self.assertEqual(len(messages), 5)
        self.assertTrue(all(message.local for message in messages))
        self.assertEqual(
            [message.images[0] for message in messages],
            [f"card-{phase}".encode() for phase in range(1, 6)],
        )
        self.assertTrue(
            all(call.args[1]["boss_asset"] == "castle.webp" for call in render.call_args_list)
        )
        self.assertEqual(len(render.call_args_list[-1].args[1]["overall_stats"]), 8)
        self.assertEqual(
            [call.args[1]["report"]["phase"] for call in render.call_args_list],
            list(range(1, 6)),
        )
