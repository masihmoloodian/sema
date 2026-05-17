"""
MCP tool implementations.

Each tool returns the minimum tokens needed for Claude to make
its next decision. Full source is never returned unless explicitly
requested via get_code().
"""

from mcp.server.fastmcp import FastMCP
from ..store.chroma import SemaStore
from ..indexer.embedder import Embedder
from ..utils.repo_map import generate_repo_map

mcp = FastMCP("sema")
_store: SemaStore | None = None
_embedder: Embedder | None = None


def init_tools(store: SemaStore, embedder: Embedder) -> None:
    global _store, _embedder
    _store = store
    _embedder = embedder


def _require_store() -> SemaStore:
    if _store is None:
        raise RuntimeError("Store not initialized. Call init_tools() first.")
    return _store


def _require_embedder() -> Embedder:
    if _embedder is None:
        raise RuntimeError("Embedder not initialized. Call init_tools() first.")
    return _embedder


@mcp.tool()
def search_code(query: str, top_k: int = 5) -> str:
    """
    Semantic search across the indexed codebase.
    Returns function/class signatures and file locations.
    Use this BEFORE reading any files to find relevant code.

    Args:
        query: Natural language description of what you're looking for.
               Examples: "JWT token validation", "database connection pool", "error handler"
        top_k: Number of results (default 5, max 10)

    Returns signatures only — call get_code() if you need the full implementation.
    """
    store = _require_store()
    embedder = _require_embedder()

    embedding = embedder.embed_one(query)
    results = store.search(embedding, top_k=min(top_k, 10))

    if not results:
        return "No results found. The codebase may not be indexed. Run: sema index ."

    lines = [f"Found {len(results)} results for '{query}':\n"]
    for r in results:
        score_pct = int(r["score"] * 100)
        lines.append(
            f"  {r['file']}::{r['name']}  [line {r['start_line']}]  ({score_pct}% match)\n"
            f"    {r['type']}: {r['signature']}\n"
        )
    return "\n".join(lines)


@mcp.tool()
def get_code(symbol_name: str) -> str:
    """
    Get the full source of a specific function, class, or method by name.
    Only call this after search_code() identified the symbol you need.
    Returns ALL implementations if multiple files define the same symbol name
    (e.g. a controller method and a service method both named "forgotPassword").

    Args:
        symbol_name: Exact name of the function or class (e.g. "validateToken")
    """
    store = _require_store()

    results = store.get_by_name(symbol_name)
    if not results:
        return f"Symbol '{symbol_name}' not found. Use search_code() to find it first."

    parts = []
    for r in results:
        parts.append(
            f"// {r['file']} ({r['chunk_type']}) — lines {r['start_line']}-{r['end_line']}\n"
            f"{r['body']}"
        )
    return "\n\n---\n\n".join(parts)


@mcp.tool()
def repo_map() -> str:
    """
    Returns a compressed map of the entire codebase.
    Shows file structure and exported symbols — no source code.
    Use this at the start of a session to understand the architecture.
    Token cost: ~400-800 tokens for a medium project.
    """
    store = _require_store()
    all_metadata = store.get_all_metadata()
    return generate_repo_map(all_metadata)


@mcp.tool()
def find_usages(symbol_name: str) -> str:
    """
    Find all places where a function or class is referenced.
    Uses semantic search — finds call sites, imports, and type references.

    Args:
        symbol_name: The function or class name to find usages of.
    """
    store = _require_store()
    embedder = _require_embedder()

    embedding = embedder.embed_one(f"calls uses imports {symbol_name}")
    results = store.search(embedding, top_k=10)

    usages = [
        r for r in results
        if symbol_name.lower() in r["signature"].lower()
        or symbol_name.lower() in r["id"].lower()
    ]

    if not usages:
        return f"No usages of '{symbol_name}' found in index."

    lines = [f"Usages of '{symbol_name}':\n"]
    for u in usages:
        lines.append(
            f"  {u['file']}::{u['name']}  [line {u['start_line']}]\n"
            f"    {u['signature']}\n"
        )
    return "\n".join(lines)


@mcp.tool()
def explain_file(file_path: str) -> str:
    """
    Returns a summary of a file: its purpose, exports, and key dependencies.
    Does NOT return the full source — use get_code() for that.

    Args:
        file_path: Relative path from project root (e.g. "src/auth/jwt.ts")
    """
    store = _require_store()
    all_meta = store.get_all_metadata()
    file_chunks = [m for m in all_meta if m["file"] == file_path]

    if not file_chunks:
        return f"File '{file_path}' not found in index. Check the path is relative to project root."

    exports = [m for m in file_chunks if m.get("exports") == "True"]
    functions = [m for m in file_chunks if m["chunk_type"] == "function"]
    classes = [m for m in file_chunks if m["chunk_type"] == "class"]

    lines = [f"File: {file_path}\n"]
    if exports:
        lines.append(f"Exports ({len(exports)}):")
        for e in exports[:10]:
            lines.append(f"  {e['chunk_type']} {e['name']}: {e['signature']}")
    if classes:
        lines.append(f"\nClasses ({len(classes)}):")
        for c in classes:
            lines.append(f"  {c['name']}: {c['signature']}")
    if functions:
        lines.append(f"\nFunctions ({len(functions)}):")
        for f in functions[:10]:
            lines.append(f"  {f['name']}: {f['signature']}")

    return "\n".join(lines)
