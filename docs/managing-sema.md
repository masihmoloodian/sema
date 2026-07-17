# Managing sema

## Remove the index for a project

```bash
cd your-project
rm -rf .sema/
```

This deletes the vector database and metadata. Run `sema index .` to rebuild.

## Deactivate sema for a project (keep index)

For Claude Code:
```bash
cd your-project
sema init --claude --uninstall
```

This calls `claude mcp remove sema -s user` and kills any running `sema serve` processes. To re-activate, run `sema init --claude` again.

For Codex:
```bash
cd your-project
sema init --codex --uninstall
```

This removes the `[mcp_servers.sema]` block from `.codex/config.toml`.

To deregister from **every** detected CLI at once (Claude Code, Codex, opencode, Grok Build), use `sema setup --uninstall` — `sema init` targets only one client and has no `--opencode` flag.

## Update sema to the latest version

Update the same way you installed. The package on PyPI is `sema-mcp`; the command stays `sema`.

```bash
# Installed with the one-liner or `uv tool install` (recommended):
uv tool upgrade sema-mcp

# Installed with pipx:
pipx upgrade sema-mcp

# Installed with pip:
pip install --upgrade sema-mcp

# Working from a git clone (contributors):
cd /path/to/sema && git pull && uv pip install -e ".[dev]"

# Check the version
sema --version
```

Your existing project indexes are untouched — no need to re-run `sema index .` unless the release notes say the index format changed.

## Fully remove sema from your machine

```bash
# 1. Deregister from every detected AI CLI (Claude Code, Codex, opencode, Grok Build)
cd your-project && sema setup --uninstall

# 2. Uninstall the sema binary — match how you installed it
uv tool uninstall sema-mcp     # or: pipx uninstall sema-mcp
                               # or: pip uninstall sema-mcp
                               # (git clone: rm -rf /path/to/sema)

# 3. Delete the cached embedding model (~80MB)
rm -rf ~/.cache/sema/

# 4. Delete any project indexes
find ~ -type d -name ".sema" 2>/dev/null
# Review the list, then remove each:
rm -rf /your-project/.sema/
```

## When to re-index

The easiest option — run `sema watch` in a terminal while you work. It re-indexes any file the moment you save it, so the index is always current:

```bash
sema watch
# 14:22:31  indexed   src/auth/auth.service.ts  (12 chunks)
# 14:22:45  indexed   src/users/users.service.ts  (8 chunks)
# Ctrl+C to stop
```

If you prefer manual re-indexing, sema tracks a SHA-256 hash of every indexed file in `.sema/hashes.json`. Running `sema index .` again only re-embeds files that actually changed — unchanged files are skipped instantly:

```bash
sema index .
# ✔ Indexed 2 files (847 unchanged, skipped)
# ✔ Generated 11 chunks
```

Use `--reset` to force a full re-index from scratch (also clears the hash store):

```bash
# Force full re-index — use after upgrading sema or changing .gitignore
sema index . --reset
```

You do **not** need to re-index when:
- Starting a new Claude Code chat session
- Restarting VS Code
- Asking a different question about the same codebase
