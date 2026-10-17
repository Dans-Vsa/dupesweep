import csv
import io
import json
import subprocess
import sys

import pytest

from conftest import write
from dupesweep import __version__
from dupesweep.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_command_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_scan_text_summary(sample_tree, capsys):
    assert main(["scan", str(sample_tree), "--top", "2"]) == 0
    out = capsys.readouterr().out
    assert "Scanned 4 files (4.0 KB)" in out
    assert "Skipped by filters: 1" in out
    assert "Largest 2 files:" in out
    assert "big.bin" in out


def test_scan_json_with_filters(sample_tree, capsys):
    assert main(["scan", str(sample_tree), "--json", "-i", "*.txt", "--hidden"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["files"] == 2  # a.txt + .hidden/secret.txt
    assert data["skipped_errors"] == 0
    assert {p["size"] for p in data["largest"]} == {5, 3}


def test_scan_size_filters(sample_tree, capsys):
    assert main(["scan", str(sample_tree), "--json", "--min-size", "1KB"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["files"] == 1
    assert data["largest"][0]["size"] == 4096


def test_scan_exclude_dir_override(sample_tree, capsys):
    assert main(["scan", str(sample_tree), "--json", "--exclude-dir", "docs"]) == 0
    data = json.loads(capsys.readouterr().out)
    paths = [p["path"] for p in data["largest"]]
    assert any("index.js" in p for p in paths)
    assert not any("big.bin" in p for p in paths)


def test_invalid_size_is_a_usage_error(sample_tree, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["scan", str(sample_tree), "--min-size", "lots"])
    assert exc.value.code == 2
    assert "invalid size" in capsys.readouterr().err


def test_min_larger_than_max(sample_tree, capsys):
    assert main(["scan", str(sample_tree), "--min-size", "2KB", "--max-size", "1KB"]) == 2


def test_unreadable_path_goes_to_stderr(tmp_path, capsys):
    assert main(["scan", str(tmp_path / "missing")]) == 0
    captured = capsys.readouterr()
    assert "Scanned 0 files" in captured.out
    assert "could not be read" in captured.err


def test_python_dash_m_entry_point(sample_tree):
    result = subprocess.run(
        [sys.executable, "-m", "dupesweep", "scan", str(sample_tree), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["files"] == 4


@pytest.fixture
def dupes(tmp_path):
    write(tmp_path / "one.txt", "same content")
    write(tmp_path / "two.txt", "same content")
    write(tmp_path / "nested" / "three.txt", "same content")
    write(tmp_path / "photo.jpg", b"JPEG" * 100)
    write(tmp_path / "backup" / "photo.jpg", b"JPEG" * 100)
    write(tmp_path / "other.txt", "different!!!")
    return tmp_path


def test_find_text_output(dupes, capsys):
    assert main(["find", str(dupes)]) == 0
    out = capsys.readouterr().out
    assert "Group 1: 2 files x 400 B  (400 B wasted)" in out
    assert "Group 2: 3 files x 12 B  (24 B wasted)" in out
    assert "Found 2 duplicate group(s): 3 redundant file(s), 424 B wasted (scanned 6 files)." in out


def test_find_json_output(dupes, capsys):
    assert main(["find", str(dupes), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["groups"] == 2
    assert data["duplicate_files"] == 3
    assert data["wasted_bytes"] == 424
    assert data["duplicates"][1]["count"] == 3
    assert data["hashing"]["size_candidates"] == 6


def test_find_respects_scan_filters(dupes, capsys):
    assert main(["find", str(dupes), "--include", "*.txt", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["groups"] == 1 and data["duplicates"][0]["size"] == 12


def test_find_without_duplicates(tmp_path, capsys):
    (tmp_path / "a").write_text("1")
    assert main(["find", str(tmp_path)]) == 0
    assert "No duplicates found (scanned 1 files)." in capsys.readouterr().out


def test_find_min_larger_than_max(dupes, capsys):
    assert main(["find", str(dupes), "--min-size", "2KB", "--max-size", "1KB"]) == 2


def test_find_sort_and_limit(dupes, capsys):
    assert main(["find", str(dupes), "--sort", "count", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert "Group 1: 3 files x 12 B" in out
    assert "Group 2" not in out
    assert "Showing 1 of 2 groups (--limit)." in out
    assert "Wasted by type: .jpg 400 B, .txt 24 B" in out


def test_find_json_summary_covers_all_groups(dupes, capsys):
    assert main(["find", str(dupes), "--json", "--limit", "1"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["duplicates"]) == 1
    assert data["summary"]["groups"] == 2
    assert data["summary"]["wasted_by_extension"] == {".jpg": 400, ".txt": 24}
    assert data["limit"] == 1 and data["sort"] == "wasted"


def test_find_csv_to_stdout(dupes, capsys):
    assert main(["find", str(dupes), "--format", "csv"]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert len(rows) == 5
    assert rows[0]["group"] == "1" and rows[0]["wasted"] == "400"


def test_find_output_file_infers_format(dupes, tmp_path, capsys):
    target = tmp_path / "out" / "report.json"
    assert main(["find", str(dupes), "-o", str(target)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Wrote json report" in captured.err
    assert json.loads(target.read_text(encoding="utf-8"))["groups"] == 2


def test_find_output_unknown_suffix(dupes, tmp_path, capsys):
    assert main(["find", str(dupes), "-o", str(tmp_path / "r.md")]) == 2
    assert "cannot tell the format" in capsys.readouterr().err


def test_find_fail_on_dupes(dupes, tmp_path, capsys):
    assert main(["find", str(dupes), "--fail-on-dupes"]) == 1
    (tmp_path / "solo").mkdir()
    (tmp_path / "solo" / "a").write_text("only one")
    assert main(["find", str(tmp_path / "solo"), "--fail-on-dupes"]) == 0


def test_find_invalid_limit(dupes, capsys):
    assert main(["find", str(dupes), "--limit", "0"]) == 2


def files_in(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_clean_is_a_dry_run_by_default(dupes, capsys):
    before = files_in(dupes)
    assert main(["clean", str(dupes)]) == 0
    out = capsys.readouterr().out
    assert "Dry run: would move to quarantine 3 file(s)" in out
    assert "--yes" in out
    assert files_in(dupes) == before


def test_clean_quarantine_then_undo(dupes, tmp_path_factory, capsys, monkeypatch):
    out_dir = tmp_path_factory.mktemp("out")  # outside the scanned tree
    monkeypatch.chdir(out_dir)
    q = out_dir / "q"
    assert main(["clean", str(dupes), "--yes", "--quarantine-dir", str(q),
                 "--keep-in", str(dupes / "backup")]) == 0
    out = capsys.readouterr().out
    assert "Done: 3 file(s) handled with quarantine" in out
    # --keep-in wins for the photo; the text group keeps the first path A-Z.
    assert files_in(dupes) == ["backup/photo.jpg", "nested/three.txt", "other.txt"]
    logs = list(q.glob("dupesweep-log-*.jsonl"))
    assert len(logs) == 1

    assert main(["undo", str(logs[0])]) == 0
    assert "Dry run: 3 of 3 file(s) can be restored" in capsys.readouterr().out
    assert main(["undo", str(logs[0]), "--yes"]) == 0
    assert "Restored 3 of 3 file(s)." in capsys.readouterr().out
    assert "photo.jpg" in files_in(dupes) and "one.txt" in files_in(dupes)


def test_clean_delete_with_keep_rule_and_custom_log(dupes, tmp_path_factory, capsys):
    log = tmp_path_factory.mktemp("logs") / "clean.jsonl"
    assert main(["clean", str(dupes), "--action", "delete", "--keep", "longest",
                 "--log", str(log), "-y"]) == 0
    assert files_in(dupes) == ["backup/photo.jpg", "nested/three.txt", "other.txt"]
    assert len(log.read_text(encoding="utf-8").splitlines()) == 3
    out = capsys.readouterr().out
    assert "undo:" not in out  # deletions cannot be undone


def test_clean_hardlink(dupes, tmp_path_factory, capsys):
    log = tmp_path_factory.mktemp("logs") / "l.jsonl"
    assert main(["clean", str(dupes), "--action", "hardlink", "--log", str(log), "--yes"]) == 0
    one, two = (dupes / "one.txt").stat(), (dupes / "two.txt").stat()
    assert one.st_ino == two.st_ino
    assert len(files_in(dupes)) == 6


def test_clean_nothing_to_do(tmp_path, capsys):
    (tmp_path / "a").write_text("alone")
    assert main(["clean", str(tmp_path), "--yes"]) == 0
    assert "Nothing to clean." in capsys.readouterr().out


def test_clean_rejects_missing_keep_in(dupes, tmp_path, capsys):
    assert main(["clean", str(dupes), "--keep-in", str(tmp_path / "nope")]) == 2


def test_undo_bad_log(tmp_path, capsys):
    assert main(["undo", str(tmp_path / "missing.jsonl")]) == 2
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{nope")
    assert main(["undo", str(bad)]) == 2


def test_find_with_cache_and_jobs(dupes, tmp_path_factory, capsys):
    cache = tmp_path_factory.mktemp("cache") / "h.sqlite"
    args = ["find", str(dupes), "--json", "--cache", str(cache), "-j", "3"]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert first["hashing"]["cache_hits"] == 0
    assert second["hashing"]["cache_hits"] == 6  # all six same-size files are small
    assert first["duplicates"] == second["duplicates"]

    assert main(["cache", "info", "--cache", str(cache)]) == 0
    assert "Entries: 6" in capsys.readouterr().out
    assert main(["cache", "prune", "--cache", str(cache)]) == 0
    assert "Removed 0 entries" in capsys.readouterr().out
    assert main(["cache", "clear", "--cache", str(cache)]) == 0
    assert main(["cache", "info", "--cache", str(cache)]) == 0
    assert "Entries: 0" in capsys.readouterr().out


def test_cache_info_without_cache(tmp_path, capsys):
    assert main(["cache", "info", "--cache", str(tmp_path / "none.sqlite")]) == 0
    assert "No cache at" in capsys.readouterr().out


def test_progress_goes_to_stderr(dupes, capsys):
    assert main(["find", str(dupes), "--progress"]) == 0
    captured = capsys.readouterr()
    assert "hashing partial" in captured.err and "6/6" in captured.err
    assert "hashing" not in captured.out
    assert main(["find", str(dupes), "--no-progress"]) == 0
    assert "hashing" not in capsys.readouterr().err
