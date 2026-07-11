# OpenAI Codex setup

## Step-by-step setup

```bash
# 1. Go to your project and index it — downloads ~80MB model on first run
cd /your/project
sema index .

# 2. Register sema with Codex
sema init --codex

# 3. Reload VS Code (if using the VS Code extension)
#    Cmd+Shift+P → "Developer: Reload Window"
#    Then open a Codex chat and type /mcp — you should see: sema  Enabled

# If using Codex CLI instead:
#    Restart Codex from your project directory: codex
#    Then type /mcp to confirm sema shows as Enabled
```

## Add `AGENTS.md` to your project

Codex reads `AGENTS.md` the way Claude Code reads `CLAUDE.md`. Without it, Codex may not call sema tools automatically. Create one at your project root:

```markdown
## Codebase navigation

This project is indexed by sema. Use sema MCP tools to locate code — do not use grep or read files directly.

| Goal | Tool |
|---|---|
| Find a function, class, or method | `search_code("natural language description")` |
| Check if something already exists before writing it | `check_reuse("what you're about to build")` |
| Read full source of a known symbol | `get_code("symbolName")` |
| Find all callers of a symbol | `find_usages("symbolName")` |
| Understand call chains and blast radius | `impact_analysis("symbolName")` |
| Architecture overview | `repo_map()` |

Always call `search_code()` before using grep or reading files directly. Before writing a new function or utility, call `check_reuse()` and reuse an existing match instead of writing a parallel implementation.
```

## Multiple projects

To serve several projects from one registration, see [Working with multiple projects](multi-project.md).

## Uninstall

```bash
sema init --codex --uninstall
```

This removes the `[mcp_servers.sema]` block from `.codex/config.toml`.
