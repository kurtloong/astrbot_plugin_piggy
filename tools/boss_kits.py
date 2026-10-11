"""Standalone raid-boss skill kits (not ordinary-pig templates).

Each kit keeps the boss's identity / raid mechanics theme, and must include:
- a tank-buster (high single-target damage; boss AI already prefers tanks)
- an AOE (`cleave` splash onto other heroes)
"""

from __future__ import annotations

# pig_id -> battle entry
BOSS_KITS: dict[str, dict] = {
    "goblin-pig": {
        "role": "输出",
        "style": "角落偷袭",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "邋遢拱",
                "text": "从角落甩出泥蹄，蹭脏坦克的护甲。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.0
                    },
                    {
                        "type": "weaken",
                        "pct": 12,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "角落背刺",
                "text": "专盯坦克后背猛凿，无视闪避。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True,
                        "pierce": 20
                    },
                    {
                        "type": "expose",
                        "pct": 15,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "泥点飞溅",
                "text": "甩起一地脏水，溅到全场。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "dot",
                        "pct": 4,
                        "turns": 2,
                        "label": "熏味"
                    }
                ]
            },
            {
                "name": "摆烂翻身",
                "text": "躺平吸一口脏气，再弹起来躲一下。",
                "cd": 4,
                "when": "low_hp",
                "effects": [
                    {
                        "type": "heal",
                        "pct": 18
                    },
                    {
                        "type": "evade",
                        "count": 1
                    },
                    {
                        "type": "mitigate",
                        "pct": 20,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "角落体质",
                "text": "习惯躲角落，闪避更高。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "dodge",
                        "pct": 12
                    },
                    {
                        "type": "buff",
                        "stat": "spd",
                        "pct": 8
                    }
                ]
            }
        ]
    },
    "frozen-pig": {
        "role": "输出",
        "style": "冰封镇压",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "冰碴撞击",
                "text": "结冰的猪鼻撞上前排。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "root",
                        "turns": 1,
                        "chance": 35
                    }
                ]
            },
            {
                "name": "极寒砸盾",
                "text": "把坦克冻成脆壳再砸碎。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "pierce": 20,
                        "sure": True
                    },
                    {
                        "type": "stun",
                        "turns": 1,
                        "label": "冻结"
                    }
                ]
            },
            {
                "name": "暴风雪",
                "text": "圈内刮起暴风雪，全队挨冻。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.27,
                        "cleave": 35
                    },
                    {
                        "type": "dot",
                        "pct": 4,
                        "turns": 3,
                        "label": "冻伤"
                    },
                    {
                        "type": "weaken",
                        "pct": 10,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "冰壳蓄力",
                "text": "裹上冰壳蓄力，打断则碎裂。",
                "cd": 4,
                "effects": [
                    {
                        "type": "charge",
                        "turns": 1,
                        "power": 2.2,
                        "interrupt": True
                    },
                    {
                        "type": "mitigate",
                        "pct": 25,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "心如寒冰",
                "text": "情绪冻结，减伤并免疫眩晕。",
                "passive": True,
                "effects": [
                    {
                        "type": "mitigate",
                        "pct": 12,
                        "turns": 99
                    },
                    {
                        "type": "immunity",
                        "kind": "stun",
                        "turns": 99
                    }
                ]
            }
        ]
    },
    "everest-pig": {
        "role": "坦克",
        "style": "雪崩俯压",
        "stats": {
            "hp": 145,
            "atk": 13,
            "def": 18,
            "spd": 6,
            "crit": 4,
            "dodge": 2
        },
        "skills": [
            {
                "name": "雪峰一踏",
                "text": "从山顶踏下，砸中坦克。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    }
                ]
            },
            {
                "name": "不可逾越",
                "text": "绝对高度落下，专砸坦克胸口。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "pierce": 20
                    },
                    {
                        "type": "expose",
                        "pct": 20,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "雪崩倾泻",
                "text": "整面山坡崩塌，全队埋雪。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.36,
                        "cleave": 35
                    },
                    {
                        "type": "root",
                        "turns": 1
                    }
                ]
            },
            {
                "name": "极顶铁壁",
                "text": "把自己冻成不可攀登的峭壁。",
                "cd": 3,
                "effects": [
                    {
                        "type": "taunt",
                        "turns": 2
                    },
                    {
                        "type": "fortify",
                        "pct": 22
                    },
                    {
                        "type": "mitigate",
                        "pct": 22,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "绝对巅峰",
                "text": "濒死仍站在峰顶。",
                "passive": True,
                "effects": [
                    {
                        "type": "last_stand",
                        "threshold": 18,
                        "pct": 25,
                        "turns": 1
                    },
                    {
                        "type": "buff",
                        "stat": "def",
                        "pct": 8
                    }
                ]
            }
        ]
    },
    "error-404-pig": {
        "role": "输出",
        "style": "页面崩溃",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "错误拱页",
                "text": "返回坏掉的页面，撞乱坦克。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "silence",
                        "turns": 1,
                        "chance": 25
                    }
                ]
            },
            {
                "name": "空指针崩",
                "text": "对坦克抛出空引用，系统直接宕机。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True
                    },
                    {
                        "type": "silence",
                        "turns": 1
                    }
                ]
            },
            {
                "name": "全站刷新",
                "text": "强制全队刷新，人人掉帧。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "purge",
                        "mode": "buffs"
                    }
                ]
            },
            {
                "name": "观测坍缩",
                "text": "打开随机错误码，结局未知。",
                "cd": 3,
                "effects": [
                    {
                        "type": "pick",
                        "options": [
                            {
                                "text": "500",
                                "effects": [
                                    {
                                        "type": "damage",
                                        "power": 1.7
                                    },
                                    {
                                        "type": "stun",
                                        "turns": 1
                                    }
                                ]
                            },
                            {
                                "text": "403",
                                "effects": [
                                    {
                                        "type": "silence",
                                        "turns": 2
                                    },
                                    {
                                        "type": "weaken",
                                        "pct": 20,
                                        "turns": 2
                                    }
                                ]
                            },
                            {
                                "text": "204",
                                "effects": [
                                    {
                                        "type": "evade",
                                        "count": 2
                                    },
                                    {
                                        "type": "heal",
                                        "pct": 12
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "name": "不可达域",
                "text": "地址永远解析失败，偶尔躲开。",
                "passive": True,
                "effects": [
                    {
                        "type": "evade",
                        "count": 1
                    },
                    {
                        "type": "buff",
                        "stat": "dodge",
                        "pct": 10
                    }
                ]
            }
        ]
    },
    "mechanical-pig": {
        "role": "输出",
        "style": "过载射控",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "校准射击",
                "text": "光眼锁定坦克，射出一发。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "mark",
                        "pct": 8,
                        "turns": 3
                    }
                ]
            },
            {
                "name": "破甲穿甲弹",
                "text": "对坦克发射穿甲弹，护甲形同虚设。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "pierce": 20,
                        "sure": True
                    },
                    {
                        "type": "expose",
                        "pct": 18,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "扇形扫射",
                "text": "炮口横扫，整队吃弹。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "hits": 2,
                        "cleave": 35
                    },
                    {
                        "type": "combo",
                        "gain": 1
                    }
                ]
            },
            {
                "name": "过载核心",
                "text": "核心过热，伤害暴涨但自损。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.04,
                        "combo_bonus": 15
                    },
                    {
                        "type": "recoil",
                        "pct": 8
                    },
                    {
                        "type": "buff",
                        "stat": "atk",
                        "pct": 20,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "人机演算",
                "text": "弹道演算常驻，暴击更高。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "crit",
                        "pct": 12
                    },
                    {
                        "type": "buff",
                        "stat": "atk",
                        "pct": 8
                    }
                ]
            }
        ]
    },
    "cyberpunk-pig": {
        "role": "输出",
        "style": "义体劫持",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "义体刮擦",
                "text": "金属爪刮过坦克护甲。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "dot",
                        "pct": 3,
                        "turns": 2,
                        "label": "漏电"
                    }
                ]
            },
            {
                "name": "黑墙处决",
                "text": "对坦克植入处决协议。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "execute": 35,
                        "sure": True
                    },
                    {
                        "type": "silence",
                        "turns": 1
                    }
                ]
            },
            {
                "name": "夜城脉冲",
                "text": "电磁脉冲扫过整条街。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.27,
                        "cleave": 35
                    },
                    {
                        "type": "silence",
                        "turns": 1,
                        "chance": 40
                    },
                    {
                        "type": "corrupting",
                        "pct": 20,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "回档重开",
                "text": "把生命读档，再偷一刀。",
                "cd": 4,
                "when": "hurt",
                "effects": [
                    {
                        "type": "rewind",
                        "turns": 2
                    },
                    {
                        "type": "damage",
                        "power": 1.36
                    }
                ]
            },
            {
                "name": "2077固件",
                "text": "义体固件常驻加速与暴击。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "spd",
                        "pct": 12
                    },
                    {
                        "type": "buff",
                        "stat": "crit",
                        "pct": 10
                    }
                ]
            }
        ]
    },
    "demon-pig": {
        "role": "输出",
        "style": "坏笑诅咒",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "小角戳",
                "text": "坏笑着戳一下坦克。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "mark",
                        "pct": 10,
                        "turns": 3
                    }
                ]
            },
            {
                "name": "契约撕咬",
                "text": "逼坦克签血契，伤害巨大。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "drain": 25,
                        "sure": True
                    },
                    {
                        "type": "corrupting",
                        "pct": 25,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "蝠翼风暴",
                "text": "振翅掀起硫磺风，扫过全队。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "weaken",
                        "pct": 12,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "坏点子袋",
                "text": "从口袋掏出随机恶作剧。",
                "cd": 3,
                "effects": [
                    {
                        "type": "pick",
                        "options": [
                            {
                                "text": "诅咒",
                                "effects": [
                                    {
                                        "type": "dot",
                                        "pct": 4,
                                        "turns": 3,
                                        "label": "诅咒"
                                    },
                                    {
                                        "type": "expose",
                                        "pct": 15,
                                        "turns": 2
                                    }
                                ]
                            },
                            {
                                "text": "魅惑",
                                "effects": [
                                    {
                                        "type": "stun",
                                        "turns": 1,
                                        "label": "迷惑"
                                    },
                                    {
                                        "type": "transfer",
                                        "mode": "buff"
                                    }
                                ]
                            },
                            {
                                "text": "爆笑",
                                "effects": [
                                    {
                                        "type": "damage",
                                        "power": 1.78,
                                        "cleave": 35
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "name": "恶魔契约",
                "text": "常驻攻击光环，越打越欢。",
                "passive": True,
                "effects": [
                    {
                        "type": "aura",
                        "stat": "atk",
                        "pct": 10
                    }
                ]
            }
        ]
    },
    "chained_crown_pig": {
        "role": "坦克",
        "style": "枷锁王权",
        "stats": {
            "hp": 155,
            "atk": 14,
            "def": 22,
            "spd": 7,
            "crit": 4,
            "dodge": 3
        },
        "skills": [
            {
                "name": "镣铐顶撞",
                "text": "铁链甩在坦克身上。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "root",
                        "turns": 1,
                        "chance": 30
                    }
                ]
            },
            {
                "name": "王冠重压",
                "text": "把王冠砸向坦克，压迫窒息。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True,
                        "pierce": 20
                    },
                    {
                        "type": "stun",
                        "turns": 1,
                        "label": "跪拜"
                    }
                ]
            },
            {
                "name": "锁链崩断",
                "text": "锁链四处崩飞，全场中伤。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.32,
                        "cleave": 35
                    },
                    {
                        "type": "thorns",
                        "pct": 25,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "王权壁垒",
                "text": "披上枷锁铁壁，嘲讽承伤。",
                "cd": 3,
                "effects": [
                    {
                        "type": "taunt",
                        "turns": 2
                    },
                    {
                        "type": "absorb",
                        "pct": 20,
                        "break_power": 1.2
                    },
                    {
                        "type": "fortify",
                        "pct": 20
                    }
                ]
            },
            {
                "name": "囚笼不屈",
                "text": "锁链越紧越不肯倒。",
                "passive": True,
                "effects": [
                    {
                        "type": "last_stand",
                        "threshold": 20,
                        "pct": 30,
                        "turns": 2
                    },
                    {
                        "type": "buff",
                        "stat": "def",
                        "pct": 8
                    }
                ]
            }
        ]
    },
    "pig_god": {
        "role": "输出",
        "style": "神谕抽签",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "神蹄轻点",
                "text": "轻轻一点，坦克就要倒霉。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "mark",
                        "pct": 12,
                        "turns": 3
                    }
                ]
            },
            {
                "name": "天罚降世",
                "text": "神罚专砸坦克额头。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True
                    },
                    {
                        "type": "expose",
                        "pct": 20,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "祥云扫荡",
                "text": "祥云铺开，全队沐浴「神恩」。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.27,
                        "cleave": 35
                    },
                    {
                        "type": "dot",
                        "pct": 4,
                        "turns": 2,
                        "label": "神罚"
                    }
                ]
            },
            {
                "name": "运势抽签",
                "text": "抽出一支签，好运坏运未知。",
                "cd": 3,
                "effects": [
                    {
                        "type": "pick",
                        "options": [
                            {
                                "weight": 2,
                                "text": "上上签",
                                "effects": [
                                    {
                                        "type": "heal",
                                        "pct": 20
                                    },
                                    {
                                        "type": "buff",
                                        "stat": "atk",
                                        "pct": 25,
                                        "turns": 2
                                    }
                                ]
                            },
                            {
                                "weight": 3,
                                "text": "下下签",
                                "effects": [
                                    {
                                        "type": "damage",
                                        "power": 1.87
                                    },
                                    {
                                        "type": "stun",
                                        "turns": 1,
                                        "label": "天谴"
                                    }
                                ]
                            },
                            {
                                "weight": 2,
                                "text": "平签",
                                "effects": [
                                    {
                                        "type": "cleanse"
                                    },
                                    {
                                        "type": "energy",
                                        "gain": 20
                                    },
                                    {
                                        "type": "damage",
                                        "power": 1.05
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "name": "天命眷顾",
                "text": "偶尔躲开，队友也沾光。",
                "passive": True,
                "effects": [
                    {
                        "type": "evade",
                        "count": 1
                    },
                    {
                        "type": "aura",
                        "stat": "crit",
                        "pct": 8
                    }
                ]
            }
        ]
    },
    "zombie-pig": {
        "role": "输出",
        "style": "尸毒饥渴",
        "stats": {
            "hp": 110,
            "atk": 17,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "腐爪撕",
                "text": "腐爪抠进坦克皮肉。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "dot",
                        "pct": 3,
                        "turns": 3,
                        "label": "尸毒",
                        "stacks": 1
                    }
                ]
            },
            {
                "name": "啃脑终结",
                "text": "扑向坦克头颅猛啃。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "drain": 15,
                        "sure": True
                    },
                    {
                        "type": "bleed_burst",
                        "stacks": 3
                    }
                ]
            },
            {
                "name": "尸毒蔓延",
                "text": "毒雾扩散，全队中毒。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "dot",
                        "pct": 3,
                        "turns": 3,
                        "label": "尸毒",
                        "spread": True
                    },
                    {
                        "type": "corrupting",
                        "pct": 15,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "脑髓饥渴",
                "text": "吸一口血，毒叠得更厚。",
                "cd": 2,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.44,
                        "drain": 15
                    },
                    {
                        "type": "dot",
                        "pct": 3,
                        "turns": 3,
                        "label": "尸毒",
                        "stacks": 2,
                        "max_stacks": 6
                    }
                ]
            },
            {
                "name": "掉渣再生",
                "text": "肉掉了还能长回来。",
                "passive": True,
                "effects": [
                    {
                        "type": "regen",
                        "pct": 4,
                        "turns": 99
                    }
                ]
            }
        ]
    },
    "pighub0876": {
        "role": "输出",
        "style": "热搜双面",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "出场拱",
                "text": "热搜登场，先拱坦克一记。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "expose",
                        "pct": 8,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "不好惹宣言",
                "text": "对坦克放狠话，真的下手很重。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True,
                        "pierce": 20
                    },
                    {
                        "type": "silence",
                        "turns": 1
                    }
                ]
            },
            {
                "name": "全网刷屏",
                "text": "话题刷屏，全队被热搜淹没。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "weaken",
                        "pct": 12,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "双面切换",
                "text": "笑面与怒面随机切换。",
                "cd": 3,
                "effects": [
                    {
                        "type": "pick",
                        "options": [
                            {
                                "text": "笑面",
                                "effects": [
                                    {
                                        "type": "leech",
                                        "pct": 30
                                    },
                                    {
                                        "type": "damage",
                                        "power": 1.53
                                    },
                                    {
                                        "type": "evade",
                                        "count": 1
                                    }
                                ]
                            },
                            {
                                "text": "怒面",
                                "effects": [
                                    {
                                        "type": "damage",
                                        "power": 1.95
                                    },
                                    {
                                        "type": "buff",
                                        "stat": "atk",
                                        "pct": 20,
                                        "turns": 2
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "name": "热搜体质",
                "text": "永远在热搜，速度与暴击常驻。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "spd",
                        "pct": 10
                    },
                    {
                        "type": "buff",
                        "stat": "crit",
                        "pct": 10
                    }
                ]
            }
        ]
    },
    "skeleton-pig": {
        "role": "坦克",
        "style": "白骨不灭",
        "stats": {
            "hp": 155,
            "atk": 14,
            "def": 22,
            "spd": 7,
            "crit": 4,
            "dodge": 3
        },
        "skills": [
            {
                "name": "骨刺戳",
                "text": "细骨刺扎进坦克。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "mark",
                        "pct": 8,
                        "turns": 3
                    }
                ]
            },
            {
                "name": "骨矛贯胸",
                "text": "整根骨矛贯穿坦克。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "pierce": 20,
                        "sure": True
                    },
                    {
                        "type": "expose",
                        "pct": 15,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "白骨雨",
                "text": "骨头从天而降，砸中所有人。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "dot",
                        "pct": 3,
                        "turns": 2,
                        "label": "骨刺"
                    }
                ]
            },
            {
                "name": "瘦成盾牌",
                "text": "骨架张开，替队友挡刀。",
                "cd": 3,
                "effects": [
                    {
                        "type": "redirect",
                        "turns": 2
                    },
                    {
                        "type": "fortify",
                        "pct": 20
                    },
                    {
                        "type": "block",
                        "count": 1,
                        "pct": 22
                    }
                ]
            },
            {
                "name": "不死执念",
                "text": "倒下一次还能爬起来。",
                "passive": True,
                "effects": [
                    {
                        "type": "revive",
                        "pct": 25
                    }
                ]
            }
        ]
    },
    "pighub0233": {
        "role": "输出",
        "style": "偶像包袱",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "构图一拱",
                "text": "摆好构图，精准拱坦克。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "combo",
                        "gain": 1
                    }
                ]
            },
            {
                "name": "包袱砸人",
                "text": "把所有包袱砸向坦克。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True,
                        "combo_bonus": 12
                    },
                    {
                        "type": "stun",
                        "turns": 1,
                        "label": "笑场"
                    }
                ]
            },
            {
                "name": "打滚镜头",
                "text": "镜头扫过全场，人人入镜挨揍。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "hits": 3,
                        "cleave": 35
                    },
                    {
                        "type": "combo",
                        "gain": 1
                    }
                ]
            },
            {
                "name": "形象管理",
                "text": "先化妆再出手，伤害与姿态齐升。",
                "cd": 3,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "atk",
                        "pct": 20,
                        "turns": 2
                    },
                    {
                        "type": "buff",
                        "stat": "crit",
                        "pct": 15,
                        "turns": 2
                    },
                    {
                        "type": "damage",
                        "power": 1.36
                    }
                ]
            },
            {
                "name": "偶像光泽",
                "text": "镜头滤镜常开，暴击与闪避更好看。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "crit",
                        "pct": 10
                    },
                    {
                        "type": "buff",
                        "stat": "dodge",
                        "pct": 8
                    }
                ]
            }
        ]
    },
    "pighub0007": {
        "role": "坦克",
        "style": "档案催缴",
        "stats": {
            "hp": 155,
            "atk": 14,
            "def": 22,
            "spd": 7,
            "crit": 4,
            "dodge": 3
        },
        "skills": [
            {
                "name": "印戳一敲",
                "text": "印章敲在坦克档案上。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "mark",
                        "pct": 10,
                        "turns": 3
                    }
                ]
            },
            {
                "name": "逾期催缴",
                "text": "逾期账单一次性结清，坦克爆表。",
                "cd": 3,
                "effects": [
                    {
                        "type": "detonate"
                    },
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True,
                        "pierce": 20
                    }
                ]
            },
            {
                "name": "批量归档",
                "text": "整队档案一起盖章。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "mark",
                        "pct": 12,
                        "turns": 3,
                        "stacks": 1
                    }
                ]
            },
            {
                "name": "入库登记",
                "text": "强制登记，嘲讽并标记。",
                "cd": 3,
                "effects": [
                    {
                        "type": "taunt",
                        "turns": 2
                    },
                    {
                        "type": "mark",
                        "pct": 15,
                        "turns": 3
                    },
                    {
                        "type": "redirect",
                        "turns": 2
                    }
                ]
            },
            {
                "name": "公爵备案",
                "text": "档案永不注销，防御与嘲讽常驻。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "def",
                        "pct": 8
                    },
                    {
                        "type": "redirect",
                        "turns": 1
                    }
                ]
            }
        ]
    },
    "pighub0830": {
        "role": "坦克",
        "style": "深渊承压",
        "stats": {
            "hp": 145,
            "atk": 13,
            "def": 18,
            "spd": 6,
            "crit": 4,
            "dodge": 2
        },
        "skills": [
            {
                "name": "深压拱",
                "text": "水压把坦克按进槽底。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    }
                ]
            },
            {
                "name": "槽底压碎",
                "text": "最深处的压力专砸坦克。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5
                    },
                    {
                        "type": "stun",
                        "turns": 1,
                        "label": "水压"
                    }
                ]
            },
            {
                "name": "暗流冲击",
                "text": "暗流席卷全场。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.27,
                        "cleave": 35
                    },
                    {
                        "type": "weaken",
                        "pct": 10,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "深海承伤",
                "text": "把火力全引到自己身上。",
                "cd": 3,
                "effects": [
                    {
                        "type": "taunt",
                        "turns": 3
                    },
                    {
                        "type": "mitigate",
                        "pct": 22,
                        "turns": 2
                    },
                    {
                        "type": "guard",
                        "pct": 40,
                        "turns": 2,
                        "target": "ally_lowest"
                    }
                ]
            },
            {
                "name": "深渊脂肪",
                "text": "厚脂肪抗压，持续回血。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "def",
                        "pct": 8
                    },
                    {
                        "type": "regen",
                        "pct": 3,
                        "turns": 99
                    }
                ]
            }
        ]
    },
    "alien-pig": {
        "role": "输出",
        "style": "绿雾侵蚀",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "蓝雾拱",
                "text": "喷出蓝雾，腐蚀坦克。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "dot",
                        "pct": 4,
                        "turns": 3,
                        "label": "辐射"
                    }
                ]
            },
            {
                "name": "雾核崩解",
                "text": "在坦克体内引爆雾核。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True
                    },
                    {
                        "type": "detonate"
                    },
                    {
                        "type": "corrupting",
                        "pct": 25,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "绿雾蒸腾",
                "text": "雾气铺满战场。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "dot",
                        "pct": 4,
                        "turns": 3,
                        "label": "辐射",
                        "spread": True
                    },
                    {
                        "type": "dispel"
                    }
                ]
            },
            {
                "name": "辐射叠印",
                "text": "叠满辐射印记再结算。",
                "cd": 2,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.27
                    },
                    {
                        "type": "mark",
                        "pct": 12,
                        "turns": 3,
                        "stacks": 2,
                        "max_stacks": 6,
                        "burst_power": 1.8
                    },
                    {
                        "type": "corrupting",
                        "pct": 15,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "异星雾质",
                "text": "身体由雾构成，攻击与毒素常驻。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "atk",
                        "pct": 10
                    },
                    {
                        "type": "buff",
                        "stat": "spd",
                        "pct": 8
                    }
                ]
            }
        ]
    },
    "pighub0872": {
        "role": "输出",
        "style": "护槽狂炫",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "拱槽",
                "text": "拱向食槽边的坦克。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "combo",
                        "gain": 1
                    }
                ]
            },
            {
                "name": "槽空暴起",
                "text": "食槽空了就暴起砸坦克。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True,
                        "execute": 40
                    },
                    {
                        "type": "buff",
                        "stat": "atk",
                        "pct": 15,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "抢槽横扫",
                "text": "为了食槽横扫一切挡路的猪。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35,
                        "drain": 15
                    },
                    {
                        "type": "silence",
                        "turns": 1,
                        "chance": 35
                    }
                ]
            },
            {
                "name": "护槽狂炫",
                "text": "狂炫一口，连击吸血。",
                "cd": 2,
                "effects": [
                    {
                        "type": "combo",
                        "gain": 2
                    },
                    {
                        "type": "damage",
                        "power": 1.53,
                        "combo_bonus": 10,
                        "drain": 20
                    },
                    {
                        "type": "leech",
                        "pct": 25
                    }
                ]
            },
            {
                "name": "槽边胃袋",
                "text": "永远饿着，攻击与吸血体质。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "atk",
                        "pct": 12
                    },
                    {
                        "type": "buff",
                        "stat": "spd",
                        "pct": 8
                    }
                ]
            }
        ]
    },
    "pighub0336": {
        "role": "坦克",
        "style": "热搜置顶",
        "stats": {
            "hp": 155,
            "atk": 14,
            "def": 22,
            "spd": 7,
            "crit": 4,
            "dodge": 3
        },
        "skills": [
            {
                "name": "星环蹭",
                "text": "蹭热度先蹭坦克一下。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "mark",
                        "pct": 10,
                        "turns": 3
                    }
                ]
            },
            {
                "name": "热搜爆炸",
                "text": "话题引爆，坦克最先中招。",
                "cd": 3,
                "effects": [
                    {
                        "type": "detonate"
                    },
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True
                    },
                    {
                        "type": "expose",
                        "pct": 18,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "全员上热搜",
                "text": "所有人都被挂上热搜。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "mark",
                        "pct": 10,
                        "turns": 3
                    }
                ]
            },
            {
                "name": "热搜置顶",
                "text": "把自己置顶，强迫大家打自己。",
                "cd": 3,
                "effects": [
                    {
                        "type": "taunt",
                        "turns": 2
                    },
                    {
                        "type": "mark",
                        "pct": 15,
                        "turns": 3
                    },
                    {
                        "type": "expose",
                        "pct": 12,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "常客厚皮",
                "text": "上热搜惯了，皮厚减伤。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "def",
                        "pct": 8
                    },
                    {
                        "type": "mitigate",
                        "pct": 8,
                        "turns": 99
                    }
                ]
            }
        ]
    },
    "pighub0315": {
        "role": "治疗",
        "style": "窑火反噬",
        "stats": {
            "hp": 135,
            "atk": 12,
            "def": 13,
            "spd": 11,
            "crit": 5,
            "dodge": 7
        },
        "skills": [
            {
                "name": "火星弹",
                "text": "弹一颗火星烫坦克。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.0
                    },
                    {
                        "type": "dot",
                        "pct": 3,
                        "turns": 2,
                        "label": "灼伤"
                    }
                ]
            },
            {
                "name": "围炉反噬",
                "text": "把围炉的热量全砸向坦克。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "sure": True
                    },
                    {
                        "type": "dot",
                        "pct": 4,
                        "turns": 3,
                        "label": "窑火"
                    }
                ]
            },
            {
                "name": "窑火喷涌",
                "text": "窑口喷火，全队挨烫。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "cleave": 35
                    },
                    {
                        "type": "dot",
                        "pct": 4,
                        "turns": 2,
                        "label": "灼伤",
                        "spread": True
                    }
                ]
            },
            {
                "name": "八猪围炉",
                "text": "先给自己续火，再继续烤人。",
                "cd": 3,
                "effects": [
                    {
                        "type": "life_link",
                        "pct": 18,
                        "self_pct": 10,
                        "target": "ally_all"
                    },
                    {
                        "type": "hot_boost",
                        "pct": 30,
                        "turns": 2
                    },
                    {
                        "type": "heal",
                        "pct": 12
                    }
                ]
            },
            {
                "name": "不卖焦虑",
                "text": "歇脚站牌常驻回血。",
                "passive": True,
                "effects": [
                    {
                        "type": "aura",
                        "heal_pct": 3
                    },
                    {
                        "type": "regen",
                        "pct": 3,
                        "turns": 99
                    }
                ]
            }
        ]
    },
    "pighub0710": {
        "role": "输出",
        "style": "脑门红温",
        "stats": {
            "hp": 115,
            "atk": 19,
            "def": 11,
            "spd": 12,
            "crit": 10,
            "dodge": 6
        },
        "skills": [
            {
                "name": "火苗戳",
                "text": "脑门火苗戳向坦克。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    },
                    {
                        "type": "mark",
                        "pct": 8,
                        "turns": 3
                    }
                ]
            },
            {
                "name": "红温爆发",
                "text": "彻底红温，对坦克泄愤。",
                "cd": 3,
                "effects": [
                    {
                        "type": "pick",
                        "options": [
                            {
                                "text": "暴怒",
                                "effects": [
                                    {
                                        "type": "damage",
                                        "power": 2.5,
                                        "sure": True
                                    },
                                    {
                                        "type": "recoil",
                                        "pct": 6
                                    }
                                ]
                            },
                            {
                                "text": "闷烧",
                                "effects": [
                                    {
                                        "type": "damage",
                                        "power": 1.87
                                    },
                                    {
                                        "type": "dot",
                                        "pct": 4,
                                        "turns": 3,
                                        "label": "红温"
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "name": "三团小火",
                "text": "三团火球蹦向全场。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05,
                        "hits": 3,
                        "cleave": 35
                    },
                    {
                        "type": "dot",
                        "pct": 4,
                        "turns": 2,
                        "label": "灼伤"
                    }
                ]
            },
            {
                "name": "一目了然",
                "text": "火光照穿弱点，再补一刀。",
                "cd": 2,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.61
                    },
                    {
                        "type": "corrupting",
                        "pct": 20,
                        "turns": 2
                    },
                    {
                        "type": "expose",
                        "pct": 15,
                        "turns": 2
                    }
                ]
            },
            {
                "name": "怒气饰品",
                "text": "越怒越强，攻击暴击常驻。",
                "passive": True,
                "effects": [
                    {
                        "type": "buff",
                        "stat": "atk",
                        "pct": 12
                    },
                    {
                        "type": "buff",
                        "stat": "crit",
                        "pct": 12
                    }
                ]
            }
        ]
    },
    "pighub0116": {
        "role": "坦克",
        "style": "祥瑞镇压",
        "stats": {
            "hp": 145,
            "atk": 13,
            "def": 18,
            "spd": 7,
            "crit": 5,
            "dodge": 4
        },
        "skills": [
            {
                "name": "龙须拱",
                "text": "龙须扫过坦克面门。",
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.05
                    }
                ]
            },
            {
                "name": "祥瑞落地",
                "text": "祥瑞之躯砸向坦克。",
                "cd": 3,
                "effects": [
                    {
                        "type": "damage",
                        "power": 2.5,
                        "pierce": 20
                    },
                    {
                        "type": "stun",
                        "turns": 1,
                        "label": "跪祥瑞"
                    },
                    {
                        "type": "redirect",
                        "turns": 1
                    }
                ]
            },
            {
                "name": "赤金龙息",
                "text": "龙息喷过整条窑道。",
                "cd": 4,
                "effects": [
                    {
                        "type": "damage",
                        "power": 1.32,
                        "cleave": 35
                    },
                    {
                        "type": "dot",
                        "pct": 4,
                        "turns": 2,
                        "label": "龙息"
                    }
                ]
            },
            {
                "name": "赤金盘踞",
                "text": "盘成金团，嘲讽并吸伤。",
                "cd": 3,
                "effects": [
                    {
                        "type": "taunt",
                        "turns": 2
                    },
                    {
                        "type": "absorb",
                        "pct": 22,
                        "break_power": 1.4
                    },
                    {
                        "type": "fortify",
                        "pct": 22
                    }
                ]
            },
            {
                "name": "猪脸龙魄",
                "text": "龙魄不灭，濒死仍能撑住。",
                "passive": True,
                "effects": [
                    {
                        "type": "last_stand",
                        "threshold": 18,
                        "pct": 25,
                        "turns": 2
                    },
                    {
                        "type": "buff",
                        "stat": "def",
                        "pct": 8
                    }
                ]
            }
        ]
    }
}

def apply_boss_kits(pigs: dict) -> list[str]:
    """Overwrite boss entries in-place; return updated ids."""
    updated = []
    for pig_id, entry in BOSS_KITS.items():
        if pig_id not in pigs:
            raise KeyError(f"battle.json missing boss pig {pig_id}")
        pigs[pig_id] = entry
        updated.append(pig_id)
    return updated
