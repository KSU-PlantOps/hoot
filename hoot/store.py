"""Local trend storage in SQLite.

HOOT keeps its own history so it stays useful when it is *not* connected to a
BAS -- carried into a building, hung on a grid wire for two days, then brought
back and its trend exported. It is also the safety net for when the BAS trend
was misconfigured, which is more often than anyone likes.

SD-card notes, because these units run on flash for months:

* WAL journaling and ``synchronous=NORMAL`` cut write amplification hard versus
  the default rollback journal.
* Trend interval is deliberately decoupled from sample interval (see
  ``LoggingConfig``), so the default duty cycle is one small transaction a
  minute rather than one every five seconds.
* Retention is enforced on a schedule, and the reclaimed pages are reused
  rather than VACUUMed on every pass -- a full VACUUM rewrites the database and
  is the opposite of kind to flash.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import statistics
import time
from dataclasses import dataclass, field
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,          -- unix epoch seconds, UTC
    channel  TEXT    NOT NULL,
    value    REAL,                      -- mean over the interval when aggregating
    unit     TEXT    NOT NULL,
    min      REAL,
    max      REAL,
    samples  INTEGER NOT NULL DEFAULT 1,
    fault    TEXT                       -- non-null means the point was unreliable
);
CREATE INDEX IF NOT EXISTS idx_readings_channel_ts ON readings (channel, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    source  TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Accumulator:
    """Collects samples between trend writes so a slow trend still records the
    excursions that happened between its samples."""

    unit: str
    values: list[float] = field(default_factory=list)
    faults: list[str] = field(default_factory=list)

    def add(self, value: float) -> None:
        self.values.append(value)

    def add_fault(self, reason: str) -> None:
        self.faults.append(reason)

    def reset(self) -> None:
        self.values.clear()
        self.faults.clear()

    @property
    def empty(self) -> bool:
        return not self.values and not self.faults

    def summarise(self) -> tuple[float | None, float | None, float | None, int, str | None]:
        """-> (mean, min, max, sample_count, fault_reason)"""
        fault = self.faults[-1] if self.faults and not self.values else None
        if not self.values:
            return None, None, None, len(self.faults), fault
        return (
            statistics.fmean(self.values),
            min(self.values),
            max(self.values),
            len(self.values),
            # A partial fault is still worth recording alongside good data.
            self.faults[-1] if self.faults else None,
        )


class Store:
    """Thin SQLite wrapper. One connection, used from the service loop."""

    def __init__(self, path: str | Path, retention_days: int = 90) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self._conn: sqlite3.Connection | None = None
        self._last_purge = 0.0

    # ---- lifecycle --------------------------------------------------------

    def open(self) -> None:
        if self._conn is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self.path,
            timeout=10.0,
            # The web UI reads on a different thread from the sampling loop.
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
        self._conn = conn
        log.info("trend store open at %s (retention %d days)", self.path, self.retention_days)

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.commit()
                self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def __enter__(self) -> "Store":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- writing ----------------------------------------------------------

    def write_batch(self, rows: Iterable[tuple], timestamp: float | None = None) -> int:
        """Insert trend rows in one transaction.

        Each row: (channel, unit, value, min, max, samples, fault)
        """
        ts = timestamp if timestamp is not None else time.time()
        payload = [(ts, ch, val, unit, lo, hi, n, fault)
                   for (ch, unit, val, lo, hi, n, fault) in rows]
        if not payload:
            return 0
        with self.conn:
            self.conn.executemany(
                "INSERT INTO readings (ts, channel, value, unit, min, max, samples, fault) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
        return len(payload)

    def log_event(self, level: str, source: str, message: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO events (ts, level, source, message) VALUES (?, ?, ?, ?)",
                (time.time(), level, source, message[:1000]),
            )

    # ---- reading ----------------------------------------------------------

    def history(
        self,
        channel: str,
        since: float | None = None,
        until: float | None = None,
        limit: int = 5000,
        bucket_seconds: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Trend rows for one channel, oldest first.

        With ``bucket_seconds`` set, rows are merged into buckets of that width
        (mean of means, min of mins, max of maxes) so a long window still spans
        its full range in ``limit`` points. Without it, a 30-day chart would get
        only the most recent ``limit`` rows -- about a day and a half at 60 s.
        """
        where = "channel = ?"
        args: list[Any] = [channel]
        if since is not None:
            where += " AND ts >= ?"
            args.append(since)
        if until is not None:
            where += " AND ts <= ?"
            args.append(until)

        if bucket_seconds > 0:
            sql = (
                "SELECT AVG(ts) ts, AVG(value) value, "
                "MIN(COALESCE(min, value)) min, MAX(COALESCE(max, value)) max, "
                "SUM(samples) samples, MAX(unit) unit, MAX(fault) fault "
                f"FROM readings WHERE {where} "
                "GROUP BY CAST(ts / ? AS INTEGER) ORDER BY ts DESC LIMIT ?"
            )
            args.append(bucket_seconds)
            # A window that is not bucket-aligned straddles one extra bucket;
            # allow for it so the oldest partial bucket is not the one dropped.
            limit += 1
        else:
            sql = ("SELECT ts, value, min, max, samples, unit, fault "
                   f"FROM readings WHERE {where} ORDER BY ts DESC LIMIT ?")
        args.append(limit)
        rows = self.conn.execute(sql, args).fetchall()
        return [dict(r) for r in reversed(rows)]

    def channels(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT channel FROM readings ORDER BY channel")
        return [r[0] for r in rows]

    def latest(self, channel: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT ts, value, min, max, unit, fault FROM readings "
            "WHERE channel = ? ORDER BY ts DESC LIMIT 1",
            (channel,),
        ).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT COUNT(*) n, MIN(ts) first, MAX(ts) last FROM readings"
        ).fetchone()
        size = self.path.stat().st_size if self.path.exists() else 0
        return {
            "rows": row["n"],
            "first_ts": row["first"],
            "last_ts": row["last"],
            "bytes": size,
            "channels": self.channels(),
        }

    def export_csv(
        self,
        channels: list[str] | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> Iterator[str]:
        """Stream CSV rows. Generator so a 90-day export never has to fit in RAM
        on a Pi serving it over HTTP."""
        sql = ("SELECT ts, channel, value, min, max, samples, unit, fault "
               "FROM readings WHERE 1=1")
        args: list[Any] = []
        if channels:
            sql += f" AND channel IN ({','.join('?' * len(channels))})"
            args.extend(channels)
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        if until is not None:
            sql += " AND ts <= ?"
            args.append(until)
        sql += " ORDER BY ts"

        yield "timestamp_iso,timestamp_epoch,channel,value,min,max,samples,unit,fault\n"
        for row in self.conn.execute(sql, args):
            iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(row["ts"]))
            fault = (row["fault"] or "").replace(",", ";").replace("\n", " ")
            yield (
                f'{iso}Z,{row["ts"]:.3f},{row["channel"]},'
                f'{_num(row["value"])},{_num(row["min"])},{_num(row["max"])},'
                f'{row["samples"]},{row["unit"]},{fault}\n'
            )

    # ---- maintenance ------------------------------------------------------

    def purge(self, force: bool = False) -> int:
        """Delete rows past the retention window. Returns rows removed."""
        if self.retention_days <= 0:
            return 0
        now = time.time()
        # Once an hour is plenty; this is called from the sampling loop.
        if not force and (now - self._last_purge) < 3600:
            return 0
        self._last_purge = now
        cutoff = now - self.retention_days * 86_400
        with self.conn:
            cur = self.conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        removed = cur.rowcount or 0
        if removed:
            log.info("purged %d trend rows older than %d days", removed, self.retention_days)
        return removed

    def vacuum(self) -> None:
        """Reclaim file space. Explicit and manual: a VACUUM rewrites the whole
        database, which is expensive on an SD card, so it is never automatic."""
        self.conn.execute("VACUUM")


def _num(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"
