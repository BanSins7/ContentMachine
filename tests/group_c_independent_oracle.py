"""GC-MOD-30: INDEPENDENT exact arithmetic oracle.

Test-only: intentionally imports NO intelligence.group_c functions, schemas,
rounding helpers, kernel constants or production C7 calculator. The externally
committed JSON fixtures are fixed snapshots of expected outputs. This file
recalculates mathematical equations independently using Decimal precision 96.
It is never used by production scoring, IAM, or migrations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext


_SCALE=Decimal("0.000000000001")
_HOUR_US=Decimal(3600000000)
_HALF_LIFE_H=Decimal(48)
_WEIGHTS=(Decimal("0.45"),Decimal("0.30"),Decimal("0.15"),Decimal("0.10"))


class OracleInputError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def _round(number:Decimal)->Decimal:
    with localcontext() as ctx:
        ctx.prec=96
        result=number.quantize(_SCALE,rounding=ROUND_HALF_UP)
    if result.copy_abs()>=Decimal("1e18"):
        raise OracleInputError("numeric_overflow")
    return result


def _number(x,label,*,positive=False,unit=False):
    if type(x) not in (str,int,Decimal):
        raise OracleInputError("missing_"+label if x is None else "invalid_"+label)
    try:
        value=Decimal(str(x))
    except Exception as exc:
        raise OracleInputError("invalid_"+label) from exc
    if (not value.is_finite() or value<0 or (positive and value==0)
            or (unit and value>1)):
        raise OracleInputError("invalid_"+label)
    if _round(value)!=value:
        raise OracleInputError("invalid_precision")
    return value


def _timestamp(text,label):
    try:
        if type(text) is not str:
            raise ValueError("ISO timestamp required")
        value=datetime.fromisoformat(text)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("aware date required")
        return value.astimezone(timezone.utc)
    except (ValueError,OverflowError) as exc:
        raise OracleInputError("invalid_"+label) from exc


def _microseconds(start:datetime,end:datetime):
    delta=end-start
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def oracle_case(case:dict)->dict:
    """Calculates every C1/C2/C3/C4/C5/C6/C7 output from independent inputs.

    Order of rounding is part of the verified contract: C5 weighted rates use
    UNROUNDED per-action smoothing, C7 weights multiply 12-digit C1/C3/C5/C6
    fields and are then individually rounded before summing.
    """
    if not isinstance(case,dict):
        raise OracleInputError("invalid_input")
    for field in ("views","previous_views","likes","comments","shares"):
        value=case.get(field)
        if type(value) is not int or value<0 or value>2**63-1:
            raise OracleInputError("missing_raw_counter" if value is None else "invalid_raw_counter")
    previous=_timestamp(case.get("prev_at"),"previous_observed_at")
    observed=_timestamp(case.get("observed_at"),"observed_at")
    published=_timestamp(case.get("published_at"),"published_at")
    interval=_microseconds(previous,observed)
    age=_microseconds(published,observed)
    if interval<=0:
        raise OracleInputError("invalid_observation_interval")
    if age<0 or previous<published:
        raise OracleInputError("invalid_publication_timestamp")
    v,prior_v=Decimal(case["views"]),Decimal(case["previous_views"])
    if v<prior_v:
        raise OracleInputError("counter_decreased")
    if v==0:
        raise OracleInputError("zero_views_rate_undefined")
    likes=Decimal(case["likes"])
    comments=Decimal(case["comments"])
    shares=Decimal(case["shares"])
    median=_number(case.get("creator_median"),"creator_median")
    niche=case.get("niche_median")
    niche=_number(niche,"niche_median") if niche is not None else None
    alpha=_number(case.get("alpha"),"alpha",positive=True)
    k=_number(case.get("k"),"k",positive=True)
    pl=_number(case.get("p_l"),"prior_like_rate",unit=True)
    pc=_number(case.get("p_c"),"prior_comment_rate",unit=True)
    ps=_number(case.get("p_s"),"prior_share_rate",unit=True)

    with localcontext() as ctx:
        ctx.prec=96
        ctx.Emax=999999999
        ctx.Emin=-999999999
        creator=_round((v+alpha)/(median+alpha))
        outlier=_round((v+alpha)/(niche+alpha)) if niche is not None else None
        growth=_round((v-prior_v)*_HOUR_US/Decimal(interval))
        like_rate=_round(likes/v)
        comment_rate=_round(comments/v)
        share_rate=_round(shares/v)
        engagement_rate=_round((likes+comments+shares)/v)
        weighted_raw=_round((likes+Decimal("1.5")*comments
                             +Decimal(3)*shares)/v)
        denom=v+k
        l=(likes+k*pl)/denom
        c=(comments+k*pc)/denom
        s=(shares+k*ps)/denom
        ls,cs,ss=map(_round,(l,c,s))
        weighted=_round(l+Decimal("1.5")*c+Decimal(3)*s)
        freshness=_round(Decimal(100)*Decimal(2)**(
            -Decimal(age)/_HOUR_US/_HALF_LIFE_H))
        components=[
            _round(metric*weight) for metric,weight in zip(
                (creator,growth,weighted,freshness),_WEIGHTS,
            )
        ]
        score=_round(sum(components,Decimal(0)))
    return {
        "creator":creator,"niche":outlier,"growth":growth,
        "like_rate":like_rate,"comment_rate":comment_rate,
        "share_rate":share_rate,"engagement_rate":engagement_rate,
        "weighted_raw":weighted_raw,
        "like_smoothed":ls,"comment_smoothed":cs,"share_smoothed":ss,
        "weighted_smoothed":weighted,"freshness":freshness,
        "creator_component":components[0],"growth_component":components[1],
        "engagement_component":components[2],"freshness_component":components[3],
        "breakout":score,
    }
