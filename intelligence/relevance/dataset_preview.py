"""M09: DB-authoritative, read-only SRE dataset preview with full pair coverage.

One repeatable-read transaction pins a human-approved C9 publication AND
explicit D1 project-owned content versions. Every selected video is paired
with every approved hypothesis leaf, including revoked/unavailable items.
A preview grants no paid execution and is never a replacement for M08 at send.
"""
from __future__ import annotations

from .ports.approved_c9 import build_c9_bundle
from .v1_contracts import (
    SREContractError, canonical_uuid, fingerprint, positive, project_id, reject,
)

PREVIEW_VERSION = "sre-dataset-preview/v1"
MAX_SELECTED_VERSIONS = 1000
MAX_HYPOTHESES = 48
MAX_PAIRS = MAX_SELECTED_VERSIONS * MAX_HYPOTHESES
_BIGINT_MAX = 9223372036854775807


def _selection(content_version_ids):
    if (type(content_version_ids) not in (list, tuple)
            or not 1 <= len(content_version_ids) <= MAX_SELECTED_VERSIONS):
        reject("content_version_ids", "expected 1..1000 explicitly selected D1 content versions")
    for item in content_version_ids:
        positive(item, "content_version_ids[]", maximum=_BIGINT_MAX)
    return sorted(set(content_version_ids))


def _warnings(content):
    data = content["data"]
    warnings = []
    for key in ("title", "description"):
        if not data.get(key):
            warnings.append(key + "_missing")
    if content["completeness"] == "metadata_only":
        warnings.extend(("transcript_unavailable", "scenes_unavailable"))
    else:
        for key in ("transcript", "scenes"):
            if not data.get(key):
                warnings.append(key + "_unavailable")
    return warnings


def read_dataset_preview(conn, project, publication, content_version_ids):
    """Read all inputs under the caller's existing consistent DB transaction.

    The lower-level method is for a trusted internal owner service, NEVER a
    raw client-provided connection or an unscoped video search.
    """
    from .taxonomy_adapter import read_authorized_taxonomy
    from .live_content_gate import read_live_content_gate

    pid = project_id(project)
    pub = canonical_uuid(publication, "publication_id")
    ids = _selection(content_version_ids)
    # S2 authorizes actual deep publication, both human reviews, facts,
    # RTB->SRE taxonomy mirror. A self-hashed pure C9 bundle is NOT enough.
    taxonomy = read_authorized_taxonomy(conn, pid, pub)
    approved = build_c9_bundle(taxonomy)
    if approved["project_id"] != pid or approved["publication_id"] != pub:
        reject("publication_id", "C9 project/publication differs", "snapshot_mismatch")
    contexts = approved["contexts"]
    keys = approved["hypothesis_keys"]
    if (not keys or len(keys) > MAX_HYPOTHESES
            or len(keys) != len(contexts)
            or len(set(keys)) != len(keys)
            or set(keys) != {entry["category_key"] for entry in contexts}):
        reject("approved_c9", "missing, duplicate or unsound hypothesis leaf coverage",
               "snapshot_mismatch")

    # Project-owned versions only. The base SRE content table (not just the
    # rights-filtered view) allows revoked but OWNED inputs to be shown as
    # blocked; otherwise revoked videos would silently disappear from preview.
    rows = conn.execute(
        """SELECT c.id,c.video_id,c.revision
           FROM sre_content_versions c
           JOIN sre_workspaces w ON w.project_id=c.project_id
           JOIN sre_project_videos pv
             ON pv.project_id=c.project_id AND pv.video_id=c.video_id
           WHERE c.project_id=%s AND c.id=ANY(%s::bigint[])
           ORDER BY c.video_id,c.revision DESC,c.id DESC""",
        (pid, ids),
    ).fetchall()
    if (len(rows) != len(ids) or
            {row[0] for row in rows} != set(ids) or
            any(type(cid) is not int or type(vid) is not int
                or type(rev) is not int or rev <= 0 for cid, vid, rev in rows)):
        # Never disclose the owner or metadata of an out-of-project ID.
        reject("content_version_ids", "missing or unowned SRE content selection",
               "rights_denied")

    selected = {}
    replaced = []
    for cid, video, revision in rows:
        if video in selected:
            # DB is ordered by decreasing revision and ID; old references are
            # explicitly reported, rather than invisibly processed twice.
            replaced.append({
                "video_id": video, "content_version_id": cid,
                "kept_content_version_id": selected[video][0],
                "reason": "superseded_in_selection",
            })
        else:
            selected[video] = (cid, revision)
    videos = []
    pairs = []
    for video in sorted(selected):
        cid, revision = selected[video]
        try:
            gate = read_live_content_gate(
                conn, pid, cid, phase="planning",
                pins={"video_id": video, "content_version_id": cid},
            )
        except SREContractError as exc:
            # A project-owned revoked/expired/invalid lineage is not a fatal
            # global preview error: retain all its hypothesis pairs as BLOCKED.
            # Deliberately no sensitive details, title or source data in output.
            reason = {
                "rights_denied": "rights_unavailable",
                "rights_expired": "content_expired",
                "missing_provenance": "invalid_d1_provenance",
                "snapshot_mismatch": "invalid_d1_provenance",
            }.get(exc.code, "content_validation_failed")
            warnings = ["content_unavailable"]
            status = "blocked"
            sha = None
            completeness = None
        else:
            content = gate["content"]
            if (content["project_id"] != pid or
                    content["content_version_id"] != cid or
                    content["video_id"] != video):
                reject("dataset_preview", "D1 gate returned another project/video/revision",
                       "snapshot_mismatch")
            warnings = _warnings(content)
            sha = gate["content_sha256"]
            completeness = content["completeness"]
            # A title-less/description-less metadata-only revision has no
            # usable text evidence, even if D1 metadata rights are valid.
            has_text = any(content["data"].get(k) for k in (
                "title", "description", "hashtags"))
            if completeness == "metadata_only" and not has_text:
                status, reason = "blocked", "no_classifiable_content"
            else:
                status, reason = "eligible", None
        videos.append({
            "video_id": video,
            "content_version_id": cid,
            "revision": revision,
            "content_sha256": sha,
            "completeness": completeness,
            "status": status,
            "reason": reason,
            "warnings": warnings,
        })
        for category in keys:
            pairs.append({
                "video_id": video,
                "content_version_id": cid,
                "category_key": category,
                "status": status,
                "reason": reason,
            })
    total = len(selected) * len(keys)
    eligible = sum(v["status"] == "eligible" for v in videos) * len(keys)
    if len(pairs) != total or eligible + (total-eligible) != total:
        reject("dataset_preview", "incomplete hypothesis/video Cartesian coverage",
               "snapshot_mismatch")
    result = {
        "schema_version": PREVIEW_VERSION,
        "project_id": pid,
        "publication_id": pub,
        "taxonomy_sha256": approved["taxonomy_sha256"],
        "taxonomy_version_id": approved["sre_taxonomy_version_id"],
        "bundle_sha256": approved["bundle_sha256"],
        "hypothesis_keys": keys,
        "selected_versions": ids,
        "deduplicated_video_versions": sorted(
            replaced, key=lambda item: (item["video_id"],item["content_version_id"])
        ),
        "videos": videos,
        "pairs": pairs,
        "counts": {
            "requested_references": len(content_version_ids),
            "unique_versions": len(ids),
            "duplicate_references": len(content_version_ids)-len(ids),
            "deduplicated_video_versions": len(replaced),
            "selected_videos": len(selected),
            "hypotheses": len(keys),
            "total_pairs": total,
            "eligible_pairs": eligible,
            "blocked_pairs": total-eligible,
            "videos_with_warnings": sum(bool(v["warnings"]) for v in videos),
        },
        "execution_allowed": False,
    }
    result["preview_sha256"] = fingerprint(result)
    return result


def load_dataset_preview(project, publication, content_version_ids, *, connect=None):
    """Stable host port; one repeatable-read/read-only transaction, no writes."""
    from intelligence.taxonomy_builder.pipeline_repository import _connect

    pid = project_id(project)
    pub = canonical_uuid(publication, "publication_id")
    selected = _selection(content_version_ids)
    factory = connect or _connect
    with factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            # Preserve the original list so counts explain repeated IDs.
            return read_dataset_preview(conn, pid, pub, list(content_version_ids))


__all__ = (
    "PREVIEW_VERSION", "MAX_SELECTED_VERSIONS", "MAX_HYPOTHESES",
    "MAX_PAIRS", "read_dataset_preview", "load_dataset_preview",
)
