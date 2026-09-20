"""Create writable runtime databases from immutable bundled seeds."""

from __future__ import annotations

import shutil
from pathlib import Path


def seed_path(runtime_path: str | Path) -> Path:
    """Return ``name.seed.sqlite3`` for a ``name.sqlite3`` runtime path."""
    runtime_path = Path(runtime_path)
    return runtime_path.with_suffix(f".seed{runtime_path.suffix}")


def ensure_runtime_cache(runtime_path: str | Path) -> Path:
    """Copy a bundled seed when the writable runtime database is absent."""
    runtime_path = Path(runtime_path)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    bundled_seed = seed_path(runtime_path)
    if not runtime_path.exists() and bundled_seed.exists():
        with bundled_seed.open("rb") as seed_file:
            is_lfs_pointer = seed_file.read(64).startswith(
                b"version https://git-lfs.github.com/spec/v1"
            )
        if not is_lfs_pointer:
            shutil.copyfile(bundled_seed, runtime_path)
    return runtime_path
