"""Tests for portable provider skill installation."""

from sema.skills import MANAGED_MARKER, SKILL_CONTENT, install_provider_skills, provider_skill_path


def test_installs_claude_and_agent_standard_skills(tmp_path):
    results = install_provider_skills(tmp_path, {"claude", "codex", "opencode"})

    # Codex and opencode deliberately share the open Agent Skills location.
    assert len(results) == 2
    claude = provider_skill_path(tmp_path, "claude")
    agents = provider_skill_path(tmp_path, "codex")
    assert claude.read_text() == SKILL_CONTENT
    assert agents.read_text() == SKILL_CONTENT
    assert provider_skill_path(tmp_path, "opencode") == agents


def test_install_is_idempotent(tmp_path):
    first = install_provider_skills(tmp_path, {"claude"})
    second = install_provider_skills(tmp_path, {"claude"})
    assert first[0].status == "installed"
    assert second[0].status == "existing"


def test_install_preserves_customized_skill(tmp_path):
    path = provider_skill_path(tmp_path, "codex")
    path.parent.mkdir(parents=True)
    path.write_text("custom team workflow\n")

    result = install_provider_skills(tmp_path, {"codex"})[0]
    assert result.status == "preserved"
    assert path.read_text() == "custom team workflow\n"


def test_install_updates_managed_skill(tmp_path):
    path = provider_skill_path(tmp_path, "claude")
    path.parent.mkdir(parents=True)
    path.write_text(f"old version\n{MANAGED_MARKER}\n")

    result = install_provider_skills(tmp_path, {"claude"})[0]
    assert result.status == "updated"
    assert path.read_text() == SKILL_CONTENT


def test_skill_declares_semantic_workflow():
    assert "name: sema-code-navigation" in SKILL_CONTENT
    assert "Do not delegate initial exploration" in SKILL_CONTENT
    for tool in (
        "search_code",
        "get_code",
        "check_reuse",
        "impact_analysis",
        "find_usages",
        "repo_map",
        "explain_file",
        "list_projects",
    ):
        assert tool in SKILL_CONTENT
