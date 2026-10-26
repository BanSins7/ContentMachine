"""SP-11 frozen wire IDs, maximum chunking and Collector V1 field projection.

This is an internal SP-owned compatibility contract. It does NOT import the
Collector branch or authorize handoff/delivery by itself.
"""
from __future__ import annotations

from re import compile as re_compile

HANDOFF_SCHEMA = "search-planner-handoff-projection/v1"
COLLECTOR_QUERY_SET_SCHEMA = "validated_query_set/v1"
CHUNK_SCHEMA = "search-planner-handoff-chunk/v1"
MAX_CHUNK_SIZE = 50
MAX_QUERIES = 480
VALIDATION_VERSION = "sp-qv-verified/v1"
NODE_RE = re_compile(r"[A-Za-z][A-Za-z0-9_.:-]{1,127}\Z")
LANGUAGE_RE = re_compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")
QUERY_FIELDS = frozenset({
    "query_id", "hypothesis_id", "direction_id", "mode",
    "text", "language", "validation_state",
})
SET_FIELDS = frozenset({
    "schema_version", "project_id", "taxonomy_publication_id",
    "taxonomy_version", "taxonomy_sha256", "generation_id",
    "validation_version", "queries",
})
