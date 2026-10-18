"""Streaming file hashes and same-directory atomic file publication."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile


def file_hash(path, algorithm='sha256', *, chunk_size=1024 * 1024):
    """Hash file contents without retaining the whole file in memory."""
    if chunk_size < 1:
        raise ValueError('chunk_size must be positive')
    digest = hashlib.new(algorithm)
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


def publish_file(temporary, destination, *, overwrite=True):
    """Atomically consume a staged file; exclusive publication never clobbers."""
    temporary, destination = Path(temporary), Path(destination)
    if overwrite:
        temporary.replace(destination)
    elif os.name == 'nt':
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)
        temporary.unlink()


@contextmanager
def atomic_path(destination, *, overwrite=True, prefix=None):
    """Yield a private path for encoders; publish only after successful exit.

    The destination's parent must exist. Both paths are on the same filesystem.
    Failed writes, exceptions, and cancellation leave the destination intact.
    Unused temporary files are removed on a best-effort basis.
    """
    destination = Path(destination)
    descriptor, name = tempfile.mkstemp(dir=destination.parent, suffix='.tmp',
                                      prefix=prefix or f'.{destination.name}.')
    os.close(descriptor)
    temporary = Path(name)
    try:
        yield temporary
        publish_file(temporary, destination, overwrite=overwrite)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # Preserve the original error or successful publication.


@contextmanager
def atomic_output(destination, *, mode='w', encoding='utf-8', newline=None,
                  fsync=False, **options):
    """Yield a text/binary stream, close it, then atomically publish it."""
    if mode not in ('w', 'wb'):
        raise ValueError('atomic_output supports only w and wb modes')
    stream_options = {} if mode == 'wb' else {'encoding': encoding, 'newline': newline}
    with atomic_path(destination, **options) as temporary:
        with temporary.open(mode, **stream_options) as output:
            yield output
            if fsync:
                output.flush()
                os.fsync(output.fileno())


def atomic_write(destination, write, **options):
    """Run a writer callback without exposing a partially written destination."""
    with atomic_output(destination, **options) as output:
        write(output)


def atomic_write_text(destination, text, **options):
    atomic_write(destination, lambda output: output.write(text), **options)


def atomic_write_json(destination, value, *, ensure_ascii=True, indent=None,
                      trailing_newline=False, **options):
    text = json.dumps(value, ensure_ascii=ensure_ascii, indent=indent)
    atomic_write_text(destination, text + ('\n' if trailing_newline else ''), **options)
