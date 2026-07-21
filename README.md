# Sema

<p align="center">
  <img src="https://raw.githubusercontent.com/get-sema/sema/main/logo.png" alt="Sema" width="480" />
</p>

<p align="center">
  <a href="https://pypi.org/project/sema-mcp/"><img src="https://img.shields.io/pypi/v/sema-mcp?color=1e88e5&label=PyPI" alt="PyPI version" /></a>
  <a href="https://pypi.org/project/sema-mcp/"><img src="https://img.shields.io/pypi/pyversions/sema-mcp" alt="Python versions" /></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat"><img src="https://img.shields.io/visual-studio-marketplace/v/MasihMoloodian.sema-codebase-chat?color=1e88e5&label=VS%20Code%20Marketplace&logo=visualstudiocode" alt="VS Code Marketplace" /></a>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="Platform: macOS | Linux" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT" />
</p>

<p align="center"><strong>One AI coding agent for your whole codebase.</strong></p>

Sema is an **agentic code engine**. It indexes your repository once, locally, then
puts that index in front of whichever model you point it at — so every provider
starts out already knowing your code, and you can **switch provider and model
mid-conversation** without losing the thread.

It runs in three places, and they share one index and one session:

| Surface | What it is |
|---|---|
| **VS Code extension** | A Cursor-style chat panel with ten engines, agent permissions, and an opt-in redaction toggle |
| **`sema chat`** | The same agent as a terminal app — modes, tools, slash commands, resumable sessions |
| **MCP server** | Register sema with Claude Code, Codex, opencode, Grok Build, or Cursor and they get semantic search in their own CLI — no extension needed |

Use one, or all three.

> **Experimental** · **macOS and Linux** · Requires **Python 3.11+** · Nothing leaves your machine. [Disclaimer](docs/faq.md#disclaimer)

---

## Quick start

**1. Install the CLI** (bootstraps uv + Python if needed):

```bash
curl -fsSL https://raw.githubusercontent.com/get-sema/sema/main/install.sh | sh
```

**2. Index your project and register your AI CLIs:**

```bash
cd your-project
sema index .     # build the local semantic index
sema setup       # register with every detected client: Claude Code, Codex, opencode, Grok Build, Cursor
```

**3. Pick a surface:**

```bash
sema chat        # the terminal agent (needs the chat extra — see below)
```

…or install the **[VS Code extension](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)**
(search **"sema"** in the Extensions view). Its **Manage** panel does everything
the CLI does — index, register, update — in a click.

Want *just* the extension, or *just* the index? Both work standalone.
**[Installation guide →](docs/installation.md)**

📦 **[sema-mcp on PyPI](https://pypi.org/project/sema-mcp/)** · 🧩 **[Extension on the VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)**

---

## Ten providers, no lock-in

Five local CLIs reuse the subscription you already pay for — **no API key, no
re-login**. Five API providers need one key each.

| Provider | Kind | Needs |
|---|---|---|
| **Claude Code** | Local CLI | your existing `claude` login |
| **Codex** | Local CLI | your existing `codex` login |
| **opencode** | Local CLI | your existing `opencode` login |
| **Grok Build** | Local CLI | your existing `grok` login |
| **Cursor Agent** | Local CLI | your existing `cursor-agent` login |
| **Claude (Anthropic)** | API | `ANTHROPIC_API_KEY` |
| **OpenAI** | API | `OPENAI_API_KEY` |
| **DeepSeek** | API | `DEEPSEEK_API_KEY` |
| **OpenRouter** | API | `OPENROUTER_API_KEY` |
| **Together AI** | API | `TOGETHER_API_KEY` |

Provider, model, and reasoning effort are all switchable **inside a running
conversation** — the history, the index, and your redaction and guard settings
carry over. Start on the subscription you pay for, finish on a cheap (or free)
model.

---

## Why

Cursor is great — it's also a subscription and a walled garden. Claude Code and
Codex are great too, but they lock you to one model per session, and they start
every session cold: **burning tokens just navigating** before they answer
anything.

Sema indexes your code once, locally. Your assistant searches that index instead
— typically **4–11× fewer tokens** per question. And `check_reuse()` tells it
whether a helper already exists *before* it writes a fourth one.

| | |
|---|---|
| **4–11×** | fewer tokens per question |
| **98%** | reuse-vs-build accuracy ([50-example eval](docs/benchmarks.md)) |
| **~150** | tokens per search — signatures, not whole files |
| **0** | code that leaves your machine |

The index is built from tree-sitter ASTs for TypeScript, JavaScript, Python and
Go (text-aware for everything else), embedded locally with `all-MiniLM-L6-v2`,
and searched as a **hybrid of semantic similarity and BM25**. No API keys, no
network. [Supported languages →](docs/languages.md)

[Why sema exists, in full →](docs/why-sema.md) · [Benchmarks →](docs/benchmarks.md)

---

## `sema chat` — the terminal agent

A full coding agent in your terminal, on the same index:

```bash
uv sync --extra chat      # or: pip install 'sema-mcp[chat]'
cd your-project
sema chat
```

```bash
sema chat --print "where is auth handled?"   # one-shot, for scripts
sema chat --resume <session_id>              # pick a conversation back up
sema chat --mode plan                        # read-only + a plan artifact
```

- **Three modes** — `ask` (no tools, just conversation), `plan` (read-only
  tools), `agent` (reads, writes, and runs commands). `Shift+Tab` cycles them.
- **34 slash commands** — `/provider`, `/model`, `/effort`, `/search`, `/reuse`,
  `/impact`, `/index`, `/devops`, `/cost`, `/sessions`, and more. Press `/`.
- **Per-tool permissions** — read-only tools run; anything that mutates asks
  first. `--yes` auto-approves for unattended runs.
- **Shared sessions** — a conversation started in VS Code resumes in the
  terminal, and back again.

**[Terminal app guide →](docs/terminal-app.md)**

---

## What the AI gets

Eight code-intelligence tools, over MCP or in-chat:

| Tool | Purpose |
|---|---|
| `search_code` | Hybrid semantic + BM25 search over the index |
| `check_reuse` | Does this helper already exist? reuse / review / safe-to-build |
| `get_code` | Full source of a function, class, or method by name |
| `repo_map` | Compressed architecture map — files and their exported symbols |
| `find_usages` | Every reference to a symbol |
| `impact_analysis` | Blast radius of changing a symbol, via the call graph |
| `explain_file` | A file's purpose, exports, and key dependencies — no source |
| `list_projects` | Which indexed projects this server serves |

**[MCP tools reference →](docs/mcp-tools.md)**

---

## Safety

**Redaction.** Opt-in scrubbing of secrets and PII *before* a request leaves your
machine — private keys, JWTs, API keys (OpenAI, Anthropic, Stripe, GitHub,
Slack, AWS, Google), emails, Luhn-validated card numbers, SSNs, and phone
numbers, by regex; names and locations via local spaCy NER when the `pii` extra
is installed. Toggle it in the extension's chat panel; preview any string with
`sema redact` (reads STDIN only, so nothing lands in your shell history).

```bash
pip install 'sema-mcp[pii]' && python -m spacy download en_core_web_sm
echo "text" | sema redact
```

**Agent permissions.** Claude Code and Codex pause protected actions for an
inline **Allow / Reject** decision in the chat, or run in a clearly marked
bypass mode inside a trusted sandbox. Each provider keeps its own choice.

**DevOps guard.** Point an AI at `kubectl`, Terraform, AWS CLI, or Helm and sema
sits in between. Every command is classified and secret-redacted *before* it
runs, never after:

- **Safe** — read-only commands run immediately (and are still audited).
- **Needs approval** — mutations are held with an approval id until *you*
  release them. Approvals are per-command and one-shot; the AI can never approve
  its own held actions.
- **Prohibited** — `terraform destroy`, force-deletes, deleting a
  cluster-critical namespace, root-account deletion: refused outright, no
  exceptions.

Enforcement is not documentation — `sema devops install-shims` puts wrappers on
your `PATH` that **fail closed** if sema isn't reachable. Everything lands in an
append-only, redacted audit log.

```bash
sema devops install-shims      # put the guard on PATH
sema devops pending            # what's waiting for you
sema devops approve <id>       # release one held command
sema devops log                # the audit trail
```

**[DevOps guard →](docs/devops-guard.md)**

---

## Keeping things current

```bash
sema update --check              # show installed Claude/Codex/opencode/Grok versions
sema update                      # run every installed agent's official updater
sema update --provider codex     # update one agent only (repeatable)
sema self-update                 # update sema itself
```

The extension exposes the same workflow under **Manage → Update agent CLIs…**.
Reload VS Code afterwards so its model picker reflects the installed CLI.

---

## Documentation

**Start here:** [Installation](docs/installation.md) · [sema for VS Code](vscode-extension/README.md) · [Terminal app](docs/terminal-app.md) · [CLI reference](docs/cli-reference.md)

| | |
|---|---|
| [Installation](docs/installation.md) | Platforms, requirements, install paths, from source |
| [sema for VS Code](vscode-extension/README.md) | The extension — chat, agent, search, reuse |
| [Terminal app](docs/terminal-app.md) | `sema chat` — modes, slash commands, sessions |
| [Claude Code](docs/claude-code.md) · [Codex](docs/codex.md) · [opencode](docs/opencode.md) · [Grok Build](docs/grok.md) · [Cursor](docs/cursor.md) · [VS Code workspace](docs/vscode-workspace.md) | Register sema with your assistant |
| [Multiple projects](docs/multi-project.md) | Serve many repos from one registration |
| [CLI reference](docs/cli-reference.md) | Every `sema` command |
| [MCP tools](docs/mcp-tools.md) | The tools your AI assistant calls |
| [DevOps guard](docs/devops-guard.md) | Analyze-first gate for `kubectl`/Terraform/AWS CLI/Helm |
| [Supported languages](docs/languages.md) | AST-aware vs text-aware indexing |
| [Configuration](docs/configuration.md) | Config file, env vars, `.gitignore` |
| [Managing sema](docs/managing-sema.md) | Update, remove, and when to re-index |
| [Troubleshooting](docs/troubleshooting.md) | Fixes for common issues |
| [Why sema](docs/why-sema.md) · [Benchmarks](docs/benchmarks.md) · [FAQ](docs/faq.md) · [Roadmap](docs/roadmap.md) | Background and details |
| [Architecture](docs/architecture.md) · [Contributing](docs/contributing.md) | How it works, how to extend it |

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Masih Moloodian.

**Masih Moloodian** · [masihmoloodian@gmail.com](mailto:masihmoloodian@gmail.com) · [Issues](https://github.com/get-sema/sema/issues)
