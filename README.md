# Sema

<p align="center">
  <img src="logo.png" alt="Sema" width="480" />
</p>

> **Experimental** — sema is under active development. APIs and index formats may change between versions. See the [disclaimer](docs/faq.md#disclaimer).

**Stop wasting tokens — on navigating your codebase, and on rewriting code that already exists. Speed up Claude Code and OpenAI Codex on large codebases.**

Sema is a semantic code indexer and MCP server. It indexes your entire codebase locally — every function, class, and method — and gives your AI assistant a search API so it never reads files blindly again, plus a reuse guard so it stops reinventing helpers you already have.

Works with
<a href="https://github.com/anthropics/claude-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/claude-ai.svg" alt="Claude" height="16" style="vertical-align:middle;" /> **Claude Code CLI**</a>,
<a href="https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="VS Code" height="16" style="vertical-align:middle;" /> **Claude Code VS Code**</a>,
<a href="https://github.com/openai/codex"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/codex-color.svg" alt="Codex" height="16" style="vertical-align:middle;" /> **OpenAI Codex CLI**</a>,
and
<a href="https://marketplace.visualstudio.com/items?itemName=openai.chatgpt"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="Codex" height="16" style="vertical-align:middle;" /> **Codex VS Code**</a>.

## Features

- **🔍 Semantic search** — `search_code()` finds code by meaning and returns signatures only (~150 tokens), never whole files.
- **♻️ Reuse guard** — `check_reuse()` tells your assistant whether a function already exists *before* it writes a new one, so it reuses instead of reinventing. **98% reuse-vs-build accuracy** in a 50-example eval on real code.
- **🕸️ Impact analysis** — `impact_analysis()` maps the call graph in both directions, so the AI sees the blast radius before a refactor.
- **📁 Multi-project** — one `sema init --root <dir>` serves every indexed repo under a directory; no re-registration when you switch projects.
- **🔒 Local & offline** — embeddings run on your machine (SBERT, ~80MB). No API keys, no internet, no code leaves your laptop.

## Why sema

Every Claude Code and Codex session starts cold. On a large project, your AI assistant burns 10,000–25,000 tokens just *navigating* — running `find`, reading full files, building a mental model from scratch — before it can help with anything.

Sema gives it a search index instead. Instead of reading a dozen files to answer *"how does auth work?"*, the AI runs one `search_code()` and fetches only the exact function bodies it needs — typically **4–11× fewer tokens**. Index once. Your AI searches forever.

That's the *reading* half of the token bill. Sema goes after the *writing* half too: before your assistant adds a new helper, `check_reuse()` searches the index for an existing one and answers **reuse / review / safe-to-build** — so it extends what's already there instead of shipping a fourth function that does the same thing.

See the [benchmarks](docs/benchmarks.md) for measured token savings on real open-source repos.

## Quick start

```bash
# 1. Install (from source — not yet on PyPI)
git clone https://github.com/masihmoloodian/sema.git
cd sema
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
echo "export PATH=\"$(pwd)/.venv/bin:\$PATH\"" >> ~/.zshrc && source ~/.zshrc

# 2. Index your project and register with your AI assistant
cd your-project
sema index .
sema init --claude     # or: sema init --codex

# 3. Reload VS Code, then type /mcp to confirm sema is connected
```

Then add a `CLAUDE.md` (or `AGENTS.md` for Codex) so your assistant calls sema before reading files — see [Claude Code setup](docs/claude-code.md) or [OpenAI Codex setup](docs/codex.md).

Requires Python 3.11+. No Docker, no external APIs, no GPU — everything runs on your machine.

## How it works

`sema index .` uses tree-sitter to parse every function, class, and method, embeds each one locally with SBERT (`all-MiniLM-L6-v2`), and stores the vectors plus full source in an embedded ChromaDB. A local MCP server then exposes search tools to Claude/Codex over stdio. `search_code()` returns signatures only; `get_code()` returns full bodies on demand.

The same index powers the rest of the toolset: [`check_reuse()`](docs/mcp-tools.md#check_reuse--dont-rewrite-what-already-exists) (*does this already exist?*), [`impact_analysis()`](docs/mcp-tools.md#impact_analysis--call-graph) (call graph and blast radius), and [multi-project serving](docs/multi-project.md) — all fully offline.

See [Architecture](docs/architecture.md) for the full picture.

## Documentation

Full docs live in [`docs/`](docs/README.md):

| | |
|---|---|
| [Installation](docs/installation.md) | Requirements and install from source |
| [Claude Code setup](docs/claude-code.md) · [Codex setup](docs/codex.md) · [VS Code workspace](docs/vscode-workspace.md) | Register sema with your assistant |
| [Working with multiple projects](docs/multi-project.md) | Serve many repos from one registration |
| [CLI reference](docs/cli-reference.md) | Every `sema` command |
| [MCP tools](docs/mcp-tools.md) | The tools your AI assistant calls |
| [Supported languages](docs/languages.md) | AST-aware vs text-aware indexing |
| [Configuration](docs/configuration.md) | Config file, env vars, `.gitignore` |
| [Managing sema](docs/managing-sema.md) | Update, remove, and when to re-index |
| [Troubleshooting](docs/troubleshooting.md) | Fixes for common issues |
| [Benchmarks](docs/benchmarks.md) · [FAQ](docs/faq.md) · [Roadmap](docs/roadmap.md) | Background and details |
| [Contributing](docs/contributing.md) | Development setup and how to extend sema |

## Contributing

Contributions are welcome — sema is intentionally small and easy to extend. See [Contributing](docs/contributing.md) for development setup and how to add a new language.

## License

MIT License — free to use, modify, and distribute. See [LICENSE](LICENSE).

Copyright (c) 2026 Masih Moloodian

## Contact

**Masih Moloodian** · [masihmoloodian@gmail.com](mailto:masihmoloodian@gmail.com)

Issues and feature requests: [github.com/masihmoloodian/sema/issues](https://github.com/masihmoloodian/sema/issues)
