"""PIE-MOD-12: deterministic candidate-only missingness, sample and balance audit.

An absence or explicit null does NOT prove upstream 'not_run' or 'partial'. The
current UAE wire does not expose those execution states; both stay unknowable.
No imputation, inferred representativeness, production authorization or stats.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from hashlib import sha256
import json
import re

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import CandidateResearchSnapshot, replay_candidate_snapshot
from .strata import CandidateStrata, replay_candidate_strata
from .outcomes import CandidateOutcomePlan, replay_candidate_outcome_plan
from .controls import CandidateMatchedControls, replay_candidate_controls
from .feature_matrix import CandidateFeatureMatrix, replay_candidate_feature_matrix

SCHEMA = 'pie-missingness-coverage/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_RATE = re.compile(r'(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)\Z')
_MAX_BYTES = 128 * 1024 * 1024


def _fail(code: str, message: str) -> None:
    raise PIEBoundaryError(code, message)


def _rate(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        _fail('invalid_coverage_population', 'Coverage denominator must be positive')
    return str((Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal('0.000001'), rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True, slots=True)
class CoveragePolicy:
    policy_version: str
    design_sha256: str
    min_matched_rows: int
    min_rows_per_role: int
    min_present_per_role_per_feature: int
    max_missing_fraction: str
    min_cell_disclosure_n: int
    require_shared_creators: bool
    require_all_high_matched: bool

    def __post_init__(self) -> None:
        if type(self.policy_version) is not str or not _VERSION.fullmatch(self.policy_version):
            _fail('invalid_coverage_policy', 'Explicit coverage policy version required')
        if type(self.design_sha256) is not str or not _SHA.fullmatch(self.design_sha256):
            _fail('invalid_coverage_policy', 'Pinned research design hash required')
        for name in ('min_matched_rows', 'min_rows_per_role',
                     'min_present_per_role_per_feature', 'min_cell_disclosure_n'):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 50000:
                _fail('invalid_coverage_policy', f'{name} must be bounded positive integer')
        if self.min_matched_rows < self.min_rows_per_role * 2:
            _fail('invalid_coverage_policy', 'Global minimum cannot be below both role minima')
        if type(self.max_missing_fraction) is not str or not _RATE.fullmatch(self.max_missing_fraction):
            _fail('invalid_coverage_policy', 'Missing allowance must be decimal [0,1]')
        for name in ('require_shared_creators', 'require_all_high_matched'):
            if type(getattr(self, name)) is not bool:
                _fail('invalid_coverage_policy', 'Balance controls must be explicit booleans')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class CandidateCoverageAudit:
    artifact_sha256: str
    matrix_sha256: str
    controls_sha256: str
    policy_sha256: str
    project_id: str
    offline_coverage_status: str
    matched_n: int
    feature_count: int
    _json: str = field(repr=False)

    @property
    def verified_live(self) -> bool:
        return False

    @property
    def ready_for_analysis(self) -> bool:
        return False

    def to_json(self) -> str:
        return self._json

    def as_dict(self) -> dict:
        return json.loads(self._json)


def _validated(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
               outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
               matrix: CandidateFeatureMatrix) -> tuple[dict, dict, dict, dict, dict]:
    if (type(snapshot) is not CandidateResearchSnapshot or type(strata) is not CandidateStrata
        or type(outcome) is not CandidateOutcomePlan or type(controls) is not CandidateMatchedControls
        or type(matrix) is not CandidateFeatureMatrix):
        _fail('invalid_coverage_input', 'All five frozen candidate source types required')
    c = replay_candidate_snapshot(snapshot.to_json(), snapshot.snapshot_sha256).as_dict()
    s = replay_candidate_strata(strata.to_json(), strata.artifact_sha256, snapshot).as_dict()
    o = replay_candidate_outcome_plan(outcome.to_json(), outcome.artifact_sha256, snapshot, strata).as_dict()
    m = replay_candidate_controls(controls.to_json(), controls.artifact_sha256,
                                  snapshot, strata, outcome).as_dict()
    f = replay_candidate_feature_matrix(matrix.to_json(), matrix.artifact_sha256,
                                        snapshot, strata, outcome, controls).as_dict()
    if (any(x.get('evidence_state') != 'unverified' for x in (c,s,o,m,f))
        or f['approved_live'] is not False or m['approved_live'] is not False
        or snapshot.project_id != s['project_id'] or s['project_id'] != o['project_id']
        or o['project_id'] != m['project_id'] or m['project_id'] != f['project_id']
        or matrix.snapshot_sha256 != snapshot.snapshot_sha256
        or matrix.strata_sha256 != strata.artifact_sha256
        or matrix.outcome_sha256 != outcome.artifact_sha256
        or matrix.controls_sha256 != controls.artifact_sha256):
        _fail('coverage_scope_mismatch', 'Candidate provenance or approval state does not match')
    return c,s,o,m,f


def _display_counts(counts: dict, cutoff: int) -> dict:
    """Hide detailed subgroup cells below explicit N; this is NOT a privacy guarantee."""
    size = counts['n']
    if size < cutoff:
        return {'n': size, 'suppressed': True,
                'present_n': None, 'null_n': None, 'absent_n': None,
                'false_n': None, 'zero_n': None, 'empty_sequence_n': None,
                'present_fraction': None, 'missing_fraction': None}
    return {**counts, 'suppressed': False,
            'present_fraction': _rate(counts['present_n'],size),
            'missing_fraction': _rate(counts['absent_n']+counts['null_n'],size)}


def _compose(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
             outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
             matrix: CandidateFeatureMatrix, policy: CoveragePolicy) -> dict:
    c,s,o,m,f = _validated(snapshot,strata,outcome,controls,matrix)
    if f['study_design_sha256'] != policy.design_sha256 or o['policy']['design_sha256'] != policy.design_sha256:
        _fail('coverage_design_mismatch', 'Coverage QA must pin exact original research design')
    population=f['population']; rows=f['rows']; fields=f['columns']
    if (len(rows) != f['row_count'] or len(fields) != f['column_count']
        or f['row_count'] != matrix.row_count or f['column_count'] != matrix.column_count):
        _fail('coverage_population_mismatch', 'Matrix row/column declaration inconsistent')
    included=population['included_count']; scanned=population['scanned_count']
    matched=population['matched_count']; unmatched=population['unmatched_count']
    neutral=population['neutral_count']; other=population['other_strata_count']
    excluded=population['original_excluded']
    if (type(scanned) is not int or type(included) is not int or type(matched) is not int
        or included != matched+unmatched+neutral+other
        or scanned != included+len(excluded) or matched != len(rows)
        or matched != 2*controls.pair_count or included != len(snapshot.included_video_ids)):
        _fail('coverage_population_mismatch', 'All selected, unmatched and source exclusions must reconcile')
    if (len(set(r['video_id'] for r in rows)) != matched
        or set(r['video_id'] for r in rows) != set(controls.high_video_ids)|set(controls.low_video_ids)):
        _fail('coverage_population_mismatch', 'Matched sample lost, duplicated or replaced source IDs')
    roles = {'high':[r for r in rows if r['role']=='high'],
             'low':[r for r in rows if r['role']=='low']}
    if len(roles['high'])+len(roles['low']) != matched:
        _fail('coverage_population_mismatch', 'Unexpected or unassigned matched role')
    if len(roles['high']) != controls.pair_count or len(roles['low']) != controls.pair_count:
        _fail('coverage_population_mismatch', 'Paired role counts must match matched-pair count')
    dictionary={(e['family'],e['name']):e for e in c['dictionary']['entries']}
    source_rows={x['video_id']:x['row'] for x in c['selection']['included']}
    if len(source_rows)!=included:
        _fail('coverage_population_mismatch', 'Duplicate or incomplete full research source')
    blockers=[]; warnings=['unknown_missingness_mechanism','selection_bias_not_established',
                            'no_live_source_rights_or_design_authentication']
    if matched < policy.min_matched_rows:
        blockers.append('insufficient_matched_n')
    for role in ('high','low'):
        if len(roles[role]) < policy.min_rows_per_role:
            blockers.append('insufficient_'+role+'_n')
    if policy.require_all_high_matched and m['unmatched_cases']:
        blockers.append('unmatched_high_cases')
    creator_sets={}
    concentration={}
    for role in ('high','low'):
        counts=Counter(source_rows[row['video_id']]['creator_key'] for row in roles[role])
        creator_sets[role]=set(counts)
        concentration[role]={'n':len(roles[role]), 'unique_creators':len(counts),
                             'maximum_posts_per_creator': max(counts.values(),default=0)}
        if len(counts)<len(roles[role]):
            warnings.append('repeated_creator_'+role)
    common=len(creator_sets['high'] & creator_sets['low'])
    if policy.require_shared_creators and not common:
        blockers.append('no_shared_creators')
    if not common:
        warnings.append('no_shared_creators')
    if m['uncontrolled_dimensions']:
        warnings.append('uncontrolled_dimensions')
    audit_columns=[]
    for index,column in enumerate(fields):
        identity=(('features_'+column['family']),column['name'])
        entry=dictionary.get(identity)
        if entry is None:
            _fail('coverage_dictionary_mismatch', 'Feature column lacks source-wide dictionary entry')
        source_stats={'eligible_n':included,'present_n':entry['present_count']-entry['null_count'],
                      'null_n':entry['null_count'],'absent_n':entry['absent_count']}
        if any(type(n) is not int or n<0 for n in source_stats.values()) or (
            source_stats['present_n']+source_stats['null_n']+source_stats['absent_n'] != included):
            _fail('coverage_dictionary_mismatch', 'Source-wide dictionary counts do not reconcile')
        per_role={}
        for role in ('high','low'):
            group=roles[role]; counts={'n':len(group),'present_n':0,'null_n':0,
                'absent_n':0,'false_n':0,'zero_n':0,'empty_sequence_n':0}
            for row in group:
                cell=row['features'][index] if len(row['features'])>index else None
                if (type(cell) is not dict or cell['family'] != column['family']
                    or cell['name'] != column['name'] or cell['representation']!=column['representation']):
                    _fail('coverage_column_mismatch', 'Feature matrix schema drift')
                observation=cell['observation']; state=observation['state']
                if state not in ('present','null','absent'):
                    _fail('coverage_state_unknown', 'Unsupported observation state')
                counts[state+'_n']+=1
                if state=='present':
                    value=observation['value']
                    if value is False: counts['false_n']+=1
                    if type(value) is int and value==0: counts['zero_n']+=1
                    if type(value) is list and not value: counts['empty_sequence_n']+=1
            if counts['present_n']+counts['null_n']+counts['absent_n']!=counts['n']:
                _fail('coverage_population_mismatch', 'Feature observation denominator drift')
            missing=counts['null_n']+counts['absent_n']
            if counts['present_n'] < policy.min_present_per_role_per_feature:
                blockers.append('insufficient_feature_coverage:'+column['family']+'.'+column['name']+':'+role)
            if Decimal(missing)/Decimal(counts['n']) > Decimal(policy.max_missing_fraction):
                blockers.append('excess_missingness:'+column['family']+'.'+column['name']+':'+role)
            per_role[role]=_display_counts(counts,policy.min_cell_disclosure_n)
        # Source-inclusive estimate is vital: matched controls are NOT the denominator
        # for claiming representativeness of original qualified UAE population.
        audit_columns.append({'family':column['family'],'name':column['name'],
          'representation':column['representation'],'unit':column['unit'],
          'source_eligible':source_stats,'matched_roles':per_role,
          'missing_cause':'not_observable_in_uae_to_pie_v1',
          'not_run_n':None,'partial_n':None,
          'not_run_or_partial_inference':'forbidden',
          'matched_vs_source_missingness_comparable':False})
    # Report remaining unmatched/neutral/other-stratum observations truthfully.
    return {'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
            'source_snapshot_sha256':snapshot.snapshot_sha256,
            'source_strata_sha256':strata.artifact_sha256,
            'source_outcome_sha256':outcome.artifact_sha256,
            'source_controls_sha256':controls.artifact_sha256,
            'source_matrix_sha256':matrix.artifact_sha256,
            'project_id':snapshot.project_id,
            'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
            'population':{'scanned_n':scanned,'included_n':included,'excluded_n':len(excluded),
                'excluded_by_reason':dict(sorted(Counter(x['reason_code'] for x in excluded).items())),
                'matched_n':matched,'pairs_n':controls.pair_count,
                'high_n':len(roles['high']),'low_n':len(roles['low']),
                'neutral_n':neutral,'unmatched_n':unmatched,'other_strata_n':other,
                'matched_fraction_of_included':_rate(matched,included),
                'eligible_fraction_of_scanned':_rate(included,scanned),
                'unmatched_by_reason':dict(sorted(Counter(x['reason_code'] for x in population['unmatched']).items()))},
            'balance':{'role_n_difference':abs(len(roles['high'])-len(roles['low'])),
                'creator_roles':concentration,'shared_creator_n':common,
                'creator_adjustment_claimed':False,'statistical_balance_proven':False,
                'uncontrolled_dimensions':m['uncontrolled_dimensions']},
            'feature_coverage':audit_columns,
            'offline_coverage_status':'blocked' if blockers else 'pass',
            'blockers':sorted(set(blockers)),'warnings':sorted(set(warnings)),
            'not_run_and_partial_states_available':False,
            'selection_bias_proven_absent':False}


def _freeze(payload: dict) -> CandidateCoverageAudit:
    raw=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf8'))>_MAX_BYTES:
        _fail('payload_too_large','Audit exceeds byte cap')
    return CandidateCoverageAudit(sha256(raw.encode('utf8')).hexdigest(),
        payload['source_matrix_sha256'],payload['source_controls_sha256'],
        payload['policy_sha256'],payload['project_id'],payload['offline_coverage_status'],
        payload['population']['matched_n'],len(payload['feature_coverage']),raw)


def build_candidate_coverage_audit(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                                   outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
                                   matrix: CandidateFeatureMatrix, policy: CoveragePolicy) -> CandidateCoverageAudit:
    """Produces auditable structural QA, NEVER licenses production statistics."""
    if type(policy) is not CoveragePolicy:
        _fail('invalid_coverage_policy', 'Explicit typed coverage policy required')
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,policy))


def replay_candidate_coverage_audit(raw: str|bytes, expected_sha256: str,
                                    snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                                    outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
                                    matrix: CandidateFeatureMatrix) -> CandidateCoverageAudit:
    """Rebuild entire audit from original frozen sources; hash alone not attestation."""
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_coverage_audit','Audit JSON and canonical SHA-256 required')
    if len(raw if type(raw) is bytes else raw.encode('utf8'))>_MAX_BYTES:
        _fail('payload_too_large','Audit exceeds byte cap')
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d:_fail('duplicate_json_key','Duplicate coverage JSON key')
            d[k]=v
        return d
    try:
        payload=json.loads(raw,object_pairs_hook=unique,
               parse_constant=lambda _: _fail('invalid_coverage_audit','Nonfinite JSON forbidden'))
    except PIEBoundaryError:
        raise
    except (ValueError,UnicodeError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_coverage_audit','Malformed coverage JSON') from exc
    if type(payload) is not dict or canonical_hash(payload)!=expected_sha256:
        _fail('coverage_hash_mismatch','Audit hash does not match')
    if (payload.get('schema_version')!=SCHEMA or payload.get('evidence_state')!='unverified'
        or payload.get('approved_live') is not False):
        _fail('unsupported_coverage_audit','Offline audit cannot become live authorized')
    p=payload.get('policy')
    if type(p) is not dict or set(p)!={
         'policy_version','design_sha256','min_matched_rows','min_rows_per_role',
         'min_present_per_role_per_feature','max_missing_fraction',
         'min_cell_disclosure_n','require_shared_creators','require_all_high_matched'}:
        _fail('invalid_coverage_policy','Complete policy required to reconstruct audit')
    try:
        policy=CoveragePolicy(**p)
    except (TypeError,KeyError) as exc:
        raise PIEBoundaryError('invalid_coverage_policy','Malformed coverage policy') from exc
    rebuilt=build_candidate_coverage_audit(snapshot,strata,outcome,controls,matrix,policy)
    if rebuilt.as_dict()!=payload:
        _fail('coverage_replay_mismatch','Resealed audit disagrees with original population')
    return rebuilt
