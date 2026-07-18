# CLI reference

sema runs on macOS and Linux. See [installation](installation.md) to get the
`sema` command, and [why sema](why-sema.md) for what it's for.

```
sema index .                                  Index the current directory (skips unchanged files)
sema index . --reset                          Delete existing index and re-index everything from scratch
sema index . --workspace my.code-workspace    Index only the folders listed in a VS Code workspace file
sema add src/app.ts                           Add or re-index a single file (--json for scripts)
sema remove src/app.ts                        Remove a file from the index — does not delete it on disk
sema list                                     List indexed files and the symbols under each (--json for scripts)
sema watch .                                  Watch for file changes and re-index automatically
sema watch . --workspace my.code-workspace    Watch all workspace folders simultaneously
sema setup                                    Detect installed AI clients and register sema with each in one shot
sema setup --skip-codex                       Register all detected clients except Codex (also --skip-claude / --skip-opencode / --skip-grok / --skip-cursor)
sema setup --uninstall                        Remove sema from every detected AI client
sema update --check                           Show installed Claude Code, Codex, opencode, and Grok Build versions
sema update                                   Run every installed coding agent's official self-updater
sema update --provider codex                  Update one agent only (claude / codex / opencode / grok; repeatable)
sema init --claude                            Register sema as MCP server with Claude Code (via claude mcp add -s user)
sema init --claude --root ~/code              Multi-project: serve every indexed project under ~/code (repeatable)
sema init --claude --uninstall                Remove sema from Claude Code and kill running processes
sema init --codex                             Register sema as MCP server with OpenAI Codex (.codex/config.toml in project)
sema init --codex --root ~/code               Multi-project registration for Codex
sema init --codex --uninstall                 Remove sema from Codex config
sema init --grok                              Register sema as MCP server with Grok Build (.grok/config.toml in project)
sema init --grok --root ~/code                Multi-project registration for Grok Build
sema init --grok --uninstall                  Remove sema from Grok Build config
sema init --cursor                            Register sema as MCP server with Cursor (.cursor/mcp.json in project)
sema init --cursor --root ~/code              Multi-project registration for Cursor
sema init --cursor --uninstall                Remove sema from Cursor config
sema search "query"                           Run a hybrid semantic+BM25 search (test without Claude)
sema search "query" --top-k 10                Return more results
sema search "query" --all-types               Include docs/config sections in results
sema get symbolName                           Print the full source of a function/class/method by name
sema reuse "what you're about to build"       Check if it already exists: reuse / review / safe-to-build verdict
sema status                                   Show index stats and which project the MCP server is serving
sema status --verbose                         Full details: index path, language breakdown, binary, registered command
sema doctor                                   Diagnose installation and registration issues
echo "text" | sema redact                     Redact names/locations from STDIN via spaCy NER (powers the extension's redact toggle)
sema serve --project .                        Start MCP server for one project (called automatically by Claude Code or Codex)
sema serve --root ~/code                       Start MCP server for every indexed project under ~/code (repeatable)
```

## `sema setup` vs `sema init`

`sema setup` is the one-command way to register sema: it detects which of
**Claude Code**, **Codex**, **opencode**, **Grok Build**, and **Cursor** are
installed and wires sema into each. It's idempotent and safe to re-run. Skip a
client with `--skip-claude`, `--skip-codex`, `--skip-opencode`, `--skip-grok`,
or `--skip-cursor`.

`sema init` registers a single client: `--claude` (the default), `--codex`,
`--grok`, or `--cursor`. It has no `--opencode` flag — use `sema setup` to reach
opencode.

Both accept `--root <dir>` (repeatable) to serve every indexed project beneath a
directory at once. See [multi-project mode](multi-project.md).

## Updating coding-agent CLIs

`sema update` keeps the local agent executables current without replacing their
authentication or configuration. It invokes each project's self-updater:
`claude update`, `codex update`, `opencode upgrade`, and `grok update`. Missing agents are
skipped; use `--provider` one or more times to target a subset. After updating,
restart active agent sessions and reload the VS Code extension so its model and
effort selectors reflect the installed versions.
