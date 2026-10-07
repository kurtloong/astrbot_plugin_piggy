import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from core.catalog import read_catalog
from core.config import PiggyError, Settings
from core.database import Database
from core.views import battle_message, request_message, stats_message, trade_message

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)


class ExchangeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root)
        await self.db.initialize()
        images = self.root / "catalog" / "images"
        images.mkdir(parents=True)
        definitions = []
        for index, pig_id in enumerate(("pig", "cat", "owl")):
            Image.new("RGB", (8, 8), (index * 40, 90, 120)).save(images / f"{pig_id}.png")
            definitions.append(
                {"id": pig_id, "name": f"{pig_id}猪", "description": "描述", "analysis": "性格"}
            )
        (self.root / "catalog" / "pigs.json").write_text(json.dumps(definitions), "utf-8")
        await self.db.catalog(read_catalog(self.root))
        self.alice = await self.db.identify("app", "alice", "group", "阿离")
        self.bob = await self.db.identify("app", "bob", "group", "阿波")

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def own(self, user, pig_id, count):
        await self.db.run(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO collections VALUES(?,?,?,0,0)", (user["id"], pig_id, count)
            )
        )

    async def counts(self, user):
        rows = await self.db.run(
            lambda c: c.execute(
                "SELECT pig_id,count FROM collections WHERE user_id=?", (user["id"],)
            ).fetchall()
        )
        return {row[0]: row[1] for row in rows}

    async def duel(self, **kwargs):
        return await self.db.create_request(
            "app", "group", "duel", self.alice["id"], self.bob["id"], "pig", now=NOW, **kwargs
        )

    async def test_trade_swaps_one_pig_each_and_reports_level_changes(self):
        await self.own(self.alice, "pig", 2)
        await self.own(self.bob, "cat", 1)
        request = await self.db.create_request(
            "app", "group", "trade", self.alice["id"], self.bob["id"], "pig", "cat", now=NOW
        )
        self.assertIn("想和你交换", request_message(Settings(), request, 2).text)
        result = await self.db.respond("app", "group", self.bob["id"], "trade", True, now=NOW)
        self.assertTrue(result["accepted"])
        self.assertEqual(await self.counts(self.alice), {"pig": 1, "cat": 1})
        self.assertEqual(await self.counts(self.bob), {"pig": 1})
        changes = result["changes"]
        self.assertEqual((changes["from_give"]["before"], changes["from_give"]["after"]), (2, 1))
        self.assertEqual(changes["to_want"]["after"], 0)
        self.assertEqual(changes["from_want"]["gained"], ["猪突猛进"])
        self.assertIn("交换成功", trade_message(Settings(), result).text)
        with self.assertRaises(PiggyError):
            await self.db.respond("app", "group", self.bob["id"], "trade", True, now=NOW)

    async def test_duel_moves_one_pig_from_loser_and_relocks_skills(self):
        await self.own(self.alice, "pig", 5)
        await self.own(self.bob, "cat", 5)
        await self.duel()
        result = await self.db.respond(
            "app", "group", self.bob["id"], "duel", True, "cat", now=NOW, seed=7
        )
        winner, loser = result["winner"], result["loser"]
        prize = result["loser_change"]["pig"]["id"]
        self.assertEqual((await self.counts(loser))[prize], 4)
        self.assertEqual((await self.counts(winner))[prize], 1)
        self.assertEqual(result["loser_change"]["before"], 5)
        self.assertEqual(result["loser_change"]["after"], 4)
        self.assertEqual(result["loser_change"]["lost"], ["全力一拱"])
        self.assertTrue(result["winner_change"]["after"] > result["winner_change"]["before"])
        records = await self.db.run(
            lambda c: c.execute("SELECT winner,seed,log FROM battle_records").fetchall()
        )
        self.assertEqual(records[0][0], winner["id"])
        self.assertEqual(records[0][1], 7)
        self.assertEqual(json.loads(records[0][2]), result["result"]["log"])
        text = battle_message(Settings(), result).text
        for line in result["result"]["log"]:
            self.assertIn(line, text)
        self.assertNotIn("省略", text)
        self.assertIn("获胜", text)
        self.assertIn("Lv5 → Lv4", text)

    async def test_losing_the_last_pig_removes_it_from_the_pen(self):
        await self.own(self.alice, "pig", 1)
        await self.own(self.bob, "cat", 1)
        await self.duel()
        result = await self.db.respond(
            "app", "group", self.bob["id"], "duel", True, "cat", now=NOW, seed=3
        )
        loser_pig = result["loser_change"]["pig"]["id"]
        self.assertNotIn(loser_pig, await self.counts(result["loser"]))
        self.assertEqual(result["loser_change"]["after"], 0)
        self.assertIn("已失去", battle_message(Settings(), result).text)

    async def test_decline_cancel_expiry_and_one_pending_request_per_side(self):
        await self.own(self.alice, "pig", 1)
        await self.own(self.bob, "cat", 1)
        with self.assertRaises(PiggyError):
            await self.db.create_request(
                "app", "group", "duel", self.alice["id"], self.alice["id"], "pig", now=NOW
            )
        with self.assertRaises(PiggyError):
            await self.db.create_request(
                "app", "group", "duel", self.alice["id"], self.bob["id"], "owl", now=NOW
            )
        await self.duel()
        with self.assertRaises(PiggyError):
            await self.duel()
        listed = await self.db.list_requests("app", "group", self.bob["id"], now=NOW)
        self.assertEqual(len(listed["incoming"]), 1)
        declined = await self.db.respond("app", "group", self.bob["id"], "duel", False, now=NOW)
        self.assertFalse(declined["accepted"])
        await self.duel()
        cancelled = await self.db.cancel_requests("app", "group", self.alice["id"], now=NOW)
        self.assertEqual(len(cancelled), 1)
        await self.duel(ttl_minutes=10)
        with self.assertRaises(PiggyError):
            await self.db.respond(
                "app", "group", self.bob["id"], "duel", True, "cat", now=NOW + timedelta(minutes=11)
            )
        self.assertEqual(await self.counts(self.bob), {"cat": 1})

    async def test_daily_duel_limit_and_vanished_challenger_pig(self):
        await self.own(self.alice, "pig", 5)
        await self.own(self.bob, "cat", 5)
        for seed in range(2):
            await self.duel(daily_limit=2)
            await self.db.respond(
                "app",
                "group",
                self.bob["id"],
                "duel",
                True,
                "cat",
                daily_limit=2,
                now=NOW,
                seed=seed,
            )
        with self.assertRaises(PiggyError):
            await self.duel(daily_limit=2)
        await self.db.run(lambda c: c.execute("DELETE FROM battle_records"))
        await self.duel()
        await self.db.run(lambda c: c.execute("DELETE FROM collections WHERE pig_id='pig'"))
        result = await self.db.respond("app", "group", self.bob["id"], "duel", True, "cat", now=NOW)
        self.assertFalse(result["accepted"])
        self.assertIn("作废", result["error"])

    async def test_concurrent_accepts_complete_the_trade_only_once(self):
        await self.own(self.alice, "pig", 1)
        await self.own(self.bob, "cat", 1)
        await self.db.create_request(
            "app", "group", "trade", self.alice["id"], self.bob["id"], "pig", "cat", now=NOW
        )
        results = await asyncio.gather(
            *(
                Database(self.root).respond("app", "group", self.bob["id"], "trade", True, now=NOW)
                for _ in range(8)
            ),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertTrue(all(isinstance(r, (dict, PiggyError)) for r in results))
        self.assertEqual(await self.counts(self.alice), {"cat": 1})

    async def test_stats_lookup_and_group_player_fallback(self):
        await self.own(self.alice, "pig", 3)
        pig = await self.db.find_pig("pig猪")
        self.assertEqual(pig["id"], (await self.db.find_pig("PIG"))["id"])
        text = stats_message(Settings(), self.alice, pig, 3, 20).text
        self.assertIn("Lv3", text)
        self.assertIn("【Lv4 解锁】", text)
        with self.assertRaises(PiggyError):
            await self.db.find_pig("不存在的猪")
        self.assertEqual(
            (await self.db.find_group_player("app", "group", "@阿波"))["id"], self.bob["id"]
        )
        await self.db.identify("app", "other", "group", "阿波")
        with self.assertRaises(PiggyError):
            await self.db.find_group_player("app", "group", "阿波")

    async def test_markdown_mode_mentions_target_and_limits_buttons(self):
        await self.own(self.alice, "pig", 1)
        request = await self.duel()
        message = request_message(Settings(battle_markdown=True), request, 1)
        self.assertTrue(message.markdown)
        self.assertIn('<qqbot-at-user id="bob" />', message.text)
        buttons = message.keyboard["content"]["rows"][0]["buttons"]
        self.assertEqual(buttons[0]["action"]["permission"]["specify_user_ids"], ["bob"])


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_version_one_database_gains_battle_tables_without_losing_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with closing(sqlite3.connect(root / "piggy.sqlite3")) as conn:
                conn.executescript("""
                    CREATE TABLE pigs (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                        analysis TEXT NOT NULL, asset TEXT NOT NULL,
                        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                        sort_order INTEGER NOT NULL
                    );
                    INSERT INTO pigs VALUES('pig','猪','d','a','x.png',1,0);
                    PRAGMA user_version=1;
                """)
            db = Database(root)
            await db.initialize()
            await db.initialize()
            rows = await db.run(lambda c: c.execute("SELECT id,battle FROM pigs").fetchall())
            self.assertEqual([tuple(r) for r in rows], [("pig", "")])
            version = await db.run(lambda c: c.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(version, 2)
            tables = await db.run(
                lambda c: {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
            )
            self.assertTrue({"requests", "battle_records"} <= tables)


if __name__ == "__main__":
    unittest.main()
