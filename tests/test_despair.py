import copy
import json
from pathlib import Path

import pytest

from core.despair import BOSS_SKILL_NAMES, DESPAIR_BOSS, DespairBattle, simulate_despair


def party(*, hp=300, atk=90):
    roles = ("坦克", "输出", "治疗", "辅助") * 2
    return [
        {
            "pig_id": f"pig-{index}",
            "label": f"玩家{index % 4 + 1}的猪{index // 4 + 1}",
            "seat": index,
            "owner_id": index % 4,
            "role": role,
            "stats": {"hp": hp, "atk": atk, "def": 30, "spd": 10 + index},
            "skills": [{"name": f"玩家技能{index}"}],
        }
        for index, role in enumerate(roles)
    ]


def test_requires_four_players_two_pigs_each_and_balanced_roles():
    with pytest.raises(ValueError, match="共 8 只"):
        DespairBattle(party()[:7])
    wrong_owner = party()
    wrong_owner[7]["owner_id"] = 0
    with pytest.raises(ValueError, match="恰好上阵 2 只"):
        DespairBattle(wrong_owner)
    wrong_role = party()
    wrong_role[0]["role"] = "输出"
    with pytest.raises(ValueError, match="2 坦克"):
        DespairBattle(wrong_role)


def test_one_health_bar_visits_all_five_phases_and_locks_each_boundary():
    fight = DespairBattle(party())
    attacker = fight.heroes[1]
    maximum = fight.boss_max_hp
    for phase, pct in enumerate((80, 60, 40, 20), start=1):
        assert fight.phase == phase
        fight.damage_boss(attacker, maximum * 10)
        assert fight.phase == phase + 1
        assert fight.boss_hp == pytest.approx(maximum * pct / 100)
    fight.damage_boss(attacker, maximum * 10)
    assert fight.boss_hp == 0
    assert [item["phase"] for item in fight.phase_history] == [1, 2, 3, 4, 5]


def test_phase_change_heals_six_percent_and_reduces_cooldown():
    fight = DespairBattle(party())
    hero = fight.heroes[0]
    hero.hp -= 100
    hero.cooldown = 3
    fight.damage_boss(fight.heroes[1], fight.boss_max_hp)
    assert hero.hp == pytest.approx(hero.max_hp - 82)
    assert hero.cooldown == 2


def test_boss_skills_are_an_independent_non_collectible_schema():
    battle_data = json.loads(
        (Path(__file__).parents[1] / "resources" / "battle.json").read_text("utf-8")
    )
    player_skills = {
        skill["name"] for entry in battle_data["pigs"].values() for skill in entry["skills"]
    }
    assert BOSS_SKILL_NAMES.isdisjoint(player_skills)
    assert {phase["actions"] for phase in DESPAIR_BOSS["phases"][:2]} == {2}
    assert {phase["actions"] for phase in DESPAIR_BOSS["phases"][2:]} == {3}
    result = simulate_despair(party(), seed=3)
    assert result["boss"]["collectible"] is False
    assert set(result["boss"]["skills"]) == BOSS_SKILL_NAMES


def test_blood_moon_pair_shares_thirty_percent_without_recursion():
    fight = DespairBattle(party())
    fight.phase = 3
    target = fight.heroes[0]
    mate = fight.heroes[4]
    target.shield = mate.shield = 0
    fight._hurt(target, 100)
    assert target.taken == pytest.approx(70)
    assert mate.taken == pytest.approx(30)
    target.hp = 0
    fight._process_falls()
    assert mate.grief


def test_crown_has_action_cap_break_window_and_one_restore():
    fight = DespairBattle(party())
    fight.phase = 4
    fight.crown = 12
    dps = fight.heroes[1]
    before = fight.crown
    fight._hero_turn(dps)
    assert before - fight.crown <= 3
    fight.crown = 2
    fight._hero_turn(dps)
    assert fight.crown == 0
    assert fight.vulnerable_turns == 2
    fight.vulnerable_turns = 1
    fight.crown_restored = False
    # The normal end-of-round restoration rule is exercised by a one-round run.
    fight.config = copy.deepcopy(fight.config)
    fight.config["max_rounds"] = 1
    fight.round = 0
    fight.run()
    assert fight.crown == 6
    assert fight.crown_restored


def test_judgment_seals_track_all_four_roles_and_reduce_signature():
    fight = DespairBattle(party())
    fight.phase = 5
    fight._warn_signature()
    judgment = fight.judgment
    assert judgment is not None
    for role in ("坦克", "输出", "治疗", "辅助"):
        judgment["values"][role] = judgment["needed"][role]
    fight._check_seals()
    assert all(judgment["broken"].values())
    before = sum(hero.hp for hero in fight.living())
    fight._resolve_signature()
    after = sum(hero.hp for hero in fight.living())
    assert before - after > 0
    assert fight.judgment_history[-1]["broken"] == {role: True for role in judgment["broken"]}


def test_boss_action_economy_is_two_then_three():
    fight = DespairBattle(party())
    for phase, expected in ((1, 2), (2, 2), (3, 3), (4, 3), (5, 3)):
        fight.phase = phase
        fight.pending = None
        before = len(fight.boss_actions)
        fight._boss_turn()
        assert len(fight.boss_actions) - before == expected


def test_two_shared_ultimate_charges_and_best_same_role_caster():
    heroes = party()
    heroes[1]["stats"]["atk"] = 91
    heroes[5]["stats"]["atk"] = 150
    fight = DespairBattle(heroes)
    fight.round = 5
    fight.ultimate_charges = 1
    fight._maybe_ultimate()
    assert fight.ultimate_history[-1]["caster"] == heroes[5]["label"]
    fight.phase = 5
    fight._warn_signature()
    fight.ultimate_charges = 1
    fight._maybe_ultimate()
    assert len(fight.ultimate_history) == 2
    fight.ultimate_charges = 1
    fight._maybe_ultimate()
    assert len(fight.ultimate_history) == 2  # one ultimate in the same warning cycle


def test_tank_ultimate_makes_the_whole_party_invincible_for_the_round():
    fight = DespairBattle(party())
    fight.round = 5
    fight.ultimate_charges = 1
    fight.pending = {"cycle": 1, "power": 1.2}
    before = [hero.hp for hero in fight.heroes]
    fight._maybe_ultimate()
    assert fight.ultimate_history[-1]["role"] == "坦克"
    for hero in fight.heroes:
        fight._hurt(hero, 999)
    assert [hero.hp for hero in fight.heroes] == before


def test_player_skills_and_personality_effects_are_not_ignored():
    weak = party()
    strong = copy.deepcopy(weak)
    weak[1]["skills"] = [
        {"name": "轻轻一碰", "cd": 0, "effects": [{"type": "damage", "power": 0.1}]}
    ]
    strong[1]["skills"] = [
        {"name": "全力爆发", "cd": 0, "effects": [{"type": "damage", "power": 8.0}]},
        {
            "name": "性格·会心过载",
            "passive": True,
            "effects": [{"type": "crit_amp", "multiplier": 2.0}],
        },
    ]
    weak_result = simulate_despair(weak, seed=31)
    strong_result = simulate_despair(strong, seed=31)
    assert weak_result != strong_result
    assert any("全力爆发" in line for line in strong_result["log"])


def test_boss_debuffs_have_real_effects_and_support_ultimate_refreshes_skills():
    fight = DespairBattle(party(), seed=5)
    target = fight.heroes[0]
    target.dodge = 0
    normal_hp = target.hp
    fight._boss_hit(target, 1.0)
    normal_loss = normal_hp - target.hp

    armored = DespairBattle(party(), seed=5)
    armored_target = armored.heroes[0]
    armored_target.dodge = 0
    armored_target.debuffs.append({"type": "破甲", "turns": 2})
    armored_hp = armored_target.hp
    armored._boss_hit(armored_target, 1.0)
    assert armored_hp - armored_target.hp > normal_loss

    frozen = fight.heroes[1]
    frozen.debuffs.append({"type": "冰封", "turns": 1})
    before = frozen.dealt
    fight._hero_turn(frozen)
    assert frozen.dealt == before

    fight.ultimate_charges = 1
    fight.pending = None
    for hero in fight.living()[:4]:
        hero.debuffs.append({"type": "尸毒", "turns": 2})
        hero.skill_cooldowns["测试技能"] = 3
    fight._maybe_ultimate()
    assert fight.ultimate_history[-1]["role"] == "辅助"
    assert all(not hero.debuffs and not hero.skill_cooldowns for hero in fight.living())


def test_silence_blocks_active_skill_and_auras_keep_their_real_meaning():
    heroes = party()
    heroes[1]["skills"] = [
        {"name": "高能主动", "cd": 0, "effects": [{"type": "damage", "power": 8.0}]}
    ]
    normal = DespairBattle(copy.deepcopy(heroes), seed=7)
    normal._hero_turn(normal.heroes[1])
    silenced = DespairBattle(copy.deepcopy(heroes), seed=7)
    silenced.heroes[1].debuffs.append({"type": "沉默", "turns": 1})
    silenced._hero_turn(silenced.heroes[1])
    assert normal.heroes[1].dealt > silenced.heroes[1].dealt

    healer = DespairBattle(copy.deepcopy(heroes), seed=9)
    source, target = healer.heroes[0], healer.heroes[1]
    source.data["skills"] = [
        {
            "name": "真实暖流",
            "passive": True,
            "effects": [{"type": "aura", "heal_pct": 7}],
        }
    ]
    target.hp -= 50
    before = target.hp
    healer._hero_turn(source)
    assert target.hp - before == pytest.approx(target.max_hp * 0.07)

    defense = DespairBattle(copy.deepcopy(heroes), seed=9)
    defense_source, defense_target = defense.heroes[0], defense.heroes[1]
    defense_source.data["skills"] = [
        {
            "name": "防御光环",
            "passive": True,
            "effects": [{"type": "aura", "stat": "def", "pct": 20}],
        }
    ]
    defense_target.hp -= 50
    before = defense_target.hp
    defense._hero_turn(defense_source)
    assert defense_target.hp == before


def test_disaster_breath_cycles_through_all_three_ailments():
    fight = DespairBattle(party(), seed=13)
    fight.phase = 2
    for hero in fight.heroes:
        hero.dodge = 0
    skill = next(item for item in fight.phase_data["skills"] if item["name"] == "灾厄吐息")
    fight._use_skill(skill)
    fight._use_skill(skill)
    ailments = {debuff["type"] for hero in fight.heroes for debuff in hero.debuffs}
    assert {"尸毒", "寒意", "易伤"} <= ailments


def test_simulation_is_deterministic_and_result_is_json_ready():
    first = simulate_despair(party(), seed=19)
    second = simulate_despair(party(), seed=19)
    assert first == second
    json.dumps(first, ensure_ascii=False)
    assert first["rounds"] <= 20
    assert first["won"]
    assert [item["phase"] for item in first["phases"]] == [1, 2, 3, 4, 5]
    assert all(
        {"seat", "hp", "max_hp", "alive", "dealt", "healed", "taken"} <= set(hero)
        for hero in first["heroes"]
    )
    assert len(first["ultimate"]["history"]) == 2


def test_player_adapter_supports_special_effects_without_basic_fallback():
    fight = DespairBattle(party(), seed=41)
    hero, ally = fight.heroes[:2]
    ally.hp -= 120

    assert fight._apply_hero_effect(hero, {"type": "snapshot"})
    saved = hero.snapshot
    fight._hurt(hero, 50, linked=False)
    assert fight._apply_hero_effect(hero, {"type": "rewind", "turns": 1})
    assert hero.hp == pytest.approx(saved)

    assert fight._apply_hero_effect(
        hero, {"type": "delay_heal", "pct": 10, "turns": 1, "target": "ally_lowest"}
    )
    before = ally.hp
    ally.data["skills"] = []
    fight._hero_turn(ally)
    assert ally.hp > before

    before = hero.hp
    assert fight._apply_hero_effect(hero, {"type": "recoil", "pct": 10})
    assert hero.hp < before
    before = ally.hp
    assert fight._apply_hero_effect(
        hero,
        {
            "type": "life_link",
            "pct": 12,
            "self_pct": -5,
            "target": "ally_lowest",
        },
    )
    assert ally.hp > before

    before = hero.hp
    assert fight._apply_hero_effect(
        hero,
        {
            "type": "sacrifice",
            "pct": 5,
            "shield_pct": 10,
            "target": "ally_lowest",
        },
    )
    assert hero.hp < before and any(unit.shield > 0 for unit in fight.heroes)
    assert fight._apply_hero_effect(hero, {"type": "taunt", "turns": 2})
    assert fight._target() is hero

    boss_before = fight.boss_hp
    assert fight._apply_hero_effect(
        hero,
        {
            "type": "pick",
            "options": [{"weight": 1, "effects": [{"type": "damage", "power": 1.0}]}],
        },
    )
    assert fight.boss_hp < boss_before
    boss_before = fight.boss_hp
    assert fight._apply_hero_effect(hero, {"type": "copy", "pct": 120})
    assert fight.boss_hp < boss_before


def test_corrupting_reflect_last_stand_and_revive_keep_distinct_semantics():
    heroes = party()
    heroes[0]["skills"] = [
        {
            "name": "不灭",
            "passive": True,
            "effects": [
                {"type": "revive", "pct": 40},
                {"type": "last_stand", "threshold": 50, "pct": 50, "turns": 2},
                {"type": "reflect", "pct": 30, "turns": 99},
            ],
        }
    ]
    fight = DespairBattle(heroes, seed=2)
    tank = fight.heroes[0]
    tank.dodge = 0
    tank.hp = tank.max_hp * 0.55
    fight._hurt(tank, tank.max_hp * 0.10, linked=False)
    assert tank.last_stand_turns == 2
    before = tank.hp
    fight._hurt(tank, 40, linked=False)
    assert before - tank.hp == pytest.approx(20)

    boss_before = fight.boss_hp
    fight._boss_hit(tank, 0.5)
    assert fight.boss_hp < boss_before
    tank.hp = 1
    fight._hurt(tank, 999, linked=False)
    assert tank.hp == pytest.approx(tank.max_hp * 0.40)

    fight.boss_hp -= 100
    assert fight._apply_hero_effect(tank, {"type": "corrupting", "pct": 75, "turns": 2})
    before = fight.boss_hp
    fight._heal_boss(100)
    assert fight.boss_hp - before == pytest.approx(25)


def test_skill_chance_failure_energy_and_fail_payload_are_consumed():
    heroes = party()
    heroes[1]["skills"] = [
        {
            "name": "蓄能",
            "gain": 50,
            "effects": [{"type": "damage", "power": 0.1, "chance": 1}],
        },
        {
            "name": "必定失手",
            "cost": 40,
            "fail": {
                "chance": 100,
                "text": "测试失败",
                "effects": [{"type": "recoil", "pct": 10}],
            },
            "effects": [{"type": "damage", "power": 5}],
        },
    ]
    fight = DespairBattle(heroes, seed=1)
    hero = fight.heroes[1]
    assert fight._cast_hero_skill(hero, hero.data["skills"][0])
    assert hero.energy == 50
    hp = hero.hp
    boss = fight.boss_hp
    assert fight._cast_hero_skill(hero, hero.data["skills"][1])
    assert hero.energy == 10 and hero.hp < hp and fight.boss_hp == boss
    assert any("测试失败" in line for line in fight.log)


def test_unknown_effect_does_not_silently_consume_a_skill_or_cooldown():
    fight = DespairBattle(party())
    hero = fight.heroes[1]
    hp, boss = hero.hp, fight.boss_hp
    skill = {"name": "未知协议", "cost": 10, "effects": [{"type": "future_effect"}]}
    hero.energy = 20
    assert fight._cast_hero_skill(hero, skill) is False
    assert hero.energy == 20 and hero.hp == hp and fight.boss_hp == boss
    assert any("不支持的效果" in line for line in fight.log)


def test_delay_heal_ticks_on_target_even_if_caster_has_fallen():
    fight = DespairBattle(party())
    caster, target = fight.heroes[:2]
    target.hp -= 100
    fight._apply_hero_effect(
        caster, {"type": "delay_heal", "pct": 10, "turns": 1, "target": "ally_lowest"}
    )
    caster.hp = 0
    before = target.hp
    target.data["skills"] = []
    fight._hero_turn(target)
    assert target.hp > before
    assert caster.healed > 0


def test_control_effects_have_distinct_boss_action_semantics():
    silenced = DespairBattle(party(), seed=11)
    silenced._apply_hero_effect(silenced.heroes[3], {"type": "silence", "turns": 1})
    silenced._boss_turn()
    names = [item["skill"] for item in silenced.boss_actions]
    assert len(names) == 2 and len(set(names)) == 1

    rooted = DespairBattle(party(), seed=11)
    rooted._apply_hero_effect(rooted.heroes[3], {"type": "root", "turns": 1})
    rooted._boss_turn()
    assert len(rooted.boss_actions) == 1

    stunned = DespairBattle(party(), seed=11)
    stunned._apply_hero_effect(stunned.heroes[3], {"type": "stun", "turns": 1})
    stunned._boss_turn()
    assert len(stunned.boss_actions) == 1

    self_stun = DespairBattle(party(), seed=11)
    hero = self_stun.heroes[3]
    self_stun._apply_hero_effect(
        hero, {"type": "stun", "turns": 1, "target": "self", "label": "闪退"}
    )
    before = hero.dealt
    self_stun._hero_turn(hero)
    assert hero.dealt == before and any(item["type"] == "眩晕" for item in hero.debuffs) is False


def test_player_skill_cooldown_decays_exactly_once_per_turn():
    fight = DespairBattle(party())
    hero = fight.heroes[1]
    hero.data["skills"] = []
    hero.skill_cooldowns["测试冷却"] = 3
    fight._hero_turn(hero)
    assert hero.skill_cooldowns["测试冷却"] == 2


def test_buffs_protection_evasion_immunity_and_hot_boost_are_distinct():
    fight = DespairBattle(party(), seed=21)
    tank, ally = fight.heroes[:2]
    base_atk = fight._hero_stat(tank, "atk")
    fight._apply_hero_effect(
        tank, {"type": "buff", "stat": "atk", "pct": 50, "turns": 2}
    )
    assert fight._hero_stat(tank, "atk") == pytest.approx(base_atk * 1.5)
    assert tank.shield == 0

    fight._apply_hero_effect(tank, {"type": "mitigate", "pct": 50, "turns": 2})
    before = tank.hp
    fight._hurt(tank, 40, linked=False)
    assert before - tank.hp == pytest.approx(20)
    fight._apply_hero_effect(tank, {"type": "block", "count": 1, "pct": 100})
    before = tank.hp
    fight._hurt(tank, 40, linked=False)
    assert tank.hp == before

    fight._apply_hero_effect(tank, {"type": "absorb", "pct": 10})
    assert tank.shield == pytest.approx(tank.max_hp * 0.10)
    ally.hp -= 100
    normal = fight._heal(ally, 20, tank)
    ally.hp -= normal
    fight._apply_hero_effect(tank, {"type": "hot_boost", "pct": 50, "target": "ally_lowest"})
    boosted = fight._heal(ally, 20, tank)
    assert boosted == pytest.approx(normal * 1.5)

    fight._apply_hero_effect(tank, {"type": "evade", "count": 1})
    before = tank.hp
    assert fight._boss_hit(tank, 1) == 0
    assert tank.hp == before and tank.evade_count == 0
    fight._apply_hero_effect(tank, {"type": "immunity", "kind": "all", "turns": 2})
    assert fight._add_debuff(tank, {"type": "尸毒", "turns": 2}, "dot") is False
    assert not tank.debuffs


def test_guard_share_fortify_and_redirect_have_independent_behavior():
    fight = DespairBattle(party())
    tank, ward = fight.heroes[:2]
    fight._apply_hero_effect(
        tank, {"type": "guard", "pct": 50, "turns": 2, "target": "ally_lowest"}
    )
    # Make ward the explicit lowest target selected by the guard.
    guarded = next(unit for unit in fight.heroes if unit.guards)
    before_guard, before_ward = tank.hp, guarded.hp
    fight._hurt(guarded, 40)
    assert tank.hp < before_guard and guarded.hp > before_ward - 40

    fight._apply_hero_effect(tank, {"type": "share", "pct": 40, "turns": 2})
    target = fight.heroes[2]
    before = [unit.hp for unit in fight.heroes]
    fight._hurt(target, 80)
    losses = [old - unit.hp for old, unit in zip(before, fight.heroes)]
    assert losses[target.index] < 80 and sum(value > 0 for value in losses) > 1

    tank.shield = 20
    fight._apply_hero_effect(tank, {"type": "fortify", "pct": 50})
    assert tank.shield > 20
    fight._apply_hero_effect(tank, {"type": "redirect", "turns": 2})
    assert fight._target() is tank


def test_leech_charge_and_channel_use_damage_and_delayed_timing():
    fight = DespairBattle(party(), seed=22)
    hero = fight.heroes[1]
    hero.hp -= 100
    before_boss, before_hp = fight.boss_hp, hero.hp
    skill = {
        "name": "吸血攻击",
        "effects": [
            {"type": "damage", "power": 1.0},
            {"type": "leech", "pct": 50},
        ],
    }
    assert fight._cast_hero_skill(hero, skill)
    assert fight.boss_hp < before_boss and hero.hp > before_hp

    boss = fight.boss_hp
    fight._apply_hero_effect(hero, {"type": "charge", "power": 2, "turns": 1})
    assert fight.boss_hp == boss
    hero.data["skills"] = []
    fight._hero_turn(hero)
    assert fight.boss_hp < boss

    boss = fight.boss_hp
    fight._apply_hero_effect(
        hero, {"type": "channel", "power": 0.5, "heal_pct": 5, "turns": 2}
    )
    assert fight.boss_hp == boss
    fight._hero_turn(hero)
    assert fight.boss_hp < boss and hero.channels

    delayed = DespairBattle(party(), seed=22)
    source = delayed.heroes[1]
    delayed._apply_hero_effect(
        source, {"type": "delay_damage", "pct": 5, "turns": 2, "label": "星核"}
    )
    boss = delayed.boss_hp
    delayed._tick_boss_statuses()
    assert delayed.boss_hp == boss
    delayed._tick_boss_statuses()
    assert delayed.boss_hp < boss and source.dealt > 0


def test_phase_transition_stops_remaining_skill_effects_and_cancels_warning():
    fight = DespairBattle(party(), seed=3)
    fight.pending = {"name": "王座冲锋", "power": 1, "due": 2, "cycle": 1}
    skill = {
        "name": "双重越界",
        "effects": [
            {"type": "damage", "power": 99},
            {"type": "damage", "power": 99},
        ],
    }
    assert fight._cast_hero_skill(fight.heroes[1], skill)
    assert fight.phase == 2
    assert fight.boss_hp == pytest.approx(fight.boss_max_hp * 0.8)
    assert any("预警「王座冲锋」已取消" in line for line in fight.log)


def test_invincibility_blocks_on_hit_debuff_and_dot_revive_ends_turn():
    fight = DespairBattle(party(), seed=4)
    target = fight.heroes[0]
    target.dodge = 0
    fight.phase = 1
    fight.round = 5
    target.invincible_until = 5
    skill = next(item for item in fight.phase_data["skills"] if item["name"] == "裂甲獠牙")
    fight._use_skill(skill)
    assert not target.debuffs

    heroes = party()
    heroes[1]["skills"] = [
        {
            "name": "复生",
            "passive": True,
            "effects": [{"type": "revive", "pct": 37}],
        },
        {"name": "本不应行动", "effects": [{"type": "damage", "power": 99}]},
    ]
    revived = DespairBattle(heroes, seed=4)
    hero = revived.heroes[1]
    hero.hp = 1
    hero.debuffs.append({"type": "尸毒", "turns": 2})
    revived._hero_turn(hero)
    assert hero.hp == pytest.approx(hero.max_hp * 0.37)
    assert hero.dealt == 0
    assert hero.debuffs[0]["turns"] == 1


def test_poise_break_without_warning_grants_expose_and_custom_timeout_is_true():
    fight = DespairBattle(party())
    fight.pending = None
    fight.poise = 1
    fight._reduce_poise(2, "控制")
    assert fight.poise_breaks == 1 and fight.boss_expose
    assert any("受到伤害提高" in line for line in fight.log)

    config = copy.deepcopy(DESPAIR_BOSS)
    config["max_rounds"] = 1
    config["stats"] = {"hp": 10**9, "atk": 1, "def": 10**6}
    result = simulate_despair(party(hp=10_000, atk=1), seed=7, boss=config)
    assert result["rounds"] == 1 and result["timeout"] is True


def test_start_of_round_dot_transition_uses_first_phase_two_disaster():
    config = copy.deepcopy(DESPAIR_BOSS)
    config["max_rounds"] = 1
    fight = DespairBattle(party(atk=1), seed=8, boss=config)
    fight.boss_dots.append(
        {"source": fight.heroes[1], "pct": 25, "turns": 1, "label": "跨阶段灼烧"}
    )
    fight.run()
    assert fight.phase >= 2
    phase_two = next(line for line in fight.log if "阶段机制·七灾预兆" in line)
    assert phase_two.endswith("尸毒。")
