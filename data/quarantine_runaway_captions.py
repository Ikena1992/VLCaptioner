"""Move caption sets with definite quality failures into a review folder."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from tqdm import tqdm


ROOT = Path(__file__).resolve().parent.parent
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif"}
COMPANION_EXTENSIONS = {".txt", ".short", ".long", ".tag", ".combined", ".csv"}
MAX_SCAN_CHARS = 200_000
WORD_RE = re.compile(r"\b[^\W_]+(?:['’-][^\W_]+)*\b", re.UNICODE)
SENTENCE_BREAK_RE = re.compile(r"[.!?]+(?:[\"')\]]+)?\s+")
CONTROL_MARKER_RE = re.compile(
    r"END_CAPTION|</?(?:think|torii_report|ground_truth_tags|long_caption|format)>",
    re.IGNORECASE,
)


def longest_sentence_words(text: str) -> int:
    return max(
        (len(WORD_RE.findall(part)) for part in SENTENCE_BREAK_RE.split(text)),
        default=0,
    )


def is_runaway_caption(text: str, max_sentence_words: int = 250) -> bool:
    """Detect the very long punctuation-free chains produced by model degeneration."""
    return longest_sentence_words(text) > max_sentence_words


def quality_issues(files: list[Path], max_sentence_words: int = 250) -> list[str]:
    """Return high-confidence caption failures for one output set."""
    by_suffix = {path.suffix.lower(): path for path in files}
    issues = []
    if not any(suffix in by_suffix for suffix in IMAGE_EXTENSIONS):
        issues.append("matching image missing")
    for suffix in (".short", ".long"):
        path = by_suffix.get(suffix)
        if path is None:
            issues.append(f"{suffix} caption missing")
            continue
        with path.open(encoding="utf-8-sig", errors="replace") as caption_file:
            caption = caption_file.read(MAX_SCAN_CHARS + 1)
        if not caption.strip():
            issues.append(f"{suffix} caption empty")
            continue
        marker = CONTROL_MARKER_RE.search(caption)
        if marker:
            issues.append(f"{suffix} contains control text ({marker.group()})")
        if suffix == ".long":
            if len(caption) > MAX_SCAN_CHARS:
                issues.append(".long caption exceeds scan limit")
            elif is_runaway_caption(caption, max_sentence_words):
                issues.append(
                    f".long has a {longest_sentence_words(caption)}-word sentence"
                )
    return issues


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
        if candidate.is_file() and candidate.suffix.lower() in COMPANION_EXTENSIONS | IMAGE_EXTENSIONS:
            files_by_stem.setdefault(candidate.stem.casefold(), []).append(candidate)

    found = 0
    checked = 0
    issue_counts: dict[str, int] = {}
    for stem, files in tqdm(sorted(files_by_stem.items()), desc="Checking captions", unit="image"):
        # Skip unrelated sidecars until an image or caption identifies an output set.
        if not any(path.suffix.lower() in IMAGE_EXTENSIONS | {".short", ".long"} for path in files):
            continue
        checked += 1
        issues = quality_issues(files, max_sentence_words)
        if not issues:
            continue
        collisions = [destination / file.name for file in files if (destination / file.name).exists()]
        if collisions:
            names = ", ".join(path.name for path in collisions)
            print(f"SKIP {stem}: {'; '.join(issues)}; "
                  f"review folder already contains {names}", flush=True)
            continue

        found += 1
        for issue in issues:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
        print(f"{'WOULD MOVE' if dry_run else 'REVIEW'} {stem}: "
              f"{'; '.join(issues)}", flush=True)
        if not dry_run:
            destination.mkdir(parents=True, exist_ok=True)
            for file in files:
                shutil.move(str(file), destination / file.name)
    print(f"Checked {checked} image set(s); "
          f"{'would move' if dry_run else 'moved'} {found} for review.", flush=True)
    for issue, count in sorted(issue_counts.items()):
        print(f"  {count} x {issue}", flush=True)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "done")
    parser.add_argument("--destination", type=Path, default=ROOT / "captionReview")
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
    print(f"Flagged {count} caption set(s) for review.")


if __name__ == "__main__":
    main()
