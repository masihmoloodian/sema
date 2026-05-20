"""
File watcher for `sema watch`.

Uses watchdog to monitor the project tree. On any file save or delete,
re-indexes only the changed file — not the whole project. Changes are
debounced 300ms so rapid saves (auto-save, formatters) only trigger one
re-index per file.
"""

import threading
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from ..indexer.parser import get_supported_extensions
from ..indexer.chunker import index_file
from ..indexer.embedder import Embedder
from ..store.chroma import SemaStore
from ..store.hashes import FileHashStore


_DEBOUNCE_SECONDS = 0.3


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        watch_root: Path,
        store: SemaStore,
        embedder: Embedder,
        on_indexed,   # callback(path, n_chunks) — used by CLI to print status
        base_root: Path | None = None,
        hash_store: FileHashStore | None = None,
    ):
        self._root = watch_root.resolve()
        self._base = (base_root or watch_root).resolve()
        self._store = store
        self._embedder = embedder
        self._on_indexed = on_indexed
        self._hash_store = hash_store
        self._extensions = get_supported_extensions()
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    # ── watchdog event hooks ──────────────────────────────────────────────────

    def on_modified(self, event):
        if not event.is_directory:
            self._schedule(event.src_path, deleted=False)

    def on_created(self, event):
        if not event.is_directory:
            self._schedule(event.src_path, deleted=False)

    def on_deleted(self, event):
        if not event.is_directory:
            self._schedule(event.src_path, deleted=True)

    def on_moved(self, event):
        if not event.is_directory:
            # old path → remove, new path → index
            self._schedule(event.src_path, deleted=True)
            self._schedule(event.dest_path, deleted=False)

    # ── debounce ──────────────────────────────────────────────────────────────

    def _schedule(self, raw_path: str, deleted: bool) -> None:
        path = str(Path(raw_path).resolve())
        with self._lock:
            existing = self._timers.pop(path, None)
            if existing:
                existing.cancel()
            t = threading.Timer(
                _DEBOUNCE_SECONDS, self._process, args=[path, deleted]
            )
            self._timers[path] = t
            t.start()

    def _process(self, path: str, deleted: bool) -> None:
        with self._lock:
            self._timers.pop(path, None)

        p = Path(path)

        # Skip files outside the project root (e.g. editor temp files in /tmp)
        try:
            p.relative_to(self._root)
        except ValueError:
            return

        # Skip the index directory itself
        try:
            p.relative_to(self._root / ".sema")
            return
        except ValueError:
            pass

        if deleted:
            rel = str(p.relative_to(self._base))
            self._store.delete_by_file(rel)
            if self._hash_store is not None:
                self._hash_store.remove(rel)
                self._hash_store.save()
            self._on_indexed(p, -1)  # -1 signals deletion
            return

        if p.suffix not in self._extensions:
            return

        if not p.exists():
            return

        try:
            n = index_file(p, self._root, self._store, self._embedder, base_root=self._base, hash_store=self._hash_store)
            self._on_indexed(p, n)
        except Exception as e:
            self._on_indexed(p, 0)  # signal skipped so callers stay informed
            _ = e  # suppress unused-var warning; errors are non-fatal in the watcher


def start_watch(
    watch_dirs: "Path | list[Path]",
    store: SemaStore,
    embedder: Embedder,
    on_indexed=None,
    base_root: Path | None = None,
    hash_store: "FileHashStore | None" = None,
) -> None:
    """
    Block until Ctrl+C, re-indexing any file that changes.

    watch_dirs: single path or list of paths to watch (workspace = list of folders).
    base_root: root for relative paths in the index; defaults to first watch dir.
    hash_store: if provided, hashes are updated after each re-index so that a
                subsequent `sema index .` skips files the watcher already handled.
    on_indexed(path, n_chunks) is called after each file is processed.
    n_chunks == -1 means the file was deleted.
    """
    if on_indexed is None:
        on_indexed = lambda path, n: None  # noqa: E731

    if isinstance(watch_dirs, Path):
        watch_dirs = [watch_dirs]

    observer = Observer()
    for watch_dir in watch_dirs:
        handler = _Handler(watch_dir, store, embedder, on_indexed, base_root=base_root, hash_store=hash_store)
        observer.schedule(handler, str(watch_dir), recursive=True)
    observer.start()

    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()
