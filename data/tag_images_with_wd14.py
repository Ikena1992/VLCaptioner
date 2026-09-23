from source_tag_file import read_source_tags
import argparse
from pathlib import Path

import pandas as pd
import timm
import torch
from PIL import Image
from huggingface_hub import hf_hub_download
from tqdm import tqdm
from torchvision.transforms import Compose, InterpolationMode, Normalize, Resize, ToTensor


MODEL_REPO = "animetimm/convnextv2_huge.dbv4-full"
LABEL_FILENAME = "selected_tags.csv"

HIGH_CONFIDENCE_THRESHOLD = 0.95
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


def download_model_files():
    model_cache_dir.mkdir(parents=True, exist_ok=True)

    print("Downloading/loading model files...")
    print(f"Model folder: {model_cache_dir}")

    labels_path = hf_hub_download(
        repo_id=MODEL_REPO,
        filename=LABEL_FILENAME,
        local_dir=str(model_cache_dir),
    )

    return Path(labels_path)


def load_model_and_labels():
    labels_path = download_model_files()
    labels_df = pd.read_csv(labels_path)

    tag_names = labels_df["name"].tolist()
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
        float(value) if pd.notna(value) else fallback_thresholds.get(category, 0.4)
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
        f"hf_hub:{MODEL_REPO}",
        pretrained=True,
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
        "model": model,
        "device": device,
        "transform": transform,
        "tag_names": tag_names,
        "best_thresholds": best_thresholds,
        "general_indexes": general_indexes,
        "character_indexes": character_indexes,
    }


def prepare_image(image_path: Path) -> Image.Image:
    image = Image.open(image_path).convert("RGBA")

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
    image_tensor = model_data["transform"](image).unsqueeze(0)
    image_tensor = image_tensor.to(model_data["device"])

    with torch.inference_mode():
        predictions = model_data["model"](image_tensor).sigmoid()[0]

    predictions = predictions.float().cpu().numpy()

    tag_names = model_data["tag_names"]

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


def find_images_without_txt(image_folder: Path) -> list[Path]:
    image_paths = []

    for path in image_folder.iterdir():
        if not path.is_file():
            continue

        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        txt_path = path.with_suffix(".txt")

        if txt_path.exists():
            continue

        image_paths.append(path)

    return sorted(image_paths)


def find_images_with_txt(image_folder: Path) -> list[Path]:
    return sorted(
        path
        for path in image_folder.iterdir()
        if path.is_file()
        and path.suffix.lower() in IMAGE_EXTENSIONS
        and path.with_suffix(".txt").is_file()
    )


def split_tags(content: str) -> list[str]:
    return [tag.strip() for tag in read_source_tags(content).replace("\n", ",").split(",") if tag.strip()]


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
        metadata_suffix = original_content[len(read_source_tags(original_content)): ]
        txt_path.write_text(", ".join([*existing_tags, *missing_tags]) + metadata_suffix, encoding="utf-8")

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

    if not untagged_image_paths and not tagged_image_paths:
        print(f"No images found in: {image_folder}")
        return

    if args.skip_high_confidence_missing_tags and not untagged_image_paths:
        print(f"No untagged images found in: {image_folder}")
        print("Existing TXT files: high-confidence missing-tag step skipped")
        return

    model_data = load_model_and_labels()

    print("----------------------------------------------------")
    print(f"Image folder: {image_folder}")
    print(f"Untagged images found: {len(untagged_image_paths)}")
    print(f"Images with TXT found: {len(tagged_image_paths)}")
    print("Threshold: per-tag optimized thresholds from selected_tags.csv")
    print(f"Existing-TXT add threshold: {HIGH_CONFIDENCE_THRESHOLD:.2f}")
    print(f"Model: {MODEL_REPO}")
    print(f"Device: {model_data['device']}")
    print("Rating tags: disabled")
    if args.skip_high_confidence_missing_tags:
        print("Existing TXT files: high-confidence missing-tag step skipped")
    else:
        print("Existing TXT files: preserve tags and append missing high-confidence tags")
    print("----------------------------------------------------")

    for image_path in tqdm(untagged_image_paths, desc="Tagging new images", unit="image"):
        try:
            tags = predict_tags(image_path, model_data)

            txt_path = image_path.with_suffix(".txt")
            txt_path.write_text(", ".join(tags), encoding="utf-8")

            tqdm.write(
                f"Tagged: {image_path.name} -> {txt_path.name} "
                f"({len(tags)} tags)"
            )

        except Exception as error:
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
            tqdm.write(f"Failed: {image_path.name} | {error}")

    print("----------------------------------------------------")
    print("Done.")


if __name__ == "__main__":
    main()
