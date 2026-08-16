# © Copyright 2026 Aaron Kimball
"""`EmbeddingModel`: local ONNX text embedding via `fastembed`, loaded from the bundled model
installed to `embedding_model_target_dir()` by `install_embedding_model()` -- never downloaded at
runtime. See docs/specs/local-search-index.md.
"""

import importlib.resources
import logging
import os
import shutil
from pathlib import Path

import numpy as np

from klorb.paths import get_klorb_data_dir

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS = 384
EMBEDDING_THREADS = 2
"""Caps `onnxruntime`'s intra-op thread pool for the embedding session. Unset (the `fastembed`
default), it sizes the pool to the machine's full physical core count."""
EMBEDDING_MODEL_RESOURCE_NAME = "embedding-model"
"""Directory name of the packaged embedding-model resource tree within `klorb.resources`, and
the subdirectory of `KLORB_DATA_DIR` it's copied to -- the on-disk cache layout
`huggingface_hub`/`fastembed` read back offline (see `install_embedding_model`)."""

_HF_HUB_OFFLINE_ENV_VAR = "HF_HUB_OFFLINE"


def embedding_model_target_dir() -> Path:
    """Where `install_embedding_model()` copies the packaged model tree to, and where
    `EmbeddingModel` points `fastembed`'s `cache_dir` at: `$KLORB_DATA_DIR/embedding-model`."""
    return get_klorb_data_dir() / EMBEDDING_MODEL_RESOURCE_NAME


def embedding_model_available() -> bool:
    """Whether `embedding_model_target_dir()` exists -- checked up front by
    `klorb.search_index.indexer.WorkspaceIndexer` before starting any background work, so an
    environment that never ran `klorb init` (most unit tests, a fresh CI container) doesn't spawn
    a thread doomed to fail on its first `EmbeddingModel()` construction."""
    return embedding_model_target_dir().is_dir()


def install_embedding_model() -> list[str]:
    """Recursively copy the packaged `embedding-model` resource tree (`klorb.resources/
    embedding-model/`, fetched once via `scripts/fetch_embedding_model.py` and shipped as package
    data) into `embedding_model_target_dir()`, creating it as needed. Always copies, the same as
    `klorb.token_estimate.install_tiktoken_cache()` and for the same reason: this is package data
    the running klorb version ships with, not something a user hand-edits. Raises `OSError` if
    the target can't be created or written."""
    target = embedding_model_target_dir()
    with importlib.resources.as_file(
        importlib.resources.files("klorb.resources").joinpath(EMBEDDING_MODEL_RESOURCE_NAME)
    ) as source:
        shutil.copytree(source, target, dirs_exist_ok=True)
    return [f"Copied embedding model to {target}."]


class EmbeddingModel:
    """Wraps a `fastembed.TextEmbedding` loaded from the bundled `EMBEDDING_MODEL_NAME` model,
    forcing `HF_HUB_OFFLINE` so a missing/stale local copy fails fast rather than silently
    reaching Hugging Face. Construction raises `FileNotFoundError` if `embedding_model_target_dir()`
    doesn't exist yet (`klorb init` hasn't run) -- the caller is expected to treat that as "the
    workspace index feature is unavailable", not to fall back to a network download.
    """

    def __init__(self) -> None:
        target_dir = embedding_model_target_dir()
        if not target_dir.is_dir():
            raise FileNotFoundError(
                f"Embedding model not found at {target_dir}; run `klorb init` first.")
        previous_offline = os.environ.get(_HF_HUB_OFFLINE_ENV_VAR)
        os.environ[_HF_HUB_OFFLINE_ENV_VAR] = "1"
        try:
            # Imported lazily so a process that never touches the search index never pays
            # onnxruntime's import cost.
            from fastembed import TextEmbedding
            self._model = TextEmbedding(
                model_name=EMBEDDING_MODEL_NAME, cache_dir=str(target_dir), threads=EMBEDDING_THREADS)
        finally:
            if previous_offline is None:
                del os.environ[_HF_HUB_OFFLINE_ENV_VAR]
            else:
                os.environ[_HF_HUB_OFFLINE_ENV_VAR] = previous_offline
        logger.debug("Loaded embedding model %r from %s.", EMBEDDING_MODEL_NAME, target_dir)

    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        """Embed `texts` as documents/passages (the index side of retrieval)."""
        return list(self._model.passage_embed(texts))

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single search query, using the model's query-side (possibly asymmetric)
        embedding path."""
        return next(iter(self._model.query_embed([text])))


_embedding_model: EmbeddingModel | None = None


def get_embedding_model() -> EmbeddingModel:
    """Return the shared `EmbeddingModel`, caching on first use.
    (Loading the ONNX model is expensive.)"""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModel()
    return _embedding_model
