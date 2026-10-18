"""Durable requests shared by the downloader and captioning windows."""
from dataset_files import IMAGE_EXTENSIONS
import json
import os
import sqlite3
import uuid
import re
import filecmp
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
        if not (QUEUE_DIR / job["id"]).exists():
            owner = downloader_lock()
            if owner is not None:
                try:
                    transition(job["id"], ("Downloading",), "Stopped")
                    remove_missing(job["id"])
                finally:
                    owner.close()
            continue
        handle = download_lock(job["id"])
        if handle is None:
            continue
        try:
            folder = QUEUE_DIR / job["id"]
            count = sum(p.is_file() and p.suffix.lower() in
                        IMAGE_EXTENSIONS
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


def remove_missing(identifier):
    """Forget missing folders only when no worker owns the batch."""
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        return False
    folder = QUEUE_DIR / identifier
    if folder.exists():
        return False
    with connect() as db:
        result = db.execute("DELETE FROM jobs WHERE id=? AND status NOT IN ('Downloading', 'Running')", (identifier,))
        return result.rowcount == 1


def cleanup_completed(identifier):
    return cleanup_batch(identifier, "Completed")


def cleanup_batch(identifier, status="Cancelled"):
    """Return unused files to images/ and delete the folder and queue record."""
    if status not in ("Completed", "Cancelled"):
        return False
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        return False
    folder = QUEUE_DIR / identifier
    base = QUEUE_DIR.resolve()
    if folder.is_symlink() or folder.resolve().parent != base:
        return False
    worker = worker_lock()
    if worker is None:
        return False
    download = None
    try:
        download = downloader_lock()
        if download is None:
            return False
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                return not folder.exists()
            if row["status"] != status:
                return False
            if folder.exists():
                entries = list(folder.iterdir())
                if any(not entry.is_file() or entry.is_symlink() for entry in entries):
                    raise OSError("Batch contains a folder or link; its files were preserved.")
                destination = QUEUE_DIR.parent
                disposable = {".downloading.lock", "conditional_rules.log"}
                # Check every conflict before moving any companion file.
                for entry in entries:
                    if entry.name in disposable:
                        continue
                    target = destination / entry.name
                    if target.exists() and (target.is_symlink() or not target.is_file()
                                            or not filecmp.cmp(entry, target, shallow=False)):
                        raise FileExistsError(f"Cannot return {entry.name}: a different file already exists in images/.")
                for entry in entries:
                    if entry.name in disposable:
                        entry.unlink(missing_ok=True)
                        continue
                    target = destination / entry.name
                    if target.exists():
                        if target.is_symlink() or not target.is_file() or not filecmp.cmp(entry, target, shallow=False):
                            raise FileExistsError(f"Cannot return {entry.name}: a different file already exists in images/.")
                    else:
                        # Both paths are on the same filesystem. Publish a whole
                        # file atomically, without overwriting a racing target.
                        if os.name == "nt":
                            os.rename(entry, target)
                            continue
                        os.link(entry, target)
                    entry.unlink()
                folder.rmdir()
            db.execute("DELETE FROM jobs WHERE id=? AND status=?", (identifier, status))
        return True
    finally:
        if download:
            download.close()
        worker.close()
