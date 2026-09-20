"""Command-line runner for the shared VLTagger pipeline."""

import argparse
import subprocess
import sys

from pipeline import ROOT, STAGES, stage_command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-wd14",
        action="store_true",
        help="Skip adding high-confidence WD14 tags to existing TXT files.",
    )
    parser.add_argument("--overwrite-danbooru-txt", action="store_true")
    parser.add_argument("--overwrite-caption-cache", action="store_true")
    args = parser.parse_args()
    for stage in STAGES:
        print(f"\n=== {stage.label} ===", flush=True)
        result = subprocess.run(
            stage_command(
                stage,
                sys.executable,
                args.overwrite_danbooru_txt,
                args.overwrite_caption_cache,
                args.skip_wd14,
            ),
            cwd=ROOT,
        )
        if result.returncode:
            return result.returncode
    print("\nAll scripts ran successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
