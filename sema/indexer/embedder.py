"""
SBERT embedder wrapper.

Lazy model loading — model downloads only on first index, not on MCP serve.
Batch embedding for efficiency. Model cached in ~/.cache/sema/models/.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

# huggingface-hub 1.23+ otherwise fetches its agent-harness registry while
# constructing request headers, including on a local-files-only model load. sema
# never sends telemetry, and disabling it still permits the intentional first-use
# model download when the local cache is empty.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"
CACHE_DIR = Path.home() / ".cache" / "sema" / "models"
BATCH_SIZE = 64


class Embedder:
    def __init__(self):
        self._model: "SentenceTransformer | None" = None

    def _load(self) -> "SentenceTransformer":
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            # Prefer the local cache.  sentence-transformers otherwise performs a
            # series of Hugging Face metadata requests even when every model file is
            # already present; that made a fresh `sema search` process spend seconds
            # on the network and weakened offline operation.  Only fall back to the
            # normal download path on the first-ever use of the model.
            try:
                self._model = SentenceTransformer(
                    MODEL_NAME,
                    cache_folder=str(CACHE_DIR),
                    local_files_only=True,
                )
            except (OSError, ValueError):
                self._model = SentenceTransformer(
                    MODEL_NAME,
                    cache_folder=str(CACHE_DIR),
                )
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts. Lazy-loads model on first call."""
        model = self._load()
        embeddings = model.encode(
            texts,
            batch_size=BATCH_SIZE,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return embeddings.tolist()

    def embed_one(self, text: str) -> list[float]:
        """Embed a single query — used by MCP search at runtime."""
        return self.embed([text])[0]
