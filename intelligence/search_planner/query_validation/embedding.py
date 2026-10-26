"""Embedding provider port: real local dense model + deterministic fixture.

Production requires a trusted, already-cached local SentenceTransformer.
This package does not download models at runtime, call OpenRouter embeddings,
or silently substitute lexical hashing for semantic embeddings.
"""
from __future__ import annotations

from hashlib import sha256
from math import sqrt, isfinite
from pathlib import Path
import re

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

MAX_TEXTS = 1600
MAX_DIMENSIONS = 4096


def _normalize(vec):
    if not isinstance(vec, (list, tuple)) or not 1 <= len(vec) <= MAX_DIMENSIONS:
        raise PipelineError("Invalid embedding dimensions", 422)
    if any(type(x) not in (int, float) or not isfinite(x) for x in vec):
        raise PipelineError("Embedding contains invalid or nonfinite values", 422)
    norm = sqrt(sum(float(x) ** 2 for x in vec))
    if not isfinite(norm) or norm <= 1e-12:
        raise PipelineError("Embedding is zero or overflows", 422)
    return tuple(float(x) / norm for x in vec)


def encode_checked(provider, texts):
    if type(texts) is not list or not 1 <= len(texts) <= MAX_TEXTS or any(
        type(t) is not str or not 1 <= len(t) <= 3000 for t in texts
    ):
        raise PipelineError("Invalid embedding batch", 422)
    encoded = provider.encode(texts)
    if not isinstance(encoded, (list, tuple)) or len(encoded) != len(texts):
        raise PipelineError("Embedding provider omitted or added vectors", 422)
    vectors = [_normalize(v) for v in encoded]
    if len({len(v) for v in vectors}) != 1:
        raise PipelineError("Inconsistent embedding dimensions", 422)
    return vectors


class SentenceTransformerEmbeddingProvider:
    """Manifest-pinned local SentenceTransformer, no downloads or silent fallback.

    model_key is derived from a manifest containing exact artifact SHA256s and
    a declared upstream revision. An optional expected model_key must match
    that derived identity; arbitrary operator-controlled strings are rejected.
    Operator must independently verify that the revision genuinely belongs
    to the intended upstream model and supports the target language(s).
    """
    mode = "semantic_local"
    __slots__ = ("_model_path", "_model_key", "_model", "_dimensions")

    def __init__(self, model_path, *, model_key=None):
        from .model_manifest import verify_model_manifest
        pinned = verify_model_manifest(model_path)
        if model_key is not None and (type(model_key) is not str or
                                      model_key != pinned["model_key"]):
            raise PipelineError("Embedding model_key differs from verified local SHA pin", 409)
        self._model_path = Path(model_path).expanduser().absolute()
        self._model_key = pinned["model_key"]
        self._dimensions = pinned["manifest"]["dimensions"]
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise PipelineError("Install sentence-transformers for semantic Query Validation", 503) from exc
        # Never omit these options or change to a hub-model identifier. A
        # version without local_files_only support must fail rather than
        # falling back to an online model load.
        try:
            model = SentenceTransformer(
                str(self._model_path), local_files_only=True,
                trust_remote_code=False,
            )
        except Exception as exc:
            raise PipelineError("Local SentenceTransformer failed to load safely", 503) from exc
        dimensions = model.get_sentence_embedding_dimension()
        if (type(dimensions) is not int or dimensions != self._dimensions):
            raise PipelineError("Loaded embedding dimension differs from pinned manifest", 409)
        # Catch modifications made while loading (or unsafe loader side effects).
        self.verify_runtime_pin()
        self._model = model

    @property
    def model_key(self):
        return self._model_key

    def verify_runtime_pin(self):
        """Rehash files for each production QV run, fail closed on drift."""
        from .model_manifest import verify_model_manifest
        actual = verify_model_manifest(self._model_path)
        if actual["model_key"] != self._model_key:
            raise PipelineError("Pinned embedding model drift detected", 409)
        return self._model_key

    def encode(self, texts):
        # Public production admission checks pin again through QV service.
        arr = self._model.encode(texts, batch_size=32, convert_to_numpy=True,
                                 normalize_embeddings=True, show_progress_bar=False)
        return arr.tolist()


class HashingTestEmbeddings:
    """Deterministic lexical TEST FIXTURE; NEVER treated as semantic model."""
    mode = "test_fixture"
    model_key = "qa-hashing-fixture-v1"

    def __init__(self, dimensions=192):
        if type(dimensions) is not int or dimensions < 64 or dimensions > 4096:
            raise ValueError("Invalid fixture dimensions")
        self.dimensions = dimensions

    def encode(self, texts):
        vectors = []
        for text in texts:
            vals = [0.0] * self.dimensions
            tokens = re.findall(r"[\w]+", text.casefold(), flags=re.UNICODE)
            for token in tokens:
                idx = int.from_bytes(sha256(token.encode("utf-8")).digest()[:8], "big") % self.dimensions
                vals[idx] += 1.0
            # A syntactically nonempty hashtag/punctuation-only input is
            # still embedded; unknown input yields a zero vector and fails closed.
            vectors.append(vals)
        return vectors
