from pathlib import Path
import csv
import sqlite3
import re
from tqdm import tqdm  # pip install tqdm
from cache_seed import ensure_runtime_cache

# -------------------------------------------------
# Configuration
# -------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
IMAGES_DIR = (SCRIPT_DIR / ".." / "images").resolve()

CHARACTER_DB = SCRIPT_DIR / "caches/danbooru_character_explanationsFromVLM.sqlite3"
WIKI_CACHE_DB = SCRIPT_DIR / "caches/cache_wiki.sqlite3"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def _load_sqlite_explanations(
    database_path: Path,
    table: str,
    explanation_column: str,
) -> dict[str, str]:
    database_path = ensure_runtime_cache(database_path)
    if not database_path.exists():
        return {}

    with sqlite3.connect(database_path) as database:
        rows = database.execute(
            f"SELECT tag, {explanation_column} FROM {table}"
        )
        return {
            tag.lower(): explanation
            for tag, explanation in rows
            if tag and explanation
        }


def load_explanations(database_path: Path) -> dict[str, str]:
    """Load VLM-generated character references from SQLite."""
    return _load_sqlite_explanations(
        database_path, "character_explanations", "standard"
    )


def load_wiki_explanations(database_path: Path) -> dict[str, str]:
    """Load Danbooru wiki character references from SQLite."""
    return _load_sqlite_explanations(database_path, "wiki_cache", "body")


def load_local_metadata(image_path: Path) -> tuple[str | None, str | None]:
    csv_file = image_path.with_suffix(".csv")
    if not csv_file.exists():
        return None, None

    with open(csv_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            return (
                row.get("characters", "").strip(),
                row.get("artists", "").strip(),
            )

    return None, None


def pick_best_character_variants(raw_names):
    groups = {}

    for name in raw_names:
        base = re.sub(r"\s*\([^)]*\)", "", name).strip().lower()

        if base not in groups:
            groups[base] = []

        groups[base].append(name)

    def score(name):
        return (
            name.count("("),  # more qualifiers = better
            len(name)         # longer = better
        )

    best = []
    for variants in groups.values():
        best_variant = max(variants, key=score)
        best.append(best_variant)

    return best


def clean_character_names(names):
    cleaned = {
        re.sub(r"\s*\([^)]*\)", "", n).strip().lower():
        re.sub(r"\s*\([^)]*\)", "", n).strip()
        for n in names
    }
    return list(cleaned.values())


def clean_explanation(text: str) -> str:
    text = re.split(r'h[45]|Voiced by', text, flags=re.IGNORECASE)[0]
    return text.strip()


# -------------------------------------------------
# Core Logic
# -------------------------------------------------
def build_torii_prompts(
    image_path: Path,
    char_explanations: dict,
    char_explanations_fallback: dict,
) -> dict[str, str] | None:
    tag_file = image_path.with_suffix(".txt")

    if not tag_file.exists():
        return None

    raw_tags = [
        t.strip() for t in tag_file.read_text(encoding="utf-8").split(",") if t.strip()
    ]
    # ---------------- Characters ----------------
    characters_raw, artists_raw = load_local_metadata(image_path)

    artist_tags = set()
    if artists_raw:
        artist_tags = {
            a.strip().lower()
            for a in artists_raw.split(",")
            if a.strip()
        }

    character_names = []
    character_traits = []

    if characters_raw:
        split_names = [c.strip() for c in characters_raw.split(",") if c.strip()]

        if "gilberta (arknights)" in map(str.lower, split_names):
            split_names = [c for c in split_names if c.lower() != "angelina (arknights)"]
        if "laevatain (arknights)" in map(str.lower, split_names):
            split_names = [c for c in split_names if c.lower() != "surtr (arknights)"]
        if "female endministrator (arknights)" in map(str.lower, split_names):
            split_names = [c for c in split_names if c.lower() != "endministrator (arknights)"]
        if "male endministrator (arknights)" in map(str.lower, split_names):
            split_names = [c for c in split_names if c.lower() != "endministrator (arknights)"]
        if "ardelia_(arknights)" in map(str.lower, split_names):
            split_names = [c for c in split_names if c.lower() != "eyjafjalla_(arknights)"]

        raw_names = pick_best_character_variants(split_names)
        for raw_name in raw_names:
            lookup_key = raw_name.lower()
            explanation = (
                char_explanations.get(lookup_key)
                or char_explanations_fallback.get(lookup_key)
            )
            if explanation:
                explanation = clean_explanation(explanation)
                character_traits.append(f"{raw_name}: [{explanation}]")

        character_names = raw_names

    # -------------------------------------------------
    # Prompt Construction
    # -------------------------------------------------
    grounding = []

    filtered_tags = [
        t for t in raw_tags
        if t.lower() not in artist_tags
    ]
    tags_string = ', '.join(filtered_tags)
    if tags_string:
        grounding.append(
            " Here are grounding tags for better understanding: "
            f"<tags>{tags_string}</tags>."
        )

    if character_names:
        grounding.append(
            " Here is a list of characters that are present in the picture: "
            f"<characters>{', '.join(character_names)}</characters>."
        )
    else:
        grounding.append(" Do not use names for characters.")

    if character_traits:
        grounding.append(
            " Here are popular tags or traits for each character on the picture: "
            "<character_traits>\n"
            f"{chr(10).join(character_traits)}\n"
            "</character_traits>."
        )
    grounding_text = "".join(grounding)
    return {
        "structured": (
            "Describe the picture in structured markdown format."
            + grounding_text
        ),
        "long": (
            "You need to write a long and very detailed caption for the picture."
            + grounding_text
        ),
    }


# -------------------------------------------------
# Main
# -------------------------------------------------
if __name__ == "__main__":
    if not IMAGES_DIR.exists():
        raise FileNotFoundError(f"Images folder not found: {IMAGES_DIR}")

    char_explanations = load_explanations(CHARACTER_DB)
    char_explanations_fallback = load_wiki_explanations(WIKI_CACHE_DB)

    image_files = [
        img for img in IMAGES_DIR.iterdir()
        if img.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    print("Validating Torii prompts...")

    valid_count = 0

    for image in tqdm(image_files, desc="Processing images", unit="image"):
        if build_torii_prompts(
            image,
            char_explanations,
            char_explanations_fallback,
        ):
            valid_count += 1

    print(f"Done! {valid_count} Torii prompt(s) can be generated in memory.\n")
