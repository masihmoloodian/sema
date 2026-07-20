"""MCP server entry point."""

from pathlib import Path
from .tools import mcp, init_tools, set_registry
from .registry import ProjectRegistry
from ..store.chroma import SemaStore
from ..indexer.embedder import Embedder
from . import devops_tools  # noqa: F401 — side-effect import, registers devops_* tools on `mcp`


def serve(project_root: Path, index_path: Path) -> None:
    """Start the MCP server for a single project. Called by `sema serve --project`."""
    store = SemaStore(index_path)
    embedder = Embedder()  # lazy — model not loaded until first search
    init_tools(store, embedder, project_root=project_root)
    mcp.run()              # blocks, listens on stdio


def serve_roots(roots: list[Path]) -> None:
    """Start the MCP server for every indexed project found under `roots`.

    Called by `sema serve --root`. Projects are discovered at startup and each
    project's store is built lazily on first query, so this stays fast even with
    many indexed projects under the roots.
    """
    embedder = Embedder()  # one shared model across all projects
    registry = ProjectRegistry.from_roots(roots, embedder)
    set_registry(registry)
    mcp.run()              # blocks, listens on stdio
