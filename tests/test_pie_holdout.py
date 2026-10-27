"""PIE-MOD-17 isolated synthetic regression tests (upstream stage07 replay MOCKED)."""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.dataset_builder import (
    SNAPSHOT_SCHEMA, CandidateResearchSnapshot,
)
from intelligence.patterns_insights.research.holdout import (
    HoldoutPolicy, HoldoutMediaLineage, build_candidate_holdout_split,
    replay_candidate_holdout_split, require_candidate_holdout_scope, require_candidate_cv_scope,
)

PROJECT = 'a' * 32
DESIGN = 'b' * 64
EARLY = '2026-10-10T12:00:00Z'
LATE = '2026-10-21T12:00:00Z'
EMBARGO = '2026-10-16T12:00:00Z'
# Values/creators/media below are invented; no real UAE source or review.
ROWS = (
    (1, 'author_a', EARLY, 'family_1'),
    (2, 'author_b', EARLY, 'family_2'),
    (3, 'author_c', EARLY, 'family_3'),
    (4, 'author_d', EARLY, 'family_4'),
    (5, 'author_e', LATE, 'family_5'),
    (6, 'author_f', LATE, 'family_6'),
    (7, 'author_g', LATE, 'family_7'),
    (8, 'author_h', LATE, 'family_8'),
    (9, 'author_a', LATE, 'family_9'),     # same creator in both arms
    (10, 'author_z', EMBARGO, 'family_10'), # mandatory embargo
    (11, 'author_i', EARLY, 'family_11'),
    (12, 'author_j', LATE, 'family_11'),   # cross-creator repost family
    (13, 'author_k', EARLY, 'family_2'),  # connected CV identity
)


def fixture(rows=ROWS):
    members = [{
        'video_id': i, 'row_sha256': f'{i:064x}', 'uae_snapshot_id': 100 + i,
        'row': {'creator_key': creator, 'published_at': published},
    } for i, creator, published, _ in sorted(rows)]
    payload = {
        'schema_version': SNAPSHOT_SCHEMA, 'evidence_state': 'unverified',
        'design': {'design_sha256': DESIGN},
        'source': {'pointer': {'project_id': PROJECT, 'as_of': '2026-10-27T00:00:00Z'}},
        'selection': {'included': members, 'excluded': []},
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    snap = CandidateResearchSnapshot(canonical_hash(payload), PROJECT, 'c'*64, 'd'*64,
                                     tuple(x['video_id'] for x in members), (), len(members), 'e'*64, raw)
    lineage = tuple(HoldoutMediaLineage(i, f'{i:064x}', family, f'revision_{i}')
                    for i, _, _, family in sorted(rows))
    policy = HoldoutPolicy(
        'holdout-policy-v1', 'creator-media-unionfold-v1', DESIGN, snap.snapshot_sha256,
        '2026-10-14T00:00:00Z', '2026-10-15T00:00:00Z',
        '2026-10-18T00:00:00Z', 3, 3, 2, 2, 2, 7127,
    )
    return snap, policy, lineage


class HoldoutSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.policy, self.lineage = fixture()

    def build(self, snapshot=None, policy=None, lineage=None):
        snapshot = self.snapshot if snapshot is None else snapshot
        policy = self.policy if policy is None else policy
        lineage = self.lineage if lineage is None else lineage
        with patch('intelligence.patterns_insights.research.holdout.replay_candidate_snapshot',
                   return_value=snapshot):
            return build_candidate_holdout_split(snapshot, policy, lineage)

    def replay(self, raw, sha, snapshot=None, policy=None, lineage=None):
        snapshot = self.snapshot if snapshot is None else snapshot
        policy = self.policy if policy is None else policy
        lineage = self.lineage if lineage is None else lineage
        with patch('intelligence.patterns_insights.research.holdout.replay_candidate_snapshot',
                   return_value=snapshot):
            return replay_candidate_holdout_split(raw, sha, snapshot, policy, lineage)

    def fail_code(self, code, fn, *args, **kwargs):
        with self.assertRaises(PIEBoundaryError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    def test_hand_calculated_temporal_quarantine_and_reconciliation(self):
        out = self.build()
        d = out.as_dict()
        self.assertEqual(out.discovery_video_ids, (2, 3, 4, 13))
        self.assertEqual(out.confirmation_video_ids, (5, 6, 7, 8))
        self.assertEqual({v['video_id']: v['reason_code'] for v in d['embargo_or_quarantine']},
                         {1: 'creator_cross_arm', 9: 'creator_cross_arm',
                          10: 'temporal_embargo', 11: 'media_family_cross_arm',
                          12: 'media_family_cross_arm'})
        self.assertEqual(d['population']['included'],
                         d['population']['discovery'] + d['population']['confirmation']
                         + d['population']['purged'])
        self.assertEqual(d['split_status'], 'candidate_exploratory_only')
        self.assertFalse(out.verified_live)
        self.assertFalse(out.ready_for_analysis)
        self.assertFalse(d['leakage_audit']['external_media_identity_authenticated'])
        self.assertIsNone(d['confirmed_hypotheses'])

    def test_creator_and_media_connected_components_stay_in_one_fold(self):
        folds = self.build().as_dict()['cross_validation_folds']
        self.assertEqual(len(folds), 2)
        self.assertEqual(sorted(i for f in folds for i in f['video_ids']), [2, 3, 4, 13])
        owner = {i: f['fold_index'] for f in folds for i in f['video_ids']}
        self.assertEqual(owner[2], owner[13])
        self.assertTrue(all(f['row_count'] > 0 for f in folds))

    def test_determinism_seed_affects_assignment_not_population(self):
        a = self.build()
        self.assertEqual(a.to_json(), self.build().to_json())
        self.assertEqual(a.artifact_sha256, self.build().artifact_sha256)
        b = self.build(policy=replace(self.policy, fold_seed=7159))
        self.assertEqual(a.discovery_video_ids, b.discovery_video_ids)
        self.assertEqual(a.confirmation_video_ids, b.confirmation_video_ids)
        self.assertNotEqual(a.policy_sha256, b.policy_sha256)
        self.assertNotEqual(a.artifact_sha256, b.artifact_sha256)

    def test_byte_exact_replay_and_rehashed_forgery(self):
        a = self.build()
        self.assertEqual(self.replay(a.to_json(), a.artifact_sha256), a)
        fake = a.as_dict()
        fake['leakage_audit']['shared_creator_count'] = 5
        self.fail_code('holdout_replay_mismatch', self.replay,
                       json.dumps(fake), canonical_hash(fake))

    def test_replay_rejects_new_policy_and_new_lineage(self):
        a = self.build()
        self.fail_code('holdout_replay_mismatch', self.replay,
                       a.to_json(), a.artifact_sha256,
                       policy=replace(self.policy, fold_seed=4))
        altered = list(self.lineage)
        altered[1] = replace(altered[1], media_family_key='another_family')
        self.fail_code('holdout_replay_mismatch', self.replay,
                       a.to_json(), a.artifact_sha256, lineage=tuple(altered))

    def test_all_media_rows_required_no_reordering(self):
        self.fail_code('holdout_lineage_population_mismatch', self.build,
                       lineage=self.lineage[:-1])
        self.fail_code('holdout_lineage_population_mismatch', self.build,
                       lineage=tuple(reversed(self.lineage)))
        self.fail_code('holdout_lineage_population_mismatch', self.build,
                       lineage=self.lineage + self.lineage[:1])

    def test_changed_row_sha_and_conflicting_revision_are_refused(self):
        altered = list(self.lineage)
        altered[0] = replace(altered[0], row_sha256='f'*64)
        self.fail_code('holdout_lineage_mismatch', self.build, lineage=tuple(altered))
        altered = list(self.lineage)
        altered[5] = replace(altered[5], media_revision_key=altered[0].media_revision_key)
        self.fail_code('holdout_revision_collision', self.build, lineage=tuple(altered))

    def test_lineage_and_policy_reject_fake_live_attestation(self):
        self.fail_code('invalid_holdout_lineage', replace, self.lineage[0],
                       provenance='attested_production')
        self.fail_code('invalid_holdout_policy', replace, self.policy,
                       status_semantics='production_ready')
        self.fail_code('invalid_holdout_policy', replace, self.policy,
                       overlap_action='keep_train')

    def test_timestamp_embargo_and_registration_guard(self):
        self.fail_code('invalid_holdout_policy', replace, self.policy,
                       discovery_before='2026-10-18T00:00:00Z')
        self.fail_code('invalid_holdout_policy', replace, self.policy,
                       registered_at='2026-10-19T00:00:00Z')
        self.fail_code('invalid_holdout_time', replace, self.policy,
                       confirmation_not_before='2026-10-18T00:00:00')
        self.fail_code('holdout_future_window', self.build,
                       policy=replace(self.policy, confirmation_not_before='2026-10-28T00:00:00Z'))

    def test_minima_fail_closed_without_cherry_picking(self):
        too_strict = replace(self.policy, minimum_discovery_rows=8)
        a = self.build(policy=too_strict)
        self.assertEqual(a.split_status, 'blocked_insufficient_disjoint_sample')
        self.assertEqual(a.discovery_video_ids, (2, 3, 4, 13))
        self.fail_code('holdout_scope_blocked', require_candidate_holdout_scope,
                       a, arm='discovery', video_ids=a.discovery_video_ids)
        more_folds = replace(self.policy, cross_validation_folds=5)
        self.assertEqual(self.build(policy=more_folds).split_status,
                         'blocked_insufficient_disjoint_sample')

    def test_exact_fold_scope_blocks_full_population_and_leaks(self):
        s = self.build()
        require_candidate_holdout_scope(s, arm='discovery', video_ids=(2, 3, 4, 13))
        require_candidate_holdout_scope(s, arm='confirmation', video_ids=(5, 6, 7, 8))
        self.fail_code('holdout_scope_mismatch', require_candidate_holdout_scope,
                       s, arm='discovery', video_ids=self.snapshot.included_video_ids)
        self.fail_code('holdout_scope_mismatch', require_candidate_holdout_scope,
                       s, arm='discovery', video_ids=(2, 3, 4))
        self.fail_code('holdout_scope_blocked', require_candidate_holdout_scope,
                       s, arm='all', video_ids=(2, 3, 4, 13))

    def test_cv_train_validation_scope_is_exclusive(self):
        split = self.build()
        folds = split.as_dict()['cross_validation_folds']
        for fold in folds:
            j = fold['fold_index']
            test_ids = tuple(fold['video_ids'])
            train_ids = tuple(sorted(i for other in folds if other['fold_index'] != j
                                     for i in other['video_ids']))
            require_candidate_cv_scope(split, validation_fold=j,
                                       training_video_ids=train_ids, validation_video_ids=test_ids)
            self.fail_code('holdout_cv_scope_mismatch', require_candidate_cv_scope,
                           split, validation_fold=j,
                           training_video_ids=split.discovery_video_ids,
                           validation_video_ids=test_ids)
            self.fail_code('holdout_cv_scope_mismatch', require_candidate_cv_scope,
                           split, validation_fold=j,
                           training_video_ids=train_ids,
                           validation_video_ids=split.confirmation_video_ids)
        self.fail_code('holdout_cv_scope_blocked', require_candidate_cv_scope,
                       split, validation_fold=True, training_video_ids=(), validation_video_ids=())

    def test_empty_confirmation_blocks_not_pseudo_confirmed(self):
        rows = tuple((i, creator, EARLY, family) for i, creator, _, family in ROWS)
        snap, policy, linkage = fixture(rows)
        policy = replace(policy, snapshot_sha256=snap.snapshot_sha256)
        a = self.build(snap, policy, linkage)
        self.assertEqual(a.split_status, 'blocked_insufficient_disjoint_sample')
        self.assertEqual(a.confirmation_video_ids, ())
        self.assertFalse(a.ready_for_analysis)

    def test_invalid_artifact_replay_duplicate_keys_nan_or_fake_live(self):
        a = self.build()
        self.fail_code('holdout_hash_mismatch', self.replay, a.to_json(), 'f'*64)
        self.fail_code('duplicate_json_key', self.replay,
                       '{"x":1,"x":2}', '0'*64)
        self.fail_code('invalid_holdout_artifact', self.replay, '{"x":NaN}', '0'*64)
        fake = a.as_dict()
        fake['approved_live'] = True
        self.fail_code('unsupported_holdout_artifact', self.replay,
                       json.dumps(fake), canonical_hash(fake))

    def test_source_wrapper_or_design_mismatch(self):
        foreign = replace(self.snapshot, project_id='f'*32)
        with patch('intelligence.patterns_insights.research.holdout.replay_candidate_snapshot',
                   return_value=foreign):
            self.fail_code('holdout_source_mismatch', build_candidate_holdout_split,
                           self.snapshot, self.policy, self.lineage)
        self.fail_code('holdout_source_mismatch', self.build,
                       policy=replace(self.policy, design_sha256='f'*64))

    def test_boolean_numeric_parameters_invalid(self):
        self.fail_code('invalid_holdout_policy', replace, self.policy,
                       fold_seed=True)
        self.fail_code('invalid_holdout_policy', replace, self.policy,
                       minimum_confirmation_creators=True)
        self.fail_code('invalid_holdout_policy', replace, self.policy,
                       cross_validation_folds=11)
        self.fail_code('invalid_holdout_lineage', HoldoutMediaLineage,
                       True, 'a'*64, 'media', 'revision')


if __name__ == '__main__':
    unittest.main()
