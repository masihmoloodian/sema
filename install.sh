#!/bin/sh
# sema installer — https://github.com/masihmoloodian/sema
#
#   curl -fsSL https://raw.githubusercontent.com/masihmoloodian/sema/main/install.sh | sh
#
# What it does:
#   1. Ensures `uv` is available (bootstraps the official installer if missing).
#   2. Installs the `sema` binary in an isolated tool environment (uv tool install).
#   3. Detects which AI clients you have (Claude Code, Codex, opencode, Grok Build,
#      Cursor) and, if this is run inside an indexed project, registers sema with
#      each via `sema setup`.
#
# Nothing here needs sudo. Everything installs under your home directory.
#
# Opt out of any piece with env vars (they pass cleanly through `curl | sh`):
#   SEMA_SKIP_CLAUDE=1     don't register with Claude Code
#   SEMA_SKIP_CODEX=1      don't register with Codex
#   SEMA_SKIP_OPENCODE=1   don't register with opencode
#   SEMA_SKIP_GROK=1       don't register with Grok Build
#   SEMA_SKIP_CURSOR=1     don't register with Cursor
#   SEMA_NO_SETUP=1        install the binary only; skip all registration
#   SEMA_YES=1             assume "yes" to every prompt (non-interactive)
#   SEMA_PACKAGE=<spec>    install source instead of PyPI's sema-mcp — e.g. a
#                          local checkout path (for testing) or a pinned version
#
# Prefer not to pipe to sh? These are equivalent:
#   uv tool install sema-mcp      (or: pipx install sema-mcp)

set -eu

# ── output helpers ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); RED=$(printf '\033[31m')
  GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m'); RESET=$(printf '\033[0m')
else
  BOLD=; DIM=; RED=; GREEN=; YELLOW=; RESET=
fi
say()  { printf '%s\n' "$*"; }
info() { printf '%s→%s %s\n' "$DIM" "$RESET" "$*"; }
ok()   { printf '%s✔%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s⚠%s  %s\n' "$YELLOW" "$RESET" "$*"; }
err()  { printf '%s✗%s %s\n' "$RED" "$RESET" "$*" >&2; }
die()  { err "$*"; exit 1; }

# Read a yes/no answer even when stdin is the piped script — prompt from the TTY.
# Returns 0 for yes. Defaults to yes on SEMA_YES=1 or when there is no TTY.
confirm() {
  [ "${SEMA_YES:-}" = "1" ] && return 0
  [ -e /dev/tty ] || return 0   # non-interactive (CI, no terminal): default yes
  printf '%s [Y/n] ' "$1" > /dev/tty
  read -r _ans < /dev/tty || return 0
  case "$_ans" in
    n*|N*) return 1 ;;
    *)     return 0 ;;
  esac
}

have() { command -v "$1" >/dev/null 2>&1; }

say ""
say "${BOLD}sema installer${RESET}"
say ""

# ── 1. ensure uv ─────────────────────────────────────────────────────────────
# uv gives us an isolated install AND fetches a suitable Python if none exists,
# which is what makes this robust across the messy Python setups users have.
if have uv; then
  ok "uv already installed ($(uv --version 2>/dev/null || echo uv))"
else
  info "uv not found — installing it (astral.sh official installer)"
  if have curl; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif have wget; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    die "need curl or wget to install uv. Install one, or run: pipx install sema-mcp"
  fi
  # uv installs to ~/.local/bin (or $XDG_BIN_HOME) — make it visible to this shell.
  for d in "$HOME/.local/bin" "${XDG_BIN_HOME:-}" "$HOME/.cargo/bin"; do
    [ -n "$d" ] && [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac
  done
  export PATH
  have uv || die "uv installed but not on PATH. Open a new terminal and re-run, or run: pipx install sema-mcp"
  ok "uv installed"
fi

# ── 2. install sema ──────────────────────────────────────────────────────────
SEMA_PACKAGE="${SEMA_PACKAGE:-sema-mcp}"
info "installing $SEMA_PACKAGE"
uv tool install --upgrade --force "$SEMA_PACKAGE"
# Make sure uv's tool bin dir is on PATH for the rest of this script.
uv tool update-shell >/dev/null 2>&1 || true
# Prepend it even when it already appears later in PATH. A project virtualenv may
# otherwise shadow the binary we just installed, causing setup to register an old
# checkout instead of this fresh uv tool.
tool_bin=$(uv tool dir --bin 2>/dev/null || printf '%s' "$(uv tool dir 2>/dev/null)/../bin")
if [ -n "$tool_bin" ] && [ -d "$tool_bin" ]; then
  PATH="$tool_bin:$PATH"
fi
export PATH

if have sema; then
  ok "sema installed → $(command -v sema)"
else
  warn "sema installed but not yet on PATH — open a new terminal (or run: uv tool update-shell)"
fi

# ── 3. register with detected AI CLIs ────────────────────────────────────────
if [ "${SEMA_NO_SETUP:-}" = "1" ]; then
  say ""
  ok "Binary installed. Skipping registration (SEMA_NO_SETUP=1)."
else
  say ""
  say "${BOLD}Detected AI clients:${RESET}"
  found_any=0
  for tool in claude codex opencode grok; do
    if have "$tool"; then ok "$tool"; found_any=1; else info "$tool ${DIM}(not installed)${RESET}"; fi
  done
  # Cursor is a GUI editor, not a PATH CLI — its ~/.cursor dir is the presence signal.
  if [ -d "$HOME/.cursor" ] || have cursor; then ok "cursor"; found_any=1; else info "cursor ${DIM}(not installed)${RESET}"; fi

  if [ "$found_any" = "0" ]; then
    say ""
    warn "No AI clients found. Install any of these, then run ${BOLD}sema setup${RESET} in your project:"
    say "    ${DIM}Claude Code${RESET}  https://github.com/anthropics/claude-code"
    say "    ${DIM}Codex${RESET}        https://github.com/openai/codex"
    say "    ${DIM}opencode${RESET}     https://opencode.ai"
    say "    ${DIM}Grok Build${RESET}   https://x.ai/cli"
    say "    ${DIM}Cursor${RESET}       https://cursor.com"
  elif [ -d ".sema/index" ]; then
    say ""
    if confirm "Register sema with the detected CLIs for this project?"; then
      sema setup || warn "Registration reported an issue — run 'sema doctor' to diagnose."
    else
      info "Skipped. Run ${BOLD}sema setup${RESET} here whenever you're ready."
    fi
  else
    say ""
    info "No index in the current directory yet. In each project you want sema for, run:"
    say "    ${BOLD}sema index .${RESET}   ${DIM}# build the semantic index${RESET}"
    say "    ${BOLD}sema setup${RESET}     ${DIM}# register with your AI CLIs${RESET}"
  fi
fi

say ""
ok "${BOLD}Done.${RESET} Try ${BOLD}sema --help${RESET} or ${BOLD}sema doctor${RESET}."
say ""
