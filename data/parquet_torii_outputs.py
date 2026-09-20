"""Stream Torii training-data Parquet files into reconstructed reports."""

from __future__ import annotations

from pathlib import Path
from parquet_lookup import matching_rows


STRUCTURED_FIELDS = (
    "min_individual_md",
    "comic_md",
    "character_thoughts",
    "long_thoughts",
    "chroma_style",
    "min_individual_json",
    "comic_json",
    "json_names",
    "json_no_names",
)
LONG_FIELDS = ("long_names", "long_no_names")
AUXILIARY_FIELDS = ("short", "few_words", "vibes", "bbox", "chars")
OUTPUT_FIELDS = STRUCTURED_FIELDS + LONG_FIELDS + AUXILIARY_FIELDS


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _first_text(row: dict[str, object], fields: tuple[str, ...]) -> tuple[str, str]:
    for field in fields:
        value = _text(row.get(field))
        if value:
            return field, value
    return "", ""


def reconstruct_torii_output(row: dict[str, object]) -> str | None:
    """Build a rich report while avoiding duplicate competing descriptions."""
    _, structured = _first_text(row, STRUCTURED_FIELDS)
    _, long_caption = _first_text(row, LONG_FIELDS)
    short = _text(row.get("short"))

    if not structured:
        structured = long_caption or short
    if not structured:
        return None

    sections = ["# Structured analysis", "", structured]
    if long_caption and long_caption != structured:
        sections.extend(["", "# Long detailed caption", "", long_caption])
    if short and short not in (structured, long_caption):
        sections.extend(["", "# Concise dataset summary", "", short])

    for field, heading in (
        ("few_words", "Key visual concepts"),
        ("vibes", "Mood and style"),
        ("bbox", "Subject bounding boxes"),
    ):
        value = _text(row.get(field))
        if value:
            sections.extend(["", f"# {heading}", "", value])

    characters = row.get("chars")
    if isinstance(characters, list) and characters:
        sections.extend(
            ["", "# Dataset character tags", "", ", ".join(map(str, characters))]
        )
    return "\n".join(sections).strip()


def load_parquet_torii_outputs(
    folder: Path,
    wanted_names: set[str],
    *,
    batch_size: int = 2048,
) -> dict[str, str]:
    """Return reports for wanted names without loading a whole Parquet file.

    Only ``batch_size`` rows of selected columns are materialized at a time. Once
    all requested names have matched, scanning stops immediately.
    """
    wanted = {name.strip().lower() for name in wanted_names if name.strip()}
    if not wanted:
        return {}
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError(
            "Parquet Torii outputs require pyarrow. Install with: "
            "python -m pip install pyarrow"
        ) from error

    results: dict[str, str] = {}
    for path in sorted(folder.glob("*.parquet")):
        parquet = pq.ParquetFile(path)
        available = set(parquet.schema_arrow.names)
        if "name" not in available or not available.intersection(OUTPUT_FIELDS):
            parquet.close()
            continue
        columns = ["name", *(field for field in OUTPUT_FIELDS if field in available)]
        print(f"Reading Torii reports from {path.name}...", flush=True)
        try:
            for row in matching_rows(parquet, "name", columns, wanted, batch_size):
                key = row["name"].strip().lower()
                report = reconstruct_torii_output(row)
                if report:
                    results.setdefault(key, report)
                    wanted.discard(key)
                if not wanted:
                    return results
        finally:
            parquet.close()
    return results
