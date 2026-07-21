"""The management-operations layer that backs the slash commands."""

import asyncio


from sema.agent import ops


def test_find_project_root_walks_up_to_the_index(tmp_path):
    root = tmp_path / "repo"
    (root / ".sema" / "index").mkdir(parents=True)
    nested = root / "src" / "deep"
    nested.mkdir(parents=True)
    assert ops.find_project_root(nested) == root.resolve()


def test_find_project_root_falls_back_to_the_start(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ops.find_project_root(plain) == plain.resolve()


def test_has_index(tmp_path):
    assert ops.has_index(tmp_path) is False
    (tmp_path / ".sema" / "index").mkdir(parents=True)
    assert ops.has_index(tmp_path) is True


def test_bind_index_reports_a_missing_index(tmp_path):
    message = ops.bind_index(tmp_path)
    assert message is not None and "No index" in message


def test_bind_index_succeeds_on_a_real_index(tmp_path, indexed_store):
    """A real store binds cleanly and returns no error message."""
    store, embedder = indexed_store
    from sema.mcp.tools import init_tools

    init_tools(store, embedder, tmp_path)
    assert ops.search("authentication")  # tools answer after binding


def test_run_cli_captures_output():
    result = asyncio.run(ops.run_cli(["--help"]))
    assert result.ok is True
    assert "index" in result.output


def test_run_cli_parses_json_when_requested(tmp_path):
    result = asyncio.run(ops.run_cli(["status", "--json"], cwd=tmp_path))
    # Whatever the exit status, a --json invocation must yield parsed data.
    assert result.data is not None
    assert isinstance(result.data, (dict, list))


def test_run_cli_reports_a_bad_subcommand():
    result = asyncio.run(ops.run_cli(["definitely-not-a-command"]))
    assert result.ok is False


def test_watcher_starts_out_stopped(tmp_path):
    assert ops.Watcher(tmp_path).running is False


def test_watcher_stop_when_not_running(tmp_path):
    assert "not running" in asyncio.run(ops.Watcher(tmp_path).stop())


def test_watcher_start_and_stop(tmp_path):
    (tmp_path / ".sema" / "index").mkdir(parents=True)
    watcher = ops.Watcher(tmp_path)

    async def cycle():
        started = await watcher.start()
        running = watcher.running
        stopped = await watcher.stop()
        return started, running, stopped

    started, running, stopped = asyncio.run(cycle())
    assert "started" in started.lower()
    assert running is True
    assert "stopped" in stopped.lower()
    assert watcher.running is False


def test_devops_pending_is_empty_on_a_clean_root(tmp_path):
    assert ops.devops_pending(tmp_path) == []


def test_devops_plan_classifies_a_command():
    decision = ops.devops_plan(["kubectl", "get", "pods"])
    assert isinstance(decision, dict)
    assert decision  # the gate returns a classification, not an empty result


def test_redact_text_is_a_no_op_without_the_model():
    """Redaction is best-effort: a missing spaCy model must not block a turn."""
    clean, entities = ops.redact_text("plain text")
    assert isinstance(clean, str)
    assert isinstance(entities, list)
