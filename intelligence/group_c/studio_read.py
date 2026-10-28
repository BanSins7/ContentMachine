"""GC-MOD-27: rights-safe versioned Group C Studio evidence bundle.

This is a read-only data adapter, NOT a scorer and not an approval engine.
An entire consistent Studio snapshot is read in one SERIALIZABLE READ ONLY
transaction, and raw observations are returned ONLY when GC24 proves the
previously completed certified C7 and every frozen peer still usable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .analytics_bridge import _persisted
from .contracts import ContractError
from .d1 import _record, _records, local_d1_ports
from .retention import _certified, _validate_current

STUDIO_VERSION="group-c-studio/v1"
MAX_STUDIO_POINTS=48
_COHORT_NAMES={
    "creator":"Автор",
    "niche":"Ниша",
    "engagement_prior":"Базовая вовлечённость",
}
_LIMITATIONS=(
    "Breakout Score — взвешенная сумма четырёх вкладов, не вероятность и не оценка 0–100.",
    "Временной ряд содержит только допущенные текущими правами и сроками хранения наблюдения.",
    "Исторический завершённый расчёт может быть недоступен после отзыва источника или истечения срока хранения.",
    "Сравнительные когорты и конфигурация фиксируются на момент утверждённого расчёта; новое наблюдение требует новой ревизии.",
)


def _project(project_id: str) -> str:
    if (type(project_id) is not str or len(project_id)!=32
            or any(x not in "0123456789abcdef" for x in project_id)):
        raise ContractError("invalid_project_id","Canonical project scope required")
    return project_id


def _id(video_id: int) -> int:
    if type(video_id) is not int or video_id<1 or video_id>=2**63:
        raise ContractError("invalid_video_id","Positive 64-bit publication id required")
    return video_id


def _response(*,project_id,video_id,run,state,reason,score=None,
              timeline=(),cohorts=(),configuration=None,approval=None,quality=None):
    return {
        "schema_version":STUDIO_VERSION,
        "project_id":project_id,"video_id":video_id,
        "run_id":str(run["id"]) if run is not None else None,
        "state":state,"reason_code":reason,
        "calculated_metric_id":run["calculated_metric_id"] if run is not None else None,
        "score":score,"timeline":list(timeline),"cohorts":list(cohorts),
        "configuration":configuration,"approval":approval,
        "quality":quality if quality is not None else {
            "status":"not_verified" if run is None else "unavailable",
            "reason_code":reason,
        },
        "limitations":list(_LIMITATIONS),
    }


def _safe_raw(conn, score, *, days):
    """Bounds timestamps and filters *all* raw eligibility; no last-good fallback.

    Retention days are checked independently by GC24 for the pinned exact C7
    evidence. For UI history they also bound historical observation times.
    """
    if days is not None and (type(days) is not int or days<1):
        raise ContractError("source_policy_unavailable","Invalid retention duration")
    rows=_records(conn.execute(
        """SELECT id,observed_at,views,source_metric_version,data_quality_status,
                  source_policy_version_id
           FROM metric_snapshots
           WHERE video_id=%s AND source_id=%s AND source_policy_version_id=%s
             AND source_metric_definition='platform_post_views'
             AND source_metric_version=%s AND data_quality_status='ok'
             AND precision='exact' AND observation_time_assumed=FALSE
             AND observed_at<=clock_timestamp()
             AND (expires_at IS NULL OR expires_at>clock_timestamp())
             AND (%s::integer IS NULL OR observed_at>=
                  clock_timestamp()-(%s::integer*interval '1 day'))
           ORDER BY observed_at DESC,id DESC LIMIT %s""",
        (score.video_id,score.source_id,score.source_policy_version_id,
         score.metric_version,days,days,MAX_STUDIO_POINTS),
    ))
    return [
        {
            "snapshot_id":r["id"],
            "observed_at":r["observed_at"],
            "views":r["views"],
            "source_metric_version":r["source_metric_version"],
            "quality_status":r["data_quality_status"],
            "is_current":r["id"]==score.snapshot_id,
            "is_previous":r["id"]==score.previous_snapshot_id,
        }
        for r in reversed(rows)
    ]


class PostgreSQLGroupCStudio:
    """Single scoped Studio GET, no unapproved projection."""

    def read(self,conn:Any,*,project_id:str,video_id:int):
        if getattr(conn,"autocommit",None) is not True:
            raise ContractError("transaction_contract_unavailable",
                                "Studio requires dedicated autocommit PG")
        _project(project_id)
        _id(video_id)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ ONLY")
            link=_record(conn.execute(
                """SELECT project_id,video_id FROM sre_project_videos
                   WHERE project_id=%s AND video_id=%s""",
                (project_id,video_id),
            ))
            if link is None:
                raise ContractError("project_video_unavailable",
                                    "No project-scoped publication")
            run=_record(conn.execute(
                """SELECT id,project_id,video_id,source_id,state,reason_code,
                          calculated_metric_id
                   FROM group_c_runs
                   WHERE project_id=%s AND video_id=%s
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (project_id,video_id),
            ))
            if run is None:
                return _response(
                    project_id=project_id,video_id=video_id,run=None,
                    state="not_calculated",reason="no_c_run",
                )
            if run["state"]!="completed" or run["calculated_metric_id"] is None:
                # A quality-blocked run deliberately has no fabricated score.
                quality=_record(conn.execute(
                    """SELECT reason_code,calculation_status,severity,created_at
                       FROM group_c_quality_reports
                       WHERE project_id=%s AND run_id=%s::uuid
                       ORDER BY created_at DESC LIMIT 1""",
                    (project_id,str(run["id"])),
                ))
                return _response(
                    project_id=project_id,video_id=video_id,run=run,
                    state=run["state"],reason=run["reason_code"],
                    quality={
                        "status":quality["calculation_status"] if quality else run["state"],
                        "reason_code":quality["reason_code"] if quality else run["reason_code"],
                        "severity":quality["severity"] if quality else None,
                    },
                )
            try:
                # No numeric source/cohort/config/status exposed before
                # current source policy, erased/invalidated C7 and all peer
                # TTL checks. We preserve original run identity.
                certified=_certified(
                    conn,project_id=project_id,
                    calculated_metric_id=run["calculated_metric_id"],
                )
                _validate_current(conn,certified,local_d1_ports())
                score=_persisted(
                    conn,project_id=project_id,
                    metric_id=run["calculated_metric_id"],run_id=str(run["id"]),
                )
            except ContractError as exc:
                return _response(
                    project_id=project_id,video_id=video_id,run=run,
                    state="unavailable",reason=exc.code,
                )
            policy=_record(conn.execute(
                """SELECT s.retention_days AS source_days,p.retention_policy
                   FROM sources s JOIN source_policy_versions p ON p.source_id=s.id
                   WHERE s.id=%s AND p.id=%s AND p.is_active=TRUE""",
                (score.source_id,score.source_policy_version_id),
            ))
            if policy is None or type(policy["retention_policy"]) is not dict:
                raise ContractError("source_policy_unavailable",
                                    "Current source rights unavailable")
            pdays=policy["retention_policy"].get("retention_days")
            dvals=[d for d in (pdays,policy["source_days"]) if d is not None]
            if any(type(d) is not int or d<1 for d in dvals):
                raise ContractError("source_policy_unavailable",
                                    "Invalid current source retention")
            days=min(dvals) if dvals else None
            timeline=_safe_raw(conn,score,days=days)
            if not any(p["is_current"] for p in timeline):
                # A restrictive provider policy can make the pinned sample
                # unavailable for UI display earlier than the GC24 gate.
                return _response(
                    project_id=project_id,video_id=video_id,run=run,
                    state="unavailable",reason="source_policy_expired",
                )
            config=_record(conn.execute(
                """SELECT c.id,c.calculation_version,c.is_active,
                          c.alpha,c.bayesian_k,c.freshness_half_life_h,
                          c.weights_json
                   FROM metric_calculation_configs c
                   WHERE c.id=%s""",
                (score.calculation_config_id,),
            ))
            approval=_record(conn.execute(
                """SELECT r.id AS review_request_id,r.subject_sha256,
                          r.calculation_version,r.created_at AS requested_at,
                          d.id AS review_decision_id,d.decision,
                          d.reviewer_id,d.identity_provider,d.created_at AS decided_at,
                          a.id AS activation_event_id,a.kind AS activation_kind,
                          a.created_at AS activated_at
                   FROM group_c_review_requests r
                   JOIN group_c_review_decisions d ON d.request_id=r.id
                   JOIN group_c_config_activation_events a
                     ON a.review_request_id=r.id AND a.review_decision_id=d.id
                   WHERE r.project_id=%s AND r.id=%s::uuid
                     AND d.id=%s::uuid AND a.id=%s::uuid
                   LIMIT 1""",
                (project_id,score.review_request_id,
                 score.review_decision_id,score.activation_event_id),
            ))
            if config is None or approval is None or approval["decision"]!="approved" \
                    or approval["activation_kind"]!="activated":
                raise ContractError("studio_approval_incompatible",
                                    "No original certified config/human approval")
            cohorts=[]
            for purpose,freeze_id in (
                ("creator",score.creator_freeze_id),
                ("engagement_prior",score.prior_freeze_id),
                ("niche",score.niche_freeze_id),
            ):
                if freeze_id is None:
                    continue
                peer=_record(conn.execute(
                    """SELECT id,purpose,state,member_count,
                              legacy_cohort_id,source_metric_version,
                              median_views,prior_like_rate,
                              prior_comment_rate,prior_share_rate,
                              evidence_sha256,sealed_at
                       FROM group_c_cohort_freezes
                       WHERE id=%s AND project_id=%s""",
                    (freeze_id,project_id),
                ))
                if peer is None or peer["purpose"]!=purpose:
                    raise ContractError("studio_peer_incompatible",
                                        "Original frozen cohort is unavailable")
                peer["label"]=_COHORT_NAMES[purpose]
                cohorts.append(peer)
            numeric={
                "breakout_score":score.breakout_score,
                "metric_kind":"weighted_components_not_probability",
                "scale":"unbounded_not_0_to_100",
                "components":[
                    {"key":"creator","label":"Сравнение с автором",
                     "value":score.score_creator_component},
                    {"key":"growth","label":"Скорость роста просмотров",
                     "value":score.score_growth_component},
                    {"key":"engagement","label":"Байесовская вовлечённость",
                     "value":score.score_engagement_component},
                    {"key":"freshness","label":"Свежесть публикации",
                     "value":score.score_freshness_component},
                ],
                "source_id":score.source_id,
                "source_policy_version_id":score.source_policy_version_id,
                "metric_version":score.metric_version,
                "calculated_metric_id":score.calculated_metric_id,
                "snapshot_id":score.snapshot_id,
                "previous_snapshot_id":score.previous_snapshot_id,
                "published_metadata_id":score.published_metadata_id,
                "evidence_sha256":score.evidence_sha256,
            }
            return _response(
                project_id=project_id,video_id=video_id,run=run,
                state="valid_current",reason=None,score=numeric,
                timeline=timeline,cohorts=cohorts,
                configuration=config,approval=approval,
                quality={"status":"verified","reason_code":None,
                         "source_policy_version_id":score.source_policy_version_id,
                         "metric_version":score.metric_version,
                         "current_snapshot_id":score.snapshot_id},
            )
