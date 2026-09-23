from quality_tags import get_quality_tag
import os
import csv
import time
import requests
from datetime import datetime
from tqdm import tqdm
from danbooru_client import danbooru_get
from gelbooru_client import lookup_gelbooru
from year_tags import get_year_tag

# ---------------- CONFIG ----------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_FOLDER = os.path.join(SCRIPT_DIR, "..", "images")
IMAGE_FOLDER = os.path.abspath(IMAGE_FOLDER)
os.makedirs(IMAGE_FOLDER, exist_ok=True)

IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".tiff",
)

REQUEST_DELAY_SECONDS = 0.5

# ---------------- DANBOORU CONFIG ----------------
DANBOORU_API = "https://danbooru.donmai.us/posts.json"

# ---------------- TXT TAG FILTER CONFIG ----------------

# Only these meta tags are allowed in TXT files.
# Danbooru raw tags use underscores, but TXT output uses spaces.
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

# General tags that should be moved into their own position in the TXT order
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

    "others",
}

# Change these if you want different period cutoffs
PERIOD_YEAR_CUTOFFS = {
    "newest": 2021,
    "recent": 2018,
    "mid": 2015,
    "early": 2011,
}


# ---------------- HELPER FUNCTIONS ----------------
def request_danbooru(params, md5_hash):
    """Request Danbooru and return parsed JSON or None."""
    try:
        response = danbooru_get(
            DANBOORU_API,
            params=params,
            timeout=30,
        )

        if response.status_code != 200:
            tqdm.write(
                f"Danbooru HTTP {response.status_code} for {md5_hash}: "
                f"{response.text[:300]}"
            )
            return None

        try:
            return response.json()
        except Exception as e:
            tqdm.write(
                f"Danbooru returned non-JSON for {md5_hash}: {e}\n"
                f"Response start: {response.text[:300]}"
            )
            return None

    except requests.RequestException as e:
        tqdm.write(f"Danbooru request failed for {md5_hash}: {e}")
        return None


def post_to_tags(post):
    """Convert a Danbooru post JSON object into our internal tag dict."""
    return {
        "source": "danbooru",
        "post_id": post.get("id", ""),
        "post_source": post.get("source", ""),
        "characters": post.get("tag_string_character", ""),
        "copyright": post.get("tag_string_copyright", ""),
        "artists": post.get("tag_string_artist", ""),
        "general": post.get("tag_string_general", ""),
        "meta": post.get("tag_string_meta", ""),
        "rating": post.get("rating", ""),
        "score": post.get("score", 0),
        "created_at": post.get("created_at", ""),
    }


def lookup_danbooru(md5_hash):
    """Lookup tags for an image on Danbooru using MD5 hash."""

    lookup_attempts = [
        {
            "md5": md5_hash,
            "limit": 1,
        },
        {
            "tags": f"md5:{md5_hash}",
            "limit": 1,
        },
    ]

    for params in lookup_attempts:
        results = request_danbooru(params, md5_hash)

        # Danbooru may return a single post object for md5=<hash>.
        # Some valid post objects do not include "md5" in the returned JSON,
        # so only require "id".
        if isinstance(results, dict):
            if "id" in results:
                return post_to_tags(results)

            tqdm.write(
                f"Unexpected Danbooru dict response for {md5_hash}: "
                f"{str(results)[:300]}"
            )
            continue

        # Danbooru may return a list for tags=md5:<hash>.
        if isinstance(results, list):
            if results:
                return post_to_tags(results[0])
            continue

    tqdm.write(f"No Danbooru post matched MD5 {md5_hash}.")
    return None


def split_tags(tag_string):
    """Split a Danbooru tag string into raw tags."""
    if not tag_string:
        return []
    return str(tag_string).strip().split()


def format_tag(tag):
    """Format a single Danbooru tag."""
    return tag.replace("_", " ")


def format_tags(tag_string):
    """Format tag string for saving in CSV."""
    return ", ".join(format_tag(tag) for tag in split_tags(tag_string))


def get_period_tag(created_at):
    """Map Danbooru created_at year to a period tag."""
    if not created_at:
        return ""

    try:
        parsed_date = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        year = parsed_date.year
    except Exception:
        return ""

    if year >= PERIOD_YEAR_CUTOFFS["newest"]:
        return "newest"
    elif year >= PERIOD_YEAR_CUTOFFS["recent"]:
        return "recent"
    elif year >= PERIOD_YEAR_CUTOFFS["mid"]:
        return "mid"
    elif year >= PERIOD_YEAR_CUTOFFS["early"]:
        return "early"
    else:
        return "old"


def get_rating_tags(rating):
    """Map Danbooru rating to TXT safety tags."""
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


def get_meta_tags_for_txt(meta_string):
    """
    Return only useful traditional-media / medium-related meta tags for TXT.
    All other Danbooru meta tags are ignored.
    """
    meta_tags = []

    for tag in split_tags(meta_string):
        formatted = format_tag(tag)
        normalized = formatted.lower()

        if normalized in ALLOWED_META_TXT_TAGS:
            meta_tags.append(formatted)

    return meta_tags


def split_general_tags(general_string):
    """
    Split general tags into:
    - count tags like 1girl, 2boys, 1other, etc.
    - remaining general tags
    """
    count_tags = []
    remaining_general_tags = []

    for tag in split_tags(general_string):
        formatted = format_tag(tag)

        if tag.lower() in COUNT_TAGS:
            count_tags.append(formatted)
        else:
            remaining_general_tags.append(formatted)

    return count_tags, remaining_general_tags


def dedupe_preserve_order(tags):
    """Remove duplicate tags while preserving order."""
    deduped = []
    seen = set()

    for tag in tags:
        if not tag:
            continue

        tag = str(tag).strip()

        if not tag:
            continue

        normalized = tag.lower()

        if normalized not in seen:
            deduped.append(tag)
            seen.add(normalized)

    return deduped


def save_tags(md5_hash, tags):
    """Save CSV and TXT files for an image's tags."""
    csv_path = os.path.join(IMAGE_FOLDER, f"{md5_hash}.csv")
    txt_path = os.path.join(IMAGE_FOLDER, f"{md5_hash}.txt")

    score = tags.get("score", 0)
    quality_tag = get_quality_tag(score)

    period_tag = get_period_tag(tags.get("created_at", ""))

    rating = tags.get("rating", "")
    safety_tags = get_rating_tags(rating)

    meta_tags = get_meta_tags_for_txt(tags.get("meta", ""))

    count_tags, general_tags = split_general_tags(tags.get("general", ""))

    character_tags = [
        format_tag(tag)
        for tag in split_tags(tags.get("characters", ""))
    ]

    copyright_tags = [
        format_tag(tag)
        for tag in split_tags(tags.get("copyright", ""))
    ]

    artist_tags = [
        format_tag(tag)
        for tag in split_tags(tags.get("artists", ""))
    ]

    try:
        # ---------------- SAVE CSV ----------------
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            writer.writerow([
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
            ])

            writer.writerow([
                md5_hash,
                tags.get("post_id", ""),
                tags.get("post_source", ""),
                format_tags(tags.get("characters", "")),
                format_tags(tags.get("copyright", "")),
                format_tags(tags.get("artists", "")),
                format_tags(tags.get("general", "")),
                format_tags(tags.get("meta", "")),
                rating,
                ", ".join(safety_tags),
                period_tag,
                tags.get("source", ""),
                score,
                quality_tag,
                tags.get("created_at", ""),
                get_year_tag(tags.get("created_at", "")),
            ])

        # ---------------- SAVE TXT ----------------
        # TXT order:
        # [quality tag],
        # [metatags],
        # [Period],
        # [safety tags],
        # [1girl/1boy/1other etc],
        # [characters],
        # [Copyright],
        # [artists],
        # [general tags]

        all_tags = []

        all_tags.append(quality_tag)
        all_tags.extend(meta_tags)

        if period_tag:
            all_tags.append(period_tag)

        all_tags.extend(safety_tags)
        all_tags.extend(count_tags)
        all_tags.extend(character_tags)
        all_tags.extend(copyright_tags)
        all_tags.extend(artist_tags)
        all_tags.extend(general_tags)

        all_tags = dedupe_preserve_order(all_tags)

        with open(txt_path, "w", encoding="utf-8") as f_txt:
            f_txt.write(", ".join(all_tags))

    except Exception as e:
        print(f"Error saving CSV/TXT for {md5_hash}: {e}")


# ---------------- MAIN ----------------
def main():
    files = [
        f for f in os.listdir(IMAGE_FOLDER)
        if f.lower().endswith(IMAGE_EXTENSIONS)
    ]

    if not files:
        print(f"No images found in: {IMAGE_FOLDER}")
        return

    print(f"Image folder: {IMAGE_FOLDER}")
    print(f"Found {len(files)} image file(s).")

    for file in tqdm(files, desc="Processing images"):
        base, ext = os.path.splitext(file)
        md5_hash = base.lower()

        if len(md5_hash) != 32:
            tqdm.write(f"Skipping {file}: filename is not a 32-character MD5 hash")
            continue

        csv_path = os.path.join(IMAGE_FOLDER, f"{md5_hash}.csv")
        if os.path.exists(csv_path):
            tqdm.write(f"Skipping {file}: CSV already exists")
            continue

        tqdm.write(f"\nProcessing MD5: {md5_hash}")

        try:
            tags = lookup_danbooru(md5_hash)
            if not tags:
                tags = lookup_gelbooru(md5_hash)
        except RuntimeError as error:
            tqdm.write(f"Skipping {md5_hash} after lookup error: {error}")
            continue

        if not tags:
            tqdm.write(f"No tags found for {md5_hash} on Danbooru or Gelbooru.")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        period = get_period_tag(tags.get("created_at", ""))

        tqdm.write(
            f"Found tags on {tags.get('source', '')}, "
            f"post ID: {tags.get('post_id', '')}, "
            f"score: {tags.get('score', 0)}, "
            f"rating: {tags.get('rating', '')}, "
            f"period: {period}"
        )

        save_tags(md5_hash, tags)

        time.sleep(REQUEST_DELAY_SECONDS)

    print("Done!\n")


if __name__ == "__main__":
    main()
