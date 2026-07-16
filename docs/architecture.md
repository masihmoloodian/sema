# Architecture

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
              Claude / Codex ◄──► search_code / get_code / repo_map / ...
```

Every indexed unit is a **Chunk** — a function, class, method, or section of a config/doc file — with its full source stored alongside its embedding vector. `search_code()` returns signatures only. `get_code()` returns the full body on demand.

Everything runs locally and offline: embeddings come from a local SBERT model (`all-MiniLM-L6-v2`, ~80MB, cached in `~/.cache/sema/models/`) and vectors live in ChromaDB's embedded mode — no Docker, no server process, no external API. See [why-sema.md](why-sema.md) for the reasoning behind these choices.

## Project structure

```
sema/
├── pyproject.toml                  # package definition (sema-mcp), deps, entry point
├── README.md
├── CLAUDE.md                       # instructions for Claude when working on sema itself
├── install.sh                      # one-line installer (POSIX sh; macOS + Linux)
├── LICENSE
├── logo.png
│
├── sema/
│   ├── cli.py                      # Click CLI: index, setup, init, serve, search,
│   │                               #   reuse, watch, status, doctor, redact, …
│   ├── reuse.py                    # reuse guard — "does this already exist?" engine
│   ├── redact.py                   # optional PII redaction (spaCy NER, `sema redact`)
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
│   │   ├── bm25.py                 # BM25 keyword index (hybrid search)
│   │   └── hashes.py               # SHA-256 hash store for incremental indexing
│   │
│   ├── mcp/
│   │   ├── server.py               # MCP stdio server entry point
│   │   ├── registry.py             # multi-project registry (serve many projects)
│   │   └── tools.py                # all 8 MCP tool implementations
│   │
│   └── utils/
│       ├── file_walker.py          # walks project, respects .gitignore
│       ├── gitignore.py            # .gitignore pattern matching
│       ├── grep.py                 # literal/regex fallback search
│       ├── watcher.py              # `sema watch` — re-index on file save
│       └── repo_map.py             # compressed repo map generator
│
├── vscode-extension/               # VS Code chat + agent panel (see below)
│
└── tests/
    ├── conftest.py
    ├── fixtures/example-repo/      # TS + Python + Go fixture for tests
    ├── test_parser.py
    ├── test_store.py
    ├── test_chunker.py
    └── test_tools.py
```

The 8 MCP tools live in `sema/mcp/tools.py`: `search_code`, `check_reuse`,
`get_code`, `repo_map`, `find_usages`, `explain_file`, `impact_analysis`, and
`list_projects`. See [mcp-tools.md](mcp-tools.md) for the full reference.

## VS Code extension

`vscode-extension/` ships a Cursor-style chat and agent panel that works with
several model providers. It has no direct access to the index — it is a thin
client that **shells out to the `sema` CLI** and reads its `--json` output
(`sema search --json`, `sema reuse --json`, `sema redact --json`, and so on).
That keeps a single source of truth: the same indexer and store back both the
MCP server and the editor UI. See [../vscode-extension/README.md](../vscode-extension/README.md).
