# MCP tools

These are the tools your AI assistant calls during a session. You never call them directly.

| Tool | Input | Returns | Tokens |
|---|---|---|---|
| `search_code(query)` | Natural language | Matching function/class signatures + file locations | ~100–200 |
| `get_code(symbol)` | Exact symbol name | Full source body — all implementations if name appears in multiple files | ~200–500 |
| `repo_map()` | — | Compressed architecture overview: files + exported symbols | ~400–800 |
| `find_usages(symbol)` | Symbol name | Call sites and references (signatures only) | ~150–300 |
| `explain_file(path)` | Relative file path | File summary: exports, classes, functions — no source code | ~100–200 |
| `impact_analysis(symbol)` | Symbol name | Call graph: what it calls + what calls it, up to 3 levels deep | ~100–400 |

## `impact_analysis` — call graph

`impact_analysis` answers two questions at once: *what does this function call?* and *what calls this function?* — traversed up to `depth` levels in both directions. Use it before changing a function to understand the blast radius.

```
impact_analysis("validateToken", depth=2)

Impact analysis for 'validateToken':

Calls (1 symbols, 1 level(s) deep):
  Level 1:
    → atob

Called by (3 callers, 1 level(s) up):
  Level 1:
    src/auth/jwt.ts::refreshToken  [line 29]
      function: refreshToken(userId: string, token: string): Promise<TokenPair>
    src/auth/middleware.ts::requireAuth  [line 3]
      function: requireAuth(req: any, res: any, next: any): void
    src/auth/middleware.ts::optionalAuth  [line 18]
      function: optionalAuth(req: any, res: any, next: any): void
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `symbol_name` | string | required | Exact function or method name |
| `depth` | int | 2 | Levels to traverse in both directions (1–3) |
| `file_path` | string | — | Narrow to a specific file when multiple files define the same symbol name |

**How it works:**

At index time, each function's AST is walked to extract every call site. Calls are stored as qualified names where possible (`jwt.verify`, `uuid.uuid4`) or bare names otherwise (`validateToken`). Common language builtins (`len`, `console.log`, `fmt.Printf`) are filtered out so the graph only shows your own symbols.

At query time the call graph is traversed breadth-first in both directions:
- **Callees** — what `symbol` calls, then what those call (downward, up to `depth` levels)
- **Callers** — what calls `symbol`, then what calls those callers (upward, up to `depth` levels)

Caller lookups are backed by an in-memory inverted index built on first use — subsequent calls are sub-millisecond regardless of codebase size. Qualified-name queries also match suffix: searching for `verify` returns callers that recorded `jwt.verify`.

**When to use it:**

- Before refactoring a function — see everything that will break
- Before changing a function signature — find all callers at once
- When debugging — trace how a call propagates through your stack
- Code review — quickly understand the scope of a change
