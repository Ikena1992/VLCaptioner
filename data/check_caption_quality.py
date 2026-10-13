"""Move caption sets with definite quality failures into a review folder."""

from __future__ import annotations

import argparse
import re
import shutil
from collections import Counter
from pathlib import Path

from tqdm import tqdm


ROOT = Path(__file__).resolve().parent.parent
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif"}
COMPANION_EXTENSIONS = {".txt", ".short", ".long", ".tag", ".combined", ".csv"}
MAX_SCAN_CHARS = 200_000
WORD_RE = re.compile(r"\b[^\W_]+(?:['’-][^\W_]+)*\b", re.UNICODE)
SENTENCE_BREAK_RE = re.compile(r"[.!?]+(?:[\"')\]]+)?\s+")
CONTROL_MARKER_RE = re.compile(
    r"END_CAPTION|</?(?:think|torii_report|ground_truth_tags|long_caption|format)>"
    r"|<\|(?:im_start|im_end|endoftext|eot_id|start_header_id|end_header_id)\|>",
    re.IGNORECASE,
)
RESPONSE_PREFIX_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:(?:short|long|detailed)\s+)?"
    r"(?:caption|description)\s*:\s*|"
    r"^\s*Here (?:is|are) (?:the|your|a) "
    r"(?:(?:short|long|detailed)\s+)?caption(?:s)?\s*:",
    re.IGNORECASE,
)


def repetition_issue(caption: str) -> str | None:
    # Visible quoted writing can legitimately repeat. Exclude it from loop checks.
    prose = re.sub(r'"[^"\n]*"|“[^”\n]*”', ' ', caption)
    sentences = [tuple(word.casefold() for word in WORD_RE.findall(part))
                 for part in SENTENCE_BREAK_RE.split(prose)]
    total_words = sum(map(len, sentences))
    for sentence, count in Counter(sentences).items():
        if len(sentence) >= 8 and count >= 3 and len(sentence) * count >= total_words / 2:
            return "repeats the same substantial sentence at least three times"
    words = [word.casefold() for word in WORD_RE.findall(prose)]
    for width in range(1, 9):
        required = max(5, (24 + width - 1) // width)
        run = 1
        for index in range(width, len(words) - width + 1):
            if words[index:index + width] == words[index - width:index]:
                run += 1
                # Each match moves one word; width consecutive matches add one copy.
                if run >= (required - 1) * width + 1:
                    return "contains a sustained consecutive word or phrase loop"
            else:
                run = 1
    return None


def longest_sentence_words(text: str) -> int:
    return max(
        (len(WORD_RE.findall(part)) for part in SENTENCE_BREAK_RE.split(text)),
        default=0,
    )


def is_runaway_caption(text: str, max_sentence_words: int = 250) -> bool:
    """Detect the very long punctuation-free chains produced by model degeneration."""
    return longest_sentence_words(text) > max_sentence_words


def caption_text_issues(caption: str, suffix: str, max_sentence_words: int = 250) -> list[str]:
    """Shared checks for generated, cached, and published captions."""
    if not caption.strip():
        return [f"{suffix} caption empty"]
    issues = []
    marker = CONTROL_MARKER_RE.search(caption)
    if marker:
        issues.append(f"{suffix} contains control text ({marker.group()})")
    if RESPONSE_PREFIX_RE.search(caption):
        issues.append(f"{suffix} starts with a response heading or preamble")
    repetition = repetition_issue(caption[:MAX_SCAN_CHARS])
    if repetition:
        issues.append(f"{suffix} {repetition}")
    if len(caption) > MAX_SCAN_CHARS:
        issues.append(f"{suffix} caption exceeds scan limit")
    elif is_runaway_caption(caption, max_sentence_words):
        issues.append(f"{suffix} has a {longest_sentence_words(caption)}-word sentence")
    return issues


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
        issues.extend(caption_text_issues(caption, suffix, max_sentence_words))
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
    unreadable = 0
    issue_counts: dict[str, int] = {}
    for stem, files in tqdm(sorted(files_by_stem.items()), desc="Checking captions", unit="image"):
        # Skip unrelated sidecars until an image or caption identifies an output set.
        if not any(path.suffix.lower() in IMAGE_EXTENSIONS | {".short", ".long"} for path in files):
            continue
        checked += 1
        try:
            issues = quality_issues(files, max_sentence_words)
        except OSError as error:
            unreadable += 1
            print(f"SKIP {stem}: could not read caption ({error})", flush=True)
            continue
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
          f"{'would move' if dry_run else 'moved'} {found} for review; "
          f"could not check {unreadable}.", flush=True)
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
