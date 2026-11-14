#!/usr/bin/env python3
"""Replace quality tags with one masterpiece tag in caption files.

Usage:
    python data/set_masterpiece.py done
    python data/set_masterpiece.py done --recursive --extension .txt

Files are updated in place. The default extension is .tag.
"""

import argparse
from pathlib import Path
from file_io import atomic_path


QUALITY_TAGS = {
    "masterpiece", "best quality", "high quality", "good quality",
    "medium quality", "normal quality", "low quality", "worst quality",
    "very aesthetic", "aesthetic", "displeasing", "very displeasing",
}


def set_masterpiece(text):
    tags = [tag.strip() for tag in text.split(",") if tag.strip()]
    remaining = [
        tag for tag in tags
        if " ".join(tag.casefold().replace("_", " ").split()) not in QUALITY_TAGS
    ]
    newline = "\r\n" if text.endswith("\r\n") else "\n" if text.endswith("\n") else "\r" if text.endswith("\r") else ""
    return ", ".join(["masterpiece", *remaining]) + newline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Folder containing caption files")
    parser.add_argument("--recursive", action="store_true", help="Include subfolders")
    parser.add_argument("--extension", default=".tag", choices=(".tag", ".txt"))
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error(f"Folder does not exist: {args.folder}")

    paths = args.folder.rglob("*") if args.recursive else args.folder.iterdir()
    changed = scanned = errors = 0
    for path in paths:
        if not path.is_file() or path.suffix.lower() != args.extension:
            continue
        scanned += 1
        try:
            if path.is_symlink():
                raise OSError('Refusing to modify a symbolic link')
            original = path.read_bytes()
            # Preserve an existing UTF-8 BOM and the trailing newline style.
            encoding = "utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8"
            updated = set_masterpiece(original.decode(encoding)).encode(encoding)
            if updated != original:
                with atomic_path(path) as temporary:
                    temporary.write_bytes(updated)
                    if path.read_bytes() != original:
                        raise OSError('Caption changed while processing; keeping current file')
                changed += 1
        except (OSError, UnicodeError) as exc:
            print(f"Error: {path}: {exc}")
            errors += 1
    print(f"Updated {changed} of {scanned} files; {errors} errors.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
