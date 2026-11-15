"""Original-image downloading, independent of the GUI and captioning pipeline."""
from file_io import publish_file
from dataset_files import IMAGE_FILE_TYPES as IMAGE_EXTENSIONS
from dataclasses import dataclass
import hashlib
from html import unescape
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.parse import urlparse

import requests

from danbooru_client import danbooru_get
from gelbooru_client import gelbooru_get, records
from gelbooru_client import post_to_tags as gelbooru_post_to_tags
from refresh_tags_from_danbooru import post_to_tags as danbooru_post_to_tags
from fetch_danbooru_tags import save_tags

MD5 = re.compile(r"^[0-9a-f]{32}$", re.I)
SCORE_FILTER = re.compile(r'score:(?:>=|<=|>|<|=)?-?\d+', re.I)
ALWAYS_BLACKLISTED = frozenset({'animated'})


def local_scan_status(message):
    """Label local counting separately from API progress without folder paths."""
    if message.startswith('Scanning '):
        return 'Local dataset: counting image tags…'
    return 'Local dataset: ' + message


RATINGS = {'g': 'g', 'general': 'g', 's': 's', 'sensitive': 's', 'safe': 's',
           'q': 'q', 'questionable': 'q', 'e': 'e', 'explicit': 'e'}


def query_rating(query):
    tokens = [token.lower()[7:] for token in query.split() if token.lower().startswith('rating:')]
    if len(tokens) > 1 or (tokens and tokens[0] not in RATINGS):
        raise ValueError('Use one rating:general, rating:sensitive, rating:questionable or rating:explicit filter.')
    return RATINGS[tokens[0]] if tokens else None


def dataset_snapshot(dataset):
    """Count tag/rating intersections, deduplicating MD5s across folders."""
    memberships = {}
    for tag, paths in dataset.matches.items():
        memberships[tag] = {('md5', p.stem.casefold()) if MD5.fullmatch(p.stem)
                            else ('path', os.path.normcase(str(p.resolve()))) for p in paths}
    rating_ids = {'g': set(memberships.get('safe', ())), 's': set(memberships.get('sensitive', ())),
                  'e': set(memberships.get('explicit', ()))}
    rating_ids['q'] = memberships.get('nsfw', set()) - rating_ids['e']
    # A duplicate MD5 may carry stale, conflicting safety tags in done1/.
    # Give the more restrictive rating precedence instead of filling two gaps.
    rating_ids['s'] -= rating_ids['e'] | rating_ids['q']
    rating_ids['g'] -= rating_ids['e'] | rating_ids['q'] | rating_ids['s']
    snapshot = {tag: len(ids) for tag, ids in memberships.items()}
    snapshot.update({(tag, rating): len(ids & rated) for tag, ids in memberships.items()
                     for rating, rated in rating_ids.items()})
    return snapshot


class DatasetCountError(RuntimeError):
    """Gap targets cannot be trusted until local dataset problems are fixed."""


@dataclass(frozen=True)
class DownloadJob:
    source: str
    tags: str
    limit: int = 100
    blacklist: frozenset[str] = frozenset()
    target_count: int | None = None


def parse_blacklist(text):
    """Whitespace-separated exact tags, matching the search field's syntax."""
    return frozenset(tag.casefold() for tag in text.split())


def gap_tag(source, query):
    """Extract one dataset tag, allowing score sorting and one score filter."""
    modifier = 'order:score' if source == 'Danbooru' else 'sort:score'
    tokens = [tag for tag in query.split() if tag.casefold() != modifier]
    query_rating(query)
    tokens = [tag for tag in tokens if not tag.lower().startswith('rating:')]
    scores = [tag for tag in tokens if SCORE_FILTER.fullmatch(tag)]
    tags = [tag for tag in tokens if not SCORE_FILTER.fullmatch(tag)]
    if len(scores) > 1 or len(tags) != 1 or tags[0].startswith('-') or tags[0].casefold().startswith(('order:', 'sort:', 'score:')):
        raise ValueError('Gap filling requires one positive tag, optionally sorted by score with one score filter')
    return tags[0]


def is_blacklisted(post, blacklist):
    return bool(blacklist_matches(post, blacklist))


def blacklist_matches(post, blacklist):
    tags = post.get("tag_string", post.get("tags", ""))
    return sorted((ALWAYS_BLACKLISTED | frozenset(blacklist)).intersection(unescape(str(tags)).casefold().split()))


def failure_reason(error):
    """Explain failures without printing exception URLs or credentials."""
    if isinstance(error, requests.Timeout):
        return "Request timed out; check connectivity or try again."
    if isinstance(error, requests.HTTPError):
        code = error.response.status_code if error.response is not None else "unknown"
        return f"HTTP {code}; check site access, credentials, or rate limits."
    if isinstance(error, requests.RequestException):
        return "Network request failed; check connectivity."
    if isinstance(error, OSError):
        return "Could not read or write files; check folder permissions and free disk space."
    if isinstance(error, ValueError):
        return "Site returned invalid tag or post data."
    if str(error) == "Original file failed MD5 verification":
        return "Original failed MD5 verification; discarded the partial file."
    return "Site request or tag processing failed; check credentials and API access."


def existing_hashes(*folders):
    return {p.stem.lower() for folder in folders if folder.exists()
            for p in folder.rglob("*") if p.is_file() and MD5.fullmatch(p.stem)
            and p.suffix.lower().lstrip(".") in IMAGE_EXTENSIONS}


class ImageIndex(set):
    """Reuse folder scans while still detecting images added during a run."""
    def __init__(self, images, done, done1=None):
        super().__init__()
        self.images, self.done = images, done
        self.done1 = done1 if done1 is not None else done.parent / "done1"
        self.locations = {}
        self.last_scan = float("-inf")
        self.refresh(force=True)

    def refresh(self, force=False):
        if not force and time.monotonic() - self.last_scan < 5:
            return
        for folder in (self.images, self.done, self.done1):
            for md5 in existing_hashes(folder):
                self.locations[md5] = "the local dataset" if folder == self.done1 else folder.name + "/"
                self.add(md5)
        self.last_scan = time.monotonic()


def publish_bundle(temporary, target, staging):
    """Commit prepared sidecars, publish the image last, and roll back on failure."""
    originals = {}
    changed = []
    try:
        for extension in ("txt", "csv"):
            destination = target.with_suffix("." + extension)
            prepared = staging / destination.name
            originals[destination] = destination.read_bytes() if destination.exists() else None
            # Preserve tag files edited while the download was in progress.
            snapshot = staging / (destination.name + ".original")
            previous = snapshot.read_bytes() if snapshot.exists() else None
            if originals[destination] != previous:
                continue
            publish_file(prepared, destination)
            changed.append(destination)
        publish_file(temporary, target, overwrite=False)
    except (OSError, ValueError):
        for destination in reversed(changed):
            original = originals[destination]
            if original is None:
                destination.unlink(missing_ok=True)
            else:
                restore = staging / (destination.name + ".restore")
                restore.write_bytes(original)
                publish_file(restore, destination)
        raise


def fetch_posts(job, page):
    if job.source == "Gelbooru":
        return records(gelbooru_get("post", tags=job.tags, limit=100, pid=page), "post")
    if job.source != "Danbooru":
        raise ValueError("Unknown image source")
    with danbooru_get("https://danbooru.donmai.us/posts.json",
                      params={"tags": job.tags, "limit": 100, "page": page + 1},
                      timeout=(10, 30)) as response:
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list) or any(not isinstance(p, dict) for p in payload):
        raise RuntimeError("Unexpected Danbooru response")
    return payload


def save_original(post, source, images, done, known, cancel, log=lambda message: None):
    if cancel.is_set():
        return "cancelled"
    if is_blacklisted(post, frozenset()):
        log('Skipped: animated posts are always blacklisted.')
        return 'blacklisted'
    md5 = str(post.get("md5", "")).lower()
    if not MD5.fullmatch(md5):
        log("Skipped: missing or invalid MD5 filename.")
        return "unavailable"
    if isinstance(known, ImageIndex):
        known.refresh()
        present = md5 in known
        location = known.locations.get(md5, "images/ or earlier in this queue")
    else:
        # Standalone callers without an index still receive a current folder check.
        done1 = done.parent / "done1"
        present = md5 in known or md5 in existing_hashes(images, done, done1)
        location = "the local dataset" if md5 in existing_hashes(done1) else "done/" if md5 in existing_hashes(done) else "images/ or earlier in this queue"
    if present:
        log(f"Skipped: matching MD5 image already in {location}.")
        known.add(md5)
        return "skipped"
    url = post.get("file_url")
    if not isinstance(url, str) or urlparse(url).scheme != "https":
        log("Skipped: original image URL unavailable or not HTTPS.")
        return "unavailable"
    extension = str(post.get("file_ext") or Path(urlparse(url).path).suffix.lstrip(".")).lower()
    if extension not in IMAGE_EXTENSIONS:
        log(f"Skipped: unsupported file type ({extension if extension.isalnum() else 'unknown'}); only images are downloaded.")
        return "unavailable"
    target = images / f"{md5}.{extension}"
    temporary = None
    stage = "original download"
    try:
        log(f"Downloading original to images/{target.name}…")
        # A separate, unauthenticated request keeps API credentials off CDN hosts.
        with requests.get(url, stream=True, timeout=(10, 30), headers={
            "User-Agent": "VLCaptioner/1.0",
            "Referer": "https://gelbooru.com/" if source == "Gelbooru" else "https://danbooru.donmai.us/",
        }) as response:
            response.raise_for_status()
            digest = hashlib.md5()
            with tempfile.NamedTemporaryFile(dir=images, suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                for chunk in response.iter_content(256 * 1024):
                    if cancel.is_set():
                        log("Cancelled: removing partial download.")
                        return "cancelled"
                    if chunk:
                        output.write(chunk)
                        digest.update(chunk)
        if cancel.is_set():
            log("Cancelled before saving the image.")
            return "cancelled"
        if digest.hexdigest() != md5:
            raise RuntimeError("Original file failed MD5 verification")
        if isinstance(known, ImageIndex):
            known.refresh(force=True)
            present = md5 in known
        else:
            present = md5 in existing_hashes(images, done, done.parent / "done1")
        if present:
            log("Skipped: matching image appeared in the local dataset during the download.")
            known.add(md5)
            return "skipped"
        stage = "tag files"
        log(f"Preparing captioner TXT and CSV tags for {md5}…")
        tags = (gelbooru_post_to_tags(post) if source == "Gelbooru"
                else danbooru_post_to_tags(post))
        with tempfile.TemporaryDirectory(dir=images, prefix=".download-tags-") as folder:
            staging = Path(folder)
            for extension in ("txt", "csv"):
                original = images / f"{md5}.{extension}"
                if original.exists():
                    snapshot = staging / (original.name + ".original")
                    shutil.copyfile(original, snapshot)
                    shutil.copyfile(snapshot, staging / original.name)
            save_tags(md5, tags, image_folder=staging)
            if cancel.is_set():
                log("Cancelled before saving; discarded prepared image and tag files.")
                return "cancelled"
            stage = "saving the image and tags"
            try:
                publish_bundle(temporary, target, staging)
            except FileExistsError:
                log("Skipped: destination image already exists; preserved the existing file.")
                known.add(md5)
                return "skipped"
        known.add(md5)
        log(f"Saved {target}; TXT/CSV tags ready (existing nonempty TXT preserved).")
        return "downloaded"
    except (requests.RequestException, OSError, RuntimeError, ValueError) as error:
        log(f"Failed during {stage}: {failure_reason(error)}")
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_job(job, images, done, known, cancel, report, log=lambda message: None,
            on_download=None, destination=None, gap_progress=None):
    if job.source not in ("Danbooru", "Gelbooru") or (job.target_count is None and job.limit < 1) or not job.tags.strip():
        raise ValueError("Tags and a positive post limit are required")
    images.mkdir(parents=True, exist_ok=True)
    counts = dict(downloaded=0, skipped=0, blacklisted=0, unavailable=0, failed=0)
    requested_rating = query_rating(job.tags)
    gap = None
    progress = None
    if job.target_count is not None or gap_progress is not None:
        from dataset_manager import scan_dataset, normalize
        gap_progress = {} if gap_progress is None else gap_progress
        snapshot_key = ('dataset_counts', str(done.resolve()))
        downloaded_key = ('downloaded_counts', str(done.resolve()))
        if snapshot_key not in gap_progress:
            folders = [folder for folder in (images, done, done.parent / 'done1') if folder.exists()]
            log('Counting tags in images/ and done/ once for this download run...')
            dataset = scan_dataset(folders, progress=lambda text: log(local_scan_status(text)),
                                   cancelled=cancel.is_set, tag_fallback=True)
            if dataset is None:
                return counts
            if dataset.warnings:
                raise DatasetCountError("Could not reliably count images/ and done/. Check unreadable tag files and folder permissions.")
            if dataset.missing or dataset.empty:
                log(f'{dataset.missing + dataset.empty:,} images have no usable tags and cannot be matched to targets.')
            snapshot = dataset_snapshot(dataset)
            gap_progress[snapshot_key] = snapshot
            gap_progress[downloaded_key] = {}
        progress = gap_progress[downloaded_key]
    if job.target_count is not None:
        tag = gap_tag(job.source, job.tags)
        if job.target_count < 1:
            raise ValueError("Gap filling requires one positive tag and a positive dataset target")
        rating = query_rating(job.tags)
        key = (normalize(tag), rating) if rating else normalize(tag)
        current = gap_progress[snapshot_key].get(key, 0)
        downloaded_earlier = progress.get(key, 0)
        gap = max(0, job.target_count - current - downloaded_earlier)
        log(f"Fill gaps: {current} unique matching images initially in images/ and done/, {downloaded_earlier} downloaded earlier; target {job.target_count}; need {gap} new images.")
        if not gap:
            return counts
    examined = 0
    page = 0
    seen = set()
    def reached_limit():
        return counts['downloaded'] >= gap if gap is not None else examined >= job.limit

    while not reached_limit() and not cancel.is_set():
        if isinstance(known, ImageIndex):
            known.refresh(force=True)
        log(f"Searching {job.source}, page {page + 1}: {job.tags}")
        posts = fetch_posts(job, page)
        if not posts:
            log("No more matching posts returned by the site.")
            break
        fresh = False
        for post in posts:
            if cancel.is_set() or reached_limit():
                break
            identity = str(post.get("id") or post.get("md5") or repr(post))
            if identity in seen:
                continue
            seen.add(identity)
            fresh = True
            examined += 1
            label = f"{job.source} post {post.get('id', 'unknown')} [{examined} checked]" if gap is not None else f"{job.source} post {post.get('id', 'unknown')} [{examined}/{job.limit}]"
            log(f"Checking {label}")
            post_log = lambda message: log(f"{label}: {message}")
            try:
                matches = blacklist_matches(post, job.blacklist)
                actual_rating = RATINGS.get(str(post.get('rating', '')).lower())
                if requested_rating and actual_rating != requested_rating:
                    post_log('Skipped: post rating does not match the requested rating.')
                    result = 'unavailable'
                elif matches:
                    post_log("Skipped: blacklist matched " + ", ".join(matches))
                    result = "blacklisted"
                else:
                    target_folder = destination() if destination else images
                    result = save_original(post, job.source, target_folder, done, known, cancel, post_log)
                if result == "cancelled":
                    break
                counts[result] += 1
                if result == 'downloaded' and progress is not None:
                    post_tags = str(post.get('tag_string', post.get('tags', '')))
                    downloaded_tags = {normalize(value) for value in unescape(post_tags).split()}
                    post_rating = RATINGS.get(str(post.get('rating', '')).lower())
                    if post_rating:
                        downloaded_tags.update({(tag, post_rating) for tag in list(downloaded_tags)})
                    if job.target_count is not None:
                        downloaded_tags.add(key)
                    for downloaded_tag in downloaded_tags:
                        progress[downloaded_tag] = progress.get(downloaded_tag, 0) + 1
            except (requests.RequestException, OSError, RuntimeError, ValueError):
                counts["failed"] += 1
                report(examined, counts.copy())
                continue
            # Submission failures must stop the row, not count a saved image as
            # both downloaded and failed or keep filling a broken batch.
            if result == "downloaded" and on_download:
                on_download(target_folder)
            report(examined, counts.copy())
        if not fresh:
            log("Stopped: site returned only posts already checked in this search.")
            break
        page += 1
    if cancel.is_set():
        log("Row stopped by user.")
    elif gap is not None:
        log(f"Gap downloads: {counts['downloaded']}/{gap}. Captioning must finish successfully to fill done/; rerun to fill any remaining gap.")
    elif examined >= job.limit:
        log(f"Post limit reached: {examined} posts checked (includes skipped and failed posts).")
    return counts
