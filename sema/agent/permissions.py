"""
Tool permission policy and the approval gate.

Every tool declares a default policy. The gate is consulted *inside* the tool
runner before a call executes; a denial comes back to the model as an ordinary
tool result ("User declined ...") so it can adapt rather than crash.

The UI supplies an ``asker`` coroutine. Headless callers can pass
``auto_allow``/``auto_deny`` instead, which is what the tests use.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable


class Policy(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class ApprovalRequest:
    """One pending tool call awaiting a decision."""

    tool: str
    summary: str
    detail: str = ""
    # Set for shell commands so "always allow this prefix" can be offered.
    prefix: str | None = None


# Decision values an asker may return.
ALLOW = "allow"
ALLOW_ALWAYS = "allow_always"
DENY = "deny"

Asker = Callable[[ApprovalRequest], Awaitable[str]]


@dataclass
class PermissionManager:
    """Resolves whether a tool call may proceed."""

    policies: dict[str, Policy] = field(default_factory=dict)
    default_policy: Policy = Policy.ASK
    asker: Asker | None = None
    # Session-scoped grants so `npm test` is not re-prompted on every call.
    always_allowed_tools: set[str] = field(default_factory=set)
    always_allowed_prefixes: set[str] = field(default_factory=set)
    # bypass mirrors the extension's 'bypass' permission mode.
    bypass: bool = False

    def policy_for(self, tool: str) -> Policy:
        return self.policies.get(tool, self.default_policy)

    def _pre_approved(self, request: ApprovalRequest) -> bool:
        if request.tool in self.always_allowed_tools:
            return True
        if request.prefix and request.prefix in self.always_allowed_prefixes:
            return True
        return False

    async def check(self, request: ApprovalRequest) -> bool:
        """True when the call may run."""
        policy = self.policy_for(request.tool)
        if policy is Policy.DENY:
            return False
        if policy is Policy.ALLOW or self.bypass:
            return True
        if self._pre_approved(request):
            return True
        if self.asker is None:
            # No interactive surface: fail closed. A headless caller that wants
            # unattended runs sets bypass=True explicitly.
            return False
        decision = await self.asker(request)
        if decision == ALLOW_ALWAYS:
            if request.prefix:
                self.always_allowed_prefixes.add(request.prefix)
            else:
                self.always_allowed_tools.add(request.tool)
            return True
        return decision == ALLOW


def default_policies() -> dict[str, Policy]:
    """Read-only tools run unattended; anything that mutates asks first."""
    read_only = [
        "search_code", "check_reuse", "get_code", "repo_map", "find_usages",
        "explain_file", "impact_analysis", "list_projects",
        "read_file", "glob", "grep",
    ]
    mutating = ["write_file", "edit_file", "bash"]
    policies = {name: Policy.ALLOW for name in read_only}
    policies.update({name: Policy.ASK for name in mutating})
    return policies


def auto_allow() -> PermissionManager:
    """A manager that approves everything — for tests and `--yes` runs."""
    return PermissionManager(policies=default_policies(), bypass=True)


def auto_deny() -> PermissionManager:
    manager = PermissionManager(policies=default_policies())
    manager.asker = _deny_asker
    return manager


async def _deny_asker(_request: ApprovalRequest) -> str:
    await asyncio.sleep(0)
    return DENY
