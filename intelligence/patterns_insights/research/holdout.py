"""PIE-MOD-17: pre-discovery creator/media-disjoint temporal holdout (candidate ONLY).

The UAE row contract does not attest media families: an explicit, fully bound
external lineage claim is required and remains UNVERIFIED. Nothing here validates
rights, real preregistration, p-values, or a confirmatory analysis.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import SNAPSHOT_SCHEMA, CandidateResearchSnapshot, replay_candidate_snapshot

SCHEMA = 'pie-creator-temporal-holdout-candidate/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/\\-]{1,160}\\Z')
_MAX_ROWS = 50_000
_MAX_BYTES = 32 * 1024 * 1024


def _fail(code: str, detail: str) -> None:
    raise PIEBoundaryError(code, detail)


def _timestamp(value: object, label: str) -> datetime:
    if type(value) is not str or not 10 <= len(value) <= 40:
        _fail('invalid_holdout_time', f'{label}: explicit ISO timestamp required')
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_holdout_time', f'{label}: malformed') from exc
    if d.tzinfo is None or d.utcoffset() is None:
        _fail('invalid_holdout_time', f'{label}: timezone required')
    return d.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class HoldoutPolicy:
    policy_version: str
    algorithm_version: str
    design_sha256: str
    snapshot_sha256: str
    registered_at: str
    discovery_before: str
    confirmation_not_before: str
    minimum_discovery_rows: int
    minimum_confirmation_rows: int
    minimum_discovery_creators: int
    minimum_confirmation_creators: int
    cross_validation_folds: int
    fold_seed: int
    split_strategy: str = 'strict_time_creator_media_disjoint'
    overlap_action: str = 'quarantine_both_arms'
    lineage_requirement: str = 'all_rows_explicit_pinned_unverified'
    status_semantics: str = 'candidate_only_no_statistical_confirmation'

    def __post_init__(self) -> None:
        if any(type(getattr(self, n)) is not str or not _VERSION.fullmatch(getattr(self, n))
               for n in ('policy_version', 'algorithm_version')):
            _fail('invalid_holdout_policy', 'Explicit policy and algorithm version required')
        if any(type(getattr(self, n)) is not str or not _SHA.fullmatch(getattr(self, n))
               for n in ('design_sha256', 'snapshot_sha256')):
            _fail('invalid_holdout_policy', 'Frozen design and snapshot SHA required')
        registered = _timestamp(self.registered_at, 'registered_at')
        discovery = _timestamp(self.discovery_before, 'discovery_before')
        confirmation = _timestamp(self.confirmation_not_before, 'confirmation_not_before')
        if not discovery < confirmation or registered > confirmation:
            _fail('invalid_holdout_policy', 'Positive embargo and pre-confirmation registration claim required')
        for name in ('minimum_discovery_rows', 'minimum_confirmation_rows',
                     'minimum_discovery_creators', 'minimum_confirmation_creators'):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= _MAX_ROWS:
                _fail('invalid_holdout_policy', 'Explicit positive bounded sample minima required')
        if type(self.cross_validation_folds) is not int or not 2 <= self.cross_validation_folds <= 10:
            _fail('invalid_holdout_policy', 'Explicit 2..10 discovery CV folds required')
        if type(self.fold_seed) is not int or not 0 <= self.fold_seed < 2**64:
            _fail('invalid_holdout_policy', 'Deterministic uint64 seed required')
        if (self.split_strategy != 'strict_time_creator_media_disjoint'
            or self.overlap_action != 'quarantine_both_arms'
            or self.lineage_requirement != 'all_rows_explicit_pinned_unverified'
            or self.status_semantics != 'candidate_only_no_statistical_confirmation'):
            _fail('invalid_holdout_policy', 'Unsupported/unsafe split or production claim')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class HoldoutMediaLineage:
    """Unverified EXTERNAL claim. A checksum is not authoritative media deduplication."""
    video_id: int
    row_sha256: str
    media_family_key: str
    media_revision_key: str
    provenance: str = 'unverified_external_claim'

    def __post_init__(self) -> None:
        if type(self.video_id) is not int or not 0 < self.video_id < 2**63:
            _fail('invalid_holdout_lineage', 'Canonical video ID required')
        if type(self.row_sha256) is not str or not _SHA.fullmatch(self.row_sha256):
            _fail('invalid_holdout_lineage', 'Exact row fingerprint required')
        for name in ('media_family_key', 'media_revision_key'):
            v = getattr(self, name)
            if type(v) is not str or not _VERSION.fullmatch(v):
                _fail('invalid_holdout_lineage', 'Explicit bounded media identity required')
        if self.provenance != 'unverified_external_claim':
            _fail('invalid_holdout_lineage', 'This stage cannot attest external media identity')


@dataclass(frozen=True, slots=True)
class CandidateHoldoutSplit:
    artifact_sha256: str
    project_id: str
    source_snapshot_sha256: str
    policy_sha256: str
    split_status: str
    discovery_video_ids: tuple[int, ...]
    confirmation_video_ids: tuple[int, ...]
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


def _lineage_map(snapshot: CandidateResearchSnapshot,
                 lineage: tuple[HoldoutMediaLineage, ...]) -> dict[int, HoldoutMediaLineage]:
    if type(lineage) is not tuple or not 1 <= len(lineage) <= _MAX_ROWS or any(
        type(x) is not HoldoutMediaLineage for x in lineage
    ):
        _fail('invalid_holdout_lineage', 'Complete immutable typed lineage required')
    ids = tuple(x.video_id for x in lineage)
    if ids != snapshot.included_video_ids:
        _fail('holdout_lineage_population_mismatch', 'Every selected row must have exactly one sorted media claim')
    return {x.video_id: x for x in lineage}


def _components(ids: tuple[int, ...], creators: dict[int, str],
                media: dict[int, str]) -> list[tuple[int, ...]]:
    """Union creator and media family: no shared identity can span discovery CV folds."""
    parent = {creators[i]: creators[i] for i in ids}

    def root(v: str) -> str:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    families: dict[str, str] = {}
    for i in ids:
        creator = creators[i]
        family = media[i]
        if family in families:
            a, b = root(creator), root(families[family])
            if a != b:
                parent[max(a, b)] = min(a, b)
        else:
            families[family] = creator
    components: dict[str, list[int]] = {}
    for i in ids:
        components.setdefault(root(creators[i]), []).append(i)
    return [tuple(sorted(v)) for v in components.values()]


def _compose(snapshot: CandidateResearchSnapshot, policy: HoldoutPolicy,
             lineage: tuple[HoldoutMediaLineage, ...]) -> dict:
    if type(snapshot) is not CandidateResearchSnapshot or type(policy) is not HoldoutPolicy:
        _fail('invalid_holdout_input', 'Exact typed snapshot and policy required')
    rebuilt = replay_candidate_snapshot(snapshot.to_json(), snapshot.snapshot_sha256)
    if rebuilt != snapshot:
        _fail('holdout_source_mismatch', 'Snapshot wrapper is not reproducible')
    source = rebuilt.as_dict()
    if (source.get('schema_version') != SNAPSHOT_SCHEMA
        or source.get('evidence_state') != 'unverified'
        or source['design']['design_sha256'] != policy.design_sha256
        or snapshot.snapshot_sha256 != policy.snapshot_sha256
        or source['source']['pointer']['project_id'] != snapshot.project_id):
        _fail('holdout_source_mismatch', 'Source identity, schema, project or design mismatch')
    if _timestamp(policy.confirmation_not_before, 'confirmation_not_before') > _timestamp(
            source['source']['pointer']['as_of'], 'as_of'):
        _fail('holdout_future_window', 'Confirmation period cannot be after frozen snapshot as-of')
    members = source['selection']['included']
    if len(members) > _MAX_ROWS or tuple(m['video_id'] for m in members) != snapshot.included_video_ids:
        _fail('holdout_source_mismatch', 'Bounded, exact sorted population required')
    links = _lineage_map(snapshot, lineage)
    # A revision cannot silently claim two different underlying media families.
    revision_families: dict[str, str] = {}
    creator: dict[int, str] = {}
    media: dict[int, str] = {}
    candidates = {'discovery': [], 'confirmation': []}
    excluded: dict[int, str] = {}
    end = _timestamp(policy.discovery_before, 'discovery_before')
    start = _timestamp(policy.confirmation_not_before, 'confirmation_not_before')
    for member in members:
        i = member['video_id']
        link = links[i]
        if member['row_sha256'] != link.row_sha256:
            _fail('holdout_lineage_mismatch', 'Media claim belongs to different source row')
        prev = revision_families.setdefault(link.media_revision_key, link.media_family_key)
        if prev != link.media_family_key:
            _fail('holdout_revision_collision', 'One media revision claims multiple families')
        row = member['row']
        key = row['creator_key']
        if type(key) is not str or not key or len(key) > 256:
            _fail('holdout_invalid_creator', 'No authoritative creator identity')
        creator[i], media[i] = key, link.media_family_key
        published = _timestamp(row['published_at'], 'published_at')
        if published < end:
            candidates['discovery'].append(i)
        elif published >= start:
            candidates['confirmation'].append(i)
        else:
            excluded[i] = 'temporal_embargo'
    # Identity leakage means quarantine all implicated records from BOTH arms.
    discovery_creators = {creator[i] for i in candidates['discovery']}
    confirmation_creators = {creator[i] for i in candidates['confirmation']}
    crossing_creators = discovery_creators & confirmation_creators
    for arm in candidates:
        for i in candidates[arm]:
            if creator[i] in crossing_creators:
                excluded[i] = 'creator_cross_arm'
    remaining = {arm: [i for i in ids if i not in excluded]
                 for arm, ids in candidates.items()}
    shared_media = ({media[i] for i in remaining['discovery']}
                    & {media[i] for i in remaining['confirmation']})
    for arm in remaining:
        for i in remaining[arm]:
            if media[i] in shared_media:
                excluded[i] = 'media_family_cross_arm'
    discovery = tuple(sorted(i for i in candidates['discovery'] if i not in excluded))
    confirmation = tuple(sorted(i for i in candidates['confirmation'] if i not in excluded))
    d_creators = {creator[i] for i in discovery}
    c_creators = {creator[i] for i in confirmation}
    if (set(discovery) & set(confirmation) or d_creators & c_creators
        or {media[i] for i in discovery} & {media[i] for i in confirmation}):
        _fail('holdout_leakage', 'Disjoint creator/media/video identity invariant failed')
    if set(discovery) | set(confirmation) | set(excluded) != set(snapshot.included_video_ids):
        _fail('holdout_population_mismatch', 'No selected source rows may vanish')

    components = _components(discovery, creator, media)
    # Deterministic component-to-fold grouping, not post-outcome stratification.
    ranked = sorted(components, key=lambda ids: (
        sha256((str(policy.fold_seed) + ':' + ','.join(sorted({creator[i] for i in ids}))).encode()).hexdigest(),
        ids))
    folded = [[] for _ in range(policy.cross_validation_folds)]
    for index, component in enumerate(ranked):
        folded[index % policy.cross_validation_folds].extend(component)
    folds = [{'fold_index': j, 'video_ids': sorted(values),
              'row_count': len(values), 'creator_count': len({creator[i] for i in values})}
             for j, values in enumerate(folded)]
    if tuple(sorted(i for fold in folds for i in fold['video_ids'])) != discovery:
        _fail('holdout_cv_leakage', 'CV folds do not partition discovery exactly')
    ready = (len(discovery) >= policy.minimum_discovery_rows
             and len(confirmation) >= policy.minimum_confirmation_rows
             and len(d_creators) >= policy.minimum_discovery_creators
             and len(c_creators) >= policy.minimum_confirmation_creators
             and len(components) >= policy.cross_validation_folds)
    return {
        'schema_version': SCHEMA, 'evidence_state': 'unverified',
        'approved_live': False, 'project_id': snapshot.project_id,
        'source_snapshot_sha256': snapshot.snapshot_sha256,
        'source_design_sha256': policy.design_sha256,
        'policy': asdict(policy), 'policy_sha256': policy.policy_sha256,
        'external_media_lineage_sha256': canonical_hash({'lineage': [asdict(x) for x in lineage]}),
        'lineage_provenance': 'unverified_external_claim',
        'split_status': 'candidate_exploratory_only' if ready else 'blocked_insufficient_disjoint_sample',
        'discovery_video_ids': list(discovery), 'confirmation_video_ids': list(confirmation),
        'cross_validation_folds': folds, 'embargo_or_quarantine': [
            {'video_id': i, 'reason_code': excluded[i]} for i in sorted(excluded)],
        'original_excluded_count': len(source['selection']['excluded']),
        'population': {'included': len(members), 'discovery': len(discovery),
                       'confirmation': len(confirmation), 'purged': len(excluded),
                       'discovery_creators': len(d_creators),
                       'confirmation_creators': len(c_creators), 'cv_components': len(components)},
        'leakage_audit': {'shared_video_count': 0, 'shared_creator_count': 0,
                          'shared_media_family_count': 0,
                          'cv_shared_creator_or_media_count': 0,
                          'temporal_embargo_enforced': True,
                          'snapshot_versions_pinned': True,
                          'external_media_identity_authenticated': False,
                          'preregistration_authenticated': False},
        'confirmed_hypotheses': None, 'p_values': None, 'causal_claim': False,
        'warnings': ['not_a_live_qualified_uae_export', 'registration_claim_not_authenticated',
                     'external_media_identity_not_authenticated',
                     'no_downstream_discovery_scope_enforcement_yet',
                     'no_confirmatory_inference_or_statistical_significance'],
    }


def _freeze(payload: dict) -> CandidateHoldoutSplit:
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    if len(raw.encode('utf-8')) > _MAX_BYTES:
        _fail('payload_too_large', 'Holdout report exceeds byte limit')
    return CandidateHoldoutSplit(sha256(raw.encode('utf-8')).hexdigest(), payload['project_id'],
        payload['source_snapshot_sha256'], payload['policy_sha256'], payload['split_status'],
        tuple(payload['discovery_video_ids']), tuple(payload['confirmation_video_ids']), raw)


def build_candidate_holdout_split(snapshot: CandidateResearchSnapshot, policy: HoldoutPolicy,
                                  lineage: tuple[HoldoutMediaLineage, ...]) -> CandidateHoldoutSplit:
    return _freeze(_compose(snapshot, policy, lineage))


def replay_candidate_holdout_split(raw: str | bytes, expected_sha256: str,
                                   snapshot: CandidateResearchSnapshot, policy: HoldoutPolicy,
                                   lineage: tuple[HoldoutMediaLineage, ...]) -> CandidateHoldoutSplit:
    if type(raw) not in (str, bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_holdout_artifact', 'Canonical JSON and SHA required')
    if len(raw if type(raw) is bytes else raw.encode('utf-8')) > _MAX_BYTES:
        _fail('payload_too_large', 'Holdout report exceeds byte limit')

    def unique(pairs: list[tuple[str, object]]) -> dict:
        d = {}
        for k, v in pairs:
            if k in d:
                _fail('duplicate_json_key', 'Duplicate holdout JSON key')
            d[k] = v
        return d

    try:
        data = json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: _fail('invalid_holdout_artifact', 'Nonfinite JSON'))
    except PIEBoundaryError:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise PIEBoundaryError('invalid_holdout_artifact', 'Malformed holdout JSON') from exc
    if type(data) is not dict or canonical_hash(data) != expected_sha256:
        _fail('holdout_hash_mismatch', 'Artifact digest mismatch')
    if data.get('schema_version') != SCHEMA or data.get('approved_live') is not False:
        _fail('unsupported_holdout_artifact', 'Cannot promote or reinterpret candidate')
    expected = build_candidate_holdout_split(snapshot, policy, lineage)
    if data != expected.as_dict() or expected.artifact_sha256 != expected_sha256:
        _fail('holdout_replay_mismatch', 'Source/policy/lineage/reconstruction mismatch')
    return expected


def require_candidate_holdout_scope(split: CandidateHoldoutSplit, *, arm: str,
                                    video_ids: tuple[int, ...]) -> None:
    """Future consumers must pass EXACT IDs; a full-population discovery fails closed."""
    if type(split) is not CandidateHoldoutSplit or type(video_ids) is not tuple:
        _fail('invalid_holdout_scope', 'Immutable split and exact tuple of IDs required')
    if split.split_status != 'candidate_exploratory_only' or arm not in ('discovery', 'confirmation'):
        _fail('holdout_scope_blocked', 'Insufficient evidence or unsupported arm')
    expected = split.discovery_video_ids if arm == 'discovery' else split.confirmation_video_ids
    if video_ids != expected:
        _fail('holdout_scope_mismatch', 'No additions, removals, duplicates or reordering')
