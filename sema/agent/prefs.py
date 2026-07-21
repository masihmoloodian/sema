"""
Sticky chat preferences — provider, model, mode, effort.

Picking a provider should outlive the session that picked it, so these are
written next to the session store and reloaded on the next start. Explicit
command-line flags always win over what is saved here.

Permission decisions are deliberately *not* stored: consent to let an agent edit
files belongs to one session, not to a machine.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .session import default_base_dir

_FILENAME = "chat-prefs.json"

# Local CLI providers reuse an existing login, so they work with no setup —
# which makes Claude Code the sensible out-of-the-box default.
DEFAULT_PROVIDER = "claude-code"


@dataclass
class Prefs:
    provider: str = DEFAULT_PROVIDER
    model: str = ""
    mode: str = "agent"
    effort: str = "default"


def prefs_path(base_dir: Path | str | None = None) -> Path:
    base = Path(base_dir) if base_dir else default_base_dir()
    return base / _FILENAME


def load(base_dir: Path | str | None = None) -> Prefs:
    """Read saved preferences, falling back to defaults on anything unreadable."""
    try:
        raw = json.loads(prefs_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Prefs()
    if not isinstance(raw, dict):
        return Prefs()
    defaults = Prefs()
    return Prefs(
        provider=str(raw.get("provider") or defaults.provider),
        model=str(raw.get("model") or defaults.model),
        mode=str(raw.get("mode") or defaults.mode),
        effort=str(raw.get("effort") or defaults.effort),
    )


def save(prefs: Prefs, base_dir: Path | str | None = None) -> None:
    """Persist preferences. Failure is silent — this is a convenience, not state."""
    path = prefs_path(base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(prefs), indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
