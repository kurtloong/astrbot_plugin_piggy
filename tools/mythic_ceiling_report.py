#!/usr/bin/env python3
"""Measure fixed-difficulty mythic timed ceilings by player level bracket."""

import argparse
import asyncio
import tempfile
from pathlib import Path

from core.catalog import initialize_battle, initialize_catalog, read_catalog
from core.database import Database
from core.raid import DUNGEONS

PROJECT = Path(__file__).resolve().parents[1]
BRACKETS = (("Lv5–9", 5), ("Lv10–14", 10), ("Lv15–19", 15), ("Lv20", 20))


async def measure(seeds: int, max_key: int) -> list[dict]:
    rows = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        initialize_catalog(root, PROJECT / "resources")
        initialize_battle(root, PROJECT / "resources")
        db = Database(root)
        await db.initialize()
        await db.catalog(read_catalog(root))
        user = await db.identify("ceiling", "admin", "ceiling", "层数测试")
        for bracket, level in BRACKETS:
            for dungeon in DUNGEONS:
                rates = {}
                empty = 0
                for key_level in range(2, max_key + 1):
                    timed = 0
                    for seed in range(seeds):
                        report = await db.test_mythic(
                            user,
                            dungeon_key=dungeon["key"],
                            key_level=key_level,
                            level=level,
                            seed=10_000 * level + 100 * dungeon["key"] + seed,
                        )
                        timed += int(report["timed"])
                    rates[key_level] = timed / seeds
                    empty = empty + 1 if timed == 0 else 0
                    if empty >= 3:
                        break
                stable = max((key for key, rate in rates.items() if rate >= 0.5), default=None)
                observed = max((key for key, rate in rates.items() if rate > 0), default=None)
                rows.append(
                    {
                        "bracket": bracket,
                        "level": level,
                        "dungeon": dungeon["name"],
                        "stable": stable,
                        "observed": observed,
                        "stable_rate": rates.get(stable, 0),
                    }
                )
    return rows


def markdown(rows: list[dict], seeds: int) -> str:
    lines = [
        "# 小猪大秘境固定难度层数压测",
        "",
        f"每个等级档采用档位下限、四职业神话小猪；每个副本每层运行 {seeds} 个固定随机种子。",
        "“稳定限时”指该层至少 50% 样本限时；“样本极限”指至少 1 个样本限时。",
        "敌人数值只由副本、层数和固定公式决定，不按队伍等级或测试结果动态补偿。",
        "",
        "| 等级档 | 副本 | 稳定限时最高层 | 该层限时率 | 样本限时最高层 |",
        "|---|---|---:|---:|---:|",
    ]
    for row in rows:
        stable = f"+{row['stable']}" if row["stable"] is not None else "无"
        observed = f"+{row['observed']}" if row["observed"] is not None else "无"
        lines.append(
            f"| {row['bracket']} | {row['dungeon']} | {stable} | "
            f"{row['stable_rate']:.0%} | {observed} |"
        )
    lines += ["", "## 各等级档结论", ""]
    for bracket, _level in BRACKETS:
        group = [row for row in rows if row["bracket"] == bracket]
        stable = [row["stable"] for row in group if row["stable"] is not None]
        observed = [row["observed"] for row in group if row["observed"] is not None]
        conservative = min(stable) if len(stable) == len(group) else "无"
        lines.append(
            f"- **{bracket}**：七本保守稳定层为 "
            f"{('+' + str(conservative)) if conservative != '无' else '无'}；"
            f"该档样本最高达到 +{max(observed) if observed else '无'}。"
        )
    absolute = max((row["observed"] for row in rows if row["observed"] is not None), default=None)
    lines += ["", f"本轮全部样本的最高限时层：+{absolute if absolute is not None else '无'}。", ""]
    return "\n".join(lines)


async def main_async(args):
    rows = await measure(args.seeds, args.max_key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown(rows, args.seeds), "utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--max-key", type=int, default=30)
    parser.add_argument("--output", type=Path, default=Path("docs/mythic_ceiling_report.md"))
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
