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

## Project structure

```
sema/
├── pyproject.toml                  # package definition, deps, entry point
├── README.md
├── CLAUDE.md                       # instructions for Claude when working on sema itself
├── LICENSE
├── logo.png
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
