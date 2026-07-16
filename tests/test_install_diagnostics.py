"""Regression tests found by a clean wheel installation."""

import json

from click.testing import CliRunner

from sema.cli import _launcher_environment, main
from sema.store.chroma import SemaStore
from sema.store.schema import Chunk


def _chunk(file: str, name: str) -> Chunk:
    return Chunk(
        id=f"{file}::{name}",
        file=file,
        language="python",
        chunk_type="function",
        name=name,
        signature=f"def {name}()",
        body=f"def {name}(): pass",
        start_line=1,
        end_line=1,
    )


def test_uv_tool_launcher_resolves_to_its_real_environment(tmp_path):
    environment = tmp_path / "tools" / "sema-mcp"
    executable = environment / "bin" / "sema"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    launcher = tmp_path / "bin" / "sema"
    launcher.parent.mkdir()
    launcher.symlink_to(executable)

    assert _launcher_environment(launcher) == environment.resolve()


def test_incremental_index_metadata_describes_the_full_store(tmp_path, monkeypatch):
    store = SemaStore(tmp_path / ".sema" / "index")
    store.upsert([_chunk("existing.py", "existing")], [[0.1] * 384])

    def fake_index_project(_folder, target_store, _embedder, **_kwargs):
        target_store.upsert([_chunk("new.py", "new")], [[0.2] * 384])
        return {"files": 1, "chunks": 1, "languages": {"python": 1}, "skipped": 1}

    monkeypatch.setattr("sema.indexer.chunker.index_project", fake_index_project)
    monkeypatch.setattr("sema.indexer.embedder.Embedder", object)

    result = CliRunner().invoke(main, ["index", str(tmp_path)])
    assert result.exit_code == 0, result.output
    meta = json.loads((tmp_path / ".sema" / "meta.json").read_text())
    assert meta["file_count"] == 2
    assert meta["chunk_count"] == 2
    assert meta["sema_version"] != "dev"
