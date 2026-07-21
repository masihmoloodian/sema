# CLAUDE.md — sema

## What this project is
sema indexes codebases semantically and exposes them as an MCP server
for Claude Code. Users run `sema index . && sema init` once per project.
Claude then calls search_code() instead of reading files blindly.

## Architecture
sema/
  indexer/      tree-sitter parsing + SBERT embedding
  store/        ChromaDB wrapper + Chunk schema
  mcp/          MCP server + 8 tools + multi-project registry
  agent/        terminal coding agent: providers, tools, permissions, loop
  tui/          Textual terminal app (`sema chat`) + slash commands
  reuse.py      reuse guard — "does this already exist?" verdict engine
  cli.py        Click CLI: index, init, serve, search, reuse, status, chat
  utils/        file_walker, gitignore, repo_map generator

## Key files to read first
- sema/store/schema.py      Chunk dataclass — the core data model
- sema/store/chroma.py      ChromaDB wrapper — how data is stored/queried
- sema/mcp/tools.py         All 8 MCP tools — the user-facing API
- sema/mcp/registry.py      Multi-project registry (serve many projects at once)
- sema/reuse.py             Reuse guard engine (check_reuse tool + `sema reuse`)
- sema/indexer/parser.py    Language dispatcher
- sema/agent/tools.py       Agent tool surface + path/staleness guardrails
- sema/agent/loop.py        Provider-agnostic agent turn loop
- sema/tui/commands.py      Slash commands (testable without a terminal)
- tests/fixtures/example-repo/  Test data

## Commands
```
uv sync --all-extras                          install everything
uv run pytest tests/ -v                       run tests
uv run sema index tests/fixtures/example-repo test indexing
uv run sema search "auth"                     test search
uv run sema chat                              terminal app (needs --extra chat)
uv run sema chat --print "question"           headless one-shot
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
8 MCP tools implemented: search_code, check_reuse, get_code, repo_map,
find_usages, explain_file, impact_analysis, list_projects.
Multi-project serving (`sema init --root`) and the reuse guard are live.
The terminal app (`sema chat`) is live: 3 modes, 10 providers, 14 tools, 34
slash commands, sharing session storage with the VS Code extension.

## Terminal app rules
1. Tool calls run in a thread (`asyncio.to_thread`) — embedding a query blocks
2. Path confinement and edit-staleness checks are structural, never advisory
3. Slash commands call `sema/agent/ops.py`, never re-implement CLI logic
4. Session JSON stays camelCase — the VS Code extension owns that schema
5. Anthropic requests: adaptive thinking only, no sampling params
