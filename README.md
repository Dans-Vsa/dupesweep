# dupesweep

A fast, dependency-free command-line tool for finding duplicate files and
reclaiming wasted disk space. It walks your folders, groups files by size,
confirms duplicates by content hash, and lets you safely delete,
hard-link or quarantine the extra copies — with an undo log.

> Status: **early development** — milestone 5 of 6 (hash cache, parallel hashing, progress) is done.

## Features

- [x] Recursive scanner with filters: min/max size, include/exclude globs,
      excluded directories (`.git`, `node_modules`, `__pycache__`, `.venv` by default),
      hidden files, optional symlink following
- [x] Hard-link and overlapping-root aware (the same physical file is never counted twice)
- [x] Human-friendly sizes in and out (`--min-size 10KB`, `1.5 MB`)
- [x] `scan` command: file count, total size, largest files, text or JSON output
- [x] Duplicate detection: size → partial hash (first + last 64 KB) → full BLAKE2b hash
- [x] `find` command: duplicate groups, redundant copies and wasted space, text or JSON
- [x] Reports: sort (`--sort wasted|size|count|path`), `--limit`, summary with wasted space per file type, JSON / CSV export (`--format`, `--output`)
- [x] `--fail-on-dupes`: exit status 1 when duplicates exist (handy in CI or cron jobs)
- [x] `clean`: quarantine (move away), hard-link or delete duplicates; keep rules
      (`first`, `oldest`, `newest`, `shortest`, `longest` path, `--keep-in DIR`);
      **dry run by default**, `--yes` to apply; every file is re-hashed right before it is touched
- [x] `undo`: restore quarantined files and turn hard links back into copies from the JSON Lines log
- [x] SQLite hash cache (`--cache`; reused while path, size and mtime are unchanged) with `cache info|prune|clear`
- [x] Parallel hashing (`--jobs N`) and a progress line on stderr (automatic in a terminal, `--progress` / `--no-progress`)
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

# Find duplicates (same filters as scan)
dupesweep find ~/Pictures ~/Backups/Pictures --min-size 1MB
dupesweep find . --json > dupes.json
dupesweep find ~/Music --sort count --limit 20          # groups with the most copies
dupesweep find ~/Data -o reports/dupes.csv             # one CSV row per file
dupesweep find ./assets --fail-on-dupes --min-size 10KB  # fail a CI job on duplicates

# Big folders: 8 hashing threads and a persistent cache for the next run
dupesweep find /mnt/archive --jobs 8 --cache
dupesweep cache info

# Preview a clean-up (nothing changes without --yes)
dupesweep clean ~/Pictures --keep oldest

# Move extra copies to a quarantine folder, keeping anything inside ~/Pictures/Best
dupesweep clean ~/Pictures --keep-in ~/Pictures/Best --quarantine-dir ~/dupes-q --yes

# Replace copies with hard links (same disk only) or delete them
dupesweep clean ./assets --action hardlink --yes
dupesweep clean ./tmp --action delete --keep newest --yes

# Put everything back
dupesweep undo ~/dupes-q/dupesweep-log-20261017-071633.jsonl --yes

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

`find` output:

```
Group 1: 2 files x 293.0 KB  (293.0 KB wasted)
  photos/big1
  photos/big2

Group 2: 3 files x 5 B  (10 B wasted)
  photos/a.txt
  photos/b.txt
  photos/sub/c.txt

Found 2 duplicate group(s): 3 redundant file(s), 293.0 KB wasted (scanned 6 files).
```

### Reports and export

| Option | Effect |
|---|---|
| `--sort wasted` (default) / `size` / `count` / `path` | Order of the groups |
| `--limit N` | List only the first N groups (the summary still covers all of them) |
| `--format text\|json\|csv`, `--json` | Output format |
| `-o FILE` | Write to a file; the format follows the suffix (`.json`, `.csv`, `.txt`) |
| `--fail-on-dupes` | Exit with status 1 if any duplicate group was found |

The text report ends with a breakdown such as `Wasted by type: .jpg 1.2 GB, .mp4 800.0 MB`. CSV has the columns `group,size,count,wasted,digest,path,mtime` — one row per file.

### Cleaning up safely

| Action | What happens | Undo |
|---|---|---|
| `quarantine` (default) | Moves the copy to `--quarantine-dir`, mirroring its absolute path (`C:\photos\a.jpg` → `<q>/C/photos/a.jpg`) | `dupesweep undo LOG --yes` moves it back |
| `hardlink` | Replaces the copy with a hard link to the kept file (frees the space, keeps the path) | `undo` turns it back into an independent copy |
| `delete` | Deletes the copy | Not possible — use quarantine if unsure |

- Without `--yes`, `clean` and `undo` only print what they would do.
- Before each action the copy is checked against the kept file again (exists, same size, same full hash). Files that changed since the scan are skipped and reported; the exit status is then 1.
- The log (`--log FILE`, default `dupesweep-log-<timestamp>.jsonl` in the quarantine dir or current dir) has one JSON object per handled file: `time, action, path, keep, target, size, digest`.
- Files that are already inside the quarantine dir are never treated as duplicates.

### Speed: cache and threads

- `--jobs N` hashes N files at a time (default: number of CPUs, max 8). Hashing is I/O bound and `hashlib` releases the GIL, so threads help most on SSDs and network drives.
- `--cache` stores partial and full hashes in SQLite (default `%LOCALAPPDATA%\dupesweep\hashes.sqlite` on Windows, `$XDG_CACHE_HOME/dupesweep/hashes.sqlite` or `~/.cache/dupesweep/hashes.sqlite` elsewhere; pass `--cache FILE` for another location). A cached hash is used only if the file's size and mtime still match.
- `dupesweep cache info` shows the entry count, `prune` forgets deleted files, `clear` empties it.
- `find --json` reports `hashing.cache_hits`.

### How duplicates are confirmed

1. **Size** — files with a unique size cannot have a twin and are never opened.
2. **Partial hash** — BLAKE2b of the size plus the first and last 64 KB. Files up to 128 KB are fully covered by this step.
3. **Full hash** — only files that still collide are read completely (BLAKE2b, 256-bit).

`find --json` reports how many files reached each stage (`hashing.size_candidates`, `partial_hashed`, `full_hashed`).

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
│   ├── cache.py        # SQLite hash cache
│   ├── cleaner.py      # keep rules, quarantine/hardlink/delete, undo log
│   ├── cli.py          # argparse commands
│   ├── finder.py       # size -> partial -> full hash grouping
│   ├── hasher.py       # partial and full BLAKE2b hashing
│   ├── report.py       # sorting, summary, JSON/CSV export
│   ├── scanner.py      # filesystem walker + filters
│   └── sizes.py        # parse/format human-readable sizes
├── tests/              # pytest suite
├── pyproject.toml
└── LICENSE
```

## License

[MIT](LICENSE) © Dans-Vsa
