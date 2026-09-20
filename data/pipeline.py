"""Single source of truth for VLTagger pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Stage:
    script: str
    label: str
    optional_key: str | None = None


STAGES = (
    Stage("convert_images_to_webp.py", "Convert images to WebP"),
    Stage("fetch_danbooru_tags.py", "Fetch Danbooru tags"),
    Stage("tag_images_with_wd14.py", "Add WD14 tags", "wd14"),
    Stage("fetch_character_explanations.py", "Fetch character explanations"),
    Stage("create_metadata_csvs.py", "Create metadata CSV files"),
    Stage("create_vlm_character_descriptions.py", "Create character descriptions"),
    Stage("generate_torii_captions.py", "Generate Torii captions"),
    Stage("normalize_torii_captions.py", "Normalize Torii captions"),
    Stage("generate_gemma_captions.py", "Generate refined captions and finalize images"),
    Stage("quarantine_runaway_captions.py", "Quarantine runaway captions"),
)


def stage_command(
    stage: Stage,
    python: str,
    overwrite_danbooru_txt=False,
    overwrite_caption_cache=False,
    skip_wd14_high_confidence=False,
):
    command = [python, str(ROOT / "data" / stage.script)]
    if stage.script == "fetch_danbooru_tags.py" and overwrite_danbooru_txt:
        command.append("--overwrite-existing-txt")
    if stage.script == "generate_gemma_captions.py" and overwrite_caption_cache:
        command.append("--overwrite-caption-cache")
    if stage.script == "tag_images_with_wd14.py" and skip_wd14_high_confidence:
        command.append("--skip-high-confidence-missing-tags")
    return command
