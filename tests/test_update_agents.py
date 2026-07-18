"""Tests for `sema update` agent CLI maintenance."""

from click.testing import CliRunner

from sema.cli import main, _AGENT_CLIS


def _capture(monkeypatch):
    """Pretend every CLI is on PATH and record how subprocess.run is invoked."""
    calls = []
    monkeypatch.setattr("sema.cli.shutil.which", lambda name: f"/bin/{name}")

    class Result:
        returncode = 0

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Result()

    monkeypatch.setattr("sema.cli.subprocess.run", run)
    return calls


def test_update_check_reports_every_installed_version(monkeypatch):
    calls = _capture(monkeypatch)
    result = CliRunner().invoke(main, ["update", "--check"])
    assert result.exit_code == 0
    # --check just runs each binary's --version (list form, no shell).
    assert calls == [
        (["/bin/claude", "--version"], {"check": False}),
        (["/bin/codex", "--version"], {"check": False}),
        (["/bin/opencode", "--version"], {"check": False}),
        (["/bin/grok", "--version"], {"check": False}),
    ]


def test_update_runs_official_curl_installer(monkeypatch):
    calls = _capture(monkeypatch)
    result = CliRunner().invoke(main, ["update", "--provider", "grok"])
    assert result.exit_code == 0
    # Runs grok's official install script through a shell — NOT `grok update`.
    assert calls == [(_AGENT_CLIS["grok"]["install"], {"shell": True, "check": False})]
    assert "x.ai/cli/install.sh" in calls[0][0]


def test_update_targets_one_provider(monkeypatch):
    calls = _capture(monkeypatch)
    result = CliRunner().invoke(main, ["update", "--provider", "opencode"])
    assert result.exit_code == 0
    assert calls == [(_AGENT_CLIS["opencode"]["install"], {"shell": True, "check": False})]
    assert "opencode-ai/opencode" in calls[0][0]


def test_every_provider_has_a_curl_installer():
    # Guard against a provider losing its installer or reverting to a self-updater.
    for name, spec in _AGENT_CLIS.items():
        assert spec["install"].startswith("curl "), name
        assert "install" in spec["install"], name


def test_codex_installer_is_non_interactive():
    # Codex's script prompts "Start Codex now?" (reads /dev/tty); skip it so a run from
    # the extension doesn't launch codex and fail with a non-zero exit.
    assert "CODEX_NON_INTERACTIVE=1" in _AGENT_CLIS["codex"]["install"]


def test_self_update_prefers_uv_tool(monkeypatch):
    monkeypatch.setattr("sema.cli.shutil.which", lambda name: "/bin/uv" if name == "uv" else None)
    calls = []

    class Result:
        def __init__(self, stdout="", returncode=0):
            self.stdout, self.returncode = stdout, returncode

    def run(cmd, **kwargs):
        if cmd[:3] == ["/bin/uv", "tool", "list"]:
            return Result(stdout="sema-mcp v0.6.0 (installed)")  # sema-mcp is a uv tool
        calls.append(cmd)
        return Result()

    monkeypatch.setattr("sema.cli.subprocess.run", run)
    result = CliRunner().invoke(main, ["self-update"])
    assert result.exit_code == 0, result.output
    assert calls == [["/bin/uv", "tool", "upgrade", "sema-mcp"]]


def test_self_update_falls_back_to_pip(monkeypatch):
    monkeypatch.setattr("sema.cli.shutil.which", lambda name: None)  # no uv, no pipx
    calls = []

    class Result:
        stdout = ""
        returncode = 0

    def run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr("sema.cli.subprocess.run", run)
    result = CliRunner().invoke(main, ["self-update"])
    assert result.exit_code == 0, result.output
    assert calls[-1][1:] == ["-m", "pip", "install", "--upgrade", "sema-mcp"]
