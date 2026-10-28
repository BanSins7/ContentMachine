"""PIE-MOD-25: append-only candidate registry from replayed stage24 quality evidence.

A registry write records *unverified exploratory/rejected/needs_review* claims.
No record in this version can be supported, rights-approved or publishable.
PostgreSQL migration 201 creates project-scoped immutable revision history.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import re

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import CandidateResearchSnapshot
from .strata import CandidateStrata
from .outcomes import CandidateOutcomePlan
from .controls import CandidateMatchedControls
from .feature_matrix import CandidateFeatureMatrix
from .holdout import CandidateHoldoutSplit, HoldoutPolicy, HoldoutMediaLineage
from .technical_numeric import TechnicalNumericPolicy, CandidateTechnicalNumericReport
from .robustness import RobustnessPolicy, CandidateRobustnessReport
from .pattern_quality import (PatternQualityPolicy, CandidatePatternQualityReport,
                              replay_candidate_pattern_quality_report)

SCHEMA='pie-immutable-pattern-registry-batch-candidate/v1'
_RECORD_SCHEMA='pie-immutable-pattern-registry-entry-candidate/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_OWNER=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._:@/-]{0,159}\Z')
_MAX_ENTRIES=16
_MAX_JSON_BYTES=32*1024*1024
_ALLOWED=('exploratory','rejected','needs_review')


def _fail(code:str,detail:str)->None:
    raise PIEBoundaryError(code,detail)


def _encode(value:dict)->str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,
                      separators=(',',':'),allow_nan=False)


def _sealed(value:dict)->tuple[str,str]:
    raw=_encode(value)
    if len(raw.encode('utf-8'))>_MAX_JSON_BYTES:
        _fail('payload_too_large','Candidate registry evidence exceeds byte cap')
    return raw,sha256(raw.encode('utf-8')).hexdigest()


@dataclass(frozen=True,slots=True)
class RegistryOwner:
    owner_id:str
    actor_origin:str='unverified_local_candidate_owner'

    def __post_init__(self)->None:
        if type(self.owner_id) is not str or not _OWNER.fullmatch(self.owner_id):
            _fail('invalid_registry_owner','Explicit bounded owner ID required')
        if self.actor_origin!='unverified_local_candidate_owner':
            _fail('invalid_registry_owner','Cannot claim reviewer/authorization identity')


@dataclass(frozen=True,slots=True)
class CandidateRegistryEntry:
    project_id:str
    series_id:str
    factor_id:str
    hypothesis_sha256:str
    evidence_sha256:str
    payload_sha256:str
    snapshot_sha256:str
    quality_sha256:str
    stratum_sha256:str
    source_rows_sha256:str
    validation_id:str
    owner_id:str
    decision:str
    _json:str=field(repr=False)

    @property
    def verified_live(self)->bool:return False

    def to_json(self)->str:return self._json

    def as_dict(self)->dict:return json.loads(self._json)


@dataclass(frozen=True,slots=True)
class CandidateRegistryBatch:
    artifact_sha256:str
    project_id:str
    source_quality_sha256:str
    record_count:int
    entries:tuple[CandidateRegistryEntry,...]
    _json:str=field(repr=False)

    @property
    def verified_live(self)->bool:return False

    @property
    def ready_for_analysis(self)->bool:return False

    def to_json(self)->str:return self._json

    def as_dict(self)->dict:return json.loads(self._json)


@dataclass(frozen=True,slots=True)
class RegistryWriteReceipt:
    project_id:str
    series_id:str
    revision:int
    revision_id:str
    evidence_sha256:str
    payload_sha256:str
    stored_status:str
    idempotent:bool


def _compose(snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
             outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
             matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
             holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
             numeric_policy:TechnicalNumericPolicy,
             numeric_report:CandidateTechnicalNumericReport,
             robustness_policy:RobustnessPolicy,
             robustness_report:CandidateRobustnessReport,
             quality_policy:PatternQualityPolicy,
             quality_report:CandidatePatternQualityReport,
             owner:RegistryOwner)->CandidateRegistryBatch:
    if not (type(snapshot) is CandidateResearchSnapshot and type(strata) is CandidateStrata
            and type(outcome) is CandidateOutcomePlan and type(controls) is CandidateMatchedControls
            and type(matrix) is CandidateFeatureMatrix and type(split) is CandidateHoldoutSplit
            and type(holdout_policy) is HoldoutPolicy
            and type(numeric_policy) is TechnicalNumericPolicy
            and type(numeric_report) is CandidateTechnicalNumericReport
            and type(robustness_policy) is RobustnessPolicy
            and type(robustness_report) is CandidateRobustnessReport
            and type(quality_policy) is PatternQualityPolicy
            and type(quality_report) is CandidatePatternQualityReport
            and type(owner) is RegistryOwner):
        _fail('invalid_registry_input','Exact typed source chain and owner required')
    rebuilt=replay_candidate_pattern_quality_report(
        quality_report.to_json(),quality_report.artifact_sha256,
        snapshot,strata,outcome,controls,matrix,split,holdout_policy,lineage,
        numeric_policy,numeric_report,robustness_policy,robustness_report)
    if rebuilt!=quality_report:
        _fail('registry_source_replay_mismatch','Frozen quality wrapper altered')
    q=quality_report.as_dict()
    n=numeric_report.as_dict()
    r=robustness_report.as_dict()
    source=snapshot.as_dict()
    c=controls.as_dict()
    if (quality_report.project_id!=snapshot.project_id
        or quality_report.policy_sha256!=quality_policy.policy_sha256
        or q.get('policy_sha256')!=quality_policy.policy_sha256
        or q.get('source_snapshot_sha256')!=snapshot.snapshot_sha256
        or q.get('source_controls_sha256')!=controls.artifact_sha256
        or q.get('source_matrix_sha256')!=matrix.artifact_sha256
        or q.get('source_holdout_sha256')!=split.artifact_sha256
        or q.get('source_technical_numeric_sha256')!=numeric_report.artifact_sha256
        or q.get('source_robustness_sha256')!=robustness_report.artifact_sha256
        or q.get('approved_live') is not False
        or q.get('can_publish_patterns') is not False
        or q.get('can_release_to_registry_as_supported') is not False
        or q.get('supported_state_reachable_with_current_source') is not False
        or q.get('decision_counts',{}).get('supported')!=0
        or quality_policy.snapshot_sha256!=snapshot.snapshot_sha256
        or numeric_report.project_id!=snapshot.project_id
        or robustness_report.project_id!=snapshot.project_id
        or numeric_report.policy_sha256!=numeric_policy.policy_sha256
        or robustness_report.policy_sha256!=robustness_policy.policy_sha256
        or n.get('registered_factor_family_sha256')!=numeric_policy.family_sha256
        or n.get('policy_sha256')!=numeric_policy.policy_sha256
        or r.get('policy_sha256')!=robustness_policy.policy_sha256
        or r.get('source_technical_numeric_sha256')!=numeric_report.artifact_sha256
        or q.get('registered_factor_count')!=len(numeric_policy.factors)
        or q.get('can_publish_patterns') is not False
        or q.get('authentic_reviewer_approval') is not False
        or q.get('source_rights_authenticated') is not False
        or q.get('independent_holdout_confirmed') is not False):
        _fail('registry_lineage_or_release_mismatch','No fake rights/release from source reports')
    factor_ids=tuple(x.factor_id for x in numeric_policy.factors)
    if (len(factor_ids)==0 or len(factor_ids)>_MAX_ENTRIES
        or len(q.get('decisions',[]))!=len(factor_ids)
        or len(n.get('factors',[]))!=len(factor_ids)
        or len(r.get('factor_sensitivities',[]))!=len(factor_ids)
        or tuple(x['factor_id'] for x in q['decisions'])!=factor_ids
        or tuple(x['factor_id'] for x in n['factors'])!=factor_ids
        or tuple(x['factor_id'] for x in r['factor_sensitivities'])!=factor_ids):
        _fail('registry_family_mismatch','Complete immutable stage22 factor family required')
    members={v['video_id']:v for v in source['selection']['included']}
    discovery=set(split.discovery_video_ids)
    pairs=[p for p in c['pairs'] if p['case_video_id'] in discovery]
    if (any((p['control_video_id'] in discovery)!=(p['case_video_id'] in discovery)
            for p in c['pairs'])
        or len(pairs)!=n.get('discovery_pair_count')
        or len(pairs)!=r.get('discovery_pair_count')
        or len(pairs)!=len({p['case_video_id'] for p in pairs})
        or len(pairs)!=len({p['control_video_id'] for p in pairs})):
        _fail('registry_population_mismatch','Original discovery matched pairs and source must align')
    if len(pairs)!=len({p['control_video_id'] for p in pairs}):
        _fail('registry_population_mismatch','Matched source control ID reused')
    pair_rows=[]
    for p in pairs:
        high,low=p['case_video_id'],p['control_video_id']
        if high not in members or low not in members:
            _fail('registry_population_mismatch','Source video missing from snapshot')
        pair_rows.append({
            'high_video_id':high,'low_video_id':low,
            'high_row_sha256':members[high]['row_sha256'],
            'low_row_sha256':members[low]['row_sha256'],
            'high_uae_snapshot_id':members[high]['uae_snapshot_id'],
            'low_uae_snapshot_id':members[low]['uae_snapshot_id'],
        })
    source_rows_sha=canonical_hash({'pairs':pair_rows})
    source_stratum_sha=c.get('stratum_sha256')
    if type(source_stratum_sha) is not str or not _SHA.fullmatch(source_stratum_sha):
        _fail('registry_stratum_mismatch','Exact stage10 matched stratum required')
    if n.get('source_stratum_sha256')!=source_stratum_sha:
        _fail('registry_stratum_mismatch','Numeric stratum cannot differ from matched controls')
    entries=[]
    for spec,stat,audit,decision in zip(
            numeric_policy.factors,n['factors'],r['factor_sensitivities'],q['decisions']):
        if decision.get('decision') not in _ALLOWED or decision.get('eligible_for_release') is not False:
            _fail('registry_supported_forbidden','No supported or released candidate in registry')
        hypothesis={
            'kind':'frozen_technical_numeric_bin_association',
            'factor_id':spec.factor_id,
            'metric_kind':spec.metric_kind,'feature_family':spec.family,
            'feature_name':spec.feature_name,'source_unit':spec.source_unit,
            'representation':'numeric_int',
            'operator':'frozen_half_open_raw_numeric_bins',
            'registered_cutoffs_raw':list(spec.bin_cutoffs_raw),
            'value_scale':spec.value_scale,
            'contrast':'matched_c7_high_minus_low_unweighted',
            'source_origin_claim':spec.source_origin_claim,
        }
        hypothesis_sha=canonical_hash(hypothesis)
        series_id=canonical_hash({'project_id':snapshot.project_id,
                                  'owner_id':owner.owner_id,
                                  'hypothesis_sha256':hypothesis_sha})
        evidence={
            'snapshot_sha256':snapshot.snapshot_sha256,
            'quality_report_sha256':quality_report.artifact_sha256,
            'numeric_report_sha256':numeric_report.artifact_sha256,
            'robustness_report_sha256':robustness_report.artifact_sha256,
            'holdout_sha256':split.artifact_sha256,
            'matched_controls_sha256':controls.artifact_sha256,
            'source_rows_sha256':source_rows_sha,
            'stratum_sha256':source_stratum_sha,
            'source_quality_policy_sha256':quality_policy.policy_sha256,
            'source_numeric_policy_sha256':numeric_policy.policy_sha256,
            'source_robustness_policy_sha256':robustness_policy.policy_sha256,
            'hypothesis_sha256':hypothesis_sha,
        }
        evidence_sha=canonical_hash(evidence)
        payload={
            'schema_version':_RECORD_SCHEMA,
            'evidence_state':'unverified',
            'approved_live':False,'may_publish':False,
            'project_id':snapshot.project_id,
            'candidate_id':series_id,'series_id':series_id,
            'hypothesis_sha256':hypothesis_sha,'factor_id':spec.factor_id,
            'owner_id':owner.owner_id,'owner_origin':owner.actor_origin,
            'hypothesis_specification':hypothesis,
            'study_design_sha256':quality_policy.design_sha256,
            'dataset_snapshot_sha256':snapshot.snapshot_sha256,
            'dataset_manifest_sha256':source['source']['pointer']['uae_manifest_sha256']
                if 'source' in source and 'pointer' in source['source'] else None,
            'stratum_sha256':source_stratum_sha,
            'source_discovery_pairs':pair_rows,
            'source_rows_sha256':source_rows_sha,
            'matched_pair_count':len(pairs),
            'sample_scope':'discovery_only',
            'source_holdout_sha256':split.artifact_sha256,
            'source_technical_numeric_sha256':numeric_report.artifact_sha256,
            'source_robustness_sha256':robustness_report.artifact_sha256,
            'source_pattern_quality_sha256':quality_report.artifact_sha256,
            'validation_id':quality_report.artifact_sha256,
            'analysis_revision_status':decision['decision'],
            'decision_reason_code':decision['transition']['reason_code'],
            'statistical_summary':{
                'raw_mean_high_minus_low':stat['matched_mean_raw_high_minus_low'],
                'scaled_mean_high_minus_low':stat['matched_mean_scaled_high_minus_low'],
                'bin_coverage':stat['bins'],
                'monotonicity':stat['monotonicity'],
                'stage23_robustness_status':audit['status'],
                'missing_observations':stat['missing_observations'],
                'calibrated_p_value':None,'adjusted_q_value':None,
                'confidence_interval':None,
            },
            'source_quality_dimensions':decision['quality_dimensions'],
            'evidence_sha256':evidence_sha,
            'source_evidence':evidence,
            'can_release_to_registry_as_supported':False,
            'independent_holdout_confirmed':False,
            'rights_and_review_authenticated':False,
        }
        raw,payload_sha=_sealed(payload)
        entries.append(CandidateRegistryEntry(
            snapshot.project_id,series_id,spec.factor_id,hypothesis_sha,
            evidence_sha,payload_sha,snapshot.snapshot_sha256,
            quality_report.artifact_sha256,source_stratum_sha,source_rows_sha,
            quality_report.artifact_sha256,owner.owner_id,decision['decision'],raw))
    batch_data={
        'schema_version':SCHEMA,'project_id':snapshot.project_id,
        'source_quality_sha256':quality_report.artifact_sha256,
        'owner_id':owner.owner_id,'record_count':len(entries),
        'entries':[entry.as_dict() for entry in entries],
        'approved_live':False,'can_publish':False,
        'supported_registry_entry_count':0,
    }
    canonical,batch_sha=_sealed(batch_data)
    return CandidateRegistryBatch(batch_sha,snapshot.project_id,
                                   quality_report.artifact_sha256,len(entries),
                                   tuple(entries),canonical)


def build_candidate_pattern_registry_batch(
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
        numeric_policy:TechnicalNumericPolicy,numeric_report:CandidateTechnicalNumericReport,
        robustness_policy:RobustnessPolicy,robustness_report:CandidateRobustnessReport,
        quality_policy:PatternQualityPolicy,quality_report:CandidatePatternQualityReport,
        owner:RegistryOwner)->CandidateRegistryBatch:
    return _compose(snapshot,strata,outcome,controls,matrix,split,holdout_policy,
                    lineage,numeric_policy,numeric_report,robustness_policy,
                    robustness_report,quality_policy,quality_report,owner)


def replay_candidate_pattern_registry_batch(raw:str|bytes,expected_sha256:str,
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
        numeric_policy:TechnicalNumericPolicy,numeric_report:CandidateTechnicalNumericReport,
        robustness_policy:RobustnessPolicy,robustness_report:CandidateRobustnessReport,
        quality_policy:PatternQualityPolicy,quality_report:CandidatePatternQualityReport,
        owner:RegistryOwner)->CandidateRegistryBatch:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_registry_artifact','Valid digest and canonical JSON required')
    try:
        encoded=raw if type(raw) is bytes else raw.encode('utf-8')
    except UnicodeError as exc:
        raise PIEBoundaryError('invalid_registry_artifact','Invalid UTF-8') from exc
    if len(encoded)>_MAX_JSON_BYTES:
        _fail('payload_too_large','Registry batch exceeds byte cap')
    def unique(items):
        val={}
        for k,v in items:
            if k in val:_fail('duplicate_json_key','Repeated JSON property')
            val[k]=v
        return val
    try:
        d=json.loads(encoded,object_pairs_hook=unique,
                     parse_constant=lambda _:_fail('invalid_registry_artifact','Nonfinite JSON'))
    except PIEBoundaryError:raise
    except (TypeError,UnicodeError,ValueError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_registry_artifact','Malformed registry JSON') from exc
    if type(d) is not dict or canonical_hash(d)!=expected_sha256:
        _fail('registry_hash_mismatch','Batch content digest mismatch')
    if (d.get('schema_version')!=SCHEMA or d.get('approved_live') is not False
        or d.get('can_publish') is not False
        or d.get('supported_registry_entry_count')!=0):
        _fail('unsupported_registry_artifact','Candidate-only schema and no-release flags required')
    expected=build_candidate_pattern_registry_batch(
        snapshot,strata,outcome,controls,matrix,split,holdout_policy,lineage,
        numeric_policy,numeric_report,robustness_policy,robustness_report,
        quality_policy,quality_report,owner)
    if (expected.artifact_sha256!=expected_sha256
        or expected.to_json().encode('utf-8')!=encoded or expected.as_dict()!=d):
        _fail('registry_replay_mismatch','Rehashed forgery or changed upstream input')
    return expected


def persist_candidate_pattern_registry(
        connection,
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
        numeric_policy:TechnicalNumericPolicy,numeric_report:CandidateTechnicalNumericReport,
        robustness_policy:RobustnessPolicy,robustness_report:CandidateRobustnessReport,
        quality_policy:PatternQualityPolicy,quality_report:CandidatePatternQualityReport,
        owner:RegistryOwner)->tuple[RegistryWriteReceipt,...]:
    """Replay the entire source tree then append idempotent project-scoped revisions.

    A per-series PostgreSQL transaction advisory lock + composite uniqueness
    serializes concurrent insertions; never UPDATE an old row or trust a
    client-supplied revision number. Caller must provide a writable connection.
    """
    batch=build_candidate_pattern_registry_batch(
        snapshot,strata,outcome,controls,matrix,split,holdout_policy,lineage,
        numeric_policy,numeric_report,robustness_policy,robustness_report,
        quality_policy,quality_report,owner)
    from psycopg.types.json import Jsonb
    receipts=[]
    with connection.transaction():
        for entry in batch.entries:
            value=entry.as_dict()
            if (value.get('analysis_revision_status') not in _ALLOWED
                or value.get('approved_live') is not False
                or value.get('may_publish') is not False
                or value.get('evidence_sha256')!=entry.evidence_sha256
                or value.get('candidate_id')!=entry.series_id
                or value.get('source_pattern_quality_sha256')!=entry.quality_sha256
                or value.get('validation_id')!=entry.validation_id
                or value.get('hypothesis_sha256')!=entry.hypothesis_sha256
                or canonical_hash(value)!=entry.payload_sha256):
                _fail('registry_write_invalid_artifact','Sealed candidate metadata changed')
            # Transaction lifetime lock; subsequent concurrent calls cannot race revision N.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (entry.project_id+':'+entry.series_id,))
            connection.execute(
                """INSERT INTO pie_pattern_registry_series
                     (project_id,series_id,hypothesis_sha256,factor_id,owner_id)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT(project_id,series_id) DO NOTHING""",
                (entry.project_id,entry.series_id,entry.hypothesis_sha256,
                 entry.factor_id,entry.owner_id))
            actual=connection.execute(
                """SELECT hypothesis_sha256,factor_id,owner_id
                     FROM pie_pattern_registry_series
                    WHERE project_id=%s AND series_id=%s FOR UPDATE""",
                (entry.project_id,entry.series_id)).fetchone()
            if actual!=(entry.hypothesis_sha256,entry.factor_id,entry.owner_id):
                _fail('registry_series_identity_collision',
                      'Series cannot silently change hypothesis/factor/owner')
            prior=connection.execute(
                """SELECT revision,revision_id::text,payload_sha256,status,
                          payload,source_snapshot_sha256,source_quality_sha256,
                          stratum_sha256,source_rows_sha256,validation_id
                     FROM pie_pattern_registry_revisions
                    WHERE project_id=%s AND series_id=%s AND evidence_sha256=%s""",
                (entry.project_id,entry.series_id,entry.evidence_sha256)).fetchone()
            if prior is not None:
                revision,uid,digest,state,stored,*provenance=prior
                if (digest!=entry.payload_sha256 or state!=entry.decision
                    or stored!=value
                    or provenance!=[entry.snapshot_sha256,entry.quality_sha256,
                                   entry.stratum_sha256,entry.source_rows_sha256,
                                   entry.validation_id]):
                    _fail('registry_same_evidence_conflict',
                          'Identical evidence fingerprint cannot be replaced')
                receipts.append(RegistryWriteReceipt(entry.project_id,entry.series_id,
                        revision,uid,entry.evidence_sha256,digest,state,True))
                continue
            largest=connection.execute(
                """SELECT COALESCE(MAX(revision),0)
                     FROM pie_pattern_registry_revisions
                    WHERE project_id=%s AND series_id=%s""",
                (entry.project_id,entry.series_id)).fetchone()[0]
            if type(largest) is not int or largest>=2**31-1:
                _fail('registry_revision_overflow','Cannot roll over immutable revision')
            revision=largest+1
            uid=connection.execute(
                """INSERT INTO pie_pattern_registry_revisions
                     (project_id,series_id,hypothesis_sha256,revision,
                      evidence_sha256,payload_sha256,source_snapshot_sha256,
                      source_quality_sha256,stratum_sha256,source_rows_sha256,
                      validation_id,status,payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING revision_id::text""",
                (entry.project_id,entry.series_id,entry.hypothesis_sha256,revision,
                 entry.evidence_sha256,entry.payload_sha256,entry.snapshot_sha256,
                 entry.quality_sha256,entry.stratum_sha256,entry.source_rows_sha256,
                 entry.validation_id,entry.decision,Jsonb(value))).fetchone()[0]
            receipts.append(RegistryWriteReceipt(entry.project_id,entry.series_id,
                    revision,uid,entry.evidence_sha256,entry.payload_sha256,
                    entry.decision,False))
    return tuple(receipts)
