"""UA-MOD-20: private qualified-only descriptive aggregation baseline.

No Group C recalculation, normalization, winsorization, significance,
causal interpretation, user-defined SQL, or direct public endpoint.

Every observed input must pass UA-MOD-19's full current-source/peer gate
and originate from the SAME fresh REPEATABLE READ PostgreSQL transaction.
A denied row, unknown metric, mismatched version, repeated video, or
subthreshold sample suppresses the ENTIRE result. No partial counts leak.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping

from .contracts import AnalyticsBoundaryError, PROJECT_RE, SHA256_RE, canonical_hash
from .readside_visibility import (
    MAX_VISIBLE_BATCH, CLOCK_SQL, VisibilityPins, VisibleSnapshot,
    VisibleSnapshotReader, _utc,
)

AGGREGATION_WIRE = "uae-descriptive-aggregation/v1"
MIN_SAMPLE = 5  # private release floor; no user override below this threshold
MAX_AGGREGATE = MAX_VISIBLE_BATCH
MAX_DECIMAL_DIGITS = 40

# The internal candidate view and peer/source verification are BOTH required.
# No score is fetched before UA-MOD-19 returns a positive exact authorized ID.
QUALIFIED_METRIC_SQL = """
SELECT sn.project_id,sn.id AS snapshot_id,sn.video_id,
 sn.source_id,sn.content_kind,v.platform,
 sn.calculated_metric_id, sn.metric_snapshot_id,
 cm.calculation_config_id, cfg.calculation_version,
 cm.creator_cohort_id,cm.prior_cohort_id,cm.niche_cohort_id,
 cc.definition_version AS creator_definition_version,
 pc.definition_version AS prior_definition_version,
 nc.definition_version AS niche_definition_version,
 cm.score_status,cm.calculation_status,cm.breakout_score,
 ms.views,ms.source_metric_definition,ms.source_metric_version,
 ms.data_quality_status,ms.precision,ms.observation_time_assumed,
 cm.calculated_at
FROM uae_readside_visibility_candidates eligible
JOIN uae_unified_snapshots sn
 ON sn.project_id=eligible.project_id AND sn.id=eligible.snapshot_id
 AND sn.video_id=eligible.video_id
JOIN videos v ON v.id=sn.video_id
JOIN calculated_metrics cm
 ON cm.id=sn.calculated_metric_id AND cm.post_id=sn.video_id
 AND cm.snapshot_id=sn.metric_snapshot_id
JOIN metric_snapshots ms
 ON ms.id=sn.metric_snapshot_id AND ms.video_id=sn.video_id
 AND ms.source_id=sn.source_id
 AND ms.source_policy_version_id=sn.source_policy_version_id
JOIN metric_calculation_configs cfg ON cfg.id=cm.calculation_config_id
JOIN comparison_cohorts cc
 ON cc.id=cm.creator_cohort_id AND cc.purpose='creator'
 AND cc.platform=v.platform AND cc.is_frozen IS TRUE
JOIN comparison_cohorts pc
 ON pc.id=cm.prior_cohort_id AND pc.purpose='engagement_prior'
 AND pc.platform=v.platform AND pc.is_frozen IS TRUE
LEFT JOIN comparison_cohorts nc
 ON nc.id=cm.niche_cohort_id AND nc.purpose='niche'
 AND nc.platform=v.platform AND nc.is_frozen IS TRUE
WHERE eligible.project_id=%s AND eligible.snapshot_id=%s
 AND eligible.video_id=%s
 AND sn.status='qualified'
 AND cm.score_status='complete'
 AND cm.calculation_status='complete'
 AND cfg.is_active IS TRUE
 AND cfg.normalization_enabled IS FALSE
 AND cm.breakout_score IS NOT NULL
 AND ms.views IS NOT NULL
 AND ms.data_quality_status='ok' AND ms.precision='exact'
 AND ms.observation_time_assumed IS FALSE
 AND ms.source_metric_definition='platform_post_views'
 AND ms.source_metric_version IS NOT NULL
 AND ms.observed_at<=statement_timestamp()
 AND ms.collected_at<=statement_timestamp()
 AND (ms.expires_at IS NULL OR ms.expires_at>statement_timestamp())
 AND (cm.niche_cohort_id IS NULL OR nc.id IS NOT NULL)
"""


def _deny(code: str) -> None:
    raise AnalyticsBoundaryError(code, "UAE descriptive aggregation denied: "+code)


def _number(value: Any) -> Decimal | None:
    """Accept PG NUMERIC Decimal and BIGINT int, but never float/NaN/inf."""
    if type(value) is Decimal:
        number=value
    elif type(value) is int:
        number=Decimal(value)
    else:
        return None
    if (not number.is_finite()
        or len(number.as_tuple().digits)>MAX_DECIMAL_DIGITS
        or abs(number.adjusted())>MAX_DECIMAL_DIGITS):
        return None
    return number


def _percentile_linear(values: tuple[Decimal,...],numerator:int,
                       denominator:int) -> Decimal:
    """Type-7 inclusive linear interpolation, exact Decimal arithmetic.

    Canonical golden: (1,2,3,4) => p25 1.75, p50 2.5, p75 3.25.
    """
    if (type(values) is not tuple or not values
        or any(type(v) is not Decimal or not v.is_finite() for v in values)
        or any(values[i]>values[i+1] for i in range(len(values)-1))
        or type(numerator) is not int or type(denominator) is not int
        or denominator<=0 or not 0<=numerator<=denominator):
        _deny("aggregation_percentile_invalid")
    if len(values)==1:
        return values[0]
    with localcontext() as ctx:
        ctx.prec=80
        index=(Decimal(len(values)-1)*numerator)/denominator
        lower=int(index)
        fraction=index-lower
        value=values[lower]+(values[min(lower+1,len(values)-1)]-values[lower])*fraction
        return +value


def _text(value: Decimal) -> str:
    """No floating conversion, no quantization or [0,100] normalization."""
    return format(value,"f")


@dataclass(frozen=True,slots=True)
class MetricStatistics:
    sample_size:int
    minimum:str
    p25:str
    median:str
    p75:str
    maximum:str
    mean:str

    def __post_init__(self)->None:
        if type(self.sample_size) is not int or self.sample_size<MIN_SAMPLE:
            _deny("aggregation_inadequate_sample")
        for x in (self.minimum,self.p25,self.median,self.p75,self.maximum,
                  self.mean):
            if type(x) is not str or _number_str(x) is None:
                _deny("aggregation_statistics_invalid")


def _number_str(value:str) -> Decimal | None:
    if type(value) is not str or len(value)>120:
        return None
    try: x=Decimal(value)
    except (InvalidOperation,ValueError):return None
    return x if _number(x) is not None else None


def _statistic(values:tuple[Decimal,...]) -> MetricStatistics:
    """Pure independent math, not a provenance/qualification operation."""
    if (type(values) is not tuple or not MIN_SAMPLE<=len(values)<=MAX_AGGREGATE
        or any(_number(v) is None for v in values)
        or any(type(v) is not Decimal for v in values)):
        _deny("aggregation_statistics_invalid")
    ordered=tuple(sorted(values))
    with localcontext() as ctx:
        ctx.prec=80
        mean=sum(ordered,Decimal(0))/len(ordered)
    return MetricStatistics(
        len(ordered),_text(ordered[0]),
        _text(_percentile_linear(ordered,1,4)),
        _text(_percentile_linear(ordered,1,2)),
        _text(_percentile_linear(ordered,3,4)),
        _text(ordered[-1]),_text(mean),
    )


@dataclass(frozen=True,slots=True)
class DescriptiveAggregate:
    """No per-video facts, media paths or raw Group C payload."""
    project_id:str
    compatibility_sha256:str
    qualified_video_count:int
    breakout:MetricStatistics
    views:MetricStatistics
    schema_version:str=AGGREGATION_WIRE
    descriptive_only:bool=True
    confidence_intervals_computed:bool=False
    causal_claims_allowed:bool=False

    def __post_init__(self)->None:
        if (type(self.project_id) is not str
            or PROJECT_RE.fullmatch(self.project_id) is None
            or type(self.compatibility_sha256) is not str
            or SHA256_RE.fullmatch(self.compatibility_sha256) is None
            or type(self.qualified_video_count) is not int
            or not MIN_SAMPLE<=self.qualified_video_count<=MAX_AGGREGATE
            or not isinstance(self.breakout,MetricStatistics)
            or not isinstance(self.views,MetricStatistics)
            or self.breakout.sample_size!=self.qualified_video_count
            or self.views.sample_size!=self.qualified_video_count
            or self.schema_version!=AGGREGATION_WIRE
            or self.descriptive_only is not True
            or self.confidence_intervals_computed is not False
            or self.causal_claims_allowed is not False):
            _deny("aggregation_result_invalid")


def _compatibility(row:Mapping[str,Any],pins:VisibilityPins) -> dict | None:
    """Strong, explicit baseline (single stratum). UA21 adds dimensions."""
    c=pins.candidate
    scope=c.scope
    if any(row.get(k)!=v for k,v in (
        ("project_id",scope.project_id),
        ("snapshot_id",pins.snapshot_id),
        ("video_id",scope.video_id),
        ("source_id",scope.source_id),
        ("content_kind",scope.content_kind),
        ("calculated_metric_id",c.group_c.calculated_metric_id),
        ("metric_snapshot_id",c.group_c.current_snapshot_id),
        ("calculation_config_id",c.group_c.config_id),
        ("calculation_version",c.group_c.config_version),
        ("creator_cohort_id",c.group_c.creator_cohort_id),
        ("prior_cohort_id",c.group_c.prior_cohort_id),
        ("niche_cohort_id",c.group_c.niche_cohort_id),
        ("source_metric_version",c.group_c.source_metric_version),
        ("score_status","complete"),
        ("calculation_status","complete"),
        ("data_quality_status","ok"),
        ("precision","exact"),
        ("observation_time_assumed",False),
        ("source_metric_definition","platform_post_views"),
    )):
        return None
    if (type(row.get("platform")) is not str
        or row["platform"] not in ("youtube","tiktok","instagram")
        or any(type(row.get(k)) is not str or not row[k]
            for k in ("creator_definition_version","prior_definition_version"))
        or (c.group_c.niche_cohort_id is None
            and row.get("niche_definition_version") is not None)
        or (c.group_c.niche_cohort_id is not None
            and (type(row.get("niche_definition_version")) is not str
                 or not row["niche_definition_version"]))):
        return None
    return {
        "platform":row["platform"],
        "source":scope.source_id,
        "kind":scope.content_kind,
        "metric_definition":row["source_metric_definition"],
        "metric_version":row["source_metric_version"],
        "c_config_id":c.group_c.config_id,
        "c_version":c.group_c.config_version,
        "creator_cohort_version":row["creator_definition_version"],
        "prior_cohort_version":row["prior_definition_version"],
        "niche_cohort_version":row.get("niche_definition_version"),
        "creator_freeze":c.group_c.creator_freeze_sha256,
        "prior_freeze":c.group_c.prior_freeze_sha256,
        "niche_freeze":c.group_c.niche_freeze_sha256,
        "a_version":c.group_a.analysis_version,
        "b_version":c.group_b.analysis_version,
        "b_taxonomy":c.group_b.taxonomy_version,
        "sre_taxonomy":c.lineage.sre_taxonomy_version_id,
    }


class QualifiedAggregationReader:
    """Trusted internal read: never accept raw caller rows or qualified booleans.

    One project/one explicit compatible stratum. Denial of ANY requested
    snapshot or metric suppresses the whole result, including counts and
    per-group suppression numbers. No arbitrary dimension API at UA20.
    """

    def __init__(self,conn:Any):
        self._visible=VisibleSnapshotReader(conn)
        self._conn=conn

    def summarize_qualified(
        self,pins:tuple[VisibilityPins,...],*,min_sample:int=MIN_SAMPLE,
    ) -> DescriptiveAggregate | None:
        if (type(min_sample) is not int
            or not MIN_SAMPLE<=min_sample<=MAX_AGGREGATE
            or type(pins) is not tuple or not 1<=len(pins)<=MAX_AGGREGATE
            or any(not isinstance(p,VisibilityPins) for p in pins)
            or len({p.snapshot_id for p in pins})!=len(pins)
            or len({p.project_id for p in pins})!=1
            or len({p.candidate.scope.video_id for p in pins})!=len(pins)):
            _deny("aggregation_request_invalid")
        # Pre-IO suppression: 0/1/2/3/4 never read raw facts or return counts.
        if len(pins)<min_sample:
            return None
        db=self._conn
        if getattr(getattr(db,"info",None),"transaction_status",1)!=0:
            _deny("aggregation_fresh_transaction_required")
        rows=[]
        visibility=VisibleSnapshotReader(db)
        try:
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                clock=db.execute(CLOCK_SQL).fetchone()
                if not isinstance(clock,Mapping):
                    _deny("aggregation_clock_invalid")
                at=_utc(clock.get("db_now"))
                if at is None:
                    _deny("aggregation_clock_invalid")
                for p in pins:
                    allowed=visibility._read(db,p,at)
                    if (type(allowed) is not VisibleSnapshot
                        or allowed.project_id!=p.project_id
                        or allowed.snapshot_id!=p.snapshot_id
                        or allowed.video_id!=p.candidate.scope.video_id):
                        return None
                    query=db.execute(QUALIFIED_METRIC_SQL,(
                        p.project_id,p.snapshot_id,p.candidate.scope.video_id
                    )).fetchall()
                    if (type(query) is not list or len(query)!=1
                        or not isinstance(query[0],Mapping)):
                        return None
                    row=dict(query[0])
                    key=_compatibility(row,p)
                    if key is None:
                        return None
                    score=_number(row.get("breakout_score"))
                    views=_number(row.get("views"))
                    if (score is None or views is None
                        or views!=views.to_integral_value()
                        or views<0 or views>=Decimal(2)**63):
                        return None
                    rows.append((key,score,views))
                # No provider/cohort/source-version pooling at UA20.
                keys=[canonical_hash(key) for key,_,_ in rows]
                if len(set(keys))!=1:
                    return None
                return DescriptiveAggregate(
                    project_id=pins[0].project_id,
                    compatibility_sha256=keys[0],
                    qualified_video_count=len(rows),
                    breakout=_statistic(tuple(r[1] for r in rows)),
                    views=_statistic(tuple(r[2] for r in rows)),
                )
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "aggregation_source_unavailable",
                "UAE descriptive aggregation storage failed",
            ) from exc


def summarize_qualified(
    reader:QualifiedAggregationReader,pins:tuple[VisibilityPins,...],
    *,min_sample:int=MIN_SAMPLE,
) -> DescriptiveAggregate | None:
    """Safe public facade over the DB-backed authorization path only."""
    if type(reader) is not QualifiedAggregationReader:
        _deny("aggregation_reader_required")
    return reader.summarize_qualified(pins,min_sample=min_sample)


__all__=(
    "AGGREGATION_WIRE","MIN_SAMPLE","MAX_AGGREGATE",
    "MetricStatistics","DescriptiveAggregate","QualifiedAggregationReader",
    "summarize_qualified","QUALIFIED_METRIC_SQL",
)
