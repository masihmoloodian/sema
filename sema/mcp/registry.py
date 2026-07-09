"""
Project registry — lets one MCP server serve multiple indexed projects at once.

A project is any directory containing a non-empty ``.sema/index/``. The registry
discovers projects under one or more scan roots, gives each a unique short name,
and resolves a name to a lazily-built store on demand.

Two construction paths:
  - ``from_single(store, embedder, project_root)`` — one explicit project
    (backward-compatible single-project mode).
  - ``from_roots(roots, embedder)`` — auto-discover every indexed project under
    the given roots (multi-project mode).

Stores and BM25 indexes are built lazily per project (on first use) so a server
watching dozens of projects starts instantly and only pays for what is queried.
"""

import os
from pathlib import Path

from ..store.chroma import SemaStore
from ..store.bm25 import BM25Index
from ..indexer.embedder import Embedder

# Directories never worth descending into while hunting for `.sema/index`.
_SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", "target", ".mypy_cache", ".pytest_cache", ".sema", ".idea",
}

_INDEX_REL = Path(".sema") / "index"


class ProjectResolutionError(Exception):
    """Raised when a tool call cannot be pinned to a single project.

    The message is already user-facing (lists the available projects) so tools
    can return ``str(e)`` directly to the assistant.
    """


def build_bm25(store: SemaStore) -> BM25Index | None:
    """Build a BM25 index over a store's chunks. Returns None if the store is empty."""
    ids, metadatas = store.get_all_for_bm25()
    if not ids:
        return None
    # BM25 text: name + signature only — body causes too many false positives because
    # common tokens like "user" appear in every function that touches the domain model.
    texts = [f"{m['name']} {m['signature']}" for m in metadatas]
    return BM25Index(ids, texts, metadatas)


def discover_projects(roots, max_depth: int = 4) -> list[tuple[Path, Path]]:
    """Find indexed projects under ``roots``.

    Returns ``(project_root, index_path)`` pairs, de-duplicated and sorted by
    project_root. Descent stops at the first ``.sema/index`` found on a branch
    (nested indexed projects are not expected) and is bounded by ``max_depth``.
    """
    found: dict[Path, Path] = {}
    for root in roots:
        root = Path(root).resolve()
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, _files in os.walk(root):
            d = Path(dirpath)
            index_path = d / _INDEX_REL
            if index_path.is_dir() and any(index_path.iterdir()):
                found[d.resolve()] = index_path.resolve()
                dirnames[:] = []  # don't descend into a project's own subtree
                continue
            if len(d.parts) - base_depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [n for n in dirnames if n not in _SKIP_DIRS]
    return sorted(found.items(), key=lambda kv: str(kv[0]))


def assign_names(roots) -> dict[Path, str]:
    """Give each project root a unique short name.

    Starts from the directory basename and prepends parent segments only for
    roots that would otherwise collide (``api`` → ``backend/api`` vs ``web/api``).
    """
    roots = list(roots)
    segs: dict[Path, int] = {r: 1 for r in roots}

    def name_of(r: Path) -> str:
        return "/".join(r.parts[-segs[r]:])

    for _ in range(64):  # bounded; each pass lengthens only colliding names
        counts: dict[str, int] = {}
        for r in roots:
            counts[name_of(r)] = counts.get(name_of(r), 0) + 1
        dups = [r for r in roots if counts[name_of(r)] > 1 and segs[r] < len(r.parts)]
        if not dups:
            break
        for r in dups:
            segs[r] += 1
    return {r: name_of(r) for r in roots}


class ProjectHandle:
    """One indexed project. Builds its store and BM25 index lazily on first use."""

    def __init__(self, name: str, project_root: Path | None, index_path: Path, embedder: Embedder):
        self.name = name
        self.project_root = project_root
        self.index_path = index_path
        self._embedder = embedder
        self._store: SemaStore | None = None
        self._bm25: BM25Index | None = None
        self._bm25_built = False

    @property
    def store(self) -> SemaStore:
        if self._store is None:
            self._store = SemaStore(self.index_path)
        return self._store

    @property
    def bm25(self) -> BM25Index | None:
        if not self._bm25_built:
            self._bm25 = build_bm25(self.store)
            self._bm25_built = True
        return self._bm25

    def chunk_count(self) -> int:
        try:
            return self.store.count()
        except Exception:
            return 0


class ProjectRegistry:
    """Holds the set of servable projects and a shared embedder."""

    def __init__(self, embedder: Embedder, roots=None):
        self.embedder = embedder
        self._roots = [Path(r).resolve() for r in (roots or [])]
        self._handles: dict[str, ProjectHandle] = {}

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def from_single(cls, store: SemaStore, embedder: Embedder, project_root: Path | None) -> "ProjectRegistry":
        reg = cls(embedder, roots=[])
        name = project_root.name if project_root else "project"
        handle = ProjectHandle(name, project_root, store.index_path, embedder)
        handle._store = store  # pre-populated — caller already built it
        reg._handles = {name: handle}
        return reg

    @classmethod
    def from_roots(cls, roots, embedder: Embedder) -> "ProjectRegistry":
        reg = cls(embedder, roots=roots)
        reg.rescan()
        return reg

    # ── discovery ────────────────────────────────────────────────────────────
    def rescan(self) -> None:
        """Re-scan the roots, preserving already-built stores for unchanged projects."""
        discovered = discover_projects(self._roots)
        names = assign_names([pr for pr, _ip in discovered])
        existing_by_root = {h.project_root: h for h in self._handles.values()}
        new_handles: dict[str, ProjectHandle] = {}
        for pr, ip in discovered:
            handle = existing_by_root.get(pr)
            if handle is None:
                handle = ProjectHandle(names[pr], pr, ip, self.embedder)
            else:
                handle.name = names[pr]  # name may shift as siblings appear/disappear
            new_handles[names[pr]] = handle
        self._handles = new_handles

    def maybe_rescan(self) -> None:
        """Rescan only in multi-project (root-backed) mode; a no-op for single mode."""
        if self._roots:
            self.rescan()

    # ── access ───────────────────────────────────────────────────────────────
    def names(self) -> list[str]:
        return sorted(self._handles)

    def handles(self) -> list[ProjectHandle]:
        return [self._handles[n] for n in self.names()]

    def resolve(self, name: str | None) -> ProjectHandle:
        if name is not None:
            handle = self._handles.get(name)
            if handle is None:
                raise ProjectResolutionError(self._unknown_msg(name))
            return handle
        if len(self._handles) == 1:
            return next(iter(self._handles.values()))
        if not self._handles:
            raise ProjectResolutionError(
                "No indexed projects are available. Run `sema index .` in a project first."
            )
        raise ProjectResolutionError(self._ambiguous_msg())

    # ── messages ─────────────────────────────────────────────────────────────
    def _list(self) -> str:
        return "\n".join(f"  • {n}" for n in self.names())

    def _ambiguous_msg(self) -> str:
        return (
            "Multiple projects are indexed — pass the `project` argument to choose one.\n"
            f"Available projects:\n{self._list()}\n"
            f'Example: project="{self.names()[0]}". Call list_projects() for details.'
        )

    def _unknown_msg(self, name: str) -> str:
        return (
            f"Project '{name}' not found.\n"
            f"Available projects:\n{self._list()}\n"
            "Call list_projects() to see all indexed projects."
        )
