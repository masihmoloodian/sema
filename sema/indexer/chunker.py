"""Orchestrates: file discovery → parsing → embedding → storing."""

from pathlib import Path
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from .parser import parse_file
from .embedder import Embedder
from ..store.chroma import SemaStore
from ..store.schema import Chunk
from ..utils.file_walker import walk_project

BATCH_SIZE = 50


def index_project(
    project_root: Path,
    store: SemaStore,
    embedder: Embedder,
    reset: bool = False,
) -> dict:
    """
    Main indexing entry point.
    Returns stats dict: {files, chunks, languages}.
    """
    if reset:
        store.reset()

    all_files = list(walk_project(project_root))
    stats: dict = {"files": 0, "chunks": 0, "languages": {}}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
    ) as progress:
        task = progress.add_task("Indexing...", total=len(all_files))

        chunk_buffer: list[Chunk] = []

        for file_path in all_files:
            chunks = parse_file(file_path, project_root)
            if chunks:
                chunk_buffer.extend(chunks)
                lang = chunks[0].language
                stats["languages"][lang] = stats["languages"].get(lang, 0) + len(chunks)
                stats["files"] += 1

            if len(chunk_buffer) >= BATCH_SIZE:
                _flush(chunk_buffer, store, embedder)
                stats["chunks"] += len(chunk_buffer)
                chunk_buffer = []

            progress.advance(task)

        if chunk_buffer:
            _flush(chunk_buffer, store, embedder)
            stats["chunks"] += len(chunk_buffer)

    return stats


def _flush(chunks: list[Chunk], store: SemaStore, embedder: Embedder) -> None:
    texts = [c.embed_text() for c in chunks]
    embeddings = embedder.embed(texts)
    store.upsert(chunks, embeddings)
