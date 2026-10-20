import asyncio
import json
import random
import secrets
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .battle import ROLES, SKILL_SLOTS, entry_for, fighter, level_for, simulate
from .catalog import PIG_REPLACEMENTS
from .config import PiggyError
from .mythic import (
    MIN_LEVEL,
    START_KEY_LEVEL,
    WEEKLY_REWARD_MILESTONES,
    run_reward_draws,
    simulate_mythic,
    weekly_reward_milestones,
)
from .personalities import PERSONALITIES, PERSONALITY_CHANCE, choices_for
from .raid import (
    DUNGEONS,
    PARTY_SIZE,
    REST_HEAL,
    REST_REVIVE_HP,
    TIMEOUT_MINUTES,
    boss_fighter,
    dungeon,
    mechanic_details,
    mechanics_for,
    simulate_raid,
)
from .raid_events import new_config, new_rewards, resolve, roll_entry, roll_interlude
from .rarity import rarity_for

EAST_ASIA = timezone(timedelta(hours=8))
# Safety valve only: with the default 50% chain chance this is never reached.
MAX_DRAW_ITEMS = 500
_SYSTEM_RANDOM = secrets.SystemRandom()
SHOP_SIZE = 5
REQUEST_LABELS = {"duel": "斗猪", "trade": "交换"}
RAID_ACTIVE = ("forming", "waiting")
DESPAIR_PARTY_SIZE = 4
DESPAIR_PIGS_PER_PLAYER = 2
DESPAIR_TIMEOUT_MINUTES = 10


def player_name(user) -> str:
    return user["alias"] or user["nickname"] or f"玩家 {user['id']:04d}"


def _owned(conn, user_id: int, pig_id: str) -> int:
    row = conn.execute(
        "SELECT count FROM collections WHERE user_id=? AND pig_id=?", (user_id, pig_id)
    ).fetchone()
    return row[0] if row else 0


def _take(conn, user_id: int, pig_id: str):
    count = _owned(conn, user_id, pig_id)
    if count < 1:
        raise PiggyError("小猪已经不在原主人的猪圈里了。")
    if count == 1:
        conn.execute("DELETE FROM collections WHERE user_id=? AND pig_id=?", (user_id, pig_id))
    else:
        conn.execute(
            "UPDATE collections SET count=count-1 WHERE user_id=? AND pig_id=?", (user_id, pig_id)
        )


def _give(conn, user_id: int, pig_id: str, now: float):
    conn.execute(
        """
        INSERT INTO collections VALUES(?,?,1,?,?) ON CONFLICT(user_id,pig_id)
        DO UPDATE SET count=count+1,last_at=excluded.last_at
        """,
        (user_id, pig_id, now, now),
    )


def _transfer(conn, source: int, target: int, pig_id: str, now: float):
    _take(conn, source, pig_id)
    _give(conn, target, pig_id, now)


def _level_change(pig: dict, before: int, after: int, cap: int) -> dict:
    skills = entry_for(pig.get("battle"))["skills"]
    old = level_for(before, cap) if before else 0
    new = level_for(after, cap) if after else 0
    old_slots, new_slots = min(old, SKILL_SLOTS), min(new, SKILL_SLOTS)
    return {
        "pig": {k: v for k, v in pig.items() if k != "battle"},
        "count_before": before,
        "count_after": after,
        "before": old,
        "after": new,
        "lost": [s["name"] for s in skills[new_slots:old_slots]],
        "gained": [s["name"] for s in skills[old_slots:new_slots]],
    }


def _week_key(now: datetime) -> str:
    """Monday in the player's UTC+8 calendar week."""
    local_day = now.astimezone(EAST_ASIA).date()
    return (local_day - timedelta(days=local_day.weekday())).isoformat()


class Database:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "piggy.sqlite3"

    async def run(self, function):
        def execute():
            with closing(sqlite3.connect(self.path, timeout=15)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA synchronous=FULL")
                with conn:
                    return function(conn)

        return await asyncio.to_thread(execute)

    async def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)

        def initialize(conn):
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise PiggyError("数据库检查失败，已停止写入；请检查备份，数据库不会被自动清空。")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5, 6, 7):
                raise PiggyError("数据库版本高于当前插件支持版本，请勿降级运行。")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, app_id TEXT NOT NULL, open_id TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '', alias TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL, UNIQUE(app_id, open_id)
                );
                CREATE TABLE IF NOT EXISTS group_players (
                    app_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id), last_seen REAL NOT NULL,
                    PRIMARY KEY(app_id, group_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS pigs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    analysis TEXT NOT NULL, asset TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), sort_order INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS draw_records (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    day TEXT NOT NULL, drawn_at REAL NOT NULL, pig_id TEXT NOT NULL REFERENCES pigs(id),
                    source_group TEXT NOT NULL, source_event TEXT NOT NULL, snapshot TEXT NOT NULL,
                    UNIQUE(user_id, day)
                );
                CREATE TABLE IF NOT EXISTS collections (
                    user_id INTEGER NOT NULL REFERENCES users(id), pig_id TEXT NOT NULL REFERENCES pigs(id),
                    count INTEGER NOT NULL CHECK(count > 0), first_at REAL NOT NULL, last_at REAL NOT NULL,
                    PRIMARY KEY(user_id, pig_id)
                );
                CREATE TABLE IF NOT EXISTS image_cache (
                    namespace TEXT NOT NULL, digest TEXT NOT NULL, object_key TEXT NOT NULL,
                    url TEXT NOT NULL, uploaded_at REAL NOT NULL,
                    PRIMARY KEY(namespace, digest)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    message_key TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS draws_user_pig ON draw_records(user_id, pig_id);
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY, app_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('duel','trade')),
                    from_user INTEGER NOT NULL REFERENCES users(id),
                    to_user INTEGER NOT NULL REFERENCES users(id),
                    give_pig TEXT NOT NULL REFERENCES pigs(id), want_pig TEXT REFERENCES pigs(id),
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
                        ('pending','accepted','declined','cancelled','expired','failed')),
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, resolved_at REAL
                );
                CREATE INDEX IF NOT EXISTS requests_to ON requests(to_user, kind, status);
                CREATE INDEX IF NOT EXISTS requests_from ON requests(from_user, kind, status);
                CREATE TABLE IF NOT EXISTS battle_records (
                    id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id),
                    day TEXT NOT NULL, fought_at REAL NOT NULL,
                    a_user INTEGER NOT NULL REFERENCES users(id), a_pig TEXT NOT NULL,
                    a_level INTEGER NOT NULL,
                    b_user INTEGER NOT NULL REFERENCES users(id), b_pig TEXT NOT NULL,
                    b_level INTEGER NOT NULL,
                    winner INTEGER NOT NULL REFERENCES users(id), seed INTEGER NOT NULL,
                    log TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS battles_a ON battle_records(a_user, day);
                CREATE INDEX IF NOT EXISTS battles_b ON battle_records(b_user, day);
                CREATE TABLE IF NOT EXISTS shop_items (
                    app_id TEXT NOT NULL, group_id TEXT NOT NULL, day TEXT NOT NULL,
                    slot INTEGER NOT NULL, pig_id TEXT NOT NULL REFERENCES pigs(id),
                    sold_to INTEGER REFERENCES users(id), paid_pig TEXT REFERENCES pigs(id),
                    sold_at REAL,
                    PRIMARY KEY(app_id, group_id, day, slot)
                );
                CREATE TABLE IF NOT EXISTS draw_sessions (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    app_id TEXT NOT NULL, group_id TEXT NOT NULL, day TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('daily','bonus')),
                    event_id TEXT NOT NULL, created_at REAL NOT NULL, items TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS draw_sessions_user ON draw_sessions(user_id, day);
                CREATE TABLE IF NOT EXISTS draw_bonus (
                    app_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id), day TEXT NOT NULL,
                    count INTEGER NOT NULL CHECK(count >= 0),
                    PRIMARY KEY(app_id, group_id, user_id, day)
                );
                CREATE TABLE IF NOT EXISTS wild_pigs (
                    id INTEGER PRIMARY KEY, app_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    day TEXT NOT NULL, pig_id TEXT NOT NULL REFERENCES pigs(id),
                    level INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','defeated','expired')),
                    attempts INTEGER NOT NULL DEFAULT 0, spawned_at REAL NOT NULL,
                    defeated_by INTEGER REFERENCES users(id), defeated_at REAL,
                    UNIQUE(app_id, group_id, day)
                );
                CREATE TABLE IF NOT EXISTS wild_battles (
                    id INTEGER PRIMARY KEY, wild_id INTEGER NOT NULL REFERENCES wild_pigs(id),
                    user_id INTEGER NOT NULL REFERENCES users(id), pig_id TEXT NOT NULL,
                    level INTEGER NOT NULL, won INTEGER NOT NULL, seed INTEGER NOT NULL,
                    log TEXT NOT NULL, summary TEXT NOT NULL, fought_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS raids (
                    id INTEGER PRIMARY KEY, app_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    day TEXT NOT NULL, dungeon INTEGER NOT NULL,
                    leader INTEGER NOT NULL REFERENCES users(id),
                    status TEXT NOT NULL DEFAULT 'forming' CHECK(status IN
                        ('forming','waiting','cleared','retreated','failed','cancelled')),
                    stage INTEGER NOT NULL DEFAULT 0, seed INTEGER NOT NULL,
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS raids_group ON raids(app_id, group_id, status);
                CREATE INDEX IF NOT EXISTS raids_status ON raids(status, expires_at);
                CREATE TABLE IF NOT EXISTS raid_members (
                    raid_id INTEGER NOT NULL REFERENCES raids(id),
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    pig_id TEXT NOT NULL REFERENCES pigs(id), seat INTEGER NOT NULL,
                    hp REAL NOT NULL DEFAULT 1, alive INTEGER NOT NULL DEFAULT 1,
                    mods TEXT NOT NULL DEFAULT '{}', joined_at REAL NOT NULL,
                    PRIMARY KEY(raid_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS raid_members_user ON raid_members(user_id);
                CREATE TABLE IF NOT EXISTS raid_entries (
                    user_id INTEGER NOT NULL REFERENCES users(id), day TEXT NOT NULL,
                    dungeon INTEGER NOT NULL, raid_id INTEGER NOT NULL REFERENCES raids(id),
                    PRIMARY KEY(user_id, day, dungeon)
                );
                CREATE TABLE IF NOT EXISTS raid_battles (
                    id INTEGER PRIMARY KEY, raid_id INTEGER NOT NULL REFERENCES raids(id),
                    stage INTEGER NOT NULL, boss_pig TEXT NOT NULL, boss_level INTEGER NOT NULL,
                    won INTEGER NOT NULL, seed INTEGER NOT NULL, events TEXT NOT NULL,
                    log TEXT NOT NULL, summary TEXT NOT NULL, fought_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pig_personalities (
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    pig_id TEXT NOT NULL REFERENCES pigs(id),
                    personality TEXT NOT NULL,
                    raid_id INTEGER REFERENCES raids(id), acquired_at REAL NOT NULL,
                    PRIMARY KEY(user_id,pig_id)
                );
                CREATE TABLE IF NOT EXISTS despair_raids (
                    id INTEGER PRIMARY KEY, app_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    week TEXT NOT NULL, leader INTEGER NOT NULL REFERENCES users(id),
                    status TEXT NOT NULL DEFAULT 'forming' CHECK(status IN
                        ('forming','cleared','failed','cancelled')),
                    seed INTEGER NOT NULL, created_at REAL NOT NULL,
                    expires_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS despair_raids_group
                    ON despair_raids(app_id,group_id,status);
                CREATE INDEX IF NOT EXISTS despair_raids_status
                    ON despair_raids(status,expires_at);
                CREATE TABLE IF NOT EXISTS despair_members (
                    raid_id INTEGER NOT NULL REFERENCES despair_raids(id),
                    user_id INTEGER NOT NULL REFERENCES users(id), seat INTEGER NOT NULL,
                    joined_at REAL NOT NULL, PRIMARY KEY(raid_id,user_id),
                    UNIQUE(raid_id,seat)
                );
                CREATE INDEX IF NOT EXISTS despair_members_user ON despair_members(user_id);
                CREATE TABLE IF NOT EXISTS despair_member_pigs (
                    raid_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    pig_id TEXT NOT NULL REFERENCES pigs(id), slot INTEGER NOT NULL,
                    PRIMARY KEY(raid_id,user_id,slot), UNIQUE(raid_id,pig_id),
                    FOREIGN KEY(raid_id,user_id) REFERENCES despair_members(raid_id,user_id)
                );
                CREATE TABLE IF NOT EXISTS despair_battles (
                    id INTEGER PRIMARY KEY, raid_id INTEGER NOT NULL UNIQUE
                        REFERENCES despair_raids(id),
                    won INTEGER NOT NULL, seed INTEGER NOT NULL, log TEXT NOT NULL,
                    summary TEXT NOT NULL, fought_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS despair_rewards (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    week TEXT NOT NULL, raid_id INTEGER NOT NULL REFERENCES despair_raids(id),
                    granted_at REAL NOT NULL, personality_pig TEXT REFERENCES pigs(id),
                    personality TEXT, UNIQUE(user_id,week)
                );
                CREATE TABLE IF NOT EXISTS despair_reward_items (
                    reward_id INTEGER NOT NULL REFERENCES despair_rewards(id),
                    slot INTEGER NOT NULL, pig_id TEXT NOT NULL REFERENCES pigs(id),
                    count_before INTEGER NOT NULL, count_after INTEGER NOT NULL,
                    PRIMARY KEY(reward_id,slot)
                );
                CREATE TABLE IF NOT EXISTS mythic_keys (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id),
                    dungeon INTEGER NOT NULL, level INTEGER NOT NULL CHECK(level >= 2),
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mythic_raids (
                    id INTEGER PRIMARY KEY, app_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    week TEXT NOT NULL, dungeon INTEGER NOT NULL, key_level INTEGER NOT NULL,
                    leader INTEGER NOT NULL REFERENCES users(id),
                    status TEXT NOT NULL DEFAULT 'forming' CHECK(status IN
                        ('forming','cleared','failed','cancelled')),
                    seed INTEGER NOT NULL, report TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mythic_raids_group
                    ON mythic_raids(app_id,group_id,status);
                CREATE TABLE IF NOT EXISTS mythic_members (
                    raid_id INTEGER NOT NULL REFERENCES mythic_raids(id),
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    pig_id TEXT NOT NULL REFERENCES pigs(id), seat INTEGER NOT NULL,
                    joined_at REAL NOT NULL, PRIMARY KEY(raid_id,user_id), UNIQUE(raid_id,seat)
                );
                CREATE INDEX IF NOT EXISTS mythic_members_user ON mythic_members(user_id);
                CREATE TABLE IF NOT EXISTS mythic_records (
                    id INTEGER PRIMARY KEY, raid_id INTEGER NOT NULL UNIQUE
                        REFERENCES mythic_raids(id),
                    app_id TEXT NOT NULL, group_id TEXT NOT NULL, week TEXT NOT NULL,
                    team_key TEXT NOT NULL, dungeon INTEGER NOT NULL, key_level INTEGER NOT NULL,
                    completed INTEGER NOT NULL, timed INTEGER NOT NULL, upgrade INTEGER NOT NULL,
                    rounds INTEGER NOT NULL, limit_rounds INTEGER NOT NULL,
                    deaths INTEGER NOT NULL, add_count INTEGER NOT NULL, score INTEGER NOT NULL,
                    report TEXT NOT NULL, completed_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mythic_records_board
                    ON mythic_records(app_id,group_id,week,key_level,timed,rounds);
                CREATE TABLE IF NOT EXISTS mythic_weekly_rewards (
                    user_id INTEGER NOT NULL REFERENCES users(id), week TEXT NOT NULL,
                    tier TEXT NOT NULL, raid_id INTEGER NOT NULL REFERENCES mythic_raids(id),
                    key_level INTEGER NOT NULL, items TEXT NOT NULL,
                    granted_at REAL NOT NULL, PRIMARY KEY(user_id,week,tier)
                );
                CREATE INDEX IF NOT EXISTS mythic_weekly_rewards_week
                    ON mythic_weekly_rewards(user_id,week);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(pigs)")}
            if "battle" not in columns:
                conn.execute("ALTER TABLE pigs ADD COLUMN battle TEXT NOT NULL DEFAULT ''")
            records = {row[1] for row in conn.execute("PRAGMA table_info(battle_records)")}
            if "summary" not in records:
                # Older records have no summary; posters for them simply omit the HP bars.
                conn.execute(
                    "ALTER TABLE battle_records ADD COLUMN summary TEXT NOT NULL DEFAULT ''"
                )
            conn.execute("PRAGMA user_version=7")
            conn.execute("COMMIT")

        try:
            await self.run(initialize)
        except sqlite3.DatabaseError as exc:
            raise PiggyError("数据库损坏或不可写，已保留原文件，请检查磁盘和备份。") from exc

    async def catalog(self, pigs: list[dict]):
        def update(conn):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE pigs SET enabled=0")
            conn.executemany(
                """
                INSERT INTO pigs(id,name,description,analysis,asset,enabled,sort_order,battle)
                VALUES(:id,:name,:description,:analysis,:asset,:enabled,:sort_order,:battle)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
                analysis=excluded.analysis, asset=excluded.asset, enabled=excluded.enabled,
                sort_order=excluded.sort_order, battle=excluded.battle
            """,
                [{"battle": "", **pig} for pig in pigs],
            )
            # Merge duplicate collections without rewriting immutable history.
            # Historical draw/battle/shop rows may still reference the disabled
            # alias, so both catalog records deliberately remain present.
            for old_id, kept_id in PIG_REPLACEMENTS.items():
                if not conn.execute("SELECT 1 FROM pigs WHERE id=?", (kept_id,)).fetchone():
                    continue
                conn.execute(
                    """
                    INSERT INTO collections(user_id,pig_id,count,first_at,last_at)
                    SELECT user_id,?,count,first_at,last_at FROM collections WHERE pig_id=?
                    ON CONFLICT(user_id,pig_id) DO UPDATE SET
                        count=collections.count+excluded.count,
                        first_at=min(collections.first_at,excluded.first_at),
                        last_at=max(collections.last_at,excluded.last_at)
                    """,
                    (kept_id, old_id),
                )
                conn.execute("DELETE FROM collections WHERE pig_id=?", (old_id,))

        await self.run(update)

    async def has_catalog(self) -> bool:
        return await self.run(
            lambda c: bool(c.execute("SELECT 1 FROM pigs WHERE enabled=1").fetchone())
        )

    async def identify(self, app_id: str, open_id: str, group_id: str, nickname: str) -> dict:
        if not app_id or not open_id:
            raise PiggyError("未取得机器人或用户的官方标识，不能安全关联收藏。")
        nickname = " ".join(nickname.split())[:64]
        now = time.time()

        def update(conn):
            conn.execute(
                """
                INSERT INTO users(app_id,open_id,nickname,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(app_id,open_id) DO UPDATE SET
                nickname=CASE WHEN excluded.nickname<>'' THEN excluded.nickname ELSE users.nickname END,
                updated_at=excluded.updated_at
            """,
                (app_id, open_id, nickname, now),
            )
            user = dict(
                conn.execute(
                    "SELECT * FROM users WHERE app_id=? AND open_id=?",
                    (app_id, open_id),
                ).fetchone()
            )
            if group_id:
                conn.execute(
                    """
                    INSERT INTO group_players VALUES(?,?,?,?) ON CONFLICT(app_id,group_id,user_id)
                    DO UPDATE SET last_seen=excluded.last_seen
                """,
                    (app_id, group_id, user["id"], now),
                )
            return user

        return await self.run(update)

    async def set_alias(self, user_id: int, alias: str):
        alias = " ".join(alias.split())
        if not 1 <= len(alias) <= 24:
            raise PiggyError("称呼请输入 1–24 个字。")
        await self.run(lambda c: c.execute("UPDATE users SET alias=? WHERE id=?", (alias, user_id)))

    async def draw(
        self,
        user_id: int,
        group_id: str,
        event_id: str,
        now: datetime | None = None,
        *,
        duplicate_rate_cap: int = 20,
        duplicate_pity: int = 2,
        gather_chance: int = 0,
        chain_chance: int = 0,
        app_id: str = "",
        all_available: bool = False,
        rng=None,
    ) -> dict:
        """Daily draw, bonus draw, or every currently available draw at once.

        Every pig drawn (the first one and each chained one) independently rolls
        "gather" (one extra copy) and then "chain" (draw another pig).
        """
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("The draw clock must be timezone-aware")
        day = now.astimezone(EAST_ASIA).date().isoformat()
        below = rng.randrange if rng else secrets.randbelow
        choose = rng.choice if rng else secrets.choice
        roll = rng.random if rng else _SYSTEM_RANDOM.random

        def draw(conn):
            conn.execute("BEGIN IMMEDIATE")

            def repeat_streak():
                records = conn.execute(
                    """
                    SELECT EXISTS(SELECT 1 FROM draw_records earlier
                        WHERE earlier.user_id=d.user_id AND earlier.pig_id=d.pig_id
                        AND earlier.id<d.id) AS repeated
                    FROM draw_records d WHERE d.user_id=? AND d.day<=?
                    ORDER BY d.day DESC LIMIT ?
                    """,
                    (user_id, day, duplicate_pity),
                ).fetchall()
                return next(
                    (i for i, row in enumerate(records) if not row["repeated"]), len(records)
                )

            def finish(session, created):
                payload = json.loads(session["items"])
                batch = isinstance(payload, dict)
                items = payload["items"] if batch else payload
                # Old same-day snapshots predate visual role/tag metadata. Rehydrate it from
                # the current catalog so repeat views render exactly like fresh draws.
                for item in items:
                    pig = item["pig"]
                    current = conn.execute(
                        "SELECT battle FROM pigs WHERE id=?", (pig["id"],)
                    ).fetchone()
                    battle = entry_for(current["battle"] if current else None)
                    pig["role"] = battle.get("role", "输出")
                    pig["style"] = battle.get("style", "均衡")
                    pig["tags"] = battle.get("tags", [pig["role"]])
                    pig["rarity"] = rarity_for(pig["id"])
                first = payload["first"] if batch else items[0]["pig"]
                sessions_today = conn.execute(
                    "SELECT count(*) FROM draw_sessions WHERE user_id=? AND day=? AND id<=?",
                    (user_id, day, session["id"]),
                ).fetchone()[0]
                result = {
                    "pig": first,
                    "items": items,
                    "day": day,
                    "created": created,
                    "kind": session["kind"],
                    "index": max(1, sessions_today),
                    "count": _owned(conn, user_id, first["id"]),
                    "new_species": created
                    and (payload["new_species"] if batch else items[0]["new"]),
                    "repeat_streak": repeat_streak() if duplicate_pity else 0,
                    "bonus_left": self._bonus_left(conn, app_id, group_id, user_id, day),
                }
                if batch:
                    result.update(
                        all_available=True,
                        opportunities=payload["opportunities"],
                        bonus_spent=payload["bonus_spent"],
                        total=payload["total"],
                        new_count=payload["new_count"],
                        gathers=payload["gathers"],
                        chains=payload["chains"],
                    )
                return result

            replay = conn.execute(
                "SELECT * FROM draw_sessions WHERE user_id=? AND event_id=?", (user_id, event_id)
            ).fetchone()
            if replay:
                return finish(replay, False)
            existing = conn.execute(
                "SELECT * FROM draw_records WHERE user_id=? AND day=?", (user_id, day)
            ).fetchone()
            bonus_before = self._bonus_left(conn, app_id, group_id, user_id, day)
            daily = existing is None
            opportunities = (1 if daily else 0) + bonus_before if all_available else 1
            if all_available and opportunities < 1:
                raise PiggyError("今天没有可以抽取的次数啦。")
            if existing and not all_available:
                if bonus_before < 1:
                    latest = conn.execute(
                        "SELECT * FROM draw_sessions WHERE user_id=? AND day=? "
                        "ORDER BY id DESC LIMIT 1",
                        (user_id, day),
                    ).fetchone()
                    if latest:
                        return finish(latest, False)
                    # Drawn before sessions existed: show the single recorded pig.
                    pig = json.loads(existing["snapshot"])
                    legacy = {
                        "id": 0,
                        "kind": "daily",
                        "items": json.dumps(
                            [{"pig": pig, "kind": "base", "parent": None, "new": False}]
                        ),
                    }
                    return finish(legacy, False)
            bonus_spent = bonus_before if all_available else int(existing is not None)
            if bonus_spent:
                conn.execute(
                    "UPDATE draw_bonus SET count=count-? "
                    "WHERE app_id=? AND group_id=? AND user_id=? AND day=?",
                    (bonus_spent, app_id, group_id, user_id, day),
                )
            available = [
                dict(row) for row in conn.execute("SELECT * FROM pigs WHERE enabled=1 ORDER BY id")
            ]
            if not available:
                raise PiggyError("猪库没有启用的小猪，请联系管理员检查。")
            for pig in available:
                battle = entry_for(pig.pop("battle", None))
                pig["role"] = battle.get("role", "输出")
                pig["style"] = battle.get("style", "均衡")
                pig["tags"] = battle.get("tags", [pig["role"]])
                pig["rarity"] = rarity_for(pig["id"])
            owned_ids = {
                row[0]
                for row in conn.execute(
                    "SELECT pig_id FROM collections WHERE user_id=?", (user_id,)
                )
            }
            timestamp = now.timestamp()

            def pick(force_new: bool) -> dict:
                owned = [p for p in available if p["id"] in owned_ids]
                unseen = [p for p in available if p["id"] not in owned_ids]
                if not unseen:
                    pool = owned
                elif not owned or force_new:
                    pool = unseen
                else:
                    # Integer comparison preserves the natural rate without rounding.
                    threshold = min(len(owned) * 100, duplicate_rate_cap * len(available))
                    pool = owned if below(100 * len(available)) < threshold else unseen
                return dict(choose(pool))

            items = []
            batch_rows = {}
            awards = {}
            total = new_count = gathers = chains = 0

            def collect(pig: dict, kind: str, parent) -> dict:
                nonlocal total, new_count, gathers, chains
                new = pig["id"] not in owned_ids
                if all_available:
                    awards[pig["id"]] = awards.get(pig["id"], 0) + 1
                    row = batch_rows.setdefault(
                        pig["id"], {"pig": pig, "count": 0, "new": False}
                    )
                    row["count"] += 1
                    row["new"] = row["new"] or new
                    total += 1
                    new_count += int(new)
                    gathers += int(kind == "gather")
                    chains += int(kind == "chain")
                else:
                    _give(conn, user_id, pig["id"], timestamp)
                owned_ids.add(pig["id"])
                return {"pig": pig, "kind": kind, "parent": parent, "new": new}

            pity = daily and bool(duplicate_pity) and repeat_streak() >= duplicate_pity
            first = pick(pity)
            if daily:
                conn.execute(
                    """
                    INSERT INTO draw_records(user_id,day,drawn_at,pig_id,source_group,source_event,snapshot)
                    VALUES(?,?,?,?,?,?,?)
                """,
                    (
                        user_id,
                        day,
                        timestamp,
                        first["id"],
                        group_id,
                        event_id,
                        json.dumps(first, ensure_ascii=False),
                    ),
                )
            for draw_index in range(opportunities):
                current = first if draw_index == 0 else pick(False)
                kind, parent, drawn = ("base" if draw_index == 0 else "batch"), None, 0
                while True:
                    index = len(items)
                    item = collect(current, kind, parent)
                    if not all_available:
                        items.append(item)
                    drawn += 1
                    # Every gathered copy can gather again. `parent` records the
                    # exact copy that independently triggered the next one.
                    gather_parent = index
                    while drawn < MAX_DRAW_ITEMS and roll() * 100 < gather_chance:
                        gather_parent = len(items)
                        item = collect(current, "gather", gather_parent - 1)
                        if not all_available:
                            items.append(item)
                        drawn += 1
                    if drawn >= MAX_DRAW_ITEMS or roll() * 100 >= chain_chance:
                        break
                    current, kind, parent = pick(False), "chain", index
            if all_available:
                conn.executemany(
                    """
                    INSERT INTO collections VALUES(?,?,?,?,?) ON CONFLICT(user_id,pig_id)
                    DO UPDATE SET count=count+excluded.count,last_at=excluded.last_at
                    """,
                    [
                        (user_id, pig_id, count, timestamp, timestamp)
                        for pig_id, count in awards.items()
                    ],
                )
                payload = {
                    "mode": "all",
                    "first": first,
                    "items": list(batch_rows.values()),
                    "opportunities": opportunities,
                    "bonus_spent": bonus_spent,
                    "total": total,
                    "new_count": new_count,
                    "new_species": batch_rows[first["id"]]["new"],
                    "gathers": gathers,
                    "chains": chains,
                }
            else:
                payload = items
            cursor = conn.execute(
                "INSERT INTO draw_sessions(user_id,app_id,group_id,day,kind,event_id,created_at,"
                "items) VALUES(?,?,?,?,?,?,?,?)",
                (
                    user_id,
                    app_id,
                    group_id,
                    day,
                    "daily" if daily else "bonus",
                    event_id,
                    timestamp,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            session = conn.execute(
                "SELECT * FROM draw_sessions WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
            return finish(session, True)

        return await self.run(draw)

    @staticmethod
    def _bonus_left(conn, app_id: str, group_id: str, user_id: int, day: str) -> int:
        row = conn.execute(
            "SELECT count FROM draw_bonus WHERE app_id=? AND group_id=? AND user_id=? AND day=?",
            (app_id, group_id, user_id, day),
        ).fetchone()
        return row[0] if row else 0

    async def bonus_count(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> int:
        day = (now or datetime.now(timezone.utc)).astimezone(EAST_ASIA).date().isoformat()
        return await self.run(lambda c: self._bonus_left(c, app_id, group_id, user_id, day))

    @staticmethod
    def _wild_today(conn, app_id: str, group_id: str, day: str, level_max: int, stamp: float):
        """One wild pig per group per day, spawned on first sight."""
        row = conn.execute(
            "SELECT * FROM wild_pigs WHERE app_id=? AND group_id=? AND day=?",
            (app_id, group_id, day),
        ).fetchone()
        if row:
            return row
        conn.execute(
            "UPDATE wild_pigs SET status='expired' "
            "WHERE app_id=? AND group_id=? AND day<? AND status='active'",
            (app_id, group_id, day),
        )
        pool = [r[0] for r in conn.execute("SELECT id FROM pigs WHERE enabled=1")]
        if not pool:
            raise PiggyError("猪库没有启用的小猪，野猪暂时无法出现。")
        conn.execute(
            "INSERT OR IGNORE INTO wild_pigs(app_id,group_id,day,pig_id,level,spawned_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                app_id,
                group_id,
                day,
                _SYSTEM_RANDOM.choice(pool),
                _SYSTEM_RANDOM.randint(1, level_max),
                stamp,
            ),
        )
        return conn.execute(
            "SELECT * FROM wild_pigs WHERE app_id=? AND group_id=? AND day=?",
            (app_id, group_id, day),
        ).fetchone()

    @staticmethod
    def _despair_expire(conn, app_id: str, group_id: str, stamp: float) -> list[dict]:
        rows = conn.execute(
            "SELECT * FROM despair_raids WHERE status='forming' AND expires_at<=?", (stamp,)
        ).fetchall()
        expired = []
        for row in rows:
            conn.execute(
                "UPDATE despair_raids SET status='cancelled',updated_at=? WHERE id=?",
                (stamp, row["id"]),
            )
            if row["app_id"] == app_id and row["group_id"] == group_id:
                expired.append({**dict(row), "status": "cancelled"})
        return expired

    async def _despair_sweep(self, app_id: str, group_id: str, stamp: float) -> list[dict]:
        def sweep(conn):
            conn.execute("BEGIN IMMEDIATE")
            return self._despair_expire(conn, app_id, group_id, stamp)

        return await self.run(sweep)

    @staticmethod
    def _group_despair(conn, app_id: str, group_id: str):
        return conn.execute(
            "SELECT * FROM despair_raids WHERE app_id=? AND group_id=? AND status='forming' "
            "ORDER BY id DESC LIMIT 1",
            (app_id, group_id),
        ).fetchone()

    @staticmethod
    def _despair_unlock(conn, user_id: int) -> set[int]:
        keys = {item["key"] for item in DUNGEONS}
        return {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT r.dungeon FROM raids r "
                "JOIN raid_members m ON m.raid_id=r.id "
                "WHERE m.user_id=? AND r.status='cleared'",
                (user_id,),
            )
            if row[0] in keys
        }

    @classmethod
    def _despair_require_unlocked(cls, conn, user_id: int):
        cleared = cls._despair_unlock(conn, user_id)
        missing = [item["name"] for item in DUNGEONS if item["key"] not in cleared]
        if missing:
            raise PiggyError(f"绝境战尚未解锁，还需通关：{'、'.join(missing)}。")

    @staticmethod
    def _despair_pigs(conn, user_id: int, pig_ids) -> list[dict]:
        pig_ids = tuple(pig_ids)
        if len(pig_ids) != DESPAIR_PIGS_PER_PLAYER or len(set(pig_ids)) != len(pig_ids):
            raise PiggyError("绝境战每人必须选择 2 只不同的小猪。")
        pigs = []
        for pig_id in pig_ids:
            row = conn.execute("SELECT * FROM pigs WHERE id=? AND enabled=1", (pig_id,)).fetchone()
            if not row:
                raise PiggyError("所选小猪不存在或当前未启用。")
            if _owned(conn, user_id, pig_id) < 1:
                raise PiggyError(f"你的猪圈里没有「{row['name']}」。")
            pig = dict(row)
            role = entry_for(pig.get("battle")).get("role", "输出")
            pig["role"] = role if role in ROLES else "输出"
            pigs.append(pig)
        return pigs

    @classmethod
    def _despair_member_check(cls, conn, user_id: int, pig_ids) -> list[dict]:
        cls._despair_require_unlocked(conn, user_id)
        regular = conn.execute(
            "SELECT r.dungeon FROM raid_members m JOIN raids r ON r.id=m.raid_id "
            "WHERE m.user_id=? AND r.status IN ('forming','waiting')",
            (user_id,),
        ).fetchone()
        if regular:
            raise PiggyError(
                f"你已经在「{dungeon(regular['dungeon'])['name']}」队伍里，不能同时参加绝境战。"
            )
        active = conn.execute(
            "SELECT 1 FROM despair_members m JOIN despair_raids r ON r.id=m.raid_id "
            "WHERE m.user_id=? AND r.status='forming'",
            (user_id,),
        ).fetchone()
        if active:
            raise PiggyError("你已经在一支绝境战队伍里了。")
        mythic = conn.execute(
            "SELECT 1 FROM mythic_members m JOIN mythic_raids r ON r.id=m.raid_id "
            "WHERE m.user_id=? AND r.status='forming'",
            (user_id,),
        ).fetchone()
        if mythic:
            raise PiggyError("你已经在一支大秘境队伍里，不能同时参加绝境战。")
        return cls._despair_pigs(conn, user_id, pig_ids)

    @staticmethod
    def _despair_role_counts(conn, raid_id: int) -> dict[str, int]:
        counts = {role: 0 for role in ROLES}
        for row in conn.execute(
            "SELECT p.battle FROM despair_member_pigs mp JOIN pigs p ON p.id=mp.pig_id "
            "WHERE mp.raid_id=?",
            (raid_id,),
        ):
            role = entry_for(row["battle"]).get("role", "输出")
            counts[role if role in counts else "输出"] += 1
        return counts

    @classmethod
    def _despair_view(cls, conn, row) -> dict:
        raid = dict(row)
        raid["leader_user"] = dict(
            conn.execute("SELECT * FROM users WHERE id=?", (row["leader"],)).fetchone()
        )
        members = []
        for member in conn.execute(
            "SELECT * FROM despair_members WHERE raid_id=? ORDER BY seat", (row["id"],)
        ):
            pigs = []
            for selected in conn.execute(
                "SELECT mp.slot,p.* FROM despair_member_pigs mp JOIN pigs p ON p.id=mp.pig_id "
                "WHERE mp.raid_id=? AND mp.user_id=? ORDER BY mp.slot",
                (row["id"], member["user_id"]),
            ):
                pig = dict(selected)
                pig["role"] = entry_for(pig.pop("battle", None)).get("role", "输出")
                pigs.append(pig)
            members.append(
                {
                    **dict(member),
                    "user": dict(
                        conn.execute(
                            "SELECT * FROM users WHERE id=?", (member["user_id"],)
                        ).fetchone()
                    ),
                    "pigs": pigs,
                }
            )
        raid["members"] = members
        raid["roles"] = cls._despair_role_counts(conn, row["id"])
        return raid

    @staticmethod
    def _despair_reward_view(conn, row) -> dict:
        reward = dict(row)
        reward["user"] = dict(
            conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        )
        items = []
        for item in conn.execute(
            "SELECT i.*,p.* FROM despair_reward_items i JOIN pigs p ON p.id=i.pig_id "
            "WHERE i.reward_id=? ORDER BY i.slot",
            (row["id"],),
        ):
            data = dict(item)
            pig = {
                key: data[key]
                for key in ("pig_id", "name", "description", "analysis", "asset")
                if key in data
            }
            pig["id"] = pig.pop("pig_id")
            pig["rarity"] = rarity_for(pig["id"])
            items.append(
                {
                    "slot": data["slot"],
                    "pig": pig,
                    "rarity": pig["rarity"],
                    "count_before": data["count_before"],
                    "count_after": data["count_after"],
                }
            )
        reward["items"] = items
        reward["personality_detail"] = None
        if row["personality"]:
            nature = PERSONALITIES[row["personality"]]
            pig = conn.execute(
                "SELECT name FROM pigs WHERE id=?", (row["personality_pig"],)
            ).fetchone()
            reward["personality_detail"] = {
                "pig_id": row["personality_pig"],
                "pig": pig["name"],
                "id": row["personality"],
                "name": nature["name"],
                "skill": nature["skill"]["text"],
            }
        return reward

    async def despair_status(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp, week = now.timestamp(), _week_key(now)

        def read(conn):
            conn.execute("BEGIN IMMEDIATE")
            expired = self._despair_expire(conn, app_id, group_id, stamp)
            row = self._group_despair(conn, app_id, group_id)
            cleared = self._despair_unlock(conn, user_id)
            reward = conn.execute(
                "SELECT * FROM despair_rewards WHERE user_id=? AND week=?", (user_id, week)
            ).fetchone()
            return {
                "raid": self._despair_view(conn, row) if row else None,
                "expired": expired,
                "week": week,
                "unlocked": len(cleared) == len(DUNGEONS),
                "cleared": cleared,
                "missing": [item for item in DUNGEONS if item["key"] not in cleared],
                "reward": self._despair_reward_view(conn, reward) if reward else None,
                "now": stamp,
            }

        return await self.run(read)

    @staticmethod
    def _insert_despair_member(conn, raid_id: int, user_id: int, seat: int, pigs, stamp: float):
        conn.execute(
            "INSERT INTO despair_members(raid_id,user_id,seat,joined_at) VALUES(?,?,?,?)",
            (raid_id, user_id, seat, stamp),
        )
        conn.executemany(
            "INSERT INTO despair_member_pigs(raid_id,user_id,pig_id,slot) VALUES(?,?,?,?)",
            [(raid_id, user_id, pig["id"], slot) for slot, pig in enumerate(pigs)],
        )

    async def open_despair(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        pig_ids,
        now: datetime | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp, week = now.timestamp(), _week_key(now)
        expired = await self._despair_sweep(app_id, group_id, stamp)

        def create(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._despair_expire(conn, app_id, group_id, stamp)
            self._raid_expire(conn, app_id, group_id, stamp)
            if self._group_despair(conn, app_id, group_id):
                raise PiggyError("本群已经有一支绝境战队伍正在组队。")
            regular = self._group_raid(conn, app_id, group_id)
            if regular:
                raise PiggyError("本群已有普通副本队伍，结束后才能开启绝境战。")
            if self._group_mythic(conn, app_id, group_id):
                raise PiggyError("本群已有大秘境队伍，结束后才能开启绝境战。")
            pigs = self._despair_member_check(conn, user_id, pig_ids)
            cursor = conn.execute(
                "INSERT INTO despair_raids(app_id,group_id,week,leader,seed,created_at,"
                "expires_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    app_id,
                    group_id,
                    week,
                    user_id,
                    secrets.randbits(32),
                    stamp,
                    stamp + DESPAIR_TIMEOUT_MINUTES * 60,
                    stamp,
                ),
            )
            self._insert_despair_member(conn, cursor.lastrowid, user_id, 0, pigs, stamp)
            row = conn.execute(
                "SELECT * FROM despair_raids WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
            return {"raid": self._despair_view(conn, row), "expired": expired, "now": stamp}

        return await self.run(create)

    async def join_despair(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        pig_ids,
        *,
        level_cap: int = 20,
        now: datetime | None = None,
        seed: int | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.timestamp()
        expired = await self._despair_sweep(app_id, group_id, stamp)

        def join(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._despair_expire(conn, app_id, group_id, stamp)
            self._raid_expire(conn, app_id, group_id, stamp)
            raid = self._group_despair(conn, app_id, group_id)
            if not raid:
                note = "上一支队伍 10 分钟内没凑满 4 人，已自动取消。" if expired else ""
                raise PiggyError(f"{note}本群目前没有绝境战队伍。")
            pigs = self._despair_member_check(conn, user_id, pig_ids)
            count = conn.execute(
                "SELECT count(*) FROM despair_members WHERE raid_id=?", (raid["id"],)
            ).fetchone()[0]
            if count >= DESPAIR_PARTY_SIZE:
                raise PiggyError("绝境战队伍已经满员。")
            selected_ids = {
                row[0]
                for row in conn.execute(
                    "SELECT pig_id FROM despair_member_pigs WHERE raid_id=?", (raid["id"],)
                )
            }
            duplicate = next((pig for pig in pigs if pig["id"] in selected_ids), None)
            if duplicate:
                raise PiggyError(f"全队不能重复选择「{duplicate['name']}」。")
            self._insert_despair_member(conn, raid["id"], user_id, count, pigs, stamp)
            roles = self._despair_role_counts(conn, raid["id"])
            if any(value > 2 for value in roles.values()):
                raise PiggyError("绝境战阵容每种职业必须恰好 2 只，当前选择会使某职业超过 2 只。")
            if count + 1 < DESPAIR_PARTY_SIZE:
                row = conn.execute(
                    "SELECT * FROM despair_raids WHERE id=?", (raid["id"],)
                ).fetchone()
                return {
                    "raid": self._despair_view(conn, row),
                    "started": False,
                    "expired": expired,
                    "now": stamp,
                }
            if any(value != 2 for value in roles.values()):
                raise PiggyError("绝境战需要恰好 2 坦克、2 输出、2 治疗、2 辅助。")
            battle = self._despair_fight(conn, raid["id"], stamp, level_cap, seed)
            return {
                "raid": battle["raid"],
                "started": True,
                "battle": battle,
                "rewards": battle["rewards"],
                "expired": expired,
            }

        return await self.run(join)

    async def leave_despair(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.timestamp()
        await self._despair_sweep(app_id, group_id, stamp)

        def leave(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._despair_expire(conn, app_id, group_id, stamp)
            raid = self._group_despair(conn, app_id, group_id)
            if (
                not raid
                or not conn.execute(
                    "SELECT 1 FROM despair_members WHERE raid_id=? AND user_id=?",
                    (raid["id"], user_id),
                ).fetchone()
            ):
                raise PiggyError("你不在本群正在组队的绝境战队伍里。")
            if raid["leader"] == user_id:
                conn.execute(
                    "UPDATE despair_raids SET status='cancelled',updated_at=? WHERE id=?",
                    (stamp, raid["id"]),
                )
                cancelled = True
            else:
                conn.execute(
                    "DELETE FROM despair_member_pigs WHERE raid_id=? AND user_id=?",
                    (raid["id"], user_id),
                )
                conn.execute(
                    "DELETE FROM despair_members WHERE raid_id=? AND user_id=?",
                    (raid["id"], user_id),
                )
                remaining = conn.execute(
                    "SELECT user_id FROM despair_members WHERE raid_id=? ORDER BY joined_at,seat",
                    (raid["id"],),
                ).fetchall()
                for seat, member in enumerate(remaining):
                    conn.execute(
                        "UPDATE despair_members SET seat=? WHERE raid_id=? AND user_id=?",
                        (seat, raid["id"], member["user_id"]),
                    )
                cancelled = False
            row = conn.execute("SELECT * FROM despair_raids WHERE id=?", (raid["id"],)).fetchone()
            return {"raid": self._despair_view(conn, row), "cancelled": cancelled, "now": stamp}

        return await self.run(leave)

    @classmethod
    def _grant_despair_reward(
        cls, conn, user_id: int, week: str, raid_id: int, stamp: float, rng
    ) -> dict:
        existing = conn.execute(
            "SELECT * FROM despair_rewards WHERE user_id=? AND week=?", (user_id, week)
        ).fetchone()
        if existing:
            view = cls._despair_reward_view(conn, existing)
            view["created"] = False
            return view
        available = [dict(row) for row in conn.execute("SELECT * FROM pigs WHERE enabled=1")]
        if not available:
            raise PiggyError("猪库没有启用的小猪，无法发放绝境战十连奖励。")
        picks = [dict(rng.choice(available)) for _ in range(10)]
        awakened = {
            row[0]
            for row in conn.execute(
                "SELECT pig_id FROM pig_personalities WHERE user_id=?", (user_id,)
            )
        }
        unawakened = [pig for pig in available if pig["id"] not in awakened]
        personality_pig = personality_id = None
        if unawakened:
            eligible_slots = [i for i, pig in enumerate(picks) if pig["id"] not in awakened]
            if not eligible_slots:
                picks[-1] = dict(rng.choice(unawakened))
                personality_slot = 9
            else:
                personality_slot = rng.choice(eligible_slots)
            chosen = picks[personality_slot]
            role = entry_for(chosen.get("battle")).get("role", "输出")
            personality_pig = chosen["id"]
            personality_id = rng.choice(choices_for(role if role in ROLES else "输出"))
        else:
            mythic = [pig for pig in available if rarity_for(pig["id"]) == "神话"]
            if mythic:
                picks[-1] = dict(rng.choice(mythic))
        cursor = conn.execute(
            "INSERT INTO despair_rewards(user_id,week,raid_id,granted_at,personality_pig,"
            "personality) VALUES(?,?,?,?,?,?)",
            (user_id, week, raid_id, stamp, personality_pig, personality_id),
        )
        reward_id = cursor.lastrowid
        for slot, pig in enumerate(picks):
            before = _owned(conn, user_id, pig["id"])
            _give(conn, user_id, pig["id"], stamp)
            conn.execute(
                "INSERT INTO despair_reward_items VALUES(?,?,?,?,?)",
                (reward_id, slot, pig["id"], before, before + 1),
            )
        if personality_id:
            conn.execute(
                "INSERT INTO pig_personalities(user_id,pig_id,personality,raid_id,acquired_at) "
                "VALUES(?,?,?,?,?)",
                (user_id, personality_pig, personality_id, None, stamp),
            )
        row = conn.execute("SELECT * FROM despair_rewards WHERE id=?", (reward_id,)).fetchone()
        view = cls._despair_reward_view(conn, row)
        view["created"] = True
        return view

    @classmethod
    def _despair_fight(
        cls, conn, raid_id: int, stamp: float, level_cap: int, seed: int | None
    ) -> dict:
        from .despair import simulate_despair

        raid = conn.execute("SELECT * FROM despair_raids WHERE id=?", (raid_id,)).fetchone()
        fight_seed = raid["seed"] if seed is None else seed
        party, fighter_meta = [], []
        selected = conn.execute(
            "SELECT m.seat member_seat,m.user_id,mp.slot,p.* "
            "FROM despair_members m JOIN despair_member_pigs mp "
            "ON mp.raid_id=m.raid_id AND mp.user_id=m.user_id "
            "JOIN pigs p ON p.id=mp.pig_id WHERE m.raid_id=? "
            "ORDER BY m.seat,mp.slot",
            (raid_id,),
        ).fetchall()
        for seat, row in enumerate(selected):
            pig = dict(row)
            user = dict(
                conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            )
            count = _owned(conn, row["user_id"], row["id"])
            personality = conn.execute(
                "SELECT personality FROM pig_personalities WHERE user_id=? AND pig_id=?",
                (row["user_id"], row["id"]),
            ).fetchone()
            level = level_for(count, level_cap)
            hero = fighter(
                pig,
                entry_for(row["battle"]),
                level,
                f"{player_name(user)}的{row['name']}",
                personality=personality[0] if personality else None,
            )
            hero["seat"] = seat
            hero["owner_seat"] = row["member_seat"]
            hero["owner_id"] = row["user_id"]
            party.append(hero)
            fighter_meta.append((user, pig, level, hero))
        result = simulate_despair(party, seed=fight_seed)
        won = bool(result.get("won"))
        status = "cleared" if won else "failed"
        settlement_week = _week_key(datetime.fromtimestamp(stamp, timezone.utc))
        conn.execute(
            "UPDATE despair_raids SET status=?,week=?,updated_at=? WHERE id=?",
            (status, settlement_week, stamp, raid_id),
        )
        outcome = {item["seat"]: item for item in result.get("heroes", [])}
        fighters = []
        for seat, (user, pig, level, hero) in enumerate(fighter_meta):
            state = outcome.get(seat, {})
            fighters.append(
                {
                    "seat": seat,
                    "owner_seat": hero["owner_seat"],
                    "user": user,
                    "pig": {key: value for key, value in pig.items() if key != "battle"},
                    "level": level,
                    "role": hero.get("role", "输出"),
                    "style": hero.get("style", "均衡"),
                    "rarity": hero.get("rarity", rarity_for(pig["id"])),
                    "personality": hero.get("personality_name", ""),
                    "hp": state.get("hp", hero["stats"]["hp"]),
                    "max_hp": state.get("max_hp", hero["stats"]["hp"]),
                    "alive": state.get("alive", True),
                    "dealt": state.get("dealt", 0),
                    "healed": state.get("healed", 0),
                    "taken": state.get("taken", 0),
                    "poise": state.get("poise", 0),
                }
            )
        summary = {
            "rounds": result.get("rounds", 0),
            "boss": result.get("boss"),
            "heroes": result.get("heroes", []),
            "timeout": bool(result.get("timeout")),
            "phases": result.get("phases", result.get("phase", [])),
            "phase_reports": result.get("phase_reports", []),
            "ultimate": result.get("ultimate", []),
        }
        cursor = conn.execute(
            "INSERT INTO despair_battles(raid_id,won,seed,log,summary,fought_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                raid_id,
                int(won),
                fight_seed,
                json.dumps(result.get("log", []), ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
                stamp,
            ),
        )
        rewards = []
        if won:
            rng = random.Random(fight_seed ^ 0xD35FA17)
            for member in conn.execute(
                "SELECT user_id FROM despair_members WHERE raid_id=? ORDER BY seat", (raid_id,)
            ):
                reward = cls._grant_despair_reward(
                    conn, member["user_id"], settlement_week, raid_id, stamp, rng
                )
                if not reward["created"]:
                    reward = {**reward, "items": [], "personality_detail": None}
                rewards.append(reward)
        view = cls._despair_view(
            conn, conn.execute("SELECT * FROM despair_raids WHERE id=?", (raid_id,)).fetchone()
        )
        boss_pig_row = conn.execute("SELECT * FROM pigs WHERE id=?", ("pighub0253",)).fetchone()
        return {
            "id": cursor.lastrowid,
            "raid": view,
            "won": won,
            "status": status,
            "week": settlement_week,
            "result": result,
            "fighters": fighters,
            "boss_pig": dict(boss_pig_row) if boss_pig_row else None,
            "rewards": rewards,
        }

    async def test_despair(self, user: dict, *, level: int = 10, seed: int | None = None) -> dict:
        """Build a read-only random balanced party and run a complete report preview."""
        from .despair import simulate_despair

        fight_seed = secrets.randbits(32) if seed is None else seed

        def simulate(conn):
            rng = random.Random(fight_seed)
            pools = {role: [] for role in ROLES}
            for row in conn.execute("SELECT * FROM pigs WHERE enabled=1 ORDER BY id"):
                pig = dict(row)
                entry = entry_for(pig.get("battle"))
                role = entry.get("role", "输出")
                if role in pools:
                    pools[role].append((pig, entry))
            missing = [role for role, pigs in pools.items() if len(pigs) < 2]
            if missing:
                raise PiggyError(
                    f"猪库缺少可测试职业：{'、'.join(missing)}（每个职业至少需要 2 只）"
                )
            selected = []
            for role in ROLES:
                selected.extend(rng.sample(pools[role], 2))
            rng.shuffle(selected)

            party, metadata = [], []
            test_users = [
                {
                    **user,
                    "id": -(index + 1),
                    "alias": f"测试玩家{index + 1}",
                    "nickname": f"测试玩家{index + 1}",
                }
                for index in range(4)
            ]
            for seat, (pig, entry) in enumerate(selected):
                owner_seat = seat // 2
                owner = test_users[owner_seat]
                hero = fighter(
                    pig,
                    entry,
                    level,
                    f"{player_name(owner)}的{pig['name']}",
                    rarity="神话",
                )
                hero.update(seat=seat, owner_seat=owner_seat, owner_id=owner["id"])
                party.append(hero)
                metadata.append((owner, pig, hero))

            result = simulate_despair(party, seed=fight_seed ^ 0xD35FA17)
            outcomes = {item["seat"]: item for item in result.get("heroes", [])}
            fighters = []
            for seat, (owner, pig, hero) in enumerate(metadata):
                state = outcomes.get(seat, {})
                fighters.append(
                    {
                        "seat": seat,
                        "owner_seat": hero["owner_seat"],
                        "user": owner,
                        "pig": {key: value for key, value in pig.items() if key != "battle"},
                        "level": level,
                        "role": hero["role"],
                        "style": hero["style"],
                        "rarity": hero["rarity"],
                        "hp": state.get("hp", hero["stats"]["hp"]),
                        "max_hp": state.get("max_hp", hero["stats"]["hp"]),
                        "alive": state.get("alive", True),
                        "dealt": state.get("dealt", 0),
                        "healed": state.get("healed", 0),
                        "taken": state.get("taken", 0),
                        "poise": state.get("poise", 0),
                    }
                )
            boss_pig = conn.execute("SELECT * FROM pigs WHERE id=?", ("pighub0253",)).fetchone()
            return {
                "id": f"TEST-{fight_seed:08X}",
                "raid": {"leader_user": user},
                "won": bool(result.get("won")),
                "status": "test",
                "week": f"10级全神话随机阵容 · 种子 {fight_seed}",
                "result": result,
                "fighters": fighters,
                "boss_pig": dict(boss_pig) if boss_pig else None,
                "rewards": [],
                "test": True,
            }

        return await self.run(simulate)

    @staticmethod
    def _wild_view(conn, row) -> dict:
        wild = dict(row)
        wild["pig"] = dict(
            conn.execute("SELECT * FROM pigs WHERE id=?", (row["pig_id"],)).fetchone()
        )
        wild["victor"] = (
            dict(conn.execute("SELECT * FROM users WHERE id=?", (row["defeated_by"],)).fetchone())
            if row["defeated_by"]
            else None
        )
        return wild

    async def wild_pig(
        self, app_id: str, group_id: str, now: datetime | None = None, level_max: int = 20
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(EAST_ASIA).date().isoformat()

        def read(conn):
            conn.execute("BEGIN IMMEDIATE")
            existed = conn.execute(
                "SELECT 1 FROM wild_pigs WHERE app_id=? AND group_id=? AND day=?",
                (app_id, group_id, day),
            ).fetchone()
            wild = self._wild_view(
                conn, self._wild_today(conn, app_id, group_id, day, level_max, now.timestamp())
            )
            wild["spawned"] = existed is None
            return wild

        return await self.run(read)

    async def active_groups(self, app_id: str, since: float) -> list[str]:
        """Groups where anyone used the plugin after `since`."""
        rows = await self.run(
            lambda c: c.execute(
                "SELECT DISTINCT group_id FROM group_players WHERE app_id=? AND last_seen>=? "
                "ORDER BY group_id",
                (app_id, since),
            ).fetchall()
        )
        return [row[0] for row in rows]

    async def challenge_wild(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        pig_id: str,
        *,
        level_cap: int = 20,
        level_max: int = 20,
        now: datetime | None = None,
        seed: int | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(EAST_ASIA).date().isoformat()
        stamp = now.timestamp()

        def challenge(conn):
            conn.execute("BEGIN IMMEDIATE")
            wild = self._wild_view(
                conn, self._wild_today(conn, app_id, group_id, day, level_max, stamp)
            )
            if wild["status"] == "defeated":
                victor = wild["victor"]
                raise PiggyError(
                    f"今天的野生「{wild['pig']['name']}」已经被 {player_name(victor)} 收服了，"
                    "明天 0 点会出现新的野猪。"
                )
            count = _owned(conn, user_id, pig_id)
            if count < 1:
                raise PiggyError("你的猪圈里没有这只小猪，换一只出战吧。")
            user = dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
            mine = dict(conn.execute("SELECT * FROM pigs WHERE id=?", (pig_id,)).fetchone())
            level = level_for(count, level_cap)
            fighters = [
                fighter(
                    mine, entry_for(mine["battle"]), level, f"{player_name(user)}的{mine['name']}"
                ),
                fighter(
                    wild["pig"],
                    entry_for(wild["pig"]["battle"]),
                    wild["level"],
                    f"野生的{wild['pig']['name']}",
                ),
            ]
            fight_seed = secrets.randbits(32) if seed is None else seed
            result = simulate(fighters[0], fighters[1], fight_seed)
            won = result["winner"] == 0
            conn.execute("UPDATE wild_pigs SET attempts=attempts+1 WHERE id=?", (wild["id"],))
            rewarded = 0
            if won:
                cursor = conn.execute(
                    "UPDATE wild_pigs SET status='defeated',defeated_by=?,defeated_at=? "
                    "WHERE id=? AND status='active'",
                    (user_id, stamp, wild["id"]),
                )
                if cursor.rowcount != 1:
                    raise PiggyError("野猪刚刚被别人收服了。")
                prize = wild["pig"]
                before = _owned(conn, user_id, prize["id"])
                _give(conn, user_id, prize["id"], stamp)
                change = _level_change(prize, before, before + 1, level_cap)
                players = [
                    row[0]
                    for row in conn.execute(
                        "SELECT user_id FROM group_players WHERE app_id=? AND group_id=?",
                        (app_id, group_id),
                    )
                ]
                conn.executemany(
                    """
                    INSERT INTO draw_bonus VALUES(?,?,?,?,1) ON CONFLICT(app_id,group_id,user_id,day)
                    DO UPDATE SET count=count+1
                    """,
                    [(app_id, group_id, player, day) for player in players],
                )
                rewarded = len(players)
            else:
                _take(conn, user_id, pig_id)
                change = _level_change(mine, count, count - 1, level_cap)
            cursor = conn.execute(
                "INSERT INTO wild_battles(wild_id,user_id,pig_id,level,won,seed,log,summary,"
                "fought_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    wild["id"],
                    user_id,
                    pig_id,
                    level,
                    int(won),
                    fight_seed,
                    json.dumps(result["log"], ensure_ascii=False),
                    json.dumps(
                        {"hp": result["hp"], "max_hp": result["max_hp"], "rounds": result["rounds"]}
                    ),
                    stamp,
                ),
            )
            wild = self._wild_view(
                conn, conn.execute("SELECT * FROM wild_pigs WHERE id=?", (wild["id"],)).fetchone()
            )
            return {
                "id": cursor.lastrowid,
                "day": day,
                "user": user,
                "wild": wild,
                "fighters": fighters,
                "result": result,
                "won": won,
                "change": change,
                "rewarded": rewarded,
            }

        return await self.run(challenge)

    @staticmethod
    def _raid_expire(conn, app_id: str, group_id: str, stamp: float) -> list[dict]:
        """Lobbies that never filled are cancelled; undecided parties retreat."""
        rows = conn.execute(
            "SELECT * FROM raids WHERE status IN ('forming','waiting') AND expires_at<=?",
            (stamp,),
        ).fetchall()
        expired = []
        for row in rows:
            status = "cancelled" if row["status"] == "forming" else "retreated"
            conn.execute(
                "UPDATE raids SET status=?,updated_at=? WHERE id=?", (status, stamp, row["id"])
            )
            if row["app_id"] == app_id and row["group_id"] == group_id:
                expired.append({**dict(row), "status": status, "dungeon": dungeon(row["dungeon"])})
        return expired

    async def _raid_sweep(self, app_id: str, group_id: str, stamp: float) -> list[dict]:
        """Commit timeouts on their own, so a rejected command cannot roll them back."""

        def sweep(conn):
            conn.execute("BEGIN IMMEDIATE")
            return self._raid_expire(conn, app_id, group_id, stamp)

        return await self.run(sweep)

    @staticmethod
    def _raid_view(conn, row) -> dict:
        raid = dict(row)
        raid["dungeon"] = dungeon(row["dungeon"])
        raid["leader_user"] = dict(
            conn.execute("SELECT * FROM users WHERE id=?", (row["leader"],)).fetchone()
        )
        members = []
        for member in conn.execute(
            "SELECT * FROM raid_members WHERE raid_id=? ORDER BY seat", (row["id"],)
        ):
            pig = dict(
                conn.execute("SELECT * FROM pigs WHERE id=?", (member["pig_id"],)).fetchone()
            )
            role = entry_for(pig.get("battle")).get("role", "输出")
            pig.pop("battle", None)
            pig["role"] = role if role in ROLES else "输出"
            members.append(
                {
                    **dict(member),
                    "mods": json.loads(member["mods"]),
                    "user": dict(
                        conn.execute(
                            "SELECT * FROM users WHERE id=?", (member["user_id"],)
                        ).fetchone()
                    ),
                    "pig": pig,
                }
            )
        raid["members"] = members
        return raid

    @staticmethod
    def _boss_pigs(conn) -> dict:
        """Boss slot id -> name and art; a missing boss pig is shown as possessed."""
        found = {
            row["id"]: {"name": row["name"], "asset": row["asset"]}
            for row in conn.execute("SELECT id,name,asset FROM pigs WHERE enabled=1")
        }
        return {
            slot: found.get(slot, {"name": "被附身的神秘小猪", "asset": ""})
            for item in DUNGEONS
            for slot in item["bosses"]
        }

    @classmethod
    def _boss_names(cls, conn) -> dict:
        return {slot: pig["name"] for slot, pig in cls._boss_pigs(conn).items()}

    @staticmethod
    def _group_raid(conn, app_id: str, group_id: str):
        return conn.execute(
            "SELECT * FROM raids WHERE app_id=? AND group_id=? AND status IN ('forming','waiting') "
            "ORDER BY id DESC LIMIT 1",
            (app_id, group_id),
        ).fetchone()

    @staticmethod
    def _raid_check_member(conn, user_id: int, dungeon_key: int, day: str, pig_id: str):
        busy = conn.execute(
            "SELECT r.* FROM raid_members m JOIN raids r ON r.id=m.raid_id "
            "WHERE m.user_id=? AND r.status IN ('forming','waiting')",
            (user_id,),
        ).fetchone()
        if busy:
            raise PiggyError(
                f"你已经在「{dungeon(busy['dungeon'])['name']}」的队伍里了，"
                "同一时间只能参加一支队伍。"
            )
        despair = conn.execute(
            "SELECT 1 FROM despair_members m JOIN despair_raids r ON r.id=m.raid_id "
            "WHERE m.user_id=? AND r.status='forming'",
            (user_id,),
        ).fetchone()
        if despair:
            raise PiggyError("你已经在绝境战队伍里了，同一时间只能参加一支队伍。")
        mythic = conn.execute(
            "SELECT 1 FROM mythic_members m JOIN mythic_raids r ON r.id=m.raid_id "
            "WHERE m.user_id=? AND r.status='forming'",
            (user_id,),
        ).fetchone()
        if mythic:
            raise PiggyError("你已经在大秘境队伍里了，同一时间只能参加一支队伍。")
        if conn.execute(
            "SELECT 1 FROM raid_entries WHERE user_id=? AND day=? AND dungeon=?",
            (user_id, day, dungeon_key),
        ).fetchone():
            raise PiggyError(
                f"你今天已经成功挑战过「{dungeon(dungeon_key)['name']}」了，"
                "每个副本每人每天成功挑战 1 次，"
                "可以换一个副本。"
            )
        if _owned(conn, user_id, pig_id) < 1:
            raise PiggyError("你的猪圈里没有这只小猪，换一只出战吧。")

    async def raid_status(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(EAST_ASIA).date().isoformat()

        def read(conn):
            conn.execute("BEGIN IMMEDIATE")
            expired = self._raid_expire(conn, app_id, group_id, now.timestamp())
            row = self._group_raid(conn, app_id, group_id)
            done = {
                r[0]
                for r in conn.execute(
                    "SELECT dungeon FROM raid_entries WHERE user_id=? AND day=?", (user_id, day)
                )
            }
            return {
                "raid": self._raid_view(conn, row) if row else None,
                "expired": expired,
                "done": done,
                "day": day,
                "now": now.timestamp(),
                "bosses": self._boss_pigs(conn),
            }

        return await self.run(read)

    async def open_raid(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        dungeon_key: int,
        pig_id: str,
        now: datetime | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(EAST_ASIA).date().isoformat()
        stamp = now.timestamp()

        expired = await self._raid_sweep(app_id, group_id, stamp)

        def create(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._raid_expire(conn, app_id, group_id, stamp)
            self._despair_expire(conn, app_id, group_id, stamp)
            if self._group_despair(conn, app_id, group_id):
                raise PiggyError("本群已有绝境战队伍，结束后才能开启普通副本。")
            if self._group_mythic(conn, app_id, group_id):
                raise PiggyError("本群已有大秘境队伍，结束后才能开启普通副本。")
            active = self._group_raid(conn, app_id, group_id)
            if active:
                name = dungeon(active["dungeon"])["name"]
                if active["status"] == "forming":
                    raise PiggyError(
                        f"本群已经有一支「{name}」队伍在组队，发送「加入副本 你的小猪」加入吧。"
                    )
                raise PiggyError(f"本群的「{name}」队伍还在副本里，等他们结束后再开新的副本。")
            self._raid_check_member(conn, user_id, dungeon_key, day, pig_id)
            cursor = conn.execute(
                "INSERT INTO raids(app_id,group_id,day,dungeon,leader,seed,created_at,expires_at,"
                "updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    app_id,
                    group_id,
                    day,
                    dungeon_key,
                    user_id,
                    secrets.randbits(32),
                    stamp,
                    stamp + TIMEOUT_MINUTES * 60,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO raid_members(raid_id,user_id,pig_id,seat,joined_at) VALUES(?,?,?,0,?)",
                (cursor.lastrowid, user_id, pig_id, stamp),
            )
            row = conn.execute("SELECT * FROM raids WHERE id=?", (cursor.lastrowid,)).fetchone()
            return {"raid": self._raid_view(conn, row), "expired": expired, "now": stamp}

        return await self.run(create)

    async def join_raid(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        pig_id: str,
        *,
        level_cap: int = 20,
        now: datetime | None = None,
        seed: int | None = None,
    ) -> dict:
        """Join the group's lobby; the fourth member starts the first boss right away."""
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(EAST_ASIA).date().isoformat()
        stamp = now.timestamp()

        expired = await self._raid_sweep(app_id, group_id, stamp)

        def join(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._raid_expire(conn, app_id, group_id, stamp)
            raid = self._group_raid(conn, app_id, group_id)
            if not raid:
                note = "上一支队伍 10 分钟内没凑满 4 人，已自动取消。" if expired else ""
                raise PiggyError(
                    f"{note}本群现在没有正在组队的副本，发送「开启副本 编号 你的小猪」发起一个吧。"
                )
            if raid["status"] != "forming":
                raise PiggyError("队伍已经出发了，副本开打后不能中途加入。")
            if conn.execute(
                "SELECT 1 FROM raid_members WHERE raid_id=? AND user_id=?", (raid["id"], user_id)
            ).fetchone():
                raise PiggyError("你已经在这支队伍里了，等其他人加入吧。")
            self._raid_check_member(conn, user_id, raid["dungeon"], day, pig_id)
            count = conn.execute(
                "SELECT count(*) FROM raid_members WHERE raid_id=?", (raid["id"],)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO raid_members(raid_id,user_id,pig_id,seat,joined_at) VALUES(?,?,?,?,?)",
                (raid["id"], user_id, pig_id, count, stamp),
            )
            if count + 1 < PARTY_SIZE:
                row = conn.execute("SELECT * FROM raids WHERE id=?", (raid["id"],)).fetchone()
                return {
                    "raid": self._raid_view(conn, row),
                    "started": False,
                    "expired": expired,
                    "now": stamp,
                }
            battle = self._raid_fight(conn, raid["id"], day, stamp, level_cap, seed, False)
            return {"raid": battle["raid"], "started": True, "battle": battle, "expired": expired}

        return await self.run(join)

    async def leave_raid(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.timestamp()

        await self._raid_sweep(app_id, group_id, stamp)

        def leave(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._raid_expire(conn, app_id, group_id, stamp)
            raid = self._group_raid(conn, app_id, group_id)
            if (
                not raid
                or not conn.execute(
                    "SELECT 1 FROM raid_members WHERE raid_id=? AND user_id=?",
                    (raid["id"], user_id),
                ).fetchone()
            ):
                raise PiggyError("你不在本群正在组队的队伍里。")
            if raid["status"] != "forming":
                raise PiggyError("队伍已经出发，不能退出；队长可以发送「撤退副本」结束副本。")
            if raid["leader"] == user_id:
                conn.execute(
                    "UPDATE raids SET status='cancelled',updated_at=? WHERE id=?",
                    (stamp, raid["id"]),
                )
                cancelled = True
            else:
                conn.execute(
                    "DELETE FROM raid_members WHERE raid_id=? AND user_id=?", (raid["id"], user_id)
                )
                for seat, (member,) in enumerate(
                    conn.execute(
                        "SELECT user_id FROM raid_members WHERE raid_id=? ORDER BY joined_at,seat",
                        (raid["id"],),
                    ).fetchall()
                ):
                    conn.execute(
                        "UPDATE raid_members SET seat=? WHERE raid_id=? AND user_id=?",
                        (seat, raid["id"], member),
                    )
                cancelled = False
            row = conn.execute("SELECT * FROM raids WHERE id=?", (raid["id"],)).fetchone()
            return {"raid": self._raid_view(conn, row), "cancelled": cancelled, "now": stamp}

        return await self.run(leave)

    def _raid_leader_waiting(self, conn, app_id, group_id, user_id, stamp, expired):
        self._raid_expire(conn, app_id, group_id, stamp)
        raid = self._group_raid(conn, app_id, group_id)
        if not raid:
            if any(e["status"] == "retreated" for e in expired):
                raise PiggyError("队长 10 分钟内没有决定，队伍已经自动撤退，已得到的奖励都保留着。")
            raise PiggyError("本群现在没有进行中的副本。")
        if raid["status"] != "waiting":
            raise PiggyError("队伍还在组队，满 4 人会自动出发。")
        if raid["leader"] != user_id:
            leader = conn.execute("SELECT * FROM users WHERE id=?", (raid["leader"],)).fetchone()
            raise PiggyError(f"只有队长 {player_name(leader)} 能决定继续还是撤退。")
        return raid

    async def continue_raid(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        *,
        level_cap: int = 20,
        now: datetime | None = None,
        seed: int | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(EAST_ASIA).date().isoformat()
        stamp = now.timestamp()

        expired = await self._raid_sweep(app_id, group_id, stamp)

        def advance(conn):
            conn.execute("BEGIN IMMEDIATE")
            raid = self._raid_leader_waiting(conn, app_id, group_id, user_id, stamp, expired)
            return self._raid_fight(conn, raid["id"], day, stamp, level_cap, seed, True)

        return await self.run(advance)

    async def retreat_raid(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.timestamp()

        expired = await self._raid_sweep(app_id, group_id, stamp)

        def retreat(conn):
            conn.execute("BEGIN IMMEDIATE")
            raid = self._raid_leader_waiting(conn, app_id, group_id, user_id, stamp, expired)
            conn.execute(
                "UPDATE raids SET status='retreated',updated_at=? WHERE id=?", (stamp, raid["id"])
            )
            row = conn.execute("SELECT * FROM raids WHERE id=?", (raid["id"],)).fetchone()
            return {"raid": self._raid_view(conn, row)}

        return await self.run(retreat)

    def _raid_fight(
        self, conn, raid_id: int, day: str, stamp: float, level_cap: int, seed, interlude: bool
    ) -> dict:
        """Fight the next boss inside the caller's transaction and settle every member."""
        raid = conn.execute("SELECT * FROM raids WHERE id=?", (raid_id,)).fetchone()
        info = dungeon(raid["dungeon"])
        stage = raid["stage"] + 1
        fight_seed = (raid["seed"] + stage * 7919) & 0xFFFFFFFF if seed is None else seed
        rng = random.Random(fight_seed ^ 0x5EED)
        enabled = [dict(r) for r in conn.execute("SELECT * FROM pigs WHERE enabled=1 ORDER BY id")]
        if not enabled:
            raise PiggyError("猪库没有启用的小猪，副本暂时无法进行。")
        members = self._raid_view(conn, raid)["members"]
        party, absent = [], []
        revived = None
        if interlude:
            # One fallen pig may rejoin for the next boss if the owner still has a copy.
            fallen = []
            for member in members:
                if member["alive"]:
                    continue
                count = _owned(conn, member["user_id"], member["pig_id"])
                if count < 1:
                    continue
                fallen.append(member)
            if fallen:
                pick = rng.choice(fallen)
                pick["alive"] = 1
                pick["hp"] = REST_REVIVE_HP
                conn.execute(
                    "UPDATE raid_members SET alive=1,hp=? WHERE raid_id=? AND user_id=?",
                    (REST_REVIVE_HP, raid_id, pick["user_id"]),
                )
                revived = pick
        for member in members:
            if not member["alive"]:
                continue
            count = _owned(conn, member["user_id"], member["pig_id"])
            if count < 1:
                absent.append(member)
                continue
            pig = dict(
                conn.execute("SELECT * FROM pigs WHERE id=?", (member["pig_id"],)).fetchone()
            )
            party.append(
                {
                    "seat": member["seat"],
                    "label": f"{player_name(member['user'])}的{pig['name']}",
                    "hp": member["hp"],
                    "mods": dict(member["mods"]),
                    "member": member,
                    "pig": pig,
                    "count": count,
                }
            )
        slot_id = info["bosses"][stage - 1]
        mechanics = mechanics_for(slot_id).MECHANICS
        cfg, rewards, events = new_config(), new_rewards(), []
        if interlude:
            for member in party:
                if revived and member["seat"] == revived["seat"]:
                    continue  # already set to REST_REVIVE_HP
                member["hp"] = min(1.0, member["hp"] + REST_HEAL)
            if revived:
                label = f"{player_name(revived['user'])}的{revived['pig']['name']}"
                events.append(
                    {
                        "kind": "关间",
                        "name": "战地急救",
                        "text": f"{label}被救了回来，以 {REST_REVIVE_HP:.0%} 生命重返战场",
                    }
                )
            for event in resolve([roll_interlude(rng)], party, rng, cfg, rewards, mechanics):
                events.append({**event, "kind": "关间"})
        for event in resolve(roll_entry(rng, info["key"]), party, rng, cfg, rewards, mechanics):
            events.append({**event, "kind": "进场"})
        boss_row = conn.execute(
            "SELECT * FROM pigs WHERE id=? AND enabled=1", (slot_id,)
        ).fetchone()
        possessed = boss_row is None
        boss_pig = dict(boss_row) if boss_row else dict(rng.choice(enabled))
        levels = [level_for(member["count"], level_cap) for member in party]
        boss = boss_fighter(
            boss_pig,
            entry_for(boss_pig["battle"]),
            stage,
            slot_id,
            ("被附身的" if possessed else "") + boss_pig["name"],
            party_levels=levels,
        )
        heroes = []
        for member, hero_level in zip(party, levels):
            personality_row = conn.execute(
                "SELECT personality FROM pig_personalities WHERE user_id=? AND pig_id=?",
                (member["member"]["user_id"], member["pig"]["id"]),
            ).fetchone()
            hero = fighter(
                member["pig"],
                entry_for(member["pig"]["battle"]),
                hero_level,
                member["label"],
                personality=personality_row[0] if personality_row else None,
            )
            hero.update(
                seat=member["seat"],
                start_hp=max(1, round(hero["stats"]["hp"] * member["hp"])),
                mods=member["mods"],
            )
            heroes.append(hero)
        ally = None
        if cfg.get("ally") and party:
            ally_pig = rng.choice(enabled)
            ally = fighter(
                ally_pig,
                entry_for(ally_pig["battle"]),
                max(1, round(sum(levels) / len(levels))),
                f"援军·{ally_pig['name']}",
            )
            heroes.append(ally)
        result = None
        if party:
            result = simulate_raid(heroes, boss, slot_id, cfg, fight_seed)
        won = bool(result and result["won"])
        if won:
            # A failed first fight may be retried the same day.  Record the
            # daily dungeon use only after the party has actually earned a win.
            conn.executemany(
                "INSERT OR IGNORE INTO raid_entries VALUES(?,?,?,?)",
                [
                    (member["user_id"], day, raid["dungeon"], raid_id)
                    for member in members
                ],
            )
        outcome = {h["seat"]: h for h in (result["heroes"] if result else [])}
        changes = {member["user_id"]: [] for member in members}
        fighters = []
        for member, hero, hero_level in zip(party, heroes, levels):
            state = outcome[member["seat"]]
            user_id, pig = member["member"]["user_id"], member["pig"]
            if state["alive"]:
                conn.execute(
                    "UPDATE raid_members SET hp=?,mods=? WHERE raid_id=? AND user_id=?",
                    (
                        state["hp"] / state["max_hp"],
                        json.dumps(member["mods"]),
                        raid_id,
                        user_id,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE raid_members SET hp=0,alive=0 WHERE raid_id=? AND user_id=?",
                    (raid_id, user_id),
                )
                before = _owned(conn, user_id, pig["id"])
                _take(conn, user_id, pig["id"])
                changes[user_id].append(_level_change(pig, before, before - 1, level_cap))
            fighters.append(
                {
                    "seat": member["seat"],
                    "user": member["member"]["user"],
                    "pig": {k: v for k, v in pig.items() if k != "battle"},
                    "level": hero_level,
                    "role": hero.get("role"),
                    "style": hero["style"],
                    "rarity": hero["rarity"],
                    "personality": hero.get("personality_name", ""),
                    "hp": state["hp"],
                    "max_hp": state["max_hp"],
                    "alive": state["alive"],
                    "dealt": state.get("dealt", 0),
                    "healed": state.get("healed", 0),
                    "taken": state.get("taken", 0),
                }
            )
        copies = 2 if rewards["mimic"] else 1
        prize = {k: v for k, v in boss_pig.items()}
        if won:
            for member in members:
                before = _owned(conn, member["user_id"], prize["id"])
                for _ in range(copies):
                    _give(conn, member["user_id"], prize["id"], stamp)
                changes[member["user_id"]].append(
                    _level_change(prize, before, before + copies, level_cap)
                )
        for _ in range(rewards["chest"]):
            for member in members:
                found = rng.choice(enabled)
                before = _owned(conn, member["user_id"], found["id"])
                _give(conn, member["user_id"], found["id"], stamp)
                changes[member["user_id"]].append(
                    _level_change(found, before, before + 1, level_cap)
                )
        bonus = int(won) + rewards["clover"]
        rewarded = 0
        if bonus:
            players = [
                row[0]
                for row in conn.execute(
                    "SELECT user_id FROM group_players WHERE app_id=? AND group_id=?",
                    (raid["app_id"], raid["group_id"]),
                )
            ]
            conn.executemany(
                """
                INSERT INTO draw_bonus VALUES(?,?,?,?,?) ON CONFLICT(app_id,group_id,user_id,day)
                DO UPDATE SET count=count+excluded.count
                """,
                [(raid["app_id"], raid["group_id"], player, day, bonus) for player in players],
            )
            rewarded = len(players)
        survivors = any(f["alive"] for f in fighters)
        if won and stage < len(info["bosses"]) and survivors:
            status = "waiting"
        elif won and stage == len(info["bosses"]):
            status = "cleared"
        else:
            status = "failed"
        personalities = []
        if status == "cleared":
            alive_seats = {fighter["seat"] for fighter in fighters if fighter["alive"]}
            for member, hero in zip(party, heroes):
                user_id, pig_id = member["member"]["user_id"], member["pig"]["id"]
                exists = conn.execute(
                    "SELECT 1 FROM pig_personalities WHERE user_id=? AND pig_id=?",
                    (user_id, pig_id),
                ).fetchone()
                if (
                    member["seat"] not in alive_seats
                    or exists
                    or rng.random() >= PERSONALITY_CHANCE
                ):
                    continue
                personality = rng.choice(choices_for(hero["role"]))
                conn.execute(
                    "INSERT INTO pig_personalities VALUES(?,?,?,?,?)",
                    (user_id, pig_id, personality, raid_id, stamp),
                )
                personalities.append(
                    {
                        "user": member["member"]["user"],
                        "pig": member["pig"]["name"],
                        "id": personality,
                        "name": PERSONALITIES[personality]["name"],
                        "skill": PERSONALITIES[personality]["skill"]["text"],
                    }
                )
        conn.execute(
            "UPDATE raids SET status=?,stage=?,expires_at=?,updated_at=? WHERE id=?",
            (status, stage, stamp + TIMEOUT_MINUTES * 60, stamp, raid_id),
        )
        summary = {
            "rounds": result["rounds"] if result else 0,
            "boss": result["boss"] if result else None,
            "heroes": result["heroes"] if result else [],
            "timeout": bool(result and result["timeout"]),
        }
        cursor = conn.execute(
            "INSERT INTO raid_battles(raid_id,stage,boss_pig,boss_level,won,seed,events,log,"
            "summary,fought_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                raid_id,
                stage,
                boss_pig["id"],
                0,  # bosses no longer use level; column kept for schema compatibility
                int(won),
                fight_seed,
                json.dumps(events, ensure_ascii=False),
                json.dumps(result["log"] if result else [], ensure_ascii=False),
                json.dumps(summary),
                stamp,
            ),
        )
        prize.pop("battle", None)
        view = self._raid_view(
            conn, conn.execute("SELECT * FROM raids WHERE id=?", (raid_id,)).fetchone()
        )
        return {
            "id": cursor.lastrowid,
            "raid": view,
            "day": day,
            "stage": stage,
            "status": status,
            "won": won,
            "boss": {
                "pig": prize,
                "label": boss["label"],
                "level": 0,
                "role": boss.get("role"),
                "style": boss["style"],
                "hp": result["boss"]["hp"] if result else boss["stats"]["hp"],
                "max_hp": result["boss"]["max_hp"] if result else boss["stats"]["hp"],
                "mechanics": mechanic_details(slot_id),
                "disabled": cfg.get("disabled"),
                "possessed": possessed,
            },
            "fighters": fighters,
            "ally": (
                {
                    "pig": {"name": ally["name"], "asset": ally["asset"]},
                    "label": ally["label"],
                    "level": ally["level"],
                    "role": ally.get("role"),
                    "style": ally["style"],
                    "hp": result["heroes"][-1]["hp"],
                    "max_hp": result["heroes"][-1]["max_hp"],
                    "alive": result["heroes"][-1]["alive"],
                    "dealt": result["heroes"][-1].get("dealt", 0),
                    "healed": result["heroes"][-1].get("healed", 0),
                    "taken": result["heroes"][-1].get("taken", 0),
                }
                if ally and result
                else None
            ),
            "absent": absent,
            "retired": [m for m in members if not m["alive"]],
            "revived": (
                {
                    "seat": revived["seat"],
                    "user": revived["user"],
                    "pig": revived["pig"],
                    "hp": REST_REVIVE_HP,
                }
                if revived
                else None
            ),
            "events": events,
            "result": result,
            "changes": [(member["user"], changes[member["user_id"]]) for member in members],
            "copies": copies if won else 0,
            "chest": rewards["chest"],
            "bonus": bonus,
            "rewarded": rewarded,
            "personalities": personalities,
            "next_boss": (
                self._boss_names(conn)[info["bosses"][stage]] if status == "waiting" else ""
            ),
        }

    async def collection(self, user_id: int) -> dict:
        def read(conn):
            conn.execute("BEGIN")
            rows = conn.execute(
                """
                SELECT p.*,coalesce(c.count,0) AS count,c.first_at,c.last_at,
                    pp.personality FROM pigs p
                LEFT JOIN collections c ON c.pig_id=p.id AND c.user_id=?
                LEFT JOIN pig_personalities pp ON pp.pig_id=p.id AND pp.user_id=?
                WHERE p.enabled=1 OR c.count>0 ORDER BY p.enabled DESC,p.sort_order,p.id
            """,
                (user_id, user_id),
            ).fetchall()
            entries = [dict(r) for r in rows]
            for entry in entries:
                entry["rarity"] = rarity_for(entry["id"])
                nature = PERSONALITIES.get(entry.get("personality") or "")
                entry["personality_name"] = nature["name"] if nature else ""
            return {
                "entries": entries,
                "active_total": sum(p["enabled"] for p in entries),
                "unlocked": sum(bool(p["enabled"] and p["count"]) for p in entries),
                "total": sum(p["count"] for p in entries),
                "species": sum(p["count"] > 0 for p in entries),
            }

        return await self.run(read)

    async def pig_personality(self, user_id: int, pig_id: str) -> str | None:
        return await self.run(
            lambda conn: (
                row[0]
                if (
                    row := conn.execute(
                        "SELECT personality FROM pig_personalities WHERE user_id=? AND pig_id=?",
                        (user_id, pig_id),
                    ).fetchone()
                )
                else None
            )
        )

    async def rankings(self, app_id: str, group_id: str) -> dict:
        if not group_id:
            raise PiggyError("请在群里查看本群玩家排行。")

        def read(conn):
            rows = conn.execute(
                """
                WITH scores AS (
                    SELECT u.id,u.open_id,u.nickname,u.alias,count(c.pig_id) species,coalesce(sum(c.count),0) total
                    FROM group_players g JOIN users u ON u.id=g.user_id
                    LEFT JOIN collections c ON c.user_id=u.id
                    WHERE g.app_id=? AND g.group_id=? GROUP BY u.id
                ), ranked AS (
                    SELECT *,RANK() OVER (ORDER BY species DESC) AS species_rank,
                    RANK() OVER (ORDER BY total DESC) AS total_rank FROM scores
                ) SELECT * FROM ranked
            """,
                (app_id, group_id),
            ).fetchall()
            return {
                kind: [
                    {**dict(row), "rank": row[f"{kind}_rank"]}
                    for row in sorted(rows, key=lambda row: (-row[kind], row["id"]))[:10]
                ]
                for kind in ("species", "total")
            }

        return await self.run(read)

    async def find_pig(self, query: str) -> dict:
        query = " ".join(query.split())
        if not query:
            raise PiggyError("请写上小猪的名字。")

        def read(conn):
            row = conn.execute(
                "SELECT * FROM pigs WHERE id=? OR name=? ORDER BY enabled DESC,sort_order LIMIT 1",
                (query.lower(), query),
            ).fetchone()
            if not row:
                raise PiggyError(f"没有找到「{query}」，请输入图鉴里小猪的完整名字。")
            return dict(row)

        return await self.run(read)

    async def pig_count(self, user_id: int, pig_id: str) -> int:
        return await self.run(lambda c: _owned(c, user_id, pig_id))

    async def find_group_player(self, app_id: str, group_id: str, name: str) -> dict:
        name = " ".join(name.lstrip("@＠").split())
        if not name:
            raise PiggyError("请 @ 你要找的玩家。")

        number = name.lstrip("#＃")
        by_number = name[:1] in "#＃" and number.isdigit()

        def read(conn):
            if by_number:
                row = conn.execute(
                    """
                    SELECT u.* FROM group_players g JOIN users u ON u.id=g.user_id
                    WHERE g.app_id=? AND g.group_id=? AND u.id=?
                    """,
                    (app_id, group_id, int(number)),
                ).fetchone()
                if not row:
                    raise PiggyError(
                        f"本群没有编号为 #{number} 的玩家，请让对方发送「我的猪圈」核对。"
                    )
                return dict(row)
            rows = conn.execute(
                """
                SELECT u.* FROM group_players g JOIN users u ON u.id=g.user_id
                WHERE g.app_id=? AND g.group_id=? AND (u.alias=? OR u.nickname=?)
                """,
                (app_id, group_id, name, name),
            ).fetchall()
            if not rows:
                raise PiggyError(
                    f"本群没有找到叫「{name}」的玩家。群名片无法识别，请改用对方的玩家编号，"
                    "例如：斗猪 #12 你的小猪（对方发送「我的猪圈」即可看到编号）。"
                )
            if len(rows) > 1:
                raise PiggyError(f"本群有多位玩家叫「{name}」，请改用 @ 或对方的玩家编号。")
            return dict(rows[0])

        return await self.run(read)

    @staticmethod
    def _expire(conn, now: float):
        conn.execute(
            "UPDATE requests SET status='expired',resolved_at=? "
            "WHERE status='pending' AND expires_at<=?",
            (now, now),
        )

    @staticmethod
    def _duels_today(conn, user_id: int, day: str) -> int:
        return conn.execute(
            "SELECT count(*) FROM battle_records WHERE day=? AND (a_user=? OR b_user=?)",
            (day, user_id, user_id),
        ).fetchone()[0]

    @staticmethod
    def _request_view(conn, row) -> dict:
        def user(user_id):
            return dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

        def pig(pig_id):
            if not pig_id:
                return None
            found = dict(conn.execute("SELECT * FROM pigs WHERE id=?", (pig_id,)).fetchone())
            found.pop("battle", None)
            return found

        return {
            **dict(row),
            "from": user(row["from_user"]),
            "to": user(row["to_user"]),
            "give": pig(row["give_pig"]),
            "want": pig(row["want_pig"]),
        }

    async def create_request(
        self,
        app_id: str,
        group_id: str,
        kind: str,
        from_user: int,
        to_user: int,
        give_pig: str,
        want_pig: str | None = None,
        *,
        ttl_minutes: int = 10,
        daily_limit: int = 5,
        now: datetime | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.timestamp()
        day = now.astimezone(EAST_ASIA).date().isoformat()
        label = REQUEST_LABELS[kind]

        def create(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._expire(conn, stamp)
            if from_user == to_user:
                raise PiggyError(f"不能和自己{label}哦。")
            give = conn.execute("SELECT name FROM pigs WHERE id=?", (give_pig,)).fetchone()
            if _owned(conn, from_user, give_pig) < 1:
                raise PiggyError(f"你的猪圈里没有「{give['name']}」。")
            if kind == "trade":
                want = conn.execute("SELECT name FROM pigs WHERE id=?", (want_pig,)).fetchone()
                if give_pig == want_pig:
                    raise PiggyError("同一种小猪就不用交换啦。")
                if _owned(conn, to_user, want_pig) < 1:
                    raise PiggyError(f"对方的猪圈里没有「{want['name']}」。")
            elif self._duels_today(conn, from_user, day) >= daily_limit:
                raise PiggyError(f"你今天已经斗了 {daily_limit} 场猪，明天再来吧。")
            pending = conn.execute(
                """
                SELECT from_user FROM requests WHERE app_id=? AND group_id=? AND kind=?
                AND status='pending' AND (from_user=? OR to_user=?)
                """,
                (app_id, group_id, kind, from_user, to_user),
            ).fetchall()
            if any(row["from_user"] == from_user for row in pending):
                raise PiggyError(f"你在本群已有一个待处理的{label}请求，可以先发送「取消请求」。")
            if pending:
                raise PiggyError(f"对方在本群还有一个待处理的{label}请求，请稍后再试。")
            cursor = conn.execute(
                """
                INSERT INTO requests(app_id,group_id,kind,from_user,to_user,give_pig,want_pig,
                created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    app_id,
                    group_id,
                    kind,
                    from_user,
                    to_user,
                    give_pig,
                    want_pig,
                    stamp,
                    stamp + ttl_minutes * 60,
                ),
            )
            row = conn.execute("SELECT * FROM requests WHERE id=?", (cursor.lastrowid,)).fetchone()
            view = self._request_view(conn, row)
            view["give_count"] = _owned(conn, from_user, give_pig)
            return view

        return await self.run(create)

    async def respond(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        kind: str,
        accept: bool,
        pig_id: str | None = None,
        *,
        level_cap: int = 20,
        daily_limit: int = 5,
        now: datetime | None = None,
        seed: int | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.timestamp()
        day = now.astimezone(EAST_ASIA).date().isoformat()
        label = REQUEST_LABELS[kind]

        def respond(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._expire(conn, stamp)
            row = conn.execute(
                """
                SELECT * FROM requests WHERE app_id=? AND group_id=? AND to_user=? AND kind=?
                AND status='pending' ORDER BY id DESC LIMIT 1
                """,
                (app_id, group_id, user_id, kind),
            ).fetchone()
            if not row:
                raise PiggyError(f"你在本群没有待处理的{label}请求（可能已过期或被撤回）。")
            view = self._request_view(conn, row)

            def close(status: str):
                conn.execute(
                    "UPDATE requests SET status=?,resolved_at=? WHERE id=?",
                    (status, stamp, row["id"]),
                )

            if not accept:
                close("declined")
                return {"request": view, "accepted": False}
            challenger, target = row["from_user"], row["to_user"]
            give_pig = row["give_pig"]
            if _owned(conn, challenger, give_pig) < 1:
                close("failed")
                return {
                    "request": view,
                    "accepted": False,
                    "error": f"对方的「{view['give']['name']}」已经不在猪圈里了，请求作废。",
                }

            def pig(pig_id):
                return dict(conn.execute("SELECT * FROM pigs WHERE id=?", (pig_id,)).fetchone())

            if kind == "trade":
                want_pig = row["want_pig"]
                if _owned(conn, target, want_pig) < 1:
                    raise PiggyError(f"你的猪圈里已经没有「{view['want']['name']}」了。")
                give, want = pig(give_pig), pig(want_pig)
                counts = {
                    (challenger, give_pig): _owned(conn, challenger, give_pig),
                    (challenger, want_pig): _owned(conn, challenger, want_pig),
                    (target, give_pig): _owned(conn, target, give_pig),
                    (target, want_pig): _owned(conn, target, want_pig),
                }
                _transfer(conn, challenger, target, give_pig, stamp)
                _transfer(conn, target, challenger, want_pig, stamp)
                close("accepted")
                return {
                    "request": view,
                    "accepted": True,
                    "changes": {
                        "from_give": _level_change(
                            give,
                            counts[(challenger, give_pig)],
                            counts[(challenger, give_pig)] - 1,
                            level_cap,
                        ),
                        "from_want": _level_change(
                            want,
                            counts[(challenger, want_pig)],
                            counts[(challenger, want_pig)] + 1,
                            level_cap,
                        ),
                        "to_want": _level_change(
                            want,
                            counts[(target, want_pig)],
                            counts[(target, want_pig)] - 1,
                            level_cap,
                        ),
                        "to_give": _level_change(
                            give,
                            counts[(target, give_pig)],
                            counts[(target, give_pig)] + 1,
                            level_cap,
                        ),
                    },
                }

            if not pig_id:
                raise PiggyError("请写上你要出战的小猪，例如：接受斗猪 猪人")
            if _owned(conn, target, pig_id) < 1:
                raise PiggyError("你的猪圈里没有这只小猪，换一只出战吧。")
            if self._duels_today(conn, target, day) >= daily_limit:
                raise PiggyError(f"你今天已经斗了 {daily_limit} 场猪，可以发送「拒绝斗猪」。")
            if self._duels_today(conn, challenger, day) >= daily_limit:
                close("failed")
                return {
                    "request": view,
                    "accepted": False,
                    "error": "对方今天的斗猪次数已经用完，请求作废。",
                }
            users = {
                uid: dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
                for uid in (challenger, target)
            }
            pigs = {challenger: pig(give_pig), target: pig(pig_id)}
            counts = {uid: _owned(conn, uid, pigs[uid]["id"]) for uid in pigs}
            levels = {uid: level_for(counts[uid], level_cap) for uid in pigs}
            fighters = [
                fighter(
                    pigs[uid],
                    entry_for(pigs[uid]["battle"]),
                    levels[uid],
                    f"{player_name(users[uid])}的{pigs[uid]['name']}",
                )
                for uid in (challenger, target)
            ]
            fight_seed = secrets.randbits(32) if seed is None else seed
            result = simulate(fighters[0], fighters[1], fight_seed)
            winner = (challenger, target)[result["winner"]]
            loser = target if winner == challenger else challenger
            prize = pigs[loser]
            loser_before = counts[loser]
            winner_before = _owned(conn, winner, prize["id"])
            _transfer(conn, loser, winner, prize["id"], stamp)
            conn.execute(
                """
                INSERT INTO battle_records(request_id,day,fought_at,a_user,a_pig,a_level,b_user,
                b_pig,b_level,winner,seed,log,summary) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["id"],
                    day,
                    stamp,
                    challenger,
                    give_pig,
                    levels[challenger],
                    target,
                    pig_id,
                    levels[target],
                    winner,
                    fight_seed,
                    json.dumps(result["log"], ensure_ascii=False),
                    json.dumps(
                        {
                            "hp": result["hp"],
                            "max_hp": result["max_hp"],
                            "rounds": result["rounds"],
                            "styles": [unit["style"] for unit in fighters],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            record_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            close("accepted")
            return {
                "request": view,
                "accepted": True,
                "users": users,
                "fighters": fighters,
                "result": result,
                "record_id": record_id,
                "day": day,
                "winner": users[winner],
                "loser": users[loser],
                "loser_change": _level_change(prize, loser_before, loser_before - 1, level_cap),
                "winner_change": _level_change(prize, winner_before, winner_before + 1, level_cap),
                "duels_left": {
                    uid: max(0, daily_limit - self._duels_today(conn, uid, day)) for uid in users
                },
            }

        return await self.run(respond)

    async def cancel_requests(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> list[dict]:
        stamp = (now or datetime.now(timezone.utc)).timestamp()

        def cancel(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._expire(conn, stamp)
            rows = conn.execute(
                "SELECT * FROM requests WHERE app_id=? AND group_id=? AND from_user=? "
                "AND status='pending'",
                (app_id, group_id, user_id),
            ).fetchall()
            if not rows:
                raise PiggyError("你在本群没有待处理的请求。")
            conn.executemany(
                "UPDATE requests SET status='cancelled',resolved_at=? WHERE id=?",
                [(stamp, row["id"]) for row in rows],
            )
            return [self._request_view(conn, row) for row in rows]

        return await self.run(cancel)

    async def list_requests(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> dict:
        stamp = (now or datetime.now(timezone.utc)).timestamp()

        def read(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._expire(conn, stamp)
            rows = conn.execute(
                "SELECT * FROM requests WHERE app_id=? AND group_id=? AND status='pending' "
                "AND (from_user=? OR to_user=?) ORDER BY id",
                (app_id, group_id, user_id, user_id),
            ).fetchall()
            views = [self._request_view(conn, row) for row in rows]
            return {
                "incoming": [v for v in views if v["to_user"] == user_id],
                "outgoing": [v for v in views if v["from_user"] == user_id],
            }

        return await self.run(read)

    @staticmethod
    def _shop_items(conn, app_id: str, group_id: str, day: str) -> list[dict]:
        """Stock today's shelf on first visit; later visits see the same five pigs."""
        rows = conn.execute(
            "SELECT * FROM shop_items WHERE app_id=? AND group_id=? AND day=? ORDER BY slot",
            (app_id, group_id, day),
        ).fetchall()
        if not rows:
            pool = [r[0] for r in conn.execute("SELECT id FROM pigs WHERE enabled=1")]
            if not pool:
                raise PiggyError("猪库没有启用的小猪，商店暂时无法上货。")
            picks = secrets.SystemRandom().sample(pool, min(SHOP_SIZE, len(pool)))
            conn.executemany(
                "INSERT OR IGNORE INTO shop_items(app_id,group_id,day,slot,pig_id) "
                "VALUES(?,?,?,?,?)",
                [(app_id, group_id, day, slot, pig) for slot, pig in enumerate(picks, 1)],
            )
            rows = conn.execute(
                "SELECT * FROM shop_items WHERE app_id=? AND group_id=? AND day=? ORDER BY slot",
                (app_id, group_id, day),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            pig = dict(conn.execute("SELECT * FROM pigs WHERE id=?", (row["pig_id"],)).fetchone())
            battle = entry_for(pig.pop("battle", None))
            pig["role"] = battle.get("role", "输出")
            pig["tags"] = battle.get("tags", [pig["role"]])
            pig["rarity"] = rarity_for(pig["id"])
            item["pig"] = pig
            item["buyer"] = (
                dict(conn.execute("SELECT * FROM users WHERE id=?", (row["sold_to"],)).fetchone())
                if row["sold_to"]
                else None
            )
            items.append(item)
        return items

    async def shop(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(EAST_ASIA).date().isoformat()

        def read(conn):
            conn.execute("BEGIN IMMEDIATE")
            items = self._shop_items(conn, app_id, group_id, day)
            for item in items:
                item["owned"] = _owned(conn, user_id, item["pig_id"])
            return {"day": day, "items": items}

        return await self.run(read)

    async def shop_exchange(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        slot: int,
        pay_pig: str,
        *,
        level_cap: int = 20,
        now: datetime | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(EAST_ASIA).date().isoformat()
        stamp = now.timestamp()

        def exchange(conn):
            conn.execute("BEGIN IMMEDIATE")
            items = self._shop_items(conn, app_id, group_id, day)
            item = next((i for i in items if i["slot"] == slot), None)
            if not item:
                raise PiggyError(f"商店今天只有 1–{len(items)} 号小猪。")
            if item["sold_to"]:
                raise PiggyError(f"{slot} 号「{item['pig']['name']}」已经被换走了，看看别的吧。")
            if pay_pig == item["pig_id"]:
                raise PiggyError("不能用同一种小猪来换哦。")
            paid = dict(conn.execute("SELECT * FROM pigs WHERE id=?", (pay_pig,)).fetchone())
            pay_before = _owned(conn, user_id, pay_pig)
            if pay_before < 1:
                raise PiggyError(f"你的猪圈里没有「{paid['name']}」。")
            got = dict(conn.execute("SELECT * FROM pigs WHERE id=?", (item["pig_id"],)).fetchone())
            got_before = _owned(conn, user_id, item["pig_id"])
            cursor = conn.execute(
                "UPDATE shop_items SET sold_to=?,paid_pig=?,sold_at=? "
                "WHERE app_id=? AND group_id=? AND day=? AND slot=? AND sold_to IS NULL",
                (user_id, pay_pig, stamp, app_id, group_id, day, slot),
            )
            if cursor.rowcount != 1:
                raise PiggyError(f"{slot} 号小猪刚刚被别人换走了。")
            _take(conn, user_id, pay_pig)
            _give(conn, user_id, item["pig_id"], stamp)
            return {
                "slot": slot,
                "paid": _level_change(paid, pay_before, pay_before - 1, level_cap),
                "got": _level_change(got, got_before, got_before + 1, level_cap),
                "left": sum(1 for i in items if not i["sold_to"]) - 1,
            }

        return await self.run(exchange)

    # ----- 小猪大秘境 -----

    @staticmethod
    def _mythic_expire(conn, app_id: str, group_id: str, stamp: float) -> list[dict]:
        rows = conn.execute(
            "SELECT * FROM mythic_raids WHERE status='forming' AND expires_at<=?", (stamp,)
        ).fetchall()
        expired = []
        for row in rows:
            conn.execute(
                "UPDATE mythic_raids SET status='cancelled',updated_at=? WHERE id=?",
                (stamp, row["id"]),
            )
            if row["app_id"] == app_id and row["group_id"] == group_id:
                expired.append({**dict(row), "status": "cancelled"})
        return expired

    @staticmethod
    def _group_mythic(conn, app_id: str, group_id: str):
        return conn.execute(
            "SELECT * FROM mythic_raids WHERE app_id=? AND group_id=? AND status='forming' "
            "ORDER BY id DESC LIMIT 1",
            (app_id, group_id),
        ).fetchone()

    @classmethod
    def _mythic_key(cls, conn, user_id: int, *, require_unlock: bool = True):
        if require_unlock:
            cls._mythic_require_unlocked(conn, user_id)
        row = conn.execute("SELECT * FROM mythic_keys WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _mythic_key_progress(conn, user_id: int) -> dict:
        rows = conn.execute(
            "SELECT r.dungeon,MAX(r.key_level) AS level FROM mythic_records r "
            "JOIN mythic_members m ON m.raid_id=r.raid_id "
            "WHERE m.user_id=? AND r.timed=1 GROUP BY r.dungeon",
            (user_id,),
        ).fetchall()
        best = {row["dungeon"]: row["level"] for row in rows}
        dungeon_keys = {item["key"] for item in DUNGEONS}
        floor = (
            max(START_KEY_LEVEL, min(best[key] for key in dungeon_keys))
            if dungeon_keys <= best.keys()
            else START_KEY_LEVEL
        )
        return {"floor": floor, "dungeons": best}

    @classmethod
    def _mythic_require_unlocked(cls, conn, user_id: int):
        cleared = cls._despair_unlock(conn, user_id)
        missing = [item["name"] for item in DUNGEONS if item["key"] not in cleared]
        if missing:
            raise PiggyError(f"小猪大秘境尚未解锁，还需通关：{'、'.join(missing)}。")

    @staticmethod
    def _mythic_pig(conn, user_id: int, pig_id: str, level_cap: int) -> dict:
        row = conn.execute("SELECT * FROM pigs WHERE id=? AND enabled=1", (pig_id,)).fetchone()
        if not row:
            raise PiggyError("所选小猪不存在或当前未启用。")
        count = _owned(conn, user_id, pig_id)
        if count < 1:
            raise PiggyError(f"你的猪圈里没有「{row['name']}」。")
        level = level_for(count, level_cap)
        if level < MIN_LEVEL:
            raise PiggyError(
                f"大秘境只允许 Lv{MIN_LEVEL} 及以上的小猪参战。"
                f"「{row['name']}」当前为 Lv{level}。"
            )
        return {**dict(row), "count": count, "level": level}

    @classmethod
    def _mythic_view(cls, conn, row) -> dict | None:
        if not row:
            return None
        raid = dict(row)
        raid["dungeon"] = dungeon(row["dungeon"])
        raid["leader_user"] = dict(
            conn.execute("SELECT * FROM users WHERE id=?", (row["leader"],)).fetchone()
        )
        raid["members"] = []
        for member in conn.execute(
            "SELECT * FROM mythic_members WHERE raid_id=? ORDER BY seat", (row["id"],)
        ):
            pig = dict(conn.execute("SELECT * FROM pigs WHERE id=?", (member["pig_id"],)).fetchone())
            role = entry_for(pig.get("battle")).get("role", "输出")
            pig.pop("battle", None)
            pig["role"] = role if role in ROLES else "输出"
            raid["members"].append(
                {
                    **dict(member),
                    "user": dict(
                        conn.execute("SELECT * FROM users WHERE id=?", (member["user_id"],)).fetchone()
                    ),
                    "pig": pig,
                }
            )
        raid["report"] = json.loads(raid["report"]) if raid.get("report") else None
        return raid

    async def mythic_status(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.timestamp()
        week = _week_key(now)

        def read(conn):
            conn.execute("BEGIN IMMEDIATE")
            expired = self._mythic_expire(conn, app_id, group_id, stamp)
            cleared = self._despair_unlock(conn, user_id)
            unlocked = len(cleared) == len(DUNGEONS)
            key = self._mythic_key(conn, user_id, require_unlock=False) if unlocked else None
            progress = self._mythic_key_progress(conn, user_id)
            row = self._group_mythic(conn, app_id, group_id)
            return {
                "week": week,
                "unlocked": unlocked,
                "missing": [item["name"] for item in DUNGEONS if item["key"] not in cleared],
                "key": key,
                "key_floor": progress["floor"],
                "key_progress": progress["dungeons"],
                "weekly_rewards": [
                    dict(reward)
                    for reward in conn.execute(
                        "SELECT tier,key_level,items,granted_at FROM mythic_weekly_rewards "
                        "WHERE user_id=? AND week=? ORDER BY key_level",
                        (user_id, week),
                    )
                ],
                "weekly_milestones": [dict(item) for item in WEEKLY_REWARD_MILESTONES],
                "raid": self._mythic_view(conn, row),
                "expired": expired,
                "now": stamp,
            }

        return await self.run(read)

    async def claim_mythic_key(
        self, user_id: int, now: datetime | None = None
    ) -> dict:
        stamp = (now or datetime.now(timezone.utc)).timestamp()

        def claim(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._mythic_require_unlocked(conn, user_id)
            key = self._mythic_key(conn, user_id, require_unlock=False)
            created = key is None
            if created:
                dungeon_key = _SYSTEM_RANDOM.choice([item["key"] for item in DUNGEONS])
                conn.execute(
                    "INSERT INTO mythic_keys(user_id,dungeon,level,updated_at) VALUES(?,?,?,?)",
                    (user_id, dungeon_key, START_KEY_LEVEL, stamp),
                )
                key = self._mythic_key(conn, user_id, require_unlock=False)
            progress = self._mythic_key_progress(conn, user_id)
            return {"key": key, "created": created, **progress}

        return await self.run(claim)

    async def lower_mythic_key(
        self, user_id: int, now: datetime | None = None
    ) -> dict:
        stamp = (now or datetime.now(timezone.utc)).timestamp()

        def lower(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._mythic_require_unlocked(conn, user_id)
            key = self._mythic_key(conn, user_id, require_unlock=False)
            if not key:
                raise PiggyError("你还没有钥石，请先发送「领取钥匙」。")
            busy = conn.execute(
                "SELECT 1 FROM mythic_members m JOIN mythic_raids r ON r.id=m.raid_id "
                "WHERE m.user_id=? AND r.status='forming'",
                (user_id,),
            ).fetchone()
            if busy:
                raise PiggyError("你正在大秘境队伍中，不能降级钥石。")
            progress = self._mythic_key_progress(conn, user_id)
            old_level = key["level"]
            new_level = max(progress["floor"], old_level - 1)
            if new_level != old_level:
                conn.execute(
                    "UPDATE mythic_keys SET level=?,updated_at=? WHERE user_id=?",
                    (new_level, stamp, user_id),
                )
                key = {**key, "level": new_level, "updated_at": stamp}
            return {
                "key": key,
                "old_level": old_level,
                "changed": new_level != old_level,
                **progress,
            }

        return await self.run(lower)

    async def open_mythic(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        pig_id: str,
        *,
        level_cap: int = 20,
        now: datetime | None = None,
        seed: int | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp, week = now.timestamp(), _week_key(now)

        def create(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._mythic_expire(conn, app_id, group_id, stamp)
            if self._group_mythic(conn, app_id, group_id):
                raise PiggyError("本群已经有一支大秘境队伍，发送「加入大秘境 你的小猪」加入吧。")
            if self._group_raid(conn, app_id, group_id) or self._group_despair(conn, app_id, group_id):
                raise PiggyError("本群已有其他副本队伍，结束后才能开启大秘境。")
            busy = conn.execute(
                "SELECT 1 FROM mythic_members m JOIN mythic_raids r ON r.id=m.raid_id "
                "WHERE m.user_id=? AND r.status='forming'",
                (user_id,),
            ).fetchone()
            if busy:
                raise PiggyError("你已经在一支大秘境队伍里了。")
            regular = conn.execute(
                "SELECT 1 FROM raid_members m JOIN raids r ON r.id=m.raid_id "
                "WHERE m.user_id=? AND r.status IN ('forming','waiting')",
                (user_id,),
            ).fetchone()
            despair = conn.execute(
                "SELECT 1 FROM despair_members m JOIN despair_raids r ON r.id=m.raid_id "
                "WHERE m.user_id=? AND r.status='forming'",
                (user_id,),
            ).fetchone()
            if regular or despair:
                raise PiggyError("你已经在其他副本队伍里，不能同时开启大秘境。")
            key = self._mythic_key(conn, user_id)
            if not key:
                raise PiggyError("你还没有钥石，请先发送「领取钥匙」。")
            self._mythic_pig(conn, user_id, pig_id, level_cap)
            cursor = conn.execute(
                "INSERT INTO mythic_raids(app_id,group_id,week,dungeon,key_level,leader,seed,"
                "created_at,expires_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    app_id,
                    group_id,
                    week,
                    key["dungeon"],
                    key["level"],
                    user_id,
                    secrets.randbits(32) if seed is None else seed,
                    stamp,
                    stamp + TIMEOUT_MINUTES * 60,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO mythic_members VALUES(?,?,?,?,?)",
                (cursor.lastrowid, user_id, pig_id, 0, stamp),
            )
            row = conn.execute("SELECT * FROM mythic_raids WHERE id=?", (cursor.lastrowid,)).fetchone()
            return {"raid": self._mythic_view(conn, row), "now": stamp}

        return await self.run(create)

    def _mythic_fight(self, conn, raid_id: int, stamp: float, level_cap: int) -> dict:
        raid = conn.execute("SELECT * FROM mythic_raids WHERE id=?", (raid_id,)).fetchone()
        rows = conn.execute(
            "SELECT m.*,u.nickname,u.alias FROM mythic_members m "
            "JOIN users u ON u.id=m.user_id WHERE m.raid_id=? ORDER BY m.seat",
            (raid_id,),
        ).fetchall()
        members = []
        for row in rows:
            pig = self._mythic_pig(conn, row["user_id"], row["pig_id"], level_cap)
            personality = conn.execute(
                "SELECT personality FROM pig_personalities WHERE user_id=? AND pig_id=?",
                (row["user_id"], row["pig_id"]),
            ).fetchone()
            user = dict(conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone())
            members.append(
                {
                    "seat": row["seat"],
                    "user": user,
                    "pig": pig,
                    "count": pig["count"],
                    "label": f"{player_name(user)}的{pig['name']}",
                    "personality": personality[0] if personality else None,
                }
            )
        catalog = {
            row["id"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM pigs WHERE enabled=1",
            )
        }
        missing = [slot for slot in dungeon(raid["dungeon"])["bosses"] if slot not in catalog]
        if missing:
            raise PiggyError("大秘境 Boss 数据缺失，请先恢复猪库后重试。")
        report = simulate_mythic(
            members,
            catalog,
            raid["dungeon"],
            raid["key_level"],
            raid["seed"],
            level_cap=level_cap,
        )
        status = "cleared" if report["completed"] else "failed"
        conn.execute(
            "UPDATE mythic_raids SET status=?,report=?,updated_at=? WHERE id=?",
            (status, json.dumps(report, ensure_ascii=False), stamp, raid_id),
        )
        team_key = ":".join(str(row["user_id"]) for row in sorted(rows, key=lambda r: r["user_id"]))
        conn.execute(
            "INSERT INTO mythic_records(raid_id,app_id,group_id,week,team_key,dungeon,key_level,"
            "completed,timed,upgrade,rounds,limit_rounds,deaths,add_count,score,report,completed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                raid_id,
                raid["app_id"],
                raid["group_id"],
                raid["week"],
                team_key,
                raid["dungeon"],
                raid["key_level"],
                int(report["completed"]),
                int(report["timed"]),
                report["upgrade"],
                report["rounds"],
                report["limit"],
                report["deaths"],
                report["add_count"],
                report["score"],
                json.dumps(report, ensure_ascii=False),
                stamp,
            ),
        )
        key_floor = self._mythic_key_progress(conn, raid["leader"])["floor"]
        next_level = max(key_floor, raid["key_level"] + report["upgrade"])
        choices = [item["key"] for item in DUNGEONS if item["key"] != raid["dungeon"]]
        next_dungeon = random.Random(raid["seed"] ^ 0xA11D).choice(choices)
        conn.execute(
            "UPDATE mythic_keys SET dungeon=?,level=?,updated_at=? WHERE user_id=?",
            (next_dungeon, next_level, stamp, raid["leader"]),
        )
        rewards = []
        if report["timed"]:
            pool = [dict(row) for row in conn.execute("SELECT * FROM pigs WHERE enabled=1")]
            day = datetime.fromtimestamp(stamp, timezone.utc).astimezone(EAST_ASIA).date().isoformat()
            for member in members:
                user_id = member["user"]["id"]
                entered = member["pig"]
                _give(conn, user_id, entered["id"], stamp)
                draws = run_reward_draws(raid["key_level"], True)
                if draws:
                    conn.execute(
                        "INSERT INTO draw_bonus VALUES(?,?,?,?,?) "
                        "ON CONFLICT(app_id,group_id,user_id,day) DO UPDATE SET count=count+?",
                        (raid["app_id"], raid["group_id"], user_id, day, draws, draws),
                    )
                weekly = []
                reward_rng = random.Random(
                    raid["seed"] ^ 0xC0FFEE ^ (user_id * 0x9E3779B1)
                )
                for milestone in weekly_reward_milestones(raid["key_level"], True):
                    rarity_pool = (
                        [pig for pig in pool if rarity_for(pig["id"]) == milestone["rarity"]]
                        if milestone["rarity"]
                        else pool
                    )
                    if not rarity_pool:
                        continue
                    picks = [reward_rng.choice(rarity_pool) for _ in range(milestone["pulls"])]
                    inserted = conn.execute(
                        "INSERT OR IGNORE INTO mythic_weekly_rewards"
                        "(user_id,week,tier,raid_id,key_level,items,granted_at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            user_id,
                            raid["week"],
                            milestone["id"],
                            raid_id,
                            raid["key_level"],
                            json.dumps([pig["id"] for pig in picks], ensure_ascii=False),
                            stamp,
                        ),
                    ).rowcount
                    if not inserted:
                        continue
                    for pig in picks:
                        _give(conn, user_id, pig["id"], stamp)
                    weekly.append(
                        {
                            **milestone,
                            "items": [
                                {k: v for k, v in pig.items() if k != "battle"} for pig in picks
                            ],
                        }
                    )
                rewards.append(
                    {
                        "user": member["user"],
                        "pig": {k: v for k, v in entered.items() if k != "battle"},
                        "draw": draws,
                        "weekly": weekly,
                    }
                )
        row = conn.execute("SELECT * FROM mythic_raids WHERE id=?", (raid_id,)).fetchone()
        return {
            "raid": self._mythic_view(conn, row),
            "report": report,
            "next_key": {"dungeon": dungeon(next_dungeon), "level": next_level},
            "key_floor": key_floor,
            "rewards": rewards,
        }

    async def join_mythic(
        self,
        app_id: str,
        group_id: str,
        user_id: int,
        pig_id: str,
        *,
        level_cap: int = 20,
        now: datetime | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.timestamp()

        def join(conn):
            conn.execute("BEGIN IMMEDIATE")
            expired = self._mythic_expire(conn, app_id, group_id, stamp)
            raid = self._group_mythic(conn, app_id, group_id)
            if not raid:
                note = "上一支大秘境队伍已超时取消。" if expired else ""
                raise PiggyError(f"{note}本群没有正在组队的大秘境。")
            if conn.execute(
                "SELECT 1 FROM mythic_members WHERE raid_id=? AND user_id=?",
                (raid["id"], user_id),
            ).fetchone():
                raise PiggyError("你已经在这支大秘境队伍里了。")
            busy = conn.execute(
                "SELECT 1 FROM mythic_members m JOIN mythic_raids r ON r.id=m.raid_id "
                "WHERE m.user_id=? AND r.status='forming'",
                (user_id,),
            ).fetchone()
            if busy:
                raise PiggyError("你已经在另一支大秘境队伍里了。")
            regular = conn.execute(
                "SELECT 1 FROM raid_members m JOIN raids r ON r.id=m.raid_id "
                "WHERE m.user_id=? AND r.status IN ('forming','waiting')",
                (user_id,),
            ).fetchone()
            despair = conn.execute(
                "SELECT 1 FROM despair_members m JOIN despair_raids r ON r.id=m.raid_id "
                "WHERE m.user_id=? AND r.status='forming'",
                (user_id,),
            ).fetchone()
            if regular or despair:
                raise PiggyError("你已经在其他副本队伍里，不能同时参加大秘境。")
            self._mythic_require_unlocked(conn, user_id)
            self._mythic_pig(conn, user_id, pig_id, level_cap)
            count = conn.execute(
                "SELECT count(*) FROM mythic_members WHERE raid_id=?", (raid["id"],)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO mythic_members VALUES(?,?,?,?,?)",
                (raid["id"], user_id, pig_id, count, stamp),
            )
            if count + 1 < PARTY_SIZE:
                row = conn.execute("SELECT * FROM mythic_raids WHERE id=?", (raid["id"],)).fetchone()
                return {"raid": self._mythic_view(conn, row), "started": False, "now": stamp}
            battle = self._mythic_fight(conn, raid["id"], stamp, level_cap)
            return {"raid": battle["raid"], "started": True, "battle": battle, "now": stamp}

        return await self.run(join)

    async def leave_mythic(
        self, app_id: str, group_id: str, user_id: int, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.timestamp()

        def leave(conn):
            conn.execute("BEGIN IMMEDIATE")
            self._mythic_expire(conn, app_id, group_id, stamp)
            raid = self._group_mythic(conn, app_id, group_id)
            if not raid or not conn.execute(
                "SELECT 1 FROM mythic_members WHERE raid_id=? AND user_id=?",
                (raid["id"], user_id),
            ).fetchone():
                raise PiggyError("你不在本群正在组队的大秘境队伍里。")
            cancelled = raid["leader"] == user_id
            if cancelled:
                conn.execute(
                    "UPDATE mythic_raids SET status='cancelled',updated_at=? WHERE id=?",
                    (stamp, raid["id"]),
                )
            else:
                conn.execute(
                    "DELETE FROM mythic_members WHERE raid_id=? AND user_id=?",
                    (raid["id"], user_id),
                )
                remaining = conn.execute(
                    "SELECT user_id FROM mythic_members WHERE raid_id=? ORDER BY seat", (raid["id"],)
                ).fetchall()
                for seat, row in enumerate(remaining):
                    conn.execute(
                        "UPDATE mythic_members SET seat=? WHERE raid_id=? AND user_id=?",
                        (seat, raid["id"], row["user_id"]),
                    )
            row = conn.execute("SELECT * FROM mythic_raids WHERE id=?", (raid["id"],)).fetchone()
            return {"raid": self._mythic_view(conn, row), "cancelled": cancelled}

        return await self.run(leave)

    async def mythic_rankings(
        self, app_id: str, group_id: str, now: datetime | None = None
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        week = _week_key(now)

        def read(conn):
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM mythic_records WHERE app_id=? AND group_id=? AND week=? "
                    "AND completed=1",
                    (app_id, group_id, week),
                )
            ]

            def quality(row):
                return (
                    row["key_level"],
                    row["timed"],
                    row["upgrade"],
                    -row["rounds"],
                    -row["deaths"],
                    -row["completed_at"],
                )

            def roster(raid_id):
                result = []
                for member in conn.execute(
                    "SELECT m.seat,u.*,p.name AS pig_name FROM mythic_members m "
                    "JOIN users u ON u.id=m.user_id JOIN pigs p ON p.id=m.pig_id "
                    "WHERE m.raid_id=? ORDER BY m.seat",
                    (raid_id,),
                ):
                    result.append(
                        {
                            "user": dict(member),
                            "name": player_name(member),
                            "pig": member["pig_name"],
                        }
                    )
                return result

            weekly_best = {}
            dungeon_best = {}
            for row in rows:
                if row["team_key"] not in weekly_best or quality(row) > quality(
                    weekly_best[row["team_key"]]
                ):
                    weekly_best[row["team_key"]] = row
                key = (row["team_key"], row["dungeon"])
                if key not in dungeon_best or quality(row) > quality(dungeon_best[key]):
                    dungeon_best[key] = row
            totals = {}
            for (team_key, _), row in dungeon_best.items():
                totals.setdefault(team_key, {"score": 0, "dungeons": 0, "row": row})
                totals[team_key]["score"] += row["score"]
                totals[team_key]["dungeons"] += 1
                if quality(row) > quality(totals[team_key]["row"]):
                    totals[team_key]["row"] = row
            weekly = sorted(weekly_best.values(), key=quality, reverse=True)[:10]
            total = sorted(
                totals.values(),
                key=lambda item: (item["score"], item["dungeons"], quality(item["row"])),
                reverse=True,
            )[:10]
            return {
                "week": week,
                "weekly": [{**row, "team": roster(row["raid_id"])} for row in weekly],
                "total": [
                    {**item, "team": roster(item["row"]["raid_id"])} for item in total
                ],
            }

        return await self.run(read)

    async def test_mythic(
        self,
        user: dict,
        *,
        dungeon_key: int = 1,
        key_level: int = 2,
        level: int = 10,
        seed: int = 7,
        add_chance: float = 30.0,
    ) -> dict:
        """Administrator preview with no persistence or rewards."""

        def read(conn):
            pools = {role: [] for role in ROLES}
            for row in conn.execute("SELECT * FROM pigs WHERE enabled=1 ORDER BY id"):
                pig = dict(row)
                role = entry_for(pig.get("battle")).get("role", "输出")
                if role in pools:
                    pools[role].append(pig)
            missing = [role for role, pigs in pools.items() if not pigs]
            if missing:
                raise PiggyError(f"猪库缺少可测试职业：{'、'.join(missing)}")
            rng = random.Random(seed)
            members = []
            for seat, role in enumerate(ROLES):
                mythic = [pig for pig in pools[role] if rarity_for(pig["id"]) == "神话"]
                pig = dict(rng.choice(mythic or pools[role]))
                members.append(
                    {
                        "seat": seat,
                        "user": {**user, "id": user["id"] * 10 + seat + 1, "nickname": f"测试{role}"},
                        "pig": pig,
                        "count": level,
                        "label": f"测试{role}的{pig['name']}",
                        "personality": None,
                    }
                )
            catalog = {
                row["id"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM pigs WHERE enabled=1",
                )
            }
            return simulate_mythic(
                members,
                catalog,
                dungeon_key,
                key_level,
                seed,
                level_cap=max(level, 20),
                add_chance=add_chance,
            )

        return await self.run(read)

    async def grant_pig(
        self, user_id: int, pig_id: str, count: int, *, level_cap: int = 20
    ) -> dict:
        stamp = time.time()

        def grant(conn):
            conn.execute("BEGIN IMMEDIATE")
            pig = dict(conn.execute("SELECT * FROM pigs WHERE id=?", (pig_id,)).fetchone())
            if not pig["enabled"]:
                raise PiggyError(f"「{pig['name']}」已经下架，不能发放。")
            before = _owned(conn, user_id, pig_id)
            conn.execute(
                """
                INSERT INTO collections VALUES(?,?,?,?,?) ON CONFLICT(user_id,pig_id)
                DO UPDATE SET count=count+excluded.count,last_at=excluded.last_at
                """,
                (user_id, pig_id, count, stamp, stamp),
            )
            return _level_change(pig, before, before + count, level_cap)

        return await self.run(grant)

    async def grant_draws(
        self, app_id: str, group_id: str, user_id: int, count: int, now: datetime | None = None
    ) -> int:
        day = (now or datetime.now(timezone.utc)).astimezone(EAST_ASIA).date().isoformat()

        def grant(conn):
            conn.execute(
                """
                INSERT INTO draw_bonus VALUES(?,?,?,?,?) ON CONFLICT(app_id,group_id,user_id,day)
                DO UPDATE SET count=count+excluded.count
                """,
                (app_id, group_id, user_id, day, count),
            )
            return self._bonus_left(conn, app_id, group_id, user_id, day)

        return await self.run(grant)

    async def raid_sample(self, dungeon_key, stage: int, level: int, seed: int) -> dict:
        """A throwaway fight with random catalog pigs, shaped like a raid stage for the replay.

        Nothing is written: no raid, no rewards, no losses.
        """
        info = dungeon(dungeon_key)
        slot_id = info["bosses"][stage - 1]

        def sample(conn):
            enabled = [
                dict(r) for r in conn.execute("SELECT * FROM pigs WHERE enabled=1 ORDER BY id")
            ]
            boss = conn.execute(
                "SELECT * FROM pigs WHERE id=? AND enabled=1", (slot_id,)
            ).fetchone()
            return enabled, dict(boss) if boss else None

        enabled, boss_pig = await self.run(sample)
        if len(enabled) < PARTY_SIZE:
            raise PiggyError(f"猪库启用的小猪不足 {PARTY_SIZE} 只，无法模拟副本。")
        rng = random.Random(seed)
        boss_pig = boss_pig or rng.choice(enabled)
        heroes = []
        for seat, pig in enumerate(rng.sample(enabled, PARTY_SIZE)):
            hero = fighter(pig, entry_for(pig["battle"]), level, f"{seat + 1}号{pig['name']}")
            hero["seat"] = seat
            heroes.append(hero)
        boss = boss_fighter(
            boss_pig,
            entry_for(boss_pig["battle"]),
            stage,
            slot_id,
            boss_pig["name"],
            party_levels=[level] * PARTY_SIZE,
        )
        result = await asyncio.to_thread(simulate_raid, heroes, boss, slot_id, new_config(), seed)
        return {
            "raid": {"dungeon": info},
            "stage": stage,
            "result": result,
            "fighters": [
                {
                    "seat": h["seat"],
                    "dealt": h["dealt"],
                    "healed": h.get("healed", 0),
                    "taken": h.get("taken", 0),
                }
                for h in result["heroes"]
                if h["seat"] is not None
            ],
            "ally": None,
            "boss": {"label": boss_pig["name"]},
            "events": [],
            "won": result["won"],
        }

    async def duel_rankings(
        self, app_id: str, group_id: str, user_id: int, min_games: int = 3, limit: int = 10
    ) -> dict:
        def read(conn):
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    WITH games AS (
                        SELECT a_user AS uid, winner FROM battle_records
                        UNION ALL SELECT b_user, winner FROM battle_records
                    )
                    SELECT u.*, count(*) AS games, sum(g.winner = u.id) AS wins
                    FROM group_players gp JOIN users u ON u.id = gp.user_id
                    JOIN games g ON g.uid = u.id
                    WHERE gp.app_id=? AND gp.group_id=? GROUP BY u.id
                    """,
                    (app_id, group_id),
                )
            ]
            for row in rows:
                row["losses"] = row["games"] - row["wins"]
                row["rate"] = row["wins"] / row["games"]
            ranked = sorted(
                (row for row in rows if row["games"] >= min_games),
                key=lambda row: (-row["rate"], -row["wins"], row["id"]),
            )
            for index, row in enumerate(ranked, 1):
                row["rank"] = index
            mine = next((row for row in rows if row["id"] == user_id), None)
            return {"top": ranked[:limit], "me": mine, "min_games": min_games}

        return await self.run(read)

    @staticmethod
    def _battle_view(conn, row, user_id: int) -> dict:
        record = dict(row)
        mine_a = record["a_user"] == user_id
        pigs = {
            pig["id"]: dict(pig)
            for pig in conn.execute(
                "SELECT id,name,asset FROM pigs WHERE id IN (?,?)",
                (record["a_pig"], record["b_pig"]),
            )
        }
        names = {pig_id: pig["name"] for pig_id, pig in pigs.items()}
        other = conn.execute(
            "SELECT * FROM users WHERE id=?", (record["b_user"] if mine_a else record["a_user"],)
        ).fetchone()
        won = record["winner"] == user_id
        loser_pig = record["b_pig"] if record["winner"] == record["a_user"] else record["a_pig"]
        return {
            **record,
            "won": won,
            "opponent": dict(other),
            "my_pig": names.get(record["a_pig" if mine_a else "b_pig"], "?"),
            "my_level": record["a_level" if mine_a else "b_level"],
            "their_pig": names.get(record["b_pig" if mine_a else "a_pig"], "?"),
            "their_level": record["b_level" if mine_a else "a_level"],
            "prize": names.get(loser_pig, "?"),
            "a_pig_name": names.get(record["a_pig"], "?"),
            "b_pig_name": names.get(record["b_pig"], "?"),
            "a_asset": pigs[record["a_pig"]]["asset"] if record["a_pig"] in pigs else "",
            "b_asset": pigs[record["b_pig"]]["asset"] if record["b_pig"] in pigs else "",
            "my_asset": (pigs.get(record["a_pig" if mine_a else "b_pig"]) or {}).get("asset", ""),
            "their_asset": (pigs.get(record["b_pig" if mine_a else "a_pig"]) or {}).get(
                "asset", ""
            ),
            "summary": json.loads(record["summary"]) if record.get("summary") else None,
        }

    async def duel_history(self, user_id: int, page: int = 1, size: int = 10) -> dict:
        def read(conn):
            total, wins = conn.execute(
                "SELECT count(*), coalesce(sum(winner=?),0) FROM battle_records "
                "WHERE a_user=? OR b_user=?",
                (user_id, user_id, user_id),
            ).fetchone()
            pages = max(1, -(-total // size))
            if not 1 <= page <= pages:
                raise PiggyError(f"页码超出范围，请输入 1–{pages}。")
            rows = conn.execute(
                "SELECT * FROM battle_records WHERE a_user=? OR b_user=? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, user_id, size, (page - 1) * size),
            ).fetchall()
            return {
                "records": [self._battle_view(conn, row, user_id) for row in rows],
                "total": total,
                "wins": wins,
                "page": page,
                "pages": pages,
            }

        return await self.run(read)

    async def duel_record(self, user_id: int, record_id: int) -> dict:
        def read(conn):
            row = conn.execute(
                "SELECT * FROM battle_records WHERE id=? AND (a_user=? OR b_user=?)",
                (record_id, user_id, user_id),
            ).fetchone()
            if not row:
                raise PiggyError(f"没有找到你参与的第 {record_id} 场斗猪。")
            view = self._battle_view(conn, row, user_id)
            view["log"] = json.loads(row["log"])
            view["players"] = {
                uid: dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
                for uid in (row["a_user"], row["b_user"])
            }
            return view

        return await self.run(read)

    async def forget_bot(self, app_id: str, open_ids) -> None:
        """Remove bot accounts that an earlier version mistook for players."""
        open_ids = [str(i) for i in open_ids if i]
        if not open_ids:
            return
        stamp = time.time()

        def forget(conn):
            marks = ",".join("?" * len(open_ids))
            ids = [
                row[0]
                for row in conn.execute(
                    f"SELECT id FROM users WHERE app_id=? AND open_id IN ({marks})",
                    (app_id, *open_ids),
                )
            ]
            if not ids:
                return
            marks = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM group_players WHERE user_id IN ({marks})", ids)
            conn.execute(
                f"UPDATE requests SET status='cancelled',resolved_at=? "
                f"WHERE status='pending' AND to_user IN ({marks})",
                (stamp, *ids),
            )

        await self.run(forget)

    async def cache_get(self, namespace: str, digest: str):
        def read(conn):
            row = conn.execute(
                "SELECT * FROM image_cache WHERE namespace=? AND digest=?",
                (namespace, digest),
            ).fetchone()
            return dict(row) if row else None

        return await self.run(read)

    async def cache_put(self, namespace: str, digest: str, key: str, url: str):
        await self.run(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO image_cache VALUES(?,?,?,?,?)",
                (namespace, digest, key, url, time.time()),
            )
        )

    async def delivery(self, key: str) -> dict:
        def begin(conn):
            conn.execute("INSERT OR IGNORE INTO deliveries VALUES(?,100,0,?)", (key, time.time()))
            return dict(
                conn.execute("SELECT * FROM deliveries WHERE message_key=?", (key,)).fetchone()
            )

        return await self.run(begin)

    async def delivery_update(self, key: str, sequence: int, done: bool):
        await self.run(
            lambda c: c.execute(
                "UPDATE deliveries SET sequence=?,done=?,updated_at=? WHERE message_key=?",
                (sequence, int(done), time.time(), key),
            )
        )

    async def prune_cache(self):
        def prune(conn):
            conn.execute("DELETE FROM deliveries WHERE updated_at<?", (time.time() - 30 * 86400,))
            conn.execute(
                "DELETE FROM image_cache WHERE uploaded_at<? OR (namespace LIKE '%:v2:temp/%' AND uploaded_at<?)",
                (time.time() - 366 * 86400, time.time() - 2 * 86400),
            )

        await self.run(prune)

    async def backup(self, keep: int) -> Path:
        def backup():
            target = self.root / "backups"
            target.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            output = target / f"piggy-{stamp}.zip"
            temp_zip = output.with_suffix(".tmp")
            try:
                with tempfile.TemporaryDirectory(dir=target) as tmp:
                    db_copy = Path(tmp) / "piggy.sqlite3"
                    with (
                        closing(sqlite3.connect(self.path)) as src,
                        closing(sqlite3.connect(db_copy)) as dst,
                    ):
                        src.backup(dst)
                        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise PiggyError("备份完整性检查失败。")
                        dst.row_factory = sqlite3.Row
                        pigs = [
                            dict(p)
                            for p in dst.execute("SELECT * FROM pigs ORDER BY sort_order,id")
                        ]
                    # Export the accepted catalog from the same DB snapshot. An administrator
                    # may be editing the working JSON while this backup runs.
                    manifest = [
                        {
                            **{k: v for k, v in p.items() if k != "battle"},
                            "enabled": bool(p["enabled"]),
                            "image": f"images/{p['asset']}",
                        }
                        for p in pigs
                    ]
                    battle = {
                        "version": 1,
                        "pigs": {p["id"]: json.loads(p["battle"]) for p in pigs if p.get("battle")},
                    }
                    with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                        archive.write(db_copy, "piggy.sqlite3")
                        archive.writestr(
                            "catalog/pigs.json", json.dumps(manifest, ensure_ascii=False, indent=2)
                        )
                        archive.writestr(
                            "catalog/battle.json", json.dumps(battle, ensure_ascii=False, indent=2)
                        )
                        for name in sorted({p["asset"] for p in pigs}):
                            archive.write(self.root / "assets" / name, f"catalog/images/{name}")
                        for path in sorted((self.root / "assets").iterdir()):
                            if (
                                path.is_file()
                                and not path.is_symlink()
                                and not path.name.endswith(".tmp")
                            ):
                                archive.write(path, f"assets/{path.name}")
                temp_zip.replace(output)
                for old in sorted(target.glob("piggy-*.zip"))[:-keep]:
                    old.unlink()
                return output
            finally:
                temp_zip.unlink(missing_ok=True)

        task = asyncio.create_task(asyncio.to_thread(backup))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Threaded SQLite backup cannot be interrupted safely. Finish it before unload.
            await task
            raise
