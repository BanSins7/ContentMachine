"""QDP-FIX-06: fully reproducible run and video selection snapshots.

SRE is the authority for the input manifest; QDP stores only DB-verified
references/sha256 and deterministic decisions. Do not copy raw transcripts
or synthesize source IDs/metrics. All writes are owned by create_run's one
project-scoped PostgreSQL transaction.
"""
from __future__ import annotations

from collections.abc import Mapping
from .contracts import QDPError, sha256
from .coverage import parse_expected_manifest

VERSION = "qdp-run-manifest/v1"
MAX_VIDEOS = 10_000


def build_run_manifest(project: str, sre_run_id: str, source: Mapping,
                       selection: Mapping, config: dict) -> dict:
    """Freeze SRE manifest, qualified/review/excluded and human watermark.

    The validated SRE input set includes *every* classified video. An empty
    qualified set is valid, an empty SRE manifest is not.
    """
    expected = parse_expected_manifest(source)
    videos = expected["videos"]
    details = selection.get("details")
    if not isinstance(details, list) or len(details) != len(videos):
        raise QDPError("qdp_manifest_incomplete",
                       "QDP selection must include every pinned SRE video", 409)
    if len(details) > MAX_VIDEOS:
        raise QDPError("too_many_items", "SRE manifest exceeds 10000 videos", 422)
    by_video = {}
    total_pairs = 0
    counter = {"qualified": 0, "needs_review": 0, "excluded": 0}
    lineage = []
    for item in details:
        vid, status = item["video_id"], item["status"]
        if type(vid) is not int or vid not in videos or str(vid) in by_video:
            raise QDPError("qdp_manifest_invalid", "Invalid or duplicate video in selection", 409)
        if status not in counter:
            raise QDPError("qdp_manifest_invalid", "Unknown QDP selection state", 409)
        counter[status] += 1
        raw_pairs = item["pairs"]
        if len(raw_pairs) != len(videos[vid]["hypothesis_keys"]):
            raise QDPError("qdp_manifest_incomplete", "Missing classified SRE leaf", 409)
        covered = {p["category_key"] for p in raw_pairs}
        if covered != videos[vid]["hypothesis_keys"]:
            raise QDPError("qdp_manifest_incomplete", "QDP hypothesis set changed", 409)
        total_pairs += len(raw_pairs)
        for row in raw_pairs:
            if row["content_version_id"] != videos[vid]["content_version_id"] or (
                    row["content_sha256"] != videos[vid]["content_sha256"]):
                raise QDPError("qdp_manifest_mismatch",
                               "QDP result content differs from SRE manifest", 409)
            lineage.append({
                "video_id": vid, "category_key": row["category_key"],
                "sre_result_id": row["result_id"],
                "content_version_id": row["content_version_id"],
                "content_sha256": row["content_sha256"],
                "evidence_sha256": row["evidence_sha256"],
                "model_decision": row["model_decision"],
                "effective_decision": row["effective_decision"],
                "human_label_id": row["human_label_id"],
            })
        by_video[str(vid)] = {
            "status": status,
            "selection_sha256": item["selection_sha256"],
            "category_keys": list(item["category_keys"]),
            "result_ids": list(item["result_ids"]),
            "content_version_id": videos[vid]["content_version_id"],
            "content_sha256": videos[vid]["content_sha256"],
            "review_label_ids": list(item["review_label_ids"]),
            "source_ids": sorted({row["source_id"] for row in raw_pairs
                                  if row.get("source_id") is not None}),
            "source_policy_version_ids": sorted({
                row["source_policy_version_id"] for row in raw_pairs
                if row.get("source_policy_version_id") is not None}),
            "metadata_observation_ids": sorted({
                row["metadata_observation_id"] for row in raw_pairs
                if type(row.get("metadata_observation_id")) is int
                and row["metadata_observation_id"] > 0}),
            "discovery_ids": sorted({
                row["discovery_id"] for row in raw_pairs
                if type(row.get("discovery_id")) is int and row["discovery_id"] > 0}),
        }
    if counter != selection["totals"]:
        raise QDPError("qdp_manifest_mismatch", "Qualified Set totals inconsistent", 409)
    if total_pairs != sum(len(v["hypothesis_keys"]) for v in videos.values()):
        raise QDPError("qdp_manifest_incomplete", "SRE matrix mismatch", 409)
    manifest = {
        "schema_version": VERSION,
        "project_id": project,
        "sre_run_id": sre_run_id,
        "taxonomy_version_id": source["taxonomy_version_id"],
        "taxonomy_sha256": expected["taxonomy_sha256"],
        "sre_manifest_sha256": expected["digest"],
        "config": config,
        "config_sha256": sha256(config),
        "selection_sha256": selection["selection_sha256"],
        "review_watermark_sha256": selection["review_watermark"],
        "totals": dict(counter),
        "expected_pairs": total_pairs,
        "expected_videos": len(videos),
        "by_video": by_video,
    }
    # No temporal/noncanonical state: identical SRE snapshot/config/reviews
    # gives bit-for-bit identical sha256 across retries/workers/processes.
    return {
        "manifest": manifest,
        "manifest_sha256": sha256(manifest),
        "sre_manifest_sha256": expected["digest"],
        "selected_sha256": selection["selection_sha256"],
        "taxonomy_sha256": expected["taxonomy_sha256"],
        "review_watermark": {"sha256": selection["review_watermark"],
                             "schema_version": "qdp-review/v1"},
        "expected_pairs": total_pairs,
        "expected_videos": len(videos),
        "lineage": sorted(lineage, key=lambda v: (v["video_id"], v["category_key"])),
    }


def read_pinned_video(conn, project: str, run_id: str, video_id: int) -> dict:
    """One JSONB-key lookup, not a full 10k-item scan. Fail closed for v1 runs."""
    row = conn.execute(
        """SELECT manifest->'by_video'->%s AS pinned
             FROM qdp_run_manifests
            WHERE run_id=%s::uuid AND project_id=%s""",
        (str(video_id), run_id, project),
    ).fetchone()
    if row is None or not isinstance(row["pinned"], dict):
        raise QDPError("qdp_manifest_missing",
                       "This QDP run lacks a verified immutable input snapshot", 409)
    return row["pinned"]


def read_run_manifest(conn, project: str, run_id: str) -> dict:
    """Internal audited manifest API; public pagination belongs to FIX-16."""
    row = conn.execute(
        """SELECT taxonomy_sha256,manifest_sha256,selected_sha256,
                  expected_pairs,expected_videos,review_watermark,manifest
             FROM qdp_run_manifests WHERE run_id=%s::uuid AND project_id=%s""",
        (run_id, project),
    ).fetchone()
    if row is None:
        raise QDPError("qdp_manifest_missing", "Immutable QDP manifest not found", 404)
    if sha256(row["manifest"]) != row["manifest_sha256"]:
        raise QDPError("qdp_manifest_integrity", "Pinned QDP manifest checksum mismatch", 409)
    if row["selected_sha256"] != row["manifest"].get("selection_sha256"):
        raise QDPError("qdp_manifest_integrity", "Pinned selection checksum mismatch", 409)
    return dict(row)
