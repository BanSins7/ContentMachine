"""UA-MOD-24: versioned project ranking manifests, qualified-only read pages.

Internal API only. D1/QDP/SRE/A/B/C provenance comes from fresh UA19 pins.
RTB is explicitly UNAVAILABLE: it has no verified candidate port in main.
A stored run is a digest-only IMMUTABLE manifest, NOT a capability/grant or
current cache. Recheck every input in a new RR transaction on every page
and export; do not use caller-provided raw rows or historical 'qualified'.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, PROJECT_RE, SHA256_RE, canonical_hash
from .descriptive_aggregation import (
    MIN_SAMPLE, MAX_AGGREGATE, MetricStatistics,
    QUALIFIED_METRIC_SQL, _compatibility, _number, _statistic,
)
from .readside_visibility import (
    CLOCK_SQL, VisibilityPins, VisibleSnapshot, VisibleSnapshotReader, _utc,
)

PROJECT_RUN_WIRE="uae-project-ranked-run/v1"
PAGE_WIRE="uae-project-rank-page/v1"
EXPORT_WIRE="uae-project-rank-export/v1"
SORT_MODES=frozenset(("breakout_desc","views_desc","video_id_asc"))
PAGE_MAX=20
RTB_STATUS="unavailable_not_bound"

# No open-ended project enumeration, no mutable or historical score cache.
RUN_INSERT_SQL="""
INSERT INTO uae_project_aggregate_runs(
 project_id,request_key,run_revision,sort_mode,sample_size,
 compatibility_sha256,lineage_sha256,stats_sha256,ranked_sha256,
 rtb_evidence_state
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (project_id,request_key,run_revision) DO NOTHING
RETURNING id::text AS run_id
"""
RUN_READ_KEY_SQL="""
SELECT id::text AS run_id,project_id,request_key,run_revision,
 schema_version,sort_mode,sample_size,compatibility_sha256,
 lineage_sha256,stats_sha256,ranked_sha256,rtb_evidence_state
FROM uae_project_aggregate_runs
WHERE project_id=%s AND request_key=%s AND run_revision=%s
"""
RUN_READ_ID_SQL="""
SELECT id::text AS run_id,project_id,request_key,run_revision,
 schema_version,sort_mode,sample_size,compatibility_sha256,
 lineage_sha256,stats_sha256,ranked_sha256,rtb_evidence_state
FROM uae_project_aggregate_runs
WHERE project_id=%s AND id=%s::uuid
"""


def _deny(code:str)->None:
    raise AnalyticsBoundaryError(code,"UAE ranked project run rejected: "+code)


def _id(value:str)->bool:
    if type(value) is not str:
        return False
    try:
        return str(UUID(value))==value
    except (TypeError,ValueError,AttributeError):
        return False


def _request_key(value:str)->bool:
    return (type(value) is str and 8<=len(value)<=120
            and all(x.isascii() and (
                x.isalnum() or x in "_.:-") for x in value))


def _pins(pins:tuple[VisibilityPins,...])->None:
    if (type(pins) is not tuple or len(pins)>MAX_AGGREGATE
        or any(not isinstance(p,VisibilityPins) for p in pins)
        or len({p.snapshot_id for p in pins})!=len(pins)
        or len({p.candidate.scope.video_id for p in pins})!=len(pins)
        or (pins and len({p.project_id for p in pins})!=1)):
        _deny("rank_request_invalid")


def _decimal_text(value:Decimal)->str:
    return format(value,"f")


@dataclass(frozen=True,slots=True)
class RankedVideo:
    video_id:int
    snapshot_id:int
    breakout_score:str
    views:int

    def __post_init__(self)->None:
        if (type(self.video_id) is not int or self.video_id<=0
            or type(self.snapshot_id) is not int or self.snapshot_id<=0
            or type(self.breakout_score) is not str
            or type(self.views) is not int or self.views<0):
            _deny("ranked_video_invalid")


@dataclass(frozen=True,slots=True)
class ProjectRankRun:
    run_id:str
    project_id:str
    request_key:str
    run_revision:int
    sort_mode:str
    sample_size:int
    compatibility_sha256:str
    lineage_sha256:str
    stats_sha256:str
    ranked_sha256:str
    breakout:MetricStatistics
    views:MetricStatistics
    rtb_evidence_state:str=RTB_STATUS
    schema_version:str=PROJECT_RUN_WIRE
    descriptive_only:bool=True

    def __post_init__(self)->None:
        if (not _id(self.run_id)
            or type(self.project_id) is not str
            or PROJECT_RE.fullmatch(self.project_id) is None
            or not _request_key(self.request_key)
            or type(self.run_revision) is not int or self.run_revision<=0
            or type(self.sort_mode) is not str or self.sort_mode not in SORT_MODES
            or type(self.sample_size) is not int
            or not MIN_SAMPLE<=self.sample_size<=MAX_AGGREGATE
            or any(type(h) is not str or SHA256_RE.fullmatch(h) is None
                   for h in (self.compatibility_sha256,self.lineage_sha256,
                             self.stats_sha256,self.ranked_sha256))
            or not isinstance(self.breakout,MetricStatistics)
            or not isinstance(self.views,MetricStatistics)
            or self.breakout.sample_size!=self.sample_size
            or self.views.sample_size!=self.sample_size
            or self.rtb_evidence_state!=RTB_STATUS
            or self.schema_version!=PROJECT_RUN_WIRE
            or self.descriptive_only is not True):
            _deny("rank_run_invalid")


@dataclass(frozen=True,slots=True)
class PageCursor:
    """Opaque-ish internal deterministic cursor; a digest is not authentication."""
    run_id:str
    ranked_sha256:str
    sort_mode:str
    next_offset:int
    checksum:str

    def __post_init__(self)->None:
        if (not _id(self.run_id)
            or type(self.ranked_sha256) is not str
            or SHA256_RE.fullmatch(self.ranked_sha256) is None
            or self.sort_mode not in SORT_MODES
            or type(self.next_offset) is not int
            or not 0<self.next_offset<=MAX_AGGREGATE
            or type(self.checksum) is not str
            or self.checksum!=_cursor_sha(self.run_id,self.ranked_sha256,
                                           self.sort_mode,self.next_offset)):
            _deny("rank_cursor_invalid")


def _cursor_sha(run_id:str,sha:str,mode:str,offset:int)->str:
    return canonical_hash({"wire":PAGE_WIRE,"run":run_id,
                           "ranked_sha":sha,"sort":mode,"next":offset})


@dataclass(frozen=True,slots=True)
class RankedPage:
    run:ProjectRankRun
    items:tuple[RankedVideo,...]
    cursor:PageCursor | None
    schema_version:str=PAGE_WIRE

    def __post_init__(self)->None:
        if (not isinstance(self.run,ProjectRankRun)
            or type(self.items) is not tuple or not self.items
            or len(self.items)>PAGE_MAX
            or any(not isinstance(p,RankedVideo) for p in self.items)
            or len({p.video_id for p in self.items})!=len(self.items)
            or (self.cursor is not None
                and (not isinstance(self.cursor,PageCursor)
                     or self.cursor.run_id!=self.run.run_id
                     or self.cursor.ranked_sha256!=self.run.ranked_sha256))
            or self.schema_version!=PAGE_WIRE):
            _deny("rank_page_invalid")


@dataclass(frozen=True,slots=True)
class RankedExport:
    """Full ≤50 rows, only internally after rechecking CURRENT visibility."""
    run:ProjectRankRun
    items:tuple[RankedVideo,...]
    schema_version:str=EXPORT_WIRE

    def __post_init__(self)->None:
        if (not isinstance(self.run,ProjectRankRun)
            or type(self.items) is not tuple
            or len(self.items)!=self.run.sample_size
            or any(not isinstance(p,RankedVideo) for p in self.items)
            or len({p.video_id for p in self.items})!=len(self.items)
            or self.schema_version!=EXPORT_WIRE):
            _deny("rank_export_invalid")


@dataclass(frozen=True,slots=True)
class _Verified:
    project_id:str
    sort_mode:str
    sample_size:int
    compatibility_sha256:str
    lineage_sha256:str
    stats_sha256:str
    ranked_sha256:str
    breakout:MetricStatistics
    views:MetricStatistics
    items:tuple[RankedVideo,...]


def _sorted(items:tuple[RankedVideo,...],mode:str)->tuple[RankedVideo,...]:
    if mode=="breakout_desc":
        return tuple(sorted(items,key=lambda p:(
            -Decimal(p.breakout_score),p.video_id,p.snapshot_id)))
    if mode=="views_desc":
        return tuple(sorted(items,key=lambda p:(
            -p.views,p.video_id,p.snapshot_id)))
    if mode=="video_id_asc":
        return tuple(sorted(items,key=lambda p:(p.video_id,p.snapshot_id)))
    _deny("rank_sort_invalid")


def _row_or_none(db:Any,sql:str,params:tuple)->dict | None:
    rows=db.execute(sql,params).fetchall()
    if type(rows) is not list or len(rows)>1 or any(
        not isinstance(r,Mapping) for r in rows
    ):
        _deny("rank_storage_invalid")
    return dict(rows[0]) if rows else None


def _has_match(row:dict | None,verified:_Verified,
               request_key:str,revision:int)->bool:
    return (row is not None
        and row.get("project_id")==verified.project_id
        and row.get("request_key")==request_key
        and row.get("run_revision")==revision
        and row.get("schema_version")==PROJECT_RUN_WIRE
        and row.get("sort_mode")==verified.sort_mode
        and row.get("sample_size")==verified.sample_size
        and row.get("compatibility_sha256")==verified.compatibility_sha256
        and row.get("lineage_sha256")==verified.lineage_sha256
        and row.get("stats_sha256")==verified.stats_sha256
        and row.get("ranked_sha256")==verified.ranked_sha256
        and row.get("rtb_evidence_state")==RTB_STATUS
        and _id(row.get("run_id")))


class ProjectRankedRunStore:
    """Privileged server-only; PostgreSQL dict_row, no public API.

    An old digest/manifest never authorizes reading. Every call re-evaluates
    ALL selected pins in one fresh repeatable-read transaction. Any hidden,
    changed or incompatible member suppresses the entire result.
    """

    def __init__(self,conn:Any):
        if conn is None or not callable(getattr(conn,"execute",None)) or not callable(
            getattr(conn,"transaction",None)):
            _deny("rank_connection_invalid")
        self._conn=conn

    def _validate(self,pins:tuple[VisibilityPins,...])->bool:
        _pins(pins)
        return len(pins)>=MIN_SAMPLE

    def _verify_all(self,db:Any,pins:tuple[VisibilityPins,...],
                    mode:str,at:Any)->_Verified | None:
        # First authorize *every* peer/source/asset before any score read.
        visible=VisibleSnapshotReader(db)
        for p in pins:
            proof=visible._read(db,p,at)
            if (type(proof) is not VisibleSnapshot
                or proof.project_id!=p.project_id
                or proof.snapshot_id!=p.snapshot_id
                or proof.video_id!=p.candidate.scope.video_id):
                return None
        ordered=sorted(pins,key=lambda p:p.candidate.scope.video_id)
        values=[]
        manifests=[]
        compatibility=[]
        for p in ordered:
            scope=p.candidate.scope
            facts=_row_or_none(db,QUALIFIED_METRIC_SQL,(
                p.project_id,p.snapshot_id,scope.video_id,
            ))
            if facts is None:
                return None
            key=_compatibility(facts,p)
            if key is None:
                return None
            score=_number(facts.get("breakout_score"))
            views=_number(facts.get("views"))
            if (score is None or views is None or views<0
                or views!=views.to_integral_value()
                or views>=Decimal(2)**63):
                return None
            fp=getattr(p.candidate,"fingerprint",None)
            if type(fp) is not str or SHA256_RE.fullmatch(fp) is None:
                return None
            # D1 scope, SRE/QDP and exact A/B/C producer/rights pins are
            # in joined fingerprint; fingerprint is not a peer signature.
            manifests.append({"snapshot":p.snapshot_id,
                              "video":scope.video_id,
                              "join_fingerprint":fp})
            compatibility.append(canonical_hash(key))
            values.append(RankedVideo(
                scope.video_id,p.snapshot_id,_decimal_text(score),int(views)
            ))
        if len(set(compatibility))!=1:
            return None
        stats_a=_statistic(tuple(Decimal(x.breakout_score) for x in values))
        stats_v=_statistic(tuple(Decimal(x.views) for x in values))
        ranked=_sorted(tuple(values),mode)
        return _Verified(
            project_id=pins[0].project_id,sort_mode=mode,
            sample_size=len(values),compatibility_sha256=compatibility[0],
            lineage_sha256=canonical_hash({
                "wire":PROJECT_RUN_WIRE,"lineage":manifests,
                # No RTB receipt/claim until supported verified RTB adapter.
                "rtb":RTB_STATUS,
            }),
            stats_sha256=canonical_hash({
                "compatibility":compatibility[0],
                "breakout":asdict(stats_a),"views":asdict(stats_v),
            }),
            ranked_sha256=canonical_hash({
                "sort":mode,
                "members":[asdict(v) for v in ranked],
            }),
            breakout=stats_a,views=stats_v,items=ranked,
        )

    @staticmethod
    def _to_receipt(row:dict,verified:_Verified)->ProjectRankRun:
        return ProjectRankRun(
            run_id=row["run_id"],project_id=verified.project_id,
            request_key=row["request_key"],
            run_revision=row["run_revision"],
            sort_mode=verified.sort_mode,
            sample_size=verified.sample_size,
            compatibility_sha256=verified.compatibility_sha256,
            lineage_sha256=verified.lineage_sha256,
            stats_sha256=verified.stats_sha256,
            ranked_sha256=verified.ranked_sha256,
            breakout=verified.breakout,views=verified.views,
        )

    def _tx_open(self,db:Any)->Any:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        clock=db.execute(CLOCK_SQL).fetchone()
        if not isinstance(clock,Mapping):
            _deny("rank_clock_invalid")
        at=_utc(clock.get("db_now"))
        if at is None:
            _deny("rank_clock_invalid")
        return at

    def _assert_fresh(self)->None:
        if getattr(getattr(self._conn,"info",None),"transaction_status",1)!=0:
            _deny("rank_fresh_transaction_required")

    def create(self,pins:tuple[VisibilityPins,...],request_key:str,
               *,run_revision:int=1,sort_mode:str="breakout_desc"
               )->ProjectRankRun | None:
        if (not _request_key(request_key)
            or type(run_revision) is not int or run_revision<=0
            or type(sort_mode) is not str or sort_mode not in SORT_MODES):
            _deny("rank_request_invalid")
        if not self._validate(pins):
            return None
        self._assert_fresh()
        db=self._conn
        try:
            with db.transaction():
                at=self._tx_open(db)
                verified=self._verify_all(db,pins,sort_mode,at)
                if verified is None:
                    return None
                inserted=_row_or_none(db,RUN_INSERT_SQL,(
                    verified.project_id,request_key,run_revision,
                    sort_mode,verified.sample_size,
                    verified.compatibility_sha256,verified.lineage_sha256,
                    verified.stats_sha256,verified.ranked_sha256,RTB_STATUS,
                ))
                row=inserted or _row_or_none(db,RUN_READ_KEY_SQL,(
                    verified.project_id,request_key,run_revision,
                ))
                if not _has_match(row,verified,request_key,run_revision):
                    _deny("rank_version_conflict")
                return self._to_receipt(row,verified)
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "rank_storage_unavailable","UAE run persistence failed",
            ) from exc

    def _read_current(self,pins:tuple[VisibilityPins,...],run_id:str
                      )->tuple[ProjectRankRun,tuple[RankedVideo,...]] | None:
        if not _id(run_id):
            _deny("rank_id_invalid")
        if not self._validate(pins):
            return None
        self._assert_fresh()
        db=self._conn
        try:
            with db.transaction():
                at=self._tx_open(db)
                # No read without a fresh, complete candidate UA19 check.
                # First verify *ALL* visibility and rights (not metrics).
                visible=VisibleSnapshotReader(db)
                for p in pins:
                    observed=visible._read(db,p,at)
                    if (type(observed) is not VisibleSnapshot
                        or observed.project_id!=p.project_id
                        or observed.snapshot_id!=p.snapshot_id
                        or observed.video_id!=p.candidate.scope.video_id):
                        return None
                row=_row_or_none(db,RUN_READ_ID_SQL,(
                    pins[0].project_id,run_id,
                ))
                if row is None or row.get("sort_mode") not in SORT_MODES:
                    return None
                # _verify_all itself rechecks all source rights + peers in
                # same transaction before C score reads; fail closed on drift.
                verified=self._verify_all(db,pins,row["sort_mode"],at)
                if verified is None or not _has_match(
                    row,verified,row.get("request_key"),row.get("run_revision")
                ):
                    return None
                return self._to_receipt(row,verified),verified.items
        except AnalyticsBoundaryError:
            raise
        except Exception as exc:
            raise AnalyticsBoundaryError(
                "rank_storage_unavailable","UAE current ranked read failed",
            ) from exc

    def page(self,run_id:str,pins:tuple[VisibilityPins,...],
             *,limit:int=10,cursor:PageCursor | None=None
             )->RankedPage | None:
        if (type(limit) is not int or not 1<=limit<=PAGE_MAX
            or (cursor is not None and not isinstance(cursor,PageCursor))
            or (cursor is not None and cursor.run_id!=run_id)):
            _deny("rank_page_request_invalid")
        result=self._read_current(pins,run_id)
        if result is None:
            return None
        receipt,ranked=result
        offset=0
        if cursor is not None:
            if (cursor.ranked_sha256!=receipt.ranked_sha256
                or cursor.sort_mode!=receipt.sort_mode):
                _deny("rank_stale_cursor")
            offset=cursor.next_offset
        if offset>=len(ranked):
            return None
        end=min(len(ranked),offset+limit)
        next_cursor=(PageCursor(
            receipt.run_id,receipt.ranked_sha256,receipt.sort_mode,
            end,_cursor_sha(receipt.run_id,receipt.ranked_sha256,
                            receipt.sort_mode,end),
        ) if end<len(ranked) else None)
        return RankedPage(receipt,ranked[offset:end],next_cursor)

    def export(self,run_id:str,pins:tuple[VisibilityPins,...]
               )->RankedExport | None:
        """No CSV/writes; typed descriptive export after current rights."""
        result=self._read_current(pins,run_id)
        if result is None:
            return None
        receipt,ranked=result
        return RankedExport(receipt,ranked)


__all__=(
    "PROJECT_RUN_WIRE","PAGE_WIRE","EXPORT_WIRE",
    "SORT_MODES","PAGE_MAX","RTB_STATUS",
    "RUN_INSERT_SQL","RUN_READ_KEY_SQL","RUN_READ_ID_SQL",
    "RankedVideo","ProjectRankRun","PageCursor","RankedPage",
    "RankedExport","ProjectRankedRunStore",
)
