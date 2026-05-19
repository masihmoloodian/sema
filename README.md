# Sema

<p align="center">
  <img src="logo.png" alt="Sema" width="480" />
</p>

> **Experimental** — sema is under active development. APIs and index formats may change between versions. See the [Disclaimer](#disclaimer) section.

**Speed up Claude Code on large codebases. Stop wasting tokens on file navigation.**

Sema is a semantic code indexer and MCP server for Claude Code. It indexes your entire codebase locally — every function, class, and method — and gives Claude a search API so it never has to read files blindly again. 

Works with
<a href="https://github.com/anthropics/claude-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/claude-ai.svg" alt="Claude" height="16" style="vertical-align:middle;" /> **Claude Code CLI** </a>
and
<a href="https://code.claude.com/docs/en/vs-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="Claude" height="16" style="vertical-align:middle;" /> Claude Code VS Code extension</a>.

Every Claude Code session starts cold. On a large project, Claude burns 10,000–25,000 tokens just *navigating* — running `find`, reading full files, building a mental model from scratch — before it can help with anything. Sema fixes this at the root.

Index once. Claude searches forever.

---

## Table of Contents

- [Why sema](#why-sema)
- [How it works](#how-it-works)
- [Before and after](#before-and-after)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Troubleshooting](#troubleshooting)
- [Managing sema](#managing-sema)
- [CLI reference](#cli-reference)
- [MCP tools](#mcp-tools)
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
| Grep for magic link references | `Bash: grep -r "magicLink\|magic_link" --include="*.ts" -l` | ~200 |
| Read auth service | `Read: auth/auth.service.ts` (392 lines) | ~4,200 |
| Read auth controller | `Read: auth/auth.controller.ts` (230 lines) | ~2,500 |
| Read JWT strategy | `Read: auth/strategies/jwt.strategy.ts` (110 lines) | ~1,200 |
| Read mailer service | `Read: mailer/mailer.service.ts` (89 lines) | ~1,000 |
| Read auth module | `Read: auth/auth.module.ts` (66 lines) | ~700 |
| **Total** | **7 tool calls** | **~10,100 tokens** |

**With sema** — one search surfaces the exact symbols:

| Step | Tool call | Tokens |
|---|---|---|
| Find relevant symbols | `search_code("magic link authentication send email")` | ~180 |
| Read service implementation | `get_code("signInMagicLink")` | ~410 |
| Read controller endpoint | `get_code("sendMagicLink")` | ~300 |
| **Total** | **3 tool calls** | **~890 tokens** |

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

**Result: 3 tool calls vs 7, ~890 tokens vs ~10,100 tokens — 11× reduction.**

---

### Test 2 — [fastapi-users/fastapi-users](https://github.com/fastapi-users/fastapi-users) (Python, 123 files)

**Question:** *"How does JWT token creation and validation work? Where is the token written and how is it decoded?"*

**Without sema:**

| Step | Tool call | Tokens |
|---|---|---|
| Find Python files related to JWT | `Bash: grep -r "jwt\|token" --include="*.py" -l` | ~200 |
| Read JWT strategy | `Read: authentication/strategy/jwt.py` (72 lines) | ~800 |
| Read JWT utilities | `Read: fastapi_users/jwt.py` (41 lines) | ~500 |
| Read user manager | `Read: fastapi_users/manager.py` (715 lines) | ~7,800 |
| **Total** | **5 tool calls** | **~9,300 tokens** |

*(manager.py must be read in full because the create/register flow spans the whole file — no way to know which lines matter without reading it)*

**With sema:**

| Step | Tool call | Tokens |
|---|---|---|
| Find JWT symbols | `search_code("JWT token create write validate")` | ~180 |
| Read token generator | `get_code("generate_jwt")` | ~250 |
| Read strategy write | `get_code("write_token")` | ~480 |
| **Total** | **3 tool calls** | **~910 tokens** |

```
search_code("JWT token create write validate")

→ fastapi_users/jwt.py::generate_jwt              (93% match)
     function: def generate_jwt(data, secret, lifetime_seconds, algorithm) -> str
→ authentication/strategy/jwt.py::write_token     (in get_code result)
     method: def write_token(self, user: models.UP) -> str
→ authentication/strategy/jwt.py::read_token      (related)
     method: def read_token(self, token: str, ...) -> models.UP
```

**Result: 3 tool calls vs 5, ~910 tokens vs ~9,300 tokens — 10× reduction.**

---

### Test 3 — [gothinkster/golang-gin-realworld-example-app](https://github.com/gothinkster/golang-gin-realworld-example-app) (Go, 30 files)

**Question:** *"How does the authentication middleware work — how is the JWT token extracted and validated per request?"*

**Without sema** — small repo, but still requires reading multiple files:

| Step | Tool call | Tokens |
|---|---|---|
| Explore project structure | `Bash: ls -la users/ common/` | ~150 |
| Read middleware file | `Read: users/middlewares.go` (75 lines) | ~810 |
| Read token utilities | `Read: common/utils.go` (99 lines) | ~1,100 |
| Read router setup | `Read: users/routers.go` (137 lines) | ~1,500 |
| **Total** | **4 tool calls** | **~3,560 tokens** |

**With sema:**

| Step | Tool call | Tokens |
|---|---|---|
| Find auth middleware | `search_code("authentication middleware JWT token")` | ~180 |
| Read middleware logic | `get_code("AuthMiddleware")` | ~454 |
| Read token generator | `get_code("GenToken")` | ~260 |
| **Total** | **3 tool calls** | **~894 tokens** |

```
search_code("authentication middleware JWT token")

→ users/middlewares.go::AuthMiddleware             (100% match)
     function: func AuthMiddleware(auto401 bool) gin.HandlerFunc
→ users/middlewares.go::extractToken               (96% match)
     function: func extractToken(c *gin.Context) string
→ common/utils.go::GenToken                        (98% match)
     function: func GenToken(id uint) string
```

**Result: 3 tool calls vs 4, ~894 tokens vs ~3,560 tokens — 4× reduction.**

*(The Go repo has only 30 files — sema's advantage grows with codebase size.)*

---

### Summary

| Repo | Language | Files | Without sema | With sema | Reduction |
|---|---|---|---|---|---|
| hoppscotch | TypeScript | 1,172 | ~10,100 tokens / 7 calls | ~890 tokens / 3 calls | **11×** |
| fastapi-users | Python | 123 | ~9,300 tokens / 5 calls | ~910 tokens / 3 calls | **10×** |
| golang-gin-realworld | Go | 30 | ~3,560 tokens / 4 calls | ~894 tokens / 3 calls | **4×** |

The pattern: sema always uses 3 tool calls (search → fetch → fetch). The "without" cost grows linearly with repo size because Claude must read more files to locate the right code. On large TypeScript or Python projects the savings are consistently 10×.

---

## Requirements

- Python 3.11 or higher
- Claude Code — [CLI](https://docs.anthropic.com/en/docs/claude-code) or [VS Code extension](https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code)
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

## Quick start

```bash
# 1. Go to your project
cd your-project

# 2. Index it — downloads the ~80MB model on first run
/path/to/sema/venv/bin/sema index .

# 3. Register sema as an MCP server with Claude Code
/path/to/sema/venv/bin/sema init

# 4. Reload VS Code
#    Press Cmd+Shift+P → "Developer: Reload Window"

# 5. Verify the connection
#    Open a new chat in Claude Code and type /mcp
#    You should see:  Local (1)  sema  ✓ Connected

# 6. Add a CLAUDE.md to your project (see Configuration section)
#    This tells Claude to use sema tools first
```

### Add CLAUDE.md to your project

Create a `CLAUDE.md` file in your project root. Without this, Claude may still fall back to reading files directly:

```markdown
## Codebase navigation

This project is indexed by sema. Use sema tools to locate code before reading files.

### Which tool to use

| Goal | Tool |
|---|---|
| Find a function, class, or method | `search_code("natural language description")` |
| Read the full body of a known symbol | `get_code("exactSymbolName")` |
| Find where a symbol is called or referenced | `find_usages("symbolName")` |
| Understand what a file exports | `explain_file("path/to/file.ts")` |
| Understand the overall architecture | `repo_map()` |

### Rules

1. **Always call `search_code()` before using Bash find/grep or Read to explore.**
2. If `search_code()` returns relevant results, use `get_code()` for the full body — do not Read the whole file.
3. If sema returns no results or the results look wrong, fall back to normal file navigation — the index may be stale or the symbol may not exist.
4. Only call `repo_map()` when you genuinely need an architecture overview — it costs ~400–800 tokens.
```

---

## Troubleshooting

### sema not listed in `/mcp`

**Check 1 — Did you run `sema init` from the right directory?**

`sema init` writes to `.claude/settings.json` in your *current working directory*. Claude Code only reads the `.claude/settings.json` that lives in the root of the project it has open. If you ran `sema init` from the wrong directory, the config landed in the wrong place.

Fix: `cd` to the project root that Claude Code has open, then re-run:

```bash
cd your-project   # the directory Claude Code is opened on
sema init
```

**Check 2 — Did you reload VS Code?**

After `sema init` writes the config, Claude Code needs a reload to pick it up:

`Cmd+Shift+P` → `Developer: Reload Window`

Then open a new chat and type `/mcp`. You should see `sema ✓ connected`.

**Check 3 — Inspect the config that was written**

```bash
cat your-project/.claude/settings.json
```

It should look like this:

```json
{
  "mcpServers": {
    "sema": {
      "command": "/absolute/path/to/.venv/bin/sema",
      "args": ["serve", "--project", "/absolute/path/to/your-project"]
    }
  }
}
```

Two things to verify:
- `command` — the path must point to an existing file. If it doesn't (`ls` it to confirm), re-run `sema init` with the venv activated so the correct binary path is detected.
- `args[1]` (`--project`) — must be the absolute path to the project root Claude Code has open.

**Check 4 — Verify the binary path manually**

```bash
# Does the binary exist?
ls $(cat your-project/.claude/settings.json | python3 -c "import sys,json; print(json.load(sys.stdin)['mcpServers']['sema']['command'])")

# Can it start the server?
/path/to/.venv/bin/sema serve --project /path/to/your-project
```

If the `serve` command errors, that's the root cause — fix it, then `sema init` again.

---

## Managing sema

### Remove the index for a project

```bash
cd your-project
rm -rf .sema/
```

This deletes the vector database and metadata. Run `sema index .` to rebuild.

### Deactivate sema for a project (keep index)

Remove sema from the project's Claude Code config:

```bash
cd your-project
sema init --uninstall
```

To re-activate, run `sema init` again.

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
sema index .                     Index the current directory
sema index . --reset             Delete existing index and re-index from scratch
sema index ./path                Index a specific path
sema watch                       Watch for file changes and re-index automatically
sema watch ./path                Watch a specific directory
sema init                        Register sema as MCP server (writes .claude/settings.json)
sema init --uninstall            Remove sema from Claude Code config
sema init --dry-run              Show what init would do without making changes
sema search "query"              Run a hybrid semantic+BM25 search (test without Claude)
sema search "query" --top-k 10  Return more results
sema search "query" --all-types  Include docs/config sections in results
sema status                      Show index stats (chunks, files, model, last updated)
sema serve --project .           Start MCP server (called automatically by Claude Code)
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
│   │   └── languages/
│   │       ├── typescript.py       # tree-sitter TS/JS chunk extraction
│   │       ├── python.py           # tree-sitter Python chunk extraction
│   │       ├── golang.py           # tree-sitter Go chunk extraction
│   │       ├── markdown.py         # heading-based section chunker
│   │       └── generic.py          # sliding-window text chunker (json, yaml, env, css…)
│   │
│   ├── store/
│   │   ├── schema.py               # Chunk dataclass — the core data model
│   │   └── chroma.py               # ChromaDB embedded client wrapper
│   │
│   ├── mcp/
│   │   ├── server.py               # MCP stdio server entry point
│   │   └── tools.py                # all 5 MCP tool implementations
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

```bash
# After adding new files or large refactors
sema index . --reset

# After small changes (new functions in existing files)
sema index .
```

You do **not** need to re-index when:
- Starting a new Claude Code chat session
- Restarting VS Code
- Asking a different question about the same codebase

---

## Limitations

Known limitations in the current version (v0.1.x):

- **No incremental indexing** — changed files require a full `--reset` re-index
- **No file watcher** — sema does not automatically detect file changes; re-index manually after code changes
- **AST-aware parsers for TypeScript, Python, Go only** — Ruby, Rust, Java, C#, and others fall back to generic text chunking (searchable, but no symbol-level granularity)
- **No call graph** — sema knows what each function does, but not which functions call which; Claude infers this from bodies
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
- [ ] `find_usages` backed by grep for exact reference matching
- [ ] `explain_file` includes import graph
- [ ] Better error messages when index is stale

### v0.3 — Incremental indexing
- [x] File watcher: `sema watch` re-indexes changed files automatically
- [ ] Git hook: `sema init --watch` installs a post-commit hook
- [ ] Only re-embed files changed since last index (tracked via git hash)

### v0.4 — More AST-aware parsers
- [ ] Rust (`.rs`) — tree-sitter-rust
- [ ] Java / Kotlin (`.java`, `.kt`) — tree-sitter-java
- [ ] Ruby (`.rb`) — tree-sitter-ruby
- [ ] C# (`.cs`) — tree-sitter-c-sharp
- [ ] C/C++ (`.c`, `.cpp`, `.h`) — tree-sitter-c
- All of these already produce text-level chunks today; these upgrades add symbol granularity

### v0.5 — Multi-project & monorepo
- [ ] Single `sema serve` handles multiple project roots
- [ ] Workspace-level index for monorepos
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
- Write `sema watch` using the `watchdog` library
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
