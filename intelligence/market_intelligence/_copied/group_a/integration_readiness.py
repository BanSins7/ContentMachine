"""GA24 standalone schema/port handshake: zero peer imports or mutations.

This is only a *declared schema inventory*, never QDP authorization, real
Group B ACK or deployed Unified Analytics capability. Missing peer objects
must block promotion without making edits to other branches.
"""
from __future__ import annotations

from collections.abc import Mapping


SCHEMAS={
    "ga":(
        "ga_runs","ga_media_refs","ga_steps","ga_results","ga_outbox",
        "ga_step_attempts","ga_qdp_revocation_receipts",
        "ga20_locally_current_results","ga_local_readable_results",
        "video_feature_a_analyses","carousel_feature_a_analyses",
    ),
    "qdp":(
        "qdp_preparations","qdp_outbox","qdp_outbox_deliveries",
        "qdp_group_current_preparations","qdp_invalidations",
    ),
    "sre_d1_media":(
        "sre_project_videos","sources","source_policy_versions",
        "media_assets","videos",
    ),
    "group_b":(
        "group_b_runs","group_b_inbox",
    ),
}
# B and UAE consume exact native GA facts; no direct "Analytics SQL write"
# is necessary or permissible from Group A.
WIRE_VERSIONS={
    "qdp_to_a":"qdp-handoff/v1",
    "qdp_ready_envelope":"qdp.preparation_ready/v1",
    "qdp_original_event":"qdp-event/v1",
    "ga_receipt":"group-a-receipt/v1",
    "media":"ga-media-manifest/v1",
    "ga_snapshot":"group-a-to-b/v1",
    "b_contract":"group-a-to-b/v1",
    "uae_group_a":"group-a-to-b/v1",
    "qdp_revocation":"qdp-event/v1",
}
REQUIRED_EXTERNAL_GATES=(
    "host_user_project_authorization",
    "qdp_live_current_preparation",
    "current_source_policy_and_verified_media_sha",
    "trusted_local_stage_executor_registry",
    "ga18_genuine_full_coverage_certification",
    "group_b_true_durable_consumer_receipt",
    "uae_independent_qualified_read",
    "pg17_fresh_upgrade_and_windows_linux_e2e",
)
NO_GUESS=("unverified","schema_missing","schema_present_not_authenticated")


def inspect_merged_schema(conn):
    """Read-only, no cached truth and no automatic peer activation."""
    if not callable(getattr(conn,"execute",None)):
        raise TypeError("explicit trusted PostgreSQL connection required")
    status={}
    for peer,objects in SCHEMAS.items():
        missing=[]
        for name in objects:
            r=conn.execute(
                "SELECT to_regclass(%s)::text",("public."+name,)
            ).fetchone()
            if r is None or r[0] is None:
                missing.append(name)
        status[peer]={
            "status":("schema_missing" if missing else
                      "schema_present_not_authenticated"),
            "required_tables_or_views":list(objects),
            "missing":missing,
            "authenticated_live":False,
        }
    return {
        "schema_version":"ga24-peer-handshake/v1",
        "overall":"unverified",
        "wires":dict(WIRE_VERSIONS),
        "schema":status,
        "unverified_external_gates":list(REQUIRED_EXTERNAL_GATES),
        "release_ready":False,
        "no_peer_schema_written":True,
    }


def require_exact_peer_wires(declared):
    """Reject incompatible version from host; not evidence of live readiness."""
    if not isinstance(declared,Mapping):
        raise ValueError("peer_wires_missing")
    reasons=[]
    for port,expected in WIRE_VERSIONS.items():
        observed=declared.get(port)
        if observed!=expected:
            reasons.append(port+("_unavailable" if observed is None
                                else "_version_mismatch"))
    return tuple(sorted(reasons))


def qdp_revocation_receiver(connect):
    """Only explicitly created; usable by existing QDP FIX-15 dispatcher."""
    from .qdp_revocation import PostgresQDPRevocationReceiver
    return PostgresQDPRevocationReceiver(connect)
