"""Sorting, summarising and exporting duplicate groups."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath

from dupesweep.finder import DuplicateGroup

SORT_KEYS = ("wasted", "size", "count", "path")
FORMATS = ("text", "json", "csv")
CSV_COLUMNS = ("group", "size", "count", "wasted", "digest", "path", "mtime")


def sort_groups(groups: Sequence[DuplicateGroup], by: str = "wasted") -> list[DuplicateGroup]:
    """Sort descending by ``by`` (``path`` sorts by the first file, A→Z)."""
    if by not in SORT_KEYS:
        raise ValueError(f"unknown sort key {by!r}; choose from {', '.join(SORT_KEYS)}")
    first_path = lambda g: str(g.files[0].path).lower()  # noqa: E731
    if by == "path":
        return sorted(groups, key=first_path)
    value = {
        "wasted": lambda g: (g.wasted, g.count),
        "size": lambda g: (g.size, g.count),
        "count": lambda g: (g.count, g.wasted),
    }[by]
    return sorted(sorted(groups, key=first_path), key=value, reverse=True)


def _extension(group: DuplicateGroup) -> str:
    suffix = PurePath(group.files[0].path).suffix.lower()
    return suffix or "(none)"


@dataclass(frozen=True)
class Summary:
    groups: int
    duplicate_files: int
    wasted_bytes: int
    largest_group_size: int
    biggest_waste: int
    by_extension: dict[str, int]  # wasted bytes per file extension, largest first

    def to_dict(self) -> dict:
        return {
            "groups": self.groups,
            "duplicate_files": self.duplicate_files,
            "wasted_bytes": self.wasted_bytes,
            "largest_group_size": self.largest_group_size,
            "biggest_waste": self.biggest_waste,
            "wasted_by_extension": self.by_extension,
        }


def summarize(groups: Sequence[DuplicateGroup]) -> Summary:
    per_ext: dict[str, int] = defaultdict(int)
    for group in groups:
        per_ext[_extension(group)] += group.wasted
    ordered = dict(sorted(per_ext.items(), key=lambda kv: (-kv[1], kv[0])))
    return Summary(
        groups=len(groups),
        duplicate_files=sum(g.count - 1 for g in groups),
        wasted_bytes=sum(g.wasted for g in groups),
        largest_group_size=max((g.count for g in groups), default=0),
        biggest_waste=max((g.wasted for g in groups), default=0),
        by_extension=ordered,
    )


def to_json(
    groups: Sequence[DuplicateGroup],
    summary: Summary | None = None,
    extra: dict | None = None,
) -> str:
    """JSON report; ``summary`` defaults to one computed from ``groups``."""
    summary = summary or summarize(groups)
    payload = {**(extra or {}), "summary": summary.to_dict()}
    payload["duplicates"] = [g.to_dict() for g in groups]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def to_csv(groups: Sequence[DuplicateGroup]) -> str:
    """One row per file, so spreadsheets can filter and sort freely."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for number, group in enumerate(groups, start=1):
        for entry in group.files:
            writer.writerow(
                [number, group.size, group.count, group.wasted, group.digest,
                 str(entry.path), f"{entry.mtime:.0f}"]
            )
    return buffer.getvalue()
