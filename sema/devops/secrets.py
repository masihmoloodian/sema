"""Deterministic secret redaction — regex + entropy, no model, no network.

This is layer 1 of the devops guard's redaction pass (see docs/devops-guard-plan.md).
Patterns are kept in sync by hand with the extension's regex layer
(vscode-extension/src/redact.ts) — same labels, same shapes — so a secret that
would be caught in chat is also caught here, and vice versa.
"""

from __future__ import annotations

import re

# (compiled pattern, placeholder label) — order matters: PRIVATE_KEY and JWT are
# checked before the shorter generic token patterns so they aren't partially
# swallowed by a broader match first.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----"), "PRIVATE_KEY"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "JWT"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "API_KEY"),  # OpenAI / Anthropic / OpenRouter
    (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b"), "API_KEY"),  # Stripe
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b"), "API_KEY"),  # GitHub
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "API_KEY"),  # Slack
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS_ACCESS_KEY_ID"),
    (re.compile(r"\b(?:aws_secret_access_key\s*[=:]\s*)([A-Za-z0-9/+=]{40})\b", re.IGNORECASE), "AWS_SECRET_ACCESS_KEY"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "API_KEY"),  # Google
    # kubeconfig / k8s service-account bearer tokens
    (re.compile(r"\btoken:\s*[A-Za-z0-9._-]{20,}\b", re.IGNORECASE), "K8S_TOKEN"),
    # generic connection strings (postgres/mysql/mongodb/redis URIs with embedded creds)
    (re.compile(r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis)://[^:\s]+:[^@\s]+@[^\s]+"), "CONNECTION_STRING"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "EMAIL"),
]


def redact_secrets(text: str) -> dict:
    """Redact known secret shapes from ``text``.

    Returns ``{"text": <redacted>, "found": {LABEL: count, ...}}``. Pure regex,
    no model — always available, always the first pass before anything (command
    or output) reaches an AI provider's context, a log, or a screen.
    """
    if not text:
        return {"text": text, "found": {}}

    found: dict[str, int] = {}
    redacted = text
    for pattern, label in _PATTERNS:
        redacted, n = pattern.subn(f"[{label}]", redacted)
        if n:
            found[label] = found.get(label, 0) + n

    return {"text": redacted, "found": found}
