"""M07: authoritative, project-scoped D1 video provenance (no Collector needed).

SRE keeps only references to Discovery D1's canonical videos/discoveries. A
client-provided dict, search text, or Collector query UUID is never authority.
The public loader uses a single REPEATABLE READ READ ONLY PostgreSQL snapshot;
the low-level reader requires the caller to supply a consistent DB transaction.
M08 will extend the late-run rights/expiry gate; M07 never dispatches paid work.
"""
from __future__ import annotations

from ..v1_contracts import (
    code, fingerprint, nonempty, positive, project_id, record, reject,
    sha256_digest, utc_time,
)

D1_PROVENANCE_VERSION = "sre-d1-provenance/v1"
_KEYS = (
    "schema_version", "project_id", "video_id", "platform",
    "platform_video_id", "discovery_id", "source_id",
    "source_policy_version_id", "source_request_id", "search_run_id",
    "search_query_id", "metadata_observation_id", "content_version_id",
    "content_sha256", "completeness", "expires_at", "provenance_sha256",
)
_OPTIONAL_BIGINTS = (
    "source_request_id", "search_run_id", "search_query_id",
    "metadata_observation_id",
)


def validate_d1_provenance(payload):
    """Validate canonical transport syntax/digest; NOT a DB authorization."""
    record(payload, "d1_provenance", required=_KEYS)
    if payload["schema_version"] != D1_PROVENANCE_VERSION:
        reject("d1_provenance.schema_version", "unsupported D1 provenance version",
               "unsupported_schema_version")
    project_id(payload["project_id"], "d1_provenance.project_id")
    for name in ("video_id", "discovery_id", "source_policy_version_id",
                 "content_version_id"):
        positive(payload[name], "d1_provenance." + name)
    for name in _OPTIONAL_BIGINTS:
        if payload[name] is not None:
            # These are D1 PostgreSQL BIGINTs; Collector UUIDs and bools fail.
            positive(payload[name], "d1_provenance." + name)
    if payload["platform"] not in ("youtube", "tiktok", "instagram"):
        reject("d1_provenance.platform", "unsupported D1 video platform")
    nonempty(payload["platform_video_id"], "d1_provenance.platform_video_id", limit=512)
    code(payload["source_id"], "d1_provenance.source_id")
    sha256_digest(payload["content_sha256"], "d1_provenance.content_sha256")
    if payload["completeness"] not in ("metadata_only", "partial", "full"):
        reject("d1_provenance.completeness", "unsupported content completeness")
    if payload["expires_at"] is not None:
        utc_time(payload["expires_at"], "d1_provenance.expires_at")
    sha256_digest(payload["provenance_sha256"], "d1_provenance.provenance_sha256")
    if fingerprint({k: v for k, v in payload.items() if k != "provenance_sha256"}) != payload["provenance_sha256"]:
        reject("d1_provenance.provenance_sha256", "provenance digest mismatch",
               "snapshot_mismatch")
    return dict(payload)


def read_d1_provenance(conn, project, content_version_id, *, external_llm=False):
    """Read exact attached D1 lineage; caller controls a consistent DB transaction.

    Project membership and actual video/discovery/source/request/query/observation
    records are required. S3 also rechecks live D1 rights and content checksum;
    a fully valid but revoked snapshot is still denied. SQL is SELECT-only.
    """
    from ..intake import read_content

    pid = project_id(project)
    cid = positive(content_version_id, "content_version_id")
    # No title lookup or global-video fallback. First validate the rights and
    # immutable content digest using the existing S3 read-time gate.
    authorized = read_content(conn, pid, cid, external_llm=external_llm)
    row = conn.execute(
        """SELECT c.id,c.project_id,c.video_id,c.source_id,
                  c.source_policy_version_id,c.provenance,c.metadata_observation_id,
                  d.id,d.video_id,d.source_id,d.search_query_id,
                  d.source_request_id,d.run_id,
                  sr.run_id,sr.source_id,sr.query_id,
                  q.source_id,
                  obs.video_id,obs.source_id,obs.source_policy_version_id,
                  v.platform,v.platform_video_id
           FROM sre_readable_content_versions c
           JOIN project_registry pr ON pr.id=c.project_id
           JOIN sre_project_videos pv
             ON pv.project_id=c.project_id AND pv.video_id=c.video_id
           JOIN sre_project_discoveries pd
             ON pd.project_id=c.project_id AND pd.video_id=c.video_id
           JOIN discoveries d ON d.id=pd.discovery_id
             AND d.id::text=c.provenance->>'discovery_id'
           JOIN videos v ON v.id=c.video_id
           LEFT JOIN source_requests sr ON sr.id=d.source_request_id
           LEFT JOIN search_queries q ON q.id=d.search_query_id
           LEFT JOIN source_video_metadata obs ON obs.id=c.metadata_observation_id
           WHERE c.project_id=%s AND c.id=%s""",
        (pid, cid),
    ).fetchall()
    # Not merely "first row wins": an unexpected ambiguous binding is denied.
    if len(row) != 1:
        reject("d1_provenance", "project discovery lineage missing or ambiguous",
               "missing_provenance")
    (stored_id,stored_project,stored_video,stored_source,stored_policy,
     stored_prov,observation_id,discovery_id,discovered_video,discovered_source,
     query_id,request_id,run_id,request_run,request_source,request_query,
     query_source,obs_video,obs_source,obs_policy,platform,platform_video_id) = row[0]
    if (stored_id != cid or stored_project != pid or
            stored_video != discovered_video or stored_source != discovered_source or
            stored_video != authorized["video_id"] or
            stored_source != authorized["source_id"] or
            stored_policy != authorized["source_policy_version_id"]):
        reject("d1_provenance", "wrong project/video/source/policy",
               "snapshot_mismatch")
    expected = {"discovery_id": discovery_id, "search_query_id": query_id,
                "source_request_id": request_id}
    if type(stored_prov) is not dict or stored_prov != expected or authorized["provenance"] != expected:
        reject("d1_provenance", "stored snapshot provenance differs from D1 records",
               "snapshot_mismatch")
    if request_id is not None and (
        request_run is None or request_run != run_id or
        request_source != stored_source or request_query != query_id
    ):
        reject("d1_provenance.source_request_id", "D1 request/run/query/source lineage mismatch",
               "snapshot_mismatch")
    if query_id is not None and query_source not in (None, stored_source):
        reject("d1_provenance.search_query_id", "D1 query belongs to different source",
               "snapshot_mismatch")
    if observation_id is not None and (
        obs_video != stored_video or obs_source != stored_source or
        obs_policy != stored_policy
    ):
        reject("d1_provenance.metadata_observation_id",
               "metadata observation from different video/source/policy",
               "snapshot_mismatch")
    raw = {
        "schema_version": D1_PROVENANCE_VERSION,
        "project_id": pid, "video_id": stored_video,
        "platform": platform, "platform_video_id": platform_video_id,
        "discovery_id": discovery_id, "source_id": stored_source,
        "source_policy_version_id": stored_policy, "source_request_id": request_id,
        "search_run_id": run_id, "search_query_id": query_id,
        "metadata_observation_id": observation_id,
        "content_version_id": cid, "content_sha256": authorized["content_sha256"],
        "completeness": authorized["completeness"],
        "expires_at": authorized["expires_at"],
    }
    raw["provenance_sha256"] = fingerprint(raw)
    return validate_d1_provenance(raw)


def load_d1_provenance(project, content_version_id, *, connect=None,
                       external_llm=False):
    """Production entry: explicit pin, one PostgreSQL read-only transaction."""
    from intelligence.taxonomy_builder.pipeline_repository import _connect

    pid = project_id(project)
    cid = positive(content_version_id, "content_version_id")
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return read_d1_provenance(
                conn, pid, cid, external_llm=external_llm
            )


def load_d1_provenance_many(project, content_version_ids, *, connect=None):
    """Read <=1000 explicitly chosen D1 content versions, idempotent on replay.

    Duplicate *version IDs* are eliminated in deterministic numeric order;
    distinct versions or different selected discoveries stay distinct.
    This function does not ingest a Collector event or attach global D1 videos.
    """
    pid = project_id(project)
    if type(content_version_ids) not in (list, tuple) or not 1 <= len(content_version_ids) <= 1000:
        reject("content_version_ids", "expected 1..1000 explicit content versions")
    ids = sorted({positive(item, "content_version_ids") for item in content_version_ids})
    from intelligence.taxonomy_builder.pipeline_repository import _connect
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return [read_d1_provenance(conn, pid, cid) for cid in ids]


__all__ = [
    "D1_PROVENANCE_VERSION", "validate_d1_provenance",
    "read_d1_provenance", "load_d1_provenance",
    "load_d1_provenance_many",
]
