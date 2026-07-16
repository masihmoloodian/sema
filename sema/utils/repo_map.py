"""Generate a compressed repository map from chunk metadata."""

from collections import defaultdict

CODE_CHUNK_TYPES = {"function", "class", "method", "interface", "struct", "module"}


def generate_repo_map(all_metadata: list[dict]) -> str:
    """
    Produce a compact map of the codebase: file paths with their exported
    symbols. No source code — just enough to understand architecture.
    """
    if not all_metadata:
        return "Index is empty. Run: sema index ."

    # Group chunks by file
    by_file: dict[str, list[dict]] = defaultdict(list)
    for m in all_metadata:
        # Documentation sections and config blobs are useful when explicitly
        # searched, but including every one in the architecture map produces a
        # large, noisy response. repo_map is intentionally code-shaped.
        if m.get("chunk_type") in CODE_CHUNK_TYPES:
            by_file[m["file"]].append(m)

    if not by_file:
        return "No code symbols are indexed. Run: sema index ."

    lines = ["# Repository Map\n"]

    for file_path in sorted(by_file.keys()):
        chunks = by_file[file_path]
        lines.append(f"\n## {file_path}")

        exports = [c for c in chunks if c.get("exports") == "True"]
        internals = [c for c in chunks if c.get("exports") != "True"]

        if exports:
            lines.append("  exports:")
            for c in exports:
                lines.append(f"    {c['chunk_type']} {c['name']}: {c['signature']}")

        non_method_internals = [
            c for c in internals if c["chunk_type"] not in ("method",)
        ]
        if non_method_internals:
            lines.append("  internal:")
            for c in non_method_internals[:5]:  # cap at 5 to keep map compact
                lines.append(f"    {c['chunk_type']} {c['name']}: {c['signature']}")
            if len(non_method_internals) > 5:
                lines.append(f"    ... and {len(non_method_internals) - 5} more")

    return "\n".join(lines)
