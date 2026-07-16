---
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
