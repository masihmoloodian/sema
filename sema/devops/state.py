"""Persistence for the devops guard: pending approvals + append-only audit log.

Both live under ``.sema/devops/`` in the current project (mirrors the
``.sema/index`` convention used by the rest of sema). The audit log
(``audit.jsonl``) never contains raw/unredacted text — callers must pass
already-redacted strings.

``approvals.json`` is the one exception: a held action has to be executable
later once a human approves it, so its *raw* argv is kept until the approval
is resolved (a kubectl/terraform/aws invocation can itself carry a token via
a flag). This is the same tradeoff Terraform-apply-later tools like Atlantis
make with stored plans. The file is written with 0600 permissions and rows
are deleted once resolved+executed — treat ``.sema/devops/approvals.json``
as sensitive and keep it out of version control (``.sema/`` is already
gitignored by `sema init`).
"""

from __future__ import annotations

import json
import os
import stat
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

_DEVOPS_DIR = ".sema/devops"
_APPROVALS_FILE = "approvals.json"
_AUDIT_FILE = "audit.jsonl"


@dataclass
class PendingAction:
    id: str
    tool: str
    redacted_command: str
    reason: str
    raw_argv: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | approved | denied
    created_at: float = field(default_factory=lambda: time.time())
    decided_at: float | None = None


def _devops_dir(root: Path) -> Path:
    d = root / _DEVOPS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _approvals_path(root: Path) -> Path:
    return _devops_dir(root) / _APPROVALS_FILE


def _audit_path(root: Path) -> Path:
    return _devops_dir(root) / _AUDIT_FILE


def _load_approvals(root: Path) -> dict[str, dict]:
    path = _approvals_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_approvals(root: Path, data: dict[str, dict]) -> None:
    path = _approvals_path(root)
    path.write_text(json.dumps(data, indent=2, default=str))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — raw_argv may carry a secret


def queue_approval(root: Path, tool: str, redacted_command: str, reason: str, raw_argv: list[str]) -> PendingAction:
    """Record a held action waiting for a human's explicit consent."""
    action = PendingAction(
        id=uuid.uuid4().hex[:12], tool=tool, redacted_command=redacted_command,
        reason=reason, raw_argv=raw_argv,
    )
    data = _load_approvals(root)
    data[action.id] = asdict(action)
    _save_approvals(root, data)
    return action


def get_pending(root: Path, action_id: str) -> PendingAction | None:
    data = _load_approvals(root)
    row = data.get(action_id)
    if row is None or row.get("status") != "pending":
        return None
    return PendingAction(**row)


def resolve_pending(root: Path, action_id: str, status: str) -> PendingAction | None:
    """Mark a pending action approved/denied. Returns None if it wasn't pending."""
    data = _load_approvals(root)
    row = data.get(action_id)
    if row is None or row.get("status") != "pending":
        return None
    row["status"] = status
    row["decided_at"] = time.time()
    _save_approvals(root, data)
    return PendingAction(**row)


def list_pending(root: Path) -> list[PendingAction]:
    data = _load_approvals(root)
    return [PendingAction(**row) for row in data.values() if row.get("status") == "pending"]


def forget(root: Path, action_id: str) -> None:
    """Drop a resolved action's raw_argv from disk once it's been executed/denied."""
    data = _load_approvals(root)
    if action_id in data:
        del data[action_id]
        _save_approvals(root, data)


def append_audit(
    root: Path,
    *,
    tool: str,
    tier: str,
    reason: str,
    redacted_command: str,
    outcome: str,
    redacted_output: str | None = None,
) -> None:
    """Append one redacted audit record. Never called with raw/unredacted text."""
    record = {
        "ts": time.time(),
        "tool": tool,
        "tier": tier,
        "reason": reason,
        "command": redacted_command,
        "outcome": outcome,  # ran | held | denied | prohibited
        "output": redacted_output,
    }
    with _audit_path(root).open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def read_audit(root: Path, limit: int = 50) -> list[dict]:
    path = _audit_path(root)
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return records[-limit:]
