"""Persistent hash cache in SQLite.

A cached hash is reused only while the file's path, size and modification time
are unchanged, so edited files are always hashed again. The cache is only ever
touched from the calling thread; worker threads just compute hashes.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from dupesweep.scanner import FileEntry

KINDS = ("partial", "full")
SCHEMA = """
CREATE TABLE IF NOT EXISTS hashes (
    path    TEXT NOT NULL,
    kind    TEXT NOT NULL,
    size    INTEGER NOT NULL,
    mtime   REAL NOT NULL,
    digest  TEXT NOT NULL,
    PRIMARY KEY (path, kind)
)
"""


def default_cache_path() -> Path:
    """``%LOCALAPPDATA%\\dupesweep`` on Windows, ``$XDG_CACHE_HOME`` or ``~/.cache`` elsewhere."""
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    elif os.environ.get("XDG_CACHE_HOME"):
        base = Path(os.environ["XDG_CACHE_HOME"])
    else:
        base = Path.home() / ".cache"
    return base / "dupesweep" / "hashes.sqlite"


def _key(entry: FileEntry) -> str:
    return str(entry.path.resolve())


class HashCache:
    """``get``/``put`` hashes for scanned files; use as a context manager."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.execute(SCHEMA)
        self.hits = 0
        self.misses = 0

    def get(self, entry: FileEntry, kind: str) -> str | None:
        row = self._db.execute(
            "SELECT size, mtime, digest FROM hashes WHERE path = ? AND kind = ?",
            (_key(entry), kind),
        ).fetchone()
        if row and row[0] == entry.size and row[1] == entry.mtime:
            self.hits += 1
            return row[2]
        self.misses += 1
        return None

    def put(self, entry: FileEntry, kind: str, digest: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown hash kind {kind!r}")
        self._db.execute(
            "INSERT OR REPLACE INTO hashes (path, kind, size, mtime, digest) VALUES (?, ?, ?, ?, ?)",
            (_key(entry), kind, entry.size, entry.mtime, digest),
        )

    def __len__(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM hashes").fetchone()[0]

    def prune(self) -> int:
        """Drop entries for files that no longer exist; returns how many were removed."""
        paths = [row[0] for row in self._db.execute("SELECT DISTINCT path FROM hashes")]
        gone = [(p,) for p in paths if not os.path.exists(p)]
        self._db.executemany("DELETE FROM hashes WHERE path = ?", gone)
        self._db.commit()
        return len(gone)

    def clear(self) -> None:
        self._db.execute("DELETE FROM hashes")
        self._db.commit()

    def close(self) -> None:
        self._db.commit()
        self._db.close()

    def __enter__(self) -> "HashCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
