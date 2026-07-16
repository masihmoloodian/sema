"""Small JSONL query worker used by editor integrations.

Keeping this process alive avoids reloading SBERT for every search. It is local,
offline, and intentionally private; the public CLI and MCP APIs stay unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .indexer.embedder import Embedder
from .mcp.registry import ProjectHandle
from .mcp.tools import _CODE_CHUNK_TYPES, _rrf_merge


class QueryEngine:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.embedder = Embedder()
        self.handle = ProjectHandle(
            self.project_root.name,
            self.project_root,
            self.project_root / ".sema" / "index",
            self.embedder,
        )

    def warm(self) -> None:
        self.embedder.embed_one("semantic code search")

    def search(self, query: str, top_k: int = 8) -> dict:
        self.handle.refresh_if_changed()
        store = self.handle.store
        top_k = max(1, min(int(top_k), 10))
        fetch_k = min(top_k * 3, 30)
        semantic = store.search(
            self.embedder.embed_one(query),
            top_k=fetch_k,
            chunk_types=_CODE_CHUNK_TYPES,
        )
        bm25 = self.handle.bm25
        if bm25:
            keyword = bm25.search(query, top_k=fetch_k, chunk_types=_CODE_CHUNK_TYPES)
            results = (
                _rrf_merge(semantic, keyword, top_k=top_k)
                if keyword and keyword[0]["score"] >= 5.0
                else semantic[:top_k]
            )
        else:
            results = semantic[:top_k]
        return {
            "query": query,
            "results": [
                {
                    "file": r["file"],
                    "name": r["name"],
                    "type": r["type"],
                    "signature": r["signature"],
                    "start_line": r["start_line"],
                    "score": round(float(r["score"]), 4),
                }
                for r in results
            ],
        }

    def get(self, symbol: str) -> dict:
        self.handle.refresh_if_changed()
        return {"implementations": self.handle.store.get_by_name(symbol)}


def serve_query_worker(project_root: Path) -> None:
    engine = QueryEngine(project_root)
    engine.warm()
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            command = request.get("command")
            if command == "search":
                result = engine.search(str(request.get("query", "")), int(request.get("top_k", 8)))
            elif command == "get":
                result = engine.get(str(request.get("symbol", "")))
            else:
                raise ValueError(f"unknown query command: {command}")
            response = {"id": request.get("id"), "result": result}
        except Exception as exc:  # keep the worker alive after a malformed/failed request
            response = {"id": request.get("id") if "request" in locals() else None, "error": str(exc)}
        print(json.dumps(response), flush=True)
