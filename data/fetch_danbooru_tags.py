from quality_tags import get_quality_tag, normalize_quality_tag
import os
import csv
import argparse
import requests
from pathlib import Path
from refresh_tags_from_danbooru import post_to_tags, get_period_tag, get_rating_tags
from tqdm import tqdm
from danbooru_client import danbooru_get
from gelbooru_client import lookup_gelbooru

# ---------------- CONFIG ----------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # script location
IMAGE_FOLDER = os.path.join(SCRIPT_DIR, "..", "images")  # one level above
IMAGE_FOLDER = os.path.abspath(IMAGE_FOLDER)
os.makedirs(IMAGE_FOLDER, exist_ok=True)

# ---------------- DANBOORU CONFIG ----------------
DANBOORU_API = "https://danbooru.donmai.us/posts.json"

# ---------------- HELPER FUNCTIONS ----------------
def lookup_danbooru(md5_hash):
    """Lookup tags for an image on Danbooru using MD5 hash."""
    params = {"tags": f"md5:{md5_hash}"}
    try:
        r = danbooru_get(DANBOORU_API, params=params, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(f"Danbooru lookup failed for {md5_hash}: {error}") from error

    results = r.json()
    if isinstance(results, list) and results:
        post = results[0]
        return post_to_tags(post)
    return None

def format_tags(tag_string):
    """Format tag string for saving."""
    tags = tag_string.strip().split()
    return ", ".join([t.replace("_", " ") for t in tags])

def save_tags(md5_hash, tags, overwrite_existing_txt=False):
    """Save CSV and TXT files for an image's tags."""
    csv_path = os.path.join(IMAGE_FOLDER, f"{md5_hash}.csv")
    txt_path = os.path.join(IMAGE_FOLDER, f"{md5_hash}.txt")

    score = tags.get("score", 0)
    quality_tag = get_quality_tag(score)

    row = {
        "md5": md5_hash,
        "post_id": tags.get("post_id", ""),
        "post_source": tags.get("post_source", ""),
        **{key: format_tags(tags.get(key, "")) for key in
           ("characters", "copyright", "artists", "general", "meta")},
        "rating": tags.get("rating", ""),
        "safety tags": ", ".join(get_rating_tags(tags.get("rating", ""))),
        "period": get_period_tag(tags.get("created_at", "")),
        "source": tags.get("source", ""),
        "score": score,
        "quality tag": quality_tag,
        "created_at": tags.get("created_at", ""),
    }
    # Enrich legacy CSVs without discarding manually edited/tagger-added fields.
    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8-sig") as handle:
            old = next(csv.DictReader(handle), {})
        row.update({key: value for key, value in old.items() if key and value})
    row["quality tag"] = normalize_quality_tag(row["quality tag"])
    temporary = Path(csv_path + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    temporary.replace(csv_path)

    if overwrite_existing_txt or not os.path.exists(txt_path):
        all_tags = [row[key] for key in ("characters", "copyright", "artists", "general", "safety tags", "quality tag") if row[key]]
        Path(txt_path).write_text(", ".join(all_tags), encoding="utf-8")

# ---------------- MAIN ----------------
def main(overwrite_existing_txt=False):
    IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff")

    files = [f for f in os.listdir(IMAGE_FOLDER)
             if f.lower().endswith(IMAGE_EXTENSIONS)]

    for file in tqdm(files, desc="Processing images"):
        base, ext = os.path.splitext(file)
        md5_hash = base.lower()

        if len(md5_hash) != 32:
            tqdm.write(f"Skipping {file}: not an MD5 hash")
            continue

        txt_path = os.path.join(IMAGE_FOLDER, f"{md5_hash}.txt")
        if os.path.exists(txt_path) and not overwrite_existing_txt:
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
            continue

        tqdm.write(f"Found tags on {tags.get('source', '')}, score: {tags.get('score', 0)}")
        save_tags(md5_hash, tags, overwrite_existing_txt=overwrite_existing_txt)

    print("Done!\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Danbooru tags with Gelbooru fallback for image files.")
    parser.add_argument(
        "--overwrite-existing-txt",
        action="store_true",
        help="Fetch tags even when a matching TXT file exists and overwrite that TXT file.",
    )
    args = parser.parse_args()
    main(overwrite_existing_txt=args.overwrite_existing_txt)
