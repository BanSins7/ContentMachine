"""GA-MOD-19: exact-version, host-authorized, no-Group-B-import handoff.

The output is the frozen group-a-to-b/v1 wire; no Group B B1-B8 logic,
CTA classification, paid model, filesystem/network operation or auto-publish.
"""
from __future__ import annotations

from uuid import UUID

from ..contracts_v1 import (
    canonical_sha256, media_manifest_v1, group_a_to_b_v1,
    REQUIRED_STREAMS, ensure_same_lineage,
)
from ..assembly import validate_video_snapshot
from ..validation import _certificate
from ..extractors.carousel import validate_photo_observation
from ..extractors.mixed_carousel import validate_mixed_observation

METHOD="group-a-handoff-v1"
PREPARED="ga19-prepared-result/v1"
VIDEO_KINDS=frozenset(("video","slideshow_video"))
CAROUSEL_KINDS=frozenset(("photo_carousel","mixed_carousel"))


class GroupBHandoffError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__("GA19 handoff denied: "+code)


def _positive(raw):
    return type(raw) is int and raw>0


def _uuid(raw):
    if type(raw) is not str:
        return False
    try:
        return str(UUID(raw))==raw
    except ValueError:
        return False


def _scope(m,item):
    return {
        "project_id":m["project_id"],"qdp_run_id":m["qdp_run_id"],
        "qdp_input_sha256":m["qdp_input_sha256"],
        "manifest_sha256":m["manifest_sha256"],
        "video_id":m["video_id"],"source_id":m["source_id"],
        "source_policy_version_id":m["source_policy_version_id"],
        "media_asset_id":item["media_asset_id"],
        "asset_sha256":item["sha256"],
    }


def _truth(status,positive_refs,negative_ref=None):
    """Evidence_count counts independent pinned provenance refs, not words.

    Even verified-zero truth emits an immutable negative observation ref;
    without one, the original frozen v1 parser rejects completion.
    """
    if status=="complete":
        if positive_refs:
            return {"status":"complete","evidence_count":len(positive_refs),
                    "verified_absence":False,"reason_code":None}
        if negative_ref is None:
            raise GroupBHandoffError("completed_stream_missing_negative_proof")
        return {"status":"complete","evidence_count":0,
                "verified_absence":True,"reason_code":None}
    if status not in ("partial","unavailable","not_run"):
        raise GroupBHandoffError("invalid_stream_state")
    return {"status":status,"evidence_count":None,
            "verified_absence":False,
            "reason_code":("stream_incomplete" if status=="partial" else
                           "stream_unavailable" if status=="unavailable"
                           else "stream_not_run")}


def _add(refs,kind,ref_sha,asset_sha):
    if type(ref_sha) is not str or len(ref_sha)!=64:
        raise GroupBHandoffError("evidence_ref_missing")
    proof={"kind":kind,"ref_sha256":ref_sha,"asset_sha256":asset_sha}
    if proof not in refs:
        refs.append(proof)
    if len(refs)>2000:
        raise GroupBHandoffError("evidence_budget_exceeded")


def _certify(snap, item,verify_coverage):
    # GA18's original stream certificates MUST be re-authorized at export time,
    # not merely trusted because somebody once set status='complete'.
    for name,cert in snap["coverage_certificates"].items():
        _certificate(cert,stream=name,asset_sha=item["sha256"],
                     evidence_sha=snap["coverage"][name]["observation_sha256"],
                     verify_coverage=verify_coverage)


def _video_truth(m,item,snap,streams,refs,verify_coverage):
    validate_video_snapshot(m,snap)
    if (snap["media_asset_id"]!=item["media_asset_id"]
        or snap["source_media_sha256"]!=item["sha256"]):
        raise GroupBHandoffError("video_asset_snapshot_mismatch")
    _certify(snap,item,verify_coverage)
    methods=snap["measurement_methods"]
    _add(refs,"scene",methods["technical_sha256"],item["sha256"])
    streams.setdefault("technical",[]).append(("complete",[
        methods["technical_sha256"]
    ],None))
    fields=(("speech","transcript","asr_sha256","speech_segments"),
            ("screen_text","screen_text","screen_text_sha256","screen_text_segments"),
            ("audio","audio","audio_events_sha256","audio_events"))
    for stream,kind,field,rows_field in fields:
        status=snap[stream+"_status"]
        digest=methods[field]
        observed=snap[rows_field]
        positive=[]
        negative=None
        if digest is not None:
            # Stage-local digest is provenance. When independently certified,
            # verified-absence must correspond to no positive observed rows.
            cert=snap["coverage_certificates"].get(stream)
            certified=cert is not None and status=="complete"
            if certified and cert["verified_absence"]:
                if observed:
                    raise GroupBHandoffError("negative_stream_has_positive_rows")
                negative=cert["certificate_sha256"]
                _add(refs,kind,negative,item["sha256"])
            else:
                _add(refs,kind,digest,item["sha256"])
                if observed or status!="complete":
                    positive.append(digest)
                elif certified:
                    # A genuine completed zero measurement demands explicit
                    # negative provenance; missing absence is not false.
                    raise GroupBHandoffError("completed_zero_without_negative_proof")
        streams.setdefault(stream,[]).append((status,positive,negative))
    subtitles=snap["has_subtitles"]
    subtitle_sha=methods["subtitle_sha256"]
    if subtitles=="unknown" or subtitle_sha is None:
        streams.setdefault("subtitles",[]).append(("unavailable",[],None))
    else:
        # Group B v1 names this evidence kind 'screen_text', although the
        # SHA is the original GA14 subtitle-track/dense-review observation,
        # not a fabricated GA13 OCR interval or a B6 style judgment.
        _add(refs,"screen_text",subtitle_sha,item["sha256"])
        streams.setdefault("subtitles",[]).append((
            "complete",[] if subtitles=="false" else [subtitle_sha],
            subtitle_sha if subtitles=="false" else None,
        ))


def _combine(m,components,refs):
    out={}
    for stream in REQUIRED_STREAMS[m["content_kind"]]:
        entries=components.get(stream,[])
        if not entries:
            out[stream]=_truth("unavailable",[])
            continue
        if all(x[0]=="complete" for x in entries):
            positives=[p for _,seq,_ in entries for p in seq]
            negatives=[p for _,_,p in entries if p is not None]
            # The aggregate can't assert verified absence if *any* asset is
            # missing source-confirmed zero or other measurements.
            status="complete"
            if positives:
                out[stream]=_truth(status,positives)
            else:
                if len(negatives)!=len(entries):
                    raise GroupBHandoffError("mixed_negative_coverage_incomplete")
                out[stream]=_truth(status,[],negative_ref=negatives[0])
        else:
            out[stream]=_truth(
                "partial" if any(x[0] in ("complete","partial") for x in entries)
                else "unavailable",[]
            )
    return out


def _receipt(m,receipt):
    if type(receipt) is not dict or set(receipt)!={
        "ga_run_id","video_analysis_ids","carousel_analysis_id"
    } or not _uuid(receipt["ga_run_id"]):
        raise GroupBHandoffError("invalid_ga_run_receipt")
    ids=receipt["video_analysis_ids"]
    if type(ids) is not dict or any(not _positive(k) or not _positive(v)
                                    for k,v in ids.items()):
        raise GroupBHandoffError("invalid_a003_receipt")
    if receipt["carousel_analysis_id"] is not None and not _positive(receipt["carousel_analysis_id"]):
        raise GroupBHandoffError("invalid_a004_receipt")
    videos={i["media_asset_id"] for i in m["items"] if i["kind"]=="video"}
    if set(ids)!=videos:
        raise GroupBHandoffError("unmatched_video_analysis_receipts")
    if m["content_kind"] in VIDEO_KINDS and receipt["carousel_analysis_id"] is not None:
        raise GroupBHandoffError("unexpected_carousel_analysis")
    if m["content_kind"] in CAROUSEL_KINDS and receipt["carousel_analysis_id"] is None:
        raise GroupBHandoffError("carousel_analysis_id_required")
    return receipt


def prepare_group_b_result(manifest,*,receipt,video_snapshots=(),
                           carousel_observation=None,verify_current,
                           verify_coverage=None,verify_origin=None,
                           extra_evidence=(),verify_evidence=None):
    """Produce a frozen A→B snapshot for EXACT canonical A003/A004 IDs.

    The result can be partial. B MUST reject partial and independently
    pin exact GA result ID/SHA and current QDP/manifest/source-policy rights.
    For mixed carousels with partial video OCR, status is intentionally
    partial even if GA17 A004 slide_state is complete.
    """
    try:
        m=media_manifest_v1(manifest)
    except (TypeError,ValueError) as exc:
        raise GroupBHandoffError("invalid_media_manifest") from exc
    if m["content_kind"]=="image_post":
        raise GroupBHandoffError("b_image_post_unsupported")
    if not callable(verify_current) or not callable(verify_origin):
        raise GroupBHandoffError("trusted_handoff_ports_required")
    r=_receipt(m,receipt)
    if type(video_snapshots) not in (list,tuple):
        raise GroupBHandoffError("video_snapshot_set_invalid")
    video_map={}
    for snap in video_snapshots:
        if type(snap) is not dict:
            raise GroupBHandoffError("invalid_video_snapshot")
        ident=snap.get("media_asset_id")
        if ident in video_map:
            raise GroupBHandoffError("duplicate_video_snapshot")
        video_map[ident]=snap
    if set(video_map)!=set(r["video_analysis_ids"]):
        raise GroupBHandoffError("video_snapshot_coverage_mismatch")
    refs,streams={},{}
    evidence=[]
    if m["content_kind"] in VIDEO_KINDS and carousel_observation is not None:
        raise GroupBHandoffError("carousel_on_video_publication")
    if m["content_kind"] in CAROUSEL_KINDS:
        if carousel_observation is None:
            raise GroupBHandoffError("carousel_observation_required")
        if m["content_kind"]=="photo_carousel":
            obs=validate_photo_observation(m,carousel_observation)
        else:
            obs=validate_mixed_observation(m,carousel_observation)
        if verify_origin({"kind":"carousel","manifest_sha256":m["manifest_sha256"],
                          "observation_sha256":obs["observation_sha256"],
                          "content_kind":m["content_kind"]}) is not True:
            raise GroupBHandoffError("carousel_observation_origin_untrusted")
        for man,slide in zip(m["items"],obs["items"]):
            proof=canonical_sha256({
                "schema_version":"ga19-immutable-slide-ref/v1",
                "observation_sha256":obs["observation_sha256"],
                "slide_index":man["index"],
                "media_asset_id":man["media_asset_id"],
                "asset_sha256":man["sha256"],
                "item_type":man["kind"],
            })
            _add(evidence,"slide",proof,man["sha256"])
            streams.setdefault("slides",[]).append(("complete",[proof],None))
        # Positive or verified-empty still-image OCR is pinned to the original
        # GA16 observation and still-slide asset. For mixed A004, the entire
        # video text coverage is intentionally unknown/incomplete.
        if m["content_kind"]=="photo_carousel":
            ocr_ref=obs["observation_sha256"]
            anchor=m["items"][0]["sha256"]
            _add(evidence,"screen_text",ocr_ref,anchor)
            if obs["text_status"]=="complete":
                if obs["total_screen_word_count"]>0:
                    streams["screen_text"]=[("complete",[ocr_ref],None)]
                else:
                    streams["screen_text"]=[("complete",[],ocr_ref)]
            else:
                streams["screen_text"]=[("partial",[],None)]
        else:
            streams["screen_text"]=[("partial",[],None)]
            for orig,slide in zip(m["items"],obs["items"]):
                if orig["kind"]=="video" and slide["video_a_analysis_id"]!=r["video_analysis_ids"][orig["media_asset_id"]]:
                    raise GroupBHandoffError("mixed_video_link_mismatch")
    else:
        obs=None
    for item in m["items"]:
        if verify_current(_scope(m,item)) is not True:
            raise GroupBHandoffError("current_media_rights_denied")
        if item["kind"]=="video":
            snap=video_map[item["media_asset_id"]]
            if verify_origin({"kind":"video","snapshot":snap,
                              "media_asset_id":item["media_asset_id"],
                              "asset_sha256":item["sha256"]}) is not True:
                raise GroupBHandoffError("video_snapshot_origin_untrusted")
            _video_truth(m,item,snap,streams,evidence,verify_coverage)
        if verify_current(_scope(m,item)) is not True:
            raise GroupBHandoffError("current_media_rights_revoked")
    if m["content_kind"]=="mixed_carousel":
        # GA17's static slide OCR cannot establish continuous video text
        # coverage: the legacy Group B v1 carousel check also requires A004
        # text_status=complete. Never forge it from complete A003 streams.
        streams["screen_text"]=[("partial",[],None)]
    if type(extra_evidence) not in (tuple,list) or len(extra_evidence)>2000:
        raise GroupBHandoffError("invalid_extra_evidence")
    if extra_evidence and not callable(verify_evidence):
        raise GroupBHandoffError("extra_evidence_verifier_required")
    sha_set={i["sha256"] for i in m["items"]}
    for row in extra_evidence:
        if (type(row) is not dict or set(row)!={"kind","ref_sha256","asset_sha256"}
            or row["kind"] not in ("frame","transcript","screen_text","audio")
            or row["asset_sha256"] not in sha_set
            or verify_evidence(dict(row)) is not True):
            raise GroupBHandoffError("unverified_extra_evidence")
        _add(evidence,row["kind"],row["ref_sha256"],row["asset_sha256"])
    states=_combine(m,streams,evidence)
    is_complete=all(x["status"]=="complete" for x in states.values())
    canonical_id=(
        r["video_analysis_ids"][m["items"][0]["media_asset_id"]]
        if m["content_kind"] in VIDEO_KINDS else r["carousel_analysis_id"]
    )
    result={
        "schema_version":"group-a-to-b/v1",
        "project_id":m["project_id"],"qdp_run_id":m["qdp_run_id"],
        "video_id":m["video_id"],"qdp_input_sha256":m["qdp_input_sha256"],
        "manifest_sha256":m["manifest_sha256"],
        "content_kind":m["content_kind"],
        "analysis_state":"completed" if is_complete else "partial",
        "video_a_analysis_id":canonical_id if m["content_kind"] in VIDEO_KINDS else None,
        "carousel_a_analysis_id":canonical_id if m["content_kind"] in CAROUSEL_KINDS else None,
        "method_version":METHOD,"streams":states,"evidence":evidence,
    }
    try:
        group_a_to_b_v1(result)
    except (TypeError,ValueError) as exc:
        raise GroupBHandoffError("frozen_a_to_b_contract_failed") from exc
    for item in m["items"]:
        if verify_current(_scope(m,item)) is not True:
            raise GroupBHandoffError("media_rights_revoked_before_handoff")
    pre={
        "schema_version":PREPARED,
        "ga_run_id":r["ga_run_id"],
        "source_id":m["source_id"],
        "source_policy_version_id":m["source_policy_version_id"],
        "video_analysis_ids":{str(k):v for k,v in r["video_analysis_ids"].items()},
        "carousel_analysis_id":r["carousel_analysis_id"],
        "video_snapshot_sha256":{
            str(k):v["observation_sha256"] for k,v in video_map.items()
        },
        "carousel_observation_sha256":None if obs is None else obs["observation_sha256"],
        "snapshot":result,
        "result_sha256":canonical_sha256(result),
        "ready_for_b":is_complete,
        "delivery_state":"eligible" if is_complete else "blocked_partial",
    }
    return {**pre,"preparation_sha256":canonical_sha256(pre)}


def validate_prepared_handoff(manifest,prepared):
    try:
        m=media_manifest_v1(manifest)
    except (TypeError,ValueError) as exc:
        raise GroupBHandoffError("invalid_media_manifest") from exc
    if type(prepared) is not dict or set(prepared)!={
        "schema_version","ga_run_id","source_id","source_policy_version_id",
        "video_analysis_ids","carousel_analysis_id","video_snapshot_sha256",
        "carousel_observation_sha256","snapshot","result_sha256","ready_for_b",
        "delivery_state","preparation_sha256"
    } or prepared["schema_version"]!=PREPARED:
        raise GroupBHandoffError("invalid_prepared_handoff")
    if not _uuid(prepared["ga_run_id"]):
        raise GroupBHandoffError("invalid_ga_run_receipt")
    if canonical_sha256({k:v for k,v in prepared.items() if k!="preparation_sha256"})!=prepared["preparation_sha256"]:
        raise GroupBHandoffError("prepared_handoff_digest_mismatch")
    if (prepared["source_id"]!=m["source_id"]
        or prepared["source_policy_version_id"]!=m["source_policy_version_id"]):
        raise GroupBHandoffError("prepared_policy_mismatch")
    if (type(prepared["video_analysis_ids"]) is not dict or
        any(type(k) is not str or not k.isdecimal() or
            not k.isascii() or (k[0]=="0") or
            not _positive(v) for k,v in prepared["video_analysis_ids"].items())):
        raise GroupBHandoffError("invalid_prepared_a003_map")
    _receipt(m,{
        "ga_run_id":prepared["ga_run_id"],
        "video_analysis_ids":{int(k):v for k,v in prepared["video_analysis_ids"].items()},
        "carousel_analysis_id":prepared["carousel_analysis_id"],
    })
    try:
        group_a_to_b_v1(prepared["snapshot"])
        for key in ("project_id","qdp_run_id","video_id","qdp_input_sha256","manifest_sha256","content_kind"):
            if prepared["snapshot"][key]!=m[key]:
                raise GroupBHandoffError("handoff_lineage_mismatch")
        if any(e["asset_sha256"] not in {item["sha256"] for item in m["items"]}
               for e in prepared["snapshot"]["evidence"]):
            raise GroupBHandoffError("handoff_foreign_evidence_asset")
    except (TypeError,ValueError) as exc:
        raise GroupBHandoffError("invalid_a_to_b_snapshot") from exc
    if (prepared["result_sha256"]!=canonical_sha256(prepared["snapshot"])
        or prepared["ready_for_b"]!=(prepared["snapshot"]["analysis_state"]=="completed")
        or prepared["delivery_state"]!=("eligible" if prepared["ready_for_b"] else "blocked_partial")):
        raise GroupBHandoffError("handoff_status_or_digest_mismatch")
    if (prepared["snapshot"]["video_a_analysis_id"] is not None and
        prepared["snapshot"]["video_a_analysis_id"]!=next(iter(prepared["video_analysis_ids"].values()))):
        raise GroupBHandoffError("handoff_video_pointer_mismatch")
    if (prepared["snapshot"]["carousel_a_analysis_id"] is not None and
        prepared["snapshot"]["carousel_a_analysis_id"]!=prepared["carousel_analysis_id"]):
        raise GroupBHandoffError("handoff_carousel_pointer_mismatch")
    return dict(prepared)
