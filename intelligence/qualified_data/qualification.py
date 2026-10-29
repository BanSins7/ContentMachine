"""FIX-04: deterministic, no-I/O, project-scoped qualified-set resolver.

Caller MUST verify the SRE LIVE run, approved taxonomy, complete manifest,
human label scope and evidence against PostgreSQL before strict=True.
A human correction changes only its (video, category) pair. There is one
QDP item per canonical D1 video, but N linked matched hypothesis leaves.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence

from .contracts import QDPError, sha256

VERDICTS = frozenset(("match", "no_match", "uncertain", "not_evaluated"))
HASH = re.compile(r"[a-f0-9]{64}\Z")


def _reject(message: str, code: str = "bad_sre_result") -> None:
    raise QDPError(code, message, 409)


def _category(value, *, strict: bool) -> str:
    if type(value) is not str or not value or len(value) > 120:
        _reject("Invalid or missing SRE category key")
    if strict:
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError("noncanonical")
        except (ValueError, AttributeError) as exc:
            raise QDPError("bad_sre_result", "Noncanonical SRE category UUID", 409) from exc
    return value


def _modern_uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value))==value
    except (ValueError,TypeError,AttributeError):
        return False


def _normalized_row(row: Mapping, *, strict: bool) -> dict:
    if not isinstance(row, Mapping):
        _reject("SRE classification row must be an object")
    vid, rid = row.get("video_id"), row.get("result_id")
    modern=row.get("source_schema")=="sre_live_m36"
    if row.get("source_schema") not in (None,"sre_live_m36"):
        _reject("Unknown SRE source contract")
    if type(vid) is not int or vid <= 0:
        _reject("Invalid canonical video ID")
    if modern:
        if not _modern_uuid(rid):
            _reject("Modern SRE must pin original UUID result")
    elif type(rid) is not int or rid <= 0:
        _reject("Missing positive SRE result ID")
    category = _category(row.get("category_key"), strict=strict)
    effective = row.get("effective_decision")
    if effective not in VERDICTS:
        _reject("SRE effective decision is invalid")
    model = row.get("model_decision", effective)
    if model not in VERDICTS:
        _reject("SRE model decision is invalid")
    readable = row.get("content_readable")
    if type(readable) is not bool:
        _reject("SRE content readability must be explicit")
    hid = row.get("human_label_id")
    human = row.get("human_label_decision")
    review = row.get("review_created_at")
    if hid is None:
        if human is not None or review is not None:
            _reject("Human decision has no current label ID")
        if effective != model:
            _reject("Effective decision differs from model without human label")
    else:
        if modern:
            if not _modern_uuid(hid):
                _reject("Modern SRE review must pin original UUID")
        elif type(hid) is not int or hid <= 0:
            _reject("Invalid human label ID")
        if human not in ("match", "no_match", "uncertain") or human != effective:
            _reject("Human correction does not match effective label")
        if strict and review is None:
            _reject("Human correction has no review timestamp")
    content_id = row.get("content_version_id")
    digest = row.get("result_content_sha256")
    if strict and (type(content_id) is not int or content_id <= 0 or
                   type(digest) is not str or not HASH.fullmatch(digest)):
        _reject("LIVE selection must reference pinned immutable content")
    if not strict and content_id is not None and (type(content_id) is not int or content_id <= 0):
        _reject("Invalid content version ID")
    if not strict and digest is not None and (
            type(digest) is not str or not HASH.fullmatch(digest)):
        _reject("Invalid SRE content SHA256")
    data = {
        "source_id": row.get("source_id"),
        "source_policy_version_id": row.get("source_policy_version_id"),
        "metadata_observation_id": row.get("sre_metadata_observation_id"),
        "discovery_id": ((row.get("content_provenance") or {}).get("discovery_id")
                         if isinstance(row.get("content_provenance"), Mapping) else None),
        "evidence_sha256": sha256(row.get("evidence")),
        "video_id": vid, "result_id": rid, "category_key": category,
        "model_decision": model, "effective_decision": effective,
        "content_readable": readable, "content_version_id": content_id,
        "content_sha256": digest, "human_label_id": hid,
        "review_created_at": str(review) if review is not None else None,
    }
    if modern:
        # Historical v1 Qualified Set hashes remain unchanged.
        data["source_schema"]="sre_live_m36"
    return data


def resolve_qualified_set(rows: Sequence[Mapping], *, strict: bool = False) -> dict:
    """Resolve 3 mutually exclusive outcomes and audit all pairs.

    strict=True requires pinned version+hash from LIVE SRE; old public
    qualify_sre_rows retains the same small shape through the wrapper.
    Empty SRE results are never considered "all no_match".
    """
    if not isinstance(rows, (list, tuple)) or not rows:
        _reject("Cannot classify an empty/unverified SRE result matrix",
                "sre_coverage_incomplete")
    grouped = defaultdict(list)
    seen = set()
    for row in rows:
        item = _normalized_row(row, strict=strict)
        pair = (item["video_id"], item["category_key"])
        if pair in seen:
            _reject("Duplicate SRE video/category pair", "sre_duplicate_result")
        seen.add(pair)
        grouped[item["video_id"]].append(item)
    qualified, review, excluded, detailed = [], [], [], []
    for video_id in sorted(grouped):
        current = sorted(grouped[video_id], key=lambda x: x["category_key"])
        positives = [r for r in current
                     if r["effective_decision"] == "match" and r["content_readable"]]
        pending = [r for r in current
                   if r["effective_decision"] in ("uncertain", "not_evaluated") or
                   (r["effective_decision"] == "match" and not r["content_readable"])]
        if positives:
            state = "qualified"
            categories = [r["category_key"] for r in positives]
            result_ids = sorted(r["result_id"] for r in positives)
            qualified.append({"video_id": video_id,
                              "category_keys": categories, "result_ids": result_ids})
        elif pending:
            state = "needs_review"
            categories, result_ids = [], []
            review.append(video_id)
        else:
            if any(r["effective_decision"] != "no_match" for r in current):
                _reject("Unresolved SRE verdict cannot be excluded")
            state = "excluded"
            categories, result_ids = [], []
            excluded.append(video_id)
        # Immutable per-video selection context for later QDP run manifest.
        detailed.append({
            "video_id": video_id, "status": state,
            "category_keys": categories,
            "result_ids": result_ids,
            "content_version_ids": sorted({r["content_version_id"] for r in current
                                           if r["content_version_id"] is not None}),
            "review_label_ids": sorted({r["human_label_id"] for r in current
                                       if r["human_label_id"] is not None}),
            "pairs": current,
            "selection_sha256": sha256({
                "version": "qdp-selection/v1", "video_id": video_id,
                "status": state, "pairs": current
            }),
        })
    review_facts = sorted([
        {"video_id": r["video_id"], "category_key": r["category_key"],
         "human_label_id": r["human_label_id"],
         "review_created_at": r["review_created_at"],
         "effective_decision": r["effective_decision"]}
        for group in detailed for r in group["pairs"] if r["human_label_id"] is not None
    ], key=lambda v: (v["video_id"], v["category_key"]))
    legacy = {
        "qualified": qualified, "needs_review": review, "excluded": excluded,
        "totals": {"qualified": len(qualified),
                   "needs_review": len(review), "excluded": len(excluded)},
    }
    return {
        **legacy, "details": detailed,
        "review_watermark": sha256({"version": "qdp-review/v1", "labels": review_facts}),
        "selection_sha256": sha256({
            "version": "qdp-qualified-set/v1",
            "items": [{"video_id": v["video_id"], "selection_sha256": v["selection_sha256"]}
                      for v in detailed],
        }),
    }
