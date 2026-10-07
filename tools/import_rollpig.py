"""Import extra pigs from a local clone of astrbot_plugin_rollpig.

Usage: python tools/import_rollpig.py /path/to/astrbot_plugin_rollpig

Entries already in resources/pigs.json are skipped, as are gallery duplicates
("·图库") and alternate cuts of pigs we already ship. Images are downscaled to
512px WebP. Running it again only adds what is still missing.
"""

import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources"
MAX_SIDE = 512
# Alternate versions of pigs that are already bundled under another name.
ALTERNATES = {"苹果猪（有尾巴版）", "猪学习(猪肉烹饪大全)"}


def skip_reason(pig: dict, known_ids: set, known_names: set) -> str:
    if pig["id"] in known_ids or pig["name"] in known_names:
        return "already bundled"
    if pig["name"].endswith("·图库"):
        return "gallery duplicate"
    if pig["name"] in ALTERNATES:
        return "alternate of bundled pig"
    return ""


def find_image(source: Path, pig_id: str) -> Path:
    for path in (source / "resource" / "image").glob(f"{pig_id}.*"):
        return path
    raise FileNotFoundError(pig_id)


def convert(path: Path, target: Path):
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original)
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        image.save(target, "WEBP", quality=80, method=6)


def main(source: Path):
    manifest = RESOURCES / "pigs.json"
    pigs = json.loads(manifest.read_text("utf-8"))
    known_ids = {p["id"] for p in pigs}
    known_names = {p["name"] for p in pigs}
    order = max(p["sort_order"] for p in pigs) + 1
    added, skipped = [], []
    for pig in json.loads((source / "resource" / "pig.json").read_text("utf-8")):
        reason = skip_reason(pig, known_ids, known_names)
        if reason:
            if reason != "already bundled":
                skipped.append((pig["id"], pig["name"], reason))
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", pig["id"]):
            raise SystemExit(f"Unsupported id: {pig['id']}")
        target = RESOURCES / "images" / f"{pig['id']}.webp"
        convert(find_image(source, pig["id"]), target)
        pigs.append(
            {
                "id": pig["id"],
                "name": pig["name"].strip(),
                "description": pig["description"].strip(),
                "analysis": pig["analysis"].strip(),
                "image": f"images/{pig['id']}.webp",
                "enabled": True,
                "sort_order": order,
            }
        )
        known_ids.add(pig["id"])
        order += 1
        added.append(pig["id"])
    manifest.write_text(json.dumps(pigs, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"added {len(added)}, skipped {len(skipped)}")
    for pig_id, name, reason in skipped:
        print(f"skip {pig_id} {name} ({reason})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
