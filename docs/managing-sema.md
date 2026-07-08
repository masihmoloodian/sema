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

## Update sema to the latest version

```bash
cd /path/to/sema

# Pull the latest changes
git pull

# Re-install (only needed if dependencies changed)
pip install -e ".[dev]"
# or with uv:
uv pip install -e ".[dev]"

# Check the version
sema --version
```

Your existing project indexes are untouched — no need to re-run `sema index .` unless the release notes say the index format changed.

> Once sema is published to PyPI, updating will be just `pip install --upgrade sema`.

## Fully remove sema from your machine

```bash
# 1. Deactivate from all projects first
cd your-project && sema init --claude --uninstall
# or for Codex projects:
cd your-project && sema init --codex --uninstall

# 2. Delete the sema repo and virtual environment
rm -rf /path/to/sema

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
