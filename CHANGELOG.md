# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-17

### Added
- TOML configuration (`./dupesweep.toml` or the user config file) with `[defaults]`,
  `[scan]`, `[find]` and `[clean]` sections, `--config FILE`, `--no-config` and
  `dupesweep config`.
- GitHub Actions CI on Linux and Windows for Python 3.11–3.13.
- This changelog.

### Changed
- Python 3.11 or newer is now required (for the standard-library `tomllib`).

## [0.5.0] - 2026-10-17

### Added
- SQLite hash cache (`--cache`) and `dupesweep cache info|prune|clear`.
- Parallel hashing with `--jobs N` and a progress line on stderr.

## [0.4.0] - 2026-10-17

### Added
- `dupesweep clean` with quarantine, hard-link and delete actions, keep rules and
  `--keep-in`; dry run unless `--yes`; files are re-verified before every action.
- `dupesweep undo` to restore quarantined files and break hard links again.

## [0.3.0] - 2026-10-17

### Added
- Report options for `find`: `--sort`, `--limit`, `--format text|json|csv`, `--output`
  and `--fail-on-dupes`, plus wasted space per file type.

## [0.2.0] - 2026-10-17

### Added
- Duplicate engine: size, then partial (head + tail) hash, then full BLAKE2b hash.
- `dupesweep find` with text and JSON output.

## [0.1.0] - 2026-10-15

### Added
- Project scaffold, size parsing/formatting, filtered scanner and `dupesweep scan`.

[1.0.0]: https://github.com/Dans-Vsa/dupesweep/releases/tag/v1.0.0
