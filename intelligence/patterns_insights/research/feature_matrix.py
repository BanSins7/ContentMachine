"""PIE-MOD-11: frozen, typed A/B feature matrix and **label-only** raw Group C outcome.

Structural candidate evidence is not a source permission, human approval or
production statistical input. Build only from byte-replayed PIE 07/08/09/10
artifacts. No imports from UAE/A/B/C runtime and no invented feature values.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import re
from typing import Literal

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import CandidateResearchSnapshot, replay_candidate_snapshot
from .strata import CandidateStrata, replay_candidate_strata
from .outcomes import CandidateOutcomePlan, replay_candidate_outcome_plan
from .controls import CandidateMatchedControls, replay_candidate_controls

SCHEMA = 'pie-feature-matrix/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_NAME = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_UNIT = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_MAX_COLUMNS = 64
_MAX_BYTES = 128 * 1024 * 1024
_KINDS = {'numeric_int':'int', 'boolean':'bool', 'category':'text',
          'ordered_sequence':'ordered_text_sequence', 'multi_label':'ordered_text_sequence'}


def _fail(code: str, detail: str) -> None:
    raise PIEBoundaryError(code, detail)


@dataclass(frozen=True, slots=True)
class FeatureColumn:
    """Intentional feature role, not a guess based on a B taxonomy label."""
    family: Literal['a', 'b']
    name: str
    representation: Literal['numeric_int', 'boolean', 'category', 'ordered_sequence', 'multi_label']
    allow_absent: bool
    allow_null: bool
    unit: str | None = None

    def __post_init__(self) -> None:
        if type(self.family) is not str or self.family not in ('a', 'b'):
            _fail('invalid_feature_policy', 'A or B family is required')
        if type(self.name) is not str or not _NAME.fullmatch(self.name):
            _fail('invalid_feature_policy', 'Expected an observed feature key')
        if type(self.representation) is not str or self.representation not in _KINDS:
            _fail('invalid_feature_policy', 'Unsupported feature representation')
        if type(self.allow_absent) is not bool or type(self.allow_null) is not bool:
            _fail('invalid_feature_policy', 'Missing and null handling must be explicit')
        if self.representation == 'numeric_int':
            if type(self.unit) is not str or not _UNIT.fullmatch(self.unit):
                _fail('invalid_feature_policy', 'Explicit numeric units required')
        elif self.unit is not None:
            _fail('invalid_feature_policy', 'Only numeric features can have a unit')


@dataclass(frozen=True, slots=True)
class FeatureMatrixPolicy:
    policy_version: str
    study_design_sha256: str
    features: tuple[FeatureColumn, ...]

    def __post_init__(self) -> None:
        if type(self.policy_version) is not str or not _VERSION.fullmatch(self.policy_version):
            _fail('invalid_feature_policy', 'Explicit policy version required')
        if type(self.study_design_sha256) is not str or not _SHA.fullmatch(self.study_design_sha256):
            _fail('invalid_feature_policy', 'Pinned study design SHA required')
        if type(self.features) is not tuple or not 1 <= len(self.features) <= _MAX_COLUMNS:
            _fail('invalid_feature_policy', 'Bounded nonempty feature tuple required')
        if any(type(c) is not FeatureColumn for c in self.features):
            _fail('invalid_feature_policy', 'Only explicit typed columns accepted')
        names = [(c.family, c.name) for c in self.features]
        if names != sorted(set(names)):
            _fail('duplicate_feature', 'Columns must be unique and canonically sorted')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class CandidateFeatureMatrix:
    artifact_sha256: str
    snapshot_sha256: str
    strata_sha256: str
    outcome_sha256: str
    controls_sha256: str
    policy_sha256: str
    project_id: str
    row_count: int
    column_count: int
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


def _validate_column_dictionary(columns: tuple[FeatureColumn,...], dictionary: dict) -> None:
    entries = {(e['family'], e['name']): e for e in dictionary['entries']}
    for c in columns:
        key = ('features_' + c.family, c.name)
        e = entries.get(key)
        if not e or not e['present_count']:
            _fail('unsupported_feature', 'Column not observed in frozen UAE dictionary')
        types = set(e['observed_types'])
        if not types <= {_KINDS[c.representation], 'null'} or _KINDS[c.representation] not in types:
            _fail('feature_type_mismatch', 'Declared feature representation disagrees with observed UAE type')
        # Across the full source population, not only the matched subset:
        # an unapproved missing/null cannot be silently hidden by matching.
        if not c.allow_absent and e['absent_count']:
            _fail('missing_feature', 'Missing column needs explicit allowance')
        if not c.allow_null and e['null_count']:
            _fail('null_feature', 'Null column needs explicit allowance')


def _value(row: dict, col: FeatureColumn) -> dict:
    values = row['features_' + col.family]
    if col.name not in values:
        if not col.allow_absent:
            _fail('missing_feature', 'Unobserved cell in selected sample')
        return {'state': 'absent', 'value': None}
    v = values[col.name]
    if v is None:
        if not col.allow_null:
            _fail('null_feature', 'Explicit null needs explicit allowance')
        return {'state':'null', 'value':None}
    match col.representation:
        case 'numeric_int':
            if type(v) is not int:
                _fail('feature_type_mismatch', 'Numeric int is not a bool/float/text')
        case 'boolean':
            if type(v) is not bool:
                _fail('feature_type_mismatch', 'Bool must retain false vs zero')
        case 'category':
            if type(v) is not str:
                _fail('feature_type_mismatch', 'Categorical value must be observed text')
        case 'multi_label' | 'ordered_sequence':
            if type(v) is not list or not all(type(x) is str for x in v):
                _fail('feature_type_mismatch', 'Sequence/multilabel expects exact string list')
        case _:
            _fail('invalid_feature_policy', 'Unsupported representation')
    if col.representation == 'multi_label':
        # B1 is a *set* semantically, not one sample per label. Keep the
        # original list intact for forensic replay, even with repeats.
        return {'state':'present', 'value':sorted(set(v)), 'observed_order':list(v)}
    # B3 sequence preserves ordering AND repeated positions; even empty list
    # is a known, present value, not null or missing.
    return {'state':'present', 'value':v}


def _build(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
           outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
           policy: FeatureMatrixPolicy) -> dict:
    if (type(snapshot) is not CandidateResearchSnapshot or type(strata) is not CandidateStrata
        or type(outcome) is not CandidateOutcomePlan or type(controls) is not CandidateMatchedControls):
        _fail('invalid_feature_input', 'Require exact frozen PIE candidate objects')
    c = replay_candidate_snapshot(snapshot.to_json(), snapshot.snapshot_sha256).as_dict()
    s = replay_candidate_strata(strata.to_json(), strata.artifact_sha256, snapshot).as_dict()
    o = replay_candidate_outcome_plan(outcome.to_json(), outcome.artifact_sha256, snapshot, strata).as_dict()
    m = replay_candidate_controls(controls.to_json(), controls.artifact_sha256,
                                  snapshot, strata, outcome).as_dict()
    if (any(x.get('evidence_state') != 'unverified' for x in (c,s,o,m))
        or s['project_id'] != c['source']['pointer']['project_id']
        or o['project_id'] != snapshot.project_id or m['project_id'] != snapshot.project_id
        or m['source_outcome_sha256'] != outcome.artifact_sha256):
        _fail('feature_scope_mismatch', 'Exact project and candidate state required')
    if (c['design']['design_sha256'] != policy.study_design_sha256
        or o['policy']['design_sha256'] != policy.study_design_sha256):
        _fail('feature_design_mismatch', 'Feature policy must pin the research study design')
    _validate_column_dictionary(policy.features, c['dictionary'])
    member_by_id = {x['video_id']:x for x in c['selection']['included']}
    if len(member_by_id) != len(snapshot.included_video_ids):
        _fail('feature_population_mismatch', 'Duplicate/missing frozen source rows')
    if not m['pairs'] or len(m['pairs']) != controls.pair_count:
        _fail('feature_population_mismatch', 'Expected nonempty frozen matched pairs')
    rows = []
    for index, pair in enumerate(m['pairs']):
        for side, role in (('case','high'), ('control','low')):
            video_id = pair[side+'_video_id']
            member = member_by_id.get(video_id)
            if (member is None or member['uae_snapshot_id'] != pair[side+'_snapshot_id']
                or member['row_sha256'] != pair[side+'_row_sha256']
                or member['analysis_weight'] != pair[side+'_weight']):
                _fail('feature_lineage_mismatch', 'Matched record must match exact UAE row and weight')
            source_row = member['row']
            a_id = source_row['group_a_analysis_id']
            b_id = source_row['group_b_analysis_id']
            cells = []
            for col in policy.features:
                lineage_id = a_id if col.family == 'a' else b_id
                lineage_sha = source_row['group_a_sha256' if col.family == 'a' else 'group_b_sha256']
                lineage_version = source_row['group_a_version' if col.family == 'a' else 'group_b_version']
                cells.append({'family':col.family, 'name':col.name,
                    'representation':col.representation, 'observation':_value(source_row,col),
                    'analysis_id':lineage_id, 'analysis_sha256':lineage_sha,
                    'analysis_version':lineage_version, 'features_sha256':source_row['features_sha256']})
            rows.append({'video_id':video_id, 'role':role, 'pair_index':index,
                         'uae_snapshot_id':member['uae_snapshot_id'],
                         'uae_snapshot_sha256':source_row['uae_snapshot_sha256'],
                         'row_sha256':member['row_sha256'], 'weight':member['analysis_weight'],
                         'features':cells,
                         # Raw C7 is the supervised outcome label ONLY, never in features.
                         'target':{'kind':'raw_c7_decimal_label', 'name':'breakout_score',
                            'value':source_row['breakout_score'],
                            'calculated_metric_id':source_row['calculated_metric_id'],
                            'calculated_metric_sha256':source_row['calculated_metric_sha256'],
                            'calculation_config_id':c['source']['pointer']['calculation_config_id'],
                            'source_metric_definition':c['source']['pointer']['source_metric_definition'],
                            'source_metric_version':c['source']['pointer']['source_metric_version'],
                            'outcome_policy_sha256':o['policy_sha256']}})
    assigned = [v['video_id'] for v in rows]
    if (len(set(assigned)) != len(assigned) or len(rows) != 2 * controls.pair_count
        or set(assigned) != set(controls.high_video_ids) | set(controls.low_video_ids)):
        _fail('feature_population_mismatch', 'Exactly one feature row per matched video')
    unmatched = m['unmatched_cases'] + m['unmatched_controls']
    if (len(rows) + len(unmatched) + len(m['neutral_video_ids'])
        + len(m['other_strata_video_ids']) != len(snapshot.included_video_ids)):
        _fail('feature_population_mismatch', 'Every included source row must be accounted for')
    return {
        'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
        'project_id':snapshot.project_id,'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_strata_sha256':strata.artifact_sha256,'source_outcome_sha256':outcome.artifact_sha256,
        'source_controls_sha256':controls.artifact_sha256,
        'study_design_sha256':policy.study_design_sha256,
        'source_dictionary_sha256':c['dictionary_sha256'],
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'columns':[asdict(col) for col in policy.features],
        'rows':rows,'row_count':len(rows),'column_count':len(policy.features),
        'pair_count':controls.pair_count,
        'population':{'scanned_count':c['selection']['scanned_count'],
                      'included_count':len(snapshot.included_video_ids),
                      'matched_count':len(rows),
                      'unmatched_count':len(unmatched),
                      'neutral_count':len(m['neutral_video_ids']),
                      'other_strata_count':len(m['other_strata_video_ids']),
                      'original_excluded':m['original_exclusions'],
                      'unmatched':unmatched,
                      'neutral_video_ids':m['neutral_video_ids'],
                      'other_strata_video_ids':m['other_strata_video_ids']},
    }


def _freeze(payload: dict) -> CandidateFeatureMatrix:
    raw=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Feature matrix exceeds byte cap')
    return CandidateFeatureMatrix(sha256(raw.encode('utf-8')).hexdigest(),
        payload['source_snapshot_sha256'],payload['source_strata_sha256'],
        payload['source_outcome_sha256'],payload['source_controls_sha256'],
        payload['policy_sha256'],payload['project_id'],payload['row_count'],
        payload['column_count'],raw)


def build_candidate_feature_matrix(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                                   outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
                                   policy: FeatureMatrixPolicy) -> CandidateFeatureMatrix:
    """Pure structural projection. Never declares research or rights approved."""
    if type(policy) is not FeatureMatrixPolicy:
        _fail('invalid_feature_policy','Explicit versioned feature policy required')
    return _freeze(_build(snapshot,strata,outcome,controls,policy))


def replay_candidate_feature_matrix(raw: str|bytes, expected_sha256: str,
                                    snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                                    outcome: CandidateOutcomePlan,
                                    controls: CandidateMatchedControls) -> CandidateFeatureMatrix:
    """Reconstruct from pinned inputs: an attacker-computed SHA is not provenance."""
    if (type(raw) not in (str,bytes) or type(expected_sha256) is not str
        or not _SHA.fullmatch(expected_sha256)):
        _fail('invalid_feature_matrix','Canonical JSON bytes/text and SHA256 required')
    if len(raw if type(raw) is bytes else raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Feature matrix exceeds byte cap')
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d:_fail('duplicate_json_key','Duplicate feature matrix JSON key')
            d[k]=v
        return d
    try:
        payload=json.loads(raw,object_pairs_hook=unique,
                parse_constant=lambda _: _fail('invalid_feature_matrix','Nonfinite JSON forbidden'))
    except PIEBoundaryError:
        raise
    except (ValueError,UnicodeError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_feature_matrix','Invalid feature matrix JSON') from exc
    if type(payload) is not dict or canonical_hash(payload)!=expected_sha256:
        _fail('feature_hash_mismatch','Feature matrix SHA does not match')
    if (payload.get('schema_version')!=SCHEMA or payload.get('evidence_state')!='unverified'
        or payload.get('approved_live') is not False):
        _fail('unsupported_feature_matrix','Only candidate/unverified matrix can replay')
    specification=payload.get('policy')
    if type(specification) is not dict or set(specification)!={'policy_version','study_design_sha256','features'}:
        _fail('invalid_feature_policy','Missing/unknown feature policy keys')
    try:
        if type(specification['features']) is not list:
            _fail('invalid_feature_policy','Features must be a list')
        columns=tuple(FeatureColumn(**f) for f in specification['features'])
        policy=FeatureMatrixPolicy(specification['policy_version'],specification['study_design_sha256'],columns)
    except (TypeError,KeyError) as exc:
        raise PIEBoundaryError('invalid_feature_policy','Malformed feature policy') from exc
    rebuilt=build_candidate_feature_matrix(snapshot,strata,outcome,controls,policy)
    if rebuilt.as_dict()!=payload:
        _fail('feature_replay_mismatch','Feature payload diverges from exact source reconstruction')
    return rebuilt
