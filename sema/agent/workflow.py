"""
System-prompt construction — the Python port of ``semaWorkflow.ts``.

The three prompt fragments are kept byte-identical to the extension so a session
resumed across surfaces gets the same instructions, and so the sema-first
navigation contract is enforced the same way everywhere.
"""

from __future__ import annotations

SEMA_WORKFLOW = (
    "This project uses sema's semantic code index. For codebase tasks, use sema to narrow "
    "context before broad file reads: your first navigation tool call must be search_code using a "
    "natural-language query; do not delegate to a subagent or use Bash, grep, glob, find, or direct "
    "file reads before that first sema search. "
    "if a result is plausibly relevant, inspect it with get_code instead of repeatedly reformulating "
    "the search. Make at most two targeted searches before falling back, and do not call repo_map "
    "merely because a targeted result is imperfect. Before adding a helper, "
    "class, function, or utility, use check_reuse when that tool is available. Before changing a "
    "known symbol, use impact_analysis or find_usages when available. Use repo_map for architecture "
    "and explain_file for a compact file summary when available. If sema returns no useful result "
    "or is unavailable, fall back to normal workspace navigation. Never claim code exists without "
    "supporting tool output or a direct file read."
)

PLAN_NOTE = (
    "You are in plan mode. Investigate the problem and produce a concise, step-by-step "
    "implementation plan — the files to change, the approach, and the order of the steps. Do "
    "NOT edit source files or write the full implementation; output Markdown plan content only, "
    "then stop. sema will save that answer as the session plan artifact."
)

ASK_NOTE = (
    "You are in Ask mode: this is simple chat, not an agent run. Answer directly from the "
    "conversation and supplied context. Do not inspect or modify the workspace, execute commands, "
    "or invoke tools. If the answer requires live repository investigation, tell the user to switch "
    "to Plan or Agent mode."
)

_BASE_PERSONA = (
    "You are sema, a coding assistant running in a terminal against the user's repository. "
    "Be direct and concise. Prefer showing a diff or the exact change over describing it. "
    "Reference files as path:line so the user can jump to them."
)


def build_system(
    context: str = "",
    reads_workspace: bool = False,
    mode: str = "agent",
    active_plan: str = "",
    active_plan_path: str = "",
    use_index: bool = True,
) -> str:
    """Assemble the system prompt for one turn.

    ``reads_workspace`` is True for agentic CLI-backed providers that inspect the
    repo themselves — those get the portable workflow only, no injected RAG
    context, because they would double up on retrieval otherwise.
    """
    parts: list[str] = []
    if not reads_workspace:
        parts.append(_BASE_PERSONA)
    if use_index:
        parts.append(SEMA_WORKFLOW)
    if mode == "plan":
        parts.append(PLAN_NOTE)
    elif mode == "ask":
        parts.append(ASK_NOTE)
    if active_plan.strip():
        label = active_plan_path or "the session plan"
        parts.append(
            f"An implementation plan for this session is saved at {label}. "
            f"Follow it unless the user redirects you.\n\n{active_plan.strip()}"
        )
    if context.strip() and not reads_workspace:
        parts.append(
            "Relevant code retrieved from the sema index for this turn:\n\n" + context.strip()
        )
    return "\n\n".join(p for p in parts if p)
