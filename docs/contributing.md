# Contributing

Contributions are welcome. sema is intentionally small — each module has a single responsibility and the test suite makes it straightforward to extend.

This guide covers the Python core (indexer, store, MCP server, CLI). The repo also ships a separate [VS Code extension](../vscode-extension/README.md) with its own setup under `vscode-extension/`. See the [architecture](architecture.md) overview for how the pieces fit together.

## Development setup

sema runs on macOS and Linux and requires Python 3.11+ (3.12 recommended). The
project uses [uv](https://docs.astral.sh/uv/) for environment and dependency
management.

```bash
git clone https://github.com/get-sema/sema.git
cd sema

# Create the virtual environment
uv venv --python 3.12 .venv

# Install with dev dependencies (or: uv sync --all-extras)
uv pip install -e ".[dev]"

# Run tests
uv run pytest tests/ -v

# Run linter
uv run ruff check sema/
```

## Adding a new language

The parser is a registry. You can register a new format without touching any core file.

**Option A — generic text baseline (no new deps):**

```python
from sema.indexer.parser import register
from sema.indexer.languages.generic import extract_chunks

register([".rb", ".java", ".rs"], extract_chunks)
```

Call this before `index_project()` runs (e.g. in a plugin loaded at startup).

**Option B — AST-aware with tree-sitter (recommended for code):**

1. Create `sema/indexer/languages/yourlang.py`
   - Implement `extract_chunks(source: str, file_path: str) -> list[Chunk]`
   - Use `tree-sitter` to parse the AST
   - Return one `Chunk` per function, class, method, interface
2. Call `register([".ext"], yourlang.extract_chunks)` — or add it to `_register_builtins()` in `parser.py`
3. Add fixture source files to `tests/fixtures/example-repo/`
4. Add test cases to `tests/test_parser.py`

The embedding, storage, and search pipeline are fully language-agnostic — you only need to write the parser.

## Good first contributions

- Add support for a new language (Rust, Java, Ruby, C#)
- Improve `find_usages` with a grep-based exact match fallback
- Add `--verbose` output to `sema index` showing each file as it's processed
- Test sema across Linux distributions and report/fix issues
- Improve search quality for a specific code pattern you've found lacking

## Submitting changes

1. Fork the repo
2. Create a branch: `git checkout -b feature/your-feature`
3. Make your changes and add tests
4. Run `uv run pytest tests/ -v` and `uv run ruff check sema/` — both must pass
5. Open a pull request with a clear description of what and why
