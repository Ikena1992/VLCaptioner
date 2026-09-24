"""MD5 lookup and tag normalization for Gelbooru's DAPI."""

import time
from datetime import datetime
from html import unescape
from pathlib import Path

import requests

from runtime_config import CONFIG_FILE, read_settings
from danbooru_cache import TagCategoryCache

API = "https://gelbooru.com/index.php"
_last_request = 0.0
CACHE_PATH = Path(__file__).parent / "caches" / "gelbooru_tag_categories.sqlite3"
CATEGORIES = {0: "general", 1: "artists", 3: "copyright", 4: "characters", 5: "meta"}


def gelbooru_get(section, **params):
    global _last_request
    settings = read_settings(CONFIG_FILE) if CONFIG_FILE.is_file() else {}
    params.update(page="dapi", s=section, q="index", json=1)
    for setting, parameter in (("GELBOORU_USER_ID", "user_id"), ("GELBOORU_API_KEY", "api_key")):
        value = settings.get(setting, "").strip().strip("\"'")
        if value:
            params[parameter] = value
    time.sleep(max(0, 0.5 - (time.monotonic() - _last_request)))
    try:
        response = requests.get(API, params=params, timeout=30,
                                headers={"User-Agent": "VLCaptioner/1.0", "Accept": "application/json"})
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        # Request exception strings can contain the API key in the query URL.
        raise RuntimeError("Gelbooru request failed; check connectivity and GELBOORU_USER_ID/GELBOORU_API_KEY.") from None
    finally:
        _last_request = time.monotonic()


def records(payload, key):
    if isinstance(payload, dict):
        if key not in payload and "@attributes" not in payload:
            raise RuntimeError("Unexpected Gelbooru response.")
        payload = payload.get(key, [])
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise RuntimeError("Unexpected Gelbooru response.")
    return payload


def lookup_gelbooru(md5_hash):
    md5_hash = md5_hash.lower()
    posts = records(gelbooru_get("post", tags=f"md5:{md5_hash}", limit=1), "post")
    post = next((p for p in posts if str(p.get("md5", "")).lower() == md5_hash), None)
    if post is None:
        return None
    tag_types = TagCategoryCache(CACHE_PATH)
    names = unescape(post.get("tags", "")).split()
    missing = list(dict.fromkeys(name for name in names if name not in tag_types))
    for offset in range(0, len(missing), 100):
        batch = missing[offset:offset + 100]
        for tag in records(gelbooru_get("tag", names=" ".join(batch), limit=100), "tag"):
            tag_types[unescape(tag["name"])] = int(tag["type"])
    groups = {category: [] for category in CATEGORIES.values()}
    for name in names:
        groups[CATEGORIES.get(tag_types.get(name), "general")].append(name)
    created_at = str(post.get("created_at", ""))
    try:
        created_at = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").isoformat()
    except ValueError:
        pass
    rating = str(post.get("rating", "")).lower()
    return {
        **{category: " ".join(tags) for category, tags in groups.items()},
        "source": "gelbooru", "post_id": post.get("id", ""),
        "post_source": unescape(post.get("source", "")),
        # Gelbooru's former "safe" rating was renamed "sensitive" when
        # "general" was introduced as the fully work-safe category.
        "rating": {"general": "g", "safe": "s", "sensitive": "s", "questionable": "q", "explicit": "e"}.get(rating, rating),
        "score": int(post.get("score", 0)), "created_at": created_at,
    }
