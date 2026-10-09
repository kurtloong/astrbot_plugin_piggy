import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image, ImageDraw

from core import replay, replay_effects, replay_scenes, video
from core.battle import FALLBACK, fighter, validate_entry
from core.config import Settings
from core.database import Database
from core.delivery import Message, Sender
from core.raid import boss_fighter, simulate_raid
from core.raid_bosses import BOSSES
from core.raid_events import new_config

ENTRY = validate_entry("fallback", FALLBACK)


def party(count=4):
    heroes = []
    for seat in range(count):
        hero = fighter(
            {"id": f"p{seat}", "name": f"猪{seat}", "asset": ""}, ENTRY, 6, f"玩家{seat}的猪{seat}"
        )
        hero["seat"] = seat
        heroes.append(hero)
    return heroes


def fight(slot="pig_god", seed=3, stage=3):
    boss = boss_fighter({"id": slot, "name": "BOSS", "asset": ""}, ENTRY, 6, stage, slot, "BOSS")
    return simulate_raid(party(), boss, slot, new_config(), seed)


def data_for(result, slot="pig_god"):
    return {
        "theme": 3,
        "dungeon": "猪神殿",
        "stage": 3,
        "stages": 3,
        "title": "猪神殿 · 第 3/3 关",
        "boss_slot": slot,
        "boss_name": "BOSS",
        "events": ["温泉：回复 30%"],
        "won": result["won"],
        "timeout": result["timeout"],
        "rounds": result["rounds"],
        "roster": result["roster"],
        "timeline": result["timeline"],
        "top": [("玩家0的猪0", 100)],
    }


class TimelineTests(unittest.TestCase):
    def test_every_log_line_is_a_step_with_everyones_state(self):
        result = fight()
        timeline = result["timeline"]
        self.assertEqual(
            [step["text"] for step in timeline],
            [line.split(" ", 1)[-1] if line[:1] == "R" else line for line in result["log"]],
        )
        for step in timeline:
            self.assertLessEqual(len(step["state"]), len(result["roster"]))
            self.assertTrue(all(len(unit) == 5 and unit[0] >= 0 for unit in step["state"]))
        final = timeline[-1]["state"]
        self.assertEqual(
            [final[u["uid"]][0] for u in result["roster"] if u["side"] == 0],
            [h["hp"] for h in result["heroes"]],
        )

    def test_cues_match_the_damage_dealt_and_mechanics_fired(self):
        result = fight()
        hits = [c for step in result["timeline"] for c in step["cues"] if c["t"] == "hit"]
        by_hero = {}
        for cue in hits:
            if cue["src"] is not None and cue["src"] < 4 and cue["dst"] >= 4:
                by_hero[cue["src"]] = by_hero.get(cue["src"], 0) + cue["amt"]
        for hero in result["heroes"]:
            self.assertAlmostEqual(by_hero.get(hero["seat"], 0), hero["dealt"], delta=4)
        names = {name for name, _ in BOSSES["pig_god"].MECHANICS}
        fired = {c["name"] for step in result["timeline"] for c in step["cues"] if c["t"] == "mech"}
        self.assertTrue(fired)
        self.assertLessEqual(fired, names)
        skills = [c for step in result["timeline"] for c in step["cues"] if c["t"] == "skill"]
        self.assertTrue(all(c["name"] for c in skills))

    def test_falls_and_summons_are_cued(self):
        for seed in range(10):
            result = fight(seed=seed)
            cues = [c["t"] for step in result["timeline"] for c in step["cues"]]
            if "fall" in cues and "summon" in cues:
                break
        self.assertIn("fall", cues)
        self.assertIn("summon", cues)
        summoned = {
            c["dst"] for step in result["timeline"] for c in step["cues"] if c["t"] == "summon"
        }
        self.assertTrue(all(result["roster"][uid]["kind"] == "minion" for uid in summoned))

    def test_status_badges_follow_dots_buffs_and_controls(self):
        result = fight("demon-pig", stage=1)
        badges = {
            glyph for step in result["timeline"] for unit in step["state"] for glyph, _ in unit[4]
        }
        self.assertTrue(badges & {"↓", "流", "易"})
        kinds = {
            kind for step in result["timeline"] for unit in step["state"] for _, kind in unit[4]
        }
        self.assertLessEqual(kinds, {"bad", "good", "control"})

    def test_same_seed_same_timeline(self):
        self.assertEqual(
            json.dumps(fight(seed=9)["timeline"]), json.dumps(fight(seed=9)["timeline"])
        )


class EffectTests(unittest.TestCase):
    def test_keywords_pick_elements(self):
        for name, element in (
            ("火焰冲撞", "fire"),
            ("急速冷冻", "ice"),
            ("雷霆一击", "thunder"),
            ("腐绿吐息", "poison"),
            ("神之一点", "holy"),
            ("幽灵猪穿墙抓", "shadow"),
            ("深渊暗流", "water"),
            ("宇宙猪陨石", "star"),
            ("机械腿踢", "metal"),
            ("拱一下", "physical"),
        ):
            self.assertEqual(replay_effects.element_of(name)[0], element, name)
        self.assertEqual(replay_effects.element_of("拱", "喷出一团火")[0], "fire")
        self.assertEqual(
            replay_effects.element_of("拱", "", "力量"), replay_effects.element_of("撞", "", "力量")
        )

    def test_every_boss_mechanic_has_its_own_scene_that_draws(self):
        expected = {(slot, index) for slot in BOSSES for index in range(3)}
        self.assertEqual(set(replay_scenes.SCENES), expected)
        self.assertEqual(
            len({scene.name for scene in replay_scenes.SCENES.values()}), len(expected)
        )
        image = Image.new("RGB", replay.SIZE)
        draw = ImageDraw.Draw(image, "RGBA")
        for team in ([(100, 960), (280, 960), (460, 960), (620, 960)], []):
            ctx = replay_scenes.Context(replay.BOSS_CENTER, team, replay.SIZE, lambda *args: None)
            for scene in replay_scenes.SCENES.values():
                for t in (0.0, 0.5, 1.0):
                    scene.play(draw, t, ctx)
        self.assertIs(replay_scenes.scene_for("nobody", 0), replay_scenes.FALLBACK)

    def test_mechanic_steps_get_a_cut_in_and_more_time(self):
        timeline = fight()["timeline"]
        durations = replay.plan(timeline, 90)
        mech = [d for s, d in zip(timeline, durations) if replay._mechanic_step(s)]
        plain = [d for s, d in zip(timeline, durations) if not s["cues"]]
        self.assertTrue(mech and plain)
        self.assertGreater(min(mech), max(plain))

    def test_base_effects_draw_for_every_element(self):
        image = Image.new("RGB", (300, 300))
        draw = ImageDraw.Draw(image, "RGBA")
        for element, _, color in replay_effects.ELEMENTS + (("physical", (), (200, 100, 100)),):
            for t in (0.0, 0.4, 1.0):
                replay_effects.impact(draw, element, (150, 150), t, color, 3)
        for t in (0.0, 0.5, 1.0):
            replay_effects.rising(draw, (150, 150), t, replay_effects.GREEN)
            replay_effects.bubble(draw, (150, 150), t, replay_effects.BLUE)
            replay_effects.arrows(draw, (150, 150), t, t > 0.4)
            replay_effects.pillar(draw, (150, 150), t)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_fits_the_requested_length(self):
        timeline = fight()["timeline"]
        for seconds in (20, 60, 90):
            durations = replay.plan(timeline, seconds)
            rounds = len({s["round"] for s in timeline if s["round"]})
            total = sum(durations) + replay.INTRO + replay.OUTRO + replay.ROUND_CARD * rounds
            self.assertLessEqual(
                total, seconds + 2 + replay.STEP_MIN * len(timeline) * (seconds == 20)
            )
            self.assertTrue(
                all(replay.STEP_MIN <= d <= replay.STEP_MAX + replay.MECH_CUTIN for d in durations)
            )

    def test_illustrated_backdrops_load_and_others_fall_back(self):
        for path in replay.BACKGROUNDS.glob("*.webp"):
            self.assertIn(path.stem, BOSSES, path.name)
            with Image.open(path) as image:
                self.assertEqual(image.size, replay.SIZE)
        result = fight()
        for slot in ("alien-pig", "pig_god"):
            stage = replay._Stage(self.root, {**data_for(result), "boss_slot": slot})
            self.assertEqual(stage.background.size, replay.SIZE)
            self.assertEqual(stage.background.mode, "RGB")

    def test_frames_cover_the_whole_fight(self):
        result = fight()
        frames = list(replay.frames(self.root, data_for(result), 20, 6))
        self.assertTrue(all(frame.size == replay.SIZE for frame in frames))
        self.assertGreater(len(frames), len(result["timeline"]) * 2)

    @unittest.skipUnless(video.available(), "PyAV not installed")
    def test_encodes_a_playable_mp4(self):
        result = fight()
        data = video.render(lambda: replay.frames(self.root, data_for(result), 20, 6), 6, 20)
        self.assertEqual(data[4:8], b"ftyp")
        self.assertLess(len(data), video.MAX_BYTES)

    def test_no_video_without_pyav_or_when_disabled(self):
        from core.views import raid_video_job

        battle = {
            "result": {**fight(), "timeline": fight()["timeline"]},
            "raid": {
                "dungeon": {
                    "key": 3,
                    "name": "猪神殿",
                    "bosses": ("demon-pig", "chained_crown_pig", "pig_god"),
                }
            },
            "stage": 3,
            "fighters": [],
            "ally": None,
            "boss": {"label": "BOSS"},
            "events": [],
            "won": True,
        }
        self.assertIsNone(raid_video_job(Settings(raid_video=False), self.root, battle))
        self.assertIsNone(raid_video_job(Settings(), self.root, {**battle, "result": None}))
        original = video.av
        try:
            video.av = None
            self.assertIsNone(raid_video_job(Settings(), self.root, battle))
        finally:
            video.av = original


class FakeTransport:
    def __init__(self, video_error=None):
        self.payloads, self.videos, self.video_error = [], [], video_error

    async def request(self, event, payload):
        self.payloads.append(dict(payload))
        return {"id": "sent"}

    async def upload_image(self, event, data):
        return "image-info"

    async def upload_video(self, event, data):
        if self.video_error:
            raise self.video_error
        self.videos.append(data)
        return "video-info"


class VideoDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name))
        await self.db.initialize()
        self.event = SimpleNamespace(
            message_obj=SimpleNamespace(message_id="incoming"), get_group_id=lambda: "group"
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    def message(self, build):
        return Message("", (b"png",), local=True, video=build)

    async def test_video_comes_before_the_poster_once(self):
        transport = FakeTransport()
        sender = Sender(Settings(), self.db, None, transport)
        await sender.send(self.event, "app", self.message(lambda: b"mp4"))
        self.assertEqual([p["msg_seq"] for p in transport.payloads], [100, 101])
        self.assertEqual(transport.payloads[0]["media"], {"file_info": "video-info"})
        self.assertEqual(transport.payloads[1]["media"], {"file_info": "image-info"})
        self.assertEqual(transport.videos, [b"mp4"])
        await sender.send(self.event, "app", self.message(lambda: b"mp4"))
        self.assertEqual(len(transport.payloads), 2)

    async def test_notice_goes_first_then_video_then_poster(self):
        transport = FakeTransport()
        sender = Sender(Settings(), self.db, None, transport)
        message = replace(self.message(lambda: b"mp4"), notice="副本进行中……")
        await sender.send(self.event, "app", message)
        self.assertEqual([p["msg_seq"] for p in transport.payloads], [100, 101, 102])
        notice, clip, poster = transport.payloads
        self.assertEqual((notice["msg_type"], notice["content"]), (0, "副本进行中……"))
        self.assertEqual(clip["media"], {"file_info": "video-info"})
        self.assertEqual(poster["media"], {"file_info": "image-info"})

    async def test_video_failures_never_break_the_poster(self):
        def broken():
            raise RuntimeError("encoder exploded")

        for build, transport in (
            (broken, FakeTransport()),
            (lambda: b"mp4", FakeTransport(video_error=RuntimeError("rejected"))),
            (lambda: None, FakeTransport()),
        ):
            logger = Mock()
            self.event.message_obj.message_id = f"event-{id(transport)}"
            sender = Sender(Settings(), self.db, None, transport, logger=logger)
            await sender.send(self.event, "app", self.message(build))
            self.assertEqual(len(transport.payloads), 1)
            self.assertEqual(transport.payloads[0]["msg_type"], 7)


if __name__ == "__main__":
    unittest.main()
