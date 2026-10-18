"""Dependency-free syntax, documentation, and tracked-file checks."""

import ast
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIRS = {"tests", "test", ".test-tmp", "captionReview", ".venv"}
LOCAL_DATA = re.compile(r"(?:images|done)[0-9]*$")
SEEDS = {
    "data/caches/cache_tags.seed.sqlite3",
    "data/caches/cache_wiki.seed.sqlite3",
    "data/caches/gelbooru_tag_categories.seed.sqlite3",
    "data/caches/danbooru_character_explanationsFromVLM.seed.sqlite3",
}


def is_local_file(name):
    parts = Path(name).parts
    if not parts:
        return False
    if parts[0] in LOCAL_DIRS or LOCAL_DATA.fullmatch(parts[0]):
        return True
    if name in {"AGENTS.md", "config.txt", "data/danbooru_credentials.py", ".env"}:
        return True
    if parts[0].startswith(".env."):
        return True
    if name.startswith(("data/env/", "data/env_joy/", "data/models/",
                        "data/parquet/", "data/parquet_examples/")):
        return True
    return name.startswith("data/caches/") and name not in SEEDS


def main():
    errors = []
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    )
    tracked = result.stdout.decode("utf-8").split("\0")
    for name in filter(None, tracked):
        if is_local_file(name):
            errors.append(f"Local-only file is tracked: {name}")

    sources = sorted({*ROOT.glob("data/*.py"), *ROOT.glob("data/*.pyw"),
                      *ROOT.glob("scripts/*.py")})
    for path in sources:
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")

    documents = sorted({*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"),
                        *ROOT.glob(".github/**/*.md")})
    for path in documents:
        text = path.read_text(encoding="utf-8")
        # Code samples are not documentation hyperlinks.
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        links = re.findall(r"\]\(([^)]+)\)", text)
        links += re.findall(r'(?:src|href)="([^"]+)"', text)
        for link in links:
            url = urlsplit(link)
            if url.scheme or url.netloc or not url.path:
                continue
            target = path.parent / unquote(url.path)
            if not target.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing link {link}")

    if errors:
        print("Repository checks failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Checked {len(sources)} Python files, {len(documents)} documents, "
          "and tracked-file hygiene.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
