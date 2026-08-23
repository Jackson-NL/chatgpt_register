"""Console output helpers that tolerate legacy Windows console encodings."""

import sys
from typing import TextIO


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
