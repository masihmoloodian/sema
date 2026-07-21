"""System prompt, attachments, and plan artifacts."""


import pytest

from sema.agent import attachments as att
from sema.agent.plan_artifact import read_plan, save_plan
from sema.agent.session import Attachment, ChatMessage
from sema.agent.workflow import ASK_NOTE, PLAN_NOTE, SEMA_WORKFLOW, build_system


# ── system prompt ───────────────────────────────────────────────────────────


def test_agent_mode_includes_the_workflow_only():
    prompt = build_system(mode="agent")
    assert SEMA_WORKFLOW in prompt
    assert PLAN_NOTE not in prompt
    assert ASK_NOTE not in prompt


def test_plan_mode_adds_the_plan_note():
    assert PLAN_NOTE in build_system(mode="plan")


def test_ask_mode_adds_the_ask_note():
    assert ASK_NOTE in build_system(mode="ask")


def test_use_index_false_drops_the_workflow():
    assert SEMA_WORKFLOW not in build_system(mode="agent", use_index=False)


def test_cli_providers_get_no_injected_context():
    """Agentic CLIs retrieve for themselves; injecting would double up."""
    prompt = build_system(context="SOME RAG CONTEXT", reads_workspace=True)
    assert "SOME RAG CONTEXT" not in prompt
    assert SEMA_WORKFLOW in prompt


def test_api_providers_do_get_injected_context():
    prompt = build_system(context="SOME RAG CONTEXT", reads_workspace=False)
    assert "SOME RAG CONTEXT" in prompt


def test_active_plan_is_injected_with_its_path():
    prompt = build_system(active_plan="1. do it", active_plan_path=".sema/plans/x.md")
    assert "1. do it" in prompt
    assert ".sema/plans/x.md" in prompt


def test_prompt_is_stable_across_calls():
    """Byte stability is what makes the cache_control breakpoint hit."""
    assert build_system(mode="agent") == build_system(mode="agent")


# ── plan artifacts ──────────────────────────────────────────────────────────


def test_save_plan_writes_under_dot_sema(tmp_path):
    artifact = save_plan(tmp_path, "abc-123", "Fix auth", "1. step")
    assert artifact.relative_path == ".sema/plans/abc-123.md"
    body = (tmp_path / artifact.relative_path).read_text()
    assert body.startswith("# Fix auth")
    assert "1. step" in body


def test_save_plan_sanitizes_the_session_id(tmp_path):
    # Separators collapse to '-' and leading '-.' are stripped, matching the
    # extension's safePart() regex — so traversal cannot escape .sema/plans.
    artifact = save_plan(tmp_path, "../../etc/passwd", "t", "c")
    assert artifact.relative_path == ".sema/plans/etc-passwd.md"
    assert (tmp_path / artifact.relative_path).exists()
    assert not (tmp_path.parent / "etc").exists()


def test_save_plan_uses_a_default_heading_for_untitled_sessions(tmp_path):
    artifact = save_plan(tmp_path, "s", "New chat", "c")
    assert artifact.markdown.startswith("# Implementation plan")


def test_read_plan_round_trips(tmp_path):
    artifact = save_plan(tmp_path, "s", "T", "content here")
    assert "content here" in read_plan(tmp_path, artifact.relative_path)


def test_read_plan_refuses_paths_outside_the_repo(tmp_path):
    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret")
    assert read_plan(tmp_path, "../outside.md") == ""


def test_read_plan_returns_empty_for_a_missing_file(tmp_path):
    assert read_plan(tmp_path, ".sema/plans/nope.md") == ""


# ── attachments ─────────────────────────────────────────────────────────────


def test_sniff_detects_png_by_magic_number():
    assert att.sniff("whatever.dat", b"\x89PNG\r\n\x1a\n rest") == ("image", "image/png")


def test_sniff_detects_pdf_by_magic_number():
    assert att.sniff("x.bin", b"%PDF-1.7") == ("pdf", "application/pdf")


def test_magic_number_beats_a_lying_extension():
    kind, _ = att.sniff("totally.txt", b"\x89PNG\r\n\x1a\n")
    assert kind == "image"


def test_sniff_uses_extension_for_source_files():
    kind, _ = att.sniff("main.py", b"print(1)")
    assert kind == "text"


def test_sniff_rejects_unknown_binary():
    assert att.sniff("blob.xyz", b"\x00\x01\x02\xff\xfe") is None


@pytest.mark.parametrize("kind,size,over", [
    ("image", 4 * 1024 * 1024, False),
    ("image", 6 * 1024 * 1024, True),
    ("text", 300 * 1024, True),
    ("pdf", 1024, False),
])
def test_check_limit(kind, size, over):
    assert (att.check_limit(kind, size) is not None) is over


def test_stage_copies_and_returns_metadata(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# hi")
    staged = att.stage(tmp_path / "staging", source)
    assert staged.name == "notes.md"
    assert staged.kind == "text"
    assert (tmp_path / "staging" / staged.id).read_bytes() == b"# hi"


def test_stage_rejects_an_oversized_file(tmp_path):
    source = tmp_path / "big.txt"
    source.write_text("x" * (att.LIMITS["text"] + 1))
    with pytest.raises(ValueError, match="capped at"):
        att.stage(tmp_path / "staging", source)


def test_stage_rejects_a_missing_file(tmp_path):
    with pytest.raises(ValueError, match="Not a file"):
        att.stage(tmp_path / "staging", tmp_path / "ghost.txt")


def test_materialize_inlines_text_and_keeps_binaries(tmp_path):
    directory = tmp_path / "staging"
    directory.mkdir()
    (directory / "t1").write_text("file body")
    (directory / "i1").write_bytes(b"\x89PNG\r\n\x1a\n")
    messages = [ChatMessage("user", "look at these", [
        Attachment("t1", "notes.md", "text", "text/plain", 9),
        Attachment("i1", "shot.png", "image", "image/png", 8),
    ])]
    out, binaries = att.materialize(directory, messages)
    assert "file body" in out[0].content
    assert "notes.md" in out[0].content
    assert [a.name for a in out[0].attachments] == ["shot.png"]
    assert [a.name for a in binaries] == ["shot.png"]


def test_materialize_survives_a_missing_file(tmp_path):
    directory = tmp_path / "staging"
    directory.mkdir()
    messages = [ChatMessage("user", "x", [
        Attachment("gone", "gone.md", "text", "text/plain", 1)
    ])]
    out, _ = att.materialize(directory, messages)
    assert "(file missing)" in out[0].content


def test_total_bytes_sums_the_transcript():
    messages = [
        ChatMessage("user", "a", [Attachment("1", "a", "text", "t", 100)]),
        ChatMessage("user", "b", [Attachment("2", "b", "text", "t", 50)]),
    ]
    assert att.total_bytes(messages) == 150


def test_format_size():
    assert att.format_size(512) == "512 B"
    assert att.format_size(2048) == "2.0 KB"
    assert att.format_size(5 * 1024 * 1024) == "5.0 MB"
