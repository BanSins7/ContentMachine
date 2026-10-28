"""UA-MOD-21: eight typed, qualified-only segmentation dimensions.

All numeric facts are persisted Group C/metric rows via UA-MOD-20.
Group B labels come only from the exact current, UA19-authorized B analysis.
No raw text/media, generic JSON labels, model inference, or arbitrary SQL.

This is an INTERNAL read, not a public analytics query or privacy release.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from .contracts import AnalyticsBoundaryError, canonical_hash, content_type_for_kind, PROJECT_RE, SHA256_RE
from .readside_visibility import VisibleSnapshotReader, VisibilityPins, VisibleSnapshot, _utc, CLOCK_SQL
from .descriptive_aggregation import (
    MIN_SAMPLE, MAX_AGGREGATE, MetricStatistics,
    QUALIFIED_METRIC_SQL, _number, _statistic, _compatibility,
)

SEGMENTATION_WIRE = "uae-typed-segmentation/v1"
DIMENSIONS = frozenset((
    "content_kind", "hook_mechanism", "video_format", "story_role",
    "editing_type", "subtitle_type", "cta_type", "topic_id",
))
HOOKS = frozenset((
    "problem", "promise", "curiosity", "myth_busting", "mistake",
    "surprise", "story", "challenge", "comparison", "proof",
))
VIDEO_FORMATS = frozenset((
    "talking_head", "dialogue_interview", "skit", "demonstration",
    "product_showcase", "screen_recording", "pov", "reaction",
    "voiceover_montage", "animation_motion_graphics", "slideshow_video",
    "vlog_bts",
))
CAROUSEL_FORMATS = frozenset((
    "explainer", "listicle", "step_by_step", "comparison",
    "storytelling", "case_study", "product_catalog", "visual_gallery",
))
STORY_ROLES = frozenset((
    "hook", "context", "problem", "consequence", "goal", "myth",
    "mistake", "cause", "explanation", "fact", "tip", "step",
    "demonstration", "comparison", "example", "proof", "testimonial",
    "product_intro", "product_demo", "before_after", "result",
    "reveal", "summary", "offer", "cta", "other",
))
EDITING = frozenset(("single_take", "slow_cuts", "medium_cuts", "fast_cuts"))
SUBTITLES = frozenset(("none", "standard", "dynamic"))
CTA_TYPES = frozenset((
    "follow", "like", "comment", "share", "save", "profile_visit",
    "link_click", "app_install", "purchase", "sign_up", "dm", "other",
))

# Only B1/B2/B3/B5/B6/B7/B8 table column identifiers.
# Fixed SQL, parameterized exact qualified run+analysis+scope, no user labels.
GROUP_B_DIMENSIONS_SQL = """
SELECT a.id AS analysis_id,a.video_id,a.source_id,
       a.source_policy_version_id,a.content_type,
       a.analysis_version,a.taxonomy_version,
       hook.mechanisms AS hook_mechanisms,
       hook.classification_status AS hook_status,
       hook.taxonomy_version AS hook_taxonomy,
       fmt.video_primary,fmt.carousel_format,
       fmt.classification_status AS format_status,
       fmt.taxonomy_version AS format_taxonomy,
       ed.editing_type,sub.subtitle_type,
       cta.analysis_status AS cta_status,
       cta.taxonomy_version AS cta_taxonomy,
       topic.topic_id,topic.needs_review AS topic_needs_review,
       topic.taxonomy_version AS topic_taxonomy,
       (SELECT cat.merged_into_id
        FROM content_b_categories cat
        WHERE cat.id=topic.category_id) AS category_merged_into,
       (SELECT tp.merged_into_id
        FROM content_b_topics tp
        WHERE tp.id=topic.topic_id) AS topic_merged_into,
       ARRAY(
         SELECT DISTINCT role FROM (
           SELECT fr.role FROM content_b_story_frames fr
           WHERE fr.analysis_id=a.id AND a.content_type='video'
           UNION ALL
           SELECT iv.role FROM content_b_story_intervals iv
           WHERE iv.analysis_id=a.id AND a.content_type='video'
           UNION ALL
           SELECT sl.role FROM content_b_story_slides sl
           WHERE sl.analysis_id=a.id AND a.content_type='carousel'
         ) selected_roles ORDER BY role
       ) AS story_roles,
       ARRAY(
         SELECT DISTINCT c.cta_type FROM content_b_ctas c
         WHERE c.analysis_id=a.id ORDER BY c.cta_type
       ) AS cta_types,
       (SELECT count(*) FROM content_b_ctas c
        WHERE c.analysis_id=a.id
          AND c.extraction_status='needs_review') AS cta_review_count
FROM group_b_runs run
JOIN content_b_analyses a
  ON a.id=run.result_analysis_id
 AND a.video_id=run.video_id
 AND a.source_id=run.source_id
 AND a.source_policy_version_id=run.source_policy_version_id
LEFT JOIN content_b_hooks hook ON hook.analysis_id=a.id
LEFT JOIN content_b_formats fmt ON fmt.analysis_id=a.id
LEFT JOIN content_b_editing ed ON ed.analysis_id=a.id
LEFT JOIN content_b_subtitles sub ON sub.analysis_id=a.id
LEFT JOIN content_b_cta_analyses cta ON cta.analysis_id=a.id
LEFT JOIN content_b_topic_assignments topic ON topic.analysis_id=a.id
WHERE run.id=%s::uuid AND run.project_id=%s AND run.video_id=%s
  AND run.source_id=%s AND run.source_policy_version_id=%s
  AND run.result_analysis_id=%s AND run.state='completed'
  AND a.id=%s AND a.state='completed' AND a.needs_review IS FALSE
"""


def _deny(code: str) -> None:
    raise AnalyticsBoundaryError(
        code, "UAE typed segmentation denied: " + code,
    )


def _labels(values: Any, allowed: frozenset[str]) -> tuple[str, ...]:
    """Typed SQL text[] -> deterministic unique categorical membership."""
    if type(values) not in (list, tuple) or len(values)>256:
        _deny("segmentation_invalid_multilabel")
    if any(type(v) is not str or v not in allowed for v in values):
        _deny("segmentation_invalid_label")
    return tuple(sorted(set(values)))


def _typed_values(row: Mapping[str,Any], dimension: str,
                  content_kind: str, taxonomy: str
                  ) -> tuple[str, ...]:
    """Pure transformation of an ALREADY source-backed B row. Not a read gate."""
    if dimension not in DIMENSIONS or type(dimension) is not str:
        _deny("segmentation_unknown_dimension")
    if type(content_kind) is not str or type(taxonomy) is not str:
        _deny("segmentation_input_invalid")
    kind=content_type_for_kind(content_kind)
    if dimension=="content_kind":
        return (content_kind,)
    if not isinstance(row,Mapping):
        _deny("segmentation_input_invalid")
    if dimension=="hook_mechanism":
        if row.get("hook_status")=="unknown":
            return ()
        if (row.get("hook_status")!="inferred"
            or row.get("hook_taxonomy")!=taxonomy):
            _deny("segmentation_hook_not_current")
        return _labels(row.get("hook_mechanisms"),HOOKS)
    if dimension=="video_format":
        if row.get("format_status") in ("unknown","needs_review"):
            return ()
        if (row.get("format_status")!="inferred"
            or row.get("format_taxonomy")!=taxonomy):
            _deny("segmentation_format_not_current")
        value=row.get("video_primary" if kind=="video" else "carousel_format")
        valid=VIDEO_FORMATS if kind=="video" else CAROUSEL_FORMATS
        if value is None:
            return ()
        if type(value) is not str or value not in valid:
            _deny("segmentation_format_invalid")
        return (value,)
    if dimension=="story_role":
        return _labels(row.get("story_roles"),STORY_ROLES)
    if dimension=="editing_type":
        if kind=="carousel":
            return ()  # not applicable, never pretend 'single_take'
        x=row.get("editing_type")
        if x is None:return ()
        if type(x) is not str or x not in EDITING:
            _deny("segmentation_editing_invalid")
        return (x,)
    if dimension=="subtitle_type":
        if kind=="carousel":
            return ()  # not applicable, not equivalent to 'none'
        x=row.get("subtitle_type")
        if x is None:return ()
        if type(x) is not str or x not in SUBTITLES:
            _deny("segmentation_subtitles_invalid")
        return (x,)
    if dimension=="cta_type":
        if row.get("cta_status") in ("not_run","partial","failed"):
            return ()
        if (row.get("cta_status")!="complete"
            or row.get("cta_taxonomy")!=taxonomy
            or type(row.get("cta_review_count")) is not int
            or row["cta_review_count"]!=0):
            _deny("segmentation_cta_not_current")
        values=_labels(row.get("cta_types"),CTA_TYPES)
        # Zero observed only if an actually COMPLETED B7 extraction ran.
        return values if values else ("none_observed",)
    if dimension=="topic_id":
        if row.get("topic_id") is None:
            return ()  # missing B8 assignment is not a D1 topic
        if (row.get("topic_needs_review") is not False
            or row.get("topic_taxonomy")!=taxonomy
            or row.get("topic_merged_into") is not None
            or row.get("category_merged_into") is not None
            or type(row.get("topic_id")) is not int
            or row["topic_id"]<=0):
            _deny("segmentation_topic_not_current")
        return ("b_topic:"+str(row["topic_id"]),)
    _deny("segmentation_unknown_dimension")


@dataclass(frozen=True,slots=True)
class TypedSegment:
    value: str
    sample_size: int
    breakout: MetricStatistics
    views: MetricStatistics

    def __post_init__(self) -> None:
        if (type(self.value) is not str or not 1<=len(self.value)<=120
            or type(self.sample_size) is not int or self.sample_size<MIN_SAMPLE
            or not isinstance(self.breakout,MetricStatistics)
            or not isinstance(self.views,MetricStatistics)
            or self.breakout.sample_size!=self.sample_size
            or self.views.sample_size!=self.sample_size):
            _deny("segmentation_result_invalid")


@dataclass(frozen=True,slots=True)
class TypedSegmentation:
    project_id: str
    compatibility_sha256: str
    dimension: str
    segments: tuple[TypedSegment,...]
    schema_version: str=SEGMENTATION_WIRE
    descriptive_only: bool=True
    causal_claims_allowed: bool=False

    def __post_init__(self) -> None:
        if (type(self.project_id) is not str
            or PROJECT_RE.fullmatch(self.project_id) is None
            or type(self.compatibility_sha256) is not str
            or SHA256_RE.fullmatch(self.compatibility_sha256) is None
            or self.dimension not in DIMENSIONS
            or type(self.segments) is not tuple or not self.segments
            or any(not isinstance(s,TypedSegment) for s in self.segments)
            or len({s.value for s in self.segments})!=len(self.segments)
            or self.schema_version!=SEGMENTATION_WIRE
            or self.descriptive_only is not True
            or self.causal_claims_allowed is not False):
            _deny("segmentation_result_invalid")


class TypedSegmentationReader:
    """Same-transaction exact B feature + qualified C/metric segmentation.

    No direct caller-provided B payload; zero/subthreshold results are None.
    All requested videos must authorize; one denied item blocks everything.
    Multi-labels produce different category membership, but never repeated
    membership for the same video in the SAME category/stratum.
    """

    def __init__(self,conn:Any):
        self._visible=VisibleSnapshotReader(conn)
        self._conn=conn

    def summarize(self,pins:tuple[VisibilityPins,...],dimension:str,
                  *,min_sample:int=MIN_SAMPLE) -> TypedSegmentation | None:
        if (type(dimension) is not str or dimension not in DIMENSIONS
            or type(min_sample) is not int
            or not MIN_SAMPLE<=min_sample<=MAX_AGGREGATE
            or type(pins) is not tuple or len(pins)>MAX_AGGREGATE
            or any(not isinstance(p,VisibilityPins) for p in pins)
            or len({p.snapshot_id for p in pins})!=len(pins)
            or (pins and len({p.project_id for p in pins})!=1)
            or len({p.candidate.scope.video_id for p in pins})!=len(pins)):
            _deny("segmentation_request_invalid")
        if len(pins)<min_sample:
            return None
        db=self._conn
        if getattr(getattr(db,"info",None),"transaction_status",1)!=0:
            _deny("segmentation_fresh_transaction_required")
        groups:dict[str,dict[int,tuple[Decimal,Decimal]]]={}
        strata:set[str]=set()
        visible=VisibleSnapshotReader(db)
        try:
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                clocks=db.execute(CLOCK_SQL).fetchone()
                if not isinstance(clocks,Mapping):
                    _deny("segmentation_clock_invalid")
                at=_utc(clocks.get("db_now"))
                if at is None:
                    _deny("segmentation_clock_invalid")
                for p in pins:
                    scope=p.candidate.scope
                    verified=visible._read(db,p,at)
                    if (type(verified) is not VisibleSnapshot
                        or verified.project_id!=p.project_id
                        or verified.snapshot_id!=p.snapshot_id
                        or verified.video_id!=scope.video_id):
                        return None
                    # Never fetch scores or B labels before current QA gate.
                    numeric=db.execute(QUALIFIED_METRIC_SQL,(
                        p.project_id,p.snapshot_id,scope.video_id,
                    )).fetchall()
                    if (type(numeric) is not list or len(numeric)!=1
                        or not isinstance(numeric[0],Mapping)):
                        return None
                    row=dict(numeric[0])
                    compatible=_compatibility(row,p)
                    if compatible is None:
                        return None
                    key=canonical_hash(compatible)
                    strata.add(key)
                    if len(strata)>1:
                        return None
                    score=_number(row.get("breakout_score"))
                    views=_number(row.get("views"))
                    if (score is None or views is None or views<0
                        or views!=views.to_integral_value()
                        or views>=Decimal(2)**63):
                        return None
                    if dimension=="content_kind":
                        labels=(scope.content_kind,)
                    else:
                        b=p.candidate.group_b
                        facts=db.execute(GROUP_B_DIMENSIONS_SQL,(
                            b.b_run_id,scope.project_id,scope.video_id,
                            scope.source_id,scope.source_policy_version_id,
                            b.b_analysis_id,b.b_analysis_id,
                        )).fetchall()
                        if (type(facts) is not list or len(facts)!=1
                            or not isinstance(facts[0],Mapping)):
                            return None
                        rec=dict(facts[0])
                        if any(rec.get(field)!=expected for field,expected in (
                            ("analysis_id",b.b_analysis_id),
                            ("video_id",scope.video_id),
                            ("source_id",scope.source_id),
                            ("source_policy_version_id",
                             scope.source_policy_version_id),
                            ("content_type",content_type_for_kind(scope.content_kind)),
                            ("analysis_version",b.analysis_version),
                            ("taxonomy_version",b.taxonomy_version),
                        )):
                            return None
                        labels=_typed_values(
                            rec,dimension,scope.content_kind,b.taxonomy_version
                        )
                    for value in labels: # already unique, sorted and typed
                        members=groups.setdefault(value,{})
                        if scope.video_id in members:
                            _deny("segmentation_duplicate_video")
                        members[scope.video_id]=(score,views)
                if len(strata)!=1:
                    return None
                published=[]
                for value,members in sorted(groups.items()):
                    if len(members)<min_sample:
                        # Do not return low-N value, count or suppression count.
                        continue
                    scores=tuple(v[0] for v in members.values())
                    raw_views=tuple(v[1] for v in members.values())
                    published.append(TypedSegment(
                        value,len(members),_statistic(scores),_statistic(raw_views),
                    ))
                if not published:
                    return None
                # Sort deterministically across all eight dimensions.
                published.sort(key=lambda s:(-s.sample_size,s.value))
                return TypedSegmentation(
                    pins[0].project_id,next(iter(strata)),dimension,
                    tuple(published),
                )
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "segmentation_storage_unavailable",
                "UAE typed segmentation dependency failed",
            ) from exc


__all__=(
    "SEGMENTATION_WIRE","DIMENSIONS","HOOKS","VIDEO_FORMATS",
    "CAROUSEL_FORMATS","STORY_ROLES","EDITING","SUBTITLES","CTA_TYPES",
    "GROUP_B_DIMENSIONS_SQL","TypedSegment","TypedSegmentation",
    "TypedSegmentationReader",
)
