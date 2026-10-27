"""SP-owned merge/readiness probe over actual installed peer schema names.

Structural schema presence is not a feature-ready or authenticated producer
attestation. No optional Collector/RTB/Group imports, provider requests,
migrations, SQL writes or background activity occur here.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _connect, _pid

VERSION = "search-planner-peer-integration-diagnostics/v1"

# Derived from checked-in upstream migration declarations, NOT invented data
# acceptance endpoints. An absent table is distinct from missing live proof.
REQUIRED_TABLES = {
    "rtb_deep_v1": (
        "tb_taxonomy_publications", "tb_deep_publications", "tb_deep_reviews",
    ),
    "rtb_deep_v2": (
        "tb_deep_v2_publications", "tb_deep_v2_reviews",
        "tb_deep_v2_sre_links",
    ),
    "sqg_qv": (
        "sqg_generations", "sqg_candidates", "qv_runs", "qv_decisions",
        "qv_coverage", "qv_outbox",
    ),
    "sp_durable_handoff": (
        "sp_handoff_events", "sp_handoff_chunks",
        "sp_handoff_query_links", "sp_delivery_attempts",
        "sp_delivery_receipts",
    ),
    "collector_planning": (
        "collector_query_sets", "collector_plans", "collector_runs",
    ),
    "collector_source_policy": (
        "sources", "source_policy_versions", "source_quota_ledger",
    ),
    "discovery_d1": (
        "videos", "discoveries", "source_requests",
        "collector_video_hypotheses",
    ),
}


def read_integration_readiness(project_id, *, connect=None):
    """Read schema and project boundary only, never return true live readiness.

    The real Collector requires a separately authenticated producer for
    collector_query_sets and receipt readback. RTB V2 requires its own
    approved 14-field reader and version-pinned context consumer. Neither
    can be inferred from to_regclass(), so both remain explicit blockers.
    """
    pid = _pid(project_id)
    factory = connect or _connect
    if not callable(factory):
        raise PipelineError("Invalid integration read-only connection", 422)

    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            project = conn.execute(
                "SELECT 1 FROM project_registry WHERE id=%s", (pid,)
            ).fetchone()
            if project is None:
                raise PipelineError("Project not found", 404)
            installed = {}
            for table in sorted({table for tables in REQUIRED_TABLES.values()
                                 for table in tables}):
                # Hardcoded public names, no user SQL, no access to provider
                # credentials, rows from peers, pricing or patient data.
                row = conn.execute(
                    "SELECT to_regclass(%s)::text", ("public." + table,)
                ).fetchone()
                if row is None or len(row) != 1:
                    raise PipelineError("Unverifiable integration schema probe", 503)
                installed[table] = row[0] is not None

    systems = {}
    blockers = []
    for name, tables in REQUIRED_TABLES.items():
        missing = [table for table in tables if not installed[table]]
        state = ("absent" if len(missing) == len(tables) else
                 "partial" if missing else "schema_detected_only")
        systems[name] = {
            "schema_state": state,
            "missing_tables": missing,
            "data_provenance_verified": False,
            "producer_acceptance_verified": False,
        }
        if missing:
            blockers.append("missing_schema:" + name)

    # These are NOT satisfiable by creating a matching table or submitting
    # a browser JSON with accepted=true. Remain false until separately
    # implemented and independently validated live peer handshakes.
    blockers.extend((
        "rtb_v2_reader_and_reviewed_context_not_integrated",
        "collector_trusted_qv_publisher_not_integrated",
        "collector_durable_receipt_readback_not_attested",
        "collector_live_execution_and_d1_outbox_not_attested",
        "external_provider_policy_consent_not_attested",
    ))
    return {
        "schema_version": VERSION,
        "project_id": pid,
        "status": "integration_blocked",
        "systems": systems,
        "blocking_reasons": sorted(set(blockers)),
        "rtb_v1_reader_available_in_sp": systems["rtb_deep_v1"]["schema_state"] == "schema_detected_only",
        "rtb_v2_context_enabled": False,
        "collector_operationally_ready": False,
        "source_dispatch_authorized": False,
        "independent_collector_receipt_verified": False,
        "paid_provider_calls": 0,
        "external_source_calls": 0,
        "schema_probe_is_not_end_to_end_certification": True,
    }
