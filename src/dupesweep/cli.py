"""Command-line interface for dupesweep."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from dupesweep import __version__
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
    find.add_argument("--json", action="store_true", help="print the groups as JSON")
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


def _print_group(number: int, group: DuplicateGroup) -> None:
    print(
        f"Group {number}: {group.count} files x {format_size(group.size)}"
        f"  ({format_size(group.wasted)} wasted)"
    )
    for entry in group.files:
        print(f"  {entry.path}")


def cmd_find(args: argparse.Namespace) -> int:
    if not _check_size_range(args):
        return 2
    scan_stats, find_stats, groups = _scan_and_find(args)
    errors = scan_stats.errors + find_stats.errors
    wasted = sum(g.wasted for g in groups)
    duplicate_files = sum(g.count - 1 for g in groups)

    if args.json:
        payload = {
            "scanned_files": scan_stats.files,
            "scanned_bytes": scan_stats.bytes,
            "groups": len(groups),
            "duplicate_files": duplicate_files,
            "wasted_bytes": wasted,
            "hashing": {
                "size_candidates": find_stats.size_candidates,
                "partial_hashed": find_stats.partial_hashed,
                "full_hashed": find_stats.full_hashed,
                "bytes_fully_hashed": find_stats.bytes_hashed,
            },
            "errors": errors,
            "duplicates": [g.to_dict() for g in groups],
        }
        print(json.dumps(payload, indent=2))
        return 0

    for number, group in enumerate(groups, start=1):
        _print_group(number, group)
        print()
    if groups:
        print(
            f"Found {len(groups)} duplicate group(s): {duplicate_files} redundant file(s), "
            f"{format_size(wasted)} wasted (scanned {scan_stats.files} files)."
        )
    else:
        print(f"No duplicates found (scanned {scan_stats.files} files).")
    _print_errors(errors)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
