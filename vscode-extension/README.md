# sema for VS Code

**Love Cursor? You'll love sema.**

A Cursor-style chat and agent panel inside VS Code — except **you** pick the
provider and the model, and you can change your mind mid-conversation. Eight
engines, one thread, backed by a local semantic index of your codebase.

> **macOS and Linux.** The sema CLI installer is a POSIX shell script; the
> extension itself runs anywhere VS Code does, but the index needs the CLI.

## Setup

Five steps, a few minutes. Steps 1 and 3 are the index — skip them if you only
want the chat panel (see [Just the chat panel](#just-the-chat-panel)).

### 1 · Install the sema CLI

This is the indexer — what makes chat codebase-aware.

```bash
curl -fsSL https://raw.githubusercontent.com/masihmoloodian/sema/main/install.sh | sh
sema --version                                          # verify
```

Prefer to do it yourself? `uv tool install sema-mcp` (or `pipx install sema-mcp`).
Requires Python 3.11+ — the installer bootstraps one if you don't have it.
Details: [docs/installation.md](../docs/installation.md).

### 2 · Install the extension

Install it from the
**[VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=MasihMoloodian.sema-codebase-chat)** —
or search **"sema"** in the Extensions view (`⇧⌘X` / `Ctrl+Shift+X`), or run
`code --install-extension MasihMoloodian.sema-codebase-chat`. Reload when prompted —
a **sema** icon appears in the Activity Bar.

Want to build it yourself instead? See [Build from source](#build-from-source).

### 3 · Open your project and build the index

1. **Open your project folder** (`File → Open Folder…`). sema indexes the open
   folder; repo-reading providers use the enclosing git root.
2. **Build the index** — the **sema** view → **Manage** → **Re-index**, or run
   `sema index .` in a terminal.
3. **If VS Code can't find `sema`** — common when the app is launched from the
   Dock/Finder, which don't inherit your shell `PATH` — run `which sema` and set
   `sema.binaryPath` to that absolute path. See [Configuration](#configuration).

### 4 · Pick a provider

You need **one**, and you can switch any time — even mid-conversation.

**A · Bring an API key** — nothing else to install. Pick **OpenRouter**,
**OpenAI**, **DeepSeek**, **Together AI**, or **Claude (Anthropic API)** in the
panel → **Set key** → paste. Keys live in VS Code SecretStorage, never in
settings. OpenRouter is one gateway to models from many providers.

**B · Reuse a local CLI** — no key management at all; Claude Code and Codex use
your existing subscription, opencode works with any provider you sign into.

```bash
curl -fsSL https://claude.ai/install.sh | bash         # Claude Code
curl -fsSL https://chatgpt.com/codex/install.sh | sh   # Codex
curl -fsSL https://opencode.ai/install | bash          # opencode
```

Then click **Log in** on that provider in the panel — it runs the CLI's own
sign-in. Install only what you'll use. If a CLI isn't on VS Code's PATH, set
`sema.chat.claudePath` / `sema.chat.codexPath` / `sema.chat.opencodePath`.
Setup guides: [Claude Code](../docs/claude-code.md) · [Codex](../docs/codex.md) · [opencode](../docs/opencode.md).

> **opencode** also needs a model: it uses the default from your `opencode`
> config, or pick a `provider/model` slug via **"+ custom id…"** (run
> `opencode models` to list yours).

### 5 · Chat

Open the **Chat** view, pick a provider, model, and mode — **Ask** (read-only),
**Plan** (propose a plan), or **Agent** (make changes) — and type. Toggle
**index** on to feed semantic context to API providers, **redact** on to strip
PII before anything is sent. Attach a screenshot, PDF, or file with **📎**.

Then try switching provider mid-thread: plan it with Claude Code, hand the same
conversation to Codex to build, review the diff with a cheap OpenRouter model.
That's the part your CLI subscription won't do.

### Just the chat panel

Don't want the index? Do step 2, then step 4A, then step 5. The extension is a
complete multi-provider chat and agent without the CLI — you lose `search_code`,
so the agent greps and reads files like any other assistant.

## Features

- **One conversation, eight engines** — provider and model pickers stay live
  between turns. Local CLI providers stream thinking and tool activity like their
  terminal apps do, and keep **per-session memory**. A **Log in** button signs you
  into Claude Code / Codex from the panel; if a message fails because you're not
  signed in, a one-click **Log in** prompt appears. Each chat is one session;
  **New chat** starts fresh.
- **Ask · Plan · Agent** — **Ask** for read-only Q&A, **Plan** to investigate with
  read-only tools and propose a step-by-step plan, **Agent** to carry it out. In
  Agent mode the model gets a full toolset — `search_code`, `get_code`, `grep`,
  `glob`, `read_file`, `write_file`, surgical `edit_file`, `delete_file`,
  `run_command` — so **even API models read, edit, and run commands**, not just the
  local CLIs. Paths stay inside the workspace; Plan mode refuses to write.
- **Index-aware** — the agent searches your sema index directly, finding the right
  function by meaning instead of grepping its way there. The **index** toggle also
  injects retrieved code as RAG context — useful for API providers, which can't
  read files themselves.
- **Reasoning effort** — shown only for the two providers whose CLI takes one, with
  each list carrying only levels verified to run. **Claude Code** (`--effort`):
  low / medium / high / extra high / **max**. **Codex**
  (`-c model_reasoning_effort=`): **none** / low / medium / high / extra high. The
  sets genuinely differ — Codex errors on Claude's `max`, Claude ignores Codex's
  `none` — so the picker only offers what the selected CLI accepts, and `default`
  sends no flag. Every other provider hides it: effort is a CLI feature, not an API
  parameter.
- **Attachments** — images, PDFs, and text files to any provider, each in its native
  form: Anthropic and OpenAI as content blocks, the local CLIs as real files on disk
  (`codex -i`, `opencode -f`, Claude Code's Read tool). Text is inlined, so it works
  everywhere. If your model can't read the type you attached, sema says so up front
  instead of quietly dropping it; switch to a text-only model mid-chat and earlier
  attachments degrade to a placeholder rather than erroring the thread.

  | Provider | Images | PDFs | Text |
  |---|:--:|:--:|:--:|
  | Claude Code | ✅ | ✅ | ✅ |
  | Anthropic, OpenAI | ✅ | ✅ | ✅ |
  | Codex | ✅ | — | ✅ |
  | opencode, OpenRouter, Together | ✅¹ | ✅¹ | ✅ |
  | DeepSeek | — | — | ✅ |

  ¹ Per model, since these are gateways. Pick a vision model (Claude, Gemini, GPT)
  to send images; text-only models like DeepSeek or GLM take text files only.
  **opencode's "Default" is treated as text-only** — it resolves to whatever that
  install is configured for (often a free, text-only model) and opencode doesn't
  report the resolved id, so choose an explicit model to attach images.
- **PII redaction** — the opt-in **redact** toggle scrubs what sema sends: regex for
  secrets and structured PII (emails, API keys, tokens, cards, SSNs, phones)
  instantly and offline, plus an optional local spaCy NER pass for person and
  location names (`pip install 'sema-mcp[pii]'` then
  `python -m spacy download en_core_web_sm`). Each turn shows what was redacted.
  Covers the prompt, injected index context, and attached text files; local CLI
  agents still read raw files themselves. Images and PDFs can't be scrubbed, so with
  **redact** on, attaching one is refused rather than sent unscrubbed.
- **Manage** — index status, chunk/file counts, model, last-updated time, index
  path, the sema binary in use, CLI registration, a file **watch** toggle, and the
  session's **token usage and estimated cost**. One-click: Re-index, Re-index
  (reset), register/unregister, watch, doctor.
- **Search** (`sema: Search code`) — semantic search; click a result to jump to the
  definition.
- **Reuse** (`sema: Check reuse`) — describe what you're about to build; sema says
  whether it already exists and lists candidates.
- **Status bar** — index freshness (chunks / files / age); warns when stale.

## Configuration

| Setting | Default | Description |
| --- | --- | --- |
| `sema.binaryPath` | `sema` | Path to the sema executable. If sema lives in a virtualenv, set e.g. `/path/to/sema/.venv/bin/sema`. |
| `sema.searchResultLimit` | `20` | How many results to request from `sema search`. |
| `sema.chat.maxTokens` | `8192` | Max tokens for a chat completion response (API providers). |
| `sema.chat.claudePath` | `claude` | Path to the Claude Code CLI. Absolute path if `claude` isn't on VS Code's PATH. |
| `sema.chat.codexPath` | `codex` | Path to the Codex CLI. Absolute path if `codex` isn't on VS Code's PATH. |

## Using the index without the extension

The index stands alone. `sema setup` registers an MCP server with Claude Code,
Codex, and opencode, so your CLI assistant gets `search_code`, `check_reuse`, and
`impact_analysis` with no editor involved. See the
[root README](https://github.com/masihmoloodian/sema).

## Build from source

```
cd vscode-extension
npm install
npm run package      # → sema-codebase-chat-<version>.vsix (bundles with esbuild first)
```

Then install the generated `.vsix` as in [step 2](#2--install-the-extension).

### Develop

```
npm install
npm run bundle       # esbuild → out/extension.js
```

Press <kbd>F5</kbd> in VS Code (with this folder open) to launch an Extension
Development Host. `npm run compile` typechecks; `npm run watch` rebuilds on save.

## Architecture

```
VS Code panels  ──CLI --json──►  sema (search / get / reuse / status)  ──►  index
   │                                                                     (ChromaDB + SBERT)
   ├─ chat (local)  ──►  Claude Code / Codex / opencode CLI  ──stream──►  panel  (reads/edits repo)
   └─ chat (API)  ──context+tools──►  Anthropic SDK · OpenAI SDK  ──stream──►  panel
                                      (OpenAI · DeepSeek · OpenRouter · Together AI share the OpenAI SDK)
```

Providers run in the Node extension host, so API keys never reach webview/page context.

## Troubleshooting

Common fixes: [docs/troubleshooting.md](../docs/troubleshooting.md). Run
`sema doctor` for a health check.
