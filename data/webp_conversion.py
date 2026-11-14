"""WebP encoding shared by dataset conversion and original-image restoration."""
import os

from dataset_files import JPEG_EXTENSIONS


def save_webp(image, output_path, source_extension, source_size):
    image.save(output_path, 'WEBP', lossless=True, quality=100, method=4)
    if source_extension not in JPEG_EXTENSIONS or os.path.getsize(output_path) <= source_size:
        return
    for quality in range(95, -1, -5):
        image.save(output_path, 'WEBP', lossless=False, quality=quality, method=4)
        if os.path.getsize(output_path) < source_size:
            return
