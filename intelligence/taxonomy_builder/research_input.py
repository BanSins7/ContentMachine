"""RTB-FIX-01: read-only, immutable B9 facts + original A8 video Research Input.

The source of authority remains B9's replay-verified snapshot and handoff, plus
A's verified, pinned context/video resolver. This module does not persist, pay,
execute any model, select a newer snapshot, or discard excess material.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from intelligence.knowledge_extraction.contracts import VideoAnalysisPin
from intelligence.knowledge_extraction.intake import PostgresAResolver
from intelligence.knowledge_extraction.knowledge_snapshot import KnowledgeSnapshotRepository
from intelligence.project_sources.context_builder import sha as canonical_sha, canonical
from .pipeline_contracts import PipelineError
from .research_contracts_v2 import (
    _project, _sha, _uuid, checked_b9_mapping, index_original_a_observations,
)

SCHEMA = "rtb-research-input/v2"
ENGINE = "rtb-research-materialization/1.0.0"
MAX_INPUT_BYTES = 1_200_000
MAX_FACTS = 400
MAX_VIDEOS = 24
MAX_OBSERVATIONS = 2400
MAX_OBSERVATION_BYTES = 48_000


def _reject(code: str):
    raise PipelineError(code, 422)


def _video_pins(pins: Any) -> list[dict]:
    if not isinstance(pins, list) or len(pins) > MAX_VIDEOS:
        _reject("rtb_research_video_count_exceeded")
    by_output = {}
    for row in pins:
        if not isinstance(row, dict) or set(row) != {
            "source_id", "source_version_id", "output_id", "output_sha256",
            "schema_version",
        } or row.get("schema_version") != "video-intelligence/v1":
            _reject("rtb_research_video_pin_invalid")
        for field in ("source_id", "source_version_id", "output_id"):
            _uuid(row.get(field), "rtb_research_video_pin_invalid")
        _sha(row.get("output_sha256"), "rtb_research_video_pin_invalid")
        if row["output_id"] in by_output:
            _reject("rtb_research_duplicate_video_pin")
        by_output[row["output_id"]] = deepcopy(row)
    return sorted(by_output.values(), key=lambda p: (
        p["source_id"], p["source_version_id"], p["output_id"]
    ))


def build_research_input(
    snapshot: dict, handoff: dict, *,
    handoff_id: str, handoff_sha256: str,
    context_envelope: dict, resolved_videos: dict,
) -> dict:
    """Pure fail-closed frozen C1+C2 data build; only upstream verified data.

    The service below alone is responsible for obtaining the verified inputs
    from PostgreSQL. No caller-supplied JSON is an authorization to dispatch.
    """
    binding = checked_b9_mapping(snapshot, handoff)
    pid = binding["project_id"]
    hid = _uuid(handoff_id, "rtb_research_handoff_id")
    hsha = _sha(handoff_sha256, "rtb_research_handoff_hash")
    if canonical_sha(handoff) != hsha:
        _reject("rtb_research_handoff_hash_changed")
    if (not isinstance(context_envelope, dict)
            or context_envelope.get("project_id") != pid
            or context_envelope.get("context_id") != binding["project_source_snapshot_id"]
            or context_envelope.get("context_sha256") != binding["project_source_snapshot_sha256"]
            or context_envelope.get("approved") is not True
            or not isinstance(context_envelope.get("approval"), dict)
            or context_envelope["approval"].get("decision") not in
                ("approved", "approved_partial")):
        _reject("rtb_research_context_pin_mismatch")
    context = context_envelope.get("context")
    if not isinstance(context, dict) or not isinstance(context.get("analysis_refs"), list):
        _reject("rtb_research_context_missing_analyses")

    pins = _video_pins(binding["video_analysis_refs"])
    actual = [row for row in context["analysis_refs"]
              if isinstance(row, dict) and row.get("schema_version") == "video-intelligence/v1"]
    if len(actual) != len(pins):
        _reject("rtb_research_a_b_video_set_mismatch")
    a_by_output = {}
    for ref in actual:
        if (not all(ref.get(k) is not None for k in
                    ("source_id", "source_version_id", "output_id", "output_sha256"))
                or ref["output_id"] in a_by_output):
            _reject("rtb_research_a_video_duplicate_or_missing")
        a_by_output[ref["output_id"]] = ref
    for pin in pins:
        a = a_by_output.get(pin["output_id"])
        if a is None or any(a.get(key) != pin[key] for key in pin):
            _reject("rtb_research_a_b_video_set_mismatch")
    observations = index_original_a_observations(pid, pins, resolved_videos)
    if len(observations) > MAX_OBSERVATIONS:
        _reject("rtb_research_observations_count_exceeded")
    if len(snapshot["facts"]) > MAX_FACTS:
        _reject("rtb_research_fact_count_exceeded")

    c1 = []
    for fact in snapshot["facts"]:
        sha = fact["fact_id"]
        c1.append({
            "b9_fact_id": sha,
            "rtb_fact_id": binding["b9_fact_sha_to_sre_uuid"][sha],
            "kind": fact["kind"],
            "reviewed_text": fact["reviewed_text"],
            "source_role": fact["source_role"],
            "subject_scope": fact["subject_scope"],
            "subject_id": fact["subject_id"],
            "citations": deepcopy(fact["citations"]),
            "original_group_id": fact["group_id"],
            "review_event_sha256": fact["review_event_sha256"],
            "source_quote_grounding_only": fact["source_quote_grounding_only"],
            "may_support_own_product_claim": (
                fact["kind"] in ("function", "product_type")
                and fact["source_role"] == "own_product"
                and fact["subject_scope"] == "own_product"
            ),
        })
    indexed = []
    for identifier in sorted(observations):
        row = observations[identifier]
        if len(canonical(row).encode("utf-8")) > MAX_OBSERVATION_BYTES:
            _reject("rtb_research_observation_too_large")
        indexed.append(row)
    manifest = {
        "schema_version": SCHEMA,
        "builder_version": ENGINE,
        "project_id": pid,
        "input_mode": "knowledge_snapshot_v1",
        "b9_snapshot_id": binding["knowledge_snapshot_id"],
        "b9_snapshot_sha256": binding["knowledge_snapshot_sha256"],
        "b9_handoff_id": hid,
        "b9_handoff_sha256": hsha,
        "a_context_id": binding["project_source_snapshot_id"],
        "a_context_sha256": binding["project_source_snapshot_sha256"],
        "a_approval_decision": context_envelope["approval"]["decision"],
        "b9_fact_sha_to_sre_uuid": deepcopy(binding["b9_fact_sha_to_sre_uuid"]),
        "video_analysis_refs": pins,
        "c1_approved_facts": c1,
        "c2_original_video_observations": indexed,
        "fact_count": len(c1),
        "observation_count": len(indexed),
        "video_count": len(pins),
        "facts_are_human_approved": True,
        "observations_are_original_a_evidence_not_approved_product_facts": True,
        "research_goal": None,
        "paid_execution_authorized": False,
        "provider_calls": 0,
    }
    if len(canonical(manifest).encode("utf-8")) > MAX_INPUT_BYTES:
        _reject("rtb_research_input_too_large")
    return {**manifest, "research_input_sha256": canonical_sha(manifest)}


class RTBResearchInputService:
    """Server-side read-only materialization from exact immutable B9/A pins.

    B's export replays the submitted human review and validates reviewer grants;
    B's materialize_c_handoff replays the immutable saved C handoff. A's resolver
    checks database provenance and SHA on every read. No SQL write is initiated
    here, and no client may supply unverified observations.
    """

    def __init__(self, connection_factory=None, *, snapshots=None, a_resolver=None):
        self.snapshots = snapshots or KnowledgeSnapshotRepository(
            connection_factory, verify_local_files=True)
        self.a_resolver = a_resolver or PostgresAResolver(
            connection_factory, require_current=False, verify_local_files=True)

    def preview(self, project_id: str, snapshot_id: str, reviewer_id: str) -> dict:
        pid = _project(project_id)
        sid = _uuid(snapshot_id, "rtb_research_snapshot_id")
        stored = self.snapshots.export_by_id(pid, sid, reviewer_id)
        if (not isinstance(stored, dict) or stored.get("snapshot_id") != sid
                or not isinstance(stored.get("snapshot"), dict)
                or stored.get("snapshot_sha256") != canonical_sha(stored["snapshot"])):
            _reject("rtb_research_b9_export_invalid")
        snapshot = stored["snapshot"]
        if snapshot.get("project_id") != pid:
            _reject("rtb_research_foreign_b9_snapshot")
        hand = self.snapshots.materialize_c_handoff(
            pid, snapshot["session_id"], reviewer_id)
        if (not isinstance(hand, dict)
                or not isinstance(hand.get("handoff"), dict)):
            _reject("rtb_research_handoff_not_materialized")
        context_id = _uuid(snapshot["context_id"], "rtb_research_context_id")
        envelope = self.a_resolver.resolve_context(pid, context_id)
        pins = _video_pins(snapshot["video_analysis_refs"])
        outputs = {}
        for pin in pins:
            typed = VideoAnalysisPin(**pin)
            outputs[pin["output_id"]] = self.a_resolver.resolve_video(
                pid, context_id, typed)
        result = build_research_input(
            snapshot, hand["handoff"],
            handoff_id=hand["handoff_id"],
            handoff_sha256=hand["handoff_sha256"],
            context_envelope=envelope, resolved_videos=outputs)
        if result["b9_snapshot_id"] != sid:
            _reject("rtb_research_b9_identity_drift")
        return result
