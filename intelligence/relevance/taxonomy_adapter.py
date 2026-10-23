"""S2: read-only, PostgreSQL-authorized deep RTB -> SRE classifier adapter.

Only a complete, immutable human-reviewed RTB deep publication and its atomic
SRE mirror are accepted. In particular, no flat-only publication, mismatched
category UUID, broken fact citation or altered projection can reach inference.

All SQL lives here; taxonomy_mapping.py provides provider-free pure checks and
hypothesis_context.py produces deterministic, source-grounded classifier inputs.
No writes, API calls, search, LLM calls, caching or cost.
"""
from __future__ import annotations

from intelligence.taxonomy_builder.pipeline_repository import _connect
from intelligence.search_planner.contracts import publication_uuid
from intelligence.search_planner.input_loader import read_published_snapshot
from .v1_contracts import project_id, reject
from .taxonomy_mapping import verify_authorized_mirror
from .hypothesis_context import build_classifier_contexts


def read_authorized_taxonomy(conn, project, publication):
    """Caller must use a REPEATABLE READ READ ONLY transaction.

    This lower-level entry point is intentionally injectable for Pg and fake
    tests. Never expose it to untrusted HTTP callers with their own DB conn.
    """
    pid = project_id(project)
    pub = publication_uuid(publication)
    rt = read_published_snapshot(conn, pid, pub)
    # The join binds run/project/publication/SRE version. Historical approved
    # publications remain accessible; do not read a mutable "active" pointer.
    row = conn.execute(
        """SELECT l.project_id, l.run_id::text,
                  l.builder_publication_id::text, l.sre_taxonomy_version_id,
                  l.source_sha256, v.status, v.snapshot_checksum,
                  p.approved_topics, p.approved_by, v.approved_by,
                  d.quality_report
           FROM tb_sre_taxonomy_links l
           JOIN tb_taxonomy_publications p
             ON p.id=l.builder_publication_id AND p.project_id=l.project_id
            AND p.run_id=l.run_id
           JOIN tb_deep_publications d
             ON d.builder_publication_id=p.id AND d.project_id=l.project_id
            AND d.run_id=l.run_id
           JOIN sre_taxonomy_versions v
             ON v.id=l.sre_taxonomy_version_id AND v.project_id=l.project_id
           WHERE l.project_id=%s AND l.builder_publication_id=%s::uuid
             AND l.run_id=%s::uuid""",
        (pid, pub, rt["rtb_run_id"]),
    ).fetchone()
    if row is None:
        reject("publication", "RTB publication has no approved SRE projection",
               "snapshot_mismatch")
    if row[0] != pid or row[1] != rt["rtb_run_id"] or row[2] != pub:
        reject("publication", "project/run/publication link differs",
               "snapshot_mismatch")

    reviews = conn.execute(
        """SELECT review_no, approved_nodes, reviewed_by
           FROM tb_deep_reviews WHERE project_id=%s AND run_id=%s::uuid
           ORDER BY review_no""",
        (pid, rt["rtb_run_id"]),
    ).fetchall()
    categories = conn.execute(
        """SELECT category_key::text, name, relation_type, definition,
                  rules, related_fact_ids, review_status, origin, independent
           FROM sre_taxonomy_categories
           WHERE project_id=%s AND taxonomy_version_id=%s
           ORDER BY id""",
        (pid, row[3]),
    ).fetchall()
    facts = conn.execute(
        """SELECT f.id::text, f.kind, f.normalized_text, f.original_quote,
                  f.origin, f.review_status
           FROM sre_facts f WHERE f.project_id=%s AND
             f.id::text = ANY(%s::text[])
           ORDER BY f.id""",
        (pid, [str(f["id"]) for f in rt["approved_facts"]]),
    ).fetchall()
    fact_links = conn.execute(
        """SELECT c.category_key::text, l.fact_id::text
           FROM sre_category_fact_links l
           JOIN sre_taxonomy_categories c
             ON c.id=l.category_id AND c.project_id=l.project_id
           WHERE l.project_id=%s AND c.taxonomy_version_id=%s
           ORDER BY c.category_key, l.fact_id""",
        (pid, row[3]),
    ).fetchall()
    source = {
        "project_id": row[0], "rtb_run_id": row[1],
        "publication_id": row[2], "sre_taxonomy_version_id": row[3],
        "source_sha256": row[4], "sre_status": row[5],
        "sre_snapshot_checksum": row[6], "approved_topics": row[7],
        "publication_approved_by": row[8],
        "sre_approved_by": row[9], "quality_report": row[10],
        "reviews": reviews, "categories": categories,
        "sre_facts": facts, "fact_links": fact_links,
    }
    return verify_authorized_mirror(rt, source)


def load_approved_taxonomy(project, publication, *, connect=None):
    """The public S2 loader: consistent read-only snapshot, no provider access."""
    pid = project_id(project)
    pub = publication_uuid(publication)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return read_authorized_taxonomy(conn, pid, pub)


def load_classifier_contexts(project, publication, *, connect=None):
    """Read DB authorization and return immutable deterministic leaf contexts."""
    return build_classifier_contexts(
        load_approved_taxonomy(project, publication, connect=connect)
    )
