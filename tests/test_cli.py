import json
import subprocess
import sys

import pytest

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
