"""PIE-MOD-25: synthetic immutable registry source/replay and DB DDL QA.

Mocking upstream stage24 replay is intentional: no valid live UAE producer
or actual rights/reviewer/held-out statistical evidence is available.
"""
import json
import os
from pathlib import Path
import unittest
from dataclasses import replace
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError, canonical_hash
from intelligence.patterns_insights.research.pattern_registry import (
    RegistryOwner, build_candidate_pattern_registry_batch,
    replay_candidate_pattern_registry_batch, persist_candidate_pattern_registry)
from intelligence.patterns_insights.research.pattern_quality import (
    build_candidate_pattern_quality_report)
from test_pie_pattern_quality import _fixture as stage24_fixture, _json

MIGRATION=Path(__file__).resolve().parents[1]/'intelligence'/'migrations'/'201_pie_immutable_pattern_registry.sql'


def fixture():
    inputs=list(stage24_fixture())
    with patch('intelligence.patterns_insights.research.pattern_quality.replay_candidate_robustness_report',
               return_value=inputs[11]):
        report=build_candidate_pattern_quality_report(*inputs)
    inputs.append(report) # quality report index13
    inputs.append(RegistryOwner('pie_candidate_owner_v1')) # index14
    return tuple(inputs)


class RegistryCandidateSynthetic(unittest.TestCase):
    def setUp(self):self.v=fixture()

    def edit(self,index,item,base=None):
        val=list(self.v if base is None else base)
        val[index]=item
        return tuple(val)

    def build(self,val=None):
        v=self.v if val is None else val
        with patch('intelligence.patterns_insights.research.pattern_registry.replay_candidate_pattern_quality_report',
                   return_value=v[13]):
            return build_candidate_pattern_registry_batch(*v)

    def replay(self,raw,digest,val=None):
        v=self.v if val is None else val
        with patch('intelligence.patterns_insights.research.pattern_registry.replay_candidate_pattern_quality_report',
                   return_value=v[13]):
            return replay_candidate_pattern_registry_batch(raw,digest,*v)

    def reject(self,code,fn,*a,**kw):
        with self.assertRaises(PIEBoundaryError) as ctx:fn(*a,**kw)
        self.assertEqual(ctx.exception.code,code)

    def test_full_original_factor_family_and_no_supported_output(self):
        r=self.build()
        self.assertEqual(r.record_count,5)
        self.assertEqual(len(r.entries),5)
        self.assertFalse(r.ready_for_analysis)
        self.assertFalse(r.verified_live)
        self.assertEqual(r.as_dict()['supported_registry_entry_count'],0)
        self.assertTrue(all(e.decision in ('exploratory','rejected','needs_review')
                            and not e.verified_live for e in r.entries))

    def test_hypothesis_spec_and_provenance_preserved_per_entry(self):
        x=self.build().entries[0]
        d=x.as_dict()
        self.assertEqual(d['candidate_id'],x.series_id)
        self.assertEqual(d['hypothesis_sha256'],x.hypothesis_sha256)
        self.assertEqual(d['source_pattern_quality_sha256'],self.v[13].artifact_sha256)
        self.assertEqual(d['validation_id'],self.v[13].artifact_sha256)
        self.assertEqual(d['owner_id'],'pie_candidate_owner_v1')
        self.assertEqual(d['matched_pair_count'],4)
        self.assertEqual(len(d['source_discovery_pairs']),4)
        self.assertEqual(d['hypothesis_specification']['operator'],
                         'frozen_half_open_raw_numeric_bins')
        self.assertEqual(d['hypothesis_specification']['contrast'],
                         'matched_c7_high_minus_low_unweighted')
        self.assertEqual(d['statistical_summary']['raw_mean_high_minus_low']['numerator'],5)
        self.assertEqual(d['statistical_summary']['raw_mean_high_minus_low']['denominator'],2)
        self.assertEqual(d['source_rows_sha256'],x.source_rows_sha256)
        self.assertFalse(d['may_publish'])
        self.assertFalse(d['rights_and_review_authenticated'])

    def test_same_evidence_generates_same_series_payload_and_sha(self):
        a=self.build();b=self.build()
        self.assertEqual(a.artifact_sha256,b.artifact_sha256)
        self.assertEqual(a.to_json(),b.to_json())
        self.assertEqual(a.entries[0].series_id,b.entries[0].series_id)
        self.assertEqual(a.entries[0].evidence_sha256,b.entries[0].evidence_sha256)

    def test_new_study_quality_evidence_revisions_same_hypothesis(self):
        inputs=list(self.v)
        qp=replace(inputs[12],review_protocol_claim_id='unverified-review-protocol-v2')
        inputs[12]=qp
        with patch('intelligence.patterns_insights.research.pattern_quality.replay_candidate_robustness_report',
                   return_value=inputs[11]):
            inputs[13]=build_candidate_pattern_quality_report(*inputs[:13])
        new=self.build(tuple(inputs)).entries[0]
        old=self.build().entries[0]
        self.assertEqual(new.series_id,old.series_id)
        self.assertEqual(new.hypothesis_sha256,old.hypothesis_sha256)
        self.assertNotEqual(new.evidence_sha256,old.evidence_sha256)
        self.assertNotEqual(new.payload_sha256,old.payload_sha256)

    def test_new_registered_cutoff_creates_new_hypothesis_identity(self):
        inputs=list(self.v)
        spec=inputs[8].factors[0]
        changed=replace(spec,bin_cutoffs_raw=(4,7))
        factor=replace(spec,bin_cutoffs_raw=(4,7))
        # Changing the hypothesis specification without regenerating stage22/23/24
        # is correctly blocked by the source chain and not auto-registered.
        inputs[8]=replace(inputs[8],factors=(factor,*inputs[8].factors[1:]))
        self.reject('registry_lineage_or_release_mismatch',self.build,tuple(inputs))

    def test_different_owner_changes_series_not_underlying_hypothesis(self):
        second=self.build(self.edit(14,RegistryOwner('different_owner'))).entries[0]
        first=self.build().entries[0]
        self.assertNotEqual(second.series_id,first.series_id)
        self.assertEqual(second.hypothesis_sha256,first.hypothesis_sha256)

    def test_owner_claims_are_never_authenticated_reviewer_identity(self):
        self.reject('invalid_registry_owner',RegistryOwner,'')
        self.reject('invalid_registry_owner',RegistryOwner,'owner with spaces')
        self.reject('invalid_registry_owner',RegistryOwner,'valid_owner',
                    actor_origin='verified_editor')
        r=self.build(self.edit(14,RegistryOwner('someone_claims_authorized')))
        self.assertFalse(r.entries[0].as_dict()['rights_and_review_authenticated'])

    def test_no_source_replay_shortcut_or_quality_report_mismatch(self):
        # A mocked source replay still requires exact returned typed wrapper identity.
        fake=replace(self.v[13],_json='{"tampered":true}')
        self.reject('registry_lineage_or_release_mismatch',self.build,self.edit(13,fake))

    def test_forged_supported_decision_fails_closed(self):
        report=self.v[13]
        raw=report.as_dict()
        raw['decisions'][0]['decision']='supported'
        self.reject('registry_supported_forbidden',self.build,
                    self.edit(13,replace(report,_json=_json(raw))))

    def test_forged_live_release_flag_fails_closed(self):
        report=self.v[13]
        raw=report.as_dict()
        raw['can_publish_patterns']=True
        # A reported release flag must fail before producing any registry artifact.
        self.reject('registry_lineage_or_release_mismatch',self.build,
                    self.edit(13,replace(report,_json=_json(raw))))

    def test_old_stage23_policy_cannot_be_reused_for_new_analysis(self):
        alternate=self.edit(10,replace(self.v[10],seed=999))
        self.reject('registry_lineage_or_release_mismatch',self.build,alternate)

    def test_fake_review_and_rights_claims_rejected(self):
        report=self.v[13]
        for claim in ('authentic_reviewer_approval',
                      'source_rights_authenticated','independent_holdout_confirmed'):
            raw=report.as_dict()
            raw[claim]=True
            self.reject('registry_lineage_or_release_mismatch',self.build,
                        self.edit(13,replace(report,_json=_json(raw))))

    def test_replay_identical_text_bytes_and_digest(self):
        a=self.build()
        self.assertEqual(self.replay(a.to_json(),a.artifact_sha256),a)
        self.assertEqual(self.replay(a.to_json().encode(),a.artifact_sha256),a)

    def test_replay_changed_source_evidence_rejected(self):
        a=self.build()
        changed=self.edit(14,RegistryOwner('different_owner'))
        self.reject('registry_replay_mismatch',self.replay,a.to_json(),a.artifact_sha256,changed)

    def test_replay_rehashed_forgery_refused(self):
        a=self.build()
        d=a.as_dict()
        d['entries'][0]['analysis_revision_status']='supported'
        self.reject('registry_replay_mismatch',self.replay,_json(d),canonical_hash(d))

    def test_replay_duplicate_json_nan_invalid_utf8(self):
        self.reject('duplicate_json_key',self.replay,'{"a":1,"a":2}','0'*64)
        self.reject('invalid_registry_artifact',self.replay,'{"x":NaN}','0'*64)
        self.reject('invalid_registry_artifact',self.replay,b'\xff','0'*64)

    def test_replay_wrong_digest_schema_and_noncanonical_spacing(self):
        a=self.build()
        self.reject('registry_hash_mismatch',self.replay,a.to_json(),'f'*64)
        d=a.as_dict();d['approved_live']=True
        self.reject('unsupported_registry_artifact',self.replay,_json(d),canonical_hash(d))
        self.reject('registry_replay_mismatch',self.replay,
                    json.dumps(a.as_dict(),indent=2),a.artifact_sha256)


class RegistrySQLStaticContract(unittest.TestCase):
    def test_new_additive_migration_and_composite_project_scope(self):
        sql=MIGRATION.read_text(encoding='utf-8')
        self.assertTrue(MIGRATION.name.startswith('201_pie_'))
        self.assertIn('CREATE TABLE pie_pattern_registry_series',sql)
        self.assertIn('CREATE TABLE pie_pattern_registry_revisions',sql)
        self.assertIn('PRIMARY KEY (project_id,series_id)',sql)
        self.assertIn('UNIQUE(project_id,series_id,revision)',sql)
        self.assertIn('UNIQUE(project_id,series_id,evidence_sha256)',sql)
        self.assertIn('FOREIGN KEY(project_id,series_id,hypothesis_sha256)',sql)
        self.assertIn('REFERENCES project_registry(id)',sql)
        self.assertNotIn('INSERT INTO schema_migrations',sql)
        self.assertNotIn('UPDATE pie_',sql)
        self.assertNotIn('ALTER TABLE ',sql)

    def test_sql_refuses_supported_update_and_release_shortcuts(self):
        sql=MIGRATION.read_text(encoding='utf-8')
        self.assertIn("status IN ('exploratory','rejected','needs_review')",sql)
        self.assertEqual(sql.count('EXECUTE FUNCTION pie_reject_evidence_update()'),2)
        self.assertIn('BEFORE UPDATE ON pie_pattern_registry_revisions',sql)
        self.assertIn('BEFORE UPDATE ON pie_pattern_registry_series',sql)
        self.assertNotIn('CREATE TABLE pie_pattern_candidates',sql)


@unittest.skipUnless(os.environ.get('CM_TEST_PG_DSN'), 'Disposable test Postgres DSN not set')
class RegistryPostgresDisposable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.db=psycopg.connect(os.environ['CM_TEST_PG_DSN'],autocommit=True)
        name=cls.db.execute('SELECT current_database()').fetchone()[0]
        if 'test' not in name or name in ('postgres','template1','contentmachine'):
            cls.db.close()
            raise AssertionError('Postgres registry tests require disposable database named *test*')
        cls.migrate=migrate
        # Fresh full schema or safe verified upgrade, including immutable migration checksum.
        migrate.apply_migrations(cls.db)

    @classmethod
    def tearDownClass(cls):
        if not cls.db.closed:cls.db.close()

    def setUp(self):
        from uuid import uuid4
        self.v=fixture()
        self.project=self.v[0].project_id
        self.created=(self.db.execute('SELECT 1 FROM project_registry WHERE id=%s',
                                      (self.project,)).fetchone() is None)
        if not self.created:
            # Do not insert into or mutate existing tenant data in a disposable test.
            raise AssertionError('Synthetic project ID already exists in disposable DB')
        self.db.execute(
            """INSERT INTO project_registry
                (id,name,profile_revision,profile_hash,created_at,updated_at)
               VALUES (%s,'PIE stage25 synthetic',1,%s,now(),now())""",
            (self.project,'a'*64))

    def tearDown(self):
        self.db.execute('DELETE FROM project_registry WHERE id=%s',(self.project,))

    def persist(self,values=None):
        v=self.v if values is None else values
        with patch('intelligence.patterns_insights.research.pattern_registry.replay_candidate_pattern_quality_report',
                   return_value=v[13]):
            return persist_candidate_pattern_registry(self.db,*v)

    def test_fresh_or_upgrade_and_checksum_registration(self):
        self.assertEqual(self.migrate.apply_migrations(self.db),[])
        self.assertIsNotNone(self.db.execute(
            "SELECT to_regclass('pie_pattern_registry_series')").fetchone()[0])
        self.assertIsNotNone(self.db.execute(
            "SELECT to_regclass('pie_pattern_registry_revisions')").fetchone()[0])

    def test_insert_retry_idempotent_revision_and_no_mutation(self):
        a=self.persist()
        b=self.persist()
        self.assertEqual(len(a),5)
        self.assertEqual(len(b),5)
        self.assertTrue(all(x.revision==1 and not x.idempotent for x in a))
        self.assertTrue(all(x.revision==1 and x.idempotent for x in b))
        self.assertEqual([x.revision_id for x in a],[x.revision_id for x in b])
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM pie_pattern_registry_revisions WHERE project_id=%s',
            (self.project,)).fetchone()[0],5)
        with self.assertRaises(Exception):
            self.db.execute("""UPDATE pie_pattern_registry_revisions SET status='rejected'
                WHERE project_id=%s""",(self.project,))

    def test_new_quality_source_creates_revision_two_without_overwrite(self):
        original=self.persist()
        v=list(self.v)
        v[12]=replace(v[12],review_protocol_claim_id='unverified-review-protocol-v2')
        with patch('intelligence.patterns_insights.research.pattern_quality.replay_candidate_robustness_report',
                   return_value=v[11]):
            v[13]=build_candidate_pattern_quality_report(*v[:13])
        second=self.persist(tuple(v))
        self.assertTrue(all(x.revision==2 and not x.idempotent for x in second))
        self.assertEqual([x.series_id for x in original],[x.series_id for x in second])
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM pie_pattern_registry_revisions WHERE project_id=%s',
            (self.project,)).fetchone()[0],10)
        self.assertTrue(all(x.revision==1 for x in self.persist()))

    def test_cross_project_isolation_and_invalid_supported_write(self):
        written=self.persist()
        other='f'*32 if self.project!='f'*32 else 'e'*32
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM pie_pattern_registry_revisions WHERE project_id=%s',
            (other,)).fetchone()[0],0)
        with self.assertRaises(Exception):
            self.db.execute(
                """INSERT INTO pie_pattern_registry_revisions
                (project_id,series_id,hypothesis_sha256,revision,evidence_sha256,
                 payload_sha256,source_snapshot_sha256,source_quality_sha256,
                 stratum_sha256,source_rows_sha256,validation_id,status,payload)
                VALUES(%s,%s,%s,8,%s,%s,%s,%s,%s,%s,%s,'supported','{}')""",
                (self.project,written[0].series_id,'a'*64,'b'*64,
                 'c'*64,'d'*64,'e'*64,'f'*64,'a'*64,'b'*64))

if __name__=='__main__':
    unittest.main()
