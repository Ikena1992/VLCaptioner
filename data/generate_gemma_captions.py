"""Generate final long and short captions with the configured Ollama model."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import random
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

from ollama_client import OllamaClient
from cache_seed import ensure_runtime_cache
from parquet_captions import load_parquet_captions
from runtime_config import load_positive_int, load_setting
from tqdm import tqdm

import create_clean_tag_files as tag_files
import finalize_caption_dataset as finalizer

SCRIPT_DIR = Path(__file__).parent.resolve()
IMAGES_DIR = (SCRIPT_DIR / ".." / "images").resolve()
CAPTION_CACHE = SCRIPT_DIR / "caches" / "captionResultsCache.sqlite3"
CAPTION_MAX_NEW_TOKENS = {"short": 384, "long": 1536}
DEFAULT_CAPTION_CONTEXT_SIZE = 65536
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def is_context_size_error(error):
    return "exceeds the available context size" in str(error).casefold()


def is_token_repeat_error(error):
    return "token repeat limit reached" in str(error).casefold()


def load_prompt_builder():
    module_path = SCRIPT_DIR / "build_gemma_prompts.py"
    spec = importlib.util.spec_from_file_location("gemma_prompt_builder", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load prompt builder: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def initialize_cache(cache_path=CAPTION_CACHE):
    cache_path = ensure_runtime_cache(cache_path)
    database = sqlite3.connect(cache_path)
    try:
        database.execute(
            "CREATE TABLE IF NOT EXISTS captions (cache_key TEXT PRIMARY KEY, "
            "image_hash TEXT NOT NULL, caption_kind TEXT NOT NULL, model TEXT NOT NULL, "
            "prompt TEXT NOT NULL, caption TEXT NOT NULL)"
        )
        database.commit()
    finally:
        database.close()


def file_hash(image_path):
    digest = hashlib.sha256()
    with image_path.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def caption_cache_key(image_path, caption_kind, model, prompt):
    digest = hashlib.sha256()
    digest.update(bytes.fromhex(file_hash(image_path)))
    digest.update(f"\0{caption_kind}\0{model}\0{prompt}".encode("utf-8"))
    return digest.hexdigest()


def get_cached_caption(image_path, caption_kind, model, prompt, cache_path=CAPTION_CACHE):
    database = sqlite3.connect(cache_path)
    try:
        row = database.execute(
            "SELECT caption FROM captions WHERE cache_key = ?",
            (caption_cache_key(image_path, caption_kind, model, prompt),),
        ).fetchone()
    finally:
        database.close()
    return row[0] if row else None


def save_cached_caption(image_path, caption_kind, model, prompt, caption, cache_path=CAPTION_CACHE):
    image_digest = file_hash(image_path)
    database = sqlite3.connect(cache_path)
    try:
        database.execute(
            "INSERT OR REPLACE INTO captions(cache_key, image_hash, caption_kind, model, prompt, caption) VALUES (?, ?, ?, ?, ?, ?)",
            (caption_cache_key(image_path, caption_kind, model, prompt), image_digest, caption_kind, model, prompt, caption),
        )
        database.commit()
    finally:
        database.close()


def normalize_caption_output(text):
    text = unicodedata.normalize("NFC", text.strip())
    text = text.split("END_CAPTION", 1)[0].strip()
    text = re.sub(r"^\s*</?format>\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"(?im)^\s*#+\s*(?:[12]\.\s*)?(?:Short|Long|Detailed) (?:description|caption)\s*:?\s*$",
        "", text,
    )
    text = re.sub(r"(?m)^\s*>\s*", "", text)
    return re.sub(r"\s+", " ", text).strip().strip('"').strip()


def load_artist_names(image_path):
    """Read the comma-separated artist tags from the image's sidecar CSV."""
    csv_file = image_path.with_suffix(".csv")
    if not csv_file.exists():
        return []

    with csv_file.open(newline="", encoding="utf-8-sig") as file:
        row = next(csv.DictReader(file), None)
    if not row:
        return []

    raw_artists = row.get("artists") or row.get("artist") or ""
    return [artist.strip() for artist in raw_artists.split(",") if artist.strip()]


def add_artist_prefix(caption, artist_names):
    """Replace leading artist attribution with one canonical CSV-derived prefix."""
    formatted_artists = [
        artist if artist.startswith("@") else f"@{artist}"
        for artist in artist_names
        if artist.strip()
    ]
    if not formatted_artists:
        return caption

    caption = re.sub(
        r"^(?:\s*Drawn\s+by\s+[^.\r\n]+\.\s*)+",
        "",
        caption,
        flags=re.IGNORECASE,
    )
    prefix = f"Drawn by {', '.join(formatted_artists)}. "
    return prefix + caption


def write_result(image_path, short_text, long_text, artist_names=None):
    """Write both results; the SQLite request cache is managed separately."""
    short_text = normalize_caption_output(short_text)
    long_text = normalize_caption_output(long_text)
    if not short_text or not long_text:
        raise RuntimeError("One or both generated captions were empty")
    artist_names = artist_names or []
    short_text = add_artist_prefix(short_text, artist_names)
    long_text = add_artist_prefix(long_text, artist_names)
    image_path.with_suffix(".short").write_text(short_text + "\n", encoding="utf-8")
    image_path.with_suffix(".long").write_text(long_text + "\n", encoding="utf-8")


def discover_images(prompt_builder, parquet_captions, overwrite_caption_cache=False):
    explanations = prompt_builder.load_character_explanations(prompt_builder.CHARACTER_DB)
    fallback = prompt_builder.load_wiki_explanations(prompt_builder.WIKI_CACHE_DB)
    prompts = {}
    parquet_cache_prompts = {}
    skipped = {"missing_csv": [], "no_prompt": [], "complete": []}
    images = sorted(
        IMAGES_DIR.glob("*.*"),
        key=lambda image: (
            image.stem.lower() not in parquet_captions,
            image.name.lower(),
        ),
    )
    for image in images:
        if image.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if not image.with_suffix(".csv").exists():
            skipped["missing_csv"].append(image.name)
            continue
        short_path, long_path = image.with_suffix(".short"), image.with_suffix(".long")
        complete = (
            not overwrite_caption_cache
            and all(path.exists() and path.read_text(encoding="utf-8-sig").strip() for path in (short_path, long_path))
        )
        torii_path = image.with_suffix(".toriiOutput")
        uses_parquet = image.stem.lower() in parquet_captions
        torii_output = (
            ""
            if uses_parquet
            else torii_path.read_text(encoding="utf-8-sig")
            if torii_path.exists()
            else ""
        )
        built = prompt_builder.build_gemma_prompts(image, explanations, fallback, torii_output)
        if built:
            if uses_parquet:
                parquet_cache_prompts[image] = built["long"]
            if not complete:
                prompts[image] = built
        elif not complete:
            skipped["no_prompt"].append(image.name)
        if complete:
            skipped["complete"].append(image.name)
    return prompts, skipped, parquet_cache_prompts


def cache_parquet_long_captions(
    parquet_captions,
    cache_prompts,
    model,
    overwrite=False,
    cache_path=CAPTION_CACHE,
):
    """Archive matching Parquet captions under the normal long-caption keys."""
    cached = 0
    for image_path, prompt in cache_prompts.items():
        if not overwrite and get_cached_caption(
            image_path, "long", model, prompt, cache_path
        ):
            continue
        save_cached_caption(
            image_path,
            "long",
            model,
            prompt,
            parquet_captions[image_path.stem.lower()],
            cache_path,
        )
        cached += 1
    return cached


def generate_caption(
    client,
    image_path,
    kind,
    model,
    prompts,
    long_caption=None,
    overwrite_caption_cache=False,
    cache_path=CAPTION_CACHE,
    context_size=DEFAULT_CAPTION_CONTEXT_SIZE,
):
    prompt = prompts[image_path][kind]
    if kind == "short" and long_caption:
        prompt += (
            "\n\n# Authoritative long caption to summarize\n<long_caption>\n"
            f"{normalize_caption_output(long_caption)}\n</long_caption>\n"
            "Write a faithful 40-70 word summary, never exceeding 85 words unless "
            "required names or visible text make that impossible. Preserve central "
            "subjects, actions, relationships, explicit concepts, and composition."
        )
    cached = None
    if not overwrite_caption_cache:
        cached = get_cached_caption(image_path, kind, model, prompt, cache_path)
    if cached:
        return cached
    options = {
        "temperature": 0.0,
        "num_predict": CAPTION_MAX_NEW_TOKENS[kind],
        "num_ctx": context_size,
        "repeat_last_n": 256,
        "repeat_penalty": 1.15,
        "stop": ["END_CAPTION"],
    }
    try:
        text, _ = client.chat(model, prompt, image_path, options)
    except RuntimeError as error:
        if not is_token_repeat_error(error):
            raise
        tqdm.write(f"Retrying {image_path.name} ({kind}): token repeat limit reached")
        # Change greedy decoding so the retry can escape the same repetition loop.
        text, _ = client.chat(model, prompt, image_path, {
            **options, "temperature": 0.3, "repeat_penalty": 1.2,
        })
    save_cached_caption(image_path, kind, model, prompt, text, cache_path)
    return text


def process_image(
    client,
    image_path,
    model,
    prompts,
    parquet_captions,
    overwrite_caption_cache=False,
    context_size=DEFAULT_CAPTION_CONTEXT_SIZE,
):
    long_path = image_path.with_suffix(".long")
    long_text = (
        ""
        if overwrite_caption_cache
        else long_path.read_text(encoding="utf-8-sig").strip() if long_path.exists() else ""
    )
    if not long_text:
        image_path.with_suffix(".short").unlink(missing_ok=True)
        long_text = parquet_captions.get(image_path.stem.lower(), "")
    if not long_text:
        long_text = generate_caption(
            client,
            image_path,
            "long",
            model,
            prompts,
            overwrite_caption_cache=overwrite_caption_cache,
            context_size=context_size,
        )
    long_text = normalize_caption_output(long_text)
    long_path.write_text(long_text + "\n", encoding="utf-8")
    short_text = generate_caption(
        client,
        image_path,
        "short",
        model,
        prompts,
        long_text,
        overwrite_caption_cache=overwrite_caption_cache,
        context_size=context_size,
    )
    write_result(image_path, short_text, long_text, load_artist_names(image_path))


def build_image_finalizer():
    """Return a per-image finisher that reuses the tagger's run-level RNG state."""
    tag_rng = random.Random(tag_files.RANDOM_SEED)
    dropout_rng = random.Random(tag_files.DROPOUT_SEED)
    dropout_protected_tags = {
        tag_files.normalize_clean_tag(tag)
        for tag in tag_files.JAVA_100_DROPOUT_PROTECT_TAGS
    }
    dropout_protected_tags.update(
        tag_files.normalize_clean_tag(tag)
        for tag in tag_files.EXTRA_DROPOUT_PROTECT_TAGS
    )
    dropout_protected_tags.discard("")

    def finish(image_path):
        csv_path = image_path.with_suffix(".csv")
        if not tag_files.process_csv(
            csv_path,
            tag_rng,
            dropout_rng,
            dropout_protected_tags,
            report_success=False,
        ):
            raise RuntimeError(f"Could not create tag file for {image_path.name}")
        if not finalizer.process_caption_pair(
            image_path.with_suffix(".short"), report_success=False
        ):
            raise RuntimeError(f"Could not finalize {image_path.name}")

    return finish


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--overwrite-caption-cache",
        action="store_true",
        help="Regenerate and replace cached short and long captions.",
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    url, config = load_setting("OLLAMA_URL")
    configured_model, _ = load_setting("OLLAMA_MODEL")
    timeout, _ = load_positive_int("OLLAMA_TIMEOUT_SECONDS", "300")
    context_size, _ = load_positive_int(
        "OLLAMA_CONTEXT_SIZE", str(DEFAULT_CAPTION_CONTEXT_SIZE)
    )
    client = OllamaClient(url, timeout)
    model = client.running_model() if configured_model.lower() == "auto" else configured_model
    if configured_model.lower() != "auto":
        client.ensure_model(model, tqdm.write)
    print(f"Ollama model: {model} ({url})\nBackend config: {config}")
    initialize_cache()
    finalizer.recover_publications()
    for journal in finalizer.DONE_DIR.glob(".finalize-*/committed.json"):
        finalizer.finish_cleanup(journal.parent)
    finish_image = build_image_finalizer()
    local_stems = {
        image.stem.lower()
        for image in IMAGES_DIR.glob("*.*")
        if image.suffix.lower() in SUPPORTED_EXTENSIONS
    }
    parquet = load_parquet_captions(SCRIPT_DIR / "parquet", local_stems)
    prompts, skipped, parquet_cache_prompts = discover_images(
        load_prompt_builder(), parquet, args.overwrite_caption_cache
    )
    cached_parquet = cache_parquet_long_captions(
        parquet,
        parquet_cache_prompts,
        model,
        overwrite=args.overwrite_caption_cache,
    )
    print(f"Cached {cached_parquet} matching Parquet long caption(s).")
    completed_images = [IMAGES_DIR / name for name in skipped["complete"]]
    if not prompts and not completed_images:
        print("No images to process.")
        print(f"Skipped without CSV metadata: {len(skipped['missing_csv'])}")
        print(f"Skipped without usable prompt: {len(skipped['no_prompt'])}")
        print(f"Skipped with existing result/cache: {len(skipped['complete'])}")
        return 0
    for image_path in tqdm(
        completed_images,
        desc="Finalizing completed captions",
        unit="image",
        dynamic_ncols=True,
    ):
        finish_image(image_path)
    failed_context = 0
    failed_repeat = 0
    progress = tqdm(prompts, desc="Refinement (Ollama)", unit="image", dynamic_ncols=True, smoothing=0.1)
    for index, image_path in enumerate(progress, start=1):
        started = time.monotonic()
        try:
            process_image(
                client,
                image_path,
                model,
                prompts,
                parquet,
                args.overwrite_caption_cache,
                context_size,
            )
            finish_image(image_path)
        except RuntimeError as error:
            if is_token_repeat_error(error):
                failed_repeat += 1
                tqdm.write(f"Skipping {image_path.name}: repetition persisted after retry ({error})")
            elif is_context_size_error(error):
                failed_context += 1
                tqdm.write(
                    f"Skipping {image_path.name}: request exceeds "
                    f"OLLAMA_CONTEXT_SIZE={context_size} ({error})"
                )
            else:
                raise
        status = (
            f"request {index}/{len(prompts)}, "
            f"{time.monotonic() - started:.1f}s/image"
        )
        if failed_context:
            status += f", context-skipped {failed_context}"
        if failed_repeat:
            status += f", repetition-skipped {failed_repeat}"
        progress.set_postfix_str(status, refresh=False)
    if failed_context or failed_repeat:
        raise SystemExit(
            f"Refinement completed with {failed_context} context-size failure(s) "
            f"and {failed_repeat} token-repetition failure(s). "
            "Retry the pipeline to process skipped images. For context-size "
            "failures, increase OLLAMA_CONTEXT_SIZE first."
        )
    print("\nDone!\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
