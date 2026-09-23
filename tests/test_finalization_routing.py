"""Regression checks for direct publication to done or captionReview."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
import finalize_caption_dataset as finalizer


class FinalizationRoutingTests(unittest.TestCase):
    def test_missing_long_caption_goes_directly_to_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            images, done, review = (root / name for name in ("images", "done", "captionReview"))
            images.mkdir()
            done.mkdir()
            (images / "sample.webp").write_bytes(b"image")
            (images / "sample.short").write_text("A character.", encoding="utf-8")
            (images / "sample.tag").write_text("1girl", encoding="utf-8")
            with patch.object(finalizer, "IMAGES_DIR", images), patch.object(finalizer, "DONE_DIR", done), patch.object(finalizer, "REVIEW_DIR", review):
                self.assertTrue(finalizer.process_caption_pair(images / "sample.short"))
                self.assertTrue((review / "sample.webp").exists())
                self.assertFalse((done / "sample.webp").exists())

    def test_review_routing_and_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            images = root / "images"
            done = root / "done"
            review = root / "captionReview"
            images.mkdir()
            done.mkdir()
            (images / "sample.webp").write_bytes(b"image")
            (images / "sample.short").write_text("A character.", encoding="utf-8")
            (images / "sample.long").write_text("A character stands outside.", encoding="utf-8")
            (images / "sample.tag").write_text("1girl", encoding="utf-8")

            with patch.object(finalizer, "IMAGES_DIR", images), patch.object(finalizer, "DONE_DIR", done), patch.object(finalizer, "REVIEW_DIR", review):
                self.assertTrue(finalizer.process_caption_pair(images / "sample.short"))
                self.assertTrue((done / "sample.webp").exists())
                self.assertFalse((review / "sample.webp").exists())

                # A revised caption must go directly to review and retire the old copy.
                (images / "sample.webp").write_bytes(b"image")
                (images / "sample.short").write_text("END_CAPTION", encoding="utf-8")
                (images / "sample.long").write_text("A character stands outside.", encoding="utf-8")
                (images / "sample.tag").write_text("1girl", encoding="utf-8")
                self.assertTrue(finalizer.process_caption_pair(images / "sample.short"))
                self.assertTrue((review / "sample.webp").exists())
                self.assertFalse((done / "sample.webp").exists())
                self.assertFalse(list(done.glob(".finalize-*")))


if __name__ == "__main__":
    unittest.main()
