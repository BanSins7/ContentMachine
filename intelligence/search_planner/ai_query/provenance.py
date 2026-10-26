"""SP-05: conservative read-only integrity audit of completed SQG output.

Do not confuse this with semantic validation (SP-06..SP-08), Collector
receipts or actual video retrieval. This verifies existing immutable SQG
candidate/coverage rows have a coherent one-to-one hypothesis accounting.
"""
from __future__ import annotations

from copy import deepcopy
import re

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.search_planner.contracts import publication_uuid


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_MODES = frozenset(("keyword", "hashtag"))
_COVERAGE = frozenset(("generated", "no_viable_queries"))


def verify_candidates_bundle(bundle: dict) -> dict:
    """Verify whole completed `query_candidates/v1`, never fix corrupt rows.

    Every selected query must correspond to precisely one approved hypothesis
    coverage row. A 0-query completed bundle is valid only if every covered
    hypothesis is marked no_viable_queries with a nonempty explanation.
    Unknown or duplicate UUIDs, counts, project pins and malformed provenance
    fail closed rather than being quietly truncated or de-duplicated.
    """
    if type(bundle) is not dict or bundle.get("schema_version") != "query_candidates/v1":
        raise PipelineError("Invalid SQG candidates bundle schema", 409)
    if bundle.get("status") != "completed":
        raise PipelineError("SQG candidates bundle is not completed", 409)
    publication_uuid(bundle.get("generation_id"))
    publication_uuid(bundle.get("taxonomy_publication_id"))
    project_id = bundle.get("project_id")
    if type(project_id) is not str or not project_id.strip():
        raise PipelineError("SQG candidates project scope missing", 409)
    version = bundle.get("taxonomy_version")
    if type(version) is not int or version < 1:
        raise PipelineError("SQG taxonomy version invalid", 409)
    source_hash = bundle.get("input_sha256")
    if type(source_hash) is not str or _HEX.fullmatch(source_hash) is None:
        raise PipelineError("SQG source hash missing", 409)
    for field in ("model_id", "prompt_version"):
        if type(bundle.get(field)) is not str or not bundle[field].strip():
            raise PipelineError("SQG output model/prompt pin missing", 409)

    items, coverage = bundle.get("candidates"), bundle.get("hypothesis_coverage")
    if (type(items) is not list or type(coverage) is not list
            or not 1 <= len(coverage) <= 48 or len(items) > 480):
        raise PipelineError("SQG candidate or hypothesis coverage count invalid", 409)
    cover_by_id = {}
    for entry in coverage:
        if type(entry) is not dict or set(entry) != {
            "hypothesis_id", "state", "candidate_count", "reason",
        }:
            raise PipelineError("Malformed SQG hypothesis coverage row", 409)
        hid, state, number = (entry["hypothesis_id"], entry["state"],
                              entry["candidate_count"])
        if (type(hid) is not str or not hid or len(hid) > 40 or hid in cover_by_id
                or state not in _COVERAGE or type(number) is not int
                or number < 0 or number > 16):
            raise PipelineError("Duplicate or invalid SQG hypothesis coverage", 409)
        explanation = entry["reason"]
        if state == "generated" and (number == 0 or explanation is not None):
            raise PipelineError("Generated hypothesis lacks valid coverage", 409)
        if state == "no_viable_queries" and (number != 0 or
                type(explanation) is not str or not explanation.strip()):
            raise PipelineError("No viable queries requires recorded explanation", 409)
        cover_by_id[hid] = entry

    counted = {hid: 0 for hid in cover_by_id}
    seen = set()
    for candidate in items:
        expected = {
            "candidate_id", "hypothesis_id", "direction_id", "branch_id",
            "source_fact_ids", "text", "query_mode", "angle", "language",
            "research_stance", "rationale",
        }
        if type(candidate) is not dict or set(candidate) != expected:
            raise PipelineError("Malformed SQG selected candidate", 409)
        cid = publication_uuid(candidate["candidate_id"])
        if cid in seen:
            raise PipelineError("Duplicate SQG candidate identity", 409)
        seen.add(cid)
        hid = candidate["hypothesis_id"]
        if hid not in counted:
            raise PipelineError("SQG candidate references uncovered hypothesis", 409)
        if (candidate["query_mode"] not in _ALLOWED_MODES
                or type(candidate["text"]) is not str
                or not 1 <= len(candidate["text"].strip()) <= 120
                or type(candidate["rationale"]) is not str
                or not candidate["rationale"].strip()):
            raise PipelineError("Invalid SQG query content", 409)
        facts = candidate["source_fact_ids"]
        if (type(facts) is not list or any(type(x) is not str or not x for x in facts)
                or len(set(facts)) != len(facts)):
            raise PipelineError("Invalid SQG source fact lineage", 409)
        counted[hid] += 1
    if sum(counted.values()) != len(items) or any(
            counted[hid] != c["candidate_count"] for hid, c in cover_by_id.items()):
        raise PipelineError("SQG persisted candidate and coverage counts disagree", 409)

    # Caller can mutate its own return value; validation never mutates the
    # underlying DB-reader's dictionary or imposes a new SQG V1 wire format.
    return deepcopy(bundle)


def candidate_manifest_sha256(bundle: dict) -> str:
    """Digest actual validated immutable bundle, not provider-generated claims."""
    return sha256(verify_candidates_bundle(bundle))
