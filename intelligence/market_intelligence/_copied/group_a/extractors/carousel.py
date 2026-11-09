"""GA-MOD-16: offline, rights-fenced image-post / photo-carousel evidence.

Order and image kind come ONLY from the GA05 byte-verified media manifest.
This stage does not analyze mixed/video slides, infer swipe durations, infer
CTA/audio, download assets, or automatically start a worker.
"""
from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from math import gcd
import re

from ..contracts_v1 import canonical_sha256, media_manifest_v1
from .ocr import OCRConfig, OCRError, _checked_frame, _identity

SCHEMA = "ga-photo-carousel/v1"
METHOD = "group-a-photo-carousel-v1"
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_SIDE = 16000
MAX_SLIDES = 100
SHA = re.compile(r"^[0-9a-f]{64}$")


class CarouselError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Group A carousel rejected: " + code)


def _verify_manifest(manifest):
    try:
        media_manifest_v1(manifest)
    except (ValueError, TypeError) as exc:
        raise CarouselError("invalid_media_manifest") from exc
    if manifest["content_kind"] not in ("image_post", "photo_carousel"):
        raise CarouselError("mixed_or_video_out_of_scope")
    if len(manifest["items"]) > MAX_SLIDES or any(
        item["kind"] != "image" or "duration_sec" in item
        for item in manifest["items"]
    ):
        raise CarouselError("non_image_slide")
    return manifest


def _read_image(item, open_ref):
    """Read a GA05-authorized, already-open descriptor, never a local path."""
    try:
        with open_ref(item["verified_local_path_ref"]) as source:
            payload = source.read(MAX_IMAGE_BYTES + 1)
            if len(payload) > MAX_IMAGE_BYTES:
                raise CarouselError("image_byte_budget_exceeded")
            if len(payload) != item["byte_size"] or sha256(payload).hexdigest() != item["sha256"]:
                raise CarouselError("image_digest_mismatch")
    except CarouselError:
        raise
    except Exception as exc:
        raise CarouselError("image_ref_unavailable") from exc
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise CarouselError("pillow_unavailable") from exc
    try:
        with Image.open(BytesIO(payload)) as image:
            if (image.width < 1 or image.height < 1 or image.width > MAX_SIDE
                or image.height > MAX_SIDE or image.width * image.height > MAX_PIXELS):
                raise CarouselError("image_pixel_budget_exceeded")
            if getattr(image, "n_frames", 1) != 1:
                raise CarouselError("animated_image_out_of_scope")
            # EXIF orientation is applied to both the image OCR and its ratio.
            oriented = ImageOps.exif_transpose(image)
            if (oriented.width > MAX_SIDE or oriented.height > MAX_SIDE
                or oriented.width * oriented.height > MAX_PIXELS):
                raise CarouselError("image_pixel_budget_exceeded")
            converted = oriented.convert("RGB")
            width, height = converted.size
            with BytesIO() as out:
                converted.save(out, format="PNG")
                png = out.getvalue()
    except CarouselError:
        raise
    except Exception as exc:
        raise CarouselError("image_decode_failed") from exc
    divisor = gcd(width, height)
    return png, width, height, f"{width // divisor}:{height // divisor}"


def _recognized_text(words):
    # Preserve OCR line boundaries and observed recognition order.
    lines = []
    for word in words:
        if lines and lines[-1][0] == word["line_ref"]:
            lines[-1][1].append(word["text"])
        else:
            lines.append((word["line_ref"], [word["text"]]))
    return "\n".join(" ".join(words) for _, words in lines)


def _scope(manifest, item):
    return {
        "project_id": manifest["project_id"],
        "qdp_run_id": manifest["qdp_run_id"],
        "video_id": manifest["video_id"],
        "qdp_input_sha256": manifest["qdp_input_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "source_id": manifest["source_id"],
        "source_policy_version_id": manifest["source_policy_version_id"],
        "media_asset_id": item["media_asset_id"],
        "asset_sha256": item["sha256"],
    }


def extract_photo_carousel(manifest, *, open_ref, verify_current,
                           backend=None, config=None):
    """Extract actual dimensions and per-image OCR using GA05 ordered refs.

    open_ref(ref) -> context manager yielding a GA05-verified binary stream.
    verify_current(scope) -> exactly True, rechecks live project/QDP/source
    authorization for EVERY image, including repeated/cached calls.
    Missing OCR is unknown, NOT verified absence of text. No SQL writes.
    """
    m = _verify_manifest(manifest)
    if not callable(open_ref) or not callable(verify_current):
        raise CarouselError("trusted_media_ports_required")
    cfg = OCRConfig(frame_budget=1) if config is None else config
    if not isinstance(cfg, OCRConfig):
        raise CarouselError("invalid_ocr_config")
    model = None if backend is None else _identity(backend)
    if model is not None and model["device"] != cfg.device:
        raise CarouselError("ocr_device_mismatch")

    items = []
    for item in m["items"]:
        if verify_current(_scope(m, item)) is not True:
            raise CarouselError("media_rights_unverified")
        png, width, height, ratio = _read_image(item, open_ref)
        result = {
            "slide_index": item["index"], "media_asset_id": item["media_asset_id"],
            "asset_sha256": item["sha256"], "item_type": "image",
            "width": width, "height": height, "aspect_ratio": ratio,
            "duration_sec": None, "text_status": "unavailable",
            "on_screen_text": None, "screen_word_count": None,
            "ocr_confidence_pct": None, "reason_code": "ocr_backend_not_connected",
        }
        if model is not None:
            try:
                raw = backend.process(png, cfg)
                checked = _checked_frame({"width": width, "height": height},
                                         raw, model, cfg)
                words = checked["words"]
                if checked["state"] == "partial_language":
                    result["text_status"] = "partial"
                    result["reason_code"] = "ocr_partial_language"
                    # Positive OCR words are still evidence; do not claim a
                    # complete slide-wide count with incomplete languages.
                    result["on_screen_text"] = _recognized_text(words) if words else None
                    result["ocr_confidence_pct"] = checked["mean_engine_confidence_pct"]
                else:
                    result["text_status"] = "complete"
                    result["on_screen_text"] = _recognized_text(words)
                    result["screen_word_count"] = len(words)
                    result["ocr_confidence_pct"] = checked["mean_engine_confidence_pct"]
                    result["reason_code"] = None
            except OCRError as exc:
                result["reason_code"] = exc.code
            except Exception:
                result["reason_code"] = "ocr_backend_failed"
        # A file may be revoked while OCR runs. Do not return an observation
        # whose media authorization is stale at the end of this slide.
        if verify_current(_scope(m, item)) is not True:
            raise CarouselError("media_rights_revoked_during_ocr")
        items.append(result)
    text_complete = all(item["text_status"] == "complete" for item in items)
    text_status = ("complete" if text_complete else
                   "unavailable" if all(x["text_status"] == "unavailable" for x in items)
                   else "partial")
    core = {
        "schema_version": SCHEMA, "method_version": METHOD,
        "project_id": m["project_id"], "qdp_run_id": m["qdp_run_id"],
        "video_id": m["video_id"], "source_id": m["source_id"],
        "source_policy_version_id": m["source_policy_version_id"],
        "qdp_input_sha256": m["qdp_input_sha256"],
        "manifest_sha256": m["manifest_sha256"],
        "content_kind": m["content_kind"], "slide_status": "complete",
        "text_status": text_status,
        "analysis_state": "completed" if text_complete else "partial",
        "ocr_model": model, "ocr_config": cfg.wire(),
        "has_audio": "unknown", "cta_slide_index": None,
        "slide_count": len(items),
        "first_text_slide": next((x["slide_index"] for x in items if x["screen_word_count"] > 0), None)
            if text_complete else None,
        "total_screen_word_count": sum(x["screen_word_count"] for x in items)
            if text_complete else None,
        "items": items,
    }
    return {**core, "observation_sha256": canonical_sha256(core)}


def validate_photo_observation(manifest, observation):
    """Fail closed before translating evidence to a durable A004 SQL write."""
    m = _verify_manifest(manifest)
    if type(observation) is not dict or not SHA.fullmatch(str(observation.get("observation_sha256", ""))):
        raise CarouselError("invalid_photo_observation")
    data = dict(observation)
    digest = data.pop("observation_sha256")
    try:
        if canonical_sha256(data) != digest:
            raise CarouselError("photo_observation_digest_mismatch")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, CarouselError):
            raise
        raise CarouselError("invalid_photo_observation") from exc
    expected = ("project_id", "qdp_run_id", "video_id", "source_id",
                "source_policy_version_id", "qdp_input_sha256", "manifest_sha256",
                "content_kind")
    if (data.get("schema_version") != SCHEMA or data.get("method_version") != METHOD
        or any(data.get(k) != m[k] for k in expected)
        or data.get("slide_status") != "complete"
        or data.get("has_audio") != "unknown"
        or data.get("cta_slide_index") is not None
        or data.get("slide_count") != len(m["items"])
        or type(data.get("items")) is not list
        or len(data["items"]) != len(m["items"])):
        raise CarouselError("photo_observation_lineage_invalid")
    complete = True
    total = 0
    first = None
    for asset, obs in zip(m["items"], data["items"]):
        if type(obs) is not dict or any(
            obs.get(k) != value for k, value in (
                ("slide_index", asset["index"]), ("item_type", "image"),
                ("media_asset_id", asset["media_asset_id"]),
                ("asset_sha256", asset["sha256"]), ("duration_sec", None),
            )
        ):
            raise CarouselError("photo_slide_lineage_invalid")
        w,h=obs.get("width"),obs.get("height")
        if (type(w) is not int or type(h) is not int or not 1<=w<=MAX_SIDE
            or not 1<=h<=MAX_SIDE or w*h>MAX_PIXELS
            or obs.get("aspect_ratio") != f"{w//gcd(w,h)}:{h//gcd(w,h)}"):
            raise CarouselError("photo_slide_geometry_invalid")
        state=obs.get("text_status")
        if state not in ("complete","partial","unavailable"):
            raise CarouselError("photo_slide_text_state_invalid")
        if state == "complete":
            if (type(obs.get("on_screen_text")) is not str
                or type(obs.get("screen_word_count")) is not int
                or obs["screen_word_count"] < 0
                or (obs["screen_word_count"] == 0) != (obs["on_screen_text"] == "")):
                raise CarouselError("photo_slide_text_invalid")
            if obs["screen_word_count"] and first is None:
                first=asset["index"]
            total+=obs["screen_word_count"]
        else:
            complete=False
            if obs.get("screen_word_count") is not None:
                raise CarouselError("partial_ocr_cannot_count_all_words")
    status=("complete" if complete else "unavailable"
            if all(x["text_status"]=="unavailable" for x in data["items"]) else "partial")
    if (data.get("text_status")!=status or
        data.get("analysis_state")!=("completed" if complete else "partial")
        or data.get("first_text_slide") != (first if complete else None)
        or data.get("total_screen_word_count") != (total if complete else None)):
        raise CarouselError("photo_observation_truth_invalid")
    return dict(observation)
