"""S3 trusted D1 discovery -> project SRE metadata snapshots.

No scraping, no download, no external model, no implicit cross-project attach.
A caller must already be authorized to associate a selected D1 discovery with
an existing project. DB membership and D1 provenance are verified server-side.
"""
from __future__ import annotations

from datetime import datetime, timezone
from json import dumps

from .content_builder import build_metadata_envelope, intake_fingerprint
from .source_policy import expiry_for_intake, verified_rights, utc_iso
from .v1_contracts import (
    SREContractError, authorize_content_usage, project_id, positive, reject,
)


def _authority(conn, discovery_id):
    row = conn.execute(
        """SELECT d.video_id,d.source_id,d.search_query_id,
                  d.source_request_id,d.run_id,
                  s.status,s.can_store_metadata,s.can_analyze_media,
                  s.retention_days,
                  p.id,p.is_active,p.reviewed_at,p.permissions,
                  p.retention_policy,
                  sr.run_id,sr.source_id,sr.query_id
           FROM discoveries d
           JOIN sources s ON s.id=d.source_id
           JOIN source_policy_versions p ON p.source_id=s.id AND p.is_active
           LEFT JOIN source_requests sr ON sr.id=d.source_request_id
           WHERE d.id=%s""", (discovery_id,),
    ).fetchone()
    if row is None:
        reject("discovery_id", "D1 discovery not found or policy disabled", "rights_denied")
    video, source_id, query_id, request_id, run_id = row[:5]
    source = {"id": source_id, "status": row[5],
              "can_store_metadata": row[6], "can_analyze_media": row[7],
              "retention_days": row[8]}
    policy = {"source_id": source_id, "id": row[9],
              "is_active": row[10], "reviewed_at": row[11],
              "permissions": row[12], "retention_policy": row[13]}
    if request_id is not None and (
        row[14] is None or row[14] != run_id
        or row[15] != source_id or row[16] != query_id
    ):
        reject("source_request_id", "D1 provider request lineage is inconsistent",
               "snapshot_mismatch")
    return video, source_id, query_id, request_id, source, policy


def _observation(conn, video, source_id, policy_id):
    """Use latest source-specific observation; do not fall back to another source."""
    raw = conn.execute(
        """SELECT id,source_policy_version_id,title,description,is_current,
                  expires_at,deleted_at
           FROM source_video_metadata
           WHERE video_id=%s AND source_id=%s
           ORDER BY fetched_at DESC,id DESC LIMIT 1""",
        (video, source_id),
    ).fetchone()
    if raw is None:
        return None
    if raw[1] != policy_id:
        # Old observations from a different policy must not be republished
        # under newly granted processing rights.
        reject("metadata_observation_id",
               "latest metadata belongs to different source policy",
               "rights_denied")
    return {"id": raw[0], "title": raw[2], "description": raw[3],
            "is_current": raw[4], "expires_at": raw[5],
            "deleted_at": raw[6]}


def attach_discovery(conn, project, discovery_id):
    """Explicit local-owner initiated intake, transactional and idempotent.

    Authentication is outside this internal service. Never expose raw
    project_id/discovery_id from remote HTTP directly to this function.
    Concurrent intake attempts for the same project are serialized by locking
    its workspace, protecting revision increment and replay check.
    """
    pid = project_id(project)
    did = positive(discovery_id, "discovery_id")
    with conn.transaction():
        ok = conn.execute(
            """SELECT 1 FROM sre_workspaces w
               JOIN project_registry p ON p.id=w.project_id
               WHERE w.project_id=%s FOR UPDATE OF w""", (pid,),
        ).fetchone()
        if not ok:
            reject("project_id", "project workspace is not registered", "rights_denied")
        video, source_id, query, request, source, policy = _authority(conn, did)
        rights = verified_rights(source, policy)
        observed = _observation(conn, video, source_id, policy["id"])
        now = conn.execute("SELECT now()").fetchone()[0]
        expiry = expiry_for_intake(source, policy, observed, now=now)

        # Grant only for the explicit selected D1 discovery; we never attach
        # the entire global D1 video catalog to a project.
        conn.execute("""INSERT INTO sre_project_videos(project_id,video_id)
                        VALUES(%s,%s) ON CONFLICT DO NOTHING""", (pid, video))
        conn.execute(
            """INSERT INTO sre_project_discoveries(project_id,video_id,discovery_id)
               VALUES(%s,%s,%s) ON CONFLICT(project_id,discovery_id) DO NOTHING""",
            (pid, video, did),
        )
        # Validate the proposed payload before inserting. A real DB-generated
        # content version ID is substituted after the immutable row commits.
        envelope = build_metadata_envelope(
            project=pid, video=video, source=source_id,
            policy_id=policy["id"], content_version=1, rights=rights,
            observation=observed, discovery_id=did, search_query_id=query,
            source_request_id=request, expiry=expiry,
        )
        # S1 normalizer adds null transcript/scenes for output shape, while
        # SQL rightly treats key presence as actual media: never persist those.
        stored_data = {k: envelope["data"][k] for k in ("title", "description", "hashtags")}
        obs_id = observed["id"] if observed else None
        key = intake_fingerprint(
            project=pid, video=video, discovery_id=did, source=source_id,
            policy_id=policy["id"], observation_id=obs_id,
            content_sha256=envelope["content_sha256"],
        )
        exists = conn.execute(
            """SELECT id,revision,expires_at FROM sre_readable_content_versions
               WHERE project_id=%s AND video_id=%s AND intake_fingerprint=%s
               ORDER BY id DESC LIMIT 1""", (pid, video, key),
        ).fetchone()
        if exists:
            # Existing readable revision remains canonical: do not extend its
            # retention deadline on retries.
            content_id, revision, saved_expiry = exists
            replay = True
        else:
            previous = conn.execute(
                """SELECT 1 FROM sre_content_versions
                   WHERE project_id=%s AND video_id=%s AND intake_fingerprint=%s""",
                (pid, video, key),
            ).fetchone()
            if previous:
                reject("intake_fingerprint",
                       "previous snapshot was revoked or expired; a new reviewed "
                       "source observation/policy is required", "rights_denied")
            revision = conn.execute(
                """SELECT coalesce(max(revision),0)+1 FROM sre_content_versions
                   WHERE project_id=%s AND video_id=%s""", (pid, video),
            ).fetchone()[0]
            content_id = conn.execute(
                """INSERT INTO sre_content_versions
                   (project_id,video_id,revision,content,provenance,
                    completeness,source_id,source_policy_version_id,
                    media_asset_id,expires_at,content_sha256,
                    intake_fingerprint,metadata_observation_id)
                   VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,'metadata_only',
                           %s,%s,NULL,%s,%s,%s,%s) RETURNING id""",
                (pid, video, revision, dumps(stored_data,ensure_ascii=False),
                 dumps(envelope["provenance"]), source_id, policy["id"],
                 expiry, envelope["content_sha256"], key, obs_id),
            ).fetchone()[0]
            replay = False

        # Derive all authoritative returned fields from the saved/rechecked
        # row rather than accepting or returning a speculative version ID.
        output = read_content(conn, pid, content_id)
        output["revision"] = revision
        output["replayed"] = replay
        return output


def read_content(conn, project, content_version_id, *, external_llm=False):
    """Read only currently permitted content; recheck D1 policy each time."""
    pid = project_id(project)
    cid = positive(content_version_id, "content_version_id")
    row = conn.execute(
        """SELECT c.id,c.video_id,c.content,c.provenance,c.completeness,
                  c.source_id,c.source_policy_version_id,c.content_sha256,
                  c.media_asset_id,c.expires_at,
                  s.status,s.can_store_metadata,s.can_analyze_media,
                  s.retention_days,p.is_active,p.reviewed_at,
                  p.permissions,p.retention_policy
           FROM sre_readable_content_versions c
           JOIN sources s ON s.id=c.source_id
           JOIN source_policy_versions p
             ON p.id=c.source_policy_version_id AND p.source_id=s.id
           WHERE c.project_id=%s AND c.id=%s
             AND NOT EXISTS (
               SELECT 1 FROM sre_live_source_tombstones revoked
               WHERE revoked.project_id=c.project_id
                 AND revoked.content_version_id=c.id
             )""", (pid, cid),
    ).fetchone()
    if row is None:
        reject("content_version_id", "snapshot missing, expired or rights revoked",
               "rights_denied")
    source = {"id": row[5], "status": row[10],
              "can_store_metadata": row[11], "can_analyze_media": row[12],
              "retention_days": row[13]}
    policy = {"source_id": row[5], "is_active": row[14],
              "reviewed_at": row[15], "permissions": row[16],
              "retention_policy": row[17]}
    rights = verified_rights(source, policy, external_llm=external_llm)
    envelope = {
        "schema_version": "sre-content-input/v1",
        "project_id": pid, "video_id": row[1],
        "source_id": row[5], "source_policy_version_id": row[6],
        "content_version_id": row[0], "content_sha256": row[7],
        "completeness": row[4], "rights": rights, "data": row[2],
        "provenance": row[3], "media_asset_id": row[8],
        "expires_at": utc_iso(row[9]),
    }
    authorized = authorize_content_usage(envelope, external_llm=external_llm,
                                         at_time=datetime.now(timezone.utc))
    from .v1_contracts import fingerprint
    if fingerprint(row[2]) != authorized["content_sha256"]:
        reject("content_sha256", "stored content does not match immutable hash",
               "snapshot_mismatch")
    return authorized
