"""M38 SRE-owned, read-only LIVE API projections; no HTTP framework or peer.

Call in one caller-owned REPEATABLE READ READ ONLY transaction, after the
trusted host has authenticated the request and authorized the project.
These queries return no prompt, raw D1 transcript, provider response, API
secret, reviewer free-text, consent token, or source rights after revocation.
"""
from __future__ import annotations

from .ports.approved_c9 import build_c9_bundle
from .taxonomy_adapter import read_authorized_taxonomy
from .live_content_gate import read_live_content_gate
from .live_verified_coverage import _verify_current_d1
from .v1_contracts import canonical_uuid, positive, project_id, reject

VERSION="sre-read-api/v1"
MAX_PAGE=100
SQL_WORKSPACE="SELECT 1 FROM sre_workspaces WHERE project_id=%s"
SQL_RUNS="""SELECT r.run_id::text,r.state,r.cancel_requested,
 r.expected_items,r.finished_items,r.blocked_items,r.created_at,r.finished_at
 FROM sre_live_runs r
 WHERE r.project_id=%s AND r.execution_mode='live'
 AND (%s::text IS NULL OR r.run_id::text>%s::text)
 ORDER BY r.run_id::text LIMIT %s"""
SQL_RUN="""SELECT run_id::text,publication_id::text,taxonomy_version_id,
 taxonomy_sha256,state,cancel_requested,expected_items,finished_items,
 blocked_items,created_at,finished_at
 FROM sre_live_runs WHERE project_id=%s AND run_id=%s::uuid
 AND execution_mode='live'"""
SQL_RESULTS="""SELECT v.item_id,v.category_key::text,v.decision,
 v.result_id::text,v.receipt_id::text,
 h.human_decision,h.review_id::text,h.revision,
 i.video_id,i.content_version_id,i.content_sha256
 FROM sre_live_verified_pair_results v
 JOIN sre_live_runs r
  ON r.project_id=v.project_id AND r.run_id=v.run_id
  AND r.execution_mode='live' AND r.cancel_requested=FALSE
 JOIN sre_live_items i
  ON i.project_id=v.project_id AND i.run_id=v.run_id AND i.id=v.item_id
 JOIN sre_live_expected_pairs p
  ON p.project_id=v.project_id AND p.run_id=v.run_id
  AND p.item_id=v.item_id AND p.category_key=v.category_key AND p.state=v.decision
 JOIN sre_live_opus_receipts t
  ON t.project_id=v.project_id AND t.receipt_id=v.receipt_id
  AND t.generation_authenticated=TRUE
  AND t.evidence_status='validated_untrusted_output'
  AND t.evidence_sha256=v.verified_chunk_sha256
 LEFT JOIN LATERAL(
  SELECT rev.human_decision,rev.review_id,rev.revision
  FROM sre_live_human_reviews rev
  WHERE rev.project_id=v.project_id AND rev.run_id=v.run_id
    AND rev.item_id=v.item_id AND rev.category_key=v.category_key
    AND rev.base_result_id=v.result_id
  ORDER BY rev.revision DESC LIMIT 1
 ) h ON TRUE
 WHERE v.project_id=%s AND v.run_id=%s::uuid
 AND (%s::bigint IS NULL
      OR (v.item_id,v.category_key::text)>(%s::bigint,%s::text))
 ORDER BY v.item_id,v.category_key::text LIMIT %s"""
SQL_REVIEW_QUEUE=SQL_RESULTS.replace(
    " WHERE v.project_id=%s AND v.run_id=%s::uuid",
    " WHERE h.review_id IS NULL AND v.project_id=%s AND v.run_id=%s::uuid",
)
SQL_REVIEWS="""SELECT h.review_id::text,h.item_id,h.category_key::text,
 h.revision,h.human_decision,h.original_model_decision,h.review_action,
 h.reason_code,h.reviewed_at,i.video_id,i.content_version_id,i.content_sha256
 FROM sre_live_human_reviews h
 JOIN sre_live_runs r
   ON r.project_id=h.project_id AND r.run_id=h.run_id
  AND r.execution_mode='live' AND r.cancel_requested=FALSE
 JOIN sre_live_verified_pair_results v
   ON v.project_id=h.project_id AND v.run_id=h.run_id
  AND v.item_id=h.item_id AND v.category_key=h.category_key
  AND v.result_id=h.base_result_id
 JOIN sre_live_items i
   ON i.project_id=h.project_id AND i.run_id=h.run_id AND i.id=h.item_id
 WHERE h.project_id=%s AND h.run_id=%s::uuid
 AND (%s::text IS NULL OR h.review_id::text>%s::text)
 ORDER BY h.review_id::text LIMIT %s"""
SQL_QUOTES="""SELECT q.quote_id,q.item_id,q.quote_sha256,q.catalog_sha256,
 q.max_usd,q.issued_at,q.expires_at,i.video_id,i.content_version_id,
 i.content_sha256
 FROM sre_live_opus_quotes q
 JOIN sre_live_runs r
  ON r.project_id=q.project_id AND r.run_id=q.run_id
  AND r.execution_mode='live' AND r.cancel_requested=FALSE
  AND r.state IN ('planned','pending_consent','queued','running','paused_budget')
 JOIN sre_live_items i
  ON i.project_id=q.project_id AND i.run_id=q.run_id AND i.id=q.item_id
 WHERE q.project_id=%s AND q.run_id=%s::uuid
 AND q.quote_id>%s AND q.expires_at>clock_timestamp()
 AND NOT EXISTS(
   SELECT 1 FROM sre_live_opus_consents consent
   WHERE consent.project_id=q.project_id AND consent.run_id=q.run_id
     AND consent.item_id=q.item_id AND consent.quote_sha256=q.quote_sha256
     AND consent.revoked_at IS NOT NULL
 )
 AND NOT EXISTS (
   SELECT 1 FROM sre_live_source_tombstones t
   WHERE t.project_id=i.project_id AND t.content_version_id=i.content_version_id
 )
 ORDER BY q.quote_id LIMIT %s"""
SQL_SCHEMA="""SELECT to_regclass('sre_live_runs') IS NOT NULL,
 to_regclass('sre_live_verified_pair_results') IS NOT NULL,
 to_regclass('sre_live_human_reviews') IS NOT NULL,
 to_regclass('sre_live_result_outbox') IS NOT NULL,
 to_regclass('sre_collector_inbox') IS NOT NULL"""


def _page(rows,limit,convert,cursor):
    exposed=[convert(row) for row in rows[:limit]]
    return {"schema_version":VERSION,"rows":exposed,
            "next_cursor":cursor(rows[limit-1]) if len(rows)>limit else None,
            "has_more":len(rows)>limit,"paid_calls":0}


def _stamp(dt):
    return dt.isoformat() if dt is not None else None


def _lim(n):
    return positive(n,"limit",maximum=MAX_PAGE)


def workspace_exists(conn,pid):
    if conn.execute(SQL_WORKSPACE,(project_id(pid),)).fetchone() is None:
        reject("project_id","SRE workspace unavailable","rights_denied")


def read_schema_health(conn,pid):
    workspace_exists(conn,pid)
    row=conn.execute(SQL_SCHEMA).fetchone()
    if row is None or len(row)!=5:
        reject("schema","SRE schema check unavailable","invalid_evidence")
    return {
        "schema_version":VERSION,"project_id":pid,
        "live_tables_present":all(x is True for x in row[:3]),
        "result_outbox_present":row[3] is True,
        "collector_inbox_present":row[4] is True,
        "status":"schema_available" if all(x is True for x in row) else "schema_incomplete",
        "live_execution_confirmed":False,"paid_dispatch_allowed":False,
        "actual_database_migration_hashes_verified":False,
    }


def list_live_runs(conn,pid,limit=50,after=None):
    _lim(limit)
    if after is not None:
        canonical_uuid(after,"after")
    rows=conn.execute(SQL_RUNS,(pid,after,after,limit+1)).fetchall()
    return _page(rows,limit,lambda x:{
        "run_id":x[0],"state":x[1],"cancel_requested":x[2],
        "expected_items":x[3],"finished_items":x[4],
        "blocked_items":x[5],"created_at":_stamp(x[6]),
        "finished_at":_stamp(x[7]),
    },lambda x:x[0])


def read_run_status(conn,pid,rid):
    rid=canonical_uuid(rid,"run_id")
    row=conn.execute(SQL_RUN,(pid,rid)).fetchone()
    if row is None:
        reject("run_id","missing or foreign LIVE run","rights_denied")
    return {
        "schema_version":VERSION,"run_id":row[0],
        "publication_id":row[1],"taxonomy_version_id":row[2],
        "taxonomy_sha256":row[3],"state":row[4],
        "cancel_requested":row[5],"expected_items":row[6],
        "finished_items":row[7],"blocked_items":row[8],
        "created_at":_stamp(row[9]),"finished_at":_stamp(row[10]),
        "paid_dispatch_allowed":False,
    }


def read_taxonomy_preview(conn,pid,publication,limit=50,after=None):
    _lim(limit)
    pub=canonical_uuid(publication,"publication_id")
    if after is not None: canonical_uuid(after,"after")
    approved=read_authorized_taxonomy(conn,pid,pub)
    bundle=build_c9_bundle(approved)
    if bundle["project_id"]!=pid or bundle["publication_id"]!=pub:
        reject("taxonomy","approved source publication mismatch","snapshot_mismatch")
    projections=sorted(bundle["node_projections"],key=lambda x:x["category_key"])
    eligible=[x for x in projections if after is None or x["category_key"]>after]
    response=_page(eligible[:limit+1],limit,lambda x:{
        "category_key":x["category_key"],"kind":x["kind"],
        "source_node_id":x["source_node_id"],
        "parent_category_key":x["parent_category_key"],
    },lambda x:x["category_key"])
    response.update({
        "publication_id":pub,
        "taxonomy_version_id":bundle["sre_taxonomy_version_id"],
        "taxonomy_sha256":bundle["taxonomy_sha256"],
        "approved_hypothesis_count":len(bundle["hypothesis_keys"]),
        "human_approved_c9":True,
    })
    return response


def read_content_preview(conn,pid,content_id):
    cid=positive(content_id,"content_version_id",maximum=9223372036854775807)
    gate=read_live_content_gate(conn,pid,cid,phase="planning")
    c=gate["content"]
    if c["project_id"]!=pid or c["content_version_id"]!=cid:
        reject("content_version_id","foreign source","rights_denied")
    data=c["data"]
    def short_text(key,max_len):
        t=data.get(key)
        return t[:max_len] if isinstance(t,str) else None
    return {
        "schema_version":VERSION,"content_version_id":cid,
        "video_id":c["video_id"],"source_id":c["source_id"],
        "source_policy_version_id":c["source_policy_version_id"],
        "content_sha256":c["content_sha256"],
        "completeness":c["completeness"],
        "title_preview":short_text("title",160),
        "description_preview":short_text("description",320),
        "rights_rechecked":True,"raw_transcript_exposed":False,
        "paid_dispatch_allowed":False,
    }


def _pair_cursor(after):
    if after is None: return (None,None)
    if type(after) is not str or "." not in after:
        reject("after","expected item-id.category-UUID cursor")
    a,b=after.split(".",1)
    if not a.isascii() or not a.isdecimal() or len(a)>19 or a.startswith("0"):
        reject("after","invalid item id")
    return (positive(int(a),"after_item",maximum=9223372036854775807),
            canonical_uuid(b,"after_category"))


def _all_pairs(conn,pid,rid,limit,after,*,only_unreviewed):
    _lim(limit)
    canonical_uuid(rid,"run_id")
    item,key=_pair_cursor(after)
    query=SQL_REVIEW_QUEUE if only_unreviewed else SQL_RESULTS
    rows=conn.execute(query,(pid,rid,item,item,key,limit+1)).fetchall()
    # Never remove revoked rows after LIMIT and turn that into a covert
    # "has_more" side channel; abort the whole page on rights denial.
    for row in rows:
        _verify_current_d1(conn,pid,row[8],row[9],row[10])
    return _page(rows,limit,_pair,lambda x:str(x[0])+"."+x[1])


def _pair(row):
    return {
        "item_id":row[0],"category_key":row[1],
        "original_model_decision":row[2],"result_id":row[3],
        "receipt_id":row[4],
        "effective_decision":row[5] if row[6] is not None else row[2],
        "human_reviewed":row[6] is not None,
        "review_id":row[6],"review_revision":row[7] or 0,
        "rights_rechecked":True,
    }


def read_verified_results(conn,pid,rid,limit=50,after=None):
    return _all_pairs(conn,pid,rid,limit,after,only_unreviewed=False)


def read_review_queue(conn,pid,rid,limit=50,after=None):
    return _all_pairs(conn,pid,rid,limit,after,only_unreviewed=True)


def read_review_history(conn,pid,rid,limit=50,after=None):
    _lim(limit);rid=canonical_uuid(rid,"run_id")
    if after is not None: canonical_uuid(after,"after")
    rows=conn.execute(SQL_REVIEWS,(pid,rid,after,after,limit+1)).fetchall()
    for x in rows:
        _verify_current_d1(conn,pid,x[9],x[10],x[11])
    return _page(rows,limit,lambda x:{
        "review_id":x[0],"item_id":x[1],"category_key":x[2],
        "revision":x[3],"human_decision":x[4],
        "original_model_decision":x[5],"action":x[6],
        "reason_code":x[7],"reviewed_at":_stamp(x[8]),
        "rights_rechecked":True,
    },lambda x:x[0])


def read_quote_preview(conn,pid,rid,limit=50,after=None):
    _lim(limit);rid=canonical_uuid(rid,"run_id")
    if after is not None:
        if type(after) is not str or not after.isascii() or not after.isdecimal() or len(after)>19:
            reject("after","expected positive quote id")
        after=positive(int(after),"after",maximum=9223372036854775807)
    run=read_run_status(conn,pid,rid)
    # Revoked/UNKNOWN/terminal runs cannot disclose financial quotes.
    if run["cancel_requested"] or run["state"] not in (
            "planned","pending_consent","queued","running","paused_budget"):
        reject("run_id","quote currently unavailable","rights_denied")
    read_authorized_taxonomy(conn,pid,run["publication_id"])
    rows=conn.execute(SQL_QUOTES,(pid,rid,after or 0,limit+1)).fetchall()
    for x in rows:
        _verify_current_d1(conn,pid,x[7],x[8],x[9])
    return _page(rows,limit,lambda x:{
        "quote_id":x[0],"item_id":x[1],
        "quote_sha256":x[2],"catalog_sha256":x[3],
        "min_total_usd":"0.000000000",
        "conditional_max_total_usd":format(x[4],".9f"),
        "issued_at":_stamp(x[5]),"expires_at":_stamp(x[6]),
        "not_spending_consent":True,"token_count_exact":False,
        "source_rights_rechecked":True,
    },lambda x:str(x[0]))


__all__=["VERSION","MAX_PAGE","workspace_exists","read_schema_health",
         "list_live_runs","read_run_status","read_taxonomy_preview",
         "read_content_preview","read_verified_results","read_review_queue",
         "read_review_history","read_quote_preview"]
