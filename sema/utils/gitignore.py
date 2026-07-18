"""Parse .gitignore files and provide pattern matching."""

from pathlib import Path
import pathspec


def load_gitignore(project_root: Path) -> pathspec.GitIgnoreSpec | None:
    """Load .gitignore from project root. Returns None if not found."""
    gitignore_path = project_root / ".gitignore"
    if not gitignore_path.exists():
        return None
    lines = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return pathspec.GitIgnoreSpec.from_lines(lines)


def is_ignored(path: Path, project_root: Path, spec: pathspec.GitIgnoreSpec | None) -> bool:
    """Check if a path should be ignored based on .gitignore patterns."""
    if spec is None:
        return False
    relative = str(path.relative_to(project_root))
    return spec.match_file(relative)


def ensure_entry(project_root: Path, entry: str) -> str | None:
    """Ensure `entry` is present in the project's .gitignore.

    Creates .gitignore containing `entry` if the file is absent, or appends `entry`
    at the end if it exists without it. Returns "created", "appended", or None when
    the entry is already ignored. Leading/trailing slashes are ignored when matching,
    so `.sema`, `.sema/`, and `/.sema/` all count as already present.
    """
    gitignore = project_root / ".gitignore"
    normalized = entry.rstrip("/") + "/"  # write directory-style, e.g. ".sema/"
    target = entry.strip().strip("/")

    if not gitignore.exists():
        gitignore.write_text(normalized + "\n")
        return "created"

    text = gitignore.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.strip("/") == target:
            return None  # already ignored

    separator = "" if text == "" or text.endswith("\n") else "\n"
    gitignore.write_text(text + separator + normalized + "\n")
    return "appended"
