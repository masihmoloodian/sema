# Before and after

These comparisons use real, publicly available open-source repositories. Each shows the actual tool calls an AI assistant would make without sema versus the sema approach — with token costs derived from real file sizes.

Token estimates: ~1 token per 4 characters of source code.

---

## Test 1 — [hoppscotch/hoppscotch](https://github.com/hoppscotch/hoppscotch) (TypeScript monorepo, 1,172 files)

**Question:** *"How does magic link authentication work end-to-end — which service methods and controller endpoints are involved?"*

**Without sema** — no index, explores by reading files:

| Step | Tool call | Tokens |
|---|---|---|
| Scan directory structure | `Bash: find . -name "*.ts" \| grep -i auth` | ~300 |
| Read auth service | `Read: auth/auth.service.ts` (392 lines) | 2,613 |
| Read auth controller | `Read: auth/auth.controller.ts` (230 lines) | 1,744 |
| Read JWT strategy | `Read: auth/strategies/jwt.strategy.ts` (110 lines) | 718 |
| Read mailer service | `Read: mailer/mailer.service.ts` (89 lines) | 621 |
| Read auth module | `Read: auth/auth.module.ts` (66 lines) | 479 |
| **Total** | **6 tool calls** | **~6,475 tokens** |

**With sema** — one search surfaces the exact symbols:

| Step | Tool call | Tokens |
|---|---|---|
| Find relevant symbols | `search_code("magic link authentication send email")` | 237 |
| Read service implementation | `get_code("signInMagicLink")` | 465 |
| Read controller endpoint | `get_code("signInMagicLink")` (controller) | 135 |
| **Total** | **3 tool calls** | **837 tokens** |

```
search_code("magic link authentication send email")

→ auth/auth.service.ts::signInMagicLink           (100% match)
     method: signInMagicLink(email: string, origin: string)
→ auth/auth.controller.ts::signInMagicLink         (95% match)
     method: signInMagicLink(@Body() authData, @Query() origin)
→ platform/auth/web/index.ts::sendMagicLink        (96% match)
     function: sendMagicLink(email: string)
→ mailer/mailer.service.ts::sendEmail              (97% match)
     method: sendEmail(...)
```

**Result: 3 tool calls vs 6, 837 tokens vs 6,475 tokens — 8× reduction.**

---

## Test 2 — [fastapi-users/fastapi-users](https://github.com/fastapi-users/fastapi-users) (Python, 123 files)

**Question:** *"How does JWT token creation and validation work? Where is the token written and how is it decoded?"*

**Without sema:**

| Step | Tool call | Tokens |
|---|---|---|
| Find Python files related to JWT | `Bash: grep -r "jwt\|token" --include="*.py" -l` | ~200 |
| Read JWT strategy | `Read: authentication/strategy/jwt.py` (72 lines) | 506 |
| Read JWT utilities | `Read: fastapi_users/jwt.py` (41 lines) | 233 |
| Read user manager | `Read: fastapi_users/manager.py` (715 lines) | 5,024 |
| **Total** | **4 tool calls** | **~5,963 tokens** |

*(manager.py must be read in full because the create/register flow spans the whole file — no way to know which lines matter without reading it)*

**With sema:**

| Step | Tool call | Tokens |
|---|---|---|
| Find JWT symbols | `search_code("JWT token create write validate")` | 229 |
| Read token generator | `get_code("generate_jwt")` | 106 |
| Read strategy write | `get_code("write_token")` | 331 |
| **Total** | **3 tool calls** | **666 tokens** |

```
search_code("JWT token create write validate")

→ fastapi_users/jwt.py::generate_jwt              (93% match)
     function: def generate_jwt(data, secret, lifetime_seconds, algorithm) -> str
→ authentication/strategy/jwt.py::write_token     (in get_code result)
     method: def write_token(self, user: models.UP) -> str
→ authentication/strategy/jwt.py::read_token      (related)
     method: def read_token(self, token: str, ...) -> models.UP
```

**Result: 3 tool calls vs 4, 666 tokens vs 5,963 tokens — 9× reduction.**

---

## Test 3 — [gothinkster/golang-gin-realworld-example-app](https://github.com/gothinkster/golang-gin-realworld-example-app) (Go, 30 files)

**Question:** *"How does the authentication middleware work — how is the JWT token extracted and validated per request?"*

**Without sema** — small repo, but still requires reading multiple files:

| Step | Tool call | Tokens |
|---|---|---|
| Explore project structure | `Bash: ls -la users/ common/` | ~150 |
| Read middleware file | `Read: users/middlewares.go` (75 lines) | 487 |
| Read token utilities | `Read: common/utils.go` (99 lines) | 760 |
| Read router setup | `Read: users/routers.go` (137 lines) | 1,000 |
| **Total** | **4 tool calls** | **~2,397 tokens** |

**With sema:**

| Step | Tool call | Tokens |
|---|---|---|
| Find auth middleware | `search_code("authentication middleware JWT token")` | 185 |
| Read middleware logic | `get_code("AuthMiddleware")` | 235 |
| Read token generator | `get_code("GenToken")` | 132 |
| **Total** | **3 tool calls** | **552 tokens** |

```
search_code("authentication middleware JWT token")

→ users/middlewares.go::AuthMiddleware             (100% match)
     function: func AuthMiddleware(auto401 bool) gin.HandlerFunc
→ users/middlewares.go::extractToken               (96% match)
     function: func extractToken(c *gin.Context) string
→ common/utils.go::GenToken                        (98% match)
     function: func GenToken(id uint) string
```

**Result: 3 tool calls vs 4, 552 tokens vs 2,397 tokens — 4× reduction.**

*(The Go repo has only 30 files — sema's advantage grows with codebase size.)*

---

## Summary

| Repo | Language | Files | Without sema | With sema | Reduction |
|---|---|---|---|---|---|
| hoppscotch | TypeScript | 1,172 | 6,475 tokens / 6 calls | 837 tokens / 3 calls | **8×** |
| fastapi-users | Python | 123 | 5,963 tokens / 4 calls | 666 tokens / 3 calls | **9×** |
| golang-gin-realworld | Go | 30 | 2,397 tokens / 4 calls | 552 tokens / 3 calls | **4×** |

Token counts are measured using `tiktoken` (cl100k_base) on the actual files from each repo, and on real `search_code` / `get_code` output. The "without" bash command costs are estimated at ~150–300 tokens each.

The pattern: sema always uses 3 tool calls (search → fetch → fetch). The "without" cost grows with repo size because the AI must read whole files to locate relevant code. On large TypeScript or Python projects the savings are consistently 8–9×.
