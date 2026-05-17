# CLAUDE.md — sema

## What this project is
sema indexes codebases semantically and exposes them as an MCP server
for Claude Code. Users run `sema index . && sema init` once per project.
Claude then calls search_code() instead of reading files blindly.

## Architecture
sema/
  indexer/      tree-sitter parsing + SBERT embedding
  store/        ChromaDB wrapper + Chunk schema
  mcp/          MCP server + 5 tools
  cli.py        Click CLI: index, init, serve, search, status
  utils/        file_walker, gitignore, repo_map generator

## Key files to read first
- sema/store/schema.py      Chunk dataclass — the core data model
- sema/store/chroma.py      ChromaDB wrapper — how data is stored/queried
- sema/mcp/tools.py         All 5 MCP tools — the user-facing API
- sema/indexer/parser.py    Language dispatcher
- tests/fixtures/example-repo/  Test data

## Commands
```
uv sync --all-extras                          install everything
uv run pytest tests/ -v                       run tests
uv run sema index tests/fixtures/example-repo test indexing
uv run sema search "auth"                     test search
uv run ruff check sema/                       lint
```

## Critical design rules
1. search_code() MUST NEVER return body — signatures only
2. get_code() is the ONLY tool that returns full source
3. All tools must work offline — no internet, no external APIs
4. ChromaDB embedded mode only — no Docker, no server process
5. Support TS first, then Go, then Python — in that priority order

## Current status
Phase 1 complete. Phase 2 in progress.
All 5 MCP tools are implemented.
