"""Procedural effects for the raid replay video.

Every function draws one moment of an effect onto an `ImageDraw.Draw(frame, "RGBA")`.
`t` runs from 0 to 1 over the effect; positions are pixel centres on the 720x1280
frame. Nothing here touches battle state.
"""

import math
import random

WHITE = (255, 255, 255)
GOLD = (255, 205, 80)
RED = (235, 80, 80)
GREEN = (110, 210, 120)
BLUE = (110, 180, 255)
PURPLE = (175, 115, 235)

# Checked in order against a skill's name, then its description.
ELEMENTS = (
    ("fire", ("火", "焰", "烧", "烤", "炎", "爆", "辣", "热", "燃"), (255, 140, 40)),
    ("ice", ("冰", "冻", "雪", "寒", "霜", "冷"), (120, 220, 255)),
    ("thunder", ("雷", "电", "闪电", "霹雳"), (255, 230, 70)),
    ("poison", ("毒", "腐", "尸", "臭", "邋遢", "瘴"), (130, 210, 80)),
    ("holy", ("光", "神", "圣", "佛", "天使", "祝福", "金"), (255, 215, 110)),
    ("water", ("水", "海", "浪", "潮", "鱼", "深渊", "泡", "暗流"), (70, 150, 255)),
    ("shadow", ("影", "鬼", "魂", "暗", "幽", "诅咒", "魔", "恶"), (175, 115, 235)),
    ("star", ("星", "宇宙", "陨", "月", "银河"), (205, 185, 255)),
    ("metal", ("机械", "钢", "铁", "炮", "枪", "锯", "齿轮"), (190, 200, 215)),
)
# Skills with no keyword take a stable colour from their pig's style.
STYLE_COLORS = (
    (240, 120, 120),
    (240, 170, 90),
    (120, 200, 160),
    (110, 170, 240),
    (200, 130, 220),
    (230, 200, 90),
)


def element_of(name: str, text: str = "", style: str = "") -> tuple[str, tuple]:
    for source in (name, text):
        for key, words, color in ELEMENTS:
            if any(word in source for word in words):
                return key, color
    index = sum(map(ord, style or name)) % len(STYLE_COLORS)
    return "physical", STYLE_COLORS[index]


def fade(color, alpha: float) -> tuple:
    return (*color[:3], max(0, min(255, int(alpha * 255))))


def luminance(color) -> float:
    r, g, b = color[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def ease(t: float) -> float:
    return 1 - (1 - t) ** 3


def pulse(t: float) -> float:
    """0 -> 1 -> 0 over the effect."""
    return math.sin(math.pi * max(0.0, min(1.0, t)))


# ----- impact shapes, one per element -----


def burst(draw, center, t, color, radius=70, rays=10):
    x, y = center
    r = radius * ease(t)
    alpha = 1 - t
    draw.ellipse(
        (x - r * 0.5, y - r * 0.5, x + r * 0.5, y + r * 0.5), fill=fade(WHITE, alpha * 0.6)
    )
    for i in range(rays):
        angle = 2 * math.pi * i / rays + 0.3
        inner, outer = r * 0.4, r
        draw.line(
            (
                x + inner * math.cos(angle),
                y + inner * math.sin(angle),
                x + outer * math.cos(angle),
                y + outer * math.sin(angle),
            ),
            fill=fade(color, alpha),
            width=5,
        )


def slash(draw, center, t, color, size=80):
    x, y = center
    reach = size * ease(min(1.0, t * 1.6))
    alpha = 1 - t
    for offset in (-14, 0, 14):
        draw.line(
            (x - reach + offset, y - reach, x + reach + offset, y + reach),
            fill=fade(color, alpha),
            width=6 if offset == 0 else 3,
        )


def flame(draw, center, t, color, seed=0):
    rng = random.Random(seed)
    x, y = center
    for _ in range(9):
        dx, height = rng.uniform(-50, 50), rng.uniform(40, 90)
        rise = height * ease(t)
        width = 16 * (1 - t) + 4
        base = y + 30 - rise * 0.3
        draw.polygon(
            ((x + dx - width, base), (x + dx + width, base), (x + dx, base - rise)),
            fill=fade(color if rng.random() < 0.6 else GOLD, 1 - t),
        )


def shards(draw, center, t, color, seed=0):
    rng = random.Random(seed)
    x, y = center
    for _ in range(8):
        angle = rng.uniform(0, 2 * math.pi)
        dist = 20 + 70 * ease(t)
        cx, cy = x + dist * math.cos(angle), y + dist * math.sin(angle)
        size = 14 * (1 - t * 0.5)
        draw.polygon(
            ((cx, cy - size), (cx + size * 0.5, cy), (cx, cy + size), (cx - size * 0.5, cy)),
            fill=fade(color, 1 - t),
            outline=fade(WHITE, 1 - t),
        )


def bolt(draw, start, end, t, color, seed=0):
    rng = random.Random(seed + int(t * 6))
    (x0, y0), (x1, y1) = start, end
    points = [(x0, y0)]
    for i in range(1, 7):
        k = i / 7
        points.append((x0 + (x1 - x0) * k + rng.uniform(-22, 22), y0 + (y1 - y0) * k))
    points.append((x1, y1))
    alpha = pulse(t) if t < 0.8 else (1 - t) * 3
    draw.line(points, fill=fade(WHITE, alpha), width=7)
    draw.line(points, fill=fade(color, alpha), width=3)


def bubbles(draw, center, t, color, seed=0, count=7):
    rng = random.Random(seed)
    x, y = center
    for _ in range(count):
        dx, rise, r = rng.uniform(-45, 45), rng.uniform(30, 80), rng.uniform(6, 14)
        cy = y + 20 - rise * ease(t)
        draw.ellipse((x + dx - r, cy - r, x + dx + r, cy + r), outline=fade(color, 1 - t), width=3)


def rays(draw, center, t, color, count=12, length=110):
    x, y = center
    alpha = pulse(t)
    spin = t * 0.6
    for i in range(count):
        angle = 2 * math.pi * i / count + spin
        draw.line(
            (x, y, x + length * math.cos(angle), y + length * math.sin(angle)),
            fill=fade(color, alpha * 0.8),
            width=4,
        )
    r = 26 * alpha
    draw.ellipse((x - r, y - r, x + r, y + r), fill=fade(WHITE, alpha * 0.8))


def smoke(draw, center, t, color, seed=0):
    rng = random.Random(seed)
    x, y = center
    for _ in range(6):
        dx, dy = rng.uniform(-50, 50), rng.uniform(-30, 30)
        r = 18 + 30 * ease(t)
        draw.ellipse(
            (x + dx - r, y + dy - r, x + dx + r, y + dy + r), fill=fade(color, (1 - t) * 0.35)
        )


def waves(draw, center, t, color):
    x, y = center
    for i in range(3):
        k = (t + i * 0.25) % 1.0
        r = 20 + 90 * k
        draw.arc(
            (x - r, y - r * 0.6, x + r, y + r * 0.6), 200, 340, fill=fade(color, 1 - k), width=4
        )


def stars(draw, center, t, color, seed=0, count=8, spread=80):
    rng = random.Random(seed)
    x, y = center
    for _ in range(count):
        angle, dist = rng.uniform(0, 2 * math.pi), spread * ease(t) * rng.uniform(0.5, 1)
        star(draw, (x + dist * math.cos(angle), y + dist * math.sin(angle)), 9, fade(color, 1 - t))


def star(draw, center, size, fill):
    x, y = center
    points = []
    for i in range(10):
        r = size if i % 2 == 0 else size * 0.45
        angle = math.pi / 2 + i * math.pi / 5
        points.append((x + r * math.cos(angle), y - r * math.sin(angle)))
    draw.polygon(points, fill=fill)


def sparks(draw, center, t, color, seed=0, count=12):
    rng = random.Random(seed)
    x, y = center
    for _ in range(count):
        angle = rng.uniform(0, 2 * math.pi)
        dist = 90 * ease(t) * rng.uniform(0.5, 1)
        cx, cy = x + dist * math.cos(angle), y + dist * math.sin(angle)
        tail = 12 * (1 - t)
        draw.line(
            (cx, cy, cx - tail * math.cos(angle), cy - tail * math.sin(angle)),
            fill=fade(color, 1 - t),
            width=3,
        )


IMPACTS = {
    "fire": flame,
    "ice": shards,
    "poison": bubbles,
    "holy": lambda d, c, t, col, seed=0: rays(d, c, t, col, length=80),
    "shadow": smoke,
    "water": lambda d, c, t, col, seed=0: waves(d, c, t, col),
    "star": stars,
    "metal": sparks,
    "physical": lambda d, c, t, col, seed=0: slash(d, c, t, col),
}


def impact(draw, element, center, t, color, seed=0, source=None):
    if element == "thunder":
        top = (center[0] + 30, center[1] - 160) if source is None else source
        bolt(draw, top, center, t, color, seed)
        burst(draw, center, t, color, 50, 6)
        return
    IMPACTS[element](draw, center, t, color, seed=seed)


def projectile(draw, start, end, t, color, size=14):
    """A glowing orb travelling from `start` to `end`."""
    (x0, y0), (x1, y1) = start, end
    k = ease(t)
    x, y = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
    for i in range(4):
        trail = max(0.0, k - i * 0.06)
        tx, ty = x0 + (x1 - x0) * trail, y0 + (y1 - y0) * trail
        r = size * (1 - i * 0.2)
        draw.ellipse((tx - r, ty - r, tx + r, ty + r), fill=fade(color, 0.7 - i * 0.15))
    draw.ellipse((x - size * 0.5, y - size * 0.5, x + size * 0.5, y + size * 0.5), fill=WHITE)


# ----- status effects -----


def rising(draw, center, t, color, seed=0):
    """Healing: sparkles drifting upward."""
    rng = random.Random(seed)
    x, y = center
    for _ in range(10):
        dx, speed = rng.uniform(-50, 50), rng.uniform(50, 110)
        cy = y + 40 - speed * ease(t)
        draw.line((x + dx - 6, cy, x + dx + 6, cy), fill=fade(color, 1 - t), width=3)
        draw.line((x + dx, cy - 6, x + dx, cy + 6), fill=fade(color, 1 - t), width=3)


def bubble(draw, center, t, color, radius=78):
    x, y = center
    r = radius * (0.7 + 0.3 * ease(min(1.0, t * 2)))
    alpha = 0.8 if t < 0.7 else (1 - t) * 2.6
    draw.ellipse((x - r, y - r, x + r, y + r), outline=fade(color, alpha), width=5)
    draw.ellipse((x - r, y - r, x + r, y + r), fill=fade(color, alpha * 0.15))


def dizzy(draw, center, t, color=GOLD):
    x, y = center
    for i in range(3):
        angle = 2 * math.pi * (t * 1.5 + i / 3)
        star(draw, (x + 44 * math.cos(angle), y - 70 + 12 * math.sin(angle)), 11, fade(color, 0.9))


def arrows(draw, center, t, up: bool, color=None):
    color = color or (GREEN if up else PURPLE)
    x, y = center
    for i, dx in enumerate((-34, 0, 34)):
        shift = 50 * ease(t) * (-1 if up else 1)
        cy = y + shift + (i % 2) * 14
        tip = -16 if up else 16
        alpha = 1 - t
        draw.polygon(
            ((x + dx - 12, cy), (x + dx + 12, cy), (x + dx, cy + tip)), fill=fade(color, alpha)
        )
        draw.line((x + dx, cy, x + dx, cy - tip * 1.5), fill=fade(color, alpha), width=5)


def spikes(draw, center, t, color=(220, 160, 90)):
    x, y = center
    r = 70
    for i in range(12):
        angle = 2 * math.pi * i / 12
        out = r + 18 * pulse(t)
        draw.line(
            (
                x + r * math.cos(angle),
                y + r * math.sin(angle),
                x + out * math.cos(angle),
                y + out * math.sin(angle),
            ),
            fill=fade(color, 1 - t * 0.7),
            width=4,
        )


def afterimage(draw, center, t, color=BLUE):
    x, y = center
    for i in range(3):
        dx = (i + 1) * 18 * pulse(t)
        draw.rounded_rectangle(
            (x - 50 - dx, y - 50, x + 50 - dx, y + 50),
            radius=20,
            outline=fade(color, 0.5 - i * 0.15),
            width=3,
        )


def shatter(draw, center, t, color=WHITE, seed=0):
    rng = random.Random(seed)
    x, y = center
    for _ in range(10):
        angle = rng.uniform(0, 2 * math.pi)
        dist = 30 + 80 * ease(t)
        cx, cy = x + dist * math.cos(angle), y + dist * math.sin(angle)
        size = 10
        draw.polygon(
            ((cx, cy), (cx + size, cy + size * 0.4), (cx + size * 0.2, cy + size)),
            fill=fade(color, 1 - t),
        )


def pillar(draw, center, t, color=GOLD, height=1280):
    x, _ = center
    width = 60 * pulse(t)
    draw.rectangle((x - width, 0, x + width, height), fill=fade(color, pulse(t) * 0.35))
    draw.rectangle((x - width * 0.3, 0, x + width * 0.3, height), fill=fade(WHITE, pulse(t) * 0.5))


def glow(draw, center, t, color, radius=90):
    x, y = center
    for i in range(3):
        r = radius * (1 + i * 0.25) * (0.8 + 0.2 * pulse(t))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fade(color, pulse(t) * (0.18 - i * 0.05)))


def stamp(draw, center, t, text, font_draw, color=RED, size=48):
    """A big word slammed onto a unit: 倒下, 禁, MISS."""
    x, y = center
    scale = 1.6 - 0.6 * ease(min(1.0, t * 3))
    alpha = 1.0 if t < 0.7 else (1 - t) * 3.3
    font_draw(text, (x, y), int(size * scale), fade(color, alpha), True)


# ----- whole-screen effects -----


def flash(draw, size, t, color=WHITE, strength=0.55):
    draw.rectangle((0, 0, *size), fill=fade(color, (1 - t) * strength))


def meteors(draw, size, targets, t, color=(255, 160, 80)):
    for index, (x, y) in enumerate(targets):
        k = min(1.0, max(0.0, t * 1.6 - index * 0.15))
        if k <= 0:
            continue
        sx, sy = x + 220, y - 520
        cx, cy = sx + (x - sx) * ease(k), sy + (y - sy) * ease(k)
        draw.line(
            (sx + (cx - sx) * 0.4, sy + (cy - sy) * 0.4, cx, cy), fill=fade(color, 0.7), width=10
        )
        draw.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), fill=fade(GOLD, 1))
        if k >= 1:
            burst(draw, (x, y), min(1.0, (t * 1.6 - index * 0.15 - 1) * 2), color, 60, 8)


def chains(draw, center, t, color=(170, 170, 180)):
    """Chain links around the boss that snap apart."""
    x, y = center
    spread = 120 * ease(t)
    for row in (-60, 0, 60):
        for side in (-1, 1):
            for i in range(4):
                lx = x + side * (40 + i * 34 + spread * (i / 4))
                ly = y + row + side * spread * 0.2
                draw.ellipse(
                    (lx - 16, ly - 9, lx + 16, ly + 9), outline=fade(color, 1 - t * 0.8), width=4
                )


def ufo(draw, center, t, color=(150, 230, 170)):
    x, y = center
    draw.ellipse((x - 70, y - 18, x + 70, y + 18), fill=fade((150, 160, 180), 0.9))
    draw.ellipse((x - 32, y - 40, x + 32, y), fill=fade(color, 0.8))
    for i in range(5):
        lx = x - 50 + i * 25
        draw.ellipse(
            (lx - 5, y - 5, lx + 5, y + 5), fill=fade(GOLD if (i + int(t * 8)) % 2 else WHITE, 1)
        )


def yinyang(draw, center, t, radius=70):
    x, y = center
    spin = 360 * t
    box = (x - radius, y - radius, x + radius, y + radius)
    draw.pieslice(box, spin, spin + 180, fill=fade((30, 30, 40), 0.75))
    draw.pieslice(box, spin + 180, spin + 360, fill=fade(WHITE, 0.75))
    for k, fill in ((0, WHITE), (1, (30, 30, 40))):
        angle = math.radians(spin + 90 + 180 * k)
        cx, cy = x + radius * 0.5 * math.cos(angle), y + radius * 0.5 * math.sin(angle)
        draw.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill=fade(fill, 0.9))


def swap(draw, a, b, t, color=RED):
    (x0, y0), (x1, y1) = a, b
    lift = 90
    for k, (sx, sy, ex, ey) in enumerate(((x0, y0, x1, y1), (x1, y1, x0, y0))):
        p = ease(t)
        cx = sx + (ex - sx) * p
        cy = min(sy, ey) - lift * math.sin(math.pi * p) * (1 if k == 0 else 0.6)
        draw.ellipse(
            (cx - 14, cy - 14, cx + 14, cy + 14), fill=fade(color if k == 0 else PURPLE, 0.9)
        )
