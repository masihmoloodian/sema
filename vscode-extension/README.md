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

Open the **Chat** view, pick a provider, model, and mode — **Ask** (simple chat),
**Plan** (read-only investigation that saves a Markdown plan), or **Agent**
(make and verify changes) — and type. Plan artifacts live under
`.sema/plans/`; switching to Agent in the same chat gives the agent the latest
plan automatically. Plan and Agent always use the sema index when it is
available. Toggle **index** on to add semantic context in Ask mode too, and
toggle **redact** on to strip PII before anything is sent. Attach a screenshot,
PDF, or file with **📎**.

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
  terminal apps do, and keep **per-session memory**. Changing provider, model, or
  mode starts a compatible native CLI session while the extension preserves and
  replays the full visible conversation, so one chat can safely mix models. A
  **Log in** button signs you
  into Claude Code / Codex from the panel; if a message fails because you're not
  signed in, a one-click **Log in** prompt appears. Each chat is one session;
  **New chat** starts fresh.
- **Ask · Plan · Agent** — **Ask** is ordinary chat with no workspace tools,
  **Plan** investigates with read-only tools and writes only its durable Markdown
  plan, and **Agent** reads that plan and carries it out. Fresh workspaces start in
  **Agent** with index and redact off and **Require approval** on. In
  Agent mode the model gets a full toolset — `search_code`, `get_code`, `grep`,
  `glob`, `read_file`, `write_file`, surgical `edit_file`, `delete_file`,
  `run_command` — so **even API models read, edit, and run commands**, not just the
  local CLIs. Paths stay inside the workspace. If no sema index exists, Agent
  falls back to workspace search/read tools instead of failing.
- **Index-aware** — the agent searches your sema index directly, finding the right
  function by meaning instead of grepping its way there. The **index** toggle also
  injects retrieved code as RAG context — useful for API providers, which can't
  read files themselves. The toggle is authoritative in every mode: when it is off,
  chat does not refresh, search, or inject the Sema index.
- **Reasoning effort** — shown only for the two local CLIs that expose it, and
  filtered for the selected model. **Claude Code** (`--effort`) offers default,
  low, medium, high, extra high, and max. **Codex**
  (`-c model_reasoning_effort=`) offers default through extra high on GPT-5.4,
  GPT-5.4 Mini, and GPT-5.5; adds max on GPT-5.6 Luna; and adds max plus ultra on
  GPT-5.6 Sol and Terra. `default` sends no override. API providers and opencode
  hide the control because their CLIs do not expose this same effort contract.
- **Agent permissions** — in **Agent** mode with Claude Code or Codex, open the
  gear menu and choose **Require approval** (the default) or **Bypass permissions**.
  When bypass is active, the composer bar shows a persistent orange **Full access**
  indicator.
  Require approval pauses protected file changes, commands, or access escalation
  and shows an inline card in the Sema chat with **Allow** and **Reject**. Claude uses the
  official Agent SDK permission callback; Codex uses the same app-server approval
  protocol as rich Codex clients. Bypass maps to each CLI's explicit dangerous
  full-access option and should only be used in a trusted, externally isolated
  workspace. The choice is stored separately for Claude Code and Codex, and changing
  it starts a new native agent thread so a permissive session is never resumed under
  a stricter label.
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
  (reset), register/unregister, watch, doctor, and **Update agent CLIs**.
- **Search** (`sema: Search code`) — semantic search; click a result to jump to the
  definition.
- **Reuse** (`sema: Check reuse`) — describe what you're about to build; sema says
  whether it already exists and lists candidates.
- **Status bar** — index freshness (chunks / files / age); warns when stale.
  It refreshes after saves and indexing, when VS Code regains focus, and every 30
  seconds, so an external `sema index .` run cannot leave a cached stale label.

## Keep agent CLIs current

New model ids and effort levels often require a newer local CLI. Open the chat
gear and choose **Update agent CLIs…** (or **Manage sema…**) to update all installed
agents or choose one.
The action opens an integrated terminal so you can see the official updater's
output and any authentication prompt. The equivalent commands are:

```bash
sema update --check
sema update
sema update --provider claude
sema update --provider codex
sema update --provider opencode
```

Under the hood these invoke `claude update`, `codex update`, and
`opencode upgrade`; existing authentication and configuration remain owned by
those CLIs. Restart active agent sessions and reload VS Code when they finish.

## Configuration

| Setting | Default | Description |
| --- | --- | --- |
| `sema.binaryPath` | `sema` | Path to the sema executable. If sema lives in a virtualenv, set e.g. `/path/to/sema/.venv/bin/sema`. |
| `sema.searchResultLimit` | `20` | How many results to request from `sema search`. |
| `sema.chat.maxTokens` | `8192` | Max tokens for a chat completion response (API providers). |
| `sema.chat.claudePath` | `claude` | Path to the Claude Code CLI. Absolute path if `claude` isn't on VS Code's PATH. |
| `sema.chat.codexPath` | `codex` | Path to the Codex CLI. Absolute path if `codex` isn't on VS Code's PATH. |
| `sema.chat.opencodePath` | `opencode` | Path to the opencode CLI. Absolute path if `opencode` isn't on VS Code's PATH. |

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
