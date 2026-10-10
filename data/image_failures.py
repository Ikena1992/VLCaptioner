"""Share per-image failures between subprocesses in one pipeline run."""
import json
import os
from pathlib import Path
import tempfile

ENV_KEY = "VLCAPTIONER_RUN_FAILURES"


def reset_failures():
    folder = Path(__file__).resolve().parent / "caches"
    folder.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="run_failures_", suffix=".json", dir=folder)
    with os.fdopen(handle, "w", encoding="utf-8") as output:
        output.write("{}")
    return name


def failures(path=None):
    path = path or os.environ.get(ENV_KEY)
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else {}


def image_key(image):
    return str(Path(image).resolve().with_suffix("")).casefold()


def is_skipped(image):
    return image_key(image) in failures()


def skip_image(image, error):
    path = os.environ.get(ENV_KEY)
    if path:
        records = failures(path)
        records[image_key(image)] = str(error)
        target = Path(path)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(records), encoding="utf-8")
        temporary.replace(target)
    print(f"Skipped image: {Path(image).name}: {error}. Continuing with remaining images.", flush=True)
