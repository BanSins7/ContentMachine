"""Offline compare of SP11 handoff to the real Collector V1 parser semantics.

Collector (feature/video-collector-dev-20261023) parses and sorts queries by
query_id before computing its trusted query-set fingerprint. The original SP
handoff preserves scientific ranking instead; its raw hash may differ. Never
silently pretend the two digests match, rewrite immutable SP inbox rows, or
publish into Collector's private tables. This is a translation proposal only.
"""
from __future__ import annotations

from copy import deepcopy
import re

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.handoff.verification import verify_handoff_projection

NODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{1,127}\Z")
LANG_RE = re.compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")
VALID_MODES = frozenset(("keyword", "hashtag"))
QUERY_KEYS = frozenset((
    "query_id", "hypothesis_id", "direction_id", "mode",
    "text", "language", "validation_state",
))


def _collector_canonical_chunk(chunk):
    """Mirror upstream parser's strict, non-authorizing V1 DTO normalization."""
    queryset = chunk["collector_query_set"]
    queries = queryset["queries"]
    if type(queries) is not list or not 1 <= len(queries) <= 50:
        raise PipelineError("Collector V1 refuses empty or oversized query set", 409)
    clean = []
    seen = set()
    for item in queries:
        if type(item) is not dict or set(item) != QUERY_KEYS:
            raise PipelineError("Collector V1 query field mismatch", 409)
        cid = publication_uuid(item.get("query_id"))
        if cid in seen:
            raise PipelineError("Collector V1 query ID repeated", 409)
        seen.add(cid)
        hyp, direction = item.get("hypothesis_id"), item.get("direction_id")
        mode, lang, text = item.get("mode"), item.get("language"), item.get("text")
        if (type(hyp) is not str or NODE_RE.fullmatch(hyp) is None or
                type(direction) is not str or NODE_RE.fullmatch(direction) is None or
                hyp == direction or type(mode) is not str or mode not in VALID_MODES or
                type(lang) is not str or LANG_RE.fullmatch(lang) is None or
                type(text) is not str or not text or len(" ".join(text.split())) > 160 or
                " ".join(text.split()) != text or
                any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text)):
            raise PipelineError("Collector V1 would reject an SP projected query", 409)
        clean.append(deepcopy(item))
    wire = deepcopy(queryset)
    wire["queries"] = sorted(clean, key=lambda item: item["query_id"])
    return wire


def assess_collector_v1_projection(projection):
    """Zero-IO schema test; self-hashes alone cannot prove a trusted QV source.

    Caller should use VerifiedHandoffProjector.preview(project, qv_run) for
    a trusted DB source. Neither this report nor its canonical digest is
    a signed receipt, Collector approval, billing consent or dispatch token.
    """
    checked = verify_handoff_projection(projection)
    diagnostics = []
    for chunk in checked["chunks"]:
        normalized = _collector_canonical_chunk(chunk)
        collector_sha = sha256(normalized)
        original_sha = chunk["collector_query_set_sha256"]
        diagnostics.append({
            "chunk_index": chunk["chunk_index"],
            "selected_query_count": chunk["chunk_size"],
            "sp_query_set_sha256": original_sha,
            "collector_v1_canonical_sha256": collector_sha,
            "same_query_ids": sorted(q["query_id"] for q in normalized["queries"])
                == sorted(chunk["query_ids"]),
            "byte_fingerprint_equal": original_sha == collector_sha,
            "normalization_required": original_sha != collector_sha,
        })
    return {
        "schema_version": "search-planner-collector-v1-wire-assessment/v1",
        "project_id": checked["project_id"],
        "validation_run_id": checked["validation_run_id"],
        "projection_sha256": checked["projection_sha256"],
        "chunk_count": checked["chunk_count"],
        "selected_query_count": checked["selected_query_count"],
        "all_chunks_structurally_parseable": True,
        "requires_collector_query_sort_normalization": any(
            item["normalization_required"] for item in diagnostics
        ),
        "chunks": diagnostics,
        "source_authentication_by_this_report": False,
        "collector_accepted": False,
        "trusted_collector_publisher_installed": False,
        "durable_receipt_verified": False,
        "external_dispatch_authorized": False,
        "provider_calls": 0,
    }
