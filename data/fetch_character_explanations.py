import os
import re
import logging
import sqlite3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter, Retry
from tqdm import tqdm
from danbooru_client import configure_danbooru_session
from danbooru_cache import TagCategoryCache, normalize_cache_key
from cache_seed import ensure_runtime_cache

# -------------------------------
# CONFIG
# -------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FOLDER = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "images"))

CACHE_FOLDER = os.path.join(SCRIPT_DIR, "caches")
os.makedirs(CACHE_FOLDER, exist_ok=True)

API_TAG_URL = "https://danbooru.donmai.us/tags.json"
API_WIKI_URL = "https://danbooru.donmai.us/wiki_pages.json"

CACHE_TAG_DB = os.path.join(CACHE_FOLDER, "cache_tags.sqlite3")
CACHE_WIKI_DB = os.path.join(CACHE_FOLDER, "cache_wiki.sqlite3")
API_FAILURE_LOG = os.path.join(CACHE_FOLDER, "danbooru_api_failures.log")

logger = logging.getLogger("danbooru_character_explanations")
logger.setLevel(logging.WARNING)
logger.addHandler(logging.FileHandler(API_FAILURE_LOG, encoding="utf-8"))

# This is I/O-bound. Override with DANBOORU_WORKERS when tuning for a slower
# connection or stricter API rate limits.
# Danbooru rate-limits aggressively. A small number of long-lived connections
# is usually faster than flooding the API and spending time retrying 429s.
MAX_WORKERS = int(os.environ.get("DANBOORU_WORKERS", "4"))
REQUEST_TIMEOUT = 10
REQUEST_INTERVAL_SECONDS = float(
    os.environ.get("DANBOORU_REQUEST_INTERVAL", "1.0")
)

# -------------------------------
# Session with retries
# -------------------------------
def make_session():
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    return configure_danbooru_session(session)

_thread_state = threading.local()
_request_lock = threading.Lock()
_next_request_time = 0.0


def get_session():
    """Return a connection-pooled session dedicated to the current worker."""
    worker_session = getattr(_thread_state, "session", None)
    if worker_session is None:
        worker_session = make_session()
        _thread_state.session = worker_session
    return worker_session


def api_get(url, **kwargs):
    """Serialize requests enough to stay below Danbooru's rate limit."""
    global _next_request_time
    with _request_lock:
        wait_seconds = _next_request_time - time.monotonic()
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _next_request_time = time.monotonic() + max(0.0, REQUEST_INTERVAL_SECONDS)
    return get_session().get(url, **kwargs)

cache_tags = TagCategoryCache(CACHE_TAG_DB)


def initialize_wiki_cache():
    ensure_runtime_cache(CACHE_WIKI_DB)
    with sqlite3.connect(CACHE_WIKI_DB) as database:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute(
            "CREATE TABLE IF NOT EXISTS wiki_cache ("
            "tag TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )


def get_cached_wiki(tag):
    tag = normalize_cache_key(tag)
    with sqlite3.connect(CACHE_WIKI_DB) as database:
        row = database.execute(
            "SELECT body FROM wiki_cache WHERE tag = ?", (tag,)
        ).fetchone()
    return None if row is None else row[0]


def cache_wiki_result(tag, body):
    tag = normalize_cache_key(tag)
    with sqlite3.connect(CACHE_WIKI_DB) as database:
        database.execute(
            "INSERT OR REPLACE INTO wiki_cache(tag, body) VALUES (?, ?)",
            (tag, body),
        )


def wiki_cache_contains(tag):
    tag = normalize_cache_key(tag)
    with sqlite3.connect(CACHE_WIKI_DB) as database:
        return database.execute(
            "SELECT 1 FROM wiki_cache WHERE tag = ?", (tag,)
        ).fetchone() is not None


initialize_wiki_cache()

# -------------------------------
# Tag extraction
# -------------------------------
def load_tags_from_folder(folder):
    tags = set()
    for filename in os.listdir(folder):
        if filename.endswith(".txt"):
            with open(os.path.join(folder, filename), "r", encoding="utf-8") as f:
                for line in f:
                    tags.update(t.strip() for t in line.split(",") if t.strip())
    return sorted(tags)

# -------------------------------
# Cleaning
# -------------------------------
def clean_character_body(text):
    lower = text.lower()
    for marker in ("h4. costumes", "h4. appearance"):
        idx = lower.find(marker)
        if idx != -1:
            text = text[:idx]
            break

    text = re.sub(r'\[\[([^|\]]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'\[/?i\]', '', text)
    text = re.sub(r'^!.*$', '', text, flags=re.MULTILINE)

    return " ".join(text.split())

# -------------------------------
# API calls
# -------------------------------
def get_tag_category(tag):
    cached_category = cache_tags.get(tag, None)
    if cached_category is not None:
        return cached_category

    # Older versions permanently cached failed requests as None. Remove the
    # marker and retry instead.
    cache_tags.pop(tag, None)

    try:
        resp = api_get(
            API_TAG_URL,
            params={"search[name]": tag.replace(" ", "_")},
            timeout=REQUEST_TIMEOUT
        )
        if not resp.ok:
            logger.warning("Category lookup failed for %r: HTTP %s", tag, resp.status_code)
            return None

        data = resp.json()
        if not data or "category" not in data[0]:
            logger.warning("Category lookup returned no usable result for %r", tag)
            # This is a valid negative lookup, not a transient request error.
            # Cache it so malformed/non-Danbooru tags are not queried again.
            cache_tags[tag] = 0
            return None

        category = data[0]["category"]
        cache_tags[tag] = category
        return category
    except Exception as error:
        logger.warning("Category lookup failed for %r: %s", tag, error)
        return None

def fetch_character_wiki(tag):
    cached_body = get_cached_wiki(tag)
    if cached_body is not None:
        return cached_body

    try:
        resp = api_get(
            API_WIKI_URL,
            params={"search[title]": tag.replace(" ", "_")},
            timeout=REQUEST_TIMEOUT
        )
        if not resp.ok:
            logger.warning("Wiki lookup failed for %r: HTTP %s", tag, resp.status_code)
            return None

        data = resp.json()
        body = ""
        if data and data[0].get("body"):
            body = clean_character_body(data[0]["body"])
    except Exception as error:
        logger.warning("Wiki lookup failed for %r: %s", tag, error)
        return None

    # Cache empty wiki pages too; otherwise every run repeats the same lookup.
    # Cache empty wiki pages too; otherwise every run repeats the same lookup.
    cache_wiki_result(tag, body)
    return body

# -------------------------------
# Worker
# -------------------------------
def process_tag(tag):
    if get_tag_category(tag) != 4:
        return None
    body = fetch_character_wiki(tag)
    if body is None:
        return None
    return tag, body

# -------------------------------
# Main
# -------------------------------
def main():
    all_tags = load_tags_from_folder(INPUT_FOLDER)
    # Avoid submitting work that the caches can already answer. Unknown tags
    # still go through the normal category lookup and wiki-fetch flow.
    tags = [
        tag
        for tag in all_tags
        if cache_tags.get(tag, 4) == 4
        and not wiki_cache_contains(tag)
    ]

    print(f"{len(tags)} tags to process...")

    with ThreadPoolExecutor(
        max_workers=max(1, MAX_WORKERS),
        thread_name_prefix="danbooru",
    ) as executor:
        futures = {executor.submit(process_tag, tag): tag for tag in tags}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Fetching"):
            future.result()

    print("Done!\n")

if __name__ == "__main__":
    main()
