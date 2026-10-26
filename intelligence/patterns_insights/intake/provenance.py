"""PIE-MOD-06: independent, read-time source and per-observation provenance gate.

A pure, explicitly injected decision layer. Provider-returned booleans/digests
are NOT database attestations; a concrete trusted project/D1/UAE adapter is
still required (and absent from the current UAE branch). This module never
upgrades mock-tested data to production-ready or exposes row data on denial.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Callable, Protocol

from ..contracts import PIEDatasetPointer, PIEBoundaryError
from .uae import UAEReadResult

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT = re.compile(r"[0-9a-f]{32}\Z")


def _deny(code: str, detail: str) -> None:
    raise PIEBoundaryError(code, detail)


def _time(value: str, label: str) -> datetime:
    if type(value) is not str or len(value) > 40 or 'T' not in value:
        _deny('invalid_provenance', f'{label}: missing timezone-aware timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_provenance', f'{label}: malformed timestamp') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _deny('invalid_provenance', f'{label}: timezone required')
    return parsed


def _identity(project_id: str, source_id: str, policy_id: int, manifest_sha256: str) -> None:
    if type(project_id) is not str or not _PROJECT.fullmatch(project_id):
        _deny('invalid_provenance', 'Invalid project scope')
    if type(source_id) is not str or not 1 <= len(source_id) <= 160 or not re.fullmatch(r'[A-Za-z0-9._:-]+', source_id):
        _deny('invalid_provenance', 'Invalid source scope')
    if type(policy_id) is not int or policy_id <= 0:
        _deny('invalid_provenance', 'Invalid source policy ID')
    if type(manifest_sha256) is not str or not _SHA.fullmatch(manifest_sha256):
        _deny('invalid_provenance', 'Invalid manifest digest')


def _bools(obj: object, fields: tuple[str, ...]) -> None:
    for name in fields:
        if type(getattr(obj, name)) is not bool:
            _deny('invalid_provenance', f'{name} must be an explicit boolean')


@dataclass(frozen=True, slots=True)
class ScopeEligibility:
    """Independently checked CURRENT project membership, source and policy claims.

    The provider must query authoritative live DB/policy rather than echo the
    untrusted manifest. The injected object alone cannot prove its own origin.
    """
    project_id: str
    source_id: str
    source_policy_version_id: int
    manifest_sha256: str
    dataset_sha256: str
    checked_at: str
    retention_until: str
    project_membership_current: bool
    source_enabled: bool
    policy_active_and_current: bool
    can_store_metadata: bool
    can_analyze_media: bool
    can_store_metrics: bool
    can_derive_scores: bool

    def __post_init__(self) -> None:
        _identity(self.project_id, self.source_id,
                  self.source_policy_version_id, self.manifest_sha256)
        if type(self.dataset_sha256) is not str or not _SHA.fullmatch(self.dataset_sha256):
            _deny('invalid_provenance', 'Invalid dataset digest')
        _time(self.checked_at, 'checked_at')
        _time(self.retention_until, 'retention_until')
        _bools(self, ('project_membership_current', 'source_enabled',
                      'policy_active_and_current', 'can_store_metadata',
                      'can_analyze_media', 'can_store_metrics', 'can_derive_scores'))


@dataclass(frozen=True, slots=True)
class RowProvenanceSubject:
    """Read-only exact reference tuple; no features, metrics, raw media or PII."""
    project_id: str
    source_id: str
    source_policy_version_id: int
    manifest_sha256: str
    video_id: int
    uae_snapshot_id: int
    uae_snapshot_sha256: str
    row_sha256: str
    qdp_run_id: str
    qdp_preparation_sha256: str
    sre_decision_sha256: str
    group_a_analysis_id: int
    group_b_analysis_id: int
    metric_snapshot_id: int
    calculated_metric_id: int
    group_a_sha256: str
    group_b_sha256: str
    metric_snapshot_sha256: str
    calculated_metric_sha256: str

    def __post_init__(self) -> None:
        _identity(self.project_id, self.source_id, self.source_policy_version_id,
                  self.manifest_sha256)
        for name in ('video_id', 'uae_snapshot_id', 'group_a_analysis_id',
                     'group_b_analysis_id', 'metric_snapshot_id', 'calculated_metric_id'):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) < 2**63:
                _deny('invalid_provenance', f'Invalid {name}')
        for name in ('uae_snapshot_sha256', 'row_sha256', 'qdp_preparation_sha256',
                     'sre_decision_sha256', 'group_a_sha256', 'group_b_sha256',
                     'metric_snapshot_sha256', 'calculated_metric_sha256'):
            value = getattr(self, name)
            if type(value) is not str or not _SHA.fullmatch(value):
                _deny('invalid_provenance', f'Invalid {name}')
        if type(self.qdp_run_id) is not str or len(self.qdp_run_id) > 50:
            _deny('invalid_provenance', 'Invalid QDP run identifier')


@dataclass(frozen=True, slots=True)
class RowEligibility:
    """Independent row-level evidence; never trust the claimed qualified flag."""
    project_id: str
    source_id: str
    source_policy_version_id: int
    manifest_sha256: str
    video_id: int
    uae_snapshot_id: int
    uae_snapshot_sha256: str
    row_sha256: str
    checked_at: str
    derived_expires_at: str
    project_video_member: bool
    uae_qualified_current: bool
    qdp_qualified_current: bool
    sre_approved_live: bool
    group_a_complete: bool
    group_b_complete: bool
    c_score_persisted_complete: bool
    lineage_exact_current: bool
    source_metric_healthy: bool

    def __post_init__(self) -> None:
        _identity(self.project_id, self.source_id, self.source_policy_version_id,
                  self.manifest_sha256)
        for name in ('video_id', 'uae_snapshot_id'):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) < 2**63:
                _deny('invalid_provenance', 'Invalid row identifier')
        for name in ('uae_snapshot_sha256', 'row_sha256'):
            x = getattr(self, name)
            if type(x) is not str or not _SHA.fullmatch(x):
                _deny('invalid_provenance', f'Invalid {name}')
        _time(self.checked_at, 'checked_at')
        _time(self.derived_expires_at, 'derived_expires_at')
        _bools(self, ('project_video_member', 'uae_qualified_current',
                      'qdp_qualified_current', 'sre_approved_live',
                      'group_a_complete', 'group_b_complete',
                      'c_score_persisted_complete', 'lineage_exact_current',
                      'source_metric_healthy'))


class ProvenanceAuthority(Protocol):
    """Production implementation MUST use authoritative data, never local mocks."""
    def check_scope(self, pointer: PIEDatasetPointer) -> ScopeEligibility: ...
    def check_row(self, subject: RowProvenanceSubject) -> RowEligibility: ...


@dataclass(frozen=True, slots=True)
class EligibilityReport:
    """No raw observations, IDs or scores in responses or diagnostics."""
    manifest_sha256: str
    total_rows: int
    structurally_eligible_rows: int
    suppressed_rows: int
    blockers: tuple[str, ...]

    @property
    def candidate_eligible(self) -> bool:
        return not self.blockers and self.total_rows > 0

    @property
    def verified_live(self) -> bool:
        # Authority's Python-reported booleans alone are not authentication.
        return False

    @property
    def ready_for_analysis(self) -> bool:
        # Requires concrete live UAE exporter and trusted source adapters.
        return False


class SourceEligibilityGate:
    """Every evaluation calls external authority again (no stale caching).

    Denied scopes skip row reads and conceal member identities. A single bad
    row suppresses the *entire* dataset rather than silently changing N/holdout.
    """
    def __init__(self, authority: ProvenanceAuthority | None = None,
                 *, now: Callable[[], datetime] | None = None):
        self._authority = authority
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _get_scope(self, pointer: PIEDatasetPointer, manifest_sha256: str,
                   dataset_sha256: str, earliest: datetime, now: datetime) -> ScopeEligibility:
        try:
            scope = self._authority.check_scope(pointer)  # type: ignore[union-attr]
        except Exception as exc:
            raise PIEBoundaryError('provenance_check_failed', 'Current scope read failed') from exc
        if not isinstance(scope, ScopeEligibility):
            _deny('invalid_provenance_response', 'Typed current scope result required')
        if (scope.project_id != pointer.project_id or scope.source_id != pointer.source_id
            or scope.source_policy_version_id != pointer.source_policy_version_id
            or scope.manifest_sha256 != manifest_sha256
            or scope.dataset_sha256 != dataset_sha256):
            _deny('provenance_scope_mismatch', 'Current scope identity changed')
        if not earliest <= _time(scope.checked_at, 'scope.checked_at') <= now:
            _deny('provenance_stale', 'Scope not rechecked at read time')
        return scope

    @staticmethod
    def _scope_blockers(scope: ScopeEligibility, now: datetime) -> set[str]:
        reasons: set[str] = set()
        for name in ('project_membership_current', 'source_enabled',
                     'policy_active_and_current', 'can_store_metadata',
                     'can_analyze_media', 'can_store_metrics', 'can_derive_scores'):
            if not getattr(scope, name):
                reasons.add('scope_' + name)
        if _time(scope.retention_until, 'retention_until') <= now:
            reasons.add('scope_expired')
        return reasons

    def evaluate(self, read: UAEReadResult) -> EligibilityReport:
        if not isinstance(read, UAEReadResult) or not isinstance(read.dataset.pointer, PIEDatasetPointer):
            _deny('invalid_read', 'Signed UAE intake result is required')
        if self._authority is None or not callable(getattr(self._authority, 'check_scope', None)) or not callable(getattr(self._authority, 'check_row', None)):
            _deny('capability_unavailable', 'Current project/source provenance authority unavailable')
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            _deny('invalid_clock', 'Timezone-aware evaluation clock required')
        dataset = read.dataset
        p = dataset.pointer
        if (type(read.issuer) is not str or not read.issuer or
            type(read.receipt_sha256) is not str or not _SHA.fullmatch(read.receipt_sha256)):
            _deny('invalid_read', 'Signed receiver receipt identity is missing')
        rights_at = _time(read.rights_checked_at, 'rights_checked_at')
        if rights_at > now:
            _deny('rights_stale', 'Future-dated rights decision')
        scope = self._get_scope(p, dataset.manifest_sha256, dataset.dataset_sha256,
                                rights_at, now)
        reasons = self._scope_blockers(scope, now)
        if reasons:
            return EligibilityReport(dataset.manifest_sha256, dataset.sample_size,
                                     0, dataset.sample_size, tuple(sorted(reasons)))
        row_expiries: list[datetime] = []
        for obs in dataset.observations:
            row = obs.as_dict()
            subject = RowProvenanceSubject(
                project_id=p.project_id, source_id=p.source_id,
                source_policy_version_id=p.source_policy_version_id,
                manifest_sha256=dataset.manifest_sha256,
                video_id=obs.video_id, uae_snapshot_id=obs.uae_snapshot_id,
                uae_snapshot_sha256=row['uae_snapshot_sha256'],
                row_sha256=obs.row_sha256,
                qdp_run_id=row['qdp_run_id'],
                qdp_preparation_sha256=row['qdp_preparation_sha256'],
                sre_decision_sha256=row['sre_decision_sha256'],
                group_a_analysis_id=row['group_a_analysis_id'],
                group_b_analysis_id=row['group_b_analysis_id'],
                metric_snapshot_id=row['metric_snapshot_id'],
                calculated_metric_id=row['calculated_metric_id'],
                group_a_sha256=row['group_a_sha256'],
                group_b_sha256=row['group_b_sha256'],
                metric_snapshot_sha256=row['metric_snapshot_sha256'],
                calculated_metric_sha256=row['calculated_metric_sha256'],
            )
            try:
                evidence = self._authority.check_row(subject)
            except Exception as exc:
                raise PIEBoundaryError('provenance_check_failed', 'Current row evidence read failed') from exc
            if not isinstance(evidence, RowEligibility):
                _deny('invalid_provenance_response', 'Typed row result required')
            for key in ('project_id', 'source_id', 'source_policy_version_id',
                        'manifest_sha256', 'video_id', 'uae_snapshot_id',
                        'uae_snapshot_sha256', 'row_sha256'):
                if getattr(subject, key) != getattr(evidence, key) or type(getattr(subject, key)) is not type(getattr(evidence, key)):
                    _deny('provenance_lineage_mismatch', 'Row evidence references different source or snapshot')
            row_now = self._now()
            if not isinstance(row_now, datetime) or row_now.tzinfo is None or row_now.utcoffset() is None:
                _deny('invalid_clock', 'Timezone-aware evaluation clock required')
            if not rights_at <= _time(evidence.checked_at, 'row.checked_at') <= row_now:
                reasons.add('row_stale')
            expiry = _time(evidence.derived_expires_at, 'derived_expires_at')
            row_expiries.append(expiry)
            if expiry <= row_now:
                reasons.add('row_expired')
            for name in ('project_video_member', 'uae_qualified_current',
                         'qdp_qualified_current', 'sre_approved_live',
                         'group_a_complete', 'group_b_complete',
                         'c_score_persisted_complete', 'lineage_exact_current',
                         'source_metric_healthy'):
                if not getattr(evidence, name):
                    reasons.add('row_' + name)
        # A policy may be revoked while scanning large datasets; recheck again
        # before returning even a non-authoritative candidate report.
        refreshed_at = self._now()
        if not isinstance(refreshed_at, datetime) or refreshed_at.tzinfo is None or refreshed_at.utcoffset() is None:
            _deny('invalid_clock', 'Timezone-aware evaluation clock required')
        latest = self._get_scope(p, dataset.manifest_sha256, dataset.dataset_sha256,
                                 rights_at, refreshed_at)
        reasons.update(self._scope_blockers(latest, refreshed_at))
        if any(expiry <= refreshed_at for expiry in row_expiries):
            reasons.add('row_expired')
        if reasons:
            return EligibilityReport(dataset.manifest_sha256, dataset.sample_size,
                                     0, dataset.sample_size, tuple(sorted(reasons)))
        # Even an affirmative mock result is NOT a production authorization.
        return EligibilityReport(dataset.manifest_sha256, dataset.sample_size,
                                 dataset.sample_size, 0, ())
