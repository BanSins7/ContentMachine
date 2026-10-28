"""M36 immutable LIVE-only outbox for QDP without importing any QDP module.

All entrypoints are trusted-host only and require a dedicated idle
autocommit PostgreSQL connection; no HTTP, provider, payment or scheduler.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .opus_consent import _actor, _authorized
from .opus_dispatch_intent import _committable
from .live_content_gate import read_live_content_gate
from .taxonomy_adapter import read_authorized_taxonomy
from .ports.outbound import LIVE_RESULT_VERSION, validate_live_result_event
from .ports.qdp_reference import make_invalidation, validate_invalidation_event
from .v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, reject, sha256_digest,
)

VERSION = "sre-live-result-outbox/v1"
WORKSPACE = "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE"
RUN_ITEM = """SELECT r.execution_mode,r.state,r.cancel_requested,
 r.publication_id::text,r.taxonomy_version_id,r.taxonomy_sha256,
 r.expected_items,r.finished_items,r.blocked_items,
 i.state,i.video_id,i.content_version_id,i.content_sha256,
 i.source_policy_version_id,i.expected_hypothesis_keys
 FROM sre_live_runs r JOIN sre_live_items i
   ON i.project_id=r.project_id AND i.run_id=r.run_id
 WHERE r.project_id=%s AND r.run_id=%s::uuid AND i.id=%s"""
VERIFIED = """SELECT category_key::text, human_decision, review_id::text,
 original_model_decision, receipt_id::text, verified_chunk_sha256
 FROM sre_m36_live_reviewed_pairs
 WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
 ORDER BY category_key"""
PAIR_COUNT = """SELECT count(*) FROM sre_live_expected_pairs
 WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s"""
PENDING = """SELECT count(*) FROM sre_live_opus_dispatch_intents d
 LEFT JOIN sre_live_opus_receipts t
   ON t.project_id=d.project_id AND t.intent_id=d.intent_id
 WHERE d.project_id=%s AND d.run_id=%s::uuid AND t.receipt_id IS NULL"""
READ_READY = """SELECT event_id::text,event_sha256,payload,state
 FROM sre_live_result_outbox
 WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s AND kind='ready'"""
INSERT_READY = """INSERT INTO sre_live_result_outbox
 (event_id,project_id,run_id,item_id,content_version_id,kind,
  payload,event_sha256,actor_id)
 VALUES (%s::uuid,%s,%s::uuid,%s,%s,'ready',%s::jsonb,%s,%s)
 ON CONFLICT DO NOTHING RETURNING event_id::text"""
INTENT_INSERT = """INSERT INTO sre_live_result_invalidation_intents
 (project_id,ready_event_id,reason_code) VALUES (%s,%s::uuid,%s)
 ON CONFLICT DO NOTHING RETURNING ready_event_id::text"""
INTENT_READ = """SELECT reason_code FROM sre_live_result_invalidation_intents
 WHERE project_id=%s AND ready_event_id=%s::uuid"""
INTENTS = """SELECT i.ready_event_id::text,i.reason_code
 FROM sre_live_result_invalidation_intents i
 LEFT JOIN sre_live_result_outbox invalidated
   ON invalidated.project_id=i.project_id
  AND invalidated.prior_ready_event_id=i.ready_event_id
 WHERE i.project_id=%s AND invalidated.event_id IS NULL
 ORDER BY i.created_at,i.ready_event_id LIMIT %s FOR UPDATE OF i SKIP LOCKED"""
READY_FOR_INVALIDATION = """SELECT event_id::text,run_id::text,item_id,
 content_version_id,payload,event_sha256 FROM sre_live_result_outbox
 WHERE project_id=%s AND event_id=%s::uuid AND kind='ready' FOR UPDATE"""
INSERT_INVALID = """INSERT INTO sre_live_result_outbox
 (event_id,project_id,run_id,item_id,content_version_id,kind,
  prior_ready_event_id,payload,event_sha256,actor_id)
 VALUES (%s::uuid,%s,%s::uuid,%s,%s,'invalidated',%s::uuid,
         %s::jsonb,%s,%s) ON CONFLICT DO NOTHING RETURNING event_id::text"""
SELECT_CLAIM = """SELECT event_id::text,kind,payload,event_sha256,
 run_id::text,item_id,content_version_id,lease_generation,attempt_count
 FROM sre_live_result_outbox e
 WHERE project_id=%s
   AND (state='pending' OR (state='leased' AND lease_until<clock_timestamp()))
   AND (kind='invalidated' OR NOT EXISTS (
     SELECT 1 FROM sre_live_result_invalidation_intents i
     WHERE i.project_id=e.project_id AND i.ready_event_id=e.event_id))
 ORDER BY CASE kind WHEN 'invalidated' THEN 0 ELSE 1 END,
          created_at,event_id
 LIMIT 1 FOR UPDATE SKIP LOCKED"""
LEASE = """UPDATE sre_live_result_outbox SET
 state='leased',lease_token=%s::uuid,lease_generation=lease_generation+1,
 lease_until=clock_timestamp()+interval '60 seconds',
 attempt_count=attempt_count+1
 WHERE project_id=%s AND event_id=%s::uuid
 RETURNING lease_generation,lease_until"""
ACK = """UPDATE sre_live_result_outbox SET state='acked',
 lease_token=NULL,lease_until=NULL,acked_at=clock_timestamp()
 WHERE project_id=%s AND event_id=%s::uuid AND state='leased'
   AND lease_token=%s::uuid AND lease_generation=%s
   AND event_sha256=%s AND lease_until>=clock_timestamp()
   AND (kind='invalidated' OR (
     NOT EXISTS(
       SELECT 1 FROM sre_live_result_invalidation_intents intent
       WHERE intent.project_id=sre_live_result_outbox.project_id
         AND intent.ready_event_id=sre_live_result_outbox.event_id
     )
     AND EXISTS(
       SELECT 1 FROM sre_live_items item
       JOIN sre_live_runs run
         ON run.project_id=item.project_id AND run.run_id=item.run_id
       JOIN sre_readable_content_versions content
         ON content.project_id=item.project_id
        AND content.id=item.content_version_id
       WHERE item.project_id=sre_live_result_outbox.project_id
         AND item.run_id=sre_live_result_outbox.run_id
         AND item.id=sre_live_result_outbox.item_id
         AND item.state='completed' AND run.state='completed'
         AND run.execution_mode='live' AND run.cancel_requested=FALSE
     )
   ))
 RETURNING event_id::text"""
READY_CURRENT = """SELECT 1 FROM sre_live_result_outbox e
 JOIN sre_live_runs r ON r.project_id=e.project_id AND r.run_id=e.run_id
 JOIN sre_live_items i
   ON i.project_id=e.project_id AND i.run_id=e.run_id AND i.id=e.item_id
 JOIN sre_readable_content_versions c
   ON c.project_id=i.project_id AND c.id=i.content_version_id
 WHERE e.project_id=%s AND e.event_id=%s::uuid AND e.kind='ready'
   AND r.execution_mode='live' AND r.state='completed'
   AND r.cancel_requested=FALSE AND i.state='completed'
   AND NOT EXISTS (
     SELECT 1 FROM sre_live_result_invalidation_intents x
     WHERE x.project_id=e.project_id AND x.ready_event_id=e.event_id)"""


def _clock(conn):
    row = conn.execute("SELECT clock_timestamp()").fetchone()
    if row is None or not isinstance(row[0], datetime) or row[0].tzinfo is None:
        reject("clock", "database UTC clock unavailable", "invalid_transition")
    return row[0].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _lock(conn, pid):
    if conn.execute(WORKSPACE, (pid,)).fetchone() is None:
        reject("project_id", "SRE workspace unavailable", "rights_denied")


def _source_ready(conn, pid, rid, iid):
    row = conn.execute(RUN_ITEM, (pid, rid, iid)).fetchone()
    if row is None:
        reject("item_id", "missing or cross-project LIVE item", "rights_denied")
    (mode,state,cancel,pub,taxid,taxsha,expected,finished,blocked,
     item_state,video,cid,content_sha,policy,leaves) = row
    if (mode != "live" or state != "completed" or cancel is not False
        or expected != finished or blocked != 0 or item_state != "completed"):
        reject("run", "only fully verified uncancelled LIVE completion may publish",
               "incomplete_run")
    if (type(leaves) is not list or not leaves
            or leaves != sorted(set(leaves))):
        reject("leaves", "not a complete canonical C9 key list",
               "incomplete_coverage")
    read_authorized_taxonomy(conn, pid, pub)
    gate = read_live_content_gate(
        conn,pid,cid,phase="before_publish",
        pins={"video_id":video,"content_version_id":cid,"content_sha256":content_sha})
    if (gate["content"]["source_policy_version_id"] != policy
            or gate["content_sha256"] != content_sha):
        reject("content", "D1 source/content or policy drift", "rights_denied")
    if conn.execute(PENDING,(pid,rid)).fetchone() != (0,):
        reject("receipts", "possible UNKNOWN paid outcome", "incomplete_coverage")
    pairs=conn.execute(VERIFIED,(pid,rid,iid)).fetchall()
    n=conn.execute(PAIR_COUNT,(pid,rid,iid)).fetchone()
    if (n is None or n[0]!=len(leaves) or len(pairs)!=len(leaves)
            or [x[0] for x in pairs]!=leaves):
        reject("pairs", "M30 receipt + latest M32 human review missing",
               "incomplete_coverage")
    decisions={"match":[],"no_match":[],"uncertain":[]}
    for key,human,review,original,receipt,evidence in pairs:
        canonical_uuid(key,"category_key")
        canonical_uuid(review,"review_id")
        canonical_uuid(receipt,"receipt_id")
        sha256_digest(evidence,"verified_chunk_sha256")
        if human not in decisions or original not in decisions:
            reject("review", "unrecognized verified human decision",
                   "invalid_evidence")
        decisions[human].append(key)
    return {"publication_id":pub,"taxonomy_version_id":taxid,
            "taxonomy_sha256":taxsha,"video_id":video,"content_version_id":cid,
            "content_sha256":content_sha,"source_policy_version_id":policy,
            "leaves":leaves,"decisions":decisions}


def publish_live_sre_result(conn,project,run_id,item_id,*,actor_id,actor_authorizer):
    """Exactly one immutable ready event per completed LIVE item (video).

    Never re-publishes an already invalidated content version; run a new
    reviewed LIVE classification for a later authorized revision instead.
    """
    pid=project_id(project);rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"publish_sre_live_result")
    _committable(conn)
    with conn.transaction():
        _lock(conn,pid)
        src=_source_ready(conn,pid,rid,iid)
        prior=conn.execute(READ_READY,(pid,rid,iid)).fetchone()
        if prior is not None:
            evt=validate_live_result_event(prior[2])
            if evt["event_sha256"]!=prior[1]:
                reject("event","saved outbox checksum mismatch","snapshot_mismatch")
            invalid=conn.execute(
                """SELECT 1 FROM sre_live_result_invalidation_intents
                   WHERE project_id=%s AND ready_event_id=%s::uuid""",
                (pid,prior[0])).fetchone()
            if invalid is not None:
                reject("event","prior publication was invalidated; new LIVE run required",
                       "rights_denied")
            return {"schema_version":VERSION,"event_id":prior[0],
                    "replayed":True,"state":prior[3],"paid_calls":0}
        when=_clock(conn)
        event_id=str(uuid4())
        ev={
            "schema_version":LIVE_RESULT_VERSION,"event_id":event_id,
            "project_id":pid,"run_id":rid,"publication_id":src["publication_id"],
            "sre_taxonomy_version_id":src["taxonomy_version_id"],
            "taxonomy_sha256":src["taxonomy_sha256"],"video_id":src["video_id"],
            "content_version_id":src["content_version_id"],
            "content_sha256":src["content_sha256"],
            "source_policy_version_id":src["source_policy_version_id"],
            "execution_mode":"live","run_status":"completed",
            "expected_hypothesis_keys":src["leaves"],
            "decisions":src["decisions"],
            "results_sha256":fingerprint(src["decisions"]),
            "rights_verified_at":when,"occurred_at":when,
        }
        ev["event_sha256"]=fingerprint(ev)
        validate_live_result_event(ev)
        import json
        inserted=conn.execute(INSERT_READY,(
            event_id,pid,rid,iid,src["content_version_id"],
            json.dumps(ev,ensure_ascii=False,sort_keys=True),ev["event_sha256"],actor,
        )).fetchone()
        if inserted!=(event_id,):
            reject("event","concurrent mismatched ready event", "idempotency_conflict")
    return {"schema_version":VERSION,"event_id":event_id,"replayed":False,
            "state":"pending","paid_calls":0}


def request_sre_result_invalidation(
    conn,project,ready_event_id,reason_code,*,actor_id,actor_authorizer,
):
    """Explicit operator/rights callback without granting arbitrary event edits.

    M34 source tombstone and M32 human supersession already insert intents
    atomically by SQL trigger. This is for a D1-only rights loss or a trusted
    operator withdrawal where no SRE source tombstone is available.
    """
    from .ports.qdp_reference import REASONS
    from .v1_contracts import SREContractError
    pid=project_id(project);eid=canonical_uuid(ready_event_id,"ready_event_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"invalidate_sre_live_result")
    if type(reason_code) is not str or reason_code not in REASONS:
        reject("reason_code","unrecognized invalidation")
    _committable(conn)
    with conn.transaction():
        _lock(conn,pid)
        old=conn.execute(READY_FOR_INVALIDATION,(pid,eid)).fetchone()
        if old is None:
            reject("event_id","only project-owned LIVE ready event can be invalidated",
                   "rights_denied")
        if reason_code=="rights_lost":
            # A caller cannot claim rights vanished and force an unrelated
            # item's status to be invalidated without an actual DB denial.
            src=old[4]
            try:
                read_live_content_gate(
                    conn,pid,old[3],phase="before_publish",
                    pins={"video_id":src["video_id"],
                          "content_version_id":old[3],
                          "content_sha256":src["content_sha256"]},
                )
            except SREContractError as err:
                if err.code not in ("rights_denied","rights_expired"):
                    raise
            else:
                reject("rights_lost","current D1 still permits this source",
                       "rights_denied")
        elif reason_code=="source_revoked":
            if conn.execute(
                """SELECT 1 FROM sre_live_source_tombstones
                   WHERE project_id=%s AND content_version_id=%s""",
                (pid,old[3])).fetchone() is None:
                reject("source_revoked","no project source tombstone",
                       "rights_denied")
        elif reason_code=="review_superseded":
            if conn.execute(
                """SELECT 1 FROM sre_live_human_reviews
                   WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
                     AND revision>1 LIMIT 1""",
                (pid,old[1],old[2])).fetchone() is None:
                reject("review_superseded","no reviewed correction exists",
                       "invalid_evidence")
        inserted=conn.execute(INTENT_INSERT,(pid,eid,reason_code)).fetchone()
        current=conn.execute(INTENT_READ,(pid,eid)).fetchone()
        if current is None:
            reject("invalidation","durable invalidation was not saved",
                   "invalid_transition")
        return {"schema_version":VERSION,"ready_event_id":eid,
                "reason_code":current[0],"intent_created":inserted is not None,
                "ready_delivery_suppressed":True,"paid_calls":0}


def materialize_sre_invalidation_intents(
    conn,project,*,actor_id,actor_authorizer,limit=50,
):
    """Convert durable M34/M32 invalidation intents to signed event payloads.

    Does not delete immutable ready events or require revoked content rights.
    No QDP API call and no automatic scheduler; trusted host invokes it.
    """
    pid=project_id(project);actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"materialize_sre_invalidation")
    count=positive(limit,"limit",maximum=100)
    _committable(conn)
    events=[]
    with conn.transaction():
        _lock(conn,pid)
        rows=conn.execute(INTENTS,(pid,count)).fetchall()
        import json
        for old_id,reason in rows:
            old=conn.execute(READY_FOR_INVALIDATION,(pid,old_id)).fetchone()
            if old is None:
                reject("ready_event", "original outbox event missing",
                       "invalid_evidence")
            if old[0]!=old_id or old[4]["event_sha256"]!=old[5]:
                reject("ready_event","source event checksum mismatch",
                       "snapshot_mismatch")
            created=make_invalidation(
                project=pid, prior_ready=old[4], reason=reason,
                event_id=str(uuid4()), occurred_at=_clock(conn),
            )
            saved=conn.execute(INSERT_INVALID,(
                created["event_id"],pid,old[1],old[2],old[3],old_id,
                json.dumps(created,ensure_ascii=False,sort_keys=True),
                created["event_sha256"],actor,
            )).fetchone()
            if saved is None:
                reject("invalidation","already claimed/changed concurrently",
                       "idempotency_conflict")
            events.append(saved[0])
    return {"schema_version":VERSION,"materialized":len(events),
            "event_ids":events,"paid_calls":0}


def claim_sre_result_event(conn,project,*,actor_id,actor_authorizer):
    """60s lease; ready events recheck current rights and full results.

    This returns an event *for downstream delivery*; downstream MUST still
    recheck rights, as external D1 policy can race network transport.
    """
    pid=project_id(project);actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"deliver_sre_result_event")
    _committable(conn)
    with conn.transaction():
        _lock(conn,pid)
        row=conn.execute(SELECT_CLAIM,(pid,)).fetchone()
        if row is None:
            return None
        (eid,kind,payload,digest,rid,iid,cid,generation,attempt)=row
        if kind=="ready":
            validate_live_result_event(payload)
            if conn.execute(READY_CURRENT,(pid,eid)).fetchone() is None:
                reject("event","revoked or stale LIVE publication",
                       "rights_denied")
            src=_source_ready(conn,pid,rid,iid)
            if (src["content_version_id"]!=cid
                or src["decisions"]!=payload["decisions"]
                or src["leaves"]!=payload["expected_hypothesis_keys"]):
                reject("event","source revision or human review superseded",
                       "snapshot_mismatch")
        elif kind=="invalidated":
            validate_invalidation_event(payload)
        else:
            reject("kind","unsupported event type")
        if payload["event_sha256"]!=digest or payload["event_id"]!=eid:
            reject("event","saved envelope SHA differs","snapshot_mismatch")
        token=str(uuid4())
        leased=conn.execute(LEASE,(token,pid,eid)).fetchone()
        if leased is None or leased[0]!=generation+1:
            reject("lease","lost event lease","invalid_transition")
    return {"schema_version":VERSION,"event":payload,
            "lease_token":token,"lease_generation":leased[0],
            "lease_until":leased[1].isoformat(),"attempt":attempt+1,
            "delivery_confirmation_required":True}


def acknowledge_sre_result_event(
    conn,project,event_id,lease_token,lease_generation,event_sha256,*,
    actor_id,actor_authorizer,
):
    """Compare-and-swap ACK; never claim client received without its ACK."""
    pid=project_id(project);eid=canonical_uuid(event_id,"event_id")
    token=canonical_uuid(lease_token,"lease_token")
    gen=positive(lease_generation,"lease_generation")
    digest=sha256_digest(event_sha256,"event_sha256")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"ack_sre_result_event")
    _committable(conn)
    with conn.transaction():
        _lock(conn,pid)
        result=conn.execute(ACK,(pid,eid,token,gen,digest)).fetchone()
        if result is None:
            reject("lease","stale/foreign/expired ACK", "invalid_transition")
    return {"schema_version":VERSION,"event_id":eid,"acknowledged":True}


__all__=("VERSION","publish_live_sre_result",
         "request_sre_result_invalidation",
         "materialize_sre_invalidation_intents",
         "claim_sre_result_event","acknowledge_sre_result_event")
