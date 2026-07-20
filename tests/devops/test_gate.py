from sema.devops import gate, runner


def _fake_execute(exit_code=0, stdout="ok", stderr=""):
    def _run(argv, timeout=None):
        return runner.RunResult(exit_code=exit_code, stdout=stdout, stderr=stderr)
    return _run


def test_plan_never_executes(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(runner, "execute", lambda *a, **k: called.append(1))
    result = gate.plan(["kubectl", "get", "pods"])
    assert result["tier"] == "safe"
    assert not called


def test_run_safe_executes_and_redacts_output(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "execute", _fake_execute(stdout="AKIAIOSFODNN7EXAMPLE leaked"))
    result = gate.run(["kubectl", "get", "secret", "x"], tmp_path)
    assert result["outcome"] == "ran"
    assert "AKIAIOSFODNN7EXAMPLE" not in result["stdout"]


def test_run_approve_holds_and_does_not_execute(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(runner, "execute", lambda *a, **k: called.append(1))
    result = gate.run(["kubectl", "scale", "deployment/web", "--replicas=2"], tmp_path)
    assert result["outcome"] == "held"
    assert "approval_id" in result
    assert not called


def test_run_prohibited_never_queues_or_executes(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(runner, "execute", lambda *a, **k: called.append(1))
    result = gate.run(["kubectl", "delete", "namespace", "kube-system"], tmp_path)
    assert result["outcome"] == "prohibited"
    assert not called
    assert gate.pending_actions(tmp_path) == []


def test_approve_flow_executes_held_action(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "execute", _fake_execute(stdout="deployment.apps/web scaled"))
    held = gate.run(["kubectl", "scale", "deployment/web", "--replicas=2"], tmp_path)
    action_id = held["approval_id"]

    result = gate.approve(action_id, tmp_path)
    assert result["outcome"] == "ran"
    assert result["exit_code"] == 0
    assert gate.pending_actions(tmp_path) == []


def test_deny_flow_never_executes(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(runner, "execute", lambda *a, **k: called.append(1))
    held = gate.run(["kubectl", "scale", "deployment/web", "--replicas=2"], tmp_path)
    action_id = held["approval_id"]

    result = gate.deny(action_id, tmp_path, reason="not now")
    assert result["outcome"] == "denied"
    assert not called
    assert gate.pending_actions(tmp_path) == []


def test_approve_unknown_id_errors(tmp_path):
    result = gate.approve("does-not-exist", tmp_path)
    assert result["outcome"] == "error"


def test_audit_log_records_every_decision(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "execute", _fake_execute())
    gate.run(["kubectl", "get", "pods"], tmp_path)
    gate.run(["kubectl", "delete", "namespace", "kube-system"], tmp_path)
    held = gate.run(["kubectl", "scale", "deployment/web", "--replicas=2"], tmp_path)
    gate.deny(held["approval_id"], tmp_path)

    log = gate.audit_log(tmp_path)
    outcomes = [r["outcome"] for r in log]
    assert outcomes == ["ran", "prohibited", "held", "denied"]


def test_run_interactive_approve_tier_prompts_and_runs_on_yes(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "execute", _fake_execute(stdout="deployment.apps/web scaled"))
    result = gate.run_interactive(
        ["kubectl", "scale", "deployment/web", "--replicas=2"], tmp_path, confirm=lambda msg: True,
    )
    assert result["outcome"] == "ran"


def test_run_interactive_approve_tier_denies_on_no(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(runner, "execute", lambda *a, **k: called.append(1))
    result = gate.run_interactive(
        ["kubectl", "scale", "deployment/web", "--replicas=2"], tmp_path, confirm=lambda msg: False,
    )
    assert result["outcome"] == "denied"
    assert not called


def test_run_interactive_prohibited_never_prompts(tmp_path):
    prompted = []
    result = gate.run_interactive(
        ["kubectl", "delete", "namespace", "kube-system"], tmp_path,
        confirm=lambda msg: prompted.append(msg) or True,
    )
    assert result["outcome"] == "prohibited"
    assert not prompted
