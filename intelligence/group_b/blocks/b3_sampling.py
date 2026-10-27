"""GB-MOD-15: deterministic B3 media sampling *plan*, never frame generation.

The target grid is 2 fps before 5s, 1 fps afterwards; at most 200 seconds
are scheduled, with <=60 requested samples by default. Existing verified A
frame/slide refs are matched with explicit time tolerance. Missing targets
stay missing, never turn into hallucinated observed frames, roles or model
delivery. GA 'scene' chapter markers are only adaptive hints, not semantic
boundaries or certified cuts.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from collections import defaultdict
import re

from ..contracts import canonical_hash
from ..context.manifest import MediaEvidenceManifest
from ..context.evidence import VerifiedObservation
from ..errors import GroupBError

VERSION="group-b-b3-sampling/v1"
VIDEO_KINDS=("video","slideshow_video")
CAROUSEL_KINDS=("photo_carousel","mixed_carousel")
_SHA=re.compile(r"[a-f0-9]{64}\Z")
_MAX_DURATION_MS=86_400_000
_MAX_OBSERVATIONS=2000


class B3SamplingError(GroupBError):
    def __init__(self, reason: str):
        self.reason=reason
        super().__init__("b3_sampling_unavailable","b3: "+reason)


def _deny(reason):
    raise B3SamplingError(reason)


def _nat(value):
    return type(value) is int and value>=0


def _positive(value):
    return type(value) is int and value>0


def _valid_sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


@dataclass(frozen=True,slots=True)
class SamplingPolicy:
    first_5_sec_fps: int = 2
    remaining_fps: int = 1
    max_sampled_frames_target: int = 60
    max_video_window_ms: int = 200_000
    extra_at_scene_cuts: bool = True
    extra_at_text_changes: bool = True

    def __post_init__(self):
        # Contract 005 frozen: this first version has exactly 2/1 fps,
        # can be raised only in a new, separately reviewed version.
        if (type(self.first_5_sec_fps) is not int or self.first_5_sec_fps!=2
            or type(self.remaining_fps) is not int or self.remaining_fps!=1
            or type(self.max_sampled_frames_target) is not int or
            not 10<=self.max_sampled_frames_target<=200
            or type(self.max_video_window_ms) is not int or
            not 5_000<=self.max_video_window_ms<=200_000
            or type(self.extra_at_scene_cuts) is not bool
            or type(self.extra_at_text_changes) is not bool):
            _deny("sampling_policy_invalid")

    def wire(self):
        return {
            "first_5_sec_fps":self.first_5_sec_fps,
            "remaining_fps":self.remaining_fps,
            "max_sampled_frames_target":self.max_sampled_frames_target,
            "max_video_window_ms":self.max_video_window_ms,
            "extra_at_scene_cuts":self.extra_at_scene_cuts,
            "extra_at_text_changes":self.extra_at_text_changes,
        }


@dataclass(frozen=True,slots=True)
class B3SamplingPlan:
    payload: dict
    plan_sha256: str

    def verify_digest(self) -> bool:
        try:
            return canonical_hash(self.payload)==self.plan_sha256
        except (TypeError,ValueError):
            return False

    def wire(self) -> dict:
        return {**self.payload,"plan_sha256":self.plan_sha256}

    def sampling_db_fields(self) -> dict:
        """Metadata only; roles/intervals and story writes belong to GB16+."""
        p=self.payload["policy"]
        return {
            "first_5_sec_fps":Decimal(str(p["first_5_sec_fps"])),
            "remaining_fps":Decimal(str(p["remaining_fps"])),
            "max_sampled_frames_target":p["max_sampled_frames_target"],
            "extra_at_scene_cuts":p["extra_at_scene_cuts"],
            "extra_at_text_changes":p["extra_at_text_changes"],
            "sampling_notes":{
                "schema_version":VERSION,"plan_sha256":self.plan_sha256,
                "coverage":self.payload["coverage"],
                "media_evidence_manifest_sha256":self.payload["media_evidence_manifest_sha256"],
                "source_data_not_model_delivered":True,
            },
        }

    @property
    def matched_refs(self) -> tuple[str,...]:
        """Original image proof IDs only, NOT proof of model consumption."""
        return tuple(x["matched_ref_sha256"] for x in self.payload["slots"]
                     if x["status"]=="matched")


def _verify_manifest(manifest):
    if (not isinstance(manifest,MediaEvidenceManifest) or
        manifest.schema_version!="group-b-media-evidence/v1" or
        not manifest.verify_digest() or
        manifest.content_kind not in VIDEO_KINDS+CAROUSEL_KINDS or
        type(manifest.assets) is not tuple or not manifest.assets or
        len(manifest.assets)>100 or type(manifest.observations) is not tuple or
        len(manifest.observations)>_MAX_OBSERVATIONS):
        _deny("source_manifest_invalid")
    assets={}
    slides=set()
    for asset in manifest.assets:
        if (not _positive(asset.asset_id) or not _valid_sha(asset.source_sha256)
            or asset.media_kind not in ("video","image") or
            not _positive(asset.slide_index) or asset.slide_index in slides
            or asset.asset_id in assets):
            _deny("media_asset_identity_invalid")
        assets[asset.asset_id]=asset
        slides.add(asset.slide_index)
    if slides!=set(range(1,len(assets)+1)):
        _deny("media_slide_ordinals_not_complete")
    if manifest.content_kind in VIDEO_KINDS:
        asset=manifest.assets[0]
        if (len(assets)!=1 or asset.media_kind!="video" or
            not _positive(asset.duration_ms) or
            asset.duration_ms>_MAX_DURATION_MS):
            _deny("video_duration_unavailable")
    else:
        if len(assets)<2:
            _deny("carousel_media_missing")
        kinds={a.media_kind for a in manifest.assets}
        if (manifest.content_kind=="photo_carousel" and kinds!={"image"}) or (
            manifest.content_kind=="mixed_carousel" and kinds!={"image","video"}
        ):
            _deny("carousel_media_kind_inconsistent")
    refs=set()
    for item in manifest.observations:
        if (not isinstance(item,VerifiedObservation) or
            not _valid_sha(item.ref_sha256) or item.ref_sha256 in refs):
            _deny("observation_identity_invalid")
        refs.add(item.ref_sha256)
        media=assets.get(item.media_asset_id)
        if media is None or item.asset_sha256!=media.source_sha256:
            _deny("observation_source_asset_mismatch")
        if item.kind in ("sampled_frame","scene","ocr_span"):
            if (not _nat(item.start_ms) or not _nat(item.end_ms) or
                item.end_ms<item.start_ms or item.timebase!="video_absolute" or
                media.media_kind!="video" or not _positive(media.duration_ms) or
                item.end_ms>media.duration_ms):
                _deny("observation_time_outside_original_media")
        if item.kind=="slide_ref":
            if (item.timebase!="none" or item.slide_index!=media.slide_index or
                item.start_ms is not None or item.end_ms is not None):
                _deny("slide_position_invalid")
    frame_by_ref={x.ref_sha256:x for x in manifest.observations
                  if x.kind=="sampled_frame"}
    for item in manifest.observations:
        if item.kind!="ocr_span":
            continue
        frame=frame_by_ref.get(item.frame_ref_sha256)
        if (frame is None or frame.media_asset_id!=item.media_asset_id
            or frame.start_ms is None or item.start_ms is None or
            item.end_ms is None or
            not item.start_ms<=frame.start_ms<=item.end_ms):
            _deny("ocr_frame_provenance_missing")
    return assets


def _farthest_select(values: tuple[int,...],cap: int,*,seeds=()) -> tuple[int,...]:
    """Stable furthest-first for bounded integer timepoints (no model calls)."""
    candidates=set(values)
    chosen=[v for v in seeds if v in candidates]
    selected=set(chosen)
    if cap<=len(selected):
        return tuple(sorted(selected))[:cap]
    while len(selected)<cap and candidates-selected:
        if not selected:
            winner=min(candidates)
        else:
            winner=min(candidates-selected,
                       key=lambda t:(-min(abs(t-s) for s in selected),t))
        selected.add(winner)
    return tuple(sorted(selected))


def _targets(duration: int,policy: SamplingPolicy,observations):
    horizon=min(duration,policy.max_video_window_ms)
    regular=tuple(range(0,min(horizon,5000),500))+tuple(
        range(5000,horizon,1000)
    )
    if not regular:
        _deny("empty_video_sampling_grid")
    early=tuple(t for t in regular if t<5000)
    markers=set()
    for item in observations:
        if (item.kind=="scene" and policy.extra_at_scene_cuts) or (
            item.kind=="ocr_span" and policy.extra_at_text_changes
        ):
            if _nat(item.start_ms) and 0<item.start_ms<horizon:
                markers.add(item.start_ms)
    budget=policy.max_sampled_frames_target
    if len(regular)<=budget and not markers:
        return tuple((t,"grid") for t in regular),len(regular),horizon,0
    # The 2fps opening is never discarded for long videos at the default
    # 60-slot cap; it is the primary hook-coverage contract.
    selected=set(early[:budget])
    if len(selected)<budget:
        selected.add(regular[-1])
    # At most one quarter of remaining capacity is allocated to adaptive
    # actual A *marker timestamps* (which still need a verified frame).
    extra_budget=min(len(markers),max(0,(budget-len(selected))//4))
    if extra_budget:
        chosen=_farthest_select(tuple(sorted(markers)),extra_budget,
                                seeds=())
        selected.update(chosen)
    if len(selected)<budget:
        off_grid=sum(t not in regular for t in selected)
        # Adaptive hints not on the regular grid already consume capacity.
        # Never create (budget regular + extra adaptive) over-reservations.
        chosen=_farthest_select(regular,budget-off_grid,
                                seeds=tuple(sorted(selected)))
        selected.update(chosen)
    if len(selected)>budget:
        _deny("sampling_budget_invariant")
    dropped_regular_count=sum(t not in selected for t in regular)
    return tuple((t,"adaptive_hint" if t in markers and t not in regular else "grid")
                 for t in sorted(selected)),len(regular),horizon,dropped_regular_count


def _video_slots(manifest,policy):
    asset=manifest.assets[0]
    frames=sorted((x for x in manifest.observations if x.kind=="sampled_frame"),
                  key=lambda o:(o.start_ms,o.ref_sha256))
    grid,raw_grid_count,horizon,dropped_regular_count=_targets(
        asset.duration_ms,policy,manifest.observations
    )
    used=set()
    slots=[]
    for t,source in grid:
        tolerance=250 if t<5000 else 500
        nearby=[f for f in frames if f.ref_sha256 not in used and
                abs(f.start_ms-t)<=tolerance]
        best=min(nearby,key=lambda o:(abs(o.start_ms-t),o.start_ms,o.ref_sha256)
                 ) if nearby else None
        if best is not None:
            used.add(best.ref_sha256)
        slots.append({
            "slot_index":len(slots)+1,
            "target_ms":t,"target_source":source,
            "tolerance_ms":tolerance,
            "slide_index":None,
            "asset_id":asset.asset_id,
            "asset_sha256":asset.source_sha256,
            "matched_ref_sha256":best.ref_sha256 if best else None,
            "matched_kind":"sampled_frame" if best else None,
            "observed_ms":best.start_ms if best else None,
            "status":"matched" if best else "missing",
            "model_delivery_state":"not_sent",
        })
    return slots,{
        "source_duration_ms":asset.duration_ms,
        "scheduled_duration_ms":horizon,
        "duration_truncated":asset.duration_ms>horizon,
        "regular_grid_target_count":raw_grid_count,
        "omitted_regular_grid_targets":dropped_regular_count,
        "observed_frame_count":len(frames),
        "unmatched_source_frame_count":len(frames)-len(used),
    }


def _carousel_slots(manifest,policy):
    ordered=sorted(manifest.assets,key=lambda a:a.slide_index)
    chosen=set(_farthest_select(tuple(a.slide_index for a in ordered),
                               min(len(ordered),policy.max_sampled_frames_target)))
    observations=defaultdict(list)
    for obs in manifest.observations:
        if obs.kind=="slide_ref":
            observations[obs.media_asset_id].append(obs)
        elif obs.kind=="sampled_frame" and obs.slide_index is not None:
            observations[obs.media_asset_id].append(obs)
    slots=[]
    for asset in ordered:
        if asset.slide_index not in chosen:
            state="omitted_budget"
            best=None
        else:
            compatible=sorted(
                [x for x in observations[asset.asset_id] if
                 (x.kind=="slide_ref" and x.slide_index==asset.slide_index and
                  asset.media_kind=="image") or
                 (x.kind=="sampled_frame" and asset.media_kind=="video" and
                  x.slide_index==asset.slide_index)],
                key=lambda x:(x.start_ms if x.start_ms is not None else -1,
                              x.ref_sha256)
            )
            best=compatible[0] if compatible else None
            state="matched" if best else "missing"
        slots.append({
            "slot_index":len(slots)+1,
            "target_ms":None,"target_source":"carousel_slide",
            "tolerance_ms":None,"slide_index":asset.slide_index,
            "asset_id":asset.asset_id,"asset_sha256":asset.source_sha256,
            "matched_ref_sha256":best.ref_sha256 if best else None,
            "matched_kind":best.kind if best else None,
            "observed_ms":best.start_ms if best and best.kind=="sampled_frame" else None,
            "status":state,"model_delivery_state":"not_sent",
        })
    return slots,{
        "source_duration_ms":None,"scheduled_duration_ms":None,
        "duration_truncated":False,
        "regular_grid_target_count":None,
        "omitted_regular_grid_targets":0,
        "observed_frame_count":sum(x.kind=="sampled_frame"
                                    for x in manifest.observations),
        "unmatched_source_frame_count":None,
    }


def build_b3_sampling_plan(manifest: MediaEvidenceManifest, *,
                           policy: SamplingPolicy | None = None) -> B3SamplingPlan:
    """Only source-first verified A evidence -> immutable, explicitly UNSENT plan.

    No image decoding, invented timestamp, inferred story role, completed
    content_b_story_* SQL rows, actual API requests, or model delivery claims.
    """
    policy=SamplingPolicy() if policy is None else policy
    if not isinstance(policy,SamplingPolicy):
        _deny("policy_invalid")
    _verify_manifest(manifest)
    video=manifest.content_kind in VIDEO_KINDS
    slots,extra=(_video_slots(manifest,policy) if video
                 else _carousel_slots(manifest,policy))
    matched=sum(x["status"]=="matched" for x in slots)
    missing=sum(x["status"]=="missing" for x in slots)
    omitted=sum(x["status"]=="omitted_budget" for x in slots)
    selected=matched+missing
    if selected>policy.max_sampled_frames_target or (
        video and len(slots)!=selected
    ):
        _deny("sample_count_budget_invalid")
    all_refs=[x["matched_ref_sha256"] for x in slots if x["matched_ref_sha256"]]
    if len(all_refs)!=len(set(all_refs)):
        _deny("source_ref_reused")
    coverage={
        **extra,
        "total_slots":len(slots),
        "scheduled_target_count":selected,
        "matched_original_refs":matched,
        "missing_scheduled_refs":missing,
        "omitted_budget_slots":omitted,
        "source_media_item_count":len(manifest.assets),
        "complete":missing==0 and omitted==0 and
                   extra["omitted_regular_grid_targets"]==0 and
                   not extra["duration_truncated"] and selected>0,
        "input_source_only":True,
        "delivered_to_model":False,
    }
    payload={
        "schema_version":VERSION,
        "project_id":manifest.project_id,
        "b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,
        "qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "media_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "policy":policy.wire(),
        "slots":slots,
        "coverage":coverage,
        "analysis_state":"planned_not_inferred",
        "role_labels_assigned":False,
    }
    return B3SamplingPlan(payload,canonical_hash(payload))
