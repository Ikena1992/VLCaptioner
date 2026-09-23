import os
import csv
import logging
import requests
import time
from tqdm import tqdm
from danbooru_client import danbooru_get
from danbooru_cache import TagCategoryCache
from source_tag_file import read_source_tags, read_source_metadata

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FOLDER = os.path.join(SCRIPT_DIR, "..", "images")
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

# Danbooru may return HTTP 429 when uncached tags are queried in a tight loop.
# Keep category lookups comfortably below the request-rate limit and retry a
# throttled request instead of presenting a valid tag as unresolved.
CATEGORY_REQUEST_INTERVAL_SECONDS = 0.35
CATEGORY_RATE_LIMIT_RETRIES = 4
last_category_request_time = 0.0


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
    global last_category_request_time
    cache_key = normalize_for_danbooru(tag)

    # Backwards compatibility for older cache entries that used the TXT tag as key.
    if cache_key in tag_cache and tag_cache[cache_key] is not None:
        return tag_cache[cache_key]
    if tag in tag_cache and tag_cache[tag] is not None:
        category = tag_cache[tag]
        tag_cache[cache_key] = category
        return category

    # Older versions cached failed requests as None. Remove those entries so
    # this run can retry the API instead of treating them as known categories.
    tag_cache.pop(cache_key, None)
    if tag != cache_key:
        tag_cache.pop(tag, None)

    try:
        response = None
        for attempt in range(CATEGORY_RATE_LIMIT_RETRIES + 1):
            wait_seconds = CATEGORY_REQUEST_INTERVAL_SECONDS - (
                time.monotonic() - last_category_request_time
            )
            if wait_seconds > 0:
                time.sleep(wait_seconds)

            response = danbooru_get(
                "https://danbooru.donmai.us/tags.json",
                params={"search[name]": cache_key},
                timeout=30,
            )
            last_category_request_time = time.monotonic()

            if response.status_code != 429:
                break

            retry_after = response.headers.get("Retry-After", "")
            try:
                retry_delay = max(float(retry_after), 2 ** (attempt + 1))
            except ValueError:
                retry_delay = 2 ** (attempt + 1)
            logger.warning(
                "Category lookup rate-limited for %r; retrying in %.1fs",
                tag,
                retry_delay,
            )
            if attempt < CATEGORY_RATE_LIMIT_RETRIES:
                time.sleep(retry_delay)

        if response is None or response.status_code != 200:
            status = response.status_code if response is not None else "unknown"
            logger.warning("Category lookup failed for %r: HTTP %s", tag, status)
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


def tags_to_csv_row(md5_name, tags):
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

    for tag in tags:
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


def write_csv(csv_file_path, row):
    """Write one CSV file using the Danbooru updater's CSV schema."""
    with open(csv_file_path, mode="w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow(row)


txt_files = [
    f for f in os.listdir(INPUT_FOLDER)
    if f.lower().endswith(".txt")
]

# Process TXT files into CSV files.
unresolved_files = 0
for filename in tqdm(txt_files, desc="Processing TXT files"):
    md5_name = os.path.splitext(filename)[0]
    csv_file_path = os.path.join(INPUT_FOLDER, f"{md5_name}.csv")

    # Skip if CSV already exists
    if os.path.exists(csv_file_path):
        continue

    file_path = os.path.join(INPUT_FOLDER, filename)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    tags = split_txt_tags(content)
    try:
        metadata = read_source_metadata(content)
        if metadata is not None:
            row = {key: str(metadata.get(key, "")) for key in CSV_FIELDNAMES}
            row["md5"] = md5_name
            original = {tag.casefold() for key in ("characters", "copyright", "artists", "general", "meta", "safety tags", "quality tag", "period") for tag in split_txt_tags(row[key])}
            additions = [tag for tag in tags if tag.casefold() not in original]
            if additions:
                row["general"] = ", ".join(filter(None, [row["general"], *additions]))
        else:
            row = tags_to_csv_row(md5_name, tags)
    except (RuntimeError, ValueError) as error:
        unresolved_files += 1
        print(f"Skipping {filename}: {error}. It will be retried next run.")
        continue
    write_csv(csv_file_path, row)

print("Processed TXT files into individual CSVs; images without TXT remain in images/.")
print(f"Deferred {unresolved_files} file(s) with unresolved Danbooru categories.")
print("Done!\n")
