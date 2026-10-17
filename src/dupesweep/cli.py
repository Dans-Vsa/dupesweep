"""Command-line interface for dupesweep."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from dupesweep import __version__
from dupesweep import report
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dupesweep",
        description="Find (and soon clean up) duplicate files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="walk directories and summarise the files found")
    _add_scan_filters(scan)
    scan.add_argument(
        "--top", type=int, default=5, metavar="N", help="show the N largest files (default: 5)"
    )
    scan.add_argument("--json", action="store_true", help="print the summary as JSON")
    scan.set_defaults(func=cmd_scan)

    find = sub.add_parser("find", help="list groups of duplicate files and the space they waste")
    _add_scan_filters(find)
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
    find.set_defaults(func=cmd_find)
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
    groups = find_duplicates(entries, find_stats)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
