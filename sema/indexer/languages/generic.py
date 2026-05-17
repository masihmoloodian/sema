"""Generic text chunker — sliding window for config/data/shell files.

Splits any text file into ~CHUNK_LINES-line chunks at blank-line boundaries.
Works well for .json, .yaml, .env, .css, .toml, .sh, .sql, and anything else
without a dedicated AST parser.
"""

from sema.store.schema import Chunk

CHUNK_LINES = 50
_MIN_LINES = 5  # chunks shorter than this are merged with the next


def extract_chunks(source: str, file_path: str) -> list[Chunk]:
    lines = source.splitlines()
    if not lines:
        return []

    groups = _split_into_groups(lines)
    chunks: list[Chunk] = []
    line_cursor = 1

    for idx, group in enumerate(groups):
        start_line = line_cursor
        end_line = line_cursor + len(group) - 1
        body = "\n".join(group)
        name = _first_meaningful_line(group, file_path, idx)
        chunks.append(
            Chunk(
                id=f"{file_path}::section:{idx}",
                file=file_path,
                language=_language_from_path(file_path),
                chunk_type="section",
                name=name,
                signature=name[:120],
                body=body,
                start_line=start_line,
                end_line=end_line,
            )
        )
        line_cursor = end_line + 1

    return chunks


def _split_into_groups(lines: list[str]) -> list[list[str]]:
    """Split lines into logical groups at blank lines, capped at CHUNK_LINES."""
    groups: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        current.append(line)
        if len(current) >= CHUNK_LINES and not line.strip():
            groups.append(current)
            current = []

    if current:
        # Merge tiny trailing group into previous if possible
        if groups and len(current) < _MIN_LINES:
            groups[-1].extend(current)
        else:
            groups.append(current)

    return groups or [[]]


def _first_meaningful_line(group: list[str], file_path: str, idx: int) -> str:
    for line in group:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "//", "/*", "*", "--")):
            return stripped[:100]
    # Fallback: filename + index
    stem = file_path.rsplit("/", 1)[-1]
    return f"{stem}:section:{idx}"


def _language_from_path(file_path: str) -> str:
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path.rsplit("/", 1)[-1] else "text"
    return ext
