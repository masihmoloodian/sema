"""
Reuse detection — is this functionality already implemented in the index?

Grounds the "reuse before you build" principle in real semantic search. Given a
description of what you are about to write, it searches the code index and
returns a *calibrated verdict* — reuse an existing implementation, review
related code, or safely build something new.

This is the piece a prompt-only "write less code" rule cannot provide: a plain
instruction can tell an agent to check for duplicates, but only the index can
actually answer whether they exist. The verdict is what closes the loop — it
says "checked: reuse this" or "checked: nothing exists, safe to build" instead
of dumping a ranked list the agent has to interpret.

Thresholds are calibrated on two labeled sets (see tests/test_reuse.py and the
eval described in docs/mcp-tools.md): the example-repo fixture and sema's own
source. On real code, genuinely-absent descriptions top out around 0.44 cosine
similarity while real matches start around 0.42, so 0.40 keeps recall at 1.0
(never miss an existing implementation — the costly error) while cutting false
positives. A false "review these" is cheap; a missed reuse is the whole point.
"""

from dataclasses import dataclass
from enum import Enum

# Reuse means functions/classes/methods — not doc or config sections.
CODE_CHUNK_TYPES = ["function", "class", "method", "interface", "struct", "module"]

# Verdict thresholds on cosine similarity (score = 1 - distance).
REUSE_STRONG = 0.55   # >= this: almost certainly already implemented
REUSE_RELATED = 0.40  # >= this: related code exists, worth a look; below: novel


class ReuseVerdict(str, Enum):
    EXISTS = "exists"    # strong match — reuse/extend rather than write new
    RELATED = "related"  # related code exists — review before building
    NOVEL = "novel"      # nothing close — safe to build


@dataclass
class ReuseResult:
    verdict: ReuseVerdict
    top_score: float
    candidates: list[dict]  # code hits at/above the RELATED bar, best first


def assess_reuse(store, embedder, description: str, top_k: int = 6) -> ReuseResult:
    """Assess whether `description` is already implemented in `store`."""
    embedding = embedder.embed_one(description)
    hits = store.search(embedding, top_k=top_k, chunk_types=CODE_CHUNK_TYPES)
    if not hits:
        return ReuseResult(ReuseVerdict.NOVEL, 0.0, [])

    top = hits[0]["score"]
    if top >= REUSE_STRONG:
        verdict = ReuseVerdict.EXISTS
    elif top >= REUSE_RELATED:
        verdict = ReuseVerdict.RELATED
    else:
        verdict = ReuseVerdict.NOVEL

    # Never surface sub-threshold noise as a "candidate".
    candidates = [h for h in hits if h["score"] >= REUSE_RELATED]
    return ReuseResult(verdict, top, candidates)


def format_reuse(result: ReuseResult, description: str) -> str:
    """Render an agent-facing verdict for the check_reuse MCP tool."""
    pct = int(round(result.top_score * 100))

    if result.verdict is ReuseVerdict.NOVEL:
        closest = f" (closest match {pct}%)" if result.top_score > 0 else ""
        return (
            f'No existing implementation found for "{description}"{closest}.\n'
            "✅ Safe to build — write the minimum that works, and prefer the "
            "standard library or an already-installed dependency over new code."
        )

    if result.verdict is ReuseVerdict.EXISTS:
        head = (
            f"⚠ This likely ALREADY EXISTS — reuse or extend it instead of "
            f"writing new code (top match {pct}%):"
        )
    else:
        head = f"Related code already exists — review before building (top match {pct}%):"

    lines = [head, ""]
    for h in result.candidates:
        lines.append(
            f"  {h['file']}::{h['name']}  [line {h['start_line']}]  ({int(round(h['score'] * 100))}% match)\n"
            f"    {h['type']}: {h['signature']}"
        )
    lines.append("")
    lines.append(
        '→ If one of these fits, call get_code("<name>") to read it, then reuse '
        "or extend it. Only write new code if none apply."
    )
    return "\n".join(lines)
