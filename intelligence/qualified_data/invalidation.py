"""QDP FIX-15: trusted, replay-safe human-veto/source invalidation.

A durable invalidation is not downstream revocation ACK. Historical QDP
preparations/receipts remain immutable until lawful retention purge. All
revocations serialize on their QDP run and increment a monotonic generation;
stale delivery leases are retired, and compensating signed revocation events
are generated for Group consumers to acknowledge separately.
"""
from __future__ import annotations

from collections.abc import Mapping
from .contracts import QDPError, project_id, positive_id, idempotency_key, sha256
from .integrations.contracts import QDPHandoffPointer
from .worker_hardening import CONSUMER
from .repository import _connect, _uuid, _json

ORIGINS = frozenset(("sre","human_review","d1_source","retention","configuration","operator"))
REASONS = frozenset((
    "human_veto","sre_evidence_revoked_or_changed","sre_run_revoked",
    "source_rights_revoked","source_retention_expired","metadata_revoked",
    "media_asset_revoked","metric_history_changed","score_config_revoked",
    "operator_revoked","current_evidence_changed",
))
SCHEMA = "qdp-invalidation/v1"


def _validate(origin,reason,video_id,group,provenance):
    if origin not in ORIGINS or type(origin) is not str:
        raise QDPError("invalid_invalidation_origin","Unrecognized trusted event origin",422)
    if reason not in REASONS or type(reason) is not str:
        raise QDPError("invalid_invalidation_reason","Unknown revocation reason",422)
    if group is not None and group not in CONSUMER:
        raise QDPError("invalid_invalidation_group","Unknown revoked analysis group",422)
    if video_id is not None:
        video_id=positive_id(video_id,"video")
    if group is not None and video_id is None:
        raise QDPError("invalid_invalidation_scope","Group scope requires a video",422)
    if type(provenance) is not str or len(provenance)!=64 or any(
            c not in "0123456789abcdef" for c in provenance):
        raise QDPError("invalid_invalidation_provenance",
                       "Authoritative origin provenance SHA256 required",422)
    return video_id


def invalidate_run(project, run_id, *, origin, reason_code, provenance_sha256,
                   video_id=None, group=None, connect=None):
    """Operator/producer-owned invalidation, never public arbitrary user JSON.

    Caller must have independently authenticated and verified the veto/source
    change; this is a low-level trusted port, not a public HTTP endpoint.
    """
    pid=project_id(project)
    rid=_uuid(run_id,"QDP run")
    vid=_validate(origin,reason_code,video_id,group,provenance_sha256)
    with (connect or _connect)() as conn:
        with conn.transaction():
            from .schema import require_qdp_schema
            require_qdp_schema(conn)
            run=conn.execute(
                """SELECT id,invalidation_generation FROM qdp_runs
                   WHERE project_id=%s AND id=%s::uuid FOR UPDATE""",
                (pid,rid)).fetchone()
            if run is None:
                raise QDPError("run_not_found","QDP run does not exist",404)
            if vid is not None and not conn.execute(
                """SELECT 1 FROM qdp_items WHERE project_id=%s
                   AND run_id=%s::uuid AND video_id=%s""",
                (pid,rid,vid)).fetchone():
                raise QDPError("item_not_found","QDP video not in this run",404)
            inserted=conn.execute(
                """INSERT INTO qdp_invalidations
                   (project_id,run_id,video_id,analysis_group,reason_code,
                    origin,provenance_sha256)
                   VALUES (%s,%s::uuid,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING RETURNING id""",
                (pid,rid,vid,group,reason_code,origin,provenance_sha256)
            ).fetchone()
            if inserted is None:
                return {"state":"replayed","project_id":pid,"run_id":rid,
                        "generation":run["invalidation_generation"],
                        "new_revocations":0}
            generation=run["invalidation_generation"]+1
            conn.execute(
                """UPDATE qdp_runs SET invalidation_generation=%s,
                   updated_at=now() WHERE project_id=%s AND id=%s::uuid""",
                (generation,pid,rid))
            ready=conn.execute(
                """SELECT p.video_id,p.analysis_group,p.input_sha256
                   FROM qdp_preparations p
                   WHERE p.project_id=%s AND p.run_id=%s::uuid AND p.state='ready'
                     AND (%s::bigint IS NULL OR p.video_id=%s::bigint)
                     AND (%s::text IS NULL OR p.analysis_group=%s::text)
                   ORDER BY p.video_id,p.analysis_group""",
                (pid,rid,vid,vid,group,group)).fetchall()
            for p in ready:
                # Pending/leased *ready* notifications are no longer legal.
                expired=conn.execute(
                    """UPDATE qdp_outbox_deliveries d
                       SET state='dead_letter',locked_by=NULL,lease_until=NULL,
                           reason_code='preparation_invalidated',updated_at=now()
                     FROM qdp_outbox o
                     WHERE o.event_id=d.event_id AND o.project_id=%s
                       AND o.run_id=%s::uuid AND o.video_id=%s
                       AND o.analysis_group=%s AND o.event_type='preparation_ready'
                       AND d.consumer=%s AND d.state IN ('pending','leased')
                     RETURNING d.event_id,d.attempts,d.lease_generation,d.consumer""",
                    (pid,rid,p["video_id"],p["analysis_group"],
                     CONSUMER[p["analysis_group"]])).fetchall()
                for d in expired:
                    if d["attempts"]>0 and d["lease_generation"]>0:
                        conn.execute(
                            """INSERT INTO qdp_delivery_attempt_log
                               (event_id,consumer,lease_generation,attempt,
                                transition,reason_code)
                               VALUES (%s::uuid,%s,%s,%s,'dead_letter',
                                       'preparation_invalidated')""",
                            (str(d["event_id"]),d["consumer"],
                             d["lease_generation"],d["attempts"]))
                event={
                    "schema_version":"qdp-event/v1",
                    "event_type":"preparation_invalidated",
                    "project_id":pid,"run_id":rid,
                    "video_id":p["video_id"],"analysis_group":p["analysis_group"],
                    "input_sha256":p["input_sha256"],
                    "invalidation_generation":generation,
                    "origin":origin,"reason_code":reason_code,
                    "provenance_sha256":provenance_sha256,
                }
                emitted=conn.execute(
                    """INSERT INTO qdp_outbox
                       (project_id,run_id,video_id,analysis_group,event_type,
                        payload,payload_sha256)
                       VALUES (%s,%s::uuid,%s,%s,'preparation_invalidated',
                               %s::jsonb,%s)
                       RETURNING event_id""",
                    (pid,rid,p["video_id"],p["analysis_group"],
                     _json(event),sha256(event))).fetchone()
                conn.execute(
                    """INSERT INTO qdp_outbox_deliveries(event_id,consumer)
                       VALUES (%s::uuid,%s)""",
                    (str(emitted["event_id"]),CONSUMER[p["analysis_group"]]))
            return {"state":"invalidated","project_id":pid,"run_id":rid,
                    "generation":generation,"new_revocations":len(ready)}


def explicit_refresh(project, prior_run_id, new_key, *, config=None, connect=None):
    """Creates a NEW immutable QDP run from CURRENT LIVE SRE; never reopens old.

    User/operator calls with a distinct idempotency key. Reuses proven
    create_run() which recomputes effective human labels and D1/SRE manifest.
    No writes to historical snapshots or revocation facts.
    """
    pid=project_id(project)
    rid=_uuid(prior_run_id,"previous QDP run")
    key=idempotency_key(new_key)
    factory=connect or _connect
    with factory() as conn:
        prior=conn.execute(
            """SELECT sre_run_id,idempotency_key,config
               FROM qdp_runs WHERE project_id=%s AND id=%s::uuid""",
            (pid,rid)).fetchone()
    if prior is None:
        raise QDPError("run_not_found","Previous QDP run does not exist",404)
    if prior["idempotency_key"]==key:
        raise QDPError("refresh_requires_new_key",
                       "Refreshing cannot replay the original run key",409)
    from .repository import create_run
    fresh=create_run(pid,str(prior["sre_run_id"]),key,
                     prior["config"] if config is None else config,connect=factory)
    if fresh["run_id"]==rid:
        raise QDPError("refresh_reused_prior_run",
                       "Refresh must never mutate the existing run",409)
    return {"ok":True,"previous_run_id":rid,"new_run_id":fresh["run_id"],
            "replayed":fresh["replayed"],"status":fresh.get("status","queued")}
