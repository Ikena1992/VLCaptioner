"""Generate final long and short captions with the configured Ollama model."""

from __future__ import annotations

from file_io import file_hash, atomic_write_text
from dataset_files import IMAGE_EXTENSIONS as SUPPORTED_EXTENSIONS, companion_paths

from runtime_config import load_bool, pipeline_images
from image_failures import is_skipped, skip_image

import argparse
import csv
import hashlib
import json
import importlib.util
import random
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

from ollama_client import OllamaClient, OllamaUnavailableError
from cache_seed import ensure_runtime_cache
from parquet_captions import load_parquet_captions
from runtime_config import load_positive_int, load_setting
from tqdm import tqdm
from caption_progress import caption_progress
from check_caption_quality import caption_text_issues

import create_clean_tag_files as tag_files
import finalize_caption_dataset as finalizer

SCRIPT_DIR = Path(__file__).parent.resolve()
IMAGES_DIR = pipeline_images()
CAPTION_CACHE = SCRIPT_DIR / "caches" / "captionResultsCache.sqlite3"
CAPTION_MAX_NEW_TOKENS = {"short": 384, "long": 1536}
DEFAULT_CAPTION_CONTEXT_SIZE = 65536


class CaptionReviewError(RuntimeError):
    def __init__(self, kind, text, issues):
        super().__init__("; ".join(issues))
        self.kind = kind
        self.text = text


class LazyModelClient:
    """Check or pull an explicit model only when inference is actually needed."""
    def __init__(self, client):
        self.client = client
        self.ready_models = set()

    def chat(self, model, *args, **kwargs):
        if model not in self.ready_models:
            self.client.ensure_model(model, tqdm.write)
            self.ready_models.add(model)
        return self.client.chat(model, *args, **kwargs)


def validate_caption(text, kind, payload=None):
    normalized = normalize_caption_output(text)
    issues = caption_text_issues(normalized, f".{kind}")
    if not normalized:
        issues = [f"{kind.capitalize()} caption was empty after normalization"]
    if payload and payload.get("done_reason") == "length":
        issues.append(f".{kind} response truncated at the token limit")
    if issues:
        raise CaptionReviewError(kind, normalized, issues)
    return normalized


def send_caption_to_review(image_path, error):
    sources = [image_path, *companion_paths(image_path)]
    finalizer.publish_files(sources, {
        image_path.with_suffix(f".{error.kind}").name: error.text,
        image_path.with_suffix(".review.json").name: json.dumps(
            {"reason": str(error), "caption_kind": error.kind}, ensure_ascii=False),
    }, review_issues=[str(error)])
    tqdm.write(f"REVIEW {image_path.name}: {error}")


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
        database.execute(
            "CREATE INDEX IF NOT EXISTS captions_by_image "
            "ON captions(image_hash, caption_kind, model)"
        )
        database.commit()
    finally:
        database.close()


def caption_reference(text, artist_names=()):
    """Remove sidecar attribution without normalizing literal scene writing."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return strip_artist_prefix(text, artist_names)


def canonical_cache_prompt(prompt):
    """Ignore presentation differences without dropping scene data or rules."""
    prompt = prompt.replace("\r\n", "\n").replace("\r", "\n")
    # Removing a standalone Torii thinking heading used to leave extra blank
    # lines after this report heading. No literal scene text is changed here.
    prompt = re.sub(r"(<torii_report>\n# Structured analysis[ \t]*\n)(?:[ \t]*\n)+",
                    r"\1\n", prompt)
    prompt = re.sub(r"(\n\n# Long caption to summarize\n<long_caption>\n)(.*?)"
                    r"(\n</long_caption>\nSummarize this caption using the short-caption task "
                    r"and source priorities above\.)(?=\nRefiner thinking mode: enabled\.\Z|\Z)",
                    lambda match: match[1] + caption_reference(match[2]) + match[3],
                    prompt, flags=re.DOTALL)
    prompt = re.sub(
        r"(\nOriginal caption reference:\n)(.*?)(\nRefiner thinking mode: (?:True|False))\Z",
        lambda match: match[1] + caption_reference(match[2]) + match[3],
        prompt, flags=re.DOTALL,
    )
    return prompt


def _cache_key(image_digest, caption_kind, model, prompt):
    digest = hashlib.sha256()
    digest.update(bytes.fromhex(image_digest))
    digest.update(f"\0{caption_kind}\0{model}\0{prompt}".encode("utf-8"))
    return digest.hexdigest()


def caption_cache_key(image_path, caption_kind, model, prompt):
    return _cache_key(file_hash(image_path), caption_kind, model,
                      canonical_cache_prompt(prompt))


def get_cached_caption(image_path, caption_kind, model, prompt, cache_path=CAPTION_CACHE):
    image_digest = file_hash(image_path)
    canonical = canonical_cache_prompt(prompt)
    database = sqlite3.connect(cache_path)
    try:
        row = None
        keys = [_cache_key(image_digest, caption_kind, model, canonical)]
        if canonical != prompt:
            keys.append(_cache_key(image_digest, caption_kind, model, prompt))
        for key in keys:
            # Check stored inputs as well as the hash: previous normalizers
            # could collapse meaningful spacing or line breaks in references.
            candidate = database.execute(
                "SELECT image_hash, caption_kind, model, prompt, caption "
                "FROM captions WHERE cache_key = ?", (key,),
            ).fetchone()
            if (candidate is not None and candidate[:3] == (image_digest, caption_kind, model)
                    and isinstance(candidate[3], str) and isinstance(candidate[4], str)
                    and canonical_cache_prompt(candidate[3]) == canonical):
                row = (candidate[4],)
                break
        if row is None:
            candidates = database.execute(
                "SELECT prompt, caption FROM captions "
                "WHERE image_hash = ? AND caption_kind = ? AND model = ? ORDER BY rowid DESC",
                (image_digest, caption_kind, model),
            )
            for cached_prompt, caption in candidates:
                if (isinstance(cached_prompt, str) and isinstance(caption, str)
                        and canonical_cache_prompt(cached_prompt) == canonical):
                    row = (caption,)
                    break
    finally:
        database.close()
    return row[0] if row else None


def save_cached_caption(image_path, caption_kind, model, prompt, caption, cache_path=CAPTION_CACHE):
    if caption_kind == "json":
        from PIL import Image, ImageOps
        from refiner_json import validate_json
        with Image.open(image_path) as image:
            width, height = ImageOps.exif_transpose(image).size
        caption = validate_json(caption, width, height)
    else:
        caption = validate_caption(caption, caption_kind)
    image_digest = file_hash(image_path)
    database = sqlite3.connect(cache_path)
    try:
        database.execute(
            "INSERT OR REPLACE INTO captions(cache_key, image_hash, caption_kind, model, prompt, caption) VALUES (?, ?, ?, ?, ?, ?)",
            (_cache_key(image_digest, caption_kind, model, canonical_cache_prompt(prompt)),
             image_digest, caption_kind, model, prompt, caption),
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
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    return text


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

    caption = strip_artist_prefix(caption, artist_names)
    prefix = f"Drawn by {', '.join(formatted_artists)}. "
    return prefix + caption


def strip_artist_prefix(caption, artist_names=()):
    # Recognize known CSV credits first, including dots followed by spaces in
    # artist names. Legacy credits without @ require a metadata match so scene
    # prose such as "Drawn by two horses, a carriage ..." stays intact.
    names = [name.strip().lstrip("@") for name in artist_names if name.strip()]
    credits = names + [", ".join(names)] if names else []
    credits += ["@" + name for name in names]
    if names:
        credits.append(", ".join("@" + name for name in names))
    credits.sort(key=len, reverse=True)
    while True:
        match = None
        for credit in credits:
            match = re.match(r"\s*Drawn\s+by\s+" + re.escape(credit) + r"\.(?:\s+|$)",
                             caption, flags=re.IGNORECASE)
            if match:
                break
        if match is None:
            match = re.match(r"\s*Drawn\s+by\s+([^\r\n]*?)\.(?:\s+|$)",
                             caption, flags=re.IGNORECASE)
            if not match or not all(name.strip().startswith("@") and len(name.strip()) > 1
                                    for name in match[1].split(",")):
                return caption
        caption = caption[match.end():]


def write_result(image_path, short_text, long_text, artist_names=None, preserve_existing_long=False):
    """Write both results; the SQLite request cache is managed separately."""
    short_text = normalize_caption_output(short_text)
    long_text = normalize_caption_output(long_text)
    if not short_text or not long_text:
        raise RuntimeError("One or both generated captions were empty")
    artist_names = artist_names or []
    short_text = add_artist_prefix(short_text, artist_names)
    long_text = add_artist_prefix(long_text, artist_names)
    atomic_write_text(image_path.with_suffix(".short"), short_text + "\n")
    if not preserve_existing_long:
        atomic_write_text(image_path.with_suffix(".long"), long_text + "\n")


def discover_images(prompt_builder, parquet_captions, overwrite_caption_cache=False, overwrite_caption_files=False, prepare_tags=None, create_json=False):
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
        try:
            if is_skipped(image):
                continue
            if image.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if not image.with_suffix(".csv").exists():
                skipped["missing_csv"].append(image.name)
                continue
            short_path, long_path = image.with_suffix(".short"), image.with_suffix(".long")
            complete = (
                not overwrite_caption_files
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
            if prepare_tags is not None:
                prepare_tags(image)
            built = prompt_builder.build_gemma_prompts(image, explanations, fallback, torii_output)
            needs_json = create_json and (overwrite_caption_cache or not image.with_suffix(".json").exists())
            if complete and needs_json and not built:
                prompts[image] = {}
                complete = False
            elif built:
                if uses_parquet:
                    parquet_cache_prompts[image] = built["long"]
                if not complete or needs_json:
                    prompts[image] = built
                    complete = False
            elif not complete:
                skipped["no_prompt"].append(image.name)
            if complete:
                skipped["complete"].append(image.name)
        except Exception as error:
            skip_image(image, error)
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
        try:
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
        except CaptionReviewError as error:
            tqdm.write(f"Not caching invalid Parquet caption for {image_path.name}: {error}")
        except Exception as error:
            skip_image(image_path, error)
    return cached


def refiner_request(client, model, prompt, image_path, options, thinking=False,
                    response_format=None):
    """Give reasoning a second budget when it consumes the first generation."""
    kwargs = {"think": thinking}
    if response_format is not None:
        kwargs["response_format"] = response_format
    text, payload = client.chat(model, prompt, image_path, options, **kwargs)
    if thinking and (payload.get("done_reason") == "length" or not text.strip()):
        tqdm.write(f"Retrying {image_path.name}: thinking returned no final answer or exhausted the token budget")
        text, payload = client.chat(model, prompt, image_path,
                                   {**options, "num_predict": options["num_predict"] * 2},
                                   **kwargs)
    return text, payload


def generate_json_sidecar(client, image_path, model, thinking=False,
                          overwrite_caption_cache=False,
                          context_size=DEFAULT_CAPTION_CONTEXT_SIZE,
                          cache_path=CAPTION_CACHE):
    from refiner_json import PROMPT, validate_json, compile_layout, write_sidecar
    destination = image_path.with_suffix(".json")
    if destination.exists() and not overwrite_caption_cache:
        return
    # Reject edits/replacements during inference rather than associating a
    # previous preview's coordinates with a new image or cache identity.
    def image_identity():
        stat = image_path.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino

    identity = image_identity()

    def ensure_same_image():
        if image_identity() != identity:
            raise ValueError("Image changed during layout generation; retry the image")

    from PIL import Image, ImageOps
    with Image.open(image_path) as image:
        width, height = ImageOps.exif_transpose(image).size
    reference_path = image_path.with_suffix(".long")
    reference = (caption_reference(reference_path.read_text(encoding="utf-8-sig"), load_artist_names(image_path))
                 if reference_path.exists() else "")
    with OllamaClient.inference_image(image_path) as preview:
        preview_size = preview.size
    pw, ph = preview_size
    prompt = PROMPT.replace('"width":800,"height":400', f'"width":{pw},"height":{ph}')
    prompt = prompt.replace('"bbox":[0,0,100,100]', f'"bbox":[0,0,{pw},{ph}]')
    prompt += f"\nInference preview canvas: width={pw}, height={ph} pixels.\n"
    prompt += "Coordinate recipe: preview-to-original-v2-exact-edges. Return all boxes in preview pixels.\n"
    prompt += "Original caption reference:\n" + reference
    prompt += f"\nRefiner thinking mode: {thinking}"
    ensure_same_image()
    cached = None if overwrite_caption_cache else get_cached_caption(image_path, "json", model, prompt, cache_path)
    text = None
    if cached:
        try:
            text = validate_json(cached, width, height)
            tqdm.write(f"Cache hit: {destination.name}")
        except (ValueError, TypeError):
            tqdm.write(f"Ignoring invalid cached JSON for {image_path.name}")
    if text is None:
        tqdm.write(f"Cache {'bypass' if overwrite_caption_cache else 'miss'}: {destination.name}; generating")
        request_prompt = prompt
        for attempt in range(2):
            raw, payload = refiner_request(client, model, request_prompt, image_path,
                {"temperature": 0.0, "num_predict": 16384 if thinking else 8192, "num_ctx": context_size},
                thinking=thinking, response_format="json")
            ensure_same_image()
            try:
                if payload.get("done_reason") == "length":
                    raise ValueError("Truncated refiner JSON")
                text = compile_layout(raw, width, height, preview_size=preview_size)
                break
            except (ValueError, TypeError) as error:
                if attempt:
                    raise
                request_prompt = prompt + f"\nPrevious output was invalid: {error}. Return a complete corrected JSON object."
        ensure_same_image()
        save_cached_caption(image_path, "json", model, prompt, text, cache_path)
    ensure_same_image()
    write_sidecar(destination, text, overwrite=overwrite_caption_cache)


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
    thinking=False,
):
    prompt = prompts[image_path][kind]
    if kind == "short" and long_caption:
        prompt += (
            "\n\n# Long caption to summarize\n<long_caption>\n"
            f"{caption_reference(long_caption, load_artist_names(image_path))}\n</long_caption>\n"
            "Summarize this caption using the short-caption task and source "
            "priorities above."
        )
    if thinking:
        prompt += "\nRefiner thinking mode: enabled."
    cached = None
    if not overwrite_caption_cache:
        cached = get_cached_caption(image_path, kind, model, prompt, cache_path)
    if cached:
        try:
            text = validate_caption(cached, kind)
            tqdm.write(f"Cache hit: {image_path.with_suffix('.' + kind).name}")
            return text
        except CaptionReviewError:
            tqdm.write(f"Ignoring invalid cached {kind} caption for {image_path.name}")
    tqdm.write(f"Cache {'bypass' if overwrite_caption_cache else 'miss'}: {image_path.with_suffix('.' + kind).name}; generating")
    options = {
        "temperature": 0.0,
        "num_predict": CAPTION_MAX_NEW_TOKENS[kind] + (8192 if thinking else 0),
        "num_ctx": context_size,
        "repeat_last_n": 256,
        # Penalizing normal repeated prose words can force Qwen into endless
        # synonym chains. Keep this neutral and validate actual repetition.
        "repeat_penalty": 1.0,
        "stop": ["END_CAPTION"],
    }
    if thinking:
        # A model can mention END_CAPTION while reasoning. Server stop tokens
        # apply to reasoning too, so normalize the final marker locally instead.
        options.pop("stop", None)
    request_prompt = prompt
    for attempt in range(2):
        # A fresh sample can escape a degenerate greedy response. Neutralize
        # repetition penalties on recovery: forced word avoidance can turn this
        # model's prose into endless synonym chains. Share one retry budget
        # with server repetition errors.
        request_options = options if not attempt else {
            **options, "temperature": 0.7, "repeat_penalty": 1.0,
        }
        try:
            text, payload = refiner_request(client, model, request_prompt, image_path,
                                           request_options, thinking=thinking)
            text = validate_caption(text, kind, payload)
            break
        except CaptionReviewError as error:
            if attempt:
                raise
            tqdm.write(f"Retrying {image_path.name} ({kind}): {error}")
            request_prompt = prompt + (
                f"\n\nPrevious response was rejected: {error}. "
                "Write a fresh, complete caption from the supplied sources. "
                "Use finite sentences describing supported scene details. "
                "Do not continue the previous response or pad with word chains, "
                "repetition, commentary, or reasoning. Follow the requested "
                "caption length and finish with END_CAPTION."
            )
        except RuntimeError as error:
            if attempt or not is_token_repeat_error(error):
                raise
            tqdm.write(f"Retrying {image_path.name} ({kind}): token repeat limit reached")
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
    overwrite_caption_files=False,
    thinking=False,
    create_json=False,
    cache_path=CAPTION_CACHE,
):
    if not overwrite_caption_files and all(
        image_path.with_suffix(ext).exists() and image_path.with_suffix(ext).read_text(encoding="utf-8-sig").strip()
        for ext in (".short", ".long")
    ):
        if create_json:
            generate_json_sidecar(client, image_path, model, thinking, overwrite_caption_cache, context_size, cache_path)
        return
    long_path = image_path.with_suffix(".long")
    long_text = (
        long_path.read_text(encoding="utf-8-sig").strip()
        if long_path.exists() and not overwrite_caption_files else ""
    )
    reuse_existing_long = bool(long_text)
    if not long_text:
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
            thinking=thinking,
            cache_path=cache_path,
        )
    long_text = validate_caption(long_text, "long")
    if not reuse_existing_long and not overwrite_caption_files:
        atomic_write_text(long_path, long_text + "\n")
    short_text = generate_caption(
        client,
        image_path,
        "short",
        model,
        prompts,
        long_text,
        overwrite_caption_cache=overwrite_caption_cache,
        context_size=context_size,
        thinking=thinking,
        cache_path=cache_path,
    )
    write_result(
        image_path, short_text, long_text, load_artist_names(image_path),
        preserve_existing_long=reuse_existing_long,
    )
    if create_json:
        generate_json_sidecar(client, image_path, model, thinking, overwrite_caption_cache, context_size, cache_path)


def build_image_finalizer(add_year_tag=False, add_copyright_tags=False):
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

    prepared = set()

    def prepare_tags(image_path):
        if image_path in prepared:
            return
        csv_path = image_path.with_suffix(".csv")
        if not tag_files.process_csv(
            csv_path,
            tag_rng,
            dropout_rng,
            dropout_protected_tags,
            report_success=False,
            add_year_tag=add_year_tag,
            add_copyright_tags=add_copyright_tags,
        ):
            raise RuntimeError(f"Could not create tag file for {image_path.name}")
        prepared.add(image_path)

    def finish(image_path):
        prepare_tags(image_path)
        if not finalizer.process_caption_pair(
            image_path.with_suffix(".short"), report_success=False
        ):
            raise RuntimeError(f"Could not finalize {image_path.name}")

    finish.prepare_tags = prepare_tags
    return finish


def refinement_progress(processed, total, elapsed, skipped=0):
    return caption_progress("Refining captions", processed, total, elapsed, skipped)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--overwrite-caption-cache",
        action="store_true",
        help="Bypass long, short and JSON caches; regenerate existing JSON when --refiner-json is enabled. Existing long/short files are controlled separately.",
    )
    parser.add_argument(
        "--overwrite-caption-files",
        action="store_true",
        help="Replace existing .long and .short files; caption cache use is controlled separately.",
    )
    parser.add_argument("--add-year-tag", action="store_true", help="Add post upload year to .tag files.")
    parser.add_argument("--add-copyright-tags", action="store_true", help="Add copyright/series tags to .tag files.")
    parser.add_argument("--refiner-thinking", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--refiner-json", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args(argv)
    thinking = args.refiner_thinking if args.refiner_thinking is not None else load_bool("REFINER_THINKING")[0]
    create_json = args.refiner_json if args.refiner_json is not None else load_bool("REFINER_JSON")[0]
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
    print(f"Ollama model: {model} ({url})\nBackend config: {config}")
    initialize_cache()
    finalizer.recover_publications()
    for journal in finalizer.DONE_DIR.glob(".finalize-*/committed.json"):
        finalizer.finish_cleanup(journal.parent)
    finish_image = build_image_finalizer(args.add_year_tag, args.add_copyright_tags)
    local_stems = {
        image.stem.lower()
        for image in IMAGES_DIR.glob("*.*")
        if image.suffix.lower() in SUPPORTED_EXTENSIONS
    }
    parquet = load_parquet_captions(SCRIPT_DIR / "parquet", local_stems)
    prompts, skipped, parquet_cache_prompts = discover_images(
        load_prompt_builder(), parquet, args.overwrite_caption_cache,
        args.overwrite_caption_files, prepare_tags=finish_image.prepare_tags, create_json=create_json,
    )
    cached_parquet = cache_parquet_long_captions(
        parquet,
        parquet_cache_prompts,
        model,
        overwrite=args.overwrite_caption_cache,
    )
    print(f"Cached {cached_parquet} matching Parquet long caption(s).")
    prompts = {image: prompt for image, prompt in prompts.items() if not is_skipped(image)}
    completed_images = [IMAGES_DIR / name for name in skipped["complete"] if not is_skipped(IMAGES_DIR / name)]
    if not prompts and not completed_images:
        print("No images to process.")
        print(f"Skipped without CSV metadata: {len(skipped['missing_csv'])}")
        print(f"Skipped without usable prompt: {len(skipped['no_prompt'])}")
        print(f"Skipped with existing result/cache: {len(skipped['complete'])}")
        return 0
    failed_other = 0
    for image_path in tqdm(
        completed_images,
        desc="Finalizing completed captions",
        unit="image",
        dynamic_ncols=True,
    ):
        try:
            finish_image(image_path)
        except Exception as error:
            failed_other += 1
            skip_image(image_path, error)
    failed_context = 0
    failed_repeat = 0
    reviewed = 0
    if prompts and configured_model.lower() != "auto":
        client = LazyModelClient(client)
    refinement_started = time.monotonic()
    print(f"Refining captions for {len(prompts):,} image(s)...", flush=True)
    for index, image_path in enumerate(prompts, start=1):
        try:
            process_image(
                client,
                image_path,
                model,
                prompts,
                parquet,
                args.overwrite_caption_cache,
                context_size,
                args.overwrite_caption_files,
                thinking=thinking, create_json=create_json,
            )
            finish_image(image_path)
        except OllamaUnavailableError:
            # A missing server/model is shared by the remaining batch. Stop
            # promptly rather than repeating a failed model check per image.
            raise
        except CaptionReviewError as error:
            try:
                send_caption_to_review(image_path, error)
                reviewed += 1
            except Exception as publication_error:
                failed_other += 1
                skip_image(image_path, publication_error)
        except Exception as error:
            skip_image(image_path, error)
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
                failed_other += 1
        print(refinement_progress(
            index, len(prompts), time.monotonic() - refinement_started,
            failed_context + failed_repeat + failed_other,
        ), flush=True)
    if failed_context or failed_repeat or failed_other:
        print(
            f"Refinement completed with {failed_context} context-size failure(s) "
            f"and {failed_repeat} token-repetition failure(s), plus {failed_other} other image failure(s). "
            "Retry the pipeline to process skipped images. For context-size "
            "failures, increase OLLAMA_CONTEXT_SIZE first."
        )
    if reviewed:
        print(f"Sent {reviewed} image(s) to captionReview for invalid or truncated captions.")
    print("\nDone!\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
