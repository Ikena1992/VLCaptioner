"""Durable requests shared by the downloader and captioning windows."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from runtime_config import PROJECT_DIR

QUEUE_DIR = PROJECT_DIR / "images" / ".caption-queue"
DB = PROJECT_DIR / "data" / "caches" / "caption_queue.sqlite3"


class QueueSubmissionError(Exception):
    """Stop downloads when their captioning requests cannot be saved."""


@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, label TEXT, count INTEGER, status TEXT, options TEXT)")
            yield db
    finally:
        db.close()


def new_batch(label, own_download=False):
    identifier = uuid.uuid4().hex
    folder = QUEUE_DIR / identifier
    folder.mkdir(parents=True)
    handle = download_lock(identifier) if own_download else None
    try:
        with connect() as db:
            db.execute("INSERT INTO jobs VALUES (?, ?, 0, 'Downloading', NULL)", (identifier, label))
    except Exception:
        if handle:
            handle.close()
        raise
    return (identifier, folder, handle) if own_download else (identifier, folder)


def update(identifier, status, count=None, options=None):
    with connect() as db:
        db.execute("UPDATE jobs SET status=?, count=COALESCE(?,count), options=COALESCE(?,options) WHERE id=?",
                   (status, count, json.dumps(options) if options is not None else None, identifier))


def jobs():
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY rowid")]


def file_lock(path):
    """Return an OS lock, released automatically when its owner exits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                handle.close()
                return None
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return None
        return handle
    except Exception:
        handle.close()
        raise


def worker_lock():
    """Prevent simultaneous model pipelines across caption windows."""
    return file_lock(DB.parent / "caption_worker.lock")


def downloader_lock():
    """Serialize download windows to prevent duplicate bundles in separate batches."""
    return file_lock(DB.parent / "downloader_worker.lock")


def download_lock(identifier):
    return file_lock(QUEUE_DIR / identifier / ".downloading.lock")


def recover_downloads():
    """Recover published images after a downloader exits unexpectedly."""
    for job in jobs():
        if job["status"] != "Downloading":
            continue
        handle = download_lock(job["id"])
        if handle is None:
            continue
        try:
            folder = QUEUE_DIR / job["id"]
            count = sum(p.is_file() and p.suffix.lower() in
                        {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
                        for p in folder.iterdir())
            transition(job["id"], ("Downloading",), "Stopped" if count else "Cancelled", count)
        finally:
            handle.close()


def transition(identifier, expected, status, count=None, options=None):
    """Change only the states the caller observed, avoiding stale UI actions."""
    with connect() as db:
        placeholders = ",".join("?" for _ in expected)
        result = db.execute(f"UPDATE jobs SET status=?, count=COALESCE(?,count), options=COALESCE(?,options) WHERE id=? AND status IN ({placeholders})",
                            (status, count, json.dumps(options) if options is not None else None, identifier, *expected))
        return result.rowcount == 1
