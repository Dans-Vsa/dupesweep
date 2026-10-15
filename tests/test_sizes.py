import pytest

from dupesweep.sizes import format_size, parse_size


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("512", 512),
        ("512B", 512),
        ("1k", 1024),
        ("1KB", 1024),
        ("1.5 MB", int(1.5 * 1024**2)),
        ("2GiB", 2 * 1024**3),
        (" 3 tb ", 3 * 1024**4),
    ],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "-1", "10XB", "1.2.3MB"])
def test_parse_size_rejects_invalid(text):
    with pytest.raises(ValueError):
        parse_size(text)


@pytest.mark.parametrize(
    ("num", "expected"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (5 * 1024**2, "5.0 MB"),
        (3 * 1024**3, "3.0 GB"),
        (2048 * 1024**4, "2048.0 TB"),
    ],
)
def test_format_size(num, expected):
    assert format_size(num) == expected


def test_format_size_rejects_negative():
    with pytest.raises(ValueError):
        format_size(-1)
