# opencode setup

[opencode](https://opencode.ai) is an open-source AI coding agent. You can use it
with sema two ways: as a **chat provider** inside the sema VS Code extension, or by
giving opencode sema's **MCP tools** so its own agent searches your index instead of
reading files blindly. New to sema? See [Why sema](why-sema.md).

## Install the opencode CLI

opencode is open source and works with any model provider you sign into. Install it
first (skip if you already have it):

```bash
# macOS / Linux (recommended)
curl -fsSL https://opencode.ai/install | bash

# npm
npm install -g opencode-ai

# Homebrew
brew install sst/tap/opencode
```

opencode ships with **free models**, so you can start immediately. To use a premium
provider (Anthropic, OpenAI, …), sign in and list your models:

```bash
opencode auth login   # pick a provider and paste a key
opencode models       # list the models available to you, as provider/model slugs
```

Set a default model in `opencode.json` (project root, or global at
`~/.config/opencode/opencode.json`) — replace the slug with one from `opencode models`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "anthropic/claude-sonnet-4-5"
}
```

Verify: `opencode --version`. Full docs: <https://opencode.ai/docs/>.

## Use opencode in the sema chat panel

opencode is a built-in provider in the sema VS Code extension (alongside Claude Code
and Codex) — no MCP setup needed.

1. Open the sema **Chat** view and pick **opencode (local)** in the provider dropdown.
2. Click **Log in** if you haven't signed in — it runs `opencode auth login`.
3. Pick a model: leave it on **default** (uses your `opencode.json` model), or choose
   **"+ custom id…"** and enter a `provider/model` slug (`opencode models` lists them).
4. Choose a mode and chat:
   - **Ask** / **Plan** run opencode's read-only `plan` agent (it won't edit files).
   - **Agent** runs the `build` agent with permissions auto-approved — it edits files
     and runs commands.

If VS Code can't find `opencode` on its `PATH` (common when the app is launched from
the Dock/Finder), set `sema.chat.opencodePath` to the absolute path (`which opencode`).

> Under the hood sema drives opencode headlessly with `opencode run --format json`.
> Each chat is one opencode session and keeps memory across turns; **New chat** starts
> a fresh one. Token counts and cost (from opencode's `step_finish` events) appear in
> the **Manage** view — free models report $0.

## Give opencode the sema tools (MCP) — optional

Let opencode's **own** agent call sema's semantic tools (`search_code`, `check_reuse`,
`get_code`, `find_usages`, `impact_analysis`, `repo_map`) so it stops grepping and
re-reading files.

1. Index your project (downloads a ~80MB model on first run):

   ```bash
   cd your-project
   sema index .
   ```

2. Register sema with opencode. `sema setup` detects opencode and writes the MCP
   block into `opencode.json` for you (it also registers any Claude Code and Codex
   you have):

   ```bash
   sema setup
   ```

   > `sema init` has no opencode flag — opencode registration only happens through
   > `sema setup`. To wire it up by hand instead, add sema as a local MCP server in
   > `opencode.json` at your project root:
   >
   > ```json
   > {
   >   "$schema": "https://opencode.ai/config.json",
   >   "mcp": {
   >     "sema": {
   >       "type": "local",
   >       "command": ["sema", "serve", "--project", "."],
   >       "enabled": true
   >     }
   >   }
   > }
   > ```

3. Restart opencode from the project directory and type `/mcp` — you should see
   **sema** connected.

### Add `AGENTS.md`

opencode reads `AGENTS.md` for project rules. Without it, opencode may not call sema's
tools automatically. Create one at your project root:

```markdown
## Codebase navigation

This project is indexed by sema. Use sema MCP tools to locate code — do not grep or
read files directly.

| Goal | Tool |
|---|---|
| Find a function, class, or method | `search_code("natural language description")` |
| Check if something already exists before writing it | `check_reuse("what you're about to build")` |
| Read full source of a known symbol | `get_code("symbolName")` |
| Find all callers of a symbol | `find_usages("symbolName")` |
| Understand call chains and blast radius | `impact_analysis("symbolName")` |
| Architecture overview | `repo_map()` |

Always call `search_code()` before grep or reading files. Before writing a new
function or utility, call `check_reuse()` and reuse an existing match instead of
writing a parallel implementation.
```

## Keep the index fresh (optional)

```bash
sema watch .
```

Detects file saves and re-indexes only changed files incrementally.

## Remove sema from opencode

Run `sema setup --uninstall` (removes sema from every detected CLI, opencode
included), or delete the `sema` entry from the `mcp` block in `opencode.json` by
hand (or set `"enabled": false`).
