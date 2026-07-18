"""Tests for the multi-client installer glue: opencode/grok/cursor config helpers
and `sema setup`. These exercise config file round-trips and client detection
without needing the real Claude/Codex/opencode/grok CLIs or Cursor installed.
"""

import json
import re

from click.testing import CliRunner

from sema.cli import (
    main,
    _codex_config_add,
    _cursor_config_add,
    _cursor_config_remove,
    _grok_config_add,
    _opencode_config_add,
    _opencode_config_remove,
    _toml_mcp_config_remove,
)


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


def test_grok_add_writes_project_scoped_block(tmp_path):
    changed, path = _grok_config_add(tmp_path, roots=None)
    assert changed is True
    assert path == tmp_path / ".grok" / "config.toml"

    content = path.read_text()
    assert "[mcp_servers.sema]" in content
    assert "enabled = true" in content
    assert f'"--project", "{tmp_path}"' in content


def test_grok_timeouts_are_toml_integers(tmp_path):
    """grok deserializes the timeouts into Option<u64>; a TOML float fails to parse
    and takes the whole [mcp_servers.sema] block down with it."""
    _grok_config_add(tmp_path, roots=None)
    content = (tmp_path / ".grok" / "config.toml").read_text()

    for field in ("startup_timeout_sec", "tool_timeout_sec"):
        value = re.search(rf"^{field} = (.+)$", content, re.MULTILINE).group(1)
        assert re.fullmatch(r"\d+", value), f"{field} must be a TOML integer, got {value!r}"


def test_codex_keeps_float_timeouts(tmp_path):
    """Codex's block is unchanged by sharing a writer with grok."""
    _codex_config_add(tmp_path, roots=None)
    content = (tmp_path / ".codex" / "config.toml").read_text()
    assert "startup_timeout_sec = 15.0" in content
    assert "tool_timeout_sec = 60.0" in content


def test_grok_add_preserves_existing_config(tmp_path):
    cfg = tmp_path / ".grok" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\n')

    assert _grok_config_add(tmp_path, roots=None)[0] is True
    content = cfg.read_text()
    assert "[mcp_servers.linear]" in content  # user's other server untouched
    assert "[mcp_servers.sema]" in content


def test_grok_add_is_idempotent(tmp_path):
    assert _grok_config_add(tmp_path, roots=None)[0] is True
    assert _grok_config_add(tmp_path, roots=None)[0] is False  # already present


def test_grok_remove_leaves_other_servers(tmp_path):
    cfg = tmp_path / ".grok" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\n')
    _grok_config_add(tmp_path, roots=None)

    assert _toml_mcp_config_remove(cfg) is True
    remaining = cfg.read_text()
    assert "[mcp_servers.sema]" not in remaining
    assert "[mcp_servers.linear]" in remaining


def test_grok_multi_project_serves_roots(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    _grok_config_add(tmp_path, roots=[root])
    content = (tmp_path / ".grok" / "config.toml").read_text()
    assert f'"--root", "{root}"' in content
    assert "--project" not in content


def test_setup_registers_grok_when_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)

    import sema.cli as cli

    monkeypatch.setattr(cli.shutil, "which",
                        lambda n: "/usr/local/bin/grok" if n == "grok" else None)
    monkeypatch.setattr(cli, "_find_claude_bin", lambda: None)

    result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 0
    assert "[mcp_servers.sema]" in (tmp_path / ".grok" / "config.toml").read_text()
    # grok reads the open Agent Skills path, shared with codex/opencode.
    skill = tmp_path / ".agents" / "skills" / "sema-code-navigation" / "SKILL.md"
    assert skill.exists()


def test_setup_grok_env_var_skip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)
    monkeypatch.setenv("SEMA_SKIP_GROK", "1")

    import sema.cli as cli
    monkeypatch.setattr(cli.shutil, "which",
                        lambda n: "/usr/local/bin/grok" if n == "grok" else None)
    monkeypatch.setattr(cli, "_find_claude_bin", lambda: None)

    result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 0
    assert not (tmp_path / ".grok").exists()  # skipped via env var


def test_init_grok_registers_and_uninstalls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)
    cfg = tmp_path / ".grok" / "config.toml"

    assert CliRunner().invoke(main, ["init", "--grok"]).exit_code == 0
    assert "[mcp_servers.sema]" in cfg.read_text()

    assert CliRunner().invoke(main, ["init", "--grok", "--uninstall"]).exit_code == 0
    assert "[mcp_servers.sema]" not in cfg.read_text()


def test_init_grok_refuses_without_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["init", "--grok"])
    assert "No index found" in result.output
    assert not (tmp_path / ".grok").exists()


# ── Cursor (.cursor/mcp.json, the .mcp.json standard shape) ───────────────────

def test_cursor_add_writes_mcp_servers_shape(tmp_path):
    changed, path = _cursor_config_add(tmp_path, roots=None)
    assert changed is True
    assert path == tmp_path / ".cursor" / "mcp.json"

    entry = json.loads(path.read_text())["mcpServers"]["sema"]
    # Cursor's stdio entry is {command: str, args: [...]} — command is NOT the
    # combined [bin, ...args] array opencode uses, and no "type" key is needed.
    assert isinstance(entry["command"], str)
    assert entry["args"][:2] == ["serve", "--project"]
    assert entry["args"][2] == str(tmp_path)
    assert "type" not in entry


def test_cursor_add_merges_without_clobbering(tmp_path):
    cfg = tmp_path / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"linear": {"url": "https://mcp.linear.app/mcp"}}}))

    assert _cursor_config_add(tmp_path, roots=None)[0] is True
    servers = json.loads(cfg.read_text())["mcpServers"]
    assert servers["linear"] == {"url": "https://mcp.linear.app/mcp"}  # user's server untouched
    assert "sema" in servers


def test_cursor_add_is_idempotent(tmp_path):
    assert _cursor_config_add(tmp_path, roots=None)[0] is True
    assert _cursor_config_add(tmp_path, roots=None)[0] is False  # already present


def test_cursor_add_skips_malformed_config(tmp_path):
    cfg = tmp_path / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{not valid json")
    assert _cursor_config_add(tmp_path, roots=None)[0] is False  # never clobber unparseable config


def test_cursor_remove_leaves_other_servers(tmp_path):
    cfg = tmp_path / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"linear": {"url": "x"}}}))
    _cursor_config_add(tmp_path, roots=None)

    assert _cursor_config_remove(cfg) is True
    servers = json.loads(cfg.read_text())["mcpServers"]
    assert "sema" not in servers and "linear" in servers


def test_cursor_remove_drops_empty_mcp_servers(tmp_path):
    _cursor_config_add(tmp_path, roots=None)
    cfg = tmp_path / ".cursor" / "mcp.json"
    assert _cursor_config_remove(cfg) is True
    assert "mcpServers" not in json.loads(cfg.read_text())  # emptied block dropped


def test_cursor_remove_missing_is_false(tmp_path):
    assert _cursor_config_remove(tmp_path / ".cursor" / "mcp.json") is False


def test_cursor_multi_project_serves_roots(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    _cursor_config_add(tmp_path, roots=[root])
    args = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())["mcpServers"]["sema"]["args"]
    assert "--root" in args and str(root) in args
    assert "--project" not in args


def test_setup_registers_cursor_when_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)

    import sema.cli as cli
    # No CLIs on PATH; Cursor present via its detector.
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)
    monkeypatch.setattr(cli, "_find_claude_bin", lambda: None)
    monkeypatch.setattr(cli, "_cursor_installed", lambda: True)

    result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 0
    assert "sema" in json.loads((tmp_path / ".cursor" / "mcp.json").read_text())["mcpServers"]
    # Cursor reads the shared .agents/skills path, so no separate skill copy.
    assert (tmp_path / ".agents" / "skills" / "sema-code-navigation" / "SKILL.md").exists()


def test_setup_cursor_skipped_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)

    import sema.cli as cli
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)
    monkeypatch.setattr(cli, "_find_claude_bin", lambda: None)
    monkeypatch.setattr(cli, "_cursor_installed", lambda: False)

    result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 0
    assert not (tmp_path / ".cursor").exists()  # not installed → not registered


def test_setup_cursor_env_var_skip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)
    monkeypatch.setenv("SEMA_SKIP_CURSOR", "1")

    import sema.cli as cli
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)
    monkeypatch.setattr(cli, "_find_claude_bin", lambda: None)
    monkeypatch.setattr(cli, "_cursor_installed", lambda: True)

    result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 0
    assert not (tmp_path / ".cursor").exists()  # skipped via env var


def test_init_cursor_registers_and_uninstalls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)
    cfg = tmp_path / ".cursor" / "mcp.json"

    assert CliRunner().invoke(main, ["init", "--cursor"]).exit_code == 0
    assert "sema" in json.loads(cfg.read_text())["mcpServers"]

    assert CliRunner().invoke(main, ["init", "--cursor", "--uninstall"]).exit_code == 0
    assert "mcpServers" not in json.loads(cfg.read_text())


def test_init_cursor_refuses_without_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["init", "--cursor"])
    assert "No index found" in result.output
    assert not (tmp_path / ".cursor").exists()


def test_setup_refuses_without_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["setup"])
    assert "No index found" in result.output


def test_setup_honours_skip_flags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sema" / "index").mkdir(parents=True)
    result = CliRunner().invoke(
        main,
        ["setup", "--skip-claude", "--skip-codex", "--skip-opencode", "--skip-grok", "--skip-cursor"],
    )
    assert result.exit_code == 0
    assert "skipped" in result.output
    # Nothing registered because everything was skipped.
    assert not (tmp_path / "opencode.json").exists()
    assert not (tmp_path / ".grok").exists()
    assert not (tmp_path / ".cursor").exists()


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
    skill = tmp_path / ".agents" / "skills" / "sema-code-navigation" / "SKILL.md"
    assert skill.exists()
    assert "search_code" in skill.read_text()


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
