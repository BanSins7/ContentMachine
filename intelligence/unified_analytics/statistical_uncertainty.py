"""UA-MOD-23 | Current-authorized sample quality and uncertainty reporting.

A quality report is only obtained by *running* an existing UA20/UA21/UA22
qualified reader, NEVER by accepting a user-supplied DTO/score table.

An empirical IQR describes dispersion *inside observed eligible samples*.
It is not a confidence interval, significance test, sampling-error bound,
causal contrast or guarantee of population representativeness. The approved
project sampling frame and upstream excluded population are UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from .contracts import AnalyticsBoundaryError
from .readside_visibility import VisibilityPins
from .descriptive_aggregation import (
    MIN_SAMPLE, MetricStatistics, DescriptiveAggregate,
    QualifiedAggregationReader,
)
from .typed_segmentation import (
    DIMENSIONS, TypedSegmentation, TypedSegmentationReader,
)
from .cohort_comparison import (
    ComparisonSpec, CompatibleCohortComparison, CompatibleCohortReader,
)

UNCERTAINTY_WIRE="uae-sample-uncertainty/v1"
UNCERTAINTY_METHOD="empirical_iqr_not_confidence_interval"
INFERENCE_STATUS="unsupported_nonprobability_sampling"
MISSINGNESS_SCOPE="included_current_qualified_observations_only"
UNKNOWN_POPULATION="not_observed_unqualified_or_excluded"


def _deny(code:str)->None:
    raise AnalyticsBoundaryError(code,"UAE uncertainty report denied: "+code)


def _decimal(value:str)->Decimal:
    """Strict already-serialized UAE Decimal metric, no binary conversion."""
    if type(value) is not str or not value or len(value)>120:
        _deny("uncertainty_decimal_invalid")
    try:
        result=Decimal(value)
    except (InvalidOperation,ValueError):
        _deny("uncertainty_decimal_invalid")
    if (not result.is_finite()
        or len(result.as_tuple().digits)>40
        or abs(result.adjusted())>40):
        _deny("uncertainty_decimal_invalid")
    return result


def _decimal_text(value:Decimal)->str:
    return format(value,"f")


@dataclass(frozen=True,slots=True)
class ObservedMissingness:
    """Safe *conditional* denominator, not full project missingness.

    Prior readers fail completely on null/unknown data, so EVERY returned
    eligible member has a persisted score and a source-view observation.
    The size/number of NOT eligible, not approved, or hidden records is
    unknowable from this read. Never emit an invented project denominator.
    """
    included_qualified_n:int
    breakout_observed_n:int
    views_observed_n:int
    breakout_missing_within_included_n:int=0
    views_missing_within_included_n:int=0
    whole_population_n:int | None=None
    excluded_or_unqualified_n:int | None=None
    scope:str=MISSINGNESS_SCOPE
    outside_status:str=UNKNOWN_POPULATION

    def __post_init__(self)->None:
        if (type(self.included_qualified_n) is not int
            or self.included_qualified_n<MIN_SAMPLE
            or any(type(v) is not int or v!=self.included_qualified_n
                   for v in (self.breakout_observed_n,self.views_observed_n))
            or type(self.breakout_missing_within_included_n) is not int
            or self.breakout_missing_within_included_n!=0
            or type(self.views_missing_within_included_n) is not int
            or self.views_missing_within_included_n!=0
            or self.whole_population_n is not None
            or self.excluded_or_unqualified_n is not None
            or self.scope!=MISSINGNESS_SCOPE
            or self.outside_status!=UNKNOWN_POPULATION):
            _deny("uncertainty_missingness_invalid")


@dataclass(frozen=True,slots=True)
class EmpiricalInterquartileRange:
    """Observed distribution p25..p75, NOT estimated uncertainty for mean."""
    sample_size:int
    p25:str
    median:str
    p75:str
    iqr_width:str
    method:str=UNCERTAINTY_METHOD
    confidence_interval:None=None

    def __post_init__(self)->None:
        if (type(self.sample_size) is not int or self.sample_size<MIN_SAMPLE
            or any(type(v) is not str for v in (
                self.p25,self.median,self.p75,self.iqr_width))
            or self.method!=UNCERTAINTY_METHOD
            or self.confidence_interval is not None):
            _deny("uncertainty_interval_invalid")
        a,m,b,w=map(_decimal,(self.p25,self.median,self.p75,self.iqr_width))
        if not (a<=m<=b and b-a==w and w>=0):
            _deny("uncertainty_interval_invalid")


def _interval(stat:MetricStatistics)->EmpiricalInterquartileRange:
    if not isinstance(stat,MetricStatistics):
        _deny("uncertainty_statistics_required")
    a=_decimal(stat.p25)
    b=_decimal(stat.p75)
    with localcontext() as ctx:
        ctx.prec=80
        width=b-a
    return EmpiricalInterquartileRange(
        stat.sample_size,stat.p25,stat.median,stat.p75,
        _decimal_text(width),
    )


def _coverage(stat:MetricStatistics)->ObservedMissingness:
    if not isinstance(stat,MetricStatistics):
        _deny("uncertainty_statistics_required")
    return ObservedMissingness(
        included_qualified_n=stat.sample_size,
        breakout_observed_n=stat.sample_size,
        views_observed_n=stat.sample_size,
    )


@dataclass(frozen=True,slots=True)
class UncertaintyDisclosure:
    """No false probability/confidence/causality statements."""
    exploratory_contrasts:int
    multiple_testing_warning:bool
    statistical_tests_performed:int=0
    p_value:None=None
    confidence_level:None=None
    correction_for_multiple_testing:None=None
    inferential_status:str=INFERENCE_STATUS
    observational_only:bool=True
    causal_claims_allowed:bool=False
    statistically_significant:None=None

    def __post_init__(self)->None:
        if (type(self.exploratory_contrasts) is not int
            or self.exploratory_contrasts<0
            or type(self.multiple_testing_warning) is not bool
            or self.multiple_testing_warning!=(self.exploratory_contrasts>1)
            or type(self.statistical_tests_performed) is not int
            or self.statistical_tests_performed!=0
            or self.p_value is not None
            or self.confidence_level is not None
            or self.correction_for_multiple_testing is not None
            or self.inferential_status!=INFERENCE_STATUS
            or self.observational_only is not True
            or self.causal_claims_allowed is not False
            or self.statistically_significant is not None):
            _deny("uncertainty_disclosure_invalid")


@dataclass(frozen=True,slots=True)
class QualifiedSummaryQuality:
    """Adds audit annotations; data originates in UA20 read, not raw rows."""
    aggregate:DescriptiveAggregate
    missingness:ObservedMissingness
    breakout_iqr:EmpiricalInterquartileRange
    views_iqr:EmpiricalInterquartileRange
    uncertainty:UncertaintyDisclosure
    schema_version:str=UNCERTAINTY_WIRE

    def __post_init__(self)->None:
        if (not isinstance(self.aggregate,DescriptiveAggregate)
            or not isinstance(self.missingness,ObservedMissingness)
            or not isinstance(self.breakout_iqr,EmpiricalInterquartileRange)
            or not isinstance(self.views_iqr,EmpiricalInterquartileRange)
            or not isinstance(self.uncertainty,UncertaintyDisclosure)
            or self.aggregate.qualified_video_count!=self.missingness.included_qualified_n
            or self.aggregate.qualified_video_count!=self.breakout_iqr.sample_size
            or self.aggregate.qualified_video_count!=self.views_iqr.sample_size
            or self.uncertainty.exploratory_contrasts!=0
            or self.schema_version!=UNCERTAINTY_WIRE):
            _deny("uncertainty_summary_invalid")


@dataclass(frozen=True,slots=True)
class QualifiedSegmentQuality:
    value:str
    missingness:ObservedMissingness
    breakout_iqr:EmpiricalInterquartileRange
    views_iqr:EmpiricalInterquartileRange


@dataclass(frozen=True,slots=True)
class QualifiedSegmentationQuality:
    segmentation:TypedSegmentation
    segments:tuple[QualifiedSegmentQuality,...]
    uncertainty:UncertaintyDisclosure
    schema_version:str=UNCERTAINTY_WIRE

    def __post_init__(self)->None:
        if (not isinstance(self.segmentation,TypedSegmentation)
            or type(self.segments) is not tuple
            or len(self.segments)!=len(self.segmentation.segments)
            or any(not isinstance(a,QualifiedSegmentQuality)
                   or a.value!=b.value
                   or a.missingness.included_qualified_n!=b.sample_size
                   or a.breakout_iqr.sample_size!=b.sample_size
                   or a.views_iqr.sample_size!=b.sample_size
                   for a,b in zip(self.segments,self.segmentation.segments))
            or not isinstance(self.uncertainty,UncertaintyDisclosure)
            or self.uncertainty.exploratory_contrasts!=len(self.segments)
            or self.schema_version!=UNCERTAINTY_WIRE):
            _deny("uncertainty_segments_invalid")


@dataclass(frozen=True,slots=True)
class QualifiedBinQuality:
    age_bin:str
    left_missingness:ObservedMissingness
    right_missingness:ObservedMissingness
    left_breakout_iqr:EmpiricalInterquartileRange
    right_breakout_iqr:EmpiricalInterquartileRange
    left_views_iqr:EmpiricalInterquartileRange
    right_views_iqr:EmpiricalInterquartileRange
    difference_confidence_interval:None=None


@dataclass(frozen=True,slots=True)
class QualifiedComparisonQuality:
    comparison:CompatibleCohortComparison
    bins:tuple[QualifiedBinQuality,...]
    uncertainty:UncertaintyDisclosure
    schema_version:str=UNCERTAINTY_WIRE

    def __post_init__(self)->None:
        if (not isinstance(self.comparison,CompatibleCohortComparison)
            or type(self.bins) is not tuple
            or len(self.bins)!=len(self.comparison.bins)
            or any(not isinstance(a,QualifiedBinQuality)
                   or a.age_bin!=b.age_bin
                   or a.difference_confidence_interval is not None
                   or a.left_missingness.included_qualified_n!=b.left.sample_size
                   or a.right_missingness.included_qualified_n!=b.right.sample_size
                   or a.left_breakout_iqr.sample_size!=b.left.sample_size
                   or a.right_breakout_iqr.sample_size!=b.right.sample_size
                   or a.left_views_iqr.sample_size!=b.left_views.sample_size
                   or a.right_views_iqr.sample_size!=b.right_views.sample_size
                   for a,b in zip(self.bins,self.comparison.bins))
            or not isinstance(self.uncertainty,UncertaintyDisclosure)
            or self.uncertainty.exploratory_contrasts!=len(self.bins)
            or self.schema_version!=UNCERTAINTY_WIRE):
            _deny("uncertainty_comparison_invalid")


class QualifiedUncertaintyReader:
    """Only trusted DB-backed readers create reportable observations.

    This facade never receives user-created aggregate objects as evidence.
    Both authorization and underlying numeric facts are read by UA20/21/22
    within those readers' ONE REPEATABLE READ transaction. The annotations
    are computed immediately from their authorized, minimal outputs and
    have no second post-authorization database read / TOCTOU window.
    """

    def __init__(self,conn:Any):
        if (conn is None or not callable(getattr(conn,"execute",None))
            or not callable(getattr(conn,"transaction",None))):
            _deny("uncertainty_connection_required")
        self._conn=conn

    def summarize(self,pins:tuple[VisibilityPins,...],
                  *,min_sample:int=MIN_SAMPLE
                  )->QualifiedSummaryQuality | None:
        result=QualifiedAggregationReader(self._conn).summarize_qualified(
            pins,min_sample=min_sample,
        )
        if result is None:
            return None
        return QualifiedSummaryQuality(
            result,_coverage(result.breakout),_interval(result.breakout),
            _interval(result.views),UncertaintyDisclosure(0),
        )

    def segment(self,pins:tuple[VisibilityPins,...],dimension:str,
                *,min_sample:int=MIN_SAMPLE
                )->QualifiedSegmentationQuality | None:
        if type(dimension) is not str or dimension not in DIMENSIONS:
            _deny("uncertainty_dimension_invalid")
        result=TypedSegmentationReader(self._conn).summarize(
            pins,dimension,min_sample=min_sample,
        )
        if result is None:
            return None
        details=tuple(
            QualifiedSegmentQuality(
                row.value,_coverage(row.breakout),
                _interval(row.breakout),_interval(row.views),
            )
            for row in result.segments
        )
        return QualifiedSegmentationQuality(
            result,details,UncertaintyDisclosure(len(details)),
        )

    def compare(self,left:tuple[VisibilityPins,...],
                right:tuple[VisibilityPins,...],spec:ComparisonSpec
                )->QualifiedComparisonQuality | None:
        if not isinstance(spec,ComparisonSpec):
            _deny("uncertainty_spec_invalid")
        result=CompatibleCohortReader(self._conn).compare(left,right,spec)
        if result is None:
            return None
        details=tuple(
            QualifiedBinQuality(
                b.age_bin,_coverage(b.left),_coverage(b.right),
                _interval(b.left),_interval(b.right),
                _interval(b.left_views),_interval(b.right_views),
            )
            for b in result.bins
        )
        return QualifiedComparisonQuality(
            result,details,UncertaintyDisclosure(len(details)),
        )


__all__=(
    "UNCERTAINTY_WIRE","UNCERTAINTY_METHOD","INFERENCE_STATUS",
    "MISSINGNESS_SCOPE","UNKNOWN_POPULATION",
    "ObservedMissingness","EmpiricalInterquartileRange",
    "UncertaintyDisclosure","QualifiedSummaryQuality",
    "QualifiedSegmentQuality","QualifiedSegmentationQuality",
    "QualifiedBinQuality","QualifiedComparisonQuality",
    "QualifiedUncertaintyReader",
)
