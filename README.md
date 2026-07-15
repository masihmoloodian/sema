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

<p align="center"><strong>One local index. Every AI, code-aware.</strong></p>

> **Experimental** — sema is under active development. APIs and index formats may change between versions. See the [disclaimer](https://github.com/masihmoloodian/sema/blob/main/docs/faq.md#disclaimer).

Sema builds a **single semantic index** of your codebase — every function, class, and method — and puts it to work two ways:

- **🧠 Code intelligence for Claude Code & Codex.** An MCP server hands your CLI assistant semantic search (`search_code`), a reuse guard (`check_reuse`), and impact analysis over stdio — so it stops reading files blindly and stops rewriting helpers you already have.
- **🖥️ A Cursor-style chat & agent in VS Code.** Not just an index — a full **chat _and_ agent** that reads, edits, and runs your repo. Use the **Claude Code**, **Codex**, and **opencode** you already run locally (no re-login), or your own **Anthropic / OpenAI / DeepSeek / OpenRouter / Together AI / AvalAI** keys — switching **provider and model mid-conversation**, all backed by the same local index. [Get it on the Marketplace →](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)

**No keys, no cloud, nothing leaves your machine.** The index runs fully offline — local SBERT embeddings, no API keys. The chat/agent talks to whichever model you point it at, with opt-in PII redaction before anything is sent.

Works with
<a href="https://github.com/anthropics/claude-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/claude-ai.svg" alt="Claude" height="16" style="vertical-align:middle;" /> **Claude Code CLI**</a>,
<a href="https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="VS Code" height="16" style="vertical-align:middle;" /> **Claude Code VS Code**</a>,
<a href="https://github.com/openai/codex"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@latest/icons/codex-color.svg" alt="Codex" height="16" style="vertical-align:middle;" /> **OpenAI Codex CLI**</a>,
and
<a href="https://marketplace.visualstudio.com/items?itemName=openai.chatgpt"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="Codex" height="16" style="vertical-align:middle;" /> **Codex VS Code**</a>.
Plus sema's own <a href="https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat"><img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/vscode.svg" alt="VS Code" height="16" style="vertical-align:middle;" /> **VS Code extension**</a>.

## By the numbers

| | |
|---|---|
| **4–11×** | fewer tokens per question |
| **98%** | reuse-vs-build accuracy ([50-example eval](https://github.com/masihmoloodian/sema/blob/main/docs/benchmarks.md)) |
| **~150** | tokens per search — signatures, not whole files |
| **0** | code that leaves your machine |

## Two ways to use it

One index. A CLI brain and a VS Code panel.

### 🧠 The index — for Claude Code & Codex (MCP)

Hands your CLI assistant semantic search, a reuse guard, and impact analysis over stdio — so it stops reading files blindly and stops rewriting helpers you already have.

- **🔍 `search_code()`** — finds code by meaning and returns signatures only (~150 tokens), never whole files.
- **♻️ `check_reuse()`** — tells your assistant whether a function already exists *before* it writes a new one. **98% reuse-vs-build accuracy** on real code.
- **🕸️ `impact_analysis()`** — maps the call graph in both directions, so the AI sees the blast radius before a refactor.
- **📁 Multi-project** — one `sema init --root <dir>` serves every indexed repo under a directory; no re-registration when you switch projects.
- **🔒 Local & offline** — embeddings run on your machine (SBERT, ~80MB). No API keys, no internet, no code leaves your laptop.

### 🖥️ The panel — a Cursor-style chat & agent

Not just an index — a full chat and agent that reads, edits, and runs your repo. Nine providers, one conversation, backed by the same local index.

- **💬 Nine engines, one conversation** — chat through **Claude Code**, **Codex**, and **opencode** running locally (reuse your login, no API key), or the **Anthropic**, **OpenAI**, **DeepSeek**, **OpenRouter**, **Together AI**, and **AvalAI** APIs with your own key — switching **provider and model between turns**. **AvalAI** is reachable from networks where the vendor APIs are blocked, so chat keeps working through an outage.
- **🤖 Ask · Plan · Agent** — **Ask** answers read-only, **Plan** investigates with read-only tools and proposes a step-by-step plan, **Agent** does the work: a real tool loop (`search_code`, `get_code`, `grep`, `glob`, `read_file`, `write_file`, surgical `edit_file`, `delete_file`, `run_command`). **Even API models act — not just the local CLIs.**
- **🔎 Powered by your index** — the agent searches your sema index directly; an index toggle can also inject retrieved code as RAG context.
- **🛡️ Opt-in PII redaction** — redact secrets and personal data before anything is sent to a remote model.
- **🛠️ Manage panel** — index status, one-click re-index / register / watch / doctor, live **token usage + estimated cost**, plus **Search** and **Reuse** from the command palette.

[Get the extension on the Marketplace →](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)

## Why sema

Every Claude Code and Codex session starts cold. On a large project, your AI assistant burns 10,000–25,000 tokens just *navigating* — running `find`, reading full files, building a mental model from scratch — before it can help with anything.

Sema gives it a search index instead. Instead of reading a dozen files to answer *"how does auth work?"*, the AI runs one `search_code()` and fetches only the exact function bodies it needs — typically **4–11× fewer tokens**. Index once. Your AI searches forever.

That's the *reading* half of the token bill. Sema goes after the *writing* half too: before your assistant adds a new helper, `check_reuse()` searches the index for an existing one and answers **reuse / review / safe-to-build** — so it extends what's already there instead of shipping a fourth function that does the same thing.

See the [benchmarks](https://github.com/masihmoloodian/sema/blob/main/docs/benchmarks.md) for measured token savings on real open-source repos.

## Quick start

**One line** — installs the `sema` command (bootstrapping [uv](https://docs.astral.sh/uv/) and a Python for it if needed), then offers to register with whichever AI CLIs you have:

```bash
curl -fsSL https://raw.githubusercontent.com/masihmoloodian/sema/main/install.sh | sh
```

Then, inside each project:

```bash
cd your-project
sema index .     # build the local semantic index
sema setup       # register with every detected CLI: Claude Code, Codex, opencode
```

Skip any client with an env var — e.g. install everything but leave Codex alone:

```bash
SEMA_SKIP_CODEX=1 curl -fsSL https://raw.githubusercontent.com/masihmoloodian/sema/main/install.sh | sh
```

`SEMA_SKIP_CLAUDE`, `SEMA_SKIP_CODEX`, `SEMA_SKIP_OPENCODE`, and `SEMA_NO_SETUP=1` (binary only) are all honoured, and `sema setup` takes the matching `--skip-*` flags.

<details>
<summary><strong>Prefer to install it yourself?</strong> (no <code>curl | sh</code>)</summary>

```bash
uv tool install sema-mcp     # or: pipx install sema-mcp / pip install sema-mcp
cd your-project
sema index .
sema setup                   # all detected CLIs at once
#   ...or one at a time:
sema init --claude           # or --codex
```

The installer script is [install.sh](https://github.com/masihmoloodian/sema/blob/main/install.sh) — read it before running if you like.
</details>

**Confirm:** reload your editor, type `/mcp`, and look for `✓ sema connected`. Stuck? Run `sema doctor`.

Requires **Python 3.11+** (uv installs one for you if you don't have it). No Docker, no external APIs, no GPU — everything runs on your machine. On PyPI the package is **`sema-mcp`** (the name `sema` was taken), but the command and import stay `sema`. Working on sema itself? [Install from source](https://github.com/masihmoloodian/sema/blob/main/docs/installation.md#install-from-source-for-development).

Then add a `CLAUDE.md` (or `AGENTS.md` for Codex) so your assistant calls sema before reading files — see [Claude Code setup](https://github.com/masihmoloodian/sema/blob/main/docs/claude-code.md) or [OpenAI Codex setup](https://github.com/masihmoloodian/sema/blob/main/docs/codex.md).

## How it works

Every message routes through the same local pipeline — retrieve context from the index, then dispatch to whichever engine you pick.

`sema index .` uses tree-sitter to parse every function, class, and method, embeds each one locally with SBERT (`all-MiniLM-L6-v2`), and stores the vectors plus full source in an embedded ChromaDB. A local MCP server then exposes search tools to Claude/Codex over stdio. `search_code()` returns signatures only; `get_code()` returns full bodies on demand.

The same index powers the rest of the toolset: [`check_reuse()`](https://github.com/masihmoloodian/sema/blob/main/docs/mcp-tools.md#check_reuse--dont-rewrite-what-already-exists) (*does this already exist?*), [`impact_analysis()`](https://github.com/masihmoloodian/sema/blob/main/docs/mcp-tools.md#impact_analysis--call-graph) (call graph and blast radius), and [multi-project serving](https://github.com/masihmoloodian/sema/blob/main/docs/multi-project.md) — all fully offline.

See [Architecture](https://github.com/masihmoloodian/sema/blob/main/docs/architecture.md) for the full picture.

## sema for VS Code

[![VS Marketplace Version](https://img.shields.io/visual-studio-marketplace/v/MasihMoloodian.sema-codebase-chat?label=VS%20Code%20Marketplace&color=1e88e5&logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)
[![Installs](https://img.shields.io/visual-studio-marketplace/i/MasihMoloodian.sema-codebase-chat?color=1e88e5)](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)

Prefer a UI? The **[sema VS Code extension](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)** is a **Cursor-style chat + agent** for your codebase, backed by the same local index. One conversation, nine engines, one index — switch provider mid-conversation.

- **💬 Nine providers, one conversation** — **Claude Code**, **Codex**, and **opencode** running locally (reuse your existing login, no API key), or the **Anthropic**, **OpenAI**, **DeepSeek**, **OpenRouter**, **Together AI**, and **AvalAI** APIs with your own key; switch provider and model between turns. **AvalAI** (`api.avalai.ir`) fronts Claude, GPT, Gemini, Grok, and open models from a network reachable when the vendor APIs are blocked — an escape hatch during an internet outage.
- **🤖 Ask · Plan · Agent** — **Ask** for read-only Q&A, **Plan** to investigate with read-only tools and propose a step-by-step plan, **Agent** to carry it out. In Agent mode the model gets a full toolset — `search_code`, `get_code`, `grep`, `glob`, `read_file`, `write_file`, surgical `edit_file`, `delete_file`, `run_command` — so **even API models (OpenRouter, OpenAI, DeepSeek, Together AI, AvalAI) read, edit files, and run commands**, not just the local CLIs. Paths stay inside the workspace; Plan mode refuses to write.
- **🔎 Index-aware** — the agent searches your sema index directly; an index toggle also injects retrieved code as RAG on demand.
- **🛡️ Opt-in PII redaction** — strip secrets and personal data before anything is sent to a remote model.
- **🧠 Reasoning-effort selector, streamed thinking + tool activity, per-session memory**, and the live **selected model id** — the model the API actually served, not what it claims to be.
- **🛠️ Manage panel** — index status, one-click re-index / register / watch / doctor, and live **token usage + estimated cost**. **⚡ Search** and **Reuse** from the command palette, with index freshness in the status bar.

**Install:** search **"sema"** in the Extensions view, run `code --install-extension MasihMoloodian.sema-codebase-chat`, or open the [Marketplace listing](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat). Prefer to build from source? See the [extension guide](https://github.com/masihmoloodian/sema/blob/main/vscode-extension/README.md#build-from-source).

## Documentation

Full docs live in [`docs/`](https://github.com/masihmoloodian/sema/blob/main/docs/README.md):

| | |
|---|---|
| [Installation](https://github.com/masihmoloodian/sema/blob/main/docs/installation.md) | Requirements, `pip install`, and install from source |
| [sema for VS Code](https://github.com/masihmoloodian/sema/blob/main/vscode-extension/README.md) | sema's own VS Code extension — chat panel, search, reuse, and index management |
| [Claude Code setup](https://github.com/masihmoloodian/sema/blob/main/docs/claude-code.md) · [Codex setup](https://github.com/masihmoloodian/sema/blob/main/docs/codex.md) · [opencode setup](https://github.com/masihmoloodian/sema/blob/main/docs/opencode.md) · [VS Code workspace](https://github.com/masihmoloodian/sema/blob/main/docs/vscode-workspace.md) | Register sema with your assistant |
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
