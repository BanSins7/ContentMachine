"""VC-MOD-03 trusted, read-only Search Planner QV source loader.

Only backend PostgreSQL evidence can create TrustedQVSource. Never accepts
client-submitted "accepted" JSON as an authorization, never ACKs qv_outbox,
never writes DB, creates jobs or calls a provider. VC-MOD-04 owns publishing.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from typing import Any

from intelligence.taxonomy_builder.pipeline_contracts import (
    PipelineError, canonical, sha256,
)
from intelligence.taxonomy_builder.pipeline_repository import _connect
from intelligence.search_planner.input_loader import read_published_snapshot
from intelligence.search_planner.generation_engine.contracts import validate_generation_config
from intelligence.search_planner.query_validation.contracts import (
    validate_policy, policy_fingerprint,
)
from .contracts import CollectorError, PROJECT_RE, require_id, require_uuid
from .ports import QvReadyV1, QvBundleShapeV1
from .query_intake import parse_validated_query_set

_TRUST_SEAL = object()


def _conflict(code: str, message: str) -> None:
    raise CollectorError(code, message, 409)


@dataclass(frozen=True, slots=True)
class TrustedQVSource:
    """DB-origin only source snapshot. Lists are carried as canonical JSON strings.

    A caller cannot mutate a selected list after a read to make a forged query
    appear approved. The opaque seal is checked again during conversion.
    """
    project_id: str
    outbox_id: str
    validation_run_id: str
    generation_id: str
    publication_id: str
    taxonomy_version: int
    taxonomy_sha256: str
    config_sha256: str
    source_bundle_sha256: str
    embedding_model_key: str
    source_candidate_count: int
    selected_count: int
    bundle_sha256: str
    _queries_json: str = field(repr=False)
    _coverage_json: str = field(repr=False)
    _decisions_json: str = field(repr=False)
    _source_seal: object = field(repr=False, compare=False)

    @property
    def selected(self) -> list[dict]:
        return json.loads(self._queries_json)

    @property
    def coverage(self) -> list[dict]:
        return json.loads(self._coverage_json)

    @property
    def decisions(self) -> list[dict]:
        return json.loads(self._decisions_json)

    def _assert_trusted(self) -> None:
        if self._source_seal is not _TRUST_SEAL:
            _conflict("untrusted_qv_source", "QV source was not verified in PostgreSQL")
        digest = sha256({
            "project_id": self.project_id, "outbox_id": self.outbox_id,
            "validation_run_id": self.validation_run_id,
            "generation_id": self.generation_id,
            "publication_id": self.publication_id,
            "taxonomy_version": self.taxonomy_version,
            "taxonomy_sha256": self.taxonomy_sha256,
            "config_sha256": self.config_sha256,
            "source_bundle_sha256": self.source_bundle_sha256,
            "embedding_model_key": self.embedding_model_key,
            "source_candidate_count": self.source_candidate_count,
            "queries": self.selected, "coverage": self.coverage,
            "decisions": self.decisions,
        })
        if digest != self.bundle_sha256 or len(self.selected) != self.selected_count:
            _conflict("qv_source_changed", "Verified Query Validation snapshot changed")


def _source_bundle(conn, pid: str, gid: str, generation: tuple) -> tuple[dict, dict, list]:
    """Rebuild EXACT sqg get_candidates_bundle on the SAME PG transaction.

    The upstream qv_runs.source_bundle_sha256 fingerprints this entire object.
    """
    source_rows = conn.execute(
        """SELECT c.id,c.hypothesis_id,c.direction_id,c.branch_id,
                  c.source_fact_ids,c.text,c.query_mode,c.angle,c.language,
                  c.research_stance,c.rationale
           FROM sqg_candidates c JOIN sqg_batches b ON b.id=c.batch_id
           WHERE c.generation_id=%s::uuid
           ORDER BY b.batch_index,c.raw_position""", (gid,),
    ).fetchall()
    sqg_coverage = conn.execute(
        """SELECT hypothesis_id,state,candidate_count,reason
           FROM sqg_hypothesis_coverage WHERE generation_id=%s::uuid
           ORDER BY hypothesis_id""", (gid,),
    ).fetchall()
    config = validate_generation_config(generation[9])
    candidates = [{
        "candidate_id": str(r[0]), "hypothesis_id": r[1],
        "direction_id": r[2], "branch_id": r[3], "source_fact_ids": r[4],
        "text": r[5], "query_mode": r[6], "angle": r[7], "language": r[8],
        "research_stance": r[9], "rationale": r[10],
    } for r in source_rows]
    coverage = [{
        "hypothesis_id": r[0], "state": r[1],
        "candidate_count": r[2], "reason": r[3],
    } for r in sqg_coverage]
    bundle = {
        "schema_version": "query_candidates/v1",
        "generation_id": gid, "project_id": pid,
        "taxonomy_publication_id": generation[6],
        "taxonomy_version": generation[7], "input_sha256": generation[8],
        "prompt_version": config["model"]["prompt_version"],
        "model_id": generation[10], "status": "completed",
        "candidates": candidates, "hypothesis_coverage": coverage,
    }
    by_id = {c["candidate_id"]: c for c in candidates}
    if len(by_id) != len(candidates):
        _conflict("qv_source_changed", "Repeated SQG candidate ID")
    return bundle, by_id, coverage


def _context_lineage(conn, gid: str, approved: dict, candidates: list[dict],
                     sqg_coverage: list[dict]) -> None:
    rows = conn.execute(
        """SELECT hypothesis_id,context,context_sha256 FROM sqg_contexts
           WHERE generation_id=%s::uuid ORDER BY hypothesis_id""",
        (gid,),
    ).fetchall()
    nodes = {n["id"]: n for n in approved["tree"]}
    approved_fact_ids = {f["id"] for f in approved["approved_facts"]}
    contexts = {}
    for hid, context, digest in rows:
        if (hid in contexts or type(context) is not dict
            or sha256(context) != digest
            or context.get("hypothesis", {}).get("id") != hid):
            _conflict("qv_source_changed", "SQG context checksum/identity mismatch")
        contexts[hid] = context
        hyp = nodes.get(hid)
        if hyp is None or hyp["kind"] != "hypothesis":
            _conflict("qv_taxonomy_lineage", "SQG hypothesis not in approved tree")
        expected_direction = context.get("direction", {}).get("id")
        expected_branch = (context.get("branch") or {}).get("id")
        current = hyp
        ancestors = []
        while current.get("parent_id") is not None:
            current = nodes.get(current["parent_id"])
            if current is None or current["id"] in ancestors:
                _conflict("qv_taxonomy_lineage", "Invalid approved RTB hierarchy")
            ancestors.append(current["id"])
        if current["id"] != expected_direction or current["kind"] != "direction":
            _conflict("qv_taxonomy_lineage", "SQG direction is not approved ancestor")
        if expected_branch is not None and (
            expected_branch not in ancestors
            or nodes[expected_branch]["kind"] != "branch"
        ):
            _conflict("qv_taxonomy_lineage", "Unapproved SQG branch")
        context_facts = context.get("approved_facts")
        if type(context_facts) is not list or any(
            not isinstance(f, dict) or f.get("id") not in approved_fact_ids
            for f in context_facts
        ):
            _conflict("qv_taxonomy_lineage", "Unknown SQG context fact")

    if not contexts or {r["hypothesis_id"] for r in sqg_coverage} != set(contexts):
        _conflict("qv_source_changed", "Incomplete SQG hypothesis coverage")
    for c in candidates:
        ctx = contexts.get(c["hypothesis_id"])
        if ctx is None:
            _conflict("qv_taxonomy_lineage", "Candidate has no approved SQG context")
        branch = (ctx.get("branch") or {}).get("id")
        if c["direction_id"] != ctx["direction"]["id"] or c["branch_id"] != branch:
            _conflict("qv_taxonomy_lineage", "Candidate direction/branch mismatch")
        facts = c["source_fact_ids"]
        if (type(facts) is not list
            or sorted(facts) != sorted(f["id"] for f in ctx["approved_facts"])):
            _conflict("qv_taxonomy_lineage", "Candidate source facts mismatch")


def _read(conn, pid: str, oid: str, *, allow_acknowledged: bool) -> TrustedQVSource:
    row = conn.execute(
        """SELECT o.run_id,o.event_type,o.payload_pointer,o.delivered_at,
                  v.generation_id,v.project_id,v.embedding_mode,v.status,
                  v.selected_count,v.source_candidate_count,v.config,
                  v.config_sha256,v.source_bundle_sha256,v.embedding_model_key,
                  g.status,g.project_id,g.publication_id,g.taxonomy_version,
                  g.taxonomy_sha256,g.rtb_run_id,g.input_sha256,g.config_snapshot,
                  g.model_id,s.id,s.event_type,s.payload_pointer,s.delivered_at
           FROM qv_outbox o
           JOIN qv_runs v ON v.id=o.run_id
           JOIN sqg_generations g ON g.id=v.generation_id AND g.project_id=v.project_id
           JOIN sqg_outbox s ON s.id=v.source_outbox_id AND s.generation_id=g.id
           WHERE o.id=%s::uuid AND v.project_id=%s""",
        (oid, pid),
    ).fetchone()
    if row is None:
        raise CollectorError("qv_event_not_found", "Project-scoped QV event not found", 404)
    (run_id, event_type, pointer, delivered, gid, owner, embedding_mode,
     state, selected_n, source_n, policy, policy_sha, source_sha, model_key,
     generation_state, source_owner, publication_id, tax_version, tax_sha,
     rtb_run_id, input_sha, generation_config, generation_model,
     source_event_id, source_event_type, source_pointer, source_delivered) = row
    rid, gid, publication_id, source_event_id = (
        str(run_id), str(gid), str(publication_id), str(source_event_id)
    )
    if (owner != pid or source_owner != pid or generation_state != "completed"
        or state != "completed" or embedding_mode != "semantic_local"):
        _conflict("qv_not_production", "Only completed production QV/SQG may be read")
    if delivered is not None and not allow_acknowledged:
        _conflict("qv_already_delivered", "QV event was already acknowledged")
    notice = QvReadyV1.parse(pid, {
        "outbox_id": oid, "validation_run_id": rid,
        "event_type": event_type, "payload_pointer": pointer,
    })
    if notice.generation_id != gid or notice.selected_count != selected_n:
        _conflict("qv_source_changed", "QV outbox pointer changed")
    if source_n < selected_n or type(source_n) is not int or source_n > 480:
        _conflict("qv_source_changed", "QV source counts invalid")
    if (source_event_type != "query_candidates_ready/v1"
        or source_delivered is None
        or type(source_pointer) is not dict
        or source_pointer.get("schema_version") != "query_candidates/v1"
        or source_pointer.get("generation_id") != gid
        or source_pointer.get("input_sha256") != input_sha):
        _conflict("qv_source_changed", "Completed SQG source event missing/mismatched")
    if (type(model_key) is not str or not 1 <= len(model_key) <= 240
        or policy_fingerprint(validate_policy(policy), model_key, embedding_mode) != policy_sha):
        _conflict("qv_config_changed", "QV policy/model fingerprint mismatch")
    approved = read_published_snapshot(conn, pid, publication_id)
    if (str(approved["rtb_run_id"]) != str(rtb_run_id)
        or approved["taxonomy_version"] != tax_version
        or approved["taxonomy_sha256"] != tax_sha):
        _conflict("qv_taxonomy_lineage", "Generation differs from approved C9 taxonomy")
    gen = (None, None, None, None, None, None, publication_id, tax_version,
           input_sha, generation_config, generation_model)
    source_bundle, source_map, sqg_coverage = _source_bundle(conn, pid, gid, gen)
    if len(source_map) != source_n or sha256(source_bundle) != source_sha:
        _conflict("qv_source_changed", "Completed SQG source bundle checksum mismatch")
    _context_lineage(conn, gid, approved, list(source_map.values()), sqg_coverage)

    decision_rows = conn.execute(
        """SELECT d.candidate_id,d.hypothesis_id,d.decision,d.reason_code,
                  d.duplicate_of,d.positive_similarity,d.negative_similarity,
                  d.score_margin,d.mmr_score,d.selected_rank
           FROM qv_decisions d WHERE d.run_id=%s::uuid
           ORDER BY d.candidate_id""", (rid,),
    ).fetchall()
    if len(decision_rows) != source_n:
        _conflict("qv_source_changed", "QV must decide every SQG candidate")
    selected = []
    decisions = []
    seen = set()
    for d in decision_rows:
        cid, hyp, decision, reason, dupe, pos, neg, margin, mmr, rank = d
        cid = str(cid)
        candidate = source_map.get(cid)
        if cid in seen or candidate is None or candidate["hypothesis_id"] != hyp:
            _conflict("qv_source_changed", "QV decision candidate/ancestry invalid")
        seen.add(cid)
        decisions.append({
            "candidate_id": cid, "decision": decision, "reason_code": reason,
            "duplicate_of": str(dupe) if dupe is not None else None,
            "positive_similarity": pos, "negative_similarity": neg,
            "score_margin": margin, "mmr_score": mmr,
        })
        if decision == "selected":
            if reason != "selected" or type(rank) is not int or not 1 <= rank <= 16:
                _conflict("qv_source_changed", "Selected query rank/reason invalid")
            selected.append({
                **candidate, "relevance_score": pos, "negative_score": neg,
                "mmr_score": mmr, "rank_in_hypothesis": rank,
            })
        elif decision != "rejected" or reason == "selected" or rank is not None:
            _conflict("qv_source_changed", "Unsupported QV decision")
    if len(selected) != selected_n or seen != set(source_map):
        _conflict("qv_source_changed", "Selected/total QV candidate counts differ")
    selected.sort(key=lambda c: (c["hypothesis_id"], c["rank_in_hypothesis"],
                                 c["candidate_id"]))
    coverage_rows = conn.execute(
        """SELECT hypothesis_id,source_count,selected_count,outcome,reason
           FROM qv_coverage WHERE run_id=%s::uuid ORDER BY hypothesis_id""",
        (rid,),
    ).fetchall()
    coverage = [{
        "hypothesis_id": c[0], "source_count": c[1],
        "selected_count": c[2], "outcome": c[3], "reason": c[4],
    } for c in coverage_rows]
    source_counts = {c["hypothesis_id"]: c["candidate_count"] for c in sqg_coverage}
    if len(coverage) != len(source_counts) or any(
        c["hypothesis_id"] not in source_counts
        or c["source_count"] != source_counts[c["hypothesis_id"]]
        for c in coverage
    ) or sum(c["selected_count"] for c in coverage) != selected_n:
        _conflict("qv_source_changed", "QV coverage/source counts mismatch")
    qv_bundle = {
        "schema_version": "validated_queries/v1",
        "validation_run_id": rid, "generation_id": gid,
        "project_id": pid, "embedding_model_key": model_key,
        "embedding_mode": embedding_mode, "config_sha256": policy_sha,
        "source_bundle_sha256": source_sha, "status": "completed",
        "queries": selected, "coverage": coverage, "decisions": decisions,
    }
    QvBundleShapeV1.parse(notice, qv_bundle)
    frozen = {
        "project_id": pid, "outbox_id": oid, "validation_run_id": rid,
        "generation_id": gid, "publication_id": publication_id,
        "taxonomy_version": tax_version, "taxonomy_sha256": tax_sha,
        "config_sha256": policy_sha, "source_bundle_sha256": source_sha,
        "embedding_model_key": model_key, "source_candidate_count": source_n,
        "queries": selected, "coverage": coverage, "decisions": decisions,
    }
    return TrustedQVSource(
        project_id=pid, outbox_id=oid, validation_run_id=rid, generation_id=gid,
        publication_id=publication_id, taxonomy_version=tax_version,
        taxonomy_sha256=tax_sha, config_sha256=policy_sha,
        source_bundle_sha256=source_sha, embedding_model_key=model_key,
        source_candidate_count=source_n, selected_count=len(selected),
        bundle_sha256=sha256(frozen),
        _queries_json=canonical(selected), _coverage_json=canonical(coverage),
        _decisions_json=canonical(decisions), _source_seal=_TRUST_SEAL,
    )


def read_trusted_qv(project_id: str, outbox_id: str, *, connect=None,
                    allow_acknowledged: bool = False) -> TrustedQVSource:
    """A single consistent *read-only* PostgreSQL snapshot with full lineage.

    'allow_acknowledged' is reserved for VC-MOD-04 publisher replay; it does
    not override any integrity/production checks. Not a public HTTP endpoint.
    """
    pid = require_id(project_id, "project_id", PROJECT_RE)
    oid = require_uuid(outbox_id, "outbox_id")
    if type(allow_acknowledged) is not bool:
        raise CollectorError("invalid_port_payload", "Invalid source replay option")
    factory = connect or _connect
    try:
        with factory() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return _read(conn, pid, oid, allow_acknowledged=allow_acknowledged)
    except PipelineError as exc:
        raise CollectorError("qv_source_not_verified", str(exc), 409) from exc


def convert_trusted_qv(source: TrustedQVSource, *, candidate_ids=None) -> dict:
    """Convert selected source IDs -> existing validated_query_set/v1 structure.

    Full 480-query input is NOT silently truncated to 50. Stage 04 chooses
    explicit shards <=50 and publishes them transactionally. All source
    metadata/scores remain in 'source.selected' for manifest provenance.
    """
    if type(source) is not TrustedQVSource:
        _conflict("untrusted_qv_source", "Only database-trusted QV source accepted")
    source._assert_trusted()
    queries = source.selected
    if candidate_ids is not None:
        if type(candidate_ids) not in (list, tuple):
            raise CollectorError("invalid_port_payload", "Explicit candidate IDs required")
        ids = list(candidate_ids)
        if not ids or len(ids) > 50 or any(type(x) is not str for x in ids):
            raise CollectorError("invalid_query_count", "One shard requires 1..50 IDs")
        if len(set(ids)) != len(ids):
            _conflict("duplicate_query_id", "Duplicate candidate in requested shard")
        selected = {q["candidate_id"]: q for q in queries}
        if any(qid not in selected for qid in ids):
            _conflict("query_not_accepted", "Requested candidate is not QV selected")
        queries = [selected[qid] for qid in ids]
    if not 1 <= len(queries) <= 50:
        raise CollectorError("invalid_query_count", "Collector shard requires 1..50 selected QV queries")
    raw = {
        "schema_version": "validated_query_set/v1",
        "project_id": source.project_id,
        "taxonomy_publication_id": source.publication_id,
        "taxonomy_version": source.taxonomy_version,
        "taxonomy_sha256": source.taxonomy_sha256,
        "generation_id": source.generation_id,
        "validation_version": "qv:" + source.validation_run_id,
        "queries": [{
            "query_id": q["candidate_id"], "hypothesis_id": q["hypothesis_id"],
            "direction_id": q["direction_id"], "mode": q["query_mode"],
            "text": q["text"], "language": q["language"],
            "validation_state": "accepted",
        } for q in queries],
    }
    parsed = parse_validated_query_set(
        raw, expected_project_id=source.project_id,
        expected_publication_id=source.publication_id,
    )
    return parsed.to_dict()
