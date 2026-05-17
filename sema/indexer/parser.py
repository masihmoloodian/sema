"""Language dispatcher — registry that maps extensions / filenames to parsers.

Built-in parsers are registered at import time. Third-party code can extend
the registry by calling register() before indexing starts:

    from sema.indexer.parser import register
    from my_pkg import rust_parser
    register([".rs"], rust_parser.extract_chunks)
"""

from pathlib import Path
from typing import Callable
from sema.store.schema import Chunk

ExtractFn = Callable[[str, str], list[Chunk]]

_EXT_REGISTRY: dict[str, ExtractFn] = {}
_NAME_REGISTRY: dict[str, ExtractFn] = {}  # exact filenames, e.g. ".env", "makefile"


def register(
    extensions: list[str],
    extract_fn: ExtractFn,
    *,
    filenames: list[str] | None = None,
) -> None:
    """Register a parser for file extensions and/or exact filenames."""
    for ext in extensions:
        _EXT_REGISTRY[ext.lower()] = extract_fn
    for name in filenames or []:
        _NAME_REGISTRY[name.lower()] = extract_fn


def get_supported_extensions() -> frozenset[str]:
    return frozenset(_EXT_REGISTRY)


def get_supported_filenames() -> frozenset[str]:
    return frozenset(_NAME_REGISTRY)


def parse_file(file_path: Path, project_root: Path) -> list[Chunk]:
    suffix = file_path.suffix.lower()
    name = file_path.name.lower()
    extract_fn = _EXT_REGISTRY.get(suffix) or _NAME_REGISTRY.get(name)
    if not extract_fn:
        return []

    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, PermissionError):
        return []

    if not source.strip():
        return []

    relative_path = str(file_path.relative_to(project_root))
    return extract_fn(source, relative_path)


def _register_builtins() -> None:
    from .languages import typescript, python, golang, markdown, generic

    register([".ts", ".tsx", ".js", ".jsx"], typescript.extract_chunks)
    register([".py"], python.extract_chunks)
    register([".go"], golang.extract_chunks)
    register([".md", ".mdx"], markdown.extract_chunks)
    register(
        [".json", ".yaml", ".yml", ".toml", ".ini", ".css", ".scss",
         ".env", ".sh", ".bash", ".txt", ".xml", ".graphql", ".sql"],
        generic.extract_chunks,
        filenames=[
            ".env", ".env.local", ".env.development", ".env.production",
            ".env.test", ".envrc", ".gitignore", ".gitattributes",
            "makefile", "dockerfile", "jenkinsfile", ".dockerignore",
        ],
    )


_register_builtins()
