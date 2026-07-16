"""Release-installer invariants exercised by the clean-install E2E test."""

from pathlib import Path


INSTALLER = (Path(__file__).parent.parent / "install.sh").read_text()


def test_installer_prefers_the_fresh_uv_tool_over_an_active_project_venv():
    assert "uv tool dir --bin" in INSTALLER
    assert 'PATH="$tool_bin:$PATH"' in INSTALLER
