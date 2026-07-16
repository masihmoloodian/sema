# Troubleshooting

## sema not listed in `/mcp`

**Step 1 — Check that sema was registered successfully**

For Claude Code:
```bash
claude mcp list
```

You should see `sema` in the output. If not, re-run:

```bash
cd your-project
sema init --claude
```

`sema init --claude` calls `claude mcp add sema -s user` internally, registering the absolute path that `which sema` resolves to. If the `claude` binary is not found, it will print the exact command to run manually:

```bash
claude mcp add sema -s user -- "$(which sema)" serve --project /path/to/project
```

For Codex, check `.codex/config.toml` in your project:
```bash
cat .codex/config.toml
```

If it's missing, re-run `sema init --codex` from your project directory.

**Step 2 — Reload VS Code**

After registering, the AI extension needs a reload:

`Cmd+Shift+P` → `Developer: Reload Window`

Then type `/mcp` in chat. You should see `sema` listed as connected/enabled.

**Step 3 — Verify the MCP server starts**

```bash
sema serve --project /path/to/your-project
```

This should block silently (no output). If it crashes with an error, that's the root cause — fix it, then re-run `sema init`.

> **Note:** Seeing `Invalid JSON: EOF while parsing` when running `serve` manually is normal. The MCP server communicates over stdin/stdout — it expects JSON-RPC messages from Claude Code, not interactive terminal input.

---

## sema registered but still not showing in `/mcp`

This usually means Claude Code is reading config from a different location than where `sema init` wrote. We discovered this the hard way.

**The issue:** Different Claude Code interfaces read MCP config differently:

| Interface | Where it reads MCP config |
|---|---|
| Claude Code CLI (`claude`) | `~/.claude.json` → `projects[path].mcpServers` |
| VS Code extension | `claude mcp add -s user` (user-scoped) |
| VS Code workspace | Same — user-scoped, NOT from `.code-workspace` `"mcp"` section |

`sema init --claude` now uses `claude mcp add -s user` which writes to the correct location for all interfaces. If you registered sema with an older version of sema (which wrote to `.claude/settings.json`), remove the old config and re-register:

```bash
# Remove old project-level config if present
cat your-project/.claude/settings.json   # check if sema is in here

# Re-register correctly
cd your-project
sema init --claude
```

---

## VS Code workspace: sema not showing after `sema init`

**The cause:** When VS Code opens a `.code-workspace` file, the Claude Code extension does NOT read the `"mcp"` section inside the workspace file. It also does NOT read `.claude/settings.json` from the workspace parent directory.

**The fix:** `sema init --claude` registers sema at the user level (`-s user`) which works for all workspaces. Make sure you ran it:

```bash
claude mcp list   # should show sema
```

If sema is listed there but still not showing in `/mcp` inside VS Code, reload the window:

`Cmd+Shift+P` → `Developer: Reload Window`

---

## AI is connected to sema but never calls the tools

Two causes:

**1. Tools not yet approved** (Claude Code)

The first time Claude tries to call a sema tool, VS Code asks for permission. If you dismissed that prompt, the tools are blocked.

Fix: In Claude Code chat, explicitly ask Claude to use sema:

```
use search_code to find authentication logic
```

When prompted "Allow sema to run search_code?", click **Allow Always**.

**2. No instruction file in the project**

Without a `CLAUDE.md` (Claude Code) or `AGENTS.md` (Codex), the AI defaults to its own navigation strategy (Bash, Read). These files tell it to call `search_code()` first.

For Claude Code, check:
```bash
cat your-project/CLAUDE.md
```

For Codex or opencode (both read `AGENTS.md`), check:
```bash
cat your-project/AGENTS.md
```

If missing or not mentioning sema, add the template. See [Claude Code setup](claude-code.md), [OpenAI Codex setup](codex.md), and [opencode setup](opencode.md) for templates.

> **Workspace note:** `CLAUDE.md` must be in each project's git root folder. A `CLAUDE.md` at the workspace parent directory is not read by Claude Code.

---

## `sema init --claude --uninstall` did not remove sema

`sema init --claude --uninstall` calls `claude mcp remove sema -s user` and kills any running `sema serve` processes. If sema still appears after uninstalling:

```bash
# Verify it was removed
claude mcp list   # sema should not appear

# Kill any lingering processes manually
pkill -f "sema serve"

# Reload VS Code
# Cmd+Shift+P → Developer: Reload Window
```

---

## `ModuleNotFoundError: No module named 'sema'`

This means the `sema` binary on your PATH points to an environment where the package is not installed.

```bash
# 1. Find which binary is being called
which sema

# 2. Run the doctor command to diagnose
sema doctor

# 3a. Fix a normal install — reinstall (match how you installed)
uv tool install --force sema-mcp     # or: pipx reinstall sema-mcp

# 3b. Fix a git clone — reinstall from source
cd /path/to/sema && uv pip install -e ".[dev]"
```

The most common cause with a source checkout: you cloned sema to a second location, set PATH to that venv, but never ran `uv pip install -e .` there.

---

## sema shows as **Failed** in `/mcp`

This means Claude Code found the sema entry in config but the server process crashed on startup. The most common cause: the registered binary path is wrong.

**Check what is registered and what it's serving:**

```bash
sema status
```

This shows the registered binary path and which project the MCP server is pointed at. If the path is wrong or the binary doesn't exist, you'll see a warning with the exact fix.

**Fix: re-register after PATH is correct**

```bash
# Make sure sema on your PATH is the right one
which sema

# Remove old registration and re-register
sema init --claude --uninstall
sema init --claude

# Reload VS Code
# Cmd+Shift+P → Developer: Reload Window
```

This commonly happens when:
- You cloned sema to a second location (e.g. `~/Desktop/sema`) and registered from there, then later fixed your PATH to point elsewhere
- You deleted or rebuilt the `.venv` after registering
- You moved the sema folder after registering

The rule: always run `sema init --claude` **after** confirming `which sema` returns the path you want.

---

## sema returns results from the wrong project

This happens when the MCP server was registered from project A, then you open project B. The server is still serving project A's index.

**Diagnose:**

```bash
sema status
```

The `Serving` line shows which project the server is pointed at. If it doesn't match your current directory, you'll see a yellow warning with the exact fix command.

**Fix:**

```bash
cd your-project
sema init --claude --uninstall
sema init --claude
```

Then reload VS Code. The server will now serve the current project's index.

> **Note:** In single-project mode the server serves one project at a time — re-run `sema init --claude` when you switch. To serve several projects at once without re-registering, use [multi-project mode](multi-project.md) (`sema init --claude --root <dir>`).

---

## Index is stale — search results look wrong

The index reflects the state of your files at the time `sema index .` was last run. If you've made significant changes since then:

```bash
# Full re-index
sema index . --reset

# Or run the watcher so the index stays live
sema watch .
```
