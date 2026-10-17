"""Optional TOML configuration with per-command defaults.

Lookup order (first file found wins): ``--config FILE``, ``./dupesweep.toml``, then
the user config file (``%APPDATA%\\dupesweep\\config.toml`` on Windows,
``$XDG_CONFIG_HOME/dupesweep/config.toml`` or ``~/.config/dupesweep/config.toml``).

Example::

    [defaults]            # every command
    min_size = "10KB"
    exclude = ["*.tmp", "Thumbs.db"]
    jobs = 4
    cache = true

    [clean]               # only `dupesweep clean`
    keep = "oldest"
    keep_in = ["~/Pictures/Best"]
    quarantine_dir = "~/dupes-quarantine"

Command-line options always win over the file.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dupesweep.cache import default_cache_path
from dupesweep.cleaner import ACTIONS, KEEP_RULES
from dupesweep.report import FORMATS, SORT_KEYS
from dupesweep.sizes import parse_size

COMMANDS = ("scan", "find", "clean")
PROJECT_FILE = "dupesweep.toml"


class ConfigError(ValueError):
    """The configuration file is invalid."""


def user_config_path() -> Path:
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    elif os.environ.get("XDG_CONFIG_HOME"):
        base = Path(os.environ["XDG_CONFIG_HOME"])
    else:
        base = Path.home() / ".config"
    return base / "dupesweep" / "config.toml"


def find_config(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    for candidate in (Path.cwd() / PROJECT_FILE, user_config_path()):
        if candidate.is_file():
            return candidate
    return None


def _size(key: str, value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return parse_size(value)
        except ValueError as exc:
            raise ConfigError(f"{key}: {exc}") from exc
    raise ConfigError(f"{key}: expected a size like \"10KB\" or a number of bytes")


def _strings(key: str, value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ConfigError(f"{key}: expected a string or a list of strings")


def _paths(key: str, value: Any) -> list[str]:
    return [str(Path(v).expanduser()) for v in _strings(key, value)]


def _bool(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{key}: expected true or false")
    return value


def _int(key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{key}: expected a whole number >= 1")
    return value


def _choice(choices):
    def convert(key: str, value: Any) -> str:
        if value not in choices:
            raise ConfigError(f"{key}: expected one of {', '.join(choices)}")
        return value

    return convert


def _cache(key: str, value: Any) -> str | None:
    if value is True:
        return str(default_cache_path())
    if value is False:
        return None
    if isinstance(value, str):
        return str(Path(value).expanduser())
    raise ConfigError(f"{key}: expected true, false or a file path")


def _path(key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{key}: expected a folder path")
    return str(Path(value).expanduser())


# config key -> (argparse dest, converter, commands it applies to)
SCAN_KEYS = ("scan", "find", "clean")
HASH_KEYS = ("find", "clean")
OPTIONS: dict[str, tuple[str, Any, tuple[str, ...]]] = {
    "min_size": ("min_size", _size, SCAN_KEYS),
    "max_size": ("max_size", _size, SCAN_KEYS),
    "include": ("include", _strings, SCAN_KEYS),
    "exclude": ("exclude", _strings, SCAN_KEYS),
    "exclude_dir": ("exclude_dirs", _strings, SCAN_KEYS),
    "hidden": ("hidden", _bool, SCAN_KEYS),
    "follow_symlinks": ("follow_symlinks", _bool, SCAN_KEYS),
    "jobs": ("jobs", _int, HASH_KEYS),
    "cache": ("cache", _cache, HASH_KEYS),
    "progress": ("progress", _bool, HASH_KEYS),
    "top": ("top", _int, ("scan",)),
    "sort": ("sort", _choice(SORT_KEYS), ("find",)),
    "limit": ("limit", _int, ("find",)),
    "format": ("format", _choice(FORMATS), ("find",)),
    "fail_on_dupes": ("fail_on_dupes", _bool, ("find",)),
    "action": ("action", _choice(ACTIONS), ("clean",)),
    "keep": ("keep", _choice(KEEP_RULES), ("clean",)),
    "keep_in": ("keep_in", _paths, ("clean",)),
    "quarantine_dir": ("quarantine_dir", _path, ("clean",)),
}


@dataclass
class Config:
    path: Path | None = None
    # command -> {argparse dest: value}
    defaults: dict[str, dict[str, Any]] = field(default_factory=dict)

    def for_command(self, command: str) -> dict[str, Any]:
        return dict(self.defaults.get(command, {}))


def parse_config(data: dict[str, Any], source: str = "config") -> Config:
    """Validate parsed TOML and turn it into per-command argparse defaults."""
    unknown_sections = set(data) - {"defaults", *COMMANDS}
    if unknown_sections:
        raise ConfigError(f"{source}: unknown section(s): {', '.join(sorted(unknown_sections))}")
    config = Config(defaults={command: {} for command in COMMANDS})
    # [defaults] first, so command sections can override it.
    for section in ("defaults", *COMMANDS):
        table = data.get(section, {})
        if not isinstance(table, dict):
            raise ConfigError(f"{source}: [{section}] must be a table")
        for key, raw in table.items():
            if key not in OPTIONS:
                raise ConfigError(f"{source}: [{section}] unknown option {key!r}")
            dest, convert, commands = OPTIONS[key]
            if section != "defaults" and section not in commands:
                raise ConfigError(f"{source}: {key!r} does not apply to `{section}`")
            value = convert(f"[{section}] {key}", raw)
            for command in (commands if section == "defaults" else (section,)):
                config.defaults[command][dest] = value
    return config


def load_config(explicit: str | None = None) -> Config:
    path = find_config(explicit)
    if path is None:
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    config = parse_config(data, str(path))
    config.path = path
    return config
