# dupesweep

A fast, dependency-free command-line tool for finding duplicate files and
reclaiming wasted disk space. It walks your folders, groups files by size,
confirms duplicates by content hash, and (soon) lets you safely delete,
hard-link or quarantine the extra copies.

> Status: **early development** — milestone 1 of 6 (scanner + `scan` command) is done.

## Features

- [x] Recursive scanner with filters: min/max size, include/exclude globs,
      excluded directories (`.git`, `node_modules`, `__pycache__`, `.venv` by default),
      hidden files, optional symlink following
- [x] Hard-link and overlapping-root aware (the same physical file is never counted twice)
- [x] Human-friendly sizes in and out (`--min-size 10KB`, `1.5 MB`)
- [x] `scan` command: file count, total size, largest files, text or JSON output
- [ ] Duplicate detection: size → partial hash → full BLAKE2 hash
- [ ] Reports: duplicate groups, wasted space, JSON / CSV export
- [ ] Clean-up actions: delete, hard-link or move duplicates, with keep rules
      (oldest, newest, shortest path, inside a folder) and dry-run by default
- [ ] Hash cache (SQLite) and parallel hashing with progress output
- [ ] Config file and packaged release

## Tech stack

- Python 3.10+ (standard library only at runtime)
- `argparse` CLI, `os.scandir`-based walker
- `pytest` for tests

## Setup

```bash
git clone https://github.com/Dans-Vsa/dupesweep.git
cd dupesweep
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
# Summarise a folder and list the 10 largest files
dupesweep scan ~/Downloads --top 10

# Only images bigger than 100 KB, including hidden folders, as JSON
dupesweep scan ~/Pictures -i "*.jpg" -i "*.png" --min-size 100KB --hidden --json

# Skip specific folders (replaces the default excluded directories)
dupesweep scan . --exclude-dir build --exclude-dir dist

# Also works without installing the script
python -m dupesweep scan . 
```

Example output:

```
Scanned 4 files (4.0 KB)
Skipped by filters: 1

Largest 2 files:
  4.0 KB  docs/big.bin
     6 B  b.log
```

Sizes accept `B`, `K/KB/KiB`, `M/MB/MiB`, `G/GB/GiB`, `T/TB/TiB` (binary units, case-insensitive).

## Running tests

```bash
python -m pytest
```

## Project structure

```
dupesweep/
├── src/dupesweep/
│   ├── __init__.py     # package version
│   ├── __main__.py     # `python -m dupesweep`
│   ├── cli.py          # argparse commands
│   ├── scanner.py      # filesystem walker + filters
│   └── sizes.py        # parse/format human-readable sizes
├── tests/              # pytest suite
├── pyproject.toml
└── LICENSE
```

## License

[MIT](LICENSE) © Dans-Vsa
