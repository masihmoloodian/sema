"""
MCP tool implementations.

Each tool returns the minimum tokens needed for Claude to make
its next decision. Full source is never returned unless explicitly
requested via get_code().

search_code and find_usages use hybrid search: semantic (vector) + BM25 (keyword)
merged with Reciprocal Rank Fusion. This covers both conceptual queries
("token validation logic") and exact-name queries ("validateToken").
"""

from mcp.server.fastmcp import FastMCP
from ..store.chroma import SemaStore
from ..store.bm25 import BM25Index
from ..indexer.embedder import Embedder
from ..utils.repo_map import generate_repo_map

# search_code only returns code symbols — never config/doc sections
_CODE_CHUNK_TYPES = ["function", "class", "method", "interface", "struct", "module"]

mcp = FastMCP("sema")
_store: SemaStore | None = None
_embedder: Embedder | None = None
_bm25: BM25Index | None = None


def init_tools(store: SemaStore, embedder: Embedder) -> None:
    global _store, _embedder, _bm25
    _store = store
    _embedder = embedder
    _bm25 = _build_bm25(store)


def _build_bm25(store: SemaStore) -> BM25Index | None:
    ids, metadatas = store.get_all_for_bm25()
    if not ids:
        return None
    # BM25 text: name + signature only — body causes too many false positives because
    # common tokens like "user" appear in every function that touches the domain model.
    texts = [
        f"{m['name']} {m['signature']}"
        for m in metadatas
    ]
    return BM25Index(ids, texts, metadatas)


def _rrf_merge(
    semantic: list[dict],
    bm25: list[dict],
    top_k: int,
    k: int = 60,
) -> list[dict]:
    """
    Asymmetric Reciprocal Rank Fusion.

    Semantic gets 2× weight vs BM25 — ensures BM25 can boost exact-name matches
    without overriding strong semantic signal on descriptive natural-language queries.
    Formula: score(d) = 2/(k+rank) for semantic + 1/(k+rank) for BM25.
    """
    rrf_scores: dict[str, float] = {}
    chunks: dict[str, dict] = {}

    for rank, r in enumerate(semantic):
        id_ = r["id"]
        rrf_scores[id_] = rrf_scores.get(id_, 0.0) + 2.0 / (k + rank + 1)  # 2× weight
        chunks[id_] = r

    for rank, r in enumerate(bm25):
        id_ = r["id"]
        rrf_scores[id_] = rrf_scores.get(id_, 0.0) + 1.0 / (k + rank + 1)  # 1× weight
        if id_ not in chunks:
            chunks[id_] = r

    sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)[:top_k]

    # Normalize scores to 0–1 for display
    max_score = rrf_scores[sorted_ids[0]] if sorted_ids else 1.0
    results = []
    for id_ in sorted_ids:
        r = dict(chunks[id_])
        r["score"] = rrf_scores[id_] / max_score
        results.append(r)

    return results


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

    top_k = min(top_k, 10)
    fetch_k = min(top_k * 3, 30)  # over-fetch from each source before merging

    embedding = embedder.embed_one(query)
    semantic = store.search(embedding, top_k=fetch_k, chunk_types=_CODE_CHUNK_TYPES)

    if _bm25:
        bm25_results = _bm25.search(query, top_k=fetch_k, chunk_types=_CODE_CHUNK_TYPES)
        # Only mix BM25 when it has confident hits — strong score means the query
        # contains exact symbol names or specific keywords. Low scores mean the
        # query is broad natural language where BM25 just adds noise.
        if bm25_results and bm25_results[0]["score"] >= 5.0:
            results = _rrf_merge(semantic, bm25_results, top_k=top_k)
        else:
            results = semantic[:top_k]
    else:
        results = semantic[:top_k]

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
    Uses hybrid search (BM25 + semantic) — finds call sites, imports, and type references.

    Args:
        symbol_name: The function or class name to find usages of.
    """
    store = _require_store()
    embedder = _require_embedder()

    results: list[dict] = []

    if _bm25:
        # BM25 finds exact name occurrences in bodies and signatures
        bm25_results = _bm25.search(symbol_name, top_k=20)
        # Exclude the definition itself — keep only callers/references
        results = [r for r in bm25_results if r["name"].lower() != symbol_name.lower()][:10]

    if not results:
        # Fallback: semantic search
        embedding = embedder.embed_one(f"calls uses imports {symbol_name}")
        sem_results = store.search(embedding, top_k=10)
        results = [
            r for r in sem_results
            if symbol_name.lower() in r["signature"].lower()
            or symbol_name.lower() in r["id"].lower()
        ]

    if not results:
        return f"No usages of '{symbol_name}' found in index."

    lines = [f"Usages of '{symbol_name}':\n"]
    for u in results:
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
