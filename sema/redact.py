"""Lightweight PII redaction — spaCy NER for person names and locations.

This is the *model* half of sema's hybrid redaction: the VS Code extension
redacts structured PII and secrets with regex (instant, offline), then pipes the
result through `sema redact` to also catch free-form entities (people, places)
that regex cannot.

spaCy and its model are an optional extra so the base install stays light:

    pip install "sema-mcp[pii]"
    python -m spacy download en_core_web_sm

When they are absent, `redact_text` raises :class:`RedactionUnavailable` and
callers fall back to regex-only redaction.
"""

from __future__ import annotations

# spaCy entity label → placeholder. Kept to high-precision personal identifiers;
# ORG/PRODUCT are intentionally skipped — in code they fire on library and tool
# names ("OpenAI", "GitHub") far more often than on real PII.
_LABELS = {
    "PERSON": "[NAME]",
    "GPE": "[LOCATION]",
    "LOC": "[LOCATION]",
}

_MODEL = "en_core_web_sm"
_nlp = None  # lazily loaded, reused for the life of the process


class RedactionUnavailable(RuntimeError):
    """spaCy or its model isn't installed — the NER pass can't run."""


def _load():
    global _nlp
    if _nlp is not None:
        return _nlp
    try:
        import spacy
    except ImportError as e:  # pragma: no cover - exercised via the CLI fallback
        raise RedactionUnavailable(
            "spaCy is not installed. Install the PII extra: pip install 'sema-mcp[pii]'"
        ) from e
    try:
        # Only NER is needed — disable the rest of the pipeline for speed.
        _nlp = spacy.load(_MODEL, disable=["tagger", "parser", "attribute_ruler", "lemmatizer"])
    except OSError as e:
        raise RedactionUnavailable(
            f"spaCy model '{_MODEL}' is not installed. Run: python -m spacy download {_MODEL}"
        ) from e
    return _nlp


def redact_text(text: str) -> dict:
    """Redact person/location entities from ``text`` via spaCy NER.

    Returns ``{"text": <redacted>, "entities": [{"type": <placeholder>, "count": n}]}``.
    Raises :class:`RedactionUnavailable` when spaCy or its model are missing.
    """
    if not text.strip():
        return {"text": text, "entities": []}

    nlp = _load()
    doc = nlp(text)

    # doc.ents are non-overlapping; apply right-to-left so char offsets stay valid.
    spans = [
        (ent.start_char, ent.end_char, _LABELS[ent.label_])
        for ent in doc.ents
        if ent.label_ in _LABELS
    ]
    spans.sort(key=lambda s: s[0], reverse=True)

    counts: dict[str, int] = {}
    redacted = text
    for start, end, placeholder in spans:
        redacted = redacted[:start] + placeholder + redacted[end:]
        counts[placeholder] = counts.get(placeholder, 0) + 1

    return {"text": redacted, "entities": [{"type": k, "count": v} for k, v in counts.items()]}
