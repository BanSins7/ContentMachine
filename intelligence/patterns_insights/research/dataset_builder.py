"""PIE-MOD-07 deterministic, immutable *candidate* research dataset builder.

No implicit cohort selection, fabricated rows, statistical weights or missingness
imputation. Uses only a structurally validated UAE export and PIE source gate.
Neither a candidate snapshot nor its SHA authorizes production analysis.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any

from ..contracts import PIEDatasetPointer, PIEBoundaryError, canonical_hash
from ..dataset_contract import PIEValidatedDataset, validate_dataset_manifest
from ..intake.uae import UAEReadResult
from ..intake.provenance import SourceEligibilityGate, EligibilityReport

SNAPSHOT_SCHEMA = 'pie-research-candidate/v1'
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_REASON = re.compile(r'[a-z][a-z0-9_]{1,79}\Z')
_WEIGHT = re.compile(r'(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?\Z')


def _fail(code: str, message: str) -> None:
    raise PIEBoundaryError(code, message)


def _hash(value: object, label: str) -> None:
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail('invalid_build_spec', f'{label} must be canonical SHA-256')


def _version(value: object, label: str) -> None:
    if type(value) is not str or not _VERSION.fullmatch(value):
        _fail('invalid_build_spec', f'{label} must be explicit bounded version')


@dataclass(frozen=True, slots=True)
class ResearchSampleWeight:
    """An explicitly specified inclusion weight, never a guessed population weight."""
    video_id: int
    value: str

    def __post_init__(self) -> None:
        if type(self.video_id) is not int or not 1 <= self.video_id < 2**63:
            _fail('invalid_weight', 'Expected canonical positive video ID')
        if type(self.value) is not str or not _WEIGHT.fullmatch(self.value):
            _fail('invalid_weight', 'Weight must be bounded positive decimal string')
        try:
            value = Decimal(self.value)
        except InvalidOperation as exc:
            raise PIEBoundaryError('invalid_weight', 'Invalid decimal weight') from exc
        if not value.is_finite() or value <= 0:
            _fail('invalid_weight', 'Zero, negative and nonfinite weights are forbidden')


@dataclass(frozen=True, slots=True)
class ResearchBuildSpec:
    """Fixed research selection *instructions*, not proof of preregistration.

    upstream UAE selected the frozen included/excluded population; this stage
    does not silently add/remove posts. Any weighting is explicit per video.
    """
    design_sha256: str
    algorithm_version: str
    weight_rule_version: str
    selection_reason_code: str
    frozen_as_of: str
    weights: tuple[ResearchSampleWeight, ...]

    def __post_init__(self) -> None:
        _hash(self.design_sha256, 'design_sha256')
        _version(self.algorithm_version, 'algorithm_version')
        _version(self.weight_rule_version, 'weight_rule_version')
        if type(self.selection_reason_code) is not str or not _REASON.fullmatch(self.selection_reason_code):
            _fail('invalid_build_spec', 'selection_reason_code must be explicit')
        if type(self.frozen_as_of) is not str or not 1 <= len(self.frozen_as_of) <= 40:
            _fail('invalid_build_spec', 'An explicit frozen_as_of timestamp is required')
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(self.frozen_as_of.replace('Z', '+00:00'))
        except ValueError as exc:
            raise PIEBoundaryError('invalid_build_spec', 'Malformed frozen_as_of') from exc
        if dt.tzinfo is None or dt.utcoffset() is None:
            _fail('invalid_build_spec', 'Frozen timestamp requires timezone')
        if type(self.weights) is not tuple or not self.weights or any(
            type(w) is not ResearchSampleWeight for w in self.weights
        ):
            _fail('invalid_build_spec', 'Nonempty immutable weights tuple required')
        ids = [weight.video_id for weight in self.weights]
        if ids != sorted(set(ids)):
            _fail('duplicate_weight', 'Weights must have strictly ascending unique video IDs')


@dataclass(frozen=True, slots=True)
class CandidateResearchSnapshot:
    """Stable, detached JSON snapshot; not authenticated or analysis-ready."""
    snapshot_sha256: str
    project_id: str
    upstream_dataset_sha256: str
    upstream_manifest_sha256: str
    included_video_ids: tuple[int, ...]
    excluded_video_ids: tuple[int, ...]
    selection_count: int
    feature_dictionary_sha256: str
    _canonical_json: str = field(repr=False)

    @property
    def verified_live(self) -> bool:
        return False

    @property
    def ready_for_analysis(self) -> bool:
        return False

    @property
    def sample_size(self) -> int:
        return len(self.included_video_ids)

    def as_dict(self) -> dict:
        """Return a defensive copy, never a reference to frozen internal state."""
        return json.loads(self._canonical_json)

    def to_json(self) -> str:
        return self._canonical_json

    def verify_offline_replay(self) -> bool:
        """Checksum/reconstruction ONLY; current rights still require fresh reads."""
        return replay_candidate_snapshot(self._canonical_json, self.snapshot_sha256) == self


def _kind(value: object) -> str:
    if value is None:
        return 'null'
    if type(value) is bool:
        return 'bool'
    if type(value) is int:
        return 'int'
    if type(value) is str:
        return 'text'
    if type(value) is list and all(type(x) is str for x in value):
        return 'ordered_text_sequence'
    _fail('invalid_feature', 'Unknown feature claim must never be invented')


def _feature_dictionary(rows: list[dict], versions: dict, pointer: PIEDatasetPointer) -> dict:
    labels = []
    for family in ('features_a', 'features_b'):
        names = sorted(set().union(*(set(row[family]) for row in rows)))
        for name in names:
            known = [row[family][name] for row in rows if name in row[family]]
            labels.append({
                'family': family,
                'name': name,
                'observed_types': sorted({_kind(v) for v in known}),
                'present_count': len(known),
                'absent_count': len(rows) - len(known),
                'null_count': sum(v is None for v in known),
            })
    return {
        'schema_version': 'pie-feature-dictionary/v1',
        'producer_versions': versions,
        'entries': labels,
        'score': {
            'name': 'breakout_score', 'owner': 'group_c',
            'arithmetic': 'raw_decimal_unbounded',
            'calculation_config_id': pointer.calculation_config_id,
            'source_metric_definition': pointer.source_metric_definition,
            'source_metric_version': pointer.source_metric_version,
        },
    }


def _compose(dataset: PIEValidatedDataset, *, issuer: str, receipt_sha256: str,
             rights_checked_at: str, spec: ResearchBuildSpec) -> dict:
    if spec.frozen_as_of != dataset.pointer.as_of:
        _fail('frozen_time_mismatch', 'Frozen clock must match exact UAE as_of')
    if type(issuer) is not str or not _VERSION.fullmatch(issuer):
        _fail('invalid_read', 'Unrecognized UAE issuer token')
    if type(rights_checked_at) is not str or not rights_checked_at:
        _fail('invalid_read', 'Missing prior rights-check timestamp')
    from datetime import datetime
    try:
        stamp = datetime.fromisoformat(rights_checked_at.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_read', 'Malformed rights-check timestamp') from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        _fail('invalid_read', 'Rights timestamp requires timezone')
    if type(receipt_sha256) is not str or not _SHA.fullmatch(receipt_sha256):
        _fail('invalid_read', 'Invalid receipt SHA')
    ids = tuple(obs.video_id for obs in dataset.observations)
    weight_ids = tuple(w.video_id for w in spec.weights)
    if ids != weight_ids:
        _fail('weight_population_mismatch', 'One explicit weight per exact included video, no extras')
    manifest = dataset.as_dict()
    rows = [obs.as_dict() for obs in dataset.observations]
    dictionary = _feature_dictionary(rows, manifest['versions'], dataset.pointer)
    included = []
    for row, observation, sample_weight in zip(rows, dataset.observations, spec.weights):
        # Preserve every typed row field, exact features/versions and raw C7
        # decimal string; no model-facing automatic projection or imputation.
        included.append({
            'video_id': observation.video_id,
            'uae_snapshot_id': observation.uae_snapshot_id,
            'row_sha256': observation.row_sha256,
            'features_sha256': row['features_sha256'],
            'metric_snapshot_sha256': row['metric_snapshot_sha256'],
            'calculated_metric_sha256': row['calculated_metric_sha256'],
            'inclusion_reason': spec.selection_reason_code,
            'analysis_weight': sample_weight.value,
            'row': row,
        })
    exclusions = manifest['selection']['exclusions']
    if dataset.scanned_count != len(included) + len(exclusions):
        _fail('sample_count_mismatch', 'Frozen selection must reconcile')
    return {
        'schema_version': SNAPSHOT_SCHEMA,
        'evidence_state': 'unverified',
        'source': {
            'pointer': asdict(dataset.pointer),
            'manifest': manifest,
            'issuer': issuer,
            'receipt_sha256': receipt_sha256,
            'rights_checked_at': rights_checked_at,
        },
        'design': {
            'design_sha256': spec.design_sha256,
            'algorithm_version': spec.algorithm_version,
            'weight_rule_version': spec.weight_rule_version,
            'selection_reason_code': spec.selection_reason_code,
            'frozen_as_of': spec.frozen_as_of,
            'weights': [asdict(w) for w in spec.weights],
        },
        'dictionary': dictionary,
        'dictionary_sha256': canonical_hash(dictionary),
        'selection': {
            'population_sha256': manifest['selection']['population_sha256'],
            'rule_version': manifest['selection']['rule_version'],
            'scanned_count': dataset.scanned_count,
            'included_count': len(included),
            'excluded_count': len(exclusions),
            'included': included,
            'excluded': exclusions,
        },
    }


def _snapshot(payload: dict) -> CandidateResearchSnapshot:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_SNAPSHOT_BYTES:
        _fail('payload_too_large', 'Candidate snapshot exceeds transport limit')
    src = payload['source']
    selection = payload['selection']
    return CandidateResearchSnapshot(
        snapshot_sha256=canonical_hash(payload),
        project_id=src['pointer']['project_id'],
        upstream_dataset_sha256=src['pointer']['uae_dataset_sha256'],
        upstream_manifest_sha256=src['pointer']['uae_manifest_sha256'],
        included_video_ids=tuple(entry['video_id'] for entry in selection['included']),
        excluded_video_ids=tuple(entry['video_id'] for entry in selection['excluded']),
        selection_count=selection['scanned_count'],
        feature_dictionary_sha256=payload['dictionary_sha256'],
        _canonical_json=encoded,
    )


def build_candidate_dataset(read: UAEReadResult, gate: SourceEligibilityGate,
                            spec: ResearchBuildSpec) -> CandidateResearchSnapshot:
    """Refresh source/row decisions, freeze candidate selection, never infer readiness.

    A mock-positive gate is allowed *only* to test candidate serialization; a
    real analysis runner must require independently verified live authority.
    """
    if not isinstance(read, UAEReadResult) or not isinstance(read.dataset, PIEValidatedDataset):
        _fail('invalid_read', 'Expected typed UAE read result')
    if type(gate) is not SourceEligibilityGate:
        _fail('capability_unavailable', 'Explicit SourceEligibilityGate required')
    if type(spec) is not ResearchBuildSpec:
        _fail('invalid_build_spec', 'Typed immutable build specification required')
    source = read.dataset
    validated = validate_dataset_manifest(source.pointer, source.as_dict())
    if source != validated:
        _fail('manifest_replay_mismatch', 'Untrusted row data changed since validation')
    report = gate.evaluate(read)  # Mandatory fresh scope and row rights checks.
    if not isinstance(report, EligibilityReport) or report.manifest_sha256 != source.manifest_sha256:
        _fail('eligibility_mismatch', 'Provenance report references another manifest')
    if (not report.candidate_eligible or report.total_rows != source.sample_size
        or report.structurally_eligible_rows != source.sample_size
        or report.suppressed_rows != 0 or report.blockers):
        _fail('eligibility_blocked', 'Dataset cannot be silently reduced or used with denied rows')
    return _snapshot(_compose(validated, issuer=read.issuer,
                              receipt_sha256=read.receipt_sha256,
                              rights_checked_at=read.rights_checked_at, spec=spec))


def _parse_snapshot(raw: str | bytes) -> dict:
    if type(raw) not in (str, bytes):
        _fail('invalid_snapshot', 'Expected JSON UTF-8 bytes or text')
    try:
        if type(raw) is bytes:
            if len(raw) > MAX_SNAPSHOT_BYTES:
                _fail('payload_too_large', 'Oversized snapshot')
            raw = raw.decode('utf-8')
        if len(raw.encode('utf-8')) > MAX_SNAPSHOT_BYTES:
            _fail('payload_too_large', 'Oversized snapshot')
    except UnicodeError as exc:
        raise PIEBoundaryError('invalid_snapshot', 'Invalid UTF-8') from exc

    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                _fail('duplicate_json_key', 'Duplicate candidate snapshot key')
            data[key] = value
        return data

    try:
        parsed = json.loads(raw, object_pairs_hook=unique,
                            parse_constant=lambda _: _fail('invalid_snapshot', 'Nonfinite JSON'))
    except PIEBoundaryError:
        raise
    except (ValueError, RecursionError, TypeError) as exc:
        raise PIEBoundaryError('invalid_snapshot', 'Malformed JSON') from exc
    if type(parsed) is not dict:
        _fail('invalid_snapshot', 'Expected snapshot object')
    return parsed


def replay_candidate_snapshot(raw: str | bytes, expected_sha256: str) -> CandidateResearchSnapshot:
    """Offline replay: reconstruct exact rows and checksums, NOT live authorization."""
    if type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_snapshot', 'Expected canonical digest')
    payload = _parse_snapshot(raw)
    if canonical_hash(payload) != expected_sha256:
        _fail('snapshot_hash_mismatch', 'Candidate snapshot mutated')
    if set(payload) != {'schema_version', 'evidence_state', 'source', 'design',
                        'dictionary', 'dictionary_sha256', 'selection'}:
        _fail('invalid_snapshot', 'Unexpected snapshot fields')
    if payload['schema_version'] != SNAPSHOT_SCHEMA or payload['evidence_state'] != 'unverified':
        _fail('unsupported_snapshot', 'Only unverified candidate snapshots may replay')
    source = payload['source']
    if type(source) is not dict or set(source) != {'pointer', 'manifest', 'issuer', 'receipt_sha256', 'rights_checked_at'}:
        _fail('invalid_snapshot', 'Source manifest/receipt metadata missing')
    if type(source['pointer']) is not dict:
        _fail('invalid_snapshot', 'Expected source pointer object')
    try:
        pointer = PIEDatasetPointer(**source['pointer'])
    except (TypeError, KeyError) as exc:
        raise PIEBoundaryError('invalid_snapshot', 'Invalid frozen pointer') from exc
    dataset = validate_dataset_manifest(pointer, source['manifest'])
    spec = payload['design']
    if type(spec) is not dict or set(spec) != {'design_sha256', 'algorithm_version',
        'weight_rule_version', 'selection_reason_code', 'frozen_as_of', 'weights'}:
        _fail('invalid_snapshot', 'Malformed research design')
    if type(spec['weights']) is not list:
        _fail('invalid_snapshot', 'Weight vector must be explicit list')
    try:
        weights = tuple(ResearchSampleWeight(**v) for v in spec['weights'])
        design = ResearchBuildSpec(spec['design_sha256'], spec['algorithm_version'],
                                   spec['weight_rule_version'], spec['selection_reason_code'],
                                   spec['frozen_as_of'], weights)
    except TypeError as exc:
        raise PIEBoundaryError('invalid_snapshot', 'Invalid sample weights/specification') from exc
    expected_payload = _compose(dataset, issuer=source['issuer'],
                                receipt_sha256=source['receipt_sha256'],
                                rights_checked_at=source['rights_checked_at'], spec=design)
    if payload != expected_payload:
        _fail('snapshot_replay_mismatch', 'Snapshot rows, selection, or dictionary were altered')
    return _snapshot(expected_payload)
