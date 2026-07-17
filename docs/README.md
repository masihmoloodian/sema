# Sema documentation

Full documentation for sema. Start with the root [README](../README.md) for the
quick start, or [Why sema](why-sema.md) for the pitch.

> sema runs on **macOS and Linux**. Requires **Python 3.11+**.

## Getting started
- [Installation](installation.md) — platforms, requirements, install paths, from source
- [Claude Code setup](claude-code.md) — register sema, add `CLAUDE.md`
- [OpenAI Codex setup](codex.md) — register sema, add `AGENTS.md`
- [opencode setup](opencode.md) — install, use as a chat provider, add sema via MCP
- [Grok Build setup](grok.md) — register sema, add `AGENTS.md`
- [sema for VS Code](../vscode-extension/README.md) — the chat + agent extension
- [Working with multiple projects](multi-project.md) — serve many repos from one registration
- [VS Code workspace setup](vscode-workspace.md) — multi-folder workspaces

## Reference
- [CLI reference](cli-reference.md) — every `sema` command
- [MCP tools](mcp-tools.md) — the tools your AI assistant calls
- [Supported languages](languages.md) — AST-aware vs text-aware
- [Configuration](configuration.md) — `.sema/config.toml`, env vars, `.gitignore`
- [Architecture](architecture.md) — how it works and project structure

## Operating sema
- [Managing sema](managing-sema.md) — update, remove, and when to re-index
- [Troubleshooting](troubleshooting.md) — fixes for common issues

## Background
- [Why sema](why-sema.md) — the problem it solves and why it's built this way
- [Benchmarks](benchmarks.md) — before/after token comparisons on real repos
- [FAQ](faq.md) — common questions, limitations, and disclaimer
- [Roadmap](roadmap.md) — planned features
- [Contributing](contributing.md) — development setup and how to extend sema
