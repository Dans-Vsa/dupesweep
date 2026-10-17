"""File hashing used to confirm duplicates.

Two stages keep the amount of data read low:

* ``partial_hash`` reads only the first and last ``PARTIAL_BLOCK`` bytes. Files
  that differ there can never be duplicates, and most non-duplicates of the
  same size are ruled out this way.
* ``full_hash`` streams the whole file through BLAKE2b. Only files that still
  collide after the partial stage are read completely.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

PARTIAL_BLOCK = 64 * 1024
CHUNK = 1024 * 1024
DIGEST_SIZE = 32  # bytes; 256-bit BLAKE2b


def _new() -> "hashlib._Hash":
    return hashlib.blake2b(digest_size=DIGEST_SIZE)


def partial_hash(path: str | os.PathLike[str], size: int | None = None) -> str:
    """Hash of the size plus the first and last ``PARTIAL_BLOCK`` bytes."""
    path = Path(path)
    if size is None:
        size = path.stat().st_size
    digest = _new()
    digest.update(size.to_bytes(8, "little"))
    with path.open("rb") as handle:
        digest.update(handle.read(PARTIAL_BLOCK))
        if size > 2 * PARTIAL_BLOCK:
            handle.seek(-PARTIAL_BLOCK, os.SEEK_END)
            digest.update(handle.read(PARTIAL_BLOCK))
        elif size > PARTIAL_BLOCK:
            digest.update(handle.read())
    return digest.hexdigest()


def partial_reads_whole_file(size: int) -> bool:
    """True when ``partial_hash`` already covered every byte of a file this size."""
    return size <= 2 * PARTIAL_BLOCK


def full_hash(path: str | os.PathLike[str]) -> str:
    """BLAKE2b hex digest of the complete file contents."""
    digest = _new()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
