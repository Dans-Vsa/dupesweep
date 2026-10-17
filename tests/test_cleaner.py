import os
from pathlib import Path

import pytest

from conftest import write
from dupesweep import cleaner
from dupesweep.finder import find_duplicates
from dupesweep.scanner import FileEntry, iter_files


def entry(path: str, mtime: float = 0.0) -> FileEntry:
    return FileEntry(Path(path), 1, mtime, (0, 0))


FILES = [
    entry("photos/2024/b.jpg", mtime=300),
    entry("a.jpg", mtime=200),
    entry("backup/very/deep/folder/c.jpg", mtime=100),
]


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("first", "a.jpg"),
        ("oldest", "backup/very/deep/folder/c.jpg"),
        ("newest", "photos/2024/b.jpg"),
        ("shortest", "a.jpg"),
        ("longest", "backup/very/deep/folder/c.jpg"),
    ],
)
def test_choose_keeper_rules(rule, expected):
    assert cleaner.choose_keeper(FILES, rule).path == Path(expected)


def test_choose_keeper_prefers_keep_in(tmp_path):
    files = [entry(str(tmp_path / "a" / "x")), entry(str(tmp_path / "keep" / "x"))]
    keeper = cleaner.choose_keeper(files, "first", keep_in=[tmp_path / "keep"])
    assert keeper.path.parent.name == "keep"
    # If no file is inside keep_in, the rule applies to all of them.
    assert cleaner.choose_keeper(files, "first", keep_in=[tmp_path / "zzz"]).path.parent.name == "a"


def test_choose_keeper_rejects_unknown_rule():
    with pytest.raises(ValueError):
        cleaner.choose_keeper(FILES, "random")


def test_quarantine_target_mirrors_absolute_path(tmp_path):
    source = tmp_path / "photos" / "a.jpg"
    target = cleaner.quarantine_target(source, tmp_path / "q")
    assert target.name == "a.jpg"
    assert target.parent.name == "photos"
    assert str(target).startswith(str(tmp_path / "q"))
    assert ":" not in str(target.relative_to(tmp_path / "q"))


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "data"
    write(root / "a.txt", "same")
    write(root / "b.txt", "same")
    write(root / "sub" / "c.txt", "same")
    write(root / "big1.bin", b"z" * 200_000)
    write(root / "big2.bin", b"z" * 200_000)
    return root


def plans_for(root, rule="first"):
    return cleaner.make_plan(find_duplicates(iter_files([root])), rule)


def test_make_plan(tree):
    plans = plans_for(tree)
    assert len(plans) == 2
    big, small = plans
    assert big.keep.path.name == "big1.bin" and [f.path.name for f in big.remove] == ["big2.bin"]
    assert small.keep.path.name == "a.txt"
    assert sorted(f.path.name for f in small.remove) == ["b.txt", "c.txt"]
    assert big.freed == 200_000 and small.freed == 8


def test_apply_delete(tree, tmp_path):
    log = tmp_path / "log.jsonl"
    result = cleaner.apply_plan(plans_for(tree), "delete", log_path=log)
    assert len(result.done) == 3 and not result.failed
    assert result.freed == 200_008
    assert sorted(p.name for p in tree.rglob("*") if p.is_file()) == ["a.txt", "big1.bin"]
    records = cleaner.read_log(log)
    assert {r["action"] for r in records} == {"delete"} and len(records) == 3


def test_apply_hardlink_and_undo(tree, tmp_path):
    log = tmp_path / "log.jsonl"
    result = cleaner.apply_plan(plans_for(tree), "hardlink", log_path=log)
    assert len(result.done) == 3
    a, b = os.stat(tree / "a.txt"), os.stat(tree / "b.txt")
    assert (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    assert (tree / "b.txt").read_text() == "same"
    assert not list(tree.rglob("*.dupesweep-tmp"))

    outcomes = cleaner.undo(cleaner.read_log(log))
    assert all(o.ok for o in outcomes)
    a, b = os.stat(tree / "a.txt"), os.stat(tree / "b.txt")
    assert a.st_ino != b.st_ino
    assert (tree / "b.txt").read_text() == "same"


def test_apply_quarantine_and_undo(tree, tmp_path):
    q, log = tmp_path / "q", tmp_path / "log.jsonl"
    result = cleaner.apply_plan(plans_for(tree), "quarantine", quarantine_dir=q, log_path=log)
    assert len(result.done) == 3
    assert not (tree / "b.txt").exists() and not (tree / "sub" / "c.txt").exists()
    moved = sorted(p.name for p in q.rglob("*") if p.is_file())
    assert moved == ["b.txt", "big2.bin", "c.txt"]

    dry = cleaner.undo(cleaner.read_log(log), dry_run=True)
    assert all(o.ok for o in dry) and not (tree / "b.txt").exists()
    restored = cleaner.undo(cleaner.read_log(log))
    assert all(o.ok for o in restored)
    assert (tree / "sub" / "c.txt").read_text() == "same"
    assert not [p for p in q.rglob("*") if p.is_file()]


def test_changed_files_are_skipped(tree):
    plans = plans_for(tree)
    (tree / "b.txt").write_text("diff")  # same size, new content
    (tree / "sub" / "c.txt").write_text("longer now")
    result = cleaner.apply_plan(plans, "delete")
    messages = sorted(o.message for o in result.failed)
    assert messages == ["content changed since the scan", "size changed since the scan"]
    assert (tree / "b.txt").exists() and (tree / "sub" / "c.txt").exists()
    assert len(result.done) == 1  # big2.bin


def test_missing_duplicate_is_skipped(tree):
    plans = plans_for(tree)
    (tree / "big2.bin").unlink()
    result = cleaner.apply_plan(plans, "delete")
    assert [o.path.name for o in result.failed] == ["big2.bin"]
    assert "cannot stat" in result.failed[0].message


def test_undo_reports_what_cannot_be_restored(tree, tmp_path):
    log = tmp_path / "log.jsonl"
    cleaner.apply_plan(plans_for(tree), "delete", log_path=log)
    outcomes = cleaner.undo(cleaner.read_log(log))
    assert not any(o.ok for o in outcomes)
    assert outcomes[0].message == "deleted files cannot be restored"


def test_read_log_rejects_garbage(tmp_path):
    bad = write(tmp_path / "bad.jsonl", "not json\n")
    with pytest.raises(ValueError):
        cleaner.read_log(bad)


def test_apply_plan_validates_arguments(tree):
    with pytest.raises(ValueError):
        cleaner.apply_plan([], "shred")
    with pytest.raises(ValueError):
        cleaner.apply_plan([], "quarantine")
