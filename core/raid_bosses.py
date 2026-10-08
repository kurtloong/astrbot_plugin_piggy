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
    # Per-boss multipliers on top of the shared raid scaling.
    HP = 1.0
    ATK = 1.0
    DEF = 1.0
    # Whether heroes must clear the boss's summoned units before hitting the boss.
    GUARDS_FIRST = True

    def __init__(self, disabled: int | None = None):
        self.disabled = disabled

    def on(self, index: int) -> bool:
        return index != self.disabled

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
    HP = 0.85

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
                    battle.note(
                        f"{boss.label}从角落里窜出来偷袭 {target.label}，暴击造成 {round(dealt)} 伤害"
                    )
            elif battle.round % 3 == 0:
                self.lurking = True
                boss.stun = max(boss.stun, 1)
                boss.stun_label = "躲在角落"
                battle.note(f"{boss.label}钻回了角落，这回合谁也打不到它")
        if self.on(1):
            for hero in battle.alive_heroes():
                stacks = sum(1 for b in hero.buffs if b.get("tag") == "stench")
                if stacks < 6:
                    hero.buffs.append({"stat": "spd", "pct": -5, "turns": 99, "tag": "stench"})
            if battle.round == 1:
                battle.note("邋遢光环弥漫开来：一股说不清的味道，全队每回合速度 -5%")

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
            battle.note(f"{boss.label}开始摆烂：躺在角落什么都不管，慢慢回血，但浑身都是破绽")


class Frozen(BossMechanics):
    pig_id = "frozen-pig"
    MECHANICS = (
        ("寒气层数", "每次攻击给目标叠 1 层寒冷，叠满 3 层冻结 1 回合"),
        ("冰块外壳", "开场自带 25% 生命的冰壳，冰壳在时不会被暴击，伤害先扣冰壳"),
        ("融化", "冰壳碎掉后防御 -30%、攻击 +20%"),
    )
    HP = 0.7

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.shell = 0.0

    def setup(self, battle):
        if self.on(1):
            self.shell = battle.boss.max_hp * 0.25
            battle.note(f"{battle.boss.label}裹着一层 {round(self.shell)} 点的冰块外壳：生人勿近")

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
            battle.note(
                f"冰块外壳碎了……外表冷硬，内心却渴望温暖。{boss.label}融化了：防御 -30%，攻击 +20%"
            )
        else:
            battle.note("冰块外壳碎了")

    def outgoing(self, battle, target, dealt):
        if not self.on(0) or not target.alive:
            return
        target.chill += 1
        if target.chill < 3:
            return
        target.chill = 0
        if target.frost_ward:
            target.frost_ward = False
            battle.note(f"篝火的余温护住了 {target.label}，没有被冻住")
            return
        target.stun = max(target.stun, 1)
        target.stun_label = "冻结"
        battle.note(f"寒气叠满 3 层，{target.label} 被冻成了冰块")


class Everest(BossMechanics):
    pig_id = "everest-pig"
    MECHANICS = (
        ("海拔上升", "每回合全队速度 -4%，可以累积；第 12 回合起全队缺氧，每回合损失 4% 生命"),
        ("不可逾越", "单次受到的伤害最多只算它最大生命的 6%，多段和持续伤害更划算"),
        ("雪崩", "第 5、10、15 回合对全队造成 1.2 倍攻击伤害，每只猪 30% 概率臣服跳过下一回合"),
    )
    HP = 0.5
    DEF = 1.1

    def round_start(self, battle):
        boss = battle.boss
        if self.on(0):
            for hero in battle.alive_heroes():
                hero.mods["spd"] = hero.mods.get("spd", 0) - 4
            if battle.round == 1:
                battle.note("海拔越来越高，空气越来越稀薄，全队每回合都会慢一点")
            if battle.round >= 12:
                lost = [
                    round(battle.hurt(hero, hero.max_hp * 0.04)) for hero in battle.alive_heroes()
                ]
                if lost:
                    battle.note(f"高处不胜寒，全队缺氧，各损失约 {max(lost)} 生命")
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
                battle.note("雪峰压顶，雪崩来了！" + "，".join(parts))

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
    HP = 1.85

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.hidden = False
        self.rolled = False

    def round_start(self, battle):
        boss = battle.boss
        if self.on(0):
            self.hidden = battle.rng.random() < 0.5
            if self.hidden:
                battle.note(f"{boss.label}进入未被观测状态：没猪能确定它是否存在")
        if self.on(1) and battle.round % 4 == 0:
            targets = battle.alive_heroes()
            if targets:
                target = battle.rng.choice(targets)
                target.glitch = True
                battle.note(f"页面不存在：{target.label} 的下一次行动返回了 404")

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
            battle.note(f"缓存回滚！{boss.label}疯狂刷新，生命回到 60%，持续伤害也被清掉了")


class Mechanical(BossMechanics):
    pig_id = "mechanical-pig"
    MECHANICS = (
        ("光学锁定", "每 3 回合锁定当前生命最高的队员，下回合对它打出必中的 2 倍伤害"),
        ("金属外壳", "防御很高，无视防御效果对它翻倍；每挨 6 次攻击会过热，防御归零 2 回合"),
        ("系统 bug", "每回合 10% 概率忘了自己是猪，原地哼哼一回合"),
    )
    HP = 1.1
    DEF = 1.6

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
                battle.note(f"红色光学眼锁定了 {self.locked.label}，运算精准")

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
                battle.note("金属外壳过热冒烟了！防御归零 2 回合")
        return raw

    def round_end(self, battle):
        if self.locked is not None and battle.round > self.lock_round:
            self.locked = None
        if self.overheat:
            self.overheat -= 1
            if not self.overheat:
                battle.boss.zeroed.discard("def")
                battle.note("金属外壳冷却完毕，防御恢复")


class Cyberpunk(BossMechanics):
    pig_id = "cyberpunk-pig"
    MECHANICS = (
        ("黑客入侵", "每 3 回合清除全队的增益、护盾和反弹"),
        ("义体进化", "每回合攻击 +4%，一直累积；驱散效果能把累积的加成清零"),
        ("超频过载", "生命低于 40% 后每回合行动 2 次，但每回合损失 4% 生命"),
    )
    HP = 1.1

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
                battle.note(f"义体改造进行中：{boss.label}的攻击已累计 +{self.evolved}%")
        if self.on(0) and battle.round % 3 == 0:
            for hero in battle.alive_heroes():
                hero.buffs = [b for b in hero.buffs if b["pct"] < 0]
                hero.shield = 0
                hero.thorns.clear()
                hero.evade = 0
            battle.note("黑客入侵！全队的增益、护盾和反弹都被删掉了")

    def dispelled(self, battle):
        if self.evolved:
            battle.boss.mods["atk"] = battle.boss.mods.get("atk", 0) - self.evolved
            battle.note(f"驱散生效，{battle.boss.label}累计 +{self.evolved}% 的义体进化被清零")
            self.evolved = 0

    def damaged(self, battle):
        if self.on(2) and not self.overclock and battle.boss.ratio < 0.4:
            self.overclock = True
            battle.note(f"{battle.boss.label}处理器超频运行！之后每回合行动 2 次")

    def extra_actions(self, battle):
        return 1 if self.overclock else 0

    def round_end(self, battle):
        boss = battle.boss
        if self.overclock and boss.alive:
            lost = battle.hurt(boss, boss.max_hp * 0.04)
            battle.note(f"超频过热，{boss.label}损失 {round(lost)} 生命")


class Demon(BossMechanics):
    pig_id = "demon-pig"
    MECHANICS = (
        ("满肚子坏点子", "开场给每只队员猪随机挂一个诅咒：虚弱、迟缓、流血或易伤"),
        ("恶作剧", "每 4 回合把两只队员猪的生命比例对调"),
        ("得逞的坏笑", "每有一只队员猪倒下，它回复 15% 生命、攻击 +10%"),
    )
    HP = 2.1
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
            battle.note("满肚子坏点子：" + "，".join(parts))

    def round_start(self, battle):
        heroes = battle.alive_heroes()
        if self.on(1) and battle.round % 4 == 0 and len(heroes) >= 2:
            a, b = battle.rng.sample(heroes, 2)
            ra, rb = a.ratio, b.ratio
            a.hp, b.hp = a.max_hp * rb, b.max_hp * ra
            battle.note(f"恶作剧！{a.label} 和 {b.label} 的生命被对调了（{ra:.0%} ⇄ {rb:.0%}）")

    def hero_fell(self, battle, hero):
        boss = battle.boss
        if self.on(2) and boss.alive:
            healed = battle.heal(boss, boss.max_hp * 0.15)
            boss.mods["atk"] = boss.mods.get("atk", 0) + 10
            battle.note(f"{boss.label}露出得逞的坏笑，回复 {healed} 生命，攻击 +10%")


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
            battle.note(f"{boss.label}被 3 条锁链束缚着：欲戴王冠，必承其重")

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
            battle.note(f"锁链崩断一条（剩 {self.chains} 条），冲击波扫过全队：" + "，".join(hits))
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
        battle.note(f"挣断锁链，王者归来！{boss.label}恢复全部属性，回复 {healed} 生命")

    def round_start(self, battle):
        heroes = battle.alive_heroes()
        if self.on(2) and battle.round % 4 == 0 and heroes:
            target = min(heroes, key=lambda h: h.stat("spd"))
            target.stun = max(target.stun, 1)
            target.stun_label = "臣服"
            battle.note(f"王者威压：{target.label} 被震慑得跪了下来")


class PigGod(BossMechanics):
    pig_id = "pig_god"
    MECHANICS = (
        ("好运加持", "暴击率 +25；每次被暴击有 30% 概率靠好运抵消"),
        ("神谕", "每 3 回合看穿全队并预告下回合的目标，下一回合全队闪避归零"),
        ("守护神降临", "生命第一次低于 50% 时召唤 2 只小猪守护灵，守护灵在场时必须先打守护灵"),
    )
    HP = 0.5

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
            battle.note(f"好运加持：{battle.boss.label}靠运气抵消了一次暴击")
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
                battle.note(f"神谕：下回合全队无处可躲，{self.prophecy.label} 将首当其冲")

    def pick_target(self, battle, candidates):
        if (
            self.prophecy is not None
            and self.prophecy.alive
            and battle.round == self.blind_round
            and self.prophecy in candidates
        ):
            return self.prophecy
        return battle.rng.choice(candidates)

    def damaged(self, battle):
        if self.on(2) and not self.summoned and battle.boss.ratio < 0.5:
            self.summoned = True
            for index in (1, 2):
                battle.summon(f"小猪守护灵{index}", 0.15, 0.5)
            battle.note("守护神降临！两只小猪守护灵挡在了神明面前，得先打倒它们")


class Zombie(BossMechanics):
    pig_id = "zombie-pig"
    MECHANICS = (
        (
            "尸毒",
            "被它咬中的猪叠 1 层尸毒，每层每回合损失 3% 生命，最多 3 层；中毒期间受到的治疗减半",
        ),
        ("一瘸一拐", "总是最后一个行动，但每次行动后有 30% 概率再扑咬一次"),
        ("尸变", "它亲手击倒的队员猪会变成僵尸小猪，站到敌方一起作战（生命为原来的 40%）"),
    )
    HP = 1.05
    GUARDS_FIRST = False

    @staticmethod
    def poison(unit):
        return next((dot for dot in unit.dots if dot["label"] == "尸毒"), None)

    def poisoned(self, unit) -> int:
        dot = self.poison(unit)
        return dot["pct"] // 3 if dot else 0

    def outgoing(self, battle, target, dealt):
        if not self.on(0) or not target.alive:
            return
        dot = self.poison(target)
        if dot is None:
            target.dots.append({"pct": 3, "turns": 99, "label": "尸毒"})
        elif dot["pct"] < 9:
            dot["pct"] += 3
            if dot["pct"] == 9:
                battle.note(f"{target.label} 身上的尸毒叠满了 3 层")

    def heal_factor(self, battle, unit):
        return 0.5 if unit.side == 0 and self.poisoned(unit) else 1.0

    def order(self, battle, units):
        if not self.on(1):
            return units
        return [u for u in units if u is not battle.boss] + [u for u in units if u is battle.boss]

    def extra_actions(self, battle):
        return 1 if self.on(1) and battle.rng.random() < 0.3 else 0

    def hero_fell(self, battle, hero):
        if self.on(2) and battle.acting is battle.boss:
            battle.summon_from(hero, f"僵尸·{hero.data['name']}", 0.4)
            battle.note(f"{hero.label} 被啃了一口……尸变了！变成僵尸小猪站到了敌方")


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
    HP = 0.7

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
            battle.note(f"{battle.boss.label}翻到了{self.face}面：{hint}")
        heroes = battle.present_heroes()
        if self.on(1) and battle.round % 4 == 0 and len(heroes) >= 2:
            target = battle.rng.choice(heroes)
            target.possessed = True
            battle.note(f"{battle.boss.label}附身到了 {target.label} 身上")

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
            ends = {max(heroes, key=lambda h: h.hp), min(heroes, key=lambda h: h.hp)}
            hits = [f"{h.label} -{round(battle.strike(boss, h, 0.8))}" for h in ends]
            battle.note("双面夹击！" + "，".join(hits))


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
    HP = 0.55
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
        battle.note(
            f"白骨重组！散落一地的骨头又拼回了 {boss.label}，生命回到 {boss.ratio:.0%}，防御 +20%"
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
            battle.note("红烧！全队被浇了一身热油，持续流血 3 回合")
        elif dish == "爆炒":
            hits = [f"{h.label} -{round(battle.strike(battle.boss, h, 0.6))}" for h in heroes]
            battle.note("爆炒！" + "，".join(hits))
        else:
            for hero in heroes:
                hero.buffs.append({"stat": "spd", "pct": -20, "turns": 2})
            battle.note("清蒸！全队被蒸得晕乎乎的，速度 -20% 2 回合")


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
    HP = 0.95

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
                battle.note("风火轮留下的火圈烧了起来，全队灼烧 4% 生命")
            if battle.round % 2 == 0:
                self.ring = True
                battle.note(f"{boss.label}踩着风火轮绕场一圈，地上留下了火圈")
        if self.on(1) and battle.round % 3 == 0:
            shield = evade = 0
            for hero in battle.present_heroes():
                shield += hero.shield
                evade += hero.evade
                hero.shield, hero.evade = 0, 0
            boss.shield += shield
            boss.evade += evade
            if shield or evade:
                battle.note(f"混天绫一卷，抢走了全队 {round(shield)} 护盾和 {evade} 次闪避")
            else:
                battle.note("混天绫扫了一圈，全队身上什么也没有")

    def crit_against(self, battle, attacker):
        if self.on(2) and self.stacks < 5:
            self.stacks += 1
            battle.boss.mods["atk"] = battle.boss.mods.get("atk", 0) + 8
            battle.note(f"{battle.boss.label}不服输！攻击 +8%（{self.stacks}/5）")
        return True

    def damaged(self, battle):
        boss = battle.boss
        if self.on(2) and not self.thrown and boss.ratio < 0.25:
            self.thrown = True
            hits = [
                f"{h.label} -{round(battle.strike(boss, h, 2.0))}" for h in battle.present_heroes()
            ]
            battle.note("乾坤圈全力一掷！" + "，".join(hits))


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
    HP = 0.6

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
            battle.note(text)
        if self.on(1) and battle.round % 4 == 0:
            self.decree = battle.rng.choice(("禁疗令", "禁技令"))
            self.until = battle.round + 1
            rule = "治疗、护盾和持续回复全部失效" if self.decree == "禁疗令" else "队员只能用普攻"
            battle.note(f"公爵颁布{self.decree}：2 回合内{rule}")

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
            battle.note(f"{battle.boss.label}的龙鳞全部脱落了")
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
    HP = 0.85

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.abyss = False

    def round_start(self, battle):
        if self.on(0):
            pct = 1 + battle.round / 4
            for hero in battle.present_heroes():
                battle.hurt(hero, hero.max_hp * pct / 100)
            if battle.round % 4 == 1:
                battle.note(f"越潜越深，水压让全队每回合损失 {pct:.1f}% 生命")
        heroes = battle.present_heroes()
        if self.on(1) and battle.round % 3 == 0 and heroes:
            target = max(heroes, key=lambda h: h.stat("atk"))
            target.lured = True
            battle.note(f"灯笼鱼的光吸引了 {target.label}：它的下一击必定暴击，但也会成为猎物")

    def damaged(self, battle):
        if self.on(2) and not self.abyss and battle.boss.ratio < 0.5:
            self.abyss = True
            battle.note("马里亚纳海沟的暗流涌了上来：全队治疗减半，护盾每回合流失一半")

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
    HP = 1.35
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
            battle.note(f"{hero.label} 被放了回来，被做了实验：{self.STATS[key]}{sign}{value}%")
        if self.docked:
            if boss.away:
                hits = [
                    f"{h.label} -{round(battle.strike(boss, h, 0.6))}"
                    for h in battle.present_heroes()
                ]
                battle.note("母舰扫射！" + "，".join(hits))
            else:
                self.docked = False
                healed = battle.heal(boss, boss.max_hp * 0.15)
                battle.note(f"{boss.label}从母舰回来了，回复 {healed} 生命")
        heroes = battle.present_heroes()
        if self.on(0) and battle.round % 4 == 0 and len(heroes) >= 2:
            target = battle.rng.choice(heroes)
            target.away = 2
            self.abducted.append(target)
            battle.note(f"绑架光束！{target.label} 被吸进了 UFO")
        if self.on(1) and battle.round % 3 == 0:
            self.doubt = battle.round
            battle.note("自我怀疑光波：全队开始怀疑猪生，这回合伤害和治疗 -40%")

    def outgoing_factor(self, battle, attacker, target):
        return 0.6 if attacker.side == 0 and battle.round == self.doubt else 1.0

    def heal_factor(self, battle, unit):
        return 0.6 if unit.side == 0 and battle.round == self.doubt else 1.0

    def damaged(self, battle):
        boss = battle.boss
        if self.on(2) and not self.called and boss.ratio < 0.5:
            self.called = self.docked = True
            boss.away = 2
            battle.note(f"{boss.label}召回了母舰，躲进去不出来了")


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
    HP = 0.5
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
            battle.note(f"{battle.boss.label}分出了两颗小星，围着星核打转")

    def round_start(self, battle):
        boss = battle.boss
        if not self.on(0) or battle.round % 5:
            return
        missing = 2 - sum(1 for star in self.stars if star.alive)
        cost = boss.max_hp * 0.1
        rebuilt = 0
        for _ in range(missing):
            if boss.hp <= cost * 1.5:
                break
            boss.hp -= cost
            self.spawn(battle)
            rebuilt += 1
        if rebuilt:
            battle.note(f"星团聚合：星核分出 {rebuilt} 颗新的小星")

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
            battle.note("星核开始坍缩！超新星倒计时 3 回合")

    def round_end(self, battle):
        if not self.countdown:
            return
        self.countdown -= 1
        if self.countdown:
            battle.note(f"超新星倒计时 {self.countdown}……")
            return
        hits = [
            f"{h.label} -{round(battle.hurt(h, h.max_hp * 0.4))}" for h in battle.present_heroes()
        ]
        battle.note("超新星爆发！" + "，".join(hits))


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
    HP = 0.8

    def __init__(self, disabled=None):
        super().__init__(disabled)
        self.rings = 3 if self.on(0) else 0
        self.near = self.far = None

    def setup(self, battle):
        if self.rings:
            battle.note(f"{battle.boss.label}身边环绕着 3 道星环")

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
            battle.note(f"星环重新转了起来，现在有 {self.rings} 道")
        heroes = battle.present_heroes()
        self.near = self.far = None
        if self.on(1) and len(heroes) >= 2:
            self.near = max(heroes, key=lambda h: h.stat("spd"))
            self.far = min(heroes, key=lambda h: h.stat("spd"))
            if battle.round % 3 == 1:
                battle.note(
                    f"引力场：{self.near.label} 被拉近（受到伤害 +25%），"
                    f"{self.far.label} 被推远（造成伤害 -25%）"
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
            battle.note("陨石雨！砸中了 " + "，".join(parts))

    def outgoing_factor(self, battle, attacker, target):
        if attacker is self.far:
            return 0.75
        if target is self.near and attacker.side == 1:
            return 1.25
        return 1.0


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
    )
}
