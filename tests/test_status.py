"""Tests for content-aware stale-index detection."""

from sema.cli import _detect_index_changes
from sema.store.hashes import FileHashStore


def track_all(project):
    hashes = FileHashStore(project / ".sema")
    for path in sorted(project.glob("*.ts")):
        hashes.update(path.name, path)
    hashes.save()


def test_detect_index_changes_fresh(tmp_path):
    (tmp_path / "one.ts").write_text("export const one = 1")
    track_all(tmp_path)
    assert _detect_index_changes(tmp_path) == (0, 0)


def test_detect_index_changes_modified_new_and_deleted(tmp_path):
    one = tmp_path / "one.ts"
    two = tmp_path / "two.ts"
    one.write_text("export const one = 1")
    two.write_text("export const two = 2")
    track_all(tmp_path)

    one.write_text("export const one = 99")
    two.unlink()
    (tmp_path / "three.ts").write_text("export const three = 3")
    assert _detect_index_changes(tmp_path) == (2, 1)
