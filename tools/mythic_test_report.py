#!/usr/bin/env python3
"""Generate reproducible Markdown and PNG test reports for 小猪大秘境."""

import argparse
import asyncio
import tempfile
from pathlib import Path

from core.catalog import initialize_battle, initialize_catalog, read_catalog
from core.database import Database
from core.mythic_views import render_mythic_report

PROJECT = Path(__file__).resolve().parents[1]


def markdown(report: dict, seed: int) -> str:
    state = "限时完成" if report["timed"] else ("超时完成" if report["completed"] else "失败")
    lines = [
        "# 小猪大秘境测试战报",
        "",
        f"- 副本：{report['dungeon']['name']} +{report['key_level']}",
        f"- 随机种子：`{seed}`",
        "- 测试队伍：四职业 Lv10 神话小猪",
        f"- 结果：{state}",
        f"- 总回合：{report['rounds']} / {report['limit']}",
        f"- 小怪进度：{report['forces']}%",
        f"- 随机 ADD：{report['add_count']} 次（整趟 30% 概率；命中后最多 1–2 次）",
        f"- 本层词缀：{'、'.join(affix['name'] for affix in report.get('affixes', [])) or '无'}",
        f"- 低层祝福：{report['blessing']['description'] if report.get('blessing') else '未启用'}",
        f"- 钥石变化：{report['upgrade']:+d}",
        f"- 队伍得分：{report['score']}",
        "",
        "## 路线结果",
        "",
        "| 阶段 | 类型 | 敌人 | 回合 | ADD | 结果 |",
        "|---:|---|---|---:|---:|---|",
    ]
    for phase in report["phases"]:
        kind = {"trash": "小怪", "add": "ADD 小怪"}.get(phase["kind"], f"Boss {phase['stage']}")
        lines.append(
            f"| {phase['index']} | {kind} | {phase['name']} | {phase['rounds']} | "
            f"{len(phase['adds'])} | {'胜利' if phase['won'] else '失败'} |"
        )
    lines += ["", "## 随机 ADD 归因", ""]
    add_number = 0
    for phase in report["phases"]:
        for event in phase["adds"]:
            add_number += 1
            lines.append(
                f"{add_number}. 阶段 {phase['index']} R{event['round']}："
                f"{event['trigger']} 触发「{event['name']}」，小怪生命 {event['added_hp']}，"
                f"形态 {'Boss 战增援' if event.get('mode') == 'boss' else '额外小怪波次'}。"
            )
    if not add_number:
        lines.append("本次随机运行没有触发 ADD。")
    lines += ["", "## 队伍统计", "", "| 玩家 | 小猪 | 职业 | 等级 | 伤害 | 治疗 | 承伤 | 倒地 |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for member in report["members"]:
        user = member["user"]
        name = user.get("alias") or user.get("nickname") or str(user["id"])
        lines.append(
            f"| {name} | {member['pig']['name']} | {member['role']} | {member['level']} | "
            f"{member['dealt']} | {member['healed']} | {member['taken']} | {member['deaths']} |"
        )
    lines += [
        "",
        "## 验证结论",
        "",
        "- 六组主题小怪和三名 Boss 均进入同一总回合计时；每组至少包含坦克、输出、治疗。",
        "- 小怪进度达到 100% 后才算完成。",
        "- 每次 ADD 均记录实际触发玩家、小猪、阶段和回合。",
        "- 所有常规小怪均来自猪圈；ADD 也只从当前副本的小怪池抽取。",
        "- 整趟先进行一次 30% ADD 判定，命中后最多出现 1–2 次。",
        "- 测试模式不写入钥石、奖励或排行榜。",
        "",
    ]
    return "\n".join(lines)


async def generate(output: Path, image_output: Path, dungeon_key: int, key_level: int, seed: int):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        initialize_catalog(root, PROJECT / "resources")
        initialize_battle(root, PROJECT / "resources")
        db = Database(root)
        await db.initialize()
        await db.catalog(read_catalog(root))
        user = await db.identify("test", "admin", "test", "测试管理员")
        report = await db.test_mythic(
            user,
            dungeon_key=dungeon_key,
            key_level=key_level,
            level=10,
            seed=seed,
        )
        card = await asyncio.to_thread(render_mythic_report, root, report, test=True)
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, markdown(report, seed), "utf-8")
    await asyncio.to_thread(image_output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(image_output.write_bytes, card.data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/mythic_test_report.md"))
    parser.add_argument("--image-output", type=Path, default=Path("docs/mythic_test_report.png"))
    parser.add_argument("--dungeon", type=int, default=3)
    parser.add_argument("--level", type=int, default=2)
    parser.add_argument("--seed", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(generate(args.output, args.image_output, args.dungeon, args.level, args.seed))


if __name__ == "__main__":
    main()
