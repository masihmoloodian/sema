# sema for VS Code

A UI for [sema](https://github.com/masihmoloodian/sema) — semantic code search,
reuse checks, and a **codebase-aware chat panel**, right inside the editor. Chat
with your code through four providers:

- **Claude Code (local)** and **Codex (local)** — reuse the CLIs you already have
  installed and logged in; no API key needed. They read (and, in Agent mode, edit)
  your repository directly.
- **Claude (Anthropic API)** and **OpenAI** — bring your own API key; sema
  retrieves the most relevant code and injects it as context (RAG).

The extension shells out to the `sema` CLI (`--json` mode) for search and
retrieval, and runs every provider from the extension host — so API keys never
reach webview/page context.

## Installation

sema has two parts: the **sema CLI** (the local indexer this extension drives) and
the **VS Code extension** itself. Install the CLI first.

### 1. Install the sema CLI

sema isn't on PyPI yet, so install it from source — this is the exact setup the
extension expects. **uv (recommended):**

```bash
git clone https://github.com/masihmoloodian/sema.git
cd sema
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"

# add sema to your PATH so it's callable from any project:
echo "export PATH=\"$(pwd)/.venv/bin:\$PATH\"" >> ~/.zshrc && source ~/.zshrc

sema --version   # verify — run from any directory
```

<details>
<summary>Using pip instead of uv</summary>

```bash
git clone https://github.com/masihmoloodian/sema.git
cd sema
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux  (Windows: .venv\Scripts\activate)
pip install -e ".[dev]"
echo "export PATH=\"$(pwd)/.venv/bin:\$PATH\"" >> ~/.zshrc && source ~/.zshrc
sema --version
```
</details>

Requires Python 3.11+. Full guide: [docs/installation.md](../docs/installation.md).

> **Recommended for the extension:** VS Code launched from the Dock/Finder often
> doesn't inherit your shell `PATH`, so it may not find `sema` even after the export
> above. The most reliable setup — and the one used locally — is to leave the binary
> in the venv and point the extension straight at it: set `sema.binaryPath` to the
> absolute path, e.g. `/path/to/sema/.venv/bin/sema` (see [step 4](#4-first-run-setup)).

### 2. Chat prerequisites

For the chat panel you need **at least one** of:
- the **Claude Code** and/or **Codex** CLI installed and logged in (recommended — no
  key management, and they can edit files in Agent mode), or
- an **Anthropic** and/or **OpenAI** API key (added from the panel — stored in VS
  Code SecretStorage, never in settings).

### 3. Install the extension (`.vsix`)

You install a packaged `sema-vscode-<version>.vsix` file (e.g.
`sema-vscode-0.1.0.vsix`). Two ways:

**From the VS Code UI**
1. Open the **Extensions** view (`⇧⌘X` / `Ctrl+Shift+X`).
2. Click the `⋯` menu at the top of the view → **Install from VSIX…**
3. Select the `.vsix` file.

**From the command line** (needs the `code` command — in VS Code run
*Shell Command: Install 'code' command in PATH* first if it's missing):
```
code --install-extension sema-vscode-0.1.0.vsix
```

Reload VS Code when prompted. A **sema** icon appears in the Activity Bar.

> Don't have a `.vsix`? Build one from source — see
> [Build from source](#build-from-source) below.

### 4. First-run setup

1. **Open your project folder** (`File → Open Folder…`). sema works against the
   open folder; chat providers that read the repo use the enclosing git root.
2. **Point the extension at the sema binary.** Set `sema.binaryPath` to the absolute
   `.venv/bin/sema` path from step 1 (e.g. `/path/to/sema/.venv/bin/sema`) — the most
   reliable option — or leave it as `sema` if the CLI is on VS Code's PATH. See
   [Configuration](#configuration).
3. **Build the index.** Open the **sema** view → **Manage** → **Re-index**
   (or run `sema index .` in a terminal). The chat panel can also build it
   automatically the first time you turn the **index** toggle on.
4. Open the **Chat** view, pick a provider/model, and start typing.

## Features

- **Chat** — a Cursor-style panel with provider, model, and reasoning-**effort**
  pickers, plus **Ask** (read-only) / **Agent** (can edit files) modes. Local CLI
  providers stream thinking and tool activity like their terminal apps do, and
  keep **per-session memory** across turns. Each chat is one session; **New chat**
  starts a fresh one. Toggle **index** on to inject sema's semantic context (RAG) —
  useful for API providers, which can't read files themselves.
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
npm run package      # → sema-vscode-<version>.vsix (runs the esbuild bundle first)
```

Then install the generated `.vsix` as in [step 3](#3-install-the-extension-vsix).

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
   └─ chat (API)  ──context──►  Anthropic / OpenAI SDK          ──stream──►  panel
```

Providers run in the Node extension host, so API keys never reach webview/page context.
