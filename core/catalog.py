import hashlib
import io
import json
import re
from pathlib import Path

from PIL import Image

from .battle import load_battle
from .config import PiggyError

# The catalog shipped before bundled pigs were synced into existing installs.
FIRST_RELEASE_SIZE = 96


def initialize_battle(data_dir: Path, resources: Path) -> None:
    """Existing installs receive the bundled battle data once; later edits are kept."""
    target = data_dir / "catalog" / "battle.json"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    temp.write_text((resources / "battle.json").read_text("utf-8"), "utf-8")
    temp.replace(target)


def _write_json(path: Path, data) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(path)


def _copy_image(resources: Path, pig: dict, images: Path) -> str:
    source = resources / pig["image"]
    (images / source.name).write_bytes(source.read_bytes())
    return f"images/{source.name}"


def initialize_catalog(data_dir: Path, resources: Path) -> None:
    target = data_dir / "catalog"
    target.mkdir(parents=True, exist_ok=True)
    if not (target / "pigs.json").exists():
        # The manifest is the completion marker, copied only after the images.
        images = target / "images"
        images.mkdir(exist_ok=True)
        definitions = json.loads((resources / "pigs.json").read_text("utf-8"))
        for pig in definitions:
            pig["image"] = _copy_image(resources, pig, images)
        _write_json(target / "pigs.json", definitions)


def sync_bundled_catalog(data_dir: Path, resources: Path) -> int:
    """Add bundled pigs (and their battle data) that this install has never seen.

    `.bundled_ids` remembers what was offered before, so admin edits are never
    overwritten and pigs an admin deleted are not brought back.
    """
    target = data_dir / "catalog"
    manifest = target / "pigs.json"
    if not manifest.exists():
        return 0
    bundled = json.loads((resources / "pigs.json").read_text("utf-8"))
    bundled_battle = json.loads((resources / "battle.json").read_text("utf-8"))["pigs"]
    marker = target / ".bundled_ids"
    data = json.loads(manifest.read_text("utf-8"))
    present = {pig["id"] for pig in data}
    battle_path = target / "battle.json"
    battle = (
        json.loads(battle_path.read_text("utf-8"))
        if battle_path.exists()
        else {"version": 1, "pigs": {}}
    )
    if marker.exists():
        seen = json.loads(marker.read_text("utf-8"))
    else:
        # Installs from before this sync existed had exactly the first release offered.
        legacy = {pig["id"] for pig in bundled if pig["sort_order"] < FIRST_RELEASE_SIZE}
        seen = {"pigs": sorted(present | legacy), "battle": sorted(set(battle["pigs"]) | legacy)}
    seen_pigs, seen_battle = set(seen["pigs"]), set(seen["battle"])
    added = [pig for pig in bundled if pig["id"] not in seen_pigs | present]
    if added:
        images = target / "images"
        images.mkdir(exist_ok=True)
        order = max((pig.get("sort_order", 0) for pig in data), default=-1) + 1
        for pig in added:
            data.append({**pig, "image": _copy_image(resources, pig, images), "sort_order": order})
            order += 1
        _write_json(manifest, data)
    present |= {pig["id"] for pig in added}
    missing_battle = {
        pig_id: entry
        for pig_id, entry in bundled_battle.items()
        if pig_id in present and pig_id not in seen_battle and pig_id not in battle["pigs"]
    }
    if missing_battle:
        battle["pigs"].update(missing_battle)
        _write_json(battle_path, battle)
    _write_json(
        marker,
        {
            "pigs": sorted(seen_pigs | {pig["id"] for pig in bundled}),
            "battle": sorted(seen_battle | set(bundled_battle)),
        },
    )
    return len(added)


def read_catalog(data_dir: Path) -> list[dict]:
    root = (data_dir / "catalog").resolve()
    try:
        definitions = json.loads((root / "pigs.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise PiggyError("猪库 JSON 无法读取，原有收藏和有效猪库均已保留。") from exc
    if not isinstance(definitions, list) or not definitions:
        raise PiggyError("猪库必须是非空列表。")
    archive = data_dir / "assets"
    archive.mkdir(parents=True, exist_ok=True)
    ids = set()
    validated = []
    for index, item in enumerate(definitions):
        if not isinstance(item, dict):
            raise PiggyError(f"猪库第 {index + 1} 项必须为对象。")
        pig_id = item.get("id", "")
        if not isinstance(pig_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", pig_id):
            raise PiggyError(f"猪库第 {index + 1} 项 ID 不合法。")
        if pig_id in ids:
            raise PiggyError(f"猪库存在重复 ID：{pig_id}")
        ids.add(pig_id)
        for key, limit in (("name", 64), ("description", 160), ("analysis", 600)):
            if not isinstance(item.get(key), str) or not 1 <= len(item[key].strip()) <= limit:
                raise PiggyError(f"{pig_id} 的 {key} 必须是 1–{limit} 字的文本。")
        enabled = item.get("enabled", True)
        order = item.get("sort_order", index)
        if type(enabled) is not bool or type(order) is not int:
            raise PiggyError(f"{pig_id} 的 enabled/sort_order 类型错误。")
        image_name = item.get("image", f"images/{pig_id}.png")
        if not isinstance(image_name, str):
            raise PiggyError(f"{pig_id} 的图片路径必须为字符串。")
        path = (root / image_name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise PiggyError(f"{pig_id} 图片不存在或超出猪库目录。")
        try:
            if path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("Image too large")
            with path.open("rb") as source:
                raw = source.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise ValueError("Image too large")
            suffix = path.suffix.lower()
            formats = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
            with Image.open(io.BytesIO(raw)) as img:
                if suffix not in {".png", ".jpg", ".jpeg", ".webp"} or img.format not in formats:
                    raise ValueError("Unsupported image format")
                # Some older bundled .png files actually contain WebP. Preserve them
                # while giving archived assets the correct suffix and upload MIME type.
                suffix = formats[img.format]
                if img.width * img.height > 16_000_000:
                    raise ValueError("Image dimensions too large")
                img.verify()
            # verify() does not decode JPEG pixels. Validate the exact bytes we archive.
            with Image.open(io.BytesIO(raw)) as img:
                img.load()
            digest = hashlib.sha256(raw).hexdigest()
            asset_name = f"{digest}{suffix}"
            dest = archive / asset_name
            if not dest.exists():
                temp = dest.with_suffix(dest.suffix + ".tmp")
                temp.write_bytes(raw)
                temp.replace(dest)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise PiggyError(f"{pig_id} 图片无法解析或超过限制。") from exc
        validated.append(
            {
                "id": pig_id,
                "name": item["name"].strip(),
                "description": item["description"].strip(),
                "analysis": item["analysis"].strip(),
                "asset": asset_name,
                "enabled": enabled,
                "sort_order": order,
            }
        )
    if not any(p["enabled"] for p in validated):
        raise PiggyError("至少保留一只启用的小猪；本次重载未生效。")
    battle_path = root / "battle.json"
    battle = load_battle(battle_path, ids) if battle_path.exists() else {}
    for pig in validated:
        entry = battle.get(pig["id"])
        pig["battle"] = json.dumps(entry, ensure_ascii=False) if entry else ""
    return validated
