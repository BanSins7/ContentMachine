"""GA-MOD-13: sparse, provenance-bound screen text recurrence tracking.

Matches OCR lines only at *observed frame instants*. Never invents a
continuously-present subtitle, disappearance instant or A003 positive
duration interval. GA-MOD-18 owns atomic persistence after real coverage.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from hashlib import sha256
import re
import unicodedata

from ..contracts_v1 import canonical_sha256

SCHEMA="ga-screen-text-tracking/v1"
METHOD="group-a-screen-text-tracking-v1"
SHA=re.compile(r"^[a-f0-9]{64}$")
TIME=re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{3}$")
MAX_FRAMES=48
MAX_LINES=4000
MAX_TRACKS=4000


class TextTrackingError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__("Group A text tracking rejected: "+code)


@dataclass(frozen=True, slots=True)
class TrackConfig:
    similarity_min: str="0.80"
    max_center_distance: str="0.22"
    min_spatial_iou: str="0.06"
    max_gap_sec: str="1.500"
    min_chars_for_fuzzy: int=5
    merge_duplicate_iou: str="0.70"
    max_tracked_lines: int=4000

    def __post_init__(self):
        for name,lower,upper in (
            ("similarity_min",Decimal("0.65"),Decimal("1")),
            ("max_center_distance",Decimal("0.02"),Decimal("0.50")),
            ("min_spatial_iou",Decimal("0"),Decimal("0.75")),
            ("max_gap_sec",Decimal("0.001"),Decimal("5.000")),
            ("merge_duplicate_iou",Decimal("0.50"),Decimal("1")),
        ):
            v=getattr(self,name)
            if type(v) is not str or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d{1,3})?",v):
                raise TextTrackingError("invalid_tracking_config")
            n=Decimal(v)
            if not lower<=n<=upper:
                raise TextTrackingError("invalid_tracking_config")
        if (type(self.min_chars_for_fuzzy) is not int or not 3<=self.min_chars_for_fuzzy<=30
            or type(self.max_tracked_lines) is not int or not 1<=self.max_tracked_lines<=MAX_LINES):
            raise TextTrackingError("invalid_tracking_config")

    def wire(self):
        return {
            "similarity_min":self.similarity_min,
            "max_center_distance":self.max_center_distance,
            "min_spatial_iou":self.min_spatial_iou,
            "max_gap_sec":self.max_gap_sec,
            "min_chars_for_fuzzy":self.min_chars_for_fuzzy,
            "merge_duplicate_iou":self.merge_duplicate_iou,
            "max_tracked_lines":self.max_tracked_lines,
        }


def _digest(obj,kind):
    if type(obj) is not dict or type(obj.get("observation_sha256" if kind=="ocr" else "manifest_sha256")) is not str:
        raise TextTrackingError("unverified_"+kind)
    field="observation_sha256" if kind=="ocr" else "manifest_sha256"
    val=obj[field]
    if not SHA.fullmatch(val):
        raise TextTrackingError("unverified_"+kind)
    try:
        real=canonical_sha256({k:v for k,v in obj.items() if k!=field})
    except (ValueError,TypeError) as exc:
        raise TextTrackingError("invalid_"+kind) from exc
    if val!=real:
        raise TextTrackingError(kind+"_digest_mismatch")


def _second(x):
    if type(x) is not str or TIME.fullmatch(x) is None:
        raise TextTrackingError("invalid_observed_pts")
    v=Decimal(x)
    if v>Decimal("600.000"):
        raise TextTrackingError("invalid_observed_pts")
    return v


def _box(raw,w,h):
    if type(raw) is not dict or set(raw)!={"x","y","width","height"} or any(
        type(raw[k]) is not int for k in ("x","y","width","height")
    ):
        raise TextTrackingError("invalid_ocr_bbox")
    x,y,bw,bh=(raw[k] for k in ("x","y","width","height"))
    if not (0<=x<w and 0<=y<h and 0<bw<=w-x and 0<bh<=h-y):
        raise TextTrackingError("ocr_bbox_outside_frame")
    return {"x":x,"y":y,"width":bw,"height":bh}


def _union_boxes(boxes):
    x=min(b["x"] for b in boxes);y=min(b["y"] for b in boxes)
    right=max(b["x"]+b["width"] for b in boxes)
    bottom=max(b["y"]+b["height"] for b in boxes)
    return {"x":x,"y":y,"width":right-x,"height":bottom-y}


def _norm(text):
    if type(text) is not str or not text.strip() or len(text)>350:
        raise TextTrackingError("invalid_ocr_text")
    text=unicodedata.normalize("NFKC",text).casefold()
    text=" ".join(text.split())
    # Normalize obvious punctuation differences without losing Cyrillic.
    text=" ".join(re.findall(r"[^\W_]+(?:['\u2019][^\W_]+)*",text,flags=re.UNICODE))
    return text


def _words(text):
    return re.findall(r"[^\W_]+(?:['\u2019][^\W_]+)*",text,flags=re.UNICODE)


def _iou(a,b,width,height):
    # Rectangle equality can be assessed in normalized coordinates even
    # if GA07 frames come from different resolutions.
    def xy(box):
        x=Decimal(box["x"])/width;y=Decimal(box["y"])/height
        return (x,y,x+Decimal(box["width"])/width,y+Decimal(box["height"])/height)
    x1,y1,x2,y2=xy(a);u1,v1,u2,v2=xy(b)
    ww=max(Decimal(0),min(x2,u2)-max(x1,u1))
    hh=max(Decimal(0),min(y2,v2)-max(y1,v1))
    intersection=ww*hh
    area=(x2-x1)*(y2-y1)+(u2-u1)*(v2-v1)-intersection
    return intersection/area if area else Decimal(0)


def _spatial(a,b):
    # Use *each observation's* actual dimensions, not one assumed global
    # resolution. Pixel aspect ratios are already baked into GA07 PNG.
    aw,ah=a["frame_width"],a["frame_height"]
    bw,bh=b["frame_width"],b["frame_height"]
    x=Decimal(a["bbox"]["x"]+a["bbox"]["width"]/2)/aw
    y=Decimal(a["bbox"]["y"]+a["bbox"]["height"]/2)/ah
    u=Decimal(b["bbox"]["x"]+b["bbox"]["width"]/2)/bw
    v=Decimal(b["bbox"]["y"]+b["bbox"]["height"]/2)/bh
    # Normalized center displacement; moving captions may shift a little
    # without suppressing a simultaneously present second caption.
    distance=((x-u)**2+(y-v)**2).sqrt()
    # Exact cross-resolution IoU with normalized fractional boxes.
    def norm(obs):
        box=obs["bbox"];w=Decimal(obs["frame_width"]);h=Decimal(obs["frame_height"])
        xx=Decimal(box["x"])/w;yy=Decimal(box["y"])/h
        return xx,yy,xx+Decimal(box["width"])/w,yy+Decimal(box["height"])/h
    l,t,r,b=norm(a);ll,tt,rr,bb=norm(b)
    ia=max(Decimal(0),min(r,rr)-max(l,ll))*max(Decimal(0),min(b,bb)-max(t,tt))
    denom=(r-l)*(b-t)+(rr-ll)*(bb-tt)-ia
    return distance,ia/denom if denom else Decimal(0)


def _line_candidates(frame):
    grouped={}
    for item in frame["words"]:
        if type(item) is not dict or type(item.get("line_ref")) is not str or not re.fullmatch(r"l\d{1,6}",item["line_ref"]):
            raise TextTrackingError("ocr_line_ref_invalid")
        text=item.get("text")
        if type(text) is not str or not text.strip() or len(text)>350:
            raise TextTrackingError("ocr_text_invalid")
        cf=item.get("confidence_pct")
        if type(cf) is not str or not re.fullmatch(r"(?:100\.00|(?:[0-9]|[1-9][0-9])\.[0-9]{2})",cf):
            raise TextTrackingError("ocr_confidence_invalid")
        box=_box(item.get("bbox"),frame["width"],frame["height"])
        grouped.setdefault(item["line_ref"],[]).append((box,text,cf))
    candidates=[]
    for line,rows in grouped.items():
        # Geometry-derived reading order, not interleaved Tesseract order.
        rows=sorted(rows,key=lambda item:(item[0]["x"],item[0]["y"],item[1]))
        text=" ".join(x[1].strip() for x in rows)
        normalized=_norm(text)
        if not normalized:
            continue
        box=_union_boxes([x[0] for x in rows])
        weight=sum((len(x[1]) for x in rows))
        conf=sum((Decimal(x[2])*len(x[1]) for x in rows),Decimal(0))/weight
        candidates.append({
            "text":text,"normalized_text":normalized,"bbox":box,
            "confidence_pct":format(conf.quantize(Decimal("0.01")),".2f"),
            "line_ref":line,"word_count":len(_words(normalized)),
            "frame_width":frame["width"],"frame_height":frame["height"],
            "frame_ref":frame["frame_ref"],"sample_index":frame["sample_index"],
            "observed_sec":frame["observed_sec"],
        })
    return sorted(candidates,key=lambda c:(
        c["bbox"]["y"],c["bbox"]["x"],c["line_ref"],c["normalized_text"]))


def _dedup(candidates,cfg):
    kept=[]
    for candidate in candidates:
        redundant=False
        for existing in kept:
            if candidate["normalized_text"]!=existing["normalized_text"]:
                continue
            d,overlap=_spatial(candidate,existing)
            if overlap>=Decimal(cfg.merge_duplicate_iou):
                redundant=True
                break
        if not redundant:
            kept.append(candidate)
    return kept


def _validate_inputs(ocr,manifest):
    _digest(ocr,"ocr")
    _digest(manifest,"manifest")
    if ocr.get("schema_version")!="ga-frame-ocr/v1" or ocr.get("method_version")!="group-a-local-frame-ocr-v1":
        raise TextTrackingError("ocr_contract_incompatible")
    if manifest.get("schema_version")!="group-a-frame-manifest/v1" or manifest.get("coverage",{}).get("type")!="sparse_observations":
        raise TextTrackingError("frame_contract_incompatible")
    if (ocr.get("source_media_sha256")!=manifest.get("source_media_sha256") or
        ocr.get("frame_manifest_sha256")!=manifest["manifest_sha256"] or
        ocr.get("temporal_coverage")!="unknown" or
        ocr.get("screen_text_absence")!="unknown" or
        ocr.get("unsampled_intervals_present") is not True or
        manifest["coverage"].get("unsampled_intervals_present") is not True):
        raise TextTrackingError("ocr_frame_lineage_or_truth_mismatch")
    source=_second(manifest.get("source_duration_sec"))
    frames=ocr.get("frames");source_frames=manifest.get("frames")
    if (type(frames) is not list or type(source_frames) is not list or
        not 1<=len(frames)<=MAX_FRAMES or len(frames)!=len(source_frames)):
        raise TextTrackingError("ocr_frame_count_mismatch")
    if type(ocr.get("project_id")) is not str or not re.fullmatch(r"[a-f0-9]{32}",ocr["project_id"]):
        raise TextTrackingError("invalid_ocr_project")
    if type(ocr.get("run_id")) is not str or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",ocr["run_id"]):
        raise TextTrackingError("invalid_ocr_run")
    count=0;last_sample=0;last_sec=Decimal("-1")
    attempts=0
    for f,s in zip(frames,source_frames):
        if type(f) is not dict or type(s) is not dict:
            raise TextTrackingError("invalid_ocr_frame")
        keys=("frame_ref","sample_index","observed_sec","width","height")
        if any(f.get(k)!=s.get(k) for k in keys) or f.get("frame_sha256")!=s.get("png_sha256"):
            raise TextTrackingError("ocr_frame_identity_mismatch")
        if f.get("state") not in (
            "text_observed","no_text_detected","partial_language",
            "unavailable","failed","skipped_budget"):
            raise TextTrackingError("invalid_ocr_frame_state")
        if (type(f["sample_index"]) is not int or f["sample_index"]<=last_sample or
            type(f["width"]) is not int or type(f["height"]) is not int or
            f["width"]<1 or f["height"]<1):
            raise TextTrackingError("invalid_ocr_frame")
        now=_second(f["observed_sec"])
        if not last_sec<now<source:
            raise TextTrackingError("ocr_pts_out_of_order")
        last_sample=f["sample_index"];last_sec=now
        words=f.get("words")
        if type(words) is not list or len(words)>2000:
            raise TextTrackingError("invalid_ocr_words")
        state=f["state"]
        if state in ("failed","unavailable","skipped_budget"):
            if words or f.get("text_status")!="unknown" or f.get("word_count") is not None:
                raise TextTrackingError("ocr_failed_frame_has_evidence")
        elif state=="no_text_detected":
            if words or f.get("text_status")!="no_text_detected_on_this_frame_only":
                raise TextTrackingError("ocr_no_text_frame_inconsistent")
        elif state=="text_observed":
            if not words or f.get("text_status")!="text_observed":
                raise TextTrackingError("ocr_words_missing")
        elif state=="partial_language":
            if ((words and f.get("text_status")!="text_observed") or
                (not words and f.get("text_status")!="unknown")):
                raise TextTrackingError("ocr_partial_language_truth_invalid")
        if state in ("text_observed","no_text_detected","partial_language"):
            count+=1
        if state!="skipped_budget":
            attempts+=1
    counters=ocr.get("counts")
    if type(counters) is not dict or counters.get("observed")!=count or counters.get("attempted")!=attempts:
        raise TextTrackingError("ocr_frame_accounting_mismatch")
    return source,frames


def _span(track):
    first=track["observations"][0]
    last=track["observations"][-1]
    gap=Decimal(last["observed_sec"])-Decimal(first["observed_sec"])
    return {
        "track_id":track["track_id"],
        "text":track["canonical_text"],
        "normalized_text":track["normalized_text"],
        "word_count":len(_words(track["normalized_text"])),
        "observation_count":len(track["observations"]),
        "first_observed_sec":first["observed_sec"],
        "last_observed_sec":last["observed_sec"],
        "sampled_span_sec":format(gap,".3f"),
        "time_semantics":"observed_instants_only",
        "continuity_verified":False,
        "start_time_bound":"at_or_before_first_observed",
        "end_time_bound":"at_or_after_last_observed",
        "intervening_coverage":"unknown" if len(track["observations"])>1 else "not_applicable",
        "closure_reason":track["closure_reason"],
        "observations":track["observations"],
    }


def track_screen_text(ocr_observation,frame_manifest,*,config=None,verify_current=None):
    """Track repeated OCR lines; never emit invented A003 duration rows.

    The caller must recheck source/project/run access. The canonical OCR
    digest only prevents accidental mutation; it does NOT grant access.
    """
    cfg=TrackConfig() if config is None else config
    if not isinstance(cfg,TrackConfig):
        raise TextTrackingError("invalid_tracking_config")
    duration,frames=_validate_inputs(ocr_observation,frame_manifest)
    context={"project_id":ocr_observation["project_id"],
             "run_id":ocr_observation["run_id"],
             "source_media_sha256":ocr_observation["source_media_sha256"],
             "frame_manifest_sha256":frame_manifest["manifest_sha256"]}
    if not callable(verify_current) or verify_current(context) is not True:
        raise TextTrackingError("tracking_current_rights_unverified")
    tracks=[];active=[];last_frame_sec=None
    count_duplicates=0;count_lines=0
    for frame in frames:
        now=Decimal(frame["observed_sec"])
        valid=frame["state"] in ("text_observed","no_text_detected","partial_language")
        if last_frame_sec is not None and now-last_frame_sec>Decimal(cfg.max_gap_sec):
            for t in active:t["closure_reason"]="sample_gap_exceeded"
            active=[]
        if not valid:
            for t in active:t["closure_reason"]="ocr_gap_unknown"
            active=[]
            last_frame_sec=now
            continue
        lines=_line_candidates(frame)
        count_lines+=len(lines)
        if count_lines>cfg.max_tracked_lines:
            raise TextTrackingError("tracking_line_budget_exceeded")
        observed=_dedup(lines,cfg)
        count_duplicates+=len(lines)-len(observed)
        matched_tracks=set();matched_lines=set()
        scores=[]
        for ti,t in enumerate(active):
            prev=t["observations"][-1]
            for li,line in enumerate(observed):
                former=t["normalized_text"];new=line["normalized_text"]
                similarity=Decimal(str(SequenceMatcher(None,former,new,autojunk=False).ratio()))
                if former!=new and (min(len(former),len(new))<cfg.min_chars_for_fuzzy
                                   or similarity<Decimal(cfg.similarity_min)):
                    continue
                distance,iou=_spatial(prev,line)
                if distance>Decimal(cfg.max_center_distance):
                    continue
                if iou<Decimal(cfg.min_spatial_iou) and distance>Decimal("0.075"):
                    continue
                # Stable global one-to-one assignment avoids merging two
                # simultaneous captions with the same normalized text.
                scores.append((similarity,iou,-distance,ti,li))
        for _,_,_,ti,li in sorted(scores,key=lambda x:(-x[0],-x[1],-x[2],x[3],x[4])):
            if ti in matched_tracks or li in matched_lines:
                continue
            t=active[ti];o=observed[li]
            t["observations"].append(o)
            matched_tracks.add(ti);matched_lines.add(li)
            # Keep the first witnessed original text and stable canonical
            # normalization: edit differences are preserved as observations.
        next_active=[active[i] for i in sorted(matched_tracks)]
        for i,t in enumerate(active):
            if i not in matched_tracks:
                t["closure_reason"]=(
                    "absent_in_sampled_frame" if frame["state"]!="partial_language"
                    else "unverified_language_gap")
        for li,line in enumerate(observed):
            if li in matched_lines:continue
            if len(tracks)>=MAX_TRACKS:
                raise TextTrackingError("tracking_track_budget_exceeded")
            fresh={
                "track_id":f"t{len(tracks)+1:04d}",
                "canonical_text":line["text"],
                "normalized_text":line["normalized_text"],
                "observations":[line],
                "closure_reason":"video_sampling_ended",
            }
            tracks.append(fresh);next_active.append(fresh)
        active=next_active
        last_frame_sec=now
    for t in active:t["closure_reason"]="video_sampling_ended"
    spans=[_span(t) for t in tracks]
    first=min((x["first_observed_sec"] for x in spans),default=None)
    # Vocabulary is deduped PER TRACK, not by every OCR repetition. A
    # reappearance after an observed absence becomes a NEW occurrence.
    counted=sum(x["word_count"] for x in spans)
    data={
        "schema_version":SCHEMA,"method_version":METHOD,
        "source_media_sha256":ocr_observation["source_media_sha256"],
        "frame_manifest_sha256":frame_manifest["manifest_sha256"],
        "ocr_observation_sha256":ocr_observation["observation_sha256"],
        "project_id":ocr_observation["project_id"],
        "run_id":ocr_observation["run_id"],
        "source_duration_sec":format(duration,".3f"),
        "status":"partial" if any(f["state"] in ("text_observed","no_text_detected","partial_language") for f in frames) else "unavailable",
        "sampling_truth":"sparse_instants_only",
        "temporal_coverage":"unknown",
        "full_video_text_absence":"unknown",
        "continuous_intervals_verified":False,
        "first_text_observed_sec":first,
        "first_text_true_onset_sec":None,
        "sampled_screen_word_count":counted if spans else None,
        "video_wide_screen_word_count":None,
        "counts":{
            "sampled_frames":len(frames),
            "recognized_line_observations":count_lines,
            "duplicate_lines_suppressed":count_duplicates,
            "distinct_occurrences":len(spans),
            "observed_frame_instants":sum(1 for f in frames if f["state"] in (
                "text_observed","no_text_detected","partial_language")),
        },
        "tracks":spans,
        # No end>start is provable from a sparse instant. GA18 must only
        # write certified intervals from stronger continuous evidence.
        "a003_screen_text_projection":[],
        "a003_publication_state":"blocked_unverified_continuity",
        "config":cfg.wire(),
    }
    return {**data,"observation_sha256":canonical_sha256(data)}
