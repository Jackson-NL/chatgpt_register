"""Console output helpers that tolerate legacy Windows console encodings."""

import queue
import sys
import threading
from typing import TextIO


_PRINT_QUEUE: "queue.Queue[tuple[tuple[object, ...], str, str, TextIO | None, bool]]" = queue.Queue(maxsize=2000)
_PRINT_WORKER_STARTED = False
_PRINT_WORKER_LOCK = threading.Lock()


def _console_print_worker() -> None:
    while True:
        objects, sep, end, stream, flush = _PRINT_QUEUE.get()
        try:
            safe_console_print(*objects, sep=sep, end=end, stream=stream, flush=flush)
        except Exception:
            # Console output is diagnostics only.  A broken/blocked stdout must
            # never kill or back-pressure browser/OAuth work.
            pass
        finally:
            try:
                _PRINT_QUEUE.task_done()
            except Exception:
                pass


def _ensure_console_print_worker() -> None:
    global _PRINT_WORKER_STARTED
    if _PRINT_WORKER_STARTED:
        return
    with _PRINT_WORKER_LOCK:
        if _PRINT_WORKER_STARTED:
            return
        thread = threading.Thread(target=_console_print_worker, name="console-log-writer", daemon=True)
        thread.start()
        _PRINT_WORKER_STARTED = True


def enqueue_console_print(
    *objects: object,
    sep: str = " ",
    end: str = "\n",
    stream: TextIO | None = None,
    flush: bool = False,
) -> bool:
    """Queue a console line without blocking the caller.

    Used by high-volume async browser flows where stdout can be a pipe owned by
    a restarted parent process.  If that pipe stops draining, synchronous
    ``print(..., flush=True)`` can freeze the event loop before the UI receives
    live logs.  Dropping console-only output is preferable to blocking work.
    """
    _ensure_console_print_worker()
    try:
        _PRINT_QUEUE.put_nowait((objects, sep, end, stream, flush))
        return True
    except queue.Full:
        return False


def safe_console_print(
    *objects: object,
    sep: str = " ",
    end: str = "\n",
    stream: TextIO | None = None,
    flush: bool = False,
) -> None:
    """Print a log line without crashing when the console cannot encode it."""
    target = stream or sys.stdout
    text = sep.join(str(value) for value in objects) + end

    try:
        target.write(text)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        target.write(safe_text)

    if flush:
        target.flush()
