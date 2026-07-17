# Roadmap

Phase 1 is complete and Phase 2 is in progress. Already shipped and live: all 8
MCP tools, multi-project serving (`sema init --root`), the [reuse guard](../sema/reuse.py)
(`check_reuse` / `sema reuse`), incremental indexing, the file watcher, and the
[VS Code extension](../vscode-extension/README.md) (chat + agent panel across 9
providers). This roadmap tracks what's next.

## v0.2 — Tool improvements
- [x] `find_usages` backed by grep for exact reference matching
- [x] Call graph: `impact_analysis(symbol, depth)` — callers + callees, BFS multi-level, qualified names, builtin filtering, inverted index cache
- [x] `explain_file` includes import graph (project vs package imports, split by relative/absolute)
- [x] Better error messages when index is stale (empty results, low confidence, symbol not found)

## v0.3 — Incremental indexing
- [x] File watcher: `sema watch` re-indexes changed files automatically
- [x] Workspace support: `sema index --workspace` and `sema watch --workspace` index only listed folders with correct base paths
- [x] Incremental indexing: SHA-256 hash store skips unchanged files; `sema index .` on an already-indexed project is ~20× faster
- [ ] Git hook: `sema init --watch` installs a post-commit hook

## v0.4 — More AST-aware parsers
- [ ] Rust (`.rs`) — tree-sitter-rust
- [ ] Java / Kotlin (`.java`, `.kt`) — tree-sitter-java
- [ ] Ruby (`.rb`) — tree-sitter-ruby
- [ ] C# (`.cs`) — tree-sitter-c-sharp
- [ ] C/C++ (`.c`, `.cpp`, `.h`) — tree-sitter-c
- All of these already produce text-level chunks today; these upgrades add symbol granularity

## v0.5 — Multi-project & monorepo
- [x] Single `sema serve` handles multiple project roots (`sema init --root <dir>`, auto-discovery, per-tool `project` argument)
- [x] Workspace-level index for monorepos (`--workspace` flag)
- [ ] Cross-project symbol search

## v1.0 — Public release
- [x] Publish to PyPI (`sema-mcp`) with a `curl | sh` one-line installer
- [x] Auto-detect and configure Grok Build (`sema init --grok`, `.grok/config.toml`, chat provider)
- [ ] Homebrew formula
- [ ] Auto-detect and configure Cursor, Copilot, Windsurf
- [ ] Token savings report after each index
- [ ] CI/CD: auto-publish on git tag
