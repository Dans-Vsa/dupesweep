from pathlib import Path

import pytest


def write(path: Path, content: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode()
    path.write_bytes(content)
    return path


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    """A small directory tree with nested folders, hidden and ignored files."""
    write(tmp_path / "a.txt", "hello")
    write(tmp_path / "b.log", "world!")
    write(tmp_path / "empty.txt", "")
    write(tmp_path / "docs" / "readme.md", "# docs")
    write(tmp_path / "docs" / "big.bin", b"x" * 4096)
    write(tmp_path / ".hidden" / "secret.txt", "shh")
    write(tmp_path / ".dotfile", "dot")
    write(tmp_path / "node_modules" / "pkg" / "index.js", "module.exports = 1")
    return tmp_path


@pytest.fixture(autouse=True)
def isolated_user_dirs(tmp_path, monkeypatch):
    """Never read the developer's real config file or write to the real cache dir."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "localappdata"))
