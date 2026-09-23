"""SQLite-backed caches shared by the Danbooru processing scripts."""

from __future__ import annotations

import sqlite3
from contextlib import closing
import re
from pathlib import Path

from cache_seed import ensure_runtime_cache


class TagCategoryCache:
    def __init__(self, database_path: str | Path):
        self.database_path = ensure_runtime_cache(database_path)
        self._initialize()

    def _initialize(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute(
                "CREATE TABLE IF NOT EXISTS tag_categories ("
                "tag TEXT PRIMARY KEY, category INTEGER)"
            )

    def get(self, tag: str, default=None):
        with closing(sqlite3.connect(self.database_path)) as database, database:
            matches = []
            for candidate in self._candidate_keys(tag):
                row = database.execute(
                    "SELECT category FROM tag_categories WHERE tag = ?",
                    (candidate,),
                ).fetchone()
                if row is not None:
                    matches.append(row[0])
            # A legacy cache can contain duplicate spellings with conflicting
            # values. Prefer the character category when any spelling confirms it.
            if 4 in matches:
                return 4
            if matches:
                return matches[0]
        return default

    @staticmethod
    def _candidate_keys(tag: str) -> tuple[str, ...]:
        """Handle escaped TXT tags and normal cache spelling differences."""
        raw = tag.strip()
        unescaped = raw.replace(r"\(", "(").replace(r"\)", ")")
        canonical = normalize_cache_key(raw)
        canonical_danbooru = canonical.replace(" ", "_")
        candidates = (
            raw, unescaped, raw.lower(), unescaped.lower(),
            canonical, canonical_danbooru,
        )
        return tuple(dict.fromkeys(candidates))

    def __contains__(self, tag: str) -> bool:
        return self.get(tag, None) is not None

    def __getitem__(self, tag: str):
        value = self.get(tag, None)
        if value is None:
            raise KeyError(tag)
        return value

    def __setitem__(self, tag: str, category) -> None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            database.execute(
                "INSERT OR REPLACE INTO tag_categories(tag, category) VALUES (?, ?)",
                (normalize_cache_key(tag), category),
            )

    def pop(self, tag: str, default=None):
        value = self.get(tag, default)
        with closing(sqlite3.connect(self.database_path)) as database, database:
            database.execute(
                "DELETE FROM tag_categories WHERE tag = ?",
                (normalize_cache_key(tag),),
            )
        return value

def normalize_cache_key(tag: str) -> str:
    """Return one shared key for equivalent Danbooru/TXT tag spellings."""
    tag = tag.strip().lower()
    tag = tag.replace(r"\(", "(").replace(r"\)", ")")
    tag = tag.replace("_", " ")
    return re.sub(r"\s+", " ", tag)
