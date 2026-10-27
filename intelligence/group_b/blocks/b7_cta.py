"""GB-MOD-19: source-verified B7 CTA occurrence classifier, no implicit scan.

0..N SQL005 CTA rows; zero is not a measured negative until an
INDEPENDENT trusted original-media scan attests exhaustiveness. Source text
is untrusted JSON data, never prompt instructions or permission to charge.
No network, model invocation, SQL, media decoding or side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
import json
import re

from ..contracts import canonical_hash
from ..context.builder import ContextPacket
from ..context.manifest import MediaEvidenceManifest
from ..providers.protocols import VerifiedChatResponse
from ..errors import GroupBError

VERSION = "group-b-b7-cta/v1"
TAXONOMY = "1.0"
CTA_TYPES = ("follow","like","comment","share","save","profile_visit",
             "link_click","app_install","purchase","sign_up","dm","other")
DESTINATIONS = ("current_post","profile","link_in_bio","website","app_store",
                "comments","dm","other","unknown")
SERVICES = ("tiktok","instagram","youtube","telegram","whatsapp",
            "apple_app_store","google_play","other")
DELIVERY = ("speech","on_screen_text")
VIDEO = ("video","slideshow_video")
CAROUSEL = ("photo_carousel","mixed_carousel")
TEXT_KINDS = ("transcript_segment","ocr_span")
IMAGE_KINDS = ("sampled_frame","slide_ref")
RELEVANT = TEXT_KINDS + IMAGE_KINDS
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")
_HANDLE = re.compile(r"@[A-Za-z0-9][A-Za-z0-9._-]{1,63}\Z")
_KEYS = frozenset(("occurrences","coverage_claim","confidence"))
_OCC_KEYS = frozenset((
    "occurrence_index","cta_type","text","spoken_text","screen_text",
    "delivery","evidence_refs","start_ms","end_ms","slide_index",
    "destination_type","destination_service","destination_url",
    "destination_handle","destination_app_name","destination_keyword",
    "destination_other_desc","confidence",
))
_SQL_ROWS = ("occurrence_index","cta_type","text","spoken_text","screen_text",
             "delivery","start_sec","end_sec","slide_index","destination_type",
             "destination_service","destination_url","destination_handle",
             "destination_app_name","destination_keyword",
             "destination_other_desc","extraction_status")


class B7CTAError(GroupBError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("b7_cta_unavailable", "b7: " + reason)


def _deny(reason):
    raise B7CTAError(reason)


def _sha(v):
    return type(v) is str and _SHA.fullmatch(v) is not None


def _text(v, *, max_length=4096, empty=True):
    if (type(v) is not str or len(v)>max_length or "\x00" in v or
        not empty and not v.strip()):
        _deny("cta_text_or_destination_invalid")
    return v


def _refs(values, allowed, *, nonempty=True):
    if (type(values) is not list or len(values)>120 or
        (nonempty and not values) or
        any(not _sha(ref) or ref not in allowed for ref in values) or
        len(values)!=len(set(values))):
        _deny("cta_refs_missing_or_forged")
    return values


def _labels(values):
    if (type(values) is not list or not 1<=len(values)<=2 or
        any(type(x) is not str or x not in DELIVERY for x in values)):
        _deny("cta_delivery_invalid")
    if len(set(values))!=len(values) or values!=sorted(values,key=DELIVERY.index):
        _deny("cta_delivery_invalid")
    return values


def _video_duration(manifest):
    if len(manifest.assets)!=1 or manifest.assets[0].media_kind!="video":
        _deny("video_source_unverified")
    duration=manifest.assets[0].duration_ms
    if type(duration) is not int or duration<=0:
        _deny("video_duration_unverified")
    return duration


def _source_context(manifest,packet):
    if (not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        manifest.content_kind not in VIDEO+CAROUSEL or
        not isinstance(packet,ContextPacket) or not packet.verify_digest()):
        _deny("source_manifest_or_context_unverified")
    video=manifest.content_kind in VIDEO
    duration=_video_duration(manifest) if video else None
    p=packet.payload
    expected={
        "schema_version":"group-b-context/v1","block":"B7",
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "input_content_kind":manifest.content_kind,
        "delivery_state":"not_sent",
        "media_delivery_state":"opaque_refs_not_attached",
    }
    if any(p.get(k)!=v for k,v in expected.items()):
        _deny("cta_context_wrong_source_or_delivery")
    source={o.ref_sha256:o for o in manifest.observations}
    if len(source)!=len(manifest.observations):
        _deny("duplicate_upstream_proof")
    relevant={r:o for r,o in source.items() if o.kind in RELEVANT}
    assets={a.asset_id:a for a in manifest.assets}
    if len(assets)!=len(manifest.assets):
        _deny("duplicate_source_asset")
    frames={r:o for r,o in relevant.items() if o.kind=="sampled_frame"}
    for ref,o in relevant.items():
        a=assets.get(o.media_asset_id)
        if a is None or o.asset_sha256!=a.source_sha256:
            _deny("cta_media_asset_cross_source")
        if video:
            if (o.timebase!="video_absolute" or type(o.start_ms) is not int or
                type(o.end_ms) is not int or not 0<=o.start_ms<=o.end_ms<=duration or
                a.media_kind!="video"):
                _deny("cta_video_source_timing_invalid")
        elif o.kind=="slide_ref":
            if (a.media_kind!="image" or o.timebase!="none" or
                o.start_ms is not None or o.end_ms is not None or
                type(o.slide_index) is not int or o.slide_index!=a.slide_index):
                _deny("carousel_slide_not_original")
        elif o.kind=="transcript_segment":
            _deny("carousel_has_video_speech_without_source")
        if o.kind=="sampled_frame":
            if (a.media_kind!="video" or o.timebase!="video_absolute" or
                type(o.start_ms) is not int or o.start_ms!=o.end_ms or
                type(a.duration_ms) is not int or a.duration_ms<=0 or
                o.start_ms>a.duration_ms):
                _deny("cta_frame_not_original_video")
        if o.kind=="ocr_span":
            frame=frames.get(o.frame_ref_sha256)
            if (frame is None or frame.media_asset_id!=o.media_asset_id or
                type(frame.start_ms) is not int or
                type(o.start_ms) is not int or type(o.end_ms) is not int or
                o.end_ms<=o.start_ms or
                not o.start_ms<=frame.start_ms<=o.end_ms):
                _deny("ocr_frame_not_source_anchored")
    entries=p.get("entries")
    omissions=p.get("omitted")
    cov=p.get("coverage")
    if (type(entries) is not list or len(entries)>2000 or
        type(omissions) is not list or len(omissions)>2000 or
        type(cov) is not dict):
        _deny("cta_context_coverage_malformed")
    selected={}
    for e in entries:
        if type(e) is not dict or not _sha(e.get("ref_sha256")):
            _deny("cta_context_entry_invalid")
        ref=e["ref_sha256"]
        o=relevant.get(ref)
        if o is None or ref in selected:
            _deny("cta_context_selected_ref_forged")
        wanted={
            "kind":o.kind,"content_sha256":o.content_sha256,
            "media_asset_id":o.media_asset_id,"asset_sha256":o.asset_sha256,
            "start_ms":o.start_ms,"end_ms":o.end_ms,
            "slide_index":o.slide_index,"frame_ref_sha256":o.frame_ref_sha256,
            "content_in_packet":o.kind in TEXT_KINDS,
            "carrier":("untrusted_utf8_data" if o.kind in TEXT_KINDS
                       else "unresolved_image_ref"),
        }
        if any(e.get(k)!=v for k,v in wanted.items()):
            _deny("cta_evidence_details_changed")
        if o.kind in TEXT_KINDS:
            raw=e.get("untrusted_text")
            if (type(raw) is not str or
                sha256(raw.encode("utf-8")).hexdigest()!=o.content_sha256 or
                set(e)!=set(wanted)|{"ref_sha256","untrusted_text"}):
                _deny("cta_source_text_sha_invalid")
        elif set(e)!=set(wanted)|{"ref_sha256"}:
            _deny("cta_visual_not_original")
        selected[ref]=e
    omitted=set()
    for e in omissions:
        if (type(e) is not dict or set(e)!={"kind","ref_sha256","reason"} or
            not _sha(e["ref_sha256"]) or e["ref_sha256"] not in relevant or
            e["kind"]!=relevant[e["ref_sha256"]].kind or
            type(e["reason"]) is not str or not e["reason"] or
            e["ref_sha256"] in selected or e["ref_sha256"] in omitted):
            _deny("cta_context_omitted_ref_forged")
        omitted.add(e["ref_sha256"])
    if set(selected)|omitted!=set(relevant):
        _deny("cta_context_hidden_evidence")
    expected_counts={
        "source_observations":len(source),
        "relevant_observations":len(relevant),
        "selected_observations":len(selected),
        "omitted_relevant":len(omitted),
        "selected_image_refs":sum(source[r].kind in IMAGE_KINDS for r in selected),
        "available_frame_refs":sum(o.kind=="sampled_frame" for o in relevant.values()),
        "available_slide_refs":sum(o.kind=="slide_ref" for o in relevant.values()),
    }
    if any(type(cov.get(k)) is not int or cov[k]!=v
           for k,v in expected_counts.items()):
        _deny("cta_context_coverage_counter_forged")
    if (type(cov.get("coverage_complete")) is not bool or
        cov["coverage_complete"] != (not omitted)):
        _deny("cta_context_completeness_forged")
    return video,duration,source,selected,not omitted


def _valid_url(value):
    if len(value)>2048 or any(x.isspace() for x in value):
        _deny("cta_url_invalid")
    try:
        parsed=urlsplit(value)
        host=parsed.hostname
        if (parsed.scheme!="https" or not host or parsed.username or
            parsed.password or parsed.fragment or parsed.port is not None or
            not re.fullmatch(r"[A-Za-z0-9.-]+",host) or "." not in host or
            host.startswith(".") or host.endswith(".") or
            any(not label or label.startswith("-") or label.endswith("-")
                for label in host.split("."))):
            _deny("cta_url_invalid")
        try:
            ip_address(host)
            _deny("cta_ip_literal_not_allowed")
        except ValueError:
            pass
    except ValueError:
        _deny("cta_url_invalid")


def _provenance_dest(row, observed):
    source="\n".join(observed)
    fields=("destination_url","destination_handle","destination_app_name",
            "destination_keyword","destination_other_desc")
    for field in fields:
        value=_text(row[field],max_length=2048)
        if value and not any(value in text for text in observed):
            _deny("cta_destination_not_verbatim_observed")
    if row["destination_url"]:
        _valid_url(row["destination_url"])
    if row["destination_handle"] and not _HANDLE.fullmatch(row["destination_handle"]):
        _deny("cta_handle_invalid")
    if row["destination_other_desc"] and row["destination_type"]!="other":
        _deny("cta_other_description_wrong_type")
    if row["destination_service"]:
        if (row["destination_service"] not in SERVICES or
            row["destination_service"].replace("_"," ") not in source.lower()
            and row["destination_service"] not in source.lower()):
            _deny("cta_service_not_explicitly_supported")
    if (row["destination_url"] and
        row["destination_type"] not in ("website","app_store","other")):
        _deny("cta_url_wrong_destination_type")
    if row["destination_handle"] and row["destination_type"] not in (
        "profile","dm","other"):
        _deny("cta_handle_wrong_destination_type")


@dataclass(frozen=True,slots=True)
class B7CTAProposal:
    payload:dict
    proposal_sha256:str
    authenticated_response:bool
    full_scan_attested:bool

    def verify_digest(self):
        try:
            return canonical_hash(self.payload)==self.proposal_sha256
        except (TypeError,ValueError):
            return False

    @property
    def can_persist(self):
        p=self.payload
        return (self.authenticated_response is True and self.verify_digest() and
                p.get("classification_status") in ("complete","partial") and
                p.get("analysis_status") in ("complete","partial") and
                p.get("source_evidence_manifest_sha256") is not None and
                (p.get("analysis_status")!="complete" or
                 self.full_scan_attested is True))

    def analysis_db_fields(self):
        if not self.can_persist:
            _deny("cta_analysis_not_ready_for_sql")
        return {"analysis_status":self.payload["analysis_status"],
                "taxonomy_version":TAXONOMY,
                "model_version":self.payload["model_version"]}

    def occurrence_db_rows(self):
        if not self.can_persist:
            _deny("cta_analysis_not_ready_for_sql")
        return tuple({key:item[key] for key in _SQL_ROWS}
                     for item in self.payload["occurrences"])


def unknown_b7(*,manifest:MediaEvidenceManifest,reason:str)->B7CTAProposal:
    if (not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        manifest.content_kind not in VIDEO+CAROUSEL or
        type(reason) is not str or not _REASON.fullmatch(reason)):
        _deny("cta_unknown_source_or_reason_invalid")
    p={
        "schema_version":VERSION,"project_id":manifest.project_id,
        "b_run_id":manifest.b_run_id,"video_id":manifest.video_id,
        "qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "model_version":None,"provider_response_id":None,
        "model_request_sha256":None,"occurrences":[],
        "analysis_status":"not_run","classification_status":"unknown",
        "full_scan_attested":False,"review_reasons":[reason],
    }
    return B7CTAProposal(p,canonical_hash(p),False,False)


def validate_b7_cta(*,manifest:MediaEvidenceManifest,packet:ContextPacket,
                    response:VerifiedChatResponse,receipt_verifier:object,
                    delivered_image_refs:tuple[str,...],
                    full_scan_verifier:object=None,
                    slide_quote_verifier:object=None)->B7CTAProposal:
    """Validate model semantic candidates against independently trusted media.

    Host receipt MUST check exact paid attempt, image attachments and input.
    full_scan_verifier must inspect complete original media independently;
    sampled OCR, a self-claimed 'complete' and a model zero never suffice.
    """
    video,duration,source,selected,selection_complete=_source_context(manifest,packet)
    visuals=tuple(r for r in selected if source[r].kind in IMAGE_KINDS)
    if (type(delivered_image_refs) is not tuple or
        len(delivered_image_refs)!=len(visuals) or
        len(set(delivered_image_refs))!=len(visuals) or
        set(delivered_image_refs)!=set(visuals)):
        _deny("cta_image_delivery_unproved")
    if (not isinstance(response,VerifiedChatResponse) or
        not _sha(response.request_sha256) or
        type(response.response_id) is not str or not response.response_id or
        type(response.model) is not str or not response.model):
        _deny("cta_model_response_invalid")
    verify=getattr(receipt_verifier,"verify_b7_response",None)
    if not callable(verify):
        _deny("cta_provider_receipt_missing")
    try:
        authenticated=verify(response,packet,delivered_image_refs)
    except Exception:
        _deny("cta_provider_receipt_failed")
    if authenticated is not True:
        _deny("cta_provider_receipt_failed")
    raw=response.json_data
    if type(raw) is not dict or set(raw)!=_KEYS:
        _deny("cta_json_shape_invalid")
    if raw["coverage_claim"] not in ("complete","partial") or raw["confidence"] not in (
        "high","medium","low"):
        _deny("cta_coverage_or_confidence_invalid")
    rows=raw["occurrences"]
    if type(rows) is not list or len(rows)>100:
        _deny("cta_occurrences_invalid")
    compiled=[]
    review=[]
    prev=None
    seen_occurrences=set()
    for index,item in enumerate(rows,1):
        if type(item) is not dict or set(item)!=_OCC_KEYS:
            _deny("cta_occurrence_shape_invalid")
        if type(item["occurrence_index"]) is not int or item["occurrence_index"]!=index:
            _deny("cta_occurrence_index_not_contiguous")
        if item["cta_type"] not in CTA_TYPES or type(item["cta_type"]) is not str:
            _deny("cta_type_not_in_sql005")
        if item["destination_type"] not in DESTINATIONS or type(item["destination_type"]) is not str:
            _deny("cta_destination_not_in_sql005")
        if (type(item["confidence"]) is not str or
            item["confidence"] not in ("high","medium","low")):
            _deny("cta_confidence_invalid")
        delivery=_labels(item["delivery"])
        refs=_refs(item["evidence_refs"],selected)
        quote=_text(item["text"],empty=False)
        speech=_text(item["spoken_text"])
        screen=_text(item["screen_text"])
        if (("speech" in delivery)!=(bool(speech)) or
            ("on_screen_text" in delivery)!=(bool(screen)) or
            not any(quote in x for x in (speech,screen))):
            _deny("cta_delivery_without_verbatim_quote")
        if video:
            start,end,slide=(item["start_ms"],item["end_ms"],item["slide_index"])
            if (type(start) is not int or type(end) is not int or
                not 0<=start<end<=duration or slide!=0):
                _deny("cta_video_position_invalid")
            position=start
        else:
            start,end,slide=(item["start_ms"],item["end_ms"],item["slide_index"])
            if (start!=0 or end!=0 or type(slide) is not int or slide<1 or
                not any(a.slide_index==slide for a in manifest.assets)):
                _deny("cta_carousel_slide_invalid")
            position=slide
        if prev is not None and position<prev:
            _deny("cta_occurrences_not_in_original_order")
        prev=position
        # Repetition at a different source position is genuine; duplicating
        # the same occurrence at the same position would inflate CTA counts.
        occurrence_key=(start,end,slide,quote,tuple(delivery),tuple(refs))
        if occurrence_key in seen_occurrences:
            _deny("cta_occurrence_duplicated_without_new_source_position")
        seen_occurrences.add(occurrence_key)
        text_evidence={"speech":[],"on_screen_text":[]}
        for ref in refs:
            o=source[ref]
            if video:
                if (type(o.start_ms) is not int or type(o.end_ms) is not int or
                    o.start_ms>end or o.end_ms<start):
                    _deny("cta_ref_wrong_time")
            elif o.slide_index!=slide:
                _deny("cta_ref_wrong_slide")
            if o.kind=="transcript_segment":
                text_evidence["speech"].append(selected[ref]["untrusted_text"])
            elif o.kind=="ocr_span":
                if (o.frame_ref_sha256 not in delivered_image_refs or
                    o.frame_ref_sha256 not in selected):
                    _deny("cta_ocr_frame_not_delivered")
                text_evidence["on_screen_text"].append(selected[ref]["untrusted_text"])
            elif o.kind=="slide_ref" and not video:
                verify_quote=getattr(slide_quote_verifier,
                                     "verify_b7_slide_quote",None)
                if not callable(verify_quote):
                    _deny("cta_carousel_quote_attestation_missing")
                try:
                    verified=verify_quote(manifest,o,screen)
                except Exception:
                    _deny("cta_carousel_quote_attestation_failed")
                if verified is not True:
                    _deny("cta_carousel_quote_attestation_failed")
                text_evidence["on_screen_text"].append(screen)
        for channel,value in (("speech",speech),("on_screen_text",screen)):
            if value and not any(value in t for t in text_evidence[channel]):
                _deny("cta_claim_not_verbatim_source")
        _provenance_dest(item,text_evidence["speech"]+text_evidence["on_screen_text"])
        low=item["confidence"]=="low"
        if low:
            review.append("cta_occurrence_low_confidence")
        if item["destination_type"]=="unknown":
            review.append("cta_destination_unknown")
        for field in ("destination_service","destination_url","destination_handle",
                      "destination_app_name","destination_keyword",
                      "destination_other_desc"):
            _text(item[field],max_length=2048)
        compiled.append({
            "occurrence_index":index,"cta_type":item["cta_type"],
            "text":quote,"spoken_text":speech or None,"screen_text":screen or None,
            "delivery":delivery,
            "start_sec":(start/1000 if video else None),
            "end_sec":(end/1000 if video else None),
            "slide_index":(None if video else slide),
            "destination_type":item["destination_type"],
            "destination_service":item["destination_service"] or None,
            "destination_url":item["destination_url"] or None,
            "destination_handle":item["destination_handle"] or None,
            "destination_app_name":item["destination_app_name"] or None,
            "destination_keyword":item["destination_keyword"] or None,
            "destination_other_desc":item["destination_other_desc"] or None,
            "extraction_status":("needs_review" if low or
                                 item["destination_type"]=="unknown"
                                 else "detected"),
            "evidence_refs":list(refs),
            "confidence":item["confidence"],
        })
    if not selection_complete:
        review.append("cta_source_selection_incomplete")
    if raw["confidence"]=="low":
        review.append("cta_result_low_confidence")
    if raw["coverage_claim"]!="complete":
        review.append("cta_scan_claim_partial")
    attested=False
    if raw["coverage_claim"]=="complete" and selection_complete:
        full=getattr(full_scan_verifier,"verify_b7_full_scan",None)
        if callable(full):
            try:
                attested=full(manifest,packet,response) is True
            except Exception:
                _deny("cta_whole_media_attestor_failed")
        if not attested:
            review.append("cta_original_full_scan_unverified")
    status="complete" if attested and not review else "partial"
    payload={
        "schema_version":VERSION,"project_id":manifest.project_id,
        "b_run_id":manifest.b_run_id,"video_id":manifest.video_id,
        "qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "model_version":response.model,
        "provider_response_id":response.response_id,
        "model_request_sha256":response.request_sha256,
        "occurrences":compiled,"analysis_status":status,
        "classification_status":status,"full_scan_attested":attested,
        "review_reasons":sorted(set(review)),
    }
    return B7CTAProposal(payload,canonical_hash(payload),True,attested)


@dataclass(frozen=True,slots=True)
class B7PreparedRequest:
    body:dict
    request_sha256:str
    prompt_sha256:str

    def verify_digest(self):
        try:
            return canonical_hash(self.body)==self.request_sha256
        except (TypeError,ValueError):
            return False


def prepare_b7_request(*,catalog,model:str,packet:ContextPacket,
                       max_output_tokens:int,attachments:tuple=())->B7PreparedRequest:
    """Build strictly UNSENT multimodal prompt only, never a paid call."""
    if (not isinstance(packet,ContextPacket) or not packet.verify_digest() or
        packet.payload.get("block")!="B7" or
        packet.payload.get("delivery_state")!="not_sent" or
        packet.payload.get("media_delivery_state")!="opaque_refs_not_attached"):
        _deny("cta_packet_not_prepared")
    from ..providers.openrouter import prepare_chat_request
    from .b7_schema import B7_OUTPUT_SCHEMA
    try:
        prompt=(Path(__file__).resolve().parents[1]/
                "prompts"/"b7_cta_v1.txt").read_text(encoding="utf-8")
    except (OSError,UnicodeError):
        _deny("cta_trusted_prompt_missing")
    if not prompt or len(prompt.encode("utf-8"))>16000:
        _deny("cta_trusted_prompt_invalid")
    body=prepare_chat_request(
        catalog=catalog,model=model,packet=packet,
        output_schema=B7_OUTPUT_SCHEMA,
        max_output_tokens=max_output_tokens,attachments=attachments,
    )
    body["messages"][0]["content"]+="\n\n"+prompt
    if len(json.dumps(body,ensure_ascii=False).encode("utf-8"))>4_000_000:
        _deny("cta_request_too_large")
    return B7PreparedRequest(
        body,canonical_hash(body),sha256(prompt.encode("utf-8")).hexdigest()
    )
