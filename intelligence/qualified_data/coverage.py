"""QDP-FIX-02: strict pinned SRE coverage/lineage proof (no external I/O).

The SRE producer owns the run plan; QDP verifies every expected
(video_id, category_key) pair and pinned immutable content version.
A row count alone cannot prove an exhaustive classification.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping

from .contracts import QDPError, sha256

HASH_RE = re.compile(r"[a-f0-9]{64}\Z")
MAX_VIDEOS = 10_000
MAX_KEYS = 48


def _bad(code: str, message: str):
    raise QDPError(code, message, 409)


def _canon_uuid(value) -> str:
    if not isinstance(value, str):
        _bad("sre_manifest_invalid", "Expected canonical taxonomy UUID")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical")
    except (ValueError, AttributeError) as exc:
        raise QDPError("sre_manifest_invalid", "Noncanonical taxonomy UUID", 409) from exc
    return value


def parse_expected_manifest(run: Mapping) -> dict:
    """Read SRE's actual run-plan item shape; no unversioned guessing.

    The currently implemented SRE offline staging uses content_versions[];
    the published sre-run-plan/v1 contract uses items[]. Both have the same
    video/content_sha/hypothesis_keys fields. Neither is accepted unless the
    authoritative run has already passed LIVE-only gating.
    """
    snapshot = run.get("dataset_snapshot")
    if not isinstance(snapshot, dict):
        _bad("sre_manifest_missing", "SRE run lacks immutable dataset manifest")
    if snapshot.get("mode") in ("offline_fixture_s4", "offline_fixture", "legacy"):
        _bad("sre_manifest_invalid", "Offline manifest is not a LIVE run manifest")
    if ("items" in snapshot) == ("content_versions" in snapshot):
        _bad("sre_manifest_missing", "Expected exactly one SRE manifest item list")
    raw = snapshot.get("items", snapshot.get("content_versions"))
    if type(raw) is not list or not 1 <= len(raw) <= MAX_VIDEOS:
        _bad("sre_manifest_invalid", "SRE run must pin 1..10000 unique videos")
    digest = snapshot.get("taxonomy_sha256")
    if type(digest) is not str or not HASH_RE.fullmatch(digest):
        _bad("sre_manifest_invalid", "Missing pinned taxonomy SHA256")
    for field, expected in (("project_id", run["project_id"]),
                            ("run_id", str(run["id"])),
                            ("taxonomy_version_id", run["taxonomy_version_id"])):
        if field in snapshot and str(snapshot[field]) != str(expected):
            _bad("sre_manifest_scope", "Manifest " + field + " mismatches SRE run")
    pinned = {}
    for item in raw:
        if not isinstance(item, dict):
            _bad("sre_manifest_invalid", "SRE run item must be an object")
        vid = item.get("video_id")
        cid = item.get("content_version_id")
        checksum = item.get("content_sha256")
        keys = item.get("hypothesis_keys")
        if (type(vid) is not int or vid <= 0 or type(cid) is not int or cid <= 0
            or type(checksum) is not str or not HASH_RE.fullmatch(checksum)):
            _bad("sre_manifest_invalid", "Invalid pinned video/content identity")
        if (type(keys) is not list or not 1 <= len(keys) <= MAX_KEYS):
            _bad("sre_manifest_invalid", "Invalid pinned hypothesis list")
        categories = [_canon_uuid(k) for k in keys]
        if len(categories) != len(set(categories)):
            _bad("sre_manifest_duplicate", "Duplicate pinned hypothesis")
        if vid in pinned:
            _bad("sre_manifest_duplicate", "Duplicate pinned video")
        pinned[vid] = {"content_version_id": cid, "content_sha256": checksum,
                       "hypothesis_keys": frozenset(categories)}
    return {"videos": pinned, "taxonomy_sha256": digest,
            "digest": sha256({"taxonomy_version_id": run["taxonomy_version_id"],
                               "taxonomy_sha256": digest,
                               "items": sorted(
                                   [{"video_id": vid, "content_version_id": v["content_version_id"],
                                     "content_sha256": v["content_sha256"],
                                     "hypothesis_keys": sorted(v["hypothesis_keys"])}
                                    for vid, v in pinned.items()],
                                   key=lambda x: x["video_id"])})}


def _evidence_grounded(row: Mapping) -> None:
    """Reject invented match evidence; quote must exist in pinned source JSON."""
    evidence = row.get("evidence")
    content = row.get("content_snapshot")
    if not isinstance(evidence, list) or not evidence or not isinstance(content, dict):
        _bad("sre_evidence_unverified", "Model match must include grounded evidence")
    valid = False
    for ev in evidence:
        if not isinstance(ev, dict):
            _bad("sre_evidence_unverified", "Invalid SRE evidence record")
        source = ev.get("source")
        quote = ev.get("quote")
        if (not isinstance(source, str) or not isinstance(quote, str)
            or not quote.strip() or len(quote) > 4096):
            _bad("sre_evidence_unverified", "Missing SRE evidence source/quote")
        # SRE stores the classified content as a JSON document. Require a
        # quoted substring in the exact claimed source field, not another video.
        origin = content.get(source)
        if isinstance(origin, str) and quote.casefold() in origin.casefold():
            valid = True
        else:
            _bad("sre_evidence_unverified", "Evidence quote not in pinned content field")
    if not valid:
        _bad("sre_evidence_unverified", "No verified evidence")


def validate_result_coverage(run: Mapping, rows: list[dict],
                             categories: list[Mapping],
                             *, only_video_id: int | None = None) -> dict:
    """Require one terminal result per pinned video × approved leaf UUID.

    Readability revocation is *not* converted to no_match: the row is still
    validated against its raw immutable version; qualified selection will
    then route unreadable matches to needs_review.
    """
    manifest = parse_expected_manifest(run)
    approved = set()
    for cat in categories:
        key = _canon_uuid(str(cat.get("category_key")))
        if key in approved or cat.get("review_status") != "approved":
            _bad("sre_taxonomy_coverage", "Non-approved or duplicate pinned category")
        approved.add(key)
    if not approved:
        _bad("sre_taxonomy_coverage", "No approved hypotheses")
    for item in manifest["videos"].values():
        if item["hypothesis_keys"] != approved:
            _bad("sre_taxonomy_coverage", "Manifest does not cover exact approved leaves")
    expected = {
        (vid, key) for vid, item in manifest["videos"].items()
        if only_video_id is None or vid == only_video_id
        for key in item["hypothesis_keys"]
    }
    if only_video_id is not None and only_video_id not in manifest["videos"]:
        _bad("sre_manifest_scope", "Video absent from pinned SRE manifest")
    observed = set()
    for row in rows:
        vid = row.get("video_id")
        key = str(row.get("category_key"))
        pair = (vid, key)
        if pair in observed:
            _bad("sre_duplicate_result", "Duplicate SRE result pair")
        if pair not in expected:
            _bad("sre_unexpected_result", "Unexpected video/category result")
        observed.add(pair)
        item = manifest["videos"][vid]
        if (row.get("content_version_id") != item["content_version_id"] or
                row.get("result_content_sha256") != item["content_sha256"]):
            _bad("sre_content_mismatch", "SRE classified content differs from pinned manifest")
        # A real result must identify its actual immutable source content.
        if not row.get("result_id") or type(row.get("result_id")) is not int:
            _bad("sre_result_invalid", "Missing SRE result ID")
        if row.get("model_decision") == "match":
            _evidence_grounded(row)
        if row.get("model_decision") == "not_evaluated" and not row.get("reason"):
            _bad("sre_not_evaluated_reason", "not_evaluated needs a terminal reason")
        trace = row.get("stage_trace")
        if not isinstance(trace, list) or not trace:
            _bad("sre_stage_unverified", "SRE result lacks verified stage provenance")
        for stage in trace:
            if not isinstance(stage, dict):
                _bad("sre_stage_unverified", "Malformed stage provenance")
            if stage.get("synthetic") is True or any(
                isinstance(stage.get(field), str) and
                re.search(r"(^|[/._:-])(offline|fake|fixture|mock|synthetic)([/._:-]|$)",
                          stage[field], flags=re.I)
                for field in ("provider", "model_id", "model_ref", "stage_id")):
                _bad("sre_synthetic_stage", "Synthetic stage in LIVE classification")
    if observed != expected:
        _bad("sre_coverage_incomplete",
             "SRE classification misses expected video/hypothesis pairs")
    return {"manifest_digest": manifest["digest"],
            "expected_pairs": len(expected),
            "verified_videos": len({v for v, _ in observed})}
