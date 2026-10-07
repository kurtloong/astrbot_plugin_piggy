import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.battle import FALLBACK
from core.catalog import initialize_catalog, read_catalog, sync_bundled_catalog


class CatalogSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.resources = Path(self.temp.name) / "resources"
        self.data = Path(self.temp.name) / "data"
        (self.resources / "images").mkdir(parents=True)
        self.bundle([("a", 0), ("b", 1), ("c", 100), ("d", 101)])

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, pigs):
        definitions = []
        for pig_id, order in pigs:
            Image.new("RGB", (8, 8), (order % 255, 80, 120)).save(
                self.resources / "images" / f"{pig_id}.webp", "WEBP"
            )
            definitions.append(
                {
                    "id": pig_id,
                    "name": f"{pig_id}猪",
                    "description": "描述",
                    "analysis": "性格",
                    "image": f"images/{pig_id}.webp",
                    "enabled": True,
                    "sort_order": order,
                }
            )
        (self.resources / "pigs.json").write_text(json.dumps(definitions), "utf-8")
        battle = {"version": 1, "pigs": {pig_id: FALLBACK for pig_id, _ in pigs}}
        (self.resources / "battle.json").write_text(json.dumps(battle), "utf-8")

    def catalog(self):
        return json.loads((self.data / "catalog" / "pigs.json").read_text("utf-8"))

    def battle(self):
        return json.loads((self.data / "catalog" / "battle.json").read_text("utf-8"))["pigs"]

    def test_fresh_install_copies_webp_and_needs_no_sync(self):
        initialize_catalog(self.data, self.resources)
        self.assertEqual(sync_bundled_catalog(self.data, self.resources), 0)
        self.assertEqual([p["id"] for p in self.catalog()], ["a", "b", "c", "d"])
        self.assertTrue((self.data / "catalog" / "images" / "a.webp").is_file())
        self.assertEqual(len(read_catalog(self.data)), 4)

    def test_upgrade_adds_new_pigs_keeps_edits_and_respects_deletions(self):
        # An older install: only the first release, admin renamed "a" and deleted "b".
        catalog = self.data / "catalog"
        (catalog / "images").mkdir(parents=True)
        Image.new("RGB", (8, 8)).save(catalog / "images" / "a.png")
        legacy = [{"id": "a", "name": "管理员改名", "description": "描述", "analysis": "性格"}]
        (catalog / "pigs.json").write_text(json.dumps(legacy), "utf-8")
        (catalog / "battle.json").write_text(
            json.dumps({"version": 1, "pigs": {"a": FALLBACK}}), "utf-8"
        )

        self.assertEqual(sync_bundled_catalog(self.data, self.resources), 2)
        pigs = {p["id"]: p for p in self.catalog()}
        self.assertEqual(set(pigs), {"a", "c", "d"})
        self.assertEqual(pigs["a"]["name"], "管理员改名")
        self.assertEqual(set(self.battle()), {"a", "c", "d"})
        self.assertEqual(len(read_catalog(self.data)), 3)

        # The admin later deletes "c"; a new release bundles "e".
        (catalog / "pigs.json").write_text(
            json.dumps([p for p in self.catalog() if p["id"] != "c"]), "utf-8"
        )
        self.bundle([("a", 0), ("b", 1), ("c", 100), ("d", 101), ("e", 102)])
        self.assertEqual(sync_bundled_catalog(self.data, self.resources), 1)
        self.assertEqual([p["id"] for p in self.catalog()], ["a", "d", "e"])
        self.assertEqual(sync_bundled_catalog(self.data, self.resources), 0)

    def test_battle_data_shipped_later_is_added_for_existing_pigs(self):
        initialize_catalog(self.data, self.resources)
        (self.data / "catalog" / "battle.json").write_text(
            json.dumps({"version": 1, "pigs": {"a": FALLBACK}}), "utf-8"
        )
        sync_bundled_catalog(self.data, self.resources)
        self.assertEqual(set(self.battle()), {"a", "c", "d"})


if __name__ == "__main__":
    unittest.main()
