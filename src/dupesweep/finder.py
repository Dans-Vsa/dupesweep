"""Group scanned files into sets of byte-identical duplicates.

Pipeline: group by size -> partial hash -> full hash. Each stage drops groups
that have only one member left, so most files are never read at all.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dupesweep.hasher import full_hash, partial_hash, partial_reads_whole_file
from dupesweep.scanner import FileEntry

if TYPE_CHECKING:
    from dupesweep.cache import HashCache

HashFunc = Callable[[FileEntry], str]
# progress(stage, done, total) is called from the calling thread only.
ProgressFunc = Callable[[str, int, int], None]


@dataclass(frozen=True)
class DuplicateGroup:
    """Two or more files with identical contents."""

    size: int
    digest: str
    files: tuple[FileEntry, ...]

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def wasted(self) -> int:
        """Bytes that would be freed by keeping only one copy."""
        return self.size * (self.count - 1)

    def to_dict(self) -> dict:
        return {
            "size": self.size,
            "count": self.count,
            "wasted": self.wasted,
            "digest": self.digest,
            "files": [str(f.path) for f in self.files],
        }


@dataclass
class FindStats:
    """How much work each stage did."""

    files: int = 0
    size_candidates: int = 0
    partial_hashed: int = 0
    full_hashed: int = 0
    bytes_hashed: int = 0
    cache_hits: int = 0
    errors: list[str] = field(default_factory=list)


def _group(entries: Iterable[FileEntry], key) -> list[list[FileEntry]]:
    buckets: dict[object, list[FileEntry]] = defaultdict(list)
    for entry in entries:
        buckets[key(entry)].append(entry)
    return [bucket for bucket in buckets.values() if len(bucket) > 1]


def _safe(func: HashFunc, entry: FileEntry) -> str | OSError:
    try:
        return func(entry)
    except OSError as exc:
        return exc


def _hash_all(
    entries: list[FileEntry],
    func: HashFunc,
    stats: FindStats,
    *,
    kind: str,
    jobs: int = 1,
    cache: HashCache | None = None,
    progress: ProgressFunc | None = None,
) -> dict[FileEntry, str]:
    """Hash ``entries`` (cache first, then ``jobs`` worker threads for the rest)."""
    digests: dict[FileEntry, str] = {}
    todo: list[FileEntry] = []
    for entry in entries:
        cached = cache.get(entry, kind) if cache is not None else None
        if cached is None:
            todo.append(entry)
        else:
            digests[entry] = cached
            stats.cache_hits += 1

    total, done = len(entries), len(digests)
    if progress and total:
        progress(kind, done, total)
    if jobs > 1 and len(todo) > 1:
        pool = ThreadPoolExecutor(max_workers=jobs)
        results = pool.map(lambda e: _safe(func, e), todo)
    else:
        pool = None
        results = (_safe(func, e) for e in todo)
    try:
        for entry, result in zip(todo, results):
            done += 1
            if isinstance(result, OSError):
                stats.errors.append(f"{entry.path}: {result.strerror or result}")
            else:
                digests[entry] = result
                if cache is not None:
                    cache.put(entry, kind, result)
            if progress:
                progress(kind, done, total)
    finally:
        if pool:
            pool.shutdown()
    return digests


def default_partial(entry: FileEntry) -> str:
    return partial_hash(entry.path, entry.size)


def default_full(entry: FileEntry) -> str:
    return full_hash(entry.path)


def find_duplicates(
    entries: Iterable[FileEntry],
    stats: FindStats | None = None,
    *,
    partial: HashFunc = default_partial,
    full: HashFunc = default_full,
    jobs: int = 1,
    cache: HashCache | None = None,
    progress: ProgressFunc | None = None,
) -> list[DuplicateGroup]:
    """Return duplicate groups, largest wasted space first.

    Files inside a group are sorted by path. ``jobs`` > 1 hashes files in worker
    threads (hashing is I/O bound and ``hashlib`` releases the GIL). ``cache``
    reuses digests of unchanged files. Unreadable files are skipped and reported
    in ``stats.errors``.
    """
    stats = stats if stats is not None else FindStats()
    entries = list(entries)
    stats.files = len(entries)

    size_groups = _group(entries, lambda e: e.size)
    candidates = [e for group in size_groups for e in group]
    stats.size_candidates = len(candidates)

    opts = {"jobs": jobs, "cache": cache, "progress": progress}
    partial_digests = _hash_all(candidates, partial, stats, kind="partial", **opts)
    stats.partial_hashed = len(partial_digests)
    partial_groups = _group(partial_digests, lambda e: (e.size, partial_digests[e]))

    # Small files were read completely by the partial stage already.
    to_full = [
        e for group in partial_groups for e in group if not partial_reads_whole_file(e.size)
    ]
    full_digests = _hash_all(to_full, full, stats, kind="full", **opts)
    stats.full_hashed = len(full_digests)
    stats.bytes_hashed = sum(e.size for e in full_digests)

    groups: list[DuplicateGroup] = []
    for group in partial_groups:
        if partial_reads_whole_file(group[0].size):
            confirmed = [(partial_digests[group[0]], group)]
        else:
            by_full = _group([e for e in group if e in full_digests], lambda e: full_digests[e])
            confirmed = [(full_digests[g[0]], g) for g in by_full]
        for digest, files in confirmed:
            ordered = tuple(sorted(files, key=lambda e: str(e.path)))
            groups.append(DuplicateGroup(size=ordered[0].size, digest=digest, files=ordered))

    groups.sort(key=lambda g: (-g.wasted, -g.count, str(g.files[0].path)))
    return groups
