import asyncio
import contextlib
import re
import secrets
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import (
    QQOfficialMessageEvent,
)
from botpy.message import GroupMessage

from .core.avatars import Avatars
from .core.battle import (
    DUMMY_DEFAULT_ROUNDS,
    DUMMY_MAX_ROUNDS,
    entry_for,
    fighter,
    level_for,
    simulate_dummy,
)
from .core.catalog import (
    initialize_battle,
    initialize_catalog,
    read_catalog,
    sync_bundled_catalog,
)
from .core.config import PiggyError, Settings, migrate_host_config
from .core.database import Database, player_name
from .core.delivery import Message, QQError, QQTransport, Sender, message_key
from .core.despair_views import (
    despair_battle_message,
    despair_lobby_message,
    despair_status_message,
)
from .core.diagnostics import exception_detail
from .core.mythic_views import (
    mythic_battle_message,
    mythic_key_action_message,
    mythic_key_message,
    mythic_lobby_message,
    mythic_rankings_message,
    mythic_status_message,
)
from .core.raid import DUNGEONS, dungeon
from .core.rendering import clean_cards
from .core.storage import ImagePublisher, UploadError
from .core.views import (
    battle_message,
    cancelled_message,
    collection_message,
    declined_message,
    draw_all_message,
    duel_history_message,
    duel_ranking_message,
    duel_replay_message,
    dummy_test_message,
    guide_message,
    raid_battle_message,
    raid_detail_message,
    raid_list_message,
    raid_lobby_message,
    ranking_message,
    request_message,
    requests_message,
    shop_exchange_message,
    shop_message,
    stats_message,
    today_message,
    trade_message,
    wild_battle_message,
    wild_message,
)

MAX_INFLIGHT = 12
ANNOUNCE_INTERVAL = 600
ANNOUNCE_ACTIVE_DAYS = 7
# Bound at import so a patched asyncio.sleep can never turn the loop into a busy spin.
_sleep = asyncio.sleep
REQUEST_TIMEOUT = 240
MENTION_TAG = re.compile(r"<@!?[^>]*>|<qqbot-at-user[^>]*>")
MENTION_ID = re.compile(
    r"<@!?([^>\s]+)>"
    r"|<qqbot-at-user\b[^>]*?\b(?:member_openid|user_openid|openid|id)=[\"']([^\"']+)[\"'][^>]*>"
)
BATTLE_COMMANDS = {
    "guide",
    "stats",
    "dummy_test",
    "duel",
    "duel_accept",
    "duel_decline",
    "trade",
    "trade_accept",
    "trade_decline",
    "cancel",
    "requests",
    "duel_ranking",
    "duel_history",
    "duel_replay",
    "shop",
    "shop_exchange",
    "wild",
    "wild_challenge",
    "raid",
    "raid_open",
    "raid_join",
    "raid_leave",
    "raid_continue",
    "raid_retreat",
    "despair",
    "despair_open",
    "despair_join",
    "despair_leave",
    "despair_test",
    "mythic",
    "mythic_open",
    "mythic_join",
    "mythic_leave",
    "mythic_key",
    "mythic_key_claim",
    "mythic_key_lower",
    "mythic_ranking",
    "mythic_test",
    "grant_pig",
    "grant_draw",
}
USAGE = {
    "duel": "用法：斗猪 @对方 你的小猪",
    "trade": "用法：小猪交换 @对方 你的小猪 对方的小猪",
    "grant_pig": "用法：发小猪 @对方 小猪名字 数量",
    "grant_draw": "用法：发再抽 @对方 数量",
}
GRANT_MAX = 999
RAID_NAMES = " / ".join(f"{item['key']} {item['name']}" for item in DUNGEONS)
RAID_USAGE = f"用法：开启副本 编号 你的小猪，例如：开启副本 1 猪人（{RAID_NAMES}）"
NAME_USAGE = {
    "duel": "斗猪 #编号 你的小猪",
    "trade": "小猪交换 #编号 你的小猪 对方的小猪",
    "grant_pig": "发小猪 #编号 小猪名字 数量",
    "grant_draw": "发再抽 #编号 数量",
}
# Words needed after the command when the target is named instead of mentioned.
NAME_MIN_WORDS = {"duel": 2, "trade": 3, "grant_pig": 3, "grant_draw": 2}


def command_words(event, names: tuple[str, ...]) -> tuple[str, ...]:
    """Parse arguments ourselves: QQ mention markup may sit anywhere in the text."""
    words = MENTION_TAG.sub(" ", message_text(event)).split()
    for index, word in enumerate(words):
        if any(word.endswith(name) for name in names):
            return tuple(words[index + 1 :])
    return tuple(words[1:])


def message_text(event) -> str:
    getter = getattr(event, "get_message_str", None)
    return (getter() if callable(getter) else getattr(event, "message_str", "")) or ""


def mention_debug(event) -> dict:
    """Shape of what QQ delivered, for diagnosing unrecognised mentions; ids are omitted."""
    raw = event.message_obj.raw_message

    def shape(value, depth=0):
        if depth > 3:
            return "..."
        if isinstance(value, dict):
            return {k: shape(v, depth + 1) for k, v in value.items() if v not in (None, "")}
        if isinstance(value, list):
            return [shape(v, depth + 1) for v in value[:5]]
        if hasattr(value, "__dict__"):
            return shape(vars(value), depth)
        return type(value).__name__

    data = getattr(raw, "raw_data", None)
    return {
        "content": getattr(raw, "content", None),
        "mentions": shape(getattr(raw, "mentions", None) or []),
        "raw_mentions": shape(data.get("mentions")) if isinstance(data, dict) else None,
        "msg_elements": shape(getattr(raw, "msg_elements", None) or []),
    }


def mention_targets(event) -> tuple[list[dict], set[str]]:
    """Return mentioned players in text order, plus every id known to be a bot."""
    raw = event.message_obj.raw_message
    entries = []

    def add(get, source):
        member, plain = get("member_openid"), get("id")
        ids = {str(i) for i in (member, plain) if i}
        if ids:
            entries.append(
                {
                    "ids": ids,
                    "open_id": str(member or plain),
                    "name": get("username") or get("nickname") or "",
                    "bot": bool(get("is_you") or get("bot")),
                    "source": f"{source}.{'member_openid' if member else 'id'}",
                }
            )

    for item in getattr(raw, "mentions", None) or []:
        add(lambda key, item=item: getattr(item, key, None), "mentions")
    data = getattr(raw, "raw_data", None)
    for item in (data.get("mentions") if isinstance(data, dict) else None) or []:
        if isinstance(item, dict):
            add(item.get, "raw_data")
    bots = {i for entry in entries if entry["bot"] for i in entry["ids"]}
    get_self = getattr(event, "get_self_id", None)
    self_id = str(get_self() or "") if callable(get_self) else ""
    if self_id and self_id not in {"qq_official", "unknown_selfid"}:
        bots.add(self_id)
    ordered = []
    for match in MENTION_ID.finditer(message_text(event)):
        tag_id = match.group(1) or match.group(2)
        known = next((e for e in entries if tag_id in e["ids"]), None)
        if known:
            ordered.append({**known, "source": f"text+{known['source']}"})
        else:
            ordered.append(
                {"ids": {tag_id}, "open_id": tag_id, "name": "", "bot": False, "source": "text"}
            )
    ordered += entries
    sender = str(event.get_sender_id())
    targets, seen = [], set()
    for entry in ordered:
        if entry["bot"] or entry["ids"] & bots or sender in entry["ids"]:
            continue
        if entry["open_id"] in seen:
            continue
        seen.add(entry["open_id"])
        targets.append(entry)
    return targets, bots


@register("astrbot_plugin_piggy", "yun474", "QQ 官方机器人每日小猪收集与副本", "1.16.0")
class PiggyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        if migrate_host_config(config):
            config.save_config()
        self.settings = Settings.from_dict(config)
        self.root = StarTools.get_data_dir("astrbot_plugin_piggy")
        self.db = Database(self.root)
        self.publisher = ImagePublisher(self.settings, self.db)
        self.transport = QQTransport(self.settings.request_timeout)
        self.sender = Sender(self.settings, self.db, self.publisher, self.transport, logger=logger)
        self.avatars = Avatars()
        self.ready = False
        self.init_lock = asyncio.Lock()
        self.maintenance_lock = asyncio.Lock()
        self.limit = asyncio.Semaphore(3)
        self.inflight = {}
        self.active_users = set()
        self.busy_notice = None
        self.stopping = False
        self.backup_task = None
        self.cleanup_task = None
        self.announce_task = None
        # The QQ client of each bot app, learnt from the latest message it handled.
        self.bots = {}

    async def initialize(self):
        async with self.init_lock:
            if self.ready:
                return
            await self.db.initialize()
            await asyncio.to_thread(
                initialize_battle, self.root, Path(__file__).parent / "resources"
            )
            if not await self.db.has_catalog():
                await asyncio.to_thread(
                    initialize_catalog, self.root, Path(__file__).parent / "resources"
                )
            synced = await asyncio.to_thread(
                sync_bundled_catalog, self.root, Path(__file__).parent / "resources"
            )
            if synced["pigs"] or synced["battle"]:
                logger.info(
                    "[piggy] Catalog sync added %s pigs and refreshed %s battle entries.",
                    synced["pigs"],
                    synced["battle"],
                )
            try:
                pigs = await asyncio.to_thread(read_catalog, self.root)
                await self.db.catalog(pigs)
            except PiggyError as exc:
                if not await self.db.has_catalog():
                    raise
                logger.error("[piggy] Catalog reload rejected, keeping previous catalog: %s", exc)
            self.ready = True
            self.backup_task = asyncio.create_task(self._backups())
            self.cleanup_task = asyncio.create_task(self._cleanup())
            if self.settings.wild_announce:
                self.announce_task = asyncio.create_task(self._announce_wild())

    async def _backups(self):
        while True:
            try:
                async with self.maintenance_lock:
                    await self.db.backup(self.settings.backup_keep)
            except Exception as exc:
                logger.error(
                    "[piggy] Scheduled backup failed (%s); original database retained.",
                    type(exc).__name__,
                )
            await asyncio.sleep(86400)

    async def _cleanup(self):
        while True:
            try:
                await self.db.prune_cache()
                await asyncio.to_thread(clean_cards, self.root)
            except Exception as exc:
                logger.warning(
                    "[piggy] Cache cleanup failed: %s", exception_detail(exc, self.settings)
                )
            await asyncio.sleep(3600)

    async def _announce_wild(self):
        while True:
            try:
                await self.announce_wild_once()
            except Exception as exc:
                logger.warning(
                    "[piggy] Wild pig announcement failed: %s",
                    exception_detail(exc, self.settings),
                )
            # Wake shortly after midnight (UTC+8), and at least every few minutes for
            # groups whose bot only becomes known after a restart.
            now = time.time() + 8 * 3600
            await _sleep(min(ANNOUNCE_INTERVAL, 86400 - now % 86400 + 5))

    async def announce_wild_once(self):
        """Spawn today's wild pig in recently active groups and post it without @."""
        if not self.settings.wild_announce:
            return
        since = time.time() - ANNOUNCE_ACTIVE_DAYS * 86400
        for app_id, bot in list(self.bots.items()):
            for group in await self.db.active_groups(app_id, since):
                if self.stopping:
                    return
                wild = await self.db.wild_pig(app_id, group, level_max=self.settings.wild_level_max)
                if not wild["spawned"]:
                    continue
                target = SimpleNamespace(bot=bot, get_group_id=lambda group=group: group)
                try:
                    message = await asyncio.to_thread(
                        wild_message, self.settings, self.root, {"open_id": ""}, wild
                    )
                    await self.sender.announce(target, message)
                except Exception as exc:
                    logger.warning(
                        "[piggy] Could not announce the wild pig in a group "
                        "(the group may not allow proactive bot messages): %s",
                        exception_detail(exc, self.settings),
                    )

    async def _handle(self, event: AstrMessageEvent, command: str, args: tuple = ()):
        if (
            not isinstance(event, QQOfficialMessageEvent)
            or not isinstance(event.message_obj.raw_message, GroupMessage)
            or not event.get_group_id()
        ):
            await event.send(event.plain_result("今日小猪收集版目前支持 QQ 官方机器人群聊。"))
            return
        event.stop_event()
        if self.stopping:
            return
        token = event.bot.api._http._token
        app_id = str(token.app_id or "")
        if not app_id or not event.message_obj.message_id:
            await event.send(event.plain_result("未取得官方消息标识，暂时无法处理收藏。"))
            return
        self.bots[app_id] = event.bot
        key = message_key(event, app_id)
        if key in self.inflight:
            await asyncio.shield(self.inflight[key])
            return
        owner = (app_id, event.get_sender_id())
        if owner in self.active_users or len(self.inflight) >= MAX_INFLIGHT:
            # A burst must not create another unbounded queue of busy replies.
            if self.busy_notice is None or self.busy_notice.done():
                self.busy_notice = asyncio.create_task(
                    self._failure(event, "小猪正在处理请求，请等当前请求完成后再试。")
                )
                await asyncio.shield(self.busy_notice)
            return
        task = asyncio.create_task(
            self._run_request(event, app_id, command, args, time.monotonic() + REQUEST_TIMEOUT)
        )
        self.inflight[key] = task
        self.active_users.add(owner)

        def finished(_):
            self.inflight.pop(key, None)
            self.active_users.discard(owner)

        task.add_done_callback(finished)
        await asyncio.shield(task)

    async def _run_request(self, event, app_id, command, args, deadline):
        try:
            await asyncio.wait_for(self.limit.acquire(), max(0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            if not self.stopping:
                await self._failure(event, "本次请求排队超时，请重新发送指令。")
            return
        try:
            if not self.stopping:
                await self._execute(event, app_id, command, args, deadline)
        finally:
            self.limit.release()

    async def _execute(self, event, app_id: str, command: str, args: tuple, deadline: float):
        try:
            if time.monotonic() >= deadline:
                raise PiggyError("本次请求排队超时，请重新发送指令。")
            await self.initialize()
            receipt = await self.db.delivery(message_key(event, app_id))
            if receipt["done"]:
                return
            raw = event.message_obj.raw_message
            nickname = getattr(getattr(raw, "author", None), "username", "") or ""
            if not nickname:
                nickname = (getattr(raw, "raw_data", {}) or {}).get("author", {}).get(
                    "username", ""
                ) or ""
            user = await self.db.identify(
                app_id, event.get_sender_id(), event.get_group_id(), nickname
            )
            if command == "draw":
                if self.settings.use_host(command):
                    self.settings.check_host()
                result = await self.db.draw(
                    user["id"],
                    event.get_group_id(),
                    event.message_obj.message_id,
                    duplicate_rate_cap=self.settings.duplicate_rate_cap,
                    duplicate_pity=self.settings.duplicate_pity,
                    gather_chance=self.settings.draw_gather_chance,
                    chain_chance=self.settings.draw_chain_chance,
                    app_id=app_id,
                )
                message = await asyncio.to_thread(
                    today_message,
                    self.settings,
                    self.root,
                    user,
                    result,
                    await self.db.collection(user["id"]),
                )
            elif command == "draw_all":
                result = await self.db.draw(
                    user["id"],
                    event.get_group_id(),
                    event.message_obj.message_id,
                    duplicate_rate_cap=self.settings.duplicate_rate_cap,
                    duplicate_pity=self.settings.duplicate_pity,
                    gather_chance=self.settings.draw_gather_chance,
                    chain_chance=self.settings.draw_chain_chance,
                    app_id=app_id,
                    all_available=True,
                )
                message = draw_all_message(
                    user, result, await self.db.collection(user["id"])
                )
            elif command in {"atlas", "pen"}:
                message = await collection_message(
                    self.settings,
                    self.root,
                    user,
                    await self.db.collection(user["id"]),
                    args[0],
                    command == "atlas",
                    args[1] if command == "pen" and len(args) > 1 else (),
                )
            elif command == "ranking":
                kind = args[0]
                if kind not in {"种类", "数量"}:
                    raise PiggyError("用法：小猪排行 种类/数量")
                boards = await self.db.rankings(app_id, event.get_group_id())
                avatars = await self.avatars.get_many(app_id, boards["species"] + boards["total"])
                message = await asyncio.to_thread(
                    ranking_message, self.settings, self.root, user, boards, avatars
                )
            elif command == "alias":
                await self.db.set_alias(user["id"], args[0])
                message = Message("称呼已经保存啦。")
            elif command == "reload":
                async with self.maintenance_lock:
                    pigs = await asyncio.to_thread(read_catalog, self.root)
                    await self.db.catalog(pigs)
                missing = sum(1 for p in pigs if p["enabled"] and not p["battle"])
                message = Message(
                    f"猪库重载完成，当前启用 {sum(p['enabled'] for p in pigs)} 种小猪。"
                    + (f"其中 {missing} 种未配置战斗数据，暂用默认属性和技能。" if missing else "")
                )
            elif command == "backup":
                async with self.maintenance_lock:
                    await self.db.backup(self.settings.backup_keep)
                message = Message("备份已保存到插件数据目录 backups，包含数据库、猪库及历史素材。")
            elif command in BATTLE_COMMANDS:
                message = await self._battle(event, app_id, user, command, args)
            else:
                self.settings.check_host()
                progress = await self.db.collection(user["id"])
                pig = next(p for p in progress["entries"] if p["enabled"])
                message = Message(
                    "# 图片链路检查\n\n![诊断小猪 #192px #192px]({{image:0}})\n\nQQ 图片转存检查已通过。",
                    (self.root / "assets" / pig["asset"],),
                )
            await self.sender.send(
                event, app_id, message, deadline, force_upload=command == "diagnose"
            )
        except PiggyError as exc:
            if isinstance(exc, UploadError):
                logger.warning(
                    "[piggy] Upload failed command=%s attempt=%s/%s retryable=%s: %s | %s",
                    command,
                    exc.attempt,
                    self.settings.upload_retry_count + 1,
                    exc.retryable,
                    exc,
                    exc.diagnostic or exception_detail(exc, self.settings),
                )
            else:
                logger.warning("[piggy] Request failed command=%s: %s", command, exc)
            if isinstance(exc, QQError) and exc.uncertain:
                return
            await self._failure(event, str(exc))
        except (sqlite3.Error, OSError) as exc:
            logger.error("[piggy] Storage failure: %s", exception_detail(exc, self.settings))
            await self._failure(
                event,
                "数据或素材暂时无法读写，请联系管理员检查。已有抽取记录不会被清空。",
            )
        except Exception as exc:
            logger.error(
                "[piggy] Unexpected request failure: %s", exception_detail(exc, self.settings)
            )
            await self._failure(event, "处理暂时失败，请联系管理员查看插件日志。")

    async def _target(self, event, app_id: str, words: tuple, command: str):
        targets, bots = mention_targets(event)
        if bots:
            await self.db.forget_bot(app_id, bots)
        if targets:
            chosen = targets[0]
            logger.info(
                "[piggy] Request target resolved command=%s source=%s open_id=%s...",
                command,
                chosen["source"],
                chosen["open_id"][:6],
            )
            target = await self.db.identify(
                app_id, chosen["open_id"], event.get_group_id(), chosen["name"]
            )
            # Some clients also leave a plain "@昵称" word in the text.
            if words and words[0][:1] in "@＠":
                words = words[1:]
            return target, words
        # Without a mention the first word must be a name, followed by the pig names.
        if len(words) < NAME_MIN_WORDS[command]:
            logger.info(
                "[piggy] No mention resolved command=%s text=%r delivered=%s",
                command,
                message_text(event),
                mention_debug(event),
            )
            raise PiggyError(
                f"没有识别到你 @ 的群友。可以改用对方的玩家编号：{NAME_USAGE[command]}"
                "（对方发送「我的猪圈」即可看到编号），也可以把编号换成对方的昵称。"
            )
        return await self.db.find_group_player(app_id, event.get_group_id(), words[0]), words[1:]

    async def _battle(self, event, app_id: str, user: dict, command: str, words: tuple):
        settings, group = self.settings, event.get_group_id()
        if command == "guide":
            owned = [p for p in (await self.db.collection(user["id"]))["entries"] if p["count"]]
            favorite = max(owned, key=lambda p: p["count"], default=None)
            return guide_message(settings, user, favorite)
        if command == "stats":
            if not words:
                raise PiggyError("用法：小猪属性 小猪名字")
            pig = await self.db.find_pig(" ".join(words))
            count = await self.db.pig_count(user["id"], pig["id"])
            personality = await self.db.pig_personality(user["id"], pig["id"])
            return stats_message(settings, user, pig, count, settings.battle_level_cap, personality)
        if command == "dummy_test":
            if not words:
                raise PiggyError("用法：测试小猪 小猪名字 [AOE] [回合数]")
            if settings.use_host("raid"):
                settings.check_host()
            rounds = DUMMY_DEFAULT_ROUNDS
            name_words = list(words)
            if len(name_words) > 1 and name_words[-1].isdigit():
                rounds = int(name_words.pop())
            targets = 1
            aoe_words = {"aoe", "群体", "3目标", "三目标"}
            modes = [word for word in name_words if word.casefold() in aoe_words]
            if modes:
                targets = 3
                name_words = [word for word in name_words if word.casefold() not in aoe_words]
            if not 1 <= rounds <= DUMMY_MAX_ROUNDS:
                raise PiggyError(f"木桩测试回合数需要在 1–{DUMMY_MAX_ROUNDS} 之间。")
            pig = await self.db.find_pig(" ".join(name_words))
            count = await self.db.pig_count(user["id"], pig["id"])
            if count < 1:
                raise PiggyError("你的猪圈里没有这只小猪，换一只测试吧。")
            personality = await self.db.pig_personality(user["id"], pig["id"])
            unit = fighter(
                pig,
                entry_for(pig.get("battle")),
                level_for(count, settings.battle_level_cap),
                f"{player_name(user)}的{pig['name']}",
                personality=personality,
            )
            result = await asyncio.to_thread(
                simulate_dummy, unit, rounds, secrets.randbits(32), targets
            )
            return await dummy_test_message(settings, self.root, user, unit, result)
        if command in ("grant_pig", "grant_draw"):
            return await self._grant(event, app_id, command, words)
        if command in ("duel", "trade"):
            target, words = await self._target(event, app_id, words, command)
            words = [word for word in words if word != "换"]
            if len(words) < (1 if command == "duel" else 2):
                raise PiggyError(USAGE[command])
            give = await self.db.find_pig(words[0])
            want = await self.db.find_pig(words[1]) if command == "trade" else None
            request = await self.db.create_request(
                app_id,
                group,
                command,
                user["id"],
                target["id"],
                give["id"],
                want["id"] if want else None,
                ttl_minutes=settings.request_ttl_minutes,
                daily_limit=settings.duel_daily_limit,
            )
            level = level_for(request["give_count"], settings.battle_level_cap)
            return request_message(settings, request, level)
        if command == "wild":
            if settings.use_host("wild"):
                settings.check_host()
            wild = await self.db.wild_pig(app_id, group, level_max=settings.wild_level_max)
            return await asyncio.to_thread(wild_message, settings, self.root, user, wild)
        if command == "wild_challenge":
            if not words:
                raise PiggyError("用法：挑战小猪 你的小猪，例如：挑战小猪 猪人")
            if settings.use_host("wild"):
                settings.check_host()
            pig = await self.db.find_pig(" ".join(words))
            result = await self.db.challenge_wild(
                app_id,
                group,
                user["id"],
                pig["id"],
                level_cap=settings.battle_level_cap,
                level_max=settings.wild_level_max,
            )
            return await wild_battle_message(settings, self.root, result)
        if command.startswith("raid"):
            return await self._raid(app_id, group, user, command, words)
        if command.startswith("despair"):
            return await self._despair(app_id, group, user, command, words)
        if command.startswith("mythic"):
            return await self._mythic(app_id, group, user, command, words)
        if command == "shop":
            shop = await self.db.shop(app_id, group, user["id"])
            if settings.use_host("shop"):
                settings.check_host()
            return await shop_message(settings, self.root, user, shop)
        if command == "shop_exchange":
            slot = words[0].strip("#＃号") if words else ""
            if not slot.isdigit() or len(words) < 2:
                raise PiggyError("用法：商店交换 编号 你的小猪，例如：商店交换 1 猪人")
            pay = await self.db.find_pig(" ".join(words[1:]))
            result = await self.db.shop_exchange(
                app_id,
                group,
                user["id"],
                int(slot),
                pay["id"],
                level_cap=settings.battle_level_cap,
            )
            return shop_exchange_message(settings, user, result)
        if command == "duel_ranking":
            board = await self.db.duel_rankings(app_id, group, user["id"])
            return duel_ranking_message(settings, user, board)
        if command == "duel_history":
            if words and not words[0].isdigit():
                raise PiggyError("用法：斗猪记录 [页码]")
            history = await self.db.duel_history(user["id"], int(words[0]) if words else 1)
            if settings.use_host("duel"):
                settings.check_host()
            return await duel_history_message(settings, self.root, user, history)
        if command == "duel_replay":
            number = words[0].lstrip("#＃") if words else ""
            if not number.isdigit():
                raise PiggyError("用法：斗猪回放 编号（编号见「斗猪记录」）")
            record = await self.db.duel_record(user["id"], int(number))
            if settings.use_host("duel"):
                settings.check_host()
            return await duel_replay_message(settings, self.root, user, record)
        if command == "cancel":
            return cancelled_message(
                settings, await self.db.cancel_requests(app_id, group, user["id"])
            )
        if command == "requests":
            return requests_message(
                settings, await self.db.list_requests(app_id, group, user["id"])
            )
        kind, verb = command.split("_")
        pig_id = None
        if kind == "duel" and verb == "accept":
            if not words:
                raise PiggyError("请写上你要出战的小猪，例如：接受斗猪 猪人")
            pig_id = (await self.db.find_pig(" ".join(words)))["id"]
        result = await self.db.respond(
            app_id,
            group,
            user["id"],
            kind,
            verb == "accept",
            pig_id,
            level_cap=settings.battle_level_cap,
            daily_limit=settings.duel_daily_limit,
        )
        if not result["accepted"]:
            return declined_message(settings, result)
        if kind == "duel":
            return await battle_message(settings, self.root, result)
        return trade_message(settings, result)

    async def _grant(self, event, app_id: str, command: str, words: tuple):
        target, words = await self._target(event, app_id, words, command)
        amount = words[-1] if words else ""
        if not amount.isdigit() or not 1 <= int(amount) <= GRANT_MAX:
            raise PiggyError(f"{USAGE[command]}（数量 1–{GRANT_MAX}）")
        amount, name = int(amount), player_name(target)
        if command == "grant_draw":
            left = await self.db.grant_draws(app_id, event.get_group_id(), target["id"], amount)
            return Message(f"已给 {name} 发放 {amount} 次再抽，今天在本群还剩 {left} 次。")
        if len(words) < 2:
            raise PiggyError(USAGE[command])
        pig = await self.db.find_pig(" ".join(words[:-1]))
        change = await self.db.grant_pig(
            target["id"], pig["id"], amount, level_cap=self.settings.battle_level_cap
        )
        level = (
            f"Lv.{change['before']} → Lv.{change['after']}"
            if change["before"] != change["after"]
            else f"Lv.{change['after']}"
        )
        return Message(
            f"已给 {name} 发放 {amount} 只{pig['name']}，现有 {change['count_after']} 只（{level}）。"
        )

    async def _raid(self, app_id: str, group: str, user: dict, command: str, words: tuple):
        settings, uid = self.settings, user["id"]
        if command == "raid":
            key = None
            if words:
                try:
                    key = dungeon(words[0])["key"]
                except KeyError:
                    raise PiggyError(f"用法：猪副本 [编号]（{RAID_NAMES}）") from None
            if settings.use_host("raid"):
                settings.check_host()
            status = await self.db.raid_status(app_id, group, uid)
            if key is None:
                return await raid_list_message(settings, self.root, user, status)
            return await raid_detail_message(settings, self.root, user, status, key)
        if command == "raid_leave":
            result = await self.db.leave_raid(app_id, group, uid)
            return raid_lobby_message(
                settings, result, "cancelled" if result["cancelled"] else "leave"
            )
        if command == "raid_retreat":
            return raid_lobby_message(
                settings, await self.db.retreat_raid(app_id, group, uid), "retreated"
            )
        if command in ("raid_join", "raid_continue") and settings.use_host("raid"):
            settings.check_host()
        if command == "raid_continue":
            battle = await self.db.continue_raid(
                app_id, group, uid, level_cap=settings.battle_level_cap
            )
            return await raid_battle_message(settings, self.root, battle)
        if command == "raid_open":
            try:
                key = dungeon(words[0])["key"] if len(words) >= 2 else None
            except KeyError:
                key = None
            if key is None:
                raise PiggyError(RAID_USAGE)
            pig = await self.db.find_pig(" ".join(words[1:]))
            result = await self.db.open_raid(app_id, group, uid, key, pig["id"])
            return raid_lobby_message(settings, result, "open")
        if not words:
            raise PiggyError("用法：加入副本 你的小猪，例如：加入副本 猪人")
        pig = await self.db.find_pig(" ".join(words))
        result = await self.db.join_raid(
            app_id, group, uid, pig["id"], level_cap=settings.battle_level_cap
        )
        if result["started"]:
            return await raid_battle_message(settings, self.root, result["battle"])
        return raid_lobby_message(settings, result, "join")

    async def _despair(self, app_id: str, group: str, user: dict, command: str, words: tuple):
        settings, uid = self.settings, user["id"]
        if command == "despair_test":
            if len(words) > 1:
                raise PiggyError("用法：测试绝境战 [随机种子]")
            try:
                seed = int(words[0]) if words else None
            except ValueError:
                raise PiggyError("随机种子必须是整数。") from None
            if settings.use_host("raid"):
                settings.check_host()
            battle = await self.db.test_despair(user, level=10, seed=seed)
            return await despair_battle_message(settings, self.root, battle)
        if command == "despair":
            status = await self.db.despair_status(app_id, group, uid)
            return despair_status_message(settings, user, status)
        if command == "despair_leave":
            result = await self.db.leave_despair(app_id, group, uid)
            return despair_lobby_message(
                settings, result, "cancelled" if result["cancelled"] else "leave"
            )
        if len(words) != 2:
            verb = "开启" if command == "despair_open" else "加入"
            raise PiggyError(f"用法：{verb}绝境战 小猪A 小猪B（每人必须选择两只不同的小猪）")
        pigs = [await self.db.find_pig(name) for name in words]
        pig_ids = [pig["id"] for pig in pigs]
        if command == "despair_open":
            result = await self.db.open_despair(app_id, group, uid, pig_ids)
            return despair_lobby_message(settings, result, "open")
        if settings.use_host("raid"):
            settings.check_host()
        result = await self.db.join_despair(
            app_id,
            group,
            uid,
            pig_ids,
            level_cap=settings.battle_level_cap,
        )
        if result["started"]:
            return await despair_battle_message(settings, self.root, result["battle"])
        return despair_lobby_message(settings, result, "join")

    async def _mythic(self, app_id: str, group: str, user: dict, command: str, words: tuple):
        settings, uid = self.settings, user["id"]
        if command == "mythic":
            return mythic_status_message(
                settings, user, await self.db.mythic_status(app_id, group, uid)
            )
        if command == "mythic_key":
            return mythic_key_message(
                settings, user, await self.db.mythic_status(app_id, group, uid)
            )
        if command == "mythic_key_claim":
            if words:
                raise PiggyError("用法：领取钥匙")
            return mythic_key_action_message(
                settings, user, await self.db.claim_mythic_key(uid), "claim"
            )
        if command == "mythic_key_lower":
            if words:
                raise PiggyError("用法：降级钥匙")
            return mythic_key_action_message(
                settings, user, await self.db.lower_mythic_key(uid), "lower"
            )
        if command == "mythic_ranking":
            if words and words[0] not in {"总分", "周榜"}:
                raise PiggyError("用法：大秘境排行 [周榜/总分]")
            board = await self.db.mythic_rankings(app_id, group)
            return mythic_rankings_message(board, total=bool(words and words[0] == "总分"))
        if command == "mythic_test":
            if len(words) > 3:
                raise PiggyError("用法：测试大秘境 [副本编号] [层数] [随机种子]")
            try:
                dungeon_key = int(words[0]) if words else 1 + secrets.randbelow(len(DUNGEONS))
                key_level = int(words[1].lstrip("+")) if len(words) >= 2 else 2 + secrets.randbelow(22)
                seed = int(words[2]) if len(words) >= 3 else secrets.randbits(32)
            except ValueError:
                raise PiggyError("副本编号、层数和随机种子必须是整数。") from None
            if not 1 <= dungeon_key <= len(DUNGEONS) or key_level < 2:
                raise PiggyError("副本编号必须是 1–7，层数必须不低于 2。")
            report = await self.db.test_mythic(
                user, dungeon_key=dungeon_key, key_level=key_level, seed=seed
            )
            return await asyncio.to_thread(
                mythic_battle_message, settings, self.root, report, test=True
            )
        if command == "mythic_leave":
            result = await self.db.leave_mythic(app_id, group, uid)
            return mythic_lobby_message(
                settings,
                result, "cancelled" if result["cancelled"] else "leave"
            )
        if not words:
            verb = "开启" if command == "mythic_open" else "加入"
            raise PiggyError(f"用法：{verb}大秘境 你的小猪")
        pig = await self.db.find_pig(" ".join(words))
        if command == "mythic_open":
            result = await self.db.open_mythic(
                app_id,
                group,
                uid,
                pig["id"],
                level_cap=settings.battle_level_cap,
            )
            return mythic_lobby_message(settings, result, "open")
        result = await self.db.join_mythic(
            app_id,
            group,
            uid,
            pig["id"],
            level_cap=settings.battle_level_cap,
        )
        if result["started"]:
            return await asyncio.to_thread(
                mythic_battle_message, settings, self.root, result["battle"]
            )
        return mythic_lobby_message(settings, result, "join")

    async def _failure(self, event, text: str):
        try:
            await self.transport.request(
                event,
                {
                    "msg_type": 0,
                    "content": text,
                    "msg_id": event.message_obj.message_id,
                    "msg_seq": 9999,
                },
            )
        except Exception as exc:
            logger.warning("[piggy] Could not send failure notice (%s).", type(exc).__name__)

    @filter.command("今日小猪", alias={"抽小猪"})
    async def draw(self, event: AstrMessageEvent):
        """每天领一只小猪，跨群共享收藏；当天重复使用会返回同一只。"""
        await self._handle(event, "draw")

    @filter.command("全部抽完")
    async def draw_all(self, event: AstrMessageEvent):
        """一次用完今日免费抽取和本群当天剩余的全部再抽次数。"""
        await self._handle(event, "draw_all")

    @filter.command("小猪图鉴")
    async def atlas(self, event: AstrMessageEvent, page: int = 1):
        """查看小猪图鉴和解锁进度。"""
        await self._handle(event, "atlas", (page,))

    @filter.command("我的猪圈")
    async def pen(
        self,
        event: AstrMessageEvent,
        first: str = "",
        second: str = "",
        third: str = "",
        fourth: str = "",
        fifth: str = "",
    ):
        """查看已收藏的小猪；可按职业/机制标签交集筛选。"""
        page = 1
        tags = []
        for value in (first, second, third, fourth, fifth):
            value = str(value).strip()
            if not value:
                continue
            if value.isdigit():
                page = int(value)
            else:
                tags.append(value)
        await self._handle(event, "pen", (page, tuple(tags)))

    @filter.command("小猪排行")
    async def ranking(self, event: AstrMessageEvent, kind: str = "种类"):
        """查看本群玩家的种类、数量双榜，各前十。"""
        await self._handle(event, "ranking", (kind,))

    @filter.command("小猪称呼")
    async def alias(self, event: AstrMessageEvent, name: str):
        """设置 1–24 字的展示称呼。用法：小猪称呼 名字；不会改变收藏归属。"""
        await self._handle(event, "alias", (name,))

    @filter.command("小猪玩法", alias={"小猪帮助"})
    async def guide(self, event: AstrMessageEvent):
        """查看稀有度、性格、副本和交换的玩法说明。"""
        await self._handle(event, "guide")

    @filter.command("小猪商店")
    async def shop(self, event: AstrMessageEvent):
        """查看今日小猪商店：每天 0 点刷新 5 只，每只限量 1 个。"""
        await self._handle(event, "shop")

    @filter.command("商店交换")
    async def shop_exchange(self, event: AstrMessageEvent):
        """用自己的 1 只小猪换商店里的小猪。用法：商店交换 编号 你的小猪"""
        await self._handle(event, "shop_exchange", command_words(event, ("商店交换",)))

    @filter.command("小猪挑战")
    async def wild(self, event: AstrMessageEvent):
        """查看本群今天的野生小猪：每天一只，打败后全群各得 1 次再抽。"""
        await self._handle(event, "wild")

    @filter.command("挑战小猪")
    async def wild_challenge(self, event: AstrMessageEvent):
        """用自己的小猪挑战野猪，输了会失去出战的小猪。用法：挑战小猪 你的小猪"""
        await self._handle(event, "wild_challenge", command_words(event, ("挑战小猪",)))

    @filter.command("猪副本")
    async def raid(self, event: AstrMessageEvent):
        """查看猪副本总览和本群的组队情况；猪副本 编号 查看某个副本的 boss 机制。"""
        await self._handle(event, "raid", command_words(event, ("猪副本",)))

    @filter.command("开启副本")
    async def raid_open(self, event: AstrMessageEvent):
        """发起猪副本组队，满 4 人自动开打。用法：开启副本 编号 你的小猪"""
        await self._handle(event, "raid_open", command_words(event, ("开启副本",)))

    @filter.command("加入副本")
    async def raid_join(self, event: AstrMessageEvent):
        """加入本群正在组队的猪副本。用法：加入副本 你的小猪"""
        await self._handle(event, "raid_join", command_words(event, ("加入副本",)))

    @filter.command("退出副本")
    async def raid_leave(self, event: AstrMessageEvent):
        """组队期间退出队伍；队长退出则整队取消。"""
        await self._handle(event, "raid_leave")

    @filter.command("继续副本")
    async def raid_continue(self, event: AstrMessageEvent):
        """队长：打完一关后继续挑战下一个 boss。"""
        await self._handle(event, "raid_continue")

    @filter.command("撤退副本")
    async def raid_retreat(self, event: AstrMessageEvent):
        """队长：打完一关后带着奖励撤退。"""
        await self._handle(event, "raid_retreat")

    @filter.command("小猪大秘境")
    async def mythic(self, event: AstrMessageEvent):
        """查看自己的钥石、本周规则和本群大秘境队伍。"""
        await self._handle(event, "mythic")

    @filter.command("查询钥匙")
    async def mythic_key(self, event: AstrMessageEvent):
        """查询当前大秘境钥石、保底层及七本限时进度。"""
        await self._handle(event, "mythic_key")

    @filter.command("领取钥匙")
    async def mythic_key_claim(self, event: AstrMessageEvent):
        """通关七个普通副本后，领取初始随机副本 +2 钥石。"""
        await self._handle(event, "mythic_key_claim", command_words(event, ("领取钥匙",)))

    @filter.command("降级钥匙")
    async def mythic_key_lower(self, event: AstrMessageEvent):
        """将钥石降低一级，但不会低于七本限时进度形成的保底层。"""
        await self._handle(event, "mythic_key_lower", command_words(event, ("降级钥匙",)))

    @filter.command("开启大秘境")
    async def mythic_open(self, event: AstrMessageEvent):
        """使用自己的钥石发起四人大秘境。用法：开启大秘境 你的小猪"""
        await self._handle(event, "mythic_open", command_words(event, ("开启大秘境",)))

    @filter.command("加入大秘境")
    async def mythic_join(self, event: AstrMessageEvent):
        """加入本群的大秘境队伍。用法：加入大秘境 你的小猪"""
        await self._handle(event, "mythic_join", command_words(event, ("加入大秘境",)))

    @filter.command("退出大秘境")
    async def mythic_leave(self, event: AstrMessageEvent):
        """退出大秘境队伍；队长退出会解散队伍。"""
        await self._handle(event, "mythic_leave")

    @filter.command("大秘境排行")
    async def mythic_ranking(self, event: AstrMessageEvent):
        """查看四人队伍周榜或七本总分榜。用法：大秘境排行 [周榜/总分]"""
        await self._handle(event, "mythic_ranking", command_words(event, ("大秘境排行",)))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("测试大秘境")
    async def mythic_test(self, event: AstrMessageEvent):
        """仅管理员：无参数随机测试，也可指定副本、层数和种子。"""
        await self._handle(event, "mythic_test", command_words(event, ("测试大秘境",)))

    @filter.command("绝境战")
    async def despair(self, event: AstrMessageEvent):
        """查看绝境战解锁、周奖励和当前组队状态。"""
        await self._handle(event, "despair")

    @filter.command("开启绝境战")
    async def despair_open(self, event: AstrMessageEvent):
        """发起四人八猪绝境战。用法：开启绝境战 小猪A 小猪B"""
        await self._handle(event, "despair_open", command_words(event, ("开启绝境战",)))

    @filter.command("加入绝境战")
    async def despair_join(self, event: AstrMessageEvent):
        """加入绝境战。用法：加入绝境战 小猪A 小猪B"""
        await self._handle(event, "despair_join", command_words(event, ("加入绝境战",)))

    @filter.command("退出绝境战")
    async def despair_leave(self, event: AstrMessageEvent):
        """组队期间退出绝境战；队长退出会解散整队。"""
        await self._handle(event, "despair_leave")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("测试绝境战")
    async def despair_test(self, event: AstrMessageEvent):
        """仅管理员：随机选四职业各两只 10 级猪，生成完整绝境战测试战报。"""
        await self._handle(event, "despair_test", command_words(event, ("测试绝境战",)))

    async def duel_ranking(self, event: AstrMessageEvent):
        """查看本群玩家的斗猪胜率排行，至少 3 场上榜。"""
        await self._handle(event, "duel_ranking")

    async def duel_history(self, event: AstrMessageEvent):
        """查看自己的斗猪历史战绩。用法：斗猪记录 [页码]"""
        await self._handle(event, "duel_history", command_words(event, ("斗猪记录",)))

    async def duel_replay(self, event: AstrMessageEvent):
        """查看自己参与的某场斗猪的完整战报。用法：斗猪回放 编号"""
        await self._handle(event, "duel_replay", command_words(event, ("斗猪回放",)))

    @filter.command("小猪属性")
    async def stats(self, event: AstrMessageEvent):
        """查看小猪的等级、属性和技能。用法：小猪属性 小猪名字"""
        await self._handle(event, "stats", command_words(event, ("小猪属性",)))

    @filter.command("测试小猪")
    async def dummy_test(self, event: AstrMessageEvent):
        """测试小猪单体或三目标 DPS。用法：测试小猪 小猪名字 [AOE] [回合数]"""
        await self._handle(event, "dummy_test", command_words(event, ("测试小猪",)))

    async def duel(self, event: AstrMessageEvent):
        """向群友发起斗猪，败者失去出战的小猪。用法：斗猪 @对方 你的小猪"""
        await self._handle(event, "duel", command_words(event, ("斗猪",)))

    async def duel_accept(self, event: AstrMessageEvent):
        """接受斗猪并选择出战小猪。用法：接受斗猪 你的小猪"""
        await self._handle(event, "duel_accept", command_words(event, ("接受斗猪",)))

    async def duel_decline(self, event: AstrMessageEvent):
        """拒绝收到的斗猪请求。"""
        await self._handle(event, "duel_decline")

    @filter.command("小猪交换")
    async def trade(self, event: AstrMessageEvent):
        """用自己的小猪换群友的小猪。用法：小猪交换 @对方 你的小猪 对方的小猪"""
        await self._handle(event, "trade", command_words(event, ("小猪交换",)))

    @filter.command("接受交换")
    async def trade_accept(self, event: AstrMessageEvent):
        """接受收到的小猪交换请求。"""
        await self._handle(event, "trade_accept")

    @filter.command("拒绝交换")
    async def trade_decline(self, event: AstrMessageEvent):
        """拒绝收到的小猪交换请求。"""
        await self._handle(event, "trade_decline")

    @filter.command("取消请求")
    async def cancel(self, event: AstrMessageEvent):
        """撤回自己在本群发出的斗猪和交换请求。"""
        await self._handle(event, "cancel")

    @filter.command("我的请求")
    async def requests(self, event: AstrMessageEvent):
        """查看本群待处理的斗猪和交换请求。"""
        await self._handle(event, "requests")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("小猪重载")
    async def reload(self, event: AstrMessageEvent):
        """仅管理员：校验并重载猪库；校验失败时保留上一份有效猪库。"""
        await self._handle(event, "reload")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("小猪备份")
    async def backup(self, event: AstrMessageEvent):
        """仅管理员：将数据库、猪库和历史素材备份到本地 backups 目录。"""
        await self._handle(event, "backup")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("小猪诊断")
    async def diagnose(self, event: AstrMessageEvent):
        """仅管理员：绕过缓存上传测试猪图，发送消息并检查 QQ 图片转存链路。"""
        await self._handle(event, "diagnose")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发小猪")
    async def grant_pig(self, event: AstrMessageEvent):
        """仅管理员：给群友发放小猪。用法：发小猪 @对方 小猪名字 数量"""
        await self._handle(event, "grant_pig", command_words(event, ("发小猪",)))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发再抽")
    async def grant_draw(self, event: AstrMessageEvent):
        """仅管理员：给群友发放今天在本群可用的再抽次数。用法：发再抽 @对方 数量"""
        await self._handle(event, "grant_draw", command_words(event, ("发再抽",)))

    async def terminate(self):
        self.stopping = True
        # Drain active thread-backed work before closing clients; queued work skips execution.
        tasks = list(self.inflight.values())
        if self.busy_notice:
            tasks.append(self.busy_notice)
        await asyncio.gather(*tasks, return_exceptions=True)
        async with self.init_lock:
            pass
        for task in (self.backup_task, self.cleanup_task, self.announce_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.publisher.close()
        await self.transport.close()
        await self.avatars.close()
