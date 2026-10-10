from source_tag_file import MARKER, read_source_tags
import argparse
import sys
import os
import tempfile
import json
from pathlib import Path
from model_access import ModelAccessError, load_with_access_error

import pandas as pd
import numpy as np
import timm
import torch
from PIL import Image, ImageOps
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import LocalEntryNotFoundError
from tqdm import tqdm
from torchvision.transforms import Compose, InterpolationMode, Normalize, Resize, ToTensor


MODEL_REPO = "animetimm/convnextv2_huge.dbv4-full"
FALLBACK_MODEL_REPO = "SmilingWolf/wd-eva02-large-tagger-v3"
LABEL_FILENAME = "selected_tags.csv"

HIGH_CONFIDENCE_THRESHOLD = 0.91
GENERAL_THRESHOLD_FALLBACK = 0.38
CHARACTER_THRESHOLD_FALLBACK = 0.51

SCRIPT_DIR = Path(__file__).resolve().parent

IMAGE_FOLDER = SCRIPT_DIR / ".." / "images"  # one level above
IMAGE_FOLDER = IMAGE_FOLDER.resolve()

model_cache_dir = SCRIPT_DIR / "models/convnextv2_huge.dbv4-full"

IMAGE_EXTENSIONS = {
    ".avif",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}

INCLUDE_GENERAL_TAGS = True
INCLUDE_CHARACTER_TAGS = True

REPLACE_UNDERSCORES = True

BANNED_TAGS = {
    "uncensored",
}

KAOMOJIS = {
    "0_0",
    "(o)_(o)",
    "+_+",
    "+_-",
    "._.",
    "<|>_<|>",
    "=_=",
    ">_<",
    "3_3",
    "6_9",
    ">_o",
    "@_@",
    "^_^",
    "o_o",
    "u_u",
    "x_x",
    "|_|",
    "||_||",
}


def normalize_tag_for_compare(tag: str) -> str:
    # Existing Danbooru TXT files may contain literal parentheses, while model
    # output escapes them. Treat both spellings as the same tag when deduping.
    return (
        tag.strip()
        .replace(r"\(", "(")
        .replace(r"\)", ")")
        .lower()
        .replace(" ", "_")
    )


def clean_tag(tag: str) -> str:
    if REPLACE_UNDERSCORES and tag not in KAOMOJIS:
        tag = tag.replace("_", " ")

    tag = tag.replace("(", r"\(").replace(")", r"\)")
    return tag


def resolve_cached_file(repo_id, filename, folder):
    """Find direct downloads, the project Hub cache, or the default Hub cache."""
    direct = folder / filename
    if direct.is_file():
        return str(direct)
    for cache in (str(folder), None):
        try:
            return hf_hub_download(
                repo_id, filename, cache_dir=cache, local_files_only=True
            )
        except LocalEntryNotFoundError:
            pass
    raise LocalEntryNotFoundError(f"No cached {filename} for {repo_id}")


def download_model_files(local_files_only=False):
    model_cache_dir.mkdir(parents=True, exist_ok=True)

    print("Downloading/loading model files...")
    print(f"Model folder: {model_cache_dir}")

    if local_files_only:
        return Path(resolve_cached_file(MODEL_REPO, LABEL_FILENAME, model_cache_dir))
    labels_path = hf_hub_download(
        repo_id=MODEL_REPO,
        filename=LABEL_FILENAME,
        local_dir=str(model_cache_dir),
        local_files_only=local_files_only,
    )

    return Path(labels_path)


def read_label_table(path):
    labels = pd.read_csv(path, keep_default_na=False)
    if labels.empty or not {"name", "category"}.issubset(labels.columns):
        raise ValueError(f"Invalid tag label table: {path}")
    names = labels["name"]
    if not names.map(lambda name: isinstance(name, str) and bool(name.strip())).all() or names.duplicated().any():
        raise ValueError(f"Tag names must be nonempty and unique: {path}")
    categories = pd.to_numeric(labels["category"], errors="coerce")
    if not categories.isin([0, 4, 9]).all():
        raise ValueError(f"Invalid tag categories (expected 0, 4, or 9): {path}")
    labels["category"] = categories.astype(int)
    return labels


def validate_model_config(config_path, tag_names):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if config.get("num_classes") != len(tag_names):
        raise ValueError("Model class count does not match selected_tags.csv")
    if "tags" in config and config["tags"] != tag_names:
        raise ValueError("Model tag order does not match selected_tags.csv")


def load_model_and_labels(local_files_only=False):
    model_source = f"hf_hub:{MODEL_REPO}"
    checkpoint_path = ""
    if local_files_only:
        config_path = resolve_cached_file(MODEL_REPO, "config.json", model_cache_dir)
        try:
            checkpoint_path = resolve_cached_file(MODEL_REPO, "model.safetensors", model_cache_dir)
        except LocalEntryNotFoundError:
            checkpoint_path = resolve_cached_file(MODEL_REPO, "pytorch_model.bin", model_cache_dir)
        model_source = f"local-dir:{Path(config_path).parent}"
    else:
        config_path = hf_hub_download(
            MODEL_REPO, "config.json", cache_dir=str(model_cache_dir)
        )
    labels_path = download_model_files(local_files_only)
    labels_df = read_label_table(labels_path)

    tag_names = labels_df["name"].tolist()
    validate_model_config(config_path, tag_names)
    categories = labels_df["category"].astype(int).tolist()
    fallback_thresholds = {
        0: GENERAL_THRESHOLD_FALLBACK,
        4: CHARACTER_THRESHOLD_FALLBACK,
    }
    if "best_threshold" in labels_df.columns:
        best_thresholds = pd.to_numeric(
            labels_df["best_threshold"], errors="coerce"
        ).tolist()
    else:
        best_thresholds = [float("nan")] * len(labels_df)
    best_thresholds = [
        float(value) if pd.notna(value) and np.isfinite(value) and 0 <= value <= 1
        else fallback_thresholds.get(category, 0.4)
        for value, category in zip(best_thresholds, categories)
    ]

    general_indexes = [
        i for i, category in enumerate(categories)
        if category == 0
    ]

    character_indexes = [
        i for i, category in enumerate(categories)
        if category == 4
    ]

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = timm.create_model(
        model_source,
        pretrained=not local_files_only,
        checkpoint_path=checkpoint_path,
        num_classes=len(tag_names),
        cache_dir=str(model_cache_dir),
    )
    model = model.to(device).eval()
    transform = Compose(
        [
            Resize((512, 512), interpolation=InterpolationMode.BICUBIC),
            ToTensor(),
            Normalize(
                mean=(0.485, 0.456, 0.406),
                std=(0.229, 0.224, 0.225),
            ),
        ]
    )

    return {
        "repo_id": MODEL_REPO,
        "threshold_description": "per-tag optimized thresholds from selected_tags.csv",
        "model": model,
        "device": device,
        "transform": transform,
        "tag_names": tag_names,
        "best_thresholds": best_thresholds,
        "general_indexes": general_indexes,
        "character_indexes": character_indexes,
    }


def load_fallback_model():
    import onnxruntime as ort

    cache_dir = SCRIPT_DIR / "models" / "wd-eva02-large-tagger-v3"
    paths = {}
    for filename in (LABEL_FILENAME, "model.onnx"):
        try:
            paths[filename] = resolve_cached_file(FALLBACK_MODEL_REPO, filename, cache_dir)
        except LocalEntryNotFoundError:
            paths[filename] = hf_hub_download(
                FALLBACK_MODEL_REPO, filename, local_dir=str(cache_dir), token=False
            )
    labels = read_label_table(paths[LABEL_FILENAME])
    categories = labels["category"].astype(int).tolist()
    available = ort.get_available_providers()
    providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in available]
    session = ort.InferenceSession(paths["model.onnx"], providers=providers)
    return {
        "repo_id": FALLBACK_MODEL_REPO,
        "threshold_description": "SmilingWolf general=0.35, character=0.85",
        "onnx_session": session,
        "device": ", ".join(session.get_providers()),
        "tag_names": labels["name"].tolist(),
        "best_thresholds": [0.85 if c == 4 else 0.35 for c in categories],
        "general_indexes": [i for i, c in enumerate(categories) if c == 0],
        "character_indexes": [i for i, c in enumerate(categories) if c == 4],
    }


def load_preferred_model():
    try:
        return load_model_and_labels(local_files_only=True)
    except LocalEntryNotFoundError:
        pass
    try:
        return load_with_access_error(load_model_and_labels, MODEL_REPO)
    except ModelAccessError as error:
        print(error)
        print(f"Using fallback tagger: {FALLBACK_MODEL_REPO}")
        return load_with_access_error(load_fallback_model, FALLBACK_MODEL_REPO)


def predict_scores(image, model_data):
    if "onnx_session" in model_data:
        session = model_data["onnx_session"]
        model_input = session.get_inputs()[0]
        height, width = model_input.shape[1:3]
        image = image.resize((width, height), Image.Resampling.BICUBIC)
        # The ONNX model expects NHWC BGR values in [0, 255] and returns probabilities.
        batch = np.asarray(image, dtype=np.float32)[:, :, ::-1][None, ...].copy()
        return session.run(None, {model_input.name: batch})[0][0]
    image_tensor = model_data["transform"](image).unsqueeze(0)
    image_tensor = image_tensor.to(model_data["device"])
    with torch.inference_mode():
        predictions = model_data["model"](image_tensor).sigmoid()[0]
    return predictions.float().cpu().numpy()


def prepare_image(image_path: Path) -> Image.Image:
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")

    background = Image.new("RGBA", image.size, (255, 255, 255, 255))
    background.alpha_composite(image)
    image = background.convert("RGB")

    width, height = image.size
    square_size = max(width, height)

    padded = Image.new("RGB", (square_size, square_size), (255, 255, 255))

    paste_x = (square_size - width) // 2
    paste_y = (square_size - height) // 2

    padded.paste(image, (paste_x, paste_y))

    return padded


def predict_tags(
    image_path: Path,
    model_data: dict,
    threshold: float | None = None,
) -> list[str]:
    image = prepare_image(image_path)
    predictions = predict_scores(image, model_data)

    tag_names = model_data["tag_names"]
    if predictions.ndim != 1 or not np.isfinite(predictions).all():
        raise ValueError("Tagger returned invalid probability scores")
    if len(predictions) != len(tag_names):
        raise ValueError("Tagger output size does not match selected_tags.csv")
    if np.any((predictions < 0) | (predictions > 1)):
        raise ValueError("Tagger probability scores must be between 0 and 1")

    candidate_indexes = []

    if INCLUDE_GENERAL_TAGS:
        candidate_indexes.extend(model_data["general_indexes"])

    if INCLUDE_CHARACTER_TAGS:
        candidate_indexes.extend(model_data["character_indexes"])

    scored_tags = []

    for index in candidate_indexes:
        score = predictions[index]
        tag = tag_names[index]
        if normalize_tag_for_compare(tag) in BANNED_TAGS:
            continue

        tag_threshold = (
            threshold
            if threshold is not None
            else model_data["best_thresholds"][index]
        )

        if score >= tag_threshold:
            scored_tags.append((tag, score))

    scored_tags.sort(key=lambda item: item[1], reverse=True)

    return [
        clean_tag(tag)
        for tag, score in scored_tags
    ]


def has_source_tags(txt_path: Path) -> bool:
    if not txt_path.exists():
        return False
    if not txt_path.is_file():
        raise IsADirectoryError(f"Tag-file path is not a file: {txt_path}")
    return bool(split_tags(txt_path.read_text(encoding="utf-8-sig")))


def find_images_without_txt(image_folder: Path) -> list[Path]:
    image_paths = []

    for path in image_folder.iterdir():
        if not path.is_file():
            continue

        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        txt_path = path.with_suffix(".txt")

        if has_source_tags(txt_path):
            continue

        image_paths.append(path)

    return sorted(image_paths)


def find_images_with_txt(image_folder: Path) -> list[Path]:
    return sorted(
        path
        for path in image_folder.iterdir()
        if path.is_file()
        and path.suffix.lower() in IMAGE_EXTENSIONS
        and has_source_tags(path.with_suffix(".txt"))
    )


def split_tags(content: str) -> list[str]:
    return [tag.strip() for tag in read_source_tags(content).replace("\n", ",").split(",") if tag.strip()]


def write_tags_atomically(txt_path: Path, content: str):
    """Keep the previous tag file intact if writing or replacement fails."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=txt_path.parent,
            prefix=f".{txt_path.name}.", suffix=".tmp", delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, txt_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def append_missing_tags(txt_path: Path, predicted_tags: list[str]) -> list[str]:
    original_content = txt_path.read_text(encoding="utf-8-sig")
    existing_tags = split_tags(original_content)
    existing_normalized = {normalize_tag_for_compare(tag) for tag in existing_tags}
    missing_tags = []

    for tag in predicted_tags:
        normalized = normalize_tag_for_compare(tag)
        if normalized not in existing_normalized:
            missing_tags.append(tag)
            existing_normalized.add(normalized)

    if missing_tags:
        # read_source_tags strips whitespace; its length is not an offset into
        # the original file. Preserve the suffix starting at the actual marker.
        marker_offset = original_content.find("\n" + MARKER)
        metadata_suffix = original_content[marker_offset:] if marker_offset >= 0 else ""
        write_tags_atomically(txt_path, ", ".join([*existing_tags, *missing_tags]) + metadata_suffix)

    return missing_tags


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-high-confidence-missing-tags",
        action="store_true",
        help="Do not append high-confidence WD14 tags to existing TXT files.",
    )
    args = parser.parse_args()
    image_folder = Path(IMAGE_FOLDER)

    if not image_folder.exists():
        raise FileNotFoundError(f"Image folder not found: {image_folder}")

    untagged_image_paths = find_images_without_txt(image_folder)
    tagged_image_paths = find_images_with_txt(image_folder)
    sidecars = {}
    for image_path in [*untagged_image_paths, *tagged_image_paths]:
        txt_path = image_path.with_suffix(".txt")
        if txt_path in sidecars:
            raise ValueError(
                f"Images share the same tag file {txt_path.name}: "
                f"{sidecars[txt_path].name} and {image_path.name}. "
                "Rename one image before tagging."
            )
        sidecars[txt_path] = image_path

    if not untagged_image_paths and not tagged_image_paths:
        print(f"No images found in: {image_folder}")
        return

    if args.skip_high_confidence_missing_tags and not untagged_image_paths:
        print(f"No untagged images found in: {image_folder}")
        print("Existing TXT files: high-confidence missing-tag step skipped")
        return

    try:
        model_data = load_preferred_model()
    except ModelAccessError as error:
        print(f"Tagger cannot start: {error}", file=sys.stderr)
        raise SystemExit(1)

    print("----------------------------------------------------")
    print(f"Image folder: {image_folder}")
    print(f"Untagged images found: {len(untagged_image_paths)}")
    print(f"Images with TXT found: {len(tagged_image_paths)}")
    print(f"Threshold: {model_data['threshold_description']}")
    print(f"Existing-TXT add threshold: {HIGH_CONFIDENCE_THRESHOLD:.2f}")
    print(f"Model: {model_data['repo_id']}")
    print(f"Device: {model_data['device']}")
    print("Rating tags: disabled")
    if args.skip_high_confidence_missing_tags:
        print("Existing TXT files: high-confidence missing-tag step skipped")
    else:
        print("Existing TXT files: preserve tags and append missing high-confidence tags")
    print("----------------------------------------------------")

    failed_count = 0
    for image_path in tqdm(untagged_image_paths, desc="Tagging new images", unit="image"):
        try:
            tags = predict_tags(image_path, model_data)
            if not tags:
                raise ValueError("No tags passed the thresholds; no TXT file was created")

            txt_path = image_path.with_suffix(".txt")
            if txt_path.is_file():
                # Empty/metadata-only source files need full tagging too, but
                # their metadata must survive regeneration.
                append_missing_tags(txt_path, tags)
            else:
                write_tags_atomically(txt_path, ", ".join(tags))

            tqdm.write(
                f"Tagged: {image_path.name} -> {txt_path.name} "
                f"({len(tags)} tags)"
            )

        except Exception as error:
            failed_count += 1
            tqdm.write(f"Failed: {image_path.name} | {error}")

    existing_txt_images = [] if args.skip_high_confidence_missing_tags else tagged_image_paths
    for image_path in tqdm(existing_txt_images, desc="Checking existing tags", unit="image"):
        try:
            tags = predict_tags(
                image_path,
                model_data,
                threshold=HIGH_CONFIDENCE_THRESHOLD,
            )
            txt_path = image_path.with_suffix(".txt")
            missing_tags = append_missing_tags(txt_path, tags)
            tqdm.write(
                f"Checked: {image_path.name} -> {txt_path.name} "
                f"({len(missing_tags)} tags added)"
            )
        except Exception as error:
            failed_count += 1
            tqdm.write(f"Failed: {image_path.name} | {error}")

    print("----------------------------------------------------")
    if failed_count:
        print(f"Tagging failed for {failed_count} image(s). Completed work is kept.", file=sys.stderr)
        raise SystemExit(1)
    print("Done.")


if __name__ == "__main__":
    main()
