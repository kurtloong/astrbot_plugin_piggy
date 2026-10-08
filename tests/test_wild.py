import asyncio
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from core.catalog import read_catalog
from core.config import PiggyError, Settings
from core.database import Database
from core.views import today_message, wild_battle_message, wild_message

NOW = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)


class Scripted(random.Random):
    """Fixed gather/chain rolls; picks still come from a seeded generator."""

    def __init__(self, rolls):
        super().__init__(7)
        self.rolls = list(rolls)

    def random(self):
        return self.rolls.pop(0) if self.rolls else 0.99

    # Defining getrandbits keeps choice()/randrange() off the scripted random().
    def getrandbits(self, k):
        return super().getrandbits(k)


def win(*args):
    return {"winner": 0, "rounds": 1, "log": ["R1 测试获胜"], "hp": [9, 0], "max_hp": [9, 9]}


def lose(*args):
    return {"winner": 1, "rounds": 1, "log": ["R1 测试落败"], "hp": [0, 9], "max_hp": [9, 9]}


class DrawAndWildTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root)
        await self.db.initialize()
        images = self.root / "catalog" / "images"
        images.mkdir(parents=True)
        definitions = []
        for index in range(12):
            pig_id = f"p{index}"
            Image.new("RGB", (8, 8), (index * 20, 90, 120)).save(images / f"{pig_id}.png")
            definitions.append(
                {"id": pig_id, "name": f"猪{index}", "description": "描述", "analysis": "性格"}
            )
        (self.root / "catalog" / "pigs.json").write_text(json.dumps(definitions), "utf-8")
        await self.db.catalog(read_catalog(self.root))
        self.alice = await self.db.identify("app", "alice", "group", "阿离")
        self.bob = await self.db.identify("app", "bob", "group", "阿波")
        self.outsider = await self.db.identify("app", "carol", "elsewhere", "阿卡")

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def draw(self, user, event, now=NOW, rolls=(), **kwargs):
        return await self.db.draw(
            user["id"],
            "group",
            event,
            now,
            gather_chance=50,
            chain_chance=50,
            app_id="app",
            rng=Scripted(rolls),
            **kwargs,
        )

    async def total(self, user):
        return (await self.db.collection(user["id"]))["total"]

    async def test_gather_then_chain_then_the_chained_pigs_roll_again(self):
        result = await self.draw(self.alice, "e1", rolls=[0.1, 0.1, 0.9, 0.1, 0.1, 0.9])
        kinds = [(item["kind"], item["parent"]) for item in result["items"]]
        self.assertEqual(
            kinds, [("base", None), ("gather", 0), ("chain", 0), ("chain", 2), ("gather", 3)]
        )
        items = result["items"]
        self.assertEqual(items[1]["pig"]["id"], items[0]["pig"]["id"])
        self.assertEqual(items[4]["pig"]["id"], items[3]["pig"]["id"])
        self.assertFalse(items[1]["new"])
        self.assertEqual(await self.total(self.alice), 5)
        self.assertTrue(result["created"])
        self.assertEqual(result["kind"], "daily")

    async def test_same_day_repeats_show_the_same_haul_until_a_bonus_is_earned(self):
        first = await self.draw(self.alice, "e1", rolls=[0.1, 0.1, 0.9, 0.9])
        again = await self.draw(self.alice, "e2", rolls=[0.1, 0.1])
        self.assertFalse(again["created"])
        self.assertEqual(again["items"], first["items"])
        self.assertEqual(await self.total(self.alice), 3)
        await self.db.run(
            lambda c: c.execute(
                "INSERT INTO draw_bonus VALUES('app','group',?,?,1)",
                (self.alice["id"], "2026-10-08"),
            )
        )
        bonus = await self.draw(self.alice, "e3", rolls=[0.9, 0.9])
        self.assertTrue(bonus["created"])
        self.assertEqual((bonus["kind"], bonus["index"], bonus["bonus_left"]), ("bonus", 2, 0))
        self.assertEqual(await self.total(self.alice), 4)
        # The same message delivered twice must not spend another chance or draw again.
        replay = await self.draw(self.alice, "e3", rolls=[0.1, 0.1])
        self.assertEqual(replay["items"], bonus["items"])
        latest = await self.draw(self.alice, "e4")
        self.assertEqual(latest["items"], bonus["items"])
        self.assertEqual(await self.total(self.alice), 4)

    async def test_bonus_chances_expire_at_midnight(self):
        await self.draw(self.alice, "e1")
        await self.db.run(
            lambda c: c.execute(
                "INSERT INTO draw_bonus VALUES('app','group',?,?,1)",
                (self.alice["id"], "2026-10-08"),
            )
        )
        tomorrow = NOW + timedelta(days=1)
        self.assertEqual(await self.db.bonus_count("app", "group", self.alice["id"], tomorrow), 0)
        daily = await self.draw(self.alice, "e2", now=tomorrow)
        self.assertEqual(daily["kind"], "daily")
        repeat = await self.draw(self.alice, "e3", now=tomorrow)
        self.assertFalse(repeat["created"])

    async def test_default_chances_average_three_pigs(self):
        rng = random.Random(2026)
        counts = []
        for day in range(800):
            result = await self.db.draw(
                self.bob["id"],
                "group",
                f"d{day}",
                NOW + timedelta(days=day),
                gather_chance=50,
                chain_chance=50,
                app_id="app",
                rng=rng,
            )
            counts.append(len(result["items"]))
        self.assertTrue(2.7 < sum(counts) / len(counts) < 3.3, sum(counts) / len(counts))
        self.assertGreater(max(counts), 6)

    async def test_draw_card_shows_every_pig_with_its_source(self):
        result = await self.draw(self.alice, "e1", rolls=[0.1, 0.1, 0.9, 0.9])
        progress = await self.db.collection(self.alice["id"])
        labels = []
        original = ImageDraw.ImageDraw.text

        def record(canvas, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original(canvas, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            card = today_message(
                Settings(display={"draw": False}), self.root, self.alice, result, progress
            )
        self.assertTrue(card.local)
        self.assertIn("本次还获得", labels)
        self.assertIn("聚集", labels)
        self.assertIn("连抽", labels)
        self.assertTrue(any("本次共 3 只" in label for label in labels))

    async def test_one_wild_pig_per_group_per_day(self):
        first = await self.db.wild_pig("app", "group", NOW, level_max=20)
        same = await self.db.wild_pig("app", "group", NOW + timedelta(hours=3), level_max=20)
        self.assertEqual(first["id"], same["id"])
        self.assertTrue(1 <= first["level"] <= 20)
        other = await self.db.wild_pig("app", "elsewhere", NOW)
        self.assertNotEqual(other["id"], first["id"])
        tomorrow = await self.db.wild_pig("app", "group", NOW + timedelta(days=1))
        self.assertNotEqual(tomorrow["id"], first["id"])
        status = await self.db.run(
            lambda c: c.execute(
                "SELECT status FROM wild_pigs WHERE id=?", (first["id"],)
            ).fetchone()[0]
        )
        self.assertEqual(status, "expired")

    async def own(self, user, pig_id, count):
        await self.db.run(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO collections VALUES(?,?,?,0,0)", (user["id"], pig_id, count)
            )
        )

    async def test_losing_costs_the_pig_and_winning_rewards_the_whole_group(self):
        wild = await self.db.wild_pig("app", "group", NOW)
        mine = next(f"p{i}" for i in range(12) if f"p{i}" != wild["pig_id"])
        await self.own(self.alice, mine, 1)
        with patch("core.database.simulate", lose):
            result = await self.db.challenge_wild("app", "group", self.alice["id"], mine, now=NOW)
        self.assertFalse(result["won"])
        self.assertEqual(await self.db.pig_count(self.alice["id"], mine), 0)
        self.assertEqual(result["change"]["after"], 0)
        await self.own(self.bob, mine, 3)
        with patch("core.database.simulate", win):
            result = await self.db.challenge_wild("app", "group", self.bob["id"], mine, now=NOW)
        self.assertTrue(result["won"])
        self.assertEqual(await self.db.pig_count(self.bob["id"], wild["pig_id"]), 1)
        self.assertEqual(await self.db.pig_count(self.bob["id"], mine), 3)
        self.assertEqual(result["rewarded"], 2)
        for user, expected in ((self.alice, 1), (self.bob, 1), (self.outsider, 0)):
            self.assertEqual(await self.db.bonus_count("app", "group", user["id"], NOW), expected)
        self.assertEqual(result["wild"]["attempts"], 2)
        card = wild_message(Settings(), self.root, self.alice, result["wild"])
        self.assertTrue(card.local)
        # Tamed for the day: no new wild pig, and nobody loses a pig trying.
        with self.assertRaises(PiggyError):
            await self.db.challenge_wild("app", "group", self.bob["id"], mine, now=NOW)
        self.assertEqual(await self.db.pig_count(self.bob["id"], mine), 3)
        self.assertEqual((await self.db.wild_pig("app", "group", NOW))["id"], wild["id"])

    async def test_only_one_challenger_tames_the_wild_pig(self):
        wild = await self.db.wild_pig("app", "group", NOW)
        mine = next(f"p{i}" for i in range(12) if f"p{i}" != wild["pig_id"])
        users = [await self.db.identify("app", f"u{i}", "group", "") for i in range(6)]
        for user in users:
            await self.own(user, mine, 1)
        with patch("core.database.simulate", win):
            results = await asyncio.gather(
                *(
                    Database(self.root).challenge_wild("app", "group", u["id"], mine, now=NOW)
                    for u in users
                ),
                return_exceptions=True,
            )
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertTrue(all(isinstance(r, (dict, PiggyError)) for r in results))
        winners = [await self.db.pig_count(u["id"], wild["pig_id"]) for u in users]
        self.assertEqual(sorted(winners), [0, 0, 0, 0, 0, 1])

    async def test_wild_battle_poster_mentions_the_group_reward(self):
        wild = await self.db.wild_pig("app", "group", NOW)
        mine = next(f"p{i}" for i in range(12) if f"p{i}" != wild["pig_id"])
        await self.own(self.alice, mine, 2)
        result = await self.db.challenge_wild(
            "app", "group", self.alice["id"], mine, now=NOW, seed=3
        )
        labels = []
        original = ImageDraw.ImageDraw.text

        def record(canvas, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original(canvas, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            poster = await wild_battle_message(Settings(), self.root, result)
        joined = "".join(labels)
        self.assertTrue(poster.local)
        self.assertIn("野猪挑战", joined)
        if result["won"]:
            self.assertIn("再抽机会", joined)
        else:
            self.assertIn("野猪还在", joined)


if __name__ == "__main__":
    unittest.main()
