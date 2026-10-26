"""M12: durable, idempotent LIVE planning from actual approved C9 + live D1.

Internal/trusted service only: an explicit project_id is NOT proof of the
requesting user's right to that project. The host must authenticate the actor.
Planning never approves spending, starts an offline/live worker, sends to a
provider, or publishes classification results. Persisted runs start in
pending_consent; items in not_evaluated. Future M13+ own batch manifests.

One project-row lock serializes same-tenant planning under READ COMMITTED.
Every relevant input is re-derived from PostgreSQL inside that transaction.
M08 rights must be rechecked immediately before future external send.
"""
from __future__ import annotations

import json
from uuid import uuid4

from .contracts import ContractError, validate_pipeline
from .dataset_preview import _selection
from .ports.approved_c9 import build_c9_bundle
from .v1_contracts import (
    canonical_uuid, fingerprint, nonempty, positive, project_id, reject,
)

PLAN_VERSION = "sre-live-plan/v1"
MAX_PLANNED_VIDEOS = 1000
MAX_INT64 = 9223372036854775807


def _key(value):
    candidate = nonempty(value, "idempotency_key", limit=120)
    if not all(c.isascii() and (c.isalnum() or c in "._-") for c in candidate):
        reject("idempotency_key", "expected ASCII alphanumeric . _ -")
    return candidate


def _pipeline(conn, pid, config_id):
    row = conn.execute(
        """SELECT config FROM sre_pipeline_configs
           WHERE project_id=%s AND id=%s""", (pid, config_id),
    ).fetchone()
    if row is None:
        reject("pipeline_config_id", "pipeline not owned by project", "rights_denied")
    try:
        conf = validate_pipeline(row[0])
    except (ContractError, ValueError, TypeError) as exc:
        reject("pipeline_config_id", "invalid approved pipeline configuration")
    # M12 pins the checked shape only. A validated model reference is not a
    # live provider model, catalog or paid execution authorization (M24+).
    active = [s for s in conf["stages"] if s.get("enabled", True)]
    if not active or not any(s["type"] == "llm_classifier" for s in active):
        reject("pipeline_config_id", "no active classifier", "unsupported_pipeline")
    return conf


def _selected_content_rows(conn, pid, ids):
    rows = conn.execute(
        """SELECT c.id,c.video_id,c.revision,c.content_sha256,
                  c.provenance,c.metadata_observation_id
           FROM sre_content_versions c
           JOIN sre_project_videos p
             ON p.project_id=c.project_id AND p.video_id=c.video_id
           WHERE c.project_id=%s AND c.id=ANY(%s::bigint[])
           ORDER BY c.video_id,c.revision DESC,c.id DESC""",
        (pid, ids),
    ).fetchall()
    if (len(rows) != len(ids) or {r[0] for r in rows} != set(ids)
            or any(type(r[0]) is not int or type(r[1]) is not int or
                   type(r[2]) is not int or r[2] <= 0 for r in rows)):
        reject("content_version_ids", "unowned/missing S3 selection",
               "rights_denied")
    chosen, superseded = {}, []
    for cid, video, revision, digest, origin, observation in sorted(
        rows, key=lambda r: (r[1], -r[2], -r[0]),
    ):
        if video not in chosen:
            chosen[video] = (cid, revision, digest, origin, observation)
        else:
            superseded.append({
                "video_id": video, "content_version_id": cid,
                "kept_content_version_id": chosen[video][0],
                "reason": "superseded_in_selection",
            })
    if not 1 <= len(chosen) <= MAX_PLANNED_VIDEOS:
        reject("content_version_ids", "unsupported selected video count")
    return chosen, sorted(superseded, key=lambda x: (x["video_id"], x["content_version_id"]))


def _verified_inputs(conn, pid, pub, ids, conf_id):
    from .taxonomy_adapter import read_authorized_taxonomy
    from .live_content_gate import read_live_content_gate

    taxonomy = read_authorized_taxonomy(conn, pid, pub)
    c9 = build_c9_bundle(taxonomy)
    if c9["project_id"] != pid or c9["publication_id"] != pub:
        reject("approved_c9", "publication identity mismatch", "snapshot_mismatch")
    keys = c9["hypothesis_keys"]
    contexts = c9["contexts"]
    if (not 1 <= len(keys) <= 48 or keys != sorted(set(keys))
            or len(contexts) != len(keys)
            or {ctx["category_key"] for ctx in contexts} != set(keys)):
        reject("approved_c9", "incomplete approved leaf coverage", "snapshot_mismatch")
    config = _pipeline(conn, pid, conf_id)
    config_sha = fingerprint(config)
    context_sha = fingerprint(contexts)
    chosen, superseded = _selected_content_rows(conn, pid, ids)
    items = []
    for video in sorted(chosen):
        cid, rev, saved_sha, origin, obs = chosen[video]
        gate = read_live_content_gate(
            conn, pid, cid, phase="planning", external_llm=True,
            pins={"video_id": video, "content_version_id": cid,
                  "content_sha256": saved_sha},
        )
        snap, proof = gate["content"], gate["provenance"]
        if snap["project_id"] != pid or snap["video_id"] != video:
            reject("content_version_ids", "D1 version changed owner", "snapshot_mismatch")
        if proof["metadata_observation_id"] != obs or proof["content_sha256"] != saved_sha:
            reject("content_version_ids", "D1 observation or SHA drift",
                   "snapshot_mismatch")
        if type(origin) is not dict or origin != snap["provenance"]:
            reject("content_version_ids", "canonical D1 origin differs from S3 storage",
                   "snapshot_mismatch")
        if not any(snap["data"].get(field) for field in (
                "title", "description", "hashtags", "transcript", "scenes")):
            reject("content_version_ids", "no classifiable source evidence",
                   "missing_provenance")
        items.append({
            "video_id": video, "content_version_id": cid,
            "content_revision": rev, "content_sha256": saved_sha,
            "source_id": snap["source_id"],
            "source_policy_version_id": snap["source_policy_version_id"],
            "discovery_id": proof["discovery_id"],
            "metadata_observation_id": obs,
            "origin": origin,
            "expected_hypothesis_keys": list(keys),
            "contexts_sha256": context_sha,
        })
    manifest = {
        "schema_version": PLAN_VERSION,
        "execution_mode": "live",
        "project_id": pid,
        "publication_id": pub,
        "taxonomy_version_id": c9["sre_taxonomy_version_id"],
        "taxonomy_sha256": c9["taxonomy_sha256"],
        "c9_bundle_sha256": c9["bundle_sha256"],
        "pipeline_config_id": conf_id,
        "pipeline_sha256": config_sha,
        "contexts_sha256": context_sha,
        "expected_hypothesis_keys": list(keys),
        "selected_content_versions": list(ids),
        "superseded_in_selection": superseded,
        "items": items,
    }
    digest = fingerprint(manifest)
    # Include exactly the immutable manifest and never the random run UUID.
    # Sorted set IDs canonicalize order/repeats. Adding a superseded version
    # changes the manifest fingerprint: input changes cannot hide behind dedup.
    return manifest, digest


def create_live_run(conn, project, publication, content_version_ids,
                    pipeline_config_id, idempotency_key):
    """Write only unapproved LIVE plan + not_evaluated per-video items.

    Atomic with its child rows, serialized by the workspace row lock.
    An identical key and canonical request returns the existing run_id even
    after later processing; conflicting content returns idempotency_conflict.
    No batches, provider attempts, finance, token spending or HTTP call.
    """
    pid = project_id(project)
    pub = canonical_uuid(publication, "publication_id")
    config_id = positive(pipeline_config_id, "pipeline_config_id",
                         maximum=MAX_INT64)
    key = _key(idempotency_key)
    ids = _selection(content_version_ids)
    for cid in ids:
        positive(cid, "content_version_ids[]", maximum=MAX_INT64)
    with conn.transaction():
        # Two writers using the same key cannot race on the UNIQUE index and
        # turn a repeat into a failure. Same project runs always serialize.
        locked = conn.execute(
            "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE",
            (pid,),
        ).fetchone()
        if locked is None:
            reject("project_id", "SRE project workspace missing", "rights_denied")
        manifest, digest = _verified_inputs(conn, pid, pub, ids, config_id)
        existing = conn.execute(
            """SELECT run_id::text,request_sha256,manifest_sha256,state
               FROM sre_live_runs
               WHERE project_id=%s AND idempotency_key=%s""",
            (pid, key),
        ).fetchone()
        if existing is not None:
            if existing[1] != digest or existing[2] != digest:
                reject("idempotency_key", "same key with changed LIVE inputs",
                       "idempotency_conflict")
            return {
                "run_id": existing[0], "project_id": pid,
                "status": existing[3], "replayed": True,
                "execution_mode": "live", "items": len(manifest["items"]),
                "hypotheses": len(manifest["expected_hypothesis_keys"]),
                "request_sha256": digest, "manifest_sha256": digest,
            }
        run_id = str(uuid4())
        conn.execute(
            """INSERT INTO sre_live_runs
                 (run_id,project_id,publication_id,taxonomy_version_id,
                  taxonomy_sha256,pipeline_config_id,pipeline_sha256,
                  request_sha256,manifest,manifest_sha256,idempotency_key,
                  expected_items,state)
               VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,
                       'pending_consent')""",
            (run_id,pid,pub,manifest["taxonomy_version_id"],
             manifest["taxonomy_sha256"],config_id,
             manifest["pipeline_sha256"],digest,
             json.dumps(manifest,ensure_ascii=False),digest,key,
             len(manifest["items"])),
        )
        for item in manifest["items"]:
            conn.execute(
                """INSERT INTO sre_live_items
                     (run_id,project_id,video_id,content_version_id,
                      content_sha256,source_id,source_policy_version_id,
                      discovery_id,metadata_observation_id,content_revision,
                      origin,expected_hypothesis_keys,contexts_sha256,
                      manifest_sha256,state)
                   VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           %s::jsonb,%s::jsonb,%s,%s,'not_evaluated')""",
                (run_id,pid,item["video_id"],item["content_version_id"],
                 item["content_sha256"],item["source_id"],
                 item["source_policy_version_id"],item["discovery_id"],
                 item["metadata_observation_id"],item["content_revision"],
                 json.dumps(item["origin"],ensure_ascii=False),
                 json.dumps(item["expected_hypothesis_keys"]),
                 item["contexts_sha256"],digest),
            )
        return {
            "run_id": run_id, "project_id": pid, "status": "pending_consent",
            "replayed": False, "execution_mode": "live",
            "items": len(manifest["items"]),
            "hypotheses": len(manifest["expected_hypothesis_keys"]),
            "request_sha256": digest, "manifest_sha256": digest,
        }


def get_live_plan(conn, project, run_id):
    """Tenant-scoped status/manifest metadata without raw content leakage."""
    pid = project_id(project)
    rid = canonical_uuid(run_id, "run_id")
    row = conn.execute(
        """SELECT state,publication_id::text,taxonomy_version_id,
                  pipeline_config_id,request_sha256,manifest_sha256,
                  expected_items,execution_mode
           FROM sre_live_runs WHERE project_id=%s AND run_id=%s::uuid""",
        (pid,rid),
    ).fetchone()
    if row is None or row[7] != "live":
        reject("run_id", "run not available to project", "rights_denied")
    items = conn.execute(
        """SELECT video_id,content_version_id,state
           FROM sre_live_items
           WHERE project_id=%s AND run_id=%s::uuid ORDER BY video_id""",
        (pid,rid),
    ).fetchall()
    if len(items) != row[6]:
        reject("run_id", "LIVE manifest/rows coverage mismatch",
               "incomplete_coverage")
    return {
        "run_id": rid, "project_id": pid, "status": row[0],
        "publication_id": row[1], "taxonomy_version_id": row[2],
        "pipeline_config_id": row[3],
        "request_sha256": row[4], "manifest_sha256": row[5],
        "execution_mode": "live",
        "items": [
            {"video_id": video,"content_version_id": ver,"state": status}
            for video,ver,status in items
        ],
    }


__all__ = ["PLAN_VERSION", "MAX_PLANNED_VIDEOS",
           "create_live_run", "get_live_plan"]
