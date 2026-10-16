"""Pig raids: four players' pigs against three bosses in a row."""

import random

from .battle import CRIT_MULTIPLIER, FURY_ROUND, FURY_STEP, SKILL_SLOTS, _Battle, _Unit, fighter
from .raid_bosses import BOSSES, BossMechanics
from .raid_events import field_event

PARTY_SIZE = 4
TIMEOUT_MINUTES = 10
MAX_RAID_ROUNDS = 25
REST_HEAL = 0.4
# When continuing to the next boss, one fallen pig may rejoin at this HP ratio.
REST_REVIVE_HP = 0.4
ULTIMATE_ROUND = 5
ULTIMATE_ROLES = frozenset(("输出", "坦克", "治疗", "辅助"))
ULTIMATE_HEAL_PCT = 45
ULTIMATE_DAMAGE_POWER = 8.0
ULTIMATE_BOSS_HP_CAP = 0.22
POISE_MAX = 160.0
POISE_MAX_BREAKS = 2
POISE_SECOND_CYCLE_FACTOR = 0.65
POISE_BREAK_DAMAGE = 1.10
# Bosses ignore party level: full kit + base stats, scaled by stage, dungeon and boss multipliers.
STAGE_HP = (7.0, 8.2, 9.6)
# Recalibrated after lethal hits began correctly triggering last-stand effects:
# the small increase preserves each dungeon's established clear-rate band
# without weakening the defensive mechanic or relaxing the balance gates.
STAGE_ATK = (2.31, 2.57, 2.89)

DUNGEONS = (
    {
        "key": 1,
        "name": "冰封猪圈",
        "intro": "寒风刺骨的废弃猪圈，越往里越冷。",
        "bosses": ("goblin-pig", "frozen-pig", "everest-pig"),
    },
    {
        "key": 2,
        "name": "机械猪厂",
        "intro": "嗡嗡作响的自动化猪厂，到处是故障的机器。",
        "bosses": ("error-404-pig", "mechanical-pig", "cyberpunk-pig"),
    },
    {
        "key": 3,
        "name": "猪神殿",
        "intro": "供奉着猪神的古老神殿，诅咒与神迹并存。",
        "bosses": ("demon-pig", "chained_crown_pig", "pig_god"),
    },
    {
        "key": 4,
        "name": "亡灵猪墓",
        "intro": "埋着历代老猪的墓园，半夜总有骨头在响。",
        "bosses": ("zombie-pig", "pighub0876", "skeleton-pig"),
    },
    {
        "key": 5,
        "name": "深海猪渊",
        "intro": "一路潜到马里亚纳海沟，水压越来越大。",
        "bosses": ("pighub0233", "pighub0007", "pighub0830"),
    },
    {
        "key": 6,
        "name": "星际猪港",
        "intro": "停满了 UFO 的太空港，外面是一整片猪星团。",
        "bosses": ("alien-pig", "pighub0872", "pighub0336"),
    },
    {
        "key": 7,
        "name": "熔岩猪窑",
        "intro": "地底窑火昼夜不灭，越往下皮肤越烫。",
        "bosses": ("pighub0315", "pighub0710", "pighub0116"),
    },
)

# Dungeon-level tuning layered on top of stage and signature-boss scaling.
# Keeping this separate from boss mechanics preserves each encounter's
# counterplay while giving the seven three-boss runs distinct progression.
# Values are calibrated against the fixed Lv5 four-role benchmark party.
DUNGEON_BOSS_SCALE = {
    1: (1.20, 1.10),  # 冰封猪圈
    2: (1.55, 1.10),  # 机械猪厂: smarter AI still has to survive sustained attrition
    3: (1.20, 1.10),  # 猪神殿
    4: (1.10, 1.02),  # 亡灵猪墓: mechanics already punish failed recovery
    5: (1.28, 1.10),  # 深海猪渊
    6: (1.08, 1.03),  # 星际猪港 already has punishing signature mechanics
    7: (1.20, 1.10),  # 熔岩猪窑
}
_BOSS_DUNGEON_SCALE = {
    slot: DUNGEON_BOSS_SCALE[item["key"]] for item in DUNGEONS for slot in item["bosses"]
}

# Small encounter-specific corrections keep a dungeon's fresh-party curve
# moving toward its final boss instead of making an early attrition check the
# only meaningful gate.
BOSS_ENCOUNTER_SCALE = {
    "cyberpunk-pig": (2.05, 1.00),
    "everest-pig": (1.08, 1.03),
    "pig_god": (1.08, 1.03),
    "skeleton-pig": (1.08, 1.03),
    "pighub0116": (1.10, 1.03),
}

GUARD_SKILL = {
    "name": "守护之光",
    "text": "替神明挡下一切。",
    "cd": 0,
    "when": None,
    "passive": False,
    "effects": [{"type": "damage", "power": 0.8}],
}


def dungeon(key) -> dict:
    for item in DUNGEONS:
        if str(item["key"]) == str(key).strip("#＃号") or item["name"] == str(key):
            return item
    raise KeyError(key)


def mechanics_for(pig_id: str) -> type[BossMechanics]:
    return BOSSES.get(pig_id, BossMechanics)


def mechanic_details(pig_id: str) -> tuple:
    """Mechanic text plus automatic, build-facing counterplay for raid UIs."""
    cls = mechanics_for(pig_id)
    counters = cls.COUNTERS or ("",) * len(cls.MECHANICS)
    return tuple(
        (name, f"{text}｜反制：{counter}" if counter else text)
        for (name, text), counter in zip(cls.MECHANICS, counters)
    )


def recommended_functions(pig_id: str) -> tuple[str, ...]:
    """Real pig-pen tags that satisfy this boss's primary build check."""
    return mechanics_for(pig_id).CORE_TAGS


def counter_failure(pig_id: str) -> str:
    """What happens when a party ignores the boss's primary build check."""
    return mechanics_for(pig_id).CORE_FAILURE


def boss_level(levels: list[int]) -> int:
    """Deprecated: bosses no longer scale with party level. Kept for old call sites."""
    return max(1, round(sum(levels) / len(levels))) if levels else 1


def boss_fighter(pig: dict, entry: dict, stage: int, slot_id: str, label: str) -> dict:
    """Raid boss at encounter strength: base stats, full kit, no level growth."""
    cls = mechanics_for(slot_id)
    # Boss strength belongs to encounter tuning, never to the collectible pig's rarity.
    unit = fighter(pig, entry, 1, label, rarity="普通")
    unit["level"] = 0
    unit["skills"] = list(entry["skills"][:SKILL_SLOTS])
    unit["locked"] = []
    stats = unit["stats"]
    unit["dot_basis"] = stats["hp"]
    dungeon_hp, dungeon_atk = _BOSS_DUNGEON_SCALE.get(slot_id, (1.0, 1.0))
    encounter_hp, encounter_atk = BOSS_ENCOUNTER_SCALE.get(slot_id, (1.0, 1.0))
    stats["hp"] = round(stats["hp"] * STAGE_HP[stage - 1] * cls.HP * dungeon_hp * encounter_hp)
    stats["atk"] = round(
        stats["atk"] * STAGE_ATK[stage - 1] * cls.ATK * dungeon_atk * encounter_atk
    )
    stats["def"] = round(stats["def"] * cls.DEF)
    return unit


class _RaidUnit(_Unit):
    def __init__(self, data: dict, side: int):
        super().__init__(data)
        self.side = side
        self.seat = data.get("seat")
        self.mods = dict(data.get("mods") or {})
        self.dot_basis = data.get("dot_basis", self.max_hp)
        self.vuln = 0
        self.chill = 0
        self.frost_ward = False
        self.charm = False
        self.glitch = False
        self.blind = False
        self.zeroed = set()
        # Rounds left off the field (abducted, or a boss away on its mothership).
        self.away = 0
        self.possessed = False
        self.lured = False
        self.marked = False

    def stat(self, key: str) -> float:
        if key in self.zeroed or (key == "dodge" and self.blind):
            return 0.0
        value = super().stat(key)
        bonus = self.mods.get(key, 0)
        if key in ("crit", "dodge"):
            return max(0.0, min(75.0, value + bonus))
        return max(1.0 if key != "def" else 0.0, value * (1 + max(-90, bonus) / 100))


class _RaidBattle(_Battle):
    def __init__(self, heroes: list[dict], boss: dict, slot_id: str, cfg: dict, seed: int):
        self.rng = random.Random(seed)
        self.log = []
        self.round = 0
        # Shared with 1v1 `_Battle` skill paths (copy, reactive triggers, leech).
        self._copy_scale = 1.0
        self._cast_output_scale = 1.0
        self._leech_pool = 0.0
        self._in_trigger = False
        self._turn_triggered = set()
        self.cfg = cfg
        self.heroes = [_RaidUnit(data, 0) for data in heroes]
        self.boss = _RaidUnit(boss, 1)
        self.foes = [self.boss]
        self.units = (self.heroes[0], self.boss)
        self.mech = mechanics_for(slot_id)(cfg.get("disabled"))
        self.fury_round = cfg.get("fury_round", FURY_ROUND)
        self.cheer = 1.0
        self.sneezer = None
        self.notes = []
        self.falls = []
        self.field_events = []
        self.void_note = ""
        self.acting = None
        self._stat_source = None
        # Damage each unit dealt to the other side, for the DPS table.
        self.dealt = {}
        # Effective HP restored and damage actually borne (including shields).
        self.healed = {}
        self.taken = {}
        # Stable internal ordering is also used as an AI tie-breaker.
        self.everyone = []
        # A balanced four-role party earns one automatic limit break per boss.
        # Eligibility is fixed from the starting roster; deaths and temporary
        # removal only affect who is currently able to cast it.
        roles = [hero.role for hero in self.heroes]
        self.ultimate_eligible = len(roles) == PARTY_SIZE and set(roles) == ULTIMATE_ROLES
        self.ultimate_used = False
        self.ultimate_round = None
        self.ultimate_role = None
        self._ultimate_guard_round = None
        # Boss resilience is a separate tactical resource.  It is intentionally
        # encounter-local and capped at two breaks so control cannot loop forever.
        self.poise_max = POISE_MAX
        self.poise = self.poise_max
        self.poise_breaks = 0
        self.poise_broken_until = None
        self.poise_locked = False
        self._poise_interrupt_pending = False
        self._poise_skill = None
        self._poise_damage_awarded = False
        self.poise_contributions = {id(hero): 0.0 for hero in self.heroes}
        for unit in self.heroes + [self.boss]:
            self._enlist(unit, "boss" if unit is self.boss else "hero")
        self._configure()

    def _enlist(self, unit, kind: str):
        unit.uid = len(self.everyone)
        unit.kind = "ally" if kind == "hero" and unit.seat is None else kind
        self.everyone.append(unit)

    def _configure(self):
        cfg, boss = self.cfg, self.boss
        if cfg.get("boss_hp"):
            boss.max_hp = round(boss.max_hp * (1 + cfg["boss_hp"] / 100))
            boss.hp = float(boss.max_hp)
        for key, value in cfg.get("boss_mods", {}).items():
            boss.mods[key] = boss.mods.get(key, 0) + value
        if cfg.get("boss_stun"):
            boss.stun, boss.stun_label = cfg["boss_stun"], "打盹"
        for hero in self.heroes:
            for key, value in cfg.get("team_mods", {}).items():
                hero.mods[key] = hero.mods.get(key, 0) + value
            for key, value in cfg.get("unit_mods", {}).get(hero.seat, {}).items():
                hero.mods[key] = hero.mods.get(key, 0) + value
            if cfg.get("unit_stun", {}).get(hero.seat):
                hero.stun, hero.stun_label = cfg["unit_stun"][hero.seat], "混乱"
            hero.shield += hero.max_hp * cfg.get("shield", 0) / 100
            hero.charm = bool(cfg.get("charm"))
            hero.frost_ward = bool(cfg.get("frost_ward"))

    # ----- helpers used by boss mechanics and field events -----

    def note(self, text: str, mech: int | None = None, field: str = "", targets=(), tag: str = ""):
        self.notes.append((text, mech, field, tuple(targets), tag))

    def flush(self):
        notes, falls = self.notes, self.falls
        self.notes, self.falls = [], []
        for text, _mech, _field, _targets, _tag in notes:
            self.say(text)
        for text in falls:
            self.say(text)
        if self.notes or self.falls:
            self.flush()

    def alive_heroes(self) -> list:
        return [h for h in self.heroes if h.alive]

    def present_heroes(self) -> list:
        """Alive heroes that are on the field and can be targeted."""
        return [h for h in self.heroes if h.alive and not h.away]

    def alive_units(self) -> list:
        return [u for u in self.heroes + self.foes if u.alive]

    @property
    def poise_broken(self) -> bool:
        return self.poise_broken_until is not None and self.round <= self.poise_broken_until

    def _reduce_poise(self, unit, amount: float, reason: str) -> float:
        """Record real tactical value; return the effective resilience loss."""
        if (
            unit is None
            or unit.side != 0
            or amount <= 0
            or not self.boss.alive
            or self.poise_broken
            or self.poise_locked
        ):
            return 0.0
        factor = 1.0 if self.poise_breaks == 0 else POISE_SECOND_CYCLE_FACTOR
        loss = min(self.poise, amount * factor)
        if loss <= 0:
            return 0.0
        self.poise -= loss
        self.poise_contributions[id(unit)] = self.poise_contributions.get(id(unit), 0.0) + loss
        if unit.role in ("坦克", "辅助"):
            self.note(f"{unit.label} 的{reason}削减 {loss:.1f} 点韧性（{self.poise:.1f}/{self.poise_max:.0f}）")
        if self.poise <= 0:
            self.poise = 0.0
            self.poise_breaks += 1
            # Remainder of this round plus the next complete round.  Only one
            # boss action is interrupted; round hooks and signature mechanics remain.
            self.poise_broken_until = self.round + 1
            self._poise_interrupt_pending = True
            self.note(
                f"{self.boss.label} 韧性归零，进入破防窗口！受到伤害提高 10%，下一次行动被打断"
            )
        return loss

    def _recover_poise(self):
        if self.poise_broken_until is None or self.round < self.poise_broken_until:
            return
        self.poise_broken_until = None
        self._poise_interrupt_pending = False
        self.poise = self.poise_max
        self.poise_locked = self.poise_breaks >= POISE_MAX_BREAKS
        if self.poise_locked:
            self.say(f"{self.boss.label} 恢复韧性，并适应了队伍战术；本场不会再次破防")
        else:
            self.say(f"{self.boss.label} 恢复 {self.poise_max:.0f} 点韧性，后续削韧效率降低")

    @staticmethod
    def _burst_power(skill: dict) -> float:
        return sum(
            effect.get("power", 0) * effect.get("hits", 1)
            for effect in skill.get("effects", ())
            if effect.get("type") == "damage"
        )

    @staticmethod
    def _boss_positive_state(unit) -> int:
        return sum(
            (
                bool(unit.shield),
                bool(unit.evade),
                bool(unit.thorns),
                bool(unit.reflects),
                bool(unit.mitigates),
                bool(unit.hot_boost),
                any(buff.get("pct", 0) > 0 for buff in unit.buffs),
            )
        )

    @staticmethod
    def _boss_negative_state(unit) -> int:
        return (
            len(unit.dots)
            + len(unit.weaken)
            + len(unit.expose)
            + len(unit.corrupting)
            + len(unit.marks)
            + bool(unit.stun)
            + bool(unit.silence)
            + bool(unit.root)
            + sum(1 for buff in unit.buffs if buff.get("pct", 0) < 0)
        )

    def _ultimate_debuff_urgency(self, unit) -> float:
        """All removable base and raid-only states, using the battle AI's weights."""
        urgency = self._cleanse_urgency(unit)
        urgency += 20 * len(unit.zeroed)
        return urgency

    def _ultimate_choice(self):
        """Pick an available caster deterministically from the current battle state."""
        available = {hero.role: hero for hero in self.present_heroes()}
        candidates = []

        dps = available.get("输出")
        if dps is not None and self.boss.alive and not self.boss.away:
            # Damage is the healthy-state default, especially during a break
            # window or when the capped strike can finish the boss.  This must
            # be a real score rather than a token fallback: ordinary cooldown
            # churn is present in almost every round-five battle and used to
            # make support win every neutral decision.
            score = 90.0
            estimated = min(
                dps.stat("atk")
                * ULTIMATE_DAMAGE_POWER
                * self.fury()
                * 60
                / (60 + self.boss.stat("def")),
                self.boss.max_hp * ULTIMATE_BOSS_HP_CAP,
            )
            if estimated >= self.boss.hp:
                score = 190.0
            else:
                if self.poise_broken:
                    score += 35.0
                if self.boss.ratio <= 0.50:
                    score += 15.0
                if self.boss.ratio <= 0.25:
                    score += 20.0
            candidates.append((score, 0, dps))

        healer = available.get("治疗")
        living = self.alive_heroes()
        if healer is not None and living:
            healable = [
                (hero, max(0.0, self.mech.heal_factor(self, hero)))
                for hero in living
            ]
            healable = [(hero, factor) for hero, factor in healable if factor > 0]
            missing = sum((1 - hero.ratio) * factor for hero, factor in healable)
            lowest = min((hero.ratio for hero, _ in healable), default=1.0)
            if lowest <= 0.30:
                score = 175.0 + (0.30 - lowest) * 100
            elif missing >= 1.0:
                score = 150.0 + missing
            elif missing >= 0.65:
                score = 110.0 + missing
            else:
                score = 0.0
            if score:
                candidates.append((score, 3, healer))

        tank = available.get("坦克")
        if tank is not None and living and self.boss.alive and not self.boss.away:
            # Conservative estimate of the boss's next ordinary hit.  This is
            # deliberately free of RNG and does not call mechanics with side effects.
            frailest = min(living, key=lambda hero: (hero.hp, hero.uid))
            expected = (
                self.boss.stat("atk")
                * self.fury()
                * 60
                / (60 + frailest.stat("def"))
            )
            if expected >= frailest.hp * 0.65:
                candidates.append((155.0 + expected / max(1, frailest.hp), 2, tank))
            elif sum(hero.ratio <= 0.55 for hero in living) >= 2:
                candidates.append((120.0, 2, tank))

        support = available.get("辅助")
        if support is not None and living:
            debuffs = sum(self._ultimate_debuff_urgency(hero) for hero in living)
            if debuffs >= 70:
                candidates.append((145.0 + min(35.0, debuffs - 70), 1, support))
            elif debuffs >= 45:
                candidates.append((115.0 + min(25.0, debuffs - 45), 1, support))
            elif debuffs >= 30:
                candidates.append((98.0 + min(12.0, debuffs - 30), 1, support))

            # Only count real, non-basic active skills.  A reset is worth the
            # one-per-boss charge when several different heroes recover long
            # cooldown techniques, not merely because two incidental cooldown
            # turns exist somewhere in the party.
            locked = []
            for hero in living:
                resettable = {skill["name"] for skill in hero.actives[1:]}
                for name, turns in hero.cooldowns.items():
                    if name in resettable and turns > 0:
                        locked.append((hero, turns))
            locked_heroes = {id(hero) for hero, _ in locked}
            long_locks = sum(turns >= 2 for _, turns in locked)
            saved_turns = sum(turns for _, turns in locked)
            exceptional_reset = (
                len(locked_heroes) >= 3
                and (long_locks >= 3 or len(locked) >= 5)
            )
            # A cooldown-only reset is reserved for an actual team burst
            # window.  Outside it, every normal round-five rotation has many
            # skills cooling down, so treating raw cooldown volume as urgent
            # would still make support the permanent default.
            if exceptional_reset and self.poise_broken:
                reset_score = 95.0 + min(
                    35.0,
                    long_locks * 4.0 + max(0, len(locked) - 3) * 3.0 + saved_turns,
                )
                candidates.append((reset_score, 1, support))

        if not candidates:
            return None
        # Score decides urgency.  The second key makes exact ties stable:
        # healer, tank, support, then damage.
        return max(candidates, key=lambda item: (item[0], item[1]))[2]

    def _ultimate_cleanse(self, unit):
        self._cleanse(unit)
        unit.vuln = 0
        unit.chill = 0
        unit.glitch = False
        unit.possessed = False
        unit.lured = False
        unit.marked = False
        unit.blind = False
        unit.zeroed.clear()

    def _try_ultimate(self) -> bool:
        """Use the balanced party's once-per-boss limit break when it is valuable."""
        if (
            not self.ultimate_eligible
            or self.ultimate_used
            or self.round < ULTIMATE_ROUND
            or self.decided() is not None
        ):
            return False
        caster = self._ultimate_choice()
        if caster is None:
            # For example, the DPS is abducted while an otherwise healthy team
            # has no urgent defensive need.  Preserve the once-per-fight charge.
            return False

        role = caster.role
        living = self.alive_heroes()
        self.ultimate_used = True
        self.ultimate_round = self.round
        self.ultimate_role = role
        if role == "输出":
            effect = {"type": "damage", "power": ULTIMATE_DAMAGE_POWER, "ultimate": True}
            raw = caster.stat("atk") * ULTIMATE_DAMAGE_POWER * self.fury()
            raw *= 60 / (60 + self.boss.stat("def"))
            raw = self.adjust_hit(caster, self.boss, raw, effect)
            raw = min(max(0.0, raw), self.boss.max_hp * ULTIMATE_BOSS_HP_CAP)
            acting, self.acting = self.acting, caster
            try:
                dealt = self.hurt(self.boss, raw, attacker=caster)
                self.after_hit(caster, self.boss, dealt)
            finally:
                self.acting = acting
            self.say(f"{caster.label} 发动极限技「终极猛攻」，对 {self.boss.label} 造成 {round(dealt)} 伤害！")
        elif role == "坦克":
            self._ultimate_guard_round = self.round
            self.say(f"{caster.label} 发动极限技「绝对守护」，全队在本回合免疫所有伤害！")
        elif role == "治疗":
            notes = []
            for hero in living:
                healed = self.heal(
                    hero,
                    hero.max_hp * ULTIMATE_HEAL_PCT / 100,
                    source=caster,
                )
                notes.append(f"{hero.label}+{healed}")
            self.say(f"{caster.label} 发动极限技「生命奇迹」，全体回复生命：{'、'.join(notes)}！")
        else:
            refreshed = 0
            cleansed = 0
            for hero in living:
                if any(turns > 0 for turns in hero.cooldowns.values()):
                    refreshed += 1
                if self._ultimate_debuff_urgency(hero):
                    cleansed += 1
                hero.cooldowns.clear()
                hero.used_skills.clear()
                self._ultimate_cleanse(hero)
            self.say(
                f"{caster.label} 发动极限技「战术重启」，刷新全队技能冷却并清除所有负面状态"
                f"（刷新 {refreshed} 名，净化 {cleansed} 名）！"
            )
        return True

    def strike(self, attacker, target, power: float, *, crit: bool = False) -> float:
        """A mechanic's hit: never misses, ignores the attacker's skill list."""
        if not target.alive:
            return 0.0
        raw = attacker.stat("atk") * power * self.rng.uniform(0.9, 1.1) * self.fury()
        raw *= self.mech.build_outgoing_factor(self, attacker)
        if crit:
            raw *= CRIT_MULTIPLIER
        raw *= 60 / (60 + target.stat("def"))
        raw *= 1 + target.vuln / 100
        acting, self.acting = self.acting, attacker
        try:
            return self.hurt(target, max(1.0, raw))
        finally:
            self.acting = acting

    def summon(self, label: str, hp_pct: float, atk_pct: float, skill: dict = GUARD_SKILL):
        base = self.boss.data
        stats = dict(base["stats"])
        stats["hp"] = max(1, round(self.boss.max_hp * hp_pct))
        stats["atk"] = max(1, round(stats["atk"] * atk_pct))
        data = {
            **base,
            "label": label,
            "stats": stats,
            "skills": [skill],
            "dot_basis": stats["hp"],
            "start_hp": stats["hp"],
        }
        unit = _RaidUnit(data, 1)
        self.foes.append(unit)
        self._enlist(unit, "minion")
        return unit

    def summon_from(self, hero, label: str, hp_pct: float):
        """Raise a fallen hero on the enemy side, keeping its own skills."""
        stats = dict(hero.data["stats"])
        stats["hp"] = max(1, round(hero.max_hp * hp_pct))
        data = {
            **hero.data,
            "label": label,
            "stats": stats,
            "seat": None,
            "mods": {},
            "dot_basis": stats["hp"],
            "start_hp": stats["hp"],
        }
        unit = _RaidUnit(data, 1)
        self.foes.append(unit)
        self._enlist(unit, "minion")
        return unit

    # ----- engine overrides -----

    def fury(self) -> float:
        return (1 + FURY_STEP * max(0, self.round - self.fury_round)) * self.cheer

    def other(self, unit):
        return self.target_for(unit)

    def allies_of(self, unit):
        if unit.side == 0:
            return [h for h in self.heroes if h.alive and not h.away]
        return [f for f in self.foes if f.alive and not f.away]

    def foes_of(self, unit):
        if unit.side == 0:
            return [f for f in self.foes if f.alive and not f.away]
        return [h for h in self.heroes if h.alive and not h.away]

    def target_for(self, unit):
        if unit.side == 0:
            foes = [f for f in self.foes if f.alive and not f.away]
            guards = [f for f in foes if f is not self.boss]
            if guards and self.mech.GUARDS_FIRST:
                return self.rng.choice(guards)
            return self.rng.choice(foes) if foes else None
        heroes = self.present_heroes()
        if not heroes:
            return None
        # Summons and redirects: prefer redirect/taunt holders when present.
        redirected = [h for h in heroes if getattr(h, "redirect", 0) > 0]
        if redirected and unit is not self.boss:
            return self.rng.choice(redirected)
        if unit is self.boss:
            return self.mech.pick_target(self, heroes)
        return self.rng.choice(heroes)

    def action_target(self, unit, enemy):
        # Keep a living target through the chain for readable replays; retarget
        # immediately when an earlier link killed it.
        return enemy if enemy is not None and enemy.alive else self.target_for(unit)

    def max_skills_per_turn(self, unit):
        # Existing boss extra-action mechanics are already their action economy.
        # Only player pigs receive skill chains, avoiding accidental boss burst
        # multiplication and preserving summon balance.
        return 2 if unit.side == 0 else 1

    def apply_passive(self, unit, effect):
        if effect["type"] == "buff" and effect.get("target") == "enemy":
            targets = [self.boss] if unit.side == 0 else self.heroes
            for target in targets:
                target.buffs.append(
                    {"stat": effect["stat"], "pct": effect["pct"], "turns": effect.get("turns", 99)}
                )
            return
        super().apply_passive(unit, effect)

    def hurt(self, unit, amount: float, source: str = "", attacker=None) -> float:
        if (
            unit.side == 0
            and unit.alive
            and self._ultimate_guard_round == self.round
            and self.round >= ULTIMATE_ROUND
        ):
            return 0.0
        was_alive = unit.alive
        dealt = super().hurt(unit, amount, source=source, attacker=attacker)
        if unit.side == 0 and dealt > 0:
            self.taken[id(unit)] = self.taken.get(id(unit), 0.0) + dealt
        credited = attacker if attacker is not None else self._stat_source or self.acting
        if credited is not None and credited.side != unit.side:
            self.dealt[id(credited)] = self.dealt.get(id(credited), 0.0) + dealt
        if dealt > 0:
            if (
                unit.side == 0
                and unit.role == "坦克"
                and credited is not None
                and credited.side == 1
            ):
                value = min(6.0, 1.0 + dealt / max(1, unit.max_hp) * 10)
                self._reduce_poise(unit, value, "坚守承伤")
        # Damage-threshold hooks must observe lethal crossings too.  Some bosses
        # (notably Error404) restore themselves at a mandatory threshold; calling
        # this before fall handling lets that recovery happen without teaching
        # the generic engine about individual encounters.
        if unit is self.boss and was_alive and dealt > 0:
            self.mech.damaged(self)
        if was_alive and not unit.alive and unit is self.boss and self.mech.prevent_fall(self):
            return dealt
        if was_alive and not unit.alive and unit.charm:
            unit.charm = False
            unit.hp = 1.0
            self.note(f"平安符护住了 {unit.label}，保留 1 点生命")
        if was_alive and not unit.alive:
            self.falls.append(f"{unit.label} 倒下了")
            if unit.side == 0 and unit.seat is not None:
                self.mech.hero_fell(self, unit)
        return dealt

    def announce_falls(self, unit, enemy):
        self.flush()

    def dot_amount(self, unit, dot):
        # The tick is hurt right after this; credit it to whoever applied the dot.
        self.acting = dot.get("source")
        amount = unit.dot_basis * dot["pct"] / 100 * self.mech.dot_factor(self, unit)
        if unit is self.boss:
            amount *= self.mech.build_incoming_factor(self)
        if unit.side == 0 and self.cfg.get("dot_cut"):
            amount *= 1 - self.cfg["dot_cut"] / 100
        return amount

    def heal(self, unit, amount: float, source=None) -> int:
        healed = super().heal(
            unit,
            amount * self.mech.heal_factor(self, unit),
            source=source,
        )
        credited = source if source is not None else self._stat_source or self.acting
        if healed > 0 and credited is not None and credited.side == 0:
            self.healed[id(credited)] = self.healed.get(id(credited), 0.0) + healed
        return healed

    def check_revive(self, unit):
        super().check_revive(unit)

    def crit_roll(self, unit, enemy) -> bool:
        return self._judge_crit(unit, enemy)

    def _judge_crit(self, unit, enemy) -> bool:
        crit = super().crit_roll(unit, enemy)
        if unit.lured and enemy is self.boss:
            unit.lured, unit.marked = False, True
            return True
        if crit and enemy is self.boss:
            return self.mech.crit_against(self, unit)
        return crit

    def apply(self, unit, enemy, effects, skill):
        previous_skill = self._poise_skill
        previous_awarded = self._poise_damage_awarded
        previous_source = self._stat_source
        self._poise_skill = (unit, skill)
        self._poise_damage_awarded = False
        self._stat_source = unit
        try:
            super().apply(unit, enemy, effects, skill)
        finally:
            self._poise_skill = previous_skill
            self._poise_damage_awarded = previous_awarded
            self._stat_source = previous_source

    def pierce(self, unit, enemy, effect):
        value = super().pierce(unit, enemy, effect)
        return self.mech.pierce(self, value) if enemy is self.boss else value

    def adjust_hit(self, unit, enemy, raw, effect):
        raw *= 1 + enemy.vuln / 100
        raw *= self.mech.outgoing_factor(self, unit, enemy)
        raw *= self.mech.build_outgoing_factor(self, unit)
        if unit is self.boss and enemy.marked:
            enemy.marked = False
            raw *= 2
            self.note(f"灯笼鱼的光还亮在 {enemy.label} 身上，这一下伤害翻倍")
        if enemy.side == 1:
            raw = self.mech.foe_hit(self, unit, enemy, raw, effect)
        if enemy is self.boss:
            raw = self.mech.incoming(self, unit, raw, effect)
            raw *= self.mech.build_incoming_factor(self)
            if unit.side == 0 and self.poise_broken:
                raw *= POISE_BREAK_DAMAGE
        return raw

    def after_hit(self, unit, enemy, dealt):
        if unit is self.boss and enemy.side == 0 and dealt > 0:
            self.mech.outgoing(self, enemy, dealt)
        elif enemy is self.boss and unit.side == 0 and dealt > 0:
            self.mech.boss_struck(self, unit, dealt)
            context = self._poise_skill
            if (
                unit.role == "输出"
                and context is not None
                and context[0] is unit
                and context[1] is not unit.actives[0]
                and not self._poise_damage_awarded
            ):
                self._poise_damage_awarded = True
                value = min(8.0, 2.0 + dealt / max(1, self.boss.max_hp) * 100)
                self._reduce_poise(unit, value, "爆发输出")

    def effect(self, unit, enemy, effect, skill):
        if self.mech.blocks_effect(self, unit, effect):
            return "被禁令挡下了"
        cleansed = []
        cleanse_worked = False
        if effect["type"] in ("cleanse", "cleanse_heal"):
            cleansed = self.resolve_targets(unit, enemy, effect.get("target", "self"))
            cleanse_worked = any(
                target.dots
                or target.stun
                or target.silence
                or target.root
                or target.weaken
                or target.expose
                or target.corrupting
                or target.delay_damages
                or target.marks
                or target.vuln
                or target.chill
                or target.glitch
                or target.possessed
                or target.lured
                or target.marked
                or target.blind
                or any(buff.get("pct", 0) < 0 for buff in target.buffs)
                for target in cleansed
            )
        heroes = self.alive_heroes()
        hero_hp = {id(hero): hero.hp for hero in heroes} if unit.side == 0 else {}
        protection_before = (
            sum(hero.shield for hero in heroes),
            sum(len(hero.guards) for hero in heroes),
            sum(bool(hero.share) for hero in heroes),
            sum(len(hero.mitigates) for hero in heroes),
            sum(hero.block_count for hero in heroes),
            sum(len(hero.immunity) for hero in heroes),
        )
        boss_positive_before = self._boss_positive_state(self.boss)
        boss_negative_before = self._boss_negative_state(self.boss)
        text = super().effect(unit, enemy, effect, skill)
        kind = effect["type"]
        if kind == "rewind" and unit.side == 0:
            restored = max(0.0, unit.hp - hero_hp.get(id(unit), unit.hp))
            if restored:
                self.healed[id(unit)] = self.healed.get(id(unit), 0.0) + restored
        if effect["type"] in ("cleanse", "cleanse_heal"):
            # Raid-only control flags live outside the base battle status lists.
            # Clear them on the effect's actual targets (not merely its caster),
            # so an automatic party cleanse really can answer raid mechanics.
            for target in cleansed:
                target.vuln = 0
                target.chill = 0
                target.glitch = False
                target.possessed = False
                target.lured = False
                target.marked = False
                target.blind = False
            if unit.side == 0 and text and cleanse_worked:
                self.mech.supported(self, unit, "cleanse")
        elif (
            effect["type"] in ("heal", "mass_heal", "triage", "life_link", "delay_heal")
            and unit.side == 0
            and text
            and any(hero.hp > hero_hp.get(id(hero), hero.hp) for hero in self.alive_heroes())
        ):
            self.mech.supported(self, unit, "heal")
        elif effect["type"] in ("dispel", "purge") and enemy is self.boss:
            self.mech.dispelled(self)

        if unit.side == 0 and text:
            if unit.role == "治疗" and kind in (
                "heal",
                "mass_heal",
                "triage",
                "cleanse_heal",
                "life_link",
                "rewind",
            ):
                effective = sum(max(0.0, hero.hp - hero_hp.get(id(hero), hero.hp)) for hero in heroes)
                if effective > 0:
                    capacity = sum(hero.max_hp for hero in heroes)
                    self._reduce_poise(
                        unit,
                        min(9.0, 2.0 + effective / max(1, capacity) * 40),
                        "有效救治",
                    )
            if unit.role == "坦克" and kind in (
                "shield",
                "guard",
                "share",
                "mitigate",
                "block",
                "absorb",
                "immunity",
                "taunt",
                "redirect",
            ):
                protection_after = (
                    sum(hero.shield for hero in heroes),
                    sum(len(hero.guards) for hero in heroes),
                    sum(bool(hero.share) for hero in heroes),
                    sum(len(hero.mitigates) for hero in heroes),
                    sum(hero.block_count for hero in heroes),
                    sum(len(hero.immunity) for hero in heroes),
                )
                if protection_after > protection_before or kind in ("taunt", "redirect"):
                    self._reduce_poise(unit, 5.0, "有效保护")
            if unit.role == "辅助":
                support_value = 0.0
                reason = "战术干扰"
                if kind in ("cleanse", "cleanse_heal") and cleanse_worked:
                    support_value, reason = 7.0, "成功净化"
                elif kind in ("dispel", "purge") and (
                    boss_positive_before > self._boss_positive_state(self.boss)
                ):
                    support_value, reason = 7.0, "成功驱散"
                elif self._boss_negative_state(self.boss) > boss_negative_before:
                    support_value = 6.0
                if support_value:
                    self._reduce_poise(unit, support_value, reason)
        return text

    def choose(self, unit, enemy) -> dict:
        if unit.side == 0 and self.mech.force_basic(self, unit):
            return unit.actives[0]
        chosen = super().choose(unit, enemy)
        if (
            unit.side == 0
            and unit.role == "输出"
            and not self.poise_broken
            and not self.poise_locked
            and self.poise <= self.poise_max * 0.25
            and chosen is not unit.actives[0]
            and self._burst_power(chosen) >= 1.5
            and any(hero.alive and hero.role != "输出" for hero in self.heroes)
        ):
            return unit.actives[0]
        return chosen

    def _skill_score(self, unit, enemy, skill) -> float:
        score = super()._skill_score(unit, enemy, skill)
        if unit.side == 0 and unit.role == "输出" and self.poise_broken:
            power = self._burst_power(skill)
            if power:
                score += 90 + power * 10
        return score

    def turn(self, unit, enemy):
        self.acting = None
        super().turn(unit, enemy)

    def act(self, unit, enemy):
        self.acting = unit
        if unit is self.boss and self.poise_broken and self._poise_interrupt_pending:
            self._poise_interrupt_pending = False
            self.say(f"{self.boss.label} 的行动被破防冲击打断了")
            return
        if unit is self.boss and self.mech.before_act(self):
            return
        if unit.glitch:
            unit.glitch = False
            lost = self.hurt(unit, unit.max_hp * 0.05)
            self.say(f"{unit.label} 返回了 404，原地疯狂刷新，损失 {round(lost)} 生命")
            return
        if unit.possessed:
            unit.possessed = False
            allies = [h for h in self.present_heroes() if h is not unit]
            if allies:
                ally = self.rng.choice(allies)
                self.say(f"{unit.label} 被附身了，转身攻击了队友 {ally.label}")
                self.cast(unit, ally, unit.actives[0])
                return
        super().act(unit, enemy)

    def decided(self):
        if not self.boss.alive:
            return 0
        if not self.alive_heroes():
            return 1
        return None

    def order(self) -> list:
        units = self.alive_units()
        if self.cfg.get("conveyor"):
            self.rng.shuffle(units)
        else:
            keyed = [(-u.stat("spd"), self.rng.random(), index) for index, u in enumerate(units)]
            units = [units[k[2]] for k in sorted(keyed)]
        units = self.mech.order(self, units)
        if not self.poise_broken and not self.poise_locked and self.poise <= self.poise_max * 0.25:
            # Let tanks/supports finish the break before the damage dealer spends
            # a saved burst.  Stable partition preserves every boss-specific order.
            dps = [unit for unit in units if unit.side == 0 and unit.role == "输出"]
            units = [unit for unit in units if unit not in dps] + dps
        return units

    def run(self) -> dict:
        for unit in self.heroes + self.foes:
            for skill in unit.data["skills"]:
                if skill["passive"]:
                    for effect in skill["effects"]:
                        self.apply_passive(unit, effect)
                    self.say(f"{unit.label} 被动「{skill['name']}」生效")
        self.mech.setup(self)
        self.mech.assess_build(self)
        self.note(f"{self.boss.label} 拥有 {self.poise_max:.0f} 点韧性；协同作战可制造破防窗口")
        self.flush()
        winner, timeout = None, False
        for self.round in range(1, MAX_RAID_ROUNDS + 1):
            if self.round == self.fury_round + 1:
                self.say("围观的猪开始起哄，之后每回合伤害都会更高！")
            self.cheer, self.sneezer, self.acting = 1.0, None, None
            for hero in self.heroes:
                hero.blind = False
            self.mech.round_start(self)
            self.flush()
            winner = self.decided()
            if winner is not None:
                break
            field_event(self)
            self.flush()
            winner = self.decided()
            if winner is not None:
                break
            self._try_ultimate()
            self.flush()
            winner = self.decided()
            if winner is not None:
                break
            for unit in self.order():
                if not unit.alive or unit.away:
                    continue
                if self.cfg.get("slip") and unit.side == 0 and self.rng.random() < 0.1:
                    self.say(f"{unit.label} 在冰面上滑倒了，这回合爬不起来")
                    continue
                if self.cfg.get("shark") and unit.side == 0 and self.rng.random() < 0.05:
                    bitten = self.hurt(unit, unit.max_hp * 0.1)
                    self.say(
                        f"一条鲨鱼从水里窜出来咬了 {unit.label} 一口，损失 {round(bitten)} 生命"
                    )
                    self.flush()
                    if not unit.alive:
                        continue
                actions = 1
                if unit is self.boss:
                    actions += self.mech.extra_actions(self)
                if unit is self.sneezer:
                    actions += 1
                for index in range(actions):
                    if not unit.alive or self.decided() is not None:
                        break
                    enemy = self.target_for(unit)
                    if enemy is None:
                        if index == 0:
                            self.say(f"{unit.label} 找不到可以攻击的目标")
                        break
                    if index == 0:
                        self.turn(unit, enemy)
                    elif not unit.stun:
                        # Boss overclock, zombie's extra lunge and the sneeze
                        # event grant a separate action, not another link in the
                        # same skill chain.  Let a zero-CD basic be used again;
                        # real cooldowns still prevent repeating techniques.
                        unit.used_skills.clear()
                        self._turn_triggered.clear()
                        self.act(unit, enemy)
                    self.flush()
                winner = self.decided()
                if winner is not None:
                    break
            if winner is not None:
                break
            self.acting = None
            self.mech.round_end(self)
            self._recover_poise()
            for unit in self.heroes + self.foes:
                unit.away = max(0, unit.away - 1)
            if self.cfg.get("leak"):
                for unit in self.alive_units():
                    self.hurt(unit, unit.dot_basis * self.cfg["leak"] / 100)
                self.note(f"漏电，所有单位损失 {self.cfg['leak']}% 生命")
            self.flush()
            winner = self.decided()
            if winner is not None:
                break
        else:
            self.round = 0
            winner, timeout = 1, True
            self.say(f"{MAX_RAID_ROUNDS} 回合过去，{self.boss.label}仍未倒下，队伍被迫撤出战斗")
        return {
            "winner": winner,
            "won": winner == 0,
            "timeout": timeout,
            "rounds": self.round or MAX_RAID_ROUNDS,
            "log": self.log,
            "heroes": [
                {
                    "seat": h.seat,
                    "hp": round(max(0, h.hp)),
                    "max_hp": h.max_hp,
                    "alive": h.alive,
                    "dealt": round(self.dealt.get(id(h), 0)),
                    "healed": round(self.healed.get(id(h), 0)),
                    "taken": round(self.taken.get(id(h), 0)),
                }
                for h in self.heroes
            ],
            "boss": {"hp": round(max(0, self.boss.hp)), "max_hp": self.boss.max_hp},
            "poise": {
                "value": round(self.poise, 1),
                "max": round(self.poise_max, 1),
                "broken": self.poise_broken,
                "breaks": self.poise_breaks,
                "max_breaks": POISE_MAX_BREAKS,
                "locked": self.poise_locked,
                "contributions": [
                    {
                        "seat": hero.seat,
                        "role": hero.role,
                        "value": round(self.poise_contributions.get(id(hero), 0.0), 1),
                    }
                    for hero in self.heroes
                ],
            },
            "counter": {
                "ready": self.mech.counter_ready,
                "name": self.mech.CORE_NAME,
                "tags": self.mech.CORE_TAGS,
            },
            "field_events": self.field_events,
            "ultimate": {
                "eligible": self.ultimate_eligible,
                "used": self.ultimate_used,
                "round": self.ultimate_round,
                "role": self.ultimate_role,
            },
        }


def simulate_raid(heroes: list[dict], boss: dict, slot_id: str, cfg: dict, seed: int) -> dict:
    return _RaidBattle(heroes, boss, slot_id, cfg, seed).run()
