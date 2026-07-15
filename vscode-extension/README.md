# sema for VS Code

A UI for [sema](https://github.com/masihmoloodian/sema) — semantic code search,
reuse checks, and a **codebase-aware chat panel**, right inside the editor. Chat
with your code through eight providers:

- **Claude Code (local)**, **Codex (local)**, and **opencode (local)** — reuse the
  CLIs you already have installed and logged in; no API key needed. They read (and, in
  Agent mode, edit) your repository directly.
- **Claude (Anthropic API)**, **OpenAI**, **DeepSeek**, **OpenRouter**, and
  **Together AI** — bring your own API key; sema retrieves the most relevant code and injects it as context
  (RAG). In **Agent** mode, the OpenAI-compatible providers (OpenAI, DeepSeek,
  OpenRouter, Together AI) also get file/command tools and carry out changes directly — creating
  and editing files, running commands — not just describing them (function-calling
  models only). OpenRouter is a single gateway to models from many providers
  (Anthropic, OpenAI, Google, Meta, …) and reports the exact per-call cost; the
  others estimate cost from public list prices.

The extension shells out to the `sema` CLI (`--json` mode) for search and
retrieval, and runs every provider from the extension host — so API keys never
reach webview/page context.

## Setup — step by step

sema has two parts: the **sema CLI** (the local indexer the extension drives) and
the **VS Code extension**. Set them up in order — it takes a few minutes.

### 1. Install the sema CLI

Always required — it's what makes chat codebase-aware (indexing, search, reuse).

```bash
pip install sema-mcp        # or: uv tool install sema-mcp
sema --version              # verify
```

<details>
<summary>Install from source instead (for development)</summary>

```bash
git clone https://github.com/masihmoloodian/sema.git
cd sema
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
echo "export PATH=\"$(pwd)/.venv/bin:\$PATH\"" >> ~/.zshrc && source ~/.zshrc
sema --version
```
</details>

Requires Python 3.11+. Full guide: [docs/installation.md](../docs/installation.md).

### 2. Install the extension

Install the packaged `sema-codebase-chat-<version>.vsix`:

- **VS Code UI** — Extensions view (`⇧⌘X` / `Ctrl+Shift+X`) → the `⋯` menu →
  **Install from VSIX…** → select the file.
- **Command line** — `code --install-extension sema-codebase-chat-<version>.vsix`
  (needs the `code` command; if it's missing, run *Shell Command: Install 'code'
  command in PATH* first).

Reload when prompted — a **sema** icon appears in the Activity Bar. Don't have a
`.vsix`? Build one — see [Build from source](#build-from-source).

### 3. Open your project and build the index

1. **Open your project folder** (`File → Open Folder…`). sema indexes the open
   folder; repo-reading providers use the enclosing git root.
2. **If VS Code can't find `sema`** — common when the app is launched from the
   Dock/Finder, which don't inherit your shell `PATH` — point it at the binary: run
   `which sema` and set `sema.binaryPath` to that absolute path (see
   [Configuration](#configuration)).
3. **Build the index** — open the **sema** view → **Manage** → **Re-index** (or run
   `sema index .`). The chat panel also offers to build it the first time you turn
   the **index** toggle on.

### 4. Choose a chat provider

You need **one** provider, and you can switch any time (even mid-conversation).
Pick whichever path fits — **path A needs no extra install**:

**A · Bring an API key** — the simplest path, nothing else to install.
Choose **OpenRouter**, **OpenAI**, **DeepSeek**, **Together AI**, or **Claude (Anthropic API)** in
the panel → **Set key** → paste your key (stored in VS Code SecretStorage, never in
settings). OpenRouter is a single gateway to models from many providers.

**B · Reuse a local CLI** — no key management in sema. Claude Code / Codex use your
existing Claude/ChatGPT subscription; **opencode** is open-source and works with any
provider you sign into. Install the CLI(s) you'll use (macOS/Linux shown; for
Homebrew, npm, and Windows see [docs/claude-code.md](../docs/claude-code.md),
[docs/codex.md](../docs/codex.md), and [docs/opencode.md](../docs/opencode.md)):

```bash
curl -fsSL https://claude.ai/install.sh | bash         # Claude Code
curl -fsSL https://chatgpt.com/codex/install.sh | sh   # Codex
curl -fsSL https://opencode.ai/install | bash          # opencode
```

Then click **Log in** on that provider in the panel — it runs the CLI's own sign-in
(`claude` / `codex login` / `opencode auth login`). Install only what you'll use. If a
CLI isn't on VS Code's PATH, set `sema.chat.claudePath` / `sema.chat.codexPath` /
`sema.chat.opencodePath` to its absolute path.

> **opencode** also needs a model: it uses the default from your `opencode` config, or
> pick a `provider/model` slug via the **"+ custom id…"** entry (run `opencode models`
> to list yours).

> **Claude Code and Codex are optional.** The API-key providers (path A) are a
> complete alternative and require no CLI.

### 5. Start chatting

Open the **Chat** view, pick your provider, model, and a mode — **Ask** (read-only),
**Plan** (propose a plan), or **Agent** (make changes) — and type. Toggle **index**
on to feed sema's semantic context to the API providers, and **redact** on to strip
PII/secrets before anything is sent. Attach a screenshot, PDF, or file with **📎**
(or paste / drag one onto the composer).

<details>
<summary>Optional — redact person &amp; location names too</summary>

The **redact** toggle catches secrets and structured PII (emails, API keys, cards,
SSNs, phone numbers) out of the box. To also redact person and location **names**,
install the local model:

```bash
pip install 'sema-mcp[pii]'
python -m spacy download en_core_web_sm
```
</details>

## Features

- **Chat** — a Cursor-style panel with provider and model pickers, plus **Ask**
  (read-only) / **Plan** (propose a plan) / **Agent** (take actions) modes. Local CLI
  providers stream thinking and tool activity like their terminal apps do, and
  keep **per-session memory** across turns. A **Log in** button signs you into
  Claude Code / Codex from the panel (via each CLI's own browser flow) and shows
  your sign-in state; if a message fails because you're not signed in, a one-click
  **Log in** prompt appears. Each chat is one session; **New chat** starts a fresh
  one. Toggle **index** on to inject sema's semantic context (RAG) — useful for API
  providers, which can't read files themselves.
- **Reasoning effort** — shown only for the two providers whose CLI actually takes one,
  with each list carrying only levels verified to actually run. **Claude Code**
  (`--effort`): low / medium / high / extra high / **max**. **Codex**
  (`-c model_reasoning_effort=`): **none** / low / medium / high / extra high. The sets
  genuinely differ — Codex errors on Claude's `max`, and Claude ignores Codex's `none` —
  so the picker only ever offers what the selected CLI accepts, and `default` sends no
  flag at all. Every other provider hides the picker: effort is a CLI feature, not an API
  parameter.
- **Attachments** — send images, PDFs, and text files to any provider. Use **📎**,
  paste a screenshot straight into the composer, drag a file in, or right-click one
  in the Explorer → **sema: Attach file to chat**. Each provider gets the file in its
  native form: Anthropic and OpenAI as image/document content blocks, and the local
  CLIs as real files on disk (`codex -i`, `opencode -f`, and Claude Code's own Read
  tool). Text files are inlined into the prompt, so they work everywhere. If the model
  you picked can't read the type you attached, sema says so up front instead of
  quietly dropping it — and if you switch to a text-only model mid-chat, earlier
  attachments degrade to a placeholder rather than erroring the conversation.

  | Provider | Images | PDFs | Text |
  |---|:--:|:--:|:--:|
  | Claude Code | ✅ | ✅ | ✅ |
  | Anthropic, OpenAI | ✅ | ✅ | ✅ |
  | Codex | ✅ | — | ✅ |
  | opencode, OpenRouter, Together | ✅¹ | ✅¹ | ✅ |
  | DeepSeek | — | — | ✅ |

  ¹ Per model, since these are gateways to many models. Pick a vision model (e.g.
  Claude, Gemini, GPT) to send images; text-only models like DeepSeek or GLM accept
  text files only. **opencode's "Default" is treated as text-only** — it resolves to
  whatever that install is configured for (often a free, text-only model such as
  `big-pickle`) and opencode doesn't report the resolved id, so choose an explicit
  model from the dropdown to attach images.
- **PII redaction** — an opt-in **redact** toggle scrubs sensitive data from
  everything sema sends to the model: regex for secrets and structured PII (emails,
  API keys, tokens, credit cards, SSNs, phone numbers) instantly and offline, plus
  an optional local spaCy NER pass for person and location names (`pip install
  'sema-mcp[pii]'` then `python -m spacy download en_core_web_sm`). Each turn shows
  what was redacted. Covers the prompt, injected index context, and attached text
  files; the local CLI agents can still read raw files themselves. Images and PDFs
  can't be scrubbed, so with **redact** on, attaching one is refused rather than sent
  unscrubbed under a "redacted" banner.
- **Manage** — index status, chunk/file counts, model, last-updated time, index
  path, the sema binary in use, Claude Code / Codex registration, a file **watch**
  toggle, and the current chat session's **token usage and estimated cost**. Plus
  one-click actions: Re-index, Re-index (reset), register/unregister, watch, doctor.
- **Search** (`sema: Search code`) — semantic search; click a result to jump to
  the definition.
- **Reuse** (`sema: Check reuse`) — describe what you're about to build; sema tells
  you whether it already exists and lists candidates.
- **Status bar** — index freshness (chunks / files / age); warns when stale.

## Configuration

| Setting | Default | Description |
| --- | --- | --- |
| `sema.binaryPath` | `sema` | Path to the sema executable. If sema lives in a virtualenv, set this to e.g. `/path/to/sema/.venv/bin/sema`. |
| `sema.searchResultLimit` | `20` | How many results to request from `sema search`. |
| `sema.chat.maxTokens` | `8192` | Max tokens for a chat completion response (API providers). |
| `sema.chat.claudePath` | `claude` | Path to the Claude Code CLI. Set an absolute path if `claude` isn't on VS Code's PATH. |
| `sema.chat.codexPath` | `codex` | Path to the Codex CLI. Set an absolute path if `codex` isn't on VS Code's PATH. |

## Build from source

```
cd vscode-extension
npm install
npm run package      # → sema-codebase-chat-<version>.vsix (runs the esbuild bundle first)
```

Then install the generated `.vsix` as in [step 2](#2-install-the-extension).

### Develop

```
npm install
npm run bundle       # esbuild → out/extension.js (bundles the SDKs)
```

Press <kbd>F5</kbd> in VS Code (with this folder open) to launch an Extension
Development Host with the extension loaded. `npm run compile` typechecks;
`npm run watch` rebuilds on save.

## Architecture

```
VS Code panels  ──CLI --json──►  sema (search / get / reuse / status)  ──►  index
   │                                                                     (ChromaDB + SBERT)
   ├─ chat (local)  ──►  Claude Code / Codex CLI  ──stream──►  panel   (reads/edits repo)
   └─ chat (API)  ──context──►  Anthropic SDK · OpenAI SDK       ──stream──►  panel
                                (OpenAI · DeepSeek · OpenRouter · Together AI share the OpenAI SDK)
```

Providers run in the Node extension host, so API keys never reach webview/page context.
