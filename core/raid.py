"""Pig raids: four players' pigs against three bosses in a row."""

import random

from .battle import CRIT_MULTIPLIER, FURY_ROUND, FURY_STEP, _Battle, _Unit, fighter
from .raid_bosses import BOSSES, BossMechanics
from .raid_events import field_event

PARTY_SIZE = 4
TIMEOUT_MINUTES = 10
MAX_RAID_ROUNDS = 25
REST_HEAL = 0.3
# Bosses match the party's average level; later stages hit harder through these
# multipliers instead, so the difficulty curve is the same at every level.
STAGE_HP = (4.6, 5.2, 5.8)
STAGE_ATK = (1.35, 1.45, 1.55)

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
)

# Effect types the replay animates on their own (damage and heals have their own cues).
STATUS_CUES = frozenset(
    {"shield", "stun", "dot", "buff", "thorns", "regen", "evade", "cleanse", "dispel", "copy"}
)

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


def boss_level(levels: list[int]) -> int:
    return max(1, round(sum(levels) / len(levels))) if levels else 1


def boss_fighter(pig: dict, entry: dict, level: int, stage: int, slot_id: str, label: str) -> dict:
    """The boss pig at raid strength; `slot_id` picks the mechanics' own multipliers."""
    cls = mechanics_for(slot_id)
    unit = fighter(pig, entry, level, label)
    stats = unit["stats"]
    unit["dot_basis"] = stats["hp"]
    # Low-level pigs have few skills and fights drag on; ease them in until Lv5.
    rookie = min(1.0, 0.7 + 0.06 * level)
    stats["hp"] = round(stats["hp"] * STAGE_HP[stage - 1] * cls.HP * rookie)
    stats["atk"] = round(stats["atk"] * STAGE_ATK[stage - 1] * cls.ATK)
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


def _statuses(unit) -> list:
    """Badges for the replay: [glyph, kind], kind being bad, good or control."""
    badges = []
    if unit.stun:
        badges.append([(unit.stun_label or "晕")[0], "control"])
    for dot in unit.dots:
        badges.append([dot.get("label", "毒")[0], "bad"])
    if any(b["pct"] < 0 for b in unit.buffs if b.get("tag") != "stench"):
        badges.append(["↓", "bad"])
    stench = sum(1 for b in unit.buffs if b.get("tag") == "stench")
    if stench:
        badges.append([f"臭{stench}", "bad"])
    if any(b["pct"] > 0 for b in unit.buffs):
        badges.append(["↑", "good"])
    flags = (
        (unit.thorns, "刺", "good"),
        (unit.regens, "愈", "good"),
        (unit.evade, "闪", "good"),
        (unit.chill, f"寒{unit.chill}", "bad"),
        (unit.vuln, "易", "bad"),
        (unit.possessed, "附", "control"),
        (unit.lured or unit.marked, "饵", "control"),
        (unit.glitch, "错", "control"),
        (unit.blind, "盲", "bad"),
        (unit.charm, "符", "good"),
    )
    badges += [[glyph, kind] for active, glyph, kind in flags if active]
    return badges[:6]


class _RaidBattle(_Battle):
    def __init__(self, heroes: list[dict], boss: dict, slot_id: str, cfg: dict, seed: int):
        self.rng = random.Random(seed)
        self.log = []
        self.round = 0
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
        # Damage each unit dealt to the other side, for the DPS table.
        self.dealt = {}
        # Replay data: every unit that ever took the field, and one step per log line
        # with the cues (who hit whom, which mechanic fired) and everyone's state after it.
        self.everyone = []
        self.timeline = []
        self.cues = []
        self._crit = False
        self._dot = ""
        for unit in self.heroes + [self.boss]:
            self._enlist(unit, "boss" if unit is self.boss else "hero")
        self._configure()

    def _enlist(self, unit, kind: str):
        unit.uid = len(self.everyone)
        unit.kind = "ally" if kind == "hero" and unit.seat is None else kind
        self.everyone.append(unit)
        if self.round or self.log:
            self.cue("summon", dst=unit.uid)

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

    def note(self, text: str, mech: int | None = None, field: str = ""):
        self.notes.append((text, mech, field))

    def cue(self, kind: str, **data):
        self.cues.append({"t": kind, **data})

    def say(self, text: str):
        super().say(text)
        self.timeline.append(
            {"round": self.round, "text": text, "cues": self.cues, "state": self.snapshot()}
        )
        self.cues = []

    def snapshot(self) -> list:
        """[hp, shield, stunned, away, statuses] for every unit, indexed by uid."""
        return [
            [round(max(0.0, u.hp)), round(u.shield), bool(u.stun), bool(u.away), _statuses(u)]
            for u in self.everyone
        ]

    def flush(self):
        notes, falls = self.notes, self.falls
        self.notes, self.falls = [], []
        for text, mech, field in notes:
            if mech is not None:
                self.cue("mech", index=mech, name=self.mech.MECHANICS[mech][0])
            if field:
                self.cue("field", name=field)
            self.say(text)
        for text, uid in falls:
            self.cue("fall", dst=uid)
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

    def strike(self, attacker, target, power: float, *, crit: bool = False) -> float:
        """A mechanic's hit: never misses, ignores the attacker's skill list."""
        if not target.alive:
            return 0.0
        raw = attacker.stat("atk") * power * self.rng.uniform(0.9, 1.1) * self.fury()
        if crit:
            raw *= CRIT_MULTIPLIER
        raw *= 60 / (60 + target.stat("def"))
        raw *= 1 + target.vuln / 100
        acting, self.acting = self.acting, attacker
        self._crit = crit
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
        if unit is self.boss:
            return self.mech.pick_target(self, heroes)
        return self.rng.choice(heroes)

    def apply_passive(self, unit, effect):
        if effect["type"] == "buff" and effect.get("target") == "enemy":
            targets = [self.boss] if unit.side == 0 else self.heroes
            for target in targets:
                target.buffs.append(
                    {"stat": effect["stat"], "pct": effect["pct"], "turns": effect.get("turns", 99)}
                )
            return
        super().apply_passive(unit, effect)

    def hurt(self, unit, amount: float, source: str = "") -> float:
        was_alive = unit.alive
        dealt = super().hurt(unit, amount)
        attacker = self.acting
        if attacker is not None and attacker.side != unit.side:
            self.dealt[id(attacker)] = self.dealt.get(id(attacker), 0.0) + dealt
        if dealt > 0:
            self.cue(
                "hit",
                src=attacker.uid if attacker is not None else None,
                dst=unit.uid,
                amt=round(dealt),
                crit=self._crit,
                dot=self._dot,
            )
        self._crit, self._dot = False, ""
        if was_alive and not unit.alive and unit is self.boss and self.mech.prevent_fall(self):
            return dealt
        if was_alive and not unit.alive and unit.charm:
            unit.charm = False
            unit.hp = 1.0
            self.note(f"平安符护住了 {unit.label}，保留 1 点生命")
        if unit is self.boss and unit.alive:
            self.mech.damaged(self)
        if was_alive and not unit.alive:
            self.falls.append((f"{unit.label} 倒下了", unit.uid))
            if unit.side == 0 and unit.seat is not None:
                self.mech.hero_fell(self, unit)
        return dealt

    def announce_falls(self, unit, enemy):
        self.flush()

    def dot_amount(self, unit, dot):
        # The tick is hurt right after this; credit it to whoever applied the dot.
        self.acting = dot.get("source")
        self._dot = dot.get("label", "持续伤害")
        amount = unit.dot_basis * dot["pct"] / 100 * self.mech.dot_factor(self, unit)
        if unit.side == 0 and self.cfg.get("dot_cut"):
            amount *= 1 - self.cfg["dot_cut"] / 100
        return amount

    def heal(self, unit, amount: float) -> int:
        healed = super().heal(unit, amount * self.mech.heal_factor(self, unit))
        if healed > 0:
            self.cue("heal", dst=unit.uid, amt=healed)
        return healed

    def check_revive(self, unit):
        if unit.hp <= 0 and unit.revive:
            self.cue("revive", dst=unit.uid)
        super().check_revive(unit)

    def crit_roll(self, unit, enemy) -> bool:
        self._crit = self._judge_crit(unit, enemy)
        return self._crit

    def _judge_crit(self, unit, enemy) -> bool:
        crit = super().crit_roll(unit, enemy)
        if unit.lured and enemy is self.boss:
            unit.lured, unit.marked = False, True
            return True
        if crit and enemy is self.boss:
            return self.mech.crit_against(self, unit)
        return crit

    def apply(self, unit, enemy, effects, skill):
        self.cue(
            "skill",
            src=unit.uid,
            dst=enemy.uid,
            name=skill["name"],
            text=skill.get("text", ""),
            style=unit.data.get("style", ""),
        )
        super().apply(unit, enemy, effects, skill)

    def damage(self, unit, enemy, effect):
        before = len(self.cues)
        text = super().damage(unit, enemy, effect)
        if not any(c["t"] == "hit" and c["dst"] == enemy.uid for c in self.cues[before:]):
            self.cue("miss", src=unit.uid, dst=enemy.uid, void=bool(self.void_note))
        return text

    def pierce(self, unit, enemy, effect):
        value = super().pierce(unit, enemy, effect)
        return self.mech.pierce(self, value) if enemy is self.boss else value

    def adjust_hit(self, unit, enemy, raw, effect):
        raw *= 1 + enemy.vuln / 100
        raw *= self.mech.outgoing_factor(self, unit, enemy)
        if unit is self.boss and enemy.marked:
            enemy.marked = False
            raw *= 2
            self.note(f"灯笼鱼的光还亮在 {enemy.label} 身上，这一下伤害翻倍")
        if enemy.side == 1:
            raw = self.mech.foe_hit(self, unit, enemy, raw, effect)
        if enemy is self.boss:
            raw = self.mech.incoming(self, unit, raw, effect)
        return raw

    def after_hit(self, unit, enemy, dealt):
        if unit is self.boss and enemy.side == 0:
            self.mech.outgoing(self, enemy, dealt)

    def effect(self, unit, enemy, effect, skill):
        if self.mech.blocks_effect(self, unit, effect):
            self.cue("blocked", dst=unit.uid)
            return "被禁令挡下了"
        text = super().effect(unit, enemy, effect, skill)
        kind = effect["type"]
        if kind == "dot" and enemy.dots:
            enemy.dots[-1]["source"] = unit
        if kind in STATUS_CUES and text:
            if kind == "stun":
                target = unit if effect.get("target") == "self" else enemy
            elif kind == "buff":
                target = enemy if effect.get("target") == "enemy" else unit
            else:
                target = enemy if kind in ("dot", "dispel") else unit
            self.cue(kind, src=unit.uid, dst=target.uid, up=effect.get("pct", 0) >= 0)
        if effect["type"] == "cleanse":
            unit.vuln = 0
            unit.chill = 0
        elif effect["type"] == "dispel" and enemy is self.boss:
            self.mech.dispelled(self)
        return text

    def choose(self, unit, enemy) -> dict:
        if unit.side == 0 and self.mech.force_basic(self, unit):
            return unit.actives[0]
        return super().choose(unit, enemy)

    def turn(self, unit, enemy):
        self.acting = None
        super().turn(unit, enemy)

    def act(self, unit, enemy):
        self.acting = unit
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
        return self.mech.order(self, units)

    def run(self) -> dict:
        for unit in self.heroes + self.foes:
            for skill in unit.data["skills"]:
                if skill["passive"]:
                    for effect in skill["effects"]:
                        self.apply_passive(unit, effect)
                    self.say(f"{unit.label} 被动「{skill['name']}」生效")
        self.mech.setup(self)
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
            field_event(self)
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
                        self.act(unit, enemy)
                    self.flush()
                winner = self.decided()
                if winner is not None:
                    break
            if winner is not None:
                break
            self.acting = None
            self.mech.round_end(self)
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
                }
                for h in self.heroes
            ],
            "boss": {"hp": round(max(0, self.boss.hp)), "max_hp": self.boss.max_hp},
            "field_events": self.field_events,
            "roster": [
                {
                    "uid": u.uid,
                    "label": u.label,
                    "name": u.data.get("name", u.label),
                    "asset": u.data.get("asset", ""),
                    "side": u.side,
                    "seat": u.seat,
                    "kind": u.kind,
                    "max_hp": u.max_hp,
                    "level": u.data.get("level", 1),
                    "style": u.data.get("style", ""),
                }
                for u in self.everyone
            ],
            "timeline": self.timeline,
        }


def simulate_raid(heroes: list[dict], boss: dict, slot_id: str, cfg: dict, seed: int) -> dict:
    return _RaidBattle(heroes, boss, slot_id, cfg, seed).run()
