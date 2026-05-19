"""Orchestrates: file discovery → parallel parsing → batched embedding → storing."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from .parser import parse_file
from .embedder import Embedder
from ..store.chroma import SemaStore
from ..store.schema import Chunk
from ..utils.file_walker import walk_project

EMBED_BATCH_SIZE = 200   # chunks per embedding call — larger = more efficient SBERT inference
PARSE_WORKERS = 8        # parallel file parsing threads (tree-sitter releases the GIL)


def index_project(
    project_root: Path,
    store: SemaStore,
    embedder: Embedder,
    reset: bool = False,
) -> dict:
    """
    Main indexing entry point.
    Returns stats dict: {files, chunks, languages}.

    Pipeline:
      1. Walk all files (fast)
      2. Parse all files in parallel via ThreadPoolExecutor
      3. Embed chunks in large batches (more efficient than many small batches)
      4. Store all at once
    """
    if reset:
        store.reset()

    all_files = list(walk_project(project_root))
    stats: dict = {"files": 0, "chunks": 0, "languages": {}}

    # ── Phase 1: parse all files in parallel ──────────────────────────────────
    all_chunks: list[Chunk] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
    ) as progress:
        parse_task = progress.add_task("Parsing...", total=len(all_files))

        with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as executor:
            futures = {
                executor.submit(parse_file, fp, project_root): fp
                for fp in all_files
            }
            for future in as_completed(futures):
                chunks = future.result()
                if chunks:
                    all_chunks.extend(chunks)
                    lang = chunks[0].language
                    stats["languages"][lang] = stats["languages"].get(lang, 0) + len(chunks)
                    stats["files"] += 1
                progress.advance(parse_task)

        # ── Phase 2: embed in large batches ───────────────────────────────────
        if all_chunks:
            embed_task = progress.add_task("Embedding...", total=len(all_chunks))

            for i in range(0, len(all_chunks), EMBED_BATCH_SIZE):
                batch = all_chunks[i: i + EMBED_BATCH_SIZE]
                _flush(batch, store, embedder)
                stats["chunks"] += len(batch)
                progress.advance(embed_task, len(batch))

    return stats


def index_file(
    file_path: Path,
    project_root: Path,
    store: SemaStore,
    embedder: Embedder,
) -> int:
    """Re-index a single file incrementally. Returns number of new chunks stored."""
    rel = str(file_path.relative_to(project_root))
    store.delete_by_file(rel)
    chunks = parse_file(file_path, project_root)
    if chunks:
        _flush(chunks, store, embedder)
    return len(chunks)


def _flush(chunks: list[Chunk], store: SemaStore, embedder: Embedder) -> None:
    texts = [c.embed_text() for c in chunks]
    embeddings = embedder.embed(texts)
    store.upsert(chunks, embeddings)
