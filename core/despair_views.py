"""Messages for the end-game despair lobby and battle settlement."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

from .despair import DESPAIR_BOSS
from .despair_rendering import PHASE_NAMES, render_despair_phase
from .views import card_message, display_name, text_message


def _pair(member: dict) -> str:
    pigs = member.get("pigs") or []
    names = " / ".join(f"{pig['name']}（{pig.get('role', '输出')}）" for pig in pigs)
    return f"{display_name(member['user'])}：{names}"


def despair_status_message(settings, user: dict, status: dict):
    unlocked = bool(status.get("unlocked"))
    cleared = len(status.get("cleared") or ())
    missing = " / ".join(item["name"] for item in status.get("missing") or [])
    reward = status.get("reward")
    blocks = [
        f"永久副本进度：{cleared}/7 · {'已解锁' if unlocked else '尚未解锁'}",
        [
            "四名玩家，每人选择两只不同小猪；全队必须恰好 2 坦克 / 2 输出 / 2 治疗 / 2 辅助",
            "单一 Boss 连续五阶段，失败不扣除小猪，可以重新组队挑战",
            "每周首次通关获得真正十连，并保证一个尚未觉醒的猪种获得性格；全觉醒后改为神话保底",
        ],
        "五阶段：" + " → ".join(PHASE_NAMES),
    ]
    if missing:
        blocks.append(f"还需永久通关：{missing}")
    blocks.append(f"本周奖励：{'已领取，可继续助战' if reward else '尚未领取'}")
    raid = status.get("raid")
    buttons = []
    if raid:
        minutes = max(
            1, math.ceil((raid["expires_at"] - status.get("now", raid["expires_at"])) / 60)
        )
        blocks.append(
            [
                f"当前队伍 {len(raid['members'])}/4 · 约 {minutes} 分钟后过期",
                *[_pair(member) for member in raid["members"]],
                "职业数量："
                + " / ".join(
                    f"{role}{raid.get('roles', {}).get(role, 0)}"
                    for role in ("坦克", "输出", "治疗", "辅助")
                ),
            ]
        )
        buttons = [("加入绝境战", "加入绝境战 "), ("退出绝境战", "退出绝境战")]
    elif unlocked:
        buttons = [("开启绝境战", "开启绝境战 ")]
    return text_message(
        settings,
        "绝境战 · 万相猪皇",
        blocks,
        buttons=buttons,
        plain_hint="发送「开启绝境战 小猪A 小猪B」发起组队" if unlocked and not raid else "",
    )


def despair_lobby_message(settings, result: dict, kind: str):
    raid = result["raid"]
    title = {
        "open": "绝境战开始组队！",
        "join": "已加入绝境战队伍",
        "leave": "已退出绝境战队伍",
        "cancelled": "绝境战队伍已解散",
    }[kind]
    if kind == "cancelled":
        return text_message(settings, title, ["队长退出，组队已经取消。不会扣除小猪或奖励资格。"])
    blocks = [
        f"组队中 {len(raid['members'])}/4",
        [_pair(member) for member in raid["members"]],
        "职业数量："
        + " / ".join(
            f"{role}{raid.get('roles', {}).get(role, 0)}"
            for role in ("坦克", "输出", "治疗", "辅助")
        ),
        "满四人时检查 2坦克 / 2输出 / 2治疗 / 2辅助，并自动进入五阶段战斗。",
    ]
    return text_message(
        settings,
        title,
        blocks,
        buttons=[("加入绝境战", "加入绝境战 "), ("退出绝境战", "退出绝境战")],
    )


def _reward_lines(rewards: list[dict]) -> list[str]:
    lines = []
    for reward in rewards:
        user = reward.get("user") or {}
        name = display_name(user) if user else f"玩家 {reward.get('user_id', '')}"
        items = reward.get("items") or []
        if not items:
            lines.append(f"{name}：本周已领取，作为助战不重复获得奖励")
            continue
        names = [item["pig"]["name"] for item in items]
        lines.append(f"{name}十连 1–5：{'、'.join(names[:5])}")
        lines.append(f"{name}十连 6–10：{'、'.join(names[5:])}")
        nature = reward.get("personality_detail")
        if nature:
            lines.append(f"{name}保底性格：{nature['pig']}【{nature['name']}】— {nature['skill']}")
    return lines


async def despair_battle_message(settings, root: Path, battle: dict):
    result = battle.get("result") or {}
    fighters = battle.get("fighters") or []
    party = [
        {
            "owner": display_name(f["user"]),
            "pig": f["pig"]["name"],
            "asset": f["pig"].get("asset", ""),
            "level": f.get("level", 0),
            "role": f.get("role", "输出"),
            "style": f.get("style", ""),
            "hp": f.get("hp", 0),
            "max_hp": f.get("max_hp", 1),
            "alive": f.get("alive", False),
        }
        for f in fighters
    ]
    rewards = _reward_lines(battle.get("rewards") or [])
    if not rewards:
        rewards = (
            ["测试战不发放奖励，也不会改动收藏、次数或绝境战进度。"]
            if battle.get("test")
            else ["挑战失败，本次不发放奖励；不会扣除出战小猪，可以重新挑战。"]
        )
    overall_stats = sorted(
        [
            {
                "owner": display_name(f["user"]),
                "pig": f["pig"]["name"],
                "dealt": f.get("dealt", 0),
                "healed": f.get("healed", 0),
                "taken": f.get("taken", 0),
                "poise": f.get("poise", 0),
            }
            for f in fighters
        ],
        key=lambda row: (-row["dealt"], -row["healed"], -row["taken"]),
    )
    reports = result.get("phase_reports") or []
    if not reports:
        phase = (result.get("boss") or {}).get("phase") or (5 if battle.get("won") else 1)
        reports = [
            {
                "phase": phase,
                "name": PHASE_NAMES[phase - 1],
                "start_round": 1,
                "end_round": result.get("rounds", 0),
                "boss_hp_start": (result.get("boss") or {}).get("max_hp", 1),
                "boss_hp_end": (result.get("boss") or {}).get("hp", 0),
                "heroes": result.get("heroes") or [],
                "actions": (result.get("boss") or {}).get("actions") or [],
                "log": result.get("log") or [],
            }
        ]
    boss_asset = (battle.get("boss_pig") or {}).get("asset", "")
    cards = []
    for index, report in enumerate(reports[:5]):
        phase_index = max(0, min(4, int(report.get("phase", 1)) - 1))
        if not report.get("mechanics"):
            report = {
                **report,
                "mechanics": DESPAIR_BOSS["phases"][phase_index].get("mechanics", []),
            }
        states = {
            item.get("index", offset): item
            for offset, item in enumerate(report.get("heroes") or [])
        }
        phase_party = [{**side, **states.get(offset, {})} for offset, side in enumerate(party)]
        cards.append(
            await asyncio.to_thread(
                render_despair_phase,
                root,
                {
                    "report": report,
                    "party": phase_party,
                    "boss": result.get("boss") or {},
                    "boss_asset": boss_asset,
                    "final": index == len(reports[:5]) - 1,
                    "overall_stats": overall_stats,
                    "rewards": rewards,
                    "footer": f"绝境战 #{battle.get('id', '')} · {battle.get('week', '')} · "
                    f"{'通关' if battle.get('won') else '挑战失败'}",
                },
            )
        )
    raid = battle.get("raid") or {}
    owner = raid.get("leader_user") or (fighters[0]["user"] if fighters else {"open_id": ""})
    return tuple(
        card_message(
            settings,
            owner,
            card,
            f"绝境战第 {reports[index]['phase']} 阶段战报",
            "raid",
            buttons=[("绝境战", "绝境战"), ("再次组队", "开启绝境战 ")]
            if index == len(cards) - 1
            else (),
        )
        for index, card in enumerate(cards)
    )
