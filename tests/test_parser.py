"""Tests for language parsers."""

import pytest
from pathlib import Path
from sema.indexer.parser import parse_file, register, get_supported_extensions

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "example-repo"


def test_parse_typescript_functions():
    chunks = parse_file(FIXTURE_REPO / "src" / "auth" / "jwt.ts", FIXTURE_REPO)
    names = [c.name for c in chunks]
    assert "generateToken" in names
    assert "validateToken" in names
    assert "refreshToken" in names
    assert "validateExpiry" in names


def test_parse_typescript_exports():
    chunks = parse_file(FIXTURE_REPO / "src" / "auth" / "jwt.ts", FIXTURE_REPO)
    by_name = {c.name: c for c in chunks}
    assert by_name["generateToken"].exports is True
    assert by_name["validateToken"].exports is True
    assert by_name["validateExpiry"].exports is False


def test_parse_typescript_class():
    chunks = parse_file(FIXTURE_REPO / "src" / "api" / "routes.ts", FIXTURE_REPO)
    types = {c.chunk_type for c in chunks}
    names = {c.name for c in chunks}
    assert "class" in types
    assert "Router" in names
    assert "get" in names
    assert "post" in names


def test_parse_python_class_and_methods():
    chunks = parse_file(FIXTURE_REPO / "services" / "session.py", FIXTURE_REPO)
    names = [c.name for c in chunks]
    assert "SessionService" in names
    assert "create" in names
    assert "invalidate" in names
    assert "get" in names


def test_parse_python_class_type():
    chunks = parse_file(FIXTURE_REPO / "services" / "session.py", FIXTURE_REPO)
    by_name = {c.name: c for c in chunks}
    assert by_name["SessionService"].chunk_type == "class"
    assert by_name["create"].chunk_type == "method"


def test_parse_go_functions():
    chunks = parse_file(FIXTURE_REPO / "handlers" / "auth.go", FIXTURE_REPO)
    names = [c.name for c in chunks]
    assert "HandleLogin" in names
    assert "HandleRefresh" in names
    assert "HandleLogout" in names


def test_parse_go_exports():
    chunks = parse_file(FIXTURE_REPO / "handlers" / "auth.go", FIXTURE_REPO)
    by_name = {c.name: c for c in chunks}
    assert by_name["HandleLogin"].exports is True


def test_parse_unsupported_extension_returns_empty(tmp_path):
    f = tmp_path / "test.rb"
    f.write_text("def hello; end")
    assert parse_file(f, tmp_path) == []


def test_parse_empty_file_returns_empty(tmp_path):
    f = tmp_path / "empty.ts"
    f.write_text("   ")
    assert parse_file(f, tmp_path) == []


def test_chunk_has_correct_file_path():
    chunks = parse_file(FIXTURE_REPO / "src" / "auth" / "jwt.ts", FIXTURE_REPO)
    assert all(c.file == "src/auth/jwt.ts" for c in chunks)


def test_chunk_has_valid_line_numbers():
    chunks = parse_file(FIXTURE_REPO / "src" / "auth" / "jwt.ts", FIXTURE_REPO)
    for c in chunks:
        assert c.start_line >= 1
        assert c.end_line >= c.start_line


# --- Markdown parser ---

def test_parse_markdown_sections():
    chunks = parse_file(FIXTURE_REPO / "README.md", FIXTURE_REPO)
    names = [c.name for c in chunks]
    assert "Authentication" in names
    assert "Session Management" in names
    assert "API Routes" in names


def test_parse_markdown_chunk_type():
    chunks = parse_file(FIXTURE_REPO / "README.md", FIXTURE_REPO)
    assert all(c.chunk_type == "section" for c in chunks)


def test_parse_markdown_language():
    chunks = parse_file(FIXTURE_REPO / "README.md", FIXTURE_REPO)
    assert all(c.language == "markdown" for c in chunks)


def test_parse_markdown_no_headings(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("Just some plain text\nwith no headings.")
    chunks = parse_file(f, tmp_path)
    assert len(chunks) == 1
    assert chunks[0].chunk_type == "section"


# --- Generic text parser ---

def test_parse_json_produces_chunks():
    chunks = parse_file(FIXTURE_REPO / "config.json", FIXTURE_REPO)
    assert len(chunks) >= 1
    assert all(c.chunk_type == "section" for c in chunks)


def test_parse_generic_env(tmp_path):
    f = tmp_path / ".env"
    f.write_text("DATABASE_URL=postgres://localhost/db\nREDIS_URL=redis://localhost\n")
    # .env is registered by filename, not extension
    chunks = parse_file(f, tmp_path)
    assert len(chunks) >= 1


def test_parse_generic_yaml(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("database:\n  host: localhost\n  port: 5432\n")
    chunks = parse_file(f, tmp_path)
    assert len(chunks) >= 1


def test_parse_generic_shell(tmp_path):
    f = tmp_path / "deploy.sh"
    f.write_text("#!/bin/bash\necho 'Deploying...'\nnpm run build\n")
    chunks = parse_file(f, tmp_path)
    assert len(chunks) >= 1


# --- Registry ---

def test_unregistered_extension_returns_empty(tmp_path):
    f = tmp_path / "test.rb"
    f.write_text("def hello; end")
    assert parse_file(f, tmp_path) == []


def test_parse_typed_arrow_function_name(tmp_path):
    # Typed arrow: `const foo: (x: T) => R = (x) => {...}` — name must not be "unknown"
    f = tmp_path / "typed.ts"
    f.write_text(
        "export const createLinter: (schema: string) => boolean = (schema) => true;\n"
    )
    chunks = parse_file(f, tmp_path)
    assert len(chunks) == 1
    assert chunks[0].name == "createLinter"
    assert chunks[0].name != "unknown"


def test_register_adds_extension(tmp_path):
    from sema.store.schema import Chunk

    def dummy_parser(source: str, file_path: str) -> list[Chunk]:
        return [Chunk(id=f"{file_path}::test:1", file=file_path, language="ruby",
                      chunk_type="function", name="test", signature="test()",
                      body=source, start_line=1, end_line=1)]

    register([".rb"], dummy_parser)
    assert ".rb" in get_supported_extensions()

    f = tmp_path / "hello.rb"
    f.write_text("def hello; end")
    chunks = parse_file(f, tmp_path)
    assert len(chunks) == 1
    assert chunks[0].language == "ruby"
