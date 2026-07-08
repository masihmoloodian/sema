# Supported languages

sema has two levels of indexing support:

## AST-aware — full symbol extraction

These parsers use tree-sitter to extract individual functions, classes, and methods with proper signatures. `search_code` and `get_code` work at symbol granularity.

| Language | Extensions |
|---|---|
| TypeScript / JavaScript | `.ts` `.tsx` `.js` `.jsx` |
| Python | `.py` |
| Go | `.go` |

## Text-aware — semantic section chunking

These files are split into ~50-line sections and embedded as prose. Content is fully searchable — sema understands what a `config.yaml` or `README.md` says — but there are no named symbols to `get_code()` by.

| Type | Extensions / filenames |
|---|---|
| Markdown | `.md` `.mdx` — split by headings |
| Config | `.json` `.yaml` `.yml` `.toml` `.ini` |
| Styles | `.css` `.scss` |
| Shell | `.sh` `.bash` |
| Data / query | `.sql` `.graphql` `.xml` |
| Dotfiles | `.env` `.gitignore` `.dockerignore` `.envrc` |
| Project files | `Makefile` `Dockerfile` `Jenkinsfile` |

## Adding support for a new language

The parser is a registry — adding a new language requires no changes to core code:

```python
from sema.indexer.parser import register
from my_rust_parser import extract_chunks   # any callable (source, file_path) -> list[Chunk]

register([".rs"], extract_chunks)
```

For languages without a dedicated tree-sitter grammar, the generic text chunker works as a baseline:

```python
from sema.indexer.parser import register
from sema.indexer.languages.generic import extract_chunks

register([".rb", ".java", ".kt", ".swift", ".cs", ".php"], extract_chunks)
```

This indexes the raw source text semantically — not symbol-level, but better than nothing.

See [Contributing](contributing.md) for a full walkthrough of adding an AST-aware parser.
