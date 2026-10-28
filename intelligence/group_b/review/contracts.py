"""GB-MOD-23 immutable B1–B8 review and gold annotation contracts.

Model SQL005 rows remain untouched; human corrections are explicit,
source-pinned overlays. Gold annotations NEVER edit effective labels. Neither
a supplied reviewer name nor a SHA is an authorization or evidence receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from ..contracts import canonical_hash
from ..errors import GroupBError
from ..blocks.b1_hook import MECHANISMS as HOOK_TYPES, DELIVERY as HOOK_DELIVERY
from ..blocks.b2_format import VIDEO_FORMATS,CAROUSEL_FORMATS
from ..blocks.b3_story import ROLES,PHASES
from ..blocks.b5_editing import KINDS as EDIT_KINDS
from ..blocks.b6_subtitles import TYPES as SUBTITLE_TYPES
from ..blocks.b7_cta import CTA_TYPES,DESTINATIONS,SERVICES
from ..topic_registry import TaxonomySnapshot,read_topic_registry

VERSION="group-b-human-review/v1"
GOLD_VERSION="group-b-gold/v1"
BLOCKS=tuple("B"+str(i) for i in range(1,9))
SHA=re.compile(r"[a-f0-9]{64}\Z")
TOKEN=re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,119}\Z")
_COLOR=re.compile(r"#[0-9A-Fa-f]{6}\Z")
NUMERIC=("cut_count","cuts_first_5_sec","cuts_per_minute","avg_shot_duration")
# Root vs individual ordered component; no dynamically supplied SQL names.
FIELDS={
 "B1":{"mechanisms","delivery","is_question","has_number","direct_address",
       "has_negation","is_command"},
 "B2":{"video_primary","video_secondary","carousel_format"},
 "B3":{"role","before_after_phase"},
 "B4":{"ai_generated","person_visible","product_visible","logo_visible"},
 "B5":{"editing_type","cut_count","cuts_per_minute","avg_shot_duration",
       "cuts_first_5_sec"},
 "B6":{"subtitle_type","position","font_size","font_style","text_color",
       "highlight_color","background_style","display_mode"},
 "B7":{"cta_type","destination_type","destination_service","destination_handle",
       "destination_url","delivery","extraction_status"},
 "B8":{"category_id","topic_id","subtopic_id"},
}
TARGETS={
 "B1":{"hook"},"B2":{"format"},
 "B3":{"frame","interval","slide"},
 "B4":{"visual"},"B5":{"editing"},"B6":{"subtitle"},
 "B7":{"occurrence"},"B8":{"topic"},
}
_ENUM={
 ("B1","mechanisms"):set(HOOK_TYPES),
 ("B1","delivery"):set(HOOK_DELIVERY),
 ("B2","video_primary"):set(VIDEO_FORMATS),
 ("B2","video_secondary"):set(VIDEO_FORMATS),
 ("B2","carousel_format"):set(CAROUSEL_FORMATS),
 ("B3","role"):set(ROLES),
 ("B3","before_after_phase"):set(PHASES),
 ("B5","editing_type"):set(EDIT_KINDS),
 ("B6","subtitle_type"):set(SUBTITLE_TYPES),
 ("B6","position"):{"top","center","bottom"},
 ("B6","font_size"):{"small","medium","large"},
 ("B6","font_style"):{"regular","bold","extra_bold"},
 ("B6","background_style"):{"none","outline","shadow","box"},
 ("B6","display_mode"):{"line","phrase","word_by_word"},
 ("B7","cta_type"):set(CTA_TYPES),
 ("B7","destination_type"):set(DESTINATIONS),
 ("B7","destination_service"):set(SERVICES),
 ("B7","delivery"):{"speech","on_screen_text"},
 ("B7","extraction_status"):{"detected","needs_review"},
}
_NULLABLE={
 ("B2","video_primary"),("B2","video_secondary"),("B2","carousel_format"),
 ("B3","before_after_phase"),("B4","ai_generated"),
 ("B6","position"),("B6","font_size"),("B6","font_style"),
 ("B6","text_color"),("B6","highlight_color"),
 ("B6","background_style"),("B6","display_mode"),
 ("B7","destination_service"),("B7","destination_handle"),
 ("B7","destination_url"),("B8","subtopic_id"),
}
_BOOL={("B1",x) for x in ("is_question","has_number","direct_address",
                          "has_negation","is_command")} | {
    ("B4","ai_generated"),("B4","person_visible"),
    ("B4","product_visible"),("B4","logo_visible")}
_MULTI={("B1","mechanisms"),("B1","delivery"),("B7","delivery")}


class ReviewError(GroupBError):
    def __init__(self,reason):
        self.reason=reason
        super().__init__("b23_review_denied","group_b_review: "+reason)


def _deny(reason):
    raise ReviewError(reason)


def _sha(v):
    return type(v) is str and SHA.fullmatch(v) is not None


def _str(v,limit=300):
    return type(v) is str and 0<len(v)<=limit and "\x00" not in v and bool(v.strip())


def gold_sample_key(*,analysis_id,block,target,index,field):
    """Stable original identity: do not use model prediction or reviewer data."""
    if (type(analysis_id) is not int or analysis_id<=0 or
        type(block) is not str or type(target) is not str or
        type(field) is not str or
        type(index) not in (type(None),int)):
        _deny("gold_sample_key_invalid")
    _target(block,target,index)
    if field not in FIELDS[block]:
        _deny("gold_sample_field_invalid")
    identity=json.dumps([analysis_id,block,target,index,field],
                        separators=(",",":"),ensure_ascii=False)
    return "sample-"+sha256(identity.encode("utf-8")).hexdigest()


def _canon_value(block,field,value):
    if type(field) is not str or field not in FIELDS[block]:
        _deny("review_field_not_whitelisted")
    key=(block,field)
    if value is None:
        if key in _NULLABLE or key in _BOOL and block=="B1":
            return None
        _deny("review_null_not_legal")
    if key in _MULTI:
        if (type(value) is not list or len(value)>12 or
            any(type(x) is not str or x not in _ENUM[key] for x in value) or
            len(set(value))!=len(value)):
            _deny("review_list_not_sql005")
        return sorted(value)
    if key in _BOOL:
        if type(value) is not bool:
            _deny("review_flag_invalid")
        return value
    if key in _ENUM:
        if type(value) is not str or value not in _ENUM[key]:
            _deny("review_enum_not_sql005")
        return value
    if block=="B8":
        if type(value) is not int or value<=0:
            _deny("review_taxonomy_id_invalid")
        return value
    if field in ("cut_count","cuts_first_5_sec"):
        if type(value) is not int or value<0:
            _deny("review_count_invalid")
        return value
    if field in ("cuts_per_minute","avg_shot_duration"):
        if type(value) is not str or len(value)>32:
            _deny("review_decimal_invalid")
        try:
            n=Decimal(value)
        except InvalidOperation:
            _deny("review_decimal_invalid")
        if (not n.is_finite() or n.as_tuple().exponent < -4 or n<0 or
            (field=="avg_shot_duration" and n==0) or n>=Decimal("100000000")):
            _deny("review_decimal_invalid")
        return str(n)
    if field in ("text_color","highlight_color"):
        if type(value) is not str or not _COLOR.fullmatch(value):
            _deny("review_hex_color_invalid")
        return value.upper()
    if field=="destination_handle":
        if type(value) is not str or len(value)>64 or not re.fullmatch(
            r"@[A-Za-z0-9][A-Za-z0-9._-]{0,62}",value
        ):
            _deny("review_handle_invalid")
        return value
    if field=="destination_url":
        from ..blocks.b7_cta import _valid_url,B7CTAError
        try:
            if type(value) is not str:
                _deny("review_url_invalid")
            _valid_url(value)
        except B7CTAError:
            _deny("review_url_invalid")
        return value
    _deny("review_value_not_supported")


def _target(block,target,index):
    if (type(block) is not str or block not in BLOCKS or
        type(target) is not str or target not in TARGETS[block]):
        _deny("review_block_target_invalid")
    if block in ("B3","B7"):
        if type(index) is not int or index<1:
            _deny("review_original_component_index_invalid")
    elif index is not None:
        _deny("review_root_cannot_use_index")


@dataclass(frozen=True,slots=True)
class ReviewDecision:
    payload:dict
    decision_sha256:str

    def verify_digest(self)->bool:
        try:
            return canonical_hash(self.payload)==self.decision_sha256
        except (ValueError,TypeError):
            return False


def make_decision(*,kind,analysis_id,analysis_key,manifest_sha256,
                  previous_revision,previous_sha256,block,target,index,
                  field,value,evidence_refs,reason,
                  gold_version=None,sample_key=None):
    """Pure construction only; a trusted reviewer admission is STILL required."""
    if (kind not in ("correction","gold_annotation") or
        type(analysis_id) is not int or analysis_id<=0 or
        not _str(analysis_key,250) or not _sha(manifest_sha256) or
        type(previous_revision) is not int or previous_revision<0 or
        not (previous_sha256 is None if previous_revision==0
             else _sha(previous_sha256))):
        _deny("review_identity_or_revision_invalid")
    _target(block,target,index)
    normalized=_canon_value(block,field,value)
    if (type(evidence_refs) is not list or not 1<=len(evidence_refs)<=40 or
        any(not _sha(x) for x in evidence_refs) or
        len(set(evidence_refs))!=len(evidence_refs)):
        _deny("review_evidence_refs_invalid")
    if not _str(reason,800):
        _deny("review_reason_missing")
    if kind=="gold_annotation":
        if (not _str(gold_version,100) or not TOKEN.fullmatch(gold_version) or
            sample_key!=gold_sample_key(analysis_id=analysis_id,
                block=block,target=target,index=index,field=field)):
            _deny("gold_version_or_sample_invalid")
    elif gold_version is not None or sample_key is not None:
        _deny("correction_cannot_claim_gold_version")
    payload={
        "schema_version":VERSION,"kind":kind,
        "analysis_id":analysis_id,"analysis_key":analysis_key,
        "source_evidence_manifest_sha256":manifest_sha256,
        "previous_revision":previous_revision,
        "previous_sha256":previous_sha256,
        "block":block,"target":target,"index":index,
        "field":field,"value":normalized,
        "evidence_refs":sorted(evidence_refs),
        "reason":reason.strip(),
        "gold_version":gold_version,"sample_key":sample_key,
    }
    return ReviewDecision(payload,canonical_hash(payload))


def verify_decision(decision)->bool:
    if not isinstance(decision,ReviewDecision) or not decision.verify_digest():
        return False
    p=decision.payload
    try:
        expected=make_decision(
            kind=p["kind"],analysis_id=p["analysis_id"],
            analysis_key=p["analysis_key"],
            manifest_sha256=p["source_evidence_manifest_sha256"],
            previous_revision=p["previous_revision"],
            previous_sha256=p["previous_sha256"],
            block=p["block"],target=p["target"],index=p["index"],
            field=p["field"],value=p["value"],evidence_refs=p["evidence_refs"],
            reason=p["reason"],gold_version=p["gold_version"],
            sample_key=p["sample_key"],
        )
        return expected.payload==p and expected.decision_sha256==decision.decision_sha256
    except (ReviewError,KeyError,TypeError,ValueError):
        return False


def validate_effective_row(block,row,registry:TaxonomySnapshot|None=None):
    """Post-patch checks for whole-row constraints, never trust a field alone."""
    if block=="B2":
        p,s,c=(row.get("video_primary"),row.get("video_secondary"),
               row.get("carousel_format"))
        if ((p is None and s is not None) or (p is not None and s==p) or
            (c is not None and (p is not None or s is not None)) or
            (row.get("_content_type")=="video" and c is not None) or
            (row.get("_content_type")=="carousel" and
             (p is not None or s is not None))):
            _deny("effective_format_cross_fields_conflict")
    elif block=="B3":
        role,phase=row.get("role"),row.get("before_after_phase")
        if (role!="before_after" and phase is not None) or (
            role=="other" and row.get("needs_review") is not True):
            _deny("effective_story_role_phase_conflict")
    elif block=="B5":
        from ..blocks.b5_editing import KINDS
        try:
            cuts=row["cut_count"]
            first=row["cuts_first_5_sec"]
            rate=Decimal(str(row["cuts_per_minute"]))
            avg=Decimal(str(row["avg_shot_duration"]))
            typ=row["editing_type"]
            good=(type(cuts) is int and cuts>=0 and type(first) is int
                  and 0<=first<=cuts and rate.is_finite() and
                  avg.is_finite() and avg>0 and typ in KINDS and
                  (typ!="single_take" or cuts==0 and rate==0) and
                  (typ!="slow_cuts" or cuts>0 and 0<rate<10) and
                  (typ!="medium_cuts" or cuts>0 and 10<=rate<30) and
                  (typ!="fast_cuts" or cuts>0 and rate>=30))
        except (ValueError,TypeError,KeyError,InvalidOperation):
            good=False
        if not good:
            _deny("effective_editing_measures_inconsistent")
    elif block=="B6":
        t=row.get("subtitle_type")
        fields=("position","font_size","font_style","text_color",
                "highlight_color","background_style","display_mode")
        if (t=="none" and any(row.get(x) is not None for x in fields)) or (
            t in ("standard","dynamic") and any(row.get(x) is None for x in
                fields if x!="highlight_color")):
            _deny("effective_subtitle_style_inconsistent")
    elif block=="B7":
        if (row.get("destination_type")!="other" and
            row.get("destination_other_desc") is not None):
            _deny("effective_cta_destination_inconsistent")
    elif block=="B8":
        if not isinstance(registry,TaxonomySnapshot) or not registry.verify_digest():
            _deny("effective_registry_current_missing")
        ids=(row.get("category_id"),row.get("topic_id"),row.get("subtopic_id"))
        cats={x.id for x in registry.categories if x.merged_into_id is None}
        topics={x.id:x for x in registry.topics if x.merged_into_id is None}
        subs={x.id:x for x in registry.subtopics if x.merged_into_id is None}
        if (ids[0] not in cats or ids[1] not in topics or
            topics[ids[1]].parent_id!=ids[0] or
            (ids[2] is not None and
             (ids[2] not in subs or subs[ids[2]].parent_id!=ids[1]))):
            _deny("effective_topic_hierarchy_not_approved")
