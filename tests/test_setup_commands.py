"""Tests for the multi-client installer glue: opencode config helpers and
`sema setup`. These exercise config file round-trips and client detection
without needing the real Claude/Codex/opencode CLIs installed.
"""

import json

from click.testing import CliRunner

from sema.cli import main, _opencode_config_add, _opencode_config_remove


def test_opencode_add_merges_without_clobbering(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(json.dumps({"theme": "dark"}) + "\n")

    changed, path = _opencode_config_add(tmp_path, roots=None)
    assert changed is True
    assert path == cfg

    data = json.loads(cfg.read_text())
    assert data["theme"] == "dark"  # user's setting preserved
    assert data["mcp"]["sema"]["type"] == "local"
    assert data["mcp"]["sema"]["enabled"] is True
    assert "serve" in data["mcp"]["sema"]["command"]


def test_opencode_add_is_idempotent(tmp_path):
    assert _opencode_config_add(tmp_path, roots=None)[0] is True
    assert _opencode_config_add(tmp_path, roots=None)[0] is False  # already present


def test_opencode_remove_leaves_other_keys(tmp_path):
    _opencode_config_add(tmp_path, roots=None)
    cfg = tmp_path / "opencode.json"
    data = json.loads(cfg.read_text())
    data["theme"] = "light"
    cfg.write_text(json.dumps(data))

    assert _opencode_config_remove(cfg) is True
    remaining = json.loads(cfg.read_text())
    assert remaining["theme"] == "light"
    assert "mcp" not in remaining  # emptied mcp block dropped


def test_opencode_remove_missing_is_false(tmp_path):
    assert _opencode_config_remove(tmp_path / "opencode.json") is False


def test_opencode_add_skips_malformed_config(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text("{not valid json")
    changed, _ = _opencode_config_add(tmp_path, roots=None)
    assert changed is False  # never clobber a file we can't parse


def test_setup_refuses_without_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["setup"])
    assert "No index found" in result.output


def test_setup_honours_skip_flags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)
    result = CliRunner().invoke(
        main, ["setup", "--skip-claude", "--skip-codex", "--skip-opencode"]
    )
    assert result.exit_code == 0
    assert "skipped" in result.output
    # Nothing registered because everything was skipped.
    assert not (tmp_path / "opencode.json").exists()


def test_setup_registers_opencode_when_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)

    # Pretend only opencode is installed.
    import sema.cli as cli

    def fake_which(name):
        return "/usr/local/bin/opencode" if name == "opencode" else None

    monkeypatch.setattr(cli.shutil, "which", fake_which)
    monkeypatch.setattr(cli, "_find_claude_bin", lambda: None)

    result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 0
    cfg = tmp_path / "opencode.json"
    assert cfg.exists()
    assert json.loads(cfg.read_text())["mcp"]["sema"]["enabled"] is True


def test_setup_env_var_skip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)
    monkeypatch.setenv("SEMA_SKIP_OPENCODE", "1")

    import sema.cli as cli
    monkeypatch.setattr(cli.shutil, "which",
                        lambda n: "/usr/local/bin/opencode" if n == "opencode" else None)
    monkeypatch.setattr(cli, "_find_claude_bin", lambda: None)

    result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 0
    assert not (tmp_path / "opencode.json").exists()  # skipped via env var
