import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from core.battle import FALLBACK, fighter, validate_entry
from core.config import PiggyError
from core.database import Database
from core.mythic import (
    MIN_LEVEL,
    MYTHIC_LIMITS,
    TRASH_COUNT,
    TRASH_ROLES,
    _add_schedule,
    affixes_for,
    rookie_blessing,
    run_reward_draws,
    simulate_mythic,
    weekly_reward_milestones,
)
from core.mythic_views import _battle_rows, mythic_battle_message
from core.raid import DUNGEONS, _RaidBattle, boss_fighter

NOW = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)
ROLES = ("坦克", "输出", "治疗", "辅助")
ENTRY = validate_entry("mythic-test", FALLBACK)


def _unit(name, role, seat):
    entry = copy.deepcopy(ENTRY)
    entry["role"] = role
    data = fighter({"id": name, "name": name, "asset": ""}, entry, 10, name)
    data["seat"] = seat
    return data


def fake_report(members, catalog, dungeon_key, key_level, seed, **kwargs):
    info = next(item for item in DUNGEONS if item["key"] == dungeon_key)
    phases = []
    for index in range(9):
        trash = index % 3 != 2
        phases.append(
            {
                "index": index + 1,
                "kind": "trash" if trash else "boss",
                "name": f"阶段{index + 1}",
                "stage": index // 3 + 1,
                "rounds": 4,
                "won": True,
                "force": (15, 15, 0, 17, 18, 0, 17, 18, 0)[index],
                "deaths": 0,
                "adds": (
                    [
                        {
                            "round": 2,
                            "seat": members[0]["seat"],
                            "trigger": members[0]["label"],
                            "name": "随机增援",
                            "added_hp": 10,
                            "pressure": 0,
                        }
                    ]
                    if index == 0
                    else []
                ),
                "log": ["测试"],
            }
        )
    return {
        "dungeon": info,
        "key_level": key_level,
        "limit": 80,
        "combat_rounds": 36,
        "death_penalty": 0,
        "rounds": 36,
        "deaths": 0,
        "forces": 100,
        "completed": True,
        "timed": True,
        "upgrade": 3,
        "score": key_level * 100 + 50,
        "add_count": 1,
        "phases": phases,
        "members": [
            {
                "seat": member["seat"],
                "user": member["user"],
                "pig": {k: v for k, v in member["pig"].items() if k != "battle"},
                "level": 5,
                "role": ROLES[member["seat"]],
                "deaths": 0,
                "dealt": 100,
                "healed": 20,
                "taken": 30,
            }
            for member in members
        ],
    }


class MythicEngineTests(unittest.TestCase):
    def test_all_seven_existing_dungeons_have_six_trash_packs_and_round_limits(self):
        keys = {item["key"] for item in DUNGEONS}
        self.assertEqual(set(MYTHIC_LIMITS), keys)
        self.assertEqual(TRASH_COUNT, 6)
        self.assertTrue(all(MYTHIC_LIMITS[key] > 0 for key in keys))

    def test_add_is_a_real_pig_unit_and_records_triggering_pig(self):
        heroes = [_unit(f"玩家{i}的猪{i}", role, i) for i, role in enumerate(ROLES)]
        boss = boss_fighter(
            {"id": "dummy", "name": "木桩", "asset": ""},
            ENTRY,
            1,
            "mythic-trash",
            "木桩",
        )
        boss["stats"]["hp"] *= 5
        add = _unit("猪圈小怪", "输出", None)
        add["seat"] = None
        battle = _RaidBattle(
            heroes,
            boss,
            "mythic-trash",
            {
                "add_budget": 1,
                "add_trigger_action": 1,
                "add_names": ("猪圈小怪",),
                "add_units": [add],
                "require_all_foes": True,
                "disable_ultimate": True,
            },
            7,
        ).run()

        self.assertEqual(len(battle["add_events"]), 1)
        self.assertTrue(all(event["seat"] in range(4) for event in battle["add_events"]))
        self.assertTrue(all("猪" in event["trigger"] for event in battle["add_events"]))
        self.assertEqual(battle["add_events"][0]["name"], "猪圈小怪")
        self.assertIn("作为小怪加入 Boss 战", "\n".join(battle["log"]))

    def test_add_is_rolled_once_per_run_at_thirty_percent_and_capped_at_two(self):
        counts = [len(_add_schedule(seed)) for seed in range(3000)]
        triggered = sum(bool(count) for count in counts) / len(counts)
        self.assertGreater(triggered, 0.27)
        self.assertLess(triggered, 0.33)
        self.assertEqual(set(counts), {0, 1, 2})

    def test_affixes_unlock_at_fixed_key_levels(self):
        self.assertEqual(len(affixes_for(1, 2, 7)), 1)
        self.assertEqual([a["id"] for a in affixes_for(1, 7, 7)][1:], ["volcanic"])
        self.assertEqual(
            [a["id"] for a in affixes_for(1, 12, 7)][1:], ["volcanic", "grievous"]
        )

    def test_rookie_blessing_only_applies_to_all_low_level_parties_and_low_keys(self):
        self.assertEqual(rookie_blessing([5, 7, 8, 9], 2)["atk"], 50)
        self.assertEqual(rookie_blessing([5, 7, 8, 9], 4)["atk"], 20)
        self.assertIsNone(rookie_blessing([5, 7, 8, 9], 5))
        self.assertIsNone(rookie_blessing([5, 7, 8, 10], 2))

    def test_reward_tiers_require_a_timed_run_and_scale_with_key_level(self):
        self.assertEqual([run_reward_draws(level, True) for level in (2, 7, 12, 17)], [0, 1, 2, 3])
        self.assertEqual(run_reward_draws(20, False), 0)
        self.assertEqual(
            [reward["id"] for reward in weekly_reward_milestones(17, True)],
            ["rare", "epic", "mythic", "ten-pull"],
        )
        self.assertEqual(weekly_reward_milestones(20, False), [])

    def test_full_route_has_six_trash_packs_three_bosses_and_one_hundred_forces(self):
        members = []
        for seat, role in enumerate(ROLES):
            pig = {"id": f"hero-{seat}", "name": f"英雄{seat}", "asset": "", "battle": ""}
            members.append(
                {
                    "seat": seat,
                    "user": {"id": seat + 1, "nickname": f"玩家{seat}", "alias": ""},
                    "pig": pig,
                    "count": MIN_LEVEL,
                    "label": f"玩家{seat}的英雄{seat}",
                }
            )
        catalog = {
            slot: {"id": slot, "name": slot, "asset": "", "battle": ""}
            for item in DUNGEONS
            for slot in item["bosses"]
        }
        for role in TRASH_ROLES:
            entry = copy.deepcopy(FALLBACK)
            entry["role"] = role
            pig_id = f"trash-{role}"
            catalog[pig_id] = {
                "id": pig_id,
                "name": f"冰雪{role}猪",
                "asset": "",
                "battle": json.dumps(entry, ensure_ascii=False),
            }
        result_template = {
            "won": True,
            "timeout": False,
            "rounds": 2,
            "log": [],
            "add_events": [],
            "heroes": [],
            "boss": {"hp": 0, "max_hp": 100},
        }

        def win(heroes, boss, slot, cfg, seed):
            return {
                **result_template,
                "heroes": [
                    {
                        "seat": hero["seat"],
                        "hp": hero["stats"]["hp"] // 2,
                        "max_hp": hero["stats"]["hp"],
                        "alive": True,
                        "dealt": 10,
                        "healed": 0,
                        "taken": 0,
                    }
                    for hero in heroes
                ],
            }

        with patch("core.mythic.simulate_raid", win):
            report = simulate_mythic(members, catalog, 1, 2, 7, add_chance=0)

        self.assertTrue(report["completed"])
        self.assertEqual(report["forces"], 100)
        self.assertEqual(len(report["phases"]), 9)
        self.assertEqual(sum(p["kind"] == "trash" for p in report["phases"]), 6)
        self.assertEqual(sum(p["kind"] == "boss" for p in report["phases"]), 3)
        self.assertTrue(all(phase["enemy"]["id"] in catalog for phase in report["phases"]))
        self.assertTrue(all(len(phase["enemies"]) == 3 for phase in report["phases"] if phase["kind"] == "trash"))
        self.assertTrue(all("style" in member and "hp_ratio" in member for member in report["members"]))
        self.assertTrue(all(0.45 <= member["hp_ratio"] <= 0.55 for member in report["members"]))

    def test_death_special_kill_and_critical_logs_are_marked_important(self):
        report = fake_report(
            [
                {
                    "seat": seat,
                    "user": {"id": seat + 1, "nickname": f"玩家{seat}"},
                    "pig": {"id": f"pig-{seat}", "name": f"小猪{seat}"},
                    "label": f"玩家{seat}的小猪{seat}",
                }
                for seat in range(4)
            ],
            {},
            1,
            2,
            7,
        )
        report["phases"][0]["log"] = [
            "R1 普通攻击造成伤害",
            "R1 小猪倒下了",
            "R2 技能造成 99 伤害（暴击）",
            "【场地事件】特殊情况发生",
            "R3 完成击杀",
        ]
        target_logs = set(report["phases"][0]["log"])
        kinds = [kind for kind, line in _battle_rows(report) if line in target_logs]
        self.assertEqual(kinds, ["log", "important", "important", "important", "important"])

    def test_detailed_report_is_a_png_below_ten_megabytes(self):
        members = [
            {
                "seat": seat,
                "user": {"id": seat + 1, "nickname": f"玩家{seat}", "alias": ""},
                "pig": {"id": f"pig-{seat}", "name": f"小猪{seat}"},
                "label": f"玩家{seat}的小猪{seat}",
            }
            for seat in range(4)
        ]
        report = fake_report(members, {}, 1, 2, 7)
        report["phases"][0]["log"] = [f"第 {index} 条详细战斗日志" for index in range(250)]
        message = mythic_battle_message(Path.cwd(), report, test=True)
        self.assertEqual(message.images[0][:8], b"\x89PNG\r\n\x1a\n")
        self.assertLessEqual(len(message.images[0]), 10 * 1024 * 1024)
        self.assertTrue(message.local)


class MythicDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name))
        await self.db.initialize()
        definitions = []
        for index, slot in enumerate(slot for item in DUNGEONS for slot in item["bosses"]):
            definitions.append(
                {
                    "id": slot,
                    "name": f"Boss{index}",
                    "description": "测试",
                    "analysis": "测试",
                    "asset": "",
                    "enabled": True,
                    "sort_order": index,
                    "battle": json.dumps(FALLBACK, ensure_ascii=False),
                }
            )
        self.hero_ids = []
        for index, role in enumerate(ROLES):
            pig_id = f"mythic-{index}"
            entry = copy.deepcopy(FALLBACK)
            entry["role"] = role
            definitions.append(
                {
                    "id": pig_id,
                    "name": f"秘境{role}猪",
                    "description": "测试",
                    "analysis": "测试",
                    "asset": "",
                    "enabled": True,
                    "sort_order": 100 + index,
                    "battle": json.dumps(entry, ensure_ascii=False),
                }
            )
            self.hero_ids.append(pig_id)
        for order, (pig_id, name) in enumerate(
            (("pig", "稀有测试猪"), ("pork-skewer", "史诗测试猪"), ("wild-boar", "神话测试猪")),
            200,
        ):
            definitions.append(
                {
                    "id": pig_id,
                    "name": name,
                    "description": "层数奖励测试",
                    "analysis": "测试",
                    "asset": "",
                    "enabled": True,
                    "sort_order": order,
                    "battle": json.dumps(FALLBACK, ensure_ascii=False),
                }
            )
        await self.db.catalog(definitions)
        self.users = [
            await self.db.identify("app", f"mythic-user-{i}", "group", f"玩家{i}")
            for i in range(4)
        ]
        for index, user in enumerate(self.users):
            await self.db.run(
                lambda conn, uid=user["id"], pid=self.hero_ids[index]: conn.execute(
                    "INSERT INTO collections VALUES(?,?,5,0,0)", (uid, pid)
                )
            )
            await self._unlock(user)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def _unlock(self, user):
        def write(conn):
            for info in DUNGEONS:
                cursor = conn.execute(
                    "INSERT INTO raids(app_id,group_id,day,dungeon,leader,status,stage,seed,"
                    "created_at,expires_at,updated_at) VALUES(?,?,?,?,?,'cleared',3,1,0,0,0)",
                    ("app", "history", "2026-01-01", info["key"], user["id"]),
                )
                conn.execute(
                    "INSERT INTO raid_members(raid_id,user_id,pig_id,seat,joined_at) VALUES(?,?,?,?,0)",
                    (cursor.lastrowid, user["id"], self.hero_ids[0], 0),
                )

        await self.db.run(write)

    async def _claim_leader_key(self):
        return await self.db.claim_mythic_key(self.users[0]["id"], NOW)

    async def test_key_starts_empty_and_can_only_be_claimed_once(self):
        status = await self.db.mythic_status("app", "group", self.users[0]["id"], NOW)
        self.assertIsNone(status["key"])
        self.assertEqual(status["key_floor"], 2)
        first = await self._claim_leader_key()
        second = await self._claim_leader_key()
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["key"]["level"], 2)
        self.assertEqual(first["key"]["user_id"], self.users[0]["id"])

    async def test_all_dungeon_timed_progress_creates_a_permanent_key_floor(self):
        await self._claim_leader_key()
        user_id = self.users[0]["id"]

        def add_progress(conn):
            conn.execute(
                "UPDATE mythic_keys SET dungeon=1,level=5 WHERE user_id=?", (user_id,)
            )
            for info in DUNGEONS:
                cursor = conn.execute(
                    "INSERT INTO mythic_raids(app_id,group_id,week,dungeon,key_level,leader,"
                    "status,seed,report,created_at,expires_at,updated_at) "
                    "VALUES('app','floor-history','2026-10-19',?,?,?,'cleared',1,'{}',0,0,0)",
                    (info["key"], 4, user_id),
                )
                conn.execute(
                    "INSERT INTO mythic_members VALUES(?,?,?,?,0)",
                    (cursor.lastrowid, user_id, self.hero_ids[0], 0),
                )
                conn.execute(
                    "INSERT INTO mythic_records(raid_id,app_id,group_id,week,team_key,dungeon,"
                    "key_level,completed,timed,upgrade,rounds,limit_rounds,deaths,add_count,score,"
                    "report,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        cursor.lastrowid,
                        "app",
                        "floor-history",
                        "2026-10-19",
                        str(user_id),
                        info["key"],
                        4,
                        1,
                        1,
                        1,
                        40,
                        80,
                        0,
                        0,
                        420,
                        "{}",
                        0,
                    ),
                )

        await self.db.run(add_progress)
        lowered = await self.db.lower_mythic_key(user_id, NOW)
        blocked = await self.db.lower_mythic_key(user_id, NOW)
        self.assertEqual((lowered["old_level"], lowered["key"]["level"]), (5, 4))
        self.assertEqual(blocked["floor"], 4)
        self.assertFalse(blocked["changed"])

        await self.db.open_mythic(
            "app", "group", user_id, self.hero_ids[0], now=NOW, seed=23
        )

        def overtime(*args, **kwargs):
            report = fake_report(*args, **kwargs)
            report.update(timed=False, upgrade=-1, rounds=report["limit"] + 1)
            return report

        result = None
        with patch("core.database.simulate_mythic", overtime):
            for index, user in enumerate(self.users[1:], 1):
                result = await self.db.join_mythic(
                    "app", "group", user["id"], self.hero_ids[index], now=NOW
                )
        self.assertEqual(result["battle"]["key_floor"], 4)
        self.assertEqual(result["battle"]["next_key"]["level"], 4)
        self.assertNotEqual(result["battle"]["next_key"]["dungeon"]["key"], 1)

    async def test_level_five_gate_is_checked_when_opening(self):
        await self._claim_leader_key()
        await self.db.run(
            lambda conn: conn.execute(
                "UPDATE collections SET count=4 WHERE user_id=? AND pig_id=?",
                (self.users[0]["id"], self.hero_ids[0]),
            )
        )
        with self.assertRaisesRegex(PiggyError, "Lv5"):
            await self.db.open_mythic(
                "app", "group", self.users[0]["id"], self.hero_ids[0], now=NOW
            )

    async def test_level_five_gate_is_rechecked_on_join_and_departure(self):
        await self._claim_leader_key()
        await self.db.open_mythic(
            "app", "group", self.users[0]["id"], self.hero_ids[0], now=NOW, seed=7
        )
        await self.db.run(
            lambda conn: conn.execute(
                "UPDATE collections SET count=4 WHERE user_id=? AND pig_id=?",
                (self.users[1]["id"], self.hero_ids[1]),
            )
        )
        with self.assertRaisesRegex(PiggyError, "Lv5"):
            await self.db.join_mythic(
                "app", "group", self.users[1]["id"], self.hero_ids[1], now=NOW
            )
        await self.db.run(
            lambda conn: conn.execute(
                "UPDATE collections SET count=5 WHERE user_id=? AND pig_id=?",
                (self.users[1]["id"], self.hero_ids[1]),
            )
        )
        for index in (1, 2):
            await self.db.join_mythic(
                "app", "group", self.users[index]["id"], self.hero_ids[index], now=NOW
            )
        # The leader traded away one copy after opening; the fourth join must
        # roll back instead of starting with an ineligible pig.
        await self.db.run(
            lambda conn: conn.execute(
                "UPDATE collections SET count=4 WHERE user_id=? AND pig_id=?",
                (self.users[0]["id"], self.hero_ids[0]),
            )
        )
        with self.assertRaisesRegex(PiggyError, "Lv5"):
            await self.db.join_mythic(
                "app", "group", self.users[3]["id"], self.hero_ids[3], now=NOW
            )
        count = await self.db.run(
            lambda conn: conn.execute(
                "SELECT count(*) FROM mythic_members WHERE raid_id=(SELECT max(id) FROM mythic_raids)"
            ).fetchone()[0]
        )
        self.assertEqual(count, 3)

    async def test_four_players_complete_run_update_key_and_team_rankings(self):
        await self._claim_leader_key()
        opened = await self.db.open_mythic(
            "app", "group", self.users[0]["id"], self.hero_ids[0], now=NOW, seed=7
        )
        old_level = opened["raid"]["key_level"]
        result = None
        with patch("core.database.simulate_mythic", fake_report):
            for index, user in enumerate(self.users[1:], 1):
                result = await self.db.join_mythic(
                    "app",
                    "group",
                    user["id"],
                    self.hero_ids[index],
                    now=NOW,
                )

        self.assertTrue(result["started"])
        self.assertTrue(result["battle"]["report"]["timed"])
        self.assertEqual(result["battle"]["report"]["add_count"], 1)
        self.assertEqual(result["battle"]["next_key"]["level"], old_level + 3)
        board = await self.db.mythic_rankings("app", "group", NOW)
        self.assertEqual(len(board["weekly"]), 1)
        self.assertEqual(len(board["weekly"][0]["team"]), 4)
        self.assertEqual(board["total"][0]["dungeons"], 1)
        self.assertEqual(board["total"][0]["score"], old_level * 100 + 50)

    async def test_low_timed_key_rewards_entered_pig_and_weekly_rare_pig(self):
        await self._claim_leader_key()
        await self.db.open_mythic(
            "app", "group", self.users[0]["id"], self.hero_ids[0], now=NOW, seed=11
        )
        result = None
        with patch("core.database.simulate_mythic", fake_report):
            for index, user in enumerate(self.users[1:], 1):
                result = await self.db.join_mythic(
                    "app", "group", user["id"], self.hero_ids[index], now=NOW
                )

        rewards = result["battle"]["rewards"]
        self.assertEqual(len(rewards), 4)
        self.assertTrue(all(reward["draw"] == 0 for reward in rewards))
        self.assertTrue(all(reward["pig"]["id"] == self.hero_ids[index] for index, reward in enumerate(rewards)))
        self.assertTrue(all([weekly["id"] for weekly in reward["weekly"]] == ["rare"] for reward in rewards))
        for index, user in enumerate(self.users):
            entered = await self.db.run(
                lambda conn, uid=user["id"], pid=self.hero_ids[index]: conn.execute(
                    "SELECT count FROM collections WHERE user_id=? AND pig_id=?", (uid, pid)
                ).fetchone()[0]
            )
            self.assertEqual(entered, 6)
        bonus_rows = await self.db.run(
            lambda conn: conn.execute("SELECT count(*) FROM draw_bonus").fetchone()[0]
        )
        self.assertEqual(bonus_rows, 0)

    async def test_high_timed_key_grants_draws_and_every_unclaimed_weekly_tier(self):
        await self.db.run(
            lambda conn: conn.execute(
                "INSERT INTO mythic_keys(user_id,dungeon,level,updated_at) VALUES(?,?,17,0)",
                (self.users[0]["id"], 1),
            )
        )
        await self.db.open_mythic(
            "app", "group", self.users[0]["id"], self.hero_ids[0], now=NOW, seed=13
        )
        result = None
        with patch("core.database.simulate_mythic", fake_report):
            for index, user in enumerate(self.users[1:], 1):
                result = await self.db.join_mythic(
                    "app", "group", user["id"], self.hero_ids[index], now=NOW
                )

        rewards = result["battle"]["rewards"]
        self.assertTrue(all(reward["draw"] == 3 for reward in rewards))
        self.assertTrue(
            all(
                [weekly["id"] for weekly in reward["weekly"]]
                == ["rare", "epic", "mythic", "ten-pull"]
                for reward in rewards
            )
        )
        self.assertTrue(all(sum(len(weekly["items"]) for weekly in reward["weekly"]) == 13 for reward in rewards))
        bonus = await self.db.run(
            lambda conn: [row[0] for row in conn.execute("SELECT count FROM draw_bonus ORDER BY user_id")]
        )
        weekly_rows = await self.db.run(
            lambda conn: conn.execute("SELECT count(*) FROM mythic_weekly_rewards").fetchone()[0]
        )
        self.assertEqual(bonus, [3, 3, 3, 3])
        self.assertEqual(weekly_rows, 16)
        message = mythic_battle_message(Path.cwd(), result["battle"])
        self.assertLessEqual(len(message.images[0]), 10 * 1024 * 1024)

        await self.db.open_mythic(
            "app", "group", self.users[0]["id"], self.hero_ids[0], now=NOW, seed=19
        )
        repeated = None
        with patch("core.database.simulate_mythic", fake_report):
            for index, user in enumerate(self.users[1:], 1):
                repeated = await self.db.join_mythic(
                    "app", "group", user["id"], self.hero_ids[index], now=NOW
                )
        self.assertTrue(all(not reward["weekly"] for reward in repeated["battle"]["rewards"]))
        weekly_rows = await self.db.run(
            lambda conn: conn.execute("SELECT count(*) FROM mythic_weekly_rewards").fetchone()[0]
        )
        self.assertEqual(weekly_rows, 16)

    async def test_completed_but_overtime_run_grants_no_reward(self):
        await self._claim_leader_key()
        await self.db.open_mythic(
            "app", "group", self.users[0]["id"], self.hero_ids[0], now=NOW, seed=17
        )

        def overtime(*args, **kwargs):
            report = fake_report(*args, **kwargs)
            report.update(timed=False, upgrade=-1, rounds=report["limit"] + 1)
            return report

        result = None
        with patch("core.database.simulate_mythic", overtime):
            for index, user in enumerate(self.users[1:], 1):
                result = await self.db.join_mythic(
                    "app", "group", user["id"], self.hero_ids[index], now=NOW
                )

        self.assertTrue(result["battle"]["report"]["completed"])
        self.assertFalse(result["battle"]["report"]["timed"])
        self.assertEqual(result["battle"]["rewards"], [])
        counts = await self.db.run(
            lambda conn: [
                conn.execute(
                    "SELECT count FROM collections WHERE user_id=? AND pig_id=?",
                    (user["id"], self.hero_ids[index]),
                ).fetchone()[0]
                for index, user in enumerate(self.users)
            ]
        )
        self.assertEqual(counts, [5, 5, 5, 5])

    async def test_admin_preview_generates_report_without_database_writes(self):
        with patch("core.database.simulate_mythic", fake_report):
            report = await self.db.test_mythic(
                self.users[0], dungeon_key=3, key_level=8, level=10, seed=29
            )

        self.assertTrue(report["completed"])
        self.assertEqual(report["dungeon"]["key"], 3)
        self.assertEqual(report["key_level"], 8)
        records = await self.db.run(
            lambda conn: conn.execute("SELECT count(*) FROM mythic_records").fetchone()[0]
        )
        keys = await self.db.run(
            lambda conn: conn.execute("SELECT count(*) FROM mythic_keys").fetchone()[0]
        )
        self.assertEqual((records, keys), (0, 0))
