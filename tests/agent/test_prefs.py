"""Sticky provider/model/mode/effort preferences."""

import json

from sema.agent import prefs
from sema.agent.providers import DEFAULT_PROVIDER


def test_default_provider_is_claude_code():
    """It reuses a local login, so a first run works with nothing configured."""
    assert prefs.DEFAULT_PROVIDER == "claude-code"
    assert DEFAULT_PROVIDER == "claude-code"
    assert prefs.Prefs().provider == "claude-code"


def test_round_trip(tmp_path):
    prefs.save(prefs.Prefs(provider="openai", model="gpt-5.6-luna",
                           mode="plan", effort="xhigh"), tmp_path)
    loaded = prefs.load(tmp_path)
    assert loaded.provider == "openai"
    assert loaded.model == "gpt-5.6-luna"
    assert loaded.mode == "plan"
    assert loaded.effort == "xhigh"


def test_missing_file_yields_defaults(tmp_path):
    assert prefs.load(tmp_path) == prefs.Prefs()


def test_corrupt_file_yields_defaults(tmp_path):
    prefs.prefs_path(tmp_path).write_text("{not json")
    assert prefs.load(tmp_path) == prefs.Prefs()


def test_non_object_json_yields_defaults(tmp_path):
    prefs.prefs_path(tmp_path).write_text("[1, 2]")
    assert prefs.load(tmp_path) == prefs.Prefs()


def test_partial_file_fills_the_gaps(tmp_path):
    prefs.prefs_path(tmp_path).write_text(json.dumps({"provider": "codex"}))
    loaded = prefs.load(tmp_path)
    assert loaded.provider == "codex"
    assert loaded.mode == "agent"      # default preserved


def test_save_creates_the_directory(tmp_path):
    nested = tmp_path / "a" / "b"
    prefs.save(prefs.Prefs(provider="grok"), nested)
    assert prefs.load(nested).provider == "grok"


def test_save_never_raises_on_an_unwritable_path(tmp_path):
    blocked = tmp_path / "afile"
    blocked.write_text("x")
    # Writing under a regular file is an OSError; prefs are a convenience and
    # must not take down a session.
    prefs.save(prefs.Prefs(), blocked / "sub")


def test_permissions_are_not_persisted(tmp_path):
    """Consent to edit files belongs to one session, not to the machine."""
    prefs.save(prefs.Prefs(provider="claude-code"), tmp_path)
    raw = json.loads(prefs.prefs_path(tmp_path).read_text())
    assert set(raw) == {"provider", "model", "mode", "effort"}
