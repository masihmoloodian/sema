"""The analyze-first gate — ties policy, redaction, state, and execution together.

This is the one function CLI commands, MCP tools, and the PATH shims all
call through. Nothing in this feature executes a command any other way; see
docs/devops-guard-plan.md, "Core invariant: analyze-first, always".

Order is fixed and not configurable per call: classify → redact → decide.
"""

from __future__ import annotations

from pathlib import Path

from . import policy, runner, state
from .secrets import redact_secrets
from . import k8s_secrets


def redact(text: str, decision: "policy.Decision | None" = None) -> str:
    """Redaction pipeline for devops command/output: structured Secret-aware
    pass, then deterministic regex secrets.

    When `decision` shows this was a `kubectl get secret ...`, Secret data is
    stripped first — it's base64-encoded, not plaintext, so the regex layer
    below would miss it entirely (see k8s_secrets.py). Deterministic
    regex/entropy matching (secrets.py) always runs after that.

    Deliberately does NOT run sema's spaCy NER pass here (the one `sema
    redact`/the extension's chat redaction use) — it was built for free-form
    conversational text, and on structured CLI/YAML output it misfires on
    ordinary capitalized field labels (`Namespace:`, `Labels:` → `[NAME]:`,
    found via real testing against `kubectl describe secret`) without adding
    any actual secret-catching power beyond what the regex/structural passes
    above already provide. The devops guard's promise is "secrets don't
    leak," not general PII redaction — for that narrower, higher-precision
    goal the deterministic layers are strictly better here.
    """
    if decision is not None and decision.tool == "kubectl" and k8s_secrets.touches_secret_resource(decision.argv):
        text = k8s_secrets.redact_secret_output(text, decision.argv)

    return redact_secrets(text)["text"]


def plan(argv: list[str]) -> dict:
    """Classify + redact-preview a command. Never executes anything."""
    decision = policy.classify(argv)
    return {
        "tier": decision.tier.value,
        "reason": decision.reason,
        "tool": decision.tool,
        "command": redact(decision.command),
    }


def run(argv: list[str], root: Path) -> dict:
    """Analyze, then act: run now (SAFE), hold for consent (APPROVE), or refuse (PROHIBITED)."""
    decision = policy.classify(argv)
    redacted_cmd = redact(decision.command)

    if decision.tier is policy.Tier.PROHIBITED:
        state.append_audit(
            root, tool=decision.tool, tier=decision.tier.value, reason=decision.reason,
            redacted_command=redacted_cmd, outcome="prohibited",
        )
        return {
            "outcome": "prohibited",
            "tier": decision.tier.value,
            "reason": decision.reason,
            "command": redacted_cmd,
            "message": "Refused — this action is never auto-run through sema, regardless of approval. Run it yourself outside the tool if you're certain.",
        }

    if decision.tier is policy.Tier.APPROVE:
        pending = state.queue_approval(root, decision.tool, redacted_cmd, decision.reason, raw_argv=argv)
        state.append_audit(
            root, tool=decision.tool, tier=decision.tier.value, reason=decision.reason,
            redacted_command=redacted_cmd, outcome="held",
        )
        return {
            "outcome": "held",
            "tier": decision.tier.value,
            "reason": decision.reason,
            "command": redacted_cmd,
            "approval_id": pending.id,
            "message": f"Suspicious/mutating action held for consent (id={pending.id}). Reason: {decision.reason}. Run `sema devops approve {pending.id}` after review, or `sema devops deny {pending.id}`.",
        }

    # SAFE
    result = runner.execute(argv)
    redacted_out = redact(result.stdout, decision)
    redacted_err = redact(result.stderr, decision)
    state.append_audit(
        root, tool=decision.tool, tier=decision.tier.value, reason=decision.reason,
        redacted_command=redacted_cmd, outcome="ran",
        redacted_output=(redacted_out + redacted_err)[:4000],
    )
    return {
        "outcome": "ran",
        "tier": decision.tier.value,
        "reason": decision.reason,
        "command": redacted_cmd,
        "exit_code": result.exit_code,
        "stdout": redacted_out,
        "stderr": redacted_err,
    }


def run_interactive(argv: list[str], root: Path, confirm) -> dict:
    """Same analyze-first gate as run(), but for a live terminal (the PATH shims).

    APPROVE-tier actions prompt right there via ``confirm(message) -> bool``
    instead of being queued async — there's already a human at a keyboard, so
    there's no need to make them come back with `sema devops approve <id>`.
    PROHIBITED is still never prompted; the answer is always no.
    """
    decision = policy.classify(argv)
    redacted_cmd = redact(decision.command)

    if decision.tier is policy.Tier.PROHIBITED:
        state.append_audit(
            root, tool=decision.tool, tier=decision.tier.value, reason=decision.reason,
            redacted_command=redacted_cmd, outcome="prohibited",
        )
        return {"outcome": "prohibited", "reason": decision.reason, "command": redacted_cmd}

    if decision.tier is policy.Tier.APPROVE:
        proceed = confirm(f"⚠ {decision.reason}\n  {redacted_cmd}\nProceed?")
        if not proceed:
            state.append_audit(
                root, tool=decision.tool, tier=decision.tier.value, reason=decision.reason,
                redacted_command=redacted_cmd, outcome="denied",
            )
            return {"outcome": "denied", "reason": decision.reason, "command": redacted_cmd}
        # falls through to execute, same as SAFE, now that a human said yes

    result = runner.execute(argv)
    redacted_out = redact(result.stdout, decision)
    redacted_err = redact(result.stderr, decision)
    state.append_audit(
        root, tool=decision.tool, tier=decision.tier.value, reason=decision.reason,
        redacted_command=redacted_cmd, outcome="ran",
        redacted_output=(redacted_out + redacted_err)[:4000],
    )
    return {
        "outcome": "ran", "command": redacted_cmd,
        "exit_code": result.exit_code, "stdout": redacted_out, "stderr": redacted_err,
    }


def approve(action_id: str, root: Path) -> dict:
    pending = state.get_pending(root, action_id)
    if pending is None:
        return {"outcome": "error", "message": f"No pending action with id={action_id}"}

    state.resolve_pending(root, action_id, "approved")
    decision = policy.classify(pending.raw_argv)  # re-derive for output-redaction context
    result = runner.execute(pending.raw_argv)
    redacted_out = redact(result.stdout, decision)
    redacted_err = redact(result.stderr, decision)
    state.append_audit(
        root, tool=pending.tool, tier="approve", reason=f"human-approved: {pending.reason}",
        redacted_command=pending.redacted_command, outcome="ran",
        redacted_output=(redacted_out + redacted_err)[:4000],
    )
    state.forget(root, action_id)
    return {
        "outcome": "ran",
        "approval_id": action_id,
        "command": pending.redacted_command,
        "exit_code": result.exit_code,
        "stdout": redacted_out,
        "stderr": redacted_err,
    }


def deny(action_id: str, root: Path, reason: str | None = None) -> dict:
    pending = state.get_pending(root, action_id)
    if pending is None:
        return {"outcome": "error", "message": f"No pending action with id={action_id}"}

    state.resolve_pending(root, action_id, "denied")
    state.append_audit(
        root, tool=pending.tool, tier="approve", reason=reason or "denied by engineer",
        redacted_command=pending.redacted_command, outcome="denied",
    )
    state.forget(root, action_id)
    return {"outcome": "denied", "approval_id": action_id, "command": pending.redacted_command}


def pending_actions(root: Path) -> list[dict]:
    return [
        {"id": p.id, "tool": p.tool, "command": p.redacted_command, "reason": p.reason}
        for p in state.list_pending(root)
    ]


def audit_log(root: Path, limit: int = 50) -> list[dict]:
    return state.read_audit(root, limit=limit)
