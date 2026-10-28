"""VC-MOD-04: transactional, replay-safe publisher for production QV events.

A single outbox row lock serializes concurrent publishers. The transaction
persists immutable manifests, ALL <=50-query shard snapshots, every QV UUID
link and full hypothesis coverage BEFORE acknowledging the upstream outbox.
No worker, provider, HTTP endpoint, Search Planner rerank, or network calls.
"""
from __future__ import annotations

from dataclasses import dataclass


from intelligence.taxonomy_builder.pipeline_contracts import canonical, sha256
from intelligence.taxonomy_builder.pipeline_repository import _connect
from .contracts import CollectorError, PROJECT_RE, require_id, require_int, require_uuid
from .qv_reader import TrustedQVSource, read_trusted_qv, convert_trusted_qv
from .ports import runtime_flags
from .adapters.main_qv import MainQVAdapter

DEFAULT_SHARD_SIZE = 20
MAX_SHARD_SIZE = 50
MAX_SELECTED = 480


def _conflict(code: str, message: str) -> None:
    raise CollectorError(code, message, 409)


@dataclass(frozen=True, slots=True)
class QVShard:
    ordinal: int
    query_set: dict
    query_set_sha256: str
    shard_sha256: str
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QVPublicationPlan:
    manifest_sha256: str
    project_id: str
    outbox_id: str
    validation_run_id: str
    selected_count: int
    source_candidate_count: int
    coverage_count: int
    status: str
    shards: tuple[QVShard, ...]

    @property
    def shard_count(self) -> int:
        return len(self.shards)


def prepare_qv_publication(source: TrustedQVSource, *, shard_size: int = DEFAULT_SHARD_SIZE
                           ) -> QVPublicationPlan:
    """Pure offline canonical sharding; does not grant authorization from JSON."""
    if type(source) is not TrustedQVSource:
        _conflict("untrusted_qv_source", "Only a server-verified QV source may be published")
    source._assert_trusted()
    require_int(shard_size, "shard_size", 1, MAX_SHARD_SIZE)
    rows = source.selected
    if len(rows) > MAX_SELECTED or len(rows) != source.selected_count:
        _conflict("qv_source_changed", "Selected count exceeds QV bound")
    ordered = sorted(rows, key=lambda r: (r["hypothesis_id"],
                                             r["rank_in_hypothesis"],
                                             r["candidate_id"]))
    if len({x["candidate_id"] for x in ordered}) != len(ordered):
        _conflict("qv_source_changed", "Repeated selected UUID")
    shards = []
    for start in range(0, len(ordered), shard_size):
        subset = ordered[start:start + shard_size]
        ids = tuple(row["candidate_id"] for row in subset)
        data = convert_trusted_qv(source, candidate_ids=ids)
        qsha = sha256(data)
        ordinal = len(shards)
        shard_hash = sha256({
            "schema_version": "collector.qv_shard/v1", "project_id": source.project_id,
            "validation_run_id": source.validation_run_id,
            "ordinal": ordinal, "query_set_sha256": qsha,
            "source_bundle_sha256": source.bundle_sha256,
            "candidate_ids": ids,
        })
        shards.append(QVShard(ordinal, data, qsha, shard_hash, ids))
    state = "no_queries" if not ordered else "published"
    manifest_hash = sha256({
        "schema_version": "collector.qv_manifest/v1",
        "project_id": source.project_id,
        "outbox_id": source.outbox_id,
        "validation_run_id": source.validation_run_id,
        "generation_id": source.generation_id,
        "publication_id": source.publication_id,
        "taxonomy_version": source.taxonomy_version,
        "taxonomy_sha256": source.taxonomy_sha256,
        "source_bundle_sha256": source.source_bundle_sha256,
        "qv_bundle_sha256": source.bundle_sha256,
        "source_candidate_count": source.source_candidate_count,
        "selected_count": source.selected_count,
        "coverage": source.coverage,
        "decisions": source.decisions,
        "shards": [{"ordinal": x.ordinal, "hash": x.shard_sha256,
                    "query_set_sha256": x.query_set_sha256,
                    "candidate_ids": list(x.candidate_ids)} for x in shards],
    })
    return QVPublicationPlan(
        manifest_hash, source.project_id, source.outbox_id,
        source.validation_run_id, len(ordered), source.source_candidate_count,
        len(source.coverage), state, tuple(shards),
    )


def _verify_existing(conn, source: TrustedQVSource, plan: QVPublicationPlan,
                     existing: tuple) -> dict:
    """Return prior publication only if every immutable source/link still matches."""
    (manifest_id, manifest_sha, bundle_sha, state, selected, shards,
     coverage, generation_id, publication_id, taxonomy_sha, taxonomy_version) = existing
    if (manifest_sha != plan.manifest_sha256 or bundle_sha != source.bundle_sha256
        or state != plan.status or selected != plan.selected_count
        or shards != plan.shard_count or coverage != plan.coverage_count
        or str(generation_id) != source.generation_id
        or str(publication_id) != source.publication_id
        or taxonomy_sha != source.taxonomy_sha256
        or taxonomy_version != source.taxonomy_version):
        _conflict("qv_manifest_conflict", "Existing manifest differs from sealed source")
    mid = str(manifest_id)
    shard_rows = conn.execute(
        """SELECT s.query_set_id,s.shard_ordinal,s.query_count,s.shard_sha256,
                  q.query_set_sha256,q.query_set
           FROM collector_query_shards s JOIN collector_query_sets q
             ON q.id=s.query_set_id AND q.project_id=s.project_id
           WHERE s.manifest_id=%s::uuid AND s.project_id=%s
           ORDER BY s.shard_ordinal""", (mid, source.project_id),
    ).fetchall()
    if len(shard_rows) != len(plan.shards):
        _conflict("qv_manifest_incomplete", "Manifest shards are missing")
    shard_ids = []
    for row, planned in zip(shard_rows, plan.shards):
        qid, ordinal, count, digest, qsha, payload = row
        if (ordinal != planned.ordinal or count != len(planned.candidate_ids)
            or digest != planned.shard_sha256
            or qsha != planned.query_set_sha256
            or sha256(payload) != qsha):
            _conflict("qv_manifest_incomplete", "Stored shard fingerprint/count differs")
        shard_ids.append(str(qid))
    persisted = conn.execute(
        """SELECT COUNT(*),COUNT(DISTINCT candidate_id)
           FROM collector_manifest_queries WHERE manifest_id=%s::uuid""",
        (mid,),
    ).fetchone()
    coverage = conn.execute(
        """SELECT COUNT(*),COALESCE(SUM(selected_count),0)
           FROM collector_manifest_coverage WHERE manifest_id=%s::uuid""",
        (mid,),
    ).fetchone()
    if persisted != (plan.selected_count, plan.selected_count) or coverage != (
        plan.coverage_count, plan.selected_count,
    ):
        _conflict("qv_manifest_incomplete", "Stored QV candidate/coverage ledger differs")
    return {"manifest_id": mid, "project_id": source.project_id,
            "validation_run_id": source.validation_run_id,
            "status": plan.status, "selected_count": plan.selected_count,
            "shard_count": plan.shard_count, "query_set_ids": shard_ids,
            "manifest_sha256": plan.manifest_sha256,
            "reused": True, "acknowledged": True, "provider_calls": 0}


def _commit(conn, source: TrustedQVSource, plan: QVPublicationPlan) -> dict:
    """Must be called in a transaction; qv_outbox row lock is the race fence."""
    locked = conn.execute(
        """SELECT o.run_id,o.delivered_at,o.event_type,o.payload_pointer,
                  v.project_id,v.generation_id,v.embedding_mode,v.status,
                  v.selected_count
           FROM qv_outbox o JOIN qv_runs v ON v.id=o.run_id
           WHERE o.id=%s::uuid AND v.project_id=%s
           FOR UPDATE OF o""", (source.outbox_id, source.project_id),
    ).fetchone()
    if locked is None:
        raise CollectorError("qv_event_not_found", "QV source no longer exists", 404)
    run_id, delivered, event_type, pointer, owner, gid, mode, status, count = locked
    if (str(run_id) != source.validation_run_id or owner != source.project_id
        or str(gid) != source.generation_id or mode != "semantic_local"
        or status != "completed" or count != source.selected_count
        or event_type != "validated_queries_ready/v1"
        or pointer != {
            "validation_run_id": source.validation_run_id,
            "generation_id": source.generation_id,
            "schema_version": "validated_queries/v1",
            "embedding_mode": "semantic_local",
            "selected_count": source.selected_count,
        }):
        _conflict("qv_event_changed", "QV event has changed during publication")

    prior = conn.execute(
        """SELECT id,manifest_sha256,qv_bundle_sha256,status,
                  selected_count,shard_count,coverage_count,generation_id,
                  publication_id,taxonomy_sha256,taxonomy_version
           FROM collector_qv_manifests
           WHERE qv_outbox_id=%s::uuid AND project_id=%s""",
        (source.outbox_id, source.project_id),
    ).fetchone()
    if prior is not None:
        if delivered is None:
            _conflict("qv_manifest_incomplete", "Manifest exists but source not ACKed")
        return _verify_existing(conn, source, plan, prior)
    if delivered is not None:
        _conflict("qv_acked_without_manifest",
                  "Upstream event ACKed without durable Collector manifest")

    manifest_id = str(conn.execute(
        """INSERT INTO collector_qv_manifests
             (project_id,qv_outbox_id,validation_run_id,generation_id,
              publication_id,taxonomy_version,taxonomy_sha256,
              qv_bundle_sha256,manifest_sha256,embedding_mode,selected_count,
              source_candidate_count,shard_count,coverage_count,status)
           VALUES (%s,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,
                   'semantic_local',%s,%s,%s,%s,%s) RETURNING id""",
        (source.project_id, source.outbox_id, source.validation_run_id,
         source.generation_id, source.publication_id, source.taxonomy_version,
         source.taxonomy_sha256, source.bundle_sha256, plan.manifest_sha256,
         plan.selected_count, source.source_candidate_count, plan.shard_count,
         plan.coverage_count, plan.status),
    ).fetchone()[0])
    selected = {x["candidate_id"]: x for x in source.selected}
    queryset_ids = []
    for shard in plan.shards:
        qid = str(conn.execute(
            """INSERT INTO collector_query_sets
                 (project_id,generation_id,publication_id,validation_origin,
                  query_set,query_set_sha256)
               VALUES (%s,%s::uuid,%s::uuid,'trusted_query_validation/v1',
                       %s::jsonb,%s) RETURNING id""",
            (source.project_id, source.generation_id, source.publication_id,
             canonical(shard.query_set), shard.query_set_sha256),
        ).fetchone()[0])
        sid = str(conn.execute(
            """INSERT INTO collector_query_shards
                 (manifest_id,project_id,generation_id,publication_id,
                  query_set_id,shard_ordinal,query_count,shard_sha256)
               VALUES (%s::uuid,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s)
               RETURNING id""",
            (manifest_id, source.project_id, source.generation_id,
             source.publication_id, qid, shard.ordinal,
             len(shard.candidate_ids), shard.shard_sha256),
        ).fetchone()[0])
        queryset_ids.append(qid)
        for position, cid in enumerate(shard.candidate_ids):
            row = selected[cid]
            conn.execute(
                """INSERT INTO collector_manifest_queries
                     (manifest_id,project_id,validation_run_id,shard_id,
                      candidate_id,hypothesis_id,direction_id,
                      rank_in_hypothesis,ordinal_in_shard,source_payload)
                   VALUES (%s::uuid,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,
                           %s::jsonb)""",
                (manifest_id, source.project_id, source.validation_run_id,
                 sid, cid, row["hypothesis_id"], row["direction_id"],
                 row["rank_in_hypothesis"], position, canonical(row)),
            )
    for coverage in source.coverage:
        conn.execute(
            """INSERT INTO collector_manifest_coverage
                 (manifest_id,project_id,validation_run_id,hypothesis_id,
                  source_count,selected_count,outcome,reason)
               VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s)""",
            (manifest_id, source.project_id, source.validation_run_id,
             coverage["hypothesis_id"], coverage["source_count"],
             coverage["selected_count"], coverage["outcome"],
             coverage["reason"]),
        )
    ack = conn.execute(
        """UPDATE qv_outbox
           SET delivered_at=now(),delivery_attempts=delivery_attempts+1
           WHERE id=%s::uuid AND run_id=%s::uuid AND delivered_at IS NULL
           RETURNING id""",
        (source.outbox_id, source.validation_run_id),
    ).fetchone()
    if ack is None:
        _conflict("qv_ack_race", "Source ACK race; entire transaction rolled back")
    return {"manifest_id": manifest_id, "project_id": source.project_id,
            "validation_run_id": source.validation_run_id,
            "status": plan.status, "selected_count": plan.selected_count,
            "shard_count": plan.shard_count, "query_set_ids": queryset_ids,
            "manifest_sha256": plan.manifest_sha256,
            "reused": False, "acknowledged": True, "provider_calls": 0}


def publish_qv(project_id: str, outbox_id: str, *,
               shard_size: int = DEFAULT_SHARD_SIZE, connect=None) -> dict:
    """Publish one production QV event atomically; safe concurrent replay."""
    pid = require_id(project_id, "project_id", PROJECT_RE)
    oid = require_uuid(outbox_id, "outbox_id")
    require_int(shard_size, "shard_size", 1, MAX_SHARD_SIZE)
    source = read_trusted_qv(pid, oid, connect=connect, allow_acknowledged=True)
    plan = prepare_qv_publication(source, shard_size=shard_size)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            return _commit(conn, source, plan)


def publish_due_qv(project_id: str, *, limit: int = 20,
                   shard_size: int = DEFAULT_SHARD_SIZE, connect=None) -> dict:
    """Opt-in backend poll. Disabled by default; never triggers provider POST."""
    pid = require_id(project_id, "project_id", PROJECT_RE)
    require_int(limit, "limit", 1, 100)
    require_int(shard_size, "shard_size", 1, MAX_SHARD_SIZE)
    if not runtime_flags().qv_consumer_enabled:
        raise CollectorError("qv_consumer_disabled",
                             "Explicit QV consumer enablement required", 403)
    source = MainQVAdapter(connect=connect)
    notices = source.pending_validated_outbox(pid, limit=limit)
    results = []
    for notice in notices:
        oid = require_uuid(notice["outbox_id"], "outbox_id")
        results.append(publish_qv(pid, oid, shard_size=shard_size, connect=connect))
    return {"project_id": pid, "published": len(results),
            "manifests": results, "provider_calls": 0}
