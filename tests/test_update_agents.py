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
