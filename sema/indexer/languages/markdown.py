"""Markdown chunk extraction — one chunk per heading section."""

import re
from sema.store.schema import Chunk

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)


def extract_chunks(source: str, file_path: str) -> list[Chunk]:
    lines = source.splitlines()
    total_lines = len(lines)
    chunks: list[Chunk] = []

    matches = list(_HEADING_RE.finditer(source))

    if not matches:
        # No headings — treat whole file as one section
        name = _stem(file_path)
        chunks.append(_make_chunk(file_path, name, source, 1, total_lines, idx=0))
        return chunks

    for i, m in enumerate(matches):
        start_line = source[: m.start()].count("\n") + 1
        end_line = (
            source[: matches[i + 1].start()].count("\n")
            if i + 1 < len(matches)
            else total_lines
        )
        heading_text = m.group(2).strip()
        body = source[m.start(): (matches[i + 1].start() if i + 1 < len(matches) else len(source))]
        chunks.append(_make_chunk(file_path, heading_text, body, start_line, end_line, idx=i))

    return chunks


def _make_chunk(
    file_path: str, name: str, body: str, start_line: int, end_line: int, idx: int
) -> Chunk:
    signature = name[:120]
    return Chunk(
        id=f"{file_path}::section:{idx}",
        file=file_path,
        language="markdown",
        chunk_type="section",
        name=name,
        signature=signature,
        body=body,
        start_line=start_line,
        end_line=end_line,
    )


def _stem(file_path: str) -> str:
    return file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
