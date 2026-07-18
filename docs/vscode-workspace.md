# VS Code workspace setup

A VS Code workspace (`.code-workspace` file) groups multiple project folders under one window. Sema supports this with two extra flags. New to sema? See [Why sema](why-sema.md); for the in-editor search and chat panels, see the [VS Code extension](../vscode-extension/README.md).

## Step-by-step

**1. Index only the workspace folders — not the whole parent directory**

```bash
cd /path/to/workspace          # the directory containing your .code-workspace file
sema index . --workspace my-project.code-workspace
```

The `--workspace` flag reads the `.code-workspace` file and indexes only the listed folders. Without it, `sema index .` would walk everything in the parent directory including unrelated repos.

Paths in the index include the project folder name (`backend/src/auth.ts`, not just `src/auth.ts`), so results are always unambiguous across projects.

**2. Register with your AI CLI**

```bash
sema setup            # registers every detected client (Claude Code, Codex, opencode, Grok Build, Cursor)
# or, Claude Code only:
sema init --claude
```

For Claude Code this runs `claude mcp add sema -s user` under the hood, which registers sema at the user level — visible in every project and workspace, not just one folder.

**3. Add CLAUDE.md to each project folder**

Claude Code anchors CLAUDE.md to each project's git root — not to the workspace parent directory. A CLAUDE.md at `/workspace/CLAUDE.md` is invisible to Claude when you're working in `/workspace/backend/`.

Create a CLAUDE.md in each project folder:

```bash
# Run from the workspace root
for dir in backend frontend bo; do
  cat > $dir/CLAUDE.md << 'EOF'
## Codebase navigation

This project is indexed by sema. Use sema tools to locate code before reading files.

| Goal | Tool |
|---|---|
| Find a function, class, or method | `search_code("natural language description")` |
| Check if something already exists before writing it | `check_reuse("what you're about to build")` |
| Read the full body of a known symbol | `get_code("exactSymbolName")` |
| Find where a symbol is called or referenced | `find_usages("symbolName")` |
| Understand what a file exports | `explain_file("path/to/file.ts")` |
| Understand the overall architecture | `repo_map()` |
| See what a function calls and what calls it | `impact_analysis("symbolName")` |

**Always call `search_code()` before using Bash find/grep or Read to explore. Call `check_reuse()` before writing a new utility and reuse any existing match. Before changing a function, call `impact_analysis()` to understand the blast radius.**
EOF
done
```

**4. Watch for changes across all workspace folders**

```bash
sema watch . --workspace my-project.code-workspace
```

A file save in any project triggers incremental re-indexing automatically.
