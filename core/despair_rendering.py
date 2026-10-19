"""Static cards for the five-phase, eight-pig despair encounter."""

from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image

from .rendering import (
    ACCENT,
    BORDER,
    PANEL,
    SUB,
    TEXT,
    WIDTH,
    Canvas,
    Card,
    _hp_bar,
    _paste_art,
    finish,
)

PHASE_NAMES = ("王座重压", "七灾回响", "血月同命", "破冠攻坚", "万相归一")
# A combined report only performs one QQ upload, so it can spend substantially
# more vertical space on each phase than the old multi-message version.
MAX_PHASE_LOG_LINES = 320
MAX_FINAL_LOG_LINES = 280
PHASE_CARD_GAP = 24


def stack_despair_phase_cards(cards: list[Card]) -> Card:
    """Join every reached phase into one image so QQ only receives one reply."""
    if not cards:
        raise ValueError("至少需要一张绝境战阶段图片")
    if len(cards) == 1:
        return cards[0]

    images = []
    try:
        for card in cards:
            with Image.open(io.BytesIO(card.data)) as source:
                images.append(source.convert("RGB"))
        width = max(image.width for image in images)
        height = sum(image.height for image in images) + PHASE_CARD_GAP * (len(images) - 1)
        combined = Image.new("RGB", (width, height), PANEL)
        y = 0
        for image in images:
            combined.paste(image, ((width - image.width) // 2, y))
            y += image.height + PHASE_CARD_GAP
        return finish(combined)
    finally:
        for image in images:
            image.close()


def _spread(entries: list[tuple[int, list[str], str]]) -> list[tuple[int, list[str], str]]:
    """Visit a long list from across its full timeline instead of only its opener."""
    if len(entries) < 3:
        return entries
    middle = entries[1:-1]
    return [entries[0], entries[-1], *middle[::2], *middle[1::2]]


def _phase_log_lines(measure: Canvas, report: dict, limit: int):
    entries = []
    for index, source in enumerate(report.get("log") or []):
        source = str(source)
        entries.append((index, measure.wrap(source, 18, 920), source))
    if sum(len(lines) for _, lines, _ in entries) <= limit:
        return [(line, source) for _, lines, source in entries for line in lines]

    # Keep each phase compact enough to become one readable section in the final
    # long image. Preserve a chronological cross-section of real skill effects
    # and important events while folding repeated basic/status ticks.
    notice = (
        f"本阶段共有 {len(entries)} 条记录；Boss 每次技能、机制及其前后记录完整保留，"
        "其余重复行动按时间线折叠。"
    )
    notice_lines = measure.wrap(notice, 18, 920)
    budget = max(1, limit - len(notice_lines))
    special_words = (
        "极限技",
        "预警",
        "阶段",
        "韧性击破",
        "倒下",
        "击败",
        "复活",
        "净化",
        "护盾",
        "裁定印",
        "王冠",
        "生命共鸣",
    )
    specials = [
        item
        for item in entries
        if any(actor in item[2] for actor in ("万相猪皇施放", "万相猪皇发动"))
        or any(word in item[2] for word in special_words)
    ]

    # A Boss cast is only understandable with the state immediately leading into
    # it and the reactions immediately following it. Keep two records on either
    # side of every cast, warning, phase mechanic and break mechanic. Required
    # context is allowed to exceed the ordinary line target; correctness is more
    # important than shaving a few thousand pixels from the one-shot long image.
    boss_context_words = (
        "万相猪皇施放",
        "万相猪皇发动",
        "万相猪皇预警",
        "万相猪皇由第",
        "阶段机制",
        "阶段转换",
        "韧性击破",
        "崩裂王冠",
        "裁定印",
        "生命共鸣",
    )
    required_indices = set()
    for position, item in enumerate(entries):
        if any(word in item[2] for word in boss_context_words):
            required_indices.update(
                range(max(0, position - 2), min(len(entries), position + 3))
            )
    required = [entries[position] for position in sorted(required_indices)]

    # Keep two fully explained player actions from every reached round. Together
    # with every selected Boss action this makes the compressed timeline much
    # easier to follow without letting routine ticks dominate the long image.
    round_actions = []
    round_action_counts = {}
    for item in entries:
        match = re.match(r"R(\d+) ", item[2])
        if (
            match
            and round_action_counts.get(match.group(1), 0) < 2
            and "万相猪皇" not in item[2]
            and any(word in item[2] for word in ("施放", "发动", "触发"))
        ):
            round_action_counts[match.group(1)] = round_action_counts.get(match.group(1), 0) + 1
            round_actions.append(item)

    chosen: dict[int, tuple[int, list[str], str]] = {}

    def take_required(candidates):
        nonlocal budget
        for item in candidates:
            if item[0] in chosen:
                continue
            chosen[item[0]] = item
            budget -= len(item[1])

    def take(candidates):
        nonlocal budget
        for item in _spread(candidates):
            if item[0] in chosen:
                continue
            cost = len(item[1])
            if cost <= budget:
                chosen[item[0]] = item
                budget -= cost

    take_required(required)
    take(specials)
    take(round_actions)
    take(entries)
    selected = [chosen[index] for index in sorted(chosen)]
    return [
        *((line, notice) for line in notice_lines),
        *((line, source) for _, lines, source in selected for line in lines),
    ]


def _defeat_log_lines(measure: Canvas, report: dict, limit: int = 18):
    """Show the final cause plus one explicit record for every fallen pig."""
    log = [str(entry) for entry in report.get("log") or []]
    deaths = [entry for entry in log if " 倒下" in entry][-8:]
    lethal = [entry for entry in log if "并倒下" in entry or "被持续伤害击倒" in entry]
    actions = [
        entry
        for entry in log
        if "万相猪皇施放" in entry or "万相猪皇发动" in entry
    ]
    cause = lethal[-1:] or actions[-1:]
    entries = [*cause, *deaths]
    lines = []
    for entry in entries:
        for line in measure.wrap(entry, 18, 920):
            if len(lines) >= limit:
                return lines
            lines.append((line, entry))
    return lines


def render_despair_phase(root: Path, data: dict):
    """Render one complete section for the encounter's combined long report."""
    report = data.get("report") or {}
    party = list(data.get("party") or [])
    overall = list(data.get("overall_stats") or []) if data.get("final") else []
    rewards = list(data.get("rewards") or []) if data.get("final") else []
    measure = Canvas(root, WIDTH, 1)
    mechanic_lines = []
    for mechanic in report.get("mechanics") or []:
        name = mechanic.get("name", "阶段机制")
        description = mechanic.get("text", "")
        # The first line is bold like the regular raid mechanic list, so wrap
        # against a narrower measure to leave room for the bullet and heavier face.
        for index, line in enumerate(measure.wrap(f"{name}：{description}", 18, 840)):
            mechanic_lines.append((line, index == 0))
    log_lines = _phase_log_lines(
        measure,
        report,
        MAX_FINAL_LOG_LINES if data.get("final") else MAX_PHASE_LOG_LINES,
    )
    reward_lines = [line for entry in rewards for line in measure.wrap(str(entry), 19, 920)]
    defeat_lines = (
        _defeat_log_lines(measure, report)
        if data.get("final") and not data.get("won") and not data.get("timeout")
        else []
    )
    defeat_count = sum(" 倒下" in str(entry) for entry in report.get("log") or [])
    measure.image.close()

    party_h = 4 * 126
    mechanics_h = 70 + len(mechanic_lines) * 29 if mechanic_lines else 0
    log_h = max(1, len(log_lines)) * 29
    overall_h = (84 + max(1, len(overall)) * 38) if data.get("final") else 0
    reward_h = (75 + max(1, len(reward_lines)) * 33) if data.get("final") else 0
    defeat_h = 70 + len(defeat_lines) * 29 if defeat_lines else 0
    party_top = 410 + mechanics_h
    log_top = party_top + 65 + party_h
    height = log_top + 70 + log_h + defeat_h + overall_h + reward_h + 70
    canvas = Canvas(root, WIDTH, height)
    phase = int(report.get("phase", 1))
    canvas.text("PIGGY  /  LAST STAND", 58, 34, 18, ACCENT, True)
    canvas.text(
        f"第 {phase}/5 阶段 · {report.get('name', PHASE_NAMES[phase - 1])}", 54, 76, 48, bold=True
    )
    start_round = report.get("start_round", 1)
    end_round = report.get("end_round", start_round)
    if data.get("final"):
        if data.get("won"):
            status = "挑战成功 · 万相猪皇已被击败"
        else:
            reason = "达到回合上限" if data.get("timeout") else "全队倒下"
            status = f"挑战失败 · 止步第 {phase}/5 阶段 · {reason}"
    else:
        status = "阶段结算"
    canvas.text(
        f"总回合 R{start_round}–R{end_round} · {status}",
        58,
        144,
        22,
        ACCENT if data.get("final") else SUB,
        bool(data.get("final")),
        width=950,
    )

    canvas.draw.rounded_rectangle(
        (56, 194, 1024, 374), radius=28, fill="#ffffff", outline=BORDER, width=2
    )
    _paste_art(canvas, root, data.get("boss_asset", ""), (76, 207, 160, 154))
    canvas.text("万相猪皇", 265, 218, 30, ACCENT, True)
    canvas.text("唯一 Boss · 五相轮转", 265, 260, 20, SUB)
    maximum = max(1, (data.get("boss") or {}).get("max_hp", 1))
    start_hp = max(0, report.get("boss_hp_start", maximum))
    end_hp = max(0, report.get("boss_hp_end", start_hp))
    _hp_bar(canvas, 265, 315, 515, end_hp, maximum, ACCENT if end_hp else SUB)
    canvas.text(f"{start_hp} → {end_hp} / {maximum}", 800, 303, 17, SUB, width=210)

    if mechanic_lines:
        canvas.text("阶段机制", 58, 410, 27, ACCENT, True)
        for index, (line, first) in enumerate(mechanic_lines):
            canvas.text(
                ("· " if first else "  ") + line,
                70,
                457 + index * 29,
                18,
                TEXT,
                first,
                width=930,
            )

    canvas.text("本阶段队伍状态", 58, party_top, 27, bold=True)
    for index, side in enumerate(party):
        row, column = divmod(index, 2)
        x, y = 56 + column * 492, party_top + 48 + row * 126
        alive = side.get("alive", True)
        canvas.draw.rounded_rectangle(
            (x, y, x + 472, y + 108),
            radius=18,
            fill="#ffffff" if alive else "#eee9e3",
            outline=BORDER,
            width=1,
        )
        _paste_art(canvas, root, side.get("asset", ""), (x + 10, y + 8, 88, 88), faded=not alive)
        canvas.text(side.get("pig", "小猪"), x + 110, y + 10, 20, TEXT, True, width=180)
        state = "存活" if alive else "已倒下"
        canvas.text(
            f"{side.get('owner', '玩家')} · {side.get('role', '输出')} · Lv{side.get('level', 0)} · {state}",
            x + 110,
            y + 40,
            16,
            SUB,
            width=345,
        )
        _hp_bar(
            canvas,
            x + 110,
            y + 73,
            180,
            side.get("hp", 0),
            side.get("max_hp", 1),
            ACCENT if alive else SUB,
        )
        canvas.text(
            f"HP {side.get('hp', 0)}/{side.get('max_hp', 1)}",
            x + 302,
            y + 58,
            14,
            SUB,
            width=158,
        )
        canvas.text(
            f"伤{side.get('dealt', 0)} 治{side.get('healed', 0)} "
            f"承{side.get('taken', 0)} 韧{side.get('poise', 0)}",
            x + 302,
            y + 81,
            13,
            SUB,
            width=158,
        )

    canvas.text("完整战斗细节", 58, log_top, 27, bold=True)
    for index, item in enumerate(log_lines or [("本阶段没有额外战斗记录", "")]):
        line, source = item
        boss_action = any(
            key in source for key in ("万相猪皇施放", "万相猪皇发动", "万相猪皇预警")
        )
        highlight = any(key in source for key in ("阶段", "极限技", "倒下", "击破", "击败"))
        canvas.text(
            line,
            70,
            log_top + 47 + index * 29,
            18,
            ACCENT if highlight else TEXT if boss_action else SUB,
            highlight or boss_action,
            width=930,
        )

    footer_top = log_top + 70 + log_h
    if data.get("final"):
        if defeat_lines:
            canvas.draw.line((56, footer_top, 1024, footer_top), fill=BORDER, width=2)
            canvas.text(
                f"灭队过程 · {defeat_count} 只猪死亡记录",
                58,
                footer_top + 20,
                27,
                ACCENT,
                True,
            )
            for index, (line, _) in enumerate(defeat_lines):
                canvas.text(line, 70, footer_top + 65 + index * 29, 18, TEXT, True, width=930)
            footer_top += defeat_h
        canvas.draw.line((56, footer_top, 1024, footer_top), fill=BORDER, width=2)
        canvas.text("全场输出统计", 58, footer_top + 20, 27, bold=True)
        canvas.text("伤害", 460, footer_top + 27, 16, SUB, True)
        canvas.text("治疗", 610, footer_top + 27, 16, SUB, True)
        canvas.text("承伤", 750, footer_top + 27, 16, SUB, True)
        canvas.text("削韧", 890, footer_top + 27, 16, SUB, True)
        for index, row in enumerate(overall):
            y = footer_top + 68 + index * 38
            canvas.text(
                f"{index + 1}. {row.get('owner', '')} · {row.get('pig', '')}",
                70,
                y,
                17,
                TEXT,
                index < 3,
                width=360,
            )
            canvas.text(str(row.get("dealt", 0)), 460, y, 17, ACCENT, True, width=125)
            canvas.text(str(row.get("healed", 0)), 610, y, 17, TEXT, width=115)
            canvas.text(str(row.get("taken", 0)), 750, y, 17, TEXT, width=115)
            canvas.text(str(row.get("poise", 0)), 890, y, 17, TEXT, width=115)
        footer_top += overall_h
        canvas.draw.line((56, footer_top, 1024, footer_top), fill=BORDER, width=2)
        outcome = "挑战成功" if data.get("won") else f"挑战失败 · 止步第 {phase}/5 阶段"
        canvas.text(outcome, 58, footer_top + 20, 27, ACCENT, True)
        for index, line in enumerate(reward_lines or ["本次没有获得奖励"]):
            canvas.text(line, 70, footer_top + 65 + index * 33, 19, TEXT, width=930)
        footer_top += reward_h
    canvas.draw.line((56, height - 75, 1024, height - 75), fill=BORDER, width=2)
    canvas.text(data.get("footer", "绝境战阶段战报"), 58, height - 53, 18, ACCENT, width=950)
    return finish(canvas.image)


def render_despair_poster(root: Path, data: dict):
    """Render one compact report from the persisted battle result."""
    log = list(data.get("log") or [])
    # The complete log remains in the database.  A bounded poster stays below
    # practical image limits even when eight heroes chain reactions for 20 rounds.
    if len(log) > 56:
        log = [*log[:24], f"……省略 {len(log) - 48} 条普通战斗记录……", *log[-24:]]
    stats = list(data.get("stats") or [])
    rewards = list(data.get("rewards") or [])
    phases = list(data.get("phases") or [])
    log_h = max(1, len(log)) * 31
    reward_h = max(1, len(rewards)) * 34
    height = (
        390 + 570 + 105 + len(stats) * 48 + 90 + len(phases) * 38 + 90 + log_h + 95 + reward_h + 115
    )
    canvas = Canvas(root, WIDTH, height)
    canvas.text("PIGGY  /  LAST STAND", 58, 34, 18, ACCENT, True)
    canvas.text("绝境战 · 万相猪皇", 54, 76, 52, bold=True, width=960)
    canvas.text(
        data.get("subtitle", "八猪协同 · 单 Boss 五阶段连续战斗"), 58, 150, 23, SUB, width=950
    )

    boss = data.get("boss") or {}
    canvas.draw.rounded_rectangle(
        (56, 210, 1024, 360), radius=28, fill="#ffffff", outline=BORDER, width=2
    )
    canvas.draw.ellipse((82, 232, 190, 340), fill=ACCENT if not data.get("won") else SUB)
    canvas.text("万相", 105, 251, 25, "#ffffff", True)
    canvas.text("猪皇", 105, 286, 25, "#ffffff", True)
    canvas.text("唯一 Boss · 五相轮转", 220, 230, 25, ACCENT, True)
    canvas.text(
        f"第 {data.get('phase', 1)}/5 阶段 · {data.get('rounds', 0)} 回合",
        220,
        270,
        21,
        SUB,
    )
    maximum = max(1, boss.get("max_hp", 1))
    current = max(0, boss.get("hp", 0))
    _hp_bar(canvas, 220, 318, 650, current, maximum, SUB if data.get("won") else ACCENT)
    canvas.text(f"{current}/{maximum}", 888, 307, 19, SUB, width=120)

    # Five equal phase gates make progress readable even when the boss is defeated.
    phase_y = 382
    for index, name in enumerate(PHASE_NAMES, 1):
        x = 56 + (index - 1) * 194
        reached = index <= data.get("phase", 1) or data.get("won")
        canvas.draw.rounded_rectangle(
            (x, phase_y, x + 184, phase_y + 66),
            radius=17,
            fill=ACCENT if reached else PANEL,
        )
        canvas.text(
            f"{index}  {name}",
            x + 14,
            phase_y + 17,
            18,
            "#ffffff" if reached else SUB,
            True,
            width=158,
        )

    party_top = 478
    canvas.text("八猪出战阵容", 58, party_top, 28, bold=True)
    for index, side in enumerate(data.get("party") or []):
        row, column = divmod(index, 4)
        x, y = 56 + column * 242, party_top + 54 + row * 250
        alive = side.get("alive", True)
        canvas.draw.rounded_rectangle((x, y + 3, x + 226, y + 232), radius=22, fill=BORDER)
        canvas.draw.rounded_rectangle(
            (x, y, x + 226, y + 229), radius=22, fill="#ffffff" if alive else "#eee9e3"
        )
        _paste_art(canvas, root, side.get("asset", ""), (x + 14, y + 12, 88, 88), faded=not alive)
        canvas.text(side.get("pig", "小猪"), x + 112, y + 15, 21, TEXT, True, width=98)
        canvas.text(side.get("role", "输出"), x + 112, y + 48, 17, ACCENT if alive else SUB, True)
        canvas.text(side.get("owner", "玩家"), x + 14, y + 110, 17, SUB, width=198)
        info = " · ".join(
            part for part in (f"Lv{side.get('level', 0)}", side.get("style", "")) if part
        )
        canvas.text(info, x + 14, y + 139, 16, SUB, width=198)
        if side.get("max_hp"):
            _hp_bar(
                canvas,
                x + 14,
                y + 181,
                198,
                side.get("hp", 0),
                side["max_hp"],
                ACCENT if alive else SUB,
            )
            canvas.text(
                f"{side.get('hp', 0)}/{side['max_hp']}  {'存活' if alive else '倒下'}",
                x + 14,
                y + 197,
                15,
                SUB,
                width=198,
            )

    stats_top = party_top + 570
    canvas.text("战斗统计", 58, stats_top, 28, bold=True)
    canvas.text("总伤害 · 有效治疗 · 承受伤害 · 削韧", 200, stats_top + 7, 19, SUB)
    best = max((row.get("dealt", 0) for row in stats), default=1) or 1
    for index, row in enumerate(stats):
        y = stats_top + 52 + index * 48
        canvas.text(
            f"{row.get('owner', '')}·{row.get('pig', '')}", 70, y, 18, TEXT, index == 0, width=275
        )
        _hp_bar(canvas, 350, y + 8, 190, row.get("dealt", 0), best, ACCENT if index == 0 else SUB)
        canvas.text(
            f"伤 {row.get('dealt', 0)}  治 {row.get('healed', 0)}  承 {row.get('taken', 0)}  韧 {row.get('poise', 0):g}",
            560,
            y,
            18,
            TEXT,
            index == 0,
            width=455,
        )

    phase_top = stats_top + 70 + len(stats) * 48
    canvas.text("阶段记录", 58, phase_top, 28, bold=True)
    if phases:
        for index, phase in enumerate(phases):
            if isinstance(phase, dict):
                line = phase.get("text") or phase.get("name", "")
            else:
                line = str(phase)
            canvas.text(
                f"{index + 1}. {line}", 70, phase_top + 52 + index * 38, 21, TEXT, width=940
            )
    else:
        canvas.text("尚未留下阶段记录", 70, phase_top + 52, 21, SUB)

    log_top = phase_top + 75 + max(1, len(phases)) * 38
    canvas.text("战斗过程", 58, log_top, 28, bold=True)
    for index, entry in enumerate(log or ["没有战斗记录"]):
        color = ACCENT if any(key in entry for key in ("阶段", "极限技", "倒下", "击败")) else SUB
        canvas.text(entry, 70, log_top + 52 + index * 31, 18, color, color == ACCENT, width=940)

    reward_top = log_top + 75 + max(1, len(log)) * 31
    canvas.draw.line((56, reward_top, 1024, reward_top), fill=BORDER, width=2)
    canvas.text("结算", 58, reward_top + 20, 28, bold=True)
    for index, line in enumerate(rewards or ["本次没有获得奖励"]):
        canvas.text(line, 70, reward_top + 70 + index * 34, 20, TEXT, width=940)
    footer = height - 76
    canvas.draw.line((56, footer, 1024, footer), fill=BORDER, width=2)
    canvas.text(
        data.get("footer", "奖励每周领取一次 · 已领奖玩家仍可助战"),
        58,
        footer + 22,
        19,
        ACCENT,
        width=960,
    )
    return finish(canvas.image)
