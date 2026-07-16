"""Tests for offline-first embedding model loading."""

import os
import sys
from types import SimpleNamespace

from sema.indexer.embedder import CACHE_DIR, Embedder


def test_hugging_face_telemetry_is_disabled_for_offline_cached_loads():
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_cached_model_load_is_local_only(monkeypatch):
    calls: list[dict] = []

    class FakeModel:
        def __init__(self, _name, **kwargs):
            calls.append(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeModel),
    )

    model = Embedder()._load()
    assert isinstance(model, FakeModel)
    assert calls == [{"cache_folder": str(CACHE_DIR), "local_files_only": True}]


def test_missing_cached_model_falls_back_to_download(monkeypatch):
    calls: list[dict] = []

    class FakeModel:
        def __init__(self, _name, **kwargs):
            calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise OSError("not cached")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeModel),
    )

    model = Embedder()._load()
    assert isinstance(model, FakeModel)
    assert calls[0]["local_files_only"] is True
    assert "local_files_only" not in calls[1]
