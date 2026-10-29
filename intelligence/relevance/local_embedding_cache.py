"""M18 persisted, bounded and revocable local ONNX embedding cache for SRE.

Only cache real M16 ONNX vectors, never simulated transport math. Every cache
lookup re-authorizes C9 publication and CURRENT D1 policy/content using M17
authoritative read, and pins the local model files by SHA before a cache hit.
A project workspace exclusive lock serializes duplicate misses/inserts and
enforces fixed per-project capacity; D1 policy row SHARE locks protect the
read/write window against concurrent revocation. No provider, finance or
classification outcome; no raw video/source text is stored.

The host MUST authenticate the project and provide trusted operator model
pins. Caller supplies one PostgreSQL conn; methods own a transaction.
"""
from __future__ import annotations

import json

from .local_embedding import (
    MAX_DIMENSIONS, normalize_real_vectors, verify_local_model_files,
    load_local_embedding_backend,
)
from .live_leaf_ranking import (
    _authorize_live_rank_inputs, _embed_one_or_more,
    _rank_inputs, _rank_vectors,
)
from .v1_contracts import (
    fingerprint, positive, project_id, reject, sha256_digest,
)

CACHE_VERSION = "sre-local-embedding-cache/v1"
MAX_PROJECT_ENTRIES = 1000
MAX_TTL_SECONDS = 86400
DEFAULT_TTL_SECONDS = 3600
MAX_CACHE_BYTES = 2000000
MAX_VECTORS = 97

_WORKSPACE_LOCK = "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE"
_POLICY = """SELECT p.id FROM source_policy_versions p
             WHERE p.id=%s AND p.is_active=TRUE FOR SHARE"""
_ITEM_POLICY = """SELECT i.source_policy_version_id
                  FROM sre_live_items i JOIN sre_live_runs r
                    ON r.run_id=i.run_id AND r.project_id=i.project_id
                  WHERE r.project_id=%s AND r.run_id=%s::uuid
                    AND i.id=%s AND r.execution_mode='live'"""
_READ = """SELECT vectors,vectors_sha256,vector_count,dimensions,expires_at
           FROM sre_local_embedding_cache
           WHERE project_id=%s AND publication_id=%s::uuid
             AND taxonomy_version_id=%s AND taxonomy_sha256=%s
             AND contexts_sha256=%s AND model_identity_sha256=%s
             AND video_id=%s AND content_version_id=%s
             AND content_sha256=%s AND source_id=%s
             AND source_policy_version_id=%s AND input_sha256=%s
             AND expires_at>clock_timestamp()"""
_INSERT = """INSERT INTO sre_local_embedding_cache
             (project_id,publication_id,taxonomy_version_id,taxonomy_sha256,
              contexts_sha256,model_identity_sha256,
              video_id,content_version_id,content_sha256,source_id,
              source_policy_version_id,input_sha256,vectors_sha256,
              vector_count,dimensions,vectors,expires_at)
             VALUES (%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                     %s,%s,%s::jsonb,
                     LEAST(clock_timestamp()+(%s * INTERVAL '1 second'),
                           COALESCE(%s::timestamptz,
                                    clock_timestamp()+(%s * INTERVAL '1 second'))))
             ON CONFLICT DO NOTHING"""
_PRUNE = """DELETE FROM sre_local_embedding_cache
            WHERE project_id=%s AND expires_at<=clock_timestamp()"""
_BOUND = """DELETE FROM sre_local_embedding_cache
            WHERE id IN (
              SELECT id FROM sre_local_embedding_cache
              WHERE project_id=%s
              ORDER BY created_at DESC,id DESC
              OFFSET %s
            )"""


def _cache_key(c9, content, model_identity, input_texts):
    """All tenant/model/taxonomy/content revision/policy/text pins."""
    if c9["project_id"] != content["project_id"]:
        reject("cache.project_id","foreign source/approved C9", "rights_denied")
    return (
        c9["project_id"],c9["publication_id"],
        c9["sre_taxonomy_version_id"],c9["taxonomy_sha256"],
        fingerprint(c9["contexts"]),model_identity,
        content["video_id"],content["content_version_id"],
        content["content_sha256"],content["source_id"],
        content["source_policy_version_id"],fingerprint(input_texts),
    )


def _vectors_from_row(row, dimensions, expected_count):
    """Untrusted DB JSON must be checked even with a valid digest string."""
    if row is None:
        return None
    stored,sha,count,dims,_=row
    if (type(stored) is not list or type(count) is not int or
            type(dims) is not int or count!=expected_count or
            dims!=dimensions or len(stored)!=expected_count or
            not 2<=len(stored)<=MAX_VECTORS):
        reject("embedding.cache","invalid stored matrix size","snapshot_mismatch")
    sha256_digest(sha,"cache.vectors_sha256")
    normalized=normalize_real_vectors(stored,dimensions=dimensions)
    # Require vectors to be explicitly unit normalized by trusted M16: an
    # arbitrary numeric DB row must not masquerade as a validated vector.
    for raw,unit in zip(stored,normalized):
        if abs(sum(float(x)*float(x) for x in raw)-1.0)>0.0001:
            reject("embedding.cache","non-unit stored embedding","snapshot_mismatch")
    if fingerprint(stored)!=sha:
        reject("embedding.cache","cached vector digest mismatch","snapshot_mismatch")
    return tuple(tuple(v) for v in stored)


def _materialize_vectors(value, *, dimensions, expected_count):
    if (type(value) not in (tuple,list) or len(value)!=expected_count
            or expected_count>MAX_VECTORS):
        reject("embedding.cache","model returned incomplete vector matrix",
               "incomplete_coverage")
    normalized=normalize_real_vectors(value,dimensions=dimensions)
    vectors=[list(row) for row in normalized]
    payload=json.dumps(vectors,ensure_ascii=False,separators=(",",":"),
                       allow_nan=False)
    if len(payload.encode("utf-8"))>MAX_CACHE_BYTES:
        reject("embedding.cache","model matrix exceeds bounded cache storage",
               "local_model_blocked")
    return vectors,payload,fingerprint(vectors)


def rank_live_run_item_cached(conn, project, run_id, item_id, *,
                              model_directory, model_pins,
                              ttl_seconds=DEFAULT_TTL_SECONDS):
    """Right-checked deduplicated persistent C9/D1 embedding reuse.

    Requires a project-authenticated host and trusted *operator* model pins.
    M18 locks the project row to make two misses serialize deterministically.
    Other projects can run concurrently without seeing each other's vectors.
    """
    pid=project_id(project)
    ttl=positive(ttl_seconds,"ttl_seconds",maximum=MAX_TTL_SECONDS)
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    from .v1_contracts import canonical_uuid
    rid=canonical_uuid(run_id,"run_id")
    with conn.transaction():
        if conn.execute(_WORKSPACE_LOCK,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace missing","rights_denied")
        policy_row=conn.execute(_ITEM_POLICY,(pid,rid,iid)).fetchone()
        if policy_row is None:
            reject("run_item","foreign/missing LIVE item","rights_denied")
        if conn.execute(_POLICY,(policy_row[0],)).fetchone() is None:
            reject("source_policy_version_id","source policy revoked",
                   "rights_denied")
        # Verify the current D1 policy/expiry + C9 approval + exact M12 pins
        # BEFORE checking cache contents or even loading the local weights.
        c9,content,gate,rid,iid=_authorize_live_rank_inputs(conn,pid,rid,iid)
        # SHA-verify the real local operator files on EVERY lookup.
        # Cache still is not valid when model files are missing/corrupt.
        pins,_=verify_local_model_files(model_directory,model_pins)
        model_identity=pins.identity_sha256()
        features,inputs=_rank_inputs(c9,content)
        cache_key=_cache_key(c9,content,model_identity,inputs)
        count=len(inputs)
        conn.execute(_PRUNE,(pid,))
        row=conn.execute(_READ,cache_key).fetchone()
        if row is None:
            # Only genuine SHA-verified M16 runtime is allowed to populate DB.
            backend=load_local_embedding_backend(model_directory,model_pins)
            if backend.model_identity_sha256!=model_identity:
                reject("model_identity_sha256","runtime model identity drifted",
                       "snapshot_mismatch")
            vectors=_embed_one_or_more(backend,inputs)
            canonical,payload,vector_sha=_materialize_vectors(
                vectors,dimensions=pins.dimensions,expected_count=count,
            )
            conn.execute(
                _INSERT,
                (*cache_key,vector_sha,count,pins.dimensions,payload,
                 ttl,content.get("expires_at"),ttl),
            )
            row=conn.execute(_READ,cache_key).fetchone()
            if row is None:
                reject("embedding.cache",
                       "cache insert rejected by expiry/rights or DDL",
                       "rights_denied")
            vectors=_vectors_from_row(row,pins.dimensions,count)
            hit=False
        else:
            vectors=_vectors_from_row(row,pins.dimensions,count)
            hit=True
        conn.execute(_BOUND,(pid,MAX_PROJECT_ENTRIES))
        # Model inference may take time. Re-run BOTH live C9 approval and
        # D1/S3 observation/rights expiry checks immediately before release;
        # a cache hit is no substitute for a fresh authoritative read.
        latest_c9,latest_content,latest_gate,_,_=_authorize_live_rank_inputs(
            conn,pid,rid,iid,
        )
        if (latest_c9["bundle_sha256"]!=c9["bundle_sha256"] or
                fingerprint(latest_content)!=fingerprint(content) or
                latest_gate["version_pin_sha256"]!=gate["version_pin_sha256"]):
            reject("embedding.cache","C9 or current D1 rights changed while ranking",
                   "snapshot_mismatch")
        result=_rank_vectors(c9,content,features,inputs,vectors,model_identity)
        result.update({
            "run_id":rid,"item_id":iid,
            "version_pin_sha256":gate["version_pin_sha256"],
            "cache_version":CACHE_VERSION,
            "cache_hit":hit,
            "cache_key_sha256":fingerprint(cache_key),
            "dispatch_allowed":False,"paid_calls":0,
        })
        result["ranking_sha256"]=fingerprint({
            k:v for k,v in result.items() if k!="ranking_sha256"
        })
        return result


def purge_local_embedding_cache(conn, project, *,
                                content_version_id=None,
                                source_policy_version_id=None,
                                expired_only=False):
    """Trusted revocation/deletion callback; no reads of revoked content.

    Deleting a source, its rights or an exact D1 revision never requires
    loading untrusted original text. Any future user-facing API MUST authz
    the project independently before invoking this internal maintenance port.
    """
    pid=project_id(project)
    if type(expired_only) is not bool:
        reject("expired_only","expected explicit boolean")
    if content_version_id is not None:
        positive(content_version_id,"content_version_id",maximum=9223372036854775807)
    if source_policy_version_id is not None:
        positive(source_policy_version_id,"source_policy_version_id",
                 maximum=9223372036854775807)
    if not expired_only and content_version_id is None and source_policy_version_id is None:
        reject("embedding.cache","explicit purge target required")
    with conn.transaction():
        if conn.execute(_WORKSPACE_LOCK,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace missing","rights_denied")
        where=["project_id=%s"]
        args=[pid]
        if content_version_id is not None:
            where.append("content_version_id=%s")
            args.append(content_version_id)
        if source_policy_version_id is not None:
            where.append("source_policy_version_id=%s")
            args.append(source_policy_version_id)
        if expired_only:
            where.append("expires_at<=clock_timestamp()")
        result=conn.execute(
            "DELETE FROM sre_local_embedding_cache WHERE "+" AND ".join(where),
            tuple(args),
        )
        return {
            "project_id":pid,"cache_version":CACHE_VERSION,
            "deleted_entries":result.rowcount,
            "paid_calls":0,
        }


__all__=(
    "CACHE_VERSION","MAX_PROJECT_ENTRIES","MAX_TTL_SECONDS",
    "rank_live_run_item_cached","purge_local_embedding_cache",
)
