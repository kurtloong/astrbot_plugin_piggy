import io
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from core.rendering import Canvas, Typeset, finish, render_dummy_poster


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_plain_text_never_loads_fallback_fonts(self):
        typeset = Typeset(self.root)
        runs = typeset.runs("今日小猪 ABC 123", 24)
        self.assertEqual(len(runs), 1)
        self.assertIs(runs[0][1], typeset.face(24))
        self.assertEqual(len(typeset.paths), 1)

    def test_emoji_symbols_and_fancy_letters_use_fallbacks(self):
        typeset = Typeset(self.root)
        for text in ("🐷", "☒", "𝓐"):
            runs = typeset.runs(f"猪{text}", 24)
            self.assertEqual([part for part, _ in runs], ["猪", text], text)
            self.assertIsNot(runs[1][1], typeset.face(24), text)

    def test_joiners_and_undrawable_characters_are_dropped(self):
        typeset = Typeset(self.root)
        runs = typeset.runs("猪\u200d\ufe0f\ue000猪", 24)
        self.assertEqual([part for part, _ in runs], ["猪猪"])

    def test_special_names_render_and_wrap(self):
        canvas = Canvas(self.root, 400, 120)
        canvas.text("🐷☒𝓐 小猪" * 6, 10, 10, 24, width=300)
        lines = canvas.wrap("🐷小猪" * 20, 24, 200)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(canvas.measure(line, 24) <= 200 for line in lines))
        finish(canvas.image)

    def test_flat_cards_become_palette_png_but_photos_stay_rgb(self):
        flat = Canvas(self.root, 600, 400)
        flat.text("小猪收集册", 20, 20, 40, bold=True)
        with Image.open(io.BytesIO(finish(flat.image).data)) as card:
            self.assertEqual(card.mode, "P")
        rng = random.Random(1)
        noisy = Image.frombytes("RGB", (300, 300), bytes(rng.randrange(256) for _ in range(270000)))
        with Image.open(io.BytesIO(finish(noisy).data)) as card:
            self.assertEqual(card.mode, "RGB")

    def test_dummy_poster_contains_raid_style_stats_and_cast_log(self):
        labels = []
        original = ImageDraw.ImageDraw.text

        def record(canvas, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original(canvas, xy, text, *args, **kwargs)

        poster = {
            "subtitle": "玩家的测试猪 · 3 回合 DPS 测试",
            "pig": "测试猪",
            "asset": "",
            "level": 5,
            "role": "输出",
            "style": "爆发",
            "rounds": 3,
            "targets": 3,
            "total_damage": 600,
            "dps": 200.0,
            "damage_by_round": [100, 200, 300],
            "damage_by_target": [300, 150, 150],
            "casts": ["R1 测试猪「普攻」，造成 100 伤害", "R2 测试猪「爆发」，造成 200 伤害"],
        }
        with patch.object(ImageDraw.ImageDraw, "text", record):
            card = render_dummy_poster(self.root, poster)

        self.assertGreater(card.height, 1000)
        joined = "".join(labels)
        for label in (
            "PIGGY  /  TRAINING",
            "训练木桩",
            "AOE 总伤害",
            "AOE DPS",
            "目标伤害",
            "木桩 A",
            "逐回合伤害",
            "施法记录",
        ):
            self.assertIn(label, joined)


if __name__ == "__main__":
    unittest.main()
