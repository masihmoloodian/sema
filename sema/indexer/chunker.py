"""Orchestrates: file discovery → parallel parsing → batched embedding → storing."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from .parser import parse_file
from .embedder import Embedder
from ..store.chroma import SemaStore
from ..store.hashes import FileHashStore
from ..store.schema import Chunk
from ..utils.file_walker import walk_project

EMBED_BATCH_SIZE = 200   # chunks per embedding call — larger = more efficient SBERT inference
PARSE_WORKERS = 8        # parallel file parsing threads (tree-sitter releases the GIL)


def index_project(
    project_root: Path,
    store: SemaStore,
    embedder: Embedder,
    reset: bool = False,
    base_root: Path | None = None,
) -> dict:
    """
    Main indexing entry point.
    Returns stats dict: {files, chunks, languages, skipped}.

    base_root: root used for relative paths stored in the index. Defaults to
    project_root. Set to the workspace root when indexing multiple projects so
    paths include the project folder name (e.g. "backend/src/auth.ts").

    Pipeline:
      1. Load hash store — skip files unchanged since last run
      2. Walk all files, separate into changed vs unchanged
      3. Delete stale chunks for changed files (before re-parsing)
      4. Parse changed files in parallel via ThreadPoolExecutor
      5. Embed chunks in large batches (more efficient than many small batches)
      6. Store all at once, update hashes
    """
    if base_root is None:
        base_root = project_root

    sema_dir = store.index_path.parent
    hash_store = FileHashStore(sema_dir)

    if reset:
        store.reset()
        hash_store.clear()

    all_files = list(walk_project(project_root))
    current_rels = {str(fp.relative_to(base_root)) for fp in all_files}

    # Remove chunks + hash entries for files that no longer exist
    stale_rels = hash_store.known_paths() - current_rels
    for rel in stale_rels:
        store.delete_by_file(rel)
        hash_store.remove(rel)

    # Separate changed (needs re-index) from unchanged (skip)
    to_index: list[Path] = []
    skipped = 0
    for fp in all_files:
        rel = str(fp.relative_to(base_root))
        if hash_store.is_unchanged(rel, fp):
            skipped += 1
        else:
            to_index.append(fp)

    stats: dict = {"files": 0, "chunks": 0, "languages": {}, "skipped": skipped}

    if not to_index:
        hash_store.save()
        return stats

    # Delete old chunks for changed files before re-parsing so stale chunks
    # (from removed/renamed functions) don't accumulate in the index.
    for fp in to_index:
        store.delete_by_file(str(fp.relative_to(base_root)))

    # ── Phase 1: parse changed files in parallel ──────────────────────────────
    all_chunks: list[Chunk] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
    ) as progress:
        parse_task = progress.add_task("Parsing...", total=len(to_index))

        with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as executor:
            futures = {
                executor.submit(parse_file, fp, base_root): fp
                for fp in to_index
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

    # Update hashes for all files we attempted to index (including empty ones)
    for fp in to_index:
        rel = str(fp.relative_to(base_root))
        hash_store.update(rel, fp)
    hash_store.save()

    return stats


def index_file(
    file_path: Path,
    project_root: Path,
    store: SemaStore,
    embedder: Embedder,
    base_root: Path | None = None,
    hash_store: FileHashStore | None = None,
) -> int:
    """Re-index a single file incrementally. Returns number of new chunks stored."""
    if base_root is None:
        base_root = project_root
    rel = str(file_path.relative_to(base_root))
    store.delete_by_file(rel)
    chunks = parse_file(file_path, base_root)
    if chunks:
        _flush(chunks, store, embedder)
    if hash_store is not None:
        hash_store.update(rel, file_path)
        hash_store.save()
    return len(chunks)


def _flush(chunks: list[Chunk], store: SemaStore, embedder: Embedder) -> None:
    texts = [c.embed_text() for c in chunks]
    embeddings = embedder.embed(texts)
    store.upsert(chunks, embeddings)
