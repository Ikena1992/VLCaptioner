#!/usr/bin/env python3
"""Finalize generated captions and their combined training text into done/."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from check_caption_quality import quality_issues


SCRIPT_DIR = Path(__file__).parent.resolve()
IMAGES_DIR = (SCRIPT_DIR / ".." / "images").resolve()
DONE_DIR = (SCRIPT_DIR / ".." / "done").resolve()
REVIEW_DIR = (SCRIPT_DIR / ".." / "captionReview").resolve()
DONE_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff",
)

INTERMEDIATE_EXTENSIONS = (
    ".csv",
    ".toriiOutput",
)

OVERWRITE_MOVED_FILES = True


def normalize_output_text(text: str) -> str:
    """Convert a caption to one clean training-data line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip()


def publish_files(sources, generated):
    """Check a staged set, then publish directly to its final folder."""
    stage = Path(tempfile.mkdtemp(prefix=".finalize-", dir=DONE_DIR))
    installed = []
    backups = {}
    destination = DONE_DIR
    try:
        for src in sources:
            shutil.copy2(src, stage / src.name)
        for name, text in generated.items():
            (stage / name).write_text(text + "\n", encoding="utf-8")
        names = [p.name for p in sources]
        names.extend(name for name in generated if name not in names)
        issues = quality_issues([stage / name for name in names])
        destination = REVIEW_DIR if issues else DONE_DIR
        destination.mkdir(parents=True, exist_ok=True)
        for name in names:
            dst = destination / name
            if dst.exists():
                if not OVERWRITE_MOVED_FILES:
                    raise FileExistsError(dst)
                backup = stage / (name + ".backup")
                shutil.copy2(dst, backup)
                backups[name] = backup
        (stage / "transaction.json").write_text(
            json.dumps({"names": names, "backups": list(backups), "destination": str(destination)}), encoding="utf-8"
        )
        for name in names:
            (stage / name).replace(destination / name)
            installed.append(name)
        (stage / "committed.json").write_text(
            json.dumps({"sources": [src.name for src in sources], "names": names,
                        "destination": str(destination)}), encoding="utf-8"
        )
    except Exception:
        # Source files remain intact, making a failed publication retryable.
        for name in reversed(installed):
            if name in backups:
                shutil.copy2(backups[name], stage / "restore.tmp")
                (stage / "restore.tmp").replace(destination / name)
            else:
                (destination / name).unlink(missing_ok=True)
        shutil.rmtree(stage)
        raise
    finish_cleanup(stage)
    return issues


def finish_cleanup(stage):
    """Resume source cleanup after a complete destination set was published."""
    record = json.loads((stage / "committed.json").read_text(encoding="utf-8"))
    sources = record if isinstance(record, list) else record["sources"]
    destination = DONE_DIR if isinstance(record, list) else Path(record["destination"])
    names = sources if isinstance(record, list) else record["names"]
    if destination not in (DONE_DIR, REVIEW_DIR):
        raise RuntimeError(f"Invalid finalization destination: {stage}")
    for name in names:
        if Path(name).name != name or not (destination / name).is_file():
            raise RuntimeError(f"Invalid or incomplete finalization journal: {stage}")
    for name in sources:
        (IMAGES_DIR / name).unlink(missing_ok=True)
    # A rerun can change the review decision. Remove its previous copy only
    # after the new set is complete and the source files are safe to delete.
    other = REVIEW_DIR if destination == DONE_DIR else DONE_DIR
    stems = {Path(name).stem for name in names}
    for stem in stems:
        for extension in set(IMAGE_EXTENSIONS) | {".short", ".long", ".tag", ".combined", ".txt"}:
            (other / f"{stem}{extension}").unlink(missing_ok=True)
    shutil.rmtree(stage)


def find_matching_image(stem: str) -> Path | None:
    matches = [
        IMAGES_DIR / f"{stem}{extension}"
        for extension in IMAGE_EXTENSIONS
        if (IMAGES_DIR / f"{stem}{extension}").exists()
    ]
    if not matches:
        return None
    if len(matches) > 1:
        print(
            f"UNCLEAR {stem}: multiple image files found: "
            f"{', '.join(path.name for path in matches)}"
        )
        return None
    return matches[0]


def clean_sidecars(stem: str) -> None:
    """Remove per-image intermediates after successful finalization."""
    for extension in INTERMEDIATE_EXTENSIONS:
        sidecar_path = IMAGES_DIR / f"{stem}{extension}"
        try:
            sidecar_path.unlink(missing_ok=True)
        except OSError as error:
            print(f"WARN {stem}: could not remove {sidecar_path.name}: {error}")


def process_caption_pair(short_path: Path, report_success: bool = True) -> bool:
    stem = short_path.stem
    long_path = IMAGES_DIR / f"{stem}.long"
    image_path = find_matching_image(stem)
    if image_path is None:
        print(f"SKIP {stem}: no clear matching image found")
        return False

    try:
        short_text = normalize_output_text(
            short_path.read_text(encoding="utf-8-sig")
        )
        long_text = normalize_output_text(long_path.read_text(encoding="utf-8-sig")) if long_path.exists() else None
    except Exception as error:
        print(f"ERROR reading captions for {stem}: {error}")
        return False

    tag_path = IMAGES_DIR / f"{stem}.tag"
    txt_path = IMAGES_DIR / f"{stem}.txt"

    try:
        if not tag_path.exists():
            print(f"SKIP {stem}: matching .tag file not found")
            return False

        tag_text = normalize_output_text(
            tag_path.read_text(encoding="utf-8-sig")
        )
        if not tag_text:
            print(f"SKIP {stem}: matching .tag file is empty")
            return False

        sources = [short_path, image_path, tag_path]
        if long_path.exists():
            sources.append(long_path)
        if txt_path.exists():
            sources.append(txt_path)
        generated = {
            short_path.name: short_text,
            f"{stem}.combined": f"{tag_text} {short_text}",
        }
        if long_text is not None:
            generated[long_path.name] = long_text
        issues = publish_files(sources, generated)
    except Exception as error:
        print(f"ERROR finalizing captions for {stem}: {error}")
        return False

    clean_sidecars(stem)
    if issues:
        print(f"REVIEW {stem}: {'; '.join(issues)}", flush=True)
    elif report_success:
        print(f"DONE {stem}")
    return True


def recover_publications():
    for transaction in DONE_DIR.glob(".finalize-*/transaction.json"):
        stage = transaction.parent
        if (stage / "committed.json").exists():
            continue
        record = json.loads(transaction.read_text(encoding="utf-8"))
        destination = Path(record.get("destination", str(DONE_DIR)))
        if destination not in (DONE_DIR, REVIEW_DIR):
            raise RuntimeError(f"Invalid finalization destination: {stage}")
        for name in record["names"]:
            if Path(name).name != name:
                raise RuntimeError(f"Invalid finalization journal: {stage}")
            if name in record["backups"]:
                shutil.copy2(stage / (name + ".backup"), stage / "restore.tmp")
                (stage / "restore.tmp").replace(destination / name)
            else:
                (destination / name).unlink(missing_ok=True)
        shutil.rmtree(stage)


def main() -> None:
    recover_publications()
    for journal in DONE_DIR.glob(".finalize-*/committed.json"):
        finish_cleanup(journal.parent)
    if not IMAGES_DIR.exists() or not IMAGES_DIR.is_dir():
        print(f"Images folder does not exist: {IMAGES_DIR}")
        return

    short_files = sorted(IMAGES_DIR.glob("*.short"))
    if not short_files:
        print(f"No .short/.long caption pairs found in: {IMAGES_DIR}")
        return

    print(f"Images folder: {IMAGES_DIR}")
    print(f"Done folder:   {DONE_DIR}")
    print(f"Found {len(short_files)} .short candidate(s).")

    processed = 0
    skipped = 0
    for short_path in short_files:
        if process_caption_pair(short_path):
            processed += 1
        else:
            skipped += 1

    print("\nFinished.")
    print(f"Processed: {processed}")
    print(f"Skipped:   {skipped}")
    if skipped:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
