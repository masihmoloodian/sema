"""Tests for incremental indexing (index_file) and the watcher handler."""

import time
import pytest

from sema.indexer.chunker import index_file
from sema.store.chroma import SemaStore
from sema.utils.watcher import _Handler


@pytest.fixture
def tmp_store(tmp_path):
    return SemaStore(tmp_path / "index")


# ── index_file ────────────────────────────────────────────────────────────────

def test_index_file_returns_chunk_count(tmp_path, tmp_store, embedder):
    f = tmp_path / "auth.ts"
    f.write_text("export function login(user: string): boolean { return true; }")
    n = index_file(f, tmp_path, tmp_store, embedder)
    assert n > 0


def test_index_file_chunks_are_searchable(tmp_path, tmp_store, embedder):
    f = tmp_path / "auth.ts"
    f.write_text("export function validateToken(token: string): boolean { return true; }")
    index_file(f, tmp_path, tmp_store, embedder)

    results = tmp_store.get_by_name("validateToken")
    assert len(results) == 1
    assert results[0]["name"] == "validateToken"


def test_index_file_replaces_old_chunks(tmp_path, tmp_store, embedder):
    f = tmp_path / "auth.ts"
    f.write_text("export function oldName(x: string): void {}")
    index_file(f, tmp_path, tmp_store, embedder)
    assert len(tmp_store.get_by_name("oldName")) == 1

    # Overwrite file with a renamed function and re-index
    f.write_text("export function newName(x: string): void {}")
    index_file(f, tmp_path, tmp_store, embedder)

    assert len(tmp_store.get_by_name("oldName")) == 0
    assert len(tmp_store.get_by_name("newName")) == 1


def test_index_file_unsupported_extension_returns_zero(tmp_path, tmp_store, embedder):
    f = tmp_path / "notes.xyz"
    f.write_text("some content")
    n = index_file(f, tmp_path, tmp_store, embedder)
    assert n == 0


def test_index_file_empty_file_returns_zero(tmp_path, tmp_store, embedder):
    f = tmp_path / "empty.ts"
    f.write_text("   ")
    n = index_file(f, tmp_path, tmp_store, embedder)
    assert n == 0


def test_index_file_deletion_removes_chunks(tmp_path, tmp_store, embedder):
    f = tmp_path / "utils.ts"
    f.write_text("export function helper(): void {}")
    index_file(f, tmp_path, tmp_store, embedder)
    assert len(tmp_store.get_by_name("helper")) == 1

    # Simulate deletion: delete_by_file is what index_file calls internally
    rel = str(f.relative_to(tmp_path))
    tmp_store.delete_by_file(rel)
    assert len(tmp_store.get_by_name("helper")) == 0


# ── _Handler debounce ─────────────────────────────────────────────────────────

def test_handler_debounces_rapid_saves(tmp_path, tmp_store, embedder):
    calls = []

    def on_indexed(path, n):
        calls.append((path, n))

    handler = _Handler(tmp_path, tmp_store, embedder, on_indexed)

    f = tmp_path / "rapid.ts"
    f.write_text("export function rapid(): void {}")

    # Fire 5 rapid saves — should collapse into one re-index
    for _ in range(5):
        handler._schedule(str(f), deleted=False)

    time.sleep(0.6)  # wait for debounce + processing
    assert len(calls) == 1


def test_handler_skips_index_directory(tmp_path, tmp_store, embedder):
    calls = []
    handler = _Handler(tmp_path, tmp_store, embedder, lambda p, n: calls.append(n))

    sema_file = tmp_path / ".sema" / "index" / "chroma.sqlite3"
    sema_file.parent.mkdir(parents=True, exist_ok=True)
    sema_file.write_text("data")

    handler._schedule(str(sema_file), deleted=False)
    time.sleep(0.6)
    assert calls == []


def test_handler_skips_unsupported_extension(tmp_path, tmp_store, embedder):
    calls = []
    handler = _Handler(tmp_path, tmp_store, embedder, lambda p, n: calls.append(n))

    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG")
    handler._schedule(str(f), deleted=False)
    time.sleep(0.6)
    assert calls == []
