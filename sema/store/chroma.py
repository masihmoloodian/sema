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

    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Store chunks with their embeddings."""
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

    def delete_by_file(self, file_path: str) -> None:
        """Remove all chunks for a file — for incremental re-index."""
        self.collection.delete(where={"file": file_path})

    def reset(self) -> None:
        """Delete and recreate the collection."""
        self.client.delete_collection(self.COLLECTION_NAME)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return self.collection.count()
