# FAQ

**Why is Claude Code / Codex so slow at the start of a session?**
Both Claude Code and Codex start every session cold — no persistent memory of your codebase. The AI has to run `find`, read files, and explore directories to build context before it can help. On a project with 50+ files this costs thousands of tokens and tens of seconds before it writes a single line of code. Sema solves this by pre-indexing your codebase so the AI can search instead of explore.

**Why does Claude Code / Codex use so many tokens?**
The main culprit is file navigation. Without an index, the AI reads entire files to find the one function it needs. On a 1,000-file TypeScript project, a single "how does auth work?" question can consume 10,000+ tokens just in file reads. Sema's `search_code()` returns only the relevant signatures (~180 tokens), and `get_code()` fetches only the exact function body needed (~300–500 tokens each).

**How do I speed up Claude Code or Codex on a large codebase?**
Install sema, run `sema index .` once in your project, then `sema setup` to register with every detected CLI (or `sema init --claude` / `sema init --codex` for one). Add a `CLAUDE.md` (Claude Code) or `AGENTS.md` (Codex) file that tells the AI to call `search_code()` first. From that point on it searches your index instead of reading files — typically 5–10× fewer tool calls per question.

**Which platforms does sema run on?**
macOS and Linux. The installer is a POSIX shell script, and sema needs Python 3.11+. Windows isn't supported.

**Does sema send my code to any external service?**
No. Sema runs entirely on your machine. The embedding model (`all-MiniLM-L6-v2`) is downloaded once (~80MB) and cached locally. No API keys, no internet connection required after setup, no data leaves your machine.

**What is an MCP server?**
MCP (Model Context Protocol) is the standard that Claude Code and Codex use to call external tools. Sema registers itself as a local MCP server — your AI assistant connects to it over stdio and gains new tools: `search_code`, `get_code`, `find_usages`, `repo_map`, `explain_file`, and `impact_analysis`. These tools give the AI structured access to your codebase without reading raw files.

**Does sema work with TypeScript, Python, Go, and other languages?**
Yes. Sema has full AST-aware parsers for TypeScript, JavaScript, Python, and Go (symbol-level granularity). All other languages and formats — including Rust, Java, Ruby, Markdown, JSON, YAML, CSS, SQL, and more — are indexed via text chunking, which makes them searchable even without symbol extraction.

---

## Limitations

Known limitations in the current version:

- **AST-aware parsers for TypeScript, JavaScript, Python, Go only** — Ruby, Rust, Java, C#, and others fall back to generic text chunking (searchable, but no symbol-level granularity)
- **Call graph is name-based** — calls are matched by symbol name, not by resolved reference; two functions with the same name in different files are indistinguishable to the graph
- **`find_usages` is approximate** — uses semantic similarity, not AST-level reference tracking; may miss some call sites
- **Multi-project is discovery-based** — one server can serve many projects via `sema init --root <dir>` ([details](multi-project.md)), but projects must live under a scanned root and each keeps a separate index (no cross-project symbol search yet)
- **Model fixed at index time** — changing the embedding model requires a full re-index
- **macOS and Linux only** — no Windows support; primarily tested on Apple Silicon (macOS)

---

## Disclaimer

> sema is an **experimental project** built to explore semantic code indexing for AI-assisted development.
>
> - The index format, CLI interface, and MCP tool signatures **may change** between versions without notice
> - There is **no guarantee of correctness** — sema may miss chunks, return stale results, or fail on unusual code patterns
> - The embedding model (`all-MiniLM-L6-v2`) runs locally and is **not fine-tuned for code** — results are based on general semantic similarity
> - sema is developed and tested primarily on Apple Silicon (macOS) against one codebase type (NestJS + Next.js TypeScript monorepo); your results may vary
> - **Do not rely on sema for security-sensitive analysis** — it is a navigation aid, not a code analysis tool
>
> Use it, break it, improve it. That's the point.
