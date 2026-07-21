"""Tool behavior, with emphasis on the two structural guardrails:
path confinement and edit staleness."""

import asyncio

import pytest

from sema.agent.permissions import (
    PermissionManager,
    auto_allow,
    auto_deny,
    default_policies,
)
from sema.agent.tools import (
    READ_ONLY_TOOLS,
    ToolContext,
    ToolError,
    build_tools,
    command_binary,
    command_prefix,
    execute,
    _edit_file,
    _glob,
    _grep,
    _read_file,
    _write_file,
)


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def hello():\n    return 1\n")
    (tmp_path / "README.md").write_text("# demo\n")
    return ToolContext(root=tmp_path, permissions=auto_allow())


# ── path confinement ────────────────────────────────────────────────────────


def test_resolve_accepts_paths_inside_the_root(ctx):
    assert ctx.resolve("src/app.py") == (ctx.root / "src" / "app.py").resolve()


def test_resolve_blocks_parent_traversal(ctx):
    with pytest.raises(ToolError, match="escapes the project root"):
        ctx.resolve("../../etc/passwd")


def test_resolve_blocks_absolute_outside_paths(ctx):
    with pytest.raises(ToolError, match="escapes the project root"):
        ctx.resolve("/etc/passwd")


def test_resolve_blocks_symlink_escape(ctx, tmp_path):
    """The check is on the resolved path, so a symlink out is caught."""
    outside = tmp_path.parent / "outside_secret"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("classified")
    link = ctx.root / "escape"
    link.symlink_to(outside)
    with pytest.raises(ToolError, match="escapes the project root"):
        ctx.resolve("escape/secret.txt")


def test_resolve_blocks_encoded_traversal_inside_a_longer_path(ctx):
    with pytest.raises(ToolError):
        ctx.resolve("src/../../../tmp/evil")


def test_root_itself_is_allowed(ctx):
    assert ctx.resolve(".") == ctx.root.resolve()


# ── read / write ────────────────────────────────────────────────────────────


def test_read_file_returns_numbered_lines(ctx):
    out = _read_file(ctx, "src/app.py")
    assert out.startswith("1\tdef hello():")
    assert "2\t    return 1" in out


def test_read_file_honors_offset_and_limit(ctx):
    (ctx.root / "many.txt").write_text("\n".join(str(i) for i in range(100)))
    out = _read_file(ctx, "many.txt", offset=10, limit=5)
    assert out.splitlines()[0] == "11\t10"
    assert "more lines" in out


def test_read_file_rejects_a_directory(ctx):
    with pytest.raises(ToolError, match="Not a file"):
        _read_file(ctx, "src")


def test_read_file_rejects_binary(ctx):
    (ctx.root / "blob.bin").write_bytes(b"\x00\xff\xfe")
    with pytest.raises(ToolError, match="Not a text file"):
        _read_file(ctx, "blob.bin")


def test_write_file_creates_parents(ctx):
    _write_file(ctx, "a/b/c.txt", "hi\n")
    assert (ctx.root / "a" / "b" / "c.txt").read_text() == "hi\n"


def test_write_file_reports_created_vs_updated(ctx):
    assert "Created" in _write_file(ctx, "new.txt", "x")
    assert "Updated" in _write_file(ctx, "new.txt", "y")


# ── edit staleness ──────────────────────────────────────────────────────────


def test_edit_requires_a_prior_read(ctx):
    with pytest.raises(ToolError, match="before editing"):
        _edit_file(ctx, "src/app.py", "return 1", "return 2")


def test_edit_succeeds_after_read(ctx):
    _read_file(ctx, "src/app.py")
    _edit_file(ctx, "src/app.py", "return 1", "return 2")
    assert "return 2" in (ctx.root / "src" / "app.py").read_text()


def test_edit_rejects_a_file_changed_since_the_read(ctx):
    """The whole reason edit is a tool and not `bash sed`."""
    _read_file(ctx, "src/app.py")
    (ctx.root / "src" / "app.py").write_text("def hello():\n    return 99\n")
    with pytest.raises(ToolError, match="changed on disk"):
        _edit_file(ctx, "src/app.py", "return 99", "return 3")


def test_stale_edit_clears_the_hash_so_a_reread_recovers(ctx):
    _read_file(ctx, "src/app.py")
    (ctx.root / "src" / "app.py").write_text("x = 1\n")
    with pytest.raises(ToolError):
        _edit_file(ctx, "src/app.py", "x = 1", "x = 2")
    _read_file(ctx, "src/app.py")
    _edit_file(ctx, "src/app.py", "x = 1", "x = 2")
    assert (ctx.root / "src" / "app.py").read_text() == "x = 2\n"


def test_edit_rejects_missing_old_string(ctx):
    _read_file(ctx, "src/app.py")
    with pytest.raises(ToolError, match="not found"):
        _edit_file(ctx, "src/app.py", "nonexistent", "x")


def test_edit_rejects_ambiguous_match(ctx):
    (ctx.root / "dup.txt").write_text("a\na\n")
    _read_file(ctx, "dup.txt")
    with pytest.raises(ToolError, match="appears 2 times"):
        _edit_file(ctx, "dup.txt", "a", "b")


def test_edit_replace_all_allows_ambiguity(ctx):
    (ctx.root / "dup.txt").write_text("a\na\n")
    _read_file(ctx, "dup.txt")
    result = _edit_file(ctx, "dup.txt", "a", "b", replace_all=True)
    assert (ctx.root / "dup.txt").read_text() == "b\nb\n"
    assert "2 replacement" in result


def test_write_then_edit_needs_no_reread(ctx):
    """write_file records the hash it just wrote, so an immediate edit works."""
    _write_file(ctx, "fresh.txt", "one\n")
    _edit_file(ctx, "fresh.txt", "one", "two")
    assert (ctx.root / "fresh.txt").read_text() == "two\n"


# ── glob / grep ─────────────────────────────────────────────────────────────


def test_glob_matches_by_name_and_relative_path(ctx):
    assert "src/app.py" in _glob(ctx, "*.py")
    assert "README.md" in _glob(ctx, "README.md")


def test_glob_skips_noise_directories(ctx):
    junk = ctx.root / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "index.py").write_text("x")
    assert "node_modules" not in _glob(ctx, "*.py")


def test_grep_reports_path_and_line(ctx):
    out = _grep(ctx, "def hello")
    assert "src/app.py:1:" in out


def test_grep_rejects_a_bad_regex(ctx):
    with pytest.raises(ToolError, match="Invalid regex"):
        _grep(ctx, "([")


def test_grep_reports_no_matches_cleanly(ctx):
    assert "No matches" in _grep(ctx, "zzzz-not-here")


# ── command parsing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command,binary",
    [
        ("npm test", "npm"),
        ("/usr/local/bin/kubectl get pods", "kubectl"),
        ("  ", ""),
        ('echo "unclosed', "echo"),
    ],
)
def test_command_binary(command, binary):
    assert command_binary(command) == binary


def test_command_prefix_is_binary_plus_subcommand():
    assert command_prefix("npm test --watch") == "npm test"
    assert command_prefix("ls") == "ls"


# ── mode gating ─────────────────────────────────────────────────────────────


def test_ask_mode_has_no_tools(ctx):
    assert build_tools(ctx, "ask") == []


def test_plan_mode_is_read_only(ctx):
    names = {t.name for t in build_tools(ctx, "plan")}
    assert names <= READ_ONLY_TOOLS
    assert "write_file" not in names
    assert "bash" not in names
    assert "search_code" in names


def test_agent_mode_has_the_full_set(ctx):
    names = {t.name for t in build_tools(ctx, "agent")}
    assert {"write_file", "edit_file", "bash", "search_code"} <= names


def test_use_index_false_drops_the_sema_tools(ctx):
    names = {t.name for t in build_tools(ctx, "agent", use_index=False)}
    assert "search_code" not in names
    assert "read_file" in names


# ── the permission gate ─────────────────────────────────────────────────────


def _tool(ctx, name):
    return next(t for t in build_tools(ctx, "agent") if t.name == name)


def test_execute_runs_an_allowed_tool(ctx):
    tool = _tool(ctx, "read_file")
    out, is_error = asyncio.run(execute(tool, {"path": "README.md"}, ctx))
    assert is_error is False
    assert "# demo" in out


def test_denied_call_returns_a_result_not_an_exception(ctx):
    """A denial must reach the model as text so it can adapt."""
    ctx.permissions = auto_deny()
    tool = _tool(ctx, "write_file")
    out, is_error = asyncio.run(execute(tool, {"path": "x.txt", "content": "y"}, ctx))
    assert is_error is True
    assert "declined" in out
    assert not (ctx.root / "x.txt").exists()


def test_tool_error_is_reported_not_raised(ctx):
    tool = _tool(ctx, "read_file")
    out, is_error = asyncio.run(execute(tool, {"path": "missing.txt"}, ctx))
    assert is_error is True
    assert "Not a file" in out


def test_bad_arguments_are_reported(ctx):
    tool = _tool(ctx, "read_file")
    out, is_error = asyncio.run(execute(tool, {"wrong": 1}, ctx))
    assert is_error is True
    assert "Invalid arguments" in out


def test_no_asker_fails_closed(ctx):
    """Without an interactive surface, an `ask` tool must not just run."""
    ctx.permissions = PermissionManager(policies=default_policies(), asker=None)
    tool = _tool(ctx, "write_file")
    out, is_error = asyncio.run(execute(tool, {"path": "x.txt", "content": "y"}, ctx))
    assert is_error is True
    assert not (ctx.root / "x.txt").exists()


def test_bash_runs_and_captures_output(ctx):
    tool = _tool(ctx, "bash")
    out, is_error = asyncio.run(execute(tool, {"command": "echo hello"}, ctx))
    assert is_error is False
    assert "hello" in out


def test_bash_reports_a_nonzero_exit(ctx):
    tool = _tool(ctx, "bash")
    out, _ = asyncio.run(execute(tool, {"command": "exit 3"}, ctx))
    assert "exit code 3" in out
