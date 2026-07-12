# Installation

## Requirements

- Python 3.11 or higher
- One of:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) or [Claude Code VS Code extension](https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code)
  - [Codex VS Code extension](https://marketplace.visualstudio.com/items?itemName=openai.chatgpt) or [OpenAI Codex CLI](https://github.com/openai/codex)
- ~80MB disk space for the embedding model (downloaded once, cached globally)
- No Docker, no external APIs, no GPU — runs entirely on your machine

## Install

```bash
pip install sema-mcp
```

This installs the `sema` command. With uv: `uv tool install sema-mcp`. Verify with `sema --version`.

> On PyPI the distribution is named **`sema-mcp`** (the name `sema` was already taken); the command and the import are both `sema`.

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

> Step 3 writes the absolute path of your current directory into `~/.zshrc` automatically.
> For bash: replace `~/.zshrc` with `~/.bashrc`.

### Using pip

```bash
git clone https://github.com/masihmoloodian/sema.git
cd sema

python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

pip install -e ".[dev]"

echo "export PATH=\"$(pwd)/.venv/bin:\$PATH\"" >> ~/.zshrc
source ~/.zshrc

sema --version
```

---

Next: [Claude Code setup](claude-code.md) · [OpenAI Codex setup](codex.md) · [VS Code workspace setup](vscode-workspace.md)
