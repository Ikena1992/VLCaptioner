#!/usr/bin/env python3
"""Move Parquet-matched image/TXT pairs into ParquetImages for personal use."""

from __future__ import annotations

import shutil
from pathlib import Path

from tqdm import tqdm


ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT / "images"
PARQUET_DIR = ROOT / "data" / "parquet"
DESTINATION_DIR = ROOT / "ParquetImages"
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
}


def load_md5s(parquet_dir: Path) -> set[str]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError(
            "This utility requires pyarrow. Run install.bat first."
        ) from error

    md5s: set[str] = set()
    for parquet_path in sorted(parquet_dir.glob("*.parquet")):
        parquet = pq.ParquetFile(parquet_path)
        if "md5" not in parquet.schema_arrow.names:
            tqdm.write(f"Skipping {parquet_path.name}: missing md5 column")
            continue
        for batch in parquet.iter_batches(columns=["md5"]):
            for value in batch.column(0).to_pylist():
                if isinstance(value, str):
                    md5 = value.strip().casefold()
                    if len(md5) == 32 and all(c in "0123456789abcdef" for c in md5):
                        md5s.add(md5)
    return md5s


def find_image_sets(images_dir: Path, md5s: set[str]):
    by_stem: dict[str, list[Path]] = {}
    for path in images_dir.iterdir():
        if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS:
            stem = path.stem.casefold()
            if stem in md5s:
                by_stem.setdefault(stem, []).append(path)
    return sorted(by_stem.items())


def move_image_set(stem: str, image_paths: list[Path], destination: Path) -> bool:
    txt_path = image_paths[0].with_suffix(".txt")
    if not txt_path.is_file():
        tqdm.write(f"SKIP {stem}: matching .txt file is missing")
        return False

    sources = [*image_paths, txt_path]
    conflicts = [destination / source.name for source in sources if (destination / source.name).exists()]
    if conflicts:
        tqdm.write(
            f"SKIP {stem}: destination already contains "
            + ", ".join(path.name for path in conflicts)
        )
        return False

    moved: list[tuple[Path, Path]] = []
    try:
        for source in sources:
            target = destination / source.name
            shutil.move(str(source), str(target))
            moved.append((source, target))
    except Exception:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                shutil.move(str(target), str(source))
        raise
    return True


def main() -> int:
    if not IMAGES_DIR.is_dir():
        raise RuntimeError(f"Images folder does not exist: {IMAGES_DIR}")
    md5s = load_md5s(PARQUET_DIR)
    image_sets = find_image_sets(IMAGES_DIR, md5s)
    DESTINATION_DIR.mkdir(parents=True, exist_ok=True)

    moved = 0
    skipped = 0
    failed = 0
    for stem, image_paths in tqdm(
        image_sets,
        desc="Moving Parquet image pairs",
        unit="image",
        dynamic_ncols=True,
    ):
        try:
            if move_image_set(stem, image_paths, DESTINATION_DIR):
                moved += 1
            else:
                skipped += 1
        except Exception as error:
            failed += 1
            tqdm.write(f"FAILED {stem}: {error}")

    print(f"Moved: {moved}; skipped: {skipped}; failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
