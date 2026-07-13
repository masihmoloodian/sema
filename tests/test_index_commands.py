"""CLI tests for the index-explorer commands: `sema list` / `add` / `remove`.

These populate a store with dummy embeddings so they run fast (no SBERT load);
`add` is covered by the end-to-end indexing tests and manual verification.
"""

import json
from pathlib import Path

from click.testing import CliRunner

from sema.cli import main
from sema.store.chroma import SemaStore
from sema.store.schema import Chunk

DIM = 384  # all-MiniLM-L6-v2 dimensionality


def _chunk(file: str, name: str, line: int = 1) -> Chunk:
    return Chunk(
        id=f"{file}::{name}",
        file=file,
        language="python",
        chunk_type="function",
        name=name,
        signature=f"def {name}()",
        body=f"def {name}(): pass",
        start_line=line,
        end_line=line + 2,
    )


def _seed(root: Path) -> None:
    """Build an index at root/.sema/index with 3 chunks across 2 files."""
    store = SemaStore(root / ".sema" / "index")
    chunks = [
        _chunk("a.py", "alpha", line=1),
        _chunk("a.py", "beta", line=10),
        _chunk("b.py", "gamma", line=1),
    ]
    store.upsert(chunks, [[0.1] * DIM for _ in chunks])


def _run(*args: str):
    # click 8.2+ keeps stdout/stderr separate; result.output is stdout only.
    return CliRunner().invoke(main, list(args))


def test_list_json_groups_by_file(tmp_path):
    _seed(tmp_path)
    res = _run("list", str(tmp_path), "--json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["file_count"] == 2
    assert data["chunk_count"] == 3
    files = {f["file"]: f for f in data["files"]}
    assert set(files) == {"a.py", "b.py"}
    # chunks are sorted by start_line within a file
    assert [c["name"] for c in files["a.py"]["chunks"]] == ["alpha", "beta"]


def test_remove_drops_a_files_chunks(tmp_path):
    _seed(tmp_path)
    res = _run("remove", "a.py", "--root", str(tmp_path), "--json")
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["ok"] is True

    after = json.loads(_run("list", str(tmp_path), "--json").output)
    assert after["file_count"] == 1
    assert after["chunk_count"] == 1
    assert after["files"][0]["file"] == "b.py"


def test_list_no_index_is_empty(tmp_path):
    res = _run("list", str(tmp_path), "--json")
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == {"files": [], "chunk_count": 0, "file_count": 0}
