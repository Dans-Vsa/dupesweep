"""Decide which copy to keep and clean up the rest — safely.

Safety rules:

* Nothing changes unless the caller asks to apply the plan (the CLI defaults to a dry run).
* Right before touching a duplicate, it is re-checked against the kept file: it must
  still exist, have the same size and the same full BLAKE2b hash. Anything that
  changed since the scan is skipped.
* Every applied action is appended to a JSON Lines undo log. Quarantined files can be
  moved back and hard links can be turned back into copies with ``undo``.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dupesweep.finder import DuplicateGroup
from dupesweep.hasher import full_hash
from dupesweep.scanner import FileEntry

KEEP_RULES = ("first", "oldest", "newest", "shortest", "longest")
ACTIONS = ("quarantine", "hardlink", "delete")


class CleanError(RuntimeError):
    """A single file could not be cleaned (reported, never fatal for the batch)."""


@dataclass(frozen=True)
class Plan:
    """The copy to keep and the copies to remove for one duplicate group."""

    group: DuplicateGroup
    keep: FileEntry
    remove: tuple[FileEntry, ...]

    @property
    def freed(self) -> int:
        return self.group.size * len(self.remove)


def _is_within(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def choose_keeper(
    files: Sequence[FileEntry], rule: str = "first", keep_in: Sequence[Path] = ()
) -> FileEntry:
    """Pick the file to keep. Files inside a ``keep_in`` folder win over all others."""
    if rule not in KEEP_RULES:
        raise ValueError(f"unknown keep rule {rule!r}; choose from {', '.join(KEEP_RULES)}")
    preferred = [f for f in files if any(_is_within(f.path, d) for d in keep_in)]
    pool = sorted(preferred or files, key=lambda f: str(f.path).lower())
    if rule == "first":
        return pool[0]
    key = {
        "oldest": lambda f: f.mtime,
        "newest": lambda f: -f.mtime,
        "shortest": lambda f: len(str(f.path)),
        "longest": lambda f: -len(str(f.path)),
    }[rule]
    return min(pool, key=key)  # min() is stable: ties keep alphabetical order


def make_plan(
    groups: Iterable[DuplicateGroup], rule: str = "first", keep_in: Sequence[Path] = ()
) -> list[Plan]:
    plans = []
    for group in groups:
        keep = choose_keeper(group.files, rule, keep_in)
        remove = tuple(f for f in group.files if f is not keep)
        plans.append(Plan(group=group, keep=keep, remove=remove))
    return plans


def quarantine_target(path: Path, quarantine_dir: Path) -> Path:
    """Mirror the absolute path of ``path`` inside ``quarantine_dir``.

    ``C:\\photos\\a.jpg`` -> ``<q>/C/photos/a.jpg``; ``/home/me/a.jpg`` -> ``<q>/home/me/a.jpg``.
    """
    absolute = path.resolve()
    parts = list(absolute.parts)
    anchor = parts.pop(0)
    drive = anchor.replace(":", "").strip("\\/")
    return quarantine_dir.joinpath(*([drive] if drive else []), *parts)


@dataclass
class Outcome:
    """What happened to one duplicate."""

    path: Path
    keep: Path
    action: str
    ok: bool
    target: Path | None = None
    message: str = ""


@dataclass
class CleanResult:
    outcomes: list[Outcome] = field(default_factory=list)
    freed: int = 0

    @property
    def done(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed(self) -> list[Outcome]:
        return [o for o in self.outcomes if not o.ok]


def _verify(dup: FileEntry, keep: FileEntry, keep_digest: str) -> None:
    try:
        size = dup.path.stat().st_size
    except OSError as exc:
        raise CleanError(f"cannot stat: {exc.strerror or exc}") from exc
    if size != keep.size:
        raise CleanError("size changed since the scan")
    if full_hash(dup.path) != keep_digest:
        raise CleanError("content changed since the scan")


def _hardlink(dup: Path, keep: Path) -> None:
    temp = dup.with_name(f".{dup.name}.dupesweep-tmp")
    os.link(keep, temp)
    try:
        os.replace(temp, dup)
    except OSError:
        temp.unlink(missing_ok=True)
        raise


def _log(log_path: Path | None, record: dict) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def apply_plan(
    plans: Iterable[Plan],
    action: str,
    *,
    quarantine_dir: Path | None = None,
    log_path: Path | None = None,
) -> CleanResult:
    """Carry out ``action`` for every duplicate in ``plans``."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; choose from {', '.join(ACTIONS)}")
    if action == "quarantine" and quarantine_dir is None:
        raise ValueError("quarantine needs a quarantine_dir")

    result = CleanResult()
    for plan in plans:
        try:
            keep_digest = full_hash(plan.keep.path)
        except OSError as exc:
            for dup in plan.remove:
                result.outcomes.append(Outcome(dup.path, plan.keep.path, action, False,
                                               message=f"kept file unreadable: {exc}"))
            continue
        for dup in plan.remove:
            outcome = Outcome(dup.path, plan.keep.path, action, ok=False)
            try:
                _verify(dup, plan.keep, keep_digest)
                if action == "delete":
                    dup.path.unlink()
                elif action == "hardlink":
                    _hardlink(dup.path, plan.keep.path)
                else:
                    target = quarantine_target(dup.path, quarantine_dir)
                    if target.exists():
                        raise CleanError(f"already in quarantine: {target}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dup.path), str(target))
                    outcome.target = target
            except (OSError, CleanError) as exc:
                outcome.message = str(exc) if isinstance(exc, CleanError) else (
                    exc.strerror or str(exc)
                )
                result.outcomes.append(outcome)
                continue
            outcome.ok = True
            result.outcomes.append(outcome)
            result.freed += plan.group.size
            _log(log_path, {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "action": action,
                "path": str(dup.path.resolve() if action != "delete" else dup.path.absolute()),
                "keep": str(plan.keep.path.resolve()),
                "target": str(outcome.target) if outcome.target else None,
                "size": plan.group.size,
                "digest": keep_digest,
            })
    return result


@dataclass
class UndoOutcome:
    path: Path
    action: str
    ok: bool
    message: str = ""


def read_log(log_path: Path) -> list[dict]:
    records = []
    with log_path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{log_path}:{number}: not a dupesweep log line") from exc
    return records


def undo(records: Iterable[dict], dry_run: bool = False) -> list[UndoOutcome]:
    """Reverse quarantine moves and hard links recorded in an undo log (newest first)."""
    outcomes = []
    for record in reversed(list(records)):
        path = Path(record["path"])
        action = record["action"]
        try:
            if action == "delete":
                raise CleanError("deleted files cannot be restored")
            if action == "quarantine":
                target = Path(record["target"])
                if not target.exists():
                    raise CleanError(f"not in quarantine any more: {target}")
                if path.exists():
                    raise CleanError("original path is occupied")
                if not dry_run:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(path))
            elif action == "hardlink":
                keep = Path(record["keep"])
                if not keep.exists():
                    raise CleanError(f"kept file is gone: {keep}")
                if not dry_run:
                    temp = path.with_name(f".{path.name}.dupesweep-tmp")
                    shutil.copy2(keep, temp)
                    os.replace(temp, path)  # breaks the link: an independent copy again
            else:
                raise CleanError(f"unknown action {action!r}")
        except (OSError, CleanError) as exc:
            outcomes.append(UndoOutcome(path, action, False, str(exc)))
            continue
        outcomes.append(UndoOutcome(path, action, True))
    return outcomes
