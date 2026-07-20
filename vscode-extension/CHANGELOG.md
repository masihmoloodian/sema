# Changelog

All notable changes to the **sema** VS Code extension are documented here.
This project adheres to [Semantic Versioning](https://semver.org).

## [0.10.0]

### Added
- **DevOps guard tools available in "Reuse a local CLI" mode.** Claude Code,
  Codex, Grok Build, and Cursor now expose `devops_plan`/`devops_run`/
  `devops_approve`/`devops_deny`/`devops_pending`/`devops_log` — an
  analyze-first gate for `kubectl`/Terraform/AWS CLI/Helm that classifies
  every command as safe, needs-approval, or prohibited before anything
  executes, with secret redaction on both the command and its output. These
  tools live in the `sema-mcp` MCP server (`sema-mcp` 0.9.0+, see
  [docs/devops-guard.md](../docs/devops-guard.md)), so no extension code
  changed — they just show up once the underlying `sema` install is current.
  Not yet available in "Bring an API key" mode, which uses its own fixed
  tool list rather than MCP discovery.

## [0.9.0]

### Added
- **Update sema from the extension.** A new **"Update sema"** action — in the Manage panel
  and the command palette (`sema: Update sema`) — upgrades the sema CLI / MCP server
  (`sema-mcp`) to its latest release via `uv tool upgrade sema-mcp` (or `pipx upgrade`),
  matching how the one-liner installs it. Reload VS Code afterward to load it. Runs the
  package upgrade directly, so it works regardless of the installed sema version.

### Changed
- **Update agent CLIs** now re-runs each agent's official install script (`curl … | sh`)
  instead of the CLI's own self-updater, which errored for some install methods.

## [0.8.0]

### Added
- **"sema: Open Chat" command.** Reveal the chat sidebar from the command palette
  (`Cmd/Ctrl+Shift+P` → **sema: Open Chat**), like Codex's "Open Codex Sidebar". Works
  even before the panel has been opened.

### Fixed
- **Stop button now interrupts Codex Agent runs that are still starting up.** In Agent +
  Approval mode, hitting Stop while the Codex app-server was in setup (`initialize` /
  `thread-resume` / `turn-start`) left the request unsettled, so the run hung and the
  spinner never stopped — most visible on a second message, which does a slower
  `thread/resume`. The abort now rejects in-flight requests so the turn ends immediately.

## [0.7.0]

### Added
- **Cursor as a local CLI chat provider.** Drives Cursor's headless agent (`cursor-agent`)
  in print mode (`--output-format stream-json`), streaming the answer, tool activity, and
  session resume, with **Sign in / Sign out** in the model menu like the other local CLIs.
  Pick **Cursor (local)** in the provider picker; set `sema.chat.cursorPath` if
  `cursor-agent` isn't on VS Code's PATH. Models come from `cursor-agent --list-models`
  (`auto` is Cursor's own router). Two deliberate gaps mirror the CLI: reasoning is
  suppressed in print mode, and the stream reports no token usage or cost.
- **Register sema with Cursor from the Manage panel.** The Manage panel shows Cursor's
  registration status alongside Claude Code, Codex, and Grok Build, with one-click
  **Register with Cursor** / **Unregister Cursor** (writes `.cursor/mcp.json` in the
  workspace, which Cursor reads), so a project you also open in Cursor gets sema's tools
  there too. This is separate from the chat provider above: the provider chats *with*
  Cursor's agent; this registers sema's tools *inside* Cursor.

## [0.6.0]

### Added
- **Grok Build (xAI) as a local CLI provider.** Drives `grok` in headless mode
  (`--output-format streaming-json`), streaming answers, reasoning, session resume, and
  token usage, with **Sign in / Sign out** in the model menu like the other local CLIs.
  Set `sema.chat.grokPath` if `grok` isn't on VS Code's PATH. Register sema with it from
  the Manage panel, or with `sema init --grok`; `sema setup` and `sema update` detect it
  too. Models come from what `grok models` actually reports (`grok-4.5`), not from xAI's
  docs, which still name a `grok-build` model the live catalog doesn't list; the header
  resolves what a turn really billed (e.g. `grok-4.5-build-free`). One deliberate gap:
  Grok's stream carries no tool-call event, so Agent mode shows no per-tool activity.
- **A header notice when the active provider can't answer yet.** Previously the only hint
  was a dot on the model pill, which you had to open the menu to understand — so the first
  feedback was a failed turn. Now the header shows **Sign in** or **Set API key** for the
  active provider, naming it and acting as the shortcut to fix it. It stays hidden until
  the sign-in check has actually run, so a signed-in user never sees a false warning.

### Fixed
- **Your prompt reaches the model as your prompt.** Sema's workflow and retrieved context
  were concatenated in front of the user's turn for the local CLIs, so the model read them
  as if the user had typed them — a bare "hi" came back as "I'll use sema's semantic index
  first…" instead of a greeting. They now travel in each CLI's own system channel:
  `claude --append-system-prompt` and `grok --rules`, matching the split the Claude Agent
  SDK path already made. Codex and opencode expose no such flag (Codex's positional *is*
  its instructions), so context is still inlined there, now with an explicit end-of-context
  fence before the request. The API providers were already correct — Anthropic passes
  `system`, and the OpenAI-compatible ones send a `system` role message. Nothing about what
  sema injects changed, only where it goes: index context still reaches every provider.
- **Signing in no longer needs a window reload to register.** Sign-in state was only
  checked when the panel first loaded or the provider changed, and the existing triggers
  both missed the normal case: the CLI's login leaves its terminal open at the shell
  prompt, and the panel stays visible throughout, so neither the terminal-close nor the
  visibility trigger fired. The panel now polls after launching a login and updates
  itself, and re-checks when the window regains focus if it believes the provider is
  signed out — which also catches a sign-in done in your own terminal. Both are bounded:
  polling stops the moment sign-in lands, and the focus check is throttled and never runs
  once signed in. Applies to every CLI provider, not just Grok.
- **File attachments across every provider.** Attach images, PDFs, and text files to a
  chat turn with **📎**, by pasting a screenshot, by dropping a file on the composer, or
  from the Explorer context menu (`sema: Attach file to chat`). Each provider receives
  the file in its native form — Anthropic `image`/`document` content blocks, OpenAI
  `image_url`/`file` content parts, and real files on disk for the local CLIs (`codex -i`,
  `opencode -f`, and an allow-listed path for Claude Code's Read tool). Text files are
  inlined into the prompt, so they work on every model including text-only ones.
- Attachment support is tracked **per model**, not per provider — the gateways (opencode,
  OpenRouter, Together) front both vision and text-only models, so a single per-provider
  flag would promise vision a model doesn't have. Attaching a type the selected model
  can't read is refused up front with an explanation; attachments already in the history
  degrade to a text placeholder when you switch to a model that can't read them, so
  switching provider mid-conversation no longer breaks the turn.
- Attached text files are covered by the **redact** toggle. Images and PDFs can't be
  scrubbed, so redact-on refuses them rather than sending unscrubbable bytes under a
  "redacted" banner.
- First unit tests for the extension (`npm test`, via `node --test`).

### Changed
- **Reasoning effort is now per-CLI, and only where it exists.** The picker appears only
  for Claude Code and Codex — the two providers whose CLI takes an effort argument — and
  each now offers exactly what its own CLI accepts, verified by running every level
  end-to-end against both CLIs:
  - **Claude Code** (`--effort`): low / medium / high / xhigh / **max**
  - **Codex** (`-c model_reasoning_effort=`): **none** / low / medium / high / xhigh

  The sets are not interchangeable: Codex fails to parse Claude's `max` ("unknown variant
  `max`"), and Claude warns and falls back to its default on Codex's `minimal`. Codex's
  `none` was previously missing and does work. Codex's `minimal` is deliberately *not*
  offered — its parser accepts it, but a real run returns HTTP 400 ("The following tools
  cannot be used with reasoning.effort 'minimal': image_gen, web_search"), so it could
  never succeed. Anthropic, OpenAI, DeepSeek, OpenRouter,
  Together, and opencode no longer declare an effort at all — it's a CLI flag, not an API
  parameter — so they hide the picker and are never sent one. The stored effort is
  validated against the active provider, so switching from Claude Code (`max`) to Codex
  falls back to `default` instead of erroring.

### Fixed
- Redaction rebuilt each turn as `{role, content}`, dropping any per-turn metadata; it
  now preserves the whole message, so attachments survive with the **redact** toggle on.
- **The resolved "Default (…)" model is no longer stale after a failed run.** The model a
  CLI resolves for `Default` was only read on a successful exit, so a failing run left the
  picker showing whatever an earlier run had resolved — e.g. it read `Default (gpt-5.5)`
  while the error said `The 'gpt-5.6-terra' model requires a newer version of Codex`. The
  model is now resolved regardless of exit status, so the picker and the error agree.
  (Note `Default` sends no `-m`: Codex picks server-side and can choose a model newer than
  your CLI. Select an explicit model — e.g. GPT-5.5 — to pin it.)
- **Codex failures now report the real error.** `CodexProvider` ignored the `turn.failed`
  event carrying the upstream message, so a failed run surfaced whatever landed last on
  stderr instead — typically an unrelated `codex_models_manager::cache: failed to load
  models cache: unknown variant 'max'` warning, which hid the actionable cause (e.g.
  "The 'gpt-5.6-terra' model requires a newer version of Codex. Please upgrade…"). Codex
  nests the upstream error as a JSON string inside `error.message`; that is now unwrapped
  and shown.
- **opencode attachment support is now per-model.** opencode is a gateway to ~55 models,
  so claiming image support for all of them meant attaching a screenshot on the
  **Default** model produced a silent "I cannot view images" reply — Default resolves to
  opencode's own configured model, commonly the free, text-only `opencode/big-pickle`.
  Vision models (Claude, Gemini, GPT) declare image/PDF support; text-only ones
  (DeepSeek, GLM, Qwen, Kimi, MiniMax) declare text; **Default** is treated as text-only,
  because opencode reports no resolved model id in its JSON stream. Attaching an image
  on a model that can't read it is now refused up front, as it already was for
  OpenRouter and Together.
- Staged attachments now keep a canonical file extension (`<id>.png`), derived from the
  sniffed media type rather than the supplied filename, so a staged file identifies
  itself to tools and in activity traces.

## [0.5.0]

### Added
- **Focused composer controls.** Attachments, Sema index/redaction/maintenance,
  Ask/Plan/Agent mode, provider/model/effort, and agent permissions now have separate
  controls instead of sharing the attachment and settings menus.
- **Runtime CLI effort discovery.** The extension reads effort levels from the exact
  configured Claude Code and Codex executables, including Codex's model-specific
  catalog, and safely falls back for older Codex releases.
- **Refreshed models with friendly display names.** The picker now shows readable names
  (e.g. "Opus 4.8") instead of raw ids, with optional `<optgroup>` sections supported in
  the schema. Model lists were updated: Claude Code (Opus 4.8 / Fable 5 / Sonnet 5 /
  Haiku 4.5 via CLI aliases), Codex (gpt-5.5 / gpt-5.4 / gpt-5.4-mini — verified against
  `codex debug models`), OpenRouter, and Together AI. Custom ids still work via
  "+ custom id…".
- **Claude (Anthropic) now has a real Agent & Plan mode**, on par with the
  OpenAI-compatible providers. The Anthropic provider used to be chat-only — in Agent
  mode it could only *describe* changes. It now runs the same agentic tool loop over the
  Messages API: it explores, edits, and runs commands directly in your workspace, feeding
  each tool result back and iterating until done (Plan mode gets the read-only subset).
  It drives the shared toolset — search_code, get_code, grep, glob, list_directory,
  read_file, write_file, edit_file, delete_file, run_command — the same engine the
  OpenAI/DeepSeek/OpenRouter/Together agents use, so behavior is consistent across every
  API provider and inspired by the Claude Code / Codex / opencode CLIs.
- **Codex (local) now shows its resolved default model.** Just like Claude Code, the
  model picker displays `default (<model>)` for Codex once the first turn resolves it
  (e.g. `default (gpt-5.5)`). Codex's `--json` stream doesn't report the model, so it's
  read from the session rollout log Codex writes, then remembered per provider.

### Fixed
- Stored reasoning effort is validated against the detected CLI and selected model
  before every run. A stale `xhigh` choice can no longer break an older Codex binary,
  and Codex authentication remains available when its config contains a newer level.
- **opencode (local) now runs in your workspace.** In Agent mode opencode was
  operating from a temp directory — creating/editing files under `$TMPDIR/opencode`
  instead of the open project — because its server-based `run` ignores the spawned
  working directory. sema now passes `--dir <workspace>` so its file and bash tools
  act on the current VS Code project, matching Claude Code and Codex.
- **Reasoning-effort levels now match the CLIs.** Codex no longer offers `minimal`
  (not supported by the current gpt-5.x models) — its levels are low / medium / high /
  extra high. Claude Code remains low / medium / high / extra high / max. The `xhigh`
  level now reads as **extra high** in the picker (the value sent to the CLI is
  unchanged).

## [0.4.0]

### Added
- **Persistent chat history & sessions (like Claude Code).** Conversations are now
  saved automatically and survive VS Code restarts. A new **history** button (clock
  icon) in the chat header opens a browser of your past chats — each showing its
  title (taken from the first message), provider, message count, and when it was
  last active — with search, click-to-open, hover-to-delete, and **+ New chat**.
  The full transcript and running token/cost usage are restored when you reopen a
  chat, and the extension reopens your last-active chat on launch.
- **Model- and provider-agnostic transcripts.** History is stored independently of
  the model, so you can switch provider or model mid-conversation and keep the same
  thread; the CLI resume handle (Claude Code / Codex) is remembered per session and
  reused only when the provider matches.
- **Per-workspace storage.** Sessions are partitioned by repository (stored under
  the extension's global storage), so each project keeps its own chat history.
  "New chat" now saves the current conversation to history instead of discarding it.

## [0.3.0]

### Added
- **Markdown rendering in chat replies.** Assistant messages now render Markdown —
  tables (horizontally scrollable), **bold**/*italic*, headings, ordered/unordered
  lists, links (open in the browser), and inline/fenced code — instead of showing
  raw `**` and pipe tables. Streaming-tolerant: partial markdown renders as it
  arrives and settles once complete.
- **PII redaction mode (opt-in).** A new **redact** toggle scrubs sensitive data
  from everything sent to the model — a fast, offline regex layer for secrets and
  structured PII (emails, API keys/tokens, credit cards, SSNs, phone numbers), plus
  an optional local spaCy NER pass (the `sema redact` command / `sema-mcp[pii]`
  extra) for person and location names. Each turn shows what was redacted; it
  covers the prompt and injected index context. Redaction that runs patterns-only
  (model not installed) says so once.
- **Redesigned chat panel — a Cursor/Claude-Code-style UX.** A branded header with
  the sema logo, a centered logo empty state, right-aligned user bubbles with plain
  flowing assistant text (tool activity inline), and a rounded composer with an
  auto-growing input and the provider/model/mode/effort/index controls tucked into
  a bottom toolbar.
- **Agentic API providers with a rich toolset.** In **Agent** mode, OpenAI,
  DeepSeek, and OpenRouter now run a full tool-use loop with `search_code` (sema's
  semantic index), `get_code`, `grep`, `glob`, `list_directory`, `read_file`,
  `write_file`, `edit_file` (surgical string replacement), `delete_file`, and
  `run_command` — so they investigate, create/modify files, and run commands like
  the local Claude Code / Codex providers instead of only describing the change.
  **Plan** mode gives the same models the read-only subset (search/read tools) to
  investigate the codebase before proposing a plan. Tool calls show as activity,
  file paths are confined to the workspace root, Plan mode refuses mutating tools,
  and the loop runs up to 50 steps. Requires a model that supports function calling.
- **Three new chat providers: DeepSeek, OpenRouter, and Together AI.** All are
  OpenAI-compatible, so they share the existing OpenAI streaming transport — bring
  your own key (stored in SecretStorage) and pick a model like any other API provider.
  - **DeepSeek** — `deepseek-v4-flash` / `deepseek-v4-pro`, with cache-aware cost
    **estimated** from public list prices.
  - **OpenRouter** — one gateway to models from many providers (`provider/model`
    slugs); usage carries the **real per-call cost**, shown as-is in Manage. The
    curated model list plus **"+ custom id…"** reaches the full catalogue.
  - **Together AI** — open models (Llama, DeepSeek, Qwen, gpt-oss, …) via `org/Model`
    slugs; cost **estimated** from public list prices. Curated list plus custom id.
- **opencode as a local provider.** Chat and agent through the open-source `opencode`
  CLI (`opencode run --format json`) alongside Claude Code and Codex. Ask/Plan use its
  read-only `plan` agent, Agent uses `build` with auto-approved permissions; sign in
  with `opencode auth login` and pick a `provider/model` (or your opencode default).
- **Provider-aware custom model ids.** The **"+ custom id…"** entry (available for
  every provider) now shows format-specific examples — e.g. OpenRouter's
  `provider/model` slugs — so any model beyond the curated lists is easy to enter.
- **Selected model id is always shown** in the top bar for every provider (and a
  `→ id` marks the model a local CLI actually used); the model dropdown lists ids
  for all providers.
- **The index control is now a toggle switch** rather than a text button.
- **Retrieved context is visible.** With the index on, each turn shows a “sema
  context” block listing the snippets sema pulled into the prompt — click any entry
  to open that file at the line.
- **Plan mode.** A third chat mode alongside Ask and Agent: it investigates
  read-only and proposes a step-by-step plan without editing. Claude Code uses its
  native `--permission-mode plan`; Codex and the API providers are read-only with a
  planning instruction.
- **Actual served model shown.** API providers now report the model the API
  actually served (e.g. OpenRouter's routed slug) into the top bar as `→ id` — the
  authoritative id, independent of what the model claims about itself in its reply.
- **Clearer errors.** Rate-limit (429) failures now explain the cause (e.g. free
  `:free` model limits) instead of showing a raw "Provider returned error", and a
  failed turn no longer leaves a stray empty "(no output)" bubble next to the error.

### Changed
- OpenAI, DeepSeek, and OpenRouter now run through a shared
  `OpenAICompatibleProvider` transport; OpenAI additionally gained cost estimates
  from public list prices.
- Refreshed the OpenAI model list to the current **GPT-5.6** family — `gpt-5.6-sol`
  (default), `gpt-5.6-terra`, `gpt-5.6-luna` — with up-to-date pricing, replacing
  the older `gpt-4o` / `gpt-4.1` defaults. Anthropic model ids were already current
  (`claude-opus-4-8` default).

## [0.2.0]

### Added
- **Sign in from the panel.** A **Log in** button for the Claude Code and Codex
  providers runs each CLI's own browser sign-in (`claude auth login` /
  `codex login`) in a terminal — no OAuth is reimplemented, so credentials are
  stored where the CLIs expect them. The button shows your live sign-in state and
  supports sign-out, and when a message fails because you're not signed in, a
  one-click **Log in** prompt appears.

## [0.1.0]

### Added
- Codebase-aware **chat panel** with four providers — Claude Code and Codex
  (local CLIs, no API key), plus the Anthropic and OpenAI APIs — with Ask/Agent
  modes, a reasoning-**effort** selector, streamed thinking and tool activity,
  and per-session memory.
- Optional **sema index** toggle to inject retrieved code as context (RAG).
- **Manage** view: index status, one-click re-index / register / watch / doctor,
  and live token usage + estimated cost for the session.
- **Search** and **Reuse** commands, and a status-bar index-freshness indicator.
