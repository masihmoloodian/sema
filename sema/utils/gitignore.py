"""Parse .gitignore files and provide pattern matching."""

from pathlib import Path
import pathspec


def load_gitignore(project_root: Path) -> pathspec.PathSpec | None:
    """Load .gitignore from project root. Returns None if not found."""
    gitignore_path = project_root / ".gitignore"
    if not gitignore_path.exists():
        return None
    lines = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def is_ignored(path: Path, project_root: Path, spec: pathspec.PathSpec | None) -> bool:
    """Check if a path should be ignored based on .gitignore patterns."""
    if spec is None:
        return False
    relative = str(path.relative_to(project_root))
    return spec.match_file(relative)
