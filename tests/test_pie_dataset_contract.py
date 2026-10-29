"""PIE-MOD-03: structural checks of synthetic row-level manifest, not live UAE."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal
import json
from pathlib import Path
import unittest

from intelligence.patterns_insights import PIEBoundaryError, PIEDatasetPointer, canonical_hash
from intelligence.patterns_insights.dataset_contract import (
    MANIFEST_SCHEMA, DATASET_SCHEMA, validate_dataset_manifest, load_dataset_json,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'pie'


def fixture(name='uae_to_pie_v1_positive.json'):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def from_fixture(payload):
    return PIEDatasetPointer(**payload['pointer']), payload['manifest']


def reseal(data):
    """Change hashes to isolate a semantic failure; NOT trusted attestation."""
    p, m = data['pointer'], data['manifest']
    for r in m['observations']:
        r['features_sha256'] = canonical_hash({'a': r['features_a'], 'b':r['features_b']})
    p['uae_dataset_sha256'] = canonical_hash({'schema_version':DATASET_SCHEMA,'observations':m['observations']})
    p['uae_manifest_sha256'] = canonical_hash(m)
    return PIEDatasetPointer(**p), m


class PIEDatasetContractTests(unittest.TestCase):
    def assert_invalid(self, data, expected, *, resealed=True):
        if resealed:
            pointer, manifest = reseal(data)
        else:
            pointer, manifest = from_fixture(data)
        with self.assertRaises(PIEBoundaryError) as ctx:
            validate_dataset_manifest(pointer, manifest)
        self.assertEqual(ctx.exception.code, expected)

    def test_accepts_exact_versioned_synthetic_rows_and_raw_unbounded_c7(self):
        p, m = from_fixture(fixture())
        result = validate_dataset_manifest(p, m)
        self.assertFalse(result.verified_live)
        self.assertEqual(result.sample_size, 2)
        self.assertEqual(result.scanned_count, 3)
        self.assertEqual(result.excluded_video_ids, (103,))
        self.assertEqual(tuple(x.video_id for x in result.observations), (101, 102))
        self.assertEqual(result.observations[0].score, Decimal('250.50'))
        self.assertEqual(result.observations[1].score, Decimal('-3.25'))
        self.assertEqual(result.manifest_sha256, p.uae_manifest_sha256)
        self.assertEqual(result.dataset_sha256, p.uae_dataset_sha256)
        self.assertEqual(result, validate_dataset_manifest(p, deepcopy(m)))
        self.assertEqual(load_dataset_json(p, json.dumps(m)), result)
        self.assertEqual(load_dataset_json(p, json.dumps(m).encode('utf-8')), result)

    def test_no_mutable_alias_or_implicit_evidence_verification(self):
        p, m = from_fixture(fixture())
        sample = validate_dataset_manifest(p, m)
        initial = sample.observations[0].as_dict()
        m['observations'][0]['breakout_score'] = '999999'
        initial['breakout_score'] = '-12345'
        sample.as_dict()['observations'][0]['breakout_score'] = '33333'
        self.assertEqual(sample.observations[0].as_dict()['breakout_score'], '250.50')
        self.assertEqual(sample.sample_size, 2)
        with self.assertRaises(FrozenInstanceError):
            sample.scanned_count = 4
        self.assertFalse(hasattr(sample, 'authorized'))

    def test_manifest_digest_failure_after_tampering(self):
        data = fixture()
        data['manifest']['selection']['rule_version'] = 'changed/v1'
        self.assert_invalid(data, 'manifest_hash_mismatch', resealed=False)

    def test_dataset_digest_failure_even_if_manifest_digest_updated(self):
        data = fixture()
        data['manifest']['observations'][0]['breakout_score'] = '9.00'
        data['pointer']['uae_manifest_sha256'] = canonical_hash(data['manifest'])
        self.assert_invalid(data, 'dataset_hash_mismatch', resealed=False)

    def test_summary_not_row_level_even_if_pointers_exist(self):
        data = fixture('uae_to_pie_v1_aggregate_rejected.json')
        self.assert_invalid(data, 'invalid_manifest_shape', resealed=False)

    def test_duplicate_included_video_and_exclusion_overlap(self):
        for kind in ('duplicate_included', 'overlap_excluded', 'duplicate_excluded', 'unsorted'):
            data = fixture()
            if kind == 'duplicate_included':
                data['manifest']['observations'][1]['video_id'] = 101
            elif kind == 'overlap_excluded':
                data['manifest']['selection']['exclusions'][0]['video_id'] = 102
            elif kind == 'duplicate_excluded':
                data['manifest']['selection']['exclusions'].append({'video_id':103,'reason_code':'duplicate'})
                data['manifest']['selection']['scanned_count'] += 1
                data['manifest']['selection']['excluded_count'] += 1
            else:
                data['manifest']['observations'].reverse()
            with self.subTest(kind=kind):
                self.assert_invalid(data, 'duplicate_video')

    def test_duplicate_snapshot_fails_even_with_distinct_video(self):
        data = fixture()
        data['manifest']['observations'][1]['uae_snapshot_id'] = 1101
        self.assert_invalid(data, 'duplicate_snapshot')

    def test_count_forgery_never_creates_fake_sample(self):
        for key, value in [('included_count',2000),('scanned_count',2000),('excluded_count',0),('included_count',True)]:
            data = fixture()
            data['manifest']['selection'][key] = value
            with self.subTest(name=key, value=value):
                self.assert_invalid(data, 'invalid_count' if type(value) is bool else 'sample_count_mismatch')

    def test_cross_project_source_and_policy_refused(self):
        for field, value in [('project_id', 'b'*32),('source_id','other'),('source_policy_version_id',8)]:
            data = fixture()
            data['manifest']['observations'][0][field] = value
            with self.subTest(field=field):
                self.assert_invalid(data,'scope_mismatch')

    def test_pointer_scope_mismatch_even_on_matching_rows(self):
        for field, value in [('project_id','c'*32),('source_id','other'),('source_metric_version','different'),('as_of','2026-10-26T11:00:00Z'),('calculation_config_id',99)]:
            data = fixture()
            data['pointer'][field] = value
            with self.subTest(field=field):
                self.assert_invalid(data,'scope_mismatch',resealed=False)

    def test_uae_engine_and_taxonomy_version_mismatch(self):
        data = fixture()
        data['manifest']['producer']['engine_version'] = 'uae-other/v1'
        self.assert_invalid(data,'version_mismatch')
        for field in ('group_a_version','group_b_version','group_b_taxonomy_version','sre_taxonomy_version','creator_cohort_version','prior_cohort_version','niche_cohort_version'):
            data = fixture()
            data['manifest']['observations'][0][field] = 'wrong-v1'
            with self.subTest(field=field):
                self.assert_invalid(data,'version_mismatch')

    def test_zero_unknown_and_unsupported_content_rejected(self):
        changes = [('video_id', 0, 'invalid_identifier'),('metric_snapshot_id',False,'invalid_identifier'),('content_kind','image_post','unsupported_kind'),('content_kind','other','unsupported_kind'),('status','partial','unqualified_input'),('score_status','pending','unqualified_input'),('qdp_run_id','no-uuid','invalid_uuid'),('qdp_run_id','0E1E0DBF-1EE0-46E2-A003-CED18C68B201','invalid_uuid')]
        for k,v,error in changes:
            data=fixture()
            data['manifest']['observations'][0][k]=v
            with self.subTest(k=k,v=v):
                self.assert_invalid(data,error)

    def test_nonfinite_numeric_strings_and_implicit_c7_scaling_rejected(self):
        for score in (None, 125, float('nan'), 'NaN', 'Infinity','-Infinity','1e9','1.2.3','not-a-score'):
            data = fixture()
            data['manifest']['observations'][0]['breakout_score'] = score
            with self.subTest(score=str(score)):
                # json canonical_hash refuses NaN before the validator sees it
                if type(score) is float:
                    with self.assertRaises(PIEBoundaryError):
                        reseal(data)
                else:
                    self.assert_invalid(data,'invalid_score')

    def test_feature_hash_catches_silent_mutation(self):
        data = fixture()
        data['manifest']['observations'][0]['features_b']['cta_has_cta'] = True
        data['pointer']['uae_dataset_sha256'] = canonical_hash({'schema_version':DATASET_SCHEMA,'observations':data['manifest']['observations']})
        data['pointer']['uae_manifest_sha256'] = canonical_hash(data['manifest'])
        self.assert_invalid(data,'feature_hash_mismatch',resealed=False)

    def test_feature_null_false_empty_sequence_have_distinct_meanings(self):
        data=fixture()
        p,m=from_fixture(data)
        first=m['observations'][0]['features_b']
        second=m['observations'][1]['features_b']
        self.assertIs(first['cta_has_cta'],False)
        self.assertIsNone(second['cta_has_cta'])
        self.assertEqual(second['hook_mechanisms'], [])
        self.assertEqual(first['story_roles'], ['hook','proof','proof'])
        self.assertIsNotNone(validate_dataset_manifest(p,m))

    def test_float_nested_dict_and_feature_length_refused(self):
        for value in (1.5,{'nested':'bad'},['ok', 1], ['x']*65, 'x'*251):
            data=fixture()
            data['manifest']['observations'][0]['features_a']['speech_wpm']=value
            with self.subTest(value=str(value)[:25]):
                self.assert_invalid(data,'invalid_features')

    def test_invalid_temporal_order_and_naive_or_nonsense_times(self):
        for field,value,err in [
            ('observed_at','2026-10-27T01:00:00Z','temporal_mismatch'),
            ('published_at','2026-10-26T08:00:00Z','temporal_mismatch'),
            ('observed_at','2026-10-25T01:00:00','invalid_timestamp'),
            ('observed_at','yesterday','invalid_timestamp'),
        ]:
            data=fixture()
            data['manifest']['observations'][0][field]=value
            with self.subTest(field=field,value=value):
                self.assert_invalid(data,err)

    def test_unknown_keys_and_wrong_manifest_schema_rejected(self):
        for target,new_key in [('manifest','extra'),('row','bad_field'),('selection','bonus')]:
            data=fixture()
            node={'manifest':data['manifest'],'row':data['manifest']['observations'][0],
                  'selection':data['manifest']['selection']}[target]
            node[new_key]=1
            with self.subTest(target=target):
                self.assert_invalid(data,'invalid_manifest_shape')
        data=fixture()
        data['manifest']['schema_version']='uae-pattern-handoff/v1'
        self.assert_invalid(data,'unsupported_schema')

    def test_json_duplicate_keys_and_special_constants_are_blocked(self):
        p,m=from_fixture(fixture())
        body=json.dumps(m)
        for malformed,err in [('{"a": 1, "a": 2}','duplicate_json_key'),(body.replace('"breakout_score": "250.50"','"breakout_score": NaN'), 'invalid_json'),('{','invalid_json')]:
            with self.subTest(err=err):
                with self.assertRaises(PIEBoundaryError) as ctx:
                    load_dataset_json(p,malformed)
                self.assertEqual(ctx.exception.code,err)

    def test_json_wrong_type_unicode_and_max_transport_limit(self):
        p,m=from_fixture(fixture())
        for bad,err in [(None,'invalid_json'),(b'\xff','invalid_json'),('x'* (64*1024*1024+1),'payload_too_large')]:
            with self.subTest(err=err):
                with self.assertRaises(PIEBoundaryError) as ctx:
                    load_dataset_json(p,bad)
                self.assertEqual(ctx.exception.code,err)

    def test_invalid_scope_version_replayed_against_different_pointer(self):
        p,m=from_fixture(fixture())
        self.assertEqual(m['schema_version'],MANIFEST_SCHEMA)
        altered=deepcopy(m)
        altered['selection']['rule_version']='new-selection/v2'
        with self.assertRaises(PIEBoundaryError) as ctx:
            validate_dataset_manifest(p,altered)
        self.assertEqual(ctx.exception.code,'manifest_hash_mismatch')


if __name__ == '__main__':
    unittest.main()
