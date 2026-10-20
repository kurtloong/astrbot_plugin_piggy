import asyncio
import io
import json
import random
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from core.catalog import initialize_catalog, read_catalog
from core.config import PiggyError, Settings
from core.database import Database
from core.diagnostics import safe_detail
from core.views import (
    _pen_entries,
    collection_message,
    draw_all_message,
    keyboard,
    md,
    ranking_message,
    today_message,
)


class DomainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root)
        await self.db.initialize()
        (self.root / "catalog" / "images").mkdir(parents=True)
        self.manifest = self.root / "catalog" / "pigs.json"
        self.write_catalog(["pig", "cat"])
        await self.db.catalog(read_catalog(self.root))
        self.user = await self.db.identify("app", "member", "group-a", "昵称")

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_duplicate_cap_and_natural_probability_boundaries(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        first = await self.db.draw(self.user["id"], "a", "first", start)
        for offset, cap, roll, repeated in (
            (1, 20, 39, True),
            (2, 20, 40, False),
            (3, 100, 99, True),
            (4, 100, 100, False),
            (5, 0, 0, False),
        ):
            # Keep exactly one of the two species owned for each boundary check.
            await self.db.run(
                lambda c: c.execute(
                    "DELETE FROM collections WHERE user_id=? AND pig_id<>?",
                    (self.user["id"], first["pig"]["id"]),
                )
            )
            with patch("core.database.secrets.randbelow", return_value=roll):
                result = await self.db.draw(
                    self.user["id"],
                    "a",
                    str(offset),
                    start + timedelta(days=offset),
                    duplicate_rate_cap=cap,
                    duplicate_pity=0,
                )
            self.assertEqual(result["pig"]["id"] == first["pig"]["id"], repeated)

    async def test_draw_all_consumes_daily_and_every_bonus_atomically(self):
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        await self.db.grant_draws("app", "group-a", self.user["id"], 2, now)
        result = await self.db.draw(
            self.user["id"],
            "group-a",
            "all",
            now,
            app_id="app",
            all_available=True,
            gather_chance=0,
            chain_chance=0,
            rng=random.Random(7),
        )

        self.assertEqual(result["opportunities"], 3)
        self.assertEqual(result["bonus_spent"], 2)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["bonus_left"], 0)
        self.assertEqual(sum(item["count"] for item in result["items"]), 3)
        self.assertEqual((await self.db.collection(self.user["id"]))["total"], 3)

        replay = await self.db.draw(
            self.user["id"], "group-a", "all", now, app_id="app", all_available=True
        )
        self.assertFalse(replay["created"])
        self.assertEqual(replay["total"], 3)
        self.assertEqual((await self.db.collection(self.user["id"]))["total"], 3)
        with self.assertRaisesRegex(PiggyError, "没有可以抽取"):
            await self.db.draw(
                self.user["id"], "group-a", "empty", now, app_id="app", all_available=True
            )

        receipt = draw_all_message(
            self.user, result, await self.db.collection(self.user["id"])
        )
        self.assertIn("用了 3 次抽取机会", receipt.text)
        self.assertIn("剩余再抽 0 次", receipt.text)

        # Asking for today's pig after a draw-all replays the grouped batch.
        # It must use the batch receipt rather than treating grouped rows as
        # ordinary gather/chain items (which do not have a ``kind`` field).
        recalled = await self.db.draw(
            self.user["id"], "group-a", "today-after-all", now, app_id="app"
        )
        self.assertTrue(recalled["all_available"])
        replay_receipt = today_message(
            Settings(), self.root, self.user, recalled, await self.db.collection(self.user["id"])
        )
        self.assertIn("这次全部抽取已经处理过啦", replay_receipt.text)
        self.assertIn("用了 3 次抽取机会", replay_receipt.text)

    async def test_pity_survives_gaps_restart_and_same_day_replays(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        first = await self.db.draw(self.user["id"], "a", "first", start)
        with patch("core.database.secrets.randbelow", return_value=0):
            for offset in (1, 8):
                result = await self.db.draw(
                    self.user["id"],
                    "b",
                    str(offset),
                    start + timedelta(days=offset),
                )
                self.assertEqual(result["pig"]["id"], first["pig"]["id"])
            self.assertEqual(result["repeat_streak"], 2)
            replay = await Database(self.root).draw(
                self.user["id"],
                "c",
                "replay",
                start + timedelta(days=8),
            )
            self.assertFalse(replay["created"])
            self.assertEqual(replay["repeat_streak"], 2)
            guaranteed = await self.db.draw(
                self.user["id"],
                "a",
                "pity",
                start + timedelta(days=9),
            )
        self.assertTrue(guaranteed["new_species"])
        self.assertEqual(guaranteed["repeat_streak"], 0)
        complete = await self.db.draw(
            self.user["id"],
            "a",
            "complete",
            start + timedelta(days=10),
            duplicate_rate_cap=0,
        )
        self.assertFalse(complete["new_species"])
        self.write_catalog(["pig", "cat", "new"])
        await self.db.catalog(read_catalog(self.root))
        added = await self.db.draw(
            self.user["id"],
            "a",
            "added",
            start + timedelta(days=11),
            duplicate_rate_cap=0,
        )
        self.assertEqual(added["pig"]["id"], "new")

    def test_duplicate_settings_validation(self):
        schema = json.loads((Path(__file__).parents[1] / "_conf_schema.json").read_text("utf-8"))
        for key, maximum in (("duplicate_rate_cap", 100), ("duplicate_pity", 30)):
            self.assertEqual(schema[key]["default"], getattr(Settings(), key))
            for invalid in (-1, maximum + 1, True, 1.5, "2", None):
                with self.subTest(key=key, invalid=invalid), self.assertRaises(PiggyError):
                    Settings.from_dict({key: invalid})
            for valid in (0, maximum):
                self.assertEqual(getattr(Settings.from_dict({key: valid}), key), valid)

    async def test_disabled_species_do_not_affect_duplicate_probability(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        first = await self.db.draw(self.user["id"], "a", "first", start)
        self.write_catalog([first["pig"]["id"], "new"])
        await self.db.catalog(read_catalog(self.root))
        with patch("core.database.secrets.randbelow", return_value=99) as random:
            result = await self.db.draw(
                self.user["id"],
                "a",
                "next",
                start + timedelta(days=1),
                duplicate_rate_cap=100,
                duplicate_pity=0,
            )
        random.assert_called_once_with(200)
        self.assertEqual(result["pig"]["id"], first["pig"]["id"])

    async def test_protection_messages_in_both_display_modes(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        await self.db.draw(self.user["id"], "a", "first", start)

        def drawn(settings, result, progress):
            labels = []
            original = ImageDraw.ImageDraw.text

            def record(canvas, xy, text, *args, **kwargs):
                labels.append(str(text))
                return original(canvas, xy, text, *args, **kwargs)

            with patch.object(ImageDraw.ImageDraw, "text", record):
                message = today_message(settings, self.root, self.user, result, progress)
            return message, " ".join(labels)

        with patch("core.database.secrets.randbelow", return_value=0):
            for offset, expected in ((1, "重复保护 1/2"), (2, "下次领取必出未收集小猪！")):
                result = await self.db.draw(
                    self.user["id"],
                    "a",
                    str(offset),
                    start + timedelta(days=offset),
                )
                progress = await self.db.collection(self.user["id"])
                hosted, text = drawn(Settings(), result, progress)
                self.assertFalse(hosted.local)
                self.assertIn(expected, text)
                local, text = drawn(Settings(display={"draw": False}), result, progress)
                self.assertTrue(local.local)
                self.assertIn(expected, text)
        _, text = drawn(Settings(duplicate_pity=0), result, progress)
        self.assertNotIn("下次领取", text)
        progress["unlocked"] = progress["active_total"]
        _, text = drawn(Settings(), result, progress)
        self.assertNotIn("下次领取", text)

    def write_catalog(self, ids):
        definitions = []
        for index, pig_id in enumerate(ids):
            Image.new("RGBA", (25, 30), (220, 40 + index, 60, 200)).save(
                self.root / "catalog" / "images" / f"{pig_id}.png"
            )
            definitions.append(
                {
                    "id": pig_id,
                    "name": pig_id,
                    "description": "固定描述",
                    "analysis": "固定性格",
                }
            )
        self.manifest.write_text(json.dumps(definitions), "utf-8")

    async def test_cross_group_concurrent_draw_is_one_record_even_with_separate_connections(
        self,
    ):
        now = datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc)
        results = await asyncio.gather(
            *[self.db.draw(self.user["id"], f"group-{i}", f"event-{i}", now) for i in range(32)]
        )
        self.assertEqual(sum(r["created"] for r in results), 1)
        self.assertEqual(len({r["pig"]["id"] for r in results}), 1)
        self.assertEqual((await self.db.collection(self.user["id"]))["total"], 1)
        await Database(self.root).initialize()
        again = await Database(self.root).draw(self.user["id"], "group-b", "retry", now)
        self.assertFalse(again["created"])
        self.assertEqual(again["count"], 1)

    async def test_duplicate_pig_replacement_merges_owned_copies(self):
        old_id, kept_id = "pighub0174", "pighub0099"
        self.write_catalog([old_id, kept_id])
        await self.db.catalog(read_catalog(self.root))
        await self.db.run(
            lambda c: c.executemany(
                "INSERT INTO collections VALUES(?,?,?,?,?)",
                (
                    (self.user["id"], old_id, 2, 20, 40),
                    (self.user["id"], kept_id, 3, 10, 30),
                ),
            )
        )
        await self.db.catalog(read_catalog(self.root))
        rows = await self.db.run(
            lambda c: c.execute(
                "SELECT pig_id,count,first_at,last_at FROM collections WHERE user_id=?",
                (self.user["id"],),
            ).fetchall()
        )
        self.assertEqual([tuple(row) for row in rows], [(kept_id, 5, 10, 40)])

    async def test_east_asia_midnight_and_inactive_history_snapshot(self):
        before = datetime(2026, 9, 12, 15, 59, 59, tzinfo=timezone.utc)
        first = await self.db.draw(self.user["id"], "a", "one", before)
        await self.db.draw(self.user["id"], "a", "two", before + timedelta(seconds=1))
        await self.db.catalog(
            [
                {
                    "id": "new",
                    "name": "新猪",
                    "description": "新增",
                    "analysis": "新增",
                    "asset": first["pig"]["asset"],
                    "enabled": True,
                    "sort_order": 0,
                }
            ]
        )
        old = await self.db.draw(self.user["id"], "b", "three", before)
        self.assertEqual(old["pig"], first["pig"])
        progress = await self.db.collection(self.user["id"])
        self.assertEqual(progress["total"], 2)
        self.assertEqual(progress["active_total"], 1)
        self.assertEqual(progress["unlocked"], 0)
        self.assertTrue(any(not p["enabled"] and p["count"] for p in progress["entries"]))

    async def test_names_are_not_identity_and_app_ids_are_isolated(self):
        second = await self.db.identify("app", "second", "group-a", "昵称")
        foreign = await self.db.identify("other-app", "member", "group-a", "昵称")
        self.assertEqual(len({self.user["id"], second["id"], foreign["id"]}), 3)
        updated = await self.db.identify("app", "member", "group-b", "新昵称")
        blank = await self.db.identify("app", "member", "group-a", "")
        self.assertEqual(updated["id"], self.user["id"])
        self.assertEqual(blank["nickname"], "新昵称")

    async def test_rank_uses_global_counts_but_local_participants_and_ties(self):
        now = datetime.now(timezone.utc)
        second = await self.db.identify("app", "second", "group-a", "昵称")
        hidden = await self.db.identify("app", "hidden", "group-other", "别群玩家")
        for user in (self.user, second, hidden):
            await self.db.draw(user["id"], "group-other", "event", now)
        result = await self.db.rankings("app", "group-a")
        self.assertEqual(len(result["total"]), 2)
        self.assertEqual([p["rank"] for p in result["total"]], [1, 1])
        self.assertEqual({p["open_id"] for p in result["total"]}, {"member", "second"})
        with self.assertRaises(PiggyError):
            await self.db.rankings("app", "")

    async def test_bad_manifest_does_not_replace_catalog(self):
        definitions = json.loads(self.manifest.read_text())
        definitions.append(definitions[0])
        self.manifest.write_text(json.dumps(definitions), "utf-8")
        with self.assertRaises(PiggyError):
            await self.db.catalog(read_catalog(self.root))
        self.assertEqual((await self.db.collection(self.user["id"]))["active_total"], 2)
        definitions = definitions[:1]
        definitions[0]["image"] = "../../outside.png"
        self.manifest.write_text(json.dumps(definitions), "utf-8")
        with self.assertRaises(PiggyError):
            read_catalog(self.root)

    async def test_corrupt_or_disguised_image_preserves_accepted_catalog(self):
        definitions = json.loads(self.manifest.read_text("utf-8"))
        asset_count = len(list((self.root / "assets").iterdir()))
        with io.BytesIO() as output:
            Image.new("RGB", (128, 128), "red").save(output, "JPEG")
            jpeg = output.getvalue()
        with io.BytesIO() as output:
            Image.new("RGB", (16, 16), "blue").save(output, "BMP")
            bmp = output.getvalue()
        for name, data in (("bad.jpg", jpeg[:-20]), ("disguised.png", bmp)):
            with self.subTest(name=name):
                (self.root / "catalog" / name).write_bytes(data)
                definitions[0]["image"] = name
                self.manifest.write_text(json.dumps(definitions), "utf-8")
                with self.assertRaises(PiggyError):
                    await self.db.catalog(read_catalog(self.root))
                self.assertEqual((await self.db.collection(self.user["id"]))["active_total"], 2)
                self.assertEqual(len(list((self.root / "assets").iterdir())), asset_count)

    async def test_legacy_wrong_suffix_uses_detected_format(self):
        definitions = json.loads(self.manifest.read_text("utf-8"))
        Image.new("RGB", (16, 16), "blue").save(self.root / "catalog" / "legacy.png", "WEBP")
        definitions[0]["image"] = "legacy.png"
        self.manifest.write_text(json.dumps(definitions), "utf-8")
        pigs = read_catalog(self.root)
        self.assertTrue(pigs[0]["asset"].endswith(".webp"))
        await self.db.catalog(pigs)

    async def test_restore_backup_and_rotation(self):
        await self.db.draw(self.user["id"], "a", "event")
        for _ in range(3):
            backup = await self.db.backup(2)
        self.assertEqual(len(list((self.root / "backups").glob("*.zip"))), 2)
        restore_root = self.root / "restored"
        with zipfile.ZipFile(backup) as archive:
            archive.extractall(restore_root)
        restored = Database(restore_root)
        await restored.initialize()
        self.assertEqual((await restored.collection(self.user["id"]))["total"], 1)
        self.assertEqual(len(read_catalog(restore_root)), 2)
        assets = list((restore_root / "assets").glob("*"))
        self.assertEqual(len(assets), 2)

    async def test_corrupt_database_is_not_overwritten(self):
        target = self.root / "broken"
        target.mkdir()
        path = target / "piggy.sqlite3"
        path.write_bytes(b"not a database")
        with self.assertRaises(PiggyError):
            await Database(target).initialize()
        self.assertEqual(path.read_bytes(), b"not a database")

    async def test_transaction_rolls_back_if_collection_write_fails(self):
        await self.db.run(
            lambda c: c.execute("""
            CREATE TRIGGER reject_collection BEFORE INSERT ON collections
            BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END
        """)
        )
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.draw(self.user["id"], "a", "event")
        count = await self.db.run(
            lambda c: c.execute("SELECT count(*) FROM draw_records").fetchone()[0]
        )
        self.assertEqual(count, 0)

    async def test_atlas_renders_all_slots_without_names_and_hides_locked_pigs(self):
        await self.db.draw(self.user["id"], "group-a", "draw")
        progress = await self.db.collection(self.user["id"])
        labels = []
        draw_text = ImageDraw.ImageDraw.text

        def record_text(canvas, xy, text, *args, **kwargs):
            labels.append(str(text))
            return draw_text(canvas, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record_text):
            message = await collection_message(
                Settings(display={"atlas": True}), self.root, self.user, progress, 1, True
            )
        self.assertEqual(len(message.images), 1)
        for pig in progress["entries"]:
            self.assertNotIn(pig["name"], labels)
        self.assertIn("已解锁 1 / 2", labels)
        with Image.open(io.BytesIO(message.images[0])) as card, card.convert("RGB") as img:
            self.assertEqual(img.width, 1080)
            for i, pig in enumerate(progress["entries"]):
                r, g, b = img.getpixel((113 + 122 * i, 439))
                if pig["count"]:
                    self.assertNotEqual(r, g)
                else:
                    self.assertEqual(r, g)
                    self.assertEqual(g, b)
        with self.assertRaises(PiggyError):
            await collection_message(Settings(), self.root, self.user, progress, 0, True)
        with self.assertRaises(PiggyError):
            await collection_message(
                Settings(display={"atlas": True}), self.root, self.user, progress, 2, True
            )

    async def test_locked_atlas_does_not_read_assets_or_reveal_changes_to_them(self):
        progress = await self.db.collection(self.user["id"])
        before = await collection_message(Settings(), self.root, self.user, progress, 1, True)
        for pig in progress["entries"]:
            pig["asset"] = "hidden-does-not-exist.png"
            pig["name"] = "不可泄露的猪名"
        after = await collection_message(Settings(), self.root, self.user, progress, 1, True)
        self.assertEqual(before.images, after.images)

    async def test_pen_only_renders_owned_names_and_empty_pen_is_a_card(self):
        progress = await self.db.collection(self.user["id"])
        empty = await collection_message(Settings(), self.root, self.user, progress, 1, False)
        self.assertEqual(len(empty.images), 1)
        self.assertTrue(empty.images[0].startswith(b"\x89PNG"))
        self.assertFalse((self.root / "cards").exists())
        await self.db.draw(self.user["id"], "a", "draw")
        progress = await self.db.collection(self.user["id"])
        labels = []
        original = ImageDraw.ImageDraw.text

        def record(canvas, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original(canvas, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            await collection_message(Settings(), self.root, self.user, progress, 1, False)
        for pig in progress["entries"]:
            self.assertEqual(pig["name"] in labels, bool(pig["count"]))

    async def test_pen_groups_owned_pigs_by_role(self):
        from core.battle import FALLBACK

        roles = {"pig": "辅助", "cat": "坦克", "dog": "治疗", "fox": "输出"}
        self.write_catalog(list(roles))
        battle = {
            "version": 1,
            "pigs": {
                pig_id: {
                    **FALLBACK,
                    "role": role,
                    "style": role,
                    "tags": [role, "自回" if pig_id == "cat" else "直伤"],
                }
                for pig_id, role in roles.items()
            },
        }
        (self.root / "catalog" / "battle.json").write_text(
            json.dumps(battle, ensure_ascii=False), "utf-8"
        )
        await self.db.catalog(read_catalog(self.root))
        for pig_id in ("pig", "cat", "dog", "fox"):
            await self.db.run(
                lambda c, pid=pig_id: c.execute(
                    "INSERT INTO collections VALUES(?,?,1,0,0)", (self.user["id"], pid)
                )
            )
        progress = await self.db.collection(self.user["id"])
        labels = []
        original = ImageDraw.ImageDraw.text

        def record(canvas, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original(canvas, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            await collection_message(Settings(), self.root, self.user, progress, 1, False)
        order = [name for name in ("坦克", "输出", "治疗", "辅助") if name in labels]
        self.assertEqual(order, ["坦克", "输出", "治疗", "辅助"])
        positions = {name: labels.index(name) for name in ("cat", "fox", "dog", "pig")}
        self.assertLess(positions["cat"], positions["fox"])
        self.assertLess(positions["fox"], positions["dog"])
        self.assertLess(positions["dog"], positions["pig"])
        self.assertIn("坦克 1 · 输出 1 · 治疗 1 · 辅助 1", labels)
        self.assertIn("自回", labels)
        self.assertIn("直伤", labels)

        labels.clear()
        with patch.object(ImageDraw.ImageDraw, "text", record):
            await collection_message(
                Settings(), self.root, self.user, progress, 1, False, ("坦克", "自回")
            )
        self.assertIn("cat", labels)
        self.assertNotIn("fox", labels)
        with self.assertRaises(PiggyError):
            await collection_message(
                Settings(), self.root, self.user, progress, 1, False, ("不存在标签",)
            )

    async def test_pen_sorts_each_role_by_level_then_rarity(self):
        from core.battle import FALLBACK
        from core.rarity import RARITIES

        entries = []
        for index, (pig_id, count, rarity) in enumerate(
            (
                ("low-mythic", 2, "神话"),
                ("high-common", 4, "普通"),
                ("high-legendary", 4, "传说"),
                ("capped-common", 25, "普通"),
                ("capped-rare", 20, "稀有"),
            )
        ):
            entries.append(
                {
                    "id": pig_id,
                    "count": count,
                    "rarity": rarity,
                    "sort_order": index,
                    "battle": json.dumps(
                        {**FALLBACK, "role": "输出", "tags": ["输出", "直伤"]},
                        ensure_ascii=False,
                    ),
                }
            )

        ordered = _pen_entries({"entries": entries}, level_cap=20)

        self.assertEqual(
            [pig["id"] for pig in ordered],
            [
                "capped-rare",
                "capped-common",
                "high-legendary",
                "high-common",
                "low-mythic",
            ],
        )
        self.assertGreater(RARITIES["神话"]["rank"], RARITIES["普通"]["rank"])

    async def test_current_96_catalog_is_one_card_and_future_expansion_has_navigation(self):
        progress = await self.db.collection(self.user["id"])
        template = progress["entries"][0]
        progress["entries"] = [{**template, "id": f"pig-{i}"} for i in range(96)]
        progress["active_total"] = 96
        one = await collection_message(
            Settings(display={"atlas": True}), self.root, self.user, progress, 1, True
        )
        self.assertEqual(len(one.images), 1)
        self.assertEqual(len(one.keyboard["content"]["rows"]), 3)
        progress["entries"] += [{**template, "id": f"extra-{i}"} for i in range(100)]
        progress["active_total"] = 196
        last = await collection_message(
            Settings(display={"atlas": True}), self.root, self.user, progress, 2, True
        )
        self.assertEqual(
            last.keyboard["content"]["rows"][-1]["buttons"][0]["action"]["data"], "小猪图鉴 1"
        )

    async def test_all_bundled_assets_validate_and_existing_catalog_is_preserved(self):
        resources = Path(__file__).resolve().parents[1] / "resources"
        other = self.root / "bundled"
        initialize_catalog(other, resources)
        definitions = read_catalog(other)
        bundled = json.loads((resources / "pigs.json").read_text("utf-8"))
        self.assertEqual(len(definitions), len(bundled))
        self.assertGreater(len(bundled), 900)
        manifest = other / "catalog" / "pigs.json"
        original = manifest.read_text("utf-8")
        manifest.write_text("[]", "utf-8")
        initialize_catalog(other, resources)
        self.assertEqual(manifest.read_text("utf-8"), "[]")
        manifest.write_text(original, "utf-8")


class ConfigAndButtonsTests(unittest.TestCase):
    def test_s3_config_trims_pasted_whitespace_and_names_invalid_fields(self):
        config = {
            "endpoint": " https://s3.example.com \n",
            "bucket": " pigs ",
            "access_key": " key ",
            "secret_key": " secret ",
            "public_base_url": " https://images.example.com ",
            "region": " auto ",
        }
        settings = Settings.from_dict(config)
        settings.check_host()
        self.assertEqual(settings.access_key, "key")
        self.assertEqual(settings.region, "auto")
        for field, value in (
            ("bucket", "https://example.com/pigs"),
            ("endpoint", "https://s3.example.com?token=x"),
            ("region", " "),
        ):
            with self.assertRaisesRegex(PiggyError, field):
                Settings.from_dict({**config, field: value}).check_host()

    def test_diagnostics_hide_secrets_proxy_passwords_and_signed_queries(self):
        settings = Settings(access_key="ACCESS_PRIVATE", secret_key="SECRET_PRIVATE")
        detail = safe_detail(
            "ACCESS_PRIVATE SECRET_PRIVATE https://user:password@proxy.local:8080/path "
            "https://s3.example.com/a?X-Amz-Signature=hidden-signature&token=hidden-token "
            "\nAuthorization: Bearer hidden-bearer\nSSL CERTIFICATE_VERIFY_FAILED",
            settings,
        )
        for secret in (
            "ACCESS_PRIVATE",
            "SECRET_PRIVATE",
            "user:password",
            "hidden-signature",
            "hidden-token",
            "hidden-bearer",
        ):
            self.assertNotIn(secret, detail)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", detail)
        self.assertNotIn("\n", detail)

    def test_today_card_mentions_requester_and_draws_description(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "assets").mkdir()
            Image.new("RGB", (16, 16)).save(root / "assets" / "pig.png")
            result = {
                "pig": {
                    "name": "猪",
                    "asset": "pig.png",
                    "description": "第一行\n第二行",
                    "analysis": '性格\n<qqbot-at-user id="someone" />',
                },
                "created": True,
                "new_species": True,
                "day": "2026-09-14",
                "count": 1,
            }
            user = {"id": 1, "open_id": "actual-member", "nickname": "不应出现在正文的昵称"}
            progress = {"unlocked": 1, "active_total": 96, "total": 1}
            labels = []
            original = ImageDraw.ImageDraw.text

            def record(canvas, xy, text, *args, **kwargs):
                labels.append(str(text))
                return original(canvas, xy, text, *args, **kwargs)

            with patch.object(ImageDraw.ImageDraw, "text", record):
                message = today_message(Settings(), root, user, result, progress)
                result["created"] = False
                repeated = today_message(Settings(), root, user, result, progress)
            self.assertTrue(message.text.startswith('<qqbot-at-user id="actual-member" />\n'))
            self.assertIn("![今日小猪", message.text)
            self.assertNotIn(user["nickname"], message.text)
            self.assertNotIn('<qqbot-at-user id="someone" />', message.text)
            self.assertIn("首次解锁！", labels)
            self.assertIn("今天已经抽过啦，还是这只", labels)
            self.assertTrue(repeated.text.startswith('<qqbot-at-user id="actual-member" />\n'))

    def test_both_rankings_only_show_ten_players_without_pagination(self):
        user = {"open_id": "requester"}
        players = [
            {"id": i, "nickname": f"玩家{i:02d}", "rank": i, "species": 20 - i, "total": 50 - i}
            for i in range(1, 13)
        ]
        labels = []
        original = ImageDraw.ImageDraw.text

        def record(canvas, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original(canvas, xy, text, *args, **kwargs)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(ImageDraw.ImageDraw, "text", record),
        ):
            message = ranking_message(
                Settings(display={"ranking": True}),
                Path(tmp),
                user,
                {"species": players, "total": players},
                {},
            )
        self.assertEqual(labels.count("玩家10"), 2)
        self.assertNotIn("玩家11", labels)
        self.assertNotIn("玩家12", labels)
        self.assertIn("收集种类榜", labels)
        self.assertIn("累计数量榜", labels)
        self.assertEqual(len(message.keyboard["content"]["rows"]), 3)
        self.assertFalse(message.local)

    def test_json_config_and_retry_validation(self):
        settings = Settings.from_dict(
            {
                "upload_headers": '{"Authorization":"Bearer secret"}',
                "success_value": "200",
            }
        )
        self.assertEqual(settings.upload_headers["Authorization"], "Bearer secret")
        self.assertEqual(settings.success_value, 200)
        self.assertNotIn("Bearer secret", repr(settings))
        for bad in (-1, 11, True, "3"):
            with self.assertRaises(PiggyError):
                Settings.from_dict({"image_retry_count": bad})

    def test_button_owner_prefix_and_navigation(self):
        for config, prefix in (
            ({}, ""),
            ({"command_prefix": ""}, ""),
            ({"command_prefix": "/"}, "/"),
            ({"command_prefix": "!"}, "!"),
            ({"command_prefix": "云云 "}, "云云 "),
        ):
            board = keyboard(Settings.from_dict(config), "member", "小猪图鉴", 2, 3)
            rows = board["content"]["rows"]
            actions = [b["action"] for row in rows for b in row["buttons"]]
            self.assertEqual(
                [a["data"] for a in actions],
                [
                    prefix + c
                    for c in (
                        "今日小猪",
                        "小猪图鉴",
                        "小猪排行",
                        "我的猪圈",
                        "小猪商店",
                        "猪副本",
                        "小猪玩法",
                        "小猪属性 ",
                        "小猪交换 ",
                        "小猪挑战",
                        "小猪图鉴 1",
                        "小猪图鉴 3",
                    )
                ],
            )
            self.assertTrue(all(a["type"] == 2 for a in actions))
            for button in rows[-1]["buttons"]:
                self.assertEqual(button["action"]["permission"]["specify_user_ids"], ["member"])
                self.assertNotIn("enter", button["action"])
        self.assertNotIn("{{image:0}}", md("{{image:0}}"))


if __name__ == "__main__":
    unittest.main()
