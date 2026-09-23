import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))

from source_tag_file import read_source_tags, read_source_metadata, write_source_tags


class SourceTagFileTests(unittest.TestCase):
    def test_round_trip_preserves_booru_fields_and_visible_meta(self):
        row = {
            "post_id": 123,
            "source": "gelbooru",
            "post_source": "https://example.org/art?id=1&size=large",
            "characters": "some character",
            "meta": "highres, watercolor (medium)",
            "rating": "e",
            "score": 121,
            "created_at": "2025-02-02T00:00:00Z",
        }
        content = write_source_tags("some character, highres, watercolor (medium)", row)
        self.assertEqual(read_source_metadata(content), row)
        self.assertEqual(
            read_source_tags(content),
            "some character, highres, watercolor (medium)",
        )

    def test_legacy_tag_only_file(self):
        self.assertEqual(read_source_tags("blue eyes, 1girl"), "blue eyes, 1girl")
        self.assertIsNone(read_source_metadata("blue eyes, 1girl"))


if __name__ == "__main__":
    unittest.main()
