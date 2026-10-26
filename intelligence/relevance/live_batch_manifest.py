"""M13: deterministic, zero-cost LIVE batch sealing and exhaustive coverage.

This service is internal. It validates already-persisted M12 pinned inputs
without fetching a mutable taxonomy or remote provider, and creates immutable
batch membership and 1:1 expected video/hypothesis pairs in one transaction.
It NEVER dispatches, approves money, fabricates semantic labels, or promotes
offline_fixture results. The host must authenticate the project owner.
"""
from __future__ import annotations

import json
from uuid import UUID, uuid5

from .v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, reject, sha256_digest,
)

BATCH_VERSION = "sre-live-batch/v1"
MAX_BATCH_SIZE = 25
MAX_PAIRS = 48000
_UNRESOLVED = frozenset(("not_evaluated", "blocked_rights", "blocked_budget"))
_FINAL = frozenset(("match", "no_match", "uncertain"))
_STATES = _UNRESOLVED | _FINAL


def _run_and_items(conn, pid, rid, *, for_update=False):
    row = conn.execute(
        """SELECT state,manifest,manifest_sha256,expected_items,execution_mode
           FROM sre_live_runs
           WHERE project_id=%s AND run_id=%s::uuid"""
        + (" FOR UPDATE" if for_update else ""),
        (pid,rid),
    ).fetchone()
    if row is None or row[4] != "live":
        reject("run_id", "LIVE run missing in project", "rights_denied")
    state, plan, digest, expected, _ = row
    if (type(plan) is not dict or plan.get("schema_version") != "sre-live-plan/v1"
            or plan.get("project_id") != pid or
            plan.get("execution_mode") != "live" or
            fingerprint(plan) != digest):
        reject("run_id", "LIVE plan manifest changed", "snapshot_mismatch")
    items = conn.execute(
        """SELECT id,video_id,content_version_id,content_sha256,
                  expected_hypothesis_keys,contexts_sha256,manifest_sha256,state
           FROM sre_live_items WHERE project_id=%s AND run_id=%s::uuid
           ORDER BY video_id,id""",(pid,rid),
    ).fetchall()
    if len(items) != expected or len(plan.get("items",[])) != expected:
        reject("live_items", "run/video manifest coverage mismatch", "incomplete_coverage")
    expected_keys = plan.get("expected_hypothesis_keys")
    if (type(expected_keys) is not list or not 1<=len(expected_keys)<=48
            or expected_keys != sorted(set(expected_keys))):
        reject("expected_hypothesis_keys","invalid/duplicate C9 leaves",
               "incomplete_coverage")
    for k in expected_keys:
        canonical_uuid(k, "expected_hypothesis_keys[]")
    pinned = {item["video_id"]: item for item in plan["items"]
              if type(item) is dict and "video_id" in item}
    if len(pinned) != expected:
        reject("live_items","duplicate pinned video IDs","incomplete_coverage")
    result=[]
    for item_id,video,cid,sha,keys,ctx,digest_item,item_state in items:
        pinned_item=pinned.get(video)
        if (pinned_item is None or type(keys) is not list or
                keys != expected_keys or digest_item != digest or
                sha != pinned_item.get("content_sha256") or
                cid != pinned_item.get("content_version_id") or
                ctx != pinned_item.get("contexts_sha256") or
                ctx != plan.get("contexts_sha256")):
            reject("live_items","pinned LIVE item input differs from C9/D1 plan",
                   "snapshot_mismatch")
        positive(item_id,"item_id",maximum=9223372036854775807)
        positive(video,"video_id",maximum=9223372036854775807)
        sha256_digest(sha,"content_sha256")
        result.append({
            "item_id":item_id,"video_id":video,"content_version_id":cid,
            "content_sha256":sha,"contexts_sha256":ctx,
            "expected_hypothesis_keys":list(keys),"state":item_state,
        })
    return state,digest,result,expected_keys


def make_batch_manifest(pid,rid,run_sha,ordinal,items,keys):
    """Pure exact M13 batch hash: stable identity independent of item order."""
    project_id(pid)
    canonical_uuid(rid,"run_id")
    sha256_digest(run_sha,"run_manifest_sha256")
    positive(ordinal,"ordinal",zero=True,maximum=1000000)
    if (type(items) is not list or not 1<=len(items)<=MAX_BATCH_SIZE
            or type(keys) is not list or not 1<=len(keys)<=48):
        reject("batch","invalid item/leaf bounds")
    ordered=sorted(items,key=lambda i:(i["video_id"],i["item_id"]))
    if len({x["item_id"] for x in ordered}) != len(ordered):
        reject("batch.items","duplicate items")
    if keys != sorted(set(keys)):
        reject("batch.keys","keys not sorted/unique")
    for k in keys:
        canonical_uuid(k,"category_key")
    item_rows=[]
    pair_rows=[]
    for i in ordered:
        if (i["expected_hypothesis_keys"] != keys or
                type(i["item_id"]) is not int or i["item_id"]<=0):
            reject("batch.items","item's expected C9 leaves differ")
        item_rows.append({
            "item_id":i["item_id"],"video_id":i["video_id"],
            "content_version_id":i["content_version_id"],
            "content_sha256":i["content_sha256"],
            "contexts_sha256":i["contexts_sha256"],
        })
        for key in keys:
            pair_rows.append({
                "item_id":i["item_id"],"video_id":i["video_id"],
                "category_key":key, "state":"not_evaluated",
            })
    if len(pair_rows)!=len(ordered)*len(keys):
        reject("batch","incomplete matrix","incomplete_coverage")
    input_sha=fingerprint({
        "run_manifest_sha256":run_sha,
        "items":item_rows,
        "expected_hypothesis_keys":keys,
    })
    manifest={
        "schema_version":BATCH_VERSION,"execution_mode":"live",
        "project_id":pid,"run_id":rid,
        "run_manifest_sha256":run_sha,"ordinal":ordinal,
        "items":item_rows,"expected_hypothesis_keys":list(keys),
        "expected_pairs":pair_rows,
    }
    return {
        "batch_id":str(uuid5(UUID(rid),f"sre-m13/{ordinal}/{input_sha}")),
        "ordinal":ordinal,
        "input_sha256":input_sha,"manifest_sha256":fingerprint(manifest),
        "manifest":manifest,"items":item_rows,"pairs":pair_rows,
    }


def _derived_batches(pid,rid,run_sha,items,keys,batch_size):
    return [
        make_batch_manifest(pid,rid,run_sha,i//batch_size,
                            items[i:i+batch_size],keys)
        for i in range(0,len(items),batch_size)
    ]


def _stored_rows(conn,pid,rid):
    batches=conn.execute(
        """SELECT batch_id::text,ordinal,manifest,manifest_sha256,input_sha256
           FROM sre_live_batches WHERE project_id=%s AND run_id=%s::uuid
           ORDER BY ordinal""",(pid,rid),
    ).fetchall()
    members=conn.execute(
        """SELECT batch_id::text,item_id,ordinal
           FROM sre_live_batch_members WHERE project_id=%s AND run_id=%s::uuid
           ORDER BY batch_id,item_id,ordinal""",(pid,rid),
    ).fetchall()
    pairs=conn.execute(
        """SELECT batch_id::text,item_id,category_key::text,state
           FROM sre_live_expected_pairs WHERE project_id=%s AND run_id=%s::uuid
           ORDER BY batch_id,item_id,category_key""",(pid,rid),
    ).fetchall()
    return batches,members,pairs


def _assert_existing(expected,batches,members,pairs):
    expected_batches=[
        (b["batch_id"],b["ordinal"],b["manifest"],b["manifest_sha256"],
         b["input_sha256"]) for b in expected
    ]
    expected_members=sorted([
        (b["batch_id"],i["item_id"],ordinal)
        for b in expected for ordinal,i in enumerate(b["items"])
    ])
    expected_pairs=sorted([
        (b["batch_id"],p["item_id"],p["category_key"])
        for b in expected for p in b["pairs"]
    ])
    if batches != expected_batches or members != expected_members:
        reject("batch_manifest","persisted batches or membership differs",
               "snapshot_mismatch")
    if [(batch,item,key) for batch,item,key,status in pairs] != expected_pairs:
        reject("pair_manifest","missing, extra or duplicated expected C9 pair",
               "incomplete_coverage")
    if any(status not in _STATES for _,_,_,status in pairs):
        reject("pair_manifest","unrecognized classification state",
               "incomplete_coverage")
    return True


def materialize_live_batches(conn, project, run_id, *, batch_size=20):
    """Exactly once per run under a project-owned transactional row lock.

    Replays check all persisted manifests/members/leaf rows; they never append
    more batches. Only pending_consent can be materialized, and no worker runs.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    size=positive(batch_size,"batch_size",maximum=MAX_BATCH_SIZE)
    with conn.transaction():
        state,digest,items,keys=_run_and_items(conn,pid,rid,for_update=True)
        if state!="pending_consent":
            reject("run_id","LIVE run not in pre-consent planning state",
                   "invalid_transition")
        expected=_derived_batches(pid,rid,digest,items,keys,size)
        if len(items)*len(keys)>MAX_PAIRS:
            reject("coverage","too many expected pairs","incomplete_coverage")
        batches,members,pairs=_stored_rows(conn,pid,rid)
        if batches or members or pairs:
            _assert_existing(expected,batches,members,pairs)
            replayed=True
        else:
            if any(item["state"]!="not_evaluated" for item in items):
                reject("live_items","cannot build batches from evaluated work",
                       "invalid_transition")
            for batch in expected:
                conn.execute(
                    """INSERT INTO sre_live_batches
                       (batch_id,run_id,project_id,ordinal,manifest,manifest_sha256,input_sha256)
                       VALUES (%s::uuid,%s::uuid,%s,%s,%s::jsonb,%s,%s)""",
                    (batch["batch_id"],rid,pid,batch["ordinal"],
                     json.dumps(batch["manifest"],ensure_ascii=False),
                     batch["manifest_sha256"],batch["input_sha256"]),
                )
                for ordinal,item in enumerate(batch["items"]):
                    conn.execute(
                        """INSERT INTO sre_live_batch_members
                           (batch_id,run_id,project_id,item_id,ordinal)
                           VALUES (%s::uuid,%s::uuid,%s,%s,%s)""",
                        (batch["batch_id"],rid,pid,item["item_id"],ordinal),
                    )
                for pair in batch["pairs"]:
                    conn.execute(
                        """INSERT INTO sre_live_expected_pairs
                           (run_id,project_id,batch_id,item_id,category_key)
                           VALUES (%s::uuid,%s,%s::uuid,%s,%s::uuid)""",
                        (rid,pid,batch["batch_id"],pair["item_id"],
                         pair["category_key"]),
                    )
            replayed=False
        return {
            "schema_version":BATCH_VERSION,
            "run_id":rid,"project_id":pid,"execution_mode":"live",
            "status":"pending_consent","replayed":replayed,
            "batch_ids":[b["batch_id"] for b in expected],
            "batches":len(expected),"items":len(items),
            "hypotheses":len(keys),"expected_pairs":len(items)*len(keys),
            "evaluated_pairs":0, "execution_allowed":False,
        }


def summarize_pair_coverage(expected, observed):
    """Pure exhaustive coverage assertion for future verified worker outputs.

    There must be exactly ONE observed entry for every expected (item, C9 key)
    pair. Missing, duplicate and unexpected rows are not silent negatives.
    A 'match'/'no_match'/'uncertain' observation is a TRANSPORT ASSERTION,
    not verified proof; future M17+ validates model evidence before persisting.
    """
    if type(expected) is not list or type(observed) is not list:
        reject("coverage","expected/observed must be lists")
    expected_keys=[]
    for pair in expected:
        if type(pair) is not dict or set(pair)!={"item_id","category_key"}:
            reject("coverage.expected","invalid expected pair shape")
        item=positive(pair["item_id"],"coverage.item_id",maximum=9223372036854775807)
        key=canonical_uuid(pair["category_key"],"coverage.category_key")
        expected_keys.append((item,key))
    if not expected_keys or len(expected_keys)!=len(set(expected_keys)):
        reject("coverage.expected","duplicate/empty expected matrix",
               "incomplete_coverage")
    rows={}
    for entry in observed:
        if type(entry) is not dict or set(entry)!={"item_id","category_key","state"}:
            reject("coverage.observed","invalid observation shape")
        key=(positive(entry["item_id"],"coverage.item_id",
                      maximum=9223372036854775807),
             canonical_uuid(entry["category_key"],"coverage.category_key"))
        if key in rows:
            reject("coverage.observed","duplicate leaf verdict","incomplete_coverage")
        state=entry["state"]
        if type(state) is not str or state not in _STATES:
            reject("coverage.observed","unsupported state")
        rows[key]=state
    if set(expected_keys)!=set(rows):
        reject("coverage.observed","missing/extra leaf labels",
               "incomplete_coverage")
    count={k:sum(v==k for v in rows.values()) for k in sorted(_STATES)}
    return {
        "total_pairs":len(expected_keys),
        "evaluated_pairs":sum(count[k] for k in _FINAL),
        "blocked_pairs":sum(count[k] for k in ("blocked_rights","blocked_budget")),
        "not_evaluated_pairs":count["not_evaluated"],
        "decisions":count,
        "complete":all(x in _FINAL for x in rows.values()),
        "partial":0<sum(count[k] for k in _FINAL)<len(expected_keys),
    }


def get_live_batch_coverage(conn, project, run_id):
    """Read actual row-level statuses; project-scoped, NO fake model decisions."""
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    state,digest,items,keys=_run_and_items(conn,pid,rid)
    batches,members,pairs=_stored_rows(conn,pid,rid)
    if not batches:
        reject("batch_manifest","batch matrix not materialized",
               "incomplete_coverage")
    batch_size=max(len(x[2]["items"]) for x in batches)
    expected=_derived_batches(pid,rid,digest,items,keys,batch_size)
    _assert_existing(expected,batches,members,pairs)
    expected_rows=[
        {"item_id":i["item_id"],"category_key":key}
        for i in items for key in keys
    ]
    observed=[
        {"item_id":item,"category_key":key,"state":status}
        for _,item,key,status in pairs
    ]
    report=summarize_pair_coverage(expected_rows,observed)
    return {
        "schema_version":BATCH_VERSION,
        "project_id":pid,"run_id":rid,"run_state":state,
        "batches":len(batches),"items":len(items),"hypotheses":len(keys),
        "batch_manifest_sha256":fingerprint([b[3] for b in batches]),
        "coverage":report,
        "execution_allowed":False,
    }


__all__=[
    "BATCH_VERSION","MAX_BATCH_SIZE","make_batch_manifest",
    "materialize_live_batches","summarize_pair_coverage",
    "get_live_batch_coverage",
]
