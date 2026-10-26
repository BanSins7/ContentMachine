"""PIE-MOD-08 deterministic *candidate-only* comparability partition.

Never converts unsigned/offline candidate research data into qualified evidence or
claim-level statistical inference. All members are assigned exactly once.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Literal

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import CandidateResearchSnapshot, replay_candidate_snapshot

SCHEMA = 'pie-comparable-strata/v1'
_NAME = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_MAX_SECONDS = 10**10


def _reject(code: str, explanation: str) -> None:
    raise PIEBoundaryError(code, explanation)


@dataclass(frozen=True, slots=True)
class AgeBucket:
    """Half-open second bounds: [min_seconds, max_seconds), max=None → infinity."""
    label: str
    min_seconds: int
    max_seconds: int | None

    def __post_init__(self) -> None:
        if type(self.label) is not str or not _NAME.fullmatch(self.label):
            _reject('invalid_strata_policy', 'Bucket label must be a stable identifier')
        if type(self.min_seconds) is not int or not 0 <= self.min_seconds <= _MAX_SECONDS:
            _reject('invalid_strata_policy', 'Minimum bucket age must be nonnegative integer')
        if self.max_seconds is not None and (
            type(self.max_seconds) is not int or not self.min_seconds < self.max_seconds <= _MAX_SECONDS
        ):
            _reject('invalid_strata_policy', 'Bucket upper bound must be greater than lower')


@dataclass(frozen=True, slots=True)
class StrataPolicy:
    """Explicit study design. No guessed ages, niches or creator deduplication."""
    policy_version: str
    publication_age_buckets: tuple[AgeBucket, ...]
    observation_lag_buckets: tuple[AgeBucket, ...]
    creator_mode: Literal['individual', 'cohort']
    niche_feature: str | None
    niche_missing: Literal['reject', 'isolate']

    def __post_init__(self) -> None:
        if type(self.policy_version) is not str or not _VERSION.fullmatch(self.policy_version):
            _reject('invalid_strata_policy', 'Explicit version required')
        if self.creator_mode not in ('individual', 'cohort') or type(self.creator_mode) is not str:
            _reject('invalid_strata_policy', 'Creator grouping must be explicit')
        if self.niche_missing not in ('reject', 'isolate') or type(self.niche_missing) is not str:
            _reject('invalid_strata_policy', 'Missing niche handling must be explicit')
        if self.niche_feature is not None and (
            type(self.niche_feature) is not str or not _NAME.fullmatch(self.niche_feature)
        ):
            _reject('invalid_strata_policy', 'Optional niche feature must be a feature name')
        if self.niche_feature is None and self.niche_missing != 'isolate':
            _reject('invalid_strata_policy', 'No niche dictionary requires isolate policy')
        for buckets in (self.publication_age_buckets, self.observation_lag_buckets):
            if type(buckets) is not tuple or not 1 <= len(buckets) <= 64:
                _reject('invalid_strata_policy', 'Explicit bounded age buckets required')
            if any(type(b) is not AgeBucket for b in buckets):
                _reject('invalid_strata_policy', 'Invalid age bucket type')
            if buckets[0].min_seconds != 0 or buckets[-1].max_seconds is not None:
                _reject('invalid_strata_policy', 'Buckets must cover 0..infinity')
            if len(set(b.label for b in buckets)) != len(buckets):
                _reject('invalid_strata_policy', 'Duplicate bucket labels')
            for left, right in zip(buckets, buckets[1:]):
                if left.max_seconds is None or left.max_seconds != right.min_seconds:
                    _reject('invalid_strata_policy', 'Buckets must be contiguous without gaps/overlap')


@dataclass(frozen=True, slots=True)
class CandidateStrata:
    """Immutable replayable stratum map; no claim of inferential comparability."""
    artifact_sha256: str
    source_snapshot_sha256: str
    policy_sha256: str
    stratum_count: int
    included_video_ids: tuple[int, ...]
    excluded_video_ids: tuple[int, ...]
    _json: str = field(repr=False)

    @property
    def verified_live(self) -> bool:
        return False

    @property
    def ready_for_analysis(self) -> bool:
        return False

    def as_dict(self) -> dict:
        return json.loads(self._json)

    def to_json(self) -> str:
        return self._json


def _age_micros(later: str, earlier: str) -> int:
    try:
        dt1 = datetime.fromisoformat(later.replace('Z', '+00:00'))
        dt0 = datetime.fromisoformat(earlier.replace('Z', '+00:00'))
    except (ValueError, AttributeError) as exc:
        raise PIEBoundaryError('invalid_strata_timestamp', 'Bad row observation time') from exc
    if dt1.utcoffset() is None or dt0.utcoffset() is None:
        _reject('invalid_strata_timestamp', 'Timezone required')
    duration = dt1 - dt0
    micros = ((duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds)
    if micros < 0:
        _reject('temporal_mismatch', 'Negative age/observation lag')
    return micros


def _bucket(value_us: int, ranges: tuple[AgeBucket, ...]) -> str:
    for bucket in ranges:
        if value_us >= bucket.min_seconds * 1_000_000 and (
            bucket.max_seconds is None or value_us < bucket.max_seconds * 1_000_000
        ):
            return bucket.label
    _reject('invalid_strata_policy', 'No explicit age bucket for row')


def _niche(value: object, policy: StrataPolicy) -> tuple[str, str | None]:
    """One canonical niche token or a *marked* missing label; no multilabel expansion."""
    if policy.niche_feature is None or value is None:
        if policy.niche_missing == 'reject':
            _reject('missing_niche', 'Niche must be observed, not inferred')
        return ('missing', None)
    if type(value) is str and 0 < len(value) <= 160 and all(ord(x) >= 32 for x in value):
        return ('known', 's:' + value)
    if type(value) is int and 0 <= value < 2**63:
        return ('known', 'i:' + str(value))
    # A B8 topic list is multi-label: duplicating the post would inflate N.
    _reject('ambiguous_niche', 'Multi-label/invalid niche cannot be assigned implicitly')


def _candidate(snapshot: CandidateResearchSnapshot) -> dict:
    if type(snapshot) is not CandidateResearchSnapshot:
        _reject('invalid_candidate', 'Require exact CandidateResearchSnapshot')
    replay = replay_candidate_snapshot(snapshot.to_json(), snapshot.snapshot_sha256)
    if replay != snapshot:
        _reject('invalid_candidate', 'Candidate wrapper does not match reconstructed record')
    return replay.as_dict()


def _policy_dict(policy: StrataPolicy) -> dict:
    return asdict(policy)


def _make(snapshot: CandidateResearchSnapshot, policy: StrataPolicy) -> dict:
    candidate = _candidate(snapshot)
    source = candidate['source']
    pointer = source['pointer']
    manifest = source['manifest']
    versions = manifest['versions']
    selected = candidate['selection']['included']
    exclusions = candidate['selection']['excluded']
    as_of = pointer['as_of']
    groups: dict[str, dict] = {}
    for member in selected:
        row = member['row']
        # The upstream data schema has already checked row membership/version pins;
        # replay recomputes those invariants before any partitioning begins.
        lag = _bucket(_age_micros(as_of, row['observed_at']), policy.observation_lag_buckets)
        age = _bucket(_age_micros(row['observed_at'], row['published_at']), policy.publication_age_buckets)
        niche_value = row['features_b'].get(policy.niche_feature) if policy.niche_feature is not None else None
        niche_status, niche = _niche(niche_value, policy)
        creator = row['creator_key'] if policy.creator_mode == 'individual' else versions['creator_cohort_version']
        # Include every comparability-critical frozen version. NULL is retained,
        # never replaced with an assumed taxonomy/cohort default.
        key = {
            'platform': row['platform'], 'content_kind': row['content_kind'],
            'source_id': pointer['source_id'],
            'source_policy_version_id': pointer['source_policy_version_id'],
            'source_metric_definition': pointer['source_metric_definition'],
            'source_metric_version': pointer['source_metric_version'],
            'calculation_config_id': pointer['calculation_config_id'],
            'group_a_version': versions['group_a_version'],
            'group_b_version': versions['group_b_version'],
            'group_b_taxonomy_version': versions['group_b_taxonomy_version'],
            'sre_taxonomy_version': versions['sre_taxonomy_version'],
            'creator_cohort_version': versions['creator_cohort_version'],
            'prior_cohort_version': versions['prior_cohort_version'],
            'niche_cohort_version': versions['niche_cohort_version'],
            'creator_mode': policy.creator_mode, 'creator_group': creator,
            'niche_feature': policy.niche_feature, 'niche_status': niche_status,
            'niche_value': niche, 'publication_age_bucket': age,
            'observation_lag_bucket': lag,
        }
        key_sha = canonical_hash(key)
        group = groups.setdefault(key_sha, {'key': key, 'key_sha256': key_sha, 'members': []})
        if group['key'] != key:
            _reject('strata_collision', 'SHA collision in stratum keys')
        group['members'].append({
            'video_id': member['video_id'],
            'uae_snapshot_id': member['uae_snapshot_id'],
            'row_sha256': member['row_sha256'],
            'analysis_weight': member['analysis_weight'],
        })
    groups_sorted = sorted(groups.values(), key=lambda group: group['key_sha256'])
    for group in groups_sorted:
        group['members'].sort(key=lambda member: member['video_id'])
        group['sample_size'] = len(group['members'])
        group['observed_niche'] = group['key']['niche_status'] == 'known'
    represented = [x['video_id'] for group in groups_sorted for x in group['members']]
    expected = [x['video_id'] for x in selected]
    if sorted(represented) != expected or len(represented) != len(set(represented)):
        _reject('strata_population_mismatch', 'Each included video must appear exactly once')
    if len(exclusions) + len(represented) != candidate['selection']['scanned_count']:
        _reject('strata_population_mismatch', 'Excluded/included counts must reconcile')
    return {
        'schema_version': SCHEMA,
        'evidence_state': 'unverified',
        'source_snapshot_sha256': snapshot.snapshot_sha256,
        'project_id': snapshot.project_id,
        'policy': _policy_dict(policy),
        'policy_sha256': canonical_hash(_policy_dict(policy)),
        'included_video_ids': expected,
        'excluded': exclusions,
        'scanned_count': candidate['selection']['scanned_count'],
        'strata': groups_sorted,
    }


def _create(payload: dict) -> CandidateStrata:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return CandidateStrata(
        artifact_sha256=sha256(encoded.encode('utf-8')).hexdigest(),
        source_snapshot_sha256=payload['source_snapshot_sha256'],
        policy_sha256=payload['policy_sha256'],
        stratum_count=len(payload['strata']),
        included_video_ids=tuple(payload['included_video_ids']),
        excluded_video_ids=tuple(x['video_id'] for x in payload['excluded']),
        _json=encoded,
    )


def build_candidate_strata(snapshot: CandidateResearchSnapshot, policy: StrataPolicy) -> CandidateStrata:
    """Produce auditable groups from an *offline* candidate only (never live ready)."""
    if type(policy) is not StrataPolicy:
        _reject('invalid_strata_policy', 'Explicit, versioned StrataPolicy required')
    return _create(_make(snapshot, policy))


def replay_candidate_strata(raw: str | bytes, expected_sha256: str,
                            snapshot: CandidateResearchSnapshot) -> CandidateStrata:
    """Compare reconstructed full content, not only attacker-recomputed SHA."""
    if type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _reject('invalid_strata', 'Expected SHA-256')
    if type(raw) not in (str, bytes):
        _reject('invalid_strata', 'Expected UTF-8 JSON')
    if len(raw if type(raw) is bytes else raw.encode('utf-8')) > 128 * 1024 * 1024:
        _reject('payload_too_large', 'Oversized stratum map')

    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                _reject('duplicate_json_key', 'Duplicate stratum JSON key')
            obj[key] = value
        return obj

    try:
        payload = json.loads(raw, object_pairs_hook=unique,
                             parse_constant=lambda _: _reject('invalid_strata', 'Nonfinite JSON'))
    except PIEBoundaryError:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise PIEBoundaryError('invalid_strata', 'Malformed stratum JSON') from exc
    if type(payload) is not dict or canonical_hash(payload) != expected_sha256:
        _reject('strata_hash_mismatch', 'Partition payload/hash mismatch')
    try:
        p = payload['policy']
        if type(p) is not dict or set(p) != {
            'policy_version', 'publication_age_buckets', 'observation_lag_buckets',
            'creator_mode', 'niche_feature', 'niche_missing'
        }:
            _reject('invalid_strata_policy', 'Unexpected/missing policy attributes')
        policy = StrataPolicy(
            p['policy_version'],
            tuple(AgeBucket(**b) for b in p['publication_age_buckets']),
            tuple(AgeBucket(**b) for b in p['observation_lag_buckets']),
            p['creator_mode'], p['niche_feature'], p['niche_missing'],
        )
    except (TypeError, KeyError) as exc:
        raise PIEBoundaryError('invalid_strata_policy', 'Invalid stratum design') from exc
    rebuilt = build_candidate_strata(snapshot, policy)
    if rebuilt.as_dict() != payload:
        _reject('strata_replay_mismatch', 'Resealed or mismatched stratum map')
    return rebuilt


def require_one_stratum(partition: CandidateStrata, video_ids: tuple[int, ...]) -> str:
    """Structural membership only; NEVER makes the group statistically valid."""
    if type(partition) is not CandidateStrata or type(video_ids) is not tuple or not video_ids:
        _reject('invalid_strata_selection', 'Nonempty exact group IDs required')
    if any(type(v) is not int for v in video_ids) or tuple(sorted(set(video_ids))) != video_ids:
        _reject('invalid_strata_selection', 'IDs must be unique, ascending canonical ints')
    payload = partition.as_dict()
    if (canonical_hash(payload) != partition.artifact_sha256
        or payload.get('source_snapshot_sha256') != partition.source_snapshot_sha256
        or payload.get('policy_sha256') != partition.policy_sha256
        or payload.get('evidence_state') != 'unverified'):
        _reject('strata_replay_mismatch', 'Stratum artifact was modified')
    strata = payload['strata']
    if len(strata) != partition.stratum_count or any(
        g['key_sha256'] != canonical_hash(g['key']) or
        g['sample_size'] != len(g['members']) for g in strata
    ):
        _reject('strata_replay_mismatch', 'Invalid stratum key or count')
    by_id = {m['video_id']: g['key_sha256'] for g in strata for m in g['members']}
    if (len(by_id) != len(partition.included_video_ids)
        or tuple(sorted(by_id)) != partition.included_video_ids):
        _reject('strata_population_mismatch', 'Membership changed')
    if any(v not in by_id for v in video_ids):
        _reject('unknown_video', 'Video not in frozen cohort')
    keys = {by_id[v] for v in video_ids}
    if len(keys) != 1:
        _reject('incompatible_strata', 'Cross-platform/kind/cohort/age comparison prohibited')
    return next(iter(keys))
