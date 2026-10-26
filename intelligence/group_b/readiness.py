"""GB-MOD-07: point-of-use QDP/A/byte-rights revalidation for each B block.

A fresh report is ephemeral and never authorizes model spending. At the time
of execution the caller MUST run the check again in the execution transaction
and use the same pinned manifest/quote, with GB-MOD-10 approval separately.
"""
from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID
from dataclasses import dataclass

from .adapters.qdp import QDPHandoff, read_current_preparation
from .context.manifest import build_media_evidence_manifest
from .context.evidence import EvidenceFailure
from .contracts import canonical_hash, ContractError
from .dependency_rules import (
    ReadinessReport, ReadinessState, blocked_report, evaluate_block_dependencies, BLOCKS
)
from .errors import DependencyUnavailable, DependencyContractMismatch
from .repository import require_runtime_schema


def _row(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip((i.name for i in cursor.description),row))


def _uuid(value) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(UUID(value))==value
    except ValueError:
        return False


def _safe_code(exc: Exception) -> tuple[ReadinessState,str]:
    """Only bounded, public reason codes. Never echo SQL paths or documents."""
    if isinstance(exc,DependencyUnavailable):
        if exc.dependency=="qdp":
            if exc.reason in (
                "qdp_tables_missing","peer_schema_probe_failed",
                "peer_sql_contract_unavailable","event_or_preparation_unavailable",
                "carousel_media_manifest_unavailable",
            ):
                return ReadinessState.PARTIAL, "qdp_producer_or_schema_unavailable"
            if exc.reason in (
                "policy_media_rights_revoked","media_rights_revoked","media_expired",
                "enrichment_expired","preparation_blocked","run_not_current",
                "qualified_item_not_current","source_media_mapping_mismatch",
            ):
                return ReadinessState.BLOCKED, "qdp_preparation_or_rights_revoked"
            return ReadinessState.BLOCKED,"qdp_event_invalid"
        if exc.dependency=="group_a":
            if exc.reason in ("ga_schema_missing","ga_schema_probe_failed",
                              "a_exact_snapshot_not_found","a_analysis_not_complete"):
                return ReadinessState.PARTIAL,"group_a_not_completed"
            return ReadinessState.BLOCKED,"group_a_snapshot_invalid"
        if exc.dependency=="storage":
            return ReadinessState.BLOCKED,"b_storage_or_identity_invalid"
        return ReadinessState.PARTIAL,"source_media_evidence_unavailable"
    if isinstance(exc,EvidenceFailure):
        if exc.reason in ("a_observation_proofs_missing","evidence_catalog_unavailable",
                          "evidence_record_unavailable","media_file_unavailable",
                          "media_roots_unconfigured","media_duration_unavailable"):
            return ReadinessState.PARTIAL,"verified_media_or_proofs_missing"
        return ReadinessState.BLOCKED,"media_integrity_or_rights_invalid"
    if isinstance(exc,DependencyContractMismatch):
        return ReadinessState.BLOCKED,"upstream_schema_incompatible"
    return ReadinessState.BLOCKED,"upstream_identity_invalid"


def assess_current_run(conn, *, project_id: str, b_run_id: str,
                       ga_result_id: int, ga_result_sha256: str,
                       media_roots, observation_catalog) -> ReadinessReport:
    """Produce a fresh read-only B1–B8 report from owned and peer DB facts.

    No caller-supplied A stream booleans are trusted. No QDP ACK, state writes,
    paid model calls or implicit peer imports. The surrounding caller owns
    transactions, authorization and post-read revocation handling.
    """
    if (type(project_id) is not str or len(project_id)!=32 or
        any(c not in "0123456789abcdef" for c in project_id) or
        not _uuid(b_run_id) or type(ga_result_id) is not int or ga_result_id<=0 or
        type(ga_result_sha256) is not str or len(ga_result_sha256)!=64 or
        any(c not in "0123456789abcdef" for c in ga_result_sha256)):
        raise ValueError("Invalid exact Group B/A run identity")
    try:
        require_runtime_schema(conn)
        data = _row(conn.execute(
            """SELECT b.id::text AS b_run_id,b.project_id,b.video_id,b.source_id,
                      b.content_type,b.state AS b_state,
                      b.video_a_analysis_id,b.carousel_a_analysis_id,
                      b.a_snapshot_sha256,b.media_manifest_sha256,
                      b.source_policy_version_id,
                      i.status AS inbox_state,i.event_key,i.event_sha256,
                      i.qdp_run_id::text AS qdp_run_id,
                      i.preparation_sha256
                 FROM group_b_runs b
                 JOIN group_b_inbox i ON i.id=b.inbox_id
                      AND i.project_id=b.project_id
                      AND i.video_id=b.video_id AND i.source_id=b.source_id
                WHERE b.project_id=%s AND b.id=%s::uuid""",
            (project_id,b_run_id)
        ))
        if data is None or data["inbox_state"]!="accepted":
            raise DependencyUnavailable("storage","inbox_not_accepted")
        if data["b_state"] not in ("draft","quoted","queued","running",
                                  "partial","needs_review"):
            raise DependencyUnavailable("storage","b_run_not_active")
        # Receipt is a durable intake record, not an execution authorization.
        receipt = _row(conn.execute(
            """SELECT r.event_id::text AS event_id,r.event_sha256,
                      r.preparation_sha256,r.run_id::text AS run_id
                 FROM group_b_inbox_receipts r
                 JOIN group_b_inbox i ON i.id=r.inbox_id
                    AND i.project_id=r.project_id
                WHERE r.project_id=%s AND r.run_id=%s::uuid""",
            (project_id,b_run_id)
        ))
        if (receipt is None or receipt["run_id"]!=b_run_id or
            receipt["event_id"]!=data["event_key"] or
            receipt["event_sha256"]!=data["event_sha256"] or
            receipt["preparation_sha256"]!=data["preparation_sha256"]):
            raise DependencyUnavailable("storage","intake_receipt_mismatch")
        pointer=QDPHandoff.parse({
            "event_id":data["event_key"],"project_id":project_id,
            "qdp_run_id":data["qdp_run_id"],"video_id":data["video_id"],
            "analysis_group":"B","input_sha256":data["preparation_sha256"],
            "schema_version":"qdp-handoff/v1",
        })
        preparation=read_current_preparation(conn,pointer,data["event_sha256"])
        if (preparation.project_id!=project_id or preparation.event_id!=pointer.event_id or
            preparation.qdp_run_id!=pointer.qdp_run_id or
            preparation.video_id!=data["video_id"] or
            preparation.source_id!=data["source_id"] or
            preparation.content_type!=data["content_type"] or
            preparation.input_sha256!=pointer.input_sha256 or
            preparation.event_sha256!=data["event_sha256"] or
            preparation.source_policy_version_id!=data["source_policy_version_id"]):
            raise DependencyUnavailable("qdp","source_media_mapping_mismatch")
        # Build an exact, fresh byte-verified A/D1 manifest. No older digest
        # can override a revoked/mutated asset after this inspection.
        manifest=build_media_evidence_manifest(
            conn,b_run_id=b_run_id,ga_result_id=ga_result_id,
            ga_result_sha256=ga_result_sha256,media_roots=media_roots,
            observation_catalog=observation_catalog,
        )
        if (manifest.project_id!=project_id or manifest.b_run_id!=b_run_id or
            manifest.qdp_run_id!=pointer.qdp_run_id or
            manifest.qdp_input_sha256!=pointer.input_sha256 or
            manifest.source_id!=data["source_id"] or
            manifest.video_id!=data["video_id"] or
            manifest.ga_result_id!=ga_result_id or
            manifest.ga_result_sha256!=ga_result_sha256 or
            manifest.media_manifest_sha256!=data["media_manifest_sha256"] or
            manifest.source_policy_version_id!=preparation.source_policy_version_id):
            raise DependencyUnavailable("group_a","a_snapshot_lineage_mismatch")
        # Current QDP video asset must be among the GA pinned assets, never
        # another video's valid media hash; carousel QDP handoff currently
        # fails closed in the peer reader until a real slide manifest exists.
        if not any((a.asset_id==preparation.media_asset_id and
                    a.source_sha256==preparation.media_sha256)
                   for a in manifest.assets):
            raise DependencyUnavailable("qdp","source_media_mapping_mismatch")
        snapshot = _row(conn.execute(
            """SELECT g.state,g.result_schema,g.result_sha256,g.snapshot,
                      g.video_a_analysis_id,g.carousel_a_analysis_id
                 FROM ga_results g
                 JOIN ga_runs a ON a.id=g.run_id
                   AND a.project_id=g.project_id AND a.video_id=g.video_id
                 JOIN group_b_runs b ON b.project_id=a.project_id
                   AND b.video_id=a.video_id AND b.source_id=a.source_id
                WHERE b.project_id=%s AND b.id=%s::uuid
                  AND g.id=%s AND g.result_sha256=%s
                  AND a.qdp_run_id=%s::uuid
                  AND a.qdp_input_sha256=%s""",
            (project_id,b_run_id,ga_result_id,ga_result_sha256,
             pointer.qdp_run_id,pointer.input_sha256)
        ))
        if (snapshot is None or snapshot["state"]!="completed" or
            snapshot["result_schema"]!="group-a-to-b/v1" or
            snapshot["result_sha256"]!=ga_result_sha256 or
            type(snapshot["snapshot"]) is not dict or
            canonical_hash(snapshot["snapshot"])!=ga_result_sha256 or
            snapshot["snapshot"].get("analysis_state")!="completed" or
            snapshot["snapshot"].get("manifest_sha256")!=manifest.media_manifest_sha256 or
            snapshot["snapshot"].get("content_kind")!=manifest.content_kind or
            (snapshot["video_a_analysis_id"] if data["content_type"]=="video"
             else snapshot["carousel_a_analysis_id"]) != (
                data["video_a_analysis_id"] if data["content_type"]=="video"
                else data["carousel_a_analysis_id"])):
            raise DependencyUnavailable("group_a","a_snapshot_lineage_mismatch")
        return evaluate_block_dependencies(
            manifest,a_streams=snapshot["snapshot"].get("streams")
        )
    except (DependencyUnavailable,DependencyContractMismatch,EvidenceFailure,
            ContractError) as exc:
        state,reason=_safe_code(exc)
        return blocked_report(project_id,b_run_id,reason,state=state)


def require_block_ready(report: ReadinessReport, block: str, *, expected_manifest_sha256: str):
    """Fail closed with no paid/worker side effects; refresh report at use time."""
    if not isinstance(report,ReadinessReport) or type(expected_manifest_sha256) is not str:
        raise ValueError("Invalid readiness report")
    decision=report.for_block(block)
    if (decision.state is not ReadinessState.READY or
        report.manifest_sha256!=expected_manifest_sha256 or
        decision.evidence_sha256!=expected_manifest_sha256):
        raise DependencyUnavailable("readiness","block_not_verified")
    return decision
