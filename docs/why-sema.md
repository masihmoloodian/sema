# Why sema

## Love Cursor? You'll love sema.

**Cursor is great.** It's also a subscription, and the pricing and the walled
garden aren't for everyone.

**So you buy Claude Code or Codex instead.** Also great — arguably better at the
actual work. But then:

- You **can't switch models mid-session.** Started in Claude and want Codex to look
  at it? New session, cold context, start over.
- You're **locked to one provider.** Maybe you'd rather route through
  **OpenRouter** or **Together AI**, or hit **OpenAI** and **Anthropic** directly
  with your own keys.
- They're **slower than Cursor at finding things.** Every session starts cold —
  `find`, `grep`, read a dozen files — burning **10,000–25,000 tokens just
  navigating** before answering anything.

Sema goes after both problems, and you can take either half on its own.

## The reading half — the indexer

Every Claude Code and Codex session starts with no memory of your codebase. On a
large project, your assistant burns thousands of tokens *navigating* — running
`find`, reading whole files, rebuilding a mental model from scratch — before it
can help with anything.

Sema gives it a search index instead. Rather than reading a dozen files to answer
*"how does auth work?"*, the assistant runs one `search_code()` and fetches only
the exact function bodies it needs — typically **4–11× fewer tokens**. Index once.
Your AI searches forever.

`search_code()` returns signatures only (~150 tokens), never whole files.
`get_code()` fetches full bodies on demand. See [benchmarks](benchmarks.md) for
measured savings on real open-source repos.

**This works without the extension.** `sema setup` registers an MCP server with
Claude Code, Codex, opencode, Grok Build, and Cursor — no editor changes needed.

## The writing half — the reuse guard

That's the reading half of the token bill. Sema goes after the writing half too.

Before your assistant adds a new helper, `check_reuse()` searches the index for an
existing one and answers **reuse / review / safe-to-build** — so it extends what's
already there instead of shipping a fourth function that does the same thing.
**98% reuse-vs-build accuracy** on a [50-example eval](benchmarks.md).

## The lock-in half — the extension

The [VS Code extension](../vscode-extension/README.md) is a Cursor-style chat *and*
agent that reads, edits, and runs your repo — with **nine engines in one
conversation**:

- **Local CLIs** — Claude Code, Codex, opencode, Grok Build. Reuse your existing login; no API
  key.
- **APIs** — Anthropic, OpenAI, DeepSeek, OpenRouter, Together AI. Bring your own
  key.

Switch **provider and model between turns**, in the same thread. Plan it with
Claude Code, hand it to Codex to build, review the diff with a cheap OpenRouter
model — no new session, no lost context.

**This works without the index.** Install the extension purely for multi-provider
chat if that's all you want.

## Privacy

**The index never leaves your machine.** Parsing and embedding run fully offline —
local SBERT (`all-MiniLM-L6-v2`, ~80MB, cached once), no API keys, no network.

The chat and agent talk to whichever model *you* point them at, with opt-in PII
redaction before anything is sent. Use the local CLI providers and nothing goes
anywhere your existing subscription doesn't already go.

## By the numbers

| | |
|---|---|
| **4–11×** | fewer tokens per question |
| **98%** | reuse-vs-build accuracy ([50-example eval](benchmarks.md)) |
| **~150** | tokens per search — signatures, not whole files |
| **0** | code that leaves your machine |

---

Next: [Installation](installation.md) · [Architecture](architecture.md) · [Benchmarks](benchmarks.md)
