"""
Exact symbol search across project files.

Used by find_usages() to locate every call site, import, and type
reference for a given symbol — things BM25/semantic search miss because
they only index chunk names and signatures, not full bodies.
"""

import re
from pathlib import Path
from .file_walker import walk_project

_MAX_LINE_LEN = 300   # truncate very long minified lines in output


def grep_symbol(
    symbol_name: str,
    project_root: Path,
    max_results: int = 30,
) -> list[dict]:
    """
    Search every indexed file for exact occurrences of symbol_name.

    Uses word-boundary matching so 'login' doesn't match 'loginUser'.
    Returns list of {file, line, context} dicts ordered by file then line.
    """
    pattern = re.compile(r"\b" + re.escape(symbol_name) + r"\b")
    results: list[dict] = []

    for file_path in walk_project(project_root):
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line_no, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                results.append({
                    "file": str(file_path.relative_to(project_root)),
                    "line": line_no,
                    "context": line.strip()[:_MAX_LINE_LEN],
                })
                if len(results) >= max_results:
                    return results

    return results


def is_definition_line(line: str, symbol_name: str) -> bool:
    """
    Return True if this line declares the symbol rather than using it.
    Covers function/class/const/var/let declarations in TS, Python, and Go.
    """
    stripped = line.strip()
    patterns = [
        # TypeScript / JavaScript
        rf"(export\s+)?(async\s+)?function\s+{re.escape(symbol_name)}\b",
        rf"(export\s+)?(const|let|var)\s+{re.escape(symbol_name)}\s*[=:]",
        rf"(export\s+)?class\s+{re.escape(symbol_name)}\b",
        # Python
        rf"def\s+{re.escape(symbol_name)}\s*\(",
        rf"class\s+{re.escape(symbol_name)}\s*[:(]",
        # Go
        rf"func\s+(\(\w+\s+\*?\w+\)\s+)?{re.escape(symbol_name)}\s*\(",
        rf"type\s+{re.escape(symbol_name)}\s+",
    ]
    return any(re.search(p, stripped) for p in patterns)
