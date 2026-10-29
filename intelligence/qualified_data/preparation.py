"""FIX-10: immutable, independent Group A/B/C preparation contracts.

SQL view separates group current rights; this Python port re-checks SRE
and exact D1 source, media bytes or metric snapshot history prior to exposure.
No paid jobs, no FFmpeg or peer imports. Historical 'ready' does not bypass
expiry/revocation. A/B/C must each recheck again at their execution time.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from .contracts import GROUPS, QDPError, positive_id, project_id, sha256
from .repository import _connect, _sre_snapshot
from .run_manifest import read_pinned_video
from .provenance import read_exact_metadata
from .media_readiness import read_verified_media
from .metric_history import read_metric_history

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
VERSION = "qdp-preparation/v2"


def build_preparation_inputs(group: str, metadata: Mapping, *,
                             media: Mapping, metrics: Mapping,
                             config_id: int | None, pinned: Mapping) -> dict:
    """Versioned, group-specific immutable pointer; no cross-group dummy IDs."""
    if group not in GROUPS:
        raise QDPError("invalid_group", "Unknown analysis group", 422)
    source = metadata.get("source_id")
    policy = metadata.get("source_policy_version_id")
    observation = metadata.get("metadata_id")
    if type(source) is not str or not source or any(
            type(v) is not int or v < 1 for v in (policy, observation)):
        raise QDPError("invalid_preparation_source", "Exact D1 source evidence missing", 409)
    cid = pinned.get("content_version_id")
    digest = pinned.get("content_sha256")
    if type(cid) is not int or cid < 1 or type(digest) is not str or (
            HEX64.fullmatch(digest) is None):
        raise QDPError("invalid_preparation_source", "Pinned SRE content invalid", 409)
    pointer = {
        "result_schema": VERSION,
        "group": group,
        "sre_content_version_id": cid,
        "sre_content_sha256": digest,
        "sre_discovery_id": metadata.get("discovery_id"),
        "enrichment_source_id": source,
        "source_policy_version_id": policy,
        "metadata_id": observation,
    }
    if type(pointer["sre_discovery_id"]) is not int or pointer["sre_discovery_id"] < 1:
        raise QDPError("invalid_preparation_source", "Pinned SRE discovery invalid", 409)
    if group in ("A","B"):
        asset=media.get("asset") if isinstance(media, Mapping) else None
        if asset is not None:
            aid, ash = asset.get("id"), asset.get("sha256")
            if type(aid) is not int or aid < 1 or type(ash) is not str or (
                    HEX64.fullmatch(ash) is None) or asset.get("media_kind")!="video" or (
                    type(asset.get("size_bytes")) is not int or asset["size_bytes"] < 1):
                raise QDPError("invalid_preparation_media", "Media asset proof malformed", 409)
        pointer.update({
            "media_asset_id": asset["id"] if asset else None,
            "media_sha256": asset["sha256"] if asset else None,
            "media_kind": asset["media_kind"] if asset else None,
            "media_size_bytes": asset["size_bytes"] if asset else None,
        })
    else:
        pair=metrics.get("growth_pair_ids")
        if type(pair) is not list or (
                pair and (len(pair)!=2 or any(type(v) is not int or v < 1 for v in pair)
                          or pair[0]==pair[1])):
            raise QDPError("invalid_metric_pair", "Malformed measured metric pair", 409)
        history_hash=metrics.get("history_sha256")
        if type(history_hash) is not str or HEX64.fullmatch(history_hash) is None:
            raise QDPError("invalid_metric_history", "Metric history checksum invalid", 409)
        definition=metrics.get("growth_definition")
        if definition is not None and (
                type(definition) is not dict or set(definition)!={"name","version"} or
                any(type(v) is not str or not v for v in definition.values())):
            raise QDPError("invalid_metric_definition", "Metric definition is invalid", 409)
        if config_id is not None and (type(config_id) is not int or config_id < 1):
            raise QDPError("invalid_score_config", "Score configuration invalid", 409)
        pointer.update({
            "metric_snapshot_ids": list(pair),
            "metric_history_sha256": history_hash,
            "metric_definition": definition,
            "score_config_id": config_id,
        })
    return pointer


def build_preparations(metadata: Mapping, pinned: Mapping, *,
                       media: Mapping, metrics: Mapping,
                       score_config_id: int | None,
                       scoring_allowed: bool) -> dict:
    """Three **independent** states and exact inputs, even if all are blocked."""
    if not isinstance(media, Mapping) or not isinstance(metrics, Mapping):
        raise QDPError("invalid_preparation", "Verified source evidence missing", 409)
    decisions = {
        g: {"state": media[g]["state"],"reason_code":media[g]["reason_code"]}
        for g in ("A","B")
    }
    c_reason = None
    if not scoring_allowed:
        c_reason="score_rights_missing"
    elif score_config_id is None:
        c_reason="score_configuration_missing"
    elif metrics.get("growth_reason_code") is not None:
        c_reason=metrics["growth_reason_code"]
    elif len(metrics.get("growth_pair_ids",[]))!=2:
        c_reason="metric_history_insufficient"
    decisions["C"]={"state":"blocked" if c_reason else "ready",
                    "reason_code":c_reason}
    out={}
    for group in GROUPS:
        d=decisions[group]
        if d["state"] not in ("ready","blocked") or (
                (d["state"]=="ready")!=(d["reason_code"] is None)):
            raise QDPError("invalid_preparation_state", "Invalid A/B/C readiness state", 409)
        inputs=build_preparation_inputs(group,metadata,media=media,
                      metrics=metrics,config_id=score_config_id,pinned=pinned)
        out[group]={"state":d["state"],"reason_code":d["reason_code"],
                    "inputs":inputs,"input_sha256":sha256(inputs)}
    return out


def _current_row(conn, pid: str, rid: str, vid: int, group: str):
    return conn.execute(
        """SELECT run_id,project_id,video_id,analysis_group,state,reason_code,
                  inputs,input_sha256,source_id,source_policy_version_id,
                  metadata_id,media_asset_id
             FROM qdp_group_current_preparations
            WHERE project_id=%s AND run_id=%s::uuid AND video_id=%s
              AND analysis_group=%s""",
        (pid,rid,vid,group)).fetchone()


def read_current_preparation(project: str, run_id: str, video_id: int,
                             group: str, *, connect=None) -> dict:
    """Current scoped read; never reuse the immutable state as authorization."""
    from .repository import _uuid
    pid, rid, vid = project_id(project), _uuid(run_id,"QDP run"), positive_id(video_id,"video")
    if group not in GROUPS:
        raise QDPError("invalid_group","Unknown analysis group",422)
    with (connect or _connect)() as conn:
        with conn.transaction():
            from .schema import require_qdp_schema
            require_qdp_schema(conn)
            # Do not reveal internal stored inputs for blocked or revoked
            # groups, even when their historical row remains immutable.
            p = _current_row(conn,pid,rid,vid,group)
            if p is None:
                return {"state":"blocked","reason_code":"preparation_not_current",
                        "group":group,"inputs":None}
            if sha256(p["inputs"])!=p["input_sha256"]:
                return {"state":"blocked","reason_code":"preparation_hash_mismatch",
                        "group":group,"inputs":None}
            pin = read_pinned_video(conn,pid,rid,vid)
            run = conn.execute(
                "SELECT COALESCE(sre_run_id,sre_live_run_id) AS sre_run_id FROM qdp_runs WHERE id=%s::uuid AND project_id=%s",
                (rid,pid)).fetchone()
            if not run:
                raise QDPError("run_not_found","QDP run not found",404)
            source, selected = _sre_snapshot(conn,pid,str(run["sre_run_id"]),
                                               only_video_id=vid)
            current = selected["details"][0] if selected["details"] else None
            if not current or current["selection_sha256"]!=pin["selection_sha256"]:
                return {"state":"blocked","reason_code":"sre_evidence_revoked_or_changed",
                        "group":group,"inputs":None}
            metadata, reason = read_exact_metadata(conn,pid,vid,pin)
            if metadata is None or metadata["metadata_id"]!=p["metadata_id"] or (
                    metadata["source_id"]!=p["source_id"] or
                    metadata["source_policy_version_id"]!=p["source_policy_version_id"]):
                return {"state":"blocked",
                        "reason_code":reason or "preparation_source_changed",
                        "group":group,"inputs":None}
            if group in ("A","B"):
                media=read_verified_media(conn,metadata)
                if media[group]["state"]!="ready" or media["asset"] is None:
                    return {"state":"blocked","reason_code":media[group]["reason_code"],
                            "group":group,"inputs":None}
                a=media["asset"]
                if (p["inputs"].get("media_asset_id")!=a["id"] or
                        p["inputs"].get("media_sha256")!=a["sha256"] or
                        p["inputs"].get("media_size_bytes")!=a["size_bytes"]):
                    return {"state":"blocked","reason_code":"media_asset_changed",
                            "group":group,"inputs":None}
            else:
                metric=read_metric_history(conn,metadata)
                conf=conn.execute(
                    """SELECT 1 FROM metric_calculation_configs
                       WHERE id=%s AND is_active AND alpha IS NOT NULL
                         AND bayesian_k IS NOT NULL""",
                    (p["inputs"].get("score_config_id"),)).fetchone()
                if (not conf or not metadata["can_derive_scores"] or
                        metadata["permissions"].get("can_derive_scores") is not True):
                    return {"state":"blocked","reason_code":"score_rights_or_config_revoked",
                            "group":group,"inputs":None}
                if (metric["growth_reason_code"] or
                        metric["growth_pair_ids"]!=p["inputs"].get("metric_snapshot_ids") or
                        metric["history_sha256"]!=p["inputs"].get("metric_history_sha256") or
                        metric["growth_definition"]!=p["inputs"].get("metric_definition")):
                    return {"state":"blocked","reason_code":"metric_history_changed",
                            "group":group,"inputs":None}
            return {"state":"ready","reason_code":None,"group":group,
                    "inputs":p["inputs"],"input_sha256":p["input_sha256"]}
