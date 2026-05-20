"""
ChromaDB wrapper.

One collection per project (keyed by project root). Body stored in metadata
(not embedded) — retrieved separately. Embedded mode only, no server process.
"""

import chromadb
from pathlib import Path
from .schema import Chunk


class SemaStore:
    COLLECTION_NAME = "sema_chunks"

    def __init__(self, index_path: Path):
        self.client = chromadb.PersistentClient(path=str(index_path))
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        # Inverted index: callee_name -> list[caller_chunk_info]
        # Built lazily on first get_callers() call, invalidated on writes.
        self._callers_cache: dict[str, list[dict]] | None = None

    def _build_callers_cache(self) -> dict[str, list[dict]]:
        cache: dict[str, list[dict]] = {}
        results = self.collection.get(include=["metadatas"])
        for meta in results["metadatas"]:
            calls_str = meta.get("calls", "")
            if not calls_str:
                continue
            caller_info = {
                "name": meta["name"],
                "file": meta["file"],
                "start_line": meta["start_line"],
                "chunk_type": meta["chunk_type"],
                "signature": meta["signature"],
            }
            for callee in calls_str.split(","):
                callee = callee.strip()
                if callee:
                    cache.setdefault(callee, []).append(caller_info)
        return cache

    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Store chunks with their embeddings."""
        self._callers_cache = None
        self.collection.upsert(
            ids=[c.id for c in chunks],
            embeddings=embeddings,
            metadatas=[{
                "file": c.file,
                "language": c.language,
                "chunk_type": c.chunk_type,
                "name": c.name,
                "signature": c.signature,
                "body": c.body,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "exports": str(c.exports),
                "parent_name": c.parent_name or "",
                "calls": ",".join(c.calls),
                "imports": ",".join(c.imports),
            } for c in chunks],
            documents=[c.embed_text() for c in chunks],
        )

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        language: str | None = None,
        chunk_types: list[str] | None = None,
    ) -> list[dict]:
        """Semantic search. Returns search results with signatures only."""
        where: dict = {}
        if language:
            where["language"] = language
        if chunk_types:
            where["chunk_type"] = {"$in": chunk_types}

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self.collection.count() or 1),
            where=where if where else None,
            include=["metadatas", "distances"],
        )

        return [
            {
                "id": results["ids"][0][i],
                "file": m["file"],
                "name": m["name"],
                "type": m["chunk_type"],
                "signature": m["signature"],
                "start_line": m["start_line"],
                "language": m["language"],
                "score": 1 - results["distances"][0][i],
            }
            for i, m in enumerate(results["metadatas"][0])
        ]

    def get_by_name(self, name: str) -> list[dict]:
        """Exact lookup by symbol name — returns all matches (multiple files may define same name)."""
        results = self.collection.get(
            where={"name": name},
            include=["metadatas"],
        )
        return [
            {
                "name": m["name"],
                "body": m["body"],
                "file": m["file"],
                "start_line": m["start_line"],
                "end_line": m["end_line"],
                "chunk_type": m["chunk_type"],
            }
            for m in results["metadatas"]
        ]

    def get_all_metadata(self) -> list[dict]:
        """Get all chunk metadata — for repo_map() tool."""
        results = self.collection.get(include=["metadatas"])
        return results["metadatas"]

    def get_all_for_bm25(self) -> tuple[list[str], list[dict]]:
        """Get all ids + metadata — for building the BM25 index at startup."""
        results = self.collection.get(include=["metadatas"])
        return results["ids"], results["metadatas"]

    def get_callers(self, symbol_name: str) -> list[dict]:
        """Find all chunks that call symbol_name.

        Matches exact names and qualified names (e.g. querying "verify"
        also matches callers that recorded "jwt.verify").
        Uses an in-memory inverted index built once per store lifetime.
        """
        if self._callers_cache is None:
            self._callers_cache = self._build_callers_cache()

        seen: set[tuple] = set()
        results: list[dict] = []

        def _add(callers: list[dict]) -> None:
            for c in callers:
                key = (c["file"], c["name"], c["start_line"])
                if key not in seen:
                    seen.add(key)
                    results.append(c)

        _add(self._callers_cache.get(symbol_name, []))
        # Also match qualified names: "jwt.verify" satisfies a query for "verify"
        suffix = f".{symbol_name}"
        for key, callers in self._callers_cache.items():
            if key.endswith(suffix):
                _add(callers)

        return results

    def get_callees(self, symbol_name: str, file_path: str | None = None) -> list[str]:
        """Get the list of symbol names called by symbol_name.

        file_path: optional — narrow to a specific file when multiple files
        define the same symbol name.
        """
        where: dict
        if file_path:
            where = {"$and": [{"name": symbol_name}, {"file": file_path}]}
        else:
            where = {"name": symbol_name}
        results = self.collection.get(where=where, include=["metadatas"])
        calls: set[str] = set()
        for meta in results["metadatas"]:
            calls_str = meta.get("calls", "")
            for c in calls_str.split(","):
                c = c.strip()
                if c:
                    calls.add(c)
        return sorted(calls)

    def delete_by_file(self, file_path: str) -> None:
        """Remove all chunks for a file — for incremental re-index."""
        self._callers_cache = None
        self.collection.delete(where={"file": file_path})

    def reset(self) -> None:
        """Delete and recreate the collection."""
        self._callers_cache = None
        self.client.delete_collection(self.COLLECTION_NAME)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return self.collection.count()
