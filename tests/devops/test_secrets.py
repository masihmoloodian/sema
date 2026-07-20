from sema.devops.secrets import redact_secrets


def test_redacts_aws_access_key():
    result = redact_secrets("export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
    assert "AKIAIOSFODNN7EXAMPLE" not in result["text"]
    assert result["found"]["AWS_ACCESS_KEY_ID"] == 1


def test_redacts_private_key_block():
    text = "-----BEGIN PRIVATE KEY-----\nMIIEvQ...\n-----END PRIVATE KEY-----"
    result = redact_secrets(text)
    assert "MIIEvQ" not in result["text"]
    assert result["found"]["PRIVATE_KEY"] == 1


def test_redacts_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    result = redact_secrets(f"Authorization: Bearer {jwt}")
    assert jwt not in result["text"]
    assert result["found"]["JWT"] == 1


def test_redacts_connection_string():
    result = redact_secrets("DATABASE_URL=postgres://admin:hunter2@db.internal:5432/prod")
    assert "hunter2" not in result["text"]
    assert result["found"]["CONNECTION_STRING"] == 1


def test_redacts_email():
    result = redact_secrets("contact devops@example.com for access")
    assert "devops@example.com" not in result["text"]
    assert result["found"]["EMAIL"] == 1


def test_leaves_clean_text_untouched():
    text = "deployment.apps/web scaled"
    result = redact_secrets(text)
    assert result["text"] == text
    assert result["found"] == {}


def test_empty_text():
    assert redact_secrets("") == {"text": "", "found": {}}


def test_redacts_multiple_secrets_in_one_blob():
    text = "key=AKIAIOSFODNN7EXAMPLE mail=a@b.com"
    result = redact_secrets(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in result["text"]
    assert "a@b.com" not in result["text"]
    assert result["found"]["AWS_ACCESS_KEY_ID"] == 1
    assert result["found"]["EMAIL"] == 1
