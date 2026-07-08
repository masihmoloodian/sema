# Configuration

## `.sema/config.toml` (optional)

```toml
[index]
include = ["*.ts", "*.tsx", "*.go", "*.py", "*.js"]
exclude = ["*.test.ts", "*.spec.ts", "*_test.go", "test_*.py"]

[model]
name = "all-MiniLM-L6-v2"
```

## Environment variables

```
SEMA_INDEX_PATH    Override index location (default: .sema/index/)
SEMA_MODEL         Override embedding model name
SEMA_LOG_LEVEL     debug | info | warning (default: warning)
```

## `.gitignore`

`sema init` adds this automatically. If you prefer to do it manually:

```gitignore
# sema
.sema/index/
```

Commit `.sema/meta.json` if you want teammates to see index stats. The index itself is machine-specific and should not be committed.
