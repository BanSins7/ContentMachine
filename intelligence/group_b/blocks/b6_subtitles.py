"""GB-MOD-18: source-pinned, detector-attested B6 subtitle classification.

No OCR, video decoding, external calls or implicit model approval here. A signed
or otherwise independently trusted detector must attest full ORIGINAL video
inspection, that a moving caption is actually a subtitle (not a title/slide),
and the measured visual style. Missing OCR or inspection means UNKNOWN, never
SQL005 subtitle_type='none'. The caller rechecks media bytes and rights.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from ..contracts import canonical_hash
from ..context.manifest import MediaEvidenceManifest
from ..context.builder import ContextPacket
from ..errors import GroupBError

VERSION = "group-b-b6-subtitles/v1"
KINDS = ("video", "slideshow_video")
TYPES = ("none", "standard", "dynamic")
STYLES = {
    "position": ("top", "center", "bottom"),
    "font_size": ("small", "medium", "large"),
    "font_style": ("regular", "bold", "extra_bold"),
    "background_style": ("none", "outline", "shadow", "box"),
    "display_mode": ("line", "phrase", "word_by_word"),
}
STYLE_FIELDS = ("position", "font_size", "font_style", "text_color",
                "highlight_color", "background_style", "display_mode")
_SCAN_KEYS = frozenset((
    "schema_version", "project_id", "b_run_id", "video_id",
    "qdp_run_id", "ga_result_id", "ga_result_sha256",
    "source_policy_version_id", "source_evidence_manifest_sha256",
    "content_kind", "inspection_scope", "subtitle_type", "confidence",
    "subtitle_ocr_refs", "subtitle_frame_refs", "dynamic_frame_refs",
    "style_evidence_refs", "style", "inspector_evidence_sha256",
))
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_HEX = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")


class B6SubtitleError(GroupBError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("b6_subtitles_unavailable", "b6: " + reason)


def _deny(code):
    raise B6SubtitleError(code)


def _sha(v):
    return type(v) is str and _SHA.fullmatch(v) is not None


def _refs(data, universe):
    if (type(data) is not list or len(data) > 200 or
        len(set(map(str, data))) != len(data) or
        any(not _sha(x) or x not in universe for x in data)):
        _deny("invalid_or_unanchored_evidence_refs")
    return tuple(data)


def _style_valid(style, kind):
    if type(style) is not dict or set(style) != set(STYLE_FIELDS):
        _deny("subtitle_style_shape_invalid")
    if kind == "none":
        if any(value is not None for value in style.values()):
            _deny("none_requires_all_null_style")
    else:
        for key, valid in STYLES.items():
            if style[key] not in valid:
                _deny("subtitle_style_enum_invalid")
        if (type(style["text_color"]) is not str or
            not _HEX.fullmatch(style["text_color"])):
            _deny("subtitle_text_color_invalid")
        if (style["highlight_color"] is not None and
            (type(style["highlight_color"]) is not str or
             not _HEX.fullmatch(style["highlight_color"]))):
            _deny("subtitle_highlight_color_invalid")


def _source(manifest):
    if (not isinstance(manifest, MediaEvidenceManifest) or
        not manifest.verify_digest() or manifest.content_kind not in KINDS or
        len(manifest.assets) != 1 or
        manifest.assets[0].media_kind != "video" or
        type(manifest.assets[0].duration_ms) is not int or
        manifest.assets[0].duration_ms <= 0):
        _deny("video_original_manifest_unverified")
    return manifest.assets[0]


def _context(manifest, packet):
    """Replay B6 selected/omitted evidence against source, not packet counters."""
    asset = _source(manifest)
    if not isinstance(packet, ContextPacket) or not packet.verify_digest():
        _deny("b6_context_invalid")
    p = packet.payload
    identity = {
        "schema_version": "group-b-context/v1", "block": "B6",
        "project_id": manifest.project_id, "b_run_id": manifest.b_run_id,
        "video_id": manifest.video_id, "qdp_run_id": manifest.qdp_run_id,
        "ga_result_id": manifest.ga_result_id,
        "ga_result_sha256": manifest.ga_result_sha256,
        "source_policy_version_id": manifest.source_policy_version_id,
        "source_evidence_manifest_sha256": manifest.manifest_sha256,
        "input_content_kind": manifest.content_kind,
        "delivery_state": "not_sent",
        "media_delivery_state": "opaque_refs_not_attached",
    }
    if any(p.get(key) != value for key, value in identity.items()):
        _deny("b6_context_lineage_invalid")
    source = {o.ref_sha256: o for o in manifest.observations}
    if len(source) != len(manifest.observations):
        _deny("b6_duplicate_source_ref")
    relevant = {ref: o for ref, o in source.items()
                if o.kind in ("ocr_span", "sampled_frame")}
    frames = {ref: o for ref, o in relevant.items()
              if o.kind == "sampled_frame"}
    for ref, o in relevant.items():
        if (o.media_asset_id != asset.asset_id or
            o.asset_sha256 != asset.source_sha256 or
            o.timebase != "video_absolute" or
            type(o.start_ms) is not int or
            type(o.end_ms) is not int or
            not 0 <= o.start_ms <= o.end_ms <= asset.duration_ms):
            _deny("b6_original_video_timebase_invalid")
        if o.kind == "sampled_frame" and (o.start_ms != o.end_ms or
                                          o.frame_ref_sha256 is not None):
            _deny("b6_invalid_frame_timing")
        if o.kind == "ocr_span":
            frame = frames.get(o.frame_ref_sha256)
            if (frame is None or o.end_ms <= o.start_ms or
                not o.start_ms <= frame.start_ms <= o.end_ms):
                _deny("b6_ocr_to_video_frame_unverified")
    entries = p.get("entries")
    omitted = p.get("omitted")
    coverage = p.get("coverage")
    if (type(entries) is not list or len(entries) > 2000 or
        type(omitted) is not list or len(omitted) > 2000 or
        type(coverage) is not dict):
        _deny("b6_context_coverage_invalid")
    selected = set()
    for entry in entries:
        if type(entry) is not dict or not _sha(entry.get("ref_sha256")):
            _deny("b6_selected_entry_invalid")
        ref = entry["ref_sha256"]
        o = relevant.get(ref)
        if o is None or ref in selected:
            _deny("b6_selected_ref_not_original")
        for field, value in {
            "kind": o.kind, "content_sha256": o.content_sha256,
            "media_asset_id": o.media_asset_id, "asset_sha256": o.asset_sha256,
            "start_ms": o.start_ms, "end_ms": o.end_ms,
            "slide_index": o.slide_index,
            "frame_ref_sha256": o.frame_ref_sha256,
            "carrier": ("unresolved_image_ref" if o.kind == "sampled_frame"
                        else "untrusted_utf8_data"),
            "content_in_packet": o.kind == "ocr_span",
        }.items():
            if entry.get(field) != value:
                _deny("b6_selected_evidence_provenance_invalid")
        expected_keys = {
            "kind", "ref_sha256", "content_sha256", "media_asset_id",
            "asset_sha256", "start_ms", "end_ms", "slide_index",
            "frame_ref_sha256", "carrier", "content_in_packet",
        }
        if o.kind == "ocr_span":
            expected_keys.add("untrusted_text")
            from hashlib import sha256
            value = entry.get("untrusted_text")
            if type(value) is not str or sha256(value.encode("utf-8")).hexdigest() != o.content_sha256:
                _deny("b6_ocr_text_hash_invalid")
        if set(entry) != expected_keys:
            _deny("b6_selected_evidence_shape_invalid")
        selected.add(ref)
    omissions = set()
    for entry in omitted:
        if (type(entry) is not dict or set(entry) !=
            {"kind", "ref_sha256", "reason"} or
            not _sha(entry["ref_sha256"]) or
            entry["ref_sha256"] not in relevant or
            entry["kind"] != relevant[entry["ref_sha256"]].kind or
            type(entry["reason"]) is not str or not entry["reason"] or
            entry["ref_sha256"] in selected or
            entry["ref_sha256"] in omissions):
            _deny("b6_omission_forged")
        omissions.add(entry["ref_sha256"])
    if selected | omissions != set(relevant):
        _deny("b6_hidden_original_evidence")
    required = {
        "source_observations": len(source),
        "relevant_observations": len(relevant),
        "selected_observations": len(selected),
        "omitted_relevant": len(omissions),
        "selected_image_refs": sum(ref in selected for ref in frames),
        "available_frame_refs": len(frames),
        "available_slide_refs": 0,
    }
    if any(type(coverage.get(k)) is not int or coverage[k] != v
           for k, v in required.items()):
        _deny("b6_coverage_counter_forged")
    if (type(coverage.get("coverage_complete")) is not bool or
        coverage["coverage_complete"] != (not omissions)):
        _deny("b6_coverage_claim_forged")
    return source, selected


@dataclass(frozen=True, slots=True)
class B6SubtitleProposal:
    payload: dict
    proposal_sha256: str
    authenticated_detector: bool

    def verify_digest(self):
        try:
            return canonical_hash(self.payload) == self.proposal_sha256
        except (TypeError, ValueError):
            return False

    @property
    def can_persist(self):
        p = self.payload
        return (self.authenticated_detector is True and self.verify_digest() and
                p.get("classification_status") == "inferred" and
                p.get("inspection_scope") == "full_original_video" and
                p.get("subtitle_type") in TYPES and
                all(k in p for k in STYLE_FIELDS))

    def db_fields(self):
        if not self.can_persist:
            _deny("b6_row_not_ready_for_sql")
        return {"subtitle_type": self.payload["subtitle_type"],
                **{key: self.payload[key] for key in STYLE_FIELDS}}


def unknown_b6(*, manifest: MediaEvidenceManifest, reason: str):
    _source(manifest)
    if type(reason) is not str or _REASON.fullmatch(reason) is None:
        _deny("reason_code_invalid")
    p = {
        "schema_version": VERSION, "project_id": manifest.project_id,
        "b_run_id": manifest.b_run_id, "video_id": manifest.video_id,
        "qdp_run_id": manifest.qdp_run_id,
        "ga_result_id": manifest.ga_result_id,
        "ga_result_sha256": manifest.ga_result_sha256,
        "source_policy_version_id": manifest.source_policy_version_id,
        "source_evidence_manifest_sha256": manifest.manifest_sha256,
        "content_kind": manifest.content_kind,
        "inspection_scope": "not_run", "subtitle_type": None,
        **{key: None for key in STYLE_FIELDS},
        "inspector_evidence_sha256": None, "scan_sha256": None,
        "subtitle_ocr_refs": [], "subtitle_frame_refs": [],
        "dynamic_frame_refs": [], "style_evidence_refs": [],
        "classification_status": "unknown", "review_reasons": [reason],
    }
    return B6SubtitleProposal(p, canonical_hash(p), False)


def validate_b6_subtitles(*, manifest: MediaEvidenceManifest,
                          packet: ContextPacket, scan: dict,
                          detector_verifier: object) -> B6SubtitleProposal:
    """Strictly validate an independently authenticated full-video subtitle scan.

    verify_b6_scan MUST validate the true media decoder/ASR/OCR/caption-vs-
    slide decision, temporal completeness and style measurement against source.
    A caller-supplied SHA alone is not a signature or proof.
    """
    source, selected = _context(manifest, packet)
    if type(scan) is not dict or set(scan) != _SCAN_KEYS:
        _deny("b6_detector_scan_shape_invalid")
    identity = {
        "schema_version": VERSION, "project_id": manifest.project_id,
        "b_run_id": manifest.b_run_id, "video_id": manifest.video_id,
        "qdp_run_id": manifest.qdp_run_id,
        "ga_result_id": manifest.ga_result_id,
        "ga_result_sha256": manifest.ga_result_sha256,
        "source_policy_version_id": manifest.source_policy_version_id,
        "source_evidence_manifest_sha256": manifest.manifest_sha256,
        "content_kind": manifest.content_kind,
    }
    if any(scan.get(k) != value for k, value in identity.items()):
        _deny("b6_detector_lineage_invalid")
    kind = scan["subtitle_type"]
    if kind not in TYPES + ("unknown",) or type(kind) is not str:
        _deny("b6_subtitle_type_invalid")
    scope = scan["inspection_scope"]
    if scope not in ("full_original_video", "partial"):
        _deny("b6_inspection_scope_invalid")
    confidence = scan["confidence"]
    if confidence not in ("high", "medium", "low"):
        _deny("b6_confidence_invalid")
    if not _sha(scan["inspector_evidence_sha256"]):
        _deny("b6_inspector_evidence_unanchored")
    ocr = _refs(scan["subtitle_ocr_refs"],
                {k for k,v in source.items() if v.kind=="ocr_span"} & selected)
    frames = _refs(scan["subtitle_frame_refs"],
                   {k for k,v in source.items() if v.kind=="sampled_frame"} & selected)
    motion = _refs(scan["dynamic_frame_refs"], set(frames))
    style_refs = _refs(scan["style_evidence_refs"], set(frames))
    _style_valid(scan["style"], kind if kind in TYPES else "none")
    review = []
    if scope != "full_original_video":
        review.append("original_video_not_exhaustively_inspected")
    if confidence == "low":
        review.append("subtitle_low_confidence")
    if kind == "unknown":
        review.append("subtitle_detection_unknown")
    elif kind == "none":
        if ocr or frames or motion or style_refs:
            _deny("none_cannot_cite_positive_subtitles")
    else:
        if not ocr or not frames or not style_refs:
            _deny("positive_subtitles_require_ocr_and_rendered_frames")
        # OCR must refer to an actual frame included in the caption evidence.
        if not all(source[ref].frame_ref_sha256 in frames for ref in ocr):
            _deny("caption_ocr_has_no_visual_frame")
        if kind == "dynamic":
            if scan["style"]["display_mode"] != "word_by_word":
                if len(motion) < 2 or len({source[x].start_ms for x in motion}) < 2:
                    _deny("dynamic_subtitles_without_temporal_motion_proof")
        elif motion or scan["style"]["display_mode"] == "word_by_word":
            _deny("standard_subtitle_with_dynamic_evidence")
        if not set(style_refs).issubset(set(frames)):
            _deny("subtitle_style_frames_unanchored")
    verifier = getattr(detector_verifier, "verify_b6_scan", None)
    if not callable(verifier):
        _deny("trusted_b6_detector_missing")
    try:
        authenticated = verifier(manifest, packet, scan)
    except Exception:
        _deny("trusted_b6_detector_failed")
    if authenticated is not True:
        _deny("trusted_b6_detector_failed")
    style = scan["style"]
    state = "needs_review" if review else "inferred"
    p = {
        **identity, "inspection_scope": scope,
        "subtitle_type": kind if kind in TYPES else None,
        **{k: style[k] for k in STYLE_FIELDS},
        "inspector_evidence_sha256": scan["inspector_evidence_sha256"],
        "scan_sha256": canonical_hash(scan),
        "subtitle_ocr_refs": list(ocr), "subtitle_frame_refs": list(frames),
        "dynamic_frame_refs": list(motion), "style_evidence_refs": list(style_refs),
        "classification_status": state,
        "review_reasons": sorted(set(review)),
    }
    return B6SubtitleProposal(p, canonical_hash(p), True)
