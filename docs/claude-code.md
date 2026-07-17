# Claude Code setup

New to sema? See [Why sema](why-sema.md) for what it does and [Installation](installation.md) to install the `sema` command first.

## Install the Claude Code CLI

sema's **Claude Code** provider drives the `claude` CLI, so install it first (skip
if you already have it). It uses your Claude subscription — no API key.

```bash
# macOS / Linux (recommended)
curl -fsSL https://claude.ai/install.sh | bash

# Homebrew (macOS / Linux)
brew install --cask claude-code
```

Then start it once and follow the browser sign-in:

```bash
claude            # first run walks you through sign-in
claude --version  # verify
```

npm (`npm install -g @anthropic-ai/claude-code`, needs Node 18+) still works but is
deprecated in favour of the installers above. Full setup and troubleshooting:
<https://code.claude.com/docs/en/setup>.

## Step-by-step

```bash
# 1. Go to your project and index it — downloads ~80MB model on first run
cd your-project
sema index .

# 2. Register sema with every AI CLI you have (Claude Code, Codex, opencode, Grok Build)
sema setup
#    Or register Claude Code only:  sema init --claude

# 3. Reload VS Code (if using the VS Code extension)
#    Cmd+Shift+P → "Developer: Reload Window"

# 4. Verify the connection — type /mcp in Claude Code chat
#    You should see:  sema  ✓ connected
```

## Add `CLAUDE.md` to your project

Without this, Claude may still fall back to reading files directly. Create a `CLAUDE.md` at your project root:

```markdown
## Codebase navigation

This project is indexed by sema. Use sema tools to locate code before reading files.

| Goal | Tool |
|---|---|
| Find a function, class, or method | `search_code("natural language description")` |
| Check if something already exists before writing it | `check_reuse("what you're about to build")` |
| Read the full body of a known symbol | `get_code("exactSymbolName")` |
| Find all callers of a symbol | `find_usages("symbolName")` |
| Understand the overall architecture | `repo_map()` |
| See what a function calls and what calls it | `impact_analysis("symbolName")` |

**Rules:**
1. Always call `search_code()` before using Bash find/grep or Read to explore.
2. If `search_code()` returns relevant results, use `get_code()` for the full body — do not Read the whole file.
3. Before writing a new function or utility, call `check_reuse()`. If it finds an existing match, reuse or extend it instead of writing a parallel implementation.
4. Before changing a function, call `impact_analysis()` to understand the blast radius.
5. If sema returns no results, fall back to normal file navigation — the index may be stale.
```

## Keep the index fresh (optional)

Run this in a background terminal while you work:

```bash
sema watch .
```

Detects file saves and re-indexes only changed files incrementally.

## Multiple projects

To serve several projects from one registration (no re-running `sema init` when you switch repos), see [Working with multiple projects](multi-project.md).

## Uninstall

```bash
sema init --claude --uninstall
```
