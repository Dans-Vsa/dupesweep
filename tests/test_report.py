import csv
import io
import json
from pathlib import Path

import pytest

from dupesweep import report
from dupesweep.finder import DuplicateGroup
from dupesweep.scanner import FileEntry


def group(size: int, *names: str, digest: str = "d") -> DuplicateGroup:
    files = tuple(FileEntry(Path(n), size, 1_700_000_000.4, (0, i)) for i, n in enumerate(names))
    return DuplicateGroup(size=size, digest=digest, files=files)


PHOTOS = group(1000, "b/photo.JPG", "c/photo.jpg")               # wasted 1000
DOCS = group(10, "a/x.txt", "b/x.txt", "c/x.txt", "d/x.txt")       # wasted 30
NOEXT = group(300, "z/Makefile", "y/Makefile", "x/Makefile")       # wasted 600
GROUPS = [DOCS, PHOTOS, NOEXT]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("wasted", [PHOTOS, NOEXT, DOCS]),
        ("size", [PHOTOS, NOEXT, DOCS]),
        ("count", [DOCS, NOEXT, PHOTOS]),
        ("path", [DOCS, PHOTOS, NOEXT]),
    ],
)
def test_sort_groups(key, expected):
    assert report.sort_groups(GROUPS, key) == expected


def test_sort_groups_rejects_unknown_key():
    with pytest.raises(ValueError):
        report.sort_groups(GROUPS, "age")


def test_summarize():
    s = report.summarize(GROUPS)
    assert (s.groups, s.duplicate_files, s.wasted_bytes) == (3, 6, 1630)
    assert (s.largest_group_size, s.biggest_waste) == (4, 1000)
    # Extensions are case-insensitive and ordered by wasted bytes.
    assert list(s.by_extension.items()) == [(".jpg", 1000), ("(none)", 600), (".txt", 30)]


def test_summarize_empty():
    s = report.summarize([])
    assert s.to_dict() == {
        "groups": 0, "duplicate_files": 0, "wasted_bytes": 0, "largest_group_size": 0,
        "biggest_waste": 0, "wasted_by_extension": {},
    }


def test_to_json_uses_given_summary_and_extra():
    data = json.loads(report.to_json([PHOTOS], report.summarize(GROUPS), {"scanned_files": 9}))
    assert data["scanned_files"] == 9
    assert data["summary"]["groups"] == 3  # summary of everything, list limited to one
    assert len(data["duplicates"]) == 1


def test_to_csv_has_one_row_per_file():
    rows = list(csv.DictReader(io.StringIO(report.to_csv([PHOTOS, DOCS]))))
    assert len(rows) == 6
    assert rows[0] == {
        "group": "1", "size": "1000", "count": "2", "wasted": "1000", "digest": "d",
        "path": str(Path("b/photo.JPG")), "mtime": "1700000000",
    }
    assert {r["group"] for r in rows[2:]} == {"2"}
