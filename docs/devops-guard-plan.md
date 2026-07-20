# DevOps Guard — feature plan

Status: **v1 implemented on `feature/devops-guard`, two rounds of real
testing done.** kubectl and Terraform are both proven end-to-end against
real tools (a [kind](https://kind.sigs.k8s.io/) cluster and a local-only
Terraform config) — safe actions auto-run, mutations are held until
`sema devops approve`, irreversible actions are refused outright, and
secrets stay redacted including the base64-encoded case (`kubectl get
secret -o yaml`) that a first pass initially missed. See "Market-readiness
pass" below for the six real bugs that second round of testing found and
fixed. AWS CLI and Helm still only have unit-tested policy logic, not
real-command verification — that and the extension approval panel are the
two clearest gaps left before calling this fully market-ready. This doc
scopes a new sema feature that lets
a DevOps engineer drive AWS, Terraform, Kubernetes, and similar day-to-day
tooling through **any AI provider** (CLI + VS Code extension), with a local
safety layer that gates risky actions and redacts secrets before anything
reaches the model, a log, or a screen.

Why this fits sema: sema already runs fully offline with a local embedding
model ([SBERT](../sema/indexer/) via ChromaDB) and already exposes tools
through one [MCP server](../sema/mcp/tools.py) that Claude Code, Codex,
Cursor, Grok, and opencode all talk to identically over stdio — see
[architecture.md](architecture.md) and the per-provider docs
([claude-code.md](claude-code.md), [codex.md](codex.md), [cursor.md](cursor.md),
[grok.md](grok.md), [opencode.md](opencode.md)). This feature is **provider-agnostic
by construction**: it's a new set of MCP tools (`devops_plan`, `devops_run`, ...)
plus a CLI, not a Claude-specific integration. Any MCP client gets the same
gating and redaction for free, with zero provider-specific code. This feature
reuses sema's existing infra — same server, same "no internet, no external
APIs" constraint, one more local model doing safety/redaction instead of
embeddings.

**Provider-agnostic design constraint:** the guard (`policy.py`, `redact.py`,
`guard.py`, `runner.py`) must never assume which model is on the other end of
the MCP connection, and must never depend on a provider-specific prompt,
system message, or tool-calling quirk. The tiering/redaction decision is made
entirely server-side, before a response is returned over MCP — so even a
provider with a weaker safety posture than Claude gets the same hard
guarantees, because the guarantee lives in sema, not in the model being
trusted to behave.

## Problem

DevOps engineers want their AI provider to run real infra commands (`terraform plan`,
`aws ec2 ...`, `kubectl apply`, `helm upgrade`, ...) instead of just editing
YAML/HCL by hand. They won't adopt it unless:
1. It can't accidentally destroy production
2. Secrets (access keys, kubeconfig tokens, tfstate secrets, DB creds) never
   leak into the model's context, chat transcript, or any log file
3. Every action is visible and auditable before and after it runs

## Core invariant: analyze-first, always

This is the non-negotiable rule the whole feature is built around: **sema
analyzes every proposed action before it is allowed to run — never after.**
The AI never gets direct execution access; it only gets to *propose* a
command, and sema's gate decides what happens next:

- **Looks safe** (matches an allow-listed read-only pattern, or a mutation
  within an already-approved low-risk tier) → runs automatically, still
  logged
- **Looks suspicious** (mutates state, touches an escalated environment,
  matches a risky pattern, or the local guard model flags something the
  static rules didn't) → execution is **held**, a plain-language warning is
  shown to the engineer (what it wants to do, why it was flagged, redacted
  diff/output), and nothing runs until the engineer explicitly consents
- **Looks prohibited** (destroy in prod, IAM root changes, force-push to a
  protected branch, ...) → never runs through sema at all, regardless of
  consent; the engineer is told to do it manually outside the tool

There is no path where a command executes before analysis happens. "Analyze,
then decide, then maybe act" is the order every single time, not just for
commands that look risky at a glance — because the whole point is that the
AI's own judgment about what's risky isn't the thing being trusted.

### Making the gate actually unbypassable

The gate is worthless if the AI can route around it. Providers like Claude
Code, Codex, and Cursor typically also have a generic shell/terminal tool —
if `terraform`/`aws`/`kubectl`/`helm` are reachable through that raw shell
tool, the AI (or a prompt-injected instruction hidden in a file it reads)
can simply run them directly and skip sema's gate entirely. Analysis has to
be structurally unavoidable, not just the documented/preferred path:

- **PATH-level interception** — `sema devops` installs thin shims for the
  gated binaries (`terraform`, `aws`, `kubectl`, `helm`, ...) earlier in
  `PATH` than the real binaries. Any invocation, whether it comes through
  sema's own MCP tool or through a generic shell tool the provider happens
  to have, gets routed through `policy.py`/`redact.py`/`guard.py` first.
  This is the only approach that holds regardless of which provider or
  which tool-calling path is used, so it should be the default.
  Real binaries are only reachable via the shims once a command clears the
  gate.
- **MCP tool as the documented interface** — `devops_plan`/`devops_run`
  remain the primary, ergonomic way providers are instructed (via
  `sema init`) to do infra work, but this is a UX nicety, not the security
  boundary. Never rely on "the provider was told to use the MCP tool" as
  the actual guarantee.
- **Fail closed** — if a shim can't reach the sema gate (daemon not
  running, misconfigured), it refuses to exec the real binary rather than
  falling through to it silently.

## Day-to-day surface to cover

Scope for v1, roughly in priority order (matches sema's existing "TS → Go →
Python" priority-order philosophy — narrow first, expand later):

| Domain     | Read/inspect (safe)                          | Mutate (gated)                                   |
|------------|-----------------------------------------------|---------------------------------------------------|
| Terraform  | `plan`, `show`, `state list`, `validate`      | `apply`, `destroy`, `import`, `state rm/mv`        |
| AWS CLI    | `describe-*`, `get-*`, `list-*`               | `create-*`, `update-*`, `delete-*`, `terminate-*`  |
| Kubernetes | `get`, `describe`, `logs`, `diff`             | `apply`, `delete`, `scale`, `rollout restart`      |
| Helm       | `helm diff`, `helm get`, `helm status`        | `helm upgrade`, `helm install`, `helm uninstall`   |
| Git (infra repos) | `log`, `diff`, `show`                  | `push`, `force-push`, `tag` on protected branches  |

Out of scope for v1: cloud providers beyond AWS (GCP/Azure), CI/CD pipeline
control (Jenkins/GitHub Actions triggers), secrets-manager writes.

## End-to-end flow

```
 Engineer (chat, CLI, or extension)
        │
        │ "scale down the staging web deployment to 2 replicas"
        ▼
 AI provider (Claude Code, Codex, Cursor, Grok, opencode, ... — any MCP client)
        │  proposes a concrete command, e.g.
        │  kubectl scale deployment/web --replicas=2 -n staging
        ▼
 ┌─────────────────────────── sema devops layer ───────────────────────────┐
 │                                                                          │
 │  1. policy.py   → classify tier: auto / approve / prohibited            │
 │  2. redact.py   → deterministic secret scan on the command + any        │
 │                    referenced files (regex + entropy, gitleaks rules)   │
 │  3. guard.py    → local light model: intent check ("does this match    │
 │                    what the engineer asked for? does it look           │
 │                    destructive beyond stated scope?") + fuzzy redact    │
 │                    pass for anything regex missed                      │
 │                                                                          │
 │  Everything downstream (model context, extension UI, audit log) only   │
 │  ever sees the output of steps 2–3 — never the raw command/output.     │
 └───────────────────────────────┬──────────────────────────────────────┘
                                  │
                 tier = auto ─────┼───── tier = approve ───── tier = prohibited
                       │          │             │                    │
                       ▼          │             ▼                    ▼
                  run now         │      show redacted diff    refuse, tell
                       │          │      in CLI prompt or       engineer to
                       │          │      extension panel,       run it
                       │          │      wait for explicit      themselves
                       │          │      approve/deny
                       │          │             │
                       │          │             ▼
                       │          │      engineer approves ──► run
                       │          │      engineer denies   ──► abort, log why
                       ▼          ▼             │
                  runner.py executes command ◄──┘
                       │
                       ▼
              raw stdout/stderr
                       │
                       ▼
         redact.py + guard.py pass (same as above, output direction)
                       │
                       ▼
        redacted result → audit log, extension panel, back to the AI provider
```

## Components

```
sema/
  devops/
    runner.py     execute terraform/aws/kubectl/helm, capture stdout+stderr
    policy.py     tiered rules: auto-run / needs-approval / prohibited
    guard.py      local light model: intent + fuzzy-secret verdicts
    redact.py     deterministic regex/entropy secret scanner (first pass)
    audit.py      structured, redacted audit log (append-only)
  cli.py           new `sema devops` subcommands
  mcp/tools.py     new MCP tools: devops_plan, devops_run, devops_approve, devops_log
vscode-extension/
  devops panel      approval queue, redacted diff viewer, audit history
```

### Tiering (`policy.py`)

- **auto** — read-only commands (`plan`, `describe-*`, `get`, `diff`, `status`)
  run immediately, no prompt
- **approve** — anything that mutates state (`apply`, `create-*`, `scale`,
  `helm upgrade`) requires an explicit yes from the engineer, shown as a
  redacted diff/summary, not a raw dump
- **prohibited** — irreversible/high-blast-radius actions (`destroy` on prod,
  `delete` on tagged-critical resources, IAM root changes, force-push to
  protected branches) are never auto-run; sema tells the engineer to do it
  themselves outside the tool

Environment awareness matters more than command name: the same `kubectl
scale` is **auto** in `dev`, **approve** in `staging`, and stricter still in
`prod`. Tier rules key off a resolved environment (namespace, AWS profile,
Terraform workspace), not just the verb.

### Redaction (`redact.py`, `guard.py`)

Two layers, always run in this order, on both the outgoing command and the
returned output:

1. **Deterministic** — regex/entropy rules (reuse gitleaks/detect-secrets
   rule sets) for known secret shapes: AWS keys, private keys, JWTs,
   connection strings, `.env`-style values. Fast, offline, no model needed.
2. **Light local model** — a small local model (same "runs offline, no
   external API" constraint as sema's embedding model) reviews what layer 1
   passed through: catches secrets that don't match a known shape, and
   flags command intent that looks broader/more destructive than what the
   engineer asked for.

Redaction happens **before** anything reaches the model's context, the audit
log, or the extension UI — never after. If the raw secret ever reaches the
model or a log first, the leak already happened.

### CLI surface

```
sema devops plan <cmd>       dry-run + redacted preview, nothing executes
sema devops run <cmd>        runs if tier=auto, else queues for approval
sema devops approve <id>     approve a queued action
sema devops deny <id>        deny a queued action, logged with reason
sema devops log              redacted audit trail
```

### Extension surface

- Approval queue: pending actions with redacted diffs, one-click approve/deny
- Redaction indicator: shows *that* something was redacted (type + count)
  without re-exposing it, so trust doesn't require blind faith
- Audit history view, filterable by environment/domain (terraform/aws/k8s)

## Is this actually helpful? A critical read

Worth being honest about this before building it, since the value isn't
uniform across the pieces above.

**Where the value is real and not redundant with existing tools:**
- The core problem this solves that *nothing else does* is: secrets and
  infra output flowing into an **LLM's context and chat transcript**.
  Terraform/AWS/kubectl already have review workflows (Atlantis, Spacelift,
  Env0, `kubectl diff`, admission controllers), but none of them were built
  worrying about "don't let a chat log or a model provider retain a
  customer's DB password." That's a genuinely new risk surface this feature
  addresses, and it's the strongest reason to build it.
- A **single, provider-agnostic interface** across Terraform/AWS/k8s/Helm
  inside the same chat/CLI an engineer already uses (instead of shelling out
  or context-switching to five different dashboards) is real day-to-day
  value, independent of the safety story.
- A unified, redacted **audit trail** of what an AI proposed vs. what a
  human actually approved is useful even outside a security conversation —
  it's the paper trail engineers will want the first time something goes
  wrong regardless of cause.

**Where to be honest about limits, so the pitch doesn't overpromise:**
- The **deterministic layers** (`policy.py` tiers, plan-before-apply,
  scoped/short-lived credentials, regex/entropy redaction) are what
  DevOps engineers will actually trust, because they're auditable,
  testable, and don't depend on a model behaving correctly. This is where
  most of the engineering effort and v1 scope should go.
- The **"light AI model as safeguard" (`guard.py`)** is the part to be most
  skeptical of. A second probabilistic model checking a first probabilistic
  model's output is not a guarantee — it's a heuristic that catches some
  fraction of what regex misses, with its own false-negative rate. Pitching
  it as *the* safety mechanism is misleading and will lose trust with
  engineers who know their tooling; pitching it as a best-effort second
  pass *on top of* deterministic rules is honest and still useful. The doc
  above already frames it this way (`redact.py` first, `guard.py` second) —
  worth keeping that order non-negotiable in the actual build.
- Much of the "safe execution" ground is already covered by things
  DevOps teams already run: OPA/Conftest policies, `terraform plan` review
  in CI, IAM least-privilege, admission controllers (Kyverno/Gatekeeper).
  This feature doesn't replace those — it should **integrate with them**
  (e.g., run existing OPA policies as part of `policy.py` rather than
  reinventing policy-as-code) instead of asking engineers to trust a new,
  parallel safety system from scratch.

**Bottom line:** yes, worth building — but lead with "keeps secrets out of
the model's context + one interface across your infra tools + full audit
trail," not "an AI watches the AI." The AI safeguard is a nice-to-have
second layer, not the pitch.

## Open questions

- Which local model for `guard.py`? Options: small local LLM via Ollama, or
  a purpose-built classifier trained on command intent + secret patterns.
  Needs to be fast enough to run on every command without annoying latency.
- How does `policy.py` learn "prod" vs "staging" — explicit config per
  project, or inferred from AWS profile / kube-context / Terraform workspace
  naming conventions?
- Should approval be blocking (engineer must respond before the AI
  provider continues) or async (the provider proceeds with other work,
  action executes once approved)?
- State locking: Terraform remote state locks need to be respected so a
  human and an AI-initiated run can't race.

## Milestones

- [x] `sema/devops/secrets.py` — deterministic regex secret scanner (kept in
      sync with the extension's `redact.ts` patterns), unit-tested
- [x] `sema/devops/gate.py` — layers `secrets.py` + sema's existing spaCy NER
      redactor (`sema/redact.py`, the model already used by `sema redact` /
      the extension's redact toggle) instead of a new bespoke model; degrades
      to regex-only when the optional PII extra isn't installed
- [x] `sema/devops/policy.py` — tier classification (SAFE/APPROVE/PROHIBITED)
      for kubectl (fully worked out), with a first pass for
      terraform/aws/helm per the coverage table above
- [x] `sema/devops/runner.py` + `sema/devops/state.py` — subprocess execution
      and pending-approval/audit-log persistence under `.sema/devops/`
- [x] `sema devops plan|run|approve|deny|pending|log` CLI commands
- [x] `devops_plan`/`devops_run`/`devops_approve`/`devops_deny`/
      `devops_pending`/`devops_log` MCP tools — provider-agnostic, same gate
      as the CLI, registered on the same server as every other sema tool
- [x] `sema devops install-shims` — PATH shims for kubectl/terraform/aws
      (helm skipped automatically if not installed) that route through the
      gate even from a raw shell tool, fail closed if `sema` isn't reachable
- [x] End-to-end verification against a real kind cluster (see below) —
      safe/approve/prohibited tiers, redaction of a real embedded secret, the
      MCP tool path, and the PATH shim path all confirmed working
- [ ] Extension approval queue panel
- [ ] Terraform/AWS CLI/Helm exercised against real infra (only kubectl has
      been proven end-to-end so far)
- [ ] Environment-aware tiering beyond namespace-name heuristics (Terraform
      workspace detection, AWS profile/account awareness)
- [ ] Git (infra repo) coverage added to policy tiers

### What shipped differs from the original sketch in one way worth calling out

The original plan sketched a separate `guard.py` "light local model" as a
second redaction pass. Turned out sema already has exactly that: `sema/redact.py`
(spaCy NER, optional `[pii]` extra) is the same model the VS Code extension's
"Redact PII & secrets" toggle uses as its hybrid layer 2, on top of the
extension's own regex layer. `gate.py` reuses it directly instead of building
a parallel model — one fewer moving part, and it's a model that was already
battle-tested by the extension rather than a new untested one.

### Proof it works — kind cluster walkthrough

```
kind create cluster --name sema-devops-test
kubectl create namespace staging
kubectl -n staging create deployment web --image=nginx:alpine --replicas=1
kubectl -n staging create configmap web-config \
  --from-literal=AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE --from-literal=note=hello
```

- `sema devops run kubectl get pods -n staging` → ran immediately (SAFE)
- `sema devops run kubectl get configmap web-config -n staging -o yaml` →
  ran immediately, but `AKIAIOSFODNN7EXAMPLE` came back as `[AWS_ACCESS_KEY_ID]`
  in the returned output *and* in the audit log — the real key never reached
  the caller or disk unredacted
- `sema devops run kubectl scale deployment/web --replicas=3 -n staging` →
  held (`outcome: "held"`, tier APPROVE); `kubectl get deployment web`
  confirmed the replica count had **not** changed yet
- `sema devops approve <id>` → only then did the deployment actually scale
  to 3, confirmed via `kubectl get deployment web`
- `sema devops run kubectl delete namespace kube-system` → refused
  (`outcome: "prohibited"`) with zero execution attempt; `kube-system`
  confirmed still present afterward
- Same three tiers re-verified through the MCP tool functions directly
  (`devops_plan`/`devops_run`/`devops_pending`/`devops_log`) — identical
  behavior to the CLI, and through the installed PATH shim
  (`kubectl get ...`, `kubectl scale ...` prompting interactively) — same
  gate, same decisions, regardless of which path invoked `kubectl`

## Market-readiness pass — what a second round of real testing found

Ran a second, harder round against a fresh kind cluster plus a local-only
Terraform config (the `local_file` provider — no cloud creds needed), aimed
specifically at day-to-day tasks and adversarial/risky ones, to see what a
real engineer would hit in the first hour of use. This found five real bugs,
all fixed and covered by new tests (304 total passing):

1. **k8s Secret values leaked through redaction entirely.**
   `kubectl get secret db-creds -o yaml` returns Secret data base64-encoded,
   not plaintext — none of the regex/NER redaction patterns match a base64
   blob, so a real AWS key planted in a test Secret came back completely
   unredacted. This is arguably the single most realistic leak path for
   this feature (reading a Secret is a routine day-to-day task), so it
   would have been a serious problem to ship with. Fixed with a dedicated
   `sema/devops/k8s_secrets.py`: structural redaction for `-o yaml`/`-o json`
   (strips values, keeps the surrounding document readable) and a blanket
   base64-blob pass for `-o jsonpath`/`-o go-template`/`-o custom-columns`,
   which extract a bare value with no structure to parse. Scoped tightly to
   `kubectl get secret*` — outside that context base64-looking substrings
   (git hashes, image digests) are common and harmless, so blanket
   redaction elsewhere would just be noise. `kubectl describe secret`
   needed no help — kubectl already masks it to byte counts by default.

2. **The first structural fix over-redacted.** The initial version of (1)
   ran the YAML structural pass and *then* unconditionally also ran the
   blanket base64-blob pass on the result — and ordinary YAML keys
   (`apiVersion`, `kind`, `uid`, ...) are themselves valid base64 alphabet,
   so they got matched and the whole document turned into
   `[K8S_SECRET_VALUE]: v1`. A too-aggressive redactor that eats legitimate
   output is its own trust problem, not just a cosmetic one — engineers
   stop believing the tool if it corrupts things that were never secret.
   Fixed by making structural redaction terminal: if it applied, the blob
   pass doesn't also run.

3. **`kubectl rollout status`/`rollout history` were misclassified as
   mutating.** The policy grouped the whole `rollout` verb as
   mutation-tier because `restart`/`undo` live under it, which meant one of
   the most common day-to-day checks — "did my rollout finish?" — needed
   human approval for no reason. Fixed by special-casing `rollout` the same
   way `config` already was: `status`/`history` are SAFE, everything else
   under `rollout` stays APPROVE.

4. **`terraform init` needed approval.** It's the very first command any
   engineer runs on a fresh checkout, touches no real infrastructure, and
   was defaulting to APPROVE because it wasn't in the read-only verb set.
   Fixed by adding `init`/`get` (module fetching) to Terraform's SAFE list.

5. **Terraform's ANSI color codes corrupted redacted output.** Even
   non-interactively, this Terraform version still emitted ANSI escape
   codes, and the NER redaction pass misfired on fragments of the escape
   sequences — real `plan` output came back with stray `[NAME]`/`[LOCATION]`
   tags spliced into resource attribute names, which would make an engineer
   trust the tool less on sight. Fixed in `runner.py`: strip ANSI from
   captured stdout/stderr before anything (redaction, logging, display) sees
   it, plus set `NO_COLOR=1`/`CLICOLOR=0` on the subprocess env as a
   best-effort first line of defense.

Also found, while stress-testing the CLI directly rather than through kind:

6. **A quoted `--` inside a command was silently dropped.**
   `sema devops plan kubectl exec pod -- sh` lost the `--` because click's
   own argument parser treats a bare `--` as its own end-of-options marker,
   regardless of `ignore_unknown_options`. Not a security issue (redaction/
   tiering still worked), but a command-fidelity one — the argv actually
   executed wouldn't have matched what was asked for. Fixed by accepting
   either unquoted argv (`sema devops plan kubectl get pods -A`, unchanged)
   or a single quoted string parsed with `shlex.split` (`sema devops plan
   "kubectl exec pod -- sh"`) — quoting is now the documented way to pass a
   command containing `--`, and it also makes the CLI's input contract match
   the MCP tools' (`command: str`) exactly.

7. **`sema devops run`/`approve` didn't propagate exit codes.** The
   underlying command's exit code was shown in the printed output but the
   `sema` process itself always exited 0 — a script doing
   `sema devops run "kubectl apply -f x.yaml" && next-step` wouldn't have
   noticed a failure. Fixed: exit code now reflects outcome (0/underlying
   code for ran, 1 for prohibited, 2 for held — distinct from a real
   failure since nothing executed and a human still needs to decide), in
   both human and `--json` output modes.

### What's still not proven, going into a market push

- **Terraform** is now proven end-to-end (`init`/`plan` ran automatically,
  `apply` held until `sema devops approve`, `destroy` refused outright,
  verified against real file-creation side effects), but only against the
  `local_file` provider — no real cloud provider (AWS/GCP/Azure) has been
  exercised yet.
- **AWS CLI and Helm** still have only unit-tested policy logic, no
  real-command verification (AWS CLI would need either real credentials or
  something like LocalStack; Helm needs a chart to install against the kind
  cluster). This is the biggest remaining gap before calling AWS/Helm
  coverage market-ready, as opposed to kubectl and Terraform which now are.
- **The extension approval-queue panel doesn't exist yet** — approvals only
  have a CLI/MCP surface (`sema devops approve/deny/pending`) today.
- **No load/concurrency testing** — `state.py`'s `approvals.json` is a
  read-modify-write JSON file with no file locking; two concurrent
  `sema devops run` calls queuing approvals at the same instant could race.
  Unlikely for a single engineer's interactive use, worth fixing before
  claiming multi-engineer/CI concurrency support.
