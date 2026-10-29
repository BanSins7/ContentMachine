"""GB-MOD-20 B8 observed publication topic classification (not Search Planner).

The model proposes *labels*, never SQL IDs/slugs; only an immutable,
freshness-checked SQL005 registry may map CATEGORY > TOPIC > SUBTOPIC IDs.
New/ambiguous labels are review proposals, NEVER auto-created. Media/model
rights, budget and authenticated provider delivery remain external gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import re

from ..contracts import canonical_hash
from ..context.builder import ContextPacket
from ..context.manifest import MediaEvidenceManifest
from ..errors import GroupBError
from ..providers.protocols import VerifiedChatResponse
from ..topic_registry import TaxonomySnapshot, TopicRegistryError, normalize_label

VERSION = "group-b-b8-topic/v1"
TAXONOMY = "1.0"
VIDEO = ("video", "slideshow_video")
CAROUSEL = ("photo_carousel", "mixed_carousel")
RELEVANT = ("transcript_segment", "ocr_span", "sampled_frame", "slide_ref")
TEXT = ("transcript_segment", "ocr_span")
VISUAL = ("sampled_frame", "slide_ref")
_KEYS = frozenset(("category_label", "topic_label", "subtopic_label",
                   "evidence_refs", "confidence"))
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")


class B8TopicError(GroupBError):
    def __init__(self, code: str):
        self.reason = code
        super().__init__("b8_topic_unavailable", "b8: " + code)


def _deny(reason: str):
    raise B8TopicError(reason)


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _verify_source(manifest, packet):
    if (not isinstance(manifest, MediaEvidenceManifest) or
        not manifest.verify_digest() or
        manifest.content_kind not in VIDEO + CAROUSEL or
        not isinstance(packet, ContextPacket) or not packet.verify_digest()):
        _deny("source_or_context_unverified")
    p = packet.payload
    identity = {
        "schema_version": "group-b-context/v1", "block": "B8",
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
    if any(p.get(k) != v for k,v in identity.items()):
        _deny("b8_input_lineage_invalid")
    original = {x.ref_sha256:x for x in manifest.observations}
    assets = {x.asset_id:x for x in manifest.assets}
    if (len(original) != len(manifest.observations) or
        len(assets) != len(manifest.assets)):
        _deny("duplicate_original_evidence_or_media")
    video = manifest.content_kind in VIDEO
    if video and (len(assets)!=1 or
                  next(iter(assets.values())).media_kind!="video" or
                  type(next(iter(assets.values())).duration_ms) is not int or
                  next(iter(assets.values())).duration_ms<=0):
        _deny("video_source_not_verified")
    relevant = {k:x for k,x in original.items() if x.kind in RELEVANT}
    frames = {k:x for k,x in relevant.items() if x.kind=="sampled_frame"}
    for x in relevant.values():
        asset = assets.get(x.media_asset_id)
        if asset is None or asset.source_sha256!=x.asset_sha256:
            _deny("b8_source_asset_mismatch")
        if x.kind=="slide_ref":
            if (video or asset.media_kind!="image" or x.timebase!="none" or
                x.start_ms is not None or x.end_ms is not None or
                type(x.slide_index) is not int or
                x.slide_index!=asset.slide_index):
                _deny("b8_slide_not_original")
        else:
            if (asset.media_kind!="video" or x.timebase!="video_absolute" or
                type(x.start_ms) is not int or type(x.end_ms) is not int or
                type(asset.duration_ms) is not int or
                not 0<=x.start_ms<=x.end_ms<=asset.duration_ms or
                (x.kind=="sampled_frame" and x.start_ms!=x.end_ms) or
                (x.kind in TEXT and x.start_ms>=x.end_ms) or
                (not video and (manifest.content_kind!="mixed_carousel" or
                                type(x.slide_index) is not int or
                                x.slide_index!=asset.slide_index))):
                _deny("b8_original_video_observation_invalid")
        if x.kind=="ocr_span":
            frame=frames.get(x.frame_ref_sha256)
            if (frame is None or frame.media_asset_id!=x.media_asset_id or
                frame.start_ms is None or
                not x.start_ms<=frame.start_ms<=x.end_ms):
                _deny("b8_ocr_original_frame_missing")
    entries=p.get("entries")
    omitted=p.get("omitted")
    counts=p.get("coverage")
    if (type(entries) is not list or len(entries)>2000 or
        type(omitted) is not list or len(omitted)>2000 or
        type(counts) is not dict):
        _deny("b8_context_coverage_invalid")
    selected={}
    for e in entries:
        if type(e) is not dict or not _sha(e.get("ref_sha256")):
            _deny("b8_selected_entry_invalid")
        ref=e["ref_sha256"]
        obs=relevant.get(ref)
        if obs is None or ref in selected:
            _deny("b8_ref_cross_source_or_duplicate")
        wanted={
            "kind":obs.kind,"ref_sha256":obs.ref_sha256,
            "content_sha256":obs.content_sha256,
            "media_asset_id":obs.media_asset_id,
            "asset_sha256":obs.asset_sha256,
            "start_ms":obs.start_ms,"end_ms":obs.end_ms,
            "slide_index":obs.slide_index,
            "frame_ref_sha256":obs.frame_ref_sha256,
            "carrier":("untrusted_utf8_data" if obs.kind in TEXT
                       else "unresolved_image_ref"),
            "content_in_packet":obs.kind in TEXT,
        }
        if any(e.get(k)!=v for k,v in wanted.items()):
            _deny("b8_selected_source_provenance_mismatch")
        if obs.kind in TEXT:
            raw=e.get("untrusted_text")
            if (type(raw) is not str or len(raw)>100000 or
                sha256(raw.encode("utf-8")).hexdigest()!=obs.content_sha256 or
                set(e)!=set(wanted)|{"untrusted_text"}):
                _deny("b8_untrusted_text_sha_mismatch")
        elif set(e)!=set(wanted):
            _deny("b8_visual_carrier_invalid")
        selected[ref]=e
    missing=set()
    for e in omitted:
        if (type(e) is not dict or set(e)!={"kind","ref_sha256","reason"} or
            not _sha(e["ref_sha256"]) or e["ref_sha256"] not in relevant or
            e["kind"]!=relevant[e["ref_sha256"]].kind or
            type(e["reason"]) is not str or not e["reason"] or
            e["ref_sha256"] in selected or e["ref_sha256"] in missing):
            _deny("b8_omission_forged")
        missing.add(e["ref_sha256"])
    if set(selected)|missing!=set(relevant):
        _deny("b8_original_source_accounting_incomplete")
    expected={
        "source_observations":len(original),
        "relevant_observations":len(relevant),
        "selected_observations":len(selected),
        "omitted_relevant":len(missing),
        "selected_image_refs":sum(original[x].kind in VISUAL for x in selected),
        "available_frame_refs":sum(o.kind=="sampled_frame" for o in relevant.values()),
        "available_slide_refs":sum(o.kind=="slide_ref" for o in relevant.values()),
    }
    if any(type(counts.get(k)) is not int or counts[k]!=v for k,v in expected.items()):
        _deny("b8_coverage_counter_mismatch")
    if (type(counts.get("coverage_complete")) is not bool or
        counts["coverage_complete"]!=(not missing)):
        _deny("b8_complete_coverage_forged")
    return original,selected,not missing


@dataclass(frozen=True,slots=True)
class B8TopicProposal:
    payload:dict
    proposal_sha256:str
    authenticated_inference:bool

    def verify_digest(self):
        try:
            return canonical_hash(self.payload)==self.proposal_sha256
        except (ValueError,TypeError):
            return False

    @property
    def can_persist(self):
        p=self.payload
        return (
            self.authenticated_inference is True and self.verify_digest() and
            p.get("classification_status")=="resolved" and
            p.get("review_reasons")==[] and
            type(p.get("category_id")) is int and p["category_id"]>0 and
            type(p.get("topic_id")) is int and p["topic_id"]>0 and
            type(p.get("subtopic_id")) in (int,type(None)) and
            p.get("taxonomy_version")==TAXONOMY and
            _sha(p.get("registry_snapshot_sha256"))
        )

    def db_fields(self):
        if not self.can_persist:
            _deny("b8_unapproved_topic_assignment")
        return {x:self.payload[x] for x in
                ("category_id","topic_id","subtopic_id","taxonomy_version","needs_review")}


def unknown_b8(*,manifest:MediaEvidenceManifest,reason:str)->B8TopicProposal:
    if (not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        manifest.content_kind not in VIDEO+CAROUSEL or
        type(reason) is not str or not _REASON.fullmatch(reason)):
        _deny("b8_unknown_source_or_reason_invalid")
    p={
        "schema_version":VERSION,"taxonomy_version":TAXONOMY,
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "registry_snapshot_sha256":None,
        "category_label":None,"topic_label":None,"subtopic_label":None,
        "category_id":None,"topic_id":None,"subtopic_id":None,
        "needs_review":True,"alias_levels":[],
        "evidence_refs":[],"delivered_image_refs":[],
        "model_request_sha256":None,"provider_response_id":None,
        "classification_status":"unknown","review_reasons":[reason],
    }
    return B8TopicProposal(p,canonical_hash(p),False)


def validate_b8_topic(*,manifest:MediaEvidenceManifest,packet:ContextPacket,
                      registry:TaxonomySnapshot,response:VerifiedChatResponse,
                      receipt_verifier:object,
                      delivered_image_refs:tuple[str,...])->B8TopicProposal:
    source,selected,all_selected=_verify_source(manifest,packet)
    if (not isinstance(registry,TaxonomySnapshot) or
        not registry.verify_digest()):
        _deny("b8_registry_unverified")
    selected_images={k for k in selected if source[k].kind in VISUAL}
    if (type(delivered_image_refs) is not tuple or
        len(delivered_image_refs)!=len(selected_images) or
        len(set(delivered_image_refs))!=len(delivered_image_refs) or
        set(delivered_image_refs)!=selected_images):
        _deny("b8_original_images_not_delivered")
    if (not isinstance(response,VerifiedChatResponse) or
        not _sha(response.request_sha256) or
        type(response.response_id) is not str or not response.response_id or
        type(response.model) is not str or not response.model):
        _deny("b8_provider_response_invalid")
    verify=getattr(receipt_verifier,"verify_b8_response",None)
    if not callable(verify):
        _deny("b8_provider_receipt_missing")
    try:
        accepted=verify(response,packet,delivered_image_refs)
    except Exception:
        _deny("b8_provider_receipt_failed")
    if accepted is not True:
        _deny("b8_provider_receipt_failed")
    data=response.json_data
    if type(data) is not dict or set(data)!=_KEYS:
        _deny("b8_model_schema_invalid")
    category,topic,subtopic=(
        data["category_label"],data["topic_label"],data["subtopic_label"]
    )
    if any(type(v) is not str or len(v)>160 or "\x00" in v
           for v in (category,topic,subtopic)):
        _deny("b8_model_topic_label_invalid")
    confidence=data["confidence"]
    if confidence not in ("high","medium","low") or type(confidence) is not str:
        _deny("b8_model_confidence_invalid")
    refs=data["evidence_refs"]
    if (type(refs) is not list or not 1<=len(refs)<=80 or
        len(set(map(str,refs)))!=len(refs) or
        any(not _sha(r) or r not in selected for r in refs)):
        _deny("b8_cited_source_not_selected")
    for ref in refs:
        o=source[ref]
        if o.kind=="ocr_span" and o.frame_ref_sha256 not in delivered_image_refs:
            _deny("b8_ocr_frame_not_delivered")
    reasons=[]
    if not all_selected:
        reasons.append("b8_selected_context_incomplete")
    if confidence=="low":
        reasons.append("b8_low_confidence")
    ids={"category_id":None,"topic_id":None,"subtopic_id":None}
    aliases=[]
    if not category or not topic:
        reasons.append("b8_model_topic_unknown")
    else:
        try:
            category_id, cat_alias=registry.resolve("category",category)
            if category_id is None:
                reasons.append("new_category_requires_review")
            else:
                ids["category_id"]=category_id
                if cat_alias:
                    aliases.append("category")
                    # An old category's topics may need explicit reparenting.
                    reasons.append("category_merge_requires_topic_review")
                topic_id,topic_alias=registry.resolve(
                    "topic",topic,category_id
                )
                if topic_id is None:
                    reasons.append("new_or_cross_category_topic_requires_review")
                else:
                    ids["topic_id"]=topic_id
                    if topic_alias:
                        aliases.append("topic")
                    if subtopic:
                        sub_id,sub_alias=registry.resolve(
                            "subtopic",subtopic,topic_id
                        )
                        if sub_id is None:
                            reasons.append("new_or_cross_topic_subtopic_requires_review")
                        else:
                            ids["subtopic_id"]=sub_id
                            if sub_alias:
                                aliases.append("subtopic")
        except TopicRegistryError as error:
            if error.reason=="taxonomy_alias_ambiguous":
                reasons.append("ambiguous_semantic_registry_match")
            else:
                _deny("b8_registry_inconsistent")
    resolved=not reasons and ids["category_id"] is not None and ids["topic_id"] is not None
    p={
        "schema_version":VERSION,"taxonomy_version":TAXONOMY,
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "registry_snapshot_sha256":registry.snapshot_sha256,
        "category_label":category,"topic_label":topic,
        "subtopic_label":subtopic,**ids,
        "needs_review":not resolved,"alias_levels":aliases,
        "evidence_refs":list(refs),
        "delivered_image_refs":list(delivered_image_refs),
        "model_request_sha256":response.request_sha256,
        "provider_response_id":response.response_id,
        "classification_status":"resolved" if resolved else "needs_review",
        "review_reasons":sorted(set(reasons)),
    }
    return B8TopicProposal(p,canonical_hash(p),True)


@dataclass(frozen=True,slots=True)
class B8PreparedRequest:
    body:dict
    request_sha256:str
    prompt_sha256:str
    def verify_digest(self):
        try:
            return canonical_hash(self.body)==self.request_sha256
        except (ValueError,TypeError):
            return False


def prepare_b8_request(*,catalog,model:str,packet:ContextPacket,
                       max_output_tokens:int,attachments:tuple=())->B8PreparedRequest:
    if (not isinstance(packet,ContextPacket) or not packet.verify_digest() or
        packet.payload.get("block")!="B8" or
        packet.payload.get("delivery_state")!="not_sent" or
        packet.payload.get("media_delivery_state")!="opaque_refs_not_attached"):
        _deny("b8_packet_not_unsent")
    from ..providers.openrouter import prepare_chat_request
    from .b8_schema import B8_OUTPUT_SCHEMA
    try:
        prompt=(Path(__file__).resolve().parents[1]/
                "prompts"/"b8_topic_v1.txt").read_text(encoding="utf-8")
    except (OSError,UnicodeError):
        _deny("b8_trusted_prompt_missing")
    if not prompt or len(prompt.encode("utf-8"))>16000:
        _deny("b8_trusted_prompt_invalid")
    body=prepare_chat_request(
        catalog=catalog,model=model,packet=packet,
        output_schema=B8_OUTPUT_SCHEMA,
        max_output_tokens=max_output_tokens,attachments=attachments,
    )
    body["messages"][0]["content"]+="\n\n"+prompt
    if len(json.dumps(body,ensure_ascii=False).encode("utf-8"))>4_000_000:
        _deny("b8_request_too_large")
    return B8PreparedRequest(body,canonical_hash(body),
                             sha256(prompt.encode("utf-8")).hexdigest())
