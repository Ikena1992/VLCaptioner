#!/usr/bin/env python3
"""Download Danbooru images whose MD5 values occur in data/parquet."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from tqdm import tqdm


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
IMAGES_DIR = ROOT / "images"
DONE_DIR = ROOT / "done"
PARQUET_DIR = DATA_DIR / "parquet"
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
}

sys.path.insert(0, str(DATA_DIR))

from danbooru_client import danbooru_get, get_danbooru_headers  # noqa: E402


def load_md5s(parquet_dir: Path) -> list[str]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError(
            "Parquet image downloads require pyarrow. Run install.bat first."
        ) from error

    md5s: set[str] = set()
    for parquet_path in sorted(parquet_dir.glob("*.parquet")):
        parquet = pq.ParquetFile(parquet_path)
        if "md5" not in parquet.schema_arrow.names:
            tqdm.write(f"Skipping {parquet_path.name}: missing md5 column")
            continue
        for batch in parquet.iter_batches(columns=["md5"]):
            for value in batch.column(0).to_pylist():
                if isinstance(value, str):
                    md5 = value.strip().lower()
                    if len(md5) == 32 and all(c in "0123456789abcdef" for c in md5):
                        md5s.add(md5)
    return sorted(md5s)


def existing_image_stems(*folders: Path) -> set[str]:
    return {
        path.stem.casefold()
        for folder in folders
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    }


def find_post(md5: str) -> dict | None:
    response = danbooru_get(
        "https://danbooru.donmai.us/posts.json",
        params={"tags": f"md5:{md5}", "limit": 1},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    posts = payload if isinstance(payload, list) else [payload]
    for post in posts:
        if isinstance(post, dict) and str(post.get("md5", "")).lower() == md5:
            return post
    return None


def image_extension(post: dict, url: str) -> str | None:
    file_ext = str(post.get("file_ext", "")).strip().lower().lstrip(".")
    suffix = f".{file_ext}" if file_ext else Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in IMAGE_EXTENSIONS else None


def download_image(md5: str, post: dict, destination: Path) -> Path:
    url = post.get("file_url") or post.get("large_file_url")
    if not isinstance(url, str) or not url.strip():
        raise RuntimeError("Danbooru did not provide an image URL")
    extension = image_extension(post, url)
    if extension is None:
        raise RuntimeError(f"unsupported image type: {post.get('file_ext', 'unknown')}")

    output_path = destination / f"{md5}{extension}"
    temporary_path = destination / f".{md5}{extension}.download"
    try:
        with requests.get(
            url,
            headers={"User-Agent": get_danbooru_headers()["User-Agent"]},
            stream=True,
            timeout=(10, 180),
        ) as response:
            response.raise_for_status()
            with temporary_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
        if not temporary_path.stat().st_size:
            raise RuntimeError("downloaded file is empty")
        temporary_path.replace(output_path)
        return output_path
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> int:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    md5s = load_md5s(PARQUET_DIR)
    if not md5s:
        print(f"No MD5 values found in {PARQUET_DIR}")
        return 0

    downloaded = 0
    skipped = 0
    missing = 0
    failed = 0
    existing = existing_image_stems(IMAGES_DIR, DONE_DIR)
    progress = tqdm(md5s, desc="Downloading Parquet images", unit="image", dynamic_ncols=True)
    for md5 in progress:
        if md5 in existing:
            skipped += 1
            continue
        try:
            post = find_post(md5)
            if post is None:
                missing += 1
                continue
            download_image(md5, post, IMAGES_DIR)
            existing.add(md5)
            downloaded += 1
        except Exception as error:
            failed += 1
            tqdm.write(f"FAILED {md5}: {error}")
        progress.set_postfix(
            downloaded=downloaded,
            skipped=skipped,
            missing=missing,
            failed=failed,
            refresh=False,
        )

    print(
        f"Downloaded: {downloaded}; already present: {skipped}; "
        f"not found: {missing}; failed: {failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
