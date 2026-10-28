"""PIE-MOD-27: deterministic source tombstones and opt-in PostgreSQL17 QA.

No live UAE/QDP/SRE producer is authenticated by these test-only fixtures.
Tests using CM_TEST_PG_DSN require an explicitly disposable test database.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import json
import os
import unittest

from intelligence.patterns_insights.contracts import PIEBoundaryError,canonical_hash
from intelligence.patterns_insights.research.pattern_invalidation import (
    SCHEMA,SourceInvalidationNotice,CurrentInvalidationAuthority,
    record_source_invalidation,is_revision_invalidated,
    require_noninvalidated_pattern_for_insight,invalidation_target_predicate,
)
from intelligence.patterns_insights.research.pattern_registry import (
    build_candidate_pattern_registry_batch,persist_candidate_pattern_registry)
from test_pie_pattern_registry import fixture as registry_fixture

ROOT=Path(__file__).resolve().parents[1]
MIGRATION=ROOT/'intelligence/migrations/202_pie_pattern_invalidation_ledger.sql'
NOW=datetime(2026,10,28,12,tzinfo=timezone.utc)
P='a'*32
OLD='b'*64
NEXT='c'*64


def notice(**changes):
    fields=dict(project_id=P,target_kind='uae_manifest',target_sha256=OLD,
                producer='uae',reason_code='new_source_snapshot',
                source_event_id='uae-source-event-v1',
                event_effective_at='2026-10-26T10:00:00Z',
                replacement_sha256=NEXT)
    fields.update(changes)
    return SourceInvalidationNotice(**fields)


class FakeAuthority:
    def __init__(self,allowed=True):
        self.allowed=allowed
        self.calls=[]
    def authorize_invalidation(self,event):
        self.calls.append(event)
        return self.allowed


class NoSQL:
    def __init__(self):self.calls=0
    def execute(self,*args,**kw):
        self.calls+=1
        raise AssertionError('SQL must not be touched when authority invalid')


class FakeExists:
    def __init__(self,status):self.status=status;self.last=None
    def execute(self,sql,args):
        self.last=(sql,args)
        return self
    def fetchone(self):return (self.status,)


class InvalidationUnitTests(unittest.TestCase):
    def reject(self,code,func,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as e:func(*args,**kwargs)
        self.assertEqual(e.exception.code,code)

    def test_reproducible_version_schema_and_same_notice_identity(self):
        e=notice()
        self.assertEqual(e.event_id,notice().event_id)
        self.assertEqual(e.event_id,canonical_hash(json.loads(e.to_json())))
        self.assertEqual(e.schema_version,SCHEMA)
        self.assertEqual(json.loads(e.to_json())['provenance_claim'],
                         'unverified_external_producer_claim')

    def test_out_of_order_earlier_notice_is_still_valid(self):
        earlier=notice(event_effective_at='2020-01-01T00:00:00Z',
                       source_event_id='revoked-backdated')
        later=notice(event_effective_at='2026-10-27T12:00:00Z',
                     source_event_id='newer-notice')
        self.assertLess(earlier.event_effective_at,later.event_effective_at)
        self.assertNotEqual(earlier.event_id,later.event_id)
        self.assertEqual(earlier.target_sha256,later.target_sha256)
        self.assertEqual(earlier.target_kind,'uae_manifest')

    def test_new_snapshot_old_digest_and_replacement_identity(self):
        e=notice(target_kind='dataset_snapshot',reason_code='new_source_snapshot')
        self.assertEqual(e.target_sha256,OLD)
        self.assertNotEqual(e.replacement_sha256,e.target_sha256)

    def test_all_upstream_producer_families_have_representable_tombstones(self):
        producers=('uae','qdp','group_a','group_b','group_c','sre',
                   'source_policy','retention','research_config')
        for producer in producers:
            kind=('matched_rows' if producer in
                  ('qdp','group_a','group_b','group_c','sre')
                  else 'study_design' if producer=='research_config' else 'uae_manifest')
            reason=('source_retention_expired' if producer=='retention'
                    else 'source_rights_revoked' if producer=='source_policy'
                    else 'configuration_changed' if producer=='research_config'
                    else 'upstream_revision')
            e=notice(producer=producer,target_kind=kind,reason_code=reason)
            self.assertEqual(e.target_kind,kind)
            self.assertEqual(e.producer,producer)

    def test_wrong_source_rights_reason_producer_forbidden(self):
        self.reject('invalid_pattern_invalidation',notice,
                    producer='group_b',reason_code='source_rights_revoked')
        self.reject('invalid_pattern_invalidation',notice,
                    producer='qdp',reason_code='source_retention_expired')

    def test_reject_unknown_kind_source_producer_and_bad_digest(self):
        for changes in (
            {'project_id':'wrong'},
            {'target_kind':'raw_video'},
            {'target_sha256':'not_sha'},
            {'producer':'untrusted_bot'},
            {'reason_code':'approved_for_insights'},
            {'source_event_id':'contains a space'},
            {'schema_version':'pie-source-invalidation-notice/v2'},
            {'provenance_claim':'verified_live_source'},
        ):
            self.reject('invalid_pattern_invalidation',notice,**changes)

    def test_reject_forged_identity_future_and_naive_time(self):
        self.reject('invalid_pattern_invalidation',notice,replacement_sha256=OLD)
        self.reject('invalid_pattern_invalidation',notice,
                    event_effective_at='2026-10-27T10:00:00')
        self.reject('invalid_pattern_invalidation',notice,
                    event_effective_at='not-a-clock')

    def test_missing_invalidation_authority_never_accesses_sql(self):
        db=NoSQL()
        self.reject('invalidation_authority_unavailable',
                    record_source_invalidation,db,notice(),None,now=NOW)
        self.assertEqual(db.calls,0)

    def test_false_and_forged_truthy_authority_never_accesses_sql(self):
        for decision in (False,'yes',1):
            db=NoSQL()
            gate=FakeAuthority(decision)
            self.reject('invalidation_authority_denied',
                        record_source_invalidation,db,notice(),gate,now=NOW)
            self.assertEqual(db.calls,0)
            self.assertEqual(len(gate.calls),1)

    def test_future_dated_event_refused_after_authenticated_gate(self):
        e=notice(event_effective_at=(NOW+timedelta(days=10)).isoformat())
        db=NoSQL()
        self.reject('invalid_pattern_invalidation',
                    record_source_invalidation,db,e,FakeAuthority(),now=NOW)
        self.assertEqual(db.calls,0)

    def test_exact_sql_predicate_all_enumerated_source_fingerprints(self):
        predicate=invalidation_target_predicate()
        for kind in ('uae_manifest','dataset_snapshot','matched_rows',
                     'quality_report','holdout','numeric_report',
                     'robustness_report','study_design','stratum'):
            self.assertIn("i.target_kind='"+kind+"'",predicate)
        self.assertIn('i.target_sha256=r.source_snapshot_sha256',predicate)
        self.assertIn("r.payload->>'dataset_manifest_sha256'",predicate)
        self.assertIn('i.target_sha256=r.stratum_sha256',predicate)
        self.reject('invalid_invalidation_query',invalidation_target_predicate,alias='foo')

    def test_is_revision_invalidated_typed_boolean_and_project_scope(self):
        db=FakeExists(True)
        self.assertTrue(is_revision_invalidated(db,P,OLD,3))
        sql,args=db.last
        self.assertIn('r.project_id=%s AND r.series_id=%s AND r.revision=%s',sql)
        self.assertIn('i.project_id=r.project_id',sql)
        self.assertEqual(args,(P,OLD,3))
        db=FakeExists(False)
        self.assertFalse(is_revision_invalidated(db,P,OLD,3))

    def test_stale_source_never_passes_insight_preflight(self):
        self.reject('pattern_evidence_invalidated',
                    require_noninvalidated_pattern_for_insight,
                    FakeExists(True),P,OLD,1)
        self.assertIsNone(require_noninvalidated_pattern_for_insight(
            FakeExists(False),P,OLD,1))

    def test_failed_or_missing_ledger_is_not_safe(self):
        class Unavailable:
            def execute(self,*args):raise RuntimeError('db offline')
        self.reject('invalidation_ledger_unavailable',is_revision_invalidated,
                    Unavailable(),P,OLD,1)
        self.reject('invalidation_ledger_unavailable',is_revision_invalidated,
                    FakeExists('true'),P,OLD,1)
        self.reject('invalid_invalidation_query',is_revision_invalidated,
                    FakeExists(False),P,OLD,0)

    def test_project_is_required_for_revision_lookup(self):
        self.reject('invalid_invalidation_query',
                    is_revision_invalidated,FakeExists(False),'other',OLD,1)

    def test_sql_migration_additive_triggered_and_scope_isolated(self):
        text=MIGRATION.read_text('utf-8')
        self.assertTrue(MIGRATION.name.startswith('202_pie_'))
        for table in ('pie_pattern_registry_invalidations',
                      'pie_pattern_registry_recompute_requests'):
            self.assertIn('CREATE TABLE '+table,text)
            self.assertIn('BEFORE UPDATE ON '+table,text)
        self.assertIn('UNIQUE (project_id,producer,source_event_id)',text)
        self.assertIn('PRIMARY KEY (project_id,event_id)',text)
        self.assertIn('FOREIGN KEY (project_id,source_revision_id)',text)
        self.assertNotIn('ALTER TABLE ',text)
        self.assertNotIn('DELETE FROM ',text)
        self.assertNotIn('INSERT INTO schema_migrations',text)

    def test_stage26_latest_only_query_has_tombstone_exclusion(self):
        from intelligence.patterns_insights.research.pattern_queries import (
            query_pattern_registry,
        )
        import inspect
        src=inspect.getsource(query_pattern_registry)
        self.assertIn('pie_pattern_registry_invalidations',src)
        self.assertIn('AND NOT EXISTS',src)
        self.assertIn('invalidation_target_predicate()',src)
        self.assertLess(src.index('ORDER BY r.series_id,r.revision DESC'),
                        src.index('AND NOT EXISTS'))

    def test_stage25_write_must_check_for_revoked_evidence(self):
        import inspect
        src=inspect.getsource(persist_candidate_pattern_registry)
        self.assertIn('pie_pattern_registry_invalidations',src)
        self.assertIn('registry_source_invalidated',src)
        self.assertIn('pie-registry-project:',src)


@unittest.skipUnless(os.environ.get('CM_TEST_PG_DSN'), 'Disposable CM_TEST_PG_DSN not set')
class PostgreSQLInvalidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.db=psycopg.connect(os.environ['CM_TEST_PG_DSN'],autocommit=True)
        name=cls.db.execute('SELECT current_database()').fetchone()[0]
        if 'test' not in name or name in ('postgres','template1','contentmachine'):
            cls.db.close()
            raise AssertionError('Must use explicitly disposable PG17 test database')
        cls.migrate=migrate
        migrate.apply_migrations(cls.db)

    @classmethod
    def tearDownClass(cls):
        if not cls.db.closed:cls.db.close()

    def setUp(self):
        from uuid import uuid4
        self.sources=registry_fixture()
        self.project=self.sources[0].project_id
        if self.db.execute('SELECT 1 FROM project_registry WHERE id=%s',
                           (self.project,)).fetchone():
            raise AssertionError('Fixture project collides with existing test state')
        self.db.execute(
            """INSERT INTO project_registry
               (id,name,profile_revision,profile_hash,created_at,updated_at)
               VALUES(%s,'PIE stage27 synthetic',1,%s,now(),now())""",
            (self.project,'e'*64))
        with patch('intelligence.patterns_insights.research.pattern_registry.replay_candidate_pattern_quality_report',
                   return_value=self.sources[13]):
            self.batch=build_candidate_pattern_registry_batch(*self.sources)
        self.manifest=self.batch.entries[0].as_dict()['dataset_manifest_sha256']

    def tearDown(self):
        self.db.execute('DELETE FROM project_registry WHERE id=%s',(self.project,))

    def _persist(self):
        with patch('intelligence.patterns_insights.research.pattern_registry.replay_candidate_pattern_quality_report',
                   return_value=self.sources[13]):
            return persist_candidate_pattern_registry(self.db,*self.sources)

    def _event(self,**changes):
        return notice(project_id=self.project,target_sha256=self.manifest,
                      replacement_sha256='f'*64,**changes)

    def test_disposable_schema_upgrade_and_checksums(self):
        self.assertEqual(self.migrate.apply_migrations(self.db),[])
        for name in ('pie_pattern_registry_series','pie_pattern_registry_revisions',
                     'pie_pattern_registry_invalidations',
                     'pie_pattern_registry_recompute_requests'):
            self.assertIsNotNone(self.db.execute(
                'SELECT to_regclass(%s)',(name,)).fetchone()[0])

    def test_revoke_all_and_idempotent_same_notice_and_reanalysis(self):
        r=self._persist()
        e=self._event()
        first=record_source_invalidation(self.db,e,FakeAuthority())
        second=record_source_invalidation(self.db,e,FakeAuthority())
        self.assertEqual(first.recompute_requests_added,5)
        self.assertFalse(first.idempotent)
        self.assertTrue(second.idempotent)
        self.assertEqual(second.recompute_requests_added,0)
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM pie_pattern_registry_recompute_requests WHERE project_id=%s',
            (self.project,)).fetchone()[0],5)
        for receipt in r:
            self.assertTrue(is_revision_invalidated(
                self.db,self.project,receipt.series_id,receipt.revision))
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM pie_pattern_registry_revisions WHERE project_id=%s',
            (self.project,)).fetchone()[0],5)
        with self.assertRaises(PIEBoundaryError) as c:self._persist()
        self.assertEqual(c.exception.code,'registry_source_invalidated')

    def test_late_earlier_revocation_persists_independent_of_delivery_order(self):
        self._persist()
        new=self._event(source_event_id='new-event',
                        event_effective_at='2026-10-27T15:00:00Z',
                        target_sha256='d'*64,
                        replacement_sha256=None)
        old=self._event(source_event_id='late-old',
                        event_effective_at='2020-01-01T00:00:00Z')
        first=record_source_invalidation(self.db,new,FakeAuthority())
        second=record_source_invalidation(self.db,old,FakeAuthority())
        self.assertEqual(first.recompute_requests_added,0)
        self.assertEqual(second.recompute_requests_added,5)
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM pie_pattern_registry_invalidations WHERE project_id=%s',
            (self.project,)).fetchone()[0],2)

    def test_recheck_prevents_stale_dashboard_and_immutability(self):
        self._persist()
        record_source_invalidation(self.db,self._event(),FakeAuthority())
        from intelligence.patterns_insights.research.pattern_queries import (
            RegistryQuery,CursorSigner,query_pattern_registry)
        from intelligence.patterns_insights.intake.provenance import SourceEligibilityGate
        class Access:
            def require_current_project_access(self,project,actor):return True
        class Reader:
            def read_fresh_qualified(self,*_):raise AssertionError('Blocked before UAE')
        with patch('intelligence.patterns_insights.research.pattern_queries._rights_revalidate',
                   return_value=True),patch(
             'intelligence.patterns_insights.research.pattern_queries._platform_from_videos',
                   return_value='youtube'):
            page=query_pattern_registry(
                self.db,RegistryQuery(self.project,self.sources[14].owner_id,page_size=20),
                Access(),Reader(),SourceEligibilityGate(),CursorSigner(b'x'*32))
        self.assertEqual(page.items,())
        with self.assertRaises(Exception):
            self.db.execute(
                """UPDATE pie_pattern_registry_invalidations
                   SET reason_code='upstream_revision' WHERE project_id=%s""",
                (self.project,))
        with self.assertRaises(Exception):
            self.db.execute(
                """UPDATE pie_pattern_registry_recompute_requests
                   SET requested_state='awaiting_trusted_source_and_review'
                   WHERE project_id=%s""",(self.project,))

    def test_conflicting_replay_same_producer_event_id_is_rejected(self):
        self._persist()
        record_source_invalidation(self.db,self._event(),FakeAuthority())
        changed=self._event(target_sha256='d'*64,replacement_sha256=None)
        with self.assertRaises(PIEBoundaryError) as context:
            record_source_invalidation(self.db,changed,FakeAuthority())
        self.assertEqual(context.exception.code,'invalidation_event_conflict')

    def test_other_project_same_source_hash_is_not_revoked(self):
        # All stage27 lookups and UPDATE denial are strictly project-scoped.
        self._persist()
        record_source_invalidation(self.db,self._event(),FakeAuthority())
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM pie_pattern_registry_invalidations WHERE project_id=%s',
            ('f'*32 if self.project!='f'*32 else 'e'*32,)
        ).fetchone()[0],0)

if __name__=='__main__':
    unittest.main()
