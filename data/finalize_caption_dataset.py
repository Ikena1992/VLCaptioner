#!/usr/bin/env python3
"""Finalize generated captions and their combined training text into done/."""

from __future__ import annotations

from dataset_files import IMAGE_EXTENSIONS, INTERMEDIATE_EXTENSIONS, FINAL_COMPANION_EXTENSIONS, bundle_stem, is_image, companion_paths
from file_io import atomic_write_json, publish_file, file_hash

from runtime_config import pipeline_images

import json
import re
import shutil
import tempfile
from pathlib import Path

from check_caption_quality import quality_issues


SCRIPT_DIR = Path(__file__).parent.resolve()
IMAGES_DIR = pipeline_images()
SOURCE_ROOT = (SCRIPT_DIR / ".." / "images").resolve()
DONE_DIR = (SCRIPT_DIR / ".." / "done").resolve()
REVIEW_DIR = (SCRIPT_DIR / ".." / "captionReview").resolve()
DONE_DIR.mkdir(parents=True, exist_ok=True)


OVERWRITE_MOVED_FILES = True


def normalize_output_text(text: str) -> str:
    """Convert a caption to one clean training-data line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip()


def write_journal(stage, name, record):
    """Recovery must see a complete journal or the previous transaction state."""
    atomic_write_json(stage / name, record)


def validate_names(names, stage):
    """Validate the entire file list before recovery mutates any path."""
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or name in ("", ".", "..")
                   or Path(name).name != name for name in names)
            or len(set(names)) != len(names)):
        raise RuntimeError(f"Invalid finalization journal names: {stage}")


def validate_hashes(hashes, names, stage):
    if (not isinstance(hashes, dict)
            or any(not isinstance(hashes.get(name), str)
                   or not re.fullmatch(r"[0-9a-f]{64}", hashes[name]) for name in names)):
        raise RuntimeError(f"Invalid finalization journal hashes: {stage}")


def validate_stage(stage):
    if stage.is_symlink() or stage.resolve().parent != DONE_DIR.resolve():
        raise RuntimeError(f"Invalid finalization recovery folder: {stage}")


def publish_files(sources, generated, review_issues=()):
    """Check a staged set, then publish directly to its final folder."""
    sources = [Path(src) for src in sources]
    names = [src.name for src in sources]
    names.extend(name for name in generated if name not in names)
    validate_names(names, "new publication")
    if len({src.name for src in sources}) != len(sources):
        raise ValueError("Finalization sources must have unique filenames")
    source_dir = IMAGES_DIR.resolve()
    if any(src.is_symlink() or src.parent.resolve() != source_dir for src in sources):
        raise ValueError("Finalization sources must belong to the current image folder")
    stage = Path(tempfile.mkdtemp(prefix=".finalize-", dir=DONE_DIR))
    installed = []
    backups = {}
    destination = DONE_DIR
    try:
        payload, backup_dir = stage / "files", stage / "backups"
        payload.mkdir()
        backup_dir.mkdir()
        source_hashes = {}
        for src in sources:
            shutil.copy2(src, payload / src.name)
            source_hashes[src.name] = file_hash(payload / src.name)
            if file_hash(src) != source_hashes[src.name]:
                raise RuntimeError(f"Source changed while staging {src.name}; originals were preserved")
        for name, text in generated.items():
            (payload / name).write_text(text + "\n", encoding="utf-8")
        output_hashes = {name: file_hash(payload / name) for name in names}
        issues = list(review_issues) + quality_issues([payload / name for name in names])
        destination = REVIEW_DIR if issues else DONE_DIR
        destination.mkdir(parents=True, exist_ok=True)
        for name in names:
            dst = destination / name
            if dst.exists():
                if not OVERWRITE_MOVED_FILES:
                    raise FileExistsError(dst)
                backup = backup_dir / name
                shutil.copy2(dst, backup)
                backups[name] = backup
        write_journal(stage, "transaction.json",
                      {"version": 2, "names": names, "backups": list(backups), "destination": str(destination)})
        for name in names:
            publish_file(payload / name, destination / name)
            installed.append(name)
        write_journal(stage, "committed.json",
                      {"sources": [src.name for src in sources], "names": names,
                       "destination": str(destination), "source_dir": str(source_dir),
                       "source_hashes": source_hashes, "output_hashes": output_hashes})
    except Exception:
        # Source files remain intact, making a failed publication retryable.
        for name in reversed(installed):
            if name in backups:
                shutil.copy2(backups[name], stage / "restore.tmp")
                publish_file(stage / "restore.tmp", destination / name)
            else:
                (destination / name).unlink(missing_ok=True)
        shutil.rmtree(stage)
        raise
    finish_cleanup(stage)
    return issues


def finish_cleanup(stage):
    """Resume source cleanup after a complete destination set was published."""
    validate_stage(stage)
    record = json.loads((stage / "committed.json").read_text(encoding="utf-8"))
    sources = record if isinstance(record, list) else record["sources"]
    destination = DONE_DIR if isinstance(record, list) else Path(record["destination"])
    names = sources if isinstance(record, list) else record["names"]
    validate_names(names, stage)
    validate_names(sources, stage)
    if not set(sources).issubset(names):
        raise RuntimeError(f"Invalid finalization source list: {stage}")
    if destination not in (DONE_DIR, REVIEW_DIR):
        raise RuntimeError(f"Invalid finalization destination: {stage}")
    for name in names:
        if (destination / name).is_symlink() or not (destination / name).is_file():
            raise RuntimeError(f"Invalid or incomplete finalization journal: {stage}")
    output_hashes = None if isinstance(record, list) else record.get("output_hashes")
    if output_hashes is not None:
        validate_hashes(output_hashes, names, stage)
        if any(file_hash(destination / name) != output_hashes[name] for name in names):
            raise RuntimeError(f"Published output changed; originals were preserved: {stage}")
    source_dir = None if isinstance(record, list) else record.get("source_dir")
    cleanup_sources = False
    if source_dir is not None:
        source_dir = Path(source_dir)
        # Recovery scans journals from every batch. Only use the recorded,
        # absolute folder, and reject redirected or unrelated source locations.
        queue_root = SOURCE_ROOT / ".caption-queue"
        if (not source_dir.is_absolute() or source_dir.resolve() != source_dir
                or not (source_dir in (SOURCE_ROOT, IMAGES_DIR.resolve())
                        or (source_dir.parent == queue_root
                            and re.fullmatch(r"[0-9a-f]{32}", source_dir.name)))):
            raise RuntimeError(f"Invalid finalization source folder: {stage}")
        source_hashes = record.get("source_hashes")
        if source_hashes is not None and output_hashes is not None:
            validate_hashes(source_hashes, sources, stage)
            cleanup_sources = all(
                not (source_dir / name).is_symlink()
                and (not (source_dir / name).exists()
                     or file_hash(source_dir / name) == source_hashes[name]) for name in sources)
            if cleanup_sources:
                for name in sources:
                    (source_dir / name).unlink(missing_ok=True)
            else:
                print(f"WARN {stage.name}: source files changed; the original set was retained for retry")
    if source_dir is None or (not isinstance(record, list) and record.get("source_hashes") is None):
        # Older journals cannot identify the original batch and file contents
        # safely. Keep the source set rather than guess what may be deleted.
        print(f"WARN {stage.name}: legacy journal lacks source identity; originals were retained")
    # A rerun can change the review decision. Remove its previous copy only
    # after the new set is complete and the source files are safe to delete.
    other = REVIEW_DIR if destination == DONE_DIR else DONE_DIR
    stems = {Path(name).stem for name in names if is_image(name)} or {bundle_stem(name) for name in names}
    if cleanup_sources:
        for stem in stems:
            old_image = other / f"{stem}.webp"
            companions = companion_paths(old_image, FINAL_COMPANION_EXTENSIONS)
            for path in companions + [other / f"{stem}{extension}" for extension in IMAGE_EXTENSIONS]:
                path.unlink(missing_ok=True)
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
        json_path = image_path.with_suffix(".json")
        if json_path.exists():
            sources.append(json_path)
        if long_path.exists():
            sources.append(long_path)
        if txt_path.exists():
            sources.append(txt_path)
        generated = {
            short_path.name: short_text,
            f"{stem}.combined": f"{tag_text}. {short_text}",
        }
        if long_text is not None:
            generated[long_path.name] = long_text
        issues = publish_files(sources, generated)
    except Exception as error:
        print(f"ERROR finalizing captions for {stem}: {error}")
        return False

    if not image_path.exists():
        clean_sidecars(stem)
    if issues:
        print(f"REVIEW {stem}: {'; '.join(issues)}", flush=True)
    elif report_success:
        print(f"DONE {stem}")
    return True


def recover_publications():
    for transaction in DONE_DIR.glob(".finalize-*/transaction.json"):
        stage = transaction.parent
        validate_stage(stage)
        if (stage / "committed.json").exists():
            continue
        record = json.loads(transaction.read_text(encoding="utf-8"))
        validate_names(record["names"], stage)
        if (not isinstance(record["backups"], list)
                or any(name not in record["names"] for name in record["backups"])):
            raise RuntimeError(f"Invalid finalization backups: {stage}")
        destination = Path(record.get("destination", str(DONE_DIR)))
        if destination not in (DONE_DIR, REVIEW_DIR):
            raise RuntimeError(f"Invalid finalization destination: {stage}")
        backups = {name: (stage / "backups" / name if record.get("version") == 2
                          else stage / (name + ".backup")) for name in record["backups"]}
        if any(path.is_symlink() or not path.is_file() for path in backups.values()):
            raise RuntimeError(f"Incomplete finalization backups: {stage}")
        for name in record["names"]:
            if name in record["backups"]:
                shutil.copy2(backups[name], stage / "restore.tmp")
                publish_file(stage / "restore.tmp", destination / name)
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
