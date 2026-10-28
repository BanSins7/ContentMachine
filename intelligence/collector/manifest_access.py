"""VC-MOD-06: project-scoped published manifest and held-run interface.

This boundary never accepts browser JSON query approval. It resolves a REAL
published QV manifest and a specific immutable shard. Existing repository
owns atomic held commit, and execution.py owns separate operator release.
"""
from __future__ import annotations

from .contracts import CollectorError, HASH_RE, PROJECT_RE, require_id, require_int, require_uuid
from .repository import _connection, trusted_preview, commit_run, _validated_commit_request
from .query_intake import parse_validated_query_set

def _scope(project_id: str, manifest_id: str):
    return (require_id(project_id, "project_id", PROJECT_RE),
            require_uuid(manifest_id, "manifest_id"))

def list_published_manifests(project_id: str, *, limit: int = 30, connect=None) -> dict:
    pid = require_id(project_id, "project_id", PROJECT_RE)
    size = require_int(limit, "limit", 1, 50)
    with _connection(connect) as conn:
        rows = conn.execute(
            """SELECT id,validation_run_id,manifest_sha256,status,selected_count,
                      shard_count,coverage_count,published_at
               FROM collector_qv_manifests
               WHERE project_id=%s ORDER BY published_at DESC,id DESC LIMIT %s""",
            (pid, size),
        ).fetchall()
    return {"schema_version": "collector.manifest_list/v1", "project_id": pid,
            "manifests": [
                {"manifest_id": str(r[0]), "validation_run_id": str(r[1]),
                 "manifest_sha256": r[2], "status": r[3],
                 "selected_count": r[4], "shard_count": r[5],
                 "coverage_count": r[6], "published_at": r[7].isoformat()}
                for r in rows], "external_calls": 0}

def _manifest(conn, pid: str, mid: str) -> tuple:
    row = conn.execute(
        """SELECT id,validation_run_id,generation_id,publication_id,taxonomy_version,
                  taxonomy_sha256,manifest_sha256,status,selected_count,
                  source_candidate_count,shard_count,coverage_count,published_at
           FROM collector_qv_manifests
           WHERE id=%s::uuid AND project_id=%s""", (mid, pid),
    ).fetchone()
    if row is None:
        raise CollectorError("manifest_not_found", "Project manifest not found", 404)
    return row

def _shards(conn, pid: str, mid: str) -> list[dict]:
    rows = conn.execute(
        """SELECT s.id,s.query_set_id,s.shard_ordinal,s.query_count,s.shard_sha256,
                  q.query_set_sha256,q.query_set
           FROM collector_query_shards s
           JOIN collector_query_sets q ON q.id=s.query_set_id
              AND q.project_id=s.project_id
              AND q.generation_id=s.generation_id
              AND q.publication_id=s.publication_id
           WHERE s.project_id=%s AND s.manifest_id=%s::uuid
           ORDER BY s.shard_ordinal""", (pid, mid),
    ).fetchall()
    result = []
    from .contracts import hash_object
    for sid, qid, ordinal, count, digest, qsha, raw in rows:
        if hash_object(raw) != qsha:
            raise CollectorError("query_set_integrity", "Shard stored hash changed", 409)
        parsed = parse_validated_query_set(raw, expected_project_id=pid)
        if len(parsed.queries) != count:
            raise CollectorError("manifest_integrity", "Published shard count mismatch", 409)
        result.append({"shard_id": str(sid), "query_set_id": str(qid),
                       "shard_ordinal": ordinal, "query_count": count,
                       "shard_sha256": digest, "query_set_sha256": qsha})
    return result

def get_published_manifest(project_id: str, manifest_id: str, *, connect=None) -> dict:
    pid, mid = _scope(project_id, manifest_id)
    with _connection(connect) as conn:
        with conn.transaction():
            row = _manifest(conn, pid, mid)
            shards = _shards(conn, pid, mid)
            cov = conn.execute(
                """SELECT hypothesis_id,source_count,selected_count,outcome,reason
                   FROM collector_manifest_coverage
                   WHERE project_id=%s AND manifest_id=%s::uuid ORDER BY hypothesis_id""",
                (pid, mid),
            ).fetchall()
            link = conn.execute(
                """SELECT COUNT(*),COUNT(DISTINCT candidate_id)
                   FROM collector_manifest_queries
                   WHERE project_id=%s AND manifest_id=%s::uuid""",
                (pid, mid),
            ).fetchone()
    if (len(shards) != row[10] or len(cov) != row[11]
        or link != (row[8], row[8])
        or sum(s["query_count"] for s in shards) != row[8]
        or sum(c[2] for c in cov) != row[8]
        or [x["shard_ordinal"] for x in shards] != list(range(len(shards)))):
        raise CollectorError("manifest_integrity", "Published QV ledger inconsistent", 409)
    return {
        "schema_version": "collector.manifest_detail/v1",
        "project_id": pid, "manifest_id": mid,
        "validation_run_id": str(row[1]), "generation_id": str(row[2]),
        "publication_id": str(row[3]), "taxonomy_version": row[4],
        "taxonomy_sha256": row[5], "manifest_sha256": row[6],
        "status": row[7], "selected_count": row[8],
        "source_candidate_count": row[9], "shard_count": row[10],
        "coverage_count": row[11], "published_at": row[12].isoformat(),
        "shards": shards,
        "coverage": [{"hypothesis_id":c[0], "source_count":c[1],
                      "selected_count":c[2], "outcome":c[3], "reason":c[4]}
                     for c in cov], "external_calls": 0,
    }

def _require_published_shard(conn, pid: str, mid: str, qid: str) -> None:
    """Pin immutable manifest and shard to expected project and QV source."""
    m = _manifest(conn, pid, mid)
    if m[7] != "published":
        raise CollectorError("no_queries", "No selected queries to plan", 409)
    row = conn.execute(
        """SELECT s.shard_sha256,q.query_set_sha256
           FROM collector_query_shards s
           JOIN collector_query_sets q ON q.id=s.query_set_id
             AND q.project_id=s.project_id
           WHERE s.manifest_id=%s::uuid AND s.project_id=%s
             AND s.query_set_id=%s::uuid AND s.generation_id=%s::uuid
             AND s.publication_id=%s::uuid
             AND q.validation_origin='trusted_query_validation/v1'""",
        (mid, pid, qid, str(m[2]), str(m[3])),
    ).fetchone()
    if row is None:
        raise CollectorError("query_set_not_in_manifest",
                             "Trusted QuerySet not in this project manifest", 404)
    for val in row:
        require_id(val, "shard_checksum", HASH_RE)

def preview_published_shard(request: dict, *, connect=None) -> dict:
    expected = {"project_id","manifest_id","query_set_id","source_id","limits"}
    if type(request) is not dict or set(request) != expected:
        raise CollectorError("invalid_preview", "A published manifest and QuerySet are required")
    pid, mid = _scope(request["project_id"], request["manifest_id"])
    qid = require_uuid(request["query_set_id"], "query_set_id")
    with _connection(connect) as conn:
        with conn.transaction():
            _require_published_shard(conn, pid, mid, qid)
    result = trusted_preview(qid, request["source_id"], request["limits"], connect=connect)
    if result["project_id"] != pid or result["trusted_query_set_id"] != qid:
        raise CollectorError("project_scope_mismatch", "Stored QuerySet changed scope", 409)
    result["manifest_id"] = mid
    result["preview_only"] = True
    result["external_calls"] = 0
    return result

def commit_published_shard(request: dict, *, connect=None) -> dict:
    expected = {"project_id","manifest_id","query_set_id","source_id","limits",
                "plan_hash","idempotency_key","confirm","max_spend_usd"}
    if type(request) is not dict or set(request) != expected:
        raise CollectorError("invalid_commit", "Published manifest and owner consent required")
    pid, mid = _scope(request["project_id"], request["manifest_id"])
    qid = require_uuid(request["query_set_id"], "query_set_id")
    raw = {k:v for k,v in request.items() if k not in ("project_id","manifest_id")}
    _validated_commit_request(raw)
    with _connection(connect) as conn:
        with conn.transaction():
            _require_published_shard(conn, pid, mid, qid)
    # commit_run owns its own SERIALIZABLE-ish project FOR UPDATE and rechecks
    # the immutable SQL query set, plan hash, consent and source quota.
    result = commit_run(raw, connect=connect)
    if result["project_id"] != pid:
        raise CollectorError("project_scope_mismatch", "Held run belongs to a different project", 409)
    result["manifest_id"] = mid
    result["query_set_id"] = qid
    return result
