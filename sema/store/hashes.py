"""
File hash store for incremental indexing.

Persists SHA-256 hashes of indexed files to .sema/hashes.json so that
subsequent `sema index .` runs skip files whose content hasn't changed.
"""

import hashlib
import json
from pathlib import Path


class FileHashStore:
    FILENAME = "hashes.json"

    def __init__(self, sema_dir: Path):
        self._path = sema_dir / self.FILENAME
        self._hashes: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._hashes, indent=2, sort_keys=True))

    def is_unchanged(self, rel_path: str, file_path: Path) -> bool:
        """True if file content matches the stored hash."""
        stored = self._hashes.get(rel_path)
        return stored is not None and stored == _sha256(file_path)

    def update(self, rel_path: str, file_path: Path) -> None:
        self._hashes[rel_path] = _sha256(file_path)

    def remove(self, rel_path: str) -> None:
        self._hashes.pop(rel_path, None)

    def clear(self) -> None:
        self._hashes.clear()

    def known_paths(self) -> set[str]:
        return set(self._hashes.keys())


def _sha256(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()
