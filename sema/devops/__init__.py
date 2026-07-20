"""sema devops guard — analyze-first execution gate for infra commands.

See docs/devops-guard-plan.md for the design. Every command proposed by an
AI provider is classified (policy), scrubbed of secrets (secrets/guard), and
only then allowed to run, held for human approval, or refused outright.
"""
