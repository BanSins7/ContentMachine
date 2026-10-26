"""B Stage01: immutable A->B contract validation, *without* database or provider calls.

This module consumes the real Stage11 context_service._snapshot() envelope.
It never claims that local validation substitutes for Stage02's DB pin/artifact
checks or Stage03's exhaustive text-fragment inventory.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

from intelligence.project_sources.context_builder import sha as canonical_sha

CONTRACT_VERSION = "ke-a-context-envelope/1"
A_CONTEXT_SCHEMA = "project-context/v1"
A_VIDEO_SCHEMA = "video-intelligence/v1"
C_INPUT_MODE = "knowledge_snapshot_v1"
LEGACY_RTB_INPUT_MODES = ("legacy_questionnaire_v1", "approved_project_context_v1")
SOURCE_ROLES = frozenset((
    "own_product", "audience_research", "competitor", "creative_reference", "other",
))
TEXT_OUTPUT_SCHEMAS = frozenset(("document-segments/v1", "web-source-extract/v1"))
# This only supplies safe defaults; B Stage03 must resolve subject identities
# and questionnaire field paths before extracting product-specific facts.
SOURCE_ROLE_DEFAULTS = {
    "product_website": "own_product",
    "competitor_website": "competitor",
    "competitor_account": "competitor",
    "promoter_account": "creative_reference",
    "user_video_reference": "creative_reference",
    "research_link": "other",
    "research_document": "other",
    "questionnaire_field": "other",
    "other": "other",
}


class ContractError(ValueError):
    """Stable reason code. Never attach source text to errors/logs."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@lru_cache(maxsize=1)
def envelope_validator() -> Draft202012Validator:
    import json

    path = Path(__file__).resolve().parent / "schemas" / "context_envelope_v1.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _unique(items: list[dict], key: str, code: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in items:
        ident = item[key]
        if ident in result:
            raise ContractError(code)
        result[ident] = item
    return result


def validate_context_envelope(
    envelope: Mapping[str, Any],
    *,
    expected_project_id: str | None = None,
    require_approved: bool = True,
) -> dict[str, Any]:
    """Check JSON Schema AND internal hash, graph, review, and coverage invariants.

    An already approved A Context is an approved *source selection*, NOT a
    human-approved B fact set. This function is read-only and deliberately
    does not fetch the current database or any artifacts.
    """
    if not isinstance(envelope, dict):
        raise ContractError("envelope_schema_invalid")
    if next(envelope_validator().iter_errors(envelope), None) is not None:
        raise ContractError("envelope_schema_invalid")

    ctx = envelope["context"]
    if expected_project_id is not None and envelope["project_id"] != expected_project_id:
        raise ContractError("foreign_project")
    if ctx["project_id"] != envelope["project_id"]:
        raise ContractError("foreign_project")
    if ctx["profile"]["revision"] != envelope["profile_revision"] or (
        ctx["profile"]["hash"] != envelope["profile_hash"]
    ):
        raise ContractError("profile_pin_mismatch")

    digest = canonical_sha(ctx)
    if digest != envelope["context_sha256"]:
        raise ContractError("context_hash_mismatch")
    if digest != envelope["input_fingerprint"]:
        raise ContractError("fingerprint_mismatch")

    approval = envelope["approval"]
    decision = approval["decision"] if approval else None
    eligible = decision in ("approved", "approved_partial")
    if envelope["approved"] != eligible:
        raise ContractError("approval_state_mismatch")
    if require_approved and not eligible:
        raise ContractError("context_not_approved")

    sources = _unique(ctx["source_manifest"], "source_id", "duplicate_source")
    analyses = _unique(ctx["analysis_refs"], "output_id", "duplicate_output")
    evidence = _unique(ctx["evidence_manifest"], "evidence_id", "duplicate_evidence")

    for source in sources.values():
        source_id = source["source_id"]
        version_id = source["source_version_id"]
        linked = {
            oid for oid, output in analyses.items()
            if output["source_id"] == source_id
        }
        if set(source["analysis_refs"]) != linked:
            raise ContractError("source_analysis_membership_mismatch")
        if source["excluded_reason"] is None and version_id is None:
            raise ContractError("unversioned_source")
        for output_id in linked:
            if analyses[output_id]["source_version_id"] != version_id:
                raise ContractError("analysis_source_version_mismatch")

    for analysis in analyses.values():
        if analysis["source_id"] not in sources:
            raise ContractError("foreign_analysis_source")

    evidence_by_source = {source_id: [] for source_id in sources}
    for record in evidence.values():
        source = sources.get(record["source_id"])
        if source is None or source["source_version_id"] != record["source_version_id"]:
            raise ContractError("evidence_source_version_mismatch")
        output_id = record["processing_output_id"]
        if output_id is not None:
            output = analyses.get(output_id)
            if output is None or (output["source_id"], output["source_version_id"]) != (
                record["source_id"], record["source_version_id"]
            ):
                raise ContractError("evidence_output_pin_mismatch")
        evidence_by_source[record["source_id"]].append(record)

    if ctx["evidence_ids"] != sorted(evidence):
        raise ContractError("evidence_inventory_mismatch")
    approved_ids = sorted(
        ident for ident, record in evidence.items()
        if record["review_decision"] == "approved"
    )
    if ctx["approved_evidence_ids"] != approved_ids:
        raise ContractError("approved_evidence_mismatch")
    for source_id, source in sources.items():
        records = evidence_by_source[source_id]
        if source["evidence_count"] != len(records) or source["approved_evidence_count"] != sum(
            record["review_decision"] == "approved" for record in records
        ):
            raise ContractError("source_coverage_mismatch")

    missing = _unique(ctx["unavailable_or_excluded_sources"], "source_id", "duplicate_exclusion")
    expected_missing = {
        sid: (source["source_version_id"], source["excluded_reason"])
        for sid, source in sources.items() if source["excluded_reason"] is not None
    }
    if {sid: (item["source_version_id"], item["reason"]) for sid, item in missing.items()} != expected_missing:
        raise ContractError("exclusion_mismatch")

    coverage = ctx["coverage"]
    if coverage != {
        "sources_total": len(sources),
        "sources_incomplete": len(missing),
        "analysis_outputs": len(analyses),
        "evidence_total": len(evidence),
        "reviewed_approved": len(approved_ids),
        "reviewed_rejected": sum(x["review_decision"] == "rejected" for x in evidence.values()),
        "review_pending": sum(x["review_decision"] is None for x in evidence.values()),
    }:
        raise ContractError("coverage_mismatch")
    for item in ctx["reviewable_relationships"]:
        if item["a"] not in evidence or item["b"] not in evidence:
            raise ContractError("relationship_evidence_mismatch")

    if eligible:
        if decision == "approved" and missing:
            raise ContractError("approved_with_missing_sources")
        if decision == "approved_partial" and not missing:
            raise ContractError("partial_without_missing_sources")
    # A payload 'approval_status' stays 'pending' forever; SQL approval is
    # in this envelope, never silently re-hash/modify A's immutable payload.
    return envelope


def default_source_role(source_type: str) -> str:
    """Conservative routing suggestion, NOT verified subject ownership."""
    return SOURCE_ROLE_DEFAULTS.get(source_type, "other")


@dataclass(frozen=True)
class TextFragmentPin:
    """Proposed B inventory identity, distinct from A's reviewable Evidence IDs.

    No extracted text is in the identity. Stage03 will resolve the immutable
    original and validate the character range before persisting this pin.
    """

    fragment_id: str
    source_id: str
    source_version_id: str
    text_sha256: str
    segment_index: int
    locator: Mapping[str, Any]
    source_role: str
    subject_scope: str
    subject_id: str | None = None
    output_id: str | None = None
    output_sha256: str | None = None
    evidence_id: str | None = None


@dataclass(frozen=True)
class VideoAnalysisPin:
    source_id: str
    source_version_id: str
    output_id: str
    schema_version: str
    output_sha256: str


def pinned_video_analyses(envelope: dict[str, Any]) -> tuple[VideoAnalysisPin, ...]:
    """Return original A refs only; no video transcript, prompts, or copy."""
    validate_context_envelope(envelope)
    return tuple(
        VideoAnalysisPin(
            source_id=output["source_id"],
            source_version_id=output["source_version_id"],
            output_id=output["output_id"],
            schema_version=output["schema_version"],
            output_sha256=output["output_sha256"],
        )
        for output in envelope["context"]["analysis_refs"]
        if output["schema_version"] == A_VIDEO_SCHEMA
    )


class ImmutableAResolver(Protocol):
    """Stage02 implementation MUST use project-scoped immutable SQL pins.

    A remote LLM/URL is never a resolver for these records. No concrete
    resolver or B run execution is introduced during Stage01.
    """

    def resolve_context(self, project_id: str, context_id: str) -> dict[str, Any]: ...

    def resolve_output(
        self, project_id: str, context_id: str, source_id: str,
        source_version_id: str, output_id: str, expected_sha256: str,
    ) -> dict[str, Any]: ...

    def resolve_evidence(
        self, project_id: str, context_id: str, evidence_id: str,
        expected_quote_sha256: str, expected_locator_sha256: str,
    ) -> dict[str, Any]: ...

    def resolve_video(
        self, project_id: str, context_id: str, pin: VideoAnalysisPin,
    ) -> dict[str, Any]: ...
