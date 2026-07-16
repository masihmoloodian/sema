# Sema

<p align="center">
  <img src="https://raw.githubusercontent.com/masihmoloodian/sema/main/logo.png" alt="Sema" width="480" />
</p>

<p align="center">
  <a href="https://pypi.org/project/sema-mcp/"><img src="https://img.shields.io/pypi/v/sema-mcp?color=1e88e5&label=PyPI" alt="PyPI version" /></a>
  <a href="https://pypi.org/project/sema-mcp/"><img src="https://img.shields.io/pypi/pyversions/sema-mcp" alt="Python versions" /></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat"><img src="https://img.shields.io/visual-studio-marketplace/v/MasihMoloodian.sema-codebase-chat?color=1e88e5&label=VS%20Code%20Marketplace&logo=visualstudiocode" alt="VS Code Marketplace" /></a>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="Platform: macOS | Linux" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT" />
</p>

<p align="center"><strong>Love Cursor? You'll love Sema.</strong></p>

Sema is a **VS Code extension**, an **agentic assistant**, and a **semantic indexer** — a Cursor-style chat that already knows your codebase, except you pick the provider and the model, and you can switch **mid-conversation**.

- **The indexer** gives every provider real semantic search. Through the extension, all providers use it; and Claude Code and Codex can use it directly in their official apps or CLIs — over MCP, no sema extension needed ([see docs](docs/claude-code.md)). Either way they stop burning tokens hunting for files.
- **The extension** gives you one chat panel with **eight engines** — Claude Code, Codex, Open Code, Anthropic, OpenAI, DeepSeek, OpenRouter, Together AI.

Sema uses **your existing Claude Code and Codex subscription** — no extra API key, no re-login. Or bring your own key for any API provider.

Use both, or either one alone.

> **Experimental** · **macOS and Linux** · Requires **Python 3.11+** · Nothing leaves your machine. [Disclaimer](docs/faq.md#disclaimer)

## Install

**1. Install the CLI** (bootstraps uv + Python if needed):

```bash
curl -fsSL https://raw.githubusercontent.com/masihmoloodian/sema/main/install.sh | sh
```

**2. Install the extension** from the **[VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)** (or search **"sema"** in the Extensions view). Want to build it yourself? [See the docs](vscode-extension/README.md#build-from-source).

**3. Index and register.** Open your project, then use the extension's **Manage** panel to build the index and register your AI CLIs in a click — or do it from the CLI:

```bash
cd your-project
sema index .     # build the local semantic index
sema setup       # register with every detected CLI: Claude Code, Codex, opencode
```

Want **just the extension** or **just the index**? Both work standalone. Plus manual installs, requirements, and troubleshooting — **for more details see the [Installation guide](docs/installation.md)**.

📦 **[sema-mcp on PyPI](https://pypi.org/project/sema-mcp/)** · 🧩 **[Extension on the VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)**

## Why

Cursor is great — it's also a subscription and a walled garden. Claude Code and Codex are great too, but they lock you to one model per session, and they start every session cold: **burn tokens just navigating** before they answer anything.

Sema indexes your code once, locally. Your assistant searches it instead — typically **4–11× fewer tokens** per question. And `check_reuse()` tells it whether a helper already exists *before* it writes a fourth one.

| | |
|---|---|
| **4–11×** | fewer tokens per question |
| **98%** | reuse-vs-build accuracy ([50-example eval](docs/benchmarks.md)) |
| **~150** | tokens per search — signatures, not whole files |
| **0** | code that leaves your machine |

[Why sema exists, in full →](docs/why-sema.md) · [Benchmarks →](docs/benchmarks.md)

## Documentation

**Start here:** [Installation](docs/installation.md) · [sema for VS Code](vscode-extension/README.md) · [CLI reference](docs/cli-reference.md)

| | |
|---|---|
| [Installation](docs/installation.md) | Platforms, requirements, install paths, from source |
| [sema for VS Code](vscode-extension/README.md) | The extension — chat, agent, search, reuse |
| [Claude Code](docs/claude-code.md) · [Codex](docs/codex.md) · [opencode](docs/opencode.md) · [VS Code workspace](docs/vscode-workspace.md) | Register sema with your assistant |
| [Multiple projects](docs/multi-project.md) | Serve many repos from one registration |
| [CLI reference](docs/cli-reference.md) | Every `sema` command |
| [MCP tools](docs/mcp-tools.md) | The tools your AI assistant calls |
| [Supported languages](docs/languages.md) | AST-aware vs text-aware indexing |
| [Configuration](docs/configuration.md) | Config file, env vars, `.gitignore` |
| [Managing sema](docs/managing-sema.md) | Update, remove, and when to re-index |
| [Troubleshooting](docs/troubleshooting.md) | Fixes for common issues |
| [Why sema](docs/why-sema.md) · [Benchmarks](docs/benchmarks.md) · [FAQ](docs/faq.md) · [Roadmap](docs/roadmap.md) | Background and details |
| [Architecture](docs/architecture.md) · [Contributing](docs/contributing.md) | How it works, how to extend it |

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Masih Moloodian.

**Masih Moloodian** · [masihmoloodian@gmail.com](mailto:masihmoloodian@gmail.com) · [Issues](https://github.com/masihmoloodian/sema/issues)
