"""Stable pig rarity and the combat multipliers attached to each tier."""

import hashlib

RARITIES = {
    "普通": {"rank": 1, "base": 1.00, "growth": 0.08, "skill": 1.00, "color": "#8A94A6"},
    "稀有": {"rank": 2, "base": 1.009, "growth": 0.082, "skill": 1.01, "color": "#3B82F6"},
    "史诗": {"rank": 3, "base": 1.018, "growth": 0.084, "skill": 1.02, "color": "#8B5CF6"},
    "传说": {"rank": 4, "base": 1.027, "growth": 0.087, "skill": 1.03, "color": "#F59E0B"},
    "神话": {"rank": 5, "base": 1.035, "growth": 0.089, "skill": 1.04, "color": "#EF4444"},
}

# A deliberately complete four-role benchmark.  These are not raid bosses, so
# encounter strength never changes just because a boss happens to share a pig.
MYTHIC_PIGS = frozenset({"big-lazy-pig", "wild-boar", "pig-bun", "pig-stamp"})


def rarity_for(pig_id: str) -> str:
    """Return a stable rarity without making old administrator catalogs migrate."""
    if pig_id in MYTHIC_PIGS:
        return "神话"
    roll = int.from_bytes(hashlib.sha256(pig_id.encode("utf-8")).digest()[:4], "big") % 100
    if roll < 4:
        return "传说"
    if roll < 16:
        return "史诗"
    if roll < 42:
        return "稀有"
    return "普通"


def rarity_info(pig_id: str, rarity: str | None = None) -> tuple[str, dict]:
    name = rarity if rarity in RARITIES else rarity_for(pig_id)
    return name, RARITIES[name]
