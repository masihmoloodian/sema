"""MCP server entry point."""

from pathlib import Path
from .tools import mcp, init_tools
from ..store.chroma import SemaStore
from ..indexer.embedder import Embedder


def serve(project_root: Path, index_path: Path) -> None:
    """Start the MCP server. Called by `sema serve` and by Claude Code."""
    store = SemaStore(index_path)
    embedder = Embedder()  # lazy — model not loaded until first search
    init_tools(store, embedder)
    mcp.run()              # blocks, listens on stdio
