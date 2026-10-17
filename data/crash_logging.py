"""Persistent GUI exception and fatal-crash diagnostics (no activity logging)."""

import faulthandler
import os
from pathlib import Path
import sys
import threading
import traceback
from datetime import datetime

LOG_PATH = Path(__file__).resolve().parent / "caches" / "crashes.log"
_stream = None
_lock = threading.Lock()


def record_exception(kind, exc_type, exc_value, exc_traceback):
    """Write and flush immediately, including when pythonw has no stderr."""
    install()
    with _lock:
        _stream.write(f"\n[{datetime.now().astimezone().isoformat()}] "
                      f"PID {os.getpid()}: {kind}\n")
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=_stream)
        _stream.flush()


def install():
    """Install once, retaining the open descriptor for faulthandler."""
    global _stream
    if _stream is not None:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _stream = LOG_PATH.open("a", encoding="utf-8", buffering=1)
    _stream.write(f"\n[{datetime.now().astimezone().isoformat()}] "
                  f"Crash diagnostics enabled; PID {os.getpid()}; "
                  f"Python {sys.version}; executable {sys.executable}\n")
    _stream.flush()
    faulthandler.enable(file=_stream, all_threads=True)

    previous_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def exception_hook(exc_type, exc_value, exc_traceback):
        record_exception("Unhandled exception", exc_type, exc_value, exc_traceback)
        if sys.stderr is not None:
            previous_hook(exc_type, exc_value, exc_traceback)

    def thread_hook(args):
        if args.exc_type is not SystemExit:
            record_exception(f"Unhandled thread exception ({args.thread.name})",
                             args.exc_type, args.exc_value, args.exc_traceback)
        if sys.stderr is not None:
            previous_thread_hook(args)

    sys.excepthook = exception_hook
    threading.excepthook = thread_hook


def attach_tk(root):
    """Tk catches callback exceptions before they reach sys.excepthook."""
    root.report_callback_exception = lambda *args: record_exception("Tk callback exception", *args)
