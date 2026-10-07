import asyncio
import math
import re
import time
from datetime import datetime
from pathlib import Path

from .battle import STAT_NAMES, STATS, describe_skill, entry_for, fighter, level_for
from .config import PiggyError, Settings
from .database import EAST_ASIA
from .delivery import Message
from .rendering import (
    ATLAS_SHEET_SIZE,
    PEN_SHEET_SIZE,
    render_collection,
    render_ranking,
    render_today,
)


def md(text: str) -> str:
    text = str(text).replace("{{", "｛｛").replace("}}", "｝｝")
    return re.sub(r"([\\`*_{}\[\]<>()#+.!|~>-])", r"\\\1", text)


def display_name(user: dict) -> str:
    return user.get("alias") or user.get("nickname") or f"玩家 {user['id']:04d}"


def paginate(items: list, page: int, size: int) -> tuple[list, int, int]:
    pages = max(1, math.ceil(len(items) / size))
    if not 1 <= page <= pages:
        raise PiggyError(f"页码超出范围，请输入 1–{pages}。")
    return items[(page - 1) * size : page * size], page, pages


def keyboard(
    settings: Settings, owner: str, command: str = "", page: int = 1, pages: int = 1
) -> dict:
    def button(label: str, data: str, private: bool = False):
        permission = {"type": 0, "specify_user_ids": [owner]} if private else {"type": 2}
        return {
            "id": data,
            "render_data": {"label": label, "visited_label": label, "style": 1},
            "action": {
                "type": 2,
                "permission": permission,
                "data": settings.command_prefix + data,
            },
        }

    rows = [
        {"buttons": [button("今日小猪", "今日小猪"), button("小猪图鉴", "小猪图鉴")]},
        {"buttons": [button("小猪排行", "小猪排行"), button("我的猪圈", "我的猪圈")]},
        {
            "buttons": [
                button("斗猪玩法", "小猪玩法"),
                button("斗猪排行", "斗猪排行"),
                button("斗猪记录", "斗猪记录"),
            ]
        },
    ]
    navigation = []
    if page > 1:
        navigation.append(button("上一页", f"{command} {page - 1}", True))
    if page < pages:
        navigation.append(button("下一页", f"{command} {page + 1}", True))
    if navigation:
        rows.append({"buttons": navigation})
    return {"content": {"rows": rows}}


def today_message(
    settings: Settings, root: Path, user: dict, result: dict, progress: dict
) -> Message:
    pig = result["pig"]
    state = (
        "今天已经抽过啦，还是这只"
        if not result["created"]
        else "首次解锁！"
        if result["new_species"]
        else "老朋友又来啦！"
    )
    protection = ""
    streak = result.get("repeat_streak", 0)
    if settings.duplicate_pity and streak and progress["unlocked"] < progress["active_total"]:
        protection = (
            "下次领取必出未收集小猪！"
            if streak >= settings.duplicate_pity
            else f"重复保护 {streak}/{settings.duplicate_pity}"
        )
    level = level_for(result["count"], settings.battle_level_cap)
    tip = f"Lv{level} · 已解锁 {min(level, 5)}/5 个技能 · 发送「小猪玩法」和群友斗猪、换猪"
    if not settings.use_host("draw"):
        card = render_today(
            root,
            display_name(user),
            result,
            progress,
            "今日已领取" if not result["created"] else state,
            protection,
            level,
        )
        return Message("", (card.data,), local=True)
    description = "\n".join(
        f"> {line}" if line else ">"
        for line in md(f"{pig['description']}\n\n{pig['analysis']}").splitlines()
    )
    text = (
        f'<qqbot-at-user id="{user["open_id"]}" />\n\n### 🐷 今日小猪\n\n'
        f"{state}\n\n**{md(pig['name'])}**\n\n"
        f"![小猪 #512px #512px]({{{{image:0}}}})\n\n"
        f"{description}\n\n"
        f"本猪拥有 **{result['count']}** 只 · 总收获 **{progress['total']}** 只\n\n"
        f"已解锁 **{progress['unlocked']}/{progress['active_total']}** · {result['day']}"
    )
    if protection:
        text += f"\n\n{protection}"
    text += f"\n\n{md(tip)}"
    return Message(text, (root / "assets" / pig["asset"],), keyboard(settings, user["open_id"]))


async def collection_message(
    settings: Settings, root: Path, user: dict, progress: dict, page: int, atlas: bool
) -> Message:
    """The same in-memory cream card serves both delivery modes."""
    title = "小猪图鉴" if atlas else "我的猪圈"
    items = (
        [p for p in progress["entries"] if p["enabled"]]
        if atlas
        else [p for p in progress["entries"] if p["count"]]
    )
    entries, page, pages = paginate(items, page, ATLAS_SHEET_SIZE if atlas else PEN_SHEET_SIZE)
    card = await asyncio.to_thread(
        render_collection,
        root,
        display_name(user),
        progress,
        entries,
        page,
        pages,
        atlas,
        settings.battle_level_cap,
    )
    return card_message(settings, user, card, title, "atlas" if atlas else "pen", page, pages)


def ranking_message(
    settings: Settings, root: Path, user: dict, boards: dict, avatars: dict
) -> Message:
    return card_message(
        settings, user, render_ranking(root, boards, avatars), "小猪排行榜", "ranking"
    )


def _button(settings: Settings, label: str, data: str) -> dict:
    # Anyone may tap; respond() only matches requests addressed to the sender.
    return {
        "id": data,
        "render_data": {"label": label, "visited_label": label, "style": 1},
        "action": {"type": 2, "permission": {"type": 2}, "data": settings.command_prefix + data},
    }


def text_message(
    settings: Settings,
    title: str,
    blocks: list,
    mention: dict | None = None,
    buttons: list[tuple[str, str]] = (),
) -> Message:
    """Blocks are paragraphs (str) or bullet lists (list of str)."""
    if not settings.battle_markdown:
        parts = [title]
        if mention:
            parts[0] = f"@{display_name(mention)} {title}"
        for block in blocks:
            parts.append("\n".join(block) if isinstance(block, list) else block)
        return Message("\n".join(parts))
    parts = []
    if mention:
        parts.append(f'<qqbot-at-user id="{mention["open_id"]}" />')
    parts.append(f"### {md(title)}")
    for block in blocks:
        if isinstance(block, list):
            parts.append("\n".join(f"- {md(line)}" for line in block))
        else:
            parts.append(md(block))
    keyboard = None
    if buttons:
        keyboard = {
            "content": {
                "rows": [{"buttons": [_button(settings, label, data) for label, data in buttons]}]
            }
        }
    return Message("\n\n".join(parts), keyboard=keyboard, markdown=True)


def guide_message(settings: Settings, user: dict, favorite: dict | None) -> Message:
    example = favorite["name"] if favorite else "猪人"
    blocks = [
        "【等级】",
        [
            f"同种猪有几只就是几级（上限 Lv{settings.battle_level_cap}），等级越高属性越强",
            "1–5 级各解锁 1 个专属技能，每只猪最多 5 个技能",
            f"小猪属性 {example} —— 查看属性和技能",
        ],
        "【斗猪】",
        [
            f"斗猪 @群友 {example} —— 用你的猪发起挑战",
            "对方发送「接受斗猪 他的猪」立即开打，或「拒绝斗猪」",
            "回合制自动对战，赢家把输家出战的那只猪收进猪圈，输家这只猪降 1 级",
            f"每天最多 {settings.duel_daily_limit} 场",
        ],
        "【交换】",
        [
            f"小猪交换 @群友 {example} 对方的猪 —— 一换一",
            "对方发送「接受交换」成交，或「拒绝交换」",
        ],
        "【请求】",
        [
            f"我的请求 / 取消请求 —— 查看或撤回，{settings.request_ttl_minutes} 分钟内未处理自动作废",
        ],
        "【战绩】",
        [
            "斗猪排行 —— 本群斗猪胜率排行，至少 3 场上榜",
            "斗猪记录 [页码] —— 自己的历史对战",
            "斗猪回放 编号 —— 重看某场的完整战报",
        ],
    ]
    if favorite:
        blocks.append(f"你的「{example}」有 {favorite['count']} 只，是你现在最强的出战选择。")
    return text_message(
        settings,
        "小猪玩法：斗猪与交换",
        blocks,
        buttons=[("小猪属性", "小猪属性 "), ("斗猪", "斗猪 "), ("我的请求", "我的请求")],
    )


def _skill_line(skill: dict, slot: int, level: int) -> str:
    state = "已解锁" if slot <= level else f"Lv{slot} 解锁"
    return f"【{state}】{skill['name']}：{skill['text']}（{describe_skill(skill)}）"


def stats_message(settings: Settings, user: dict, pig: dict, count: int, cap: int) -> Message:
    entry = entry_for(pig.get("battle"))
    level = level_for(count, cap)
    unit = fighter(pig, entry, level)
    stats = unit["stats"]
    if count:
        owned = f"{display_name(user)} 拥有 {count} 只 · Lv{level}（上限 Lv{cap}）"
    else:
        owned = f"{display_name(user)} 还没有这只小猪，以下为 Lv1 数据"
    numbers = " · ".join(
        f"{STAT_NAMES[key]} {stats[key]}{'%' if key in ('crit', 'dodge') else ''}" for key in STATS
    )
    skills = [
        _skill_line(skill, slot, level if count else 0)
        for slot, skill in enumerate(entry["skills"], 1)
    ]
    return text_message(
        settings, f"{pig['name']}（{entry['style']}）", [owned, numbers, "技能", skills]
    )


def _level_text(change: dict) -> str:
    name = change["pig"]["name"]
    if not change["after"]:
        text = f"「{name}」全部输光，Lv{change['before']} → 已失去"
    elif not change["before"]:
        text = f"新获得「{name}」Lv{change['after']}"
    elif change["before"] == change["after"]:
        text = f"「{name}」Lv{change['after']}（已达等级上限）"
    else:
        text = f"「{name}」Lv{change['before']} → Lv{change['after']}"
    if change["lost"]:
        text += f"，失去技能：{'、'.join(change['lost'])}"
    if change["gained"]:
        text += f"，解锁技能：{'、'.join(change['gained'])}"
    return text


def request_message(settings: Settings, request: dict, level: int) -> Message:
    sender, target = display_name(request["from"]), request["to"]
    minutes = max(1, round((request["expires_at"] - request["created_at"]) / 60))
    if request["kind"] == "duel":
        return text_message(
            settings,
            f"{sender} 向你发起斗猪！",
            [
                f"对方出战：「{request['give']['name']}」Lv{level}",
                "发送「接受斗猪 你的小猪」应战，或发送「拒绝斗猪」。",
                f"败者会失去出战的那只小猪，等级随之下降。请求 {minutes} 分钟内有效。",
            ],
            mention=target,
            buttons=[("接受斗猪", "接受斗猪 "), ("拒绝斗猪", "拒绝斗猪")],
        )
    return text_message(
        settings,
        f"{sender} 想和你交换小猪！",
        [
            f"对方给出「{request['give']['name']}」，想换你的「{request['want']['name']}」。",
            f"发送「接受交换」或「拒绝交换」，请求 {minutes} 分钟内有效。",
        ],
        mention=target,
        buttons=[("接受交换", "接受交换"), ("拒绝交换", "拒绝交换")],
    )


def declined_message(settings: Settings, result: dict) -> Message:
    request = result["request"]
    label = "斗猪" if request["kind"] == "duel" else "交换"
    if result.get("error"):
        return text_message(settings, f"{label}请求已作废", [result["error"]], request["from"])
    return text_message(
        settings,
        f"{display_name(request['to'])} 拒绝了你的{label}请求",
        ["下次再约吧。"],
        mention=request["from"],
    )


def battle_message(settings: Settings, result: dict) -> Message:
    a, b = result["fighters"]
    fight = result["result"]
    winner, loser = result["winner"], result["loser"]
    hp = " / ".join(
        f"{unit['label']} {fight['hp'][i]}/{fight['max_hp'][i]}" for i, unit in enumerate((a, b))
    )
    left = result["duels_left"]
    return text_message(
        settings,
        f"斗猪：{a['label']} Lv{a['level']} VS {b['label']} Lv{b['level']}",
        [
            fight["log"],
            f"{display_name(winner)} 获胜！（{fight['rounds']} 回合，剩余生命 {hp}）",
            f"{display_name(loser)} 的「{result['loser_change']['pig']['name']}」归 "
            f"{display_name(winner)} 所有。",
            [
                f"{display_name(loser)}：{_level_text(result['loser_change'])}",
                f"{display_name(winner)}：{_level_text(result['winner_change'])}",
            ],
            "今日剩余斗猪次数："
            + "，".join(
                f"{display_name(user)} {left[uid]} 场" for uid, user in result["users"].items()
            ),
            "发送「斗猪记录」查看历史战绩，「斗猪排行」看本群胜率榜",
        ],
        mention=result["request"]["from"],
    )


def trade_message(settings: Settings, result: dict) -> Message:
    request, changes = result["request"], result["changes"]
    sender, target = display_name(request["from"]), display_name(request["to"])
    return text_message(
        settings,
        "交换成功！",
        [
            f"{sender} 的「{request['give']['name']}」⇄ {target} 的「{request['want']['name']}」",
            [
                f"{sender}：{_level_text(changes['from_give'])}",
                f"{sender}：{_level_text(changes['from_want'])}",
                f"{target}：{_level_text(changes['to_want'])}",
                f"{target}：{_level_text(changes['to_give'])}",
            ],
        ],
        mention=request["from"],
    )


def _rate(wins: int, games: int) -> str:
    return f"{wins / games:.0%}" if games else "0%"


def duel_ranking_message(settings: Settings, user: dict, board: dict) -> Message:
    need = board["min_games"]
    lines = [
        f"{row['rank']:02d}. {display_name(row)}  胜 {row['wins']} / 负 {row['losses']}  "
        f"胜率 {_rate(row['wins'], row['games'])}"
        for row in board["top"]
    ]
    blocks = [lines or [f"还没有玩家打满 {need} 场，快去斗猪吧。"]]
    me = board["me"]
    if not me:
        blocks.append("你还没有斗过猪，发送「小猪玩法」看看怎么开始。")
    elif not any(row["id"] == me["id"] for row in board["top"]):
        mine = f"你：胜 {me['wins']} / 负 {me['losses']}，胜率 {_rate(me['wins'], me['games'])}"
        if me["games"] < need:
            mine += f"，再打 {need - me['games']} 场即可上榜"
        elif "rank" in me:
            mine += f"，排第 {me['rank']} 名"
        blocks.append(mine)
    blocks.append(f"本群玩家 · 跨群累计战绩 · 至少 {need} 场上榜 · 发送「斗猪记录」查看自己的对战")
    return text_message(settings, "斗猪胜率排行", blocks)


def duel_history_message(settings: Settings, user: dict, history: dict) -> Message:
    total, wins = history["total"], history["wins"]
    if not total:
        return text_message(
            settings, "斗猪记录", ["你还没有斗过猪，发送「小猪玩法」看看怎么开始。"]
        )
    lines = []
    for record in history["records"]:
        day = datetime.fromtimestamp(record["fought_at"], EAST_ASIA).strftime("%m-%d")
        result = "胜" if record["won"] else "负"
        change = "得到" if record["won"] else "失去"
        lines.append(
            f"#{record['id']} {day} {result} vs {display_name(record['opponent'])} · "
            f"我方「{record['my_pig']}」Lv{record['my_level']} vs "
            f"对方「{record['their_pig']}」Lv{record['their_level']} · "
            f"{change}「{record['prize']}」"
        )
    blocks = [
        f"共 {total} 场，胜 {wins} 负 {total - wins}，胜率 {_rate(wins, total)}",
        lines,
        "发送「斗猪回放 编号」查看完整战报",
    ]
    if history["pages"] > 1:
        blocks.append(f"第 {history['page']}/{history['pages']} 页，发送「斗猪记录 页码」翻页")
    return text_message(settings, f"{display_name(user)} 的斗猪记录", blocks)


def duel_replay_message(settings: Settings, record: dict) -> Message:
    players = record["players"]
    a, b = players[record["a_user"]], players[record["b_user"]]
    winner = players[record["winner"]]
    day = datetime.fromtimestamp(record["fought_at"], EAST_ASIA).strftime("%Y-%m-%d %H:%M")
    return text_message(
        settings,
        f"斗猪回放 #{record['id']}：{display_name(a)}的{record['a_pig_name']} "
        f"Lv{record['a_level']} VS {display_name(b)}的{record['b_pig_name']} "
        f"Lv{record['b_level']}",
        [
            day,
            record["log"],
            f"{display_name(winner)} 获胜，赢走了「{record['prize']}」。",
        ],
    )


def _request_line(request: dict, incoming: bool) -> str:
    other = display_name(request["from"] if incoming else request["to"])
    minutes = max(1, math.ceil((request["expires_at"] - time.time()) / 60))
    if request["kind"] == "duel":
        text = (
            f"斗猪：{other} 出战「{request['give']['name']}」"
            if incoming
            else (f"斗猪：你用「{request['give']['name']}」挑战 {other}")
        )
    elif incoming:
        text = f"交换：{other} 用「{request['give']['name']}」换你的「{request['want']['name']}」"
    else:
        text = f"交换：你用「{request['give']['name']}」换 {other} 的「{request['want']['name']}」"
    return f"{text}（约 {minutes} 分钟后过期）"


def requests_message(settings: Settings, requests: dict) -> Message:
    blocks = []
    if requests["incoming"]:
        blocks += ["收到的请求", [_request_line(r, True) for r in requests["incoming"]]]
    if requests["outgoing"]:
        blocks += ["发出的请求", [_request_line(r, False) for r in requests["outgoing"]]]
    if not blocks:
        blocks = ["你在本群没有待处理的请求。"]
    return text_message(settings, "我的请求", blocks)


def cancelled_message(settings: Settings, cancelled: list[dict]) -> Message:
    return text_message(
        settings,
        f"已撤回 {len(cancelled)} 个请求",
        [[_request_line(r, False).rsplit("（", 1)[0] for r in cancelled]],
    )


def card_message(settings, user, card, title, command, page=1, pages=1):
    hosted = settings.use_host(command)
    return Message(
        f"![{title} #{card.width}px #{card.height}px]({{{{image:0}}}})" if hosted else "",
        (card.data,),
        keyboard(settings, user["open_id"], title, page, pages) if hosted else None,
        local=not hosted,
    )
