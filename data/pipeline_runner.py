"""Command-line runner for the shared VLTagger pipeline."""

import argparse
import subprocess
import sys
import os
from image_failures import ENV_KEY, reset_failures, failures

from pipeline import ROOT, STAGES, stage_command, stage_skip_reason, stage_description


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-wd14",
        action="store_true",
        help="Skip adding high-confidence WD14 tags to existing TXT files.",
    )
    parser.add_argument("--overwrite-danbooru-txt", action="store_true")
    parser.add_argument("--overwrite-caption-cache", action="store_true")
    parser.add_argument("--overwrite-caption-files", action="store_true")
    parser.add_argument("--add-year-tag", action="store_true")
    parser.add_argument("--add-copyright-tags", action="store_true")
    args = parser.parse_args()
    os.environ[ENV_KEY] = reset_failures()
    options = dict(overwrite_danbooru_txt=args.overwrite_danbooru_txt,
                   overwrite_caption_cache=args.overwrite_caption_cache,
                   skip_wd14_high_confidence=args.skip_wd14,
                   add_year_tag=args.add_year_tag, add_copyright_tags=args.add_copyright_tags,
                   overwrite_caption_files=args.overwrite_caption_files)
    for stage in STAGES:
        reason = stage_skip_reason(stage, ROOT / "images", **options)
        if reason:
            print(f"Skipped {stage.label}: {reason}", flush=True)
            continue
        print(f"\n=== {stage.label} ===", flush=True)
        print(stage_description(stage, **options), flush=True)
        result = subprocess.run(
            stage_command(
                stage,
                sys.executable,
                args.overwrite_danbooru_txt,
                args.overwrite_caption_cache,
                args.skip_wd14,
                args.add_year_tag,
                args.add_copyright_tags,
                args.overwrite_caption_files,
            ),
            cwd=ROOT,
        )
        if result.returncode:
            return result.returncode
    print(f"\nRun finished. Skipped images: {len(failures())}. Originals are kept for retry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
