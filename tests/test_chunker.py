"""Tests for the chunker orchestration layer."""

import pytest
from pathlib import Path
from sema.store.chroma import SemaStore
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
