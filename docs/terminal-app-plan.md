# sema Terminal App — Implementation Plan

> **Status: implemented.** Shipped as `sema chat` (see
> [terminal-app.md](terminal-app.md) for the user-facing guide). 244 tests cover
> the agent and TUI layers. Two deviations from the plan below, both noted
> inline: milestone 1's `cli.py` refactor was replaced by `sema/agent/ops.py`
> (in-process for index queries, subprocess for management commands), and the
> agent loop is hand-written rather than the SDK tool runner, because it has to
> drive Anthropic, OpenAI-family, and CLI-backed providers through one path.

A Claude-Code-style TUI (`sema chat`) that exposes **every** capability currently
available through the sema CLI and the VS Code extension, from one terminal
interface.

**Stack:** Python + Textual (in-repo, `uv`-managed) · tools called in-process ·
full coding agent (read/write/edit/bash) with a permission gate.

---

## 1. Feature inventory — what must be covered

### 1.1 From the CLI (`sema/cli.py`)

| Command | Terminal-app surface |
|---|---|
| `index [--workspace] [--reset] [--verbose]` | `/index`, `/reindex`, `/reindex reset` + Manage pane |
| `watch [--workspace]` | `/watch on\|off\|status`, background task + status-bar indicator |
| `search <q> [--top-k] [--all-types]` | `/search` slash command **and** the `search_code` agent tool |
| `get <symbol> [--project]` | `/get`, `get_code` tool |
| `reuse <description>` | `/reuse`, `check_reuse` tool |
| `list [path]` | `/files` — indexed-file browser |
| `add <file>` / `remove <file>` | `/add`, `/rm` — incremental index edits |
| `status [-v]` | Status bar (compact) + `/status` (full) |
| `doctor` | `/doctor` — runs checks, renders pass/fail rows |
| `redact` | Applied automatically to outbound context; `/redact` to inspect |
| `serve` | Not exposed (the TUI *is* the client); `/mcp` shows registration state |
| `init` / `setup` / `--uninstall` | `/setup` wizard — per-client register/unregister |
| `update` / `self-update` | `/update agents`, `/update sema` |
| `devops plan\|run\|approve\|deny\|pending\|log` | `/devops` + the devops approval modal (see §5) |

### 1.2 From the VS Code extension (`vscode-extension/src/`)

| Extension feature | Terminal-app surface |
|---|---|
| Chat panel (`chatPanel.ts`) | The main transcript view |
| Modes: **ask / plan / agent** (`chatMode.ts`) | `/mode` + Shift-Tab cycle; drives tool availability |
| Providers (`providers/`): anthropic, openai, openrouter, deepseek, together, openai-compatible, **cli** (claude/codex/opencode/grok/cursor) | `/provider`, `/model` — same provider registry, ported |
| Model + effort selection (`modelSelection.ts`) | `/model`, `/effort` — capability-probed per provider |
| `SEMA_WORKFLOW` / `PLAN_NOTE` / `ASK_NOTE` (`semaWorkflow.ts`) | Reused verbatim as the system-prompt builder |
| Plan artifact (`planArtifact.ts`) | Plan-mode answer saved to disk; `/plan` to view/apply |
| Session store (`sessionStore.ts`) | `/sessions`, `/resume`, `/new`; same on-disk JSON schema |
| Attachments (`attachments.ts`) | `/attach <path>`, drag-path paste; same sniff/limit rules |
| CLI-session resume (`cliSessionId`, mode/model/permission contract) | Preserved so a session started in the editor resumes in the terminal |
| Manage view (`manageView.ts`) | `/manage` — reindex, doctor, watch toggle, updates |
| Status bar (`statusBar.ts`) | Persistent bottom bar |
| Redaction (`redact.ts`) | Same pipeline before any outbound send |
| Usage/cost tally (`SessionUsage`) | `/cost` + status bar |

**Compatibility requirement:** the terminal app reads and writes the *same*
session store, plan artifacts, and attachment staging directory as the
extension. A session must be resumable in either surface.

---

## 2. Architecture

```
sema/
  agent/
    loop.py          # provider-agnostic agent loop (tool_runner for Anthropic)
    providers/       # ported from the extension: anthropic, openai, openrouter,
                     #   deepseek, together, openai_compatible, cli
    tools.py         # @beta_tool wrappers: 8 sema tools + fs/bash/glob/grep
    permissions.py   # allow / ask / deny policy + approval queue
    prompt.py        # SEMA_WORKFLOW + PLAN_NOTE + ASK_NOTE + repo_map + CLAUDE.md
    session.py       # StoredSession-compatible read/write, usage tally
    attachments.py   # sniff / stage / materialize (mirrors the TS module)
  tui/
    app.py           # Textual App shell
    transcript.py    # streamed markdown, tool cards, thinking pane
    input.py         # multiline input, history, slash-command completion
    status.py        # model · mode · tokens · cost · context% · watch state
    modals/          # approval, model picker, session picker, manage, doctor
    commands.py      # slash-command registry -> CLI functions
  cli.py             # + `sema chat`
```

**Design rule:** every slash command calls the *same underlying function* the
CLI command calls — no reimplementation. `sema/cli.py` gets a light refactor so
each command body is a callable importable from `sema.core.*`, with the Click
decorator as a thin shell.

---

## 3. The agent loop

Anthropic path uses the SDK's beta **tool runner** — it drives the
call → execute → loop cycle while still yielding each assistant message before
tools run, which is where the permission gate hooks in.

```python
runner = client.beta.messages.tool_runner(
    model=session.model,
    max_tokens=64000,
    thinking={"type": "adaptive", "display": "summarized"},
    output_config={"effort": session.effort},
    system=build_system(context, reads_workspace, mode, plan, plan_path, use_index),
    tools=tools_for_mode(session.mode),
    messages=session.messages,
)
```

API notes (current, do not regress):
- `thinking={"type": "adaptive"}` — `budget_tokens` returns 400 on Opus 4.7+.
- No `temperature` / `top_p` / `top_k` — also 400 on Opus 4.7+.
- `display: "summarized"` — the default is `"omitted"`, which would render an
  empty thinking pane.
- Stream everything (`max_tokens` is large); use `.get_final_message()`.
- `cache_control` breakpoint on the last system block; keep it byte-stable
  (no timestamps in the system prompt) or the cache never hits.

Non-Anthropic providers keep their existing per-provider adapters, normalized to
one internal event stream: `text_delta`, `thinking_delta`, `tool_call`,
`tool_result`, `usage`, `done`.

**Mode → tool policy**

| Mode | Tools |
|---|---|
| `ask` | none — conversation only |
| `plan` | read-only (sema tools, `read_file`, `glob`, `grep`); output saved as plan artifact |
| `agent` | full set incl. `write_file`, `edit_file`, `bash` |

---

## 4. Tools

The 8 existing MCP tools are wrapped as `@beta_tool` functions over
`sema/mcp/tools.py` — schemas generate from the signatures:

`search_code` · `check_reuse` · `get_code` · `repo_map` · `find_usages` ·
`explain_file` · `impact_analysis` · `list_projects`

Plus the coding-agent set: `read_file`, `write_file`, `edit_file`, `bash`,
`glob`, `grep`.

Two invariants preserved from the MCP contract:
1. `search_code` never returns bodies — signatures only.
2. `get_code` is the only tool that returns full source.

Guardrails built in from the first commit:
- **Path confinement** — resolve every model-supplied path, verify it stays
  under the project root (`Path.resolve().is_relative_to(root)`); reject `..`,
  symlink escapes, and absolute paths outside.
- **Staleness check on `edit_file`** — reject the write if the file changed
  since the model last read it. This is the reason `edit` is a dedicated tool
  rather than `bash sed`.
- **`bash`** — timeout, output cap, and rejection of shell operators unless the
  user approves that specific invocation.

---

## 5. Permissions & the devops gate

Per-tool policy: `allow` / `ask` / `deny`.

- `allow` — read-only sema tools, `read_file`, `glob`, `grep`
- `ask` — `write_file`, `edit_file`, `bash`
- Session-scoped "always allow this prefix" so `npm test` isn't re-prompted 40×

The gate lives **inside the tool function**: it awaits an approval future the
TUI resolves via a modal. On denial the function returns
`"User declined this action"` as a normal tool result and the model adapts —
no custom loop needed.

**devops integration.** `sema devops` already implements an analyze-first
execution gate for `kubectl` / `terraform` / `aws` / `helm`. The `bash` tool
routes any command matching those binaries through `devops_plan` first, renders
the plan in the approval modal, and only then calls `devops_run`. `/devops
pending`, `/devops approve <id>`, `/devops deny <id>`, and `/devops log` are
available as slash commands.

---

## 6. TUI layout

```
┌────────────────────────────────────────────────────────────┐
│ transcript  — streamed markdown, tool cards, thinking pane │
│                                                            │
├────────────────────────────────────────────────────────────┤
│ > input (multiline, ↑/↓ history, / completion)             │
├────────────────────────────────────────────────────────────┤
│ agent · opus-4.8 · xhigh · 12.4k tok · $0.31 · 18% · ●watch│
└────────────────────────────────────────────────────────────┘
```

- **Tool cards** — collapsible: name + arg summary + result preview; expand for
  full output.
- **Thinking pane** — dim italic, collapsed by default, toggled with `Ctrl-T`.
- **Ctrl-C** — cancels the current turn (stream aborted, history intact).
  Twice in a row exits.
- **Shift-Tab** — cycles ask → plan → agent.
- Textual is async; `AsyncAnthropic` streams inside a worker and posts events to
  the UI via `post_message`.

---

## 7. Slash commands

```
/help  /clear  /new  /quit
/mode ask|plan|agent          /model [name]      /provider [name]   /effort [level]
/search <q>   /get <symbol>   /reuse <desc>      /map   /usages <sym>   /impact <sym>
/index [--reset]  /watch on|off  /add <f>  /rm <f>  /files  /status  /doctor
/attach <path>    /plan [apply]  /sessions  /resume <id>  /cost  /redact
/devops pending|approve|deny|log      /manage   /setup   /update agents|sema
```

Unknown `/x` falls through to the model as plain text.

---

## 8. Build order

| # | Milestone | Deliverable |
|---|---|---|
| 1 | **Core extraction** | Refactor `cli.py` bodies into `sema/core/*` so both Click and the TUI call one implementation. No behavior change; existing tests stay green. |
| 2 | **Tool layer** | `agent/tools.py` — 8 sema tools + read/glob/grep, read-only. Unit tests per tool. |
| 3 | **Headless loop** | `agent/loop.py` + Anthropic provider + a plain-`print` REPL. Verify the agent answers real questions about this repo. |
| 4 | **Mutating tools + permissions** | write/edit/bash, path guard, staleness check, terminal `y/n` gate. devops routing. |
| 5 | **Textual UI** | Transcript, input, status bar, approval modal. Replaces the print REPL. |
| 6 | **Sessions + attachments** | Read/write the extension's `StoredSession` schema; `/sessions`, `/resume`, `/attach`. Cross-surface resume test. |
| 7 | **Provider parity** | Port the remaining providers (openai, openrouter, deepseek, together, openai-compatible, cli-backed) + `/model` `/effort` pickers. |
| 8 | **Management surface** | `/index` `/watch` `/doctor` `/status` `/manage` `/setup` `/update` modals. |
| 9 | **Plan mode + artifacts** | `PLAN_NOTE` enforcement, artifact save/read, `/plan apply`. |
| 10 | **Polish** | Themes, key-binding config, `--print` non-interactive mode for scripting, docs. |

Milestones 1–3 are the first usable slice. 1–6 is feature-complete for daily
use; 7–10 closes parity with the extension.

---

## 9. Testing

- **Unit** — each tool (incl. the path guard and staleness rejection), the
  permission policy resolver, the session (de)serializer against extension-written
  fixtures, slash-command parsing.
- **Integration** — a scripted agent run against `tests/fixtures/example-repo`
  with a recorded/stubbed provider; asserts tool-call sequence and final state.
- **Cross-surface** — write a session in the terminal app, load it with the
  extension's `SessionStore` tests (and the reverse).
- **Snapshot** — Textual's `pilot` + snapshot testing for the transcript and
  modals.

Target: keep the suite at parity with the existing 175 tests plus roughly one
test per new tool and command.

---

## 10. Open decisions

1. **Default model** — `claude-opus-4-8` vs. inheriting the extension's last
   selection from the session store. *Leaning: inherit, fall back to Opus 4.8.*
2. **API key source** — reuse the extension's stored key location, or read
   `ANTHROPIC_API_KEY` / `ant auth login` profile. *Leaning: env → profile →
   extension store, in that order.*
3. **Should `sema chat` be the default `sema` invocation with no args?**
   *Leaning: no — keep `sema` printing help; too surprising otherwise.*
4. **Packaging** — Textual as a required dependency vs. an
   `uv sync --extra chat` optional group. *Leaning: optional extra, so the MCP
   server stays lean.*
