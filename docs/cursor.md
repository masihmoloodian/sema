# Cursor setup

New to sema? See [Why sema](why-sema.md) for what it does and [Installation](installation.md) to install the `sema` command first.

Cursor is an editor, not a CLI. sema registers with it by writing an MCP config file —
there's no `cursor` binary to drive and no sema chat provider for Cursor (it's its own
agent). The [sema VS Code extension](../vscode-extension/README.md) is for VS Code; in
Cursor you use sema's tools through Cursor's own agent.

## Step-by-step setup

```bash
# 1. Go to your project and index it — downloads ~80MB model on first run
cd /your/project
sema index .

# 2. Register sema with every AI client you have (Claude Code, Codex, opencode, Grok Build, Cursor)
sema setup
#    Or register Cursor only:  sema init --cursor

# 3. Reload Cursor, then enable sema under Settings → MCP
#    (Cursor asks you to approve a newly added server once.)
#    Ask the agent something like "search the codebase for the auth handler"
#    and confirm it calls the sema tools.
```

`sema init --cursor` writes an `mcpServers.sema` entry to `.cursor/mcp.json` in your
project — the `.mcp.json` standard shape Cursor uses. It's project-scoped, so it lives in
the repo and can be committed to share sema with your team.

## Portable committed config

The written config bakes in the absolute project path (`--project /abs/path`), which is
correct on the machine that ran `sema init` but not on a teammate's. Cursor supports
`${workspaceFolder}` interpolation, so if you commit `.cursor/mcp.json` for a team, you
can hand-edit the path to make it portable:

```json
{
  "mcpServers": {
    "sema": {
      "command": "sema",
      "args": ["serve", "--project", "${workspaceFolder}"]
    }
  }
}
```

This assumes `sema` is on Cursor's PATH and the workspace root is the indexed project.
sema's own `status`/`doctor` checks expect the absolute form, so they'll read a
`${workspaceFolder}` config as "registered" but can't verify the path — that's fine for a
committed team config.

## Add `AGENTS.md` to your project

Cursor reads `AGENTS.md` (and `.cursor/rules/*.mdc`). Without guidance, Cursor may not
call sema tools automatically. The simplest option is an `AGENTS.md` at your project root:

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

`sema init --cursor` also installs the same guidance as a skill under
`.agents/skills/sema-code-navigation/` — Cursor reads that shared Agent Skills path, so
one copy serves Cursor, Codex, opencode, and Grok Build.

## Multiple projects

To serve several projects from one registration, see [Working with multiple projects](multi-project.md).

## Uninstall

```bash
sema init --cursor --uninstall
```

This removes the `sema` entry from `.cursor/mcp.json`, leaving any other MCP servers in
the file untouched.
