"""Tests for SemaStore ChromaDB wrapper."""

import pytest
from sema.store.schema import Chunk
from sema.store.chroma import SemaStore


@pytest.fixture
def store(tmp_path):
    return SemaStore(tmp_path / "index")


def _make_chunk(name: str, file: str = "test.ts", language: str = "typescript") -> Chunk:
    return Chunk(
        id=f"{file}::{name}",
        file=file,
        language=language,
        chunk_type="function",
        name=name,
        signature=f"{name}(arg: string): void",
        body=f"function {name}(arg: string) {{ return arg; }}",
        start_line=1,
        end_line=3,
    )


def test_upsert_and_count(store):
    chunk = _make_chunk("foo")
    store.upsert([chunk], [[0.1] * 384])
    assert store.count() == 1


def test_upsert_idempotent(store):
    chunk = _make_chunk("foo")
    store.upsert([chunk], [[0.1] * 384])
    store.upsert([chunk], [[0.1] * 384])
    assert store.count() == 1


def test_get_by_name(store):
    chunk = _make_chunk("myFunc")
    store.upsert([chunk], [[0.1] * 384])
    results = store.get_by_name("myFunc")
    assert len(results) == 1
    assert results[0]["name"] == "myFunc"
    assert "function myFunc" in results[0]["body"]


def test_get_by_name_multiple(store):
    c1 = _make_chunk("doThing", file="a.ts")
    c2 = _make_chunk("doThing", file="b.ts")
    store.upsert([c1, c2], [[0.1] * 384, [0.2] * 384])
    results = store.get_by_name("doThing")
    assert len(results) == 2
    files = {r["file"] for r in results}
    assert files == {"a.ts", "b.ts"}


def test_get_by_name_not_found(store):
    assert store.get_by_name("doesNotExist") == []


def test_delete_by_file(store):
    c1 = _make_chunk("alpha", file="a.ts")
    c2 = _make_chunk("beta", file="b.ts")
    store.upsert([c1, c2], [[0.1] * 384, [0.2] * 384])
    assert store.count() == 2
    store.delete_by_file("a.ts")
    assert store.count() == 1


def test_reset(store):
    chunk = _make_chunk("foo")
    store.upsert([chunk], [[0.1] * 384])
    store.reset()
    assert store.count() == 0


def test_get_all_metadata(store):
    c1 = _make_chunk("alpha")
    c2 = _make_chunk("beta")
    store.upsert([c1, c2], [[0.1] * 384, [0.2] * 384])
    meta = store.get_all_metadata()
    names = {m["name"] for m in meta}
    assert "alpha" in names
    assert "beta" in names
