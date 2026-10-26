"""Only immutable, fully approved hierarchical RTB publications may enter Search Planner.

No writes, no LLMs, no network search, no costs. One PostgreSQL consistent
read joins the *same project/run* across publications, reviews, facts and profile.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _pid, _connect
from .contracts import publication_uuid, validate_published_snapshot


def read_published_snapshot(conn, project_id, publication_id):
    """Read one authoritative DB row, cryptographically pinned by stored tree.

    Both reviews must come from the SAME project/run, and the published tree
    must exactly equal the immutable final (review #2) approved_nodes JSONB.
    A flat B9->C publication without a deep publication can never enter SP1.
    Injectable connection supports explicit offline/test read assertions.
    """
    pid = _pid(project_id)
    pub = publication_uuid(publication_id)
    row = conn.execute(
        """SELECT d.project_id, d.run_id, d.builder_publication_id,
                  p.taxonomy_version, d.tree, f.approved_facts,
                  r.profile_snapshot, r.status, dr.status,
                  ARRAY(SELECT rv.review_no FROM tb_deep_reviews rv
                        WHERE rv.project_id=d.project_id AND rv.run_id=d.run_id
                        ORDER BY rv.review_no) AS human_reviews
           FROM tb_deep_publications d
           JOIN tb_taxonomy_publications p
             ON p.id=d.builder_publication_id
            AND p.project_id=d.project_id AND p.run_id=d.run_id
           JOIN tb_runs r
             ON r.id=d.run_id AND r.project_id=d.project_id
           JOIN tb_deep_runs dr
             ON dr.run_id=d.run_id AND dr.project_id=d.project_id
           JOIN tb_fact_review_submissions f
             ON f.run_id=d.run_id AND f.project_id=d.project_id
           WHERE d.project_id=%s AND d.builder_publication_id=%s::uuid
             AND EXISTS (
                 SELECT 1 FROM tb_deep_reviews final_review
                 WHERE final_review.project_id=d.project_id
                   AND final_review.run_id=d.run_id
                   AND final_review.review_no=2
                   AND final_review.approved_nodes=d.tree
             )
             AND EXISTS (
                 SELECT 1 FROM tb_deep_reviews first_review
                 WHERE first_review.project_id=d.project_id
                   AND first_review.run_id=d.run_id
                   AND first_review.review_no=1
             )""",
        (pid, pub),
    ).fetchone()
    if row is None:
        # Flat RTB publications have no deep row, and are not a substitute.
        raise PipelineError("Approved hierarchical RTB publication not found", 404)
    # Defense in depth even if the DB adapter is replaced by a test stub.
    if row[0] != pid or str(row[2]) != pub:
        raise PipelineError("Publication does not belong to requested project", 404)
    snapshot = {
        "project_id": row[0],
        "rtb_run_id": str(row[1]),
        "taxonomy_publication_id": str(row[2]),
        "taxonomy_version": row[3],
        "tree": row[4],
        "approved_facts": row[5],
        "profile_snapshot": row[6],
        "run_status": row[7],
        "deep_status": row[8],
        "human_reviews": list(row[9]),
    }
    # The second defense prevents an unapproved DB row being treated as approved.
    return validate_published_snapshot(snapshot)


def load_approved_publication(project_id, publication_id, *, connect=None):
    """A single repeatable-read, read-only transaction; zero provider calls.

    connect may be injected by offline/integration tests. Never expose a
    caller-controlled conn from HTTP input. The identifier is the immutable
    tb_taxonomy_publications.id, not a speculative RTB run ID.
    """
    _pid(project_id)
    publication_uuid(publication_id)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return read_published_snapshot(conn, project_id, publication_id)


def list_approved_publications(project_id, *, limit=30, connect=None):
    """SP-13 list only genuinely reviewed Deep C9 publications; no writes.

    Do not mistake a flat tb_taxonomy_publications row for SP-ready content.
    Limit at SQL and return a minimal cursor-free latest-first selection.
    Each row is still revalidated by the exact trusted reader before use.
    """
    pid = _pid(project_id)
    if type(limit) is not int or not 1<=limit<=50:
        raise PipelineError("Invalid Search Planner publication page size",422)
    factory=connect if connect is not None else _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows=conn.execute(
                """SELECT d.builder_publication_id,p.taxonomy_version,d.published_at
                     FROM tb_deep_publications d
                     JOIN tb_taxonomy_publications p
                       ON p.id=d.builder_publication_id
                      AND p.project_id=d.project_id AND p.run_id=d.run_id
                     JOIN tb_deep_runs dr
                       ON dr.run_id=d.run_id AND dr.project_id=d.project_id
                    WHERE d.project_id=%s
                      AND dr.status='approved'
                      AND EXISTS (
                         SELECT 1 FROM tb_deep_reviews rv
                          WHERE rv.project_id=d.project_id
                            AND rv.run_id=d.run_id AND rv.review_no=1
                      )
                      AND EXISTS (
                         SELECT 1 FROM tb_deep_reviews rv
                          WHERE rv.project_id=d.project_id
                            AND rv.run_id=d.run_id AND rv.review_no=2
                            AND rv.approved_nodes=d.tree
                      )
                    ORDER BY d.published_at DESC,d.builder_publication_id
                    LIMIT %s""",
                (pid,limit),
            ).fetchall()
            result=[]
            for pub,version,created in rows:
                approved=read_published_snapshot(conn,pid,str(pub))
                if approved["taxonomy_version"]!=version:
                    raise PipelineError("C9 publication changed during listing",409)
                result.append({
                    "taxonomy_publication_id":str(pub),
                    "taxonomy_version":version,
                    "taxonomy_sha256":approved["taxonomy_sha256"],
                    "approved_hypothesis_count":sum(
                        node["kind"]=="hypothesis" for node in approved["tree"]),
                    "approved_at":created.isoformat() if created is not None else None,
                    "source_status":"approved_deep_c9",
                })
    return {
        "schema_version":"search-planner-approved-publications/v1",
        "project_id":pid,"publications":result,
        "count":len(result),"provider_requests_in_this_read":0,
    }
