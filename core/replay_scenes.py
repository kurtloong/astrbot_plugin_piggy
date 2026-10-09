"""One illustrated scene per boss mechanic for the raid replay.

Each scene draws what its mechanic is about: 召回母舰 brings a mothership down
over the boss, 锁链崩断 shatters chains, 黑客入侵 rains code across the screen.
`t` runs 0 -> 1 over the scene; positions are on the 720x1280 replay frame.
"""

import math
import random
from dataclasses import dataclass
from typing import Callable, NamedTuple

from .replay_effects import (
    BLUE,
    GOLD,
    GREEN,
    PURPLE,
    RED,
    WHITE,
    arrows,
    bubbles,
    burst,
    dizzy,
    ease,
    fade,
    flame,
    flash,
    glow,
    pulse,
    rays,
    rising,
    shatter,
    smoke,
    sparks,
    star,
)

DARK = (25, 20, 35)
GREY = (150, 155, 165)


class Context(NamedTuple):
    boss: tuple
    team: list
    size: tuple
    text: Callable  # text(word, centre, size, fill, bold, stroke)
    # Centres of the units the mechanic landed on, and the variant it fired as.
    targets: list = []
    tag: str = ""


@dataclass(frozen=True)
class Scene:
    name: str
    color: tuple
    play: Callable  # play(draw, t, ctx)
    heavy: bool = False  # shakes the screen when it lands


# ----- props -----


def ufo(draw, center, scale, t, color=(150, 240, 170)):
    x, y = center
    w, h = 130 * scale, 34 * scale
    draw.ellipse((x - w * 1.25, y + h * 0.2, x + w * 1.25, y + h * 2.2), fill=fade(color, 0.25))
    draw.ellipse(
        (x - w * 0.5, y - h * 1.6, x + w * 0.5, y + h * 0.3),
        fill=fade((190, 245, 255), 0.85),
        outline=fade(WHITE, 0.9),
        width=3,
    )
    draw.ellipse(
        (x - w, y - h * 0.5, x + w, y + h * 0.7),
        fill=fade((140, 150, 170), 1),
        outline=fade((90, 95, 110), 1),
        width=4,
    )
    draw.ellipse(
        (x - w * 0.8, y - h * 0.15, x + w * 0.8, y + h * 0.35), fill=fade((110, 118, 135), 1)
    )
    for i in range(7):
        lx = x - w * 0.75 + i * w * 0.25
        on = (i + int(t * 12)) % 2
        r = 7 * scale
        draw.ellipse(
            (lx - r, y + h * 0.1 - r, lx + r, y + h * 0.1 + r),
            fill=fade(GOLD if on else (255, 120, 120), 1),
        )


def light_beam(draw, top, bottom, width_top, width_bottom, t, color):
    (tx, ty), (bx, by) = top, bottom
    alpha = pulse(t) * 0.55 + 0.1
    draw.polygon(
        (
            (tx - width_top, ty),
            (tx + width_top, ty),
            (bx + width_bottom, by),
            (bx - width_bottom, by),
        ),
        fill=fade(color, alpha),
    )
    for i in range(4):
        k = (t * 1.5 + i / 4) % 1
        y = ty + (by - ty) * (1 - k)
        half = width_top + (width_bottom - width_top) * (1 - k)
        draw.ellipse(
            (bx - half, y - 8, bx + half, y + 8), outline=fade(WHITE, 0.6 * (1 - k)), width=3
        )


def snowflake(draw, center, size, color):
    x, y = center
    for i in range(3):
        angle = math.pi / 3 * i
        dx, dy = size * math.cos(angle), size * math.sin(angle)
        draw.line((x - dx, y - dy, x + dx, y + dy), fill=color, width=3)
    draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)


def ice_block(draw, center, size, t, crack=0.0):
    x, y = center
    half = size / 2
    draw.rounded_rectangle(
        (x - half, y - half, x + half, y + half),
        radius=18,
        fill=fade((180, 230, 255), 0.45),
        outline=fade(WHITE, 0.9),
        width=5,
    )
    draw.polygon(
        (
            (x - half + 14, y - half + 14),
            (x - half + 70, y - half + 14),
            (x - half + 14, y - half + 70),
        ),
        fill=fade(WHITE, 0.45),
    )
    if crack:
        rng = random.Random(4)
        for _ in range(6):
            px, py = x, y
            for _ in range(4):
                nx, ny = px + rng.uniform(-50, 50) * crack, py + rng.uniform(-50, 50) * crack
                draw.line((px, py, nx, ny), fill=fade(WHITE, 1), width=4)
                px, py = nx, ny


def mountain(draw, size, rise, color=(110, 130, 160)):
    w, h = size
    base = h * 0.78 + (1 - rise) * 400
    peak = base - 420
    draw.polygon(
        ((-60, base + 300), (w * 0.42, peak), (w + 60, base + 300)), fill=fade(color, 0.85)
    )
    draw.polygon(
        (
            (w * 0.29, peak + 150),
            (w * 0.42, peak),
            (w * 0.56, peak + 150),
            (w * 0.47, peak + 120),
            (w * 0.4, peak + 160),
        ),
        fill=fade(WHITE, 0.95),
    )


def window(draw, center, size, title, ctx, t):
    x, y = center
    w, h = size
    draw.rounded_rectangle(
        (x - w / 2, y - h / 2, x + w / 2, y + h / 2),
        radius=14,
        fill=fade(WHITE, 0.95),
        outline=fade(GREY, 1),
        width=3,
    )
    draw.rectangle((x - w / 2, y - h / 2, x + w / 2, y - h / 2 + 32), fill=fade((220, 225, 235), 1))
    for i, color in enumerate((RED, GOLD, GREEN)):
        draw.ellipse(
            (x - w / 2 + 12 + i * 22, y - h / 2 + 10, x - w / 2 + 26 + i * 22, y - h / 2 + 24),
            fill=color,
        )
    ctx.text(title, (x, y + 10), int(h * 0.36), fade((120, 130, 150), 1), True)


def clock(draw, center, radius, t, color):
    x, y = center
    draw.ellipse(
        (x - radius, y - radius, x + radius, y + radius),
        fill=fade(WHITE, 0.85),
        outline=fade(color, 1),
        width=6,
    )
    for i in range(12):
        angle = math.pi / 6 * i
        draw.line(
            (
                x + radius * 0.82 * math.cos(angle),
                y + radius * 0.82 * math.sin(angle),
                x + radius * 0.95 * math.cos(angle),
                y + radius * 0.95 * math.sin(angle),
            ),
            fill=fade(DARK, 0.8),
            width=3,
        )
    for length, speed, width in ((0.55, -2.0, 7), (0.8, -12.0, 4)):
        angle = -math.pi / 2 + speed * math.pi * t
        draw.line(
            (x, y, x + radius * length * math.cos(angle), y + radius * length * math.sin(angle)),
            fill=fade(DARK, 1),
            width=width,
        )
    draw.arc(
        (x - radius - 26, y - radius - 26, x + radius + 26, y + radius + 26),
        200,
        330,
        fill=fade(color, 0.9),
        width=8,
    )
    ax, ay = (
        x + (radius + 26) * math.cos(math.radians(200)),
        y + (radius + 26) * math.sin(math.radians(200)),
    )
    draw.polygon(((ax - 16, ay), (ax + 6, ay - 18), (ax + 8, ay + 12)), fill=fade(color, 0.9))


def code_rain(draw, size, t, color, ctx, seed=0):
    rng = random.Random(seed)
    w, h = size
    for column in range(14):
        x = 25 + column * 50
        speed, offset = rng.uniform(600, 1100), rng.uniform(0, h)
        head = (offset + speed * t) % (h + 300) - 150
        for k in range(7):
            ctx.text(
                rng.choice("01アイ#$%"),
                (x, head - k * 34),
                26,
                fade(color, (1 - k / 7) * 0.9),
                k == 0,
            )


def gear(draw, center, radius, angle, color, teeth=10):
    x, y = center
    points = []
    for i in range(teeth * 2):
        r = radius if i % 2 == 0 else radius * 0.78
        a = angle + math.pi * i / teeth
        points.append((x + r * math.cos(a), y + r * math.sin(a)))
    draw.polygon(points, fill=fade(color, 0.9), outline=fade(DARK, 0.6))
    draw.ellipse(
        (x - radius * 0.32, y - radius * 0.32, x + radius * 0.32, y + radius * 0.32),
        fill=fade(DARK, 0.7),
    )


def chip(draw, center, size, t, color):
    x, y = center
    half = size / 2
    for i in range(6):
        k = -half + 12 + i * (size - 24) / 5
        for dx, dy in ((k, -half - 16), (k, half + 16), (-half - 16, k), (half + 16, k)):
            draw.line(
                (x + dx * (abs(dx) <= half), y + dy * (abs(dy) <= half), x + dx, y + dy),
                fill=fade(GOLD, 1),
                width=4,
            )
    draw.rounded_rectangle(
        (x - half, y - half, x + half, y + half),
        radius=12,
        fill=fade((40, 45, 60), 1),
        outline=fade(color, 1),
        width=4,
    )
    draw.rectangle(
        (x - half * 0.5, y - half * 0.5, x + half * 0.5, y + half * 0.5),
        fill=fade(color, 0.4 + 0.6 * pulse(t * 3 % 1)),
    )


def horns(draw, center, t, color=(200, 40, 50)):
    x, y = center
    lift = 40 * ease(t)
    for side in (-1, 1):
        base = x + side * 70
        draw.polygon(
            (
                (base - 26, y - 110 - lift),
                (base + 26, y - 110 - lift),
                (base + side * 50, y - 210 - lift),
            ),
            fill=fade(color, 1),
        )
    draw.arc((x - 90, y - 40, x + 90, y + 80), 20, 160, fill=fade(DARK, 0.9), width=10)


def crown(draw, center, size, t, color=GOLD):
    x, y = center
    w, h = size, size * 0.6
    draw.polygon(
        (
            (x - w / 2, y),
            (x - w / 2, y - h),
            (x - w / 4, y - h * 0.5),
            (x, y - h * 1.15),
            (x + w / 4, y - h * 0.5),
            (x + w / 2, y - h),
            (x + w / 2, y),
        ),
        fill=fade(color, 1),
        outline=fade((150, 100, 30), 1),
        width=4,
    )
    for dx, gem in ((-w / 4, RED), (0, BLUE), (w / 4, GREEN)):
        draw.ellipse((x + dx - 9, y - h * 0.35 - 9, x + dx + 9, y - h * 0.35 + 9), fill=gem)


def chain(draw, start, end, t, broken=0.0, color=(175, 175, 185)):
    (x0, y0), (x1, y1) = start, end
    links = 9
    for i in range(links):
        k = i / (links - 1)
        x, y = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
        drift = (k - 0.5) * 2 * broken * 60
        y += abs(drift) * 0.8
        x += drift
        vertical = i % 2
        rx, ry = (10, 18) if vertical else (18, 10)
        draw.ellipse(
            (x - rx, y - ry, x + rx, y + ry), outline=fade(color, 1 - broken * 0.6), width=5
        )


def clover(draw, center, size, color=(80, 190, 90)):
    x, y = center
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        draw.ellipse(
            (
                x + dx * size * 0.5 - size * 0.5,
                y + dy * size * 0.5 - size * 0.5,
                x + dx * size * 0.5 + size * 0.5,
                y + dy * size * 0.5 + size * 0.5,
            ),
            fill=fade(color, 1),
        )
    draw.line((x, y, x + size * 0.4, y + size * 1.6), fill=fade((60, 140, 60), 1), width=6)


def coin(draw, center, size, angle):
    x, y = center
    squash = abs(math.cos(angle))
    draw.ellipse(
        (x - size * squash, y - size, x + size * squash, y + size),
        fill=fade(GOLD, 1),
        outline=fade((170, 120, 30), 1),
        width=3,
    )


def eye(draw, center, width, t, color):
    x, y = center
    open_k = min(1.0, t * 3)
    h = width * 0.42 * open_k
    draw.ellipse(
        (x - width, y - h, x + width, y + h),
        fill=fade(WHITE, 0.95),
        outline=fade(color, 1),
        width=6,
    )
    r = width * 0.32 * open_k
    draw.ellipse((x - r, y - r, x + r, y + r), fill=fade(color, 1))
    draw.ellipse((x - r * 0.45, y - r * 0.45, x + r * 0.45, y + r * 0.45), fill=fade(DARK, 1))


def ghost(draw, center, size, t, color=(235, 240, 255)):
    x, y = center
    bob = 10 * math.sin(t * 8)
    y += bob
    draw.ellipse((x - size, y - size, x + size, y + size * 0.6), fill=fade(color, 0.85))
    points = [(x - size, y)]
    for i in range(7):
        px = x - size + i * size / 3
        points.append((px, y + size * 1.3 + (8 if i % 2 else -8) * math.sin(t * 10)))
    points.append((x + size, y))
    draw.polygon(points, fill=fade(color, 0.85))
    for dx in (-size * 0.35, size * 0.35):
        draw.ellipse((x + dx - 7, y - 12, x + dx + 7, y + 6), fill=fade(DARK, 0.9))


def skull(draw, center, size, color):
    x, y = center
    draw.ellipse((x - size, y - size, x + size, y + size * 0.8), fill=fade(color, 0.95))
    draw.rectangle(
        (x - size * 0.55, y + size * 0.5, x + size * 0.55, y + size * 1.05), fill=fade(color, 0.95)
    )
    for dx in (-size * 0.4, size * 0.4):
        draw.ellipse(
            (x + dx - size * 0.25, y - size * 0.2, x + dx + size * 0.25, y + size * 0.3),
            fill=fade(DARK, 1),
        )


def hand(draw, center, size, rise, color=(120, 170, 100)):
    x, y = center
    y += (1 - rise) * size * 2
    draw.rounded_rectangle(
        (x - size * 0.45, y - size * 0.6, x + size * 0.45, y + size), radius=10, fill=fade(color, 1)
    )
    for i in range(4):
        fx_ = x - size * 0.42 + i * size * 0.28
        draw.rounded_rectangle(
            (fx_, y - size * 1.3 - (i % 2) * 10, fx_ + size * 0.2, y - size * 0.5),
            radius=6,
            fill=fade(color, 1),
        )


def yinyang(draw, center, radius, angle):
    x, y = center
    box = (x - radius, y - radius, x + radius, y + radius)
    draw.pieslice(box, angle, angle + 180, fill=fade(DARK, 0.92))
    draw.pieslice(box, angle + 180, angle + 360, fill=fade(WHITE, 0.95))
    for k, fill, dot in ((0, DARK, WHITE), (1, WHITE, DARK)):
        a = math.radians(angle + 180 * k)
        cx, cy = x + radius / 2 * math.cos(a), y + radius / 2 * math.sin(a)
        draw.ellipse(
            (cx - radius / 2, cy - radius / 2, cx + radius / 2, cy + radius / 2),
            fill=fade(fill, 0.95),
        )
        draw.ellipse(
            (cx - radius / 7, cy - radius / 7, cx + radius / 7, cy + radius / 7), fill=fade(dot, 1)
        )
    draw.ellipse(box, outline=fade(GOLD, 1), width=5)


def bone(draw, center, length, angle, color=(240, 235, 220)):
    x, y = center
    dx, dy = length / 2 * math.cos(angle), length / 2 * math.sin(angle)
    draw.line((x - dx, y - dy, x + dx, y + dy), fill=fade(color, 1), width=12)
    for ex, ey in ((x - dx, y - dy), (x + dx, y + dy)):
        for side in (-1, 1):
            ox, oy = -dy / length * 14 * side, dx / length * 14 * side
            draw.ellipse((ex + ox - 9, ey + oy - 9, ex + ox + 9, ey + oy + 9), fill=fade(color, 1))


def wok(draw, center, t, color=(60, 60, 70)):
    x, y = center
    draw.pieslice((x - 160, y - 90, x + 160, y + 90), 0, 180, fill=fade(color, 1))
    draw.line((x + 150, y, x + 290, y - 30), fill=fade((120, 80, 40), 1), width=14)
    for i in range(5):
        k = (t * 1.6 + i / 5) % 1
        sx = x - 90 + i * 45
        draw.line(
            (sx, y - 10 - 160 * k, sx + 14 * math.sin(k * 9), y - 40 - 160 * k),
            fill=fade(WHITE, 0.7 * (1 - k)),
            width=6,
        )


def wheel(draw, center, radius, angle, color=(255, 120, 40)):
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=fade(color, 1), width=8)
    for i in range(6):
        a = angle + math.pi / 3 * i
        draw.line(
            (x, y, x + radius * math.cos(a), y + radius * math.sin(a)), fill=fade(GOLD, 1), width=4
        )
        fx_, fy = x + (radius + 12) * math.cos(a), y + (radius + 12) * math.sin(a)
        draw.polygon(((fx_ - 10, fy), (fx_ + 10, fy), (fx_, fy - 34)), fill=fade(color, 0.9))


def ribbon(draw, size, t, color=(225, 40, 70), y=960):
    w, _ = size
    head = -100 + (w + 300) * ease(t)
    points_top, points_bottom = [], []
    for i in range(30):
        x = head - i * 26
        wave = 70 * math.sin(i * 0.45 + t * 10)
        points_top.append((x, y + wave - 20))
        points_bottom.append((x, y + wave + 20))
    draw.polygon(points_top + points_bottom[::-1], fill=fade(color, 0.85))


def scroll(draw, center, t, ctx, word="禁"):
    x, y = center
    open_w = 260 * ease(min(1.0, t * 2))
    draw.rectangle(
        (x - open_w, y - 120, x + open_w, y + 120),
        fill=fade((245, 230, 190), 1),
        outline=fade((150, 110, 60), 1),
        width=4,
    )
    for side in (-1, 1):
        rx = x + side * open_w
        draw.rounded_rectangle(
            (rx - 16, y - 140, rx + 16, y + 140), radius=10, fill=fade((140, 60, 40), 1)
        )
    if t > 0.45:
        ctx.text(word, (x, y), 150, fade(RED, min(1.0, (t - 0.45) * 4)), True)


def scale_flake(draw, center, size, angle, color=GOLD):
    x, y = center
    draw.pieslice(
        (x - size, y - size, x + size, y + size),
        math.degrees(angle),
        math.degrees(angle) + 180,
        fill=fade(color, 0.95),
        outline=fade((170, 120, 40), 1),
    )


def angler(draw, center, t, color=(255, 235, 140)):
    x, y = center
    tip = (x + 120, y - 200)
    draw.line(
        (x + 20, y - 120, x + 90, y - 230, tip[0], tip[1]),
        fill=fade((90, 80, 70), 1),
        width=6,
        joint="curve",
    )
    r = 26 + 10 * pulse(t * 2 % 1)
    glow(draw, tip, t, color, 120)
    draw.ellipse((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), fill=fade(color, 1))
    return tip


def vortex(draw, center, t, color):
    x, y = center
    for arm in range(4):
        points = []
        for i in range(40):
            k = i / 40
            angle = arm * math.pi / 2 + k * 6 + t * 8
            r = 20 + 300 * k
            points.append((x + r * math.cos(angle), y + r * 0.5 * math.sin(angle)))
        draw.line(points, fill=fade(color, 0.6), width=8)


def planet_rings(draw, center, t, color, count=3):
    x, y = center
    for i in range(count):
        r = 200 - i * 22
        tilt = 0.28 + i * 0.05
        draw.ellipse((x - r, y - r * tilt, x + r, y + r * tilt), outline=fade(color, 0.95), width=7)
        angle = 2 * math.pi * (t * (1.2 + i * 0.4) + i / count)
        star(draw, (x + r * math.cos(angle), y + r * tilt * math.sin(angle)), 13, fade(WHITE, 1))


def meteor(draw, start, end, k, color=(255, 150, 60)):
    (x0, y0), (x1, y1) = start, end
    x, y = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
    for i in range(6):
        trail = max(0.0, k - i * 0.05)
        tx, ty = x0 + (x1 - x0) * trail, y0 + (y1 - y0) * trail
        r = 26 - i * 3
        draw.ellipse(
            (tx - r, ty - r, tx + r, ty + r), fill=fade(color if i else GOLD, 0.9 - i * 0.13)
        )
    draw.ellipse((x - 18, y - 18, x + 18, y + 18), fill=fade((120, 90, 70), 1))


def bug(draw, center, size, t):
    x, y = center
    for side in (-1, 1):
        for i in range(3):
            ly = y - size * 0.4 + i * size * 0.4
            wiggle = 10 * math.sin(t * 20 + i)
            draw.line(
                (x + side * size * 0.5, ly, x + side * (size + 20), ly + wiggle),
                fill=fade(DARK, 1),
                width=5,
            )
    draw.ellipse(
        (x - size * 0.6, y - size, x + size * 0.6, y + size),
        fill=fade((60, 160, 70), 1),
        outline=fade(DARK, 1),
        width=4,
    )
    draw.line((x, y - size, x, y + size), fill=fade(DARK, 1), width=3)
    draw.ellipse(
        (x - size * 0.35, y - size * 1.4, x + size * 0.35, y - size * 0.8), fill=fade(DARK, 1)
    )


def thermometer(draw, center, t, color=RED):
    x, y = center
    draw.rounded_rectangle(
        (x - 18, y - 140, x + 18, y + 60),
        radius=18,
        fill=fade(WHITE, 0.95),
        outline=fade(DARK, 0.8),
        width=3,
    )
    level = y + 50 - 180 * ease(t)
    draw.rounded_rectangle((x - 9, level, x + 9, y + 60), radius=9, fill=fade(color, 1))
    draw.ellipse(
        (x - 32, y + 40, x + 32, y + 104), fill=fade(color, 1), outline=fade(DARK, 0.8), width=3
    )


def depth_gauge(draw, center, t, ctx):
    x, y = center
    depth = int(1000 + 9000 * ease(t))
    draw.rounded_rectangle(
        (x - 120, y - 36, x + 120, y + 36), radius=18, fill=fade((10, 25, 60), 0.85)
    )
    ctx.text(f"-{depth}m", (x, y), 36, fade((140, 210, 255), 1), True)


# ----- the 54 scenes -----


def lurk(draw, t, c):
    x, y = c.boss
    draw.ellipse((x - 220, y - 160, x + 220, y + 160), fill=fade(DARK, 0.8 * min(1.0, t * 3)))
    for dx in (-50, 50):
        r = 16 * (1 if (t * 6) % 1 > 0.15 else 0.2)
        draw.ellipse(
            (x + dx - 22, y - 30 - r, x + dx + 22, y - 30 + r), fill=fade((255, 230, 80), 1)
        )
    smoke(draw, c.boss, t, (60, 70, 50))


def stench(draw, t, c):
    for i, (x, y) in enumerate(c.team):
        for k in range(3):
            phase = (t + k / 3) % 1
            points = [
                (x - 40 + k * 30 + 18 * math.sin(phase * 8 + j), y + 40 - j * 22 - phase * 60)
                for j in range(6)
            ]
            draw.line(points, fill=fade((150, 180, 60), 0.85), width=6)
        rng = random.Random(i)
        for _ in range(4):
            a = rng.uniform(0, 6.3) + t * 9
            draw.ellipse(
                (
                    x + 60 * math.cos(a) - 4,
                    y - 70 + 20 * math.sin(a) - 4,
                    x + 60 * math.cos(a) + 4,
                    y - 70 + 20 * math.sin(a) + 4,
                ),
                fill=DARK,
            )


def lazy(draw, t, c):
    x, y = c.boss
    draw.rounded_rectangle(
        (x - 200, y + 70, x + 200, y + 130), radius=20, fill=fade((180, 140, 110), 0.95)
    )
    draw.rounded_rectangle((x - 210, y + 40, x - 130, y + 90), radius=20, fill=fade(WHITE, 0.95))
    for i in range(4):
        k = (t + i / 4) % 1
        c.text(
            "Z",
            (x + 80 + k * 120, y - 60 - k * 160),
            int(30 + 30 * k),
            fade((90, 110, 160), 1 - k),
            True,
        )
    rising(draw, c.boss, t, GREEN)


def freeze_team(draw, t, c):
    for i, center in enumerate(c.team):
        ice_block(draw, center, 150 * ease(min(1.0, t * 2)), t)
        snowflake(draw, (center[0] + 50, center[1] - 80), 16, fade(WHITE, 1))


def ice_shell(draw, t, c):
    ice_block(draw, c.boss, 340, t, crack=max(0.0, t - 0.6) * 2.5)
    for i in range(6):
        a = i * 1.05 + t * 2
        snowflake(
            draw,
            (c.boss[0] + 210 * math.cos(a), c.boss[1] + 140 * math.sin(a)),
            20,
            fade(WHITE, 0.95),
        )


def melt(draw, t, c):
    x, y = c.boss
    shatter(draw, c.boss, t, (200, 240, 255), seed=2)
    for i in range(7):
        dx = -150 + i * 50
        k = (t * 1.4 + i * 0.13) % 1
        dy = 90 + 220 * k
        draw.ellipse(
            (x + dx - 10, y + dy - 16, x + dx + 10, y + dy + 16), fill=fade((120, 200, 255), 1 - k)
        )
    c.text("融化", (x, y - 200), 50, fade((255, 120, 90), pulse(t)), True)


def altitude(draw, t, c):
    mountain(draw, c.size, ease(min(1.0, t * 1.5)))
    for i in range(10):
        y = 200 + i * 100
        x = -300 + (c.size[0] + 600) * ((t * 1.3 + i * 0.17) % 1)
        draw.line((x - 180, y, x, y), fill=fade(WHITE, 0.8), width=5)


def summit(draw, t, c):
    mountain(draw, c.size, 1.0, (120, 140, 170))
    x, y = c.boss
    draw.rounded_rectangle(
        (x - 230, y - 200, x + 230, y + 200),
        radius=40,
        outline=fade((200, 220, 255), pulse(t)),
        width=14,
    )
    c.text("不可逾越", (x, y - 240), 48, fade(WHITE, pulse(t)), True)


def avalanche(draw, t, c):
    w, h = c.size
    mountain(draw, c.size, 1.0)
    front = -200 + (w + 400) * ease(t)
    rng = random.Random(3)
    for _ in range(60):
        bx = front - rng.uniform(0, 380)
        by = 800 + rng.uniform(-80, 380)
        r = rng.uniform(20, 60)
        draw.ellipse((bx - r, by - r, bx + r, by + r), fill=fade(WHITE, 0.95))
    flash(draw, c.size, t, WHITE, 0.25)


def unobserved(draw, t, c):
    x, y = c.boss
    rng = random.Random(int(t * 12))
    for _ in range(10):
        by = y + rng.uniform(-180, 180)
        shift = rng.uniform(-60, 60)
        draw.rectangle(
            (x - 220 + shift, by, x + 220 + shift, by + rng.uniform(6, 20)),
            fill=fade((120, 200, 255), 0.6),
        )
    for dx, color in ((-8, (255, 60, 60)), (8, (60, 200, 255))):
        draw.ellipse(
            (x - 150 + dx, y - 150, x + 150 + dx, y + 150), outline=fade(color, 0.7), width=6
        )
    c.text("? ? ?", (x, y - 210), 46, fade(WHITE, pulse(t)), True)


def page_404(draw, t, c):
    for center in c.team[:2] or [c.boss]:
        k = ease(min(1.0, t * 2.5))
        window(draw, (center[0], center[1] - 40), (230 * k + 20, 160 * k + 20), "404", c, t)


def rollback(draw, t, c):
    clock(draw, (c.boss[0], c.boss[1] - 20), 120, t, (120, 200, 255))
    rising(draw, c.boss, t, (120, 200, 255))


def lock_on(draw, t, c):
    target = max(c.team, key=lambda p: p[0]) if c.team else c.boss
    x, y = target
    r = 140 - 80 * ease(min(1.0, t * 1.6))
    draw.ellipse((x - r, y - r, x + r, y + r), outline=fade(RED, 1), width=5)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line(
            (x + dx * r * 0.5, y + dy * r * 0.5, x + dx * r * 1.3, y + dy * r * 1.3),
            fill=fade(RED, 1),
            width=5,
        )
    bx, by = c.boss[0] - 40, c.boss[1] - 40
    draw.line((bx, by, x, y), fill=fade(RED, 0.5 + 0.5 * pulse(t * 3 % 1)), width=3)
    draw.ellipse((bx - 14, by - 14, bx + 14, by + 14), fill=fade(RED, 1))


def overheat(draw, t, c):
    x, y = c.boss
    thermometer(draw, (x + 220, y - 40), t)
    for i in range(6):
        k = (t * 1.5 + i / 6) % 1
        sx = x - 120 + i * 48
        draw.ellipse(
            (sx - 20 - 30 * k, y - 120 - 200 * k - 20, sx + 20 + 30 * k, y - 120 - 200 * k + 20),
            fill=fade((120, 120, 120), 0.6 * (1 - k)),
        )
    glow(draw, c.boss, t, (255, 90, 40), 200)


def system_bug(draw, t, c):
    x, y = c.boss
    bug(draw, (x + 170 * math.cos(t * 7), y - 150 + 30 * math.sin(t * 9)), 34, t)
    c.text("ERROR", (x, y + 190), 44, fade(RED, pulse(t * 2 % 1)), True)


def hack(draw, t, c):
    code_rain(draw, c.size, t, (80, 255, 140), c)
    for center in c.team:
        shatter(draw, center, t, (120, 255, 160))
        c.text("DELETED", (center[0], center[1] - 70), 22, fade((80, 255, 140), pulse(t)), True)


def evolve(draw, t, c):
    x, y = c.boss
    for i, (dx, dy, r) in enumerate(((-170, -120, 60), (170, -100, 50), (0, 170, 45))):
        gear(draw, (x + dx, y + dy), r, t * 4 * (1 if i % 2 else -1), (180, 185, 200))
    arrows(draw, c.boss, t, True, (255, 80, 200))


def overclock(draw, t, c):
    chip(draw, (c.boss[0], c.boss[1] - 10), 150, t, (255, 80, 200))
    for i in range(4):
        a = i * math.pi / 2 + t * 5
        start = (c.boss[0] + 230 * math.cos(a), c.boss[1] + 230 * math.sin(a))
        draw.line((start, c.boss), fill=fade((255, 120, 220), pulse(t)), width=5)
    flash(draw, c.size, (t * 4) % 1, (255, 80, 200), 0.15)


def curse(draw, t, c):
    horns(draw, c.boss, t)
    for i, center in enumerate(c.team):
        k = ease(min(1.0, max(0.0, t * 1.6 - i * 0.15)))
        x, y = c.boss[0] + (center[0] - c.boss[0]) * k, c.boss[1] + (center[1] - c.boss[1]) * k
        skull(draw, (x, y - 60), 24, (200, 140, 230))
        if k >= 1:
            smoke(draw, center, t, (140, 60, 180), seed=i)


def prank(draw, t, c):
    if len(c.team) < 2:
        return
    a, b = c.team[0], c.team[-1]
    mid = ((a[0] + b[0]) / 2, min(a[1], b[1]) - 200)
    for start, end, color in ((a, b, RED), (b, a, GREEN)):
        k = ease(t)
        x = start[0] + (end[0] - start[0]) * k
        y = start[1] + (mid[1] - start[1]) * math.sin(math.pi * k)
        draw.rounded_rectangle((x - 50, y - 14, x + 50, y + 14), radius=7, fill=fade(color, 0.95))
    horns(draw, c.boss, min(1.0, t * 2))
    c.text("嘿嘿", (c.boss[0], c.boss[1] + 170), 48, fade((200, 40, 50), pulse(t)), True)


def grin(draw, t, c):
    x, y = c.boss
    glow(draw, c.boss, t, (230, 40, 40), 220)
    draw.arc((x - 150, y - 60, x + 150, y + 140), 15, 165, fill=fade((120, 0, 10), 1), width=16)
    for dx in (-60, 60):
        draw.polygon(
            ((x + dx - 30, y - 60), (x + dx + 30, y - 60), (x + dx, y - 20)),
            fill=fade((255, 230, 60), 1),
        )


def chains_bind(draw, t, c):
    x, y = c.boss
    for dy in (-90, 0, 90):
        chain(draw, (x - 330, y + dy - 60), (x + 330, y + dy + 60), t, broken=0.0)
    for side in (-1, 1):
        draw.rectangle(
            (x + side * 330 - 20, y - 200, x + side * 330 + 20, y + 200),
            fill=fade((90, 90, 100), 1),
        )


def king_returns(draw, t, c):
    x, y = c.boss
    for dy in (-90, 0, 90):
        chain(draw, (x - 330, y + dy - 60), (x + 330, y + dy + 60), t, broken=ease(t))
    sparks(draw, c.boss, t, GOLD, count=30)
    crown(draw, (x, y - 150 - 40 * (1 - ease(t))), 170, t)
    rays(draw, (x, y - 200), t, GOLD, 18, 260)


def overawe(draw, t, c):
    target = min(c.team, key=lambda p: p[0]) if c.team else c.boss
    crown(draw, (c.boss[0], c.boss[1] - 170), 140, t)
    x, y = target
    for i in range(6):
        k = (t * 2 + i / 6) % 1
        draw.line(
            (x - 70 + i * 28, y - 200 + 160 * k, x - 70 + i * 28, y - 160 + 160 * k),
            fill=fade((120, 90, 160), 0.9),
            width=6,
        )
    c.text("跪", (x, y - 20), 70, fade((120, 90, 160), pulse(t)), True)


def luck(draw, t, c):
    x, y = c.boss
    clover(draw, (x + 200, y - 160), 34)
    for i in range(10):
        k = (t * 1.2 + i / 10) % 1
        coin(draw, (x - 250 + i * 55, y - 260 + 420 * k), 20, t * 10 + i)


def oracle(draw, t, c):
    x = c.size[0] / 2
    rays(draw, (x, 170), t, (255, 230, 150), 20, 500)
    eye(draw, (x, 170), 130, t, (230, 170, 60))
    for center in c.team[:1]:
        draw.line((x, 220, center[0], center[1]), fill=fade((255, 220, 120), 0.7), width=10)
        glow(draw, center, t, GOLD, 100)


def guardians(draw, t, c):
    x, y = c.boss
    for dx in (-230, 230):
        k = ease(t)
        ghost(draw, (x + dx, y + 160 - 160 * k), 46, t)
        glow(draw, (x + dx, y), t, (255, 240, 200), 100)
    rays(draw, c.boss, t, (255, 240, 200), 14, 220)


def zombie_poison(draw, t, c):
    w, _ = c.size
    draw.rectangle((0, 820, w, 1280), fill=fade((90, 160, 60), 0.3 * pulse(t)))
    for i, center in enumerate(c.team):
        x, y = center
        skull(draw, (x, y - 110 - 24 * math.sin(t * 6 + i)), 40, (150, 230, 100))
        bubbles(draw, center, t, (120, 210, 80), seed=i, count=14)
    k = ease(min(1.0, t * 2))
    x, y = c.boss
    draw.arc((x - 110, y + 20, x + 110, y + 160), 20, 160, fill=fade((120, 210, 80), 1), width=14)
    for i in range(5):
        draw.ellipse(
            (
                x - 80 + i * 40 - 8,
                y + 130 + 120 * k - 8,
                x - 80 + i * 40 + 8,
                y + 130 + 120 * k + 8,
            ),
            fill=fade((120, 210, 80), 1 - k * 0.5),
        )


def zombie_limp(draw, t, c):
    x, y = c.boss
    sway = 40 * math.sin(t * 9)
    smoke(draw, (x + sway, y), t, (110, 140, 90))
    for i in range(5):
        fx_, fy = x - 260 + i * 130, y + 180 + (14 if i % 2 else -14)
        alpha = 1 - abs(((t * 2.5) % 1) - i / 5)
        draw.ellipse(
            (fx_ - 34, fy - 18, fx_ + 34, fy + 18), fill=fade((80, 110, 60), max(0.2, alpha))
        )
        for toe in range(3):
            draw.ellipse(
                (
                    fx_ + 30 + toe * 4 - 7,
                    fy - 20 + toe * 14 - 7,
                    fx_ + 30 + toe * 4 + 7,
                    fy - 20 + toe * 14 + 7,
                ),
                fill=fade((80, 110, 60), max(0.2, alpha)),
            )
    hand(draw, (x + 210, y + 40), 34, ease(min(1.0, t * 2)))


def zombie_rise(draw, t, c):
    for i, center in enumerate(c.team):
        hand(draw, (center[0], center[1] + 40), 30, ease(min(1.0, t * 1.5 - i * 0.1)))
        glow(draw, center, t, (110, 200, 90), 90)


def yin_yang(draw, t, c):
    yinyang(draw, c.boss, 170, 360 * ease(t))
    w, h = c.size
    draw.rectangle((0, 0, w / 2, h), fill=fade(DARK, 0.25 * pulse(t)))
    draw.rectangle((w / 2, 0, w, h), fill=fade(WHITE, 0.2 * pulse(t)))


def possess(draw, t, c):
    if not c.team:
        return
    target = c.team[len(c.team) // 2]
    k = ease(t)
    x = c.boss[0] + (target[0] - c.boss[0]) * k
    y = c.boss[1] + (target[1] - c.boss[1]) * k - 120 * math.sin(math.pi * k)
    ghost(draw, (x, y), 40, t, (190, 160, 255))
    if k > 0.8:
        glow(draw, target, t, PURPLE, 110)


def pincer(draw, t, c):
    if not c.team:
        return
    for center, side in (
        (max(c.team, key=lambda p: p[0]), 1),
        (min(c.team, key=lambda p: p[0]), -1),
    ):
        x, y = center
        close = ease(t)
        for k in (-1, 1):
            arc_y = y + k * (120 - 90 * close)
            draw.arc(
                (x - 110, arc_y - 60, x + 110, arc_y + 60),
                200 if k < 0 else 20,
                340 if k < 0 else 160,
                fill=fade((190, 140, 255), 1),
                width=12,
            )


def reassemble(draw, t, c):
    x, y = c.boss
    rng = random.Random(5)
    for i in range(12):
        sx, sy = x + rng.uniform(-320, 320), y + rng.uniform(150, 400)
        k = ease(t)
        bone(draw, (sx + (x - sx) * k, sy + (y - sy) * k), 70, rng.uniform(0, 3.1) * (1 - k))
    if t > 0.7:
        skull(draw, (x, y - 140), 50, (240, 235, 220))


def bone_thin(draw, t, c):
    x, y = c.boss
    for i in range(5):
        bone(draw, (x - 160 + i * 80, y + 170), 60, 0.4 * math.sin(t * 6 + i))
    c.text("打不中骨缝", (x, y - 200), 40, fade(WHITE, pulse(t)), True)


def cook(draw, t, c):
    x = c.size[0] / 2
    wok(draw, (x, 980), t)
    flame(draw, (x, 1090), t, (255, 120, 40))
    for i, center in enumerate(c.team):
        k = ease(min(1.0, t * 1.5))
        draw.ellipse(
            (
                center[0] - 14,
                center[1] - 200 * (1 - k) - 14,
                center[0] + 14,
                center[1] - 200 * (1 - k) + 14,
            ),
            fill=fade((255, 160, 60), 1),
        )


def fire_wheels(draw, t, c):
    w, _ = c.size
    for i, y in enumerate((900, 1080)):
        x = -100 + (w + 200) * ((t + i * 0.5) % 1)
        wheel(draw, (x, y), 60, t * 20)
        flame(draw, (x - 80, y + 20), t, (255, 120, 40), seed=i)


def red_ribbon(draw, t, c):
    ribbon(draw, c.size, t)
    for center in c.team:
        if t > 0.5:
            c.text("盾 闪避 -", (center[0], center[1] - 80), 20, fade(WHITE, pulse(t)), True)


def golden_ring(draw, t, c):
    start = c.boss
    for i, end in enumerate(c.team or [c.boss]):
        k = ease(min(1.0, max(0.0, t * 1.5 - i * 0.12)))
        x = start[0] + (end[0] - start[0]) * k
        y = start[1] + (end[1] - start[1]) * k - 160 * math.sin(math.pi * k)
        draw.ellipse((x - 44, y - 44, x + 44, y + 44), outline=fade(GOLD, 1), width=12)
        draw.ellipse((x - 54, y - 54, x + 54, y + 54), outline=fade(WHITE, 0.5), width=3)
        if k >= 1:
            burst(draw, end, min(1.0, (t * 1.5 - i * 0.12 - 1) * 3), GOLD, 90, 12)


def tide(draw, t, c):
    w, h = c.size
    level = h * 0.85 - 260 * pulse(t)
    points = (
        [(0, h)] + [(w * i / 16, level + 22 * math.sin(t * 10 + i)) for i in range(17)] + [(w, h)]
    )
    draw.polygon(points, fill=fade((60, 140, 230), 0.55))
    for i in range(5):
        fx_ = (t * 300 + i * 160) % (w + 100) - 50
        fy = level + 60 + i * 30
        draw.ellipse((fx_ - 24, fy - 10, fx_ + 24, fy + 10), fill=fade((255, 170, 80), 0.9))
        draw.polygon(
            ((fx_ - 24, fy), (fx_ - 40, fy - 12), (fx_ - 40, fy + 12)),
            fill=fade((255, 170, 80), 0.9),
        )


def decree(draw, t, c):
    scroll(draw, (c.size[0] / 2, 660), t, c)


def dragon_scales(draw, t, c):
    x, y = c.boss
    rng = random.Random(8)
    for i in range(16):
        sx, sy = x + rng.uniform(-150, 150), y + rng.uniform(-120, 120)
        target = c.team[i % len(c.team)] if c.team else (x, y + 400)
        k = ease(t)
        px, py = sx + (target[0] - sx) * k, sy + (target[1] - sy) * k - 120 * math.sin(math.pi * k)
        scale_flake(draw, (px, py), 18, t * 8 + i)


def pressure(draw, t, c):
    w, h = c.size
    for i in range(8):
        inset = i * 40
        draw.rectangle(
            (inset, inset, w - inset, h - inset),
            outline=fade((10, 30, 80), 0.85 * (1 - i / 8)),
            width=40,
        )
    depth_gauge(draw, (w / 2, 140), t, c)
    for i, center in enumerate(c.team):
        bubbles(draw, center, t, (160, 220, 255), seed=i)


def lantern(draw, t, c):
    tip = angler(draw, c.boss, t)
    if c.team:
        target = max(c.team, key=lambda p: p[1] + p[0] * 0.01)
        draw.polygon(
            (tip, (target[0] - 90, target[1] + 40), (target[0] + 90, target[1] + 40)),
            fill=fade((255, 240, 160), 0.35 * pulse(t) + 0.1),
        )
        glow(draw, target, t, (255, 235, 140), 110)


def abyss(draw, t, c):
    draw.rectangle((0, 0, *c.size), fill=fade((5, 15, 45), 0.45 * pulse(t)))
    vortex(draw, (c.size[0] / 2, 980), t, (60, 110, 200))
    for i, center in enumerate(c.team):
        draw.ellipse(
            (center[0] - 80, center[1] - 80, center[0] + 80, center[1] + 80),
            outline=fade((60, 110, 200), 0.7),
            width=5,
        )


def abduct(draw, t, c):
    if not c.team:
        return
    target = c.team[0]
    x = min(max(target[0], 170), c.size[0] - 170)
    top = (x, 640 - 40 * math.sin(t * 6))
    light_beam(
        draw, (top[0], top[1] + 20), (target[0], target[1] + 90), 34, 120, t, (150, 255, 170)
    )
    ufo(draw, top, 1.0, t)
    lift = 60 * ease(t)
    glow(draw, (target[0], target[1] - lift), t, (150, 255, 170), 110)


def doubt(draw, t, c):
    for center in c.team:
        x, y = center
        for k in range(3):
            r = 18 + k * 16
            a = t * 10 + k
            draw.arc(
                (x - r, y - 120 - r, x + r, y - 120 + r),
                math.degrees(a),
                math.degrees(a) + 270,
                fill=fade(PURPLE, 0.9),
                width=4,
            )
        c.text("?", (x + 40, y - 170 - 20 * math.sin(t * 6)), 44, fade((160, 255, 190), 1), True)
    ufo(draw, (c.boss[0], c.boss[1] - 190), 0.55, t)


def mothership(draw, t, c):
    w, _ = c.size
    descend = ease(min(1.0, t * 1.6))
    top = (c.boss[0], -160 + 300 * descend)
    light_beam(
        draw, (top[0], top[1] + 40), (c.boss[0], c.boss[1] + 170), 80, 220, t, (150, 255, 170)
    )
    ufo(draw, top, 2.2, t)
    if t > 0.5:
        draw.ellipse(
            (c.boss[0] - 170, c.boss[1] - 170, c.boss[0] + 170, c.boss[1] + 170),
            fill=fade((210, 255, 220), (t - 0.5) * 1.4),
        )
    for center in c.team:
        draw.line(
            (top[0], top[1] + 60, center[0], center[1]),
            fill=fade((150, 255, 170), 0.5 * pulse(t * 3 % 1)),
            width=5,
        )


def cluster(draw, t, c):
    x, y = c.boss
    rng = random.Random(2)
    glow(draw, c.boss, t, (210, 190, 255), 240)
    for _ in range(40):
        a, r = rng.uniform(0, 6.3), rng.uniform(220, 420)
        k = ease(t)
        px, py = x + r * (1 - k) * math.cos(a + t * 3), y + r * 0.8 * (1 - k) * math.sin(a + t * 3)
        draw.line(
            (px, py, px + (x - px) * 0.15, py + (y - py) * 0.15), fill=fade(WHITE, 0.8), width=3
        )
        star(draw, (px, py), 16, fade((235, 220, 255), 1))


def gravity_pull(draw, t, c):
    x, y = c.boss
    for i in range(7):
        r = 360 * (1 - ((t + i / 7) % 1))
        draw.ellipse(
            (x - r, y - r * 0.6, x + r, y + r * 0.6), outline=fade((220, 200, 255), 0.95), width=6
        )
    for center in c.team:
        k = (t * 2) % 1
        draw.line((center[0], center[1], x, y), fill=fade((220, 200, 255), 0.7), width=6)
        mx, my = center[0] + (x - center[0]) * k, center[1] + (y - center[1]) * k
        star(draw, (mx, my), 14, fade(WHITE, 1))


def supernova(draw, t, c):
    x, y = c.boss
    for i in range(3):
        r = 900 * ease(max(0.0, t - i * 0.12))
        draw.ellipse(
            (x - r, y - r, x + r, y + r), outline=fade((255, 240, 200), 1 - t), width=30 - i * 8
        )
    flash(draw, c.size, t, (255, 250, 230), 0.85)
    rays(draw, c.boss, t, GOLD, 24, 600)


def rings(draw, t, c):
    glow(draw, c.boss, t, (190, 200, 255), 230)
    planet_rings(draw, c.boss, t, (210, 220, 255), count=3)
    planet_rings(draw, (c.boss[0], c.boss[1] + 10), t + 0.3, (255, 230, 180), count=1)


def gravity_field(draw, t, c):
    w, _ = c.size
    for i in range(10):
        y = 560 + i * 72
        bend = 70 * pulse(t)
        draw.arc(
            (-80, y - bend, w + 80, y + bend + 50),
            0,
            180,
            fill=fade((180, 160, 255), 0.85),
            width=5,
        )
    if len(c.team) >= 2:
        fast, slow = c.team[0], c.team[-1]
        glow(draw, fast, t, (255, 110, 110), 110)
        glow(draw, slow, t, (110, 160, 255), 110)
        arrows(draw, fast, t, False, (255, 110, 110))
        arrows(draw, slow, t, True, (110, 160, 255))
        c.text("拉近", (fast[0], fast[1] - 140), 34, fade(WHITE, 1), True)
        c.text("推远", (slow[0], slow[1] - 140), 34, fade(WHITE, 1), True)


def meteor_shower(draw, t, c):
    targets = (c.team or [c.boss])[:3]
    for i, (x, y) in enumerate(targets * 1 + targets[:1]):
        k = min(1.0, max(0.0, t * 1.5 - i * 0.18))
        if k <= 0:
            continue
        meteor(draw, (x + 260, y - 760), (x, y), ease(k))
        if k >= 1:
            burst(draw, (x, y), min(1.0, (t * 1.5 - i * 0.18 - 1) * 3), (255, 150, 60), 110, 12)


# ----- kiln dungeon (熔岩猪窑): props -----

LAVA = (255, 80, 30)
EMBER = (255, 150, 50)
CORE = (255, 236, 160)
CHAR = (45, 26, 20)
STEAM = (235, 230, 225)


def span(t, start, end):
    """0 before `start`, 1 after `end`, linear in between."""
    return max(0.0, min(1.0, (t - start) / (end - start)))


def lerp(a, b, k):
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)


def slam(c, word, center, t, start, size, color=WHITE, end=1.0):
    """A big word that slams in at `start` and fades out towards `end`."""
    if t < start:
        return
    k = ease(min(1.0, (t - start) / 0.16))
    alpha = min(1.0, (t - start) * 8) * min(1.0, max(0.0, (end - t) / 0.12 + 0.001))
    c.text(word, center, int(size * (1.7 - 0.7 * k)), fade(color, alpha), True, 6)


def tongue(draw, base, height, width, t, seed=0, alpha=1.0, lean=0.0):
    """One flickering flame tongue: red rim, orange body, pale core."""
    x, y = base
    phase = random.Random(seed).uniform(0, 6.3)
    height *= 0.88 + 0.12 * math.sin(t * 26 + phase)
    for color, k in ((LAVA, 1.0), (EMBER, 0.72), (CORE, 0.42)):
        h, w = height * k, width * k
        left, right = [], []
        for i in range(10):
            s = i / 9
            sway = (0.3 * math.sin(t * 17 + phase + s * 3.4) + lean) * w * s * 1.5
            half = w * (1 - s) ** 0.8 * (0.72 + 0.28 * math.sin(math.pi * min(1.0, s * 1.7)))
            left.append((x - half + sway, y - h * s))
            right.append((x + half + sway, y - h * s))
        bottom = [
            (x + w * 0.72 * math.cos(a / 6 * math.pi), y + w * 0.3 * math.sin(a / 6 * math.pi))
            for a in range(7)
        ]
        draw.polygon(left + right[::-1] + bottom, fill=fade(color, alpha * 0.9))


def blaze(draw, center, spread, height, t, seed=0, count=7, alpha=1.0):
    """A patch of tongues across an oval, tallest in the middle."""
    x, y = center
    rng = random.Random(seed)
    for i in range(count):
        k = (i + 0.5) / count * 2 - 1
        h = height * (1 - 0.55 * abs(k)) * rng.uniform(0.75, 1.05)
        tongue(
            draw,
            (x + k * spread, y + rng.uniform(-6, 6)),
            h,
            h * 0.28,
            t,
            seed * 13 + i,
            alpha,
            lean=k * 0.15,
        )


def embers(draw, box, t, seed=0, count=24, color=EMBER, alpha=1.0):
    """Glowing sparks drifting upward through `box`."""
    x0, y0, x1, y1 = box
    rng = random.Random(seed)
    for _ in range(count):
        x, speed, phase = rng.uniform(x0, x1), rng.uniform(0.6, 1.4), rng.random()
        p = (t * speed + phase) % 1
        cx = x + 16 * math.sin(p * 9 + phase * 6)
        cy = y1 - (y1 - y0) * p
        r = 2 + 3.5 * (1 - p)
        a = alpha * min(1.0, p * 5) * (1 - p)
        draw.line((cx, cy, cx - 3, cy + 12), fill=fade(color, a * 0.6), width=2)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fade(CORE if r > 4 else color, a))


def heat_vignette(draw, size, strength, color=LAVA):
    w, h = size
    for i in range(6):
        inset = i * 34
        draw.rectangle(
            (inset, inset, w - inset, h - inset),
            outline=fade(color, strength * (1 - i / 6) ** 1.6),
            width=34,
        )


def haze(draw, size, top, bottom, t, alpha=0.35, color=EMBER):
    w, _ = size
    for i in range(9):
        y = bottom - (bottom - top) * ((i / 9 + t * 0.6) % 1)
        points = [(x, y + 7 * math.sin(x / 38 + t * 14 + i)) for x in range(0, w + 40, 40)]
        draw.line(
            points, fill=fade(color, alpha * (1 - abs(y - (top + bottom) / 2) / 400)), width=3
        )


def shockring(draw, center, radius, alpha, color=EMBER, flat=0.32, width=12):
    x, y = center
    box = (x - radius, y - radius * flat, x + radius, y + radius * flat)
    draw.ellipse(box, outline=fade(color, alpha), width=width)
    draw.ellipse(box, outline=fade(CORE, alpha * 0.8), width=max(2, width // 3))


def cracks(draw, center, length, k, seed=0, count=8, color=LAVA):
    """Glowing fissures splitting the floor outward from `center`."""
    x, y = center
    rng = random.Random(seed)
    for i in range(count):
        angle = 2 * math.pi * i / count + rng.uniform(-0.3, 0.3)
        points, px, py = [(x, y)], x, y
        steps = 6
        for s in range(1, steps + 1):
            reach = length * k * s / steps
            a = angle + rng.uniform(-0.35, 0.35)
            px, py = x + reach * math.cos(a), y + reach * 0.36 * math.sin(a)
            points.append((px, py))
        draw.line(points, fill=fade(CHAR, 0.9), width=10)
        draw.line(points, fill=fade(color, 0.95), width=5)
        draw.line(points, fill=fade(CORE, 0.8), width=2)


def firewood(draw, center, length, angle, color=(115, 64, 32)):
    x, y = center
    dx, dy = length / 2 * math.cos(angle), length / 2 * math.sin(angle)
    draw.line((x - dx, y - dy, x + dx, y + dy), fill=fade(color, 1), width=24)
    for ex, ey in ((x - dx, y - dy), (x + dx, y + dy)):
        draw.ellipse((ex - 12, ey - 12, ex + 12, ey + 12), fill=fade((190, 140, 90), 1))
        draw.ellipse((ex - 6, ey - 6, ex + 6, ey + 6), outline=fade(color, 1), width=2)


def bonfire(draw, center, height, t, seed=0, alpha=1.0):
    x, y = center
    draw.ellipse((x - 120, y - 18, x + 120, y + 30), fill=fade(CHAR, 0.55 * alpha))
    for angle in (-0.35, 0.35, 0.0):
        firewood(draw, (x, y + 4), 190, angle)
    blaze(draw, (x, y), 70, height, t, seed, count=6, alpha=alpha)


def grate(draw, center, width, heat):
    """A grill grate glowing from dull iron to red-hot as `heat` rises."""
    x, y = center
    color = (
        int(90 + 165 * heat),
        int(90 - 10 * heat),
        int(100 - 70 * heat),
    )
    half, depth = width / 2, 26
    draw.polygon(
        (
            (x - half, y - depth),
            (x + half, y - depth),
            (x + half + 26, y + depth),
            (x - half - 26, y + depth),
        ),
        outline=fade(color, 1),
        width=6,
    )
    for i in range(9):
        k = i / 8
        top = x - half + width * k
        draw.line((top, y - depth, top + (k - 0.5) * 52, y + depth), fill=fade(color, 1), width=5)


def anger(draw, center, size, alpha=1.0, color=(255, 55, 55)):
    """The cartoon throbbing-vein mark."""
    x, y = center
    for qx, qy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        cx, cy = x + qx * size * 0.62, y + qy * size * 0.62
        r = size * 0.55
        a = math.degrees(math.atan2(-qy, -qx))
        draw.arc(
            (cx - r, cy - r, cx + r, cy + r),
            a - 48,
            a + 48,
            fill=fade(color, alpha),
            width=max(4, int(size * 0.2)),
        )


def chat_bubble(draw, box, tail, fill=WHITE, alpha=1.0, outline=(60, 60, 70)):
    draw.polygon(
        (tail, (box[0] + 30, box[3] - 4), (box[0] + 62, box[3] - 4)), fill=fade(fill, alpha)
    )
    draw.rounded_rectangle(
        box, radius=22, fill=fade(fill, alpha), outline=fade(outline, alpha), width=3
    )


def ticks(draw, pos, size, color, alpha=1.0):
    x, y = pos
    for dx in (0, size * 0.5):
        draw.line(
            (
                (x + dx, y),
                (x + dx + size * 0.3, y + size * 0.3),
                (x + dx + size * 0.8, y - size * 0.4),
            ),
            fill=fade(color, alpha),
            width=4,
        )


def lucky_cloud(draw, center, size, alpha=1.0, color=(255, 214, 110)):
    """祥云: a puffy golden cloud with curled ends."""
    x, y = center
    s = size
    edge = (205, 140, 45)
    draw.ellipse((x - s * 1.25, y - s * 0.3, x + s * 1.25, y + s * 0.42), fill=fade(color, alpha))
    for dx, dy, r in ((-0.62, -0.18, 0.42), (0.0, -0.38, 0.58), (0.62, -0.16, 0.44)):
        cx, cy = x + dx * s, y + dy * s
        draw.ellipse((cx - r * s, cy - r * s, cx + r * s, cy + r * s), fill=fade(color, alpha))
        draw.arc(
            (cx - r * s * 0.55, cy - r * s * 0.55, cx + r * s * 0.55, cy + r * s * 0.55),
            150,
            420,
            fill=fade(edge, alpha),
            width=4,
        )
    tail = [
        (x + s * 1.2 + s * 0.35 * k * math.cos(k * 5), y + s * 0.1 + s * 0.3 * k * math.sin(k * 5))
        for k in (i / 10 for i in range(11))
    ]
    draw.line(tail, fill=fade(color, alpha), width=int(s * 0.16))


def fire_cone(draw, origin, aim, t, spread=0.32, seed=0):
    """A roaring cone of breath fire from `origin` towards `aim`."""
    ox, oy = origin
    angle = math.atan2(aim[1] - oy, aim[0] - ox)
    length = math.hypot(aim[0] - ox, aim[1] - oy) + 60
    rng = random.Random(seed + int(t * 20))
    for color, k, a in ((LAVA, 1.0, 0.75), (EMBER, 0.7, 0.85), (CORE, 0.38, 0.9)):
        edge_l, edge_r = [], []
        for i in range(11):
            s = i / 10
            reach = length * s
            wobble = rng.uniform(-0.05, 0.05)
            half = spread * k * (0.25 + 0.75 * s) + wobble
            edge_l.append(
                (ox + reach * math.cos(angle - half), oy + reach * math.sin(angle - half))
            )
            edge_r.append(
                (ox + reach * math.cos(angle + half), oy + reach * math.sin(angle + half))
            )
        draw.polygon(edge_l + edge_r[::-1], fill=fade(color, a))
    for i in range(8):
        s = (t * 2.5 + i / 8) % 1
        cx, cy = ox + length * s * math.cos(angle), oy + length * s * math.sin(angle)
        r = 14 + 34 * s
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fade(CORE, 0.25 * (1 - s)))


def spotlight(draw, size, center, radius, alpha, color=(10, 5, 12)):
    """Darken everything except a soft circle around `center`."""
    w, h = size
    x, y = center
    outer = radius * 1.5
    for box in (
        (0, 0, w, y - outer),
        (0, y + outer, w, h),
        (0, y - outer, x - outer, y + outer),
        (x + outer, y - outer, w, y + outer),
    ):
        if box[2] > box[0] and box[3] > box[1]:
            draw.rectangle(box, fill=fade(color, alpha))
    draw.ellipse(
        (x - outer, y - outer, x + outer, y + outer),
        outline=fade(color, alpha),
        width=int(outer - radius),
    )
    for i in range(4):
        r = radius - i * 8
        draw.ellipse(
            (x - r, y - r, x + r, y + r), outline=fade(color, alpha * (0.5 - i * 0.12)), width=8
        )


def kiln_air(draw, t, c, strength=0.3):
    """Shared kiln atmosphere: hot edges and sparks rising through the frame."""
    heat_vignette(draw, c.size, strength * 0.7 * (0.6 + 0.4 * pulse(t)))
    embers(draw, (0, 300, c.size[0], c.size[1]), t, seed=7, count=26, alpha=0.9)


def main_target(c, fallback=None):
    if c.targets:
        return c.targets[0]
    if c.team:
        return c.team[0]
    return fallback or (c.boss[0], c.boss[1] + 420)


# ----- kiln dungeon (熔岩猪窑): scenes -----


def ember_pass(draw, t, c):
    """围炉传火: the party's hits gather as embers on the boss, then hurl back at one pig."""
    kiln_air(draw, t, c)
    bx, by = c.boss
    orb = (bx, by - 30)
    target = main_target(c)
    gather = span(t, 0.0, 0.5)
    for i, (x, y) in enumerate(c.team or [target]):
        for j in range(8):
            p = gather * 1.35 - j * 0.08
            if not 0 < p < 1:
                continue
            k = ease(p)
            mx = x + (orb[0] - x) * k + 70 * math.sin(k * math.pi) * (1 if i % 2 else -1)
            my = y + (orb[1] - y) * k - 160 * math.sin(k * math.pi)
            r = 6 + 6 * (1 - k)
            draw.ellipse(
                (mx - r * 2.2, my - r * 2.2, mx + r * 2.2, my + r * 2.2), fill=fade(EMBER, 0.35)
            )
            draw.ellipse((mx - r, my - r, mx + r, my + r), fill=fade(CORE, 0.95))
    throw = span(t, 0.5, 0.78)
    if throw <= 0:
        r = 26 + 64 * ease(gather)
        for k, color, a in ((1.7, LAVA, 0.3), (1.0, EMBER, 0.9), (0.55, CORE, 1.0)):
            draw.ellipse(
                (orb[0] - r * k, orb[1] - r * k, orb[0] + r * k, orb[1] + r * k),
                fill=fade(color, a),
            )
        for i in range(10):
            a = t * 9 + i * 0.63
            px, py = orb[0] + r * 1.4 * math.cos(a), orb[1] + r * 0.6 * math.sin(a)
            draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill=fade(CORE, 1))
        c.text("余烬", (orb[0], orb[1] - r - 46), 40, fade(CORE, min(1.0, t * 4)), True, 5)
    elif throw < 1:
        k = ease(throw)
        head = lerp(orb, target, k)
        head = (head[0], head[1] - 180 * math.sin(math.pi * k))
        for i in range(9):
            back = max(0.0, k - i * 0.05)
            tx, ty = lerp(orb, target, back)
            ty -= 180 * math.sin(math.pi * back)
            r = 46 - i * 4
            draw.ellipse(
                (tx - r, ty - r, tx + r, ty + r), fill=fade(EMBER if i else CORE, 0.8 - i * 0.08)
            )
        tongue(draw, (head[0], head[1] + 30), 130, 50, t, 3, lean=-0.4)
    hit = span(t, 0.76, 1.0)
    if hit > 0:
        x, y = target
        shockring(draw, (x, y + 70), 60 + 200 * ease(hit), 1 - hit)
        blaze(draw, (x, y + 70), 90, 220 * pulse(min(1.0, hit * 1.4)), t, 5, alpha=1 - hit * 0.5)
        sparks(draw, target, hit, CORE, seed=4, count=24)
        slam(c, f"真伤 -{c.tag}" if c.tag else "真伤", (x, y - 170), t, 0.78, 50, CORE)


def crowd_heat(draw, t, c):
    """人多更暖: every pig standing round the fire feeds it; the boss grows hotter."""
    count = len(c.team)
    heat = count / 4
    kiln_air(draw, t, c, 0.2 + 0.25 * heat)
    haze(draw, c.size, 560, 1180, t, 0.25 + 0.3 * heat)
    bx, by = c.boss
    fire = (bx, by + 190)
    grow = ease(span(t, 0.0, 0.6))
    for i, (x, y) in enumerate(c.team):
        start = (x, y + 40)
        points = []
        for s in range(13):
            k = s / 12
            px, py = lerp(start, fire, k)
            points.append((px + 14 * math.sin(k * 12 + t * 20 + i), py))
        draw.line(points, fill=fade(EMBER, 0.65 * grow), width=7)
        draw.line(points, fill=fade(CORE, 0.5 * grow), width=2)
        p = (t * 1.8 + i * 0.25) % 1
        dx, dy = lerp(start, fire, p)
        draw.ellipse((dx - 9, dy - 9, dx + 9, dy + 9), fill=fade(CORE, grow))
        tongue(draw, (x, y - 110), 70 * grow, 22, t, 20 + i)
        slam(c, "+1", (x + 46, y - 150), t, 0.18 + i * 0.08, 30, CORE, 0.95)
    bonfire(draw, fire, 70 + 130 * heat * grow, t, seed=2)
    thermometer(draw, (650, 620), heat * grow, LAVA)
    slam(c, f"×{count}", (bx - 210, by - 120), t, 0.35, 72, CORE)
    if c.tag:
        slam(c, f"攻击 {c.tag}", (bx, by - 210), t, 0.5, 46, (255, 120, 80))


def roast_seat(draw, t, c):
    """烤位轮值: a spit and red-hot grate drop over today's seat; neighbours warm up."""
    kiln_air(draw, t, c, 0.22)
    x, y = main_target(c)
    drop = ease(span(t, 0.0, 0.35))
    heat = span(t, 0.2, 0.7)
    draw.polygon(
        ((x - 50, 0), (x + 50, 0), (x + 150, y + 100), (x - 150, y + 100)),
        fill=fade(EMBER, 0.16 + 0.1 * pulse(t)),
    )
    blaze(draw, (x, y + 118), 120, 70 + 70 * heat, t, seed=11, count=7, alpha=0.4 + 0.6 * heat)
    grate(draw, (x, y + 92), 220, heat)
    top = y - 120 - 260 * (1 - drop)
    for side in (-1, 1):
        px = x + side * 128
        draw.line((px, y + 92, px, top), fill=fade((70, 60, 60), 1), width=12)
    draw.line((x - 150, top, x + 150, top), fill=fade((160, 160, 170), 1), width=8)
    crank = t * 14
    hx, hy = x + 150, top
    draw.line(
        (hx, hy, hx + 34 * math.cos(crank), hy + 34 * math.sin(crank)),
        fill=fade((160, 160, 170), 1),
        width=6,
    )
    smoke(draw, (x, top - 40), (t * 1.4) % 1, (120, 100, 95), seed=3)
    plate_y = top - 64
    draw.rounded_rectangle(
        (x - 78, plate_y - 30, x + 78, plate_y + 30),
        radius=14,
        fill=fade(LAVA, drop),
        outline=fade(CORE, drop),
        width=3,
    )
    c.text("烤位", (x, plate_y), 38, fade(WHITE, drop), True, 4)
    team = sorted(c.team, key=lambda p: p[0])
    if (x, y) in team:
        index = team.index((x, y))
        for n in (index - 1, index + 1):
            if 0 <= n < len(team):
                nx, ny = team[n]
                for k in range(3):
                    p = (t * 1.3 + k / 3) % 1
                    curl = [
                        (nx - 24 + k * 24 + 10 * math.sin(p * 8 + s), ny - 60 - p * 120 - s * 10)
                        for s in range(5)
                    ]
                    draw.line(curl, fill=fade(STEAM, 0.7 * (1 - p)), width=5)
                rising(draw, (nx, ny), (t * 1.5) % 1, GREEN, seed=n)
                arrows(
                    draw,
                    (nx + (1 if n > index else -1) * 30, ny - 150),
                    span(t, 0.4, 1.0),
                    True,
                    GREEN,
                )


def green_cross(draw, center, size, color, alpha=1.0):
    x, y = center
    arm = size * 0.32
    draw.rectangle((x - arm, y - size, x + arm, y + size), fill=fade(color, alpha))
    draw.rectangle((x - size, y - arm, x + size, y + arm), fill=fade(color, alpha))


def anti_heal(draw, t, c):
    """越劝越火: the healer's comfort flies over, catches fire and feeds the boss's head."""
    bx, by = c.boss
    head = (bx, by - 130)
    healer = main_target(c, (bx - 220, by + 420))
    kiln_air(draw, t, c, 0.18 + 0.3 * span(t, 0.4, 1.0))
    hx, hy = healer
    talk = span(t, 0.0, 0.25)
    burn = span(t, 0.22, 0.5)
    if t < 0.62:
        box = (hx - 92, hy - 236, hx + 92, hy - 170)
        chat_bubble(draw, box, (hx - 10, hy - 140), WHITE, talk * (1 - span(t, 0.45, 0.62)))
        c.text("别气啦~", (hx, hy - 203), 30, fade((60, 160, 90), talk * (1 - burn)), True)
        if burn > 0:
            blaze(draw, (hx, hy - 170), 80, 90 * burn, t, seed=9, count=5, alpha=burn)
    fly = span(t, 0.12, 0.62)
    for i in range(6):
        p = fly * 1.4 - i * 0.07
        if not 0 < p < 1:
            continue
        k = ease(p)
        mx, my = lerp((hx + (i - 2.5) * 22, hy - 80), head, k)
        my -= 150 * math.sin(math.pi * k)
        if k < 0.55:
            color = (
                int(GREEN[0] + (LAVA[0] - GREEN[0]) * k / 0.55),
                int(GREEN[1] + (EMBER[1] - GREEN[1]) * k / 0.55),
                int(GREEN[2] + (LAVA[2] - GREEN[2]) * k / 0.55),
            )
            glow(draw, (mx, my), 0.5, color, 50)
            green_cross(draw, (mx, my), 30, color)
            green_cross(draw, (mx, my), 14, WHITE, 0.8)
        else:
            tongue(draw, (mx, my + 20), 70, 26, t, 30 + i, lean=-0.3)
    rage = span(t, 0.5, 0.9)
    if rage > 0:
        for i, dx in enumerate((-60, 0, 60)):
            tongue(
                draw,
                (head[0] + dx, head[1] + 10),
                (120 + 70 * (i == 1)) * ease(rage),
                40,
                t,
                40 + i,
                lean=dx / 300,
            )
        for i, (dx, dy) in enumerate(((-150, -40), (150, -70))):
            anger(draw, (head[0] + dx, head[1] + dy), 40 + 10 * math.sin(t * 30 + i), rage)
        for side in (-1, 1):
            p = (t * 2.2) % 1
            sx = head[0] + side * (100 + 60 * p)
            draw.ellipse(
                (
                    sx - 22 - 20 * p,
                    head[1] - 40 - 50 * p - 22,
                    sx + 22 + 20 * p,
                    head[1] - 40 - 50 * p + 22,
                ),
                fill=fade(STEAM, 0.7 * (1 - p) * rage),
            )
        label = f"火气 {c.tag}/6" if c.tag else "火气 ↑"
        slam(c, label, (bx, by - 300), t, 0.55, 54, (255, 110, 80))


def flame_lock(draw, t, c):
    """对线点名: lights out on everyone else, a red line and crosshair pin one pig."""
    bx, by = c.boss
    x, y = main_target(c)
    dim = min(1.0, t * 4) * (1 - span(t, 0.9, 1.0))
    spotlight(draw, c.size, (x, y - 10), 140, 0.62 * dim)
    draw.ellipse((bx - 170, by - 170, bx + 170, by + 170), outline=fade(LAVA, 0.6 * dim), width=10)
    reach = ease(span(t, 0.1, 0.4))
    eye = (bx, by - 40)
    rng = random.Random(int(t * 18))
    points = [eye]
    for i in range(1, 9):
        px, py = lerp(eye, (x, y), i / 9 * reach)
        points.append((px + rng.uniform(-18, 18), py + rng.uniform(-10, 10)))
    points.append(lerp(eye, (x, y), reach))
    draw.line(points, fill=fade(LAVA, 0.9), width=16)
    draw.line(points, fill=fade(CORE, 1.0), width=5)
    lock = ease(span(t, 0.25, 0.6))
    r = 190 - 100 * lock
    spin = t * 3
    draw.ellipse((x - r, y - r, x + r, y + r), outline=fade(LAVA, 1), width=6)
    for i in range(4):
        a = spin + i * math.pi / 2
        draw.line(
            (
                x + (r - 30) * math.cos(a),
                y + (r - 30) * math.sin(a),
                x + (r + 34) * math.cos(a),
                y + (r + 34) * math.sin(a),
            ),
            fill=fade(CORE, 1),
            width=7,
        )
    mid = lerp(eye, (x, y), 0.5)
    if reach >= 1:
        badge = ease(span(t, 0.4, 0.55))
        draw.ellipse(
            (mid[0] - 50 * badge, mid[1] - 50 * badge, mid[0] + 50 * badge, mid[1] + 50 * badge),
            fill=fade(LAVA, 1),
            outline=fade(CORE, 1),
            width=5,
        )
        c.text("VS", mid, int(44 * badge) + 1, fade(WHITE, badge), True, 4)
    box = (bx + 70, by - 230, bx + 270, by - 160)
    chat_bubble(draw, box, (bx + 60, by - 120), (255, 240, 235), span(t, 0.3, 0.45))
    c.text("就你了", (bx + 170, by - 195), 32, fade(LAVA, span(t, 0.3, 0.45)), True)
    slam(c, "对线", (x, y - 190), t, 0.55, 64, CORE)


def read_receipt(draw, t, c):
    """已读不回: the hit lands as an unanswered chat; ignored too long, the boss bursts."""
    bx, by = c.boss
    if c.tag == "burst":
        kiln_air(draw, t, c, 0.25 + 0.4 * span(t, 0.5, 1.0))
        cx, cy = bx, by - 230
        build = span(t, 0.0, 0.55)
        draw.pieslice(
            (cx - 110, cy - 110, cx + 110, cy + 110),
            180,
            360,
            fill=fade((30, 26, 34), 0.9),
            outline=fade(WHITE, 0.9),
            width=5,
        )
        for i, color in enumerate(((90, 200, 120), (250, 210, 80), (240, 70, 60))):
            draw.arc(
                (cx - 92, cy - 92, cx + 92, cy + 92),
                180 + i * 60,
                240 + i * 60,
                fill=fade(color, 1),
                width=16,
            )
        needle = math.pi + math.pi * min(1.0, ease(build) * 1.05) + 0.08 * math.sin(t * 60) * build
        draw.line(
            (cx, cy, cx + 86 * math.cos(needle), cy + 86 * math.sin(needle)),
            fill=fade(WHITE, 1),
            width=7,
        )
        draw.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill=fade(WHITE, 1))
        for side in (-1, 1):
            for k in range(3):
                p = (t * 2.4 + k / 3) % 1
                sx = bx + side * (90 + 120 * p)
                sy = by - 120 - 70 * p
                r = 18 + 30 * p
                draw.ellipse((sx - r, sy - r, sx + r, sy + r), fill=fade(STEAM, 0.75 * (1 - p)))
        anger(draw, (bx - 140, by - 110), 40 + 8 * math.sin(t * 40), build)
        anger(draw, (bx + 150, by - 60), 34 + 8 * math.sin(t * 40 + 1), build)
        pop = span(t, 0.55, 1.0)
        if pop > 0:
            shockring(draw, (bx, by), 80 + 380 * ease(pop), 1 - pop, LAVA, flat=0.8, width=18)
            blaze(draw, (bx, by + 120), 160, 260 * pulse(pop), t, seed=21, count=8)
            shatter(draw, (cx, cy), pop, CORE, seed=5)
            flash(draw, c.size, min(1.0, pop * 1.6), (255, 120, 80), 0.25)
            slam(c, "憋炸了", (bx, by + 240), t, 0.58, 70, CORE)
        return
    kiln_air(draw, t, c, 0.16)
    hitter = main_target(c, (bx - 200, by + 420))
    rise = ease(span(t, 0.0, 0.25))
    left, top = 190, 130 + 40 * (1 - rise)
    right, bottom = 530, 390 + 40 * (1 - rise)
    draw.rounded_rectangle(
        (left - 10, top - 10, right + 10, bottom + 10), radius=36, fill=fade((20, 20, 26), rise)
    )
    draw.rounded_rectangle((left, top, right, bottom), radius=28, fill=fade((236, 236, 240), rise))
    draw.rectangle((left, top + 16, right, top + 56), fill=fade((210, 70, 60), rise))
    c.text("红温猪", ((left + right) / 2, top + 36), 26, fade(WHITE, rise), True)
    lines = (("在吗？", 0.15), ("看招！", 0.3), ("回一下嘛", 0.45))
    for i, (word, start) in enumerate(lines):
        k = ease(span(t, start, start + 0.12))
        y = top + 100 + i * 72
        x0 = left + 22 - 60 * (1 - k)
        box = (x0, y - 28, x0 + 190, y + 28)
        draw.rounded_rectangle(box, radius=20, fill=fade((120, 210, 120), k))
        c.text(word, (x0 + 95, y), 28, fade((20, 60, 20), k), True)
        mark = span(t, start + 0.1, start + 0.2)
        c.text("已读", (x0 + 240, y + 14), 20, fade((140, 140, 150), mark), True)
        ticks(draw, (x0 + 200, y + 10), 20, (90, 150, 230), mark)
    swipe = span(t, 0.55, 0.75)
    if swipe > 0:
        mid = lerp(hitter, (bx, by), 0.65)
        c.text("伤害", mid, 44, fade(WHITE, 1 - span(t, 0.85, 1.0)), True, 4)
        length = 90 * ease(swipe)
        draw.line(
            (mid[0] - length, mid[1] - length * 0.5, mid[0] + length, mid[1] + length * 0.5),
            fill=fade(RED, 1),
            width=12,
        )
        draw.line(
            (mid[0] - length, mid[1] + length * 0.5, mid[0] + length, mid[1] - length * 0.5),
            fill=fade(RED, 1),
            width=12,
        )
    slam(c, "已读不回", (bx, by + 200), t, 0.6, 58, (210, 210, 220))


def blessing(draw, center, kind, alpha=1.0):
    """The stolen gift: a shield bubble or a green buff orb."""
    x, y = center
    if kind == "shield":
        draw.ellipse(
            (x - 46, y - 46, x + 46, y + 46),
            fill=fade(BLUE, 0.35 * alpha),
            outline=fade((200, 230, 255), alpha),
            width=6,
        )
        draw.polygon(
            (
                (x, y - 26),
                (x + 22, y - 14),
                (x + 18, y + 14),
                (x, y + 28),
                (x - 18, y + 14),
                (x - 22, y - 14),
            ),
            fill=fade((200, 230, 255), alpha),
        )
    else:
        draw.ellipse(
            (x - 44, y - 44, x + 44, y + 44),
            fill=fade(GREEN, 0.4 * alpha),
            outline=fade((210, 255, 210), alpha),
            width=6,
        )
        arrows(draw, (x, y + 18), 0.0, True, (230, 255, 230))


def misbless(draw, t, c):
    """祥瑞错位: a golden cloud scoops a pig's buff or shield and crowns the boss with it."""
    kiln_air(draw, t, c, 0.15)
    bx, by = c.boss
    vx, vy = main_target(c)
    gift_home = (vx, vy - 150)
    crown_at = (bx, by - 210)
    kind = c.tag or "buff"
    swoop = ease(span(t, 0.0, 0.3))
    carry = ease(span(t, 0.3, 0.72))
    if carry <= 0:
        cloud = lerp((-160, vy - 420), (gift_home[0], gift_home[1] - 70), swoop)
        gift = gift_home
    else:
        cloud = lerp((gift_home[0], gift_home[1] - 70), (crown_at[0], crown_at[1] - 70), carry)
        cloud = (cloud[0], cloud[1] - 200 * math.sin(math.pi * carry))
        gift = (cloud[0], cloud[1] + 70)
    for i in range(14):
        back = max(0.0, (carry if carry > 0 else swoop) - i * 0.04)
        if carry > 0:
            px, py = lerp((gift_home[0], gift_home[1] - 70), (crown_at[0], crown_at[1] - 70), back)
            py -= 200 * math.sin(math.pi * back)
        else:
            px, py = lerp((-160, vy - 420), (gift_home[0], gift_home[1] - 70), back)
        star(draw, (px + 60, py + 10), 12 - i * 0.6, fade(GOLD, 0.9 - i * 0.06))
    land = span(t, 0.72, 1.0)
    if land > 0:
        gift = crown_at
        rays(draw, crown_at, land, GOLD, 18, 220)
        shockring(draw, (bx, by + 150), 80 + 160 * ease(land), 1 - land, GOLD)
    blessing(draw, gift, kind)
    lucky_cloud(draw, cloud, 92, 1 - span(t, 0.85, 1.0))
    if carry > 0:
        for i in range(3):
            c.text(
                "？",
                (vx - 50 + i * 50, vy - 120 - 18 * math.sin(t * 12 + i)),
                40,
                fade(WHITE, 1 - land),
                True,
                4,
            )
    slam(c, "赐错了！", (bx, by + 230), t, 0.74, 60, GOLD)


def short_takeoff(draw, t, c):
    """短腿起飞: paw at the ground, then either wobble into the sky or belly-flop."""
    bx, by = c.boss
    feet = (bx, by + 150)
    if c.tag == "fly":
        kiln_air(draw, t, c, 0.15)
        light_beam(draw, (bx, -40), (bx, feet[1]), 40, 190, t, GOLD)
        for i in range(12):
            p = (t * 2 + i / 12) % 1
            x = bx - 230 + i * 42
            draw.line(
                (x, feet[1] - p * 700, x, feet[1] - p * 700 - 70),
                fill=fade(WHITE, 0.6 * (1 - p)),
                width=4,
            )
        trail = [
            (bx + 80 * math.sin(k * 14) * (1 - k), by - 40 - 620 * k)
            for k in (i / 30 * ease(t) for i in range(31))
        ]
        draw.line(trail, fill=fade(CORE, 0.9), width=10)
        shadow = 160 * (1 - 0.7 * ease(t))
        draw.ellipse(
            (bx - shadow, feet[1] - shadow * 0.2, bx + shadow, feet[1] + shadow * 0.2),
            fill=fade(CHAR, 0.6),
        )
        slam(c, "飞……飞起来了？", (bx, by - 260), t, 0.3, 50, GOLD)
        return
    if c.tag == "crash":
        kiln_air(draw, t, c, 0.3)
        fall = ease(span(t, 0.0, 0.42))
        belly_y = -220 + (feet[1] - 20 + 220) * fall
        shadow = 60 + 140 * fall
        draw.ellipse(
            (bx - shadow, feet[1] - shadow * 0.22, bx + shadow, feet[1] + shadow * 0.22),
            fill=fade(CHAR, 0.7),
        )
        draw.ellipse(
            (bx - 200, belly_y - 110, bx + 200, belly_y + 50),
            fill=fade((255, 175, 170), 1),
            outline=fade((150, 80, 80), 1),
            width=6,
        )
        draw.arc(
            (bx - 18, belly_y - 40, bx + 18, belly_y - 10),
            20,
            160,
            fill=fade((150, 80, 80), 1),
            width=5,
        )
        for dx in (-130, 130):
            draw.ellipse(
                (bx + dx - 30, belly_y + 20, bx + dx + 30, belly_y + 70),
                fill=fade((255, 175, 170), 1),
                outline=fade((150, 80, 80), 1),
                width=4,
            )
        boom = span(t, 0.42, 1.0)
        if boom > 0:
            cracks(draw, feet, 420, ease(boom), seed=6, count=10)
            shockring(draw, feet, 80 + 520 * ease(boom), 1 - boom, EMBER, width=18)
            rng = random.Random(4)
            for _ in range(14):
                a = rng.uniform(math.pi * 1.05, math.pi * 1.95)
                d = rng.uniform(120, 320) * ease(boom)
                rx = bx + d * math.cos(a) * 1.6
                ry = feet[1] + d * math.sin(a) + 400 * boom**2
                s = rng.uniform(8, 18)
                draw.polygon(
                    ((rx, ry - s), (rx + s, ry), (rx, ry + s * 0.8), (rx - s * 0.9, ry)),
                    fill=fade((90, 60, 50), 1 - boom * 0.5),
                )
            for i, hero in enumerate(c.targets or c.team):
                burst(draw, hero, span(t, 0.48 + i * 0.04, 0.85), LAVA, 100, 12)
            flash(draw, c.size, min(1.0, boom * 1.8), (255, 200, 150), 0.3)
            slam(c, "啪叽！", (bx, by - 230), t, 0.44, 84, CORE)
        return
    if c.tag == "land":
        kiln_air(draw, t, c, 0.25)
        drop = ease(span(t, 0.0, 0.35))
        shadow = 50 + 150 * drop
        draw.ellipse(
            (bx - shadow, feet[1] - shadow * 0.22, bx + shadow, feet[1] + shadow * 0.22),
            fill=fade(CHAR, 0.7),
        )
        meteor(draw, (bx + 120, -200), feet, drop, EMBER)
        splash = span(t, 0.35, 1.0)
        if splash > 0:
            shockring(draw, feet, 60 + 420 * ease(splash), 1 - splash, LAVA, width=16)
            blaze(draw, feet, 150, 200 * pulse(splash), t, seed=31, count=7)
            for i, hero in enumerate(c.targets or c.team):
                k = ease(span(t, 0.38 + i * 0.05, 0.75 + i * 0.05))
                px, py = lerp(feet, hero, k)
                py -= 220 * math.sin(math.pi * k)
                draw.ellipse((px - 18, py - 18, px + 18, py + 18), fill=fade(LAVA, 1))
                draw.ellipse((px - 9, py - 9, px + 9, py + 9), fill=fade(CORE, 1))
                if k >= 1:
                    blaze(draw, (hero[0], hero[1] + 70), 50, 110, t, seed=40 + i, count=4)
            slam(c, "落地！", (bx, by - 230), t, 0.4, 64, CORE)
        return
    kiln_air(draw, t, c, 0.2)
    rng = random.Random(int(t * 10))
    for i in range(10):
        side = -1 if (int(t * 12) + i) % 2 else 1
        p = (t * 3 + i / 10) % 1
        dx = side * (60 + 200 * p)
        r = 22 + 40 * p
        draw.ellipse(
            (
                bx + dx - r,
                feet[1] - 10 - 30 * p - r * 0.6,
                bx + dx + r,
                feet[1] - 10 - 30 * p + r * 0.6,
            ),
            fill=fade((150, 110, 80), 0.75 * (1 - p)),
        )
    for i in range(5):
        y = feet[1] + 18 + i * 8
        draw.line(
            (bx - 120 + rng.uniform(-10, 10), y, bx + 120 + rng.uniform(-10, 10), y),
            fill=fade(CORE, 0.6),
            width=3,
        )
    charge = ease(span(t, 0.0, 0.9))
    draw.arc(
        (bx - 180, by - 180, bx + 180, by + 180),
        -90,
        -90 + 360 * charge,
        fill=fade(GOLD, 1),
        width=14,
    )
    draw.arc(
        (bx - 180, by - 180, bx + 180, by + 180),
        -90,
        -90 + 360 * charge,
        fill=fade(CORE, 1),
        width=4,
    )
    c.text("防御 ↓", (bx + 200, by - 160), 36, fade((255, 120, 120), span(t, 0.2, 0.35)), True, 4)
    for x, y in c.team:
        k = (t * 2) % 1
        draw.polygon(
            ((x, y - 140 - 30 * k), (x - 22, y - 110 - 30 * k), (x + 22, y - 110 - 30 * k)),
            fill=fade(CORE, 0.9),
        )
    slam(c, "打断它！", (bx, by + 260), t, 0.35, 56, CORE)


def seat_breath(draw, t, c):
    """龙息扫座: one roaring breath sweeps along a row of seats and scorches them."""
    bx, by = c.boss
    mouth = (bx, by + 10)
    hits = sorted(c.targets or c.team[:3], key=lambda p: p[0])
    kiln_air(draw, t, c, 0.3)
    if not hits:
        fire_cone(draw, mouth, (bx, by + 420), t)
        return
    sweep = span(t, 0.15, 0.78)
    first, last = hits[0], hits[-1]
    start = (first[0] - 90, first[1] + 40)
    end = (last[0] + 90, last[1] + 40)
    if 0 < t < 0.9:
        charge = span(t, 0.0, 0.15)
        for k, color in ((1.0, LAVA), (0.6, EMBER), (0.3, CORE)):
            r = 50 * charge * k
            draw.ellipse(
                (mouth[0] - r, mouth[1] - r, mouth[0] + r, mouth[1] + r), fill=fade(color, 0.9)
            )
        if sweep > 0:
            fire_cone(draw, mouth, lerp(start, end, ease(sweep)), t, spread=0.3, seed=3)
    aim_x = lerp(start, end, ease(sweep))[0]
    for i, (x, y) in enumerate(hits):
        if aim_x < x - 40:
            continue
        burnt = span(t, 0.15 + (i + 0.5) / len(hits) * 0.63, 1.0)
        draw.ellipse((x - 110, y + 50, x + 110, y + 100), fill=fade(CHAR, 0.75))
        blaze(draw, (x, y + 76), 80, 150 * (1 - 0.4 * burnt), t, seed=50 + i, count=5)
        draw.rounded_rectangle(
            (x - 30, y - 176, x + 30, y - 120),
            radius=12,
            fill=fade(LAVA, 1),
            outline=fade(CORE, 1),
            width=3,
        )
        c.text("灼", (x, y - 148), 32, fade(WHITE, 1), True)
    slam(c, "龙息扫座", (360, 830), t, 0.7, 64, CORE)


SCENES = {
    ("goblin-pig", 0): Scene("lurk", (90, 110, 80), lurk),
    ("goblin-pig", 1): Scene("stench", (150, 180, 60), stench),
    ("goblin-pig", 2): Scene("lazy", (180, 160, 120), lazy),
    ("frozen-pig", 0): Scene("freeze", (150, 225, 255), freeze_team),
    ("frozen-pig", 1): Scene("ice_shell", (150, 225, 255), ice_shell),
    ("frozen-pig", 2): Scene("melt", (255, 140, 110), melt),
    ("everest-pig", 0): Scene("altitude", (180, 200, 230), altitude),
    ("everest-pig", 1): Scene("summit", (200, 210, 230), summit),
    ("everest-pig", 2): Scene("avalanche", (235, 245, 255), avalanche, heavy=True),
    ("error-404-pig", 0): Scene("unobserved", (120, 200, 255), unobserved),
    ("error-404-pig", 1): Scene("page_404", (120, 200, 255), page_404),
    ("error-404-pig", 2): Scene("rollback", (120, 200, 255), rollback),
    ("mechanical-pig", 0): Scene("lock_on", (255, 70, 70), lock_on),
    ("mechanical-pig", 1): Scene("overheat", (255, 110, 60), overheat),
    ("mechanical-pig", 2): Scene("bug", (90, 170, 90), system_bug),
    ("cyberpunk-pig", 0): Scene("hack", (80, 255, 140), hack),
    ("cyberpunk-pig", 1): Scene("evolve", (255, 80, 200), evolve),
    ("cyberpunk-pig", 2): Scene("overclock", (255, 80, 200), overclock, heavy=True),
    ("demon-pig", 0): Scene("curse", (175, 70, 200), curse),
    ("demon-pig", 1): Scene("prank", (230, 70, 90), prank),
    ("demon-pig", 2): Scene("grin", (230, 50, 50), grin),
    ("chained_crown_pig", 0): Scene("chains", (180, 180, 190), chains_bind),
    ("chained_crown_pig", 1): Scene("king_returns", (255, 210, 90), king_returns, heavy=True),
    ("chained_crown_pig", 2): Scene("overawe", (130, 100, 170), overawe),
    ("pig_god", 0): Scene("luck", (255, 215, 100), luck),
    ("pig_god", 1): Scene("oracle", (255, 225, 140), oracle),
    ("pig_god", 2): Scene("guardians", (255, 240, 200), guardians),
    ("zombie-pig", 0): Scene("zombie_poison", (120, 200, 80), zombie_poison),
    ("zombie-pig", 1): Scene("zombie_limp", (110, 140, 90), zombie_limp),
    ("zombie-pig", 2): Scene("zombie_rise", (110, 200, 90), zombie_rise),
    ("pighub0876", 0): Scene("yin_yang", (230, 230, 240), yin_yang),
    ("pighub0876", 1): Scene("possess", (170, 130, 240), possess),
    ("pighub0876", 2): Scene("pincer", (190, 140, 255), pincer),
    ("skeleton-pig", 0): Scene("reassemble", (240, 235, 220), reassemble),
    ("skeleton-pig", 1): Scene("bone_thin", (240, 235, 220), bone_thin),
    ("skeleton-pig", 2): Scene("cook", (255, 130, 50), cook),
    ("pighub0233", 0): Scene("fire_wheels", (255, 110, 40), fire_wheels),
    ("pighub0233", 1): Scene("red_ribbon", (225, 40, 70), red_ribbon),
    ("pighub0233", 2): Scene("golden_ring", (255, 205, 80), golden_ring, heavy=True),
    ("pighub0007", 0): Scene("tide", (60, 140, 230), tide),
    ("pighub0007", 1): Scene("decree", (210, 60, 60), decree),
    ("pighub0007", 2): Scene("dragon_scales", (255, 205, 100), dragon_scales),
    ("pighub0830", 0): Scene("pressure", (20, 50, 110), pressure),
    ("pighub0830", 1): Scene("lantern", (255, 230, 120), lantern),
    ("pighub0830", 2): Scene("abyss", (40, 80, 170), abyss),
    ("alien-pig", 0): Scene("abduct", (140, 255, 170), abduct),
    ("alien-pig", 1): Scene("doubt", (170, 130, 240), doubt),
    ("alien-pig", 2): Scene("mothership", (140, 255, 170), mothership, heavy=True),
    ("pighub0872", 0): Scene("cluster", (210, 190, 255), cluster),
    ("pighub0872", 1): Scene("gravity_pull", (200, 180, 255), gravity_pull),
    ("pighub0872", 2): Scene("supernova", (255, 245, 220), supernova, heavy=True),
    ("pighub0336", 0): Scene("rings", (200, 210, 255), rings),
    ("pighub0336", 1): Scene("gravity_field", (170, 150, 255), gravity_field),
    ("pighub0336", 2): Scene("meteor_shower", (255, 150, 60), meteor_shower, heavy=True),
    ("pighub0315", 0): Scene("ember_pass", (255, 140, 60), ember_pass),
    ("pighub0315", 1): Scene("crowd_heat", (255, 150, 70), crowd_heat),
    ("pighub0315", 2): Scene("roast_seat", (255, 160, 50), roast_seat),
    ("pighub0710", 0): Scene("anti_heal", (255, 70, 50), anti_heal),
    ("pighub0710", 1): Scene("flame_lock", (255, 60, 45), flame_lock),
    ("pighub0710", 2): Scene("read_receipt", (255, 90, 60), read_receipt, heavy=True),
    ("pighub0116", 0): Scene("misbless", (255, 200, 90), misbless),
    ("pighub0116", 1): Scene("short_takeoff", (255, 170, 70), short_takeoff, heavy=True),
    ("pighub0116", 2): Scene("seat_breath", (255, 110, 40), seat_breath),
}
FALLBACK = Scene(
    "generic",
    GOLD,
    lambda draw, t, c: (rays(draw, c.boss, t, GOLD, 16, 260), dizzy(draw, c.boss, t)),
)


def scene_for(slot: str, index: int) -> Scene:
    return SCENES.get((slot, index), FALLBACK)
