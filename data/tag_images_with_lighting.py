"""Append optional lighting tags before metadata and caption generation."""

import csv
import importlib.util
import io
import sys
from pathlib import Path

from PIL import Image, ImageOps
from tqdm import tqdm

from dataset_files import IMAGE_EXTENSIONS
from file_io import atomic_output
from image_failures import is_skipped, skip_image
from lighting_tags import LIGHTING_TAGS, MODEL_REPO, MODEL_REVISION
from runtime_config import pipeline_images
from tag_images_with_wd14 import (
    append_missing_tags, clean_tag, normalize_tag_for_compare, split_tags,
    write_tags_atomically,
)


def load_lighting_tagger():
    """Use the upstream three-seed ensemble and its calibrated default thresholds."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    required = ("inference.py", "tagger_config.json", "seed0.safetensors",
                "seed1.safetensors", "seed2.safetensors")
    options = dict(revision=MODEL_REVISION,
                   cache_dir=str(Path(__file__).resolve().parent / "models" / "booru-lighting-tagger"),
                   allow_patterns=[*required, "LICENSE", "NOTICE"])
    try:
        folder = Path(snapshot_download(MODEL_REPO, local_files_only=True, **options))
    except LocalEntryNotFoundError:
        folder = None
    # The Hub can return a partial snapshot after an interrupted download.
    # Require every seed before using the offline path or importing its code.
    if folder is None or any(not (folder / name).is_file() for name in required):
        folder = Path(snapshot_download(MODEL_REPO, local_files_only=False, **options))
    missing = [name for name in required if not (folder / name).is_file()]
    if missing:
        raise FileNotFoundError("Lighting model download is incomplete; retry online. Missing: " + ", ".join(missing))
    spec = importlib.util.spec_from_file_location("vlcaptioner_booru_lighting", folder / "inference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LightingTagger(folder, preset="default")


def append_csv_tags(path, tags):
    """Keep existing CSV metadata, while making lighting available to caption prompts."""
    if not path.exists():
        return
    original = path.read_bytes()
    with io.StringIO(original.decode("utf-8-sig"), newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    if not fields or len(set(fields)) != len(fields) or "general" not in fields or len(rows) != 1:
        raise ValueError(f"Expected one metadata row with a general column: {path.name}")
    row = rows[0]
    if None in row or any(value is None for value in row.values()):
        raise ValueError(f"Malformed metadata CSV: {path.name}")
    general = split_tags(row["general"])
    seen = {normalize_tag_for_compare(tag) for tag in general}
    added = []
    for tag in tags:
        key = normalize_tag_for_compare(tag)
        if key not in seen:
            added.append(tag)
            seen.add(key)
    if added:
        row["general"] = ", ".join([*general, *added])
        with io.StringIO(newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(row)
            updated = handle.getvalue().encode("utf-8")
        with atomic_output(path, mode="wb", fsync=True) as handle:
            handle.write(updated)
        return original, updated


def save_lighting_tags(image_path, tags):
    if not isinstance(tags, list) or any(not isinstance(tag, str) or tag not in LIGHTING_TAGS for tag in tags):
        raise ValueError("Lighting tagger returned invalid tags")
    tags = [clean_tag(tag) for tag in dict.fromkeys(tags)]
    txt_path = image_path.with_suffix(".txt")
    if txt_path.exists():
        # Validate the source before publishing CSV changes. In particular,
        # unreadable files and directories must leave all metadata untouched.
        txt_path.read_text(encoding="utf-8-sig")
    # Repair an existing CSV even when TXT already contains these tags from a
    # previous interrupted run. Metadata creation otherwise reuses existing CSVs.
    csv_path = image_path.with_suffix(".csv")
    csv_change = append_csv_tags(csv_path, tags)
    try:
        if txt_path.exists():
            return append_missing_tags(txt_path, tags)
        if tags:
            write_tags_atomically(txt_path, ", ".join(tags))
        return tags
    except Exception as error:
        if csv_change is not None:
            original, updated = csv_change
            # Windows may permit reading a TXT while blocking its replacement.
            # Undo our CSV edit, but never overwrite a newer external edit.
            try:
                if csv_path.read_bytes() != updated:
                    raise RuntimeError("CSV changed during tag save; newer metadata was retained")
                with atomic_output(csv_path, mode="wb", fsync=True) as handle:
                    handle.write(original)
            except Exception as restore_error:
                raise RuntimeError(
                    f"TXT save failed ({error}); CSV could not be restored ({restore_error}). Retry the image."
                ) from error
        raise


def find_images(folder):
    images = sorted(path for path in folder.iterdir()
                    if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and not is_skipped(path))
    sidecars = {}
    conflicts = set()
    for image in images:
        key = image.stem.casefold()
        if key in sidecars:
            previous = sidecars[key]
            error = f"Images share a lighting tag file: {previous.name} and {image.name}. Rename one image."
            skip_image(previous, error)
            skip_image(image, error)
            conflicts.update((previous, image))
        sidecars[key] = image
    return [image for image in images if image not in conflicts]


def image_identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", line_buffering=True)
    images = find_images(pipeline_images())
    if not images:
        print("No images need lighting tags.")
        return 0
    print(f"Loading {MODEL_REPO} v1.0.2 (three seeds, default thresholds)...", flush=True)
    try:
        tagger = load_lighting_tagger()
    except Exception as error:
        print(f"Lighting tagger cannot start: {error}", file=sys.stderr)
        return 1
    failed = 0
    for image_path in tqdm(images, desc="Adding lighting tags", unit="image"):
        try:
            identity = image_identity(image_path)
            with Image.open(image_path) as image:
                # Apply camera orientation before upstream padding and RGB/BGR conversion.
                with ImageOps.exif_transpose(image) as oriented:
                    result = tagger.tag_images([oriented])[0]
            if image_identity(image_path) != identity:
                raise RuntimeError("Image changed during lighting inference; retry the image")
            added = save_lighting_tags(image_path, result["tags"])
            tqdm.write(f"Lighting: {image_path.name} ({len(added)} tags added)")
        except Exception as error:
            failed += 1
            skip_image(image_path, error)
    print(f"Lighting tagging finished: {len(images) - failed} image(s), {failed} failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
