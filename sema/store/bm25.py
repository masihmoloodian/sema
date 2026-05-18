"""
BM25 keyword index over indexed chunks.

Complements the semantic (vector) index — exact symbol names and code keywords
that are poor semantic signal rank high here. Combined with semantic search via
Reciprocal Rank Fusion (RRF) in tools.py.
"""

import re
from rank_bm25 import BM25Okapi

# Common English navigation words that appear in natural-language queries but match
# too many unrelated function names (find→findById, add→addMember, get→getUser…).
# Stripping them from BM25 queries prevents noise while keeping symbol-name matches.
_STOP_WORDS = {
    "a", "an", "the", "and", "or", "to", "in", "on", "of", "for",
    "with", "by", "from", "at", "is", "it", "its", "be", "as", "are",
    "this", "that", "into", "via", "using", "use", "how", "do", "does",
    # common imperative words that are also ubiquitous function name prefixes
    "get", "set", "add", "find", "list", "make", "show", "run",
    "create", "delete", "remove", "update", "fetch", "load", "save",
    "handle", "build", "init", "check", "send", "read", "write",
    # architecture words that match many files but carry no selector value
    "endpoint", "controller", "service", "handler", "method", "function",
    "class", "module", "component", "route", "api", "request", "response",
    "input", "output", "param", "params", "field", "type", "value",
}


def _tokenize(text: str, remove_stopwords: bool = False) -> list[str]:
    """Tokenize for BM25 — splits camelCase so 'forgotPassword' matches 'forgot password'."""
    # Split camelCase: forgotPassword → forgot Password
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Split PascalCase runs: HTMLParser → HTML Parser
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    if remove_stopwords:
        tokens = [t for t in tokens if t not in _STOP_WORDS]
    return tokens


class BM25Index:
    """In-memory BM25 index built from ChromaDB metadata at server startup."""

    def __init__(self, ids: list[str], texts: list[str], metadatas: list[dict]):
        self._ids = ids
        self._meta = {id_: meta for id_, meta in zip(ids, metadatas)}
        # Corpus is indexed without stop-word removal so symbol names stay intact
        tokenized = [_tokenize(t) for t in texts]
        self._bm25 = BM25Okapi(tokenized)

    def search(
        self,
        query: str,
        top_k: int = 10,
        chunk_types: list[str] | None = None,
    ) -> list[dict]:
        """Return top_k results ranked by BM25 score, optionally filtered by chunk_type."""
        # Remove stop words from the query only — keeps natural-language queries from
        # matching every function named get*/find*/add* etc.
        tokens = _tokenize(query, remove_stopwords=True)
        raw_scores = self._bm25.get_scores(tokens)

        ranked = sorted(
            [(i, float(s)) for i, s in enumerate(raw_scores) if s > 0],
            key=lambda x: x[1],
            reverse=True,
        )

        results: list[dict] = []
        for idx, score in ranked:
            id_ = self._ids[idx]
            meta = self._meta[id_]
            if chunk_types and meta.get("chunk_type") not in chunk_types:
                continue
            results.append({
                "id": id_,
                "file": meta["file"],
                "name": meta["name"],
                "type": meta["chunk_type"],
                "signature": meta["signature"],
                "start_line": meta["start_line"],
                "language": meta["language"],
                "score": score,
            })
            if len(results) >= top_k:
                break

        return results
