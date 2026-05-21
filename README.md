# Sema

<p align="center">
  <img src="logo.png" alt="Sema" width="480" />
</p>

> **Experimental** — sema is under active development. APIs and index formats may change between versions. See the [Disclaimer](#disclaimer) section.

**Stop wasting tokens on file navigation. Speed up Claude Code and OpenAI Codex on large codebases.**

Sema is a semantic code indexer and MCP server. It indexes your entire codebase locally — every function, class, and method — and gives your AI assistant a search API so it never has to read files blindly again.

Works with
<a href="https://github.com/anthropics/claude-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/claude-ai.svg" alt="Claude" height="16" style="vertical-align:middle;" /> **Claude Code CLI**</a>,
<a href="https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="VS Code" height="16" style="vertical-align:middle;" /> **Claude Code VS Code**</a>,
and
<a href="https://github.com/openai/codex"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/openai.svg" alt="OpenAI" height="16" style="vertical-align:middle;" /> **OpenAI Codex CLI**</a>.

Every Claude Code session starts cold. On a large project, Claude burns 10,000–25,000 tokens just *navigating* — running `find`, reading full files, building a mental model from scratch — before it can help with anything. Sema fixes this at the root.

Index once. Claude searches forever.

---

## Table of Contents

- [Why sema](#why-sema)
- [How it works](#how-it-works)
- [Before and after](#before-and-after)
- [Requirements](#requirements)
- [Installation](#installation)
- [Claude Code setup](#claude-code-setup)
- [OpenAI Codex setup](#openai-codex-setup)
- [VS Code workspace setup](#vs-code-workspace-setup)
- [Troubleshooting](#troubleshooting)
- [Managing sema](#managing-sema)
  - [Update sema](#update-sema-to-the-latest-version)
- [CLI reference](#cli-reference)
- [MCP tools](#mcp-tools)
  - [impact_analysis — call graph](#impact_analysis----call-graph)
- [Supported languages](#supported-languages)
- [Project structure](#project-structure)
- [Configuration](#configuration)
- [When to re-index](#when-to-re-index)
- [Limitations](#limitations)
- [FAQ](#faq)
- [Disclaimer](#disclaimer)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)

---

## Why sema

Every Claude Code session starts cold. Claude has no memory of your codebase, so it explores — running shell commands, reading files one by one, building a mental model from scratch. This costs tokens, takes time, and happens again every single session.

The root problem: **Claude Code navigates by reading, not by searching.**

sema gives Claude a search index. Instead of:
```
find . -name "*.ts" | head -20
cat apps/api/src/auth/auth.service.ts       # 200 lines
cat apps/api/src/users/users.service.ts     # 150 lines
cat apps/api/src/auth/auth.controller.ts    # 120 lines
...
```

Claude does:
```
search_code("user authentication")          # 10 signatures, ~150 tokens
get_code("forgotPassword")                  # exact function body, ~300 tokens
```

Same answer. 4–11× fewer tokens. No file reading needed. (See [Before and after](#before-and-after) for measurements on real repos.)

---

## How it works

```
Your codebase
    │
    ▼
sema index .
    │
    ├── tree-sitter parses every function, class, method, interface
    ├── SBERT (all-MiniLM-L6-v2) embeds each chunk locally — no API key
    └── ChromaDB stores vectors + full source bodies on disk
                        │
                        ▼
              .sema/index/  (local, gitignored)
                        │
                        ▼
              MCP server (stdio)
                        │
                        ▼
              Claude Code ◄──► search_code / get_code / repo_map / ...
```

Every indexed unit is a **Chunk** — a function, class, method, or section of a config/doc file — with its full source stored alongside its embedding vector. `search_code()` returns signatures only. `get_code()` returns the full body on demand.

---

## Before and after

These comparisons use real, publicly available open-source repositories. Each shows the actual tool calls Claude would make without sema versus the sema approach — with token costs derived from real file sizes.

Token estimates: ~1 token per 4 characters of source code.

---

### Test 1 — [hoppscotch/hoppscotch](https://github.com/hoppscotch/hoppscotch) (TypeScript monorepo, 1,172 files)

**Question:** *"How does magic link authentication work end-to-end — which service methods and controller endpoints are involved?"*

**Without sema** — Claude has no index, explores by reading files:

| Step | Tool call | Tokens |
|---|---|---|
| Scan directory structure | `Bash: find . -name "*.ts" \| grep -i auth` | ~300 |
| Read auth service | `Read: auth/auth.service.ts` (392 lines) | 2,613 |
| Read auth controller | `Read: auth/auth.controller.ts` (230 lines) | 1,744 |
| Read JWT strategy | `Read: auth/strategies/jwt.strategy.ts` (110 lines) | 718 |
| Read mailer service | `Read: mailer/mailer.service.ts` (89 lines) | 621 |
| Read auth module | `Read: auth/auth.module.ts` (66 lines) | 479 |
| **Total** | **6 tool calls** | **~6,475 tokens** |

**With sema** — one search surfaces the exact symbols:

| Step | Tool call | Tokens |
|---|---|---|
| Find relevant symbols | `search_code("magic link authentication send email")` | 237 |
| Read service implementation | `get_code("signInMagicLink")` | 465 |
| Read controller endpoint | `get_code("signInMagicLink")` (controller) | 135 |
| **Total** | **3 tool calls** | **837 tokens** |

```
search_code("magic link authentication send email")

→ auth/auth.service.ts::signInMagicLink           (100% match)
     method: signInMagicLink(email: string, origin: string)
→ auth/auth.controller.ts::signInMagicLink         (95% match)
     method: signInMagicLink(@Body() authData, @Query() origin)
→ platform/auth/web/index.ts::sendMagicLink        (96% match)
     function: sendMagicLink(email: string)
→ mailer/mailer.service.ts::sendEmail              (97% match)
     method: sendEmail(...)
```

**Result: 3 tool calls vs 6, 837 tokens vs 6,475 tokens — 8× reduction.**

---

### Test 2 — [fastapi-users/fastapi-users](https://github.com/fastapi-users/fastapi-users) (Python, 123 files)

**Question:** *"How does JWT token creation and validation work? Where is the token written and how is it decoded?"*

**Without sema:**

| Step | Tool call | Tokens |
|---|---|---|
| Find Python files related to JWT | `Bash: grep -r "jwt\|token" --include="*.py" -l` | ~200 |
| Read JWT strategy | `Read: authentication/strategy/jwt.py` (72 lines) | 506 |
| Read JWT utilities | `Read: fastapi_users/jwt.py` (41 lines) | 233 |
| Read user manager | `Read: fastapi_users/manager.py` (715 lines) | 5,024 |
| **Total** | **4 tool calls** | **~5,963 tokens** |

*(manager.py must be read in full because the create/register flow spans the whole file — no way to know which lines matter without reading it)*

**With sema:**

| Step | Tool call | Tokens |
|---|---|---|
| Find JWT symbols | `search_code("JWT token create write validate")` | 229 |
| Read token generator | `get_code("generate_jwt")` | 106 |
| Read strategy write | `get_code("write_token")` | 331 |
| **Total** | **3 tool calls** | **666 tokens** |

```
search_code("JWT token create write validate")

→ fastapi_users/jwt.py::generate_jwt              (93% match)
     function: def generate_jwt(data, secret, lifetime_seconds, algorithm) -> str
→ authentication/strategy/jwt.py::write_token     (in get_code result)
     method: def write_token(self, user: models.UP) -> str
→ authentication/strategy/jwt.py::read_token      (related)
     method: def read_token(self, token: str, ...) -> models.UP
```

**Result: 3 tool calls vs 4, 666 tokens vs 5,963 tokens — 9× reduction.**

---

### Test 3 — [gothinkster/golang-gin-realworld-example-app](https://github.com/gothinkster/golang-gin-realworld-example-app) (Go, 30 files)

**Question:** *"How does the authentication middleware work — how is the JWT token extracted and validated per request?"*

**Without sema** — small repo, but still requires reading multiple files:

| Step | Tool call | Tokens |
|---|---|---|
| Explore project structure | `Bash: ls -la users/ common/` | ~150 |
| Read middleware file | `Read: users/middlewares.go` (75 lines) | 487 |
| Read token utilities | `Read: common/utils.go` (99 lines) | 760 |
| Read router setup | `Read: users/routers.go` (137 lines) | 1,000 |
| **Total** | **4 tool calls** | **~2,397 tokens** |

**With sema:**

| Step | Tool call | Tokens |
|---|---|---|
| Find auth middleware | `search_code("authentication middleware JWT token")` | 185 |
| Read middleware logic | `get_code("AuthMiddleware")` | 235 |
| Read token generator | `get_code("GenToken")` | 132 |
| **Total** | **3 tool calls** | **552 tokens** |

```
search_code("authentication middleware JWT token")

→ users/middlewares.go::AuthMiddleware             (100% match)
     function: func AuthMiddleware(auto401 bool) gin.HandlerFunc
→ users/middlewares.go::extractToken               (96% match)
     function: func extractToken(c *gin.Context) string
→ common/utils.go::GenToken                        (98% match)
     function: func GenToken(id uint) string
```

**Result: 3 tool calls vs 4, 552 tokens vs 2,397 tokens — 4× reduction.**

*(The Go repo has only 30 files — sema's advantage grows with codebase size.)*

---

### Summary

| Repo | Language | Files | Without sema | With sema | Reduction |
|---|---|---|---|---|---|
| hoppscotch | TypeScript | 1,172 | 6,475 tokens / 6 calls | 837 tokens / 3 calls | **8×** |
| fastapi-users | Python | 123 | 5,963 tokens / 4 calls | 666 tokens / 3 calls | **9×** |
| golang-gin-realworld | Go | 30 | 2,397 tokens / 4 calls | 552 tokens / 3 calls | **4×** |

Token counts are measured using `tiktoken` (cl100k_base) on the actual files from each repo, and on real `search_code` / `get_code` output. The "without" bash command costs are estimated at ~150–300 tokens each.

The pattern: sema always uses 3 tool calls (search → fetch → fetch). The "without" cost grows with repo size because Claude must read whole files to locate relevant code. On large TypeScript or Python projects the savings are consistently 8–9×.

---

## Requirements

- Python 3.11 or higher
- One of:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) or [VS Code extension](https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code)
  - [OpenAI Codex CLI](https://github.com/openai/codex) (`npm install -g @openai/codex`)
- ~80MB disk space for the embedding model (downloaded once, cached globally)
- No Docker, no external APIs, no GPU — runs entirely on your machine

---

## Installation

> sema is not yet published to PyPI. Install from source.

### Install from source

```bash
# 1. Clone the repository
git clone https://github.com/masihmoloodian/sema.git
cd sema

# 2. Create a virtual environment
python3 -m venv .venv

# 3. Activate it
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 4. Install sema and all dependencies
pip install -e ".[dev]"

# 5. Verify installation
sema --version
```

### Using uv (faster install)

```bash
git clone https://github.com/masihmoloodian/sema.git
cd sema

uv venv --python 3.12 .venv
uv pip install -e ".[dev]"

.venv/bin/sema --version
```

> If `sema` is not on your PATH after installing, use the full path:
> `.venv/bin/sema` (macOS/Linux) or `.venv\Scripts\sema` (Windows)

---

## Claude Code setup

### Step-by-step

```bash
# 1. Go to your project and index it — downloads ~80MB model on first run
cd your-project
sema index .

# 2. Register sema with Claude Code
sema init

# 3. Reload VS Code (if using the VS Code extension)
#    Cmd+Shift+P → "Developer: Reload Window"

# 4. Verify the connection — type /mcp in Claude Code chat
#    You should see:  sema  ✓ connected
```

### Add `CLAUDE.md` to your project

Without this, Claude may still fall back to reading files directly. Create a `CLAUDE.md` at your project root:

```markdown
## Codebase navigation

This project is indexed by sema. Use sema tools to locate code before reading files.

| Goal | Tool |
|---|---|
| Find a function, class, or method | `search_code("natural language description")` |
| Read the full body of a known symbol | `get_code("exactSymbolName")` |
| Find all callers of a symbol | `find_usages("symbolName")` |
| Understand the overall architecture | `repo_map()` |
| See what a function calls and what calls it | `impact_analysis("symbolName")` |

**Rules:**
1. Always call `search_code()` before using Bash find/grep or Read to explore.
2. If `search_code()` returns relevant results, use `get_code()` for the full body — do not Read the whole file.
3. Before changing a function, call `impact_analysis()` to understand the blast radius.
4. If sema returns no results, fall back to normal file navigation — the index may be stale.
```

### Keep the index fresh (optional)

Run this in a background terminal while you work:

```bash
sema watch .
```

Detects file saves and re-indexes only changed files incrementally.

### Uninstall

```bash
sema init --uninstall
```

---

## OpenAI Codex setup

### Step-by-step setup

```bash
# 1. Install Codex CLI (if not already installed)
npm install -g @openai/codex

# 2. Go to your project and index it
cd /your/project
sema index .

# 3. Register sema with Codex
sema init --codex

# 4. Restart Codex from your project directory
cd /your/project
codex
```

That's it. Type `/mcp` inside Codex to confirm sema shows as **Enabled**.

### What gets written

`sema init --codex` creates `.codex/config.toml` inside your project:

```toml
[mcp_servers.sema]
enabled = true
command = "/absolute/path/to/sema"
args = ["serve", "--project", "/absolute/path/to/your/project"]
startup_timeout_sec = 15.0
tool_timeout_sec = 60.0
```

> **Why project-level config?** Codex reads `.codex/config.toml` from the current project directory. Unlike VS Code, Codex does not support template variables like `{workspace_folder}` in MCP args — the project path must be hardcoded. Project-level config is the correct pattern: each project gets its own entry pointing to its own index.

Because the config contains an absolute path specific to your machine, add it to `.gitignore`:

```bash
echo ".codex/" >> .gitignore
```

### Add `AGENTS.md` to your project

Codex reads `AGENTS.md` the way Claude Code reads `CLAUDE.md`. Without it, Codex may not call sema tools automatically. Create one at your project root:

```markdown
## Codebase navigation

This project is indexed by sema. Use sema MCP tools to locate code — do not use grep or read files directly.

| Goal | Tool |
|---|---|
| Find a function, class, or method | `search_code("natural language description")` |
| Read full source of a known symbol | `get_code("symbolName")` |
| Find all callers of a symbol | `find_usages("symbolName")` |
| Understand call chains and blast radius | `impact_analysis("symbolName")` |
| Architecture overview | `repo_map()` |

Always call `search_code()` before using grep or reading files directly.
```

### Verify it's working

Inside a Codex session, ask:

```
Use search_code to find how authentication works in this codebase
```

Codex should call `search_code(...)` directly and return matching function signatures — not fall back to ripgrep or file reading.

### Uninstall

```bash
sema init --codex --uninstall
```

This removes the `[mcp_servers.sema]` block from `.codex/config.toml`.

---

## VS Code workspace setup

A VS Code workspace (`.code-workspace` file) groups multiple project folders under one window. Sema supports this with two extra flags.

### Step-by-step

**1. Index only the workspace folders — not the whole parent directory**

```bash
cd /path/to/workspace          # the directory containing your .code-workspace file
sema index . --workspace my-project.code-workspace
```

The `--workspace` flag reads the `.code-workspace` file and indexes only the listed folders. Without it, `sema index .` would walk everything in the parent directory including unrelated repos.

Paths in the index include the project folder name (`backend/src/auth.ts`, not just `src/auth.ts`), so results are always unambiguous across projects.

**2. Register with Claude Code**

```bash
sema init
```

This runs `claude mcp add sema -s user` under the hood, which registers sema at the user level — visible in every project and workspace, not just one folder.

**3. Add CLAUDE.md to each project folder**

Claude Code anchors CLAUDE.md to each project's git root — not to the workspace parent directory. A CLAUDE.md at `/workspace/CLAUDE.md` is invisible to Claude when you're working in `/workspace/backend/`.

Create a CLAUDE.md in each project folder:

```bash
# Run from the workspace root
for dir in backend frontend bo; do
  cat > $dir/CLAUDE.md << 'EOF'
## Codebase navigation

This project is indexed by sema. Use sema tools to locate code before reading files.

| Goal | Tool |
|---|---|
| Find a function, class, or method | `search_code("natural language description")` |
| Read the full body of a known symbol | `get_code("exactSymbolName")` |
| Find where a symbol is called or referenced | `find_usages("symbolName")` |
| Understand what a file exports | `explain_file("path/to/file.ts")` |
| Understand the overall architecture | `repo_map()` |
| See what a function calls and what calls it | `impact_analysis("symbolName")` |

**Always call `search_code()` before using Bash find/grep or Read to explore. Before changing a function, call `impact_analysis()` to understand the blast radius.**
EOF
done
```

**4. Watch for changes across all workspace folders**

```bash
sema watch . --workspace my-project.code-workspace
```

A file save in any project triggers incremental re-indexing automatically.

---

## Troubleshooting

### sema not listed in `/mcp`

**Step 1 — Check that `sema init` ran successfully**

```bash
claude mcp list
```

You should see `sema` in the output. If not, re-run:

```bash
cd your-project
sema init
```

`sema init` calls `claude mcp add sema -s user` internally. If the `claude` binary is not found, it will print the exact command to run manually:

```bash
claude mcp add sema -s user -- /path/to/.venv/bin/sema serve --project /path/to/project
```

**Step 2 — Reload VS Code**

After registering, Claude Code needs a reload:

`Cmd+Shift+P` → `Developer: Reload Window`

Then type `/mcp` in Claude Code chat. You should see `sema ✓ connected`.

**Step 3 — Verify the MCP server starts**

```bash
/path/to/.venv/bin/sema serve --project /path/to/your-project
```

This should block silently (no output). If it crashes with an error, that's the root cause — fix it, then re-run `sema init`.

> **Note:** Seeing `Invalid JSON: EOF while parsing` when running `serve` manually is normal. The MCP server communicates over stdin/stdout — it expects JSON-RPC messages from Claude Code, not interactive terminal input.

---

### sema registered but still not showing in `/mcp`

This usually means Claude Code is reading config from a different location than where `sema init` wrote. We discovered this the hard way.

**The issue:** Different Claude Code interfaces read MCP config differently:

| Interface | Where it reads MCP config |
|---|---|
| Claude Code CLI (`claude`) | `~/.claude.json` → `projects[path].mcpServers` |
| VS Code extension | `claude mcp add -s user` (user-scoped) |
| VS Code workspace | Same — user-scoped, NOT from `.code-workspace` `"mcp"` section |

`sema init` now uses `claude mcp add -s user` which writes to the correct location for all interfaces. If you registered sema with an older version of sema (which wrote to `.claude/settings.json`), remove the old config and re-register:

```bash
# Remove old project-level config if present
cat your-project/.claude/settings.json   # check if sema is in here

# Re-register correctly
cd your-project
sema init
```

---

### VS Code workspace: sema not showing after `sema init`

**The cause:** When VS Code opens a `.code-workspace` file, the Claude Code extension does NOT read the `"mcp"` section inside the workspace file. It also does NOT read `.claude/settings.json` from the workspace parent directory.

**The fix:** `sema init` registers sema at the user level (`-s user`) which works for all workspaces. Make sure you ran it:

```bash
claude mcp list   # should show sema
```

If sema is listed there but still not showing in `/mcp` inside VS Code, reload the window:

`Cmd+Shift+P` → `Developer: Reload Window`

---

### Claude is connected to sema but never calls the tools

Two causes:

**1. Tools not yet approved**

The first time Claude tries to call a sema tool, VS Code asks for permission. If you dismissed that prompt, the tools are blocked.

Fix: In Claude Code chat, explicitly ask Claude to use sema:

```
use search_code to find authentication logic
```

When prompted "Allow sema to run search_code?", click **Allow Always**.

**2. No CLAUDE.md in the project**

Without a CLAUDE.md, Claude defaults to its own navigation strategy (Bash, Read). The CLAUDE.md is what tells Claude to call `search_code()` first.

Check if the file exists at the git root of the project you're working in:

```bash
cat your-project/CLAUDE.md
```

If it's missing or doesn't mention sema, add it. See [Add CLAUDE.md to your project](#add-claudemd-to-your-project) for the template.

> **Workspace note:** CLAUDE.md must be in each project's git root folder. A CLAUDE.md at the workspace parent directory is not read by Claude Code.

---

### `sema init --uninstall` did not remove sema

`sema init --uninstall` calls `claude mcp remove sema -s user` and kills any running `sema serve` processes. If sema still appears after uninstalling:

```bash
# Verify it was removed
claude mcp list   # sema should not appear

# Kill any lingering processes manually
pkill -f "sema serve"

# Reload VS Code
# Cmd+Shift+P → Developer: Reload Window
```

---

### Index is stale — search results look wrong

The index reflects the state of your files at the time `sema index .` was last run. If you've made significant changes since then:

```bash
# Full re-index
sema index . --reset

# Or run the watcher so the index stays live
sema watch .
```

---

## Managing sema

### Remove the index for a project

```bash
cd your-project
rm -rf .sema/
```

This deletes the vector database and metadata. Run `sema index .` to rebuild.

### Deactivate sema for a project (keep index)

```bash
cd your-project
sema init --uninstall
```

This calls `claude mcp remove sema -s user` and kills any running `sema serve` processes. To re-activate, run `sema init` again.

### Update sema to the latest version

```bash
cd /path/to/sema

# Pull the latest changes
git pull

# Re-install (only needed if dependencies changed)
pip install -e ".[dev]"
# or with uv:
uv pip install -e ".[dev]"

# Check the version
sema --version
```

Your existing project indexes are untouched — no need to re-run `sema index .` unless the release notes say the index format changed.

> Once sema is published to PyPI, updating will be just `pip install --upgrade sema`.

### Fully remove sema from your machine

```bash
# 1. Deactivate from all projects first
cd your-project && sema init --uninstall

# 2. Delete the sema repo and virtual environment
rm -rf /path/to/sema

# 3. Delete the cached embedding model (~80MB)
rm -rf ~/.cache/sema/

# 4. Delete any project indexes
find ~ -type d -name ".sema" 2>/dev/null
# Review the list, then remove each:
rm -rf /your-project/.sema/
```

---

## CLI reference

```
sema index .                                  Index the current directory (skips unchanged files)
sema index . --reset                          Delete existing index and re-index everything from scratch
sema index . --workspace my.code-workspace    Index only the folders listed in a VS Code workspace file
sema watch .                                  Watch for file changes and re-index automatically
sema watch . --workspace my.code-workspace    Watch all workspace folders simultaneously
sema init                                     Register sema as MCP server with Claude Code (via claude mcp add -s user)
sema init --uninstall                         Remove sema from Claude Code and kill running processes
sema init --codex                             Register sema as MCP server with OpenAI Codex (.codex/config.toml in project)
sema init --codex --uninstall                 Remove sema from Codex config
sema search "query"                           Run a hybrid semantic+BM25 search (test without Claude)
sema search "query" --top-k 10               Return more results
sema search "query" --all-types               Include docs/config sections in results
sema status                                   Show index stats (chunks, files, model, last updated)
sema serve --project .                        Start MCP server (called automatically by Claude Code)
```

---

## MCP tools

These are the tools Claude calls during a session. You never call them directly.

| Tool | Input | Returns | Tokens |
|---|---|---|---|
| `search_code(query)` | Natural language | Matching function/class signatures + file locations | ~100–200 |
| `get_code(symbol)` | Exact symbol name | Full source body — all implementations if name appears in multiple files | ~200–500 |
| `repo_map()` | — | Compressed architecture overview: files + exported symbols | ~400–800 |
| `find_usages(symbol)` | Symbol name | Call sites and references (signatures only) | ~150–300 |
| `explain_file(path)` | Relative file path | File summary: exports, classes, functions — no source code | ~100–200 |
| `impact_analysis(symbol)` | Symbol name | Call graph: what it calls + what calls it, up to 3 levels deep | ~100–400 |

### `impact_analysis` — call graph

`impact_analysis` answers two questions at once: *what does this function call?* and *what calls this function?* — traversed up to `depth` levels in both directions. Use it before changing a function to understand the blast radius.

```
impact_analysis("validateToken", depth=2)

Impact analysis for 'validateToken':

Calls (1 symbols, 1 level(s) deep):
  Level 1:
    → atob

Called by (3 callers, 1 level(s) up):
  Level 1:
    src/auth/jwt.ts::refreshToken  [line 29]
      function: refreshToken(userId: string, token: string): Promise<TokenPair>
    src/auth/middleware.ts::requireAuth  [line 3]
      function: requireAuth(req: any, res: any, next: any): void
    src/auth/middleware.ts::optionalAuth  [line 18]
      function: optionalAuth(req: any, res: any, next: any): void
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `symbol_name` | string | required | Exact function or method name |
| `depth` | int | 2 | Levels to traverse in both directions (1–3) |
| `file_path` | string | — | Narrow to a specific file when multiple files define the same symbol name |

**How it works:**

At index time, each function's AST is walked to extract every call site. Calls are stored as qualified names where possible (`jwt.verify`, `uuid.uuid4`) or bare names otherwise (`validateToken`). Common language builtins (`len`, `console.log`, `fmt.Printf`) are filtered out so the graph only shows your own symbols.

At query time the call graph is traversed breadth-first in both directions:
- **Callees** — what `symbol` calls, then what those call (downward, up to `depth` levels)
- **Callers** — what calls `symbol`, then what calls those callers (upward, up to `depth` levels)

Caller lookups are backed by an in-memory inverted index built on first use — subsequent calls are sub-millisecond regardless of codebase size. Qualified-name queries also match suffix: searching for `verify` returns callers that recorded `jwt.verify`.

**When to use it:**

- Before refactoring a function — see everything that will break
- Before changing a function signature — find all callers at once
- When debugging — trace how a call propagates through your stack
- Code review — quickly understand the scope of a change

---

## Supported languages

sema has two levels of indexing support:

### AST-aware — full symbol extraction

These parsers use tree-sitter to extract individual functions, classes, and methods with proper signatures. `search_code` and `get_code` work at symbol granularity.

| Language | Extensions |
|---|---|
| TypeScript / JavaScript | `.ts` `.tsx` `.js` `.jsx` |
| Python | `.py` |
| Go | `.go` |

### Text-aware — semantic section chunking

These files are split into ~50-line sections and embedded as prose. Content is fully searchable — sema understands what a `config.yaml` or `README.md` says — but there are no named symbols to `get_code()` by.

| Type | Extensions / filenames |
|---|---|
| Markdown | `.md` `.mdx` — split by headings |
| Config | `.json` `.yaml` `.yml` `.toml` `.ini` |
| Styles | `.css` `.scss` |
| Shell | `.sh` `.bash` |
| Data / query | `.sql` `.graphql` `.xml` |
| Dotfiles | `.env` `.gitignore` `.dockerignore` `.envrc` |
| Project files | `Makefile` `Dockerfile` `Jenkinsfile` |

### Adding support for a new language

The parser is a registry — adding a new language requires no changes to core code:

```python
from sema.indexer.parser import register
from my_rust_parser import extract_chunks   # any callable (source, file_path) -> list[Chunk]

register([".rs"], extract_chunks)
```

For languages without a dedicated tree-sitter grammar, the generic text chunker works as a baseline:

```python
from sema.indexer.parser import register
from sema.indexer.languages.generic import extract_chunks

register([".rb", ".java", ".kt", ".swift", ".cs", ".php"], extract_chunks)
```

This indexes the raw source text semantically — not symbol-level, but better than nothing.

---

## Project structure

```
sema/
├── pyproject.toml                  # package definition, deps, entry point
├── README.md
├── CLAUDE.md                       # instructions for Claude when working on sema itself
├── LICENSE
├── logo.svg
│
├── sema/
│   ├── cli.py                      # Click CLI: index, init, serve, search, status
│   │
│   ├── indexer/
│   │   ├── parser.py               # parser registry — register() for new formats
│   │   ├── chunker.py              # orchestrates parse → embed → store
│   │   ├── embedder.py             # SBERT wrapper (lazy model load, batch embedding)
│   │   ├── builtins.py             # per-language builtin sets filtered from call graph
│   │   └── languages/
│   │       ├── typescript.py       # tree-sitter TS/JS chunk extraction + call extraction
│   │       ├── python.py           # tree-sitter Python chunk extraction + call extraction
│   │       ├── golang.py           # tree-sitter Go chunk extraction + call extraction
│   │       ├── markdown.py         # heading-based section chunker
│   │       └── generic.py          # sliding-window text chunker (json, yaml, env, css…)
│   │
│   ├── store/
│   │   ├── schema.py               # Chunk dataclass — the core data model
│   │   ├── chroma.py               # ChromaDB embedded client wrapper
│   │   └── hashes.py               # SHA-256 hash store for incremental indexing
│   │
│   ├── mcp/
│   │   ├── server.py               # MCP stdio server entry point
│   │   └── tools.py                # all 6 MCP tool implementations
│   │
│   └── utils/
│       ├── file_walker.py          # walks project, respects .gitignore
│       ├── gitignore.py            # .gitignore pattern matching
│       └── repo_map.py             # compressed repo map generator
│
└── tests/
    ├── conftest.py
    ├── fixtures/example-repo/      # TS + Python + Go fixture for tests
    ├── test_parser.py
    ├── test_store.py
    ├── test_chunker.py
    └── test_tools.py
```

---

## Configuration

### `.sema/config.toml` (optional)

```toml
[index]
include = ["*.ts", "*.tsx", "*.go", "*.py", "*.js"]
exclude = ["*.test.ts", "*.spec.ts", "*_test.go", "test_*.py"]

[model]
name = "all-MiniLM-L6-v2"
```

### Environment variables

```
SEMA_INDEX_PATH    Override index location (default: .sema/index/)
SEMA_MODEL         Override embedding model name
SEMA_LOG_LEVEL     debug | info | warning (default: warning)
```

### `.gitignore`

`sema init` adds this automatically. If you prefer to do it manually:

```gitignore
# sema
.sema/index/
```

Commit `.sema/meta.json` if you want teammates to see index stats. The index itself is machine-specific and should not be committed.

---

## When to re-index

The easiest option — run `sema watch` in a terminal while you work. It re-indexes any file the moment you save it, so the index is always current:

```bash
sema watch
# 14:22:31  indexed   src/auth/auth.service.ts  (12 chunks)
# 14:22:45  indexed   src/users/users.service.ts  (8 chunks)
# Ctrl+C to stop
```

If you prefer manual re-indexing, sema tracks a SHA-256 hash of every indexed file in `.sema/hashes.json`. Running `sema index .` again only re-embeds files that actually changed — unchanged files are skipped instantly:

```bash
sema index .
# ✔ Indexed 2 files (847 unchanged, skipped)
# ✔ Generated 11 chunks
```

Use `--reset` to force a full re-index from scratch (also clears the hash store):

```bash
# Force full re-index — use after upgrading sema or changing .gitignore
sema index . --reset
```

You do **not** need to re-index when:
- Starting a new Claude Code chat session
- Restarting VS Code
- Asking a different question about the same codebase

---

## Limitations

Known limitations in the current version (v0.1.x):

- **AST-aware parsers for TypeScript, Python, Go only** — Ruby, Rust, Java, C#, and others fall back to generic text chunking (searchable, but no symbol-level granularity)
- **Call graph is name-based** — calls are matched by symbol name, not by resolved reference; two functions with the same name in different files are indistinguishable to the graph
- **`find_usages` is approximate** — uses semantic similarity, not AST-level reference tracking; may miss some call sites
- **Single project per server** — one `sema serve` process serves one project root
- **Model fixed at index time** — changing the embedding model requires a full re-index
- **Tested on macOS only** — Apple Silicon M4 Pro, macOS 26.4; Linux likely works; Windows untested

---

## FAQ

**Why is Claude Code so slow at the start of a session?**
Claude Code has no persistent memory of your codebase between sessions. Every new chat starts cold — Claude has to run `find`, read files, and explore directories to build context before it can help. On a project with 50+ files this costs thousands of tokens and tens of seconds before Claude writes a single line of code. Sema solves this by pre-indexing your codebase so Claude can search instead of explore.

**Why does Claude Code use so many tokens?**
The main culprit is file navigation. Without an index, Claude reads entire files to find the one function it needs. On a 1,000-file TypeScript project, a single "how does auth work?" question can consume 10,000+ tokens just in file reads. Sema's `search_code()` returns only the relevant signatures (~180 tokens), and `get_code()` fetches only the exact function body needed (~300–500 tokens each).

**How do I speed up Claude Code on a large codebase?**
Install Sema, run `sema index .` once in your project, then `sema init` to register it with Claude Code. Add a `CLAUDE.md` file that tells Claude to call `search_code()` first. From that point on Claude searches your index instead of reading files — typically 5–10× fewer tool calls per question.

**Does sema send my code to any external service?**
No. Sema runs entirely on your machine. The embedding model (`all-MiniLM-L6-v2`) is downloaded once (~80MB) and cached locally. No API keys, no internet connection required after setup, no data leaves your machine.

**What is an MCP server for Claude Code?**
MCP (Model Context Protocol) is the standard that Claude Code uses to call external tools. Sema registers itself as a local MCP server — Claude Code connects to it over stdio and gains five new tools: `search_code`, `get_code`, `find_usages`, `repo_map`, and `explain_file`. These tools give Claude structured access to your codebase without reading raw files.

**Does sema work with TypeScript, Python, Go, and other languages?**
Yes. Sema has full AST-aware parsers for TypeScript, JavaScript, Python, and Go (symbol-level granularity). All other languages and formats — including Rust, Java, Ruby, Markdown, JSON, YAML, CSS, SQL, and more — are indexed via text chunking, which makes them searchable even without symbol extraction.

---

## Disclaimer

> sema is an **experimental project** built to explore semantic code indexing for AI-assisted development.
>
> - The index format, CLI interface, and MCP tool signatures **may change** between versions without notice
> - There is **no guarantee of correctness** — sema may miss chunks, return stale results, or fail on unusual code patterns
> - The embedding model (`all-MiniLM-L6-v2`) runs locally and is **not fine-tuned for code** — results are based on general semantic similarity
> - sema has been tested on one machine (Apple Silicon M4 Pro, macOS 26.4) and one codebase type (NestJS + Next.js TypeScript monorepo); your results may vary
> - **Do not rely on sema for security-sensitive analysis** — it is a navigation aid, not a code analysis tool
>
> Use it, break it, improve it. That's the point.

---

## Roadmap

### v0.2 — Tool improvements
- [x] `find_usages` backed by grep for exact reference matching
- [x] Call graph: `impact_analysis(symbol, depth)` — callers + callees, BFS multi-level, qualified names, builtin filtering, inverted index cache
- [x] `explain_file` includes import graph (project vs package imports, split by relative/absolute)
- [x] Better error messages when index is stale (empty results, low confidence, symbol not found)

### v0.3 — Incremental indexing
- [x] File watcher: `sema watch` re-indexes changed files automatically
- [x] Workspace support: `sema index --workspace` and `sema watch --workspace` index only listed folders with correct base paths
- [x] Incremental indexing: SHA-256 hash store skips unchanged files; `sema index .` on an already-indexed project is ~20× faster
- [ ] Git hook: `sema init --watch` installs a post-commit hook

### v0.4 — More AST-aware parsers
- [ ] Rust (`.rs`) — tree-sitter-rust
- [ ] Java / Kotlin (`.java`, `.kt`) — tree-sitter-java
- [ ] Ruby (`.rb`) — tree-sitter-ruby
- [ ] C# (`.cs`) — tree-sitter-c-sharp
- [ ] C/C++ (`.c`, `.cpp`, `.h`) — tree-sitter-c
- All of these already produce text-level chunks today; these upgrades add symbol granularity

### v0.5 — Multi-project & monorepo
- [ ] Single `sema serve` handles multiple project roots
- [x] Workspace-level index for monorepos (`--workspace` flag)
- [ ] Cross-project symbol search

### v1.0 — Public release
- [ ] Publish to PyPI
- [ ] Homebrew formula
- [ ] Auto-detect and configure Cursor, Copilot, Windsurf
- [ ] Token savings report after each index
- [ ] CI/CD: auto-publish on git tag

---

## Contributing

Contributions are welcome. sema is intentionally small — each module has a single responsibility and the test suite makes it straightforward to extend.

### Development setup

```bash
git clone https://github.com/masihmoloodian/sema.git
cd sema

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate       # macOS/Linux
# .venv\Scripts\activate        # Windows

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run linter
ruff check sema/
```

### Adding a new language

The parser is a registry. You can register a new format without touching any core file.

**Option A — generic text baseline (no new deps):**

```python
from sema.indexer.parser import register
from sema.indexer.languages.generic import extract_chunks

register([".rb", ".java", ".rs"], extract_chunks)
```

Call this before `index_project()` runs (e.g. in a plugin loaded at startup).

**Option B — AST-aware with tree-sitter (recommended for code):**

1. Create `sema/indexer/languages/yourlang.py`
   - Implement `extract_chunks(source: str, file_path: str) -> list[Chunk]`
   - Use `tree-sitter` to parse the AST
   - Return one `Chunk` per function, class, method, interface
2. Call `register([".ext"], yourlang.extract_chunks)` — or add it to `_register_builtins()` in `parser.py`
3. Add fixture source files to `tests/fixtures/example-repo/`
4. Add test cases to `tests/test_parser.py`

The embedding, storage, and search pipeline are fully language-agnostic — you only need to write the parser.

### Good first contributions

- Add support for a new language (Rust, Java, Ruby, C#)
- Improve `find_usages` with a grep-based exact match fallback
- Add `--verbose` output to `sema index` showing each file as it's processed
- Test sema on Linux or Windows and report/fix issues
- Improve search quality for a specific code pattern you've found lacking

### Submitting changes

1. Fork the repo
2. Create a branch: `git checkout -b feature/your-feature`
3. Make your changes and add tests
4. Run `pytest tests/ -v` and `ruff check sema/` — both must pass
5. Open a pull request with a clear description of what and why

---

## License

MIT License — free to use, modify, and distribute. See [LICENSE](LICENSE) for the full text.

Copyright (c) 2026 Masih Moloodian

---

## Contact

**Masih Moloodian**
[masihmoloodian@gmail.com](mailto:masihmoloodian@gmail.com)

Issues and feature requests: [github.com/masihmoloodian/sema/issues](https://github.com/masihmoloodian/sema/issues)
