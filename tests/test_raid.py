import asyncio
import copy
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from core import raid_events
from core.battle import FALLBACK, fighter, validate_entry
from core.catalog import read_catalog
from core.config import PiggyError, Settings
from core.database import Database
from core.raid import DUNGEONS, _RaidBattle, boss_fighter, simulate_raid
from core.raid_events import (
    ENTRY_EVENTS,
    FIELD_EVENTS,
    INTERLUDE_EVENTS,
    field_event,
    new_config,
    new_rewards,
    resolve,
    roll_entry,
)
from core.views import (
    raid_battle_message,
    raid_detail_message,
    raid_list_message,
    raid_lobby_message,
)

NOW = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)
BOSS_IDS = [slot for item in DUNGEONS for slot in item["bosses"]]
ENTRY = validate_entry("fallback", FALLBACK)


def outcome(won=True, dead=(), rounds=6, timeout=False):
    """A fake simulate_raid: `dead` seats fall, everyone else ends at half health."""

    def fake(heroes, boss, slot_id, cfg, seed):
        return {
            "winner": 0 if won else 1,
            "won": won,
            "timeout": timeout,
            "rounds": rounds,
            "log": ["R1 测试战斗"],
            "heroes": [
                {
                    "seat": h.get("seat"),
                    "hp": 0 if h.get("seat") in dead else h["stats"]["hp"] // 2,
                    "max_hp": h["stats"]["hp"],
                    "alive": h.get("seat") not in dead,
                }
                for h in heroes
            ],
            "boss": {"hp": 0 if won else 9, "max_hp": boss["stats"]["hp"]},
            "field_events": [],
        }

    return fake


def event(event_id):
    return next(e for e in ENTRY_EVENTS + INTERLUDE_EVENTS if e["id"] == event_id)


def unit(name, level=5, seat=None):
    pig = {"id": name, "name": name, "asset": ""}
    data = fighter(pig, ENTRY, level, name)
    data["seat"] = seat
    return data


def battle(slot, heroes=4, cfg=None, seed=1):
    boss = boss_fighter({"id": slot, "name": "BOSS", "asset": ""}, ENTRY, 1, slot, "BOSS")
    party = [unit(f"猪{i}", seat=i) for i in range(heroes)]
    return _RaidBattle(party, boss, slot, cfg or new_config(), seed)


class RaidStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root)
        await self.db.initialize()
        images = self.root / "catalog" / "images"
        images.mkdir(parents=True)
        ids = BOSS_IDS + [f"p{i}" for i in range(6)]
        definitions = []
        for index, pig_id in enumerate(ids):
            Image.new("RGB", (8, 8), (index * 12, 90, 120)).save(images / f"{pig_id}.png")
            definitions.append(
                {"id": pig_id, "name": f"猪{pig_id}", "description": "描述", "analysis": "性格"}
            )
        (self.root / "catalog" / "pigs.json").write_text(json.dumps(definitions), "utf-8")
        await self.db.catalog(read_catalog(self.root))
        self.users = [await self.db.identify("app", f"u{i}", "group", f"玩家{i}") for i in range(6)]
        self.outsider = await self.db.identify("app", "out", "elsewhere", "路人")
        # Random events would change the payouts; tests that need them inject their own.
        for name, value in (("roll_entry", []), ("roll_interlude", event("nothing"))):
            patcher = patch(f"core.database.{name}", lambda *args, value=value: value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for index, user in enumerate(self.users):
            await self.own(user, f"p{index}", 2)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def own(self, user, pig_id, count):
        await self.db.run(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO collections VALUES(?,?,?,0,0)", (user["id"], pig_id, count)
            )
        )

    async def count(self, user, pig_id):
        return await self.db.pig_count(user["id"], pig_id)

    async def raid_row(self):
        return await self.db.run(
            lambda c: dict(c.execute("SELECT * FROM raids ORDER BY id DESC LIMIT 1").fetchone())
        )

    async def entries(self):
        return await self.db.run(
            lambda c: c.execute("SELECT count(*) FROM raid_entries").fetchone()[0]
        )

    async def fill(self, key=1, now=NOW, users=None, fake=None):
        users = users or self.users[:4]
        await self.db.open_raid(
            "app", "group", users[0]["id"], key, f"p{self.users.index(users[0])}", now
        )
        result = None
        with patch("core.database.simulate_raid", fake or outcome()):
            for user in users[1:]:
                result = await self.db.join_raid(
                    "app", "group", user["id"], f"p{self.users.index(user)}", now=now
                )
        return result

    async def test_fourth_member_starts_the_first_boss_and_locks_the_party(self):
        leader = self.users[0]
        opened = await self.db.open_raid("app", "group", leader["id"], 1, "p0", NOW)
        self.assertEqual(len(opened["raid"]["members"]), 1)
        with self.assertRaises(PiggyError):
            await self.db.open_raid("app", "group", self.users[1]["id"], 2, "p1", NOW)
        with self.assertRaises(PiggyError):
            await self.db.join_raid("app", "group", leader["id"], "p0", now=NOW)
        with self.assertRaises(PiggyError):
            await self.db.join_raid("app", "group", self.users[1]["id"], "p5", now=NOW)
        with patch("core.database.simulate_raid", outcome()):
            for user in self.users[1:3]:
                result = await self.db.join_raid(
                    "app", "group", user["id"], f"p{self.users.index(user)}", now=NOW
                )
                self.assertFalse(result["started"])
            self.assertEqual(await self.entries(), 0)
            result = await self.db.join_raid("app", "group", self.users[3]["id"], "p3", now=NOW)
        self.assertTrue(result["started"])
        self.assertEqual(result["battle"]["stage"], 1)
        self.assertEqual(result["battle"]["status"], "waiting")
        self.assertEqual(await self.entries(), 4)
        with self.assertRaises(PiggyError) as caught:
            await self.db.join_raid("app", "group", self.users[4]["id"], "p4", now=NOW)
        self.assertIn("不能中途加入", str(caught.exception))
        lobby = raid_lobby_message(Settings(), opened, "open")
        self.assertIn("1/4", lobby.text)
        self.assertIn("（输出）", lobby.text)
        self.assertIn("还缺职业：坦克 / 治疗 / 辅助", lobby.text)

    async def test_lobby_lists_pig_roles_and_missing_roles(self):
        roles = {"p0": "坦克", "p1": "输出", "p2": "治疗"}
        battle = {
            "pigs": {
                pig_id: {**FALLBACK, "role": role, "style": role} for pig_id, role in roles.items()
            }
        }
        (self.root / "catalog" / "battle.json").write_text(
            json.dumps(battle, ensure_ascii=False), "utf-8"
        )
        await self.db.catalog(read_catalog(self.root))
        opened = await self.db.open_raid("app", "group", self.users[0]["id"], 1, "p0", NOW)
        joined = await self.db.join_raid("app", "group", self.users[1]["id"], "p1", now=NOW)
        third = await self.db.join_raid("app", "group", self.users[2]["id"], "p2", now=NOW)
        lobby = raid_lobby_message(Settings(), third, "join")
        self.assertIn("猪p0（坦克）", lobby.text)
        self.assertIn("猪p1（输出）", lobby.text)
        self.assertIn("猪p2（治疗）", lobby.text)
        self.assertIn("还缺职业：辅助", lobby.text)
        self.assertIn(
            "还缺职业：输出 / 治疗 / 辅助",
            raid_lobby_message(Settings(), opened, "open").text,
        )
        self.assertIn(
            "还缺职业：治疗 / 辅助",
            raid_lobby_message(Settings(), joined, "join").text,
        )

    async def test_only_one_player_becomes_the_fourth_member(self):
        await self.db.open_raid("app", "group", self.users[0]["id"], 1, "p0", NOW)
        for index in (1, 2):
            await self.db.join_raid("app", "group", self.users[index]["id"], f"p{index}", now=NOW)
        with patch("core.database.simulate_raid", outcome()):
            results = await asyncio.gather(
                *(
                    Database(self.root).join_raid(
                        "app", "group", self.users[index]["id"], f"p{index}", now=NOW
                    )
                    for index in (3, 4, 5)
                ),
                return_exceptions=True,
            )
        self.assertEqual(sum(isinstance(r, dict) and r["started"] for r in results), 1)
        self.assertTrue(all(isinstance(r, (dict, PiggyError)) for r in results))
        self.assertEqual(await self.entries(), 4)

    async def test_members_can_leave_and_be_replaced_but_the_leader_disbands(self):
        await self.db.open_raid("app", "group", self.users[0]["id"], 1, "p0", NOW)
        await self.db.join_raid("app", "group", self.users[1]["id"], "p1", now=NOW)
        await self.db.join_raid("app", "group", self.users[2]["id"], "p2", now=NOW)
        left = await self.db.leave_raid("app", "group", self.users[1]["id"], NOW)
        self.assertFalse(left["cancelled"])
        self.assertEqual([m["seat"] for m in left["raid"]["members"]], [0, 1])
        await self.db.join_raid("app", "group", self.users[4]["id"], "p4", now=NOW)
        cancelled = await self.db.leave_raid("app", "group", self.users[0]["id"], NOW)
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual((await self.raid_row())["status"], "cancelled")
        self.assertEqual(await self.entries(), 0)
        await self.db.open_raid("app", "group", self.users[1]["id"], 1, "p1", NOW)

    async def test_lobby_expires_after_ten_minutes_without_spending_entries(self):
        await self.db.open_raid("app", "group", self.users[0]["id"], 1, "p0", NOW)
        await self.db.join_raid("app", "group", self.users[1]["id"], "p1", now=NOW)
        later = NOW + timedelta(minutes=11)
        with self.assertRaises(PiggyError) as caught:
            await self.db.join_raid("app", "group", self.users[2]["id"], "p2", now=later)
        self.assertIn("自动取消", str(caught.exception))
        self.assertEqual((await self.raid_row())["status"], "cancelled")
        self.assertEqual(await self.entries(), 0)
        status = await self.db.raid_status("app", "group", self.users[0]["id"], later)
        self.assertIsNone(status["raid"])
        await self.db.open_raid("app", "group", self.users[0]["id"], 1, "p0", later)

    async def test_each_dungeon_once_per_player_per_day(self):
        await self.fill(1, fake=outcome(won=False))
        with self.assertRaises(PiggyError) as caught:
            await self.db.open_raid("app", "group", self.users[0]["id"], 1, "p0", NOW)
        self.assertIn("每个副本每人每天 1 次", str(caught.exception))
        await self.db.open_raid("app", "group", self.users[0]["id"], 2, "p0", NOW)
        await self.db.leave_raid("app", "group", self.users[0]["id"], NOW)
        tomorrow = NOW + timedelta(days=1)
        await self.db.open_raid("app", "group", self.users[0]["id"], 1, "p0", tomorrow)

    async def test_fallen_pigs_are_lost_and_victory_rewards_everyone(self):
        result = await self.fill(1, fake=outcome(won=True, dead=(1,)))
        battle = result["battle"]
        boss_id = DUNGEONS[0]["bosses"][0]
        self.assertTrue(battle["won"])
        self.assertEqual(await self.count(self.users[1], "p1"), 1)
        self.assertEqual(await self.count(self.users[0], "p0"), 2)
        for user in self.users[:4]:
            self.assertEqual(await self.count(user, boss_id), 1)
        for user, expected in ((self.users[0], 1), (self.users[5], 1), (self.outsider, 0)):
            self.assertEqual(await self.db.bonus_count("app", "group", user["id"], NOW), expected)
        members = {m["user_id"]: m for m in battle["raid"]["members"]}
        self.assertEqual(members[self.users[1]["id"]]["alive"], 0)
        self.assertAlmostEqual(members[self.users[0]["id"]]["hp"], 0.5, delta=0.02)
        with self.assertRaises(PiggyError):
            await self.db.continue_raid("app", "group", self.users[2]["id"], now=NOW)
        with patch("core.database.simulate_raid", outcome(won=False, dead=(0, 2, 3))):
            second = await self.db.continue_raid("app", "group", self.users[0]["id"], now=NOW)
        self.assertEqual(second["stage"], 2)
        self.assertEqual(second["status"], "failed")
        # Seat 1 fell in stage 1 but is revived for the continue fight.
        self.assertEqual(second["revived"]["seat"], 1)
        self.assertEqual([f["seat"] for f in second["fighters"]], [0, 1, 2, 3])
        self.assertEqual(second["retired"], [])
        self.assertTrue(any(e["name"] == "战地急救" for e in second["events"]))
        self.assertEqual(await self.count(self.users[0], "p0"), 1)
        self.assertEqual(await self.count(self.users[0], DUNGEONS[0]["bosses"][1]), 0)
        with self.assertRaises(PiggyError):
            await self.db.continue_raid("app", "group", self.users[0]["id"], now=NOW)

    async def test_undecided_leader_retreats_after_ten_minutes(self):
        await self.fill(2)
        later = NOW + timedelta(minutes=11)
        with self.assertRaises(PiggyError) as caught:
            await self.db.continue_raid("app", "group", self.users[0]["id"], now=later)
        self.assertIn("自动撤退", str(caught.exception))
        self.assertEqual((await self.raid_row())["status"], "retreated")

    async def test_leader_retreats_and_keeps_the_rewards(self):
        await self.fill(3)
        with self.assertRaises(PiggyError):
            await self.db.retreat_raid("app", "group", self.users[1]["id"], NOW)
        result = await self.db.retreat_raid("app", "group", self.users[0]["id"], NOW)
        self.assertEqual(result["raid"]["status"], "retreated")
        self.assertEqual(await self.count(self.users[1], DUNGEONS[2]["bosses"][0]), 1)
        self.assertIn("撤退", raid_lobby_message(Settings(), result, "retreated").text)
        with self.assertRaises(PiggyError):
            await self.db.continue_raid("app", "group", self.users[0]["id"], now=NOW)

    async def test_three_wins_clear_the_dungeon(self):
        await self.fill(1)
        with (
            patch("core.database.simulate_raid", outcome()),
            patch("core.database.PERSONALITY_CHANCE", 1),
        ):
            second = await self.db.continue_raid("app", "group", self.users[0]["id"], now=NOW)
            third = await self.db.continue_raid("app", "group", self.users[0]["id"], now=NOW)
        self.assertEqual(second["status"], "waiting")
        self.assertEqual(third["status"], "cleared")
        self.assertEqual(third["next_boss"], "")
        for slot in DUNGEONS[0]["bosses"]:
            self.assertEqual(await self.count(self.users[2], slot), 1)
        self.assertEqual(len(third["personalities"]), 4)
        self.assertIsNotNone(await self.db.pig_personality(self.users[0]["id"], "p0"))
        with self.assertRaises(PiggyError):
            await self.db.continue_raid("app", "group", self.users[0]["id"], now=NOW)

    async def test_absent_pig_sits_out_and_missing_boss_is_possessed(self):
        await self.fill(1)
        await self.db.run(lambda c: c.execute("DELETE FROM collections WHERE pig_id='p2'"))
        await self.db.run(
            lambda c: c.execute("UPDATE pigs SET enabled=0 WHERE id=?", (DUNGEONS[0]["bosses"][1],))
        )
        with patch("core.database.simulate_raid", outcome()):
            second = await self.db.continue_raid("app", "group", self.users[0]["id"], now=NOW)
        self.assertEqual([m["user_id"] for m in second["absent"]], [self.users[2]["id"]])
        self.assertTrue(second["boss"]["possessed"])
        self.assertTrue(second["boss"]["label"].startswith("被附身的"))

    async def test_rare_events_pay_out(self):
        with patch(
            "core.database.roll_entry",
            lambda rng, key: [event("chest"), event("clover"), event("mimic")],
        ):
            result = await self.fill(1)
        battle = result["battle"]
        boss_id = DUNGEONS[0]["bosses"][0]
        self.assertEqual((battle["chest"], battle["bonus"], battle["copies"]), (1, 2, 2))
        self.assertGreaterEqual(await self.count(self.users[0], boss_id), 2)
        # 2 starting pigs, 2 boss pigs from the mimic and 1 random pig from the chest.
        self.assertEqual((await self.db.collection(self.users[0]["id"]))["total"], 5)
        self.assertEqual(await self.db.bonus_count("app", "group", self.users[5]["id"], NOW), 2)
        gained = sum(len(changes) for _, changes in battle["changes"])
        self.assertEqual(gained, 8)

    async def test_real_battle_poster_with_an_ally(self):
        await self.db.run(
            lambda c: c.executemany(
                "INSERT OR REPLACE INTO collections VALUES(?,?,5,0,0)",
                [(u["id"], f"p{i}") for i, u in enumerate(self.users[:4])],
            )
        )
        with patch("core.database.roll_entry", lambda rng, key: [event("ally")]):
            await self.db.open_raid("app", "group", self.users[0]["id"], 3, "p0", NOW)
            for index in (1, 2, 3):
                result = await self.db.join_raid(
                    "app", "group", self.users[index]["id"], f"p{index}", now=NOW, seed=11
                )
        battle = result["battle"]
        self.assertIsNotNone(battle["ally"])
        self.assertGreater(len(battle["result"]["log"]), 4)
        labels = []
        original = ImageDraw.ImageDraw.text

        def record(canvas, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original(canvas, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            poster = await raid_battle_message(Settings(), self.root, battle)
        self.assertTrue(poster.local)
        if poster.video is not None:
            self.assertIn("副本进行中", poster.notice)
            self.assertIn("第 1/3 关", poster.notice)
        quiet = await raid_battle_message(Settings(raid_video=False), self.root, battle)
        self.assertIsNone(quiet.video)
        self.assertEqual(quiet.notice, "")
        joined = "".join(labels)
        for text in ("BOSS", "援军", "野生援军", "伤害统计", "/回合", "战斗过程", "结算"):
            self.assertIn(text, joined)
        status = await self.db.raid_status("app", "group", self.users[0]["id"], NOW)
        for message in (
            raid_list_message(Settings(), self.root, self.users[0], status),
            raid_detail_message(Settings(), self.root, self.users[0], status, 6),
        ):
            labels.clear()
            with patch.object(ImageDraw.ImageDraw, "text", record):
                card = await message
            self.assertTrue(card.local)
            joined = "".join(labels)
            self.assertIn("今天", joined)
            self.assertIn("怎么玩", joined)

    async def test_raid_cards_list_every_dungeon_and_detail_text(self):
        status = await self.db.raid_status("app", "group", self.users[0]["id"], NOW)
        labels = []
        original = ImageDraw.ImageDraw.text

        def record(canvas, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original(canvas, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            await raid_list_message(Settings(), self.root, self.users[0], status)
        joined = "".join(labels)
        for item in DUNGEONS:
            self.assertIn(item["name"], joined)
        self.assertIn("三重锁链", joined)
        self.assertIn(f"{len(DUNGEONS)}/{len(DUNGEONS)}", joined)
        labels.clear()
        with patch.object(ImageDraw.ImageDraw, "text", record):
            await raid_detail_message(Settings(), self.root, self.users[0], status, 4)
        joined = "".join(labels)
        self.assertIn("亡灵猪墓", joined)
        self.assertIn("白骨重组", joined)
        self.assertIn("开启副本 4", joined)


class RaidEngineTests(unittest.TestCase):
    def test_ally_dodge_reaction_belongs_to_support_not_attacker(self):
        support_entry = copy.deepcopy(FALLBACK)
        support_entry.pop("engine", None)
        support_entry["role"] = "辅助"
        support_entry["skills"][1] = {
            "name": "伪装转交",
            "text": "让正在承伤的队友躲开下一击。",
            "cd": 2,
            "effects": [{"type": "evade", "count": 1, "target": "ally_tank"}],
        }
        support_entry["skills"][2] = {
            "name": "卧底回报",
            "text": "队友闪避后把情报转成全队攻势。",
            "when": "on_ally_dodge",
            "effects": [{"type": "buff", "stat": "atk", "pct": 20, "turns": 2, "target": "ally_all"}],
        }
        support = fighter(
            {"id": "support", "name": "辅助"}, validate_entry("support", support_entry), 5, "辅助"
        )
        tank = unit("坦克", seat=1)
        tank["role"] = "坦克"
        boss = boss_fighter(
            {"id": BOSS_IDS[0], "name": "BOSS", "asset": ""}, ENTRY, 1, BOSS_IDS[0], "BOSS"
        )
        fight = _RaidBattle([support, tank], boss, BOSS_IDS[0], new_config(), 17)
        helper, ward = fight.heroes
        ward.hp = ward.max_hp * 0.5

        fight.effect(helper, fight.boss, support_entry["skills"][1]["effects"][0], helper.actives[1])
        self.assertEqual(ward.evade, 1)
        fight.damage(fight.boss, ward, {"type": "damage", "power": 1.0})

        self.assertEqual(ward.evade, 0)
        self.assertTrue(any(buff["stat"] == "atk" for buff in helper.buffs))
        self.assertTrue(any(buff["stat"] == "atk" for buff in ward.buffs))

    def test_break_and_ally_hurt_reactions_follow_the_actual_defender(self):
        watcher_entry = copy.deepcopy(FALLBACK)
        watcher_entry.pop("engine", None)
        watcher_entry["role"] = "辅助"
        watcher_entry["skills"][1] = {
            "name": "急救回线",
            "text": "坦克受击后立刻补上一层保护。",
            "when": "on_ally_hurt",
            "effects": [{"type": "shield", "pct": 10, "target": "ally_tank"}],
        }
        tank_entry = copy.deepcopy(FALLBACK)
        tank_entry.pop("engine", None)
        tank_entry["role"] = "坦克"
        tank_entry["skills"][1] = {
            "name": "碎盾反应",
            "text": "自身护盾破裂时立刻加固。",
            "when": "on_shield_break",
            "effects": [{"type": "mitigate", "pct": 25, "turns": 2}],
        }
        watcher = fighter(
            {"id": "watcher", "name": "观察员"},
            validate_entry("watcher", watcher_entry),
            5,
            "观察员",
        )
        tank = fighter(
            {"id": "tank", "name": "坦克"}, validate_entry("tank", tank_entry), 5, "坦克"
        )
        tank["seat"] = 1
        boss = boss_fighter(
            {"id": BOSS_IDS[0], "name": "BOSS", "asset": ""}, ENTRY, 1, BOSS_IDS[0], "BOSS"
        )
        fight = _RaidBattle([watcher, tank], boss, BOSS_IDS[0], new_config(), 18)
        helper, ward = fight.heroes
        ward.shield = 1

        fight.damage(fight.boss, ward, {"type": "damage", "power": 2.0, "sure": True})

        self.assertTrue(ward.mitigates)
        self.assertGreater(ward.shield, 0)
        self.assertFalse(helper.mitigates)

    def setUp(self):
        self.boss_pig = {"id": "pig_god", "name": "猪神", "asset": ""}

    def test_same_seed_same_fight(self):
        party = [unit(f"猪{i}", seat=i) for i in range(4)]
        boss = boss_fighter(self.boss_pig, ENTRY, 3, "pig_god", "猪神")
        first = simulate_raid(party, boss, "pig_god", new_config(), 42)
        again = simulate_raid(party, boss, "pig_god", new_config(), 42)
        self.assertEqual(first["log"], again["log"])
        self.assertEqual(first["heroes"], again["heroes"])

    def test_boss_scales_by_stage_only_with_full_kit(self):
        early = boss_fighter(self.boss_pig, ENTRY, 1, "pig_god", "猪神")
        later = boss_fighter(self.boss_pig, ENTRY, 3, "pig_god", "猪神")
        again = boss_fighter(self.boss_pig, ENTRY, 1, "pig_god", "猪神")
        self.assertEqual(early["stats"], again["stats"])
        self.assertLess(early["stats"]["hp"], later["stats"]["hp"])
        self.assertLess(early["stats"]["atk"], later["stats"]["atk"])
        self.assertEqual(early["level"], 0)
        self.assertEqual(len(early["skills"]), 5)
        self.assertEqual(early["locked"], [])
        self.assertEqual(early["dot_basis"], fighter(self.boss_pig, ENTRY, 1)["stats"]["hp"])

    def test_goblin_lurks_out_of_reach(self):
        fight = battle("goblin-pig")
        fight.round = 3
        fight.mech.round_start(fight)
        before = fight.boss.hp
        text = fight.damage(
            fight.heroes[0], fight.boss, {"type": "damage", "power": 1.0, "sure": True}
        )
        self.assertIn("打了个空", text)
        self.assertEqual(fight.boss.hp, before)
        fight.round = 4
        weakest = fight.heroes[2]
        weakest.hp = 20
        fight.mech.round_start(fight)
        self.assertLess(weakest.hp, 20)

    def test_frozen_chills_into_freeze_and_shell_blocks_crits(self):
        fight = battle("frozen-pig")
        fight.mech.setup(fight)
        self.assertFalse(fight.mech.crit_against(fight, fight.heroes[0]))
        hero = fight.heroes[0]
        for _ in range(3):
            fight.mech.outgoing(fight, hero, 1)
        self.assertEqual((hero.stun, hero.stun_label, hero.chill), (1, "冻结", 0))
        fight.mech.incoming(fight, hero, fight.mech.shell + 5, {})
        self.assertLess(fight.boss.mods["def"], 0)

    def test_everest_caps_each_hit(self):
        fight = battle("everest-pig")
        capped = fight.adjust_hit(fight.heroes[0], fight.boss, 10_000, {})
        self.assertAlmostEqual(capped, fight.boss.max_hp * 0.06)

    def test_404_rolls_back_once(self):
        fight = battle("error-404-pig")
        boss = fight.boss
        fight.hurt(boss, boss.max_hp * 0.7)
        self.assertAlmostEqual(boss.ratio, 0.6)
        fight.hurt(boss, boss.max_hp * 0.4)
        self.assertLess(boss.ratio, 0.4)

    def test_mechanical_overheats_after_six_hits(self):
        fight = battle("mechanical-pig")
        self.assertGreater(fight.boss.stat("def"), 0)
        for _ in range(6):
            fight.mech.incoming(fight, fight.heroes[0], 10, {})
        self.assertEqual(fight.boss.stat("def"), 0)
        fight.mech.round_end(fight)
        fight.mech.round_end(fight)
        self.assertGreater(fight.boss.stat("def"), 0)

    def test_cyberpunk_overclocks_into_two_actions(self):
        fight = battle("cyberpunk-pig")
        self.assertEqual(fight.mech.extra_actions(fight), 0)
        fight.hurt(fight.boss, fight.boss.max_hp * 0.7)
        self.assertEqual(fight.mech.extra_actions(fight), 1)
        fight.round = 1
        fight.mech.round_start(fight)
        fight.mech.dispelled(fight)
        self.assertEqual(fight.boss.mods.get("atk", 0), 0)

    def test_demon_feeds_on_fallen_pigs(self):
        fight = battle("demon-pig")
        fight.boss.hp = fight.boss.max_hp * 0.5
        fight.hurt(fight.heroes[0], 10_000)
        self.assertAlmostEqual(fight.boss.ratio, 0.65)
        self.assertEqual(fight.boss.mods["atk"], 10)

    def test_chained_king_breaks_free(self):
        fight = battle("chained_crown_pig")
        fight.mech.setup(fight)
        self.assertEqual(fight.boss.mods["atk"], -30)
        fight.hurt(fight.boss, fight.boss.max_hp * 0.3)
        self.assertEqual((fight.mech.chains, fight.boss.mods["atk"]), (2, -20))
        fight.hurt(fight.boss, fight.boss.max_hp * 0.5)
        self.assertEqual((fight.mech.chains, fight.boss.mods["atk"]), (0, 0))
        self.assertTrue(fight.mech.returned)
        self.assertAlmostEqual(fight.boss.ratio, 0.35)

    def test_pig_god_guardians_must_fall_first(self):
        fight = battle("pig_god")
        self.assertIs(fight.target_for(fight.heroes[0]), fight.boss)
        fight.hurt(fight.boss, fight.boss.max_hp * 0.6)
        guards = fight.foes[1:]
        self.assertEqual(len(guards), 2)
        for _ in range(10):
            self.assertIn(fight.target_for(fight.heroes[0]), guards)
        for guard in guards:
            fight.hurt(guard, 10_000)
        self.assertIs(fight.target_for(fight.heroes[0]), fight.boss)

    def test_damage_is_credited_to_each_pig(self):
        fight = battle("goblin-pig")
        hero, other = fight.heroes[0], fight.heroes[1]
        fight.acting = hero
        fight.hurt(fight.boss, 40)
        fight.effect(hero, fight.boss, {"type": "dot", "pct": 5, "turns": 2}, {})
        fight.acting = None
        fight.dot_amount(fight.boss, fight.boss.dots[-1])
        fight.hurt(fight.boss, 10)
        fight.acting = other
        fight.hurt(hero, 30)
        self.assertEqual(fight.dealt[id(hero)], 50)
        self.assertNotIn(id(other), fight.dealt)
        result = battle("pig_god").run()
        dealt = [h["dealt"] for h in result["heroes"]]
        self.assertTrue(all(isinstance(value, int) for value in dealt))
        self.assertGreater(sum(dealt), 0)

    def test_zombie_poisons_and_raises_its_victims(self):
        fight = battle("zombie-pig")
        hero = fight.heroes[0]
        hero.dots.append({"pct": 5, "turns": 3, "label": "尸毒"})
        for _ in range(4):
            fight.mech.outgoing(fight, hero, 1)
        poison = [d for d in hero.dots if d.get("stacks")]
        self.assertEqual([(d["pct"], d["label"]) for d in poison], [(9, "尸毒×3")])
        self.assertEqual(hero.dots[0]["pct"], 5)
        self.assertEqual(fight.mech.heal_factor(fight, hero), 0.5)
        self.assertIs(fight.order()[-1], fight.boss)
        fight.acting = fight.boss
        fight.hurt(fight.heroes[1], 10_000)
        self.assertEqual(len(fight.foes), 2)
        self.assertTrue(fight.foes[1].label.startswith("僵尸"))
        self.assertEqual(fight.foes[1].side, 1)
        fight.acting = None
        fight.hurt(fight.heroes[2], 10_000)
        self.assertEqual(len(fight.foes), 2)

    def test_two_faced_ghost_flips_damage_types_and_possesses(self):
        fight = battle("pighub0876")
        fight.round = 1
        fight.mech.round_start(fight)
        self.assertEqual(fight.mech.dot_factor(fight, fight.boss), 2.0)
        self.assertEqual(fight.mech.incoming(fight, None, 10, {"true": True}), 20)
        self.assertEqual(fight.mech.incoming(fight, None, 10, {}), 5)
        fight.round = 3
        fight.mech.round_start(fight)
        self.assertEqual(fight.mech.dot_factor(fight, fight.boss), 0.0)
        hero = fight.heroes[0]
        hero.possessed = True
        boss_hp = fight.boss.hp
        fight.act(hero, fight.boss)
        self.assertFalse(hero.possessed)
        self.assertEqual(fight.boss.hp, boss_hp)
        self.assertTrue(any("被附身了" in line for line in fight.log))

    def test_skeleton_reassembles_twice(self):
        fight = battle("skeleton-pig")
        boss = fight.boss
        fight.hurt(boss, 10**6)
        self.assertTrue(boss.alive)
        self.assertAlmostEqual(boss.ratio, 0.35)
        fight.hurt(boss, 10**6)
        self.assertAlmostEqual(boss.ratio, 0.15)
        self.assertEqual(boss.mods["def"], 40)
        fight.hurt(boss, 10**6)
        self.assertFalse(boss.alive)
        self.assertEqual(fight.mech.incoming(fight, None, 10, {"hits": 3}), 6)

    def test_nezha_always_moves_first(self):
        fight = battle("pighub0233")
        for hero in fight.heroes:
            hero.mods["spd"] = 500
        for _ in range(5):
            self.assertIs(fight.order()[0], fight.boss)

    def test_fish_duke_decrees_and_scales(self):
        fight = battle("pighub0007")
        hero = fight.heroes[0]
        hero.hp = 10
        fight.round = 4
        fight.mech.decree, fight.mech.until = "禁疗令", 5
        self.assertEqual(fight.heal(hero, 50), 0)
        heal = {"type": "heal", "pct": 20}
        self.assertEqual(fight.effect(hero, fight.boss, heal, {}), "被禁令挡下了")
        fight.round = 6
        self.assertGreater(fight.heal(hero, 50), 0)
        fight.mech.decree, fight.mech.until = "禁技令", 7
        self.assertIs(fight.choose(hero, fight.boss), hero.actives[0])
        shields = sum(h.shield for h in fight.heroes)
        self.assertAlmostEqual(fight.mech.incoming(fight, hero, 10, {}), 8)
        self.assertEqual(fight.mech.scales, 19)
        self.assertGreater(sum(h.shield for h in fight.heroes), shields)

    def test_deep_sea_lure_crits_then_doubles_the_counter(self):
        fight = battle("pighub0830")
        hero = fight.heroes[0]
        hero.lured = True
        self.assertTrue(fight.crit_roll(hero, fight.boss))
        self.assertTrue(hero.marked)
        self.assertEqual(fight.adjust_hit(fight.boss, hero, 10, {}), 20)
        self.assertFalse(hero.marked)

    def test_alien_abducts_a_pig_off_the_field(self):
        fight = battle("alien-pig")
        fight.round = 4
        fight.mech.round_start(fight)
        taken = [h for h in fight.heroes if h.away]
        self.assertEqual(len(taken), 1)
        self.assertNotIn(taken[0], fight.present_heroes())
        for _ in range(20):
            self.assertIsNot(fight.target_for(fight.boss), taken[0])
        fight.hurt(fight.boss, fight.boss.max_hp * 0.6)
        self.assertTrue(fight.boss.away)
        self.assertIsNone(fight.target_for(fight.heroes[1]))

    def test_star_cluster_shares_damage(self):
        fight = battle("pighub0872")
        fight.mech.setup(fight)
        core, first, second = fight.foes
        before = [unit.hp for unit in fight.foes]
        share = fight.adjust_hit(fight.heroes[0], first, 90, {})
        self.assertAlmostEqual(share, 30)
        self.assertAlmostEqual(before[0] - core.hp, 30)
        self.assertAlmostEqual(before[2] - second.hp, 30)

    def test_cosmic_rings_absorb_every_segment(self):
        fight = battle("pighub0336")
        before = fight.boss.hp
        text = fight.damage(
            fight.heroes[0], fight.boss, {"type": "damage", "power": 1, "hits": 3, "sure": True}
        )
        self.assertIn("打了个空", text)
        self.assertEqual((fight.boss.hp, fight.mech.rings), (before, 0))
        fight.round = 3
        fight.mech.round_start(fight)
        self.assertEqual(fight.mech.rings, 1)

    def test_red_hot_counter_has_automatic_success_and_failure_paths(self):
        fight = battle("pighub0710")
        hero, boss = fight.heroes[0], fight.boss
        fight.mech.round_start(fight)
        before = boss.hp

        # One hit is the warned failure path: it is read and deals nothing.
        first = fight.adjust_hit(hero, boss, 100, {})
        self.assertEqual(first, 0)
        self.assertEqual(boss.hp, before)
        self.assertFalse(fight.mech.replied)

        # A later hit in the same automatic round opens the success path.
        fight.after_hit(hero, boss, 25)
        self.assertTrue(fight.mech.replied)
        self.assertAlmostEqual(boss.hp, before - boss.max_hp * 0.06)
        fight.flush()
        self.assertTrue(any("追问成功" in line for line in fight.log))

        # The window resets: next round's first hit is warned and blocked again.
        fight.mech.round_start(fight)
        self.assertFalse(fight.mech.read)
        self.assertEqual(fight.adjust_hit(hero, boss, 100, {}), 0)

    def test_raid_cleanse_clears_mechanic_flags_on_actual_targets(self):
        fight = battle("error-404-pig")
        caster, ally = fight.heroes[:2]
        ally.glitch = ally.possessed = ally.lured = ally.marked = True
        ally.chill = 2
        fight.effect(caster, fight.boss, {"type": "cleanse", "target": "ally_all"}, {})
        self.assertFalse(ally.glitch)
        self.assertFalse(ally.possessed)
        self.assertFalse(ally.lured)
        self.assertFalse(ally.marked)
        self.assertEqual(ally.chill, 0)

    def test_raid_ai_values_cleanse_and_dispel_against_real_mechanic_state(self):
        fight = battle("error-404-pig")
        support, ally = fight.heroes[:2]
        support.data["role"] = "辅助"
        support.actives = [
            support.actives[0],
            {
                "name": "团队净化",
                "text": "清掉机制状态。",
                "cd": 0,
                "when": None,
                "passive": False,
                "effects": [{"type": "cleanse", "target": "ally_all"}],
            },
            {
                "name": "普通输出",
                "text": "只造成伤害。",
                "cd": 0,
                "when": None,
                "passive": False,
                "effects": [{"type": "damage", "power": 1.5}],
            },
        ]
        ally.glitch = True
        self.assertEqual(fight.choose(support, fight.boss)["name"], "团队净化")

        ally.glitch = False
        fight.boss.shield = 100
        support.actives[1] = {
            "name": "拆解增益",
            "text": "驱散 boss。",
            "cd": 0,
            "when": None,
            "passive": False,
            "effects": [{"type": "dispel"}],
        }
        self.assertEqual(fight.choose(support, fight.boss)["name"], "拆解增益")

    def test_red_hot_only_gains_fire_from_support_that_really_resolves(self):
        fight = battle("pighub0710")
        support, ally = fight.heroes[:2]
        cleanse = {"type": "cleanse", "target": "ally_all"}
        delayed = {"type": "delay_heal", "pct": 20, "turns": 1, "target": "ally_lowest"}

        fight.effect(support, fight.boss, cleanse, {})
        fight.effect(support, fight.boss, delayed, {})
        self.assertEqual(fight.mech.stacks, 0)

        ally.glitch = True
        fight.effect(support, fight.boss, cleanse, {})
        self.assertEqual(fight.mech.stacks, 1)
        ally.hp = ally.max_hp / 2
        fight.effect(support, fight.boss, {"type": "heal", "pct": 20, "target": "ally_lowest"}, {})
        self.assertEqual(fight.mech.stacks, 2)

    def test_raid_extra_action_can_repeat_a_zero_cooldown_basic(self):
        fight = battle("goblin-pig", heroes=4, seed=9)
        hero = fight.heroes[0]
        hero.actives = [hero.actives[0]]

        def sneeze_every_round(running):
            running.sneezer = hero

        with patch("core.raid.field_event", sneeze_every_round):
            result = fight.run()
        round_one = [line for line in result["log"] if line.startswith("R1 ")]
        casts = [line for line in round_one if f"{hero.label}「{hero.actives[0]['name']}」" in line]
        self.assertEqual(len(casts), 2)

    def test_every_mechanic_has_searchable_automatic_counterplay(self):
        forbidden = ("停手", "手动", "玩家输入", "发送指令", "转火", "移动到")
        for slot in BOSS_IDS:
            fight = battle(slot)
            self.assertEqual(len(fight.mech.COUNTERS), 3, slot)
            for hint in fight.mech.COUNTERS:
                self.assertIn("【", hint, slot)
                self.assertTrue(all(word not in hint for word in forbidden), (slot, hint))

    def test_boss_mechanics_are_listed_for_every_boss(self):
        for slot in BOSS_IDS:
            fight = battle(slot)
            self.assertEqual(len(fight.mech.MECHANICS), 3, slot)
            result = fight.run()
            self.assertIn(result["winner"], (0, 1))

    def test_raid_shares_battle_runtime_state(self):
        """_RaidBattle must expose 1v1 internals used by copy / triggers / cleave."""
        fight = battle("goblin-pig")
        self.assertEqual(fight._copy_scale, 1.0)
        self.assertFalse(fight._in_trigger)
        before = fight.heroes[1].hp
        fight.hurt(fight.heroes[1], 10, attacker=fight.boss)
        self.assertLess(fight.heroes[1].hp, before)

    def test_mutual_guards_do_not_recurse(self):
        """Two tanks guarding each other used to blow the stack in raid.hurt."""
        fight = battle("goblin-pig")
        a, b = fight.heroes[0], fight.heroes[1]
        a.hp = a.max_hp
        b.hp = b.max_hp
        a.guards.append({"ward": b, "pct": 50, "turns": 3})
        b.guards.append({"ward": a, "pct": 50, "turns": 3})
        fight.hurt(b, 100, attacker=fight.boss)
        self.assertLess(a.hp, a.max_hp)
        self.assertLess(b.hp, b.max_hp)
        self.assertTrue(a.alive and b.alive)


class RaidEventTests(unittest.TestCase):
    def party(self):
        return [{"seat": i, "label": f"猪{i}", "hp": 0.5, "mods": {}} for i in range(4)]

    def test_every_entry_and_interlude_event_resolves(self):
        for item in ENTRY_EVENTS + INTERLUDE_EVENTS:
            party, cfg, rewards = self.party(), new_config(), new_rewards()
            resolved = resolve([item], party, random.Random(3), cfg, rewards, (("机制", ""),) * 3)
            self.assertNotIn("{", resolved[0]["text"], item["id"])
            fight = battle("pig_god", cfg=cfg)
            fight.run()

    def test_event_effects(self):
        party, cfg, rewards = self.party(), new_config(), new_rewards()
        rng = random.Random(1)
        resolve(
            [event("spring"), event("drums"), event("nap"), event("charm")],
            party,
            rng,
            cfg,
            rewards,
        )
        self.assertEqual([m["hp"] for m in party], [0.8] * 4)
        fight = battle("goblin-pig", cfg=cfg)
        self.assertEqual(fight.heroes[0].mods["atk"], 15)
        self.assertEqual(fight.boss.stun, 1)
        hero = fight.heroes[1]
        fight.hurt(hero, 10_000)
        self.assertEqual(hero.hp, 1.0)
        resolve([event("rockfall")] * 10, party, rng, cfg, rewards)
        self.assertTrue(all(m["hp"] == 0.01 for m in party))
        resolve([event("page")], party, rng, cfg, rewards)
        self.assertEqual(sum(m["mods"].get("atk", 0) for m in party), 10)
        resolve([event("tablet")], party, rng, cfg, rewards, (("甲", ""), ("乙", ""), ("丙", "")))
        self.assertIn(cfg["disabled"], (0, 1, 2))
        self.assertFalse(battle("pig_god", cfg=cfg).mech.on(cfg["disabled"]))

    def test_dungeon_events_stay_in_their_dungeon(self):
        rng = random.Random(5)
        seen = {item["key"]: set() for item in DUNGEONS}
        for key in seen:
            for _ in range(600):
                seen[key] |= {e["dungeon"] for e in roll_entry(rng, key)}
            self.assertEqual(seen[key], {None, key})

    def test_every_field_event(self):
        for item in FIELD_EVENTS:
            fight = battle("pig_god")
            fight.heroes[0].buffs.append({"stat": "atk", "pct": 10, "turns": 3})
            with (
                patch.object(raid_events, "FIELD_CHANCE", 1.0),
                patch.object(raid_events, "_pick", lambda rng, pool, item=item: item),
            ):
                field_event(fight)
            fight.flush()
            self.assertEqual(fight.field_events, [item["name"]])
            self.assertTrue(any(item["name"] in line for line in fight.log))

    def test_boss_normal_attacks_prefer_tanks_then_taunt(self):
        from core.raid_bosses import BossMechanics

        fight = battle("goblin-pig", heroes=4, seed=11)
        # Disable goblin mechanics that steal targeting.
        fight.mech.disabled = 0
        for hero, role in zip(fight.heroes, ("输出", "坦克", "治疗", "辅助")):
            hero.data["role"] = role
        picks = [BossMechanics().pick_target(fight, fight.heroes) for _ in range(40)]
        self.assertTrue(all(p.role == "坦克" for p in picks))

        fight.heroes[0].taunt = 3
        fight.heroes[0].data["role"] = "输出"
        taunt_picks = [BossMechanics().pick_target(fight, fight.heroes) for _ in range(20)]
        self.assertTrue(all(p is fight.heroes[0] for p in taunt_picks))

    def test_healing_basic_targets_party_damage_and_replays_both_chain_links(self):
        fight = battle("goblin-pig", heroes=4, seed=13)
        healer = fight.heroes[0]
        healer.data["role"] = "治疗"
        healer.actives = [
            healer.actives[0],
            {
                "name": "群体护理",
                "text": "为受伤队友包扎。",
                "cd": 0,
                "when": None,
                "passive": False,
                "effects": [{"type": "mass_heal", "pct": 20}],
            },
        ]
        wounded = fight.heroes[1]
        wounded.hp = wounded.max_hp / 2

        actions = fight.plan_actions(healer, fight.boss)
        self.assertEqual([skill["name"] for skill in actions], ["猪突猛进", "群体护理"])
        fight.act(healer, fight.boss)
        self.assertGreater(wounded.hp, wounded.max_hp / 2)
        replayed = [
            cue["name"]
            for step in fight.timeline
            for cue in step["cues"]
            if cue["t"] == "skill"
        ]
        self.assertEqual(replayed[-2:], ["猪突猛进", "群体护理"])

        for hero in fight.heroes:
            hero.hp = hero.max_hp
        healer.used_skills.clear()
        self.assertEqual(fight.plan_actions(healer, fight.boss), [healer.actives[0]])


if __name__ == "__main__":
    unittest.main()
