"""Tests for auto-ignoring the local index (utils.gitignore.ensure_entry)."""

from sema.utils.gitignore import ensure_entry


def test_creates_gitignore_when_absent(tmp_path):
    result = ensure_entry(tmp_path, ".sema/")
    assert result == "created"
    assert (tmp_path / ".gitignore").read_text() == ".sema/\n"


def test_appends_to_end_preserving_existing(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("node_modules/\n*.log\n")
    result = ensure_entry(tmp_path, ".sema/")
    assert result == "appended"
    assert gi.read_text() == "node_modules/\n*.log\n.sema/\n"


def test_appends_newline_when_file_lacks_trailing_newline(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("dist/")  # no trailing newline
    assert ensure_entry(tmp_path, ".sema/") == "appended"
    assert gi.read_text() == "dist/\n.sema/\n"


def test_idempotent_when_already_present(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text(".sema/\n")
    assert ensure_entry(tmp_path, ".sema/") is None
    assert gi.read_text() == ".sema/\n"  # unchanged, no duplicate


def test_matches_regardless_of_slashes(tmp_path):
    # `.sema`, `/.sema/`, `/.sema` all mean the same ignore — none should be duplicated.
    for existing in (".sema", "/.sema/", "/.sema"):
        gi = tmp_path / ".gitignore"
        gi.write_text(f"{existing}\n")
        assert ensure_entry(tmp_path, ".sema/") is None
        assert gi.read_text() == f"{existing}\n"


def test_comment_mentioning_sema_does_not_count(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("# .sema/ is the index\nbuild/\n")
    assert ensure_entry(tmp_path, ".sema/") == "appended"
    assert gi.read_text() == "# .sema/ is the index\nbuild/\n.sema/\n"
