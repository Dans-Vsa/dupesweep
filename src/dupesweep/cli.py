"""Command-line interface for dupesweep."""

from __future__ import annotations

import argparse
import json
import sys
import time
import os
from collections.abc import Sequence
from pathlib import Path

from dupesweep import __version__
from dupesweep import cleaner, report
from dupesweep.cache import HashCache, default_cache_path
from dupesweep.config import Config, ConfigError, load_config, user_config_path
from dupesweep.finder import DuplicateGroup, FindStats, find_duplicates
from dupesweep.scanner import ScanOptions, ScanStats, iter_files
from dupesweep.sizes import format_size, parse_size


def _size_arg(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _add_scan_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="+", help="files or directories to scan")
    parser.add_argument(
        "--min-size", type=_size_arg, default=1, metavar="SIZE",
        help="ignore files smaller than SIZE (default: 1 byte, i.e. skip empty files)",
    )
    parser.add_argument(
        "--max-size", type=_size_arg, default=None, metavar="SIZE",
        help="ignore files larger than SIZE",
    )
    parser.add_argument(
        "-i", "--include", action="append", default=[], metavar="GLOB",
        help="only scan files matching GLOB (repeatable)",
    )
    parser.add_argument(
        "-e", "--exclude", action="append", default=[], metavar="GLOB",
        help="skip files matching GLOB (repeatable)",
    )
    parser.add_argument(
        "--exclude-dir", action="append", default=None, metavar="GLOB", dest="exclude_dirs",
        help="skip directories matching GLOB (repeatable; replaces the defaults "
        ".git, node_modules, __pycache__, .venv)",
    )
    parser.add_argument("--hidden", action="store_true", help="include hidden files and folders")
    parser.add_argument(
        "--follow-symlinks", action="store_true", help="follow symbolic links while walking"
    )


DEFAULT_JOBS = min(8, os.cpu_count() or 1)


def _add_hash_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-j", "--jobs", type=int, default=DEFAULT_JOBS, metavar="N",
        help=f"hash N files in parallel (default: {DEFAULT_JOBS})",
    )
    parser.add_argument(
        "--cache", nargs="?", const=str(default_cache_path()), default=None, metavar="FILE",
        help="reuse hashes of unchanged files from an SQLite cache "
        f"(default file: {default_cache_path()})",
    )
    progress = parser.add_mutually_exclusive_group()
    progress.add_argument(
        "--progress", dest="progress", action="store_true", default=None,
        help="show hashing progress on stderr (default: only in an interactive terminal)",
    )
    progress.add_argument(
        "--no-progress", dest="progress", action="store_false", help="never show progress"
    )


class _Progress:
    """Single-line progress on stderr: ``hashing partial  120/500``."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stderr
        self._last = None

    def __call__(self, stage: str, done: int, total: int) -> None:
        key = (stage, done * 50 // total)  # redraw at most ~50 times per stage
        if key == self._last and done != total:
            return
        self._last = key
        self.stream.write(f"\rhashing {stage:<7} {done:>{len(str(total))}}/{total}")
        if done == total:
            self.stream.write("\n")
        self.stream.flush()


def _options_from_args(args: argparse.Namespace) -> ScanOptions:
    options = ScanOptions(
        min_size=args.min_size,
        max_size=args.max_size,
        include=args.include,
        exclude=args.exclude,
        include_hidden=args.hidden,
        follow_symlinks=args.follow_symlinks,
    )
    if args.exclude_dirs is not None:
        options.exclude_dirs = args.exclude_dirs
    return options


def _add_config_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--config", metavar="FILE",
        help=f"read defaults from FILE (default: ./dupesweep.toml or {user_config_path()})",
    )
    group.add_argument("--no-config", action="store_true", help="ignore configuration files")


def build_parser(config: Config | None = None) -> argparse.ArgumentParser:
    config = config or Config()
    parser = argparse.ArgumentParser(
        prog="dupesweep",
        description="Find and safely clean up duplicate files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_config_options(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="walk directories and summarise the files found")
    _add_scan_filters(scan)
    scan.add_argument(
        "--top", type=int, default=5, metavar="N", help="show the N largest files (default: 5)"
    )
    scan.add_argument("--json", action="store_true", help="print the summary as JSON")
    scan.set_defaults(**config.for_command("scan"))
    scan.set_defaults(func=cmd_scan)

    find = sub.add_parser("find", help="list groups of duplicate files and the space they waste")
    _add_scan_filters(find)
    _add_hash_options(find)
    find.add_argument(
        "--sort", choices=report.SORT_KEYS, default="wasted",
        help="order of the groups (default: wasted space)",
    )
    find.add_argument(
        "--limit", type=int, default=None, metavar="N", help="only list the first N groups"
    )
    find.add_argument(
        "-f", "--format", choices=report.FORMATS, default=None,
        help="output format (default: text, or from the --output suffix)",
    )
    find.add_argument("--json", action="store_true", help="shortcut for --format json")
    find.add_argument(
        "-o", "--output", metavar="FILE", help="write the report to FILE (.json or .csv)"
    )
    find.add_argument(
        "--fail-on-dupes", action="store_true",
        help="exit with status 1 when duplicates are found (useful in CI)",
    )
    find.set_defaults(**config.for_command("find"))
    find.set_defaults(func=cmd_find)

    clean = sub.add_parser(
        "clean", help="remove, hard-link or quarantine duplicates (dry run unless --yes)"
    )
    _add_scan_filters(clean)
    _add_hash_options(clean)
    clean.add_argument(
        "--action", choices=cleaner.ACTIONS, default="quarantine",
        help="what to do with the extra copies (default: quarantine = move them away)",
    )
    clean.add_argument(
        "--keep", choices=cleaner.KEEP_RULES, default="first",
        help="which copy to keep: first (A-Z path), oldest, newest, shortest or longest path",
    )
    clean.add_argument(
        "--keep-in", action="append", default=[], metavar="DIR",
        help="prefer keeping copies inside DIR (repeatable)",
    )
    clean.add_argument(
        "--quarantine-dir", default="dupesweep-quarantine", metavar="DIR",
        help="where quarantined files go (default: ./dupesweep-quarantine)",
    )
    clean.add_argument(
        "--log", metavar="FILE",
        help="undo log to append to (default: dupesweep-log-<timestamp>.jsonl in the "
        "quarantine dir, or the current dir for other actions)",
    )
    clean.add_argument("-y", "--yes", action="store_true", help="actually apply the changes")
    clean.set_defaults(**config.for_command("clean"))
    clean.set_defaults(func=cmd_clean)

    undo = sub.add_parser("undo", help="restore files recorded in a clean log")
    undo.add_argument("log", help="the .jsonl log written by clean")
    undo.add_argument("-y", "--yes", action="store_true", help="actually restore the files")
    undo.set_defaults(func=cmd_undo)

    cache = sub.add_parser("cache", help="inspect or maintain the hash cache")
    cache.add_argument("action", choices=("info", "prune", "clear"))
    cache.add_argument(
        "--cache", default=str(default_cache_path()), metavar="FILE",
        help="cache file (default: %(default)s)",
    )
    cache.set_defaults(func=cmd_cache)

    show = sub.add_parser("config", help="show which config file is used and its values")
    show.set_defaults(func=cmd_config, config_obj=config)
    return parser


def _check_size_range(args: argparse.Namespace) -> bool:
    if args.min_size and args.max_size is not None and args.min_size > args.max_size:
        print("error: --min-size cannot be larger than --max-size", file=sys.stderr)
        return False
    return True


def _print_errors(errors: list[str]) -> None:
    if errors:
        print(f"\n{len(errors)} path(s) could not be read:", file=sys.stderr)
        for message in errors:
            print(f"  {message}", file=sys.stderr)


def cmd_scan(args: argparse.Namespace) -> int:
    if not _check_size_range(args):
        return 2

    stats = ScanStats()
    entries = list(iter_files(args.paths, _options_from_args(args), stats))
    largest = sorted(entries, key=lambda e: e.size, reverse=True)[: max(args.top, 0)]

    if args.json:
        payload = {
            "files": stats.files,
            "bytes": stats.bytes,
            "skipped_filtered": stats.skipped_filtered,
            "skipped_errors": stats.skipped_errors,
            "errors": stats.errors,
            "largest": [{"path": str(e.path), "size": e.size} for e in largest],
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Scanned {stats.files} files ({format_size(stats.bytes)})")
    if stats.skipped_filtered:
        print(f"Skipped by filters: {stats.skipped_filtered}")
    if largest:
        print(f"\nLargest {len(largest)} files:")
        width = max(len(format_size(e.size)) for e in largest)
        for e in largest:
            print(f"  {format_size(e.size):>{width}}  {e.path}")
    if stats.errors:
        print(f"\n{stats.skipped_errors} path(s) could not be read:", file=sys.stderr)
        for message in stats.errors:
            print(f"  {message}", file=sys.stderr)
    return 0


def _scan_and_find(args: argparse.Namespace) -> tuple[ScanStats, FindStats, list[DuplicateGroup]]:
    scan_stats = ScanStats()
    entries = list(iter_files(args.paths, _options_from_args(args), scan_stats))
    find_stats = FindStats()
    show_progress = args.progress if args.progress is not None else sys.stderr.isatty()
    options = {
        "jobs": max(1, args.jobs),
        "progress": _Progress() if show_progress else None,
    }
    if args.cache:
        with HashCache(args.cache) as cache:
            groups = find_duplicates(entries, find_stats, cache=cache, **options)
    else:
        groups = find_duplicates(entries, find_stats, **options)
    return scan_stats, find_stats, groups


def _report_format(args: argparse.Namespace) -> str | None:
    """Resolve --json / --format / --output suffix; ``None`` means a usage error."""
    if args.json:
        return "json"
    if args.format:
        return args.format
    if args.output:
        suffix = Path(args.output).suffix.lower()
        guessed = {".json": "json", ".csv": "csv", ".txt": "text"}.get(suffix)
        if guessed is None:
            print(
                f"error: cannot tell the format of {args.output!r}; pass --format json|csv|text",
                file=sys.stderr,
            )
        return guessed
    return "text"


def _render_text(
    groups: list[DuplicateGroup], summary: report.Summary, scanned: int, total_groups: int
) -> str:
    lines: list[str] = []
    for number, group in enumerate(groups, start=1):
        lines.append(
            f"Group {number}: {group.count} files x {format_size(group.size)}"
            f"  ({format_size(group.wasted)} wasted)"
        )
        lines.extend(f"  {entry.path}" for entry in group.files)
        lines.append("")
    if summary.groups:
        lines.append(
            f"Found {summary.groups} duplicate group(s): {summary.duplicate_files} redundant "
            f"file(s), {format_size(summary.wasted_bytes)} wasted (scanned {scanned} files)."
        )
        if len(groups) < total_groups:
            lines.append(f"Showing {len(groups)} of {total_groups} groups (--limit).")
        top = list(summary.by_extension.items())[:5]
        if len(summary.by_extension) > 1:
            lines.append(
                "Wasted by type: "
                + ", ".join(f"{ext} {format_size(size)}" for ext, size in top)
            )
    else:
        lines.append(f"No duplicates found (scanned {scanned} files).")
    return "\n".join(lines) + "\n"


def cmd_find(args: argparse.Namespace) -> int:
    if not _check_size_range(args):
        return 2
    if args.limit is not None and args.limit < 1:
        print("error: --limit must be at least 1", file=sys.stderr)
        return 2
    fmt = _report_format(args)
    if fmt is None:
        return 2

    scan_stats, find_stats, groups = _scan_and_find(args)
    errors = scan_stats.errors + find_stats.errors
    groups = report.sort_groups(groups, args.sort)
    summary = report.summarize(groups)
    shown = groups[: args.limit] if args.limit else groups

    if fmt == "json":
        extra = {
            "scanned_files": scan_stats.files,
            "scanned_bytes": scan_stats.bytes,
            "groups": summary.groups,
            "duplicate_files": summary.duplicate_files,
            "wasted_bytes": summary.wasted_bytes,
            "sort": args.sort,
            "limit": args.limit,
            "hashing": {
                "size_candidates": find_stats.size_candidates,
                "partial_hashed": find_stats.partial_hashed,
                "full_hashed": find_stats.full_hashed,
                "bytes_fully_hashed": find_stats.bytes_hashed,
                "cache_hits": find_stats.cache_hits,
            },
            "errors": errors,
        }
        text = report.to_json(shown, summary, extra)
    elif fmt == "csv":
        text = report.to_csv(shown)
    else:
        text = _render_text(shown, summary, scan_stats.files, len(groups))

    if args.output:
        try:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8", newline="")
        except OSError as exc:
            print(f"error: cannot write {args.output}: {exc}", file=sys.stderr)
            return 1
        print(
            f"Wrote {fmt} report to {args.output}: {summary.groups} group(s), "
            f"{format_size(summary.wasted_bytes)} wasted.",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(text)

    _print_errors(errors)
    if args.fail_on_dupes and summary.groups:
        return 1
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    if not _check_size_range(args):
        return 2
    keep_in = [Path(d) for d in args.keep_in]
    for folder in keep_in:
        if not folder.is_dir():
            print(f"error: --keep-in folder does not exist: {folder}", file=sys.stderr)
            return 2
    quarantine_dir = Path(args.quarantine_dir)
    scan_stats, find_stats, groups = _scan_and_find(args)
    if args.action == "quarantine":
        # Never treat files that are already quarantined as candidates.
        groups = [g for g in groups if not any(_inside(f.path, quarantine_dir) for f in g.files)]
    plans = cleaner.make_plan(groups, args.keep, keep_in)
    freed = sum(p.freed for p in plans)
    count = sum(len(p.remove) for p in plans)
    errors = scan_stats.errors + find_stats.errors

    if not plans:
        print(f"No duplicates found (scanned {scan_stats.files} files). Nothing to clean.")
        _print_errors(errors)
        return 0

    verb = {"quarantine": "move to quarantine", "hardlink": "hard-link", "delete": "DELETE"}
    for number, plan in enumerate(plans, start=1):
        print(f"Group {number} ({format_size(plan.group.size)} each):")
        print(f"  {'keep':<10} {plan.keep.path}")
        for dup in plan.remove:
            print(f"  {args.action:<10} {dup.path}")
    print()

    if not args.yes:
        print(
            f"Dry run: would {verb[args.action]} {count} file(s) and free "
            f"{format_size(freed)}. Re-run with --yes to apply."
        )
        _print_errors(errors)
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    if args.log:
        log_path = Path(args.log)
    elif args.action == "quarantine":
        log_path = quarantine_dir / f"dupesweep-log-{stamp}.jsonl"
    else:
        log_path = Path(f"dupesweep-log-{stamp}.jsonl")
    result = cleaner.apply_plan(
        plans, args.action, quarantine_dir=quarantine_dir, log_path=log_path
    )
    for outcome in result.failed:
        print(f"skipped {outcome.path}: {outcome.message}", file=sys.stderr)
    print(
        f"Done: {len(result.done)} file(s) handled with {args.action}, "
        f"{format_size(result.freed)} freed, {len(result.failed)} skipped."
    )
    if result.done:
        undo_hint = "" if args.action == "delete" else f" (undo: dupesweep undo {log_path} --yes)"
        print(f"Log: {log_path}{undo_hint}")
    _print_errors(errors)
    return 1 if result.failed else 0


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except (ValueError, OSError):
        return False
    return True


def cmd_undo(args: argparse.Namespace) -> int:
    log_path = Path(args.log)
    try:
        records = cleaner.read_log(log_path)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read log: {exc}", file=sys.stderr)
        return 2
    outcomes = cleaner.undo(records, dry_run=not args.yes)
    for outcome in outcomes:
        status = "restored" if outcome.ok else "skipped "
        if not args.yes and outcome.ok:
            status = "would restore"
        line = f"{status} {outcome.path}"
        print(line if outcome.ok else f"{line}: {outcome.message}")
    ok = sum(o.ok for o in outcomes)
    if args.yes:
        print(f"Restored {ok} of {len(outcomes)} file(s).")
    else:
        print(f"Dry run: {ok} of {len(outcomes)} file(s) can be restored. Re-run with --yes.")
    return 0 if ok == len(outcomes) else 1


def cmd_cache(args: argparse.Namespace) -> int:
    path = Path(args.cache)
    if args.action != "clear" and not path.exists():
        print(f"No cache at {path} yet (it is created by find/clean --cache).")
        return 0
    with HashCache(path) as cache:
        if args.action == "info":
            size = path.stat().st_size
            print(f"Cache: {path}\nEntries: {len(cache)}\nSize: {format_size(size)}")
        elif args.action == "prune":
            print(f"Removed {cache.prune()} entries for files that no longer exist.")
        else:
            cache.clear()
            print(f"Cleared {path}.")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    config: Config = args.config_obj
    if config.path is None:
        print("No config file found. Looked for ./dupesweep.toml and", user_config_path())
        return 0
    print(f"Config: {config.path}")
    for command, values in config.defaults.items():
        if values:
            print(f"[{command}]")
            for dest, value in sorted(values.items()):
                print(f"  {dest} = {value!r}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    pre = argparse.ArgumentParser(add_help=False)
    _add_config_options(pre)
    known, _ = pre.parse_known_args(argv)
    try:
        config = Config() if known.no_config else load_config(known.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser = build_parser(config)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
