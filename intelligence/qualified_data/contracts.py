"""Pure, strict Qualified Data Pipeline v1 contracts. No network or database access."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

PROJECT_RE = re.compile(r"[0-9a-f]{32}\Z")
KEY_RE = re.compile(r"[A-Za-z0-9._:-]{12,128}\Z")
GROUPS = ("A", "B", "C")


class QDPError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


def project_id(value: str) -> str:
    if type(value) is not str or PROJECT_RE.fullmatch(value) is None:
        raise QDPError("invalid_project", "Expected a ContentMachine project ID")
    return value


def positive_id(value, label: str) -> int:
    if type(value) is not int or value < 1:
        raise QDPError("invalid_id", "Invalid " + label)
    return value


def sha256(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def options(raw: dict | None) -> dict:
    """No provider calls or media extraction are enabled by configuration."""
    if raw is None:
        raw = {}
    if type(raw) is not dict or set(raw) - {"max_age_days", "require_published_at"}:
        raise QDPError("invalid_config", "Unsupported qualified-data configuration")
    age = raw.get("max_age_days")
    if age is not None and (type(age) is not int or not 1 <= age <= 365):
        raise QDPError("invalid_config", "max_age_days must be null or 1..365")
    require = raw.get("require_published_at", False)
    if type(require) is not bool:
        raise QDPError("invalid_config", "require_published_at must be boolean")
    return {"max_age_days": age, "require_published_at": require}


def idempotency_key(value: str) -> str:
    if type(value) is not str or KEY_RE.fullmatch(value) is None:
        raise QDPError("invalid_idempotency_key", "Expected a 12..128 character idempotency key")
    return value


def qualify_sre_rows(rows: list[dict]) -> dict:
    """Use one pinned, completed SRE run, with current human labels overriding its labels.

    Rows must already be selected from one project, approved taxonomy and run.
    The eligible match must still reference a currently readable SRE content version.
    Unknown and human-review states never become positive labels.
    """
    video_rows: dict[int, list[dict]] = {}
    for row in rows:
        if type(row.get("video_id")) is not int or row["video_id"] <= 0:
            raise QDPError("bad_sre_result", "Invalid SRE video ID", 409)
        verdict = row.get("effective_decision")
        if verdict not in ("match", "no_match", "uncertain", "not_evaluated"):
            raise QDPError("bad_sre_result", "Invalid SRE decision", 409)
        video_rows.setdefault(row["video_id"], []).append(row)
    qualified, review, excluded = [], [], []
    for video_id in sorted(video_rows):
        related = video_rows[video_id]
        matched = [r for r in related if r["effective_decision"] == "match"
                   and r.get("content_readable") is True]
        if matched:
            qualified.append({
                "video_id": video_id,
                "category_keys": sorted({str(x["category_key"]) for x in matched}),
                "result_ids": sorted({int(x["result_id"]) for x in matched}),
            })
        elif any(r["effective_decision"] in ("uncertain", "not_evaluated")
                 or (r["effective_decision"] == "match" and not r.get("content_readable"))
                 for r in related):
            review.append(video_id)
        else:
            excluded.append(video_id)
    return {"qualified": qualified, "needs_review": review, "excluded": excluded,
            "totals": {"qualified": len(qualified), "needs_review": len(review),
                       "excluded": len(excluded)}}


def freshness_reason(published_at: datetime | None, cfg: dict,
                     *, now: datetime | None = None) -> str | None:
    if published_at is None:
        return "published_at_unknown" if (cfg["require_published_at"]
                                           or cfg["max_age_days"] is not None) else None
    if published_at.tzinfo is None:
        return "published_at_naive"
    now = now or datetime.now(timezone.utc)
    if published_at > now:
        return "published_at_future"
    if cfg["max_age_days"] is not None and (now-published_at).total_seconds() > cfg["max_age_days"]*86400:
        return "published_at_too_old"
    return None


def analysis_readiness(*, media_asset_id: int | None, media_allowed: bool,
                       metric_snapshot_ids: list[int], metric_definitions: list[str],
                       scoring_allowed: bool, active_score_config_id: int | None) -> dict:
    """No group is actually analyzed. Ready means a downstream job may be considered."""
    reasons = {}
    if media_asset_id is None or not media_allowed:
        reasons["A"] = "media_rights_or_asset_missing"
        reasons["B"] = "media_rights_or_asset_missing"
    if not scoring_allowed:
        reasons["C"] = "score_rights_missing"
    elif active_score_config_id is None:
        reasons["C"] = "score_configuration_missing"
    elif len(metric_snapshot_ids) < 2:
        reasons["C"] = "metric_history_insufficient"
    elif len(set(metric_definitions)) != 1 or not metric_definitions[0]:
        reasons["C"] = "metric_definition_incompatible"
    return {
        group: {"state": "blocked", "reason_code": reasons[group]}
        if group in reasons else {"state": "ready", "reason_code": None}
        for group in GROUPS
    }
