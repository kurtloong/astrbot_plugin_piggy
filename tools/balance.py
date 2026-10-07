"""Tune resources/battle.json so pigs of the same level win roughly half their fights.

Usage:
  python tools/balance.py report [--levels 1,3,5]
  python tools/balance.py tune --from-order 96 [--rounds 6]

Each pig fights a fixed random sample of opponents from the whole catalog.
`tune` only adjusts pigs whose sort_order >= --from-order, so already released
pigs keep their numbers. Level-1 win rate moves hp; the skill unlocked at level
N is scaled by its marginal effect on the level-N win rate. Values are clamped
to readable ranges and rounded at the end.
"""

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.battle import fighter, simulate, validate_battle  # noqa: E402

RESOURCES = ROOT / "resources"
OPPONENTS = 60
PCT_CAP = {"heal": 45, "shield": 40, "dot": 12, "thorns": 70, "regen": 6, "revive": 60}


def load():
    pigs = {p["id"]: p for p in json.loads((RESOURCES / "pigs.json").read_text("utf-8"))}
    raw = json.loads((RESOURCES / "battle.json").read_text("utf-8"))
    return pigs, raw


def dump(raw):
    lines = ["{", f'  "version": {raw["version"]},', '  "pigs": {']
    items = list(raw["pigs"].items())
    for index, (pig_id, entry) in enumerate(items):
        stats = entry["stats"]
        lines.append(f"    {json.dumps(pig_id)}: {{")
        lines.append(f'      "style": {json.dumps(entry["style"], ensure_ascii=False)},')
        lines.append(
            '      "stats": {'
            + ", ".join(f'"{k}": {stats[k]}' for k in ("hp", "atk", "def", "spd", "crit", "dodge"))
            + "},"
        )
        lines.append('      "skills": [')
        for slot, skill in enumerate(entry["skills"]):
            text = json.dumps(skill, ensure_ascii=False, separators=(", ", ": "))
            lines.append(f"        {text}" + ("," if slot < 4 else ""))
        lines.append("      ]")
        lines.append("    }" + ("," if index < len(items) - 1 else ""))
    lines += ["  }", "}", ""]
    (RESOURCES / "battle.json").write_text("\n".join(lines), "utf-8")


def rates(pigs, raw, level, seed=7):
    data = validate_battle(raw, pigs)
    ids = sorted(data)
    rng = random.Random(seed)
    wins, games = dict.fromkeys(ids, 0), dict.fromkeys(ids, 0)
    fight_seed = level * 1_000_000
    for pig_id in ids:
        for opponent in rng.sample([i for i in ids if i != pig_id], min(OPPONENTS, len(ids) - 1)):
            fight_seed += 1
            pair = [
                fighter(pigs[p], data[p], level, p)
                for p in ((pig_id, opponent) if fight_seed % 2 else (opponent, pig_id))
            ]
            winner = pair[simulate(*pair, fight_seed)["winner"]]["pig_id"]
            wins[pig_id] += winner == pig_id
            games[pig_id] += 1
    return {pig_id: wins[pig_id] / games[pig_id] for pig_id in ids}


def scale_skill(skill, factor):
    for effect in skill["effects"] + skill.get("fail", {}).get("effects", []):
        kind = effect["type"]
        if kind == "damage":
            hits = effect.get("hits", 1)
            effect["power"] = round(min(3.2 / hits, max(0.35, effect["power"] * factor)), 2)
            if "power_max" in effect:
                effect["power_max"] = round(
                    min(6, max(effect["power"], effect["power_max"] * factor)), 2
                )
        elif kind in PCT_CAP:
            effect["pct"] = max(1, min(PCT_CAP[kind], round(effect["pct"] * factor, 1)))
        elif kind == "buff":
            cap = 40 if effect["stat"] in ("crit", "dodge") else 60
            effect["pct"] = max(-cap, min(cap, round(effect["pct"] * factor))) or (
                1 if factor > 1 else -1
            )
        if kind == "stun" or ("chance" in effect and kind != "damage"):
            chance = effect.get("chance", 100)
            selfish = kind == "stun" and effect.get("target") == "self"
            chance = max(5, min(100, round(chance / factor if selfish else chance * factor)))
            if chance >= 100:
                effect.pop("chance", None)
            else:
                effect["chance"] = chance
    if "fail" in skill:
        skill["fail"]["chance"] = max(5, min(90, round(skill["fail"]["chance"] / factor)))


def tidy(entry):
    def five(value):
        return int(5 * round(value / 5))

    for skill in entry["skills"]:
        if "fail" in skill:
            skill["fail"]["chance"] = max(5, five(skill["fail"]["chance"]))
        for effect in skill["effects"] + skill.get("fail", {}).get("effects", []):
            if effect["type"] == "damage":
                effect["power"] = round(round(effect["power"] * 20) / 20, 2)
                if "power_max" in effect:
                    effect["power_max"] = max(
                        effect["power"], round(round(effect["power_max"] * 20) / 20, 2)
                    )
            elif effect["type"] == "regen":
                effect["pct"] = round(effect["pct"] * 2) / 2
            elif "pct" in effect:
                effect["pct"] = round(effect["pct"])
                if effect["type"] == "buff" and abs(effect["pct"]) > 12:
                    effect["pct"] = five(effect["pct"])
            if "chance" in effect:
                chance = five(effect["chance"])
                if chance >= 100:
                    effect.pop("chance")
                else:
                    effect["chance"] = max(5, chance)
            for key in ("power", "power_max", "pct"):
                if isinstance(effect.get(key), float) and effect[key].is_integer():
                    effect[key] = int(effect[key])


def spread(result):
    return f"{min(result.values()):.2f}-{max(result.values()):.2f}"


def report(levels):
    pigs, raw = load()
    for level in levels:
        result = rates(pigs, raw, level)
        ordered = sorted(result.items(), key=lambda item: item[1])
        print(f"L{level} spread {spread(result)}")
        for pig_id, rate in ordered[:5] + ordered[-5:]:
            print(f"  {rate:.2f} {pig_id} {pigs[pig_id]['name']}")


def tune(from_order, rounds):
    pigs, raw = load()
    targets = [i for i in raw["pigs"] if pigs[i]["sort_order"] >= from_order]
    for round_index in range(rounds):
        result = {level: rates(pigs, raw, level) for level in range(1, 6)}
        print(round_index, *(f"L{lv} {spread(r)}" for lv, r in result.items()), flush=True)
        for pig_id in targets:
            entry = raw["pigs"][pig_id]
            low = 0.5 - result[1][pig_id]
            entry["stats"]["hp"] = max(40, min(250, round(entry["stats"]["hp"] * (1 + 0.25 * low))))
            for level in range(2, 6):
                gap = (0.5 - result[level][pig_id]) - 0.5 * (0.5 - result[level - 1][pig_id])
                scale_skill(entry["skills"][level - 1], 1 + 0.35 * gap)
    for pig_id in targets:
        tidy(raw["pigs"][pig_id])
    validate_battle(raw, pigs)
    dump(raw)
    result = {level: rates(pigs, raw, level) for level in range(1, 6)}
    print("final", *(f"L{lv} {spread(r)}" for lv, r in result.items()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("report", "tune"))
    parser.add_argument("--levels", default="1,3,5")
    parser.add_argument("--from-order", type=int, default=0)
    parser.add_argument("--rounds", type=int, default=6)
    args = parser.parse_args()
    if args.command == "report":
        report([int(level) for level in args.levels.split(",")])
    else:
        tune(args.from_order, args.rounds)
