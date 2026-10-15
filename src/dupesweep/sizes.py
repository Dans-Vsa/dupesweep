"""Parse and format human-readable byte sizes (e.g. "1.5MB", "200k")."""

from __future__ import annotations

import re

_UNITS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "MIB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "GIB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
    "TIB": 1024**4,
}

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")

_DISPLAY_UNITS = ("B", "KB", "MB", "GB", "TB")


def parse_size(text: str) -> int:
    """Convert a size such as ``"10KB"`` or ``"1.5 gb"`` into bytes.

    Units are binary (1 KB = 1024 bytes). A bare number means bytes.
    """
    match = _SIZE_RE.match(text)
    if not match:
        raise ValueError(f"invalid size: {text!r}")
    number, unit = match.groups()
    unit = unit.upper()
    if unit not in _UNITS:
        raise ValueError(f"unknown size unit {unit!r} in {text!r}")
    return int(float(number) * _UNITS[unit])


def format_size(num_bytes: int) -> str:
    """Render a byte count with one decimal place, e.g. ``"1.5 MB"``."""
    if num_bytes < 0:
        raise ValueError("size cannot be negative")
    value = float(num_bytes)
    for unit in _DISPLAY_UNITS:
        if value < 1024 or unit == _DISPLAY_UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")
