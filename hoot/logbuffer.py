"""In-memory copy of recent log output, for the web UI's Logs tab.

On a Pi the service log goes to journald, which a technician holding only a
laptop and a browser cannot reach. This keeps the last few thousand lines in
memory so the web UI can show and download them.

It is lost on restart. The persistent record is the ``events`` table in the
trend store (downloadable from the web UI) and ``journalctl -u hoot``.
"""

from __future__ import annotations

import collections
import logging
import threading
from typing import Any

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"

#: Roughly a day of normal operation, or a few minutes of a noisy fault storm,
#: in well under a megabyte.
CAPACITY = 5000


class RingBufferHandler(logging.Handler):
    """Keeps the most recent ``capacity`` formatted records."""

    def __init__(self, capacity: int = CAPACITY) -> None:
        super().__init__()
        self._records: collections.deque[dict[str, Any]] = collections.deque(maxlen=capacity)
        self._guard = threading.Lock()
        self.setFormatter(logging.Formatter(FORMAT, DATEFMT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            self.handleError(record)
            return
        entry = {"ts": record.created, "level": record.levelname,
                 "levelno": record.levelno, "logger": record.name, "line": line}
        with self._guard:
            self._records.append(entry)

    def entries(self, min_level: int = logging.NOTSET, limit: int | None = None) -> list[dict[str, Any]]:
        """Oldest first, filtered to ``min_level`` and above, newest ``limit``."""
        with self._guard:
            items = [e for e in self._records if e["levelno"] >= min_level]
        return items[-limit:] if limit else items

    def text(self, min_level: int = logging.NOTSET) -> str:
        return "".join(e["line"] + "\n" for e in self.entries(min_level))

    def clear(self) -> None:
        with self._guard:
            self._records.clear()


#: The process-wide buffer. One per process, like the root logger it hangs off.
BUFFER = RingBufferHandler()


def install() -> RingBufferHandler:
    """Attach the buffer to the root logger. Idempotent."""
    root = logging.getLogger()
    if BUFFER not in root.handlers:
        root.addHandler(BUFFER)
    return BUFFER
