"""PIE-MOD-26: project+owner scoped, rights-refreshed, redacted registry retrieval.

Every read requires injected CURRENT project membership authority, fresh typed
UAEReadResult, and SourceEligibilityGate.evaluate() for ALL original source rows.
Absent live UAE or provenance authority => no results; a DB row is not consent.
No candidate here is supported, approved for publication or inferential.
"""
from __future__ import annotations

import base64
import csv
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import hmac
import io
import json
import re
from typing import Callable, Protocol

from ..contracts import PIEBoundaryError, canonical_hash
from ..intake.uae import UAEReadResult
from ..intake.provenance import SourceEligibilityGate
from .pattern_registry import _RECORD_SCHEMA, _ALLOWED

SCHEMA='pie-safe-pattern-registry-query/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_FEATURE=re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_ACTOR=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._:@/-]{0,159}\Z')
_PLATFORM=re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
_MAX_CURSOR_BYTES=4096
_MAX_ROWS_PER_PAGE=80
_CURSOR_TTL_SECONDS=900


def _fail(code:str,message:str)->None:
    raise PIEBoundaryError(code,message)


def _json(d:dict)->str:
    return json.dumps(d,ensure_ascii=False,sort_keys=True,
                      separators=(',',':'),allow_nan=False)


@dataclass(frozen=True,slots=True)
class RegistryQuery:
    project_id:str
    principal_id:str
    hypothesis_sha256:str|None=None
    b_feature_name:str|None=None
    platform:str|None=None
    status:str|None=None
    min_matched_pairs:int=1
    min_supported_bins:int=0
    page_size:int=20
    cursor:str|None=None
    view:str='latest_only'
    redaction:str='remove_source_rows_video_ids_and_source_fingerprints'

    def __post_init__(self)->None:
        if type(self.project_id) is not str or not re.fullmatch(r'[0-9a-f]{32}',self.project_id):
            _fail('invalid_registry_query','Exact project ID required')
        if type(self.principal_id) is not str or not _ACTOR.fullmatch(self.principal_id):
            _fail('invalid_registry_query','Explicit bounded principal ID required')
        if self.hypothesis_sha256 is not None and (
            type(self.hypothesis_sha256) is not str or not _SHA.fullmatch(self.hypothesis_sha256)):
            _fail('invalid_registry_query','Invalid hypothesis fingerprint filter')
        if self.b_feature_name is not None and (
            type(self.b_feature_name) is not str or not _FEATURE.fullmatch(self.b_feature_name)):
            _fail('invalid_registry_query','B feature requires exact source feature name')
        if self.platform is not None and (
            type(self.platform) is not str or not _PLATFORM.fullmatch(self.platform)):
            _fail('invalid_registry_query','Exact platform key required')
        if self.status is not None and (type(self.status) is not str or self.status not in _ALLOWED):
            _fail('invalid_registry_query','Candidate-only status filter')
        for name,low,high in (('min_matched_pairs',1,100000),
                              ('min_supported_bins',0,16),
                              ('page_size',1,_MAX_ROWS_PER_PAGE)):
            val=getattr(self,name)
            if type(val) is not int or not low<=val<=high:
                _fail('invalid_registry_query','Bounded unweighted count and page size filters required')
        if self.cursor is not None and (
            type(self.cursor) is not str or not 1<=len(self.cursor)<=_MAX_CURSOR_BYTES):
            _fail('invalid_registry_cursor','Opaque bounded cursor expected')
        if (self.view!='latest_only' or
            self.redaction!='remove_source_rows_video_ids_and_source_fingerprints'):
            _fail('invalid_registry_query','Cannot query old revision fallback or unredacted view')

    def fingerprint(self)->str:
        # Cursor scope binds all filters, project, owner and safe view; cursor excluded.
        d={key:getattr(self,key) for key in self.__dataclass_fields__ if key!='cursor'}
        return canonical_hash(d)


@dataclass(frozen=True,slots=True)
class RedactedPattern:
    series_id:str
    hypothesis_sha256:str
    revision:int
    factor_id:str
    feature_family:str
    feature_name:str
    metric_kind:str
    platform:str|None
    status:str
    matched_pair_count:int
    supported_bin_count:int
    descriptive_direction:str
    description:str
    validation_kind:str='unverified_descriptive_quality_gate'
    inferential_p_value:None=None
    causal_claim:bool=False
    publication_allowed:bool=False

    def as_dict(self)->dict:
        return {k:getattr(self,k) for k in self.__dataclass_fields__}


@dataclass(frozen=True,slots=True)
class PatternQueryPage:
    project_id:str
    items:tuple[RedactedPattern,...]
    next_cursor:str|None
    source_rights_revalidated:bool
    schema_version:str=SCHEMA

    def as_dict(self)->dict:
        return {'schema_version':self.schema_version,'project_id':self.project_id,
                'items':[x.as_dict() for x in self.items],
                'next_cursor':self.next_cursor,
                'source_rights_revalidated':self.source_rights_revalidated,
                'can_publish':False,'confirmed_discoveries':None}


class CurrentProjectAuthorizer(Protocol):
    """Trusted integration MUST check current authenticated actor membership."""
    def require_current_project_access(self,project_id:str,principal_id:str)->bool:...


class FreshUAEReader(Protocol):
    """Live adapter must re-read the exact frozen source, not return a cached authorization."""
    def read_fresh_qualified(self,project_id:str,manifest_sha256:str)->UAEReadResult:...


class CursorSigner:
    """Server-side 32+ byte secret binds short-lived keyset cursors to query scope."""
    def __init__(self,key:bytes):
        if type(key) is not bytes or len(key)<32:
            _fail('invalid_registry_cursor_key','Unpredictable server key of >=32 bytes required')
        self._key=key

    def issue(self,scope:str,watermark:str,last_series_id:str)->str:
        if type(scope) is not str or not _SHA.fullmatch(scope):
            _fail('invalid_registry_cursor','Scope digest required')
        if type(last_series_id) is not str or not _SHA.fullmatch(last_series_id):
            _fail('invalid_registry_cursor','Last series digest required')
        raw=_json({'v':1,'scope':scope,'watermark':watermark,'last_series':last_series_id}).encode()
        mac=hmac.new(self._key,raw,sha256).digest()
        return base64.urlsafe_b64encode(raw+mac).decode().rstrip('=')

    def parse(self,token:str,scope:str,now:datetime)->tuple[datetime,str]:
        if type(token) is not str or not 1<=len(token)<=_MAX_CURSOR_BYTES:
            _fail('invalid_registry_cursor','Malformed/oversized cursor')
        try:
            body=base64.b64decode(token+'='*((-len(token))%4),altchars=b'-_',validate=True)
        except (ValueError,TypeError) as exc:
            raise PIEBoundaryError('invalid_registry_cursor','Cannot decode cursor') from exc
        if len(body)<34:
            _fail('invalid_registry_cursor','Short cursor')
        raw,mac=body[:-32],body[-32:]
        if not hmac.compare_digest(hmac.new(self._key,raw,sha256).digest(),mac):
            _fail('registry_cursor_tampered','Cursor signature invalid')
        try:
            d=json.loads(raw)
        except (TypeError,ValueError,UnicodeError) as exc:
            raise PIEBoundaryError('invalid_registry_cursor','Invalid cursor payload') from exc
        if (type(d) is not dict or set(d)!= {'v','scope','watermark','last_series'}
            or d['v']!=1 or d['scope']!=scope
            or type(d['last_series']) is not str or not _SHA.fullmatch(d['last_series'])):
            _fail('registry_cursor_scope_mismatch','Cursor belongs to different query scope')
        try:
            watermark=datetime.fromisoformat(d['watermark'])
        except (ValueError,TypeError) as exc:
            raise PIEBoundaryError('invalid_registry_cursor','Missing timestamp') from exc
        if (watermark.tzinfo is None or watermark.utcoffset() is None or
            watermark>now or now-watermark>timedelta(seconds=_CURSOR_TTL_SECONDS)):
            _fail('registry_cursor_expired','Cursor window expired or clock invalid')
        return watermark,d['last_series']


def _redacted(payload:dict,revision:int,platform:str|None)->RedactedPattern:
    if (type(payload) is not dict or payload.get('schema_version')!=_RECORD_SCHEMA
        or payload.get('approved_live') is not False
        or payload.get('may_publish') is not False
        or payload.get('can_release_to_registry_as_supported') is not False
        or payload.get('analysis_revision_status') not in _ALLOWED):
        _fail('registry_untrusted_row','Invalid or elevated candidate row')
    hypothesis=payload.get('hypothesis_specification')
    stats=payload.get('statistical_summary')
    if type(hypothesis) is not dict or type(stats) is not dict:
        _fail('registry_untrusted_row','Missing source hypothesis or diagnostics')
    if not (type(payload.get('source_discovery_pairs')) is list
            and type(stats.get('bin_coverage')) is list
            and type(payload.get('matched_pair_count')) is int
            and payload['matched_pair_count']>=1):
        _fail('registry_untrusted_row','Unexpected original matched study shape')
    if len(payload['source_discovery_pairs'])!=payload['matched_pair_count']:
        _fail('registry_untrusted_row','Incomplete original matched sample')
    metric=hypothesis.get('metric_kind')
    name=hypothesis.get('feature_name')
    family=hypothesis.get('feature_family')
    if (metric not in ('duration','speech_wpm','speech_coverage',
                      'cuts_per_minute','first_5_seconds_cuts')
        or type(name) is not str or not _FEATURE.fullmatch(name)
        or family not in ('a','b')):
        _fail('registry_untrusted_row','Unexpected source feature name/type')
    supported=sum(x.get('supported') is True for x in stats['bin_coverage']
                  if type(x) is dict)
    state=payload['analysis_revision_status']
    explanation=(
        'Exploratory matched-pair association, not a verified causal effect.'
        if state=='exploratory'
        else 'Fragile candidate excluded by preregistered robustness checks.'
        if state=='rejected'
        else 'Candidate requires review or more eligible observations.'
    )
    return RedactedPattern(
        series_id=payload['series_id'],hypothesis_sha256=payload['hypothesis_sha256'],
        revision=revision,factor_id=payload['factor_id'],
        feature_family=family,feature_name=name,metric_kind=metric,platform=platform,
        status=state,matched_pair_count=payload['matched_pair_count'],
        supported_bin_count=supported,
        descriptive_direction=stats.get('monotonicity') or 'unsupported',
        description=explanation,
    )


def _rights_revalidate(payload:dict,reader:FreshUAEReader,
                       gate:SourceEligibilityGate,project_id:str)->bool:
    """Refresh authenticated scope/ALL row rights and frozen matched source IDs."""
    if (reader is None or not callable(getattr(reader,'read_fresh_qualified',None))
        or type(gate) is not SourceEligibilityGate):
        _fail('registry_rights_unavailable','Live UAE reader and provenance gate required')
    try:
        result=reader.read_fresh_qualified(project_id,payload['dataset_manifest_sha256'])
    except Exception:
        return False
    if not isinstance(result,UAEReadResult):
        return False
    dataset=result.dataset
    if (dataset.pointer.project_id!=project_id or
        dataset.manifest_sha256!=payload['dataset_manifest_sha256'] or
        dataset.pointer.uae_manifest_sha256!=payload['dataset_manifest_sha256']):
        return False
    try:
        report=gate.evaluate(result)  # Recheck current project scope + ALL row policy.
    except Exception:
        return False
    if (not report.candidate_eligible or report.suppressed_rows!=0
        or report.structurally_eligible_rows!=report.total_rows
        or report.manifest_sha256!=payload['dataset_manifest_sha256']):
        return False
    fresh={}
    for obs in dataset.observations:
        if obs.video_id in fresh:return False
        fresh[obs.video_id]=(obs.row_sha256,obs.uae_snapshot_id)
    pairs=payload['source_discovery_pairs']
    if type(pairs) is not list or len(pairs)!=payload['matched_pair_count']:
        return False
    for pair in pairs:
        if (type(pair) is not dict or
            fresh.get(pair.get('high_video_id'))!=
                (pair.get('high_row_sha256'),pair.get('high_uae_snapshot_id'))
            or fresh.get(pair.get('low_video_id'))!=
                (pair.get('low_row_sha256'),pair.get('low_uae_snapshot_id'))):
            return False
    return True


def _platform_from_videos(connection,pairs:list,requested:str|None)->str|None|bool:
    # All original matched publications must have valid, visible video platform.
    ids=set()
    for pair in pairs:
        if type(pair) is not dict:
            return False
        for k in ('high_video_id','low_video_id'):
            v=pair.get(k)
            if type(v) is not int or not 1<=v<2**63:return False
            ids.add(v)
    if len(ids)!=len(pairs)*2:
        return False
    found=connection.execute(
        'SELECT id,platform FROM videos WHERE id=ANY(%s)',(sorted(ids),)).fetchall()
    if len(found)!=len(ids):
        return False
    platforms={x[1] for x in found}
    if len({x[0] for x in found})!=len(ids) or len(platforms)!=1:
        # Do not pool mixed platforms for a single comparable metric trend.
        return False
    observed=next(iter(platforms))
    if type(observed) is not str or not _PLATFORM.fullmatch(observed):
        return False
    if requested is not None and observed!=requested:return False
    return observed


def query_pattern_registry(connection,query:RegistryQuery,authorizer:CurrentProjectAuthorizer,
                           reader:FreshUAEReader,gate:SourceEligibilityGate,
                           signer:CursorSigner,*,now:datetime|None=None)->PatternQueryPage:
    """One rights-checked, redacted, keyset-paginated *latest revision* page."""
    if type(query) is not RegistryQuery or type(signer) is not CursorSigner:
        _fail('invalid_registry_query','Exact query and server cursor signer required')
    if (authorizer is None or
        not callable(getattr(authorizer,'require_current_project_access',None))):
        _fail('registry_project_access_unavailable','Independent access authority required')
    # Mandatory authorization BEFORE SQL; do not reveal project existence on deny.
    try:
        authorized=authorizer.require_current_project_access(query.project_id,query.principal_id)
    except Exception:
        authorized=False
    if authorized is not True:
        _fail('registry_project_access_denied','Current owner/project read not authorized')
    if (reader is None or not callable(getattr(reader,'read_fresh_qualified',None))
        or type(gate) is not SourceEligibilityGate):
        _fail('registry_rights_unavailable','Fresh UAE and row-provenance authority unavailable')
    now=now if now is not None else datetime.now(timezone.utc)
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        _fail('invalid_registry_clock','Timezone-aware evaluation clock required')
    scope=query.fingerprint()
    if query.cursor is None:
        # DB clock establishes snapshot watermark, newest registry writes
        # cannot reappear in the middle of a paginated session.
        watermark=connection.execute('SELECT statement_timestamp()').fetchone()[0]
        if not isinstance(watermark,datetime) or watermark.tzinfo is None:
            _fail('invalid_registry_clock','Postgres timezone-aware clock expected')
        if watermark>now+timedelta(seconds=5):
            _fail('invalid_registry_clock','Database clock unreasonable')
        last='0'*64
    else:
        watermark,last=signer.parse(query.cursor,scope,now)
    # Latest within frozen page watermark per series, THEN filter status.
    # Owner predicate is mandatory to avoid broad project member disclosure.
    rows=connection.execute(
        """WITH latest AS (
             SELECT DISTINCT ON (r.series_id)
                    r.series_id,r.revision,r.status,r.hypothesis_sha256,
                    r.evidence_sha256,r.payload_sha256,r.source_snapshot_sha256,
                    r.source_quality_sha256,r.source_rows_sha256,r.payload
             FROM pie_pattern_registry_revisions AS r
             JOIN pie_pattern_registry_series AS s
                ON s.project_id=r.project_id AND s.series_id=r.series_id
            WHERE r.project_id=%s AND s.project_id=%s
              AND s.owner_id=%s AND r.created_at<=%s
            ORDER BY r.series_id,r.revision DESC
         )
         SELECT series_id,revision,status,hypothesis_sha256,evidence_sha256,
                payload_sha256,source_snapshot_sha256,source_quality_sha256,
                source_rows_sha256,payload
         FROM latest
         WHERE series_id>%s
           AND (%s::text IS NULL OR hypothesis_sha256=%s)
           AND (%s::text IS NULL OR status=%s)
           AND (%s::text IS NULL OR
                (payload#>>'{hypothesis_specification,feature_family}')='b'
                AND (payload#>>'{hypothesis_specification,feature_name}')=%s)
           AND (payload->>'matched_pair_count')::integer >= %s
           AND (SELECT count(*) FROM
              jsonb_array_elements(payload#>'{statistical_summary,bin_coverage}') AS bin
              WHERE bin->>'supported'='true') >= %s
         ORDER BY series_id ASC LIMIT %s""",
        (query.project_id,query.project_id,query.principal_id,watermark,
         last,query.hypothesis_sha256,query.hypothesis_sha256,
         query.status,query.status,query.b_feature_name,query.b_feature_name,
         query.min_matched_pairs,query.min_supported_bins,query.page_size+1)).fetchall()
    if len(rows)>query.page_size:
        to_read=rows[:query.page_size]
        has_more=True
    else:
        to_read=rows
        has_more=False
    safe=[]
    for row in to_read:
        (series_id,revision,status,hypothesis_sha,evidence_sha,payload_sha,
         snapshot_sha,quality_sha,source_rows_sha,payload)=row
        if (type(payload) is not dict or type(series_id) is not str
            or type(revision) is not int or not 1<=revision<2**31):
            _fail('registry_untrusted_row','Invalid stored revision')
        if (payload.get('project_id')!=query.project_id
            or payload.get('owner_id')!=query.principal_id
            or payload.get('series_id')!=series_id
            or payload.get('hypothesis_sha256')!=hypothesis_sha
            or payload.get('analysis_revision_status')!=status
            or payload.get('evidence_sha256')!=evidence_sha
            or payload.get('source_rows_sha256')!=source_rows_sha
            or payload.get('source_pattern_quality_sha256')!=quality_sha
            or payload.get('dataset_snapshot_sha256')!=snapshot_sha
            or canonical_hash(payload)!=payload_sha):
            _fail('registry_untrusted_row','Stored source fingerprint or tenant mismatch')
        # No source ID, video ID or metric enters the response on denied rights.
        if not _rights_revalidate(payload,reader,gate,query.project_id):
            continue
        platform=_platform_from_videos(connection,payload['source_discovery_pairs'],query.platform)
        if platform is False:continue
        safe.append(_redacted(payload,revision,platform))
    next_cursor=(signer.issue(scope,watermark.isoformat(),to_read[-1][0])
                 if has_more and to_read else None)
    return PatternQueryPage(query.project_id,tuple(safe),next_cursor,True)


def export_redacted_pattern_page(page:PatternQueryPage,format:str='json')->str:
    """Only safe view objects; never serialize full provenance or raw JSONB."""
    if type(page) is not PatternQueryPage:
        _fail('invalid_registry_export','A rights-rechecked safe page required')
    if page.source_rights_revalidated is not True:
        _fail('invalid_registry_export','No unrestricted raw export')
    if format=='json':
        return _json(page.as_dict())
    if format!='csv':
        _fail('invalid_registry_export','Supported export formats are json and csv')
    columns=tuple(RedactedPattern.__dataclass_fields__)
    buf=io.StringIO()
    writer=csv.writer(buf,lineterminator='\n')
    writer.writerow(columns)
    for entry in page.items:
        writer.writerow([getattr(entry,key) if getattr(entry,key) is not None else ''
                         for key in columns])
    return buf.getvalue()
