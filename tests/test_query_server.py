"""Tests for the editor's persistent JSONL query protocol."""

import io
import json

from sema import query_server


def test_query_worker_handles_multiple_requests_in_one_warm_process(monkeypatch, capsys, tmp_path):
    calls = []

    class FakeEngine:
        def __init__(self, root):
            calls.append(("init", root))

        def warm(self):
            calls.append(("warm",))

        def search(self, query, top_k):
            calls.append(("search", query, top_k))
            return {"results": [{"name": "answer"}]}

        def get(self, symbol):
            calls.append(("get", symbol))
            return {"implementations": [{"name": symbol}]}

    monkeypatch.setattr(query_server, "QueryEngine", FakeEngine)
    monkeypatch.setattr(
        query_server.sys,
        "stdin",
        io.StringIO(
            json.dumps({"id": 1, "command": "search", "query": "auth", "top_k": 4})
            + "\n"
            + json.dumps({"id": 2, "command": "get", "symbol": "answer"})
            + "\n"
        ),
    )

    query_server.serve_query_worker(tmp_path)

    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert output == [
        {"ready": True},
        {"id": 1, "result": {"results": [{"name": "answer"}]}},
        {"id": 2, "result": {"implementations": [{"name": "answer"}]}},
    ]
    assert calls == [
        ("init", tmp_path.resolve()),
        ("warm",),
        ("search", "auth", 4),
        ("get", "answer"),
    ]


def test_query_engine_checks_index_revision_before_search_and_get(monkeypatch, tmp_path):
    calls = []

    class FakeEmbedder:
        def embed_one(self, text):
            return [text]

    class FakeStore:
        def search(self, *_args, **_kwargs):
            return []

        def get_by_name(self, symbol):
            return [{"name": symbol}]

    class FakeHandle:
        def __init__(self, *_args):
            self.store = FakeStore()
            self.bm25 = None

        def refresh_if_changed(self):
            calls.append("refresh")

    monkeypatch.setattr(query_server, "Embedder", FakeEmbedder)
    monkeypatch.setattr(query_server, "ProjectHandle", FakeHandle)

    engine = query_server.QueryEngine(tmp_path)
    assert engine.search("auth", 4) == {"query": "auth", "results": []}
    assert engine.get("answer") == {"implementations": [{"name": "answer"}]}
    assert calls == ["refresh", "refresh"]
