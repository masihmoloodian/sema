"""Permission policy resolution and session-scoped grants."""

import asyncio


from sema.agent.permissions import (
    ALLOW,
    ALLOW_ALWAYS,
    DENY,
    ApprovalRequest,
    PermissionManager,
    Policy,
    auto_allow,
    auto_deny,
    default_policies,
)


def check(manager, tool="bash", prefix=None):
    return asyncio.run(
        manager.check(ApprovalRequest(tool=tool, summary="s", prefix=prefix))
    )


def test_default_policies_split_read_from_write():
    policies = default_policies()
    assert policies["search_code"] is Policy.ALLOW
    assert policies["read_file"] is Policy.ALLOW
    assert policies["write_file"] is Policy.ASK
    assert policies["bash"] is Policy.ASK


def test_allow_policy_needs_no_asker():
    manager = PermissionManager(policies=default_policies())
    assert check(manager, "read_file") is True


def test_deny_policy_always_refuses():
    manager = PermissionManager(policies={"bash": Policy.DENY}, asker=_always_allow)
    assert check(manager) is False


def test_ask_without_an_asker_fails_closed():
    manager = PermissionManager(policies=default_policies(), asker=None)
    assert check(manager) is False


def test_bypass_skips_the_prompt():
    calls = []

    async def asker(request):
        calls.append(request)
        return DENY

    manager = PermissionManager(policies=default_policies(), asker=asker, bypass=True)
    assert check(manager) is True
    assert calls == []


def test_allow_always_grants_the_prefix_for_the_session():
    prompts = []

    async def asker(request):
        prompts.append(request.prefix)
        return ALLOW_ALWAYS

    manager = PermissionManager(policies=default_policies(), asker=asker)
    assert check(manager, prefix="npm test") is True
    # Second call with the same prefix must not re-prompt.
    assert check(manager, prefix="npm test") is True
    assert prompts == ["npm test"]


def test_a_different_prefix_still_prompts():
    prompts = []

    async def asker(request):
        prompts.append(request.prefix)
        return ALLOW_ALWAYS

    manager = PermissionManager(policies=default_policies(), asker=asker)
    check(manager, prefix="npm test")
    check(manager, prefix="rm -rf")
    assert prompts == ["npm test", "rm -rf"]


def test_allow_always_without_a_prefix_grants_the_whole_tool():
    prompts = []

    async def asker(request):
        prompts.append(request.tool)
        return ALLOW_ALWAYS

    manager = PermissionManager(policies=default_policies(), asker=asker)
    assert check(manager, "write_file") is True
    assert check(manager, "write_file") is True
    assert prompts == ["write_file"]


def test_plain_allow_does_not_persist():
    prompts = []

    async def asker(request):
        prompts.append(request.tool)
        return ALLOW

    manager = PermissionManager(policies=default_policies(), asker=asker)
    check(manager, "write_file")
    check(manager, "write_file")
    assert len(prompts) == 2


def test_unknown_tool_uses_the_default_policy():
    manager = PermissionManager(policies={}, default_policy=Policy.DENY)
    assert check(manager, "mystery") is False


def test_auto_helpers():
    assert check(auto_allow()) is True
    assert check(auto_deny()) is False


async def _always_allow(_request):
    return ALLOW
