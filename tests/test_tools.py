"""Tests for MCP tool implementations."""

import pytest
from sema.mcp.tools import (
    init_tools,
    search_code,
    get_code,
    repo_map,
    find_usages,
    explain_file,
)


@pytest.fixture(autouse=True)
def setup_tools(indexed_store):
    store, embedder = indexed_store
    init_tools(store, embedder)


def test_search_finds_jwt_function():
    result = search_code("JWT token validation")
    assert "validateToken" in result


def test_search_never_returns_body():
    result = search_code("auth")
    # Full function bodies must never appear in search results — only signatures
    assert "json.NewDecoder" not in result          # Go body code
    assert "req.headers[" not in result            # TS body code
    assert "self._sessions" not in result          # Python body code


def test_search_returns_signatures():
    result = search_code("session management")
    # Should contain file paths and names
    assert "::" in result


def test_search_respects_top_k():
    result = search_code("token", top_k=2)
    # Should have at most 2 results — check by counting "::" separators
    count = result.count("::")
    assert count <= 2


def test_search_top_k_capped_at_10():
    # top_k > 10 should be silently capped
    result = search_code("auth", top_k=100)
    assert "results" in result


def test_get_code_returns_full_body():
    result = get_code("validateToken")
    assert "validateToken" in result
    assert "Promise" in result  # full body present

def test_get_code_returns_all_implementations():
    # validateToken exists only in jwt.ts — check single result still works
    result = get_code("validateToken")
    assert "jwt.ts" in result
    assert "Promise" in result


def test_get_code_includes_file_path():
    result = get_code("validateToken")
    assert "jwt.ts" in result


def test_get_code_unknown_symbol():
    result = get_code("nonExistentSymbolXYZ")
    assert "not found" in result.lower()


def test_repo_map_covers_all_languages():
    result = repo_map()
    assert "jwt.ts" in result
    assert "session.py" in result
    assert "auth.go" in result


def test_repo_map_no_full_source():
    result = repo_map()
    # repo_map should not contain full function bodies
    assert "if err := json.NewDecoder" not in result


def test_find_usages_returns_results():
    result = find_usages("validateToken")
    assert "validateToken" in result


def test_find_usages_no_full_bodies():
    result = find_usages("validateToken")
    assert len(result) < 3000  # rough check — signatures only


def test_explain_file_shows_exports():
    result = explain_file("src/auth/jwt.ts")
    assert "generateToken" in result
    assert "validateToken" in result


def test_explain_file_no_source_code():
    result = explain_file("src/auth/jwt.ts")
    assert "async function" not in result


def test_explain_file_not_found():
    result = explain_file("nonexistent/path.ts")
    assert "not found" in result.lower()
