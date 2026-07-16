"""Tests for `sema update` agent CLI maintenance."""

from click.testing import CliRunner

from sema.cli import main


def test_update_check_reports_every_installed_provider(monkeypatch):
    calls = []
    monkeypatch.setattr("sema.cli.shutil.which", lambda name: f"/bin/{name}")

    class Result:
        returncode = 0

    def run(args, check):
        calls.append((args, check))
        return Result()

    monkeypatch.setattr("sema.cli.subprocess.run", run)
    result = CliRunner().invoke(main, ["update", "--check"])
    assert result.exit_code == 0
    assert calls == [
        (["/bin/claude", "--version"], False),
        (["/bin/codex", "--version"], False),
        (["/bin/opencode", "--version"], False),
    ]


def test_update_can_target_one_provider(monkeypatch):
    calls = []
    monkeypatch.setattr("sema.cli.shutil.which", lambda name: f"/bin/{name}")

    class Result:
        returncode = 0

    def run(args, check):
        calls.append(args)
        return Result()

    monkeypatch.setattr("sema.cli.subprocess.run", run)
    result = CliRunner().invoke(main, ["update", "--provider", "opencode"])
    assert result.exit_code == 0
    assert calls == [["/bin/opencode", "upgrade"]]
