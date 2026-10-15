import os
from pathlib import Path

import pytest

from dupesweep.scanner import ScanOptions, ScanStats, iter_files


def names(entries, root: Path) -> list[str]:
    return [e.path.relative_to(root).as_posix() for e in entries]


def test_default_scan_skips_hidden_empty_and_ignored_dirs(sample_tree):
    stats = ScanStats()
    found = names(iter_files([sample_tree], stats=stats), sample_tree)
    assert found == ["a.txt", "b.log", "docs/big.bin", "docs/readme.md"]
    assert stats.files == 4
    assert stats.bytes == 5 + 6 + 4096 + 6
    assert stats.skipped_filtered == 1  # empty.txt (min_size=1)


def test_include_hidden(sample_tree):
    found = names(iter_files([sample_tree], ScanOptions(include_hidden=True)), sample_tree)
    assert ".dotfile" in found
    assert ".hidden/secret.txt" in found


def test_min_and_max_size(sample_tree):
    opts = ScanOptions(min_size=6, max_size=100)
    assert names(iter_files([sample_tree], opts), sample_tree) == ["b.log", "docs/readme.md"]


def test_include_patterns_match_name_or_relative_path(sample_tree):
    assert names(iter_files([sample_tree], ScanOptions(include=["*.txt"])), sample_tree) == [
        "a.txt"
    ]
    assert names(iter_files([sample_tree], ScanOptions(include=["docs/*"])), sample_tree) == [
        "docs/big.bin",
        "docs/readme.md",
    ]


def test_exclude_patterns(sample_tree):
    found = names(iter_files([sample_tree], ScanOptions(exclude=["*.bin", "*.log"])), sample_tree)
    assert found == ["a.txt", "docs/readme.md"]


def test_exclude_dirs_can_be_cleared(sample_tree):
    found = names(iter_files([sample_tree], ScanOptions(exclude_dirs=[])), sample_tree)
    assert "node_modules/pkg/index.js" in found


def test_overlapping_roots_yield_each_file_once(sample_tree):
    entries = list(iter_files([sample_tree, sample_tree / "docs"]))
    paths = [e.path.resolve() for e in entries]
    assert len(paths) == len(set(paths)) == 4


def test_single_file_root(sample_tree):
    entries = list(iter_files([sample_tree / "a.txt"]))
    assert [e.path.name for e in entries] == ["a.txt"]
    assert entries[0].size == 5


def test_missing_root_is_reported_not_raised(tmp_path):
    stats = ScanStats()
    assert list(iter_files([tmp_path / "nope"], stats=stats)) == []
    assert stats.skipped_errors == 1
    assert "nope" in stats.errors[0]


def test_hard_links_are_reported_once(tmp_path):
    original = tmp_path / "orig.txt"
    original.write_text("same physical file")
    try:
        os.link(original, tmp_path / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("hard links not supported here")
    assert len(list(iter_files([tmp_path]))) == 1


def test_entries_carry_size_and_mtime(sample_tree):
    entry = next(e for e in iter_files([sample_tree]) if e.path.name == "big.bin")
    assert entry.size == 4096
    assert entry.mtime == pytest.approx(os.stat(entry.path).st_mtime)
