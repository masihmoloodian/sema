# Working with multiple projects

By default one `sema serve` process serves one project. **Multi-project mode** lets a single registration serve every indexed project under one or more root directories — so you can switch between repos without re-registering.

## How it works

You register sema once against a **root** directory (e.g. `~/code`). At startup the server scans that root for any folder containing a `.sema/index/` and serves them all. Each project gets a short **name** (its folder name), and the AI targets one by passing a `project` argument to any tool.

- Add a new project later? Just `sema index .` inside it — no re-registration needed. The server picks it up (it re-scans when the assistant calls `list_projects()`).
- Each project keeps its own independent index; nothing is merged.

## Setup

```bash
# 1. Index each project (once per project)
cd ~/code/backend  && sema index .
cd ~/code/frontend && sema index .

# 2. Register sema once, pointed at the parent root
sema init --claude --root ~/code      # or: sema init --codex --root ~/code

# 3. Reload VS Code, then type /mcp to confirm sema is connected
```

You can pass `--root` more than once if your projects live under several parents:

```bash
sema init --claude --root ~/work --root ~/oss
```

## Using it

The AI calls `list_projects()` to see what's available, then passes `project="<name>"` to any tool:

```
list_projects()
→ backend   —  ~/code/backend   (412 chunks)
→ frontend  —  ~/code/frontend  (388 chunks)

search_code("jwt validation", project="backend")
get_code("generateToken", project="frontend")
```

When only **one** project is indexed, the `project` argument is optional everywhere — single-project usage is unchanged. When several are indexed and you omit `project`, the tool returns an error listing the available names rather than guessing.

## Recommended `CLAUDE.md` / `AGENTS.md` note

Add this to each project (or a shared parent) so the assistant targets the right project:

```markdown
This machine runs sema in multi-project mode. Call `list_projects()` first to see
indexed projects, then pass `project="<name>"` to every sema tool
(search_code, get_code, find_usages, repo_map, explain_file, impact_analysis).
```

## Checking status

```bash
sema status
```

In multi-project mode this shows the served root(s) and every project discovered under them, marking the one matching your current directory.

## Single-project vs multi-project

| | Single-project | Multi-project |
|---|---|---|
| Register | `sema init --claude` (in the project) | `sema init --claude --root <dir>` |
| Serves | one project | every indexed project under the root(s) |
| `project` argument | optional (ignored) | required when >1 project |
| Add a project | re-run `sema init` | just `sema index .` under the root |

## Notes & limits

- Discovery scans up to a few directory levels below each root and skips heavy dirs (`node_modules`, `.git`, `.venv`, …).
- Project stores are built lazily on first query, so serving many projects stays fast at startup.
- Names come from folder basenames; if two projects share a name they're disambiguated by a parent segment (`backend/api` vs `web/api`).
