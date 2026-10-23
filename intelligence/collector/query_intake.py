"""Strictly parse *structurally* validated query-set snapshots, no IO.

This is intentionally NOT a trusted query-set loader. Do not promote client
JSON to an approved SQG generation: future live runs require a server-side
PostgreSQL verifier and a persisted Query Validation publication.
"""
from __future__ import annotations

from .contracts import (
    CollectorError, HASH_RE, LANGUAGE_RE, NODE_RE, QUERY_ID_RE, PROJECT_RE,
    QUERY_MODES, QUERY_SET_SCHEMA, ValidatedQuery, ValidatedQuerySet,
    require_dict, require_id, require_int, require_text, require_uuid,
)

QUERY_SET_FIELDS = {
    "schema_version", "project_id", "taxonomy_publication_id",
    "taxonomy_version", "taxonomy_sha256", "generation_id",
    "validation_version", "queries",
}
QUERY_FIELDS = {
    "query_id", "hypothesis_id", "direction_id", "mode",
    "text", "language", "validation_state",
}


def parse_validated_query_set(raw: dict, *, expected_project_id: str | None = None,
                              expected_publication_id: str | None = None
                              ) -> ValidatedQuerySet:
    """Parse untrusted offline data. No assertion of approval is made."""
    require_dict(raw, QUERY_SET_FIELDS, "query_set")
    if raw["schema_version"] != QUERY_SET_SCHEMA:
        raise CollectorError("invalid_schema_version", "Query set schema version mismatch")
    pid = require_id(raw["project_id"], "project_id", PROJECT_RE)
    pubid = require_uuid(raw["taxonomy_publication_id"], "taxonomy_publication_id")
    if expected_project_id is not None and pid != expected_project_id:
        raise CollectorError("project_scope_mismatch", "Query set belongs to a different project", 409)
    if expected_publication_id is not None and pubid != expected_publication_id:
        raise CollectorError("publication_scope_mismatch", "Wrong RTB publication", 409)
    version = require_int(raw["taxonomy_version"], "taxonomy_version", 1, 2**31 - 1)
    checksum = require_id(raw["taxonomy_sha256"], "taxonomy_sha256", HASH_RE)
    generation = require_uuid(raw["generation_id"], "generation_id")
    validation_version = require_text(raw["validation_version"], "validation_version", 120)
    if not isinstance(raw["queries"], list) or not 1 <= len(raw["queries"]) <= 50:
        raise CollectorError("invalid_query_count", "Query set must contain 1 to 50 queries")
    queries = []
    seen: set[str] = set()
    for row in raw["queries"]:
        require_dict(row, QUERY_FIELDS, "query")
        query_id = require_id(row["query_id"], "query_id", QUERY_ID_RE)
        hypothesis_id = require_id(row["hypothesis_id"], "hypothesis_id", NODE_RE)
        direction_id = require_id(row["direction_id"], "direction_id", NODE_RE)
        if query_id in seen:
            raise CollectorError("duplicate_query_id", "Duplicate query_id")
        seen.add(query_id)
        if hypothesis_id == direction_id:
            raise CollectorError("invalid_ancestry", "Hypothesis and direction must differ")
        mode = row["mode"]
        if type(mode) is not str or mode not in QUERY_MODES:
            raise CollectorError("unsupported_query_mode", "Invalid query mode")
        text = require_text(row["text"], "query text", 160)
        language = require_id(row["language"], "language", LANGUAGE_RE)
        if row["validation_state"] != "accepted":
            raise CollectorError("query_not_accepted", "Only accepted query candidates may be previewed")
        queries.append(ValidatedQuery(
            query_id, hypothesis_id, direction_id, mode, text, language,
        ))
    return ValidatedQuerySet(
        pid, pubid, version, checksum, generation, validation_version,
        tuple(sorted(queries, key=lambda item: item.query_id)),
    )
