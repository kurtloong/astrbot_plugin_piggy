import asyncio
import copy
import json
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from core.battle import FALLBACK
from core.config import PiggyError
from core.database import Database
from core.raid import DUNGEONS

SUNDAY = datetime(2026, 10, 18, 15, 59, tzinfo=timezone.utc)
MONDAY = datetime(2026, 10, 18, 16, 0, tzinfo=timezone.utc)
ROLES = ("坦克", "输出", "治疗", "辅助")


def battle_result(heroes, seed=1, won=True):
    return {
        "won": won,
        "timeout": False,
        "rounds": 10,
        "log": ["绝境测试"],
        "heroes": [
            {
                "seat": hero["seat"],
                "hp": hero["stats"]["hp"] if won else 0,
                "max_hp": hero["stats"]["hp"],
                "alive": won,
                "dealt": 100,
                "healed": 20,
                "taken": 30,
                "poise": 10,
            }
            for hero in heroes
        ],
        "boss": {"hp": 0 if won else 100, "max_hp": 1000},
        "phases": [1, 2, 3, 4, 5],
        "ultimate": {"history": []},
    }


class DespairDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name))
        await self.db.initialize()
        definitions = []
        self.pig_ids = []
        for index in range(8):
            pig_id = f"despair-pig-{index}"
            entry = copy.deepcopy(FALLBACK)
            entry["role"] = ROLES[index // 2]
            definitions.append(
                {
                    "id": pig_id,
                    "name": f"绝境猪{index}",
                    "description": "测试",
                    "analysis": "测试",
                    "asset": "",
                    "enabled": True,
                    "sort_order": index,
                    "battle": json.dumps(entry, ensure_ascii=False),
                }
            )
            self.pig_ids.append(pig_id)
        # Include an enabled mythic for the completed-personality fallback.
        definitions.append(
            {
                "id": "big-lazy-pig",
                "name": "神话猪",
                "description": "测试",
                "analysis": "测试",
                "asset": "",
                "enabled": True,
                "sort_order": 8,
                "battle": json.dumps(FALLBACK, ensure_ascii=False),
            }
        )
        await self.db.catalog(definitions)
        self.users = [
            await self.db.identify("app", f"despair-user-{i}", "group", f"玩家{i}")
            for i in range(4)
        ]
        for index, user in enumerate(self.users):
            for pig_id in self.pig_ids[index * 2 : index * 2 + 2]:
                await self.db.run(
                    lambda conn, uid=user["id"], pid=pig_id: conn.execute(
                        "INSERT INTO collections VALUES(?,?,1,0,0)", (uid, pid)
                    )
                )

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def unlock(self, user):
        def write(conn):
            for info in DUNGEONS:
                cursor = conn.execute(
                    "INSERT INTO raids(app_id,group_id,day,dungeon,leader,status,stage,seed,"
                    "created_at,expires_at,updated_at) VALUES(?,?,?,?,?,'cleared',3,1,0,0,0)",
                    ("app", "history", "2026-01-01", info["key"], user["id"]),
                )
                conn.execute(
                    "INSERT INTO raid_members(raid_id,user_id,pig_id,seat,joined_at) "
                    "VALUES(?,?,?,?,0)",
                    (cursor.lastrowid, user["id"], self.pig_ids[0], 0),
                )

        await self.db.run(write)

    async def unlock_all(self):
        for user in self.users:
            await self.unlock(user)

    async def form(self, now=MONDAY, won=True):
        await self.db.open_despair("app", "group", self.users[0]["id"], self.pig_ids[:2], now)
        result = None
        with patch(
            "core.despair.simulate_despair",
            lambda heroes, seed=1: battle_result(heroes, seed, won),
        ):
            for index, user in enumerate(self.users[1:], 1):
                result = await self.db.join_despair(
                    "app",
                    "group",
                    user["id"],
                    self.pig_ids[index * 2 : index * 2 + 2],
                    now=now,
                    seed=7,
                )
        return result

    async def test_historical_clears_unlock_and_missing_is_reported(self):
        status = await self.db.despair_status("app", "group", self.users[0]["id"], MONDAY)
        self.assertFalse(status["unlocked"])
        self.assertEqual(len(status["missing"]), len(DUNGEONS))
        await self.unlock(self.users[0])
        status = await self.db.despair_status("app", "group", self.users[0]["id"], MONDAY)
        self.assertTrue(status["unlocked"])
        self.assertEqual(status["missing"], [])

    async def test_two_distinct_owned_pigs_and_unique_team_species(self):
        await self.unlock_all()
        with self.assertRaisesRegex(PiggyError, "2 只不同"):
            await self.db.open_despair(
                "app", "group", self.users[0]["id"], [self.pig_ids[0]] * 2, MONDAY
            )
        await self.db.open_despair("app", "group", self.users[0]["id"], self.pig_ids[:2], MONDAY)
        # Give player two a duplicate species already selected by player one.
        await self.db.run(
            lambda conn: conn.execute(
                "INSERT INTO collections VALUES(?,?,1,0,0)",
                (self.users[1]["id"], self.pig_ids[0]),
            )
        )
        with self.assertRaises(PiggyError):
            await self.db.join_despair(
                "app",
                "group",
                self.users[1]["id"],
                [self.pig_ids[0], self.pig_ids[2]],
                now=MONDAY,
            )

    async def test_win_grants_exact_ten_and_one_new_personality(self):
        await self.unlock_all()
        before = [
            await self.db.run(
                lambda c, uid=u["id"]: c.execute(
                    "SELECT coalesce(sum(count),0) FROM collections WHERE user_id=?", (uid,)
                ).fetchone()[0]
            )
            for u in self.users
        ]
        result = await self.form()
        self.assertTrue(result["started"])
        self.assertTrue(result["battle"]["won"])
        self.assertEqual(len(result["rewards"]), 4)
        for index, reward in enumerate(result["rewards"]):
            self.assertTrue(reward["created"])
            self.assertEqual(len(reward["items"]), 10)
            self.assertIsNotNone(reward["personality_detail"])
            after = await self.db.run(
                lambda c, uid=self.users[index]["id"]: c.execute(
                    "SELECT sum(count) FROM collections WHERE user_id=?", (uid,)
                ).fetchone()[0]
            )
            self.assertEqual(after, before[index] + 10)

    async def test_reward_is_idempotent_and_week_uses_utc8_monday(self):
        await self.unlock_all()
        result = await self.form(now=SUNDAY)
        reward = result["rewards"][0]
        repeated = await self.db.run(
            lambda conn: self.db._grant_despair_reward(
                conn,
                self.users[0]["id"],
                reward["week"],
                result["raid"]["id"],
                MONDAY.timestamp(),
                random.Random(99),
            )
        )
        self.assertFalse(repeated["created"])
        self.assertEqual(repeated["id"], reward["id"])
        self.assertEqual(len(repeated["items"]), 10)
        next_week = await self.db.despair_status("app", "group", self.users[0]["id"], MONDAY)
        self.assertIsNone(next_week["reward"])

    async def test_reward_week_is_decided_when_the_battle_finishes(self):
        await self.unlock_all()
        await self.db.open_despair(
            "app", "group", self.users[0]["id"], self.pig_ids[:2], SUNDAY
        )
        with patch(
            "core.despair.simulate_despair",
            lambda heroes, seed=1: battle_result(heroes, seed, True),
        ):
            result = None
            for index, user in enumerate(self.users[1:], 1):
                result = await self.db.join_despair(
                    "app",
                    "group",
                    user["id"],
                    self.pig_ids[index * 2 : index * 2 + 2],
                    now=MONDAY,
                    seed=7,
                )
        self.assertEqual(result["battle"]["week"], "2026-10-19")
        self.assertTrue(all(reward["week"] == "2026-10-19" for reward in result["rewards"]))

    async def test_failure_does_not_consume_pigs_and_can_retry(self):
        await self.unlock_all()
        result = await self.form(won=False)
        self.assertFalse(result["battle"]["won"])
        self.assertEqual(result["rewards"], [])
        for index, user in enumerate(self.users):
            for pig_id in self.pig_ids[index * 2 : index * 2 + 2]:
                self.assertEqual(await self.db.pig_count(user["id"], pig_id), 1)
        retry = await self.db.open_despair(
            "app", "group", self.users[0]["id"], self.pig_ids[:2], MONDAY
        )
        self.assertEqual(retry["raid"]["status"], "forming")

    async def test_concurrent_reward_grant_is_idempotent(self):
        await self.unlock_all()
        result = await self.form()
        user_id = self.users[0]["id"]
        before = await self.db.run(
            lambda conn: conn.execute(
                "SELECT sum(count) FROM collections WHERE user_id=?", (user_id,)
            ).fetchone()[0]
        )

        async def grant(seed):
            def write(conn):
                conn.execute("BEGIN IMMEDIATE")
                return self.db._grant_despair_reward(
                    conn,
                    user_id,
                    "2099-01-05",
                    result["raid"]["id"],
                    MONDAY.timestamp(),
                    random.Random(seed),
                )

            return await self.db.run(write)

        rewards = await asyncio.gather(grant(1), grant(2))
        self.assertEqual(sorted(reward["created"] for reward in rewards), [False, True])
        self.assertEqual(rewards[0]["id"], rewards[1]["id"])
        after = await self.db.run(
            lambda conn: conn.execute(
                "SELECT sum(count) FROM collections WHERE user_id=?", (user_id,)
            ).fetchone()[0]
        )
        self.assertEqual(after, before + 10)


if __name__ == "__main__":
    unittest.main()
