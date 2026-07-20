"""Tier classification — the analyze-first gate's core decision.

Every proposed command is classified into exactly one tier *before* it is
allowed to run (see docs/devops-guard-plan.md, "Core invariant: analyze-first,
always"):

  SAFE       — read-only, runs immediately, still logged
  APPROVE    — mutates state; held until a human explicitly consents
  PROHIBITED — irreversible / high-blast-radius; never runs through sema,
               regardless of consent

kubectl is the fully-worked-out ruleset (it's what's actually exercised
end-to-end against a kind cluster — see tests/devops/test_e2e_kind.py).
Terraform/AWS CLI/Helm get a first pass here per docs/devops-guard-plan.md's
coverage table; they are intentionally simpler and not yet environment-aware
(e.g. Terraform workspace detection) — narrow first, expand later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Tier(str, Enum):
    SAFE = "safe"
    APPROVE = "approve"
    PROHIBITED = "prohibited"


@dataclass
class Decision:
    tier: Tier
    reason: str
    tool: str
    argv: list[str] = field(default_factory=list)

    @property
    def command(self) -> str:
        return " ".join(self.argv)


_CRITICAL_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}
_PROD_NAMESPACE_RE = re.compile(r"prod", re.IGNORECASE)

_KUBECTL_READ_VERBS = {
    "get", "describe", "logs", "diff", "top", "explain", "api-resources",
    "api-versions", "version", "cluster-info", "events", "wait",
}
_KUBECTL_MUTATE_VERBS = {
    "apply", "create", "patch", "scale", "rollout", "edit", "label",
    "annotate", "cordon", "uncordon", "drain", "exec", "cp", "port-forward",
    "taint", "replace", "set", "expose", "autoscale", "run",
}


def _flag_value(argv: list[str], names: set[str]) -> str | None:
    """Return the value of the first ``--name value`` or ``--name=value`` flag found."""
    for i, tok in enumerate(argv):
        for name in names:
            if tok == name and i + 1 < len(argv):
                return argv[i + 1]
            if tok.startswith(name + "="):
                return tok.split("=", 1)[1]
    return None


def _has_flag(argv: list[str], names: set[str]) -> bool:
    return any(tok in names for tok in argv)


def _positional_args(argv: list[str]) -> list[str]:
    """argv with `--flag value` / `--flag=value` / bare `--flag` pairs stripped."""
    out = []
    skip_next = False
    for tok in argv:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-"):
            if "=" not in tok and not tok.startswith("--all"):
                skip_next = True  # best-effort: assume `--flag value` form
            continue
        out.append(tok)
    return out


def _classify_kubectl(argv: list[str]) -> Decision:
    tool = "kubectl"
    if not argv:
        return Decision(Tier.APPROVE, "empty kubectl invocation", tool, argv)

    verb = argv[0]
    namespace = _flag_value(argv, {"-n", "--namespace"})
    all_namespaces = _has_flag(argv, {"-A", "--all-namespaces"})

    if verb == "config":
        sub = argv[1] if len(argv) > 1 else ""
        if sub in {"view", "get-contexts", "current-context"}:
            return Decision(Tier.SAFE, "read-only kubeconfig inspection", tool, argv)
        return Decision(Tier.APPROVE, "kubeconfig mutation (context/cluster change)", tool, argv)

    if verb == "rollout":
        # `rollout status`/`history` only watch/inspect — a very common
        # day-to-day check that shouldn't need approval just because it
        # shares a verb with `restart`/`undo`/`pause`/`resume`.
        sub = argv[1] if len(argv) > 1 else ""
        if sub in {"status", "history"}:
            return Decision(Tier.SAFE, f"read-only rollout inspection ('rollout {sub}')", tool, argv)
        return Decision(Tier.APPROVE, f"rollout mutation ('rollout {sub}')", tool, argv)

    if verb in _KUBECTL_READ_VERBS:
        return Decision(Tier.SAFE, f"read-only verb '{verb}'", tool, argv)

    if verb == "delete":
        positional = _positional_args(argv[1:])
        resource_type = positional[0].lower() if positional else ""
        names = positional[1:]

        if resource_type in {"namespace", "ns"} and any(n in _CRITICAL_NAMESPACES for n in names):
            return Decision(Tier.PROHIBITED, "delete of a cluster-critical namespace", tool, argv)
        if resource_type in {"crd", "customresourcedefinition"}:
            return Decision(Tier.PROHIBITED, "delete of a CustomResourceDefinition — cluster-wide blast radius", tool, argv)
        if namespace in _CRITICAL_NAMESPACES:
            return Decision(Tier.PROHIBITED, f"mutating a cluster-critical namespace ({namespace})", tool, argv)
        if all_namespaces:
            return Decision(Tier.PROHIBITED, "delete across all namespaces", tool, argv)
        if _has_flag(argv, {"--all"}) and not names:
            return Decision(Tier.PROHIBITED, f"delete --all {resource_type or '<resource>'} with no explicit name", tool, argv)
        if _has_flag(argv, {"--force"}) and _flag_value(argv, {"--grace-period"}) == "0":
            return Decision(Tier.PROHIBITED, "force delete with --grace-period=0 skips graceful termination", tool, argv)

        reason = "delete mutates cluster state"
        if namespace and _PROD_NAMESPACE_RE.search(namespace):
            reason += f" in a production-looking namespace ({namespace})"
        return Decision(Tier.APPROVE, reason, tool, argv)

    if verb in _KUBECTL_MUTATE_VERBS:
        if namespace in _CRITICAL_NAMESPACES:
            return Decision(Tier.PROHIBITED, f"mutating a cluster-critical namespace ({namespace})", tool, argv)
        reason = f"mutating verb '{verb}'"
        if namespace and _PROD_NAMESPACE_RE.search(namespace):
            reason += f" in a production-looking namespace ({namespace})"
        return Decision(Tier.APPROVE, reason, tool, argv)

    # Unknown verb: default closed — never auto-run something unrecognized.
    return Decision(Tier.APPROVE, f"unrecognized kubectl verb '{verb}' — defaulting to approval-required", tool, argv)


_TERRAFORM_READ = {
    "plan", "show", "validate", "output", "fmt", "version", "graph", "providers",
    "init", "get",  # setup/fetch — don't touch real infra, needed on every fresh checkout
}
_TERRAFORM_APPROVE = {"apply", "import", "refresh", "taint", "untaint"}
_TERRAFORM_PROHIBITED = {"destroy"}


def _classify_terraform(argv: list[str]) -> Decision:
    tool = "terraform"
    verb = argv[0] if argv else ""
    if verb == "state":
        sub = argv[1] if len(argv) > 1 else ""
        if sub == "list" or sub == "show":
            return Decision(Tier.SAFE, "read-only terraform state inspection", tool, argv)
        return Decision(Tier.APPROVE, f"terraform state mutation ('state {sub}')", tool, argv)
    if verb in _TERRAFORM_READ:
        return Decision(Tier.SAFE, f"read-only terraform verb '{verb}'", tool, argv)
    if verb in _TERRAFORM_PROHIBITED:
        return Decision(Tier.PROHIBITED, "terraform destroy is irreversible — run it yourself outside sema", tool, argv)
    if verb in _TERRAFORM_APPROVE:
        return Decision(Tier.APPROVE, f"terraform verb '{verb}' changes real infrastructure", tool, argv)
    return Decision(Tier.APPROVE, f"unrecognized terraform verb '{verb}' — defaulting to approval-required", tool, argv)


_AWS_READ_PREFIXES = ("describe-", "get-", "list-")
_AWS_PROHIBITED_SUBSTRINGS = ("delete-account", "delete-organization", "remove-root", "delete-user-root")


def _classify_aws(argv: list[str]) -> Decision:
    tool = "aws"
    # aws <service> <action> [...]
    action = argv[1] if len(argv) > 1 else ""
    if any(s in action for s in _AWS_PROHIBITED_SUBSTRINGS):
        return Decision(Tier.PROHIBITED, f"'{action}' is account-level and irreversible", tool, argv)
    if action.startswith(_AWS_READ_PREFIXES):
        return Decision(Tier.SAFE, f"read-only AWS action '{action}'", tool, argv)
    if not action:
        return Decision(Tier.APPROVE, "empty/incomplete aws invocation", tool, argv)
    return Decision(Tier.APPROVE, f"mutating AWS action '{action}'", tool, argv)


_HELM_READ = {"status", "get", "history", "list", "diff", "show", "template"}
_HELM_APPROVE = {"upgrade", "install", "rollback"}
_HELM_APPROVE_DESTRUCTIVE = {"uninstall", "delete"}


def _classify_helm(argv: list[str]) -> Decision:
    tool = "helm"
    verb = argv[0] if argv else ""
    if verb in _HELM_READ:
        return Decision(Tier.SAFE, f"read-only helm verb '{verb}'", tool, argv)
    if verb in _HELM_APPROVE_DESTRUCTIVE:
        return Decision(Tier.APPROVE, f"helm '{verb}' removes a release — needs explicit consent", tool, argv)
    if verb in _HELM_APPROVE:
        return Decision(Tier.APPROVE, f"helm verb '{verb}' changes a live release", tool, argv)
    return Decision(Tier.APPROVE, f"unrecognized helm verb '{verb}' — defaulting to approval-required", tool, argv)


_DISPATCH = {
    "kubectl": _classify_kubectl,
    "terraform": _classify_terraform,
    "aws": _classify_aws,
    "helm": _classify_helm,
}


def classify(argv: list[str]) -> Decision:
    """Classify a proposed command's tier. ``argv`` includes the binary name."""
    if not argv:
        return Decision(Tier.APPROVE, "empty command", "unknown", argv)

    binary = argv[0].rsplit("/", 1)[-1]
    handler = _DISPATCH.get(binary)
    if handler is None:
        return Decision(Tier.APPROVE, f"'{binary}' is not a recognized devops tool — defaulting to approval-required", binary, argv)
    return handler(argv[1:])
