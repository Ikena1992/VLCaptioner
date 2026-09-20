import os
from PIL import Image, UnidentifiedImageError
from tqdm import tqdm

# Folder named 'images' one level above this script
folder_path = os.path.join(os.path.dirname(__file__), "..", "images")
folder_path = os.path.abspath(folder_path)

# Supported image extensions
image_extensions = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".gif")
jpeg_extensions = (".jpg", ".jpeg")


def save_webp(img, output_path, source_extension, source_size):
    img.save(output_path, "WEBP", lossless=True, quality=100, method=4)
    if source_extension not in jpeg_extensions or os.path.getsize(output_path) <= source_size:
        return

    for quality in range(95, -1, -5):
        img.save(output_path, "WEBP", lossless=False, quality=quality, method=4)
        if os.path.getsize(output_path) < source_size:
            return


def verify_webp(output_path):
    """Raise if the published output is missing, truncated, or not WebP."""
    with Image.open(output_path) as converted:
        if converted.format != "WEBP":
            raise ValueError(f"expected WebP output, got {converted.format!r}")
        converted.verify()

# Get a list of all image files
image_files = [
    f for f in os.listdir(folder_path)
    if f.lower().endswith(image_extensions)
]

failed_files = []

# Process images with a progress bar
for filename in tqdm(image_files, desc="Converting images", unit="image"):
    file_path = os.path.join(folder_path, filename)
    source_extension = os.path.splitext(filename)[1].lower()
    source_size = os.path.getsize(file_path)
    new_filename = os.path.splitext(filename)[0] + ".webp"
    new_file_path = os.path.join(folder_path, new_filename)
    temp_file_path = new_file_path + ".tmp"

    try:
        with Image.open(file_path) as img:
            img.thumbnail((3000, 3000), Image.Resampling.LANCZOS)

            # Convert transparency to white background
            if img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            ):
                img = img.convert("RGBA")

                white_background = Image.new("RGBA", img.size, (255, 255, 255, 255))
                white_background.alpha_composite(img)

                img = white_background.convert("RGB")
            else:
                img = img.convert("RGB")

            # Save image as WebP
            save_webp(img, temp_file_path, source_extension, source_size)

        # Publish atomically. Reopen the file at its final path before removing
        # the source: a successful save/rename alone does not prove that a
        # readable converted image remains there.
        os.replace(temp_file_path, new_file_path)
        verify_webp(new_file_path)
        os.remove(file_path)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        # A failed save may leave an incomplete WebP. Preserve the source and
        # remove only that incomplete output before continuing the batch.
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        failed_files.append((file_path, str(error)))
        tqdm.write(f"Skipped {file_path}: {error}")

print(f"Converted {len(image_files) - len(failed_files)} image(s) to WebP.")
if failed_files:
    print(f"Skipped {len(failed_files)} unreadable image(s); originals were preserved:")
    for file_path, error in failed_files:
        print(f"  {file_path}: {error}")
print("Done!\n")
