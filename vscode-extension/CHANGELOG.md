# Changelog

All notable changes to the **sema** VS Code extension are documented here.
This project adheres to [Semantic Versioning](https://semver.org).

## [0.5.0]

### Added
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
