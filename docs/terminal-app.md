# `sema chat` — the terminal app

A coding agent in your terminal, built on the same semantic index the MCP server
and the VS Code extension use. Everything the CLI and the extension can do is
reachable from here.

```bash
uv sync --extra chat      # or: pip install 'sema-mcp[chat]'
cd your-project
sema chat
```

The chat extras are optional so the MCP server stays lean — without them,
`sema chat` prints the install command and exits.

---

## Layout

On open, the wordmark — the same two-ribbon S as the extension icon, drawn in
block characters, with a compact `▟▛ ▟▛ sema` fallback on narrow terminals:

```
   ▟██████▙   ▗▄▄▖ ▗▄▄▄▖▗▖  ▗▖ ▗▄▖
 ▟██████▛     ▐▌   ▐▌   ▐▛▚▞▜▌▐▌ ▐▌
   ▟██████▙    ▝▀▚▖▐▛▀▀▘▐▌  ▐▌▐▛▀▜▌
 ▟██████▛     ▗▄▄▞▘▐▙▄▄▖▐▌  ▐▌▐▌ ▐▌

  /Users/masih/w/sema · Claude Code (local CLI) · default · mode agent
```

Then:

```
┌────────────────────────────────────────────────────────────┐
│ transcript — streamed markdown, tool cards, thinking pane  │
├────────────────────────────────────────────────────────────┤
│ ▚▘ sema · search_code (4s · 1,234 tokens · ctrl+c to stop) │
├────────────────────────────────────────────────────────────┤
│ > input (multiline, ↑/↓ history, / commands)               │
├────────────────────────────────────────────────────────────┤
│ agent · claude-code/default · high · 12.4k tok · $0.31     │
└────────────────────────────────────────────────────────────┘
```

While a turn runs, the indicator line above the input animates the two ribbons
and reports what sema is doing right now — `thinking`, the name of the tool it
just invoked, then `responding` — alongside elapsed time, tokens spent so far,
and how to interrupt. It disappears when the turn ends.

| Key | Action |
|---|---|
| `/` | Open the command menu |
| `Enter` | Send — or pick the highlighted command when the menu is open |
| `Shift+Enter` | Newline |
| `↑` / `↓` | Navigate the menu; otherwise previous / next prompt |
| `Tab` | Pick the highlighted command |
| `Esc` | Close the menu |
| `Ctrl+C` | Cancel the running turn; again to quit |
| `Shift+Tab` | Cycle mode: ask → plan → agent |
| `Ctrl+T` | Show or hide the reasoning pane |
| `Ctrl+L` | Clear the transcript |

### The command menu

Typing `/` opens a filtered list above the input:

```
╭────────────────────────────────────────────────────────╮
│ /sessions   List saved sessions                        │
│ /search     Semantic code search                       │
│ /setup      Register sema with every detected AI CLI   │
│ /reuse      Does this already exist?                   │
╰────────────────────────────────────────────────────────╯
 /se
```

Keep typing to narrow it. Prefix matches rank first, then substring matches —
so `/se` offers `search` and `setup` before `reuse`. Picking a command that
takes arguments leaves a trailing space with the caret ready; one that doesn't
is complete as typed. The menu closes as soon as you start typing arguments, so
`Enter` sends from then on.

`Enter` on a command you have typed **in full** runs it rather than
re-completing it — `/mode` + `Enter` opens the mode picker. Use `Tab` when you
want completion regardless.

### Pickers

`/provider`, `/model`, `/mode`, `/effort`, and `/resume` open an arrow-navigable
picker when called with no argument:

```
╭──────────────────────────────────────────────────────────╮
│ Provider                                                 │
│                                                          │
│  Claude Code (local CLI)      uses your local login      │
│  Codex (local CLI)            uses your local login      │
│  Claude (Anthropic)      ●    needs ANTHROPIC_API_KEY    │
│  OpenAI                       needs OPENAI_API_KEY       │
│                                                          │
│  ↑↓ navigate · ⏎ select · Esc cancel                     │
╰──────────────────────────────────────────────────────────╯
```

`●` marks the current value and the list opens on it, so `Enter` without moving
is a no-op. `⭐` marks the recommended option. `Esc` cancels and changes nothing.

Passing an argument skips the picker entirely — `/provider openai`,
`/model claude-haiku-4-5`, `/mode plan` — which is what you want when scripting
or when you already know the id.

---

## Modes

| Mode | Tools | Use for |
|---|---|---|
| **ask** | none | Plain conversation; no repo access |
| **plan** | read-only (sema tools, `read_file`, `glob`, `grep`) | Investigate and produce a plan; saved to `.sema/plans/<session>.md` |
| **agent** | full set, including `write_file`, `edit_file`, `bash` | Actually make the change |

Plan mode's only side effect is the plan file. `/plan` shows it; it is injected
into later turns so the agent follows it.

---

## Providers

Local CLI providers reuse an existing login and need no API key. Key-based
providers read the matching environment variable.

| Provider | id | Auth | Verified |
|---|---|---|---|
| Claude Code | `claude-code` | local `claude` login | ✅ ask · agent · resume |
| Codex | `codex` | local `codex` login | ✅ ask · agent · resume |
| opencode | `opencode` | local login | ✅ ask · agent · resume |
| Grok Build | `grok` | local login | ✅ ask · agent · resume |
| Cursor Agent | `cursor` | local login | ⚠️ flags unverified — `cursor-agent` was not installed on the machine this was built on |
| Claude (API) | `anthropic` | `ANTHROPIC_API_KEY`, or an `ant auth login` profile | ✅ wire protocol (mock server) |
| OpenAI | `openai` | `OPENAI_API_KEY` | ✅ wire protocol (mock server) |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | ✅ wire protocol (mock server) |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | ✅ wire protocol (mock server) |
| Together AI | `together` | `TOGETHER_API_KEY` | ✅ wire protocol (mock server) |

"Verified" means run for real against the live CLI, or — for the API providers —
against a local server speaking the provider's actual SSE format, which
exercises the real SDK client and this project's stream parsing. The API
providers have not been billed against a live endpoint.

**Claude Code is the default** — it reuses your existing local login, so a first
run needs nothing configured. Switch with `/provider`, `/model`, and `/effort`.

Your picks are **remembered**: provider, model, mode, and effort are written to
`chat-prefs.json` beside the session store and restored on the next start. An
explicit flag (`--provider openai`) overrides the saved value for that run
without overwriting it. A saved model that doesn't belong to the current
provider is discarded rather than sent to an API that would reject it.

Each CLI has its own flag vocabulary and JSON event schema, captured from its
`--help` and a real run rather than assumed — for example Codex emits whole
`item.completed` messages while Grok streams `{"type":"text","data":…}` deltas,
and `codex exec resume` rejects `--sandbox` and `-m`, so a resumed Codex thread
keeps the model and sandbox policy it was created with. Changing `/model`
mid-session starts a fresh thread rather than silently ignoring you.

> **CLI providers in agent mode ask once.** They run their own tools and cannot
> be prompted per call, so the first agent-mode turn asks whether the provider
> may edit files for the whole session. Answer once and it is not raised again;
> switching provider asks afresh, and `--yes` skips the question entirely.
> Permission is never persisted across runs — only the provider/model choice is.

---

## Commands

```
/help  /clear  /new  /quit                    session and app
/mode ask|plan|agent   /provider   /model   /effort
/search <q>   /get <sym>   /reuse <desc>   /map   /usages   /impact   /explain
/index [--reset]   /watch on|off|status   /add <f>   /rm <f>   /files
/status   /doctor   /manage   /setup   /update agents|sema
/sessions   /resume <id>   /cost   /attach <path>   /plan   /redact   /tools
/devops pending|approve <id>|deny <id>|log
```

Text starting with `/` that names no command is sent to the model as an ordinary
prompt, so `/usr/local/bin/foo` is safe to paste.

---

## Permissions

Read-only tools run unattended. `write_file`, `edit_file`, and `bash` prompt
before running, with three answers: allow once, always allow (the tool, or the
command prefix like `npm test`), or deny. A denial is returned to the model as a
normal tool result, so it adapts rather than failing.

`kubectl`, `terraform`, `aws`, and `helm` are routed through sema's
analyze-first devops gate rather than executed directly — inspect the queue with
`/devops pending` and release with `/devops approve <id>`.

Two guardrails are structural rather than advisory:

- **Path confinement** — every model-supplied path is resolved and must stay
  under the project root; `..`, symlink escapes, and outside absolute paths are
  rejected before any filesystem call.
- **Edit staleness** — `edit_file` refuses to write a file that changed since
  the model read it. This is why editing is a dedicated tool rather than
  `bash sed`.

`--yes` bypasses the prompts for unattended runs.

---

## Sessions

Sessions are stored in the VS Code extension's own format and location, so one
conversation moves between the two surfaces — including the CLI-thread handle
that lets Claude Code and Codex resume their own memory.

```bash
sema chat --resume <id>     # or /sessions then /resume <id> in-app
```

Set `SEMA_CHAT_HOME` to override where sessions are kept.

---

## Headless use

```bash
sema chat --print "which module owns rate limiting?"
git diff | sema chat --print "review this diff"
sema chat --print --yes "add a docstring to parse_config"   # allows edits
```

`--print` streams the answer to stdout and sends tool activity to stderr, so it
composes in a pipeline. Without `--yes`, mutating tools are denied — an
unattended run has no one to ask.

---

## Flags

| Flag | Meaning |
|---|---|
| `--root PATH` | Project root (default: nearest indexed ancestor of the cwd) |
| `--provider ID` | Starting provider |
| `--model ID` | Starting model |
| `--mode MODE` | `ask`, `plan`, or `agent` |
| `--resume ID` | Resume a saved session |
| `--yes` | Auto-approve every tool call |
| `--print` | One-shot headless run |
