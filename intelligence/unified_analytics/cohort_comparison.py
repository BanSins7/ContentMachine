"""UA-MOD-22: descriptive, same-source compatible cohort comparisons.

No raw input rows, score recomputation, causal inference or user API.
All peers and every asset are checked through UA19 in ONE PG17
REPEATABLE READ snapshot before fetching numeric and author/time facts.

A comparison is intentionally limited to two explicitly pinned, project-
authorized groups (5-50 videos each combined <=50), controlled publication
windows, author mode and deterministic equal-age observation bins.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Any, Mapping

from .contracts import AnalyticsBoundaryError, PROJECT_RE, SHA256_RE, canonical_hash
from .readside_visibility import (
    VisibilityPins, VisibleSnapshotReader, VisibleSnapshot, CLOCK_SQL, _utc,
)
from .descriptive_aggregation import (
    MAX_AGGREGATE, MIN_SAMPLE, QUALIFIED_METRIC_SQL,
    _compatibility, _number, _statistic, MetricStatistics,
)

COMPARISON_WIRE="uae-compatible-cohorts/v1"
AGE_BIN_DAYS=frozenset((7,14,30))
AUTHOR_MODES=frozenset(("same_creator","disjoint_creators"))
MAX_AGE_DAYS=365

# Exact publication metadata pin from the already-verified UA19 candidate.
# Views/score are read separately using UA20 QUALIFIED_METRIC_SQL.
# No user-provided SQL identifiers or arbitrary author filters.
AUTHOR_TIME_SQL="""
SELECT sn.project_id,sn.id AS snapshot_id,sn.video_id,
       sn.source_id,sn.source_policy_version_id,
       cm.id AS calculated_metric_id,cm.published_metadata_id,
       v.creator_id,cr.platform AS creator_platform,v.platform,
       md.id AS metadata_id,md.published_at,md.fetched_at,
       md.is_current,md.deleted_at,md.expires_at,
       md.source_id AS metadata_source_id,
       md.source_policy_version_id AS metadata_policy_id,
       ms.id AS metric_snapshot_id,ms.observed_at,
       ms.collected_at,ms.expires_at AS metric_expires_at
FROM uae_readside_visibility_candidates candidate
JOIN uae_unified_snapshots sn
 ON sn.project_id=candidate.project_id
 AND sn.id=candidate.snapshot_id AND sn.video_id=candidate.video_id
JOIN calculated_metrics cm
 ON cm.id=sn.calculated_metric_id AND cm.post_id=sn.video_id
 AND cm.snapshot_id=sn.metric_snapshot_id
JOIN videos v ON v.id=sn.video_id
JOIN creators cr ON cr.id=v.creator_id AND cr.platform=v.platform
JOIN source_video_metadata md
 ON md.id=cm.published_metadata_id
 AND md.video_id=sn.video_id
 AND md.source_id=sn.source_id
 AND md.source_policy_version_id=sn.source_policy_version_id
JOIN metric_snapshots ms
 ON ms.id=sn.metric_snapshot_id AND ms.video_id=sn.video_id
 AND ms.source_id=sn.source_id
 AND ms.source_policy_version_id=sn.source_policy_version_id
WHERE candidate.project_id=%s AND candidate.snapshot_id=%s
 AND candidate.video_id=%s AND sn.status='qualified'
 AND md.is_current IS TRUE AND md.deleted_at IS NULL
 AND (md.expires_at IS NULL OR md.expires_at>statement_timestamp())
 AND (ms.expires_at IS NULL OR ms.expires_at>statement_timestamp())
 AND ms.observed_at<=statement_timestamp()
 AND ms.collected_at<=statement_timestamp()
"""


def _fail(code:str)->None:
    raise AnalyticsBoundaryError(code,"UAE compatible comparison rejected: "+code)


@dataclass(frozen=True,slots=True)
class PublicationWindow:
    """UTC half-open [start,end) publication time; not arbitrary SQL."""
    start:datetime
    end:datetime

    def __post_init__(self)->None:
        a,b=_utc(self.start),_utc(self.end)
        if (a is None or b is None
            or self.start.utcoffset()!=timedelta()
            or self.end.utcoffset()!=timedelta()
            or a>=b or (b-a)>timedelta(days=366)
            or a.year<2000 or b.year>2200):
            _fail("comparison_window_invalid")

    @property
    def duration(self)->timedelta:
        return self.end-self.start

    def includes(self,value:datetime)->bool:
        return self.start<=value<self.end


@dataclass(frozen=True,slots=True)
class ComparisonSpec:
    left_window:PublicationWindow
    right_window:PublicationWindow
    age_bin_days:int=7
    author_mode:str="same_creator"
    creator_id:int | None=None
    min_sample:int=MIN_SAMPLE

    def __post_init__(self)->None:
        if (not isinstance(self.left_window,PublicationWindow)
            or not isinstance(self.right_window,PublicationWindow)
            or type(self.age_bin_days) is not int
            or self.age_bin_days not in AGE_BIN_DAYS
            or type(self.min_sample) is not int
            or not MIN_SAMPLE<=self.min_sample<=MAX_AGGREGATE
            or type(self.author_mode) is not str
            or self.author_mode not in AUTHOR_MODES
            or ((self.author_mode=="same_creator")!=
                (type(self.creator_id) is int and self.creator_id>0))
            or self.left_window.duration!=self.right_window.duration):
            _fail("comparison_spec_invalid")
        # Require identical windows or entirely disjoint ones.
        # Partial overlap permits cohort membership leaks and biased samples.
        a,b=self.left_window,self.right_window
        if not (a==b or a.end<=b.start or b.end<=a.start):
            _fail("comparison_overlap_invalid")


@dataclass(frozen=True,slots=True)
class CohortBinComparison:
    age_bin:str
    left:MetricStatistics
    right:MetricStatistics
    left_views:MetricStatistics
    right_views:MetricStatistics
    breakout_median_difference:str
    views_median_difference:str

    def __post_init__(self)->None:
        if (type(self.age_bin) is not str
            or not self.age_bin.startswith("age_days_")
            or any(not isinstance(v,MetricStatistics) for v in (
                self.left,self.right,self.left_views,self.right_views))
            or self.left.sample_size!=self.left_views.sample_size
            or self.right.sample_size!=self.right_views.sample_size
            or any(type(x) is not str for x in (
                self.breakout_median_difference,self.views_median_difference))):
            _fail("comparison_bin_invalid")


@dataclass(frozen=True,slots=True)
class CompatibleCohortComparison:
    project_id:str
    compatibility_sha256:str
    author_mode:str
    age_bin_days:int
    bins:tuple[CohortBinComparison,...]
    schema_version:str=COMPARISON_WIRE
    descriptive_only:bool=True
    confidence_intervals_computed:bool=False
    causal_claims_allowed:bool=False

    def __post_init__(self)->None:
        if (type(self.project_id) is not str
            or PROJECT_RE.fullmatch(self.project_id) is None
            or type(self.compatibility_sha256) is not str
            or SHA256_RE.fullmatch(self.compatibility_sha256) is None
            or type(self.author_mode) is not str
            or self.author_mode not in AUTHOR_MODES
            or type(self.age_bin_days) is not int
            or self.age_bin_days not in AGE_BIN_DAYS
            or type(self.bins) is not tuple or not self.bins
            or any(not isinstance(b,CohortBinComparison) for b in self.bins)
            or tuple(b.age_bin for b in self.bins)!=tuple(
                sorted({b.age_bin for b in self.bins}))
            or self.schema_version!=COMPARISON_WIRE
            or self.descriptive_only is not True
            or self.confidence_intervals_computed is not False
            or self.causal_claims_allowed is not False):
            _fail("comparison_result_invalid")


def _common_compatibility(row:Mapping[str,Any],pins:VisibilityPins
                           )->dict[str,Any] | None:
    """Exact C + peer check, then compare DEFINITION not creator cohort ID.

    Current UA19 has verified all frozen cohort identities per input.
    Cross-author creator/cohort freeze hashes may rightly differ; C config,
    source metric, cohort definition version, A/B/SRE schemas must not.
    """
    key=_compatibility(row,pins)
    if key is None:
        return None
    for name in ("creator_freeze","prior_freeze","niche_freeze"):
        key.pop(name)
    # Even independently approved histories must use one CURRENT policy
    # version: two generations of rights never become one comparison.
    key["source_policy_version_id"]=pins.candidate.scope.source_policy_version_id
    return key


def _age_bin(published:datetime,observed:datetime,
             days:int,as_of:datetime)->str | None:
    if published>as_of or observed>as_of or observed<published:
        return None
    age=observed-published
    if age>=timedelta(days=MAX_AGE_DAYS+1):
        return None
    first=(age.days//days)*days
    return f"age_days_{first:03d}_{first+days:03d}"


def _delta(a:str,b:str)->str:
    """Exact descriptive difference right - left, no ratio/CI/causality."""
    with localcontext() as c:
        c.prec=80
        return format(Decimal(b)-Decimal(a),"f")


class CompatibleCohortReader:
    """Internal current-rights read of two tightly controlled cohorts.

    All pins MUST be server-selected and project-authorized. A user must
    never be allowed to arbitrarily probe overlapping tiny subsets.
    Results only contain full >=min-N bins present on BOTH sides.
    """

    def __init__(self,conn:Any):
        self._conn=conn
        self._visible=VisibleSnapshotReader(conn)

    def compare(self,left:tuple[VisibilityPins,...],
                right:tuple[VisibilityPins,...],
                spec:ComparisonSpec)->CompatibleCohortComparison | None:
        if (not isinstance(spec,ComparisonSpec)
            or type(left) is not tuple or type(right) is not tuple
            or len(left)+len(right)>MAX_AGGREGATE
            or any(not isinstance(p,VisibilityPins) for p in left+right)
            or len({p.snapshot_id for p in left+right})!=len(left)+len(right)
            or len({p.candidate.scope.video_id for p in left+right})
                 !=len(left)+len(right)
            or (left+right and
                len({p.project_id for p in left+right})!=1)):
            _fail("comparison_request_invalid")
        if len(left)<spec.min_sample or len(right)<spec.min_sample:
            return None
        db=self._conn
        if getattr(getattr(db,"info",None),"transaction_status",1)!=0:
            _fail("comparison_fresh_transaction_required")
        visible=VisibleSnapshotReader(db)
        members:dict[int,dict[str,list[tuple[Decimal,Decimal]]]]={
            0:{},1:{},
        }
        strata:set[str]=set()
        authors=[set(),set()]
        try:
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                clock=db.execute(CLOCK_SQL).fetchone()
                if not isinstance(clock,Mapping):
                    _fail("comparison_clock_invalid")
                now=_utc(clock.get("db_now"))
                if now is None or max(
                    spec.left_window.end,spec.right_window.end
                )>now:
                    return None
                # ALL current approval/right checks before any metric/author
                # facts. No per-member result is returned on failed approval.
                for p in left+right:
                    proof=visible._read(db,p,now)
                    if (type(proof) is not VisibleSnapshot
                        or proof.project_id!=p.project_id
                        or proof.snapshot_id!=p.snapshot_id
                        or proof.video_id!=p.candidate.scope.video_id):
                        return None
                for side,pins in enumerate((left,right)):
                    window=(spec.left_window,spec.right_window)[side]
                    for p in pins:
                        scope=p.candidate.scope
                        facts=db.execute(QUALIFIED_METRIC_SQL,(
                            p.project_id,p.snapshot_id,scope.video_id,
                        )).fetchall()
                        context=db.execute(AUTHOR_TIME_SQL,(
                            p.project_id,p.snapshot_id,scope.video_id,
                        )).fetchall()
                        if (type(facts) is not list or len(facts)!=1
                            or not isinstance(facts[0],Mapping)
                            or type(context) is not list or len(context)!=1
                            or not isinstance(context[0],Mapping)):
                            return None
                        row=dict(facts[0])
                        meta=dict(context[0])
                        common=_common_compatibility(row,p)
                        if common is None:
                            return None
                        strata.add(canonical_hash(common))
                        if len(strata)>1:
                            return None
                        if any(meta.get(k)!=v for k,v in (
                            ("project_id",scope.project_id),
                            ("snapshot_id",p.snapshot_id),
                            ("video_id",scope.video_id),
                            ("source_id",scope.source_id),
                            ("source_policy_version_id",
                             scope.source_policy_version_id),
                            ("calculated_metric_id",
                             p.candidate.group_c.calculated_metric_id),
                            ("published_metadata_id",
                             p.candidate.group_c.metadata_id),
                            ("metadata_id",p.candidate.group_c.metadata_id),
                            ("metadata_source_id",scope.source_id),
                            ("metadata_policy_id",
                             scope.source_policy_version_id),
                            ("metric_snapshot_id",
                             p.candidate.group_c.current_snapshot_id),
                            ("platform",row["platform"]),
                            ("creator_platform",row["platform"]),
                            ("is_current",True),
                            ("deleted_at",None),
                        )):
                            return None
                        # PostgreSQL booleans must be literal True, not\n                        # accidental truthy JSON/int conversions.\n                        if meta.get("is_current") is not True:\n                            return None\n                        creator=meta.get("creator_id")
                        if type(creator) is not int or creator<=0:
                            return None
                        authors[side].add(creator)
                        if (spec.author_mode=="same_creator"
                            and creator!=spec.creator_id):
                            return None
                        published=_utc(meta.get("published_at"))
                        observed=_utc(meta.get("observed_at"))
                        if (published is None or observed is None
                            or not window.includes(published)
                            or (meta.get("expires_at") is not None and
                                (_utc(meta["expires_at"]) or now)<=now)
                            or (meta.get("metric_expires_at") is not None and
                                (_utc(meta["metric_expires_at"]) or now)<=now)):
                            return None
                        age_bin=_age_bin(
                            published,observed,spec.age_bin_days,now
                        )
                        if age_bin is None:
                            return None
                        score=_number(row.get("breakout_score"))
                        views=_number(row.get("views"))
                        if (score is None or views is None or views<0
                            or views!=views.to_integral_value()
                            or views>=Decimal(2)**63):
                            return None
                        members[side].setdefault(age_bin,[]).append(
                            (score,views)
                        )
                if spec.author_mode=="disjoint_creators" and (
                    authors[0]&authors[1]
                ):
                    return None
                if len(strata)!=1:
                    return None
                result=[]
                for label in sorted(set(members[0]) & set(members[1])):
                    a,b=members[0][label],members[1][label]
                    if len(a)<spec.min_sample or len(b)<spec.min_sample:
                        continue
                    sa=_statistic(tuple(x[0] for x in a))
                    sb=_statistic(tuple(x[0] for x in b))
                    va=_statistic(tuple(x[1] for x in a))
                    vb=_statistic(tuple(x[1] for x in b))
                    result.append(CohortBinComparison(
                        label,sa,sb,va,vb,
                        _delta(sa.median,sb.median),
                        _delta(va.median,vb.median),
                    ))
                if not result:
                    return None
                return CompatibleCohortComparison(
                    left[0].project_id,next(iter(strata)),
                    spec.author_mode,spec.age_bin_days,tuple(result),
                )
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "comparison_dependency_unavailable",
                "UAE cohort comparison could not verify its dependencies",
            ) from exc


__all__=(
    "COMPARISON_WIRE","AGE_BIN_DAYS","AUTHOR_MODES","MAX_AGE_DAYS",
    "AUTHOR_TIME_SQL","PublicationWindow","ComparisonSpec",
    "CohortBinComparison","CompatibleCohortComparison",
    "CompatibleCohortReader",
)
