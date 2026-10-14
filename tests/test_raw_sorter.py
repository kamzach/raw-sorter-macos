"""Jednoduché nezávislé testy bez ExifToolu a bez RAW dat."""
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import raw_sorter_macos as sorter


class RawSorterTests(unittest.TestCase):
    def test_date_original(self):
        day, field = sorter.date_from_exif({"DateTimeOriginal": "2025:10:13 19:32:04"})
        self.assertEqual(day.isoformat(), "2025-10-13")
        self.assertEqual(field, "DateTimeOriginal")

    def test_invalid_exif_date(self):
        day, field = sorter.date_from_exif({"DateTimeOriginal": "2025:99:99 12:00:00"})
        self.assertIsNone(day)
        self.assertIsNone(field)

    def test_unique_name_when_target_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            target = folder / "L1000001.DNG"
            target.write_bytes(b"old")
            src = folder / "source" / "L1000001.DNG"
            pairs, note = sorter.unique_pairs(src, target, [], set())
            self.assertEqual(pairs[0][1].name, "L1000001_002.DNG")
            self.assertTrue(note)
            self.assertEqual(target.read_bytes(), b"old")

    def test_sidecar_target_follows_renamed_raw(self):
        raw = Path("/source/L1000001.DNG")
        sidecar = Path("/source/L1000001.xmp")
        target = Path("/destination/L1000001_002.DNG")
        pairs = sorter.pairs_for(raw, target, [sidecar])
        self.assertEqual(pairs[1][1].name, "L1000001_002.xmp")


if __name__ == "__main__":
    unittest.main()
