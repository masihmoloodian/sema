# Sema

**Semantic codebase indexer and MCP server for Claude Code.**

sema was built to solve a real pain: every time you start a Claude Code session, Claude has zero knowledge of your codebase. It wastes thousands of tokens running `find`, reading full files, and exploring directories before it can help with anything. On a large project, Claude can burn 10,000–25,000 tokens just *navigating* before writing a single line.

sema fixes this by indexing your codebase once — parsing every function, class, and method into semantic chunks, embedding them locally, and serving them through an MCP server. Claude calls `search_code("query")` instead of reading files blindly.

> **Experimental** — sema is under active development. APIs and index formats may change between versions. See the [Disclaimer](#disclaimer) section.

---

## Table of Contents

- [Why sema](#why-sema)
- [How it works](#how-it-works)
- [Before and after](#before-and-after)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Managing sema](#managing-sema)
- [CLI reference](#cli-reference)
- [MCP tools](#mcp-tools)
- [Supported languages](#supported-languages)
- [Project structure](#project-structure)
- [Configuration](#configuration)
- [When to re-index](#when-to-re-index)
- [Limitations](#limitations)
- [Disclaimer](#disclaimer)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)

---

## Why sema

Every Claude Code session starts cold. Claude has no memory of your codebase, so it explores — running shell commands, reading files one by one, building a mental model from scratch. This costs tokens, takes time, and happens again every single session.

The root problem: **Claude navigates by reading, not by searching.**

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

Same answer. 20× fewer tokens. No file reading needed.

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

**Question:** *"How does authentication work end-to-end?"*

### Without sema

| Step | Tool | Cost |
|---|---|---|
| Find relevant files | `Bash: find . -name "*.ts"` | ~500 tokens |
| Narrow to auth files | `Bash: find *auth* *token*` | ~200 tokens |
| Read controller | `Read: auth.controller.ts` | ~1,500 tokens |
| Read service | `Read: auth.service.ts` | ~2,800 tokens |
| Read user service | `Read: users.service.ts` | ~1,800 tokens |
| Read entity | `Read: user.entity.ts` | ~900 tokens |
| Read module | `Read: auth.module.ts` | ~400 tokens |
| More grep/find calls | `Bash` × 4 | ~800 tokens |
| **Total** | **12+ tool calls** | **~9,000 tokens** |

### With sema

| Step | Tool | Cost |
|---|---|---|
| Find relevant symbols | `search_code("user authentication")` | ~150 tokens |
| Get controller methods | `get_code("login")` | ~80 tokens |
| Get service implementations | `get_code("validateUser")` | ~500 tokens |
| Get token helper | `get_code("generateToken")` | ~150 tokens |
| **Total** | **4–7 tool calls** | **~880 tokens** |

**Result: same answer, ~10× fewer tokens, zero full-file reads.**

---

## Requirements

- Python 3.11 or higher
- Claude Code (VS Code extension or CLI)
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
# CLAUDE.md

## Codebase navigation — use sema MCP tools first

This project is indexed by sema. Always use sema tools before reading files.

### Which tool to use

**Backend / logic tasks** ("how does auth work?", "where is X validated?"):
1. `search_code("query")` — find relevant functions/classes by natural language
2. `get_code("symbolName")` — read one function's full body
3. `find_usages("symbolName")` — find where something is used

**Frontend / UI tasks** ("add footer to landing page", "update the nav"):
1. `repo_map()` — lists all files including page.tsx and layout.tsx — start here
2. `explain_file("apps/web/src/app/page.tsx")` — inspect a specific page
3. `search_code()` only for hooks and logic, not for finding which file to edit

**Session start / architecture questions:**
1. `repo_map()` — always call this first for a full picture of the codebase

Do NOT use Bash find/grep or Read to explore the codebase until sema returns
no results. sema is faster and uses far fewer tokens.
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

Remove sema from the project's Claude Code config:

```bash
cd your-project
sema init --uninstall
```

Or manually delete the `mcpServers.sema` entry from `.claude/settings.json` in your project directory.

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
sema index .               Index the current directory
sema index . --reset       Delete existing index and re-index from scratch
sema index ./path          Index a specific path
sema init                  Register sema as MCP server with Claude Code
sema init --uninstall      Remove sema from Claude Code config
sema init --dry-run        Show what init would do without making changes
sema search "query"        Run a semantic search (test without Claude)
sema search "query" --top-k 10   Return more results
sema status                Show index stats (chunks, files, model, last updated)
sema serve --project .     Start MCP server (called automatically by Claude Code)
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
- [ ] File watcher: `sema watch` re-indexes changed files in the background
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
