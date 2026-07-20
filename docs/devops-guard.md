# DevOps guard

Lets an AI provider drive real infra commands — Terraform, `kubectl`, AWS CLI,
Helm — without a DevOps engineer having to trust the model's own judgment
about what's safe. Every command is classified and secret-redacted **before**
it runs, not after. See [docs/devops-guard-plan.md](devops-guard-plan.md) for
the full design writeup and test log; this doc is the shorter "how do I use
it" version.

## The core idea

sema sits between the AI provider and the real binary. Nothing gets to
`terraform`/`kubectl`/`aws`/`helm` without going through this first:

```
AI provider proposes a command
        │
        ▼
   classify (safe / needs-approval / prohibited)
        │
        ▼
   redact secrets from the command + its eventual output
        │
        ▼
 ┌──────────────┬───────────────────┬──────────────────┐
 │  safe         │  needs approval    │  prohibited        │
 │  runs now     │  held until a       │  refused outright,  │
 │               │  human explicitly   │  no exceptions       │
 │               │  approves           │                     │
 └──────────────┴───────────────────┴──────────────────┘
```

- **Safe** — read-only commands (`kubectl get`, `terraform plan`, `aws
  describe-*`, `terraform init`, `kubectl rollout status`, ...). Runs
  immediately, still logged.
- **Needs approval** — anything that mutates state (`kubectl scale`,
  `terraform apply`, `kubectl apply`, `helm upgrade`). Held until a human
  runs `sema devops approve <id>` — the AI cannot approve its own held
  actions.
- **Prohibited** — irreversible/high-blast-radius (`kubectl delete namespace
  kube-system`, `terraform destroy`, deleting a CRD, force-delete with
  `--grace-period=0`). Never runs through sema, period — not even with
  approval. The engineer is told to run it themselves outside the tool if
  they're certain.

Redaction runs on **both** the command and its output, always, regardless of
tier — including the base64-encoded case (`kubectl get secret -o yaml`)
that plain regex alone would miss. See [devops-guard-plan.md](devops-guard-plan.md#market-readiness-pass--what-a-second-round-of-real-testing-found)
for exactly how that was found and fixed.

## Provider-agnostic by construction

This isn't Claude-specific. It's a new set of [MCP tools](mcp-tools.md)
(`devops_plan`, `devops_run`, `devops_approve`, `devops_deny`,
`devops_pending`, `devops_log`) registered on the same MCP server every
other sema tool lives on — the one Claude Code, Codex, Cursor, Grok Build,
and opencode all already talk to. Any of them gets identical gating with
zero extra wiring, because the gate lives server-side in sema, not in a
prompt telling the model to behave.

## Using it from the CLI

```
sema devops plan "kubectl scale deployment/web --replicas=3 -n staging"
  → APPROVE  scale deployment/web --replicas=3 -n staging
    mutating verb 'scale'

sema devops run "kubectl scale deployment/web --replicas=3 -n staging"
  → held for approval, prints an id

sema devops approve <id>       # only now does it actually execute
sema devops deny <id>          # or refuse it — nothing runs either way until you decide

sema devops pending            # what's currently waiting on you
sema devops log                # full redacted audit trail — everything decided, ever
```

Quote the command as a single string if it contains a `--` separator (e.g.
`kubectl exec pod -- sh`) — unquoted works fine otherwise.

### PATH shims — closing the "just use the raw shell" hole

An AI provider that also has a generic shell/terminal tool could otherwise
just run `terraform apply` directly and skip the gate entirely. `sema devops
install-shims` installs thin wrapper scripts for `kubectl`/`terraform`/`aws`
that route through the same gate no matter which tool invoked them — the MCP
tool, a raw shell command, or an engineer typing at a prompt. Prepend the
printed directory to `PATH` and it applies everywhere, fails closed if
`sema` itself isn't reachable.

## Making sure infra commands actually go through the guard

Two separate questions, two separate answers:

**"Will the AI choose to call `devops_run` instead of just running `kubectl`
in its shell tool?"** — usually yes, MCP tool descriptions are enough context
for a model to prefer them, and it's what was observed in real testing.
But "usually" isn't a guarantee — steer it explicitly. Add this to the
project's `CLAUDE.md` (same pattern sema already uses for `search_code`,
see [vscode-workspace.md](vscode-workspace.md)):

```markdown
## Infra commands

This project's devops actions (kubectl, terraform, aws, helm) are gated by
sema's devops guard. Always use `devops_plan`/`devops_run` instead of a raw
shell command for any kubectl/terraform/aws/helm invocation — never run
these binaries directly via a shell/terminal tool.
```

**"What if the AI (or a prompt-injected instruction in some file it reads)
runs `kubectl` directly anyway, ignoring that guidance?"** — this is the
question that actually matters for safety, and prompt guidance alone can't
answer it: a model can always be steered around a system-prompt instruction.
The real answer is `sema devops install-shims` (above) — it replaces the
real `kubectl`/`terraform`/`aws` binaries on `PATH` with wrappers that route
through the same gate regardless of which tool invoked them. Verified
directly: with shims installed, calling `kubectl` as a completely plain
shell command — bypassing `devops_run` entirely — still gets classified,
still redacts output, still holds mutating actions for approval, and still
refuses `kubectl delete namespace kube-system` outright. The MCP tool is the
ergonomic path; the shim is the actual guarantee. Install both.

## Does this work through the VS Code extension?

Short answer: **depends which mode of the extension you're using.**

- **"Reuse a local CLI" mode** (Claude Code, Codex, Grok Build, Cursor,
  opencode, embedded in the extension's panel) — **yes, already works, no
  extra wiring needed.** These CLIs talk to sema over standard MCP, the same
  way they do outside the extension. Once `sema init --claude` (or the
  equivalent for your CLI) has registered sema, `devops_plan`/`devops_run`/
  etc. show up as tools the model can call, right there in the extension's
  chat panel — this is exactly the path verified end-to-end against a real
  kind cluster.
- **"Bring an API key" mode** (the extension's own built-in chat, calling
  Anthropic/OpenAI/OpenRouter/etc. directly) — **not yet.** That mode has
  its own hand-wired, hardcoded list of sema tools
  ([`semaWorkflow.ts`](../vscode-extension/src/semaWorkflow.ts)) rather than
  generic MCP tool discovery, so the new `devops_*` tools aren't surfaced
  there until someone explicitly adds them to that list. This is real
  scoped work, not a config flip.
- **Either mode, one current gap:** there's no approval-queue panel in the
  extension yet. A held action still gets approved from a terminal
  (`sema devops approve <id>`) — usable inside VS Code's integrated
  terminal, just not a click-to-approve UI yet. That's tracked as an open
  milestone in [devops-guard-plan.md](devops-guard-plan.md).

## What's proven vs. what isn't

| Tool | Policy tiering | Verified against the real thing |
|---|---|---|
| kubectl | ✅ full | ✅ real kind cluster |
| Terraform | ✅ first pass | ✅ local-only config (`local_file` provider) — no real cloud provider yet |
| AWS CLI | ✅ first pass | ❌ not yet — needs real credentials or something like LocalStack |
| Helm | ✅ first pass | ❌ not yet — needs a chart against a real cluster |

Full test log, including the bugs found and fixed along the way, is in
[devops-guard-plan.md](devops-guard-plan.md#market-readiness-pass--what-a-second-round-of-real-testing-found).
