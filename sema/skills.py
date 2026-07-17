"""Portable sema navigation skill installed for supported coding agents."""

from dataclasses import dataclass
from pathlib import Path


SKILL_NAME = "sema-code-navigation"
MANAGED_MARKER = "<!-- sema-managed-skill: remove this line before customizing -->"
SKILL_CONTENT = """---
name: sema-code-navigation
description: Use sema's semantic index to locate, understand, reuse, and safely change code. Trigger for codebase exploration, implementation, debugging, refactoring, or impact analysis in an indexed project.
allowed-tools: mcp__sema__*
---

<!-- sema-managed-skill: remove this line before customizing -->

# Navigate code with sema

Use sema before broad file reads or recursive grep so the model gets focused context quickly. For a codebase task, after this skill is activated, the next tool call MUST be `mcp__sema__search_code` (shown as `search_code` in some clients). Do not call Agent, Task, Explore, Bash, Grep, Glob, Read, LS, Find, or another navigation tool first. Merely loading this skill or discovering the tool does not satisfy this step.

Do not delegate initial exploration to an Agent, Task, Explore, or other subagent before the current agent calls `search_code()` itself. Delegated agents may not inherit this skill or sema's MCP tools. After the first sema search has narrowed the area, delegate only when the remaining work genuinely benefits from it, and pass the sema result into the delegated task.

1. Start codebase exploration with `search_code()` using a natural-language description. If a result is plausibly relevant, inspect it instead of repeatedly reformulating the search; make at most two targeted searches before falling back.
2. Call `get_code()` for the strongest plausible exact symbol returned by search; it is the source-reading tool. Do not call `repo_map()` merely because a targeted result is imperfect.
3. Before adding a function, helper, class, or utility, call `check_reuse()` and reuse or extend a relevant implementation.
4. Before changing a known symbol, call `impact_analysis()`; use `find_usages()` when exact references matter.
5. Use `repo_map()` for architecture and `explain_file()` for a compact file summary. In multi-project mode, call `list_projects()` first and pass `project` explicitly.
6. Treat sema output as a focused starting point. If results are empty or stale, fall back to normal navigation and recommend `sema index .`.

Never claim that code exists unless a sema result or a direct file read supports it. `search_code()` returns signatures only; full source must come from `get_code()` or a direct read after sema has narrowed the location.
"""


@dataclass(frozen=True)
class SkillInstall:
    path: Path
    status: str  # "installed" | "updated" | "existing" | "preserved"


def provider_skill_path(project_root: Path, provider: str) -> Path:
    """Return the project-local skill path used by a provider.

    opencode and Grok Build discover the open Agent Skills path as well as their
    native ones, so they share `.agents/skills` with Codex and avoid duplicate
    skill entries.
    """
    if provider == "claude":
        base = project_root / ".claude" / "skills"
    elif provider in {"codex", "opencode", "grok"}:
        base = project_root / ".agents" / "skills"
    else:
        raise ValueError(f"Unsupported skill provider: {provider}")
    return base / SKILL_NAME / "SKILL.md"


def install_provider_skills(project_root: Path, providers: set[str]) -> list[SkillInstall]:
    """Install sema's skill without overwriting a user's existing skill."""
    installs: list[SkillInstall] = []
    seen: set[Path] = set()
    for provider in sorted(providers):
        path = provider_skill_path(project_root, provider)
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            existing = path.read_text()
            if existing == SKILL_CONTENT:
                status = "existing"
            elif MANAGED_MARKER in existing:
                path.write_text(SKILL_CONTENT)
                status = "updated"
            else:
                status = "preserved"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(SKILL_CONTENT)
            status = "installed"
        installs.append(SkillInstall(path=path, status=status))
    return installs
