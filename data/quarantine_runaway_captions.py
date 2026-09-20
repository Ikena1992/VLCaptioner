"""Move image sets whose long caption contains a runaway word chain."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from tqdm import tqdm


ROOT = Path(__file__).resolve().parent.parent
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif"}
COMPANION_EXTENSIONS = {".txt", ".short", ".tag", ".combined"}
MAX_SCAN_CHARS = 200_000
WORD_RE = re.compile(r"\b[^\W_]+(?:['’-][^\W_]+)*\b", re.UNICODE)
SENTENCE_BREAK_RE = re.compile(r"[.!?]+(?:[\"')\]]+)?\s+")


def longest_sentence_words(text: str) -> int:
    return max(
        (len(WORD_RE.findall(part)) for part in SENTENCE_BREAK_RE.split(text)),
        default=0,
    )


def is_runaway_caption(text: str, max_sentence_words: int = 250) -> bool:
    """Detect the very long punctuation-free chains produced by model degeneration."""
    return longest_sentence_words(text) > max_sentence_words


def quarantine(
    source: Path,
    destination: Path,
    max_sentence_words: int = 250,
    dry_run: bool = False,
) -> int:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("Source and destination must be different folders")

    files_by_stem: dict[str, list[Path]] = {}
    for candidate in source.iterdir():
        if candidate.suffix.lower() in COMPANION_EXTENSIONS | IMAGE_EXTENSIONS:
            files_by_stem.setdefault(candidate.stem.casefold(), []).append(candidate)

    found = 0
    long_files = sorted(source.glob("*.long"))
    for long_file in tqdm(long_files, desc="Scanning long captions", unit="file"):
        with long_file.open(encoding="utf-8-sig", errors="replace") as caption_file:
            text = caption_file.read(MAX_SCAN_CHARS)
        longest = longest_sentence_words(text)
        if longest <= max_sentence_words:
            continue

        files = [long_file, *files_by_stem.get(long_file.stem.casefold(), [])]
        collisions = [destination / file.name for file in files if (destination / file.name).exists()]
        if collisions:
            names = ", ".join(path.name for path in collisions)
            print(f"SKIP {long_file.name}: destination already contains {names}")
            continue

        found += 1
        print(f"{'WOULD MOVE' if dry_run else 'MOVE'} {long_file.stem} "
              f"(longest sentence: {longest} words)")
        if not dry_run:
            destination.mkdir(parents=True, exist_ok=True)
            for file in files:
                shutil.move(str(file), destination / file.name)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "done")
    parser.add_argument("--destination", type=Path, default=ROOT / "runawayCaptions")
    parser.add_argument("--max-sentence-words", type=int, default=250)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.source.is_dir():
        parser.error(f"source folder does not exist: {args.source}")
    if args.max_sentence_words < 1:
        parser.error("--max-sentence-words must be positive")

    count = quarantine(
        args.source,
        args.destination,
        args.max_sentence_words,
        args.dry_run,
    )
    print(f"Detected {count} runaway caption set(s).")


if __name__ == "__main__":
    main()
