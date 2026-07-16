"""Tests for multi-project support: discovery, naming, resolution, and tools."""

import pytest
from pathlib import Path

from sema.mcp.registry import (
    ProjectRegistry,
    ProjectResolutionError,
    discover_projects,
    assign_names,
)

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "example-repo"


# The `multi_root` fixture (two indexed projects + one un-indexed folder) is
# defined in conftest.py so the reuse-guard tests can share it.


# ── discovery ────────────────────────────────────────────────────────────────

def test_discover_finds_indexed_projects(multi_root):
    found = discover_projects([multi_root])
    roots = {pr.name for pr, _ip in found}
    assert roots == {"proj-a", "proj-b"}


def test_discover_ignores_unindexed_dirs(multi_root):
    found = discover_projects([multi_root])
    assert all(pr.name != "not-a-project" for pr, _ip in found)


def test_discover_returns_index_paths(multi_root):
    found = dict(discover_projects([multi_root]))
    for pr, ip in found.items():
        assert ip == pr / ".sema" / "index"
        assert ip.is_dir()


def test_discover_empty_index_dir_ignored(tmp_path):
    (tmp_path / "empty" / ".sema" / "index").mkdir(parents=True)  # no files inside
    assert discover_projects([tmp_path]) == []


# ── naming ───────────────────────────────────────────────────────────────────

def test_assign_names_uses_basename_when_unique():
    roots = [Path("/code/backend"), Path("/code/frontend")]
    names = assign_names(roots)
    assert names[Path("/code/backend")] == "backend"
    assert names[Path("/code/frontend")] == "frontend"


def test_assign_names_disambiguates_collisions():
    roots = [Path("/code/backend/api"), Path("/code/web/api")]
    names = assign_names(roots)
    assert names[Path("/code/backend/api")] == "backend/api"
    assert names[Path("/code/web/api")] == "web/api"
    assert len(set(names.values())) == 2


# ── resolution ───────────────────────────────────────────────────────────────

def test_registry_from_roots_resolves_by_name(multi_root, embedder):
    reg = ProjectRegistry.from_roots([multi_root], embedder)
    assert set(reg.names()) == {"proj-a", "proj-b"}
    handle = reg.resolve("proj-a")
    assert handle.name == "proj-a"


def test_registry_ambiguous_without_name_raises(multi_root, embedder):
    reg = ProjectRegistry.from_roots([multi_root], embedder)
    with pytest.raises(ProjectResolutionError) as exc:
        reg.resolve(None)
    assert "proj-a" in str(exc.value) and "proj-b" in str(exc.value)


def test_registry_unknown_name_raises(multi_root, embedder):
    reg = ProjectRegistry.from_roots([multi_root], embedder)
    with pytest.raises(ProjectResolutionError) as exc:
        reg.resolve("nope")
    assert "not found" in str(exc.value)


def test_registry_single_resolves_without_name(indexed_store):
    store, embedder = indexed_store
    reg = ProjectRegistry.from_single(store, embedder, FIXTURE_REPO)
    # No name needed when there's exactly one project.
    assert reg.resolve(None).store is store


def test_registry_lazy_store_built_once(multi_root, embedder):
    reg = ProjectRegistry.from_roots([multi_root], embedder)
    handle = reg.resolve("proj-a")
    assert handle._store is None          # not built until accessed
    s1 = handle.store
    s2 = handle.store
    assert s1 is s2                       # cached


def test_registry_reloads_cached_readers_after_index_commit(multi_root, embedder):
    reg = ProjectRegistry.from_roots([multi_root], embedder)
    handle = reg.resolve("proj-a")
    old_store = handle.store
    old_bm25 = handle.bm25

    meta = handle.index_path.parent / "meta.json"
    meta.write_text('{"indexed_at":"new revision with a different size"}')

    refreshed = reg.resolve("proj-a")
    assert refreshed is handle
    assert refreshed.store is not old_store
    assert refreshed.bm25 is not old_bm25


# ── tools via registry ───────────────────────────────────────────────────────

def test_list_projects_multi(multi_root, embedder):
    from sema.mcp.tools import set_registry, list_projects
    set_registry(ProjectRegistry.from_roots([multi_root], embedder))
    out = list_projects()
    assert "proj-a" in out and "proj-b" in out
    assert "2 projects" in out


def test_search_code_requires_project_when_ambiguous(multi_root, embedder):
    from sema.mcp.tools import set_registry, search_code
    set_registry(ProjectRegistry.from_roots([multi_root], embedder))
    out = search_code("jwt token")
    assert "Multiple projects" in out
    assert "proj-a" in out


def test_search_code_with_project_returns_results(multi_root, embedder):
    from sema.mcp.tools import set_registry, search_code
    set_registry(ProjectRegistry.from_roots([multi_root], embedder))
    out = search_code("jwt token generation", project="proj-a")
    assert "generateToken" in out


def test_get_code_with_project(multi_root, embedder):
    from sema.mcp.tools import set_registry, get_code
    set_registry(ProjectRegistry.from_roots([multi_root], embedder))
    out = get_code("generateToken", project="proj-b")
    assert "function generateToken" in out


def test_unknown_project_lists_available(multi_root, embedder):
    from sema.mcp.tools import set_registry, get_code
    set_registry(ProjectRegistry.from_roots([multi_root], embedder))
    out = get_code("generateToken", project="ghost")
    assert "not found" in out and "proj-a" in out


def test_single_project_tools_ignore_project_arg(indexed_store):
    """Backward compat: with one project, tools work with no project argument."""
    from sema.mcp.tools import init_tools, search_code, list_projects
    store, embedder = indexed_store
    init_tools(store, embedder, project_root=FIXTURE_REPO)
    assert "generateToken" in search_code("jwt token generation")
    assert "1 project" in list_projects()
