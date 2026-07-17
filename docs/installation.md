# Installation

sema runs on **macOS and Linux**. It needs **Python 3.11+** — the one-line
installer bootstraps one for you if you don't have it.

## Requirements

- macOS or Linux
- Python 3.11 or higher (installer can provide it via [uv](https://docs.astral.sh/uv/))
- ~80MB disk for the embedding model (downloaded once, cached globally)
- One AI assistant to point sema at:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) or [Claude Code VS Code extension](https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code)
  - [OpenAI Codex CLI](https://github.com/openai/codex) or [Codex VS Code extension](https://marketplace.visualstudio.com/items?itemName=openai.chatgpt)
  - [opencode](opencode.md)
  - or sema's own [VS Code extension](../vscode-extension/README.md)
- No Docker, no external APIs, no GPU — everything runs on your machine

## Three ways to install

Pick the one that fits — each part works on its own.

| Path | You get | Steps |
|---|---|---|
| **Full sema** | The index *and* the chat panel | CLI installer → `sema index` / `sema setup` → extension |
| **Just the extension** | The multi-provider chat panel only | Install the extension; the index is optional |
| **Just the index** | Semantic search for Claude Code / Codex, no editor extension | CLI installer → `sema index` / `sema setup` |

### Just the extension

Want the multi-provider chat panel and nothing else? Skip the CLI entirely.

```bash
code --install-extension MasihMoloodian.sema-codebase-chat
```

Or search **"sema"** in the Extensions view. Add an API key in the panel and start
chatting — the index is optional. Full guide: [sema for VS Code](../vscode-extension/README.md).

### Just the index

Want Claude Code / Codex to stop hunting for files, with no editor extension? Do
the CLI install below (recommended one-liner), then `sema index .` and
`sema setup` in your project — that's it.

## Install (recommended)

One line — installs the `sema` command (bootstrapping `uv` and a Python for it if
needed), then offers to register with whichever AI CLIs you have:

```bash
curl -fsSL https://raw.githubusercontent.com/masihmoloodian/sema/main/install.sh | sh
```

Then, inside each project:

```bash
cd your-project
sema index .     # build the local semantic index
sema setup       # register with every detected CLI: Claude Code, Codex, opencode, Grok Build
```

Confirm with `sema --version`, and inside your editor type `/mcp` to see
`✓ sema connected`. Stuck? Run `sema doctor`.

Skip any client with an env var (they pass cleanly through `curl | sh`):

```bash
SEMA_SKIP_CODEX=1 curl -fsSL https://raw.githubusercontent.com/masihmoloodian/sema/main/install.sh | sh
```

`SEMA_SKIP_CLAUDE`, `SEMA_SKIP_CODEX`, `SEMA_SKIP_OPENCODE`, `SEMA_YES=1`
(non-interactive), and `SEMA_NO_SETUP=1` (binary only) are all honoured. The
installer is [install.sh](https://github.com/masihmoloodian/sema/blob/main/install.sh) — read it first if you like.

## Install manually

Prefer not to pipe to a shell? Install the package directly:

```bash
uv tool install sema-mcp     # or: pipx install sema-mcp / pip install sema-mcp
sema --version
```

Then, per project:

```bash
cd your-project
sema index .
sema setup                   # all detected CLIs at once
#   ...or one at a time:
sema init --claude           # or --codex
```

> On PyPI the distribution is named **`sema-mcp`** (the name `sema` was already
> taken); the command and the import are both `sema`.

## Install from source (for development)

> For contributing, or to run the latest unreleased code.

### Using uv (recommended)

```bash
# 1. Clone to wherever you want
git clone https://github.com/masihmoloodian/sema.git
cd sema

# 2. Create a virtual environment and install
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"

# 3. Add sema to your PATH so you can call it from any project
echo "export PATH=\"$(pwd)/.venv/bin:\$PATH\"" >> ~/.zshrc
source ~/.zshrc

# 4. Verify — run this from any directory
sema --version
```

> Step 3 writes the absolute path of your current directory into `~/.zshrc`.
> For bash, replace `~/.zshrc` with `~/.bashrc`.

### Using pip

```bash
git clone https://github.com/masihmoloodian/sema.git
cd sema

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"

echo "export PATH=\"$(pwd)/.venv/bin:\$PATH\"" >> ~/.zshrc
source ~/.zshrc

sema --version
```

---

Next: [Claude Code setup](claude-code.md) · [OpenAI Codex setup](codex.md) · [opencode setup](opencode.md) · [sema for VS Code](../vscode-extension/README.md)
