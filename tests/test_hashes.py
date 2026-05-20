"""Tests for FileHashStore incremental indexing."""

import pytest
from pathlib import Path
from sema.store.hashes import FileHashStore


@pytest.fixture
def sema_dir(tmp_path):
    d = tmp_path / ".sema"
    d.mkdir()
    return d


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_new_store_is_empty(sema_dir):
    hs = FileHashStore(sema_dir)
    assert hs.known_paths() == set()


def test_unknown_file_is_not_unchanged(sema_dir, tmp_path):
    hs = FileHashStore(sema_dir)
    f = _write(tmp_path / "foo.ts", "hello")
    assert hs.is_unchanged("foo.ts", f) is False


def test_update_then_is_unchanged(sema_dir, tmp_path):
    hs = FileHashStore(sema_dir)
    f = _write(tmp_path / "foo.ts", "hello")
    hs.update("foo.ts", f)
    assert hs.is_unchanged("foo.ts", f) is True


def test_modified_file_is_not_unchanged(sema_dir, tmp_path):
    hs = FileHashStore(sema_dir)
    f = _write(tmp_path / "foo.ts", "hello")
    hs.update("foo.ts", f)
    f.write_text("hello world")  # change content
    assert hs.is_unchanged("foo.ts", f) is False


def test_save_and_reload(sema_dir, tmp_path):
    hs = FileHashStore(sema_dir)
    f = _write(tmp_path / "foo.ts", "hello")
    hs.update("foo.ts", f)
    hs.save()

    hs2 = FileHashStore(sema_dir)
    assert hs2.is_unchanged("foo.ts", f) is True


def test_remove_clears_entry(sema_dir, tmp_path):
    hs = FileHashStore(sema_dir)
    f = _write(tmp_path / "foo.ts", "hello")
    hs.update("foo.ts", f)
    hs.remove("foo.ts")
    assert hs.is_unchanged("foo.ts", f) is False
    assert "foo.ts" not in hs.known_paths()


def test_clear_empties_all(sema_dir, tmp_path):
    hs = FileHashStore(sema_dir)
    f = _write(tmp_path / "foo.ts", "hello")
    g = _write(tmp_path / "bar.ts", "world")
    hs.update("foo.ts", f)
    hs.update("bar.ts", g)
    hs.clear()
    assert hs.known_paths() == set()


def test_known_paths_returns_all_tracked(sema_dir, tmp_path):
    hs = FileHashStore(sema_dir)
    f = _write(tmp_path / "a.ts", "a")
    g = _write(tmp_path / "b.py", "b")
    hs.update("a.ts", f)
    hs.update("b.py", g)
    assert hs.known_paths() == {"a.ts", "b.py"}


def test_corrupted_hashes_file_returns_empty(sema_dir):
    (sema_dir / "hashes.json").write_text("not json {{")
    hs = FileHashStore(sema_dir)
    assert hs.known_paths() == set()
