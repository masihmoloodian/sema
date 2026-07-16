"""Tests for fast, filtered project discovery."""

from sema.utils.file_walker import walk_project


def test_walk_project_prunes_excluded_directories(tmp_path, monkeypatch):
    src = tmp_path / "src"
    deps = tmp_path / "node_modules"
    src.mkdir()
    deps.mkdir()
    (src / "app.ts").write_text("export function app() {}")
    (deps / "dependency.ts").write_text("export function dependency() {}")

    visited: list[str] = []

    def fake_walk(root, topdown=True):
        dirs = ["node_modules", "src"]
        visited.append(str(root))
        yield str(root), dirs, []
        # A top-down walker only descends into names left in the mutable list.
        if "node_modules" in dirs:
            visited.append(str(deps))
            yield str(deps), [], ["dependency.ts"]
        if "src" in dirs:
            visited.append(str(src))
            yield str(src), [], ["app.ts"]

    monkeypatch.setattr("sema.utils.file_walker.os.walk", fake_walk)
    files = list(walk_project(tmp_path))

    assert files == [src / "app.ts"]
    assert str(deps) not in visited


def test_walk_project_prunes_gitignored_directory(tmp_path):
    (tmp_path / ".gitignore").write_text("generated/\n")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "large.ts").write_text("export const generated = true")
    (tmp_path / "kept.ts").write_text("export const kept = true")

    assert list(walk_project(tmp_path)) == [tmp_path / ".gitignore", tmp_path / "kept.ts"]


def test_walk_project_prunes_generated_output_at_any_depth(tmp_path):
    source = tmp_path / "packages" / "web" / "src" / "app.ts"
    generated = tmp_path / "packages" / "web" / "out" / "app.js"
    source.parent.mkdir(parents=True)
    generated.parent.mkdir(parents=True)
    source.write_text("export const source = true")
    generated.write_text("const generated = true")

    assert list(walk_project(tmp_path)) == [source]
