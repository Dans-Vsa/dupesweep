import json
from pathlib import Path

import pytest

from conftest import write
from dupesweep.cache import default_cache_path
from dupesweep.cli import main
from dupesweep.config import ConfigError, find_config, load_config, parse_config


def test_defaults_apply_to_every_command_and_sections_override():
    config = parse_config({
        "defaults": {"min_size": "2KB", "exclude": "*.tmp", "jobs": 2},
        "find": {"sort": "count", "jobs": 6},
        "clean": {"keep": "oldest", "keep_in": ["~/best"], "quarantine_dir": "~/q"},
    })
    assert config.for_command("scan") == {"min_size": 2048, "exclude": ["*.tmp"]}
    find = config.for_command("find")
    assert find["sort"] == "count" and find["jobs"] == 6 and find["min_size"] == 2048
    clean = config.for_command("clean")
    assert clean["jobs"] == 2 and clean["keep"] == "oldest"
    assert clean["keep_in"] == [str(Path.home() / "best")]
    assert clean["quarantine_dir"] == str(Path.home() / "q")


def test_cache_option_forms():
    assert parse_config({"find": {"cache": True}}).for_command("find")["cache"] == str(
        default_cache_path()
    )
    assert parse_config({"find": {"cache": False}}).for_command("find")["cache"] is None
    assert parse_config({"find": {"cache": "x.sqlite"}}).for_command("find")["cache"] == "x.sqlite"


def test_exclude_dir_maps_to_scanner_option():
    config = parse_config({"defaults": {"exclude_dir": [".git", "build"]}})
    assert config.for_command("scan")["exclude_dirs"] == [".git", "build"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"general": {}}, "unknown section"),
        ({"find": {"colour": "red"}}, "unknown option"),
        ({"find": {"keep": "oldest"}}, "does not apply to `find`"),
        ({"defaults": {"min_size": "lots"}}, "min_size"),
        ({"defaults": {"jobs": 0}}, "whole number"),
        ({"defaults": {"hidden": "yes"}}, "true or false"),
        ({"find": {"sort": "age"}}, "expected one of"),
        ({"defaults": {"include": [1, 2]}}, "list of strings"),
        ({"find": "nope"}, "must be a table"),
    ],
)
def test_invalid_config(data, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_find_config_lookup_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    user = tmp_path / "appdata" / "dupesweep" / "config.toml"
    assert find_config() is None
    write(user, "")
    assert find_config() == user
    project = write(tmp_path / "dupesweep.toml", "")
    assert find_config() == project
    explicit = write(tmp_path / "other.toml", "")
    assert find_config(str(explicit)) == explicit
    with pytest.raises(ConfigError, match="not found"):
        find_config(str(tmp_path / "missing.toml"))


def test_load_config_reports_toml_errors(tmp_path):
    bad = write(tmp_path / "bad.toml", "[find\nsort = ")
    with pytest.raises(ConfigError, match="bad.toml"):
        load_config(str(bad))


@pytest.fixture
def dupes(tmp_path):
    root = tmp_path / "data"
    write(root / "a.txt", "tiny")
    write(root / "b.txt", "tiny")
    write(root / "x.bin", b"0" * 4096)
    write(root / "y.bin", b"0" * 4096)
    return root


def test_cli_uses_config_and_flags_win(dupes, tmp_path, capsys):
    cfg = write(tmp_path / "cfg.toml", '[defaults]\nmin_size = "1KB"\n[find]\nformat = "json"\n')
    assert main(["--config", str(cfg), "find", str(dupes)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["groups"] == 1  # the 4-byte files are below min_size

    assert main(["--config", str(cfg), "find", str(dupes), "--min-size", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["groups"] == 2

    assert main(["--no-config", "find", str(dupes)]) == 0
    assert "Found 2 duplicate group(s)" in capsys.readouterr().out


def test_cli_picks_up_project_file(dupes, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "dupesweep.toml", "[clean]\nkeep = \"longest\"\naction = \"delete\"\n")
    assert main(["clean", str(dupes)]) == 0
    assert "would DELETE 2 file(s)" in capsys.readouterr().out
    assert main(["config"]) == 0
    out = capsys.readouterr().out
    assert "dupesweep.toml" in out and "keep = 'longest'" in out


def test_cli_bad_config_is_a_usage_error(dupes, tmp_path, capsys):
    cfg = write(tmp_path / "cfg.toml", "[find]\nkeep = \"oldest\"\n")
    assert main(["--config", str(cfg), "find", str(dupes)]) == 2
    assert "does not apply" in capsys.readouterr().err


def test_config_command_without_file(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["config"]) == 0
    assert "No config file found" in capsys.readouterr().out
