"""Shared pytest fixtures."""

import pytest
from pathlib import Path

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "example-repo"


@pytest.fixture(scope="session")
def fixture_repo() -> Path:
    return FIXTURE_REPO


@pytest.fixture(scope="session")
def embedder():
    """Session-scoped embedder — model loaded once, reused across all tests."""
    from sema.indexer.embedder import Embedder
    e = Embedder()
    e.embed_one("warmup")  # pre-load model into memory before any test uses it
    return e


@pytest.fixture(scope="session")
def indexed_store(tmp_path_factory):
    """
    Index the example-repo fixture and return (store, embedder).
    Scoped to session so we only pay the embedding cost once.
    """
    from sema.store.chroma import SemaStore
    from sema.indexer.embedder import Embedder
    from sema.indexer.chunker import index_project

    index_path = tmp_path_factory.mktemp("sema_index")
    store = SemaStore(index_path)
    embedder = Embedder()
    index_project(FIXTURE_REPO, store, embedder)
    return store, embedder
