"""Walk directory trees and yield regular files that match the scan filters."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileEntry:
    """A regular file discovered during a scan."""

    path: Path
    size: int
    mtime: float
    inode: tuple[int, int]  # (st_dev, st_ino), used to spot hard links


@dataclass
class ScanOptions:
    """Filters applied while walking the filesystem.

    ``include``/``exclude`` are glob patterns matched against the file name
    and against the path relative to the scan root (using ``/`` separators).
    ``exclude_dirs`` patterns prune whole directories by name.
    """

    min_size: int = 1
    max_size: int | None = None
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    exclude_dirs: list[str] = field(
        default_factory=lambda: [".git", "node_modules", "__pycache__", ".venv"]
    )
    include_hidden: bool = False
    follow_symlinks: bool = False


@dataclass
class ScanStats:
    """Counters collected while scanning, useful for the final summary."""

    files: int = 0
    bytes: int = 0
    skipped_filtered: int = 0
    skipped_errors: int = 0
    errors: list[str] = field(default_factory=list)


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _matches_any(name: str, rel_path: str, patterns: Iterable[str]) -> bool:
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel_path, pattern)
        for pattern in patterns
    )


def _passes_filters(entry: os.DirEntry, rel_path: str, size: int, options: ScanOptions) -> bool:
    if size < options.min_size:
        return False
    if options.max_size is not None and size > options.max_size:
        return False
    if options.include and not _matches_any(entry.name, rel_path, options.include):
        return False
    if options.exclude and _matches_any(entry.name, rel_path, options.exclude):
        return False
    return True


def iter_files(
    roots: Iterable[str | os.PathLike[str]],
    options: ScanOptions | None = None,
    stats: ScanStats | None = None,
) -> Iterator[FileEntry]:
    """Yield every regular file under ``roots`` that passes ``options``.

    The same physical file (same device + inode) is only yielded once, so
    overlapping roots or followed symlinks never produce fake duplicates.
    Unreadable directories and files are counted in ``stats`` and skipped.
    """
    options = options or ScanOptions()
    stats = stats if stats is not None else ScanStats()
    seen_inodes: set[tuple[int, int]] = set()
    seen_dirs: set[tuple[int, int]] = set()

    for root in roots:
        root_path = Path(root)
        if root_path.is_file():
            entry = _entry_for_single_file(root_path, stats)
            if entry and entry.inode not in seen_inodes and entry.size >= options.min_size:
                seen_inodes.add(entry.inode)
                stats.files += 1
                stats.bytes += entry.size
                yield entry
            continue

        stack = [root_path]
        while stack:
            directory = stack.pop()
            try:
                dir_stat = directory.stat()
                dir_key = (dir_stat.st_dev, dir_stat.st_ino)
                if dir_key in seen_dirs and dir_key != (0, 0):
                    continue
                seen_dirs.add(dir_key)
                with os.scandir(directory) as it:
                    children = sorted(it, key=lambda e: e.name)
            except OSError as exc:
                stats.skipped_errors += 1
                stats.errors.append(f"{directory}: {exc.strerror or exc}")
                continue

            subdirs: list[Path] = []
            for child in children:
                if not options.include_hidden and _is_hidden(child.name):
                    continue
                try:
                    if child.is_dir(follow_symlinks=options.follow_symlinks):
                        if not _matches_any(child.name, child.name, options.exclude_dirs):
                            subdirs.append(Path(child.path))
                        continue
                    if not child.is_file(follow_symlinks=options.follow_symlinks):
                        continue
                    st = child.stat(follow_symlinks=options.follow_symlinks)
                    if st.st_ino == 0:
                        # On Windows DirEntry.stat() leaves inode/device unset.
                        st = os.stat(child.path, follow_symlinks=options.follow_symlinks)
                except OSError as exc:
                    stats.skipped_errors += 1
                    stats.errors.append(f"{child.path}: {exc.strerror or exc}")
                    continue

                rel_path = Path(child.path).relative_to(root_path).as_posix()
                if not _passes_filters(child, rel_path, st.st_size, options):
                    stats.skipped_filtered += 1
                    continue

                inode = (st.st_dev, st.st_ino)
                if inode != (0, 0) and inode in seen_inodes:
                    continue
                seen_inodes.add(inode)
                stats.files += 1
                stats.bytes += st.st_size
                yield FileEntry(Path(child.path), st.st_size, st.st_mtime, inode)

            # Reverse so directories are visited in alphabetical order.
            stack.extend(reversed(subdirs))


def _entry_for_single_file(path: Path, stats: ScanStats) -> FileEntry | None:
    try:
        st = path.stat()
    except OSError as exc:
        stats.skipped_errors += 1
        stats.errors.append(f"{path}: {exc.strerror or exc}")
        return None
    return FileEntry(path, st.st_size, st.st_mtime, (st.st_dev, st.st_ino))
