#!/usr/bin/env python3
"""
Create .tag files directly from existing Danbooru CSV files.

Input per image:
  <stem>.csv    - metadata exported by the Danbooru update script

Output per image:
  <stem>.tag    - final ordered comma-separated tag file

No Danbooru/API requests are made. All data comes from the CSV file. General
tags are cleaned in memory before the final tag list is assembled.

Final .tag order:
  [quality tag], [metatags], [Period], [safety tags],
  [1girl/1boy/1other etc], [characters], [Copyright], [@artists], [general tags]

Important:
  - CSV fields are treated as comma-separated tag lists.
  - If a CSV field has no comma, the whole field is treated as ONE tag.
    This preserves copyright tags like "sousou no frieren".
  - Only meta tags may use a raw Danbooru fallback splitter.
  - Artist tags are prefixed with "@".
  - Only the final general-tag section is randomly shuffled.
"""

from __future__ import annotations

from quality_tags import get_quality_tag, normalize_quality_tag
from year_tags import get_year_tag

import csv
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Iterable

from clean_general_tags import (
    CONDITIONAL_LOG_FILE,
    DEBUG_CONDITIONAL_RULES,
    CONDITIONAL_LOG_FILE,
    DEBUG_CONDITIONAL_RULES,
    CONDITIONAL_LOG_FILE,
    DEBUG_CONDITIONAL_RULES,
    DROPOUT_ENABLED,
    DROPOUT_MIN_TAGS,
    DROPOUT_RATE,
    DROPOUT_SEED,
    EXTRA_DROPOUT_PROTECT_TAGS,
    JAVA_100_DROPOUT_PROTECT_TAGS,
    apply_tag_dropout,
    clean_general_tags_with_debug,
    normalize_tag as normalize_clean_tag,
    write_conditional_log,
)

# ---------------- CONFIG ----------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_FOLDER = os.path.join(SCRIPT_DIR, "..", "images")
IMAGE_FOLDER = os.path.abspath(IMAGE_FOLDER)

RECURSIVE = False
OVERWRITE_EXISTING = True

OUTPUT_EXTENSION = ".tag"

EXCLUDED_OUTPUT_TAGS = {
    "very aesthetic", "aesthetic", "displeasing", "very displeasing",
}

# Artist config
ARTIST_PREFIX = "@"

# Randomize only the final general-tag section.
# Header sections like quality/meta/period/safety/count/characters/copyright/artists stay stable.
RANDOMIZE_GENERAL_TAGS = True

# None = fresh random order each run.
# Use an integer like 1234 for repeatable random order.
RANDOM_SEED = None

# If the CSV has no "quality tag" column, this derives it from "score".
DERIVE_QUALITY_FROM_SCORE_IF_MISSING = True

# If the CSV has no "period" column, this derives it from "created_at".
DERIVE_PERIOD_FROM_CREATED_AT_IF_MISSING = True

# Change these if you want different period cutoffs.
PERIOD_YEAR_CUTOFFS = {
    "newest": 2021,
    "recent": 2018,
    "mid": 2015,
    "early": 2011,
}

# Only these meta tags from the CSV "meta" column are allowed into .tag files.
# Danbooru raw tags use underscores, but this script normalizes output to spaces.
ALLOWED_META_TXT_TAGS = {
    "acrylic paint (medium)",
    "airbrush (medium)",
    "ballpoint pen (medium)",
    "brush (medium)",
    "chalk (medium)",
    "calligraphy brush (medium)",
    "painting",
    "charcoal (medium)",
    "colored pencil (medium)",
    "color ink (medium)",
    "coupy pencil (medium)",
    "crayon (medium)",
    "gouache (medium)",
    "graphite (medium)",
    "ink (medium)",
    "marker (medium)",
    "millipen (medium)",
    "nib pen (medium)",
    "oil painting (medium)",
    "painting (medium)",
    "pastel (medium)",
    "photo (medium)",
    "tempera (medium)",
    "watercolor (medium)",
    "watercolor pencil (medium)",
    "traditional media",
}

# General tags that are moved into the count-tag slot and removed from final general tags.
COUNT_TAGS = {
    "1girl",
    "2girls",
    "3girls",
    "4girls",
    "5girls",
    "6+girls",

    "1boy",
    "2boys",
    "3boys",
    "4boys",
    "5boys",
    "6+boys",

    "1other",
    "2others",
    "3others",
    "4others",
    "5others",
    "6+others",

    # Kept in case your data contains it.
    "others",
}


# ---------------- TAG HELPERS ----------------
def normalize_header(name: str) -> str:
    """Normalize CSV header names for case-insensitive lookup."""
    return " ".join(str(name).strip().lower().replace("_", " ").split())


def normalize_tag(tag: str) -> str:
    """Normalize one tag for output: underscores to spaces, trim extra whitespace."""
    return " ".join(str(tag).replace("_", " ").strip().split())


def split_csv_tags(value: str) -> list[str]:
    """Split comma-separated tag fields from CSV values."""
    if not value:
        return []

    tags: list[str] = []

    for part in str(value).split(","):
        tag = normalize_tag(part)
        if tag:
            tags.append(tag)

    return tags


def split_csv_or_single_tag(value: str) -> list[str]:
    """
    Split CSV-created tag fields.

    Important:
    If there is no comma, treat the whole value as ONE tag.

    This preserves tags like:
      sousou no frieren
      fate grand order
      to aru majutsu no index
      frieren
      ringsel
    """
    if not value:
        return []

    text = str(value).strip()
    if not text:
        return []

    if "," in text:
        return split_csv_tags(text)

    return [normalize_tag(text)]


def split_meta_tags(value: str) -> list[str]:
    """
    Split meta tags.

    CSV-created meta fields:
      watercolor (medium), traditional media

    Raw Danbooru-style fallback:
      watercolor_(medium) traditional_media

    For no-comma values:
      - if it looks like raw Danbooru space-separated tags, split on spaces
      - otherwise preserve it as one tag
    """
    if not value:
        return []

    text = str(value).strip()
    if not text:
        return []

    if "," in text:
        return split_csv_tags(text)

    # Raw Danbooru-style multi-tag fallback.
    # Example: "watercolor_(medium) traditional_media"
    if "_" in text and " " in text:
        return [
            normalize_tag(part)
            for part in text.split()
            if normalize_tag(part)
        ]

    # Single CSV-style tag.
    # Example: "watercolor (medium)" or "traditional media"
    return [normalize_tag(text)]


def dedupe_preserve_order(tags: Iterable[str]) -> list[str]:
    """Remove duplicates while preserving first occurrence and order."""
    out: list[str] = []
    seen: set[str] = set()

    for tag in tags:
        tag = normalize_tag(tag)
        if not tag:
            continue

        key = tag.lower()
        if key not in seen:
            out.append(tag)
            seen.add(key)

    return out


def get_column(row: dict[str, str], *names: str) -> str:
    """Read a CSV value using normalized/case-insensitive header aliases."""
    normalized_row = {
        normalize_header(k): v
        for k, v in row.items()
        if k is not None
    }

    for name in names:
        value = normalized_row.get(normalize_header(name))
        if value is not None:
            return str(value).strip()

    return ""


def get_period_tag(created_at: str) -> str:
    """Same period mapping as the Danbooru update script."""
    if not created_at:
        return ""

    try:
        parsed_date = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        year = parsed_date.year
    except Exception:
        return ""

    if year >= PERIOD_YEAR_CUTOFFS["newest"]:
        return "newest"
    if year >= PERIOD_YEAR_CUTOFFS["recent"]:
        return "recent"
    if year >= PERIOD_YEAR_CUTOFFS["mid"]:
        return "mid"
    if year >= PERIOD_YEAR_CUTOFFS["early"]:
        return "early"

    return "old"


def get_rating_tags(rating: str) -> list[str]:
    """Map Danbooru rating to safety tags, same as the update script."""
    rating = str(rating).strip().lower()

    rating_map = {
        "e": ["explicit", "nsfw"],
        "explicit": ["explicit", "nsfw"],

        "q": ["nsfw"],
        "questionable": ["nsfw"],

        "s": ["sensitive"],
        "sensitive": ["sensitive"],

        "g": ["safe"],
        "general": ["safe"],
    }

    return rating_map.get(rating, [])


def get_meta_tags_for_output(meta_value: str) -> list[str]:
    """Whitelist useful traditional-media / medium-related meta tags only."""
    out: list[str] = []

    for tag in split_meta_tags(meta_value):
        tag = normalize_tag(tag)
        if tag.lower() in ALLOWED_META_TXT_TAGS:
            out.append(tag)

    return out


def split_count_tags_from_general(general_tags: Iterable[str]) -> tuple[list[str], list[str]]:
    """Move 1girl/1boy/1other/etc. into a dedicated slot."""
    count_tags: list[str] = []
    remaining_general_tags: list[str] = []

    for tag in general_tags:
        tag = normalize_tag(tag)
        if not tag:
            continue

        if tag.lower() in COUNT_TAGS:
            count_tags.append(tag)
        else:
            remaining_general_tags.append(tag)

    return count_tags, remaining_general_tags


def prefix_artist_tags(artist_tags: Iterable[str]) -> list[str]:
    """Prefix every artist tag with @, without double-prefixing."""
    out: list[str] = []

    for artist in artist_tags:
        artist = normalize_tag(artist)
        if not artist:
            continue

        if artist.startswith(ARTIST_PREFIX):
            out.append(artist)
        else:
            out.append(f"{ARTIST_PREFIX}{artist}")

    return out


# ---------------- FILE HELPERS ----------------
def read_first_csv_row(csv_path: Path) -> dict[str, str] | None:
    """Read the first data row from a CSV file."""
    try:
        with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                return dict(row)
    except Exception as exc:
        print(f"ERROR reading CSV {csv_path}: {exc}")
        return None

    return None


def iter_csv_files(folder: Path) -> Iterable[Path]:
    """Yield CSV files from the configured image folder."""
    pattern = "**/*.csv" if RECURSIVE else "*.csv"
    yield from sorted(folder.glob(pattern))


# ---------------- BUILD LOGIC ----------------
def build_tag_list(
    row: dict[str, str],
    cleaned_general_tags: list[str],
    rng: random.Random,
    add_year_tag: bool = False,
    add_copyright_tags: bool = False,
) -> list[str]:
    """Build the final ordered tag list from one CSV row."""
    # [quality tag]
    quality_tag = get_column(row, "quality tag", "quality")
    if not quality_tag and DERIVE_QUALITY_FROM_SCORE_IF_MISSING:
        quality_tag = get_quality_tag(get_column(row, "score"))
    quality_tags = [normalize_quality_tag(quality_tag)] if quality_tag else []

    # [metatags]
    meta_tags = get_meta_tags_for_output(
        get_column(row, "meta", "metatags", "meta tags")
    )

    # [Period]
    period_tag = get_column(row, "period")
    if not period_tag and DERIVE_PERIOD_FROM_CREATED_AT_IF_MISSING:
        period_tag = get_period_tag(get_column(row, "created_at", "created at"))
    period_tags = [normalize_tag(period_tag)] if period_tag else []

    year_tag = (get_column(row, "year tag") or get_year_tag(get_column(row, "created_at", "created at"))) if add_year_tag else ""

    # [safety tags]
    rating = get_column(row, "rating")
    safety_tags = get_rating_tags(rating)

    if not safety_tags:
        safety_tags = split_csv_tags(get_column(row, "safety tags", "safety"))

    # [1girl/1boy/1other etc] + [general tags]
    count_tags, general_tags = split_count_tags_from_general(cleaned_general_tags)

    # Randomize only final general tags.
    if RANDOMIZE_GENERAL_TAGS:
        general_tags = list(general_tags)
        rng.shuffle(general_tags)

    # [characters]
    character_tags = split_csv_or_single_tag(
        get_column(row, "characters", "character")
    )

    # [Copyright]
    copyright_tags = (
        split_csv_or_single_tag(get_column(row, "copyright", "copyrights", "copytags"))
        if add_copyright_tags else []
    )

    # [@artists]
    raw_artist_tags = split_csv_or_single_tag(
        get_column(row, "artists", "artist")
    )
    artist_tags = prefix_artist_tags(raw_artist_tags)

    # Final order:
    # [quality tag], [metatags], [Period], [safety tags],
    # [1girl/1boy/1other etc], [characters], [Copyright], [@artists], [general tags]
    ordered_tags = dedupe_preserve_order(
        [
            *quality_tags,
            *meta_tags,
            *period_tags,
            *([year_tag] if year_tag else []),
            *safety_tags,
            *count_tags,
            *character_tags,
            *copyright_tags,
            *artist_tags,
            *general_tags,
        ]
    )
    return [tag for tag in ordered_tags if tag.casefold() not in EXCLUDED_OUTPUT_TAGS]


def process_csv(
    csv_path: Path,
    rng: random.Random,
    dropout_rng: random.Random,
    dropout_protected_tags: set[str],
    report_success: bool = True,
    add_year_tag: bool = False,
    add_copyright_tags: bool = False,
) -> bool:
    """Create one .tag file directly from one .csv."""
    output_path = csv_path.with_suffix(OUTPUT_EXTENSION)

    if output_path.exists() and not OVERWRITE_EXISTING:
        print(f"SKIP existing: {output_path.name}")
        return False

    row = read_first_csv_row(csv_path)
    if row is None:
        print(f"SKIP unreadable/empty CSV: {csv_path.name}")
        return False

    general_value = get_column(row, "general", "general tags")
    clean_tags, conditional_hits = clean_general_tags_with_debug(general_value)
    write_conditional_log(csv_path, conditional_hits)
    clean_tags = apply_tag_dropout(
        clean_tags,
        dropout_rate=DROPOUT_RATE if DROPOUT_ENABLED else 0.0,
        rng=dropout_rng,
        min_tags=DROPOUT_MIN_TAGS,
        protected_tags=dropout_protected_tags,
    )

    final_tags = build_tag_list(
        row, clean_tags, rng,
        add_year_tag=add_year_tag,
        add_copyright_tags=add_copyright_tags,
    )

    try:
        output_path.write_text(", ".join(final_tags), encoding="utf-8")
    except Exception as exc:
        print(f"ERROR writing {output_path}: {exc}")
        return False

    if report_success:
        print(f"WROTE {output_path.name} ({len(final_tags)} tags)")
    return True


# ---------------- MAIN ----------------
def main() -> None:
    folder = Path(IMAGE_FOLDER)

    if not folder.exists() or not folder.is_dir():
        print(f"Image folder does not exist: {folder}")
        return

    csv_files = list(iter_csv_files(folder))

    if not csv_files:
        print(f"No CSV files found in: {folder}")
        return

    rng = random.Random(RANDOM_SEED)
    dropout_rng = random.Random(DROPOUT_SEED)
    dropout_rate = DROPOUT_RATE if DROPOUT_ENABLED else 0.0
    if not 0.0 <= dropout_rate <= 1.0:
        raise SystemExit("DROPOUT_RATE must be between 0.0 and 1.0")
    dropout_rate = DROPOUT_RATE if DROPOUT_ENABLED else 0.0
    if not 0.0 <= dropout_rate <= 1.0:
        raise SystemExit("DROPOUT_RATE must be between 0.0 and 1.0")
    dropout_rate = DROPOUT_RATE if DROPOUT_ENABLED else 0.0
    if not 0.0 <= dropout_rate <= 1.0:
        raise SystemExit("DROPOUT_RATE must be between 0.0 and 1.0")
    dropout_protected_tags = {
        normalize_clean_tag(tag) for tag in JAVA_100_DROPOUT_PROTECT_TAGS
    }
    dropout_protected_tags.update(
        normalize_clean_tag(tag) for tag in EXTRA_DROPOUT_PROTECT_TAGS
    )
    dropout_protected_tags.discard("")

    if DEBUG_CONDITIONAL_RULES:
        (folder / CONDITIONAL_LOG_FILE).write_text("", encoding="utf-8")

    if DEBUG_CONDITIONAL_RULES:
        (folder / CONDITIONAL_LOG_FILE).write_text("", encoding="utf-8")

    if DEBUG_CONDITIONAL_RULES:
        (folder / CONDITIONAL_LOG_FILE).write_text("", encoding="utf-8")

    print(f"Image folder: {folder}")
    print(f"Found {len(csv_files)} CSV file(s).")
    print(f"Randomize general tags: {RANDOMIZE_GENERAL_TAGS}")
    print(f"Random seed: {RANDOM_SEED}")
    print(f"Artist prefix: {ARTIST_PREFIX}")

    written = 0

    for csv_path in csv_files:
        if process_csv(csv_path, rng, dropout_rng, dropout_protected_tags):
            written += 1

    print(f"Done. Wrote {written} .tag file(s).")


if __name__ == "__main__":
    main()
