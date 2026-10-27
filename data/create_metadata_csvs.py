from file_io import atomic_output
from runtime_config import pipeline_images
import os
import csv
from pathlib import Path
from image_failures import is_skipped, skip_image
import logging
from tqdm import tqdm
from danbooru_client import danbooru_get
from danbooru_cache import TagCategoryCache
from source_tag_file import read_source_tags, read_source_metadata
from lighting_tags import LIGHTING_TAGS

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FOLDER = pipeline_images().as_posix()
INPUT_FOLDER = os.path.abspath(INPUT_FOLDER)

CACHE_FOLDER = os.path.join(SCRIPT_DIR, "caches")
os.makedirs(CACHE_FOLDER, exist_ok=True)

# Load or create cache
cache_database = os.path.join(CACHE_FOLDER, "cache_tags.sqlite3")
API_FAILURE_LOG = os.path.join(CACHE_FOLDER, "danbooru_api_failures.log")

logger = logging.getLogger("danbooru_csv_categories")
logger.setLevel(logging.WARNING)
logger.addHandler(logging.FileHandler(API_FAILURE_LOG, encoding="utf-8"))

tag_cache = TagCategoryCache(cache_database)


# Same CSV columns, in the same order, as refresh_tags_from_danbooru.py.
CSV_FIELDNAMES = [
    "md5",
    "post_id",
    "post_source",
    "characters",
    "copyright",
    "artists",
    "general",
    "meta",
    "rating",
    "safety tags",
    "period",
    "source",
    "score",
    "quality tag",
    "created_at",
    "year tag",
]


quality_tags_set = {
    "masterpiece",
    "best quality",
    "high quality",
    "medium quality",
    "good quality",
    "normal quality",
    "low quality",
    "worst quality",
    "very aesthetic",
    "aesthetic",
    "displeasing",
    "very displeasing",
}

# These tags are written into TXT files by refresh_tags_from_danbooru.py,
# but they are not Danbooru tag categories. Keep them out of "general".
safety_tags_set = {
    "explicit",
    "nsfw",
    "sensitive",
    "safe",
}

period_tags_set = {
    "newest",
    "recent",
    "mid",
    "early",
    "old",
}

# The shared client owns the single rate-limit/retry budget for each lookup.
CATEGORY_REQUEST_INTERVAL_SECONDS = 0.35
CATEGORY_RATE_LIMIT_RETRIES = 4


def normalize_for_danbooru(tag):
    """Convert TXT tag formatting back to Danbooru API tag formatting."""
    # TXT captions escape parentheses (for example ``shower \(place\)``),
    # while Danbooru's API expects the canonical name ``shower_(place)``.
    return (
        tag.strip()
        .replace(r"\(", "(")
        .replace(r"\)", ")")
        .replace(" ", "_")
        .lower()
    )


def get_tag_category(tag):
    """Get a confirmed Danbooru category, or None when it cannot be resolved."""
    cache_key = normalize_for_danbooru(tag)

    # Backwards compatibility for older cache entries that used the TXT tag as key.
    if cache_key in tag_cache and tag_cache[cache_key] is not None:
        return tag_cache[cache_key]
    if tag in tag_cache and tag_cache[tag] is not None:
        category = tag_cache[tag]
        tag_cache[cache_key] = category
        return category

    # Some lighting labels use e621 vocabulary and have no Danbooru entry.
    # Use the local category only when no confirmed category is available.
    if cache_key in LIGHTING_TAGS:
        return 0

    # Older versions cached failed requests as None. Remove those entries so
    # this run can retry the API instead of treating them as known categories.
    tag_cache.pop(cache_key, None)
    if tag != cache_key:
        tag_cache.pop(tag, None)

    try:
        with danbooru_get(
            "https://danbooru.donmai.us/tags.json",
            params={"search[name]": cache_key}, timeout=30,
            request_interval=CATEGORY_REQUEST_INTERVAL_SECONDS,
            rate_limit_retries=CATEGORY_RATE_LIMIT_RETRIES,
        ) as response:
            if response.status_code != 200:
                logger.warning("Category lookup failed for %r: HTTP %s", tag, response.status_code)
                return None
            data = response.json()
        if not data or "category" not in data[0]:
            logger.warning("Category lookup returned no usable result for %r", tag)
            return None

        # 0 = general, 1 = artist, 3 = copyright, 4 = character, 5 = meta.
        category = data[0]["category"]
        tag_cache[cache_key] = category
        return category

    except Exception as e:
        logger.warning("Category lookup failed for %r: %s", tag, e)
        return None


def split_txt_tags(content):
    """Split a TXT file's comma-separated tags."""
    return [
        t.strip()
        for t in read_source_tags(content).split(",")
        if t.strip()
    ]


def is_quality_tag(tag):
    """Recognize quality labels locally without querying Danbooru."""
    normalized_tag = tag.strip().lower()
    return (
        normalized_tag in quality_tags_set
        or normalized_tag.endswith(" quality")
    )


def tags_to_csv_row(md5_name, tags, metadata=None):
    """
    Convert tags from one TXT file into the same CSV shape as
    refresh_tags_from_danbooru.py.

    Only values that can be found in the TXT file are filled.
    Values that do not exist in the TXT file are left empty.
    """
    artists = []
    general = []
    characters = []
    copyright_tags = []
    meta = []
    quality = []
    safety_tags = []
    period_tags = []
    known_categories = {}
    if metadata:
        for column, category in (("general", 0), ("artists", 1), ("copyright", 3),
                                 ("characters", 4), ("meta", 5)):
            value = metadata.get(column, "")
            if isinstance(value, str):
                for tag in split_txt_tags(value):
                    known_categories[normalize_for_danbooru(tag)] = category
    seen = set()

    for tag in tags:
        key = normalize_for_danbooru(tag)
        if key in seen:
            continue
        seen.add(key)
        normalized_tag = tag.strip().lower()

        if is_quality_tag(tag):
            quality.append(tag)
            continue

        if normalized_tag in safety_tags_set:
            safety_tags.append(tag)
            continue

        if normalized_tag in period_tags_set:
            period_tags.append(tag)
            continue

        category = known_categories.get(key)
        if category is None:
            category = get_tag_category(tag)
        if category is None:
            raise RuntimeError(f"Danbooru category lookup unresolved for {tag!r}")

        if category == 1:
            artists.append(tag)
        elif category == 3:
            copyright_tags.append(tag)
        elif category == 4:
            characters.append(tag)
        elif category == 5:
            meta.append(tag)
        else:
            general.append(tag)

    return {
        "md5": md5_name,
        "post_id": "",
        "post_source": "",
        "characters": ", ".join(characters),
        "copyright": ", ".join(copyright_tags),
        "artists": ", ".join(artists),
        "general": ", ".join(general),
        "meta": ", ".join(meta),
        "rating": "",
        "safety tags": ", ".join(safety_tags),
        "period": ", ".join(period_tags),
        "source": "",
        "score": "",
        "quality tag": ", ".join(quality),
        "created_at": "",
        "year tag": "",
    }


TAG_COLUMNS = ("characters", "copyright", "artists", "general", "meta",
               "safety tags", "quality tag", "period")


def write_csv(csv_file_path, row):
    """Publish complete metadata only if no destination already exists."""
    try:
        with atomic_output(csv_file_path, newline="", overwrite=False, fsync=True) as handle:
            fields = list(dict.fromkeys([*CSV_FIELDNAMES, *row]))
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(row)
    except FileExistsError:
        return False
    return True


def main():
    txt_files = [p for p in Path(INPUT_FOLDER).iterdir() if p.is_file() and p.suffix.lower() == ".txt"]
    unresolved_files = 0
    created_files = skipped_files = 0
    for path in tqdm(txt_files, desc="Preparing metadata"):
        if is_skipped(path):
            continue
        csv_path = path.with_suffix(".csv")
        try:
            if csv_path.is_file():
                skipped_files += 1
                continue
            if csv_path.exists():
                raise IsADirectoryError(f"CSV output path is not a file: {csv_path}")
            content = path.read_text(encoding="utf-8-sig")
            tags = split_txt_tags(content)
            if not tags:
                raise ValueError("Source tag file has no tags")
            # Create missing CSVs from the visible TXT tags.
            metadata = read_source_metadata(content)
            row = tags_to_csv_row(path.stem, tags, metadata)
            if metadata is not None:
                for key in CSV_FIELDNAMES:
                    if key not in TAG_COLUMNS and key != "md5":
                        value = metadata.get(key, "")
                        row[key] = "" if value is None else str(value)
            if write_csv(csv_path, row):
                created_files += 1
            else:
                skipped_files += 1
        except Exception as error:
            unresolved_files += 1
            skip_image(path, error)
            tqdm.write(f"Could not prepare {path.name}: {error}. Existing files were preserved; retry after fixing the issue.")
    if unresolved_files:
        print(f"Metadata unavailable for {unresolved_files} image(s); remaining images will continue.")
    print(f"Metadata step finished: {created_files} CSV(s) created, {skipped_files} existing CSV(s) skipped, {unresolved_files} image(s) failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
