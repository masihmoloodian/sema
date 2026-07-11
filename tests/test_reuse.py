"""
Tests for the reuse guard (check_reuse).

Includes a labeled precision/recall evaluation that proves the calibrated
verdict genuinely improves on the naive baselines an agent would otherwise use:
"always build" (no reuse detection) and "any search hit = reuse" (raw top-1 with
no threshold, i.e. trusting search_code's first result unconditionally).
"""

from sema.reuse import (
    assess_reuse,
    ReuseVerdict,
    REUSE_RELATED,
)

# Functionality that DOES exist in tests/fixtures/example-repo.
POSITIVES = [
    "generate a jwt token for a user",
    "validate a jwt token and return the user",
    "refresh an access token",
    "check whether a token has expired",
    "middleware that requires an authenticated user",
    "optional authentication middleware",
    "create a new user session",
    "invalidate or destroy a session",
    "look up a session by its id",
    "http handler for user login",
    "http endpoint to log out",
    "register a route handler on the router",
]

# Functionality that does NOT exist in the fixture (an auth-only repo).
NEGATIVES = [
    "parse a CSV file into rows",
    "connect to a postgres database and run a query",
    "resize an image to a thumbnail",
    "send an email notification to a user",
    "cache values in redis with a TTL",
    "rate limit incoming http requests",
    "upload a file to an S3 bucket",
    "render markdown text to html",
    "compute the nth fibonacci number",
    "encrypt a file using AES",
    "schedule a recurring background cron job",
    "compress a directory into a zip archive",
]


def _flagged(result) -> bool:
    """A reuse result 'flags' when it found existing/related code (not novel)."""
    return result.verdict is not ReuseVerdict.NOVEL


# ── basic verdict behavior ────────────────────────────────────────────────────

def test_existing_functionality_is_flagged(indexed_store):
    store, embedder = indexed_store
    result = assess_reuse(store, embedder, "generate a jwt token for a user")
    assert result.verdict in (ReuseVerdict.EXISTS, ReuseVerdict.RELATED)
    assert result.candidates, "should surface at least one existing candidate"


def test_absent_functionality_is_novel(indexed_store):
    store, embedder = indexed_store
    result = assess_reuse(store, embedder, "connect to a postgres database and run a query")
    assert result.verdict is ReuseVerdict.NOVEL
    assert result.candidates == []  # no sub-threshold noise surfaced


def test_candidates_never_below_related_threshold(indexed_store):
    store, embedder = indexed_store
    result = assess_reuse(store, embedder, "middleware that requires an authenticated user")
    assert all(c["score"] >= REUSE_RELATED for c in result.candidates)


# ── the improvement metric ────────────────────────────────────────────────────

def _confusion(flags_pos, flags_neg):
    tp = sum(flags_pos)
    fn = len(flags_pos) - tp
    fp = sum(flags_neg)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1


def test_check_reuse_beats_naive_baselines(indexed_store):
    """
    check_reuse's calibrated verdict should cleanly separate 'exists' from
    'novel', and beat both baselines an ungrounded agent would use.
    """
    store, embedder = indexed_store

    pos_flags = [_flagged(assess_reuse(store, embedder, d)) for d in POSITIVES]
    neg_flags = [_flagged(assess_reuse(store, embedder, d)) for d in NEGATIVES]

    prec, rec, f1 = _confusion(pos_flags, neg_flags)

    # Baseline A — "always build": never detects reuse (recall 0, F1 0).
    _, _, f1_always_build = _confusion([False] * len(POSITIVES), [False] * len(NEGATIVES))

    # Baseline B — "any search hit counts": search_code always returns a top
    # result, so a naive agent trusting it flags everything, including negatives.
    _, _, f1_raw_top1 = _confusion([True] * len(POSITIVES), [True] * len(NEGATIVES))

    # Calibrated verdict is near-perfect on this labeled set...
    assert prec >= 0.9, f"precision too low: {prec:.2f}"
    assert rec >= 0.9, f"recall too low: {rec:.2f}"
    assert f1 >= 0.9, f"F1 too low: {f1:.2f}"

    # ...and materially better than either naive strategy.
    assert f1 > f1_raw_top1 + 0.15, f"check_reuse F1 {f1:.2f} not clearly > raw-top-1 {f1_raw_top1:.2f}"
    assert f1 > f1_always_build, f"check_reuse F1 {f1:.2f} not > always-build {f1_always_build:.2f}"


# ── MCP tool wrapper ──────────────────────────────────────────────────────────

def test_tool_novel_says_safe_to_build(indexed_store):
    from sema.mcp.tools import init_tools, check_reuse
    store, embedder = indexed_store
    init_tools(store, embedder)
    out = check_reuse("upload a file to an S3 bucket")
    assert "Safe to build" in out


def test_tool_existing_says_reuse(indexed_store):
    from sema.mcp.tools import init_tools, check_reuse
    store, embedder = indexed_store
    init_tools(store, embedder)
    out = check_reuse("generate a jwt token for a user")
    assert "ALREADY EXISTS" in out or "Related code already exists" in out
    assert "get_code" in out  # tells the agent how to act on it


def test_tool_requires_project_when_ambiguous(multi_root, embedder):
    from sema.mcp.tools import set_registry, check_reuse
    from sema.mcp.registry import ProjectRegistry
    set_registry(ProjectRegistry.from_roots([multi_root], embedder))
    out = check_reuse("generate a jwt token")
    assert "Multiple projects" in out


def test_tool_with_project_selects_project(multi_root, embedder):
    from sema.mcp.tools import set_registry, check_reuse
    from sema.mcp.registry import ProjectRegistry
    set_registry(ProjectRegistry.from_roots([multi_root], embedder))
    out = check_reuse("generate a jwt token for a user", project="proj-a")
    assert "generateToken" in out
