import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


class ConversionTests(unittest.TestCase):
    def run_conversion(self, folder):
        data = folder / "data"
        data.mkdir()
        script = data / "convert_images_to_webp.py"
        shutil.copyfile(ROOT / "data" / script.name, script)
        return subprocess.run([sys.executable, str(script)], capture_output=True, text=True)

    def test_existing_webp_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            images = folder / "images"
            images.mkdir()
            Image.new("RGB", (2, 2), "red").save(images / "image.png")
            Image.new("RGB", (2, 2), "blue").save(images / "image.webp")
            original = (images / "image.webp").read_bytes()
            (images / "image.webp.tmp").write_text("unrelated temporary file")
            result = self.run_conversion(folder)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual((images / "image.webp").read_bytes(), original)
            self.assertTrue((images / "image.png").exists())
            self.assertEqual((images / "image.webp.tmp").read_text(), "unrelated temporary file")

    def test_colliding_sources_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            images = folder / "images"
            images.mkdir()
            for extension in ("png", "jpg"):
                Image.new("RGB", (2, 2)).save(images / ("image." + extension))
            result = self.run_conversion(folder)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue((images / "image.png").exists())
            self.assertTrue((images / "image.jpg").exists())
            self.assertFalse((images / "image.webp").exists())

    def test_orientation_is_preserved_in_converted_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            images = folder / "images"
            images.mkdir()
            source = Image.new("RGB", (2, 3), "red")
            exif = Image.Exif()
            exif[274] = 6
            source.save(images / "image.png", exif=exif)
            result = self.run_conversion(folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            with Image.open(images / "image.webp") as converted:
                self.assertEqual(converted.size, (3, 2))
            self.assertFalse((images / "image.png").exists())


if __name__ == "__main__":
    unittest.main()
