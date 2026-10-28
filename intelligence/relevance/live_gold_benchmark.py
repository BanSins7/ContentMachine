"""M33 reproducible human-gold benchmark. Trusted internal, zero-cost, no raw text.

Only existing M30 authenticated model outputs with LATEST explicit M32 human
reviews can enter a new cohort; no synthetic correctness or price/permission
claims. Original model is scored against human gold, never the human overlay
against itself. Video-grouped deterministic stratified holdout avoids the same
video appearing on both sides. No training, calibration probability or POST.

Binary match metrics explicitly count model 'uncertain' on known-positive gold
as missed positive (FN); on known-negative gold as unscored abstention, NOT
true negative. Human-gold 'uncertain' excluded from the binary denominator,
counted separately. No significance/accuracy number is invented for empty sets.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from .live_verified_coverage import _verify_current_d1
from .opus_consent import _actor, _authorized
from .opus_dispatch_intent import _committable
from .v1_contracts import (
    canonical_uuid,fingerprint,positive,project_id,reject,sha256_digest,
)

VERSION="sre-live-gold-benchmark/v1"
LABELS=frozenset(("match","no_match","uncertain"))
SPLITS=frozenset(("train","eval"))
_MAX=1000
_SEED=re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_FIELDS=frozenset((
    "item_id","video_id","content_version_id","content_sha256",
    "category_key","original_result_id","human_review_id","human_revision",
    "model_decision","gold_decision","quote_sha256","catalog_sha256",
    "model_revision","split",
))
SQL_WORKSPACE_SHARE="SELECT 1 FROM sre_workspaces WHERE project_id=%s"
SQL_WORKSPACE_EXCLUSIVE="SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE"
SQL_SOURCE="""SELECT v.item_id,v.category_key::text,v.result_id::text,v.decision,
    i.video_id,i.content_version_id,i.content_sha256,
    h.review_id::text,h.revision,h.human_decision,
    d.quote_sha256,d.catalog_sha256,d.model_revision
    FROM sre_live_verified_pair_results v
    JOIN sre_live_items i
      ON i.project_id=v.project_id AND i.run_id=v.run_id AND i.id=v.item_id
    JOIN sre_live_runs r
      ON r.project_id=v.project_id AND r.run_id=v.run_id
    JOIN sre_live_opus_receipts t
      ON t.project_id=v.project_id AND t.receipt_id=v.receipt_id
    JOIN sre_live_opus_dispatch_intents d
      ON d.project_id=t.project_id AND d.intent_id=t.intent_id
    JOIN LATERAL (
       SELECT h1.review_id,h1.revision,h1.human_decision,
              h1.base_result_id,h1.human_verified,
              h1.quote_sha256,h1.catalog_sha256
       FROM sre_live_human_reviews h1
       WHERE h1.project_id=v.project_id AND h1.run_id=v.run_id
         AND h1.item_id=v.item_id AND h1.category_key=v.category_key
       ORDER BY h1.revision DESC LIMIT 1
    ) h ON h.base_result_id=v.result_id AND h.human_verified=TRUE
      AND h.quote_sha256=d.quote_sha256
      AND h.catalog_sha256=d.catalog_sha256
    WHERE v.project_id=%s AND v.run_id=%s::uuid
      AND r.execution_mode='live'
      AND r.cancel_requested=FALSE
      AND r.state IN ('queued','running','partial','completed')
      AND t.generation_authenticated=TRUE
      AND t.evidence_status='validated_untrusted_output'
    ORDER BY v.item_id,v.category_key LIMIT 1001"""
SQL_RUN="""SELECT state,cancel_requested,execution_mode
    FROM sre_live_runs WHERE project_id=%s AND run_id=%s::uuid"""
SQL_INSERT="""INSERT INTO sre_live_gold_benchmark_snapshots (
    benchmark_id,project_id,run_id,snapshot_sha256,split_seed,
    holdout_percent,min_evaluation_examples,manifest,actor_id)
    VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s::jsonb,%s)
    ON CONFLICT DO NOTHING RETURNING benchmark_id::text"""
SQL_EXISTING="""SELECT benchmark_id::text
    FROM sre_live_gold_benchmark_snapshots
    WHERE project_id=%s AND run_id=%s::uuid AND snapshot_sha256=%s"""
SQL_READ="""SELECT benchmark_id::text,run_id::text,snapshot_sha256,
    split_seed,holdout_percent,min_evaluation_examples,manifest,created_at
    FROM sre_live_gold_benchmark_snapshots
    WHERE project_id=%s AND benchmark_id=%s::uuid"""


def _policy(seed,holdout_percent,min_eval):
    if type(seed) is not str or not _SEED.fullmatch(seed):
        reject("split_seed","required bounded deterministic holdout seed",
               "invalid_benchmark_policy")
    if type(holdout_percent) is not int or not 10<=holdout_percent<=90:
        reject("holdout_percent","integer 10..90 percent required",
               "invalid_benchmark_policy")
    if type(min_eval) is not int or not 1<=min_eval<=1000:
        reject("min_evaluation_examples","integer 1..1000 required",
               "invalid_benchmark_policy")
    return seed,holdout_percent,min_eval


def _samples(rows):
    """Validate real M30/M32 typed rows; never accept client-supplied gold."""
    if type(rows) is not list or not 1<=len(rows)<=_MAX:
        reject("cohort","1..1000 authentic human-reviewed pairs required",
               "insufficient_gold")
    prepared=[];keys=set();same_item={};video_origin={}
    for row in rows:
        if type(row) not in (tuple,list) or len(row)!=13:
            reject("cohort","invalid database human-gold row shape",
                   "invalid_evidence")
        (iid,key,result,model,video,cid,content_sha,review,revision,gold,
         quote_sha,catalog_sha,model_revision)=row
        iid=positive(iid,"item_id",maximum=9223372036854775807)
        video=positive(video,"video_id",maximum=9223372036854775807)
        cid=positive(cid,"content_version_id",maximum=9223372036854775807)
        key=canonical_uuid(key,"category_key")
        result=canonical_uuid(result,"original_result_id")
        review=canonical_uuid(review,"human_review_id")
        sha256_digest(content_sha,"content_sha256")
        sha256_digest(quote_sha,"quote_sha256")
        sha256_digest(catalog_sha,"catalog_sha256")
        if (type(revision) is not int or not 1<=revision<=10000
            or type(model) is not str or model not in LABELS
            or type(gold) is not str or gold not in LABELS
            or type(model_revision) is not str
            or not 6<=len(model_revision)<=120):
            reject("cohort","invalid human source/model gold metadata",
                   "invalid_evidence")
        token=(iid,key)
        if token in keys or (
            iid in same_item and same_item[iid]!=(video,cid,content_sha)
        ):
            reject("cohort","duplicate category or video/source pin drift",
                   "invalid_evidence")
        if video in video_origin and video_origin[video]!=(iid,cid,content_sha):
            reject("cohort","one video cannot have multiple source versions",
                   "dataset_leakage")
        keys.add(token)
        same_item[iid]=(video,cid,content_sha)
        video_origin[video]=(iid,cid,content_sha)
        prepared.append({
            "item_id":iid,"video_id":video,"content_version_id":cid,
            "content_sha256":content_sha,"category_key":key,
            "original_result_id":result,"human_review_id":review,
            "human_revision":revision,"model_decision":model,
            "gold_decision":gold,"quote_sha256":quote_sha,
            "catalog_sha256":catalog_sha,"model_revision":model_revision,
        })
    return sorted(prepared,key=lambda r:(r["item_id"],r["category_key"]))


def _split(rows,pid,seed,holdout_percent):
    """Deterministic approximate class-signature strata by whole video."""
    groups=defaultdict(list)
    for row in rows:groups[row["video_id"]].append(row)
    strata=defaultdict(list)
    for video,items in groups.items():
        signature="/".join(sorted(set(x["gold_decision"] for x in items)))
        strata[signature].append(video)
    eval_ids=set()
    for signature,ids in sorted(strata.items()):
        ordered=sorted(ids,key=lambda vid:hashlib.sha256(
            f"{seed}:{pid}:{vid}:{signature}".encode("utf-8")).hexdigest())
        # Approximate per-stratum holdout. A single-video stratum cannot
        # split across train/eval, so it goes to eval and is reported.
        n=max(1,(len(ordered)*holdout_percent+99)//100)
        eval_ids.update(ordered[:n])
    result=[{**row,"split":"eval" if row["video_id"] in eval_ids
             else "train"} for row in rows]
    if {x["video_id"] for x in result if x["split"]=="train"} & (
            {x["video_id"] for x in result if x["split"]=="eval"}):
        reject("split","same source video in train and evaluation",
               "dataset_leakage")
    return result


def _ratio(n,d):
    return (format((Decimal(n)/Decimal(d)).quantize(
        Decimal("0.000001"),rounding=ROUND_HALF_UP),".6f") if d else None)


def _metrics(rows,min_eval):
    """Pure honest binary match metrics; coverage and abstentions explicit."""
    if type(rows) is not list or any(
        type(r) is not dict or set(r)!=_FIELDS
        or r.get("split") not in SPLITS for r in rows
    ):
        reject("manifest","invalid evaluation snapshot manifest",
               "invalid_evidence")
    ev=[r for r in rows if r["split"]=="eval"]
    tr=[r for r in rows if r["split"]=="train"]
    gold_counts=Counter(r["gold_decision"] for r in ev)
    train_counts=Counter(r["gold_decision"] for r in tr)
    positives=sum(r["gold_decision"]=="match" for r in ev)
    negatives=sum(r["gold_decision"]=="no_match" for r in ev)
    uncertain=sum(r["gold_decision"]=="uncertain" for r in ev)
    eligible=positives+negatives
    tp=sum(r["model_decision"]=="match" and r["gold_decision"]=="match" for r in ev)
    fp=sum(r["model_decision"]=="match" and r["gold_decision"]=="no_match" for r in ev)
    fn=sum(r["model_decision"]!="match" and r["gold_decision"]=="match" for r in ev)
    tn=sum(r["model_decision"]=="no_match" and r["gold_decision"]=="no_match" for r in ev)
    abstained_positive=sum(r["model_decision"]=="uncertain" and
                           r["gold_decision"]=="match" for r in ev)
    abstained_negative=sum(r["model_decision"]=="uncertain" and
                           r["gold_decision"]=="no_match" for r in ev)
    assert tp+fn==positives
    assert tn+fp+abstained_negative==negatives
    precision=_ratio(tp,tp+fp)
    recall=_ratio(tp,tp+fn)
    f1=_ratio(2*tp,2*tp+fp+fn)
    enough=eligible>=min_eval and positives>0 and negatives>0
    status=("descriptive_holdout_not_production_calibrated" if enough
            else "insufficient_gold_or_class_coverage")
    return {
        "status":status,
        "evaluation_count":len(ev),
        "training_partition_count_not_fitted":len(tr),
        "evaluation_video_groups":len({r["video_id"] for r in ev}),
        "training_video_groups":len({r["video_id"] for r in tr}),
        "evaluation_gold_counts":{
            x:gold_counts[x] for x in ("match","no_match","uncertain")},
        "training_gold_counts":{
            x:train_counts[x] for x in ("match","no_match","uncertain")},
        "binary_gold_denominator":eligible,
        "excluded_human_uncertain_count":uncertain,
        "model_abstained_on_gold_positive_count":abstained_positive,
        "model_abstained_on_gold_negative_count":abstained_negative,
        "confusion_matrix":{"tp":tp,"fp":fp,"fn":fn,"tn":tn},
        "precision_match":precision,"recall_match":recall,"f1_match":f1,
        "strict_known_gold_accuracy":_ratio(tp+tn,eligible),
        "prediction_coverage_known_gold":_ratio(
            eligible-abstained_positive-abstained_negative,eligible),
        "meets_minimum_sample_and_both_classes":enough,
        "confidence_calibration_status":"unavailable_no_authenticated_probabilities",
        "brier_score":None,"expected_calibration_error":None,
        "confidence_intervals":None,"statistical_significance":None,
        "sample_selection_randomized":False,
        "claim_of_model_generalization":False,
    }


def _manifest(rows,pid,rid,seed,holdout_percent):
    sample=_samples(rows)
    assigned=_split(sample,pid,seed,holdout_percent)
    return assigned


def _frozen_sha(pid,rid,seed,pct,minimum,items):
    return fingerprint({
        "schema_version":VERSION,"project_id":pid,"run_id":rid,
        "split_seed":seed,"holdout_percent":pct,
        "min_evaluation_examples":minimum,"items":items,
    })


def _source(conn,pid,rid):
    run=conn.execute(SQL_RUN,(pid,rid)).fetchone()
    if (run is None or run[2]!="live" or run[1] is not False
        or run[0] not in ("queued","running","partial","completed")):
        reject("run_id","foreign, cancelled or unreviewable LIVE source run",
               "rights_denied")
    rows=conn.execute(SQL_SOURCE,(pid,rid)).fetchall()
    if len(rows)>_MAX:
        reject("cohort","oversized review cohort; require explicit bounded design",
               "insufficient_gold")
    for row in rows:
        _verify_current_d1(conn,pid,row[4],row[5],row[6])
    return rows


def _public(pid,rid,seed,pct,manifest,min_eval):
    stat=_metrics(manifest,min_eval)
    return {
        "schema_version":VERSION,"project_id":pid,"run_id":rid,
        "snapshot_sha256":_frozen_sha(pid,rid,seed,pct,min_eval,manifest),
        "split_seed":seed,"holdout_percent":pct,
        "min_evaluation_examples":min_eval,
        "human_reviewed_sample_count":len(manifest),
        **stat,
        "source_rights_rechecked":True,
        "raw_source_or_reviewer_notes_present":False,
        "paid_post_calls_issued_here":0,
        "training_occurred":False,
        "calibration_claimed":False,
    }


def preview_live_sre_gold_benchmark(conn,project,run_id,*,actor_id,
    actor_authorizer,split_seed="sre-gold-v1",holdout_percent=30,
    min_evaluation_examples=10):
    """Read-only current human-gold cohort; NOT an approved quality claim."""
    pid=project_id(project);rid=canonical_uuid(run_id,"run_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_gold_benchmark")
    seed,pct,minimum=_policy(split_seed,holdout_percent,min_evaluation_examples)
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if conn.execute(SQL_WORKSPACE_SHARE,(pid,)).fetchone() is None:
            reject("project_id","foreign workspace","rights_denied")
        rows=_source(conn,pid,rid)
        if not rows:
            return {
                "schema_version":VERSION,"project_id":pid,"run_id":rid,
                "status":"no_authenticated_human_gold",
                "human_reviewed_sample_count":0,"binary_gold_denominator":0,
                "precision_match":None,"recall_match":None,"f1_match":None,
                "confidence_calibration_status":
                    "unavailable_no_authenticated_probabilities",
                "paid_post_calls_issued_here":0,
                "claim_of_model_generalization":False,
            }
        manifest=_manifest(rows,pid,rid,seed,pct)
        return _public(pid,rid,seed,pct,manifest,minimum)


def record_live_sre_gold_benchmark(conn,project,run_id,*,actor_id,
    actor_authorizer,split_seed="sre-gold-v1",holdout_percent=30,
    min_evaluation_examples=10):
    """Immutable cohort materialization: no model training, POST or labels."""
    pid=project_id(project);rid=canonical_uuid(run_id,"run_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"record_sre_gold_benchmark")
    seed,pct,minimum=_policy(split_seed,holdout_percent,min_evaluation_examples)
    _committable(conn)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE_EXCLUSIVE,(pid,)).fetchone() is None:
            reject("project_id","foreign workspace","rights_denied")
        rows=_source(conn,pid,rid)
        if not rows:
            reject("cohort","no real M32 human-gold labels yet","insufficient_gold")
        manifest=_manifest(rows,pid,rid,seed,pct)
        report=_public(pid,rid,seed,pct,manifest,minimum)
        identity=str(uuid4())
        row=conn.execute(SQL_INSERT,(
            identity,pid,rid,report["snapshot_sha256"],seed,pct,minimum,
            json.dumps(manifest,sort_keys=True,separators=(",",":")),actor,
        )).fetchone()
        if row is not None:
            stored=row[0];created=True
        else:
            existing=conn.execute(SQL_EXISTING,(
                pid,rid,report["snapshot_sha256"])).fetchone()
            if existing is None:
                reject("benchmark","immutable snapshot insert conflict",
                       "budget_invariant")
            stored=existing[0];created=False
    return {**report,"benchmark_id":stored,"created":created,
            "snapshot_immutable":True}


def _read_saved(conn,pid,benchmark_id):
    bid=canonical_uuid(benchmark_id,"benchmark_id")
    row=conn.execute(SQL_READ,(pid,bid)).fetchone()
    if row is None:
        reject("benchmark_id","unknown/foreign gold snapshot","rights_denied")
    bid,rid,sha,seed,pct,minimum,manifest,when=row
    if (type(manifest) is not list or not manifest
            or len(manifest)>_MAX
            or any(type(x) is not dict or set(x)!=_FIELDS
                   for x in manifest)):
        reject("manifest","invalid immutable snapshot","invalid_evidence")
    seed,pct,minimum=_policy(seed,pct,minimum)
    if len(manifest)>_MAX or not manifest:
        reject("manifest","invalid snapshot sample count","invalid_evidence")
    # Check frozen IDs, category pins, no duplicate and split reproducibility.
    sample=_samples([
        (x["item_id"],x["category_key"],x["original_result_id"],
         x["model_decision"],x["video_id"],x["content_version_id"],
         x["content_sha256"],x["human_review_id"],x["human_revision"],
         x["gold_decision"],x["quote_sha256"],x["catalog_sha256"],
         x["model_revision"]) for x in manifest
    ])
    expected=_split(sample,pid,seed,pct)
    if manifest!=expected or _frozen_sha(
        pid,rid,seed,pct,minimum,manifest)!=sha:
        reject("manifest","source hash or video split changed","snapshot_mismatch")
    return bid,rid,sha,seed,pct,minimum,manifest,when


def read_saved_sre_gold_benchmark(conn,project,benchmark_id,*,actor_id,
    actor_authorizer,min_evaluation_examples=10):
    """Historical as-of-review snapshot; newer M32 corrections do not rewrite."""
    pid=project_id(project)
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_gold_benchmark")
    _policy("sre-gold-v1",30,min_evaluation_examples)
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if conn.execute(SQL_WORKSPACE_SHARE,(pid,)).fetchone() is None:
            reject("project_id","foreign workspace","rights_denied")
        bid,rid,sha,seed,pct,minimum,manifest,when=_read_saved(
            conn,pid,benchmark_id)
        for x in manifest:
            _verify_current_d1(conn,pid,x["video_id"],
                               x["content_version_id"],x["content_sha256"])
        report=_public(pid,rid,seed,pct,manifest,minimum)
    return {
        **report,"benchmark_id":bid,
        "created_at":when.isoformat() if when is not None else None,
        "historical_review_ids_frozen":True,
        "may_differ_from_latest_human_review":True,
    }


def compare_saved_sre_gold_benchmarks(conn,project,benchmark_a,benchmark_b,*,
    actor_id,actor_authorizer,min_evaluation_examples=10):
    """Only paired exact evaluation sample keys and same gold are comparable."""
    pid=project_id(project);actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"compare_sre_gold_benchmarks")
    _policy("sre-gold-v1",30,min_evaluation_examples)
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if conn.execute(SQL_WORKSPACE_SHARE,(pid,)).fetchone() is None:
            reject("project_id","foreign workspace","rights_denied")
        a=_read_saved(conn,pid,benchmark_a)
        b=_read_saved(conn,pid,benchmark_b)
        for dataset in (a,b):
            for x in dataset[6]:
                _verify_current_d1(conn,pid,x["video_id"],
                    x["content_version_id"],x["content_sha256"])
        def keyed(dataset):
            return {(x["video_id"],x["content_sha256"],x["category_key"]):x
                    for x in dataset[6] if x["split"]=="eval"}
        x=keyed(a);y=keyed(b)
        if set(x)!=set(y) or not x or any(
            x[k]["gold_decision"]!=y[k]["gold_decision"] for k in x
        ):
            return {
                "schema_version":VERSION,
                "status":"not_comparable_unpaired_or_label_drift",
                "benchmark_a":a[0],"benchmark_b":b[0],
                "paired_evaluation_count":0,
                "superiority_claimed":False,"paid_post_calls_issued_here":0,
            }
        left=_metrics([{**z,"split":"eval"} for z in x.values()],a[5])
        right=_metrics([{**z,"split":"eval"} for z in y.values()],b[5])
        def delta(key):
            if left[key] is None or right[key] is None:return None
            return format(Decimal(right[key])-Decimal(left[key]),".6f")
        return {
            "schema_version":VERSION,
            "status":("paired_descriptive_only" if
                      left["meets_minimum_sample_and_both_classes"] and
                      right["meets_minimum_sample_and_both_classes"]
                      else "paired_insufficient_gold"),
            "benchmark_a":a[0],"benchmark_b":b[0],
            "paired_evaluation_count":len(x),
            "original_a":left,"candidate_b":right,
            "delta_f1_match":delta("f1_match"),
            "delta_precision_match":delta("precision_match"),
            "delta_recall_match":delta("recall_match"),
            "model_version_a":sorted({
                (z["model_revision"],z["catalog_sha256"],
                 z["quote_sha256"]) for z in x.values()}),
            "model_version_b":sorted({
                (z["model_revision"],z["catalog_sha256"],
                 z["quote_sha256"]) for z in y.values()}),
            "confidence_intervals":None,"significance_test":None,
            "superiority_claimed":False,
            "source_rights_rechecked":True,
            "paid_post_calls_issued_here":0,
        }


__all__=(
    "VERSION","preview_live_sre_gold_benchmark",
    "record_live_sre_gold_benchmark","read_saved_sre_gold_benchmark",
    "compare_saved_sre_gold_benchmarks",
)
