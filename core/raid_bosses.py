"""Signature mechanics of the nine raid bosses.

Each boss is its own class; the raid engine calls the hooks below at fixed
moments. `battle` is the running raid battle (see core.raid), `boss` is
`battle.boss`. Messages go through `battle.note` so they land after the action
that triggered them.
"""


class BossMechanics:
    pig_id = ""
    # (name, description) for each of the three mechanics.
    MECHANICS: tuple = ()
    # One concise, engine-accurate counterplay hint for each mechanic.  The raid is
    # fully automatic, so these may only describe build choices or conditions the
    # simulator can actually resolve; never ask the player to stop attacking,
    # switch targets or move on command.
    COUNTERS: tuple = ()
    # Per-boss multipliers on top of the shared raid scaling.
    HP = 1.0
    ATK = 1.0
    DEF = 1.0
    # Whether heroes must clear the boss's summoned units before hitting the boss.
    GUARDS_FIRST = True
    # One real, searchable party-building check. These values are filled from
    # CORE_CHECKS below so every boss has an explicit preparation gate.
    CORE_NAME = ""
    CORE_TAGS: tuple[str, ...] = ()
    CORE_FAILURE = ""
    UNPREPARED_OUTGOING = 1.0
    UNPREPARED_INCOMING = 1.0

    def __init__(self, disabled: int | None = None):
        self.disabled = disabled
        self.counter_ready = True

    def on(self, index: int) -> bool:
        return index != self.disabled

    def say(self, battle, index: int, text: str, targets=(), tag: str = ""):
        """A mechanic's log line; the replay shows that mechanic's own effect with it.

        `targets` are the units the effect lands on and `tag` picks a variant of the
        scene (e.g. a take-off that succeeds or crashes).
        """
        battle.note(text, mech=index, targets=targets, tag=tag)

    def setup(self, battle):
        pass

    def round_start(self, battle):
        pass

    def round_end(self, battle):
        pass

    def before_act(self, battle) -> bool:
        """Return True when the mechanic replaces the boss's normal action."""
        return False

    def extra_actions(self, battle) -> int:
        return 0

    def pick_target(self, battle, candidates: list):
        """Normal (non-mechanic) boss attacks prefer taunt, then tanks."""
        taunted = [h for h in candidates if getattr(h, "taunt", 0) > 0]
        if taunted:
            return battle.rng.choice(taunted)
        tanks = [
            h
            for h in candidates
            if getattr(h, "role", None) == "坦克" or h.data.get("role") == "坦克"
        ]
        if tanks:
            return battle.rng.choice(tanks)
        return battle.rng.choice(candidates)

    def incoming(self, battle, attacker, raw: float, effect: dict) -> float:
        return raw

    def crit_against(self, battle, attacker) -> bool:
        return True

    def pierce(self, battle, value: float) -> float:
        return value

    def outgoing(self, battle, target, dealt: float):
        pass

    def damaged(self, battle):
        pass

    def hero_fell(self, battle, hero):
        pass

    def dispelled(self, battle):
        pass

    def foe_hit(self, battle, attacker, target, raw: float, effect: dict) -> float:
        """Any enemy-side unit (boss or summon) is about to take `raw` from a hero."""
        return raw

    def prevent_fall(self, battle) -> bool:
        """Return True (after restoring HP) to keep the boss standing."""
        return False

    def heal_factor(self, battle, unit) -> float:
        return 1.0

    def blocks_effect(self, battle, unit, effect: dict) -> bool:
        return False

    def force_basic(self, battle, unit) -> bool:
        return False

    def order(self, battle, units: list) -> list:
        return units

    def dot_factor(self, battle, unit) -> float:
        return 1.0

    def outgoing_factor(self, battle, attacker, target) -> float:
        return 1.0

    def boss_struck(self, battle, attacker, dealt: float):
        """A hero just dealt `dealt` damage to the boss."""

    def supported(self, battle, unit, kind: str):
        """A hero just successfully healed (`heal`) or cleansed (`cleanse`)."""

    def assess_build(self, battle):
        """Resolve this boss's primary, searchable party-building requirement."""
        party_tags = {tag for hero in battle.heroes for tag in hero.data.get("tags", ())}
        self.counter_ready = bool(party_tags.intersection(self.CORE_TAGS))
        tags = " / ".join(self.CORE_TAGS)
        if self.counter_ready:
            battle.note(f"核心检定通过：【{tags}】已就位，可以处理「{self.CORE_NAME}」")
        else:
            battle.note(f"核心检定缺失：【{tags}】未就位；{self.CORE_FAILURE}")

    def unprepared_weight(self, battle) -> float:
        """Rarity can brute-force part of a missed mechanic without replacing its counter."""
        ranks = [hero.data.get("rarity_rank", 1) for hero in battle.heroes]
        average = sum(ranks) / len(ranks) if ranks else 1
        return max(0.0, min(1.0, (5 - average) / 4))

    def build_outgoing_factor(self, battle, attacker) -> float:
        if attacker.side == 1 and not self.counter_ready:
            weight = self.unprepared_weight(battle)
            return 1 + (self.UNPREPARED_OUTGOING - 1) * weight
        return 1.0

    def build_incoming_factor(self, battle) -> float:
        if self.counter_ready:
            return 1.0
        weight = self.unprepared_weight(battle)
        return 1 - (1 - self.UNPREPARED_INCOMING) * weight


class Goblin(BossMechanics):
    pig_id = "goblin-pig"
    MECHANICS = (
        (
            "角落潜伏",
            "每 3 回合钻回角落 1 回合，这回合打它全部落空；出来时偷袭生命最低的队员，必定暴击",
        ),
        ("邋遢光环", "每回合给全体队员叠 1 层熏味，每层速度 -5%，最多 6 层，清除负面效果可以驱散"),
        ("摆烂", "生命第一次低于 30% 时躺平 2 回合回血，但躺平期间受到的伤害 +30%"),
    )
    ATK = 1.25
    HP = 1.6

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.lurking = False
        self.flat = False

    def round_start(self, battle):
        boss = battle.boss
        if self.on(0):
            if self.lurking:
                self.lurking = False
                targets = battle.alive_heroes()
                if targets:
                    target = min(targets, key=lambda h: h.ratio)
                    dealt = battle.strike(boss, target, 1.2, crit=True)
                    self.say(
                        battle,
                        0,
                        f"{boss.label}从角落里窜出来偷袭 {target.label}，暴击造成 {round(dealt)} 伤害",
                    )
            elif battle.round % 3 == 0:
                self.lurking = True
                boss.stun = max(boss.stun, 1)
                boss.stun_label = "躲在角落"
                self.say(battle, 0, f"{boss.label}钻回了角落，这回合谁也打不到它")
        if self.on(1):
            for hero in battle.alive_heroes():
                stacks = sum(1 for b in hero.buffs if b.get("tag") == "stench")
                if stacks < 6:
                    hero.buffs.append({"stat": "spd", "pct": -5, "turns": 99, "tag": "stench"})
            if battle.round == 1:
                self.say(battle, 1, "邋遢光环弥漫开来：一股说不清的味道，全队每回合速度 -5%")

    def incoming(self, battle, attacker, raw, effect):
        if self.lurking:
            battle.void_note = f"{battle.boss.label}躲在角落里"
            return 0
        if self.flat and battle.boss.stun and battle.boss.stun_label == "摆烂躺平":
            raw *= 1.3
        return raw

    def damaged(self, battle):
        boss = battle.boss
        if self.on(2) and not self.flat and boss.ratio < 0.3:
            self.flat = True
            boss.stun = max(boss.stun, 2)
            boss.stun_label = "摆烂躺平"
            boss.regens.append({"pct": 8, "turns": 2})
            self.say(
                battle, 2, f"{boss.label}开始摆烂：躺在角落什么都不管，慢慢回血，但浑身都是破绽"
            )


class Frozen(BossMechanics):
    pig_id = "frozen-pig"
    MECHANICS = (
        ("寒气层数", "每次攻击给目标叠 1 层寒冷，叠满 3 层冻结 1 回合"),
        ("冰块外壳", "开场自带 25% 生命的冰壳，冰壳在时不会被暴击，伤害先扣冰壳"),
        ("融化", "冰壳碎掉后防御 -30%、攻击 +20%"),
    )
    ATK = 1.25
    HP = 1.55

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.shell = 0.0

    def setup(self, battle):
        if self.on(1):
            self.shell = battle.boss.max_hp * 0.25
            self.say(
                battle, 1, f"{battle.boss.label}裹着一层 {round(self.shell)} 点的冰块外壳：生人勿近"
            )

    def crit_against(self, battle, attacker):
        return self.shell <= 0

    def incoming(self, battle, attacker, raw, effect):
        if self.shell <= 0:
            return raw
        absorbed = min(self.shell, raw)
        self.shell -= absorbed
        raw -= absorbed
        if self.shell <= 0:
            self.melt(battle)
        elif raw <= 0:
            battle.void_note = f"冰壳吸收了 {round(absorbed)} 伤害"
        return raw

    def melt(self, battle):
        boss = battle.boss
        if self.on(2):
            boss.mods["def"] = boss.mods.get("def", 0) - 30
            boss.mods["atk"] = boss.mods.get("atk", 0) + 20
            self.say(
                battle,
                2,
                f"冰块外壳碎了……外表冷硬，内心却渴望温暖。{boss.label}融化了：防御 -30%，攻击 +20%",
            )
        else:
            self.say(battle, 1, "冰块外壳碎了")

    def outgoing(self, battle, target, dealt):
        if not self.on(0) or not target.alive:
            return
        target.chill += 1
        if target.chill < 3:
            return
        target.chill = 0
        if target.frost_ward:
            target.frost_ward = False
            self.say(battle, 0, f"篝火的余温护住了 {target.label}，没有被冻住")
            return
        target.stun = max(target.stun, 1)
        target.stun_label = "冻结"
        self.say(battle, 0, f"寒气叠满 3 层，{target.label} 被冻成了冰块")


class Everest(BossMechanics):
    pig_id = "everest-pig"
    MECHANICS = (
        ("海拔上升", "每回合全队速度 -4%，可以累积；第 12 回合起全队缺氧，每回合损失 4% 生命"),
        ("不可逾越", "单次受到的伤害最多只算它最大生命的 6%，多段和持续伤害更划算"),
        ("雪崩", "第 5、10、15 回合对全队造成 1.2 倍攻击伤害，每只猪 30% 概率臣服跳过下一回合"),
    )
    ATK = 1.4
    HP = 1.5
    DEF = 1.25

    def round_start(self, battle):
        boss = battle.boss
        if self.on(0):
            for hero in battle.alive_heroes():
                hero.mods["spd"] = hero.mods.get("spd", 0) - 4
            if battle.round == 1:
                self.say(battle, 0, "海拔越来越高，空气越来越稀薄，全队每回合都会慢一点")
            if battle.round >= 12:
                lost = [
                    round(battle.hurt(hero, hero.max_hp * 0.04)) for hero in battle.alive_heroes()
                ]
                if lost:
                    self.say(battle, 0, f"高处不胜寒，全队缺氧，各损失约 {max(lost)} 生命")
        if self.on(2) and battle.round in (5, 10, 15):
            parts = []
            for hero in battle.alive_heroes():
                dealt = battle.strike(boss, hero, 1.2)
                text = f"{hero.label} -{round(dealt)}"
                if hero.alive and battle.rng.random() < 0.3:
                    hero.stun = max(hero.stun, 1)
                    hero.stun_label = "臣服"
                    text += "（臣服）"
                parts.append(text)
            if parts:
                self.say(battle, 2, "雪峰压顶，雪崩来了！" + "，".join(parts))

    def incoming(self, battle, attacker, raw, effect):
        if self.on(1):
            cap = battle.boss.max_hp * 0.06
            if raw > cap:
                return cap
        return raw


class Error404(BossMechanics):
    pig_id = "error-404-pig"
    MECHANICS = (
        (
            "薛定谔状态",
            "每回合 50% 进入未被观测，此时受到的每次伤害 50% 作废；必中和真实伤害不受影响",
        ),
        ("页面不存在", "每 4 回合让一只队员猪 404，它的下一次行动变成原地刷新，并损失 5% 生命"),
        ("缓存回滚", "生命第一次低于 40% 时回滚到 60%，同时清除身上的持续伤害"),
    )
    ATK = 1.3
    HP = 2.05

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.hidden = False
        self.rolled = False

    def round_start(self, battle):
        boss = battle.boss
        if self.on(0):
            self.hidden = battle.rng.random() < 0.5
            if self.hidden:
                self.say(battle, 0, f"{boss.label}进入未被观测状态：没猪能确定它是否存在")
        if self.on(1) and battle.round % 4 == 0:
            targets = battle.alive_heroes()
            if targets:
                target = battle.rng.choice(targets)
                target.glitch = True
                self.say(battle, 1, f"页面不存在：{target.label} 的下一次行动返回了 404")

    def incoming(self, battle, attacker, raw, effect):
        if (
            self.hidden
            and not (effect.get("true") or effect.get("sure"))
            and battle.rng.random() < 0.5
        ):
            battle.void_note = "薛定谔：这一击没被观测到"
            return 0
        return raw

    def damaged(self, battle):
        boss = battle.boss
        if self.on(2) and not self.rolled and boss.ratio < 0.4:
            self.rolled = True
            boss.hp = boss.max_hp * 0.6
            boss.dots.clear()
            self.say(battle, 2, f"缓存回滚！{boss.label}疯狂刷新，生命回到 60%，持续伤害也被清掉了")


class Mechanical(BossMechanics):
    pig_id = "mechanical-pig"
    MECHANICS = (
        ("光学锁定", "每 3 回合锁定当前生命最高的队员，下回合对它打出必中的 2 倍伤害"),
        ("金属外壳", "防御很高，无视防御效果对它翻倍；每挨 6 次攻击会过热，防御归零 2 回合"),
        ("系统 bug", "每回合 10% 概率忘了自己是猪，原地哼哼一回合"),
    )
    ATK = 1.25
    HP = 1.95
    DEF = 1.55

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.locked = None
        self.lock_round = 0
        self.hits = 0
        self.overheat = 0

    def round_start(self, battle):
        if self.on(0) and battle.round % 3 == 0:
            targets = battle.alive_heroes()
            if targets:
                self.locked = max(targets, key=lambda h: h.hp)
                self.lock_round = battle.round
                self.say(battle, 0, f"红色光学眼锁定了 {self.locked.label}，运算精准")

    def before_act(self, battle):
        boss = battle.boss
        if self.locked is not None and battle.round == self.lock_round + 1:
            target, self.locked = self.locked, None
            if target.alive:
                dealt = battle.strike(boss, target, 2.0)
                battle.say(
                    f"{boss.label}按锁定坐标开火，对 {target.label} 造成 {round(dealt)} 伤害"
                )
                return True
        if self.on(2) and battle.rng.random() < 0.1:
            battle.say(f"{boss.label}忘了自己其实是只猪，原地哼哼了一回合")
            return True
        return False

    def pierce(self, battle, value):
        return min(100, value * 2) if self.on(1) else value

    def incoming(self, battle, attacker, raw, effect):
        if self.on(1) and not self.overheat:
            self.hits += 1
            if self.hits >= 6:
                self.hits = 0
                self.overheat = 2
                battle.boss.zeroed.add("def")
                self.say(battle, 1, "金属外壳过热冒烟了！防御归零 2 回合")
        return raw

    def round_end(self, battle):
        if self.locked is not None and battle.round > self.lock_round:
            self.locked = None
        if self.overheat:
            self.overheat -= 1
            if not self.overheat:
                battle.boss.zeroed.discard("def")
                self.say(battle, 1, "金属外壳冷却完毕，防御恢复")


class Cyberpunk(BossMechanics):
    pig_id = "cyberpunk-pig"
    MECHANICS = (
        ("黑客入侵", "每 3 回合清除全队的增益、护盾和反弹"),
        ("义体进化", "每回合攻击 +4%，一直累积；驱散效果能把累积的加成清零"),
        ("超频过载", "生命低于 40% 后每回合行动 2 次，但每回合损失 4% 生命"),
    )
    ATK = 1.0
    HP = 1.12

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.evolved = 0
        self.overclock = False

    def round_start(self, battle):
        boss = battle.boss
        if self.on(1):
            self.evolved += 4
            boss.mods["atk"] = boss.mods.get("atk", 0) + 4
            if self.evolved in (4, 20, 40, 60):
                self.say(battle, 1, f"义体改造进行中：{boss.label}的攻击已累计 +{self.evolved}%")
        if self.on(0) and battle.round % 3 == 0:
            for hero in battle.alive_heroes():
                hero.buffs = [b for b in hero.buffs if b["pct"] < 0]
                hero.shield = 0
                hero.thorns.clear()
                hero.evade = 0
            self.say(battle, 0, "黑客入侵！全队的增益、护盾和反弹都被删掉了")

    def dispelled(self, battle):
        if self.evolved:
            battle.boss.mods["atk"] = battle.boss.mods.get("atk", 0) - self.evolved
            self.say(
                battle, 1, f"驱散生效，{battle.boss.label}累计 +{self.evolved}% 的义体进化被清零"
            )
            self.evolved = 0

    def damaged(self, battle):
        if self.on(2) and not self.overclock and battle.boss.ratio < 0.4:
            self.overclock = True
            self.say(battle, 2, f"{battle.boss.label}处理器超频运行！之后每回合行动 2 次")

    def extra_actions(self, battle):
        return 1 if self.overclock else 0

    def round_end(self, battle):
        boss = battle.boss
        if self.overclock and boss.alive:
            lost = battle.hurt(boss, boss.max_hp * 0.04)
            self.say(battle, 2, f"超频过热，{boss.label}损失 {round(lost)} 生命")


class Demon(BossMechanics):
    pig_id = "demon-pig"
    MECHANICS = (
        ("满肚子坏点子", "开场给每只队员猪随机挂一个诅咒：虚弱、迟缓、流血或易伤"),
        ("恶作剧", "每 4 回合把两只队员猪的生命比例对调"),
        ("得逞的坏笑", "每有一只队员猪倒下，它回复 15% 生命、攻击 +10%"),
    )
    ATK = 1.32
    HP = 2.25
    CURSES = ("虚弱", "迟缓", "流血", "易伤")

    def setup(self, battle):
        if not self.on(0):
            return
        parts = []
        for hero in battle.alive_heroes():
            curse = battle.rng.choice(self.CURSES)
            if curse == "虚弱":
                hero.buffs.append({"stat": "atk", "pct": -20, "turns": 99})
            elif curse == "迟缓":
                hero.buffs.append({"stat": "spd", "pct": -25, "turns": 99})
            elif curse == "流血":
                hero.dots.append({"pct": 3, "turns": 8, "label": "流血"})
            else:
                hero.vuln += 20
            parts.append(f"{hero.label}「{curse}」")
        if parts:
            self.say(battle, 0, "满肚子坏点子：" + "，".join(parts))

    def round_start(self, battle):
        heroes = battle.alive_heroes()
        if self.on(1) and battle.round % 4 == 0 and len(heroes) >= 2:
            a, b = battle.rng.sample(heroes, 2)
            ra, rb = a.ratio, b.ratio
            a.hp, b.hp = a.max_hp * rb, b.max_hp * ra
            self.say(
                battle, 1, f"恶作剧！{a.label} 和 {b.label} 的生命被对调了（{ra:.0%} ⇄ {rb:.0%}）"
            )

    def hero_fell(self, battle, hero):
        boss = battle.boss
        if self.on(2) and boss.alive:
            healed = battle.heal(boss, boss.max_hp * 0.15)
            boss.mods["atk"] = boss.mods.get("atk", 0) + 10
            self.say(battle, 2, f"{boss.label}露出得逞的坏笑，回复 {healed} 生命，攻击 +10%")


class ChainedKing(BossMechanics):
    pig_id = "chained_crown_pig"
    MECHANICS = (
        (
            "三重锁链",
            "开场被 3 条锁链束缚，攻击、速度 -30%；生命每降 25% 挣断 1 条，压制减少 10%，并冲击全队",
        ),
        ("王者归来", "3 条锁链全断时恢复全部属性，回复 15% 生命"),
        ("王者威压", "每 4 回合让速度最低的队员猪臣服，跳过 1 回合"),
    )
    ATK = 0.98
    HP = 0.85

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.chains = 0
        self.returned = False

    def setup(self, battle):
        if self.on(0):
            self.chains = 3
            boss = battle.boss
            boss.mods["atk"] = boss.mods.get("atk", 0) - 30
            boss.mods["spd"] = boss.mods.get("spd", 0) - 30
            self.say(battle, 0, f"{boss.label}被 3 条锁链束缚着：欲戴王冠，必承其重")

    def damaged(self, battle):
        boss = battle.boss
        while self.chains and boss.ratio <= 0.25 * self.chains:
            self.chains -= 1
            boss.mods["atk"] += 10
            boss.mods["spd"] += 10
            hits = [
                f"{hero.label} -{round(battle.strike(boss, hero, 0.7))}"
                for hero in battle.alive_heroes()
            ]
            self.say(
                battle,
                0,
                f"锁链崩断一条（剩 {self.chains} 条），冲击波扫过全队：" + "，".join(hits),
            )
            if not self.chains:
                self.king_returns(battle)
        if not self.on(0) and not self.returned and boss.ratio <= 0.25:
            self.king_returns(battle)

    def king_returns(self, battle):
        if not self.on(1) or self.returned:
            return
        self.returned = True
        boss = battle.boss
        healed = battle.heal(boss, boss.max_hp * 0.15)
        self.say(battle, 1, f"挣断锁链，王者归来！{boss.label}恢复全部属性，回复 {healed} 生命")

    def round_start(self, battle):
        heroes = battle.alive_heroes()
        if self.on(2) and battle.round % 4 == 0 and heroes:
            target = min(heroes, key=lambda h: h.stat("spd"))
            target.stun = max(target.stun, 1)
            target.stun_label = "臣服"
            self.say(battle, 2, f"王者威压：{target.label} 被震慑得跪了下来")


class PigGod(BossMechanics):
    pig_id = "pig_god"
    MECHANICS = (
        ("好运加持", "暴击率 +25；每次被暴击有 30% 概率靠好运抵消"),
        ("神谕", "每 3 回合看穿全队并预告下回合的目标，下一回合全队闪避归零"),
        ("守护神降临", "生命第一次低于 50% 时召唤 2 只小猪守护灵，守护灵在场时必须先打守护灵"),
    )
    ATK = 1.24
    HP = 1.18

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.blind_round = 0
        self.prophecy = None
        self.summoned = False

    def setup(self, battle):
        if self.on(0):
            battle.boss.mods["crit"] = battle.boss.mods.get("crit", 0) + 25

    def crit_against(self, battle, attacker):
        if self.on(0) and battle.rng.random() < 0.3:
            self.say(battle, 0, f"好运加持：{battle.boss.label}靠运气抵消了一次暴击")
            return False
        return True

    def round_start(self, battle):
        if not self.on(1):
            return
        if battle.round == self.blind_round:
            for hero in battle.alive_heroes():
                hero.blind = True
        if battle.round % 3 == 0:
            targets = battle.alive_heroes()
            if targets:
                self.blind_round = battle.round + 1
                self.prophecy = battle.rng.choice(targets)
                self.say(battle, 1, f"神谕：下回合全队无处可躲，{self.prophecy.label} 将首当其冲")

    def pick_target(self, battle, candidates):
        if (
            self.prophecy is not None
            and self.prophecy.alive
            and battle.round == self.blind_round
            and self.prophecy in candidates
        ):
            return self.prophecy
        return super().pick_target(battle, candidates)

    def damaged(self, battle):
        if self.on(2) and not self.summoned and battle.boss.ratio < 0.5:
            self.summoned = True
            for index in (1, 2):
                battle.summon(f"小猪守护灵{index}", 0.15, 0.5)
            self.say(battle, 2, "守护神降临！两只小猪守护灵挡在了神明面前，得先打倒它们")


class Zombie(BossMechanics):
    pig_id = "zombie-pig"
    MECHANICS = (
        (
            "尸毒",
            "被它咬中的猪叠 1 层尸毒，每层每回合损失 4% 生命，最多 3 层；中毒期间只获得 25% 治疗。净化会完整移除尸毒",
        ),
        ("一瘸一拐", "总是最后一个行动，但每次行动后有 30% 概率再扑咬一次"),
        ("尸变", "它亲手击倒的队员猪会变成僵尸小猪，站到敌方一起作战（生命为原来的 40%）"),
    )
    ATK = 0.92
    HP = 0.84
    GUARDS_FIRST = False

    @staticmethod
    def poison(unit):
        # The zombie's own skills also apply a dot called 尸毒; only count the stacking one.
        return next((dot for dot in unit.dots if dot.get("stacks")), None)

    def poisoned(self, unit) -> int:
        dot = self.poison(unit)
        return dot["stacks"] if dot else 0

    def outgoing(self, battle, target, dealt):
        if not self.on(0) or not target.alive:
            return
        dot = self.poison(target)
        if dot is None:
            target.dots.append({"pct": 4, "turns": 99, "label": "尸毒×1", "stacks": 1})
        elif dot["stacks"] < 3:
            dot["stacks"] += 1
            dot["pct"] = 4 * dot["stacks"]
            dot["label"] = f"尸毒×{dot['stacks']}"
            if dot["stacks"] == 3:
                self.say(battle, 0, f"{target.label} 身上的尸毒叠满了 3 层")

    def heal_factor(self, battle, unit):
        return 0.25 if unit.side == 0 and self.poisoned(unit) else 1.0

    def order(self, battle, units):
        if not self.on(1):
            return units
        return [u for u in units if u is not battle.boss] + [u for u in units if u is battle.boss]

    def extra_actions(self, battle):
        return 1 if self.on(1) and battle.rng.random() < 0.3 else 0

    def hero_fell(self, battle, hero):
        if self.on(2) and battle.acting is battle.boss:
            battle.summon_from(hero, f"僵尸·{hero.data['name']}", 0.4)
            self.say(battle, 2, f"{hero.label} 被啃了一口……尸变了！变成僵尸小猪站到了敌方")


class TwoFacedGhost(BossMechanics):
    pig_id = "pighub0876"
    MECHANICS = (
        (
            "阴阳两面",
            "每 2 回合切换一次：阴面时普通伤害减半、真实伤害和持续伤害翻倍；阳面时持续伤害无效、普通伤害 +20%",
        ),
        ("附身", "每 4 回合附身一只队员猪，它的下一次行动改为攻击一名队友"),
        ("双面夹击", "生命低于 40% 后，每回合结束时同时打一下生命最高和最低的两只猪"),
    )
    ATK = 1.26
    HP = 1.5

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.face = "阴"

    def round_start(self, battle):
        if self.on(0) and battle.round % 2 == 1:
            self.face = "阴" if battle.round % 4 == 1 else "阳"
            hint = (
                "普通攻击打不痛，用真实伤害和持续伤害"
                if self.face == "阴"
                else "持续伤害无效，用普通攻击"
            )
            self.say(battle, 0, f"{battle.boss.label}翻到了{self.face}面：{hint}")
        heroes = battle.present_heroes()
        if self.on(1) and battle.round % 4 == 0 and len(heroes) >= 2:
            target = battle.rng.choice(heroes)
            target.possessed = True
            self.say(battle, 1, f"{battle.boss.label}附身到了 {target.label} 身上")

    def incoming(self, battle, attacker, raw, effect):
        if not self.on(0):
            return raw
        if self.face == "阴":
            return raw * 2 if effect.get("true") else raw * 0.5
        return raw if effect.get("true") else raw * 1.2

    def dot_factor(self, battle, unit):
        if unit is battle.boss and self.on(0):
            return 2.0 if self.face == "阴" else 0.0
        return 1.0

    def round_end(self, battle):
        boss = battle.boss
        heroes = battle.present_heroes()
        if self.on(2) and boss.alive and boss.ratio < 0.4 and heroes:
            high, low = max(heroes, key=lambda h: h.hp), min(heroes, key=lambda h: h.hp)
            ends = [high] if high is low else [high, low]
            hits = [f"{h.label} -{round(battle.strike(boss, h, 0.8))}" for h in ends]
            self.say(battle, 2, "双面夹击！" + "，".join(hits))


class Skeleton(BossMechanics):
    pig_id = "skeleton-pig"
    MECHANICS = (
        ("白骨重组", "被打倒两次都会重组站起来，分别回到 35% 和 15% 生命，每次重组后防御 +20%"),
        ("瘦成这样", "多段攻击每一段伤害 -40%，单段攻击伤害 +25%"),
        (
            "红烧爆炒清蒸",
            "每 3 回合按顺序施放：红烧（全队流血）、爆炒（全队受到 0.6 倍伤害）、清蒸（全队速度 -20%，2 回合）",
        ),
    )
    ATK = 1.35
    HP = 1.55
    DISHES = ("红烧", "爆炒", "清蒸")

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.lives = 2 if self.on(0) else 0
        self.cooked = 0

    def prevent_fall(self, battle):
        if not self.lives:
            return False
        self.lives -= 1
        boss = battle.boss
        boss.hp = boss.max_hp * (0.35 if self.lives else 0.15)
        boss.dots.clear()
        boss.mods["def"] = boss.mods.get("def", 0) + 20
        self.say(
            battle,
            0,
            f"白骨重组！散落一地的骨头又拼回了 {boss.label}，生命回到 {boss.ratio:.0%}，防御 +20%",
        )
        return True

    def incoming(self, battle, attacker, raw, effect):
        if not self.on(1):
            return raw
        return raw * 0.6 if effect.get("hits", 1) > 1 else raw * 1.25

    def round_start(self, battle):
        if not self.on(2) or battle.round % 3:
            return
        dish = self.DISHES[self.cooked % 3]
        self.cooked += 1
        heroes = battle.present_heroes()
        if dish == "红烧":
            for hero in heroes:
                hero.dots.append({"pct": 3, "turns": 3, "label": "红烧"})
            self.say(battle, 2, "红烧！全队被浇了一身热油，持续流血 3 回合")
        elif dish == "爆炒":
            hits = [f"{h.label} -{round(battle.strike(battle.boss, h, 0.6))}" for h in heroes]
            self.say(battle, 2, "爆炒！" + "，".join(hits))
        else:
            for hero in heroes:
                hero.buffs.append({"stat": "spd", "pct": -20, "turns": 2})
            self.say(battle, 2, "清蒸！全队被蒸得晕乎乎的，速度 -20% 2 回合")


class Nezha(BossMechanics):
    pig_id = "pighub0233"
    MECHANICS = (
        ("风火轮", "每回合总是第一个出手；每 2 回合留下火圈，下一回合全队灼烧 4% 生命"),
        ("混天绫", "每 3 回合把全队的护盾和闪避次数抢到自己身上"),
        (
            "不服输",
            "每被暴击一次攻击 +8%（最多 5 层）；生命低于 25% 时扔出乾坤圈，对全队造成 2 倍伤害",
        ),
    )
    ATK = 1.06
    HP = 1.12

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.ring = False
        self.stacks = 0
        self.thrown = False

    def order(self, battle, units):
        if not self.on(0):
            return units
        return [u for u in units if u is battle.boss] + [u for u in units if u is not battle.boss]

    def round_start(self, battle):
        boss = battle.boss
        if self.on(0):
            if self.ring:
                self.ring = False
                for hero in battle.present_heroes():
                    battle.hurt(hero, hero.max_hp * 0.04)
                self.say(battle, 0, "风火轮留下的火圈烧了起来，全队灼烧 4% 生命")
            if battle.round % 2 == 0:
                self.ring = True
                self.say(battle, 0, f"{boss.label}踩着风火轮绕场一圈，地上留下了火圈")
        if self.on(1) and battle.round % 3 == 0:
            shield = evade = 0
            for hero in battle.present_heroes():
                shield += hero.shield
                evade += hero.evade
                hero.shield, hero.evade = 0, 0
            boss.shield += shield
            boss.evade += evade
            if shield or evade:
                self.say(battle, 1, f"混天绫一卷，抢走了全队 {round(shield)} 护盾和 {evade} 次闪避")
            else:
                self.say(battle, 1, "混天绫扫了一圈，全队身上什么也没有")

    def crit_against(self, battle, attacker):
        if self.on(2) and self.stacks < 5:
            self.stacks += 1
            battle.boss.mods["atk"] = battle.boss.mods.get("atk", 0) + 8
            self.say(battle, 2, f"{battle.boss.label}不服输！攻击 +8%（{self.stacks}/5）")
        return True

    def damaged(self, battle):
        boss = battle.boss
        if self.on(2) and not self.thrown and boss.ratio < 0.25:
            self.thrown = True
            hits = [
                f"{h.label} -{round(battle.strike(boss, h, 2.0))}" for h in battle.present_heroes()
            ]
            self.say(battle, 2, "乾坤圈全力一掷！" + "，".join(hits))


class FishDuke(BossMechanics):
    pig_id = "pighub0007"
    MECHANICS = (
        ("潮汐", "涨潮、退潮每回合交替：涨潮时全队速度 -15%、它闪避 +10；退潮时它搁浅，防御 -30%"),
        (
            "公爵御令",
            "每 4 回合下一道令，持续 2 回合：禁疗令让治疗、护盾和持续回复失效，禁技令让队员只能用普攻",
        ),
        (
            "龙鳞甲",
            "身上 20 片鳞，鳞片在时受到的伤害 -20%；每挨一次打掉一片，鳞片给随机一只队员猪 3% 生命的护盾",
        ),
    )
    ATK = 1.45
    HP = 2.1

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.tide = []
        self.decree = ""
        self.until = 0
        self.scales = 20 if self.on(2) else 0

    @staticmethod
    def shift(unit, key, value):
        unit.mods[key] = unit.mods.get(key, 0) + value

    def round_start(self, battle):
        boss = battle.boss
        if self.on(0):
            for unit, key, value in self.tide:
                self.shift(unit, key, -value)
            if battle.round % 2 == 1:
                self.tide = [(boss, "dodge", 10)] + [(h, "spd", -15) for h in battle.alive_heroes()]
                text = "涨潮了：全队速度 -15%，公爵在浪里游刃有余"
            else:
                self.tide = [(boss, "def", -30)]
                text = f"退潮了：{boss.label}搁浅在沙滩上，防御 -30%"
            for unit, key, value in self.tide:
                self.shift(unit, key, value)
            self.say(battle, 0, text)
        if self.on(1) and battle.round % 4 == 0:
            self.decree = battle.rng.choice(("禁疗令", "禁技令"))
            self.until = battle.round + 1
            rule = "治疗、护盾和持续回复全部失效" if self.decree == "禁疗令" else "队员只能用普攻"
            self.say(battle, 1, f"公爵颁布{self.decree}：2 回合内{rule}")

    def active(self, battle, kind) -> bool:
        return self.decree == kind and battle.round <= self.until

    def heal_factor(self, battle, unit):
        return 0.0 if unit.side == 0 and self.active(battle, "禁疗令") else 1.0

    def blocks_effect(self, battle, unit, effect):
        return (
            unit.side == 0
            and self.active(battle, "禁疗令")
            and effect["type"] in ("heal", "shield", "regen")
        )

    def force_basic(self, battle, unit):
        return self.active(battle, "禁技令")

    def incoming(self, battle, attacker, raw, effect):
        if self.scales <= 0:
            return raw
        self.scales -= 1
        heroes = battle.present_heroes()
        if heroes:
            hero = battle.rng.choice(heroes)
            hero.shield += hero.max_hp * 0.03
        if not self.scales:
            self.say(battle, 2, f"{battle.boss.label}的龙鳞全部脱落了")
        return raw * 0.8


class DeepSea(BossMechanics):
    pig_id = "pighub0830"
    MECHANICS = (
        ("深海水压", "每回合全队损失（1 + 回合数 ÷ 4）% 生命，越往后越疼"),
        (
            "灯笼鱼诱光",
            "每 3 回合诱惑攻击最高的队员猪：它的下一击对 boss 必定暴击，但 boss 的下一次攻击会对它造成双倍伤害",
        ),
        ("深渊暗流", "生命低于 50% 后全队受到的治疗减半，护盾每回合流失一半"),
    )
    ATK = 1.3
    HP = 1.55

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.abyss = False

    def round_start(self, battle):
        if self.on(0):
            pct = 1 + battle.round / 4
            for hero in battle.present_heroes():
                battle.hurt(hero, hero.max_hp * pct / 100)
            if battle.round % 4 == 1:
                self.say(battle, 0, f"越潜越深，水压让全队每回合损失 {pct:.1f}% 生命")
        heroes = battle.present_heroes()
        if self.on(1) and battle.round % 3 == 0 and heroes:
            target = max(heroes, key=lambda h: h.stat("atk"))
            target.lured = True
            self.say(
                battle, 1, f"灯笼鱼的光吸引了 {target.label}：它的下一击必定暴击，但也会成为猎物"
            )

    def damaged(self, battle):
        if self.on(2) and not self.abyss and battle.boss.ratio < 0.5:
            self.abyss = True
            self.say(battle, 2, "马里亚纳海沟的暗流涌了上来：全队治疗减半，护盾每回合流失一半")

    def heal_factor(self, battle, unit):
        return 0.5 if self.abyss and unit.side == 0 else 1.0

    def round_end(self, battle):
        if self.abyss:
            for hero in battle.alive_heroes():
                hero.shield *= 0.5


class Alien(BossMechanics):
    pig_id = "alien-pig"
    MECHANICS = (
        (
            "绑架光束",
            "每 4 回合把一只队员猪吸进 UFO 离场 2 回合；回来时被做了实验，随机一项属性 +20% 或 -20%",
        ),
        ("自我怀疑光波", "每 3 回合发动一次，当回合全队伤害和治疗 -40%"),
        (
            "召回母舰",
            "生命第一次低于 50% 时离场 2 回合，期间母舰每回合扫射全队、队员打不到它；回来时回复 15% 生命",
        ),
    )
    ATK = 1.22
    HP = 1.48
    STATS = {"atk": "攻击", "def": "防御", "spd": "速度"}

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.abducted = []
        self.doubt = 0
        self.called = False
        self.docked = False

    def round_start(self, battle):
        boss = battle.boss
        for hero in list(self.abducted):
            if hero.away:
                continue
            self.abducted.remove(hero)
            key = battle.rng.choice(tuple(self.STATS))
            value = battle.rng.choice((20, -20))
            hero.mods[key] = hero.mods.get(key, 0) + value
            sign = "+" if value > 0 else ""
            self.say(
                battle, 0, f"{hero.label} 被放了回来，被做了实验：{self.STATS[key]}{sign}{value}%"
            )
        if self.docked:
            if boss.away:
                hits = [
                    f"{h.label} -{round(battle.strike(boss, h, 0.6))}"
                    for h in battle.present_heroes()
                ]
                self.say(battle, 2, "母舰扫射！" + "，".join(hits))
            else:
                self.docked = False
                healed = battle.heal(boss, boss.max_hp * 0.15)
                self.say(battle, 2, f"{boss.label}从母舰回来了，回复 {healed} 生命")
        heroes = battle.present_heroes()
        if self.on(0) and battle.round % 4 == 0 and len(heroes) >= 2:
            target = battle.rng.choice(heroes)
            target.away = 2
            self.abducted.append(target)
            self.say(battle, 0, f"绑架光束！{target.label} 被吸进了 UFO")
        if self.on(1) and battle.round % 3 == 0:
            self.doubt = battle.round
            self.say(battle, 1, "自我怀疑光波：全队开始怀疑猪生，这回合伤害和治疗 -40%")

    def outgoing_factor(self, battle, attacker, target):
        return 0.6 if attacker.side == 0 and battle.round == self.doubt else 1.0

    def heal_factor(self, battle, unit):
        return 0.6 if unit.side == 0 and battle.round == self.doubt else 1.0

    def damaged(self, battle):
        boss = battle.boss
        if self.on(2) and not self.called and boss.ratio < 0.5:
            self.called = self.docked = True
            boss.away = 2
            self.say(battle, 2, f"{boss.label}召回了母舰，躲进去不出来了")


STAR_SKILL = {
    "name": "星尘",
    "text": "细碎的星光。",
    "cd": 0,
    "when": None,
    "passive": False,
    "effects": [{"type": "damage", "power": 0.7}],
}


class StarCluster(BossMechanics):
    pig_id = "pighub0872"
    MECHANICS = (
        ("星团聚合", "开场是星核加 2 颗小星；每 5 回合，星核用自己 10% 的生命补回被打碎的小星"),
        ("引力牵引", "打在星团任何一员身上的伤害，都平均分给所有还活着的成员"),
        (
            "超新星倒计时",
            "星核生命低于 25% 后倒计时 3 回合，没打倒它就对全队造成各自最大生命 40% 的伤害",
        ),
    )
    ATK = 1.12
    HP = 1.2
    GUARDS_FIRST = False

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.stars = []
        self.countdown = None

    def spawn(self, battle):
        self.stars.append(battle.summon(f"小星{len(self.stars) + 1}", 0.15, 0.35, STAR_SKILL))

    def setup(self, battle):
        if self.on(0):
            self.spawn(battle)
            self.spawn(battle)
            self.say(battle, 0, f"{battle.boss.label}分出了两颗小星，围着星核打转")

    def round_start(self, battle):
        boss = battle.boss
        interval = 5 if self.counter_ready else 3
        if not self.on(0) or battle.round % interval:
            return
        missing = 2 - sum(1 for star in self.stars if star.alive)
        cost = boss.max_hp * (0.1 if self.counter_ready else 0.0)
        rebuilt = 0
        for _ in range(missing):
            if boss.hp <= cost * 1.5:
                break
            boss.hp -= cost
            self.spawn(battle)
            rebuilt += 1
        if rebuilt:
            suffix = "" if self.counter_ready else "（缺少核心反制，重建不再消耗星核生命）"
            self.say(battle, 0, f"星团聚合：星核分出 {rebuilt} 颗新的小星{suffix}")

    def foe_hit(self, battle, attacker, target, raw, effect):
        if not self.on(1):
            return raw
        members = [f for f in battle.foes if f.alive and not f.away]
        if len(members) < 2 or target not in members:
            return raw
        share = raw / len(members)
        for member in members:
            if member is not target:
                battle.hurt(member, share)
        return share

    def damaged(self, battle):
        if self.on(2) and self.countdown is None and battle.boss.ratio < 0.25:
            self.countdown = 3
            self.say(battle, 2, "星核开始坍缩！超新星倒计时 3 回合")

    def round_end(self, battle):
        if not self.countdown:
            return
        self.countdown -= 1
        if self.countdown:
            self.say(battle, 2, f"超新星倒计时 {self.countdown}……")
            return
        pct = 0.4 if self.counter_ready else 0.7
        hits = [
            f"{h.label} -{round(battle.hurt(h, h.max_hp * pct))}" for h in battle.present_heroes()
        ]
        self.say(battle, 2, "超新星爆发！" + "，".join(hits))


class CosmicRing(BossMechanics):
    pig_id = "pighub0336"
    MECHANICS = (
        (
            "星环环绕",
            "身边 3 道星环，每道完整挡下一次攻击，多段攻击每段都算一次；每 3 回合恢复 1 道",
        ),
        (
            "引力场",
            "每回合把速度最快的猪拉近，它当回合受到的伤害 +25%；把最慢的推远，它当回合造成的伤害 -25%",
        ),
        (
            "陨石雨",
            "每 4 回合落下 3 颗陨石，每颗打随机一只猪 0.8 倍伤害；被砸中两次的猪眩晕 1 回合",
        ),
    )
    ATK = 1.4
    HP = 1.65

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.rings = 3 if self.on(0) else 0
        self.near = self.far = None

    def setup(self, battle):
        if self.rings:
            self.say(battle, 0, f"{battle.boss.label}身边环绕着 3 道星环")

    def incoming(self, battle, attacker, raw, effect):
        if self.rings and not effect.get("true"):
            self.rings -= 1
            battle.void_note = f"星环挡下了这一击，还剩 {self.rings} 道"
            return 0
        return raw

    def round_start(self, battle):
        boss = battle.boss
        if self.on(0) and battle.round % 3 == 0 and self.rings < 3:
            self.rings += 1
            self.say(battle, 0, f"星环重新转了起来，现在有 {self.rings} 道")
        heroes = battle.present_heroes()
        self.near = self.far = None
        if self.on(1) and len(heroes) >= 2:
            self.near = max(heroes, key=lambda h: h.stat("spd"))
            self.far = min(heroes, key=lambda h: h.stat("spd"))
            if battle.round % 3 == 1:
                self.say(
                    battle,
                    1,
                    f"引力场：{self.near.label} 被拉近（受到伤害 +25%），"
                    f"{self.far.label} 被推远（造成伤害 -25%）",
                )
        if self.on(2) and battle.round % 4 == 0 and heroes:
            counts = {}
            for _ in range(3):
                target = battle.rng.choice(heroes)
                battle.strike(boss, target, 0.8)
                counts[target] = counts.get(target, 0) + 1
            parts = []
            for target, count in counts.items():
                text = f"{target.label}×{count}"
                if count >= 2 and target.alive:
                    target.stun = max(target.stun, 1)
                    target.stun_label = "眩晕"
                    text += "（眩晕）"
                parts.append(text)
            self.say(battle, 2, "陨石雨！砸中了 " + "，".join(parts))

    def outgoing_factor(self, battle, attacker, target):
        if attacker is self.far:
            return 0.75
        if target is self.near and attacker.side == 1:
            return 1.25
        return 1.0


class Campfire(BossMechanics):
    pig_id = "pighub0315"
    MECHANICS = (
        (
            "围炉传火",
            "队员对它造成的伤害会变成余烬；它下一次出手时，对目标额外造成余烬 22% 的真实伤害，然后清空",
        ),
        (
            "人多更暖",
            "场上存活队员越多窑火越旺：4/3/2/1 只时它攻击 +22%/+8%/0%/-15%",
        ),
        (
            "烤位轮值",
            "每回合按座位轮流当烤位：强制点名该座且受伤 +20%，回合结束烤位回 10% 生命，左右邻座各回 5%",
        ),
    )
    HP = 1.3
    ATK = 1.2
    HEAT = {4: 22, 3: 8, 2: 0, 1: -15}

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.embers = 0.0
        self.heat = 0
        self.roast = 0

    def round_start(self, battle):
        if self.on(1):
            alive = len(battle.alive_heroes())
            want = self.HEAT.get(alive, -25)
            if want != self.heat:
                battle.boss.mods["atk"] = battle.boss.mods.get("atk", 0) - self.heat + want
                self.heat = want
                sign = "+" if want > 0 else ""
                self.say(
                    battle,
                    1,
                    f"窑火旁还有 {alive} 只猪：人多更暖，{battle.boss.label}攻击 {sign}{want}%",
                    tag=f"{sign}{want}%",
                )
        if self.on(2):
            self.roast = (battle.round - 1) % 4
            hero = next((h for h in battle.alive_heroes() if h.seat == self.roast), None)
            if hero:
                self.say(
                    battle,
                    2,
                    f"烤位轮到座位 {self.roast}：{hero.label} 请上烤架",
                    targets=[hero],
                )
            else:
                self.say(battle, 2, f"烤位轮到座位 {self.roast}，可那位已经不在了")

    def boss_struck(self, battle, attacker, dealt):
        if self.on(0) and dealt > 0:
            self.embers += dealt

    def outgoing(self, battle, target, dealt):
        if not self.on(0) or self.embers <= 0 or not target.alive:
            return
        bonus = battle.hurt(target, self.embers * 0.22)
        bank = round(self.embers)
        self.embers = 0.0
        if bonus > 0:
            self.say(
                battle,
                0,
                f"围炉传火！余烬 {bank} 燎向 {target.label}，额外真实伤害 {round(bonus)}",
                targets=[target],
                tag=str(round(bonus)),
            )

    def pick_target(self, battle, candidates):
        if self.on(2):
            for hero in candidates:
                if hero.seat == self.roast:
                    return hero
        return super().pick_target(battle, candidates)

    def outgoing_factor(self, battle, attacker, target):
        if self.on(2) and attacker is battle.boss and target.seat == self.roast:
            return 1.2
        return 1.0

    def round_end(self, battle):
        if not self.on(2):
            return
        by_seat = {h.seat: h for h in battle.alive_heroes() if h.seat is not None}
        roast = by_seat.get(self.roast)
        if roast:
            healed = battle.heal(roast, roast.max_hp * 0.10)
            if healed:
                battle.note(f"烤位上的 {roast.label} 回了 {healed} 生命")
        for seat in (self.roast - 1, self.roast + 1):
            neighbor = by_seat.get(seat)
            if neighbor:
                healed = battle.heal(neighbor, neighbor.max_hp * 0.05)
                if healed:
                    battle.note(f"邻座 {neighbor.label} 被余温暖到，回了 {healed} 生命")


class RedHot(BossMechanics):
    pig_id = "pighub0710"
    MECHANICS = (
        (
            "越劝越火",
            "队员每次成功治疗或驱散，它攻击永久 +4%（最多 6 层）；越安慰它越红温",
        ),
        (
            "对线点名",
            "每回合锁定上回合伤害最高的队员：本回合只打它且伤害 ×1.3；若已倒下则改打生命最高者",
        ),
        (
            "已读不回",
            "每回合第一次伤害无效并留下已读标记；同回合再次命中会追问成功，使它自损 6% 最大生命并失去 1 层越劝越火",
        ),
    )
    HP = 1.35
    ATK = 1.15

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.stacks = 0
        self.lock = None
        self.round_damage = {}
        self.read = False
        self.replied = False

    def supported(self, battle, unit, kind):
        if not self.on(0) or self.stacks >= 6:
            return
        self.stacks += 1
        battle.boss.mods["atk"] = battle.boss.mods.get("atk", 0) + 4
        verb = "治疗" if kind == "heal" else "驱散"
        self.say(
            battle,
            0,
            f"{unit.label} 的{verb}反而把它劝火了！攻击 +4%（{self.stacks}/6）",
            targets=[unit],
            tag=str(self.stacks),
        )

    def round_start(self, battle):
        self.read = False
        self.replied = False
        if self.on(1):
            if self.round_damage:
                seat = max(self.round_damage, key=self.round_damage.get)
                self.lock = next((h for h in battle.heroes if h.seat == seat), None)
            else:
                self.lock = None
            self.round_damage = {}
            target = self._lock_target(battle)
            if target:
                self.say(
                    battle,
                    1,
                    f"对线点名：{battle.boss.label} 锁死了 {target.label}",
                    targets=[target],
                )

    def _lock_target(self, battle):
        if self.lock and self.lock.alive and not self.lock.away:
            return self.lock
        heroes = battle.present_heroes()
        return max(heroes, key=lambda h: h.hp) if heroes else None

    def pick_target(self, battle, candidates):
        if self.on(1):
            target = self._lock_target(battle)
            if target in candidates:
                return target
        return super().pick_target(battle, candidates)

    def outgoing_factor(self, battle, attacker, target):
        if self.on(1) and attacker is battle.boss and target is self._lock_target(battle):
            return 1.3
        return 1.0

    def incoming(self, battle, attacker, raw, effect):
        if self.on(2) and not self.read:
            self.read = True
            battle.void_note = "已读不回"
            self.say(
                battle,
                2,
                f"{battle.boss.label} 已读不回，把这一下划掉了；同回合再命中一次即可追问",
                targets=[attacker] if attacker.side == 0 else (),
                tag="read",
            )
            return 0
        return raw

    def boss_struck(self, battle, attacker, dealt):
        if attacker.seat is not None:
            self.round_damage[attacker.seat] = self.round_damage.get(attacker.seat, 0.0) + dealt
        if (
            not self.on(2)
            or not self.counter_ready
            or not self.read
            or self.replied
            or not battle.boss.alive
        ):
            return
        self.replied = True
        lost = battle.hurt(battle.boss, battle.boss.max_hp * 0.06)
        if self.stacks > 0 and self.on(0):
            self.stacks -= 1
            battle.boss.mods["atk"] = battle.boss.mods.get("atk", 0) - 4
            suffix = "，火气 -1 层"
        else:
            suffix = ""
        self.say(
            battle,
            2,
            f"追问成功！{battle.boss.label} 绷不住了，自损 {round(lost)} 生命{suffix}",
            targets=[attacker] if attacker.side == 0 else (),
            tag="burst",
        )

    def round_end(self, battle):
        pass


class DragonPig(BossMechanics):
    pig_id = "pighub0116"
    MECHANICS = (
        (
            "祥瑞错位",
            "每 2 回合结束：随机把一只队员身上的 1 个增益或全部护盾转移到自己身上",
        ),
        (
            "短腿起飞",
            "每 4 回合刨蹄蓄力 1 回合（防御 -30%）：蓄力期受伤 ≥12% 最大生命则肚子着地（自损 5% + 全队 1.05 倍），否则升空 1 回合，落地全队 0.75 倍并回 10% 生命",
        ),
        (
            "龙息扫座",
            "每 3 回合以随机一只队员的座位为中心，灼烧该座及相邻座位（最多 3 只）",
        ),
    )
    HP = 0.62
    ATK = 0.75

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.charging = False
        self.charge_damage = 0.0
        self.flying = False

    def round_start(self, battle):
        boss = battle.boss
        if self.flying and not boss.away:
            hits = [
                f"{h.label} -{round(battle.strike(boss, h, 0.75))}" for h in battle.present_heroes()
            ]
            healed = battle.heal(boss, boss.max_hp * 0.10)
            self.say(
                battle,
                1,
                f"{boss.label}歪斜落地！" + "，".join(hits) + f"，回复 {healed} 生命",
                targets=battle.present_heroes(),
                tag="land",
            )
            self.flying = False
        if self.charging:
            self.charging = False
            boss.mods["def"] = boss.mods.get("def", 0) + 30
            threshold = boss.max_hp * 0.12
            if self.charge_damage >= threshold:
                lost = battle.hurt(boss, boss.max_hp * 0.05)
                hits = [
                    f"{h.label} -{round(battle.strike(boss, h, 1.05))}"
                    for h in battle.present_heroes()
                ]
                self.say(
                    battle,
                    1,
                    f"短腿起飞失败！{boss.label} 肚子着地自损 {round(lost)}，" + "，".join(hits),
                    targets=battle.present_heroes(),
                    tag="crash",
                )
            else:
                boss.away = 1
                self.flying = True
                self.say(
                    battle,
                    1,
                    f"{boss.label} 居然飞起来了（歪歪扭扭），这回合打不到它",
                    tag="fly",
                )
            self.charge_damage = 0.0
        if self.on(1) and battle.round % 4 == 0 and boss.alive and not boss.away:
            self.charging = True
            self.charge_damage = 0.0
            boss.mods["def"] = boss.mods.get("def", 0) - 30
            self.say(
                battle,
                1,
                f"{boss.label} 开始刨蹄蓄力，防御 -30%——现在打它能把它摔下来",
                tag="charge",
            )
        if self.on(2) and battle.round % 3 == 0:
            heroes = [h for h in battle.present_heroes() if h.seat is not None]
            if not heroes:
                return
            center = battle.rng.choice(heroes)
            seats = {center.seat, center.seat - 1, center.seat + 1}
            targets = [h for h in heroes if h.seat in seats]
            parts = []
            for hero in targets:
                dealt = battle.strike(boss, hero, 0.95)
                hero.dots.append({"pct": 3, "turns": 2, "label": "龙息灼烧", "source": boss})
                parts.append(f"{hero.label} -{round(dealt)}")
            self.say(
                battle,
                2,
                f"龙息扫座！以座位 {center.seat} 为中心烧到了 " + "，".join(parts),
                targets=targets,
            )

    def boss_struck(self, battle, attacker, dealt):
        if self.charging:
            self.charge_damage += dealt

    def round_end(self, battle):
        if not self.on(0) or not battle.boss.alive or battle.round % 2:
            return
        candidates = [
            h
            for h in battle.alive_heroes()
            if h.shield > 0 or any(b.get("pct", 0) > 0 for b in h.buffs)
        ]
        if not candidates:
            return
        hero = battle.rng.choice(candidates)
        buffs = [b for b in hero.buffs if b.get("pct", 0) > 0]
        if buffs and (hero.shield <= 0 or battle.rng.random() < 0.5):
            buff = battle.rng.choice(buffs)
            hero.buffs.remove(buff)
            battle.boss.buffs.append(dict(buff))
            self.say(
                battle,
                0,
                f"祥瑞错位：{hero.label} 的增益飞到了 {battle.boss.label} 头上",
                targets=[hero],
                tag="buff",
            )
        elif hero.shield > 0:
            amount = hero.shield
            battle.boss.shield += amount
            hero.shield = 0
            self.say(
                battle,
                0,
                f"祥瑞错位：{hero.label} 的 {round(amount)} 护盾赐给了 {battle.boss.label}",
                targets=[hero],
                tag="shield",
            )


BOSSES = {
    cls.pig_id: cls
    for cls in (
        Goblin,
        Frozen,
        Everest,
        Error404,
        Mechanical,
        Cyberpunk,
        Demon,
        ChainedKing,
        PigGod,
        Zombie,
        TwoFacedGhost,
        Skeleton,
        Nezha,
        FishDuke,
        DeepSea,
        Alien,
        StarCluster,
        CosmicRing,
        Campfire,
        RedHot,
        DragonPig,
    )
}


# Build-facing counterplay shown on the raid detail card and battle report.  Keeping
# it separate from MECHANICS lets old saves/events continue to identify mechanics
# by their stable name while the UI can explain how an automatic party answers
# them.  Bracketed words are searchable skill/build tags rather than manual orders.
BOSS_COUNTERS = {
    "goblin-pig": (
        "【持续伤害/多段】潜伏时持续伤害仍会结算；现身后自动恢复直接命中",
        "【净化】可清掉熏味；速度增益也能抵消减速",
        "【爆发】躺平的 2 回合是自动集火窗口，伤害提高 30%",
    ),
    "frozen-pig": (
        "【净化/抗控】净化可清寒冷，抗控或篝火余温可挡冻结",
        "【多段/破盾】攻击次数多的队伍更快击碎冰壳，之后暴击恢复",
        "【爆发/减攻】冰壳碎后趁低防爆发；减攻可压住它的攻击提升",
    ),
    "everest-pig": (
        "【加速/治疗】速度增益抵消爬升，群疗与护盾覆盖后期缺氧",
        "【多段/持续伤害】单段封顶，攻击段数和持续伤害不受总量封顶",
        "【护盾/净化】第 5 回合前周期性预警；护盾承伤，净化解除臣服",
    ),
    "error-404-pig": (
        "【必中/真实伤害】未被观测时仍稳定命中，不依赖随机观测",
        "【净化/抗控】净化型技能会清除自身 404，抗控构筑降低空转损失",
        "【斩杀/爆发】从 40% 以上直接击倒可跳过回滚；否则只触发一次",
    ),
    "mechanical-pig": (
        "【护盾/守护/减伤】锁定提前 1 回合预警，防护可自动覆盖被点名者",
        "【多段/无视防御】6 次命中使其过热 2 回合；穿透效果收益翻倍",
        "【稳定收益】系统故障是队伍的免费输出与恢复窗口",
    ),
    "cyberpunk-pig": (
        "【错峰增益/持续恢复】第 3 回合清除后再施加增益，持续恢复不被删除",
        "【驱散】任意成功驱散都能把累计攻击加成清零",
        "【爆发/减攻/护盾】40% 后尽快收尾；双动同时承受减攻与护盾",
    ),
    "demon-pig": (
        "【净化】开场诅咒可被净化；群体恢复可覆盖未及时清掉的流血",
        "【均衡治疗】保持全队血线接近，生命比例对调的收益与风险都会降低",
        "【护盾/守护/复活】避免减员就不会给它回血和攻击成长",
    ),
    "chained_crown_pig": (
        "【护盾/群疗】每跨 25% 生命自动预警并冲击全队，群体防护承接冲击",
        "【爆发/减攻】最后一条锁链断裂后尽快收尾，减攻压住完全解放",
        "【加速/净化/抗控】提高最低速度减少固定受控；净化可解除臣服",
    ),
    "pig_god": (
        "【多段/稳定伤害】多次命中摊薄暴击被好运抵消的波动",
        "【护盾/闪避】目标会提前 1 回合预告，自动防护可覆盖被点名者",
        "【范围/多段】守护灵出现后系统自动优先攻击；范围伤害可同时压低它们",
    ),
    "zombie-pig": (
        "【净化/持续恢复】净化是核心反制：立刻清除尸毒并恢复完整治疗效率；持续恢复只能辅助稳线",
        "【控制/减攻】控制与减攻减少末位扑咬；额外扑咬不是必定触发",
        "【护盾/守护/复活】避免由 boss 亲手击倒即可阻止尸变增援",
    ),
    "pighub0876": (
        "【真实/持续伤害/普通伤害】伤害类型齐全的队伍会自动利用阴阳弱点",
        "【护盾/守护】附身只持续下一次行动，团队防护会自动承接误伤",
        "【群疗/减攻】40% 后的双目标夹击由群疗、护盾和减攻共同处理",
    ),
    "skeleton-pig": (
        "【续航】重组只有 2 次且血量递减，稳定续航可换完它的三条命",
        "【单段爆发】单段伤害提高 25%；多段仍可用于破除其他次数型效果",
        "【净化/加速】流血和减速都可净化；群疗覆盖爆炒的固定群伤",
    ),
    "pighub0233": (
        "【护盾/持续恢复】先手不可抢，但开场护盾和持续恢复可提前生效",
        "【即时治疗】少带可被抢走的护盾/闪避，或用即时治疗维持血线",
        "【低暴击/减攻/群盾】低暴击构筑减慢成长；群盾承接 25% 的乾坤圈",
    ),
    "pighub0007": (
        "【加速/爆发】加速抵消涨潮；退潮的低防回合会被自动爆发利用",
        "【构筑多样性】禁疗与禁技交替，伤害、护盾、持续恢复可互相补位",
        "【多段】每次命中剥 1 片鳞并给队伍护盾，多段技能加速破甲",
    ),
    "pighub0830": (
        "【群疗/护盾】水压逐回合预警并递增，群体续航覆盖固定生命损失",
        "【守护/减伤/护盾】诱光者必暴击后被标记，团队防护承接后续双倍攻击",
        "【持续恢复/减攻】50% 前铺持续恢复，暗流后用减攻降低续航压力",
    ),
    "alien-pig": (
        "【均衡队伍/召唤】随机离场不会锁死单一职业，均衡构筑能自动补位",
        "【持续伤害/护盾】每 3 回合减益仅持续当回合，预先施加的效果照常结算",
        "【群疗/护盾/持续伤害】离场 2 回合无法直击，防护抗扫射且持续伤害照常结算",
    ),
    "pighub0872": (
        "【范围/多段】范围技能压低小星；重建会消耗星核 10% 生命",
        "【稳定收益】伤害会自动均摊，系统选择任何目标都不会浪费单体输出",
        "【爆发/护盾/群疗】25% 时明确倒数 3 回合；爆发击倒或防护承受爆炸",
    ),
    "pighub0336": (
        "【多段/真实伤害】多段逐道拆环，真实伤害可直接穿过星环",
        "【均衡速度/护盾】缩小速度差降低牵引影响，护盾保护被拉近者",
        "【群盾/净化/抗控】第 4 回合周期预警；群盾承伤，净化解除眩晕",
    ),
    "pighub0315": (
        "【减攻/护盾/守护】伤害转为下次攻击的余烬，团队防护承接被点名者",
        "【减攻/续航】人数越多攻击越高但火力也更足，减攻与群疗稳定满编收益",
        "【均衡续航】烤位由系统按座次轮换，并自动回复该座及相邻队员",
    ),
    "pighub0710": (
        "【护盾/持续恢复】直接治疗和驱散会加火；护盾、减伤与已铺恢复不触发劝火",
        "【守护/护盾/减伤】按上回合伤害自动点名，保护型技能承接 1.3 倍攻击",
        "【多段/连携】首击会提示已读；同回合第 2 次命中自动追问并使其自损",
    ),
    "pighub0116": (
        "【即时治疗/净化】少囤可被转移的增益和护盾，或用驱散清掉偷走的增益",
        "【爆发/多段】每 4 回合蓄力且防御 -30%，累计 12% 生命伤害即可打断",
        "【群盾/群疗/净化】第 3 回合周期预警；群防承伤，净化灼烧",
    ),
}

# Hand-authored primary build checks. `tags` are restricted to real pig-pen
# filter tags; the detailed COUNTERS above still explain secondary answers.
# Missing a core answer strengthens the pressure already described by that
# boss's mechanics rather than adding a new unrelated attack.
CORE_CHECKS = {
    "goblin-pig": {
        "name": "熏味失控",
        "tags": ("净化",),
        "failure": "熏味无法清除，潜伏偷袭伤害提高且更难在躺平窗口收尾",
        "outgoing": 1.35,
        "incoming": 0.75,
    },
    "frozen-pig": {
        "name": "寒冷冻结",
        "tags": ("净化", "抗控"),
        "failure": "寒冷会稳定叠成冻结，冰壳阶段的攻防压力同时提高",
        "outgoing": 1.30,
        "incoming": 0.80,
    },
    "everest-pig": {
        "name": "单击封顶",
        "tags": ("多段", "持续伤害"),
        "failure": "队伍无法绕过单次伤害上限，战斗拖入缺氧与连续雪崩",
        "outgoing": 1.50,
        "incoming": 0.45,
    },
    "error-404-pig": {
        "name": "缓存回滚",
        "tags": ("爆发", "斩杀"),
        "failure": "无法跨过回滚线，未被观测与生命回滚会显著拉长战斗",
        "outgoing": 1.28,
        "incoming": 0.76,
    },
    "mechanical-pig": {
        "name": "金属过热",
        "tags": ("多段", "爆发"),
        "failure": "难以触发外壳过热，光学锁定会在高防阶段持续处刑队员",
        "outgoing": 1.50,
        "incoming": 0.45,
    },
    "cyberpunk-pig": {
        "name": "义体进化",
        "tags": ("驱散", "减益"),
        "failure": "永久攻击成长无法归零，超频后的双动会迅速失控",
        "outgoing": 1.35,
        "incoming": 0.82,
    },
    "demon-pig": {
        "name": "开场诅咒",
        "tags": ("净化", "持续治疗"),
        "failure": "四种诅咒会全程保留，血线交换更容易制造减员与恶魔成长",
        "outgoing": 1.50,
        "incoming": 0.45,
    },
    "chained_crown_pig": {
        "name": "断链冲击",
        "tags": ("群疗", "护盾", "持续治疗"),
        "failure": "三次断链群伤无人承接，王者归来前后会形成连续减员",
        "outgoing": 1.50,
        "incoming": 0.55,
    },
    "pig_god": {
        "name": "守护神降临",
        "tags": ("多段", "爆发"),
        "failure": "守护灵清理过慢，神谕点名会在拖长的战斗中反复发动",
        "outgoing": 1.55,
        "incoming": 0.40,
    },
    "zombie-pig": {
        "name": "尸毒治疗封锁",
        "tags": ("净化",),
        "failure": "尸毒持续叠层且治疗只剩 25%，倒下的队员还会发生尸变",
        "outgoing": 1.0,
        "incoming": 1.0,
    },
    "pighub0876": {
        "name": "阴阳换面",
        "tags": ("持续伤害", "爆发"),
        "failure": "伤害类型无法适应阴阳切换，夹击阶段会因战斗拖长而失守",
        "outgoing": 1.55,
        "incoming": 0.40,
    },
    "skeleton-pig": {
        "name": "三条骨命",
        "tags": ("爆发", "持续治疗"),
        "failure": "缺少单段爆发或长线续航，难以换完三条命并扛住轮番上菜",
        "outgoing": 1.40,
        "incoming": 0.58,
    },
    "pighub0233": {
        "name": "混天绫夺取",
        "tags": ("持续治疗", "减益"),
        "failure": "护盾与闪避被夺后没有替代续航，乾坤圈前的攻击成长无法压低",
        "outgoing": 1.50,
        "incoming": 0.45,
    },
    "pighub0007": {
        "name": "二十片龙鳞",
        "tags": ("多段", "爆发"),
        "failure": "龙鳞剥落过慢，禁疗与禁技会覆盖更长的高防战斗",
        "outgoing": 1.55,
        "incoming": 0.40,
    },
    "pighub0830": {
        "name": "深海水压",
        "tags": ("群疗", "持续治疗"),
        "failure": "递增水压无人覆盖，进入暗流后治疗减半会快速压垮全队",
        "outgoing": 1.40,
        "incoming": 0.60,
    },
    "alien-pig": {
        "name": "母舰离场",
        "tags": ("持续伤害", "护盾", "持续治疗"),
        "failure": "Boss 离场时队伍只能挨母舰扫射，无法靠预铺效果继续攻防",
        "outgoing": 1.55,
        "incoming": 0.40,
    },
    "pighub0872": {
        "name": "星团重建",
        "tags": ("多段", "爆发"),
        "failure": "小星重建速度超过清理速度，更容易拖到超新星爆发",
        "outgoing": 1.70,
        "incoming": 0.30,
    },
    "pighub0336": {
        "name": "三重星环",
        "tags": ("多段", "爆发"),
        "failure": "星环恢复速度接近拆除速度，陨石雨会在长线战斗中不断叠加",
        "outgoing": 1.55,
        "incoming": 0.40,
    },
    "pighub0315": {
        "name": "余烬反噬",
        "tags": ("减益", "守护", "铁壁"),
        "failure": "输出会转成下一击余烬，缺少减攻与保护时被点名者承受完整反噬",
        "outgoing": 1.60,
        "incoming": 0.35,
    },
    "pighub0710": {
        "name": "已读不回",
        "tags": ("多段", "连击", "持续治疗"),
        "failure": "每回合首击被抹除且直接治疗持续加火，难以触发追问自损",
        "outgoing": 1.70,
        "incoming": 0.30,
    },
    "pighub0116": {
        "name": "短腿起飞",
        "tags": ("爆发", "多段"),
        "failure": "蓄力期打不够 12% 生命，Boss 会升空、群伤并回复生命",
        "outgoing": 1.55,
        "incoming": 0.40,
    },
}

for _pig_id, _counters in BOSS_COUNTERS.items():
    BOSSES[_pig_id].COUNTERS = _counters

for _pig_id, _check in CORE_CHECKS.items():
    _boss = BOSSES[_pig_id]
    _boss.CORE_NAME = _check["name"]
    _boss.CORE_TAGS = _check["tags"]
    _boss.CORE_FAILURE = _check["failure"]
    _boss.UNPREPARED_OUTGOING = _check["outgoing"]
    _boss.UNPREPARED_INCOMING = _check["incoming"]
