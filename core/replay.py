"""Turn a raid battle timeline into video frames.

`frames(root, data, seconds, fps)` yields RGB frames for the whole fight: a title
card, every timeline step sped up to fit `seconds`, and a result card. `data` is
built by `replay_data()` from the battle dict `Database._raid_fight` returns.

Each stage is played on its own illustrated backdrop (resources/raid_backgrounds):
the boss stands on the middle platform, the party on the floor below. Units breathe,
lunge when they act and flash when hit; effects are drawn on their own layer and
given a soft bloom before being composited.
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from . import replay_effects as fx
from . import replay_scenes as scenes
from .raid import mechanics_for
from .rendering import Typeset

W, H = 720, 1280
SIZE = (W, H)
INTRO, OUTRO, ROUND_CARD = 2.0, 2.6, 0.45
STEP_MIN, STEP_MAX, EMPHASIS = 0.22, 0.8, 0.4
# A boss mechanic gets a cut-in: veil, boss portrait and name band, then its scene.
MECH_CUTIN = 1.1
CUTIN_SPLIT = 0.4
IMPACT_AT = 0.35
BACKGROUNDS = Path(__file__).resolve().parents[1] / "resources" / "raid_backgrounds"

# Where everyone stands on the backdrop (feet positions).
BOSS_BOX = 300
BOSS_FOOT = (360, 720)
BOSS_CENTER = (BOSS_FOOT[0], BOSS_FOOT[1] - BOSS_BOX // 2)
MINION_BOX = 120
MINION_FEET = ((118, 770), (602, 770), (112, 590), (608, 590), (220, 470), (500, 470))
# Where the boss platform sits on each illustrated backdrop (feet y); the default is 720.
BOSS_FLOOR = {
    "goblin-pig": 690,
    "frozen-pig": 760,
    "everest-pig": 660,
    "error-404-pig": 680,
    "mechanical-pig": 720,
    "cyberpunk-pig": 680,
    "demon-pig": 690,
    "chained_crown_pig": 705,
    "pig_god": 700,
    "zombie-pig": 720,
    "pighub0876": 790,
    "skeleton-pig": 690,
    "pighub0233": 700,
    "pighub0007": 805,
    "pighub0830": 790,
    "alien-pig": 700,
    "pighub0872": 680,
    "pighub0336": 640,
    "pighub0315": 700,
    "pighub0710": 720,
    "pighub0116": 680,
}
HERO_BOX = 150
HERO_FOOT_Y = 1062
PLATE_TOP, PLATE_H = 1072, 94
BANNER_Y = 826
CAPTION_TOP = 1182

INK = (255, 255, 255)
DIM = (205, 200, 220)
PANEL = (18, 14, 30)
OUTLINE = (30, 18, 32)
HP = (236, 78, 96)
TRAIL = (255, 236, 200)
TRACK = (60, 48, 70)
BADGE_COLORS = {"bad": (205, 60, 85), "good": (60, 165, 100), "control": (125, 85, 215)}
# Backdrop for dungeons without an illustration yet: top colour, bottom colour.
THEMES = {
    1: ((70, 110, 160), (200, 225, 245)),
    2: ((60, 66, 82), (150, 158, 172)),
    3: ((120, 70, 50), (230, 190, 150)),
    4: ((40, 52, 46), (120, 140, 120)),
    5: ((20, 60, 120), (90, 160, 220)),
    6: ((30, 24, 70), (120, 100, 190)),
    7: ((72, 24, 16), (255, 148, 55)),
}


def replay_data(battle: dict) -> dict | None:
    """The parts of a fought stage the video needs, or None when nothing was fought."""
    result = battle.get("result")
    if not result or not result.get("timeline"):
        return None
    info = battle["raid"]["dungeon"]
    stage = battle["stage"]
    slot = info["bosses"][stage - 1]
    roster = result["roster"]
    dealt = {f["seat"]: f.get("dealt", 0) for f in battle["fighters"]}
    ally = (battle.get("ally") or {}).get("dealt", 0)
    damage = [
        (u["label"], ally if u["kind"] == "ally" else dealt.get(u["seat"], 0))
        for u in roster
        if u["side"] == 0
    ]
    top = sorted(damage, key=lambda row: -row[1])[:3]
    return {
        "theme": info["key"],
        "dungeon": info["name"],
        "stage": stage,
        "stages": len(info["bosses"]),
        "title": f"{info['name']} · 第 {stage}/{len(info['bosses'])} 关",
        "boss_slot": slot,
        "mechanics": dict(enumerate(text for _, text in mechanics_for(slot).MECHANICS)),
        "boss_name": battle["boss"]["label"],
        "events": [f"{e['name']}：{e['text']}" for e in battle["events"]][:3],
        "won": battle["won"],
        "timeout": result["timeout"],
        "rounds": result["rounds"],
        "roster": roster,
        "timeline": result["timeline"],
        "top": [row for row in top if row[1] > 0],
    }


class _Clock:
    def __init__(self, fps: int):
        self.fps, self.frame = fps, 0

    @property
    def seconds(self) -> float:
        return self.frame / self.fps


class _Stage:
    """The backdrop, every unit's sprites and where each unit stands."""

    def __init__(self, root: Path, data: dict):
        self.root = root
        self.data = data
        self.typeset = Typeset(root)
        self.roster = data["roster"]
        self.boss = next(u for u in self.roster if u["kind"] == "boss")
        self.background = self._background()
        self.feet, self.boxes, self.sprites, self.centers = {}, {}, {}, {}
        floor = BOSS_FLOOR.get(data["boss_slot"], BOSS_FOOT[1])
        lift = floor - BOSS_FOOT[1]
        self._place(self.boss, (BOSS_FOOT[0], floor), BOSS_BOX)
        self.boss_center = self.centers[self.boss["uid"]]
        for unit, (x, y) in zip((u for u in self.roster if u["kind"] == "minion"), MINION_FEET):
            self._place(unit, (x, y + lift), MINION_BOX)
        heroes = [u for u in self.roster if u["side"] == 0]
        spacing = W / max(1, len(heroes))
        for index, unit in enumerate(heroes):
            box = int(min(HERO_BOX, spacing - 26))
            self._place(unit, (spacing * (index + 0.5), HERO_FOOT_Y), box)
        self.plate_width = int(min(170, spacing - 12))

    def _place(self, unit, foot, box):
        uid = unit["uid"]
        self.feet[uid] = foot
        self.boxes[uid] = box
        self.centers[uid] = (foot[0], foot[1] - box * 0.55)
        self.sprites[uid] = self._sprites(unit, box)

    def _background(self) -> Image.Image:
        path = BACKGROUNDS / f"{self.data['boss_slot']}.webp"
        if path.is_file():
            with Image.open(path) as source:
                image = ImageOps.fit(source.convert("RGB"), SIZE, Image.Resampling.LANCZOS)
        else:
            top, bottom = THEMES.get(self.data["theme"], THEMES[3])
            image = Image.new("RGB", SIZE)
            draw = ImageDraw.Draw(image)
            for y in range(H):
                k = y / H
                draw.line(
                    (0, y, W, y), fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bottom))
                )
        # Darken the top and bottom bands so the HUD and name plates stay readable.
        shade = Image.new("L", (1, H))
        for y in range(H):
            if y < 190:
                alpha = 190 * (1 - y / 190) ** 1.4
            elif y > 1010:
                alpha = 205 * ((y - 1010) / (H - 1010)) ** 1.2
            else:
                alpha = 0
            shade.putpixel((0, y), int(alpha))
        dark = Image.new("RGB", SIZE, (8, 6, 16))
        image.paste(dark, (0, 0), shade.resize(SIZE))
        # The frame stays RGB: translucent drawing then blends instead of overwriting.
        draw = ImageDraw.Draw(image, "RGBA")
        self.text(draw, self.data["title"], (24, 30), 26, INK, True, stroke=3, anchor="lm")
        return image

    def _sprites(self, unit, box: int):
        path = self.root / "assets" / unit["asset"] if unit["asset"] else None
        if path and path.is_file():
            with Image.open(path) as original:
                art = ImageOps.exif_transpose(original).convert("RGBA")
        else:
            art = Image.new("RGBA", (box, box), (0, 0, 0, 0))
            ImageDraw.Draw(art).ellipse((10, 10, box - 10, box - 10), fill=(225, 190, 180, 255))
        art.thumbnail((box, box), Image.Resampling.LANCZOS)
        if _opaque(art):
            art = _card(art)
        alpha = art.getchannel("A")
        grey = ImageOps.grayscale(art.convert("RGB")).convert("RGBA")
        grey.putalpha(alpha.point(lambda a: a * 0.55))
        white = Image.new("RGBA", art.size, (255, 255, 255, 0))
        white.putalpha(alpha)
        return art, grey, white

    # ----- text -----

    def text(self, draw, value, at, size, fill, bold=False, stroke=0, anchor="mm"):
        value = str(value)
        width = self.typeset.length(draw, value, size, bold)
        x, y = at
        if anchor[0] == "m":
            x -= width / 2
        elif anchor[0] == "r":
            x -= width
        y -= size * 0.62
        outline = (*OUTLINE, fill[3]) if len(fill) == 4 else OUTLINE
        self.typeset.draw(
            draw, (x, y), value, size, fill, bold, stroke, outline if stroke else None
        )

    def fit(self, draw, value, size, width, bold=False) -> str:
        value = str(value)
        if self.typeset.length(draw, value, size, bold) <= width:
            return value
        while value and self.typeset.length(draw, value + "…", size, bold) > width:
            value = value[:-1]
        return value + "…"

    # ----- units -----

    def present(self, state, uid) -> bool:
        return uid < len(state) and uid in self.feet

    def units(self, frame, draw, state, motion, clock: float):
        """Shadows and sprites; `motion` maps uid -> (dx, dy, flash)."""
        order = sorted(self.feet, key=lambda uid: self.feet[uid][1])
        for uid in order:
            if not self.present(state, uid):
                continue
            hp, away = state[uid][0], state[uid][3]
            fx_, fy = self.feet[uid]
            box = self.boxes[uid]
            art, grey, white = self.sprites[uid]
            if away:
                draw.ellipse(
                    (fx_ - box * 0.4, fy - 10, fx_ + box * 0.4, fy + 10),
                    outline=(*DIM, 160),
                    width=3,
                )
                self.text(draw, "离场", (fx_, fy - box * 0.45), 26, (*DIM, 220), True, stroke=3)
                continue
            down = hp <= 0
            dx, dy, flash = motion.get(uid, (0, 0, 0))
            bob = 0 if down else 4 * math.sin(clock * 3.2 + uid * 1.7)
            shadow_w = box * (0.36 if down else 0.42) * (1 - bob / 80)
            draw.ellipse(
                (fx_ - shadow_w + dx, fy - 9, fx_ + shadow_w + dx, fy + 11), fill=(0, 0, 0, 95)
            )
            sprite = grey if down else art
            x = int(fx_ - sprite.width / 2 + dx)
            y = int(fy - sprite.height + dy - bob + (14 if down else 0))
            frame.paste(sprite, (x, y), sprite)
            if flash > 0 and not down:
                mask = white.getchannel("A").point(lambda a, k=flash * 0.85: a * k)
                frame.paste((255, 255, 255), (x, y), mask)

    def hud(self, draw, state, trail):
        boss = self.boss
        uid = boss["uid"]
        draw.rounded_rectangle((16, 62, W - 16, 120), radius=16, fill=(*PANEL, 175))
        hp, shield = state[uid][0], state[uid][1]
        self.text(
            draw,
            self.fit(draw, boss["label"], 24, 420, True),
            (32, 80),
            24,
            INK,
            True,
            stroke=2,
            anchor="lm",
        )
        self.text(draw, f"{hp}/{boss['max_hp']}", (W - 32, 80), 18, DIM, True, anchor="rm")
        self._bar(draw, (32, 98, W - 32, 110), hp, trail[uid][0], shield, boss["max_hp"], 6)
        if state[uid][4:] and state[uid][4] and hp > 0:
            self.badges(draw, state[uid][4], (W - 32, 140), 34, align="right")
        for unit in self.roster:
            uid = unit["uid"]
            if unit["kind"] == "boss" or not self.present(state, uid) or state[uid][3]:
                continue
            hp, shield, stunned = state[uid][:3]
            badges = state[uid][4] if len(state[uid]) > 4 else []
            fx_, fy = self.feet[uid]
            box = self.boxes[uid]
            if unit["side"] == 0:
                self._plate(draw, unit, hp, trail[uid][0], shield)
            else:
                self._bar(
                    draw,
                    (fx_ - 52, fy + 14, fx_ + 52, fy + 22),
                    hp,
                    trail[uid][0],
                    shield,
                    unit["max_hp"],
                    4,
                )
            head = fy - box - 8
            if stunned and hp > 0:
                fx.dizzy(draw, (fx_, head + 40), 0.25)
            if badges and hp > 0:
                self.badges(draw, badges, (fx_, head - 18), 30, align="center")

    def _plate(self, draw, unit, hp, trail_hp, shield):
        fx_, _ = self.feet[unit["uid"]]
        half = self.plate_width / 2
        x0, y0, x1, y1 = fx_ - half, PLATE_TOP, fx_ + half, PLATE_TOP + PLATE_H
        draw.rounded_rectangle(
            (x0, y0, x1, y1), radius=14, fill=(*PANEL, 185), outline=(255, 255, 255, 40), width=2
        )
        owner, _, pig = unit["label"].rpartition("的")
        inner = self.plate_width - 16
        self.text(draw, self.fit(draw, owner or "援军", 15, inner), (fx_, y0 + 16), 15, DIM)
        self.text(
            draw,
            self.fit(draw, pig or unit["label"], 19, inner, True),
            (fx_, y0 + 40),
            19,
            INK,
            True,
        )
        self._bar(draw, (x0 + 8, y0 + 60, x1 - 8, y0 + 70), hp, trail_hp, shield, unit["max_hp"], 5)
        self.text(draw, f"{hp}/{unit['max_hp']}", (fx_, y0 + 83), 13, DIM)

    def _bar(self, draw, box, hp, trail_hp, shield, max_hp, radius):
        x0, y0, x1, y1 = box
        span = x1 - x0
        draw.rounded_rectangle(box, radius=radius, fill=(*TRACK, 230))
        ratio = max(0.0, min(1.0, hp / max_hp)) if max_hp else 0
        trail_ratio = max(ratio, min(1.0, trail_hp / max_hp)) if max_hp else 0
        if trail_ratio > ratio:
            draw.rounded_rectangle(
                (x0, y0, x0 + span * trail_ratio, y1), radius=radius, fill=(*TRAIL, 235)
            )
        if ratio > 0:
            draw.rounded_rectangle(
                (x0, y0, x0 + max(radius * 2, span * ratio), y1), radius=radius, fill=(*HP, 255)
            )
            draw.line(
                (x0 + radius, y0 + 2, x0 + max(radius * 2, span * ratio) - radius, y0 + 2),
                fill=(255, 190, 200, 160),
                width=2,
            )
        if max_hp and shield > 0:
            k = min(1.0, shield / max_hp)
            draw.rounded_rectangle(
                (x0, y0 - 5, x0 + max(6, span * k), y0 - 1), radius=2, fill=(120, 200, 255, 255)
            )

    def badges(self, draw, badges, anchor, size, align="center"):
        """Status icons: dots, debuffs and controls in red/purple, buffs in green."""
        gap = 4
        widths = [max(size, self.typeset.length(draw, g, size - 12, True) + 14) for g, _ in badges]
        total = sum(widths) + gap * (len(widths) - 1)
        x, y = anchor
        x = x - total if align == "right" else x - total / 2
        for (glyph, kind), width in zip(badges, widths):
            fill = BADGE_COLORS.get(kind, BADGE_COLORS["bad"])
            draw.rounded_rectangle(
                (x, y - size / 2, x + width, y + size / 2),
                radius=size // 2,
                fill=(*fill, 245),
                outline=(255, 255, 255, 230),
                width=2,
            )
            self.text(draw, glyph, (x + width / 2, y + 1), size - 12, INK, True)
            x += width + gap


def _opaque(art: Image.Image) -> bool:
    """Square art with its own background rather than a cut-out pig."""
    solid = art.getchannel("A").point(lambda a: 255 if a > 200 else 0).histogram()[255]
    return solid > 0.9 * art.width * art.height


def _card(art: Image.Image) -> Image.Image:
    """Frame square art as a rounded portrait card with a white rim and a soft glow."""
    pad = 10
    w, h = art.width + pad * 2, art.height + pad * 2
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    halo = Image.new("L", (w, h), 0)
    ImageDraw.Draw(halo).rounded_rectangle((2, 2, w - 3, h - 3), radius=26, fill=130)
    card.paste((255, 240, 220, 255), (0, 0), halo.filter(ImageFilter.GaussianBlur(5)))
    ImageDraw.Draw(card).rounded_rectangle(
        (pad - 5, pad - 5, w - pad + 4, h - pad + 4), radius=22, fill=(255, 255, 255, 255)
    )
    mask = Image.new("L", art.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, art.width - 1, art.height - 1), radius=18, fill=255
    )
    card.paste(art, (pad, pad), mask)
    return card


def _blend(previous: list, current: list, k: float) -> list:
    blended = []
    for index, now in enumerate(current):
        before = previous[index] if index < len(previous) else now
        blended.append(
            [
                round(before[0] + (now[0] - before[0]) * k),
                round(before[1] + (now[1] - before[1]) * k),
                now[2],
                now[3],
                now[4] if len(now) > 4 else [],
            ]
        )
    return blended


def _emphasised(step: dict) -> bool:
    return any(
        c["t"] in ("fall", "revive") or (c["t"] == "hit" and c.get("crit")) for c in step["cues"]
    )


def _mechanic_step(step: dict) -> bool:
    return any(c["t"] == "mech" for c in step["cues"])


def plan(timeline: list, seconds: float) -> list[float]:
    """Seconds for each step so the whole video fits in `seconds`."""
    rounds = len({step["round"] for step in timeline if step["round"]})
    budget = seconds - INTRO - OUTRO - ROUND_CARD * rounds
    loud = sum(1 for step in timeline if _emphasised(step))
    mechanics = sum(1 for step in timeline if _mechanic_step(step))
    count = max(1, len(timeline))
    # Boss mechanics keep their cut-in longest; plain emphasis is the first thing dropped.
    extra, cutin = EMPHASIS, MECH_CUTIN
    base = (budget - EMPHASIS * loud - MECH_CUTIN * mechanics) / count
    if base < STEP_MIN:
        extra = 0.0
        base = (budget - MECH_CUTIN * mechanics) / count
    if base < STEP_MIN:
        cutin = MECH_CUTIN / 2
        base = (budget - cutin * mechanics) / count
    base = max(STEP_MIN, min(STEP_MAX, base))
    return [
        base + (cutin if _mechanic_step(step) else extra if _emphasised(step) else 0.0)
        for step in timeline
    ]


def frames(root: Path, data: dict, seconds: float = 60, fps: int = 15):
    stage = _Stage(root, data)
    clock = _Clock(fps)
    timeline = data["timeline"]
    durations = plan(timeline, seconds)
    start = timeline[0]["state"]
    yield from _intro(stage, start, clock)
    previous, last_round = start, 0
    for step, duration in zip(timeline, durations):
        if step["round"] and step["round"] != last_round:
            last_round = step["round"]
            yield from _round_card(stage, previous, clock, last_round)
        yield from _step(stage, step, previous, duration, clock)
        previous = step["state"]
    yield from _outro(stage, previous, clock)


# ----- composition -----


def _compose(
    stage: _Stage, state, trail, motion, clock: _Clock, effects=None, overlay=None, shake=(0, 0)
):
    """One finished frame: backdrop, units, HUD, bloomed effects layer, then overlays."""
    frame = stage.background.copy()
    draw = ImageDraw.Draw(frame, "RGBA")
    stage.units(frame, draw, state, motion, clock.seconds)
    stage.hud(draw, state, trail)
    if effects is not None:
        layer = Image.new("RGBA", SIZE, (0, 0, 0, 0))
        effects(ImageDraw.Draw(layer, "RGBA"), layer)
        if layer.getbbox():
            small = layer.resize((W // 3, H // 3), Image.Resampling.BILINEAR)
            bloom = small.filter(ImageFilter.GaussianBlur(5)).resize(
                SIZE, Image.Resampling.BILINEAR
            )
            frame.paste(bloom, (0, 0), bloom)
            frame.paste(layer, (0, 0), layer)
    if overlay is not None:
        overlay(ImageDraw.Draw(frame, "RGBA"), frame)
    clock.frame += 1
    if shake != (0, 0):
        frame = ImageChops.offset(frame, *shake)
    return frame


def _intro(stage, state, clock):
    data = stage.data
    count = max(1, round(INTRO * clock.fps))
    for index in range(count):
        t = index / max(1, count - 1)

        def overlay(draw, frame, t=t):
            fade_in = max(0.0, 1 - t * 4)
            if fade_in:
                draw.rectangle((0, 0, W, H), fill=(0, 0, 0, int(255 * fade_in)))
            slide = fx.ease(min(1.0, t * 2.2))
            y = 300
            draw.polygon(
                (
                    (-40 + 800 * (1 - slide), y - 70),
                    (W + 40, y - 90),
                    (W + 40, y + 60),
                    (-40 + 800 * (1 - slide), y + 80),
                ),
                fill=(*PANEL, 215),
            )
            draw.line((0, y - 74, W, y - 94), fill=(255, 210, 120, 220), width=4)
            draw.line((0, y + 84, W, y + 64), fill=(255, 210, 120, 220), width=4)
            stage.text(
                draw,
                data["dungeon"],
                (W / 2 + 80 * (1 - slide), y - 22),
                58,
                (255, 236, 200, 255),
                True,
                stroke=4,
            )
            stage.text(
                draw,
                f"第 {data['stage']}/{data['stages']} 关 · BOSS {data['boss_name']}",
                (W / 2, y + 36),
                26,
                INK,
                True,
                stroke=3,
            )
            for row, event in enumerate(data["events"]):
                k = fx.ease(min(1.0, max(0.0, t * 2.5 - 0.6 - row * 0.2)))
                if k <= 0:
                    continue
                width = min(W - 80, stage.typeset.length(draw, event, 20) + 40)
                x0 = W / 2 - width / 2
                yy = 880 + row * 52 + 30 * (1 - k)
                draw.rounded_rectangle(
                    (x0, yy - 20, x0 + width, yy + 20),
                    radius=20,
                    fill=(*PANEL, int(200 * k)),
                    outline=(255, 210, 120, int(200 * k)),
                    width=2,
                )
                stage.text(
                    draw,
                    stage.fit(draw, event, 20, width - 30),
                    (W / 2, yy),
                    20,
                    (255, 236, 200, int(255 * k)),
                )

        yield _compose(stage, state, state, {}, clock, overlay=overlay)


def _round_card(stage, state, clock, number):
    count = max(1, round(ROUND_CARD * clock.fps))
    for index in range(count):
        t = index / max(1, count - 1)

        def overlay(draw, frame, t=t):
            sweep = fx.ease(min(1.0, t * 2.5))
            alpha = min(1.0, (1 - t) * 4)
            y = 820
            x = -W + 2 * W * sweep
            draw.polygon(
                (
                    (x - 120, y - 46),
                    (x + W + 120, y - 60),
                    (x + W + 120, y + 46),
                    (x - 120, y + 60),
                ),
                fill=(*PANEL, int(210 * alpha)),
            )
            stage.text(
                draw,
                f"第 {number} 回合",
                (W / 2, y),
                46,
                (255, 236, 200, int(255 * alpha)),
                True,
                stroke=4,
            )

        yield _compose(stage, state, state, {}, clock, overlay=overlay)


def _outro(stage, state, clock):
    data = stage.data
    if data["won"]:
        head, color = "胜 利", (255, 214, 110)
        sub = f"击败了 {data['boss_name']}"
    elif data["timeout"]:
        head, color = "撤 出", (200, 200, 215)
        sub = f"{data['rounds']} 回合没能打倒 boss"
    else:
        head, color = "团 灭", (230, 90, 100)
        sub = f"{data['boss_name']} 获胜"
    count = max(1, round(OUTRO * clock.fps))
    best = max((dealt for _, dealt in data["top"]), default=1) or 1
    for index in range(count):
        t = index / max(1, count - 1)

        def overlay(draw, frame, t=t):
            veil = min(1.0, t * 3)
            draw.rectangle((0, 0, W, H), fill=(8, 6, 16, int(170 * veil)))
            pop = 1.5 - 0.5 * fx.ease(min(1.0, t * 3))
            stage.text(
                draw, head, (W / 2, 420), int(110 * pop), (*color, int(255 * veil)), True, stroke=6
            )
            stage.text(draw, sub, (W / 2, 520), 28, (*INK, int(255 * veil)), True, stroke=3)
            if data["top"]:
                stage.text(
                    draw, "伤害排行", (W / 2, 620), 26, (255, 236, 200, int(255 * veil)), True
                )
            for row, (label, dealt) in enumerate(data["top"]):
                k = fx.ease(min(1.0, max(0.0, t * 2.2 - 0.3 - row * 0.15)))
                y = 690 + row * 78
                draw.rounded_rectangle(
                    (70, y - 30, W - 70, y + 30), radius=18, fill=(*PANEL, int(210 * k))
                )
                stage.text(draw, f"{row + 1}", (104, y), 30, (*color, int(255 * k)), True)
                stage.text(
                    draw,
                    stage.fit(draw, label, 22, 300),
                    (140, y - 8),
                    22,
                    (*INK, int(255 * k)),
                    True,
                    anchor="lm",
                )
                bar = (140, y + 12, 140 + 380 * k * dealt / best, y + 20)
                draw.rounded_rectangle(bar, radius=4, fill=(*color, int(255 * k)))
                stage.text(
                    draw, str(dealt), (W - 92, y), 24, (*INK, int(255 * k)), True, anchor="rm"
                )

        yield _compose(stage, state, state, {}, clock, overlay=overlay)


def _step(stage, step, previous, duration, clock):
    cues = step["cues"]
    count = max(3, round(duration * clock.fps))
    skill = next((c for c in cues if c["t"] == "skill"), None)
    element, color = (
        fx.element_of(skill["name"], skill.get("text", ""), skill.get("style", ""))
        if skill
        else ("physical", fx.GOLD)
    )
    mech = next((c for c in cues if c["t"] == "mech"), None)
    scene = scenes.scene_for(stage.data["boss_slot"], mech["index"]) if mech else None
    # A mechanic step spends its first part on the cut-in; everything else waits for it.
    impact_at = CUTIN_SPLIT + 0.08 if scene else IMPACT_AT
    heavy = any(c["t"] == "hit" and c.get("crit") for c in cues) or bool(scene and scene.heavy)
    hit_targets = {c["dst"] for c in cues if c["t"] == "hit" and c.get("amt")}
    rng = random.Random(len(step["text"]) + step["round"])
    for index in range(count):
        t = index / max(1, count - 1)
        impact_t = max(0.0, (t - impact_at) / (1 - impact_at))
        state = _blend(previous, step["state"], min(1.0, impact_t / 0.45))
        trail = _blend(previous, step["state"], max(0.0, (impact_t - 0.55) / 0.45))
        motion = _motion(stage, skill, hit_targets, t, impact_t)

        def effects(draw, layer, t=t, impact_t=impact_t, state=state):
            if scene and t >= CUTIN_SPLIT:
                _scene(stage, draw, scene, state, (t - CUTIN_SPLIT) / (1 - CUTIN_SPLIT), mech)
            if not scene or t >= CUTIN_SPLIT:
                for cue in cues:
                    _cue(stage, draw, cue, t, impact_t, element, color, skill, impact_at)
            if skill and not scene:
                _banner(stage, draw, skill["name"], color, t)

        def overlay(draw, frame, t=t):
            _caption(stage, draw, step["text"])
            if scene and t < CUTIN_SPLIT:
                _cutin(stage, frame, draw, mech, scene, t / CUTIN_SPLIT)

        shake = (0, 0)
        if heavy and impact_at <= t <= impact_at + 0.3:
            shake = (rng.randint(-10, 10), rng.randint(-8, 8))
        yield _compose(stage, state, trail, motion, clock, effects, overlay, shake)


def _motion(stage, skill, hit_targets, t, impact_t) -> dict:
    motion = {}
    if (
        skill
        and skill["src"] in stage.feet
        and skill["dst"] in stage.centers
        and skill["src"] != skill["dst"]
    ):
        sx, sy = stage.feet[skill["src"]]
        tx, ty = stage.centers[skill["dst"]]
        length = math.hypot(tx - sx, ty - sy) or 1
        reach = 48 * fx.pulse(min(1.0, t / (IMPACT_AT * 1.25)))
        motion[skill["src"]] = ((tx - sx) / length * reach, (ty - sy) / length * reach * 0.6, 0)
    if 0 < impact_t < 0.45:
        for uid in hit_targets:
            dx = 8 * math.sin(impact_t * 70)
            motion[uid] = (dx, 0, 1 - impact_t / 0.45)
    return motion


def _caption(stage, draw, text):
    draw.rectangle((0, CAPTION_TOP, W, H), fill=(*PANEL, 120))
    stage.text(draw, stage.fit(draw, text, 20, W - 48), (W / 2, CAPTION_TOP + 34), 20, (*INK, 235))


def _banner(stage, draw, name, color, t):
    alpha = min(1.0, t * 6, (1.15 - t) * 4)
    if alpha <= 0:
        return
    slide = fx.ease(min(1.0, t * 5))
    width = min(W - 60, stage.typeset.length(draw, name, 34, True) + 110)
    cx = W / 2 + 60 * (1 - slide)
    x0, x1 = cx - width / 2, cx + width / 2
    y = BANNER_Y
    draw.polygon(
        ((x0 - 18, y + 30), (x0 + 18, y - 30), (x1 + 18, y - 30), (x1 - 18, y + 30)),
        fill=fx.fade(color, alpha * 0.9),
    )
    draw.polygon(
        ((x0 - 6, y + 22), (x0 + 22, y - 22), (x0 + 40, y - 22), (x0 + 12, y + 22)),
        fill=fx.fade(fx.WHITE, alpha * 0.55),
    )
    stage.text(
        draw,
        stage.fit(draw, name, 34, width - 60, True),
        (cx, y),
        34,
        fx.fade(fx.WHITE, alpha),
        True,
        stroke=4,
    )


def _cutin(stage, frame, draw, cue, scene, t):
    """Veil, a band in the mechanic's colour, the boss sliding in and the mechanic's name."""
    color = scene.color
    veil = min(1.0, t * 4) * (1.0 if t < 0.85 else (1 - t) * 6.6)
    draw.rectangle((0, 0, W, H), fill=(10, 6, 18, int(165 * veil)))
    slide = fx.ease(min(1.0, t * 2.2))
    top, bottom = 470, 780
    edge = -80 + (W + 160) * (1 - slide)
    draw.polygon(
        ((edge, top + 40), (W + 80, top), (W + 80, bottom - 40), (edge, bottom)),
        fill=fx.fade(color, 0.93 * veil),
    )
    for offset in (-26, 26):
        draw.line(
            (0, top + 62 + offset, W, top + 22 + offset),
            fill=fx.fade(fx.WHITE, 0.4 * veil),
            width=3,
        )
    art = stage.sprites[stage.boss["uid"]][0]
    portrait = art.resize((art.width * 3 // 4, art.height * 3 // 4))
    x = int(-portrait.width + (portrait.width + 30) * slide)
    frame.paste(portrait, (max(-portrait.width + 1, x), top + 40), portrait)
    name_x = W / 2 + 110 - 60 * (1 - slide)
    stage.text(draw, cue["name"], (name_x, top + 112), 58, fx.fade(fx.WHITE, veil), True, stroke=5)
    description = stage.data.get("mechanics", {}).get(cue["index"], "")
    for row, line in enumerate(_wrap(stage, draw, description, 21, 400)[:3]):
        stage.text(
            draw, line, (name_x, top + 178 + row * 30), 21, fx.fade(fx.WHITE, veil * 0.95), stroke=2
        )


def _wrap(stage, draw, text, size, width):
    lines, line = [], ""
    for char in text:
        if line and stage.typeset.length(draw, line + char, size) > width:
            lines.append(line)
            line = ""
        line += char
    return lines + [line] if line else lines


def _scene(stage, draw, scene, state, t, mech=None):
    team = [
        stage.centers[u["uid"]]
        for u in stage.roster
        if u["side"] == 0
        and stage.present(state, u["uid"])
        and state[u["uid"]][0] > 0
        and not state[u["uid"]][3]
    ]
    mech = mech or {}
    targets = [stage.centers[uid] for uid in mech.get("targets", ()) if uid in stage.centers]
    ctx = scenes.Context(
        stage.boss_center,
        team,
        SIZE,
        lambda *a: stage.text(draw, *a),
        targets,
        mech.get("tag", ""),
    )
    draw.rectangle((0, 0, W, H), fill=fx.fade(scene.color, 0.14 * fx.pulse(t)))
    scene.play(draw, t, ctx)


def _cue(stage, draw, cue, t, impact_t, element, color, skill, impact_at=IMPACT_AT):
    kind = cue["t"]
    dst = stage.centers.get(cue.get("dst"))
    src = stage.centers.get(cue.get("src"))
    seed = (cue.get("dst") or 0) * 31 + (cue.get("src") or 0)
    if kind == "skill" and src:
        fx.glow(draw, src, min(1.0, t / IMPACT_AT), color, 90)
        if dst and dst != src and t <= IMPACT_AT:
            fx.projectile(draw, src, dst, t / IMPACT_AT, color, 18)
        return
    if dst is None or t < impact_at:
        return
    if kind == "hit":
        crit = cue.get("crit")
        if cue.get("dot"):
            fx.bubbles(draw, dst, impact_t, fx.PURPLE, seed)
            number_color = (210, 150, 255)
        elif skill and cue.get("src") == skill["src"]:
            fx.impact(
                draw, element, dst, impact_t, color, seed, src if element == "thunder" else None
            )
            fx.burst(draw, dst, impact_t, color, 80, 10)
            number_color = fx.GOLD if crit else (255, 92, 92)
        else:
            fx.burst(draw, dst, impact_t, (255, 210, 160), 70, 8)
            number_color = (255, 92, 92)
        if crit:
            fx.flash(draw, SIZE, min(1.0, impact_t * 3), fx.WHITE, 0.3)
        label = f"暴击 {cue['amt']}" if crit else str(cue["amt"])
        _number(stage, draw, dst, impact_t, label, number_color, 46 if crit else 34, seed, crit)
    elif kind == "miss":
        fx.afterimage(draw, dst, impact_t)
        _number(stage, draw, dst, impact_t, "化解" if cue.get("void") else "MISS", DIM, 30, seed)
    elif kind == "heal":
        fx.rising(draw, dst, impact_t, fx.GREEN, seed)
        _number(stage, draw, dst, impact_t, f"+{cue['amt']}", (110, 235, 130), 32, seed)
    elif kind == "shield":
        fx.bubble(draw, dst, impact_t, (120, 200, 255))
    elif kind == "stun":
        fx.dizzy(draw, dst, impact_t)
    elif kind == "dot":
        fx.bubbles(draw, dst, impact_t, color, seed)
    elif kind == "buff":
        fx.arrows(draw, dst, impact_t, cue.get("up", True))
    elif kind == "thorns":
        fx.spikes(draw, dst, impact_t)
    elif kind == "regen":
        fx.rising(draw, dst, impact_t, fx.GREEN, seed)
    elif kind == "evade":
        fx.afterimage(draw, dst, impact_t)
    elif kind in ("cleanse", "dispel"):
        fx.shatter(draw, dst, impact_t, fx.WHITE if kind == "cleanse" else fx.PURPLE, seed)
    elif kind == "copy":
        fx.glow(draw, dst, impact_t, (120, 200, 255), 90)
    elif kind == "revive":
        fx.pillar(draw, dst, impact_t)
    elif kind == "summon":
        fx.glow(draw, dst, impact_t, fx.GOLD, 100)
        fx.rays(draw, dst, impact_t, fx.GOLD, 12, 110)
    elif kind == "fall":
        fx.stamp(
            draw,
            dst,
            impact_t,
            "倒下",
            lambda w, c, s, f, b: stage.text(draw, w, c, s, f, b, stroke=4),
            (240, 80, 90),
            46,
        )
    elif kind == "blocked":
        fx.stamp(
            draw,
            dst,
            impact_t,
            "禁",
            lambda w, c, s, f, b: stage.text(draw, w, c, s, f, b, stroke=4),
            (240, 80, 90),
            52,
        )
    elif kind == "field":
        _field(draw, cue["name"], impact_t, stage)


def _number(stage, draw, center, t, text, color, size, seed, crit=False):
    x, y = center
    jitter = (seed % 5 - 2) * 14
    rise = 80 * fx.ease(t)
    alpha = 1.0 if t < 0.7 else (1 - t) * 3.3
    pop = 1.5 - 0.5 * fx.ease(min(1.0, t * 4)) if crit else 1.15 - 0.15 * fx.ease(min(1.0, t * 5))
    stage.text(
        draw,
        text,
        (x + jitter, y - 50 - rise),
        int(size * pop),
        fx.fade(color, alpha),
        True,
        stroke=4,
    )


def _field(draw, name, t, stage):
    if name == "quake":
        fx.flash(draw, SIZE, t, (120, 90, 60), 0.25)
        for i in range(5):
            x = 80 + i * 140
            draw.line(
                (x, 1050, x + 30, 1010, x + 10, 980), fill=fx.fade((60, 40, 30), 1 - t), width=5
            )
    elif name == "meteor":
        boss = stage.boss_center
        scenes.meteor(draw, (boss[0] + 260, -120), boss, fx.ease(min(1.0, t * 1.5)))
    elif name == "lightning":
        fx.bolt(draw, (W / 2, 0), stage.boss_center, t, fx.GOLD)
        fx.flash(draw, SIZE, t, (255, 250, 200), 0.3)
    elif name == "rainbow":
        for i, color in enumerate(
            ((255, 90, 90), (255, 190, 80), (120, 210, 120), (100, 170, 255), (180, 120, 230))
        ):
            r = 420 - i * 18
            draw.arc(
                (W / 2 - r, 820 - r, W / 2 + r, 820 + r),
                200,
                340,
                fill=fx.fade(color, fx.pulse(t) * 0.8),
                width=16,
            )
    elif name == "wind":
        for i in range(8):
            y = 300 + i * 100
            x = -200 + (W + 400) * fx.ease(t)
            draw.line((x - 180, y, x, y), fill=fx.fade(fx.WHITE, 0.7), width=5)
    elif name == "cheer":
        fx.stars(draw, (W / 2, 820), t, fx.GOLD, count=22, spread=340)
    else:
        fx.stars(draw, (W / 2, 820), t, (255, 240, 200), count=12, spread=220)
