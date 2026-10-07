import asyncio
import json
import secrets
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .battle import SKILL_SLOTS, entry_for, fighter, level_for, simulate
from .config import PiggyError

EAST_ASIA = timezone(timedelta(hours=8))
REQUEST_LABELS = {"duel": "斗猪", "trade": "交换"}


def player_name(user) -> str:
    return user["alias"] or user["nickname"] or f"玩家 {user['id']:04d}"


def _owned(conn, user_id: int, pig_id: str) -> int:
    row = conn.execute(
        "SELECT count FROM collections WHERE user_id=? AND pig_id=?", (user_id, pig_id)
    ).fetchone()
    return row[0] if row else 0


def _transfer(conn, source: int, target: int, pig_id: str, now: float):
    count = _owned(conn, source, pig_id)
    if count < 1:
        raise PiggyError("小猪已经不在原主人的猪圈里了。")
    if count == 1:
        conn.execute("DELETE FROM collections WHERE user_id=? AND pig_id=?", (source, pig_id))
    else:
        conn.execute(
            "UPDATE collections SET count=count-1 WHERE user_id=? AND pig_id=?", (source, pig_id)
        )
    conn.execute(
        """
        INSERT INTO collections VALUES(?,?,1,?,?) ON CONFLICT(user_id,pig_id)
        DO UPDATE SET count=count+1,last_at=excluded.last_at
        """,
        (target, pig_id, now, now),
    )


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
            if version not in (0, 1, 2):
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
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(pigs)")}
            if "battle" not in columns:
                conn.execute("ALTER TABLE pigs ADD COLUMN battle TEXT NOT NULL DEFAULT ''")
            conn.execute("PRAGMA user_version=2")
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
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("The draw clock must be timezone-aware")
        day = now.astimezone(EAST_ASIA).date().isoformat()

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

            existing = conn.execute(
                "SELECT * FROM draw_records WHERE user_id=? AND day=?", (user_id, day)
            ).fetchone()
            created = existing is None
            if created:
                available = conn.execute(
                    "SELECT * FROM pigs WHERE enabled=1 ORDER BY id"
                ).fetchall()
                if not available:
                    raise PiggyError("猪库没有启用的小猪，请联系管理员检查。")
                owned_ids = {
                    row[0]
                    for row in conn.execute(
                        "SELECT pig_id FROM collections WHERE user_id=?", (user_id,)
                    )
                }
                owned = [p for p in available if p["id"] in owned_ids]
                unseen = [p for p in available if p["id"] not in owned_ids]
                if not unseen:
                    pool = owned
                elif not owned or (duplicate_pity and repeat_streak() >= duplicate_pity):
                    pool = unseen
                else:
                    # Integer comparison preserves the natural rate without rounding.
                    threshold = min(len(owned) * 100, duplicate_rate_cap * len(available))
                    pool = owned if secrets.randbelow(100 * len(available)) < threshold else unseen
                pig = dict(secrets.choice(pool))
                pig.pop("battle", None)
                timestamp = now.timestamp()
                conn.execute(
                    """
                    INSERT INTO draw_records(user_id,day,drawn_at,pig_id,source_group,source_event,snapshot)
                    VALUES(?,?,?,?,?,?,?)
                """,
                    (
                        user_id,
                        day,
                        timestamp,
                        pig["id"],
                        group_id,
                        event_id,
                        json.dumps(pig, ensure_ascii=False),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO collections VALUES(?,?,1,?,?) ON CONFLICT(user_id,pig_id)
                    DO UPDATE SET count=count+1,last_at=excluded.last_at
                """,
                    (user_id, pig["id"], timestamp, timestamp),
                )
            else:
                pig = json.loads(existing["snapshot"])
            count = conn.execute(
                "SELECT count FROM collections WHERE user_id=? AND pig_id=?",
                (user_id, pig["id"]),
            ).fetchone()[0]
            return {
                "pig": pig,
                "day": day,
                "created": created,
                "count": count,
                "new_species": created and count == 1,
                "repeat_streak": repeat_streak() if duplicate_pity else 0,
            }

        return await self.run(draw)

    async def collection(self, user_id: int) -> dict:
        def read(conn):
            conn.execute("BEGIN")
            rows = conn.execute(
                """
                SELECT p.*,coalesce(c.count,0) AS count,c.first_at,c.last_at FROM pigs p
                LEFT JOIN collections c ON c.pig_id=p.id AND c.user_id=?
                WHERE p.enabled=1 OR c.count>0 ORDER BY p.enabled DESC,p.sort_order,p.id
            """,
                (user_id,),
            ).fetchall()
            entries = [dict(r) for r in rows]
            return {
                "entries": entries,
                "active_total": sum(p["enabled"] for p in entries),
                "unlocked": sum(bool(p["enabled"] and p["count"]) for p in entries),
                "total": sum(p["count"] for p in entries),
                "species": sum(p["count"] > 0 for p in entries),
            }

        return await self.run(read)

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

        def read(conn):
            rows = conn.execute(
                """
                SELECT u.* FROM group_players g JOIN users u ON u.id=g.user_id
                WHERE g.app_id=? AND g.group_id=? AND (u.alias=? OR u.nickname=?)
                """,
                (app_id, group_id, name, name),
            ).fetchall()
            if not rows:
                raise PiggyError(f"本群没有找到叫「{name}」的玩家，请直接 @ 对方。")
            if len(rows) > 1:
                raise PiggyError(f"本群有多位玩家叫「{name}」，请直接 @ 对方。")
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
                b_pig,b_level,winner,seed,log) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
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
                ),
            )
            close("accepted")
            return {
                "request": view,
                "accepted": True,
                "users": users,
                "fighters": fighters,
                "result": result,
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
        names = {
            pig["id"]: pig["name"]
            for pig in conn.execute(
                "SELECT id,name FROM pigs WHERE id IN (?,?)", (record["a_pig"], record["b_pig"])
            )
        }
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
