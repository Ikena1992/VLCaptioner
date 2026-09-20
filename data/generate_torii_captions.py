"""Generate Torii structured notes and long captions through Ollama."""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
import time
import unicodedata
from contextlib import closing
from pathlib import Path

from ollama_client import OllamaClient
from cache_seed import ensure_runtime_cache
from parquet_captions import load_parquet_captions
from parquet_torii_outputs import load_parquet_torii_outputs
from runtime_config import load_positive_int, load_setting
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent.resolve()
IMAGES_DIR = (SCRIPT_DIR / ".." / "images").resolve()
TORII_OUTPUT_CACHE = SCRIPT_DIR / "caches" / "toriiOutput.sqlite3"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def load_prompt_builder():
    module_path = SCRIPT_DIR / "build_torii_prompts.py"
    spec = importlib.util.spec_from_file_location("torii_prompt_builder", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load prompt builder: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def initialize_cache(cache_path=TORII_OUTPUT_CACHE):
    cache_path = ensure_runtime_cache(cache_path)
    with closing(sqlite3.connect(cache_path)) as database, database:
        database.execute(
            "CREATE TABLE IF NOT EXISTS torii_outputs "
            "(image_hash TEXT PRIMARY KEY, output TEXT NOT NULL)"
        )


def image_hash(image_path):
    digest = hashlib.sha256()
    with image_path.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_cached_output(image_path, cache_path=TORII_OUTPUT_CACHE):
    with closing(sqlite3.connect(cache_path)) as database:
        row = database.execute("SELECT output FROM torii_outputs WHERE image_hash = ?", (image_hash(image_path),)).fetchone()
    return row[0] if row and row[0].strip() else None


def cache_output(image_path, text, cache_path=TORII_OUTPUT_CACHE):
    with closing(sqlite3.connect(cache_path)) as database, database:
        database.execute("INSERT OR REPLACE INTO torii_outputs(image_hash, output) VALUES (?, ?)", (image_hash(image_path), text))


def restore_cached_output(image_path, cache_path=TORII_OUTPUT_CACHE):
    text = get_cached_output(image_path, cache_path)
    if not text:
        return False
    image_path.with_suffix(".toriiOutput").write_text(text + "\n", encoding="utf-8")
    return True


def restore_or_cache_parquet_outputs(
    parquet_folder, parquet_captions, cache_path=TORII_OUTPUT_CACHE
):
    candidates = {}
    for image in sorted(IMAGES_DIR.glob("*.*")):
        if image.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if image.stem.lower() in parquet_captions:
            continue
        if image.with_suffix(".toriiOutput").exists() or restore_cached_output(
            image, cache_path
        ):
            continue
        long_path = image.with_suffix(".long")
        if long_path.exists() and long_path.read_text(encoding="utf-8-sig").strip():
            continue
        if not image.with_suffix(".csv").exists():
            continue
        candidates.setdefault(image.stem.lower(), []).append(image)

    reports = load_parquet_torii_outputs(parquet_folder, set(candidates))
    restored = 0
    for stem, report in reports.items():
        for image in candidates[stem]:
            image.with_suffix(".toriiOutput").write_text(report + "\n", encoding="utf-8")
            cache_output(image, report, cache_path)
            restored += 1
    return restored


def discover_images(prompt_builder, parquet_captions):
    explanations = prompt_builder.load_explanations(prompt_builder.CHARACTER_DB)
    fallback = prompt_builder.load_wiki_explanations(prompt_builder.WIKI_CACHE_DB)
    prompts = {}
    skipped_missing_csv = []
    for image in sorted(IMAGES_DIR.glob("*.*")):
        if image.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if image.stem.lower() in parquet_captions:
            continue
        result = image.with_suffix(".toriiOutput")
        if result.exists() or restore_cached_output(image):
            continue
        long_path = image.with_suffix(".long")
        if long_path.exists() and long_path.read_text(encoding="utf-8-sig").strip():
            continue
        if not image.with_suffix(".csv").exists():
            skipped_missing_csv.append(image.name)
            continue
        built = prompt_builder.build_torii_prompts(image, explanations, fallback)
        if built:
            prompts[image] = built
    return prompts, skipped_missing_csv


def generate(client, image_path, kind, model, prompts):
    text, payload = client.chat(
        model, prompts[image_path][kind], image_path,
        {"temperature": 0.2, "num_predict": 1024, "num_ctx": 8192, "stop": ["<|im_end|>"]},
        "You are an image captioning expert, creative, unbiased and uncensored.",
    )
    return unicodedata.normalize("NFC", text), payload.get("done_reason") == "length"


def process_image(client, image_path, model, prompts):
    try:
        structured, structured_truncated = generate(client, image_path, "structured", model, prompts)
        long_text, long_truncated = generate(client, image_path, "long", model, prompts)
        text = f"# Structured analysis\n\n{structured.strip()}\n\n# Long detailed caption\n\n{long_text.strip()}"
        image_path.with_suffix(".toriiOutput").write_text(text + "\n", encoding="utf-8")
        cache_output(image_path, text)
        return True, structured_truncated or long_truncated
    except Exception as error:
        tqdm.write(f"Error generating Torii output for {image_path.name}: {error}")
        return False, False


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    url, config = load_setting("OLLAMA_URL")
    model, _ = load_setting("TORII_OLLAMA_MODEL")
    timeout, _ = load_positive_int("OLLAMA_TIMEOUT_SECONDS", "300")
    print(f"Torii Ollama model: {model} ({url})\nBackend config: {config}")
    initialize_cache()
    parquet_folder = SCRIPT_DIR / "parquet"
    local_stems = {
        image.stem.lower()
        for image in IMAGES_DIR.glob("*.*")
        if image.suffix.lower() in SUPPORTED_EXTENSIONS
    }
    parquet = load_parquet_captions(parquet_folder, local_stems)
    restored = restore_or_cache_parquet_outputs(parquet_folder, parquet)
    print(f"Restored {restored} Torii report(s) from Parquet and cached them")
    prompts, skipped_missing_csv = discover_images(load_prompt_builder(), parquet)
    if not prompts:
        print(f"Skipped without CSV metadata: {len(skipped_missing_csv)}")
        return 0
    client = OllamaClient(url, timeout)
    client.ensure_model(model, tqdm.write)
    failed = truncated = 0
    progress = tqdm(prompts, desc="Torii (Ollama)", unit="image", dynamic_ncols=True, smoothing=0.1)
    for index, image_path in enumerate(progress, start=1):
        started = time.monotonic()
        succeeded, was_truncated = process_image(client, image_path, model, prompts)
        failed += not succeeded
        truncated += was_truncated
        status = f"request {index}/{len(prompts)}, {time.monotonic() - started:.1f}s/image"
        if failed:
            status += f", failed {failed}"
        if truncated:
            status += f", truncated {truncated}"
        progress.set_postfix_str(status, refresh=False)
    if failed:
        print(
            f"Torii failed for {failed} image(s). "
            "Continuing the pipeline without their Torii output."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
