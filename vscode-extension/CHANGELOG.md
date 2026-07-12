# Changelog

All notable changes to the **sema** VS Code extension are documented here.
This project adheres to [Semantic Versioning](https://semver.org).

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
