# Changelog

All notable changes to the **sema** VS Code extension are documented here.
This project adheres to [Semantic Versioning](https://semver.org).

## [0.3.0]

### Added
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
- **Two new chat providers: DeepSeek and OpenRouter.** Both are OpenAI-compatible,
  so they share the existing OpenAI streaming transport — bring your own key (stored
  in SecretStorage) and pick a model like any other API provider.
  - **DeepSeek** — `deepseek-v4-flash` / `deepseek-v4-pro`, with cache-aware cost
    **estimated** from public list prices.
  - **OpenRouter** — one gateway to models from many providers (`provider/model`
    slugs); usage carries the **real per-call cost**, shown as-is in Manage. The
    curated model list plus **"+ custom id…"** reaches the full catalogue.
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
