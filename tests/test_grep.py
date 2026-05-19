"""Tests for grep_symbol and is_definition_line."""

import pytest
from pathlib import Path
from sema.utils.grep import grep_symbol, is_definition_line


# ── grep_symbol ───────────────────────────────────────────────────────────────

def test_finds_function_call(tmp_path):
    (tmp_path / "app.ts").write_text(
        "import { validateToken } from './auth';\n"
        "const user = validateToken(req.headers.token);\n"
    )
    results = grep_symbol("validateToken", tmp_path)
    assert len(results) == 2
    assert all(r["file"] == "app.ts" for r in results)


def test_word_boundary_no_partial_match(tmp_path):
    (tmp_path / "utils.ts").write_text(
        "validateTokenExpiry(token);\n"   # should NOT match
        "validateToken(token);\n"          # should match
    )
    results = grep_symbol("validateToken", tmp_path)
    assert len(results) == 1
    assert "validateToken(token)" in results[0]["context"]


def test_returns_correct_line_numbers(tmp_path):
    (tmp_path / "service.py").write_text(
        "x = 1\n"
        "y = 2\n"
        "result = myFunc(x)\n"   # line 3
    )
    results = grep_symbol("myFunc", tmp_path)
    assert len(results) == 1
    assert results[0]["line"] == 3


def test_groups_multiple_hits_in_same_file(tmp_path):
    (tmp_path / "routes.ts").write_text(
        "router.get('/', authenticate);\n"
        "router.post('/', authenticate);\n"
        "router.delete('/', authenticate);\n"
    )
    results = grep_symbol("authenticate", tmp_path)
    assert len(results) == 3


def test_respects_max_results(tmp_path):
    lines = "\n".join(f"call_{i}(myFunc);" for i in range(50))
    (tmp_path / "big.ts").write_text(lines)
    results = grep_symbol("myFunc", tmp_path, max_results=10)
    assert len(results) == 10


def test_returns_empty_for_no_match(tmp_path):
    (tmp_path / "app.ts").write_text("function hello() {}\n")
    results = grep_symbol("nonExistent", tmp_path)
    assert results == []


def test_searches_across_multiple_files(tmp_path):
    (tmp_path / "a.ts").write_text("doThing();\n")
    (tmp_path / "b.py").write_text("do_thing()\n")
    (tmp_path / "c.go").write_text("doThing()\n")
    results = grep_symbol("doThing", tmp_path)
    files = {r["file"] for r in results}
    assert "a.ts" in files
    assert "c.go" in files


def test_skips_node_modules(tmp_path):
    nm = tmp_path / "node_modules" / "lib"
    nm.mkdir(parents=True)
    (nm / "index.ts").write_text("validateToken();\n")
    (tmp_path / "app.ts").write_text("validateToken();\n")
    results = grep_symbol("validateToken", tmp_path)
    assert all("node_modules" not in r["file"] for r in results)


# ── is_definition_line ────────────────────────────────────────────────────────

@pytest.mark.parametrize("line", [
    "function validateToken(token: string): User {",
    "export function validateToken(token: string) {",
    "export async function validateToken(token) {",
    "const validateToken = (token) => {",
    "export const validateToken: Validator = (token) => {",
    "class validateToken {",
    "def validateToken(self, token):",
    "class validateToken(Base):",
    "func validateToken(token string) (*User, error) {",
    "func (s *Service) validateToken(token string) {",
])
def test_is_definition_line_true(line):
    assert is_definition_line(line, "validateToken") is True


@pytest.mark.parametrize("line", [
    "const user = validateToken(req.token);",
    "if (!validateToken(token)) return;",
    "import { validateToken } from './auth';",
    "type Result = ReturnType<typeof validateToken>;",
    "// calls validateToken internally",
])
def test_is_definition_line_false(line):
    assert is_definition_line(line, "validateToken") is False
