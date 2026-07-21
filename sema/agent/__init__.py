"""The sema coding agent: providers, tools, permissions, and the turn loop."""

from __future__ import annotations

__all__ = [
    "Agent", "AgentConfig", "PermissionManager", "Session", "SessionStore",
    "build_system", "build_tools",
]


def __getattr__(name: str):
    # Lazy so `import sema.agent` stays cheap for the CLI's --help path.
    if name in ("Agent", "AgentConfig"):
        from .loop import Agent, AgentConfig

        return {"Agent": Agent, "AgentConfig": AgentConfig}[name]
    if name == "PermissionManager":
        from .permissions import PermissionManager

        return PermissionManager
    if name in ("Session", "SessionStore"):
        from .session import Session, SessionStore

        return {"Session": Session, "SessionStore": SessionStore}[name]
    if name == "build_system":
        from .workflow import build_system

        return build_system
    if name == "build_tools":
        from .tools import build_tools

        return build_tools
    raise AttributeError(name)
