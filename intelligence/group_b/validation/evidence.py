"""GB-MOD-21 shared, fail-closed original-media evidence checks.

This is a SECOND line of defense for already domain-validated B proposals,
not a substitute for per-block receipt/decoder/semantic admission. A model,
JSON digest or unchecked caller 'approved' boolean cannot authenticate media.
"""
from __future__ import annotations

from hashlib import sha256
import re

from ..context.builder import ContextPacket
from ..context.manifest import MediaEvidenceManifest
from ..contracts import canonical_hash

_SHA=re.compile(r"[a-f0-9]{64}\Z")
TEXT_KINDS=frozenset(("transcript_segment","ocr_span"))
IMAGE_KINDS=frozenset(("sampled_frame","slide_ref"))
VIDEO_KINDS=frozenset(("video","slideshow_video"))
SELECTED_KEY_TYPES={
    "evidence_refs":TEXT_KINDS|IMAGE_KINDS|{"audio_event","scene"},
    "subtitle_ocr_refs":frozenset(("ocr_span",)),
    "subtitle_frame_refs":frozenset(("sampled_frame",)),
    "dynamic_frame_refs":frozenset(("sampled_frame",)),
    "style_evidence_refs":TEXT_KINDS|IMAGE_KINDS,
    "delivered_image_refs":IMAGE_KINDS,
    "delivered_visual_refs":IMAGE_KINDS,
    "delivered_text_refs":TEXT_KINDS,
    "sampled_frame_ref":frozenset(("sampled_frame",)),
}
REF_LIST_FIELDS=frozenset(SELECTED_KEY_TYPES)-{"sampled_frame_ref"}


def _hash(x):
    return type(x) is str and _SHA.fullmatch(x) is not None


def check_original_manifest(manifest:MediaEvidenceManifest)->tuple[str,...]:
    if not isinstance(manifest,MediaEvidenceManifest) or not manifest.verify_digest():
        return ("media_evidence_manifest_unverified",)
    if manifest.content_kind not in ("video","slideshow_video",
                                      "photo_carousel","mixed_carousel"):
        return ("source_content_kind_unsupported",)
    original={o.ref_sha256:o for o in manifest.observations}
    assets={a.asset_id:a for a in manifest.assets}
    if (len(original)!=len(manifest.observations) or
        len(assets)!=len(manifest.assets) or
        not assets):
        return ("source_media_or_ref_duplication",)
    for obs in manifest.observations:
        if not _hash(obs.ref_sha256) or not _hash(obs.content_sha256):
            return ("unverified_original_ref_sha",)
        asset=assets.get(obs.media_asset_id)
        if asset is None or asset.source_sha256!=obs.asset_sha256:
            return ("source_media_asset_sha_mismatch",)
        if obs.start_ms is not None or obs.end_ms is not None:
            if (type(obs.start_ms) is not int or
                type(obs.end_ms) is not int or
                obs.start_ms<0 or obs.end_ms<obs.start_ms or
                type(asset.duration_ms) is not int or
                obs.end_ms>asset.duration_ms):
                return ("original_time_outside_media",)
        if obs.kind=="slide_ref":
            if (asset.media_kind!="image" or
                type(obs.slide_index) is not int or
                obs.slide_index!=asset.slide_index or
                obs.start_ms is not None or obs.end_ms is not None):
                return ("slide_not_bound_to_original_media",)
        if obs.kind=="ocr_span" and obs.frame_ref_sha256 is not None:
            frame=original.get(obs.frame_ref_sha256)
            if (frame is None or frame.kind!="sampled_frame" or
                frame.media_asset_id!=obs.media_asset_id or
                type(frame.start_ms) is not int or
                obs.start_ms is None or obs.end_ms is None or
                not obs.start_ms<=frame.start_ms<=obs.end_ms):
                return ("ocr_frame_wrong_source_or_time",)
    return ()


def check_packet(manifest:MediaEvidenceManifest, packet:ContextPacket,
                 block:str):
    """Return (reason_codes, selected entries), never trust packet counters."""
    if not isinstance(packet,ContextPacket) or not packet.verify_digest():
        return ("unverified_context_packet",),{}
    p=packet.payload
    pins={
        "schema_version":"group-b-context/v1","block":block,
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "qdp_run_id":manifest.qdp_run_id,"video_id":manifest.video_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "input_content_kind":manifest.content_kind,
        "delivery_state":"not_sent",
        "media_delivery_state":"opaque_refs_not_attached",
    }
    if any(p.get(k)!=v for k,v in pins.items()):
        return ("context_not_from_current_original",),{}
    relevant_kinds={
        "B1":("transcript_segment","ocr_span","sampled_frame",
              "slide_ref","audio_event"),
        "B2":("sampled_frame","slide_ref"),
        "B3":("sampled_frame","slide_ref","scene","transcript_segment","ocr_span"),
        "B4":("sampled_frame","slide_ref"),
        "B6":("ocr_span","sampled_frame"),
        "B7":("transcript_segment","ocr_span","sampled_frame","slide_ref"),
        "B8":("transcript_segment","ocr_span","sampled_frame","slide_ref"),
    }[block]
    original={o.ref_sha256:o for o in manifest.observations
              if o.kind in relevant_kinds}
    entries=p.get("entries")
    omissions=p.get("omitted")
    coverage=p.get("coverage")
    if (type(entries) is not list or type(omissions) is not list or
        type(coverage) is not dict or len(entries)>2000 or
        len(omissions)>2000):
        return ("context_manifest_accounting_invalid",),{}
    selected={}
    for e in entries:
        if type(e) is not dict or not _hash(e.get("ref_sha256")):
            return ("evidence_ref_not_original",),{}
        ref=e["ref_sha256"]
        o=original.get(ref)
        if o is None or ref in selected:
            return ("evidence_ref_not_original",),{}
        wanted={
            "kind":o.kind,"ref_sha256":o.ref_sha256,
            "content_sha256":o.content_sha256,
            "media_asset_id":o.media_asset_id,
            "asset_sha256":o.asset_sha256,
            "start_ms":o.start_ms,"end_ms":o.end_ms,
            "slide_index":o.slide_index,
            "frame_ref_sha256":o.frame_ref_sha256,
            "carrier":("untrusted_utf8_data" if o.kind in TEXT_KINDS
                       else "unresolved_image_ref" if o.kind in IMAGE_KINDS
                       else "verified_observation_ref"),
            "content_in_packet":o.kind in TEXT_KINDS,
        }
        if any(e.get(key)!=value for key,value in wanted.items()):
            return ("context_evidence_metadata_forged",),{}
        keys=set(wanted)
        if o.kind in TEXT_KINDS:
            raw=e.get("untrusted_text")
            if (type(raw) is not str or
                sha256(raw.encode("utf-8")).hexdigest()!=o.content_sha256):
                return ("untrusted_asr_ocr_content_sha_mismatch",),{}
            keys.add("untrusted_text")
        if set(e)!=keys:
            return ("context_entry_unexpected_field",),{}
        selected[ref]=e
    missing=set()
    for item in omissions:
        if (type(item) is not dict or
            set(item)!={"kind","ref_sha256","reason"} or
            not _hash(item["ref_sha256"]) or
            item["ref_sha256"] not in original or
            item["kind"]!=original[item["ref_sha256"]].kind or
            type(item["reason"]) is not str or not item["reason"] or
            item["ref_sha256"] in selected or item["ref_sha256"] in missing):
            return ("omitted_media_ref_forged",),{}
        missing.add(item["ref_sha256"])
    if set(selected)|missing!=set(original):
        return ("source_evidence_hidden",),{}
    expected={
        "source_observations":len(manifest.observations),
        "relevant_observations":len(original),
        "selected_observations":len(selected),
        "omitted_relevant":len(missing),
        "selected_image_refs":sum(e["kind"] in IMAGE_KINDS for e in selected.values()),
        "available_frame_refs":sum(o.kind=="sampled_frame" for o in original.values()),
        "available_slide_refs":sum(o.kind=="slide_ref" for o in original.values()),
    }
    if (any(type(coverage.get(k)) is not int or coverage[k]!=v
            for k,v in expected.items()) or
        type(coverage.get("coverage_complete")) is not bool or
        coverage["coverage_complete"]!=(not missing)):
        return ("context_evidence_coverage_forged",),{}
    return (),selected


def check_claims(manifest:MediaEvidenceManifest, proposal_payload:dict,
                 *,packet:ContextPacket|None,block:str)->tuple[str,...]:
    """Re-check claimed refs, actual delivered video frames and time positions."""
    original={o.ref_sha256:o for o in manifest.observations}
    errors=[]
    claimed=[]
    def scan(value, *,field=None,depth=0):
        if depth>9:
            errors.append("claim_nesting_unbounded")
            return
        if type(value) is dict:
            for k,v in value.items():
                if k in REF_LIST_FIELDS:
                    if (type(v) is not list or len(v)>200 or
                        any(not _hash(x) for x in v) or
                        (all(type(x) is str for x in v) and
                         len(v)!=len(set(v)))):
                        errors.append("claim_refs_malformed")
                    else:
                        claimed.extend((k,x) for x in v)
                elif k=="sampled_frame_ref":
                    if v is not None:
                        claimed.append((k,v))
                else:
                    scan(v,field=k,depth=depth+1)
        elif type(value) is list:
            if len(value)>2000:
                errors.append("claim_rows_unbounded")
            else:
                for x in value:
                    scan(x,field=field,depth=depth+1)
    scan(proposal_payload)
    for field,ref in claimed:
        kind=SELECTED_KEY_TYPES[field]
        o=original.get(ref) if _hash(ref) else None
        if o is None or o.kind not in kind:
            errors.append("claim_ref_wrong_original_or_kind")
    if packet is not None:
        p_err,selected=check_packet(manifest,packet,block)
        if p_err:
            return p_err
        selected_image={x for x,v in selected.items() if v["kind"] in IMAGE_KINDS}
        # The original supporting frame MUST be in the actual delivered set
        # whenever a claim rests on OCR text. A selected frame alone is not
        # proof that the provider saw its actual bytes.
        delivered=(proposal_payload.get("delivered_image_refs") or
                   proposal_payload.get("delivered_visual_refs") or [])
        if type(delivered) is list:
            if any(x not in selected_image for x in delivered):
                errors.append("delivered_visual_ref_not_selected")
        for _,ref in claimed:
            o=original.get(ref)
            if o is not None and o.kind=="ocr_span":
                if (o.frame_ref_sha256 is not None and
                    o.frame_ref_sha256 not in selected_image):
                    errors.append("ocr_original_frame_not_in_context")
        if any(e["kind"] in TEXT_KINDS for e in selected.values()):
            exact_texts=[e["untrusted_text"] for e in selected.values()
                         if e["kind"] in TEXT_KINDS]
            # Only speech has unambiguous ASR provenance across B blocks.
            # Semantic summaries, optional descriptions and slide-only
            # captions remain subject to domain + independent attestor.
            def check_speech(row, depth=0):
                if depth>8:
                    return
                if type(row) is dict:
                    if "spoken_text" in row:
                        quote=row["spoken_text"]
                        if (quote is not None and quote!="" and
                            (type(quote) is not str or
                             not any(quote in x for x in exact_texts))):
                            errors.append("speech_quote_not_exact_asr")
                    for x in row.values():
                        check_speech(x,depth+1)
                elif type(row) is list:
                    for x in row:
                        check_speech(x,depth+1)
            check_speech(proposal_payload)
    # Original duration and slide index remain bounded, with no interpretation
    # of semantic interval meaning (block validators and attestor own that).
    assets={a.asset_id:a for a in manifest.assets}
    is_video=manifest.content_kind in VIDEO_KINDS
    def check_placements(obj,depth=0):
        if depth>9:
            return
        if type(obj) is dict:
            s=obj.get("start_ms")
            e=obj.get("end_ms")
            slide=obj.get("slide_index")
            if s is not None and e is not None:
                if (type(s) is not int or type(e) is not int or
                    s<0 or e<s or
                    (is_video and (len(assets)!=1 or
                                   type(next(iter(assets.values())).duration_ms) is not int or
                                   e>next(iter(assets.values())).duration_ms))):
                    errors.append("model_time_outside_original")
            if slide is not None and slide!=0 and type(slide) is int:
                if slide<1 or not any(a.slide_index==slide for a in assets.values()):
                    errors.append("model_slide_not_original")
            for x in obj.values():
                check_placements(x,depth+1)
        elif type(obj) is list:
            for x in obj:
                check_placements(x,depth+1)
    check_placements(proposal_payload)
    return tuple(sorted(set(errors)))
