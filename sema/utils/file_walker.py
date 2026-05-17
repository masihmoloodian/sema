"""Walk project directory, respecting .gitignore and common excludes."""

from pathlib import Path
from typing import Iterator
from .gitignore import load_gitignore, is_ignored

ALWAYS_EXCLUDE_DIRS = {
    ".git", ".sema", "node_modules", "__pycache__", ".venv", "venv",
    "env", "dist", "build", "coverage", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "vendor", "third_party",
}

ALWAYS_EXCLUDE_SUFFIX_PATTERNS = {
    ".min.js", ".min.ts", ".d.ts",
}

# Lock files and auto-generated files that carry no semantic value
ALWAYS_EXCLUDE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "pnpm-lock.yml",
    "poetry.lock", "cargo.lock", "composer.lock", "gemfile.lock",
    "pipfile.lock", "bun.lockb",
}


def walk_project(project_root: Path) -> Iterator[Path]:
    """Yield all source files that should be indexed.

    Supported extensions are driven by the parser registry, so any newly
    registered parser is automatically picked up here.
    """
    from sema.indexer.parser import get_supported_extensions, get_supported_filenames

    supported_exts = get_supported_extensions()
    supported_names = get_supported_filenames()
    spec = load_gitignore(project_root)

    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue

        # Skip excluded directories
        parts = set(path.relative_to(project_root).parts[:-1])
        if parts & ALWAYS_EXCLUDE_DIRS:
            continue

        # Skip lock / generated files
        if path.name.lower() in ALWAYS_EXCLUDE_FILES:
            continue

        # Check extension or exact filename against the parser registry
        suffix = path.suffix.lower()
        name = path.name.lower()
        if suffix not in supported_exts and name not in supported_names:
            continue

        # Skip minified / declaration files
        if any(path.name.endswith(p) for p in ALWAYS_EXCLUDE_SUFFIX_PATTERNS):
            continue

        # Skip .gitignore-matched files
        if is_ignored(path, project_root, spec):
            continue

        yield path
