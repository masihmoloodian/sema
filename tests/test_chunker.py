"""Tests for the chunker orchestration layer."""

import shutil
from pathlib import Path
from sema.store.chroma import SemaStore
from sema.store.hashes import FileHashStore
from sema.indexer.embedder import Embedder
from sema.indexer.chunker import index_project

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "example-repo"


def test_index_project_returns_stats(tmp_path):
    store = SemaStore(tmp_path / "idx")
    embedder = Embedder()
    stats = index_project(FIXTURE_REPO, store, embedder)
    assert stats["files"] > 0
    assert stats["chunks"] > 0
    assert len(stats["languages"]) > 0


def test_index_project_covers_all_languages(tmp_path):
    store = SemaStore(tmp_path / "idx")
    embedder = Embedder()
    stats = index_project(FIXTURE_REPO, store, embedder)
    assert "typescript" in stats["languages"]
    assert "python" in stats["languages"]
    assert "go" in stats["languages"]


def test_index_project_reset_clears_existing(tmp_path):
    store = SemaStore(tmp_path / "idx")
    embedder = Embedder()
    index_project(FIXTURE_REPO, store, embedder)
    count_first = store.count()
    index_project(FIXTURE_REPO, store, embedder, reset=True)
    count_second = store.count()
    assert count_second == count_first


def test_incremental_skips_unchanged_files(tmp_path):
    store = SemaStore(tmp_path / "idx")
    embedder = Embedder()

    # First run — full index, hashes written
    stats1 = index_project(FIXTURE_REPO, store, embedder)
    assert stats1["skipped"] == 0
    assert stats1["files"] > 0
    assert (tmp_path / "idx").parent.joinpath("hashes.json").exists() or \
           (tmp_path / "hashes.json").exists() or \
           any((tmp_path).rglob("hashes.json"))

    # Second run — nothing changed, all files should be skipped
    stats2 = index_project(FIXTURE_REPO, store, embedder)
    assert stats2["files"] == 0
    assert stats2["skipped"] == stats1["files"] + stats1["skipped"]
    # Chunk count in store unchanged
    assert store.count() == stats1["chunks"]


def test_incremental_reindexes_changed_file(tmp_path):
    # Copy fixture to a writable tmp location so we can modify a file
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo, ignore=shutil.ignore_patterns(".sema"))

    store = SemaStore(tmp_path / "idx")
    embedder = Embedder()

    index_project(repo, store, embedder)
    count_before = store.count()

    # Modify one file
    jwt_file = repo / "src" / "auth" / "jwt.ts"
    jwt_file.write_text(jwt_file.read_text() + "\n// modified\n")

    stats = index_project(repo, store, embedder)
    assert stats["files"] == 1          # only the modified file re-indexed
    assert stats["skipped"] == 6        # the other 6 files unchanged
    assert store.count() == count_before  # chunk count stable (same functions)


def test_incremental_reset_clears_hashes(tmp_path):
    store = SemaStore(tmp_path / "idx")
    embedder = Embedder()

    index_project(FIXTURE_REPO, store, embedder)
    # After reset, all files should be re-indexed (skipped == 0)
    stats = index_project(FIXTURE_REPO, store, embedder, reset=True)
    assert stats["skipped"] == 0
    assert stats["files"] > 0


def test_incremental_removes_stale_hashes_for_deleted_files(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo, ignore=shutil.ignore_patterns(".sema"))

    store = SemaStore(tmp_path / "idx")
    embedder = Embedder()
    index_project(repo, store, embedder)

    # Delete a file
    deleted = repo / "src" / "auth" / "middleware.ts"
    deleted.unlink()

    index_project(repo, store, embedder)
    # The deleted file should not be in the index
    results = store.get_by_name("requireAuth")
    assert results == []
    # Its hash entry should be gone (won't be re-checked next run)
    hash_store = FileHashStore(store.index_path.parent)
    assert "src/auth/middleware.ts" not in hash_store.known_paths()
