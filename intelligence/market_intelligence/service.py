"""Independent MI analytics: reuse project references and canonical D1 only.

Network-free research execution. Discovery URL ingestion is an existing local
queued job. Author crawling and provider metric acquisition are NOT implied.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from intelligence import config
from intelligence.discovery import service as discovery
from intelligence.project_sources.repository import (
    RegistryProblem, _project, create_source, list_sources,
)
from .contracts import MarketError, digest, summarize_market, validate_seeds, verified_rtb_context
from .profile_jobs import queue_profiles, list_jobs
from .media_worker import queue_available, get_status as media_status
from .performance import enrich

SQL_VIDEO_EVIDENCE = """
SELECT v.id,v.canonical_url,v.platform,
  COALESCE(c.platform_creator_id,miprofile.profile_url) AS creator_key,
  v.published_at, a.id AS mi_analysis_id,a.technical,a.creative
FROM videos v
LEFT JOIN creators c ON c.id=v.creator_id
LEFT JOIN LATERAL(
 SELECT profile_url FROM mi_author_publications mp
 WHERE mp.project_id=%s AND mp.video_id=v.id
   AND EXISTS(SELECT 1 FROM project_sources ps
      WHERE ps.project_id=mp.project_id AND ps.canonical_url=mp.profile_url
        AND ps.tombstoned_at IS NULL AND ps.link_kind='account')
 ORDER BY mp.profile_url LIMIT 1
) miprofile ON TRUE
LEFT JOIN LATERAL(
 SELECT ma.id,ma.technical,ma.creative FROM mi_video_analyses ma
 JOIN project_sources ps ON ps.project_id=ma.project_id AND ps.id=ma.source_id
     AND ps.tombstoned_at IS NULL AND ps.can_analyze AND ps.can_store
 JOIN project_media_assets pa ON pa.project_id=ma.project_id AND pa.id=ma.media_asset_id
     AND pa.content_sha256=ma.media_sha256
 WHERE ma.project_id=%s AND ma.video_id=v.id
 ORDER BY ma.created_at DESC,ma.id DESC LIMIT 1
) a ON TRUE
WHERE (
 EXISTS (SELECT 1 FROM project_sources ps
     WHERE ps.project_id=%s AND ps.tombstoned_at IS NULL
       AND ps.source_type='user_video_reference' AND ps.link_kind='video'
       AND ps.platform=v.platform AND ps.platform_native_id=v.platform_video_id)
 OR EXISTS (SELECT 1 FROM project_sources ps
     WHERE ps.project_id=%s AND ps.tombstoned_at IS NULL
       AND ps.source_type IN ('competitor_account','promoter_account')
       AND ps.link_kind='account' AND ps.platform=v.platform
       AND c.canonical_url=ps.canonical_url)
 OR EXISTS (SELECT 1 FROM mi_author_publications mp
     WHERE mp.project_id=%s AND mp.video_id=v.id
       AND EXISTS (SELECT 1 FROM project_sources ps
          WHERE ps.project_id=mp.project_id AND ps.canonical_url=mp.profile_url
            AND ps.tombstoned_at IS NULL AND ps.link_kind='account'))
)
ORDER BY v.id LIMIT 2001
"""

# Only canonical D1 snapshots; no Group C calculations, materialized scores,
# or Group B/Group A analysis tables are accessed.
SQL_METRIC_HISTORY = """
WITH recent AS(
 SELECT m.id,m.video_id,m.source_id,m.observed_at,m.views,m.likes,
        m.comments,m.shares,m.saves,m.source_metric_definition,
        m.source_metric_version,m.data_quality_status,m.precision,m.observation_time_assumed,
        ROW_NUMBER() OVER(PARTITION BY m.video_id ORDER BY m.observed_at DESC,m.id DESC) AS rn
 FROM metric_snapshots m
 JOIN sources s ON s.id=m.source_id
 JOIN source_policy_versions policy ON policy.id=m.source_policy_version_id
   AND policy.source_id=m.source_id AND policy.is_active
 WHERE m.video_id=ANY(%s)
   AND s.status='enabled' AND s.can_store_metrics AND s.can_derive_scores
   AND (policy.permissions->>'can_store_metrics')='true'
   AND (policy.permissions->>'can_derive_scores')='true'
   AND m.observed_at<=now() AND m.observed_at>=now()-interval '90 days'
   AND (m.expires_at IS NULL OR m.expires_at>now())
)
SELECT id,video_id,source_id,observed_at,views,likes,comments,shares,saves,
       source_metric_definition,source_metric_version,data_quality_status,precision,observation_time_assumed
FROM recent WHERE rn<=4 ORDER BY video_id,observed_at,id
"""


def _conn():
    if not config.enabled() or not config.dsn():
        raise MarketError("database_disabled", "Для исследования нужен PostgreSQL.", 503)
    import psycopg
    try:
        return psycopg.connect(config.dsn(), connect_timeout=4)
    except psycopg.OperationalError as exc:
        raise MarketError("database_unavailable", "Не удалось подключиться к PostgreSQL.", 503) from exc


def _project_checked(project_id):
    try:
        _project(project_id)
    except RegistryProblem as exc:
        raise MarketError("project_unavailable", str(exc), exc.http_status) from exc


def _seeds_from_registry(project_id):
    sources = list_sources(project_id)["sources"]
    seeds = {"videos": [], "authors": []}
    for ref in sources:
        if ref.get("link_kind") not in ("video", "account") or ref.get("platform") not in ("tiktok", "instagram", "youtube"):
            continue
        kind = "videos" if ref["link_kind"] == "video" else "authors"
        if ref["source_type"] not in (("user_video_reference",) if kind == "videos" else ("competitor_account", "promoter_account")):
            continue
        seeds[kind].append({"url": ref["canonical_url"], "platform": ref["platform"],
                            "native_id": ref["platform_native_id"]})
    seeds["videos"].sort(key=lambda x: (x["platform"], x["native_id"] or "", x["url"]))
    seeds["authors"].sort(key=lambda x: (x["platform"], x["url"]))
    return seeds


def _read_dataset(conn, project_id):
    """Read canonical D1 plus MI-owned analyses, calculate creator stats locally."""
    from collections import defaultdict
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_VIDEO_EVIDENCE,
                    (project_id,project_id,project_id,project_id,project_id))
        rows=cur.fetchall()
    truncated=len(rows)>2000
    observations=[]
    for r in rows[:2000]:
        creative=r["creative"] or {}
        observations.append({
            "video_id":r["id"],"url":r["canonical_url"],
            "platform":r["platform"],"creator_id":r["creator_key"],
            "published_at":r["published_at"].isoformat() if r["published_at"] else None,
            "metric_snapshot_id":None,"metric_observed_at":None,"views":None,
            "mi_analysis_id":r["mi_analysis_id"],
            "format_family":creative.get("format_family"),
            "hook_mechanisms":creative.get("hook_mechanisms") or [],
            "hook_text":creative.get("hook_text"),
            "hook_visual_description":None,
            "story_roles":creative.get("story_roles") or [],
            "technical":r["technical"],
            "creative_state":creative.get("state","unavailable")
        })
    histories=defaultdict(list)
    if observations:
        video_ids=[r["video_id"] for r in observations]
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SQL_METRIC_HISTORY,(video_ids,))
            for item in cur.fetchall():
                data=dict(item)
                data["observed_at"]=item["observed_at"].isoformat()
                histories[item["video_id"]].append(data)
    # Never mask corrected/retracted newest counters by using older 'ok' rows.
    # Growth pairs must use the same provider, definition and version.
    safe_history={}
    for vid,points in histories.items():
        newest=points[-1]
        def ok(point):
            return (point["data_quality_status"]=="ok" and point["precision"]=="exact"
                and point["observation_time_assumed"] is False
                and type(point["views"]) is int and point["views"]>=0)
        if not ok(newest):
            continue
        prior=next((p for p in reversed(points[:-1])
           if ok(p) and p["source_id"]==newest["source_id"]
              and p["source_metric_definition"]==newest["source_metric_definition"]
              and p["source_metric_version"]==newest["source_metric_version"]),None)
        safe_history[vid]=([prior] if prior else [])+[newest]
    return enrich(observations,safe_history),truncated


class MarketIntelligenceService:
    def register(self, project_id: str, videos: list, authors: list) -> dict:
        """Validate everything first, then reuse persisted Project Sources.

        Video URLs are enqueued through D1's *existing* idempotent offline
        importer. This never calls social APIs or pretends to collect metrics.
        """
        seeds = validate_seeds(videos, authors)
        _project_checked(project_id)
        created, existing = 0, 0
        for kind, source_type in (("videos", "user_video_reference"),
                                  ("authors", "competitor_account")):
            for row in seeds[kind]:
                try:
                    create_source(project_id, {"url": row["url"], "source_type": source_type})
                    created += 1
                except RegistryProblem as exc:
                    if exc.http_status != 409:
                        raise MarketError("source_register_failed", str(exc), exc.http_status) from exc
                    existing += 1
        discovery_result = None
        if seeds["videos"]:
            # Existing imports are idempotent; this source intentionally has no
            # rights to import provider metrics or assert real video performance.
            urls = "\n".join(x["url"] for x in seeds["videos"])
            key = "mi." + project_id + "." + digest(seeds["videos"])[:28]
            try:
                discovery_result = discovery.create_import(urls, "urls", "", key)
                discovery.start_worker()
            except discovery.DiscoveryError as exc:
                # Sources remain durable, and retry with the same request is safe.
                raise MarketError("discovery_queue_failed", str(exc), 503) from exc
        author_jobs = queue_profiles(project_id, seeds["authors"])
        media = queue_available(project_id)
        return {
            "ok": True, "registered": created, "already_registered": existing,
            "discovery_import": discovery_result,
            "author_jobs": author_jobs,
            "media_analytics": media,
            "author_collection": "queued_metadata_only",
            "provider_requests": 0, "llm_requests": 0,
        }

    def authors_status(self, project_id: str) -> dict:
        _project_checked(project_id)
        return list_jobs(project_id)

    def start_media_analysis(self, project_id: str) -> dict:
        _project_checked(project_id)
        return {"ok":True,**queue_available(project_id)}

    def media_analysis_status(self, project_id: str) -> dict:
        _project_checked(project_id)
        return media_status(project_id)

    def preview(self, project_id: str) -> dict:
        _project_checked(project_id)
        seeds = _seeds_from_registry(project_id)
        with _conn() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                rows, truncated = _read_dataset(conn, project_id)
        report = summarize_market(project_id, seeds, rows, truncated=truncated)
        return {"ok": True, "snapshot": report, "payload_sha256": digest(report)}

    def freeze(self, project_id: str) -> dict:
        _project_checked(project_id)
        seeds = _seeds_from_registry(project_id)
        with _conn() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                rows, truncated = _read_dataset(conn, project_id)
                report = summarize_market(project_id, seeds, rows, truncated=truncated)
                checksum = digest(report)
                new = conn.execute("""
                    INSERT INTO market_intelligence_snapshots
                      (project_id,payload_sha256,payload,status)
                    VALUES (%s,%s,%s::jsonb,%s)
                    ON CONFLICT(project_id,payload_sha256) DO NOTHING
                    RETURNING id
                    """, (project_id, checksum, json.dumps(report,ensure_ascii=False),
                          report["status"])).fetchone()
                if new is None:
                    old = conn.execute("""
                        SELECT id FROM market_intelligence_snapshots
                        WHERE project_id=%s AND payload_sha256=%s
                        """, (project_id, checksum)).fetchone()
                    if old is None:
                        raise MarketError("snapshot_conflict", "Не удалось определить сохранённую версию.", 409)
                    ident = old[0]
                else:
                    ident = new[0]
        return {"ok": True, "snapshot_id": str(ident), "payload_sha256": checksum,
                "status": report["status"], "coverage": report["coverage"],
                "format_families": report["format_families"],
                "provider_requests": 0, "llm_requests": 0}

    def get(self, project_id: str, snapshot_id: str) -> dict:
        _project_checked(project_id)
        try:
            ident = str(UUID(snapshot_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise MarketError("invalid_snapshot_id", "Некорректный идентификатор отчёта.", 422) from exc
        with _conn() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                found = conn.execute("""
                    SELECT payload_sha256,payload,created_at
                    FROM market_intelligence_snapshots
                    WHERE project_id=%s AND id=%s
                    """, (project_id, ident)).fetchone()
                if not found:
                    raise MarketError("snapshot_not_found", "Отчёт не найден.", 404)
                checksum, report, created_at = found
                if digest(report) != checksum:
                    raise MarketError("snapshot_integrity", "Отчёт повреждён.", 409)
                # A historical report must never launder revoked provider metrics
                # into a fresh RTB context. Fail closed until it is recomputed.
                current, _ = _read_dataset(conn, project_id)
                current_by_id = {r["video_id"]: r for r in current}
                for evidence in report["video_evidence"]:
                    now_row = current_by_id.get(evidence["video_id"])
                    if now_row is None or (evidence["metric_snapshot_id"] is not None
                        and now_row["metric_snapshot_id"] != evidence["metric_snapshot_id"]) or (
                        evidence["group_b_analysis_id"] is not None and
                        now_row["group_b_analysis_id"] != evidence["group_b_analysis_id"]):
                        raise MarketError("snapshot_stale", "Источники или права изменились. Создай новый отчёт.", 409)
        return {"ok": True, "snapshot_id": ident, "created_at": created_at.isoformat(),
                "payload_sha256": checksum, "snapshot": report}

    def rtb_context(self, project_id: str, snapshot_id: str) -> dict:
        saved = self.get(project_id, snapshot_id)
        return {"ok": True, "snapshot_id": saved["snapshot_id"],
                "context": verified_rtb_context(saved["snapshot"]),
                "read_only": True, "opus_dispatched": False}
