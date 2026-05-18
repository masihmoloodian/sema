"""Tests for BM25Index — tokenization and keyword search."""

import pytest
from sema.store.bm25 import BM25Index, _tokenize


# ── tokenizer ────────────────────────────────────────────────────────────────

def test_tokenize_lowercase():
    assert _tokenize("Hello World") == ["hello", "world"]


def test_tokenize_camel_case():
    tokens = _tokenize("forgotPassword")
    assert "forgot" in tokens
    assert "password" in tokens


def test_tokenize_pascal_case():
    tokens = _tokenize("ForgotPassword")
    assert "forgot" in tokens
    assert "password" in tokens


def test_tokenize_splits_non_alpha():
    tokens = _tokenize("reset_password_token")
    assert "reset" in tokens
    assert "password" in tokens
    assert "token" in tokens


def test_tokenize_html_acronym():
    tokens = _tokenize("HTMLParser")
    assert "html" in tokens
    assert "parser" in tokens


# ── BM25Index ────────────────────────────────────────────────────────────────

@pytest.fixture
def small_index():
    ids = [
        "auth.ts::forgotPassword:10",
        "auth.ts::resetPassword:30",
        "jwt.ts::validateToken:5",
        "user.ts::findByEmail:20",
    ]
    texts = [
        "forgotPassword(email: string): Promise<void>  const token = randomBytes(32)",
        "resetPassword(token: string, password: string): Promise<void>  user.passwordHash = hash",
        "validateToken(token: string): Promise<User>  jwt.verify(token, secret)",
        "findByEmail(email: string): Promise<User>  return repo.findOne({ email })",
    ]
    metadatas = [
        {"file": "auth.ts", "name": "forgotPassword", "chunk_type": "function",
         "signature": "forgotPassword(email: string)", "start_line": 10, "language": "typescript"},
        {"file": "auth.ts", "name": "resetPassword", "chunk_type": "function",
         "signature": "resetPassword(token: string, password: string)", "start_line": 30, "language": "typescript"},
        {"file": "jwt.ts", "name": "validateToken", "chunk_type": "function",
         "signature": "validateToken(token: string): Promise<User>", "start_line": 5, "language": "typescript"},
        {"file": "user.ts", "name": "findByEmail", "chunk_type": "function",
         "signature": "findByEmail(email: string): Promise<User>", "start_line": 20, "language": "typescript"},
    ]
    return BM25Index(ids, texts, metadatas)


def test_exact_name_match(small_index):
    results = small_index.search("validateToken")
    assert len(results) > 0
    assert results[0]["name"] == "validateToken"


def test_camel_case_query_matches(small_index):
    # "forgot password" (two words) should match "forgotPassword"
    results = small_index.search("forgot password")
    names = [r["name"] for r in results]
    assert "forgotPassword" in names


def test_no_match_returns_empty(small_index):
    results = small_index.search("nonexistent xyz abc")
    assert results == []


def test_chunk_type_filter(small_index):
    results = small_index.search("token", chunk_types=["method"])
    assert results == []  # all chunks are "function" type


def test_chunk_type_filter_passes_matching(small_index):
    results = small_index.search("token", chunk_types=["function"])
    assert len(results) > 0


def test_top_k_respected(small_index):
    results = small_index.search("token password email", top_k=2)
    assert len(results) <= 2


def test_scores_are_positive(small_index):
    results = small_index.search("token")
    assert all(r["score"] > 0 for r in results)


def test_result_has_required_fields(small_index):
    results = small_index.search("token")
    r = results[0]
    for field in ("id", "file", "name", "type", "signature", "start_line", "language", "score"):
        assert field in r
