"""The five-phase Despair encounter.

This module intentionally does not use the collectible-pig skill executor.  A
Despair boss has its own action economy, targeting, cooldowns and JSON schema;
this prevents copy effects and player balance changes from leaking into the
encounter.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .battle import describe_effect

ROLES = ("坦克", "输出", "治疗", "辅助")
DESPAIR_PARTY_SIZE = 8
DESPAIR_MAX_ROUNDS = 200
SUPPORTED_HERO_EFFECTS = frozenset(
    {
        "absorb", "aura", "bleed_burst", "block", "buff", "channel", "charge",
        "cleanse", "cleanse_heal", "combo", "copy", "corrupting", "crit_amp",
        "damage", "delay_damage", "delay_heal", "detonate", "dispel", "dot",
        "energy", "evade", "expose", "fortify", "guard", "heal", "hot_boost",
        "immunity", "last_stand", "leech", "life_link", "mark", "mass_heal",
        "mitigate", "pick", "purge", "recoil", "redirect", "reflect", "regen",
        "revive", "rewind", "root", "sacrifice", "share", "shield", "silence",
        "snapshot", "stun", "taunt", "thorns", "transfer", "triage", "weaken",
    }
)


def _load_boss() -> dict:
    path = Path(__file__).resolve().parents[1] / "resources" / "despair_boss.json"
    return json.loads(path.read_text("utf-8"))


DESPAIR_BOSS = _load_boss()
BOSS_SKILL_NAMES = frozenset(
    skill["name"]
    for phase in DESPAIR_BOSS["phases"]
    for skill in (*phase["skills"], phase["signature"])
)


@dataclass
class _Hero:
    data: dict
    index: int
    owner: object
    label: str = field(init=False)
    role: str = field(init=False)
    max_hp: float = field(init=False)
    hp: float = field(init=False)
    atk: float = field(init=False)
    defense: float = field(init=False)
    speed: float = field(init=False)
    crit: float = field(init=False)
    dodge: float = field(init=False)
    shield: float = 0.0
    cooldown: int = 0
    skill_cooldowns: dict[str, int] = field(default_factory=dict)
    regens: list[dict] = field(default_factory=list)
    debuffs: list[dict] = field(default_factory=list)
    grief: bool = False
    invincible_until: int = -1
    death_processed: bool = False
    dealt: float = 0.0
    healed: float = 0.0
    taken: float = 0.0
    poise: float = 0.0
    cleansed: int = 0
    revived: bool = False
    revive_pct: float = 0.0
    energy: int = 0
    combo: int = 0
    combo_max: int = 5
    delay_heals: list[dict] = field(default_factory=list)
    reflects: list[dict] = field(default_factory=list)
    snapshot: float | None = None
    hp_history: list[float] = field(default_factory=list)
    last_stand_used: bool = False
    last_stand_turns: int = 0
    last_stand_pct: float = 0.0
    taunt: int = 0
    buffs: list[dict] = field(default_factory=list)
    hot_boosts: list[dict] = field(default_factory=list)
    evade_count: int = 0
    immunity: dict[str, int] = field(default_factory=dict)
    mitigates: list[dict] = field(default_factory=list)
    block_count: int = 0
    block_pct: float = 100.0
    guards: list[dict] = field(default_factory=list)
    share_pct: float = 0.0
    share_turns: int = 0
    charges: list[dict] = field(default_factory=list)
    channels: list[dict] = field(default_factory=list)

    def __post_init__(self):
        stats = self.data.get("stats") or {}
        self.label = self.data.get("label") or self.data.get("name") or f"小猪{self.index + 1}"
        self.role = self.data.get("role", "输出")
        self.max_hp = float(stats.get("hp", 100))
        self.hp = min(self.max_hp, float(self.data.get("start_hp", self.max_hp)))
        self.atk = float(stats.get("atk", 20))
        self.defense = float(stats.get("def", 10))
        self.speed = float(stats.get("spd", 10))
        self.crit = float(stats.get("crit", 5))
        self.dodge = float(stats.get("dodge", 5))
        self.hp_history = [self.hp]
        self.revive_pct = max(
            (
                float(effect.get("pct", 0))
                for skill in self.data.get("skills", [])
                if skill.get("passive")
                for effect in skill.get("effects", [])
                if effect.get("type") == "revive"
            ),
            default=0.0,
        )

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def ratio(self) -> float:
        return self.hp / self.max_hp if self.max_hp else 0.0


class DespairBattle:
    """Deterministic engine for one eight-pig, five-phase boss encounter."""

    def __init__(self, heroes: list[dict], seed: int = 1, boss: dict | None = None):
        if len(heroes) != DESPAIR_PARTY_SIZE:
            raise ValueError("绝境战需要 4 名玩家、每人 2 只小猪，共 8 只。")
        owners = [self._owner(data) for data in heroes]
        seats = [data.get("seat") for data in heroes]
        paired_seats = sorted(seats.count(seat) for seat in set(seats)) == [2, 2, 2, 2]
        if all(owner is None for owner in owners) and paired_seats:
            owners = seats
        else:
            owners = [index // 2 if owner is None else owner for index, owner in enumerate(owners)]
        if sorted(owners.count(owner) for owner in set(owners)) != [2, 2, 2, 2]:
            raise ValueError("绝境战的每名玩家必须恰好上阵 2 只小猪。")
        role_counts = {role: sum(data.get("role") == role for data in heroes) for role in ROLES}
        if any(role_counts[role] != 2 for role in ROLES):
            raise ValueError("绝境战阵容必须包含 2 坦克、2 输出、2 治疗和 2 辅助。")

        self.rng = random.Random(seed)
        self.seed = seed
        self.config = boss or DESPAIR_BOSS
        self.heroes = [_Hero(data, index, owners[index]) for index, data in enumerate(heroes)]
        total_hp = sum(hero.max_hp for hero in self.heroes)
        total_atk = sum(hero.atk for hero in self.heroes)
        base_boss_hp = max(total_hp * 1.65, total_atk * 5.5)
        self.boss_max_hp = float(
            self.config.get("stats", {}).get(
                "hp", base_boss_hp * float(self.config.get("hp_scale", 1.0))
            )
        )
        self.boss_hp = self.boss_max_hp
        self.boss_atk = float(
            self.config.get("stats", {}).get(
                "atk", max(14, total_hp / len(self.heroes) * 0.135)
            )
        )
        self.boss_def = float(self.config.get("stats", {}).get("def", 20))
        self.phase = 1
        self.phase_round = 0
        self.round = 0
        self.log: list[str] = []
        self.phase_history = [{"phase": 1, "name": self.phase_data["name"], "round": 1}]
        self.boss_actions: list[dict] = []
        self.skill_counts: dict[str, int] = {}
        self.cooldowns: dict[str, int] = {}
        self.boss_expose: list[dict] = []
        self.boss_weaken: list[dict] = []
        self.boss_dots: list[dict] = []
        self.boss_delays: list[dict] = []
        self.boss_corrupting: list[dict] = []
        self.boss_silence = 0
        self.boss_root = 0
        self.boss_stun = 0
        self.last_nonbasic = ""
        self.pending: dict | None = None
        self.poise_max = 180.0
        self.poise = self.poise_max
        self.poise_breaks = 0
        self.crown = 0
        self.crown_restored = False
        self.vulnerable_turns = 0
        self.resonance = 0.0
        self.resonance_need = total_hp * 0.30
        self.resonance_triggers = 0
        self.p5_rounds = 0
        self.damage_bonus = 0.0
        self.heal_penalty = 0.0
        self.revive_blocked = False
        self.judgment: dict | None = None
        self.judgment_history: list[dict] = []
        self.judgment_serial = 0
        self.ultimate_charges = 0
        self.round_five_charge = False
        self.phase_five_charge = False
        self.phase_five_ultimate_pending = False
        self.ultimate_history: list[dict] = []
        self.ultimate_warning_used: set[int] = set()
        self._leech_pool = 0.0
        for hero in self.heroes:
            for skill in hero.data.get("skills", []):
                if not skill.get("passive"):
                    continue
                for effect in skill.get("effects", []):
                    if effect.get("type") == "shield":
                        hero.shield += hero.max_hp * float(effect.get("pct", 0)) / 100
                    elif effect.get("type") == "evade":
                        hero.evade_count += int(effect.get("count", 1))
                    elif effect.get("type") == "mark":
                        self.boss_expose.append(
                            {
                                "pct": float(effect.get("pct", 0)),
                                "turns": int(effect.get("turns", 99)),
                            }
                        )
        self.phase_reports: list[dict] = []
        self._phase_report_start = self._new_phase_report_start()

    @staticmethod
    def _owner(data: dict):
        for key in ("owner_id", "player_id", "owner", "player"):
            if data.get(key) is not None:
                return data[key]
        return None

    @property
    def phase_data(self) -> dict:
        return self.config["phases"][self.phase - 1]

    def living(self) -> list[_Hero]:
        return [hero for hero in self.heroes if hero.alive]

    def _detail_snapshot(self) -> dict:
        return {
            "boss_hp": self.boss_hp,
            "poise": self.poise,
            "crown": self.crown,
            "boss_states": {
                "易伤": len(self.boss_expose),
                "虚弱": len(self.boss_weaken),
                "持续伤害": len(self.boss_dots),
                "延迟伤害": len(self.boss_delays),
                "腐化治疗": len(self.boss_corrupting),
                "沉默": self.boss_silence,
                "禁锢": self.boss_root,
                "眩晕": self.boss_stun,
            },
            "heroes": [
                {
                    "hp": hero.hp,
                    "shield": hero.shield,
                    "debuffs": Counter(item["type"] for item in hero.debuffs),
                }
                for hero in self.heroes
            ],
        }

    def _detail_notes(self, before: dict, *, boss_actor: bool = False) -> list[str]:
        notes = []
        boss_delta = before["boss_hp"] - self.boss_hp
        if boss_delta > 0:
            notes.append(
                f"万相猪皇受到 {round(boss_delta)} 反击伤害"
                if boss_actor
                else f"对万相猪皇造成 {round(boss_delta)} 伤害"
            )
        elif boss_delta < 0:
            notes.append(f"万相猪皇恢复 {round(-boss_delta)} 生命")
        poise = before["poise"] - self.poise
        if poise > 0:
            notes.append(f"削减 {round(poise)} 点韧性")
        crown = before["crown"] - self.crown
        if crown > 0:
            notes.append(f"击碎 {round(crown)} 层王冠")
        for hero, old in zip(self.heroes, before["heroes"]):
            hp_delta = hero.hp - old["hp"]
            if hp_delta > 0:
                notes.append(f"{hero.label}恢复 {round(hp_delta)} 生命")
            elif hp_delta < 0:
                notes.append(f"{hero.label}受到 {round(-hp_delta)} 伤害")
            shield_delta = hero.shield - old["shield"]
            if shield_delta > 0:
                notes.append(f"{hero.label}获得 {round(shield_delta)} 护盾")
            elif shield_delta < 0:
                notes.append(f"{hero.label}损失 {round(-shield_delta)} 护盾")
            current = Counter(item["type"] for item in hero.debuffs)
            added = list((current - old["debuffs"]).elements())
            removed = list((old["debuffs"] - current).elements())
            if added:
                notes.append(f"{hero.label}获得{'、'.join(added)}")
            if removed:
                notes.append(f"清除{hero.label}的{'、'.join(removed)}")
        current_states = {
            "易伤": len(self.boss_expose),
            "虚弱": len(self.boss_weaken),
            "持续伤害": len(self.boss_dots),
            "延迟伤害": len(self.boss_delays),
            "腐化治疗": len(self.boss_corrupting),
            "沉默": self.boss_silence,
            "禁锢": self.boss_root,
            "眩晕": self.boss_stun,
        }
        for name, value in current_states.items():
            increase = value - before["boss_states"][name]
            if increase > 0:
                notes.append(f"万相猪皇获得{name}" + (f"×{increase}" if increase > 1 else ""))
        return notes

    @staticmethod
    def _effect_notes(effects: list[dict]) -> list[str]:
        notes = []
        for effect in effects:
            if effect.get("type") in {"damage", "heal", "mass_heal", "triage", "cleanse_heal"}:
                continue
            try:
                text = describe_effect(effect)
            except (KeyError, TypeError, ValueError):
                continue
            if text not in notes:
                notes.append(text)
        return notes

    def _complete_log(self, index: int, text: str):
        """Fill an action placeholder, including a phase snapshot taken mid-action."""
        self.log[index] = text
        for report in reversed(self.phase_reports):
            if report.get("log") and report["log"][-1] == "":
                report["log"][-1] = text
                break

    def _new_phase_report_start(self) -> dict:
        return {
            "phase": self.phase,
            "name": self.phase_data["name"],
            "round": max(1, self.round),
            "boss_hp": self.boss_hp,
            "log": len(self.log),
            "actions": len(self.boss_actions),
            "hero_totals": [
                {
                    "dealt": hero.dealt,
                    "healed": hero.healed,
                    "taken": hero.taken,
                    "poise": hero.poise,
                }
                for hero in self.heroes
            ],
        }

    def _phase_report(self) -> dict:
        start = self._phase_report_start
        phase_config = self.config["phases"][start["phase"] - 1]
        heroes = []
        for hero, totals in zip(self.heroes, start["hero_totals"]):
            heroes.append(
                {
                    "index": hero.index,
                    "hp": round(hero.hp),
                    "max_hp": round(hero.max_hp),
                    "alive": hero.alive,
                    "dealt": round(hero.dealt - totals["dealt"]),
                    "healed": round(hero.healed - totals["healed"]),
                    "taken": round(hero.taken - totals["taken"]),
                    "poise": round(hero.poise - totals["poise"]),
                }
            )
        return {
            "phase": start["phase"],
            "name": start["name"],
            "start_round": start["round"],
            "end_round": self.round,
            "boss_hp_start": round(start["boss_hp"]),
            "boss_hp_end": round(self.boss_hp),
            "mechanics": phase_config.get("mechanics", []),
            "heroes": heroes,
            "actions": self.boss_actions[start["actions"] :],
            "log": self.log[start["log"] :],
        }

    @staticmethod
    def _has_effect(hero: _Hero, kind: str) -> bool:
        return any(
            effect.get("type") == kind
            for skill in hero.data.get("skills", [])
            for effect in skill.get("effects", [])
        )

    def _aura_bonus(self, stat: str) -> float:
        return sum(
            float(effect.get("pct", 0))
            for hero in self.living()
            for skill in hero.data.get("skills", [])
            if skill.get("passive")
            for effect in skill.get("effects", [])
            if effect.get("type") == "aura" and effect.get("stat") == stat
        )

    def _hero_stat(self, hero: _Hero, stat: str) -> float:
        base = {
            "atk": hero.atk,
            "def": hero.defense,
            "spd": hero.speed,
            "crit": hero.crit,
            "dodge": hero.dodge,
        }[stat]
        bonus = sum(item["pct"] for item in hero.buffs if item["stat"] == stat)
        bonus += self._aura_bonus(stat)
        bonus += sum(
            float(effect.get("pct", 0))
            for skill in hero.data.get("skills", [])
            if skill.get("passive")
            for effect in skill.get("effects", [])
            if effect.get("type") == "buff" and effect.get("stat") == stat
        )
        if stat in {"crit", "dodge"}:
            if stat == "dodge" and any(item["type"] == "禁锢" for item in hero.debuffs):
                return 0.0
            return max(0.0, min(90.0, base + bonus))
        return max(0.0, base * (1 + bonus / 100))

    def mate_of(self, hero: _Hero) -> _Hero | None:
        return next(
            (other for other in self.heroes if other is not hero and other.owner == hero.owner),
            None,
        )

    def _effective(self, raw: float, hero: _Hero) -> float:
        armor = 0.75 if any(item["type"] == "破甲" for item in hero.debuffs) else 1.0
        defense = self._hero_stat(hero, "def")
        return max(1.0, raw * 60 / (60 + defense * armor))

    def _hurt(self, hero: _Hero, amount: float, *, linked: bool = True) -> float:
        if not hero.alive:
            return 0.0
        if self.round <= hero.invincible_until:
            return 0.0
        if any(item["type"] == "易伤" for item in hero.debuffs):
            amount *= 1.20
        guard = next(
            (item for item in hero.guards if item["turns"] > 0 and item["source"].alive), None
        )
        if linked and guard:
            transferred = amount * guard["pct"] / 100
            amount -= transferred
            self._hurt(guard["source"], transferred, linked=False)
        if linked and hero.share_turns and hero.share_pct:
            peers = [unit for unit in self.living() if unit is not hero]
            if peers:
                shared = amount * hero.share_pct / 100
                amount -= shared
                each = shared / len(peers)
                for peer in peers:
                    self._hurt(peer, each, linked=False)
        passive_mitigation = sum(
            float(effect.get("pct", 0))
            for skill in hero.data.get("skills", [])
            if skill.get("passive")
            for effect in skill.get("effects", [])
            if effect.get("type") == "mitigate"
        )
        mitigation = min(80.0, passive_mitigation + sum(item["pct"] for item in hero.mitigates))
        amount *= 1 - mitigation / 100
        if hero.block_count:
            hero.block_count -= 1
            amount *= 1 - min(100.0, hero.block_pct) / 100
        if hero.last_stand_turns:
            amount *= 1 - min(80.0, hero.last_stand_pct) / 100
        mate = self.mate_of(hero)
        if self.phase == 3 and linked and mate is not None and mate.alive:
            shared = amount * 0.30
            amount -= shared
            self._hurt(mate, shared, linked=False)
        absorbed = min(hero.shield, amount)
        hero.shield -= absorbed
        lost = min(hero.hp, max(0.0, amount - absorbed))
        hero.hp -= lost
        hero.taken += absorbed + lost
        if (
            hero.alive
            and not hero.last_stand_used
            and self._passive_effect(hero, "last_stand") is not None
        ):
            effect = self._passive_effect(hero, "last_stand")
            if hero.ratio * 100 <= float(effect.get("threshold", 30)):
                hero.last_stand_used = True
                hero.last_stand_pct = float(effect.get("pct", 20))
                hero.last_stand_turns = max(1, int(effect.get("turns", 1)))
        self._process_falls()
        return absorbed + lost

    def _process_falls(self):
        for hero in self.heroes:
            if hero.alive or hero.death_processed:
                continue
            if (
                not self.revive_blocked
                and not hero.revived
                and (hero.revive_pct or self._has_effect(hero, "revive"))
            ):
                hero.revived = True
                pct = hero.revive_pct or 25.0
                hero.hp = hero.max_hp * pct / 100
                self.log.append(f"{hero.label} 的性格令其重新站起！")
                continue
            hero.death_processed = True
            mate = self.mate_of(hero)
            if self.phase >= 3 and mate is not None and mate.alive:
                mate.grief = True
                self.log.append(f"{hero.label} 倒下，{mate.label} 获得悲鸣。")

    def _heal(self, target: _Hero, amount: float, source: _Hero | None) -> float:
        if not target.alive:
            return 0.0
        penalty = self.heal_penalty
        famine = next((item for item in target.debuffs if item["type"] == "饥荒"), None)
        if famine:
            penalty = max(penalty, 0.40)
            famine["uses"] -= 1
        if any(item["type"] == "受疗降低" for item in target.debuffs):
            penalty = max(penalty, 0.15)
        inverse = next((item for item in target.debuffs if item["type"] == "逆疗"), None)
        if inverse:
            self._heal_boss(amount * 0.50)
            target.debuffs.remove(inverse)
        passive_boost = sum(
            float(effect.get("pct", 0))
            for skill in target.data.get("skills", [])
            if skill.get("passive")
            for effect in skill.get("effects", [])
            if effect.get("type") == "hot_boost"
        )
        amount *= 1 + (passive_boost + sum(item["pct"] for item in target.hot_boosts)) / 100
        amount *= 1 - penalty
        restored = min(amount, target.max_hp - target.hp)
        target.hp += restored
        if source is not None:
            source.healed += restored
        if self.phase == 3 and restored:
            self.resonance += restored
            while self.resonance >= self.resonance_need:
                self.resonance -= self.resonance_need
                self.resonance_triggers += 1
                for hero in self.living():
                    hero.shield += hero.max_hp * 0.10
                self._reduce_poise(25, "生命共鸣")
                self.resonance_need *= 1.25
        if self.judgment and source is not None:
            self.judgment["values"]["治疗"] += restored
            self._check_seals()
        return restored

    def _add_debuff(self, target: _Hero, item: dict, family: str = "all") -> bool:
        passive = {
            effect.get("kind", "all")
            for skill in target.data.get("skills", [])
            if skill.get("passive")
            for effect in skill.get("effects", [])
            if effect.get("type") == "immunity"
        }
        if (
            target.immunity.get("all", 0)
            or target.immunity.get(family, 0)
            or "all" in passive
            or family in passive
        ):
            self.log.append(f"{target.label} 免疫了{item['type']}。")
            return False
        target.debuffs.append(item)
        return True

    @staticmethod
    def _passive_effect(hero: _Hero, kind: str) -> dict | None:
        return next(
            (
                effect
                for skill in hero.data.get("skills", [])
                if skill.get("passive")
                for effect in skill.get("effects", [])
                if effect.get("type") == kind
            ),
            None,
        )

    def _heal_boss(self, amount: float) -> float:
        reduction = min(90.0, sum(item["pct"] for item in self.boss_corrupting))
        amount *= 1 - reduction / 100
        restored = min(amount, self.boss_max_hp - self.boss_hp)
        self.boss_hp += restored
        return restored

    def _reduce_poise(self, value: float, reason: str):
        self.poise -= value
        for hero in self.living():
            if hero.role == "坦克" and reason == "坚守":
                hero.poise += value
                break
        if self.poise > 0:
            return
        self.poise_breaks += 1
        self.poise = self.poise_max * min(1.5, 1 + self.poise_breaks * 0.15)
        if self.pending:
            self.log.append(f"韧性击破，打断「{self.pending['name']}」！")
            self.pending = None
            self.judgment = None
        else:
            self.boss_expose.append({"pct": 15.0, "turns": 2})
            self.log.append("韧性击破，万相猪皇本回合受到伤害提高 15%！")

    def damage_boss(self, hero: _Hero, amount: float) -> float:
        """Deal damage and enforce the next phase boundary without overflow."""
        if self.boss_hp <= 0:
            return 0.0
        if self.boss_expose:
            amount *= 1 + max(item["pct"] for item in self.boss_expose) / 100
        if any(item["type"] == "寒意" for item in hero.debuffs):
            amount *= 0.85
        factor = 1.0
        if self.phase == 1 and self.poise_breaks == 0:
            factor *= 0.80
        if self.phase == 4:
            factor *= 1.25 if self.vulnerable_turns else 0.70
        amount *= (
            factor
            * (1.20 if hero.grief else 1.0)
            * float(self.config.get("hero_damage_scale", 1.0))
            * float(self.phase_data.get("damage_taken_scale", 1.0))
        )
        boundary_pct = self.config["phase_thresholds"][self.phase - 1]
        boundary = self.boss_max_hp * boundary_pct / 100
        floor = boundary if self.phase < 5 else 0.0
        available = max(0.0, self.boss_hp - floor)
        dealt = min(max(0.0, amount), available)
        self.boss_hp -= dealt
        hero.dealt += dealt
        if self.judgment and hero.role == "输出":
            self.judgment["values"]["输出"] += dealt
            self._check_seals()
        if self.boss_hp <= boundary and self.phase < 5:
            self._transition()
        return dealt

    def _transition(self):
        old = self.phase
        self.phase += 1
        self.phase_round = 0
        if self.pending:
            self.log.append(f"阶段转换，预警「{self.pending['name']}」已取消。")
        self.phase_reports.append(self._phase_report())
        self.pending = None
        self.judgment = None
        self.cooldowns.clear()
        self.last_nonbasic = ""
        self.poise = self.poise_max
        for hero in self.living():
            self._heal(hero, hero.max_hp * 0.06, None)
            hero.cooldown = max(0, hero.cooldown - 1)
            for name in list(hero.skill_cooldowns):
                hero.skill_cooldowns[name] = max(0, hero.skill_cooldowns[name] - 1)
        if self.phase == 4:
            self.crown = 12
            self.crown_restored = False
        if self.phase == 5:
            self.heal_penalty = 0.30
            self.revive_blocked = True
            if not self.phase_five_charge:
                self.ultimate_charges = min(2, self.ultimate_charges + 1)
                self.phase_five_charge = True
                self.phase_five_ultimate_pending = True
        self.phase_history.append(
            {"phase": self.phase, "name": self.phase_data["name"], "round": self.round}
        )
        self._phase_report_start = self._new_phase_report_start()
        self.log.append(f"万相猪皇由第 {old} 阶段进入「{self.phase_data['name']}」！")

    def _hero_targets(self, hero: _Hero, effect: dict) -> list[_Hero]:
        target = effect.get("target", "self")
        living = self.living()
        if target in ("ally_all", "all_allies"):
            return living
        if target in ("ally_lowest", "lowest"):
            return [min(living, key=lambda unit: (unit.ratio, unit.index))]
        if target == "ally_tank":
            tanks = [unit for unit in living if unit.role == "坦克"]
            return [min(tanks or living, key=lambda unit: (unit.ratio, unit.index))]
        return [hero]

    def _skill_score(self, hero: _Hero, skill: dict) -> float:
        effects = skill.get("effects", [])
        kinds = {effect.get("type") for effect in effects}
        missing = sum(1 - unit.ratio for unit in self.living())
        score = 10.0 + sum(float(effect.get("power", 0)) * 12 for effect in effects)
        if kinds & {"heal", "mass_heal", "triage", "cleanse_heal", "regen"}:
            score += missing * 35
        if kinds & {"cleanse", "cleanse_heal", "transfer"}:
            score += sum(len(unit.debuffs) for unit in self.living()) * 28
        if kinds & {"shield", "guard", "mitigate", "block", "redirect", "fortify"}:
            score += sum(unit.ratio < 0.65 for unit in self.living()) * 15
        if kinds & {"expose", "weaken", "purge", "dispel", "stun", "root"}:
            score += 22
        if skill.get("when") == "low_hp":
            score += 80 if hero.ratio < 0.4 else -1000
        if skill.get("when") == "enemy_low":
            score += 80 if self.boss_hp / self.boss_max_hp < 0.35 else -1000
        return score

    def _cast_hero_skill(self, hero: _Hero, skill: dict, *, triggered: bool = False) -> bool:
        def supported(items: list[dict]) -> bool:
            for item in items:
                if item.get("type") not in SUPPORTED_HERO_EFFECTS:
                    return False
                if item.get("type") == "pick" and any(
                    not supported(option.get("effects", [])) for option in item.get("options", [])
                ):
                    return False
            return True

        normal = skill.get("effects", [])
        failed_effects = (skill.get("fail") or {}).get("effects", [])
        if not supported(normal) or not supported(failed_effects):
            self.log.append(
                f"R{self.round} {hero.label}「{skill.get('name', '未知技能')}」，"
                "含不支持的效果，本次改用职业行动。"
            )
            return False
        cost = int(skill.get("cost", 0))
        if cost > hero.energy:
            return False
        hero.energy -= cost
        hero.energy = min(100, hero.energy + int(skill.get("gain", 0)))
        self._leech_pool = 0.0
        before = self._detail_snapshot()
        log_index = len(self.log)
        self.log.append("")
        effects = normal
        failure = skill.get("fail")
        failed = False
        failure_text = ""
        if failure and self.rng.randrange(100) < int(failure.get("chance", 0)):
            failed = True
            effects = failure.get("effects", [])
            failure_text = f"失手：{failure.get('text', '失败')}"
        useful = False
        phase = self.phase
        for effect in effects:
            useful = self._apply_hero_effect(hero, effect) or useful
            if self.phase != phase or self.boss_hp <= 0 or not hero.alive:
                break
        notes = self._detail_notes(before)
        notes.extend(note for note in self._effect_notes(effects) if note not in notes)
        if failure_text:
            notes.insert(0, failure_text)
        action = "触发" if triggered else "施放"
        self._complete_log(log_index, (
            f"R{self.round} {hero.label}{action}「{skill['name']}」，"
            + "；".join(notes or ["没有产生有效效果"])
            + "。"
        ))
        # A valid cast is consumed even if every chance roll missed.  Only an
        # unsupported schema returns False and may fall back to the role action.
        return useful or failed

    def _apply_hero_effect(self, hero: _Hero, effect: dict) -> bool:
        kind = effect.get("type")
        if "chance" in effect and self.rng.randrange(100) >= float(effect["chance"]):
            return True
        if kind == "damage":
            power = float(effect.get("power", 0.8))
            combo = hero.combo if effect.get("combo_bonus") else 0
            if combo:
                power *= 1 + combo * float(effect["combo_bonus"]) / 100
                hero.combo = 0
            for _ in range(max(1, int(effect.get("hits", 1)))):
                raw = self._hero_stat(hero, "atk")
                raw *= power * self.rng.uniform(0.94, 1.06)
                crit = self.rng.randrange(100) < self._hero_stat(hero, "crit")
                if crit:
                    multiplier = 2.0 if self._has_effect(hero, "crit_amp") else 1.6
                    raw *= multiplier
                defense = self.boss_def * (1 - min(100.0, float(effect.get("pierce", 0))) / 100)
                raw *= 60 / (60 + defense)
                if effect.get("execute") and self.boss_hp / self.boss_max_hp < 0.35:
                    raw *= 1.35
                phase = self.phase
                self._leech_pool += self.damage_boss(hero, raw)
                if self.phase != phase or self.boss_hp <= 0:
                    break
            return True
        if kind in {"dot", "delay_damage"}:
            collection = self.boss_delays if kind == "delay_damage" else self.boss_dots
            collection.append(
                {
                    "source": hero,
                    "pct": float(effect.get("pct", 3)),
                    "turns": int(effect.get("turns", 2)),
                    "label": effect.get("label", "持续伤害"),
                }
            )
            return True
        if kind in {"heal", "cleanse_heal", "triage", "mass_heal"}:
            targets = self.living() if kind == "mass_heal" else self._hero_targets(hero, effect)
            for target in targets:
                pct = float(effect.get("pct", 12))
                if kind == "triage" and target.ratio * 100 <= float(effect.get("threshold", 40)):
                    pct += float(effect.get("bonus", 0))
                self._heal(target, target.max_hp * pct / 100, hero)
                if kind == "cleanse_heal" and target.debuffs:
                    target.debuffs.clear()
                    hero.cleansed += 1
            return True
        if kind == "shield":
            for target in self._hero_targets(hero, effect):
                target.shield += target.max_hp * float(effect.get("pct", 10)) / 100
            return True
        if kind == "guard":
            targets = self._hero_targets(hero, effect)
            if effect.get("target", "ally_lowest") in {"ally_lowest", "lowest"}:
                others = [unit for unit in self.living() if unit is not hero]
                if others:
                    targets = [min(others, key=lambda unit: (unit.ratio, unit.index))]
            for target in targets:
                target.guards.append(
                    {
                        "source": hero,
                        "pct": float(effect.get("pct", 30)),
                        "turns": max(1, int(effect.get("turns", 2))) + 1,
                    }
                )
            return True
        if kind == "mitigate":
            hero.mitigates.append(
                {"pct": float(effect.get("pct", 20)), "turns": int(effect.get("turns", 2)) + 1}
            )
            return True
        if kind == "block":
            hero.block_count += int(effect.get("count", 1))
            hero.block_pct = float(effect.get("pct", 100))
            return True
        if kind == "redirect":
            hero.taunt = max(hero.taunt, int(effect.get("turns", 1)) + 1)
            return True
        if kind == "fortify":
            hero.shield += (hero.shield + self._hero_stat(hero, "def") * 10) * float(
                effect.get("pct", 20)
            ) / 100
            return True
        if kind == "share":
            turns = int(effect.get("turns", 2)) + 1
            for target in self.living():
                target.share_pct = max(target.share_pct, float(effect.get("pct", 30)))
                target.share_turns = max(target.share_turns, turns)
            return True
        if kind in {"cleanse", "transfer"}:
            changed = False
            for target in self._hero_targets(hero, effect):
                changed = changed or bool(target.debuffs)
                target.debuffs.clear()
            if changed:
                hero.cleansed += 1
                if self.judgment and hero.role == "辅助":
                    self.judgment["values"]["辅助"] += 1
                    self._check_seals()
            return changed
        if kind == "regen":
            for target in self._hero_targets(hero, effect):
                target.regens.append(
                    {
                        "source": hero,
                        "pct": float(effect.get("pct", 3)),
                        "turns": min(6, int(effect.get("turns", 2))),
                    }
                )
            return True
        if kind == "delay_heal":
            for target in self._hero_targets(hero, effect):
                target.delay_heals.append(
                    {
                        "target": target,
                        "source": hero,
                        "pct": float(effect.get("pct", 10)),
                        "turns": max(1, int(effect.get("turns", 1))),
                    }
                )
            return True
        if kind == "weaken":
            self.boss_weaken.append(
                {"pct": float(effect.get("pct", 12)), "turns": int(effect.get("turns", 2))}
            )
            return True
        if kind == "silence":
            if effect.get("target") == "self":
                self._add_debuff(hero, {"type": "沉默", "turns": int(effect.get("turns", 1))})
                return True
            self.boss_silence = max(self.boss_silence, int(effect.get("turns", 1)))
            return True
        if kind == "root":
            if effect.get("target") == "self":
                self._add_debuff(hero, {"type": "禁锢", "turns": int(effect.get("turns", 1))})
                return True
            self.boss_root = max(self.boss_root, int(effect.get("turns", 1)))
            self._reduce_poise(18, "控制")
            return True
        if kind == "stun":
            if effect.get("target") == "self":
                self._add_debuff(hero, {"type": "眩晕", "turns": int(effect.get("turns", 1))})
                return True
            self.boss_stun = max(self.boss_stun, int(effect.get("turns", 1)))
            self._reduce_poise(24, "控制")
            return True
        if kind in {"expose", "mark"}:
            self.boss_expose.append(
                {"pct": float(effect.get("pct", 12)), "turns": int(effect.get("turns", 2))}
            )
            return True
        if kind == "corrupting":
            self.boss_corrupting.append(
                {
                    "pct": float(effect.get("pct", 20)),
                    "turns": max(1, int(effect.get("turns", 2))),
                }
            )
            return True
        if kind in {"purge", "dispel", "detonate", "bleed_burst"}:
            stored = [*self.boss_dots, *self.boss_delays]
            if kind in {"detonate", "bleed_burst"} and stored:
                for dot in stored:
                    self.damage_boss(hero, self.boss_max_hp * dot["pct"] / 100)
                self.boss_dots.clear()
                self.boss_delays.clear()
            else:
                self.crown = max(0, self.crown - 2)
                self._reduce_poise(12, "驱散")
            return True
        if kind == "buff":
            for target in self._hero_targets(hero, effect):
                target.buffs.append(
                    {
                        "stat": effect.get("stat", "atk"),
                        "pct": float(effect.get("pct", 10)),
                        "turns": int(effect.get("turns", 2)) + 1,
                    }
                )
            return True
        if kind == "aura":
            # Passive auras are read directly by _aura_bonus and _hero_turn.
            return True
        if kind == "hot_boost":
            for target in self._hero_targets(hero, effect):
                target.hot_boosts.append(
                    {"pct": float(effect.get("pct", 20)), "turns": int(effect.get("turns", 3)) + 1}
                )
            return True
        if kind == "evade":
            for target in self._hero_targets(hero, effect):
                target.evade_count += int(effect.get("count", 1))
            return True
        if kind == "immunity":
            immunity = effect.get("kind", "all")
            hero.immunity[immunity] = max(
                hero.immunity.get(immunity, 0), int(effect.get("turns", 2)) + 1
            )
            return True
        if kind == "leech":
            if self._leech_pool:
                targets = self._hero_targets(hero, effect)
                self._heal(
                    targets[0], self._leech_pool * float(effect.get("pct", 20)) / 100, hero
                )
            return True
        if kind == "absorb":
            hero.shield += hero.max_hp * float(effect.get("pct", 10)) / 100
            return True
        if kind == "charge":
            hero.charges.append(
                {
                    "turns": max(1, int(effect.get("turns", 1))),
                    "power": float(effect.get("power", 1.25)),
                }
            )
            return True
        if kind == "channel":
            hero.channels.append(
                {
                    "turns": max(1, int(effect.get("turns", 1))),
                    "power": float(effect.get("power", 0)),
                    "heal_pct": float(effect.get("heal_pct", 0)),
                }
            )
            return True
        if kind == "copy":
            # Boss and player schemas stay separate; reproduce the last boss move
            # as a player-scaled counter instead of trying to execute boss JSON.
            last = self.boss_actions[-1]["skill"] if self.boss_actions else "普通攻击"
            scale = float(effect.get("pct", 100)) / 100
            self.damage_boss(hero, self._hero_stat(hero, "atk") * scale * 60 / (60 + self.boss_def))
            self.log.append(f"{hero.label} 复刻「{last}」反击。")
            return True
        if kind == "recoil":
            self._hurt(hero, hero.max_hp * float(effect.get("pct", 10)) / 100, linked=False)
            return True
        if kind == "revive":
            hero.revive_pct = max(hero.revive_pct, float(effect.get("pct", 25)))
            return True
        if kind == "taunt":
            hero.taunt = max(hero.taunt, int(effect.get("turns", 1)) + 1)
            return True
        if kind in {"reflect", "thorns"}:
            hero.reflects.append(
                {
                    "pct": float(effect.get("pct", 20)),
                    "turns": max(1, int(effect.get("turns", 2))) + 1,
                    "dot_pct": float(effect.get("dot_pct", 0)),
                }
            )
            return True
        if kind == "last_stand":
            hero.last_stand_pct = float(effect.get("pct", 20))
            hero.last_stand_turns = max(1, int(effect.get("turns", 1)))
            hero.last_stand_used = True
            return True
        if kind == "snapshot":
            hero.snapshot = hero.hp
            return True
        if kind == "rewind":
            steps = max(1, int(effect.get("turns", 1)))
            past = hero.snapshot
            if past is None:
                past = hero.hp_history[max(0, len(hero.hp_history) - 1 - steps)]
            restored = max(0.0, min(hero.max_hp, past) - hero.hp)
            self._heal(hero, restored, hero)
            hero.snapshot = None
            return True
        if kind == "sacrifice":
            self._hurt(hero, hero.max_hp * float(effect.get("pct", 10)) / 100, linked=False)
            for target in self._hero_targets(hero, effect):
                if effect.get("shield_pct"):
                    target.shield += target.max_hp * float(effect["shield_pct"]) / 100
                if effect.get("heal_pct"):
                    self._heal(target, target.max_hp * float(effect["heal_pct"]) / 100, hero)
            if effect.get("mitigate_pct"):
                hero.last_stand_pct = max(hero.last_stand_pct, float(effect["mitigate_pct"]))
                hero.last_stand_turns = max(hero.last_stand_turns, 2)
            return True
        if kind == "life_link":
            self_pct = float(effect.get("self_pct", 0))
            if self_pct < 0:
                self._hurt(hero, hero.max_hp * abs(self_pct) / 100, linked=False)
            elif self_pct > 0:
                self._heal(hero, hero.max_hp * self_pct / 100, hero)
            for target in self._hero_targets(hero, effect):
                self._heal(target, target.max_hp * float(effect.get("pct", 10)) / 100, hero)
            return True
        if kind == "pick":
            options = effect.get("options") or []
            if not options:
                return True
            chosen = self.rng.choices(options, weights=[item.get("weight", 1) for item in options])[0]
            phase = self.phase
            for nested in chosen.get("effects", []):
                self._apply_hero_effect(hero, nested)
                if self.phase != phase or self.boss_hp <= 0:
                    break
            return True
        if kind == "energy":
            for target in self._hero_targets(hero, effect):
                target.energy = min(100, target.energy + int(effect.get("gain", 0)))
            return True
        if kind == "combo":
            hero.combo_max = int(effect.get("max", hero.combo_max))
            hero.combo = min(hero.combo_max, hero.combo + int(effect.get("gain", 1)))
            return True
        return False

    def _role_action(self, hero: _Hero):
        notes = []
        if hero.role == "治疗":
            target = min(self.living(), key=lambda unit: (unit.ratio, unit.index))
            healed = self._heal(target, target.max_hp * 0.18, hero)
            if healed:
                notes.append(f"为{target.label}恢复 {round(healed)} 生命")
            power = 0.45
        elif hero.role == "辅助":
            afflicted = max(self.living(), key=lambda unit: (len(unit.debuffs), -unit.index))
            if afflicted.debuffs:
                removed = "、".join(item["type"] for item in afflicted.debuffs)
                afflicted.debuffs.clear()
                hero.cleansed += 1
                notes.append(f"清除{afflicted.label}的{removed}")
                if self.judgment:
                    self.judgment["values"]["辅助"] += 1
                    self._check_seals()
            power = 0.65
        elif hero.role == "坦克":
            shield = hero.max_hp * 0.06
            hero.shield += shield
            notes.append(f"自身获得 {round(shield)} 护盾")
            power = 0.80
        else:
            power = 1.45
        raw = self._hero_stat(hero, "atk")
        raw *= power * self.rng.uniform(0.94, 1.06) * 60 / (60 + self.boss_def)
        log_index = len(self.log)
        self.log.append("")
        dealt = self.damage_boss(hero, raw)
        if dealt:
            notes.insert(0, f"对万相猪皇造成 {round(dealt)} 伤害")
        self._complete_log(log_index, (
            f"R{self.round} {hero.label}执行{hero.role}职业行动，"
            + "；".join(notes or ["没有产生有效效果"])
            + "。"
        ))

    def _hero_turn(self, hero: _Hero):
        if not hero.alive:
            return
        hero.hp_history.append(hero.hp)
        del hero.hp_history[:-8]
        if self.phase_five_ultimate_pending:
            self.phase_five_ultimate_pending = False
            self._maybe_ultimate()
            if self.boss_hp <= 0:
                return
        active_debuffs = {item["type"] for item in hero.debuffs}
        for debuff in list(hero.debuffs):
            revived_from_tick = False
            if debuff["type"] == "尸毒":
                revived_before = hero.revived
                self._hurt(hero, hero.max_hp * 0.04, linked=False)
                revived_from_tick = hero.revived and not revived_before
            debuff["turns"] -= 1
            if debuff["turns"] <= 0:
                if debuff["type"] == "星爆标记":
                    self._hurt(hero, hero.max_hp * 0.15, linked=False)
                hero.debuffs.remove(debuff)
            if revived_from_tick:
                self.log.append(f"{hero.label} 被持续伤害击倒后复活，本回合无法行动。")
                return
        for delayed in list(hero.delay_heals):
            delayed["turns"] -= 1
            if delayed["turns"] <= 0:
                target = delayed["target"]
                healed = self._heal(
                    target, target.max_hp * delayed["pct"] / 100, delayed["source"]
                )
                hero.delay_heals.remove(delayed)
                self.log.append(
                    f"R{self.round} {hero.label}的延迟治疗生效，"
                    f"为{target.label}恢复 {round(healed)} 生命。"
                )
        for regen in list(hero.regens):
            healed = self._heal(hero, hero.max_hp * regen["pct"] / 100, regen["source"])
            if healed:
                self.log.append(
                    f"R{self.round} {hero.label}的持续治疗生效，恢复 {round(healed)} 生命。"
                )
            regen["turns"] -= 1
            if regen["turns"] <= 0:
                hero.regens.remove(regen)
        for charge in list(hero.charges):
            charge["turns"] -= 1
            if charge["turns"] <= 0:
                raw = self._hero_stat(hero, "atk") * charge["power"] * 60 / (60 + self.boss_def)
                dealt = self.damage_boss(hero, raw)
                hero.charges.remove(charge)
                self.log.append(
                    f"R{self.round} {hero.label}完成蓄力，对万相猪皇造成 {round(dealt)} 伤害。"
                )
        channeling = bool(hero.channels)
        for channel in list(hero.channels):
            details = []
            if channel["power"]:
                raw = self._hero_stat(hero, "atk") * channel["power"] * 60 / (60 + self.boss_def)
                dealt = self.damage_boss(hero, raw)
                details.append(f"对万相猪皇造成 {round(dealt)} 伤害")
            if channel["heal_pct"]:
                healed = self._heal(hero, hero.max_hp * channel["heal_pct"] / 100, hero)
                details.append(f"自身恢复 {round(healed)} 生命")
            if details:
                self.log.append(
                    f"R{self.round} {hero.label}维持引导，{'；'.join(details)}。"
                )
            channel["turns"] -= 1
            if channel["turns"] <= 0:
                hero.channels.remove(channel)
        hero.cooldown = max(0, hero.cooldown - 1)
        for name in list(hero.skill_cooldowns):
            hero.skill_cooldowns[name] = max(0, hero.skill_cooldowns[name] - 1)
        if not hero.alive or active_debuffs & {"冰封", "眩晕"} or channeling:
            if active_debuffs & {"冰封", "眩晕"}:
                self.log.append(f"{hero.label} 被控制，无法行动。")
            elif channeling:
                self.log.append(f"{hero.label} 正在维持引导。")
            return
        for skill in hero.data.get("skills", []):
            if not skill.get("passive"):
                continue
            for effect in skill.get("effects", []):
                if effect.get("type") == "aura" and effect.get("heal_pct"):
                    for ally in self.living():
                        self._heal(ally, ally.max_hp * float(effect["heal_pct"]) / 100, hero)
                elif effect.get("type") == "regen":
                    for target in self._hero_targets(hero, effect):
                        self._heal(
                            target,
                            target.max_hp
                            * float(effect.get("pct", 0))
                            * int(effect.get("stacks", 1))
                            / 100,
                            hero,
                        )
        skills = [
            skill
            for skill in hero.data.get("skills", [])
            if not skill.get("passive")
            and not hero.skill_cooldowns.get(skill.get("name", ""), 0)
            and int(skill.get("cost", 0)) <= hero.energy
            and (skill.get("when") != "hurt" or hero.ratio < 0.7)
            and (skill.get("when") != "low_hp" or hero.ratio < 0.4)
            and (
                skill.get("when") != "enemy_low"
                or self.boss_hp / self.boss_max_hp < 0.35
            )
            and skill.get("when") not in {
                "on_crit",
                "on_dodge",
                "on_ally_dodge",
                "on_ally_hurt",
                "on_shield_break",
                "after_charge",
                "on_kill",
            }
        ]
        if active_debuffs & {"封技", "沉默"}:
            skills = []
        skill = max(skills, key=lambda item: self._skill_score(hero, item), default=None)
        phase_before_hit = self.phase
        useful = False
        if skill and skill.get("effects"):
            useful = self._cast_hero_skill(hero, skill)
            if useful:
                hero.skill_cooldowns[skill["name"]] = int(skill.get("cd", 0)) + 1
        if not useful:
            self._role_action(hero)
            if any("cost" in item or "gain" in item for item in hero.data.get("skills", [])):
                hero.energy = min(100, hero.energy + 8)
        poise = {"坦克": 8, "输出": 6, "治疗": 3, "辅助": 7}[hero.role]
        hero.poise += poise
        self._reduce_poise(poise, "进攻")
        if phase_before_hit == 4 and self.phase == 4 and self.crown:
            layers = min(3, {"输出": 2, "辅助": 2, "坦克": 1, "治疗": 1}[hero.role])
            self.crown = max(0, self.crown - layers)
            if not self.crown:
                self.vulnerable_turns = 2
                self.log.append("崩裂王冠破碎，万相猪皇进入两回合破防！")
        hero.taunt = max(0, hero.taunt - 1)
        hero.last_stand_turns = max(0, hero.last_stand_turns - 1)
        for reflect in list(hero.reflects):
            reflect["turns"] -= 1
            if reflect["turns"] <= 0:
                hero.reflects.remove(reflect)
        for collection in (hero.buffs, hero.hot_boosts, hero.mitigates, hero.guards):
            for item in list(collection):
                item["turns"] -= 1
                if item["turns"] <= 0:
                    collection.remove(item)
        hero.share_turns = max(0, hero.share_turns - 1)
        if not hero.share_turns:
            hero.share_pct = 0.0
        for key in list(hero.immunity):
            hero.immunity[key] = max(0, hero.immunity[key] - 1)

    def _target(self, *, lowest: bool = False, healer: bool = False) -> _Hero:
        pool = self.living()
        taunters = [
            hero
            for hero in pool
            if hero.taunt > 0
            or self._passive_effect(hero, "taunt") is not None
            or self._passive_effect(hero, "redirect") is not None
        ]
        if taunters and not lowest:
            pool = taunters
        if healer:
            healing = [hero for hero in pool if hero.role == "治疗"]
            if healing:
                pool = healing
        tanks = [hero for hero in pool if hero.role == "坦克"]
        if tanks and not lowest:
            pool = tanks
        return min(pool, key=lambda hero: (hero.ratio if lowest else -hero.taken, hero.index))

    def _trigger(self, hero: _Hero, when: str):
        if not hero.alive:
            return
        for skill in hero.data.get("skills", []):
            if skill.get("when") != when or hero.skill_cooldowns.get(skill["name"], 0):
                continue
            if int(skill.get("cost", 0)) > hero.energy:
                continue
            applied = self._cast_hero_skill(hero, skill, triggered=True)
            if applied:
                hero.skill_cooldowns[skill["name"]] = int(skill.get("cd", 0)) + 1
            return

    def _boss_hit(self, target: _Hero, power: float, *, max_hp_pct: float = 0.0) -> float:
        forced_evade = target.evade_count > 0
        if forced_evade:
            target.evade_count -= 1
        if forced_evade or self.rng.randrange(100) < self._hero_stat(target, "dodge"):
            self.log.append(f"{target.label} 闪开了万相猪皇的攻击。")
            self._trigger(target, "on_dodge")
            for ally in self.living():
                if ally is not target:
                    self._trigger(ally, "on_ally_dodge")
            return 0.0
        pressure = max(0, self.round - 1) * float(
            self.config.get("pressure_per_round", 0.0)
        )
        enrage_round = int(self.config.get("enrage_round", DESPAIR_MAX_ROUNDS))
        pressure += max(0, self.round - enrage_round) * float(
            self.config.get("enrage_per_round", 0.0)
        )
        raw = (
            self.boss_atk
            * power
            * (1 + self.damage_bonus)
            * float(self.config.get("damage_scale", 1.0))
            * float(self.phase_data.get("outgoing_damage_scale", 1.0))
            * (1 + pressure)
        )
        if self.boss_weaken:
            raw *= 1 - max(item["pct"] for item in self.boss_weaken) / 100
        raw += target.max_hp * max_hp_pct
        raw = self._effective(raw, target)
        if target.grief:
            raw *= 1.20
        dealt = self._hurt(target, raw)
        if dealt:
            pct = sum(
                float(effect.get("pct", 18))
                for skill in target.data.get("skills", [])
                if skill.get("passive")
                for effect in skill.get("effects", [])
                if effect.get("type") in {"thorns", "reflect"}
            )
            pct += sum(item["pct"] for item in target.reflects)
            if pct:
                self.damage_boss(target, dealt * pct / 100)
        if dealt:
            for ally in self.living():
                if ally is not target:
                    self._trigger(ally, "on_ally_hurt")
        if self.judgment and target.role == "坦克":
            self.judgment["values"]["坦克"] += dealt
            self._check_seals()
        return dealt

    def _choose_skill(self) -> dict:
        skills = self.phase_data["skills"]
        ready = [skill for skill in skills if self.cooldowns.get(skill["name"], 0) <= 0]
        if self.boss_silence:
            return ready[0]
        nonbasic = [skill for skill in ready[1:] if skill["name"] != self.last_nonbasic]
        if self.phase == 5:
            priority = ("生灭倒转", "绝境剥夺", "四职崩坏")
        else:
            priority = tuple(skill["name"] for skill in reversed(skills[1:]))
        for name in priority:
            skill = next((item for item in nonbasic if item["name"] == name), None)
            if skill:
                return skill
        return ready[0]

    def _use_skill(self, skill: dict):
        name = skill["name"]
        before = self._detail_snapshot()
        log_index = len(self.log)
        self.log.append("")
        self.skill_counts[name] = self.skill_counts.get(name, 0) + 1
        if skill.get("cooldown"):
            self.cooldowns[name] = skill["cooldown"] + 1
            self.last_nonbasic = name
        action = {"round": self.round, "phase": self.phase, "skill": name}
        self.boss_actions.append(action)

        def finish():
            detail = "；".join(
                self._detail_notes(before, boss_actor=True) or ["没有产生有效效果"]
            )
            action["detail"] = detail
            self._complete_log(log_index, f"R{self.round} 万相猪皇施放「{name}」，{detail}。")

        if name in ("御前裁决", "裂甲獠牙", "断生獠牙", "绝境剥夺"):
            target = self._target(lowest=name in ("断生獠牙", "绝境剥夺"))
            power = skill["power"]
            if name == "断生獠牙" and target.ratio < 0.35:
                power *= 1.20
            dealt = self._boss_hit(target, power)
            if name == "裂甲獠牙" and dealt > 0:
                self._add_debuff(target, {"type": "破甲", "turns": 2}, "weaken")
            elif name == "绝境剥夺" and dealt > 0:
                self._add_debuff(target, {"type": "封技", "turns": 2})
            finish()
            return
        if name in ("震地三连", "裂界连爪"):
            pool = self.living()
            for target in sorted(pool, key=lambda hero: (hero.ratio, hero.index))[:3]:
                self._boss_hit(target, skill["power"])
            finish()
            return
        if name == "灾厄吐息":
            ailments = ("尸毒", "寒意", "易伤")
            offset = (self.skill_counts[name] - 1) * 2
            for index, target in enumerate(self.living()[:2]):
                if self._boss_hit(target, skill["power"]) > 0:
                    self._add_debuff(
                        target,
                        {"type": ailments[(offset + index) % len(ailments)], "turns": 3},
                        "dot",
                    )
            finish()
            return
        if name == "颠倒敕令":
            for target in sorted(self.living(), key=lambda hero: (-hero.shield, hero.index))[:3]:
                removed = min(target.shield, target.max_hp * 0.10)
                target.shield -= removed
                self._heal_boss(removed * 0.20)
            finish()
            return
        if name in ("禁声钟鸣", "王冠震荡", "血税征收"):
            for target in list(self.living()):
                bonus = self.crown // 3 * 0.08 if name == "王冠震荡" else 0.0
                dealt = self._boss_hit(target, skill["power"] * (1 + bonus))
                if name == "禁声钟鸣" and target.index < 2 and dealt > 0:
                    self._add_debuff(target, {"type": "沉默", "turns": 1})
                if name == "血税征收":
                    dealt += self._hurt(target, target.max_hp * 0.005)
                    self._heal_boss(dealt * 0.20)
            finish()
            return
        if name == "同命撕咬":
            owner = min(
                {hero.owner for hero in self.living()},
                key=lambda key: sum(hero.hp for hero in self.living() if hero.owner == key),
            )
            for target in [hero for hero in self.living() if hero.owner == owner]:
                if self._boss_hit(target, skill["power"]) > 0:
                    self._add_debuff(target, {"type": "受疗降低", "turns": 2}, "weaken")
            finish()
            return
        if name == "饥荒凝视":
            self._add_debuff(
                self._target(healer=True), {"type": "饥荒", "turns": 4, "uses": 2}, "weaken"
            )
            finish()
            return
        if name == "生灭倒转":
            healers = sorted(
                self.living(), key=lambda hero: (hero.role != "治疗", -hero.healed, hero.index)
            )[:2]
            for target in healers:
                if self._boss_hit(target, skill["power"]) > 0:
                    self._add_debuff(target, {"type": "逆疗", "turns": 2}, "weaken")
            finish()
            return
        if name == "四职崩坏":
            for role in ROLES:
                pool = [hero for hero in self.living() if hero.role == role]
                if pool:
                    self._boss_hit(
                        min(pool, key=lambda hero: (hero.ratio, hero.index)), skill["power"]
                    )
            finish()
            return
        if name == "万相轮舞":
            for target in sorted(self.living(), key=lambda hero: (hero.ratio, hero.index))[:4]:
                self._boss_hit(target, skill["power"])
        finish()

    def _warn_signature(self):
        signature = self.phase_data["signature"]
        self.pending = {
            "name": signature["name"],
            "power": signature.get("power", 0.0),
            "due": self.round + signature.get("warning", 1),
            "cycle": self.judgment_serial + 1,
        }
        if self.phase == 5:
            self.judgment_serial += 1
            total_hp = sum(hero.max_hp for hero in self.heroes)
            self.judgment = {
                "cycle": self.judgment_serial,
                "values": {role: 0.0 for role in ROLES},
                "needed": {
                    "坦克": total_hp * 0.09,
                    "输出": self.boss_max_hp * 0.035,
                    "治疗": total_hp * 0.08,
                    "辅助": 1.0,
                },
                "broken": {role: False for role in ROLES},
            }
            self.pending["cycle"] = self.judgment_serial
        self.log.append(f"万相猪皇预警「{signature['name']}」！")

    def _signature_due(self) -> bool:
        return bool(self.pending and self.pending["due"] <= self.round)

    def _resolve_signature(self) -> bool:
        pending = self.pending
        if not pending:
            return False
        name = pending["name"]
        if name == "万疫归一" and not any(hero.debuffs for hero in self.living()):
            pending["due"] = self.round + 1
            self.log.append("全队已无可引爆状态，「万疫归一」延后结算。")
            return False
        self.pending = None
        self.skill_counts[name] = self.skill_counts.get(name, 0) + 1
        before = self._detail_snapshot()
        log_index = len(self.log)
        self.log.append("")
        action = {"round": self.round, "phase": self.phase, "skill": name}
        self.boss_actions.append(action)
        if name == "王座冲锋":
            self._boss_hit(self._target(), pending["power"])
        elif name == "万疫归一":
            for hero in self.living():
                count = min(5, len(hero.debuffs))
                self._hurt(hero, hero.max_hp * 0.02 * count)
                hero.debuffs.clear()
        elif name == "赤月收割":
            reduction = min(0.50, self.resonance_triggers * 0.15)
            for hero in list(self.living()):
                self._boss_hit(hero, pending["power"] * (1 - reduction))
        elif name == "帝威坠落":
            reduction = min(0.48, (12 - self.crown) * 0.04)
            for hero in list(self.living()):
                self._boss_hit(hero, pending["power"] * (1 - reduction))
        elif name == "终局裁定":
            seals = sum(self.judgment["broken"].values()) if self.judgment else 0
            if self.judgment:
                self.judgment_history.append(
                    {
                        "cycle": self.judgment["cycle"],
                        "values": dict(self.judgment["values"]),
                        "broken": dict(self.judgment["broken"]),
                    }
                )
            for hero in list(self.living()):
                self._boss_hit(hero, pending["power"] * (1 - seals * 0.15))
            self.judgment = None
        detail = "；".join(
            self._detail_notes(before, boss_actor=True) or ["没有产生有效效果"]
        )
        action["detail"] = detail
        self._complete_log(
            log_index, f"R{self.round} 万相猪皇施放阶段绝招「{name}」，{detail}！"
        )
        return True

    def _check_seals(self):
        if not self.judgment:
            return
        for role in ROLES:
            if self.judgment["values"][role] >= self.judgment["needed"][role]:
                self.judgment["broken"][role] = True

    def _maybe_warn(self):
        if self.pending:
            return
        local = self.phase_round
        if (
            self.phase < 5
            and local == 2
            and not self.skill_counts.get(self.phase_data["signature"]["name"])
        ):
            self._warn_signature()
        elif self.phase == 5 and local in (1, 4):
            self._warn_signature()

    def _phase_event_start(self):
        """Resolve mechanics that do not consume one of the boss's actions."""
        if self.phase == 2 and self.living():
            disasters = ("尸毒", "冰封", "星爆标记", "腐化护幕")
            disaster = disasters[(self.phase_round - 1) % len(disasters)]
            if disaster == "腐化护幕":
                restored = self._heal_boss(self.boss_max_hp * 0.01)
                detail = f"万相猪皇恢复 {round(restored)} 生命"
            else:
                target = min(self.living(), key=lambda hero: (len(hero.debuffs), hero.index))
                applied = self._add_debuff(
                    target, {"type": disaster, "turns": 2}, "dot"
                )
                detail = (
                    f"{target.label}获得{disaster}，持续 2 回合"
                    if applied
                    else f"{target.label}免疫{disaster}"
                )
            self.log.append(f"R{self.round} 阶段机制·七灾预兆「{disaster}」，{detail}。")

    def _tick_boss_statuses(self):
        for dot in list(self.boss_dots):
            source = dot["source"]
            if source.alive:
                dealt = self.damage_boss(source, self.boss_max_hp * dot["pct"] / 100)
                self.log.append(
                    f"R{self.round} {source.label}施加的{dot['label']}结算，"
                    f"对万相猪皇造成 {round(dealt)} 伤害。"
                )
                if self.phase_five_ultimate_pending:
                    self.phase_five_ultimate_pending = False
                    self._maybe_ultimate()
            dot["turns"] -= 1
            if dot["turns"] <= 0:
                self.boss_dots.remove(dot)
        for delayed in list(self.boss_delays):
            delayed["turns"] -= 1
            if delayed["turns"] <= 0:
                source = delayed["source"]
                if source.alive:
                    dealt = self.damage_boss(
                        source, self.boss_max_hp * delayed["pct"] / 100
                    )
                    self.log.append(
                        f"R{self.round} {source.label}施加的{delayed['label']}爆发，"
                        f"对万相猪皇造成 {round(dealt)} 伤害。"
                    )
                self.boss_delays.remove(delayed)
        for statuses in (self.boss_expose, self.boss_weaken):
            for status in list(statuses):
                status["turns"] -= 1
                if status["turns"] <= 0:
                    statuses.remove(status)
        for status in list(self.boss_corrupting):
            status["turns"] -= 1
            if status["turns"] <= 0:
                self.boss_corrupting.remove(status)

    def _phase_event_end(self):
        if self.phase == 3:
            before = self._detail_snapshot()
            log_index = len(self.log)
            self.log.append("")
            for hero in list(self.living()):
                self._boss_hit(hero, 0.12)
            detail = "；".join(
                self._detail_notes(before, boss_actor=True) or ["没有产生有效效果"]
            )
            self._complete_log(log_index, f"R{self.round} 阶段机制「血潮」，{detail}。")

    def _best_caster(self, role: str) -> _Hero | None:
        pool = [hero for hero in self.living() if hero.role == role]
        if not pool:
            return None
        if role == "坦克":
            return max(
                pool, key=lambda hero: (hero.max_hp + self._hero_stat(hero, "def") * 5, -hero.index)
            )
        if role == "治疗":
            return max(
                pool, key=lambda hero: (self._hero_stat(hero, "atk") + hero.max_hp * 0.1, -hero.index)
            )
        if role == "辅助":
            return max(
                pool,
                key=lambda hero: (
                    self._hero_stat(hero, "spd") + self._hero_stat(hero, "atk") * 0.2,
                    -hero.index,
                ),
            )
        return max(pool, key=lambda hero: (self._hero_stat(hero, "atk"), -hero.index))

    def _maybe_ultimate(self):
        if not self.ultimate_charges:
            return
        warning_cycle = self.pending["cycle"] if self.pending else 0
        if warning_cycle and warning_cycle in self.ultimate_warning_used:
            return
        living = self.living()
        lowest = min(hero.ratio for hero in living)
        debuffs = sum(len(hero.debuffs) for hero in living)
        if self.judgment:
            missing = [role for role in ROLES if not self.judgment["broken"][role]]
            role = max(
                missing,
                key=lambda item: 1 - self.judgment["values"][item] / self.judgment["needed"][item],
            )
        elif lowest < 0.38:
            role = "治疗"
        elif self.pending and self.pending["power"] >= 1:
            role = "坦克"
        elif debuffs >= 4:
            role = "辅助"
        else:
            role = "输出"
        caster = self._best_caster(role) or max(
            living, key=lambda hero: self._hero_stat(hero, "atk")
        )
        role = caster.role
        before = self._detail_snapshot()
        log_index = len(self.log)
        self.log.append("")
        if role == "输出":
            self.damage_boss(caster, caster.atk * 8)
        elif role == "坦克":
            for hero in living:
                hero.invincible_until = self.round
        elif role == "治疗":
            for hero in living:
                self._heal(hero, hero.max_hp * 0.45, caster)
        else:
            for hero in living:
                hero.debuffs.clear()
                hero.cooldown = 0
                hero.skill_cooldowns.clear()
        if self.judgment:
            self.judgment["values"][role] = self.judgment["needed"][role]
            self._check_seals()
        self.ultimate_charges -= 1
        if warning_cycle:
            self.ultimate_warning_used.add(warning_cycle)
        item = {"round": self.round, "phase": self.phase, "role": role, "caster": caster.label}
        self.ultimate_history.append(item)
        detail = "；".join(self._detail_notes(before) or ["没有产生有效效果"])
        self._complete_log(
            log_index, f"R{self.round} {caster.label}发动{role}极限技，{detail}！"
        )

    def _boss_turn(self):
        actions = self.phase_data["actions"]
        if self.boss_root:
            actions = min(actions, 1)
            self.log.append("万相猪皇受禁锢影响，本回合只能行动一次。")
        if self.boss_stun:
            actions = max(0, actions - 1)
            self.log.append("万相猪皇受眩晕影响，本回合少行动一次。")
        for _ in range(actions):
            if not self.living() or self.boss_hp <= 0:
                break
            if self.phase_five_ultimate_pending:
                self.phase_five_ultimate_pending = False
                self._maybe_ultimate()
                if self.boss_hp <= 0:
                    break
            if self._signature_due():
                if not self._resolve_signature():
                    self._use_skill(self._choose_skill())
            else:
                self._use_skill(self._choose_skill())
        self.boss_silence = max(0, self.boss_silence - 1)
        self.boss_root = max(0, self.boss_root - 1)
        self.boss_stun = max(0, self.boss_stun - 1)
        if self.phase == 5 and self.p5_rounds > 5 and self.living():
            before = self._detail_snapshot()
            action = {"round": self.round, "phase": 5, "skill": "绝境余波", "extra": True}
            self.boss_actions.append(action)
            for hero in list(self.living()):
                self._boss_hit(hero, 0.35)
            detail = "；".join(
                self._detail_notes(before, boss_actor=True) or ["没有产生有效效果"]
            )
            action["detail"] = detail
            self.log.append(f"R{self.round} 万相猪皇发动「绝境余波」，{detail}。")

    def _soft_enrage(self):
        """Apply unavoidable raid pressure so late fights end through combat, not a timer."""
        enrage_round = int(self.config.get("enrage_round", DESPAIR_MAX_ROUNDS))
        layer = self.round - enrage_round
        if layer <= 0 or not self.living():
            return
        base = float(self.config.get("enrage_aoe_base_pct", 0.0))
        step = float(self.config.get("enrage_aoe_step_pct", 0.0))
        pct = base + (layer - 1) * step
        if pct <= 0:
            return
        before = self._detail_snapshot()
        action = {
            "round": self.round,
            "phase": self.phase,
            "skill": "绝境威压",
            "extra": True,
            "layer": layer,
        }
        self.boss_actions.append(action)
        for hero in list(self.living()):
            self._hurt(hero, hero.max_hp * pct / 100, linked=False)
        detail = "；".join(self._detail_notes(before, boss_actor=True) or ["没有产生有效效果"])
        action["detail"] = detail
        self.log.append(
            f"R{self.round} 万相猪皇发动软狂暴「绝境威压」第 {layer} 层"
            f"（全队 {pct:g}% 最大生命伤害），{detail}。"
        )

    def run(self) -> dict:
        max_rounds = min(DESPAIR_MAX_ROUNDS, self.config.get("max_rounds", DESPAIR_MAX_ROUNDS))
        while self.round < max_rounds:
            if not self.living() or self.boss_hp <= 0:
                break
            self.round += 1
            self.phase_round += 1
            if self.phase == 5:
                self.p5_rounds += 1
                self.damage_bonus += 0.10
            phase_before_tick = self.phase
            self._tick_boss_statuses()
            if self.phase != phase_before_tick and self.phase_round == 0:
                # A start-of-round DoT crossed the phase gate.  The newly entered
                # phase is already acting this round, so its first event is index 1.
                self.phase_round = 1
            if self.boss_hp <= 0:
                break
            if self.round == 5 and not self.round_five_charge:
                self.ultimate_charges = min(2, self.ultimate_charges + 1)
                self.round_five_charge = True
            for name in list(self.cooldowns):
                self.cooldowns[name] = max(0, self.cooldowns[name] - 1)
            self._maybe_warn()
            self._phase_event_start()
            self._maybe_ultimate()
            phase_at_start = self.phase
            for hero in sorted(
                self.living(), key=lambda unit: (-self._hero_stat(unit, "spd"), unit.index)
            ):
                self._hero_turn(hero)
                if self.boss_hp <= 0:
                    break
            if self.boss_hp <= 0:
                break
            if self.phase_five_ultimate_pending:
                self.phase_five_ultimate_pending = False
                self._maybe_ultimate()
                if self.boss_hp <= 0:
                    break
            self._boss_turn()
            self._phase_event_end()
            self._soft_enrage()
            if phase_at_start == 4 and self.phase == 4 and self.vulnerable_turns:
                self.vulnerable_turns -= 1
                if not self.vulnerable_turns and not self.crown_restored:
                    self.crown = 6
                    self.crown_restored = True
                    self.log.append("崩裂王冠恢复 6 层。")
        return self.result(max_rounds=max_rounds)

    def result(self, *, max_rounds: int | None = None) -> dict:
        won = self.boss_hp <= 0
        limit = max_rounds or min(
            DESPAIR_MAX_ROUNDS, self.config.get("max_rounds", DESPAIR_MAX_ROUNDS)
        )
        phase_reports = [*self.phase_reports]
        current_report = self._phase_report()
        if not phase_reports or phase_reports[-1]["phase"] != current_report["phase"]:
            phase_reports.append(current_report)
        return {
            "winner": 0 if won else 1,
            "won": won,
            "timeout": self.round >= limit and not won and bool(self.living()),
            "rounds": self.round,
            "seed": self.seed,
            "heroes": [
                {
                    "index": hero.index,
                    "seat": hero.data.get("seat", hero.index),
                    "owner": hero.owner,
                    "label": hero.label,
                    "role": hero.role,
                    "hp": round(hero.hp),
                    "max_hp": round(hero.max_hp),
                    "alive": hero.alive,
                    "dealt": round(hero.dealt),
                    "healed": round(hero.healed),
                    "taken": round(hero.taken),
                    "poise": round(hero.poise),
                }
                for hero in self.heroes
            ],
            "boss": {
                "id": self.config["id"],
                "name": self.config["name"],
                "hp": round(self.boss_hp),
                "max_hp": round(self.boss_max_hp),
                "phase": self.phase,
                "collectible": False,
                "skills": sorted(BOSS_SKILL_NAMES),
                "skill_counts": dict(sorted(self.skill_counts.items())),
                "actions": self.boss_actions,
            },
            "phases": self.phase_history,
            "phase_reports": phase_reports,
            "log": self.log,
            "stats": {
                "poise_breaks": self.poise_breaks,
                "resonance_triggers": self.resonance_triggers,
                "crown": self.crown,
                "revive_blocked": self.revive_blocked,
            },
            "judgment": self.judgment,
            "judgments": self.judgment_history,
            "ultimate": {
                "charges": self.ultimate_charges,
                "history": self.ultimate_history,
            },
        }


def simulate_despair(heroes: list[dict], seed: int = 1, boss: dict | None = None) -> dict:
    """Simulate an entire Despair encounter and return a stable JSON-ready result."""
    return DespairBattle(heroes, seed=seed, boss=boss).run()
