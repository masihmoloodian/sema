from sema.devops.policy import Tier, classify


def test_kubectl_get_is_safe():
    d = classify(["kubectl", "get", "pods", "-A"])
    assert d.tier is Tier.SAFE


def test_kubectl_describe_is_safe():
    d = classify(["kubectl", "describe", "pod", "web-1", "-n", "prod"])
    assert d.tier is Tier.SAFE  # reading prod is informational, not mutating


def test_kubectl_scale_is_approve():
    d = classify(["kubectl", "scale", "deployment/web", "--replicas=2", "-n", "staging"])
    assert d.tier is Tier.APPROVE


def test_kubectl_apply_is_approve():
    d = classify(["kubectl", "apply", "-f", "deploy.yaml"])
    assert d.tier is Tier.APPROVE


def test_kubectl_delete_ordinary_resource_is_approve():
    d = classify(["kubectl", "delete", "pod", "web-1", "-n", "staging"])
    assert d.tier is Tier.APPROVE


def test_kubectl_delete_kube_system_namespace_is_prohibited():
    d = classify(["kubectl", "delete", "namespace", "kube-system"])
    assert d.tier is Tier.PROHIBITED


def test_kubectl_delete_into_kube_system_via_flag_is_prohibited():
    d = classify(["kubectl", "delete", "configmap", "foo", "-n", "kube-system"])
    assert d.tier is Tier.PROHIBITED


def test_kubectl_delete_crd_is_prohibited():
    d = classify(["kubectl", "delete", "crd", "widgets.example.com"])
    assert d.tier is Tier.PROHIBITED


def test_kubectl_delete_all_namespaces_is_prohibited():
    d = classify(["kubectl", "delete", "pods", "--all-namespaces"])
    assert d.tier is Tier.PROHIBITED


def test_kubectl_delete_all_with_no_name_is_prohibited():
    d = classify(["kubectl", "delete", "deployments", "--all", "-n", "staging"])
    assert d.tier is Tier.PROHIBITED


def test_kubectl_force_delete_grace_zero_is_prohibited():
    d = classify(["kubectl", "delete", "pod", "web-1", "--force", "--grace-period=0"])
    assert d.tier is Tier.PROHIBITED


def test_kubectl_config_view_is_safe():
    d = classify(["kubectl", "config", "view"])
    assert d.tier is Tier.SAFE


def test_kubectl_config_use_context_is_approve():
    d = classify(["kubectl", "config", "use-context", "prod-cluster"])
    assert d.tier is Tier.APPROVE


def test_kubectl_rollout_status_is_safe():
    d = classify(["kubectl", "rollout", "status", "deployment/web", "-n", "staging"])
    assert d.tier is Tier.SAFE


def test_kubectl_rollout_history_is_safe():
    d = classify(["kubectl", "rollout", "history", "deployment/web"])
    assert d.tier is Tier.SAFE


def test_kubectl_rollout_restart_is_approve():
    d = classify(["kubectl", "rollout", "restart", "deployment/web", "-n", "staging"])
    assert d.tier is Tier.APPROVE


def test_kubectl_rollout_undo_is_approve():
    d = classify(["kubectl", "rollout", "undo", "deployment/web"])
    assert d.tier is Tier.APPROVE


def test_kubectl_unknown_verb_defaults_approve():
    d = classify(["kubectl", "some-future-verb", "x"])
    assert d.tier is Tier.APPROVE


def test_terraform_init_is_safe():
    assert classify(["terraform", "init", "-input=false"]).tier is Tier.SAFE


def test_terraform_plan_is_safe():
    assert classify(["terraform", "plan"]).tier is Tier.SAFE


def test_terraform_apply_is_approve():
    assert classify(["terraform", "apply"]).tier is Tier.APPROVE


def test_terraform_destroy_is_prohibited():
    assert classify(["terraform", "destroy"]).tier is Tier.PROHIBITED


def test_terraform_state_list_is_safe():
    assert classify(["terraform", "state", "list"]).tier is Tier.SAFE


def test_terraform_state_rm_is_approve():
    assert classify(["terraform", "state", "rm", "aws_instance.x"]).tier is Tier.APPROVE


def test_aws_describe_is_safe():
    assert classify(["aws", "ec2", "describe-instances"]).tier is Tier.SAFE


def test_aws_create_is_approve():
    assert classify(["aws", "ec2", "create-security-group"]).tier is Tier.APPROVE


def test_aws_delete_account_is_prohibited():
    assert classify(["aws", "organizations", "delete-account", "--account-id", "123"]).tier is Tier.PROHIBITED


def test_helm_status_is_safe():
    assert classify(["helm", "status", "myrelease"]).tier is Tier.SAFE


def test_helm_upgrade_is_approve():
    assert classify(["helm", "upgrade", "myrelease", "chart/"]).tier is Tier.APPROVE


def test_unrecognized_tool_defaults_approve():
    d = classify(["rm", "-rf", "/"])
    assert d.tier is Tier.APPROVE


def test_empty_command_defaults_approve():
    assert classify([]).tier is Tier.APPROVE
