"""Tests for call graph: get_callers, get_callees, impact_analysis, import extraction."""

import pytest
from sema.store.schema import Chunk
from sema.store.chroma import SemaStore
from sema.indexer.languages.typescript import extract_chunks as ts_extract
from sema.indexer.languages.python import extract_chunks as py_extract
from sema.indexer.languages.golang import extract_chunks as go_extract


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_chunk(name: str, calls: list[str], file: str = "a.ts") -> Chunk:
    return Chunk(
        id=f"{file}::{name}",
        file=file,
        language="typescript",
        chunk_type="function",
        name=name,
        signature=f"{name}(): void",
        body=f"function {name}() {{}}",
        start_line=1,
        end_line=2,
        calls=calls,
    )


@pytest.fixture
def store(tmp_path):
    return SemaStore(tmp_path / "index")


# ── get_callers / get_callees ─────────────────────────────────────────────────

def test_get_callees_returns_calls_list(store):
    store.upsert([_make_chunk("foo", ["bar", "baz"])], [[0.1] * 384])
    assert store.get_callees("foo") == ["bar", "baz"]


def test_get_callees_empty_when_no_calls(store):
    store.upsert([_make_chunk("foo", [])], [[0.1] * 384])
    assert store.get_callees("foo") == []


def test_get_callees_unknown_symbol(store):
    assert store.get_callees("nonexistent") == []


def test_get_callers_exact_match(store):
    store.upsert([
        _make_chunk("foo", ["bar"]),
        _make_chunk("baz", ["qux"]),
    ], [[0.1] * 384, [0.2] * 384])
    callers = store.get_callers("bar")
    assert len(callers) == 1
    assert callers[0]["name"] == "foo"


def test_get_callers_suffix_match(store):
    # "jwt.verify" in calls list should match query for "verify"
    store.upsert([_make_chunk("authenticate", ["jwt.verify", "checkExpiry"])], [[0.1] * 384])
    callers = store.get_callers("verify")
    assert len(callers) == 1
    assert callers[0]["name"] == "authenticate"


def test_get_callers_no_false_positives(store):
    # "verify" should NOT match "verifyAndDecode" or "preVerify"
    store.upsert([_make_chunk("foo", ["verifyAndDecode", "preVerify"])], [[0.1] * 384])
    assert store.get_callers("verify") == []


def test_get_callers_multiple_callers(store):
    store.upsert([
        _make_chunk("a", ["validate"], file="a.ts"),
        _make_chunk("b", ["validate"], file="b.ts"),
        _make_chunk("c", ["other"],    file="c.ts"),
    ], [[0.1] * 384, [0.2] * 384, [0.3] * 384])
    callers = store.get_callers("validate")
    caller_names = {c["name"] for c in callers}
    assert caller_names == {"a", "b"}


def test_get_callers_cache_built_once(store):
    store.upsert([_make_chunk("foo", ["bar"])], [[0.1] * 384])
    assert store._callers_cache is None
    store.get_callers("bar")
    assert store._callers_cache is not None
    # second call reuses cache
    cache_id = id(store._callers_cache)
    store.get_callers("bar")
    assert id(store._callers_cache) == cache_id


def test_get_callers_cache_invalidated_on_upsert(store):
    store.upsert([_make_chunk("foo", ["bar"])], [[0.1] * 384])
    store.get_callers("bar")
    assert store._callers_cache is not None
    store.upsert([_make_chunk("baz", ["bar"])], [[0.3] * 384])
    assert store._callers_cache is None  # invalidated


def test_get_callers_cache_invalidated_on_delete(store):
    store.upsert([_make_chunk("foo", ["bar"])], [[0.1] * 384])
    store.get_callers("bar")
    store.delete_by_file("a.ts")
    assert store._callers_cache is None


def test_get_callees_file_path_filter(store):
    # Same symbol name in two files — file_path should narrow which callees we get
    c1 = Chunk(
        id="x.ts::process:1", file="x.ts", language="typescript",
        chunk_type="function", name="process", signature="process(): void",
        body="", start_line=1, end_line=2, calls=["fetchData"],
    )
    c2 = Chunk(
        id="y.ts::process:1", file="y.ts", language="typescript",
        chunk_type="function", name="process", signature="process(): void",
        body="", start_line=1, end_line=2, calls=["saveData"],
    )
    store.upsert([c1, c2], [[0.1] * 384, [0.2] * 384])
    assert store.get_callees("process", file_path="x.ts") == ["fetchData"]
    assert store.get_callees("process", file_path="y.ts") == ["saveData"]


# ── impact_analysis tool ──────────────────────────────────────────────────────

def test_impact_analysis_shows_callees_and_callers(indexed_store):
    from sema.mcp.tools import init_tools, impact_analysis
    store, embedder = indexed_store
    init_tools(store, embedder)

    result = impact_analysis("validateToken")
    assert "Calls" in result
    assert "Called by" in result


def test_impact_analysis_callers_include_refreshToken(indexed_store):
    from sema.mcp.tools import init_tools, impact_analysis
    store, embedder = indexed_store
    init_tools(store, embedder)

    result = impact_analysis("validateToken")
    assert "refreshToken" in result
    assert "requireAuth" in result
    assert "optionalAuth" in result


def test_impact_analysis_multi_level_callees(indexed_store):
    from sema.mcp.tools import init_tools, impact_analysis
    store, embedder = indexed_store
    init_tools(store, embedder)

    # refreshToken calls validateToken which calls atob — depth=2 should show both levels
    result = impact_analysis("refreshToken", depth=2)
    assert "Level 1" in result
    assert "Level 2" in result
    assert "validateToken" in result


def test_impact_analysis_unknown_symbol(indexed_store):
    from sema.mcp.tools import init_tools, impact_analysis
    store, embedder = indexed_store
    init_tools(store, embedder)

    result = impact_analysis("totallyUnknownXYZ")
    assert "none detected" in result.lower() or "no indexed callers" in result.lower()


def test_impact_analysis_file_path_in_header(indexed_store):
    from sema.mcp.tools import init_tools, impact_analysis
    store, embedder = indexed_store
    init_tools(store, embedder)

    result = impact_analysis("validateToken", file_path="src/auth/jwt.ts")
    assert "src/auth/jwt.ts" in result


# ── import extraction ─────────────────────────────────────────────────────────

def test_typescript_imports_extracted():
    src = '''
import { User } from "../types";
import { validateToken } from "./jwt";

export function requireAuth(req: any): void {}
'''
    chunks = ts_extract(src, "middleware.ts")
    assert len(chunks) > 0
    imports = chunks[0].imports
    assert "../types" in imports
    assert "./jwt" in imports


def test_typescript_no_imports_returns_empty():
    src = "export function foo(): void {}"
    chunks = ts_extract(src, "foo.ts")
    assert chunks[0].imports == []


def test_python_imports_extracted():
    src = '''
from dataclasses import dataclass
import uuid

def create() -> None:
    pass
'''
    chunks = py_extract(src, "session.py")
    assert len(chunks) > 0
    imports = chunks[0].imports
    assert "dataclasses" in imports
    assert "uuid" in imports


def test_python_relative_import_extracted():
    src = '''
from .auth import validate

def check() -> None:
    pass
'''
    chunks = py_extract(src, "middleware.py")
    imports = chunks[0].imports
    assert any("auth" in imp for imp in imports)


def test_go_imports_extracted():
    src = '''
package main

import (
    "encoding/json"
    "net/http"
)

func Handle() {}
'''
    chunks = go_extract(src, "handler.go")
    assert len(chunks) > 0
    imports = chunks[0].imports
    assert "encoding/json" in imports
    assert "net/http" in imports


def test_all_chunks_in_file_share_imports():
    """Every chunk in a file should carry the file's imports."""
    src = '''
import { A } from "./a";
import { B } from "./b";

export function foo() {}
export function bar() {}
export function baz() {}
'''
    chunks = ts_extract(src, "multi.ts")
    assert len(chunks) == 3
    for c in chunks:
        assert "./a" in c.imports
        assert "./b" in c.imports


# ── explain_file import section ───────────────────────────────────────────────

def test_explain_file_shows_project_imports(indexed_store):
    from sema.mcp.tools import init_tools, explain_file
    store, embedder = indexed_store
    init_tools(store, embedder)

    result = explain_file("src/auth/middleware.ts")
    # middleware.ts imports from "./jwt" (relative — project import)
    assert "Imports (project)" in result
    assert "./jwt" in result


def test_explain_file_shows_package_imports(indexed_store):
    from sema.mcp.tools import init_tools, explain_file
    store, embedder = indexed_store
    init_tools(store, embedder)

    result = explain_file("handlers/auth.go")
    assert "Imports (packages)" in result
    assert "encoding/json" in result
    assert "net/http" in result


def test_explain_file_no_imports_section_when_empty(indexed_store):
    from sema.mcp.tools import init_tools, explain_file
    store, embedder = indexed_store
    init_tools(store, embedder)

    # routes.ts has no import statements in the fixture
    result = explain_file("src/api/routes.ts")
    assert "Imports" not in result
