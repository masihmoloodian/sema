from sema.devops import k8s_secrets


def test_touches_secret_resource_true_for_get_secret():
    assert k8s_secrets.touches_secret_resource(["get", "secret", "db-creds", "-n", "staging"])
    assert k8s_secrets.touches_secret_resource(["get", "secrets"])
    assert k8s_secrets.touches_secret_resource(["get", "secret/db-creds"])


def test_touches_secret_resource_false_for_other_resources_or_verbs():
    assert not k8s_secrets.touches_secret_resource(["get", "pods"])
    assert not k8s_secrets.touches_secret_resource(["describe", "secret", "db-creds"])
    assert not k8s_secrets.touches_secret_resource(["delete", "secret", "db-creds"])
    assert not k8s_secrets.touches_secret_resource([])


YAML_SECRET = """apiVersion: v1
data:
  AWS_ACCESS_KEY_ID: QUtJQUlPU0ZPRE5ON0VYQU1QTEU=
  password: U3VwZXJTZWNyZXQxMjM=
kind: Secret
metadata:
  creationTimestamp: "2026-07-20T08:43:39Z"
  name: db-creds
  namespace: staging
  resourceVersion: "357"
  uid: c898f389-132f-43d7-bb6f-0455c3e97f9b
type: Opaque
"""


def test_redact_yaml_secret_strips_values_not_structure():
    out = k8s_secrets.redact_secret_output(YAML_SECRET)
    assert "QUtJQUlPU0ZPRE5ON0VYQU1QTEU=" not in out
    assert "U3VwZXJTZWNyZXQxMjM=" not in out
    # structure/metadata survives untouched — this was the over-redaction bug
    assert "apiVersion: v1" in out
    assert "kind: Secret" in out
    assert "namespace: staging" in out
    assert "uid: c898f389-132f-43d7-bb6f-0455c3e97f9b" in out
    assert "resourceVersion: \"357\"" in out


JSON_SECRET = """{
  "apiVersion": "v1",
  "data": {
    "AWS_ACCESS_KEY_ID": "QUtJQUlPU0ZPRE5ON0VYQU1QTEU=",
    "password": "U3VwZXJTZWNyZXQxMjM="
  },
  "kind": "Secret",
  "metadata": {"name": "db-creds", "namespace": "staging"}
}"""


def test_redact_json_secret_strips_values_not_structure():
    out = k8s_secrets.redact_secret_output(JSON_SECRET)
    assert "QUtJQUlPU0ZPRE5ON0VYQU1QTEU=" not in out
    assert '"kind": "Secret"' in out
    assert '"namespace": "staging"' in out


def test_redact_jsonpath_bare_value_with_argv_context():
    argv = ["get", "secret", "db-creds", "-n", "staging", "-o", "jsonpath={.data.password}"]
    out = k8s_secrets.redact_secret_output("QUtJQUlPU0ZPRE5ON0VYQU1QTEU=", argv)
    assert "QUtJQUlPU0ZPRE5ON0VYQU1QTEU=" not in out


def test_redact_go_template_bare_value_with_argv_context():
    argv = ["get", "secret", "db-creds", "-o", "go-template={{.data.password}}"]
    out = k8s_secrets.redact_secret_output("QUtJQUlPU0ZPRE5ON0VYQU1QTEU=", argv)
    assert "QUtJQUlPU0ZPRE5ON0VYQU1QTEU=" not in out


def test_bare_value_without_argv_is_left_alone():
    # No argv context — can't confirm this came from a bare-extraction
    # format, so don't guess. This was the old (broken) default behavior.
    out = k8s_secrets.redact_secret_output("QUtJQUlPU0ZPRE5ON0VYQU1QTEU=")
    assert out == "QUtJQUlPU0ZPRE5ON0VYQU1QTEU="


def test_connection_error_text_is_not_over_redacted():
    # Regression test: found via real testing through the VS Code extension.
    # A `kubectl get secret ... -o yaml` against an unreachable cluster
    # returns a plain-English connection error, not YAML/JSON. The old
    # blanket base64-blob fallback fired on this anyway — "couldn't",
    # "server", "resource" are all valid base64 alphabet (6+ letters decode
    # without error under base64.b64decode(validate=True), which only
    # checks charset/padding, not whether the bytes mean anything) — and
    # redacted plain error text as if it were secret data.
    argv = ["get", "secret", "db-creds", "-n", "staging", "-o", "yaml"]
    text = (
        "Unhandled Error: couldn't get current server API group list: "
        "the server could not find the requested resource\n"
        "Error from server (NotFound): the server could not find the requested resource\n"
    )
    out = k8s_secrets.redact_secret_output(text, argv)
    assert out == text  # untouched — no Secret data was ever in this output


def test_describe_style_text_without_data_block_is_untouched():
    argv = ["get", "secret", "db-creds", "-o", "wide"]
    text = "NAME       TYPE     DATA   AGE\ndb-creds   Opaque   2      3m\n"
    out = k8s_secrets.redact_secret_output(text, argv)
    assert out == text


def test_empty_output_untouched():
    assert k8s_secrets.redact_secret_output("") == ""
    assert k8s_secrets.redact_secret_output("   ") == "   "
