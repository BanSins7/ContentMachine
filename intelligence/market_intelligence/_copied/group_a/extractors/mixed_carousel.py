"""GA-MOD-17: exact-asset A003 video slides in authorized mixed carousels.

All clocks are local to the individual video asset, NEVER swipe timestamps.
No automatic video A003 creation (GA18 owns cross-stream assembly/writes).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re

from ..contracts_v1 import canonical_sha256, media_manifest_v1
from .carousel import CarouselError, _read_image, _recognized_text, _scope
from .ocr import OCRConfig, OCRError, _identity, _checked_frame
from .audio_decode import DecodedAudio

SCHEMA = "ga-mixed-carousel/v1"
METHOD = "group-a-mixed-carousel-v1"
A003_BINDING = "ga-video-slide-a003/v1"
DIGEST = re.compile(r"^[0-9a-f]{64}$")
RATIO = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")
SECOND = re.compile(r"^(0|[1-9][0-9]*)\.[0-9]{3}$")
TIMELINE_KINDS = frozenset(("scene", "speech", "screen_text", "audio"))
MAX_SEGMENTS = 2000


def _time(value, *, positive=False):
    if type(value) is not str or not SECOND.fullmatch(value):
        raise CarouselError("video_local_time_invalid")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CarouselError("video_local_time_invalid") from exc
    if parsed > Decimal("600") or (parsed <= 0 if positive else parsed < 0):
        raise CarouselError("video_local_time_invalid")
    return parsed


def _bind_video(manifest, item, raw):
    """Validate exact A003/source/asset/clock/segment binding, never URL match."""
    if type(raw) is not dict or set(raw) != {
        "schema_version", "analysis_id", "media_asset_id", "source_media_sha256",
        "video_id", "source_id", "source_policy_version_id", "state",
        "duration_sec", "aspect_ratio", "audio_status", "timeline", "binding_sha256"
    } or raw["schema_version"] != A003_BINDING:
        raise CarouselError("a003_binding_contract_invalid")
    if (type(raw["analysis_id"]) is not int or raw["analysis_id"] < 1
        or type(raw["media_asset_id"]) is not int
        or raw["media_asset_id"] != item["media_asset_id"]
        or raw["source_media_sha256"] != item["sha256"]
        or type(raw["video_id"]) is not int or raw["video_id"] != manifest["video_id"]
        or raw["source_id"] != manifest["source_id"]
        or type(raw["source_policy_version_id"]) is not int
        or raw["source_policy_version_id"] != manifest["source_policy_version_id"]):
        raise CarouselError("a003_asset_lineage_mismatch")
    if raw["state"] not in ("completed", "partial"):
        raise CarouselError("a003_analysis_state_invalid")
    if raw["audio_status"] not in ("not_run", "complete", "partial", "unavailable"):
        raise CarouselError("a003_audio_status_invalid")
    duration = _time(raw["duration_sec"], positive=True) if raw["duration_sec"] is not None else None
    if raw["state"] == "completed" and duration is None:
        raise CarouselError("a003_completed_without_duration")
    if "duration_sec" in item and raw["duration_sec"] != item["duration_sec"]:
        raise CarouselError("a003_manifest_duration_mismatch")
    ratio = raw["aspect_ratio"]
    if ratio is not None and (type(ratio) is not str or not RATIO.fullmatch(ratio)):
        raise CarouselError("a003_aspect_ratio_invalid")
    if raw["state"] == "completed" and ratio is None:
        raise CarouselError("a003_completed_without_geometry")
    seq = raw["timeline"]
    if type(seq) is not list or len(seq) > MAX_SEGMENTS:
        raise CarouselError("a003_timeline_invalid")
    previous = None
    for seg in seq:
        if type(seg) is not dict or set(seg) != {"kind", "start_sec", "end_sec"} or seg["kind"] not in TIMELINE_KINDS:
            raise CarouselError("a003_timeline_invalid")
        start = _time(seg["start_sec"])
        end = _time(seg["end_sec"], positive=True)
        if duration is None or not start < end <= duration:
            raise CarouselError("a003_timeline_out_of_bounds")
        key = (start, end, seg["kind"])
        if previous is not None and key < previous:
            raise CarouselError("a003_timeline_not_ordered")
        previous = key
    if type(raw["binding_sha256"]) is not str or not DIGEST.fullmatch(raw["binding_sha256"]):
        raise CarouselError("a003_binding_digest_invalid")
    if canonical_sha256({k: v for k, v in raw.items() if k != "binding_sha256"}) != raw["binding_sha256"]:
        raise CarouselError("a003_binding_digest_mismatch")
    return dict(raw)


def _video_bytes_current(item, open_ref):
    """Force GA05 current-file/hash check, without buffering video into RAM."""
    try:
        with open_ref(item["verified_local_path_ref"]) as stream:
            if not stream.read(1):
                raise CarouselError("video_asset_empty")
    except CarouselError:
        raise
    except Exception as exc:
        raise CarouselError("video_asset_unavailable") from exc


def _image_item(item, open_ref, model, backend, cfg):
    png, width, height, ratio = _read_image(item, open_ref)
    result = {
        "slide_index": item["index"], "item_type": "image",
        "media_asset_id": item["media_asset_id"], "asset_sha256": item["sha256"],
        "aspect_ratio": ratio, "duration_sec": None, "video_a_analysis_id": None,
        "video_binding_sha256": None, "video_timeline": [],
        "text_status": "unavailable", "on_screen_text": None,
        "screen_word_count": None, "ocr_confidence_pct": None,
        "reason_code": "ocr_backend_not_connected",
    }
    if model is not None:
        try:
            checked = _checked_frame({"width":width,"height":height},
                                      backend.process(png, cfg), model, cfg)
            words = checked["words"]
            result["on_screen_text"] = _recognized_text(words) if words else ""
            result["ocr_confidence_pct"] = checked["mean_engine_confidence_pct"]
            if checked["state"] == "partial_language":
                result["text_status"] = "partial"
                result["reason_code"] = "ocr_partial_language"
            else:
                result["text_status"] = "complete"
                result["screen_word_count"] = len(words)
                result["reason_code"] = None
        except OCRError as exc:
            result["reason_code"] = exc.code
        except Exception:
            result["reason_code"] = "ocr_backend_failed"
    return result


def _video_item(manifest, item, open_ref, read_a003):
    _video_bytes_current(item, open_ref)
    raw = read_a003(manifest, item)
    bound = None if raw is None else _bind_video(manifest, item, raw)
    complete = bound is not None and bound["state"] == "completed"
    return {
        "slide_index": item["index"], "item_type": "video",
        "media_asset_id": item["media_asset_id"], "asset_sha256": item["sha256"],
        "aspect_ratio": bound["aspect_ratio"] if bound else None,
        "duration_sec": bound["duration_sec"] if bound else None,
        "video_a_analysis_id": bound["analysis_id"] if bound else None,
        "video_binding_sha256": bound["binding_sha256"] if bound else None,
        "video_timeline": bound["timeline"] if bound else [],
        # An A003 video stream's timed OCR is not a complete static-slide
        # word count. Do not synthesize that summary from sparse spans.
        "text_status": "unavailable", "on_screen_text": None,
        "screen_word_count": None, "ocr_confidence_pct": None,
        "reason_code": None if complete else ("a003_analysis_partial" if bound else "a003_analysis_missing"),
    }


def _audio_proof(item, audio_proofs):
    proof = audio_proofs.get(item["media_asset_id"])
    if proof is None:
        return None
    if not isinstance(proof, DecodedAudio) or proof.source_sha256 != item["sha256"]:
        raise CarouselError("audio_evidence_asset_mismatch")
    if proof.probe.state not in ("present", "absent"):
        raise CarouselError("audio_evidence_invalid")
    return proof.probe.state == "present"


def _summary(items, audio_states):
    image_ok = all(x["text_status"] == "complete" for x in items if x["item_type"] == "image")
    videos_ok = all(x["reason_code"] is None for x in items if x["item_type"] == "video")
    complete_text = all(x["text_status"] == "complete" for x in items)
    text_status = ("complete" if complete_text else "unavailable"
                   if all(x["text_status"] == "unavailable" for x in items) else "partial")
    audio_status = ("complete" if all(x is not None for x in audio_states) else
                    "unavailable" if all(x is None for x in audio_states) else "partial")
    has_audio = ("true" if True in audio_states else
                 "false" if audio_states and all(x is False for x in audio_states) else "unknown")
    return {
        "analysis_state": "completed" if image_ok and videos_ok else "partial",
        "text_status": text_status, "audio_status": audio_status, "has_audio": has_audio,
        "first_text_slide": next((x["slide_index"] for x in items if x["screen_word_count"] and x["screen_word_count"] > 0), None)
            if complete_text else None,
        "total_screen_word_count": sum(x["screen_word_count"] for x in items)
            if complete_text else None,
    }


def extract_mixed_carousel(manifest, *, open_ref, verify_current, read_a003,
                           backend=None, config=None, audio_proofs=None):
    """Observe ordered image/video assets, reuse only pinned A003 video facts.

    read_a003(manifest, item): a pinned trustworthy A003 read or None.
    audio_proofs: optional asset-id -> already-authorized GA09 DecodedAudio.
    No consumer can infer publication-wide timeline/swipe duration.
    """
    try:
        m = media_manifest_v1(manifest)
    except (ValueError, TypeError) as exc:
        raise CarouselError("invalid_media_manifest") from exc
    if m["content_kind"] != "mixed_carousel":
        raise CarouselError("not_mixed_carousel")
    if not callable(open_ref) or not callable(verify_current) or not callable(read_a003):
        raise CarouselError("trusted_mixed_ports_required")
    cfg = OCRConfig(frame_budget=1) if config is None else config
    if not isinstance(cfg, OCRConfig):
        raise CarouselError("invalid_ocr_config")
    model = None if backend is None else _identity(backend)
    if model is not None and model["device"] != cfg.device:
        raise CarouselError("ocr_device_mismatch")
    if audio_proofs is None:
        audio_proofs = {}
    if type(audio_proofs) is not dict:
        raise CarouselError("invalid_audio_proofs")
    videos = {x["media_asset_id"] for x in m["items"] if x["kind"] == "video"}
    if any(type(key) is not int or key not in videos for key in audio_proofs):
        raise CarouselError("orphan_audio_proof")
    items, audio_states = [], []
    for item in m["items"]:
        scope = _scope(m, item)
        if verify_current(scope) is not True:
            raise CarouselError("media_rights_unverified")
        if item["kind"] == "image":
            result = _image_item(item, open_ref, model, backend, cfg)
        else:
            result = _video_item(m, item, open_ref, read_a003)
            audio_states.append(_audio_proof(item, audio_proofs))
        if verify_current(scope) is not True:
            raise CarouselError("media_rights_revoked_during_analysis")
        items.append(result)
    summary = _summary(items, audio_states)
    core = {
        "schema_version": SCHEMA, "method_version": METHOD,
        "project_id": m["project_id"], "qdp_run_id": m["qdp_run_id"],
        "video_id": m["video_id"], "source_id": m["source_id"],
        "source_policy_version_id": m["source_policy_version_id"],
        "qdp_input_sha256": m["qdp_input_sha256"],
        "manifest_sha256": m["manifest_sha256"],
        "content_kind": "mixed_carousel", "slide_status": "complete",
        "slide_count": len(items), "ocr_model": model, "ocr_config": cfg.wire(),
        "cta_slide_index": None, "items": items, **summary,
        "video_clock_semantics": "per_asset_local_zero",
        "publication_swipe_duration_sec": None,
    }
    return {**core, "observation_sha256": canonical_sha256(core)}


def validate_mixed_observation(manifest, raw):
    """Recompute digest and cross-slide truth before any A004 transaction."""
    try:
        m = media_manifest_v1(manifest)
    except (TypeError, ValueError) as exc:
        raise CarouselError("invalid_media_manifest") from exc
    if m["content_kind"] != "mixed_carousel":
        raise CarouselError("not_mixed_carousel")
    if type(raw) is not dict or set(raw) != {
        "schema_version", "method_version", "project_id", "qdp_run_id",
        "video_id", "source_id", "source_policy_version_id", "qdp_input_sha256",
        "manifest_sha256", "content_kind", "slide_status", "slide_count",
        "ocr_model", "ocr_config", "cta_slide_index", "items", "analysis_state",
        "text_status", "audio_status", "has_audio", "first_text_slide",
        "total_screen_word_count", "video_clock_semantics",
        "publication_swipe_duration_sec", "observation_sha256"
    } or raw["schema_version"] != SCHEMA or raw["method_version"] != METHOD:
        raise CarouselError("invalid_mixed_observation")
    if (canonical_sha256({k:v for k,v in raw.items() if k!="observation_sha256"})
        != raw["observation_sha256"]):
        raise CarouselError("mixed_observation_digest_mismatch")
    if (any(raw.get(k) != m[k] for k in ("project_id","qdp_run_id","video_id",
        "source_id","source_policy_version_id","qdp_input_sha256","manifest_sha256",
        "content_kind"))
        or raw["slide_count"] != len(m["items"])
        or raw["slide_status"] != "complete"
        or raw["cta_slide_index"] is not None
        or raw["video_clock_semantics"] != "per_asset_local_zero"
        or raw["publication_swipe_duration_sec"] is not None
        or type(raw["items"]) is not list or len(raw["items"]) != len(m["items"])):
        raise CarouselError("mixed_observation_lineage_invalid")
    for origin, item in zip(m["items"], raw["items"]):
        if type(item) is not dict or set(item) != {
            "slide_index","item_type","media_asset_id","asset_sha256","aspect_ratio",
            "duration_sec","video_a_analysis_id","video_binding_sha256","video_timeline",
            "text_status","on_screen_text","screen_word_count","ocr_confidence_pct",
            "reason_code"
        } or (item["slide_index"],item["item_type"],item["media_asset_id"],item["asset_sha256"]) != (
            origin["index"],origin["kind"],origin["media_asset_id"],origin["sha256"]):
            raise CarouselError("mixed_slide_lineage_invalid")
        if item["item_type"] == "image":
            if (item["duration_sec"] is not None or item["video_a_analysis_id"] is not None
                or item["video_binding_sha256"] is not None or item["video_timeline"] != []
                or type(item["aspect_ratio"]) is not str
                or not RATIO.fullmatch(item["aspect_ratio"])
                or item["text_status"] not in ("complete", "partial", "unavailable")):
                raise CarouselError("mixed_image_claim_invalid")
            if item["text_status"] == "complete":
                if (type(item["screen_word_count"]) is not int
                    or item["screen_word_count"] < 0
                    or type(item["on_screen_text"]) is not str
                    or (item["on_screen_text"]=="") != (item["screen_word_count"]==0)
                    or item["reason_code"] is not None):
                    raise CarouselError("mixed_image_text_invalid")
            elif item["screen_word_count"] is not None:
                raise CarouselError("mixed_image_partial_count")
        else:
            if (item["text_status"] != "unavailable" or item["on_screen_text"] is not None
                or item["screen_word_count"] is not None or item["ocr_confidence_pct"] is not None):
                raise CarouselError("mixed_video_text_unverified")
            if item["video_a_analysis_id"] is None:
                if (item["duration_sec"] is not None or item["video_binding_sha256"] is not None
                    or item["aspect_ratio"] is not None or item["video_timeline"] != []
                    or item["reason_code"] != "a003_analysis_missing"):
                    raise CarouselError("mixed_video_analysis_missing")
            else:
                if type(item["video_a_analysis_id"]) is not int or item["video_a_analysis_id"] <= 0:
                    raise CarouselError("mixed_video_analysis_id_invalid")
                if type(item["video_binding_sha256"]) is not str or not DIGEST.fullmatch(item["video_binding_sha256"]):
                    raise CarouselError("mixed_video_binding_unpinned")
                if item["duration_sec"] is not None:
                    duration = _time(item["duration_sec"], positive=True)
                    for seg in item["video_timeline"]:
                        if (type(seg) is not dict or set(seg) != {"kind","start_sec","end_sec"}
                            or seg["kind"] not in TIMELINE_KINDS
                            or not 0 <= _time(seg["start_sec"]) < _time(seg["end_sec"],positive=True) <= duration):
                            raise CarouselError("mixed_video_timeline_invalid")
                if item["reason_code"] not in (None, "a003_analysis_partial"):
                    raise CarouselError("mixed_video_state_invalid")
    # Aggregate truth and completed state derive exclusively from authenticated
    # per-item states. Audio evidence cannot be reconstructed from this wire
    # without GA09 raw immutable attestations; restrict global cross-fields.
    if raw["has_audio"] not in ("true","false","unknown") or raw["audio_status"] not in ("complete","partial","unavailable"):
        raise CarouselError("mixed_audio_claim_invalid")
    if raw["text_status"] not in ("partial","unavailable") or raw["first_text_slide"] is not None or raw["total_screen_word_count"] is not None:
        raise CarouselError("mixed_global_text_claim_invalid")
    complete = (all(x["text_status"]=="complete" for x in raw["items"] if x["item_type"]=="image")
                and all(x["reason_code"] is None for x in raw["items"] if x["item_type"]=="video"))
    if raw["analysis_state"] != ("completed" if complete else "partial"):
        raise CarouselError("mixed_global_state_invalid")
    return dict(raw)
