import hashlib

import pytest

from conftest import write
from dupesweep.hasher import (
    PARTIAL_BLOCK,
    full_hash,
    partial_hash,
    partial_reads_whole_file,
)


def blake(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def test_full_hash_matches_hashlib(tmp_path):
    data = b"abc" * 500_000  # several read chunks
    path = write(tmp_path / "f.bin", data)
    assert full_hash(path) == blake(data)


def test_full_hash_of_empty_file(tmp_path):
    assert full_hash(write(tmp_path / "empty", b"")) == blake(b"")


@pytest.mark.parametrize("size", [0, 1, PARTIAL_BLOCK, PARTIAL_BLOCK + 1, 2 * PARTIAL_BLOCK])
def test_partial_hash_covers_small_files_completely(tmp_path, size):
    a = write(tmp_path / "a", b"x" * size)
    b = write(tmp_path / "b", b"x" * size)
    assert partial_hash(a) == partial_hash(b)
    assert partial_reads_whole_file(size)
    if size:
        # Changing the very last byte must change the partial hash.
        c = write(tmp_path / "c", b"x" * (size - 1) + b"y")
        assert partial_hash(c) != partial_hash(a)


def test_partial_hash_samples_head_and_tail_of_large_files(tmp_path):
    size = 3 * PARTIAL_BLOCK
    base = bytearray(b"a" * size)
    middle = bytearray(base)
    middle[size // 2] = ord("b")
    tail = bytearray(base)
    tail[-1] = ord("b")
    pa = write(tmp_path / "base", bytes(base))
    pm = write(tmp_path / "middle", bytes(middle))
    pt = write(tmp_path / "tail", bytes(tail))
    assert not partial_reads_whole_file(size)
    assert partial_hash(pa) == partial_hash(pm)  # the middle is not sampled...
    assert full_hash(pa) != full_hash(pm)  # ...which is what the full hash is for
    assert partial_hash(pa) != partial_hash(pt)


def test_partial_hash_includes_size(tmp_path):
    a = write(tmp_path / "a", b"x" * 10)
    assert partial_hash(a, size=10) != partial_hash(a, size=11)


def test_hashing_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        full_hash(tmp_path / "missing")
    with pytest.raises(OSError):
        partial_hash(tmp_path / "missing")
