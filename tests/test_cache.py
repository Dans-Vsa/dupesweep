import os

import pytest

from conftest import write
from dupesweep.cache import HashCache, default_cache_path
from dupesweep.finder import FindStats, find_duplicates
from dupesweep.scanner import iter_files


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "data"
    write(root / "a.txt", "same")
    write(root / "b.txt", "same")
    write(root / "big1.bin", b"q" * 300_000)
    write(root / "big2.bin", b"q" * 300_000)
    return root


def entries(root):
    return list(iter_files([root]))


def test_put_get_and_invalidation(tmp_path, tree):
    a = next(e for e in entries(tree) if e.path.name == "a.txt")
    with HashCache(tmp_path / "c.sqlite") as cache:
        assert cache.get(a, "partial") is None
        cache.put(a, "partial", "abc")
        assert cache.get(a, "partial") == "abc"
        assert cache.get(a, "full") is None  # kinds are separate
        assert (cache.hits, cache.misses) == (1, 2)
        assert len(cache) == 1

    # Persisted across connections...
    with HashCache(tmp_path / "c.sqlite") as cache:
        assert cache.get(a, "partial") == "abc"
    # ...but ignored once the file changes.
    os.utime(a.path, (a.mtime + 10, a.mtime + 10))
    changed = next(e for e in entries(tree) if e.path.name == "a.txt")
    with HashCache(tmp_path / "c.sqlite") as cache:
        assert cache.get(changed, "partial") is None


def test_put_rejects_unknown_kind(tmp_path, tree):
    with HashCache(tmp_path / "c.sqlite") as cache, pytest.raises(ValueError):
        cache.put(entries(tree)[0], "sha1", "x")


def test_prune_and_clear(tmp_path, tree):
    files = entries(tree)
    with HashCache(tmp_path / "c.sqlite") as cache:
        for e in files:
            cache.put(e, "partial", "d")
        (tree / "a.txt").unlink()
        assert cache.prune() == 1
        assert len(cache) == 3
        cache.clear()
        assert len(cache) == 0


def test_find_duplicates_uses_the_cache(tmp_path, tree):
    calls = []

    def counting_full(entry):
        calls.append(entry.path.name)
        return "full-" + str(entry.size)

    with HashCache(tmp_path / "c.sqlite") as cache:
        first = FindStats()
        groups1 = find_duplicates(entries(tree), first, cache=cache, full=counting_full)
        second = FindStats()
        groups2 = find_duplicates(entries(tree), second, cache=cache, full=counting_full)

    assert [g.digest for g in groups1] == [g.digest for g in groups2]
    assert first.cache_hits == 0
    assert second.cache_hits == 6  # 4 partial + 2 full
    assert calls == ["big1.bin", "big2.bin"]  # full hashes computed only once


def test_default_cache_path_respects_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    path = default_cache_path()
    assert path.name == "hashes.sqlite" and path.parent.name == "dupesweep"
    assert path.parent.parent in (tmp_path / "local", tmp_path / "xdg")
