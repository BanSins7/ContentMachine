"""Transactional, zero-network qualified analytics assembler.

One request = one project/video/source snapshot. Source permissions are verified
on EVERY request and the read-side view rechecks them again. Evidence is joined
to canonical videos, never copied into a competing video catalog.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from intelligence import config, migrate
from .contracts import (
    AnalyticsError, ENGINE_VERSION, canonical_hash, evaluate_qualification,
    validate_request, _json_value, _RIGHTS,
)
from .metrics import compute_c_metrics, MetricError

def _connect():
    if not config.dsn():
        raise AnalyticsError("database_unavailable", "PostgreSQL is not configured", 503)
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(config.dsn(), connect_timeout=5, row_factory=dict_row)

def _one(conn, sql, args):
    return conn.execute(sql, args).fetchone()

def _all(conn, sql, args):
    return conn.execute(sql, args).fetchall()

def _json(value):
    from psycopg.types.json import Jsonb
    return Jsonb(_json_value(value))

def _assert_rights(source: dict, policy: dict):
    permissions = policy.get("permissions") or {}
    if (source.get("status") != "enabled"
        or policy.get("is_active") is not True
        or policy.get("source_id") != source.get("id")
        or policy.get("reviewed_at") is None):
        raise AnalyticsError("source_policy_denied", "Active reviewed source policy required", 403)
    for right in _RIGHTS:
        if source.get(right) is not True or permissions.get(right) is not True:
            raise AnalyticsError("rights_denied", "Missing explicit source right: " + right, 403)

def _source(conn, source_id):
    if type(source_id) is not str or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,120}", source_id):
        raise AnalyticsError("invalid_source_id", "Invalid source")
    row = _one(conn, """
        SELECT s.id,s.status,s.can_store_metadata,s.can_analyze_media,
               s.can_store_metrics,s.can_derive_scores,s.retention_days,
               p.id AS policy_id,p.source_id AS policy_source_id,
               p.is_active,p.reviewed_at,p.permissions,p.retention_policy
        FROM sources s LEFT JOIN source_policy_versions p
          ON p.source_id=s.id AND p.is_active
        WHERE s.id=%s
        """, (source_id,))
    if row is None or row["policy_id"] is None:
        raise AnalyticsError("source_policy_denied", "Source policy not active", 403)
    source = {k: row[k] for k in ("id", "status", "can_store_metadata",
              "can_analyze_media", "can_store_metrics", "can_derive_scores",
              "retention_days")}
    policy = {"id": row["policy_id"], "source_id": row["policy_source_id"],
              "is_active": row["is_active"], "reviewed_at": row["reviewed_at"],
              "permissions": row["permissions"],
              "retention_policy":row["retention_policy"]}
    _assert_rights(source, policy)
    days=(policy["retention_policy"] or {}).get("derived_retention_days")
    if type(days) is not int or not 1<=days<=365:
        raise AnalyticsError("derived_retention_not_approved",
                             "Reviewed source policy must specify derived_retention_days 1..365",403)
    policy["derived_retention_days"]=min(days,source["retention_days"] or days)
    return source, policy

def _project_video(conn, project_id, video_id):
    row = _one(conn, """
        SELECT v.id,v.platform,v.creator_id,v.content_kind,v.platform_video_id
        FROM sre_project_videos link JOIN videos v ON v.id=link.video_id
        WHERE link.project_id=%s AND link.video_id=%s
        """, (project_id, video_id))
    if row is None:
        raise AnalyticsError("video_not_in_project", "Link video through SRE first", 404)
    return row

def _a_and_b(conn, video_id, source_id, policy_id, kind):
    if kind in ("video", "slideshow_video"):
        a_table = "video_feature_a_analyses"
        view = "video_feature_a_summary"
        fk = "video_a_analysis_id"
    elif kind in ("photo_carousel", "mixed_carousel"):
        a_table = "carousel_feature_a_analyses"
        view = "carousel_feature_a_summary"
        fk = "carousel_a_analysis_id"
    else:
        return {}, {}, {}, {}
    # Prefer the newest completed B bound to exactly the A observation; if B
    # absent, return latest A without claiming full qualification.
    b = _one(conn, f"""
        SELECT b.* FROM content_b_analyses b
        JOIN {a_table} a ON a.id=b.{fk}
        WHERE b.video_id=%s AND b.source_id=%s
          AND b.source_policy_version_id=%s
          AND b.state='completed' AND NOT b.needs_review
          AND a.video_id=b.video_id AND a.source_id=b.source_id
          AND a.source_policy_version_id=b.source_policy_version_id
          AND a.state='completed'
        ORDER BY b.analyzed_at DESC NULLS LAST,b.id DESC LIMIT 1
        """, (video_id, source_id, policy_id))
    if b:
        a = _one(conn, f"SELECT * FROM {a_table} WHERE id=%s", (b[fk],))
    else:
        a = _one(conn, f"""
            SELECT * FROM {a_table}
            WHERE video_id=%s AND source_id=%s AND source_policy_version_id=%s
              AND state='completed' ORDER BY measured_at DESC NULLS LAST,id DESC LIMIT 1
            """, (video_id, source_id, policy_id))
    if not a:
        return {}, {}, {}, {}
    summary = _one(conn, f"SELECT * FROM {view} WHERE analysis_id=%s", (a["id"],)) or {}
    a_data = dict(summary)
    a_data.update({"analysis_id": a["id"], "video_id": a["video_id"],
                   "source_id": a["source_id"], "state": a["state"],
                   "source_policy_version_id": a["source_policy_version_id"]})
    if kind in ("photo_carousel", "mixed_carousel"):
        a_data["content_kind"] = a["content_kind"]
        verified=_one(conn, """
            SELECT count(*) AS count,
                   count(*) FILTER(WHERE media.id IS NOT NULL
                       AND media.video_id=%s AND media.source_id=%s
                       AND media.permission<>'metadata_only'
                       AND media.sha256~'^[0-9a-f]{64}$'
                       AND (media.expires_at IS NULL OR media.expires_at>now())) AS good
            FROM carousel_feature_a_items i
            LEFT JOIN media_assets media ON media.id=i.media_asset_id
            WHERE i.analysis_id=%s
        """,(video_id,source_id,a["id"]))
        a_data["verified_media"]=(verified["count"]>=2 and verified["count"]==verified["good"])
    else:
        media=_one(conn, """
            SELECT id,expires_at FROM media_assets
            WHERE id=%s AND video_id=%s AND source_id=%s
              AND permission<>'metadata_only' AND sha256~'^[0-9a-f]{64}$'
              AND (expires_at IS NULL OR expires_at>now())
        """,(a["media_asset_id"],video_id,source_id)) if a["media_asset_id"] else None
        a_data["verified_media"]=media is not None
    a_payload = {k: v for k, v in summary.items()
                 if k not in ("media_asset_id", "source_id", "video_id")}
    a_payload["verified_media"]=a_data["verified_media"]
    if not b:
        return a_data, {}, a_payload, {}
    b_data = dict(b)
    b_payload = _b_payload(conn, b)
    return a_data, b_data, a_payload, b_payload

def _b_payload(conn, b):
    aid = b["id"]
    # All B1-B8 existing typed observations. No semantic inference here.
    parts = {}
    for key, table, fields in (
        ("hook", "content_b_hooks", "taxonomy_version,classification_status,mechanisms,delivery,spoken_text,screen_text,visual_description,is_question,has_number,direct_address,has_negation,is_command,start_sec,end_sec,slide_index"),
        ("format", "content_b_formats", "taxonomy_version,classification_status,video_primary,video_secondary,carousel_format"),
        ("visual", "content_b_visual_content", "ai_generated,person_visible,product_visible,logo_visible"),
        ("editing", "content_b_editing", "editing_type,cut_count,cuts_per_minute,avg_shot_duration,cuts_first_5_sec"),
        ("subtitles", "content_b_subtitles", "subtitle_type,position,font_size,font_style,text_color,highlight_color,background_style,display_mode"),
        ("cta_summary", "content_b_cta_summary", "analysis_status,has_cta,detected_occurrences"),
        ("topic", "content_b_topic_assignments", "category_id,topic_id,subtopic_id,taxonomy_version,needs_review"),
    ):
        row = _one(conn, f"SELECT {fields} FROM {table} WHERE analysis_id=%s", (aid,))
        parts[key] = row or None
    parts["hook_parts"] = _all(conn, """
        SELECT part_index,start_sec,end_sec,slide_index,verbatim_text,mechanisms
        FROM content_b_hook_parts WHERE analysis_id=%s ORDER BY part_index
        """, (aid,))
    parts["story"] = {
        "frames": _all(conn, """SELECT frame_index,time_sec,role,before_after_phase,needs_review
                    FROM content_b_story_frames WHERE analysis_id=%s ORDER BY frame_index""", (aid,)),
        "slides": _all(conn, """SELECT slide_index,role,before_after_phase,needs_review
                    FROM content_b_story_slides WHERE analysis_id=%s ORDER BY slide_index""", (aid,)),
        "intervals": _all(conn, """SELECT interval_index,start_sec,end_sec,role,before_after_phase,needs_review
                    FROM content_b_story_intervals WHERE analysis_id=%s ORDER BY interval_index""", (aid,))
    }
    parts["ctas"] = _all(conn, """
        SELECT occurrence_index,cta_type,text,spoken_text,screen_text,
               delivery,start_sec,end_sec,slide_index,destination_type,extraction_status
        FROM content_b_ctas WHERE analysis_id=%s ORDER BY occurrence_index
        """, (aid,))
    parts["analysis_version"] = b["analysis_version"]
    parts["taxonomy_version"] = b["taxonomy_version"]
    return parts

def _relevance(conn, project_id, video_id):
    # Human decisions supersede machine predictions for the same category.
    label = _one(conn, """
        SELECT h.id,h.label,h.category_key,h.taxonomy_version_id
        FROM sre_human_labels h JOIN sre_taxonomy_versions t
          ON t.id=h.taxonomy_version_id AND t.project_id=h.project_id
        JOIN sre_workspaces w ON w.project_id=h.project_id
                             AND w.active_taxonomy_id=h.taxonomy_version_id
        WHERE h.project_id=%s AND h.video_id=%s AND h.superseded_at IS NULL
          AND t.status='approved' AND h.label='match'
        ORDER BY h.created_at DESC,h.id DESC LIMIT 1
        """, (project_id, video_id))
    if label:
        return {"match": True, "label_id": label["id"], "result_id": None,
                "taxonomy_version_id": label["taxonomy_version_id"],
                "category_key": str(label["category_key"]), "origin": "human"}
    result = _one(conn, """
        SELECT r.id,r.category_key,r.taxonomy_version_id FROM sre_classification_results r
        JOIN sre_classification_runs cr ON cr.id=r.run_id AND cr.project_id=r.project_id
        JOIN sre_taxonomy_versions t ON t.id=r.taxonomy_version_id
                                  AND t.project_id=r.project_id
        JOIN sre_workspaces w ON w.project_id=r.project_id
                             AND w.active_taxonomy_id=r.taxonomy_version_id
        WHERE r.project_id=%s AND r.video_id=%s AND r.decision='match'
          AND cr.status='completed' AND t.status='approved'
          AND NOT EXISTS (SELECT 1 FROM sre_human_labels h
                 WHERE h.project_id=r.project_id AND h.video_id=r.video_id
                   AND h.taxonomy_version_id=r.taxonomy_version_id
                   AND h.category_key=r.category_key AND h.superseded_at IS NULL
                   AND h.label<>'match')
        ORDER BY r.created_at DESC,r.id DESC LIMIT 1
        """, (project_id,video_id))
    if result:
        return {"match": True, "label_id": None, "result_id": result["id"],
                "taxonomy_version_id": result["taxonomy_version_id"],
                "category_key": str(result["category_key"]), "origin": "sre"}
    return {"match": False}

def _metrics(conn, project_id, video, source_id, policy_id, content_type):
    current = _one(conn, """
        SELECT * FROM metric_snapshots
        WHERE video_id=%s AND source_id=%s AND source_policy_version_id=%s
          AND data_quality_status='ok'
          AND (expires_at IS NULL OR expires_at>now())
        ORDER BY observed_at DESC,id DESC LIMIT 1
        """, (video["id"],source_id,policy_id))
    if not current:
        return {}, {}, {}, {"reason": "no_valid_metric_snapshot"}
    # We neither rewrite nor guess externally collected timestamps.
    current["valid_retention"] = True
    config = _one(conn, """
      SELECT config.*,approval.min_creator_posts,approval.min_prior_posts,
             approval.min_niche_posts FROM metric_calculation_configs config
      JOIN analytics_config_approvals approval ON approval.config_id=config.id
      WHERE config.is_active=TRUE AND approval.is_active=TRUE
        AND approval.project_id=%s AND approval.source_id=%s
    """,(project_id,source_id))
    if not config:
        return current, {}, {}, {"reason": "missing_approved_c_config"}
    previous = _one(conn, """
        SELECT * FROM metric_snapshots
        WHERE video_id=%s AND source_id=%s AND source_policy_version_id=%s
          AND source_metric_definition=%s AND source_metric_version=%s
          AND observation_time_assumed=FALSE AND data_quality_status='ok'
          AND observed_at<%s AND (expires_at IS NULL OR expires_at>now())
        ORDER BY observed_at DESC,id DESC LIMIT 1
        """, (video["id"],source_id,policy_id,current["source_metric_definition"],
                 current["source_metric_version"],current["observed_at"]))
    metadata = _one(conn, """
        SELECT * FROM source_video_metadata
        WHERE video_id=%s AND source_id=%s AND source_policy_version_id=%s
          AND published_at IS NOT NULL AND published_at<=%s
          AND fetched_at<=%s AND deleted_at IS NULL
          AND (expires_at IS NULL OR expires_at>now())
        ORDER BY fetched_at DESC,id DESC LIMIT 1
        """, (video["id"],source_id,policy_id,current["observed_at"],current["observed_at"]))
    common=(video["platform"],content_type,source_id,current["source_metric_definition"],current["observed_at"])
    creator = _one(conn, """
        SELECT * FROM comparison_cohorts c WHERE c.purpose='creator'
          AND c.creator_id=%s AND c.platform=%s AND c.content_type=%s
          AND c.source_id=%s AND c.source_metric_definition=%s
          AND c.evaluated_as_of<=%s AND c.is_frozen
          AND c.post_count>=%s AND c.median_views IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM comparison_cohort_members m
                       WHERE m.cohort_id=c.id AND m.comparison_post_id=%s)
          AND analytics_cohort_healthy(%s,%s,c.id,%s,%s,%s,%s)
        ORDER BY c.evaluated_as_of DESC,c.id DESC LIMIT 1
        """, (video["creator_id"],*common,config["min_creator_posts"],video["id"],
              project_id,source_id,current["source_metric_definition"],current["source_metric_version"],
              config["min_creator_posts"],current["observed_at"])) if video["creator_id"] else None
    prior = _one(conn, """
        SELECT * FROM comparison_cohorts c WHERE c.purpose='engagement_prior'
          AND c.platform=%s AND c.content_type=%s AND c.source_id=%s
          AND c.source_metric_definition=%s AND c.evaluated_as_of<=%s
          AND c.is_frozen AND c.post_count>=%s
          AND c.prior_like_rate IS NOT NULL AND c.prior_comment_rate IS NOT NULL
          AND c.prior_share_rate IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM comparison_cohort_members m
                       WHERE m.cohort_id=c.id AND m.comparison_post_id=%s)
          AND analytics_cohort_healthy(%s,%s,c.id,%s,%s,%s,%s)
        ORDER BY c.evaluated_as_of DESC,c.id DESC LIMIT 1
        """, (*common,config["min_prior_posts"],video["id"],
              project_id,source_id,current["source_metric_definition"],current["source_metric_version"],
              config["min_prior_posts"],current["observed_at"]))
    topic = _one(conn, """
        SELECT topic_id FROM video_topics WHERE video_id=%s AND relevance_state='relevant'
        ORDER BY labeled_at DESC,topic_id LIMIT 1
        """, (video["id"],))
    niche = _one(conn, """
        SELECT * FROM comparison_cohorts c WHERE c.purpose='niche'
          AND c.topic_id=%s AND c.platform=%s AND c.content_type=%s
          AND c.source_id=%s AND c.source_metric_definition=%s
          AND c.evaluated_as_of<=%s AND c.is_frozen AND c.post_count>=%s
          AND c.median_views IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM comparison_cohort_members m
                       WHERE m.cohort_id=c.id AND m.comparison_post_id=%s)
          AND analytics_cohort_healthy(%s,%s,c.id,%s,%s,%s,%s)
        ORDER BY c.evaluated_as_of DESC,c.id DESC LIMIT 1
        """, (topic["topic_id"],*common,config["min_niche_posts"],video["id"],
              project_id,source_id,current["source_metric_definition"],current["source_metric_version"],
              config["min_niche_posts"],current["observed_at"])) if topic else None
    if not (previous and metadata and creator and prior):
        absent = [name for name,value in (("previous",previous),("publication",metadata),
                 ("creator_cohort",creator),("prior_cohort",prior)) if value is None]
        return current, {}, {}, {"reason": "missing_c_evidence", "missing": absent}
    try:
        values = compute_c_metrics(current,previous,metadata,config,creator,prior,niche)
    except MetricError as err:
        return current, {}, {}, {"reason": err.code}
    return current, values, {
        "config_id": config["id"], "previous_snapshot_id": previous["id"],
        "published_metadata_id": metadata["id"],
        "creator_cohort_id": creator["id"], "niche_cohort_id": niche["id"] if niche else None,
        "prior_cohort_id": prior["id"],
    }, {}

def _store_c(conn, video_id, snapshot_id, values, lineage):
    fields = ["post_id","snapshot_id","calculation_config_id","previous_snapshot_id",
              "published_metadata_id","creator_cohort_id","niche_cohort_id","prior_cohort_id"]
    vals = [video_id,snapshot_id,lineage["config_id"],lineage["previous_snapshot_id"],
            lineage["published_metadata_id"],lineage["creator_cohort_id"],
            lineage["niche_cohort_id"],lineage["prior_cohort_id"]]
    fields += list(values)
    vals += list(values.values())
    placeholders = ",".join(["%s"] * len(vals))
    row = _one(conn, "INSERT INTO calculated_metrics (" + ",".join(fields) +
       ") VALUES (" + placeholders + ") ON CONFLICT ON CONSTRAINT uq_c_calculation_lineage DO NOTHING RETURNING id",
       tuple(vals))
    if row:
        return row["id"]
    existing = _one(conn, """
        SELECT * FROM calculated_metrics WHERE snapshot_id=%s AND calculation_config_id=%s
          AND previous_snapshot_id IS NOT DISTINCT FROM %s
          AND creator_cohort_id IS NOT DISTINCT FROM %s
          AND niche_cohort_id IS NOT DISTINCT FROM %s
          AND prior_cohort_id IS NOT DISTINCT FROM %s
          AND published_metadata_id IS NOT DISTINCT FROM %s AND score_status='complete'
        """, (snapshot_id,lineage["config_id"],lineage["previous_snapshot_id"],
              lineage["creator_cohort_id"],lineage["niche_cohort_id"],lineage["prior_cohort_id"],
              lineage["published_metadata_id"]))
    if not existing:
        raise AnalyticsError("c_lineage_conflict", "Existing C result is not complete", 409)
    # Never return a cached C identifier for numeric values that differ from
    # persisted arithmetic. Otherwise JSON C7 and SQL aggregate C7 diverge.
    for field,value in values.items():
        if existing[field] is None or str(existing[field]) != str(value):
            if existing[field] is None and value is None:
                continue
            raise AnalyticsError("c_value_conflict",
                "Persisted C calculation does not match the requested evidence",409)
    return existing["id"]

def run_qualification(project_id: str, video_id: int, source_id: str, request_key: str, *, connect=None) -> dict:
    validate_request(project_id, video_id, request_key)
    factory = connect or _connect
    with factory() as conn, conn.transaction():
        # Source permissions are checked even when the idempotency key exists.
        source,policy = _source(conn,source_id)
        video = _project_video(conn,project_id,video_id)
        # A single project/request_key is one immutable idempotency operation,
        # including concurrent attempts for DIFFERENT video IDs.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))",
                     (project_id,request_key))
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s),hashint8(%s::bigint))",
                     (project_id,video_id))
        prior = _one(conn, "SELECT * FROM analytics_unified_snapshots WHERE project_id=%s AND request_key=%s",
                     (project_id,request_key))
        if prior:
            if prior["video_id"] != video_id or prior["source_id"] != source_id:
                raise AnalyticsError("idempotency_conflict", "Request key bound to a different input", 409)
            visible=_one(conn, "SELECT * FROM analytics_visible_snapshots WHERE id=%s AND project_id=%s",
                         (prior["id"],project_id))
            if visible is None:
                raise AnalyticsError("snapshot_unreadable",
                    "Cached snapshot is no longer readable under current policy, evidence or retention",403)
            return _public(visible)
        kind = video["content_kind"]
        a,b,features_a,features_b = _a_and_b(conn,video_id,source_id,policy["id"],kind)
        relevant = _relevance(conn,project_id,video_id)
        typ = "video" if kind in ("video","slideshow_video") else "carousel"
        metric,c_values,c_lineage,c_blockers = _metrics(conn,project_id,video,source_id,policy["id"],typ)
        c_id = _store_c(conn,video_id,metric["id"],c_values,c_lineage) if c_values else None
        c = ({**c_values,"post_id":video_id,"snapshot_id":metric["id"]} if c_id else {})
        e = {"source":source,"policy":policy,"project_link":True,
             "video_id":video_id,"content_kind":kind,
             "a":a,"b":b,"features_b":features_b,"c":c,"metric":metric,"relevance":relevant}
        decision = evaluate_qualification(e)
        if c_blockers:
            decision["blockers"].append("c_" + c_blockers["reason"])
            decision["readiness"]["c"] = False
            if decision["status"]=="qualified":
                decision["status"]="partial"
        lineage = {
            "source_policy_version_id":policy["id"],
            "a_analysis_id":a.get("analysis_id"),"b_analysis_id":b.get("id"),
            "metric_snapshot_id":metric.get("id"),"c_calculated_metric_id":c_id,
            "c_input_ids":c_lineage,"c_unavailable":c_blockers,
            "relevance_origin":relevant.get("origin"),
            "relevance_taxonomy_version_id":relevant.get("taxonomy_version_id"),
            "relevance_category_key":relevant.get("category_key"),
        }
        payload = {"project_id":project_id,"video_id":video_id,"source_id":source_id,
                   "kind":kind,"engine_version":ENGINE_VERSION,
                   "status":decision["status"],"readiness":decision["readiness"],
                   "blockers":decision["blockers"],"features_a":features_a,
                   "features_b":features_b,
                   "metrics_c":c,"lineage":lineage}
        fingerprint = canonical_hash(payload)
        row = _one(conn, """
            INSERT INTO analytics_unified_snapshots (
              project_id,video_id,source_id,source_policy_version_id,request_key,
              engine_version,content_kind,status,video_a_analysis_id,
              carousel_a_analysis_id,b_analysis_id,metric_snapshot_id,
              calculated_metric_id,relevance_label_id,relevance_result_id,
              readiness,blockers,features_a,features_b,metrics_c,lineage,
              fingerprint,derived_expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,
                    now() + %s * INTERVAL '1 day')
            ON CONFLICT (project_id,fingerprint) DO NOTHING RETURNING *
        """, (project_id,video_id,source_id,policy["id"],request_key,ENGINE_VERSION,
              kind,decision["status"],a.get("analysis_id") if typ=="video" else None,
              a.get("analysis_id") if typ=="carousel" else None,b.get("id"),
              metric.get("id"),c_id,relevant.get("label_id"),relevant.get("result_id"),
              _json(decision["readiness"]),_json(decision["blockers"]),
              _json(features_a),_json(features_b),_json(c),_json(lineage),fingerprint,
              policy["derived_retention_days"]))
        if not row:
            row = _one(conn, "SELECT * FROM analytics_unified_snapshots WHERE project_id=%s AND fingerprint=%s",
                       (project_id,fingerprint))
        return _public(row)

def _public(row):
    return {key: _json_value(row[key]) for key in (
        "id","project_id","video_id","source_id","request_key","engine_version",
        "content_kind","status","readiness","blockers","features_a",
        "features_b","metrics_c","lineage","fingerprint")}

def list_unified(project_id: str, *, limit=30, qualified_only=True, connect=None) -> list[dict]:
    if type(project_id) is not str or not re.fullmatch(r"[0-9a-f]{32}",project_id):
        raise AnalyticsError("invalid_project", "Invalid project id")
    if type(limit) is not int or not 1<=limit<=100:
        raise AnalyticsError("invalid_limit", "Limit must be 1..100")
    factory=connect or _connect
    with factory() as conn:
        rows=_all(conn, """
          SELECT * FROM analytics_visible_snapshots WHERE project_id=%s
            AND (%s=FALSE OR status='qualified')
          ORDER BY created_at DESC,id DESC LIMIT %s
        """, (project_id,qualified_only,limit))
        return [_public(row) for row in rows]
