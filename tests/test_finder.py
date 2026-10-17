from pathlib import Path

import pytest

from conftest import write
from dupesweep.finder import FindStats, default_partial, find_duplicates
from dupesweep.hasher import PARTIAL_BLOCK
from dupesweep.scanner import iter_files


@pytest.fixture
def dupe_tree(tmp_path: Path) -> Path:
    """Three small copies, two large copies, a same-size-but-different large file."""
    big = b"".join(i.to_bytes(4, "little") for i in range(PARTIAL_BLOCK))  # 4 blocks
    write(tmp_path / "a.txt", "hello")
    write(tmp_path / "b.txt", "hello")
    write(tmp_path / "sub" / "c.txt", "hello")
    write(tmp_path / "unique.txt", "world")  # same size as "hello", different bytes
    write(tmp_path / "big1.bin", big)
    write(tmp_path / "copy" / "big2.bin", big)
    changed = bytearray(big)
    changed[len(big) // 2] ^= 0xFF  # differs only in the middle
    write(tmp_path / "big3.bin", bytes(changed))
    write(tmp_path / "lonely.dat", "no twin here at all")
    return tmp_path


def names(group):
    return [f.path.name for f in group.files]


def test_find_duplicates(dupe_tree):
    stats = FindStats()
    groups = find_duplicates(iter_files([dupe_tree]), stats)
    assert [names(g) for g in groups] == [["big1.bin", "big2.bin"], ["a.txt", "b.txt", "c.txt"]]
    big, small = groups
    assert big.wasted == 4 * PARTIAL_BLOCK and big.count == 2
    assert small.wasted == 10 and small.size == 5
    assert len(big.digest) == 64


def test_find_duplicates_stats(dupe_tree):
    stats = FindStats()
    find_duplicates(iter_files([dupe_tree]), stats)
    assert stats.files == 8
    assert stats.size_candidates == 7  # everything except lonely.dat
    assert stats.partial_hashed == 7
    # Only the three large files share a partial hash; the small ones were fully read.
    assert stats.full_hashed == 3
    assert stats.bytes_hashed == 3 * 4 * PARTIAL_BLOCK


def test_no_duplicates(tmp_path):
    write(tmp_path / "a", "1")
    write(tmp_path / "b", "22")
    assert find_duplicates(iter_files([tmp_path])) == []
    assert find_duplicates([]) == []


def test_unreadable_files_are_reported_not_fatal(dupe_tree):
    def failing_partial(entry):
        if entry.path.name == "b.txt":
            raise PermissionError(13, "Permission denied")
        return default_partial(entry)

    stats = FindStats()
    groups = find_duplicates(iter_files([dupe_tree]), stats, partial=failing_partial)
    assert ["a.txt", "c.txt"] in [names(g) for g in groups]
    assert len(stats.errors) == 1 and "b.txt" in stats.errors[0]


def test_custom_hash_functions_are_used(dupe_tree):
    calls = []

    def fake_full(entry):
        calls.append(entry.path.name)
        return "same"

    groups = find_duplicates(iter_files([dupe_tree]), full=fake_full)
    # With a full hash that always matches, big3 joins the big group.
    assert names(groups[0]) == ["big1.bin", "big3.bin", "big2.bin"]  # sorted by full path
    assert sorted(calls) == ["big1.bin", "big2.bin", "big3.bin"]
    assert groups[0].digest == "same"


def test_to_dict(dupe_tree):
    d = find_duplicates(iter_files([dupe_tree]))[1].to_dict()
    assert d["count"] == 3 and d["wasted"] == 10 and d["size"] == 5
    assert [Path(p).name for p in d["files"]] == ["a.txt", "b.txt", "c.txt"]


def test_parallel_hashing_gives_the_same_result(dupe_tree):
    serial = find_duplicates(iter_files([dupe_tree]), jobs=1)
    parallel = find_duplicates(iter_files([dupe_tree]), jobs=4)
    assert serial == parallel


def test_progress_callback(dupe_tree):
    events = []
    find_duplicates(iter_files([dupe_tree]), jobs=2,
                    progress=lambda stage, done, total: events.append((stage, done, total)))
    partial = [e for e in events if e[0] == "partial"]
    full = [e for e in events if e[0] == "full"]
    assert partial[0] == ("partial", 0, 7) and partial[-1] == ("partial", 7, 7)
    assert full[-1] == ("full", 3, 3)
    assert [d for _, d, _ in partial] == sorted(d for _, d, _ in partial)


def test_parallel_errors_are_collected(dupe_tree):
    def flaky(entry):
        if entry.path.name == "big1.bin":
            raise OSError(5, "I/O error")
        return "x"

    stats = FindStats()
    groups = find_duplicates(iter_files([dupe_tree]), stats, full=flaky, jobs=3)
    assert any("big1.bin" in e for e in stats.errors)
    # big2 and big3 now "match" via the fake hash; big1 dropped out.
    assert ["big3.bin", "big2.bin"] in [names(g) for g in groups]
