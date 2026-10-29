"""Real SRE M11/M30/M32 -> modern QDP Qualified Set source projection.

READ ONLY. No fabricated legacy sre_classification_results/BIGINT IDs,
provider replies, approval labels or SRE completion events. Every input
comes from a completed LIVE SRE run, M36 signed source outbox delivered to
the QDP-owned durable inbox, M30 authenticated pair and latest M32 review.
"""
from __future__ import annotations
from uuid import UUID
from intelligence.qualified_data.contracts import QDPError,sha256
from intelligence.qualified_data.qualification import resolve_qualified_set
from intelligence.qualified_data.coverage import parse_expected_manifest
from intelligence.relevance.ports.outbound import validate_live_result_event
from intelligence.relevance.v1_contracts import fingerprint

MAX_ITEMS=10_000


def _deny(code):
    raise QDPError(code,"Modern SRE LIVE source failed authoritative verification",409)


def _uuid(value):
    if type(value) is not str:
        _deny("sre_live_identity_invalid")
    try:
        parsed=str(UUID(value))
    except (ValueError,TypeError,AttributeError):
        _deny("sre_live_identity_invalid")
    if parsed != value:
        _deny("sre_live_identity_invalid")
    return parsed


def read_sre_live_snapshot(conn,pid,rid,*,only_video_id=None):
    """Requires a caller-owned consistent transaction and same DB as SRE."""
    run=conn.execute(
        """SELECT r.run_id::text AS id,r.project_id,r.taxonomy_version_id,
                  r.publication_id::text AS publication_id,
                  r.taxonomy_sha256,r.manifest,r.manifest_sha256,r.expected_items,
                  r.finished_items,r.blocked_items,r.state,r.cancel_requested,
                  r.execution_mode,t.status AS taxonomy_status
           FROM sre_live_runs r
           JOIN sre_taxonomy_versions t
             ON t.id=r.taxonomy_version_id AND t.project_id=r.project_id
           WHERE r.project_id=%s AND r.run_id=%s::uuid
           FOR SHARE OF r""",(pid,rid),
    ).fetchone()
    if run is None:
        _deny("sre_live_not_found")
    if (run["state"]!="completed" or run["execution_mode"]!="live"
        or run["cancel_requested"] is not False
        or run["taxonomy_status"]!="approved"
        or type(run["expected_items"]) is not int
        or not 1<=run["expected_items"]<=MAX_ITEMS
        or run["expected_items"]!=run["finished_items"]
        or run["blocked_items"]!=0
        or fingerprint(run["manifest"])!=run["manifest_sha256"]):
        _deny("sre_live_unverified_completion")
    raw_items=conn.execute(
        """SELECT id,video_id,content_version_id,content_sha256,
                  source_id,source_policy_version_id,discovery_id,
                  metadata_observation_id,expected_hypothesis_keys,state
           FROM sre_live_items
           WHERE project_id=%s AND run_id=%s::uuid
           ORDER BY video_id""",(pid,rid),
    ).fetchall()
    if len(raw_items)!=run["expected_items"]:
        _deny("sre_live_manifest_item_count")
    videos=set()
    items=[]
    for item in raw_items:
        if (item["state"]!="completed" or
            type(item["video_id"]) is not int or item["video_id"]<=0
            or item["video_id"] in videos or
            type(item["expected_hypothesis_keys"]) is not list
            or item["expected_hypothesis_keys"]!=sorted(set(item["expected_hypothesis_keys"]))
            or not 1<=len(item["expected_hypothesis_keys"])<=48):
            _deny("sre_live_incomplete_item")
        videos.add(item["video_id"])
        items.append({
            "video_id":item["video_id"],
            "content_version_id":item["content_version_id"],
            "content_sha256":item["content_sha256"],
            "hypothesis_keys":item["expected_hypothesis_keys"],
        })
    source={
        "id":rid,"project_id":pid,
        "taxonomy_version_id":run["taxonomy_version_id"],
        "taxonomy_sha256":run["taxonomy_sha256"],
        "sre_origin":"sre_live_m36",
        "dataset_snapshot":{
            "mode":"sre_live_m36","project_id":pid,"run_id":rid,
            "taxonomy_version_id":run["taxonomy_version_id"],
            "taxonomy_sha256":run["taxonomy_sha256"],
            "items":items,
        },
    }
    expected=parse_expected_manifest(source)
    rows=[]
    for item in raw_items:
        vid=item["video_id"]
        if only_video_id is not None and vid!=only_video_id:
            continue
        # An actual QDP-received source event, not merely the presence of
        # SRE M36 rows, is required before creating a qualified source.
        received=conn.execute(
            """SELECT q.event_id::text,event.payload,event.event_sha256,
                      event.item_id,event.content_version_id
               FROM qdp_sre_live_events q
               JOIN sre_live_result_outbox event
                 ON event.event_id=q.event_id AND event.project_id=q.project_id
               WHERE q.project_id=%s AND q.sre_run_id=%s::uuid
                 AND q.video_id=%s AND q.kind='ready'
                 AND event.kind='ready' AND event.state='acked'
                 AND NOT EXISTS(
                   SELECT 1 FROM qdp_sre_live_tombstones tomb
                   WHERE tomb.project_id=q.project_id
                     AND tomb.prior_ready_event_id=q.event_id)
                 AND NOT EXISTS(
                   SELECT 1 FROM sre_live_result_invalidation_intents intent
                   WHERE intent.project_id=q.project_id
                     AND intent.ready_event_id=q.event_id)
               """,(pid,rid,vid),
        ).fetchall()
        if len(received)!=1:
            _deny("sre_live_qdp_receipt_or_rights_missing")
        eid,payload,digest,iid,content=received[0]
        if (iid!=item["id"] or content!=item["content_version_id"]
            or payload.get("event_id")!=eid
            or payload.get("event_sha256")!=digest
            or payload.get("project_id")!=pid
            or payload.get("run_id")!=rid
            or payload.get("publication_id")!=run["publication_id"]
            or payload.get("sre_taxonomy_version_id")!=run["taxonomy_version_id"]
            or payload.get("taxonomy_sha256")!=run["taxonomy_sha256"]
            or payload.get("video_id")!=vid
            or payload.get("content_sha256")!=item["content_sha256"]
            or payload.get("source_policy_version_id")!=item["source_policy_version_id"]
            or payload.get("expected_hypothesis_keys")!=item["expected_hypothesis_keys"]):
            _deny("sre_live_qdp_source_mismatch")
        validate_live_result_event(payload)
        pairs=conn.execute(
            """SELECT p.category_key::text AS category_key,
                      p.original_model_decision,p.human_decision,
                      p.review_id::text AS review_id,p.receipt_id::text AS receipt_id,
                      p.verified_chunk_sha256,
                      verified.result_id::text AS result_id,
                      review.reviewed_at
               FROM sre_m36_live_only_export p
               JOIN sre_live_verified_pair_results verified
                 ON verified.project_id=p.project_id AND verified.run_id=p.run_id
                  AND verified.item_id=p.item_id AND verified.category_key=p.category_key
               JOIN sre_live_human_reviews review
                 ON review.review_id=p.review_id
                  AND review.project_id=p.project_id
                  AND review.run_id=p.run_id AND review.item_id=p.item_id
                  AND review.category_key=p.category_key
                  AND review.base_result_id=verified.result_id
               WHERE p.project_id=%s AND p.event_id=%s::uuid
               ORDER BY p.category_key""",(pid,eid),
        ).fetchall()
        if (len(pairs)!=len(item["expected_hypothesis_keys"])
            or [p["category_key"] for p in pairs]!=item["expected_hypothesis_keys"]):
            _deny("sre_live_reviewed_pair_coverage")
        decisions={"match":[],"no_match":[],"uncertain":[]}
        for p in pairs:
            key=p["category_key"]
            if (p["original_model_decision"] not in decisions or
                p["human_decision"] not in decisions):
                _deny("sre_live_invalid_decision")
            decisions[p["human_decision"]].append(key)
            rows.append({
                "source_schema":"sre_live_m36",
                "video_id":vid,
                "result_id":_uuid(p["result_id"]),
                "category_key":_uuid(key),
                "model_decision":p["original_model_decision"],
                "effective_decision":p["human_decision"],
                "content_readable":True,
                "human_label_id":_uuid(p["review_id"]),
                "human_label_decision":p["human_decision"],
                "review_created_at":p["reviewed_at"].isoformat(),
                "content_version_id":item["content_version_id"],
                "result_content_sha256":item["content_sha256"],
                "source_id":item["source_id"],
                "source_policy_version_id":item["source_policy_version_id"],
                "sre_metadata_observation_id":item["metadata_observation_id"],
                "content_provenance":{"discovery_id":item["discovery_id"]},
                "evidence":{
                    "m30_verified_chunk_sha256":p["verified_chunk_sha256"],
                    "authenticated_receipt_id":p["receipt_id"],
                },
            })
        if decisions!=payload["decisions"]:
            _deny("sre_live_human_review_superseded")
    if only_video_id is not None and not rows:
        _deny("sre_live_video_not_found")
    selection=resolve_qualified_set(rows,strict=True)
    source["manifest_digest"]=expected["digest"]
    return source,selection


def list_eligible_modern_sre_runs(conn,pid,*,limit=25,cursor=None):
    """Only completed modern LIVE runs whose every source was actually
    delivered to QDP and remains readable. Stable paginated project scope.
    """
    if type(limit) is not int or not 1<=limit<=100:
        _deny("sre_live_page_size")
    anchor=None
    if cursor is not None:
        cid=_uuid(cursor)
        anchor=conn.execute(
            """SELECT created_at,run_id::text AS id
               FROM sre_live_runs WHERE project_id=%s AND run_id=%s::uuid""",
            (pid,cid),
        ).fetchone()
        if anchor is None:
            _deny("sre_live_cursor_not_found")
    at=anchor["created_at"] if anchor else None
    cursor_id=anchor["id"] if anchor else None
    rows=conn.execute(
        """SELECT r.run_id AS id,r.taxonomy_version_id,
                  r.created_at,r.finished_at
           FROM sre_live_runs r
           JOIN sre_taxonomy_versions taxonomy
             ON taxonomy.id=r.taxonomy_version_id
             AND taxonomy.project_id=r.project_id
           WHERE r.project_id=%s AND r.execution_mode='live'
             AND r.state='completed' AND r.cancel_requested=FALSE
             AND taxonomy.status='approved'
             AND r.finished_items=r.expected_items AND r.blocked_items=0
             AND NOT EXISTS (
               SELECT 1 FROM sre_live_items item
               WHERE item.project_id=r.project_id AND item.run_id=r.run_id
                 AND (item.state<>'completed' OR NOT EXISTS (
                   SELECT 1 FROM qdp_sre_live_events q
                   JOIN sre_live_result_outbox origin
                     ON origin.project_id=q.project_id
                     AND origin.event_id=q.event_id
                   WHERE q.project_id=r.project_id
                     AND q.sre_run_id=r.run_id AND q.video_id=item.video_id
                     AND q.kind='ready' AND origin.kind='ready'
                     AND origin.state='acked' AND origin.item_id=item.id
                     AND NOT EXISTS(
                       SELECT 1 FROM qdp_sre_live_tombstones tomb
                       WHERE tomb.project_id=q.project_id
                         AND tomb.prior_ready_event_id=q.event_id)
                     AND EXISTS(
                       SELECT 1 FROM sre_m36_live_only_export live
                       WHERE live.project_id=q.project_id
                         AND live.event_id=q.event_id)
                 ))
             )
             AND (%s::timestamptz IS NULL OR
               (r.created_at,r.run_id)<(%s::timestamptz,%s::uuid))
           ORDER BY r.created_at DESC,r.run_id DESC LIMIT %s""",
        (pid,at,at,cursor_id,limit+1),
    ).fetchall()
    page=rows[:limit]
    return {"runs":[{
        "id":r["id"],"taxonomy_version_id":r["taxonomy_version_id"],
        "created_at":r["created_at"],"finished_at":r["finished_at"],
        "source":"sre_live_m36",
    } for r in page],
    "next_cursor":str(page[-1]["id"]) if len(rows)>limit else None}
