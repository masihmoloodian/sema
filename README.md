# Sema

<p align="center">
  <img src="https://raw.githubusercontent.com/masihmoloodian/sema/main/logo.png" alt="Sema" width="480" />
</p>

<p align="center">
  <a href="https://pypi.org/project/sema-mcp/"><img src="https://img.shields.io/pypi/v/sema-mcp?color=1e88e5&label=PyPI" alt="PyPI version" /></a>
  <a href="https://pypi.org/project/sema-mcp/"><img src="https://img.shields.io/pypi/pyversions/sema-mcp" alt="Python versions" /></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat"><img src="https://img.shields.io/visual-studio-marketplace/v/MasihMoloodian.sema-codebase-chat?color=1e88e5&label=VS%20Code%20Marketplace&logo=visualstudiocode" alt="VS Code Marketplace" /></a>
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT" />
</p>

> **Experimental** — sema is under active development. APIs and index formats may change between versions. See the [disclaimer](https://github.com/masihmoloodian/sema/blob/main/docs/faq.md#disclaimer).

**Semantic search over your codebase, a reuse guard that stops your AI reinventing code you already have — and a Cursor-style chat + agent that can actually change it. All local.**

Sema builds one local semantic index of your codebase — every function, class, and method — and puts it to work two ways:

- **🧠 Code intelligence for Claude Code & Codex.** An MCP server hands your CLI assistant semantic search (`search_code`), a reuse guard (`check_reuse`), and impact analysis, so it stops reading files blindly and stops rewriting helpers that already exist.
- **🖥️ A Cursor-style AI panel in VS Code.** Not just an index — a full **chat _and_ agent** that reads, edits, and runs commands in your repo. Use the **Claude Code** and **Codex** you already run locally (no re-login), or your own **Anthropic / OpenAI / DeepSeek / OpenRouter / Together AI** keys — switching **provider and model mid-conversation**, with the same index as context. [Get it on the Marketplace →](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)

The index runs fully offline — local SBERT embeddings, no API keys, no code leaves your machine. The chat/agent talks to whichever model you point it at.

Works with
<a href="https://github.com/anthropics/claude-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/claude-ai.svg" alt="Claude" height="16" style="vertical-align:middle;" /> **Claude Code CLI**</a>,
<a href="https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="VS Code" height="16" style="vertical-align:middle;" /> **Claude Code VS Code**</a>,
<a href="https://github.com/openai/codex"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/codex-color.svg" alt="Codex" height="16" style="vertical-align:middle;" /> **OpenAI Codex CLI**</a>,
and
<a href="https://marketplace.visualstudio.com/items?itemName=openai.chatgpt"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="Codex" height="16" style="vertical-align:middle;" /> **Codex VS Code**</a>.
Plus sema's own <a href="https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="VS Code" height="16" style="vertical-align:middle;" /> **VS Code extension**</a> — a Cursor-style **chat + agent** panel that reads, edits, and runs your codebase through the Claude Code and Codex you already run locally, or your own API keys.

## Features

### The index — code intelligence for Claude Code & Codex (MCP)

- **🔍 Semantic search** — `search_code()` finds code by meaning and returns signatures only (~150 tokens), never whole files.
- **♻️ Reuse guard** — `check_reuse()` tells your assistant whether a function already exists *before* it writes a new one. **98% reuse-vs-build accuracy** in a 50-example eval on real code.
- **🕸️ Impact analysis** — `impact_analysis()` maps the call graph in both directions, so the AI sees the blast radius before a refactor.
- **📁 Multi-project** — one `sema init --root <dir>` serves every indexed repo under a directory; no re-registration when you switch projects.
- **🔒 Local & offline** — embeddings run on your machine (SBERT, ~80MB). No API keys, no internet, no code leaves your laptop.

### The VS Code panel — a Cursor-style chat & agent

- **💬 Seven providers, one conversation** — chat through **Claude Code**, **Codex**, **Anthropic**, **OpenAI**, **DeepSeek**, **OpenRouter**, and **Together AI**, switching **provider and model between turns**.
- **🤖 Ask · Plan · Agent** — **Ask** answers read-only, **Plan** investigates with read-only tools and proposes a step-by-step plan, **Agent** does the work: a real tool loop — `search_code`, `get_code`, `grep`, `glob`, `read_file`, `write_file`, surgical `edit_file`, `delete_file`, `run_command` — so it reads, edits files, and runs commands. **Even API models (OpenRouter, OpenAI, DeepSeek, Together AI) act — not just the local CLIs.**
- **🔎 Powered by your index** — the agent searches your sema index directly (`search_code` / `get_code`); an index toggle can also inject retrieved code as RAG context.
- **🛠️ Manage panel** — index status, one-click re-index / register / watch / doctor, live **token usage + estimated cost**, plus **Search** and **Reuse** from the command palette.

[Get the extension on the Marketplace →](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)

## Why sema

Every Claude Code and Codex session starts cold. On a large project, your AI assistant burns 10,000–25,000 tokens just *navigating* — running `find`, reading full files, building a mental model from scratch — before it can help with anything.

Sema gives it a search index instead. Instead of reading a dozen files to answer *"how does auth work?"*, the AI runs one `search_code()` and fetches only the exact function bodies it needs — typically **4–11× fewer tokens**. Index once. Your AI searches forever.

That's the *reading* half of the token bill. Sema goes after the *writing* half too: before your assistant adds a new helper, `check_reuse()` searches the index for an existing one and answers **reuse / review / safe-to-build** — so it extends what's already there instead of shipping a fourth function that does the same thing.

See the [benchmarks](https://github.com/masihmoloodian/sema/blob/main/docs/benchmarks.md) for measured token savings on real open-source repos.

## Quick start

```bash
# 1. Install — provides the `sema` command
pip install sema-mcp        # or: uv tool install sema-mcp

# 2. Index your project and register with your AI assistant
cd your-project
sema index .
sema init --claude     # or: sema init --codex

# 3. Reload VS Code, then type /mcp to confirm sema is connected
```

Requires **Python 3.11+**. On PyPI the package is **`sema-mcp`** (the name `sema` was taken), but the command and import stay `sema`. Working on sema itself? [Install from source](https://github.com/masihmoloodian/sema/blob/main/docs/installation.md#install-from-source-for-development).

Then add a `CLAUDE.md` (or `AGENTS.md` for Codex) so your assistant calls sema before reading files — see [Claude Code setup](https://github.com/masihmoloodian/sema/blob/main/docs/claude-code.md) or [OpenAI Codex setup](https://github.com/masihmoloodian/sema/blob/main/docs/codex.md).

Requires Python 3.11+. No Docker, no external APIs, no GPU — everything runs on your machine.

## How it works

`sema index .` uses tree-sitter to parse every function, class, and method, embeds each one locally with SBERT (`all-MiniLM-L6-v2`), and stores the vectors plus full source in an embedded ChromaDB. A local MCP server then exposes search tools to Claude/Codex over stdio. `search_code()` returns signatures only; `get_code()` returns full bodies on demand.

The same index powers the rest of the toolset: [`check_reuse()`](https://github.com/masihmoloodian/sema/blob/main/docs/mcp-tools.md#check_reuse--dont-rewrite-what-already-exists) (*does this already exist?*), [`impact_analysis()`](https://github.com/masihmoloodian/sema/blob/main/docs/mcp-tools.md#impact_analysis--call-graph) (call graph and blast radius), and [multi-project serving](https://github.com/masihmoloodian/sema/blob/main/docs/multi-project.md) — all fully offline.

See [Architecture](https://github.com/masihmoloodian/sema/blob/main/docs/architecture.md) for the full picture.

## sema for VS Code

[![VS Marketplace Version](https://img.shields.io/visual-studio-marketplace/v/MasihMoloodian.sema-codebase-chat?label=VS%20Code%20Marketplace&color=1e88e5&logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)
[![Installs](https://img.shields.io/visual-studio-marketplace/i/MasihMoloodian.sema-codebase-chat?color=1e88e5)](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)

Prefer a UI? The **[sema VS Code extension](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)** is a **Cursor-style chat + agent** for your codebase, backed by the same local index. Chat through the **Claude Code** and **Codex** you already have installed (or your own API keys), switch **provider and model mid-session**, and let it act:

- **💬 Seven providers, one conversation** — **Claude Code** and **Codex** running locally (reuse your existing login, no API key), or the **Anthropic**, **OpenAI**, **DeepSeek**, **OpenRouter**, and **Together AI** APIs with your own key; switch provider and model between turns.
- **🤖 Ask · Plan · Agent** — **Ask** for read-only Q&A, **Plan** to investigate with read-only tools and propose a step-by-step plan, **Agent** to carry it out. In Agent mode the model gets a full toolset — `search_code`, `get_code`, `grep`, `glob`, `read_file`, `write_file`, surgical `edit_file`, `delete_file`, `run_command` — so **even API models (OpenRouter, OpenAI, DeepSeek, Together AI) read, edit files, and run commands**, not just the local CLIs. Paths stay inside the workspace; Plan mode refuses to write.
- **🔎 Index-aware** — the agent searches your sema index directly; an index toggle also injects retrieved code as RAG on demand.
- **🧠 Reasoning-effort selector, streamed thinking + tool activity, per-session memory**, and the live **selected model id** — the model the API actually served, not what it claims to be.
- **🛠️ Manage panel** — index status, one-click re-index / register / watch / doctor, and live **token usage + estimated cost**. **⚡ Search** and **Reuse** from the command palette, with index freshness in the status bar.

**Install:** search **"sema"** in the Extensions view, run `code --install-extension MasihMoloodian.sema-codebase-chat`, or open the [Marketplace listing](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat). Prefer to build from source? See the [extension guide](https://github.com/masihmoloodian/sema/blob/main/vscode-extension/README.md#build-from-source).

## Documentation

Full docs live in [`docs/`](https://github.com/masihmoloodian/sema/blob/main/docs/README.md):

| | |
|---|---|
| [Installation](https://github.com/masihmoloodian/sema/blob/main/docs/installation.md) | Requirements, `pip install`, and install from source |
| [sema for VS Code](https://github.com/masihmoloodian/sema/blob/main/vscode-extension/README.md) | sema's own VS Code extension — chat panel, search, reuse, and index management |
| [Claude Code setup](https://github.com/masihmoloodian/sema/blob/main/docs/claude-code.md) · [Codex setup](https://github.com/masihmoloodian/sema/blob/main/docs/codex.md) · [VS Code workspace](https://github.com/masihmoloodian/sema/blob/main/docs/vscode-workspace.md) | Register sema with your assistant |
| [Working with multiple projects](https://github.com/masihmoloodian/sema/blob/main/docs/multi-project.md) | Serve many repos from one registration |
| [CLI reference](https://github.com/masihmoloodian/sema/blob/main/docs/cli-reference.md) | Every `sema` command |
| [MCP tools](https://github.com/masihmoloodian/sema/blob/main/docs/mcp-tools.md) | The tools your AI assistant calls |
| [Supported languages](https://github.com/masihmoloodian/sema/blob/main/docs/languages.md) | AST-aware vs text-aware indexing |
| [Configuration](https://github.com/masihmoloodian/sema/blob/main/docs/configuration.md) | Config file, env vars, `.gitignore` |
| [Managing sema](https://github.com/masihmoloodian/sema/blob/main/docs/managing-sema.md) | Update, remove, and when to re-index |
| [Troubleshooting](https://github.com/masihmoloodian/sema/blob/main/docs/troubleshooting.md) | Fixes for common issues |
| [Benchmarks](https://github.com/masihmoloodian/sema/blob/main/docs/benchmarks.md) · [FAQ](https://github.com/masihmoloodian/sema/blob/main/docs/faq.md) · [Roadmap](https://github.com/masihmoloodian/sema/blob/main/docs/roadmap.md) | Background and details |
| [Contributing](https://github.com/masihmoloodian/sema/blob/main/docs/contributing.md) | Development setup and how to extend sema |

## Contributing

Contributions are welcome — sema is intentionally small and easy to extend. See [Contributing](https://github.com/masihmoloodian/sema/blob/main/docs/contributing.md) for development setup and how to add a new language.

## License

MIT License — free to use, modify, and distribute. See [LICENSE](https://github.com/masihmoloodian/sema/blob/main/LICENSE).

Copyright (c) 2026 Masih Moloodian

## Contact

**Masih Moloodian** · [masihmoloodian@gmail.com](mailto:masihmoloodian@gmail.com)

Issues and feature requests: [github.com/masihmoloodian/sema/issues](https://github.com/masihmoloodian/sema/issues)
