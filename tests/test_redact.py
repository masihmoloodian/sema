"""Tests for the `sema redact` NER command (the model half of PII redaction).

Runs whether or not the optional `[pii]` extra is installed: without spaCy/model
it asserts graceful degradation; with them it asserts real redaction.
"""

import json

import pytest
from click.testing import CliRunner

from sema.cli import main


def _has_model() -> bool:
    try:
        import spacy

        spacy.load("en_core_web_sm")
        return True
    except Exception:
        return False


HAS_MODEL = _has_model()


def test_redact_reads_stdin_json():
    text = "Contact Alice Johnson in Berlin."
    res = CliRunner().invoke(main, ["redact", "--json"], input=text)
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    if HAS_MODEL:
        assert data["redacted"] is True
        assert "Alice Johnson" not in data["text"]
        assert "[NAME]" in data["text"]
    else:
        # No model → degrade gracefully: input echoed unchanged, redacted=False.
        assert data["redacted"] is False
        assert data["error"] == "unavailable"
        assert data["text"] == text


def test_redact_empty_input():
    res = CliRunner().invoke(main, ["redact", "--json"], input="")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["text"] == ""


@pytest.mark.skipif(not HAS_MODEL, reason="spaCy model en_core_web_sm not installed")
def test_redact_text_function():
    from sema.redact import redact_text

    out = redact_text("Contact Bob Smith about the London office.")
    assert "Bob" not in out["text"]
    assert "[NAME]" in out["text"]
    assert any(e["type"] == "[NAME]" for e in out["entities"])
