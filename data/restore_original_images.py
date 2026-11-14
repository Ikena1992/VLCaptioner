"""Upgrade undersized MD5-named WebP images; preview unless --apply is given.

Run while captioning is stopped. Sidecars and existing cache entries are retained.
Upgraded images are published as WebP (maximum 3000 pixels), so later pipeline
conversion leaves their bytes and caption cache keys unchanged.
"""
import argparse
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import requests
from PIL import Image, ImageOps

from booru_downloader import MD5, failure_reason
from danbooru_client import danbooru_get
from gelbooru_client import gelbooru_get, records
from file_io import atomic_path, file_hash
from runtime_config import PROJECT_DIR
from cache_seed import ensure_runtime_cache
from webp_conversion import save_webp


class NoUpgrade(Exception):
    """A valid original would not improve the local dimensions."""


def copy_caption_cache(old, new, cache_folder):
    """Add aliases without deleting old entries or overwriting newer captions."""
    for name, table in (('toriiOutput.sqlite3', 'torii_outputs'),
                        ('captionResultsCache.sqlite3', 'captions')):
        path = ensure_runtime_cache(cache_folder / name)
        if not path.is_file():
            continue
        with closing(sqlite3.connect(path)) as db, db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if table not in tables:
                raise RuntimeError(f'Expected caption table missing in {name}; image retained')
            if 'torii_outputs' in tables:
                row = db.execute('SELECT output FROM torii_outputs WHERE image_hash=?', (old,)).fetchone()
                if row and row[0].strip():
                    db.execute(
                        "INSERT INTO torii_outputs(image_hash, output) VALUES (?, ?) "
                        "ON CONFLICT(image_hash) DO UPDATE SET output=excluded.output "
                        "WHERE trim(torii_outputs.output, char(9)||char(10)||char(13)||' ')=''",
                        (new, row[0]))
            if 'captions' in tables:
                for kind, model, prompt, caption in db.execute(
                        'SELECT caption_kind, model, prompt, caption FROM captions WHERE image_hash=?', (old,)).fetchall():
                    if not caption.strip():
                        continue
                    digest = hashlib.sha256(bytes.fromhex(new))
                    digest.update(f'\0{kind}\0{model}\0{prompt}'.encode('utf-8'))
                    db.execute(
                        'INSERT INTO captions(cache_key, image_hash, caption_kind, model, prompt, caption) VALUES (?, ?, ?, ?, ?, ?) '
                        'ON CONFLICT(cache_key) DO UPDATE SET caption=excluded.caption '
                        "WHERE trim(captions.caption, char(9)||char(10)||char(13)||' ')=''",
                        (digest.hexdigest(), new, kind, model, prompt, caption))


def lookup(md5, sources):
    failures = []
    for source in sources:
        try:
            if source == 'danbooru':
                with danbooru_get('https://danbooru.donmai.us/posts.json',
                                  params={'tags': f'md5:{md5}', 'limit': 1}, timeout=30) as response:
                    posts = response.json()
                if not isinstance(posts, list) or any(not isinstance(p, dict) for p in posts):
                    raise ValueError('Invalid post response')
            else:
                posts = records(gelbooru_get('post', tags=f'md5:{md5}', limit=1), 'post')
        except Exception:
            failures.append(source)
            print(f'  {source} lookup failed; trying remaining sources.')
            continue
        for post in posts:
            if str(post.get('md5', '')).lower() == md5 and post.get('file_url'):
                return {**post, '_download_source': source}
    if failures:
        raise RuntimeError('Source lookup failed')
    return None


def download_original(post, expected, sources, temporary):
    """Try the other site if the first site's original URL is broken."""
    remaining = [source for source in sources if source != post.get('_download_source', sources[0])]
    for attempt, source in enumerate([None, *remaining]):
        try:
            if attempt:
                post = lookup(expected, [source])
                if not post:
                    continue
            origin = post.get('_download_source', sources[0])
            with requests.get(post['file_url'], stream=True, timeout=(10, 30), headers={
                    'User-Agent': 'VLCaptioner/1.0',
                    'Referer': 'https://gelbooru.com/' if origin == 'gelbooru'
                    else 'https://danbooru.donmai.us/',
            }) as response:
                response.raise_for_status()
                with temporary.open('wb') as output:
                    for chunk in response.iter_content(1024 * 1024):
                        output.write(chunk)
            if file_hash(temporary, 'md5') != expected:
                raise ValueError('Original MD5 mismatch')
            with Image.open(temporary) as image:
                image.verify()
            return
        except (requests.RequestException, OSError, ValueError, RuntimeError):
            print('  Original download failed verification or access; trying remaining sources.')
    raise RuntimeError('No verified original could be downloaded')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', nargs='?', type=Path, default=PROJECT_DIR / 'done')
    parser.add_argument('--apply', action='store_true', help='Download and replace files (default: preview)')
    parser.add_argument('--source', choices=['both', 'danbooru', 'gelbooru'], default='both')
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error('Dataset folder does not exist')
    sources = ['danbooru', 'gelbooru'] if args.source == 'both' else [args.source]
    errors = 0
    for path in list(args.folder.rglob('*')):
        if not path.is_file() or path.suffix.lower() != '.webp' or not MD5.fullmatch(path.stem):
            continue
        try:
            target = path
            if path.is_symlink():
                raise ValueError('Symlink; refusing to replace')
            expected = path.stem.lower()
            if file_hash(path, 'md5') == expected:
                continue
            print(f'Check original: {path}')
            post = lookup(expected, sources)
            if not post:
                print('  Original unavailable on selected sources.')
                continue
            old = file_hash(path)
            with Image.open(path) as image:
                if getattr(image, 'is_animated', False):
                    print('  Animated local image skipped to preserve frames.')
                    continue
                image = ImageOps.exif_transpose(image)
                width, height = image.size
            try:
                remote_width = int(post.get('image_width') or post.get('width') or 0)
                remote_height = int(post.get('image_height') or post.get('height') or 0)
            except (TypeError, ValueError, OverflowError):
                # Optional metadata is only an optimization. Verify dimensions
                # from the downloaded original if a site's fields are malformed.
                remote_width = remote_height = 0
            if remote_width > 0 and remote_height > 0:
                scale = min(1, 3000 / max(remote_width, remote_height))
                # Metadata can describe dimensions before EXIF rotation. Compare
                # long/short edges here; decoded oriented dimensions win below.
                remote_edges = sorted((remote_width * scale, remote_height * scale))
                local_edges = sorted((width, height))
                if all(remote <= local for remote, local in zip(remote_edges, local_edges)):
                    print('  Already large enough after conversion; skipped download.')
                    continue
            if not args.apply:
                print('  Would download and check for a larger converted image.')
                continue
            with atomic_path(target) as temporary:
                download_original(post, expected, sources, temporary)
                source_size = temporary.stat().st_size
                with Image.open(temporary) as image:
                    source_extension = '.jpg' if image.format == 'JPEG' else ''
                    if getattr(image, 'is_animated', False):
                        raise NoUpgrade('Animated original skipped to preserve frames')
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((3000, 3000), Image.Resampling.LANCZOS)
                    if image.width < width or image.height < height or image.size == (width, height):
                        raise NoUpgrade('Original is not larger after conversion; keeping local image')
                    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                        rgba = image.convert('RGBA')
                        background = Image.new('RGBA', rgba.size, 'white')
                        background.alpha_composite(rgba)
                        image = background.convert('RGB')
                    else:
                        image = image.convert('RGB')
                    # Encode before hashing: caption caches refer to the final bytes.
                    save_webp(image, temporary, source_extension, source_size)
                with Image.open(temporary) as image:
                    if image.format != 'WEBP':
                        raise ValueError('Expected WebP output')
                    image.verify()
                if file_hash(path) != old:
                    raise RuntimeError('Dataset image changed during download; stop captioning first')
                copy_caption_cache(old, file_hash(temporary), PROJECT_DIR / 'data' / 'caches')
                if file_hash(path) != old:
                    raise RuntimeError('Dataset image changed during cache migration')
            print('  Replaced; caption caches preserved.')
        except NoUpgrade as exc:
            print(f'  {exc}')
        except Exception as exc:
            errors += 1
            print(f'  Failed; original retained: {failure_reason(exc)}')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
