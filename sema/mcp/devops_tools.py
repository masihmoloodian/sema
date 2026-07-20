"""MCP tools for the devops guard — provider-agnostic by construction.

These register on the same FastMCP server every other sema tool does, so
Claude Code, Codex, Cursor, Grok, and opencode all get identical gating with
zero provider-specific code (see docs/devops-guard-plan.md). This module is
imported for its side effect of registering tools on `tools.mcp` — see
server.py.

The gate itself (classify → redact → decide) lives in ``sema/devops/gate.py``
and is intentionally identical to what `sema devops run` does on the CLI —
an AI provider going through MCP gets no different treatment than an
engineer at a terminal.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from ..devops import gate
from .tools import mcp, _resolve
from .registry import ProjectResolutionError


def _devops_root(project: str | None) -> Path:
    """Resolve which project's .sema/devops/ dir to use, defaulting to the only one."""
    try:
        handle = _resolve(project)
    except (ProjectResolutionError, RuntimeError):
        # No indexed project resolvable (e.g. devops-only usage, no registry
        # initialized yet) — fall back to cwd rather than failing the gate.
        return Path.cwd()
    return handle.project_root or Path.cwd()


@mcp.tool()
def devops_plan(command: str, project: str | None = None) -> str:
    """
    Classify an infra command (terraform/aws/kubectl/helm) WITHOUT running it.

    Always call this before devops_run when you're unsure whether an action
    is safe, mutating, or destructive — it shows the tier and reason with no
    side effects. Command output is never returned raw; secrets are redacted
    before you see anything.

    Args:
        command: The full shell command, e.g. "kubectl scale deployment/web --replicas=2 -n staging"
        project: Which indexed project's .sema/devops/ dir to use. Optional if only one project is indexed.
    """
    argv = shlex.split(command)
    result = gate.plan(argv)
    return json.dumps(result, indent=2)


@mcp.tool()
def devops_run(command: str, project: str | None = None) -> str:
    """
    Analyze an infra command, then act: run it now if safe, hold it for a
    human's explicit approval if it mutates state, or refuse it outright if
    it's irreversible/high-blast-radius (destroy prod, delete a cluster-
    critical namespace, IAM root changes, ...).

    This NEVER executes an unanalyzed command. If the result says
    outcome="held", the action has NOT run — tell the engineer the
    approval_id and that `sema devops approve <id>` (or the extension's
    approval queue) is what makes it run. If outcome="prohibited", do not
    suggest working around it — tell the engineer to run it themselves
    outside sema if they're certain.

    Args:
        command: The full shell command, e.g. "kubectl apply -f deploy.yaml -n staging"
        project: Which indexed project's .sema/devops/ dir to use. Optional if only one project is indexed.
    """
    argv = shlex.split(command)
    result = gate.run(argv, _devops_root(project))
    return json.dumps(result, indent=2)


@mcp.tool()
def devops_approve(approval_id: str, project: str | None = None) -> str:
    """
    Approve a held action by id and execute it. Only call this after the
    human engineer has explicitly told you to approve it — never approve a
    held action on your own judgment; that defeats the point of holding it.

    Args:
        approval_id: The id returned by devops_run when outcome="held"
        project: Which indexed project's .sema/devops/ dir to use. Optional if only one project is indexed.
    """
    result = gate.approve(approval_id, _devops_root(project))
    return json.dumps(result, indent=2)


@mcp.tool()
def devops_deny(approval_id: str, reason: str | None = None, project: str | None = None) -> str:
    """
    Deny a held action by id. Nothing executes.

    Args:
        approval_id: The id returned by devops_run when outcome="held"
        reason: Optional reason, recorded in the audit log
        project: Which indexed project's .sema/devops/ dir to use. Optional if only one project is indexed.
    """
    result = gate.deny(approval_id, _devops_root(project), reason=reason)
    return json.dumps(result, indent=2)


@mcp.tool()
def devops_pending(project: str | None = None) -> str:
    """
    List actions currently held for approval, with their redacted command
    and the reason each was flagged.

    Args:
        project: Which indexed project's .sema/devops/ dir to use. Optional if only one project is indexed.
    """
    rows = gate.pending_actions(_devops_root(project))
    return json.dumps(rows, indent=2)


@mcp.tool()
def devops_log(limit: int = 50, project: str | None = None) -> str:
    """
    Show the redacted audit trail of everything the devops guard has decided
    (ran / held / denied / prohibited) — use this to answer "what infra
    changes have been made" or to review recent activity.

    Args:
        limit: Max entries to return, most recent last
        project: Which indexed project's .sema/devops/ dir to use. Optional if only one project is indexed.
    """
    rows = gate.audit_log(_devops_root(project), limit=limit)
    return json.dumps(rows, indent=2)
