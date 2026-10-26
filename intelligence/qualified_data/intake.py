"""FIX-01: authoritative, fail-closed LIVE SRE run intake.

No provider calls, no synthetic fixture fallback, no QDP writes. SRE controls
its execution_mode and published results; QDP checks its live schema before
querying. SRE's planned completion outbox/inbox and full manifest integrity
will be implemented in FIX-02/FIX-05, not silently simulated here.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping

from .contracts import QDPError, project_id

# A run flagged LIVE with an obvious mock/fixture model must not be accepted.
# This is a minimum rejection rule; validated model/evidence attestation and
# exhaustive result coverage are stronger FIX-02/SRE producer obligations.
_FAKE_MODEL = re.compile(r"(?:^|[/._:\s-])(fake|mock|fixture|offline|synthetic|test)(?:$|[/._:\s-])", re.I)
_LIVE_COLUMN = """
 SELECT COUNT(*)=2 AS available
   FROM pg_attribute
  WHERE NOT attisdropped AND
    ((attrelid=to_regclass('sre_classification_runs') AND attname='execution_mode')
      OR (attrelid=to_regclass('sre_content_versions') AND attname='content_sha256'))
"""


def _uuid(raw: str, what: str) -> str:
    try:
        value = str(uuid.UUID(str(raw)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise QDPError("invalid_id", "Invalid " + what) from exc
    if type(raw) is not str or raw != value:
        raise QDPError("invalid_id", "Noncanonical " + what)
    return value


def require_sre_live_schema(conn) -> None:
    """Fail with 503 while independent SRE's additive live migration is absent."""
    row = conn.execute(_LIVE_COLUMN).fetchone()
    if not row or row["available"] is not True:
        raise QDPError("sre_live_schema_unavailable",
                       "SRE live execution-mode contract is not installed", 503)


def _verify_models(models: object) -> None:
    if not isinstance(models, Mapping) or not models:
        raise QDPError("sre_model_unverified",
                       "LIVE SRE run has no verifiable resolved model manifest", 409)

    def visit(value):
        if isinstance(value, str):
            return bool(_FAKE_MODEL.search(value))
        if isinstance(value, Mapping):
            return any(visit(k) or visit(v) for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return any(visit(v) for v in value)
        return False

    if visit(models):
        raise QDPError("sre_synthetic_model",
                       "Synthetic or test model cannot supply production qualification", 409)


def read_live_sre_run(conn, project: str, sre_run_id: str, *, lock: bool = False) -> dict:
    """Read authoritative live run + approved project taxonomy; never trust client labels.

    A missing peer migration is *unavailable*, not a reason to fall back
    to the legacy completed rows. Call within a caller-owned transaction.
    """
    pid, rid = project_id(project), _uuid(sre_run_id, "SRE run ID")
    require_sre_live_schema(conn)
    query = """
        SELECT r.id,r.project_id,r.taxonomy_version_id,r.status,
               r.execution_mode,r.resolved_models,r.dataset_snapshot,
               t.status AS taxonomy_status
          FROM sre_classification_runs r
          JOIN sre_taxonomy_versions t
            ON t.id=r.taxonomy_version_id AND t.project_id=r.project_id
         WHERE r.project_id=%s AND r.id=%s::uuid
    """
    if lock:
        query += " FOR SHARE OF r"
    row = conn.execute(query, (pid, rid)).fetchone()
    if row is None:
        raise QDPError("sre_not_found", "SRE run not found in this project", 404)
    if row["status"] != "completed":
        raise QDPError("sre_not_completed", "QDP requires completed SRE classification", 409)
    if row["execution_mode"] != "live":
        raise QDPError("sre_not_live", "QDP requires a verified LIVE SRE run", 409)
    if row["taxonomy_status"] != "approved":
        raise QDPError("sre_not_approved", "SRE taxonomy must be approved", 409)
    _verify_models(row["resolved_models"])
    return dict(row)


def get_live_result_rows(conn, project: str, sre_run_id: str,
                         only_video_id: int | None = None, *,
                         verified_run: dict | None = None) -> list[dict]:
    """Return all DB-authored classification rows, not browser-supplied JSON.

    Deliberately preserve rows with unapproved categories/versions so they
    FAIL rather than disappear from a partial join and falsely imply no_match.
    Full expected manifest × leaf coverage is the FIX-02 responsibility.
    """
    pid, rid = project_id(project), _uuid(sre_run_id, "SRE run ID")
    run = verified_run or read_live_sre_run(conn, pid, rid)
    if (str(run.get("id")) != rid or run.get("project_id") != pid or
            run.get("execution_mode") != "live" or run.get("status") != "completed" or
            run.get("taxonomy_status") != "approved"):
        raise QDPError("sre_run_mismatch", "SRE run scope/mode changed", 409)
    if only_video_id is not None and (type(only_video_id) is not int or only_video_id < 1):
        raise QDPError("invalid_id", "Invalid video ID")
    rows = conn.execute(
        """SELECT result.id AS result_id, result.video_id,
                  result.category_key::text AS category_key,
                  result.taxonomy_version_id AS result_taxonomy_version_id,
                  result.content_version_id,
                  result.decision AS model_decision,
                  result.reason,result.evidence,result.stage_trace,
                  sc.content_sha256 AS result_content_sha256,
                  sc.content AS content_snapshot,
                  COALESCE(h.label,result.decision) AS effective_decision,
                  h.id AS human_label_id,h.label AS human_label_decision,
                  h.created_at AS review_created_at,
                  (cv.id IS NOT NULL) AS content_readable,
                  c.review_status AS category_review_status,
                  (pv.video_id IS NOT NULL) AS project_video_linked
             FROM sre_classification_results result
             JOIN sre_classification_runs sr
               ON sr.id=result.run_id AND sr.project_id=result.project_id
              AND sr.status='completed' AND sr.execution_mode='live'
             LEFT JOIN sre_content_versions sc
               ON sc.id=result.content_version_id AND sc.project_id=result.project_id
              AND sc.video_id=result.video_id
             LEFT JOIN sre_taxonomy_categories c
               ON c.project_id=result.project_id
              AND c.taxonomy_version_id=result.taxonomy_version_id
              AND c.category_key=result.category_key
             LEFT JOIN sre_project_videos pv
               ON pv.project_id=result.project_id AND pv.video_id=result.video_id
             LEFT JOIN sre_readable_content_versions cv
               ON cv.id=result.content_version_id AND cv.project_id=result.project_id
              AND cv.video_id=result.video_id
             LEFT JOIN LATERAL (
                SELECT h.id,h.label,h.created_at
                  FROM sre_human_labels h
                 WHERE h.project_id=result.project_id AND h.video_id=result.video_id
                   AND h.taxonomy_version_id=result.taxonomy_version_id
                   AND h.category_key=result.category_key AND h.superseded_at IS NULL
                 ORDER BY h.created_at DESC,h.id DESC LIMIT 1
             ) h ON TRUE
            WHERE result.run_id=%s::uuid AND result.project_id=%s
              AND (%s::bigint IS NULL OR result.video_id=%s::bigint)
            ORDER BY result.video_id,result.category_key""",
        (rid, pid, only_video_id, only_video_id)).fetchall()
    found = set()
    for row in rows:
        key = (row["video_id"], str(row["category_key"]))
        if key in found:
            raise QDPError("sre_duplicate_result", "Duplicate SRE video/category result", 409)
        found.add(key)
        if row["result_taxonomy_version_id"] != run["taxonomy_version_id"]:
            raise QDPError("sre_taxonomy_mismatch", "SRE result belongs to another taxonomy", 409)
        if row["category_review_status"] != "approved":
            raise QDPError("sre_unapproved_category", "Unapproved SRE leaf result", 409)
        if not row["project_video_linked"]:
            raise QDPError("sre_unlinked_video", "SRE result has no project video link", 409)
        if row["model_decision"] not in ("match", "no_match", "uncertain", "not_evaluated"):
            raise QDPError("sre_invalid_decision", "Invalid SRE model decision", 409)
    if not rows and only_video_id is None:
        # Zero matched rows is fine when real classified rows are no_match;
        # accepting truly empty run requires proof from expected manifest (FIX-02).
        raise QDPError("sre_empty", "Completed SRE run has no verified results", 409)
    return list(rows)


def verify_live_coverage(conn, run: dict, rows: list[dict],
                         *, only_video_id: int | None = None) -> dict:
    """Verify all expected leaf decisions against DB taxonomy + pinned SRE manifest."""
    from .coverage import validate_result_coverage
    categories = conn.execute(
        """SELECT category_key::text AS category_key,review_status
             FROM sre_taxonomy_categories
            WHERE project_id=%s AND taxonomy_version_id=%s
            ORDER BY category_key""",
        (run["project_id"], run["taxonomy_version_id"])).fetchall()
    return validate_result_coverage(run, rows, categories,
                                    only_video_id=only_video_id)


def read_eligible_sre_run_list(conn, project: str, limit: int = 30,
                               cursor: str | None = None) -> dict:
    """Read-only, stable keyset pagination for a future LIVE run picker (FIX-16)."""
    pid = project_id(project)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise QDPError("invalid_limit", "Expected limit 1..100")
    require_sre_live_schema(conn)
    after = None
    if cursor is not None:
        cid = _uuid(cursor, "cursor")
        after = conn.execute(
            """SELECT created_at,id FROM sre_classification_runs
                 WHERE id=%s::uuid AND project_id=%s
                   AND execution_mode='live' AND status='completed'""",
            (cid, pid)).fetchone()
        if after is None:
            raise QDPError("sre_cursor_not_found", "SRE cursor not found", 404)
    # Batch past invalid/unverified peer entries before forming the page.
    # Filtering only after SQL LIMIT can otherwise hide later valid LIVE runs.
    eligible = []
    batch_size = min(200, max(50, limit + 1))
    query = """
       SELECT r.id,r.taxonomy_version_id,r.created_at,r.finished_at,r.resolved_models
         FROM sre_classification_runs r
         JOIN sre_taxonomy_versions t
           ON t.id=r.taxonomy_version_id AND t.project_id=r.project_id
        WHERE r.project_id=%s AND r.status='completed'
          AND r.execution_mode='live' AND t.status='approved'
          AND r.resolved_models <> '{}'::jsonb
          AND (%s::timestamptz IS NULL OR
               (r.created_at,r.id) < (%s::timestamptz,%s::uuid))
        ORDER BY r.created_at DESC,r.id DESC LIMIT %s
    """
    while len(eligible) <= limit:
        pos = after["created_at"] if after else None
        position_id = after["id"] if after else None
        rows = conn.execute(query, (pid, pos, pos, position_id, batch_size)).fetchall()
        if not rows:
            break
        for row in rows:
            after = row
            try:
                _verify_models(row["resolved_models"])
            except QDPError:
                continue
            eligible.append({key: row[key] for key in
                             ("id", "taxonomy_version_id", "created_at", "finished_at")})
            if len(eligible) > limit:
                break
        if len(eligible) > limit or len(rows) < batch_size:
            break
    # Anchor at the last *returned* run, not at the end of the scanned batch.
    page = eligible[:limit]
    next_cursor = str(page[-1]["id"]) if len(eligible) > limit else None
    return {"runs": page, "next_cursor": next_cursor}
