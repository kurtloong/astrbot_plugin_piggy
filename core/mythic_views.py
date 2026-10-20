"""Player-facing status text and detailed image reports for 小猪大秘境."""

import math
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .delivery import Message
from .rendering import (
    ACCENT,
    BORDER,
    PANEL,
    SUB,
    TEXT,
    Canvas,
    _hp_bar,
    _paste_art,
    finish,
)
from .views import card_message, text_message

MAX_REPORT_BYTES = 10 * 1024 * 1024


def _mythic_text(settings: Settings, *args, **kwargs) -> Message:
    """Hosted mythic reports already require Markdown, so expose their lobby buttons too."""
    if settings.use_host("raid") and not settings.battle_markdown:
        settings = replace(settings, battle_markdown=True)
    return text_message(settings, *args, **kwargs)


def _name(user: dict) -> str:
    return user.get("alias") or user.get("nickname") or f"玩家 {user['id']:04d}"


def mythic_status_message(settings: Settings, user: dict, status: dict) -> Message:
    if not status["unlocked"]:
        return _mythic_text(
            settings,
            "小猪大秘境尚未解锁",
            ["还需完整通关：" + "、".join(status["missing"]) + "。"],
            buttons=[("猪副本", "猪副本")],
        )
    key = status["key"]
    from .raid import dungeon

    blocks = [
        f"本周：{status['week']}",
        (
            f"你的钥石：{dungeon(key['dungeon'])['name']} +{key['level']}"
            if key
            else "你的钥石：尚未领取（发送「领取钥匙」获得初始 +2 钥石）"
        ),
        f"钥石保底层：+{status.get('key_floor', 2)}",
        "规则：四人各带一只 Lv5+ 小猪，六组小怪与三名 Boss 连战，以总回合限时。",
        "每趟有 30% 概率出现随机 ADD；出现时整趟仅 1–2 次，战报会注明触发小猪。",
        "词缀：+2 起启用强韧/残暴，+7 增加火山，+12 增加重伤。",
        "全队均为 Lv5–9 时，+2/+3/+4 获得逐层衰减的新锐祝福，+5 起失效。",
    ]
    claimed = {reward["tier"] for reward in status.get("weekly_rewards", [])}
    milestones = status.get("weekly_milestones", [])
    if milestones:
        progress = " · ".join(
            f"+{item['key_level']} {item['name']}"
            f"（{'已领' if item['id'] in claimed else '未领'}）"
            for item in milestones
        )
        blocks.append(f"本周限时层数奖励：{progress}")
    blocks.append("每次限时必得自己的参战猪；+7/+12/+17 另得 1/2/3 次当天再抽。")
    raid = status.get("raid")
    if raid:
        blocks += [
            f"本群组队：{raid['dungeon']['name']} +{raid['key_level']} "
            f"{len(raid['members'])}/4",
            [
                f"{index}. {_name(member['user'])} · {member['pig']['name']}"
                f"（{member['pig']['role']}）"
                for index, member in enumerate(raid["members"], 1)
            ],
        ]
        buttons = [("加入大秘境", "加入大秘境 "), ("退出大秘境", "退出大秘境")]
        hint = "点击“加入大秘境”并补上你的小猪名称，或发送「加入大秘境 你的小猪」。"
    else:
        if key:
            blocks.append("发送「开启大秘境 你的小猪」使用钥石组队。")
            buttons = [
                ("开启大秘境", "开启大秘境 "),
                ("查询钥匙", "查询钥匙"),
                ("降级钥匙", "降级钥匙"),
            ]
            hint = "发送「开启大秘境 你的小猪」开始组队。"
        else:
            blocks.append("领取钥石后即可开启大秘境组队。")
            buttons = [("领取钥匙", "领取钥匙"), ("查询钥匙", "查询钥匙")]
            hint = "发送「领取钥匙」获得初始钥石。"
    return _mythic_text(
        settings,
        "小猪大秘境",
        blocks,
        buttons=buttons,
        plain_hint=hint,
    )


def mythic_key_message(settings: Settings, user: dict, status: dict) -> Message:
    if not status["unlocked"]:
        return mythic_status_message(settings, user, status)
    key = status.get("key")
    if not key:
        return _mythic_text(
            settings,
            "我的大秘境钥石",
            ["目前没有钥石，可领取一枚随机副本 +2 钥石。"],
            buttons=[("领取钥匙", "领取钥匙"), ("小猪大秘境", "小猪大秘境")],
            plain_hint="发送「领取钥匙」领取初始钥石。",
        )
    from .raid import DUNGEONS, dungeon

    progress = status.get("key_progress", {})
    blocks = [
        f"当前：{dungeon(key['dungeon'])['name']} +{key['level']}",
        f"保底：+{status.get('key_floor', 2)}",
        [
            f"{item['key']}. {item['name']}："
            f"{('+' + str(progress[item['key']])) if item['key'] in progress else '尚未限时'}"
            for item in DUNGEONS
        ],
        "七本的限时最高层都达到同一层后，该层会成为永久降级保底。",
    ]
    return _mythic_text(
        settings,
        "我的大秘境钥石",
        blocks,
        buttons=[("开启大秘境", "开启大秘境 "), ("降级钥匙", "降级钥匙")],
        plain_hint="发送「开启大秘境 你的小猪」使用当前钥石。",
    )


def mythic_key_action_message(settings: Settings, user: dict, result: dict, action: str) -> Message:
    from .raid import dungeon

    key = result["key"]
    label = f"{dungeon(key['dungeon'])['name']} +{key['level']}"
    if action == "claim":
        text = (
            f"已领取大秘境钥石：{label}。" if result["created"] else f"已有钥石：{label}。"
        )
        return _mythic_text(
            settings,
            "大秘境钥石",
            [text],
            buttons=[("开启大秘境", "开启大秘境 "), ("查询钥匙", "查询钥匙")],
            plain_hint="发送「开启大秘境 你的小猪」开始组队。",
        )
    if result["changed"]:
        text = (
            f"钥石已从 +{result['old_level']} 降为 +{key['level']}："
            f"{dungeon(key['dungeon'])['name']}。当前保底为 +{result['floor']}。"
        )
    else:
        text = f"钥石已经处于保底 +{result['floor']}，不能继续降级：{label}。"
    return _mythic_text(
        settings,
        "大秘境钥石",
        [text],
        buttons=[("开启大秘境", "开启大秘境 "), ("查询钥匙", "查询钥匙")],
        plain_hint="发送「开启大秘境 你的小猪」开始组队。",
    )


def mythic_lobby_message(settings: Settings, result: dict, kind: str) -> Message:
    raid = result["raid"]
    if kind == "cancelled":
        return _mythic_text(
            settings,
            f"「{raid['dungeon']['name']} +{raid['key_level']}」队伍已解散",
            ["队长退出，组队已经取消。"],
            buttons=[("小猪大秘境", "小猪大秘境"), ("再次组队", "开启大秘境 ")],
        )
    title = "已退出大秘境队伍" if kind == "leave" else f"【{raid['dungeon']['name']} +{raid['key_level']}】"
    blocks = [
        f"队长：{_name(raid['leader_user'])}",
        [
            f"{index}. {_name(member['user'])} · {member['pig']['name']}"
            f"（{member['pig']['role']}）"
            for index, member in enumerate(raid["members"], 1)
        ],
        f"当前 {len(raid['members'])}/4，满员后自动完成整趟大秘境模拟。",
    ]
    return _mythic_text(
        settings,
        title,
        blocks,
        buttons=[("加入大秘境", "加入大秘境 "), ("退出大秘境", "退出大秘境")],
        plain_hint="发送「加入大秘境 你的小猪」加入。",
    )


def _battle_rows(report: dict) -> list[tuple[str, str]]:
    """Flatten every combat log line into render rows without truncation."""
    rows: list[tuple[str, str]] = []
    for phase in report["phases"]:
        icon = "胜利" if phase["won"] else "失败"
        kind = {"trash": "小怪", "add": "ADD 小怪"}.get(
            phase["kind"], f"Boss {phase['stage']}"
        )
        rows.append(
            ("phase", f"阶段 {phase['index']} · {kind} · {phase['name']} · "
             f"{phase['rounds']} 回合 · {icon}")
        )
        for event in phase["adds"]:
            rows.append(
                ("add", f"R{event['round']}  {event['trigger']} 触发「{event['name']}」 · "
                 f"小怪生命 {event['added_hp']} · "
                 f"{'Boss 战入场' if event.get('mode') == 'boss' else '追加整波小怪'}")
            )
        for line in phase["log"]:
            text = str(line)
            important = text.startswith("【") or any(
                keyword in text
                for keyword in (
                    "倒下了",
                    "死亡",
                    "击杀",
                    "击败",
                    "暴击",
                    "特殊",
                    "场地事件",
                    "随机 ADD",
                    "站了起来",
                    "复活",
                    "团灭",
                )
            )
            rows.append(("important" if important else "log", text))
    return rows


def render_mythic_report(root: Path, battle: dict, *, test: bool = False):
    report = battle.get("report", battle)
    title = "小猪大秘境测试战报" if test else "小猪大秘境战报"
    state = "限时完成" if report["timed"] else ("超时完成" if report["completed"] else "挑战失败")
    rows = _battle_rows(report)
    # Wrap first so the canvas is exactly as tall as the complete log requires.
    probe = Canvas(root, 1080, 100)
    wrapped: list[tuple[str, str]] = []
    for kind, value in rows:
        size = 22 if kind == "phase" else 18
        for line in probe.wrap(value, size, 918 if kind in {"log", "important"} else 936):
            wrapped.append((kind, line))
    probe.image.close()

    reward_lines = []
    if not test and battle.get("next_key"):
        key = battle["next_key"]
        floor = f"（保底 +{battle['key_floor']}）" if "key_floor" in battle else ""
        reward_lines.append(f"队长新钥石：{key['dungeon']['name']} +{key['level']}{floor}")
        for reward in battle.get("rewards", []):
            draw = f"，再抽×{reward['draw']}" if reward["draw"] else ""
            reward_lines.append(
                f"{_name(reward['user'])}：参战猪「{reward['pig']['name']}」×1{draw}"
            )
            for weekly in reward.get("weekly", []):
                if weekly["id"] == "ten-pull":
                    names = [item["name"] for item in weekly["items"]]
                    for start in range(0, len(names), 5):
                        prefix = "  周奖励·十连抽：" if start == 0 else "    "
                        reward_lines.append(prefix + "、".join(names[start : start + 5]))
                else:
                    reward_lines.append(
                        f"  周奖励·{weekly['name']}：「{weekly['items'][0]['name']}」"
                    )

    route_rows = math.ceil(len(report["phases"]) / 3)
    intro_height = 880 + len(reward_lines) * 36 + route_rows * 190
    height = intro_height + 180 + sum(47 if kind == "phase" else 27 for kind, _ in wrapped)
    canvas = Canvas(root, 1080, max(900, height))
    draw = canvas.draw
    canvas.text("PIGGY  /  MYTHIC DUNGEON", 56, 34, 18, ACCENT, True)
    canvas.text(title, 54, 74, 52, TEXT, True)
    canvas.text(f"{report['dungeon']['name']} +{report['key_level']}  ·  {state}", 58, 151, 28, ACCENT, True)
    affix_text = " · ".join(affix["name"] for affix in report.get("affixes", [])) or "无"
    if report.get("blessing"):
        affix_text += " · 新锐祝福"
    canvas.text(f"本层词缀：{affix_text}", 58, 185, 16, SUB, True, 960)
    draw.rounded_rectangle((56, 207, 1024, 360), radius=26, fill=PANEL)
    summary = (
        ("总回合", f"{report['rounds']} / {report['limit']}"),
        ("小怪进度", f"{report['forces']}%"),
        ("随机 ADD", f"{report['add_count']} 次"),
        ("队伍得分", str(report['score'])),
    )
    for index, (label, value) in enumerate(summary):
        x = 86 + index * 235
        canvas.text(label, x, 229, 18, SUB)
        canvas.text(value, x, 270, 31, TEXT, True, 210)
    canvas.text(
        f"战斗 {report['combat_rounds']} 回合 + 倒地罚时 {report['death_penalty']} 回合 · "
        f"钥石结果 {report['upgrade']:+d}", 80, 326, 17, SUB, width=920
    )

    y = 398
    canvas.text("队伍统计", 56, y, 28, TEXT, True)
    canvas.text("形象、状态与全程输出统计", 190, y + 7, 17, SUB)
    party_top = y + 48
    card_w, gap, card_h = 230, 16, 322
    best_damage = max((member["dealt"] for member in report["members"]), default=1) or 1
    total_damage = sum(member["dealt"] for member in report["members"]) or 1
    for index, member in enumerate(report["members"]):
        x = 56 + index * (card_w + gap)
        draw.rounded_rectangle((x, party_top + 3, x + card_w, party_top + card_h + 3), radius=22, fill=BORDER)
        draw.rounded_rectangle((x, party_top, x + card_w, party_top + card_h), radius=22, fill="#ffffff")
        draw.rounded_rectangle((x + 12, party_top + 12, x + card_w - 12, party_top + 133), radius=16, fill="#f9f4ed")
        _paste_art(canvas, root, member["pig"].get("asset", ""), (x + 24, party_top + 17, card_w - 48, 111))
        canvas.text(_name(member["user"]), x + 14, party_top + 145, 16, SUB, width=card_w - 28)
        canvas.text(member["pig"]["name"], x + 14, party_top + 171, 22, TEXT, True, card_w - 28)
        info = " · ".join(
            part for part in (f"Lv{member['level']}", member.get("role", ""), member.get("style", "")) if part
        )
        canvas.text(info, x + 14, party_top + 204, 15, ACCENT, True, card_w - 28)
        _hp_bar(canvas, x + 14, party_top + 232, card_w - 28, member["dealt"], best_damage, ACCENT)
        canvas.text(
            f"伤 {member['dealt']}  治 {member['healed']}",
            x + 14, party_top + 247, 15, TEXT, member["dealt"] == best_damage, card_w - 28,
        )
        canvas.text(
            f"承 {member['taken']}  倒地 {member['deaths']}  末战 {member.get('hp_ratio', 0):.0%}",
            x + 14, party_top + 272, 14, SUB, width=card_w - 28,
        )
        canvas.text(
            f"DPS {member['dealt'] / max(1, report['combat_rounds']):.0f} · 占比 {member['dealt'] / total_damage:.0%} · 最低 {member.get('min_hp_ratio', 0):.0%}",
            x + 14, party_top + 296, 14, ACCENT, member["dealt"] == best_damage, card_w - 28,
        )
    y = party_top + card_h + 30
    if reward_lines:
        canvas.text("钥石与奖励", 56, y, 28, TEXT, True)
        y += 45
        for line in reward_lines:
            canvas.text(line, 72, y, 18, TEXT, width=940)
            y += 36
    y += 14
    canvas.text("副本路线", 56, y, 28, TEXT, True)
    canvas.text("每场敌人均显示猪圈原图；ADD 以独立阶段或 Boss 战增援记录", 190, y + 7, 16, SUB)
    y += 48
    route_w, route_h, route_gap = 310, 166, 19
    for index, phase in enumerate(report["phases"]):
        col, row = index % 3, index // 3
        x, top = 56 + col * (route_w + route_gap), y + row * 190
        draw.rounded_rectangle((x, top + 3, x + route_w, top + route_h + 3), radius=20, fill=BORDER)
        draw.rounded_rectangle((x, top, x + route_w, top + route_h), radius=20, fill="#ffffff")
        draw.rounded_rectangle((x + 10, top + 10, x + 112, top + 156), radius=14, fill="#f9f4ed")
        enemies = phase.get("enemies") or [phase.get("enemy") or {}]
        art_h = max(34, min(42, 118 // max(1, len(enemies))))
        for enemy_index, enemy in enumerate(enemies[:3]):
            _paste_art(
                canvas,
                root,
                enemy.get("asset", ""),
                (x + 19, top + 18 + enemy_index * 42, 84, art_h),
            )
        boss_add = next(
            (event for event in phase["adds"] if event.get("mode") == "boss" and event.get("pigs")),
            None,
        )
        if boss_add:
            draw.rounded_rectangle((x + 62, top + 99, x + 110, top + 153), radius=10, fill="#ffffff", outline=ACCENT, width=2)
            _paste_art(canvas, root, boss_add["pigs"][0].get("asset", ""), (x + 66, top + 104, 40, 38))
            canvas.text("ADD", x + 70, top + 137, 10, ACCENT, True, 34)
        kind = {"trash": "副本小怪", "add": "ADD 波次"}.get(phase["kind"], f"Boss {phase['stage']}")
        canvas.text(f"{phase['index']:02d}  {kind}", x + 126, top + 16, 16, ACCENT, True, route_w - 140)
        canvas.text(phase["name"], x + 126, top + 45, 21, TEXT, True, route_w - 140)
        canvas.text(
            f"{phase['rounds']} 回合 · {'胜利' if phase['won'] else '失败'}",
            x + 126, top + 82, 16, SUB, width=route_w - 140,
        )
        if phase["adds"]:
            add_names = "、".join(event["name"] for event in phase["adds"])
            canvas.text(f"ADD：{add_names}", x + 126, top + 112, 15, ACCENT, True, route_w - 140)
        elif phase["force"]:
            canvas.text(f"小怪进度 +{phase['force']}%", x + 126, top + 112, 15, SUB, width=route_w - 140)
    y += route_rows * 190
    y += 2
    draw.line((56, y, 1024, y), fill=BORDER, width=2)
    y += 26
    canvas.text("详细战斗日志", 56, y, 30, TEXT, True)
    canvas.text(f"共 {sum(len(p['log']) for p in report['phases'])} 条，以下完整呈现", 810, y + 7, 15, SUB)
    y += 54
    for kind, line in wrapped:
        if kind == "phase":
            y += 5
            draw.rounded_rectangle((56, y - 5, 1024, y + 31), radius=9, fill=PANEL)
            canvas.text(line, 70, y, 22, ACCENT, True, 936)
            y += 42
        elif kind == "add":
            canvas.text("ADD  " + line, 70, y, 18, ACCENT, True, 936)
            y += 27
        else:
            canvas.text(
                line,
                82,
                y,
                18,
                ACCENT if kind == "important" else TEXT,
                kind == "important",
                918,
            )
            y += 27
    card = finish(canvas.image)
    if not 0 < len(card.data) <= MAX_REPORT_BYTES:
        raise ValueError("Mythic battle report exceeds the 10 MB image limit")
    return card


def mythic_battle_message(
    settings: Settings, root: Path, battle: dict, *, test: bool = False
) -> Message:
    card = render_mythic_report(root, battle, test=test)
    title = "小猪大秘境测试战报" if test else "小猪大秘境战报"
    owner = battle.get("raid", {}).get("leader_user") or {"open_id": ""}
    return card_message(
        settings,
        owner,
        card,
        title,
        "raid",
        buttons=[("查询钥匙", "查询钥匙"), ("再次组队", "开启大秘境 ")],
    )


def mythic_rankings_message(board: dict, total: bool = False) -> Message:
    rows = board["total" if total else "weekly"]
    title = "七本总分队伍榜" if total else "本周队伍冲层榜"
    lines = [f"【大秘境{title}】", f"周起始：{board['week']}"]
    if not rows:
        lines.append("暂无完成记录。")
        return Message("\n".join(lines))
    for index, item in enumerate(rows, 1):
        if total:
            row = item["row"]
            result = f"{item['score']} 分 · 已完成 {item['dungeons']}/7 本"
        else:
            row = item
            result = (
                f"{row['key_level']} 层 · {row['rounds']}/{row['limit_rounds']} 回合"
                f" · ADD {row['add_count']}"
            )
        team = " / ".join(f"{member['name']}·{member['pig']}" for member in item["team"])
        lines.append(f"{index}. {result}\n   {team}")
    return Message("\n".join(lines))
