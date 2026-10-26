"""C-FIX-00: pure contracts for the future B9/A8 -> Deep V2 boundary.

No DB access, provider calls, payments or changes to legacy RTB. Source of truth:
B9 knowledge-snapshot/v1, B9 knowledge-c-handoff/v1 and A video-intelligence/v1.
These helpers deliberately do not pretend to implement C-run lifecycle.
"""
from __future__ import annotations

from copy import deepcopy
import re
from uuid import UUID, NAMESPACE_URL, uuid5

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.knowledge_extraction.knowledge_snapshot import build_c_handoff
from .deep_contracts import NODE_PROPERTIES
from .pipeline_contracts import PipelineError

PROJECT_RE = re.compile(r"^[0-9a-f]{32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
FACT_NAMESPACE = "contentmachine/rtb/b9-fact/v1"
A_VIDEO_SCHEMA = "video-intelligence/v1"


def _fail(code):
    raise PipelineError(code, 422)


def _project(project_id):
    if not isinstance(project_id, str) or not PROJECT_RE.fullmatch(project_id):
        _fail("c_invalid_project_id")
    return project_id


def _uuid(value, code):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            _fail(code)
    except (ValueError, TypeError, AttributeError):
        _fail(code)
    return value


def _sha(value, code):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        _fail(code)
    return value


def b9_fact_sre_uuid(project_id, fact_id):
    """Project-scoped, deterministic UUID for the same B9 fact SHA across C-runs.

    The B9 SHA remains in original immutable B9 payload and the mapping manifest.
    The derived UUID is the RTB/SRE adapter identity, NOT a regenerated fact.
    """
    pid = _project(project_id)
    digest = _sha(fact_id, "c_invalid_b9_fact_id")
    return str(uuid5(NAMESPACE_URL, FACT_NAMESPACE + "/" + pid + "/" + digest))


def checked_b9_mapping(snapshot, handoff):
    """Verify exact B9 C handoff; return stable adapter mapping, not revised facts."""
    if not isinstance(snapshot, dict) or not isinstance(handoff, dict):
        _fail("c_missing_knowledge_handoff")
    pid = _project(snapshot.get("project_id"))
    if snapshot.get("schema_version") != "knowledge-snapshot/v1":
        _fail("c_knowledge_schema_version")
    if handoff.get("schema_version") != "knowledge-c-handoff/v1":
        _fail("c_handoff_schema_version")
    _uuid(snapshot.get("context_id"), "c_invalid_a_context_id")
    _uuid(handoff.get("snapshot_id"), "c_invalid_b9_snapshot_id")
    expected_digest = canonical_sha(snapshot)
    if handoff.get("snapshot_sha256") != expected_digest:
        _fail("c_b9_handoff_sha_mismatch")
    try:
        expected = build_c_handoff(snapshot, handoff["snapshot_id"], expected_digest)
    except (ValueError, KeyError, TypeError) as exc:
        raise PipelineError("c_b9_handoff_invalid", 422) from exc
    if handoff != expected:
        _fail("c_b9_handoff_changed")
    facts = snapshot.get("facts")
    if not isinstance(facts, list) or len(facts) != snapshot.get("approved_count") or not facts:
        _fail("c_b9_approved_facts_invalid")
    mapping = {}
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("review_status") != "human_approved":
            _fail("c_unapproved_b9_fact")
        key = _sha(fact.get("fact_id"), "c_invalid_b9_fact_id")
        if key in mapping:
            _fail("c_duplicate_b9_fact_id")
        mapping[key] = b9_fact_sre_uuid(pid, key)
    return {
        "project_id": pid,
        "knowledge_snapshot_id": handoff["snapshot_id"],
        "knowledge_snapshot_sha256": expected_digest,
        "project_source_snapshot_id": snapshot["context_id"],
        "project_source_snapshot_sha256": _sha(snapshot["context_sha256"], "c_invalid_a_context_sha"),
        "b9_fact_sha_to_sre_uuid": mapping,
        "video_analysis_refs": deepcopy(snapshot["video_analysis_refs"]),
    }


def index_original_a_observations(project_id, video_pins, resolved_outputs):
    """Wrap originals with stable refs; do not modify A's evidence content.

    resolved_outputs must be supplied by verified PostgresAResolver.resolve_video(),
    keyed by pinned output_id. This extra pure check prevents swapped inputs.
    """
    pid = _project(project_id)
    if not isinstance(video_pins, list) or not isinstance(resolved_outputs, dict):
        _fail("c_a_video_inputs_invalid")
    pins = {}
    for pin in video_pins:
        if not isinstance(pin, dict) or pin.get("schema_version") != A_VIDEO_SCHEMA:
            _fail("c_a_video_schema")
        oid = _uuid(pin.get("output_id"), "c_invalid_a_output_id")
        _uuid(pin.get("source_id"), "c_invalid_a_source_id")
        _uuid(pin.get("source_version_id"), "c_invalid_a_source_version_id")
        _sha(pin.get("output_sha256"), "c_invalid_a_output_sha")
        if oid in pins:
            _fail("c_duplicate_a_output")
        pins[oid] = pin
    if set(pins) != set(resolved_outputs):
        _fail("c_a_video_missing_or_extra")
    indexed = {}
    for oid in sorted(pins):
        pin = pins[oid]
        source = resolved_outputs[oid]
        if not isinstance(source, dict) or source.get("schema_version") != A_VIDEO_SCHEMA:
            _fail("c_a_video_schema")
        if (source.get("project_id") != pid
                or source.get("source_id") != pin["source_id"]
                or source.get("video_source_version_id") != pin["source_version_id"]
                or canonical_sha(source) != pin["output_sha256"]):
            _fail("c_a_video_provenance_mismatch")
        evidence = source.get("evidence")
        if not isinstance(evidence, dict):
            _fail("c_a_evidence_missing")
        for evidence_id, original in sorted(evidence.items()):
            if (not isinstance(evidence_id, str) or not evidence_id
                    or len(evidence_id) > 256 or not isinstance(original, dict)):
                _fail("c_a_evidence_invalid")
            observation_id = oid + "::" + evidence_id
            if observation_id in indexed:
                _fail("c_duplicate_a_observation")
            indexed[observation_id] = {
                "observation_id": observation_id,
                "source_id": pin["source_id"],
                "source_version_id": pin["source_version_id"],
                "output_id": oid,
                "output_sha256": pin["output_sha256"],
                "a_evidence_id": evidence_id,
                "original_evidence": deepcopy(original),
            }
    return indexed


def project_v2_tree_to_v1_core(nodes):
    """Deterministic 14->11 field structural projection, without AI or mutation."""
    if not isinstance(nodes, list):
        _fail("c_v2_tree_invalid")
    names = set(NODE_PROPERTIES)
    result = []
    for node in nodes:
        if not isinstance(node, dict) or not names.issubset(node):
            _fail("c_v2_core_fields_missing")
        result.append({key: deepcopy(node[key]) for key in NODE_PROPERTIES})
    return result
