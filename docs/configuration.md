# Configuration

sema is designed to work with zero configuration. There is no config file to
write and no required environment variables — `sema index .` picks sensible
defaults and figures out what to index on its own. This page documents the few
things you *can* influence and where sema stores its data.

## What gets indexed

File selection is automatic. sema indexes every file whose extension or name is
handled by a registered parser (see [Supported languages](languages.md)), and
skips the rest. On top of your `.gitignore`, it always excludes:

| Category | Examples |
|---|---|
| Directories | `.git` `.sema` `node_modules` `__pycache__` `.venv` `venv` `env` `dist` `build` `coverage` `.mypy_cache` `.pytest_cache` `.ruff_cache` `vendor` `third_party` |
| Lock files | `package-lock.json` `yarn.lock` `pnpm-lock.yaml` `poetry.lock` `cargo.lock` `Gemfile.lock` … |
| Generated files | `*.min.js` `*.min.ts` `*.d.ts` |

Anything matched by your project's `.gitignore` is skipped too. To index only a
subset of a repo (for example a single workspace), use
[`sema index --workspace`](vscode-workspace.md).

## Environment variables

sema reads no configuration from the environment during indexing or serving —
the embedding model and index location are fixed (see below). The only
environment variables it honours are the installer-set skip flags for
`sema setup`:

```
SEMA_SKIP_CLAUDE      Set to 1 to skip Claude Code during `sema setup`
SEMA_SKIP_CODEX       Set to 1 to skip OpenAI Codex during `sema setup`
SEMA_SKIP_OPENCODE    Set to 1 to skip opencode during `sema setup`
SEMA_SKIP_GROK        Set to 1 to skip Grok Build during `sema setup`
```

These mirror the `--skip-claude` / `--skip-codex` / `--skip-opencode` / `--skip-grok` flags and
exist so the one-line installer can register only the clients you want. See the
[CLI reference](cli-reference.md).

## Where sema stores things

| Path | What it is |
|---|---|
| `.sema/index/` | The ChromaDB vector index (embedded mode, machine-specific) |
| `.sema/meta.json` | Index stats: chunk/file counts, model, timestamp, sema version |
| `~/.cache/sema/models/` | Downloaded embedding model, shared across projects |

The embedding model is **all-MiniLM-L6-v2** (~80 MB, local SBERT). It downloads
once on your first `sema index` and is cached under `~/.cache/sema/models/`;
after that everything runs fully offline — no internet, no external APIs.

## `.gitignore`

sema does **not** modify your `.gitignore`. Add the index directory yourself so
the machine-specific index isn't committed:

```gitignore
# sema
.sema/index/
```

You can commit `.sema/meta.json` if you want teammates to see index stats. The
index itself is machine-specific and should not be committed.
