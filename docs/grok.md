# Grok Build setup

New to sema? See [Why sema](why-sema.md) for what it does and [Installation](installation.md) to install the `sema` command first.

## Install the Grok Build CLI

sema's **Grok Build** provider drives xAI's `grok` CLI, so install it first (skip if
you already have it). Sign in with your existing SpaceXAI account — no API key needed.

```bash
# macOS / Linux
curl -fsSL https://x.ai/cli/install.sh | bash

# Windows PowerShell
irm https://x.ai/cli/install.ps1 | iex
```

Then sign in once (it opens a browser) and verify:

```bash
grok login
grok --version
```

## Step-by-step setup

```bash
# 1. Go to your project and index it — downloads ~80MB model on first run
cd /your/project
sema index .

# 2. Register sema with every AI CLI you have (Claude Code, Codex, opencode, Grok Build)
sema setup
#    Or register Grok Build only:  sema init --grok

# 3. Start Grok from your project directory and trust the folder when asked
grok
#    Type /mcps — you should see: sema, with its tool count

# 4. Verify from the shell at any time
grok mcp list          # sema ... (project)
grok mcp doctor sema   # ✓ handshake OK, ✓ 8 tools discovered
```

`sema init --grok` writes a `[mcp_servers.sema]` block to `.grok/config.toml` in your
project. Grok loads `.grok/config.toml` from every directory between your working
directory and the git root, so the registration follows the project rather than your
whole machine — commit it to share sema with your team.

## You must trust the folder first

**Grok does not start project-scoped MCP servers in an untrusted folder.** This is
deliberate: a repo you clone could otherwise run its own `.grok/config.toml` servers the
moment you open it. So `sema init --grok` succeeds, but sema stays dark until you grant
trust. `grok mcp doctor sema` names it plainly:

```
sema (stdio: /path/to/sema serve --project /your/project)
  ✗ folder untrusted (repo-local (project-scoped) server not started for an untrusted folder)
```

Fix it by running `grok` once in the project and accepting the trust prompt. Grok records
the grant in `~/.grok/trusted_folders.toml`, and sema loads from then on:

```
sema (stdio: /path/to/sema serve --project /your/project)
  ✓ command found
  ✓ server started (0.0s)
  ✓ handshake OK (protocol 2025-06-18)
  ✓ 8 tools discovered
```

Org-wide, folder trust can be turned off with `[folder_trust] enabled = false` in
`~/.grok/config.toml` or `GROK_FOLDER_TRUST=false`, but leaving it on is the safer default.

## Add `AGENTS.md` to your project

Grok reads `AGENTS.md` (and `CLAUDE.md`, for Claude Code compatibility). Without one, Grok may not call sema tools automatically. Create one at your project root:

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

## Grok-specific notes

**Tool names are namespaced.** Grok prefixes MCP tools with the server name, so sema's
tools appear as `sema__search_code`, `sema__check_reuse`, and so on. Grok reaches them
through its built-in `search_tool` / `use_tool` meta-tools rather than listing each one
directly, so write `AGENTS.md` in terms of the plain names above and let Grok resolve them.

**Large results are truncated at 20,000 bytes.** Grok caps each MCP tool result and
spills the remainder to a file under the session's `mcp/` folder. `search_code` returns
signatures only, so it stays well under; `repo_map()` on a large project can hit the cap.
Raise it if you see truncated maps:

```toml
# ~/.grok/config.toml or .grok/config.toml
[mcp]
max_output_bytes = 40000
```

or set `GROK_MAX_MCP_OUTPUT_BYTES`.

**If you already registered with Claude Code, sema may already be visible.** Grok reads
`~/.claude.json` for MCP servers by default. A project-scoped `.grok/config.toml` entry
takes priority over that import, so registering both is safe and never double-loads sema.

## Multiple projects

To serve several projects from one registration, see [Working with multiple projects](multi-project.md).

## Uninstall

```bash
sema init --grok --uninstall
```

This removes the `[mcp_servers.sema]` block from `.grok/config.toml`, leaving any other
MCP servers in the file untouched.
