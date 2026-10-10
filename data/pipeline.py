"""Single source of truth for VLTagger pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from source_tag_file import has_source_tags


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Stage:
    script: str
    label: str
    optional_key: str | None = None


STAGES = (
    Stage("convert_images_to_webp.py", "Convert images to WebP"),
    Stage("fetch_danbooru_tags.py", "Fetch Danbooru / Gelbooru tags"),
    Stage("tag_images_with_wd14.py", "Generate image tags", "wd14"),
    Stage("fetch_character_explanations.py", "Fetch character explanations"),
    Stage("create_metadata_csvs.py", "Create metadata CSV files"),
    Stage("create_vlm_character_descriptions.py", "Create character descriptions"),
    Stage("generate_torii_captions.py", "Generate Torii captions"),
    Stage("normalize_torii_captions.py", "Normalize Torii captions"),
    Stage("generate_gemma_captions.py", "Generate refined captions and finalize images"),
)


def stage_description(stage, overwrite_danbooru_txt=False, skip_wd14_high_confidence=False,
                      overwrite_caption_cache=False, overwrite_caption_files=False,
                      add_year_tag=False, add_copyright_tags=False):
    if stage.script == "fetch_danbooru_tags.py":
        return "Replace existing source tags from matching posts" if overwrite_danbooru_txt else "Fetch source tags only for images without tags"
    if stage.script == "tag_images_with_wd14.py":
        return "Tag images without tags; keep existing tags" if skip_wd14_high_confidence else "Tag new images; add missing tags at 0.91 confidence"
    if stage.script == "generate_gemma_captions.py":
        text = "Replace caption files" if overwrite_caption_files else "Keep existing caption files"
        text += "; bypass refinement cache" if overwrite_caption_cache else "; reuse refinement cache"
        if add_year_tag:
            text += "; include upload year"
        if add_copyright_tags:
            text += "; include series tags"
        return text
    return {
        "convert_images_to_webp.py": "Convert supported originals; preserve existing WebP files",
        "fetch_character_explanations.py": "Fetch missing character references",
        "create_metadata_csvs.py": "Merge new source tags into metadata; preserve existing fields",
        "create_vlm_character_descriptions.py": "Generate missing character descriptions",
        "generate_torii_captions.py": "Reuse Torii reports; generate missing reports",
        "normalize_torii_captions.py": "Prepare Torii reports for refinement",
    }.get(stage.script, stage.label)


def stage_skip_reason(stage, image_folder, overwrite_danbooru_txt=False,
                      skip_wd14_high_confidence=False, **options):
    def has_tags(path):
        try:
            return has_source_tags(path)
        except (OSError, UnicodeError):
            # Let the actual step report and skip an unreadable source file.
            return False
    images = [p for p in image_folder.iterdir() if p.is_file() and p.suffix.lower()
              in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp", ".avif"}]
    if stage.script == "convert_images_to_webp.py":
        if not any(p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".gif"} for p in images):
            return "No images need WebP conversion"
    elif stage.script == "fetch_danbooru_tags.py":
        if not any(re.fullmatch(r"[0-9a-fA-F]{32}", p.stem) and
                   (overwrite_danbooru_txt or not has_tags(p.with_suffix(".txt"))) for p in images):
            return "No matching image hashes need source-tag lookup"
    elif stage.script == "tag_images_with_wd14.py" and skip_wd14_high_confidence:
        if all(has_tags(p.with_suffix(".txt")) for p in images):
            return "All images have tags; adding missing tags is turned off"
    return None


def stage_command(
    stage: Stage,
    python: str,
    overwrite_danbooru_txt=False,
    overwrite_caption_cache=False,
    skip_wd14_high_confidence=False,
    add_year_tag=False,
    add_copyright_tags=False,
    overwrite_caption_files=False,
):
    command = [python, str(ROOT / "data" / stage.script)]
    if stage.script == "fetch_danbooru_tags.py" and overwrite_danbooru_txt:
        command.append("--overwrite-existing-txt")
    if stage.script == "generate_gemma_captions.py" and overwrite_caption_cache:
        command.append("--overwrite-caption-cache")
    if stage.script == "generate_gemma_captions.py" and overwrite_caption_files:
        command.append("--overwrite-caption-files")
    if stage.script == "generate_gemma_captions.py" and add_year_tag:
        command.append("--add-year-tag")
    if stage.script == "generate_gemma_captions.py" and add_copyright_tags:
        command.append("--add-copyright-tags")
    if stage.script == "tag_images_with_wd14.py" and skip_wd14_high_confidence:
        command.append("--skip-high-confidence-missing-tags")
    return command
