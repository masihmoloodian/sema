# CLI reference

```
sema index .                                  Index the current directory (skips unchanged files)
sema index . --reset                          Delete existing index and re-index everything from scratch
sema index . --workspace my.code-workspace    Index only the folders listed in a VS Code workspace file
sema watch .                                  Watch for file changes and re-index automatically
sema watch . --workspace my.code-workspace    Watch all workspace folders simultaneously
sema init --claude                            Register sema as MCP server with Claude Code (via claude mcp add -s user)
sema init --claude --root ~/code              Multi-project: serve every indexed project under ~/code (repeatable)
sema init --claude --uninstall               Remove sema from Claude Code and kill running processes
sema init --codex                             Register sema as MCP server with OpenAI Codex (.codex/config.toml in project)
sema init --codex --root ~/code               Multi-project registration for Codex
sema init --codex --uninstall                Remove sema from Codex config
sema search "query"                           Run a hybrid semantic+BM25 search (test without Claude)
sema search "query" --top-k 10               Return more results
sema search "query" --all-types               Include docs/config sections in results
sema status                                   Show index stats and which project the MCP server is serving
sema status --verbose                         Full details: index path, language breakdown, binary, registered command
sema doctor                                   Diagnose installation and registration issues
sema serve --project .                        Start MCP server for one project (called automatically by Claude Code or Codex)
sema serve --root ~/code                       Start MCP server for every indexed project under ~/code (repeatable)
```
