"""PIE-MOD-32: mocked human review scenarios and optional disposable PG17.

Stage26/27/31 current rights and human role grants are FAKE in unit tests;
this does not constitute a live reviewer or authorized/public release.
"""
from __future__ import annotations

from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import json
import os
import unittest

from intelligence.patterns_insights.contracts import PIEBoundaryError,canonical_hash
from intelligence.patterns_insights.research.human_review import (
    ReviewAction,CurrentReviewGrant,CandidateReviewEvent,
    append_candidate_review,replay_candidate_review_event,
    _state,_ROLES,
)
from intelligence.patterns_insights.research.insight_verification import _audit
from intelligence.patterns_insights.research.pattern_queries import RegistryQuery

NOW=datetime(2026,10,28,13,tzinfo=timezone.utc)
PROJECT='f'*32
SERIES='a'*64
GEN='b'*64
HYP='c'*64
VHASH='d'*64
PROMPT='e'*64


class Cursor:
    def __init__(self,one=None,many=None):self.one=one;self.many=many
    def fetchone(self):return self.one
    def fetchall(self):return [] if self.many is None else self.many


class ReviewDB:
    def __init__(self):
        self.rows=[]
        self.calls=[]
        self.fail=False
    class Tx:
        def __enter__(self):return None
        def __exit__(self,*exc):return False
    def transaction(self):return self.Tx()
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if self.fail:raise RuntimeError('database offline')
        if 'FROM pie_candidate_insight_review_events' in sql:
            return Cursor(many=list(self.rows))
        if 'INSERT INTO pie_candidate_insight_review_events' in sql:
            p=args[-1].obj
            self.rows.append((
                args[3],args[5],args[4],args[6],args[8],args[9],args[11],
                args[12],args[14],args[15],args[16],args[2],p,args[7],
            ))
            return Cursor(one=(args[5],))
        return Cursor()


class FakeAuthority:
    def __init__(self,role=None,enabled=True,expires=None):
        self.role=role
        self.enabled=enabled
        self.expires=expires if expires is not None else NOW+timedelta(hours=1)
        self.calls=[]
    def require_current_review_grant(self,project,actor,kind,generation):
        self.calls.append((project,actor,kind,generation))
        if not self.enabled:raise PermissionError('No current role')
        return CurrentReviewGrant(project,actor,self.role or _ROLES[kind],
                                  '9'*64,self.expires)


def action(kind='review_decision',*,request=None,decision=None,
           reason=None,edit=None,label=None,target=None):
    if decision is None and kind=='review_decision':decision='review_candidate_internal'
    if reason is None:
        reason={
            'review_decision':'evidence_consistent' if decision=='review_candidate_internal' else 'insufficient_support',
            'change_suggestion':'clarify_uncertainty',
            'appeal':'challenge_prior_decision',
            'adjudication':'policy_reassessment',
            'calibration_vote':'independent_calibration',
            'calibration_gold':'independent_calibration',
        }[kind]
    return ReviewAction(request or str(uuid4()),kind,reason,
                        decision_code=decision,edit_code=edit,
                        calibration_label=label,target_event_sha256=target)


class HumanReviewTests(unittest.TestCase):
    def setUp(self):
        self.db=ReviewDB()
        self.query=RegistryQuery(PROJECT,'original-owner')
        self.good=_audit(PROJECT,GEN,SERIES,2,HYP,VHASH,PROMPT,'exact_source_consistent')
        self.quarantine=_audit(PROJECT,GEN,SERIES,2,HYP,VHASH,PROMPT,'numeric_or_scope_contradiction')
        self.calls=0

    def reject(self,code,fn,*a,**kw):
        with self.assertRaises(PIEBoundaryError) as err:fn(*a,**kw)
        self.assertEqual(err.exception.code,code)

    def submit(self,act=None,actor='reviewer-one',authority=None,
               report=None,db=None):
        if act is None:act=action()
        if authority is None:authority=FakeAuthority()
        if db is None:db=self.db
        if report is None:report=self.good
        def verified(*a,**kw):
            self.calls+=1
            return report,None
        with patch('intelligence.patterns_insights.research.human_review.verify_candidate_insight',
                   side_effect=verified):
            result=append_candidate_review(
                db,self.query,SERIES,GEN,actor,act,authority,
                None,None,None,None,now=NOW)
        return result

    def test_reviewer_can_mark_candidate_for_internal_review_only(self):
        event,receipt=self.submit()
        self.assertEqual(event.event_seq,1)
        self.assertIsNone(event.parent_event_sha256)
        self.assertEqual(event.actor_role,'reviewer')
        self.assertEqual(receipt.internal_state,'candidate_reviewed_internally_only')
        self.assertFalse(receipt.may_public_read)
        self.assertFalse(receipt.may_publish)
        self.assertFalse(receipt.human_release_approved)
        self.assertFalse(event.as_dict()['original_insight_modified'])

    def test_quarantined_source_cannot_be_internally_approved(self):
        self.reject('review_quarantine_unapprovable',self.submit,
                    report=self.quarantine)
        self.assertEqual(len(self.db.rows),0)

    def test_quarantined_candidate_can_be_rejected(self):
        act=action(decision='reject_candidate')
        _,out=self.submit(act,report=self.quarantine)
        self.assertEqual(out.internal_state,'quarantined')
        self.assertFalse(out.may_publish)

    def test_quarantined_candidate_can_request_changes(self):
        act=action(decision='request_changes')
        _,out=self.submit(act,report=self.quarantine)
        self.assertEqual(out.internal_state,'quarantined')

    def test_edit_suggestion_is_structured_nonexecuting_version_one(self):
        e,r=self.submit(action('change_suggestion',edit='clarify_uncertainty'))
        self.assertEqual(e.review_edit_version,1)
        self.assertEqual(e.as_dict()['edit_code'],'clarify_uncertainty')
        self.assertFalse(e.as_dict()['original_insight_modified'])
        self.assertEqual(r.internal_state,'awaiting_internal_review')

    def test_second_suggestion_version_is_incremental_and_parented(self):
        first,_=self.submit(action('change_suggestion',edit='clarify_uncertainty'))
        second,_=self.submit(action('change_suggestion',edit='add_caveat',
                                   reason='request_more_evidence'),actor='reviewer-two')
        self.assertEqual(second.review_edit_version,2)
        self.assertEqual(second.parent_event_sha256,first.event_sha256)
        self.assertEqual(second.event_seq,2)

    def test_review_request_idempotent_with_current_reauthorization(self):
        a=action()
        first,r1=self.submit(a)
        second,r2=self.submit(a)
        self.assertEqual(first,second)
        self.assertFalse(r1.idempotent)
        self.assertTrue(r2.idempotent)
        self.assertEqual(self.calls,2)
        self.assertEqual(len(self.db.rows),1)

    def test_idempotency_key_reuse_for_different_decision_refused(self):
        req=str(uuid4())
        self.submit(action(request=req))
        self.reject('review_request_reused',self.submit,
                    action(request=req,decision='reject_candidate'))

    def test_two_distinct_reviewers_can_disagree_without_publishing(self):
        self.submit(action(),actor='reviewer-one')
        _,receipt=self.submit(action(decision='reject_candidate'),actor='reviewer-two')
        self.assertTrue(receipt.reviewer_disagreement)
        self.assertEqual(receipt.internal_state,'disputed_internal')
        self.assertFalse(receipt.human_release_approved)

    def test_single_reviewer_latest_decision_is_versioned_not_overwritten(self):
        self.submit(action(),actor='reviewer-one')
        _,receipt=self.submit(action(decision='reject_candidate'),actor='reviewer-one')
        self.assertFalse(receipt.reviewer_disagreement)
        self.assertEqual(receipt.internal_state,'rejected_internal')
        self.assertEqual(len(self.db.rows),2)

    def test_denied_source_cannot_be_reused_to_replay_approved_review(self):
        self.submit()
        with patch('intelligence.patterns_insights.research.human_review.verify_candidate_insight',
                   side_effect=PIEBoundaryError('insight_rights_not_verified','revoked')):
            self.reject('insight_rights_not_verified',append_candidate_review,
                        self.db,self.query,SERIES,GEN,'reviewer-one',action(),
                        FakeAuthority(),None,None,None,None,now=NOW)
        self.assertEqual(len(self.db.rows),1)

    def test_source_owner_cannot_self_review(self):
        self.reject('review_role_separation',self.submit,actor='original-owner')
        self.assertFalse(self.db.rows)

    def test_missing_or_expired_role_grant_fails_before_sql(self):
        self.reject('review_authority_unavailable',self.submit,authority=None,
                    actor='bad name')
        self.reject('review_access_denied',self.submit,authority=FakeAuthority(enabled=False))
        self.reject('review_access_denied',self.submit,
                    authority=FakeAuthority(expires=NOW))
        self.assertEqual(len(self.db.rows),0)

    def test_forged_role_for_event_kind_is_refused(self):
        self.reject('review_access_denied',self.submit,
                    authority=FakeAuthority(role='curator'))
        self.assertFalse(self.db.rows)

    def test_appeal_must_target_original_rejection(self):
        rejected,_=self.submit(action(decision='reject_candidate'))
        event,_=self.submit(action('appeal',target=rejected.event_sha256),
                            actor='original-owner')
        self.assertEqual(event.actor_role,'appellant')
        self.assertEqual(event.as_dict()['target_event_sha256'],rejected.event_sha256)

    def test_appeal_never_auto_approves_published_release(self):
        rej,_=self.submit(action(decision='reject_candidate'))
        _,receipt=self.submit(action('appeal',target=rej.event_sha256),
                              actor='original-owner')
        self.assertEqual(receipt.internal_state,'appeal_pending')
        self.assertFalse(receipt.may_publish)

    def test_appeal_non_author_and_non_denial_refused(self):
        approved,_=self.submit()
        self.reject('review_appeal_denied',self.submit,
                    action('appeal',target=approved.event_sha256),actor='original-owner')
        self.reject('review_role_separation',self.submit,
                    action('appeal',target=approved.event_sha256),actor='other-owner')

    def test_duplicate_appeal_is_rejected(self):
        denied,_=self.submit(action(decision='reject_candidate'))
        self.submit(action('appeal',target=denied.event_sha256),actor='original-owner')
        self.reject('review_appeal_denied',self.submit,
                    action('appeal',target=denied.event_sha256),actor='original-owner')

    def test_adjudicator_must_be_third_party(self):
        denied,_=self.submit(action(decision='reject_candidate'),actor='reviewer-one')
        appeal,_=self.submit(action('appeal',target=denied.event_sha256),
                             actor='original-owner')
        self.reject('review_adjudication_denied',self.submit,
                    action('adjudication',target=appeal.event_sha256,
                           decision='return_to_review'),actor='reviewer-one')
        _,receipt=self.submit(action('adjudication',target=appeal.event_sha256,
                                     decision='return_to_review'),actor='adjudicator-three')
        self.assertEqual(receipt.internal_state,'reopened_for_review')
        self.assertFalse(receipt.human_release_approved)

    def test_adjudication_uphold_denial_is_not_publication(self):
        denied,_=self.submit(action(decision='reject_candidate'))
        appeal,_=self.submit(action('appeal',target=denied.event_sha256),actor='original-owner')
        _,receipt=self.submit(action('adjudication',target=appeal.event_sha256,
                                     decision='uphold_rejection'),actor='adjudicator-three')
        self.assertEqual(receipt.internal_state,'rejection_upheld')
        self.assertFalse(receipt.may_public_read)

    def test_adjudication_against_nonappeal_event_is_refused(self):
        denied,_=self.submit(action(decision='reject_candidate'))
        self.reject('review_adjudication_denied',self.submit,
                    action('adjudication',target=denied.event_sha256,
                           decision='uphold_rejection'),actor='adjudicator-three')

    def test_blind_calibration_vote_requires_separate_reviewer(self):
        _,r=self.submit(action('calibration_vote',label='candidate_consistent'),
                        actor='reviewer-one')
        self.assertEqual(r.internal_state,'awaiting_internal_review')
        self.assertIsNone(r.calibration_consensus)
        self.reject('review_calibration_conflict',self.submit,
                    action('calibration_vote',label='candidate_contradictory'),
                    actor='reviewer-one')

    def test_gold_label_requires_two_distinct_consensus_votes(self):
        self.submit(action('calibration_vote',label='candidate_consistent'),
                    actor='reviewer-one')
        self.reject('review_calibration_conflict',self.submit,
                    action('calibration_gold',label='candidate_consistent'),
                    actor='curator-three')
        self.submit(action('calibration_vote',label='candidate_consistent'),
                    actor='reviewer-two')
        _,r=self.submit(action('calibration_gold',label='candidate_consistent'),
                        actor='curator-three')
        self.assertEqual(r.calibration_consensus,'candidate_consistent')
        self.assertFalse(r.human_release_approved)

    def test_no_calibration_votes_after_gold_consensus(self):
        self.submit(action('calibration_vote',label='candidate_consistent'),actor='reviewer-one')
        self.submit(action('calibration_vote',label='candidate_consistent'),actor='reviewer-two')
        self.submit(action('calibration_gold',label='candidate_consistent'),actor='curator-three')
        self.reject('review_calibration_conflict',self.submit,
                    action('calibration_vote',label='candidate_consistent'),
                    actor='reviewer-four')

    def test_pending_appeal_survives_unrelated_calibration(self):
        rejected,_=self.submit(action(decision='reject_candidate'))
        self.submit(action('appeal',target=rejected.event_sha256),actor='original-owner')
        _,receipt=self.submit(action('calibration_vote',label='uncertain'),
                              actor='reviewer-two')
        self.assertEqual(receipt.internal_state,'appeal_pending')
        self.assertFalse(receipt.may_publish)

    def test_disagreeing_calibration_labels_cannot_form_false_gold(self):
        self.submit(action('calibration_vote',label='candidate_consistent'),
                    actor='reviewer-one')
        self.submit(action('calibration_vote',label='candidate_contradictory'),
                    actor='reviewer-two')
        self.reject('review_calibration_conflict',self.submit,
                    action('calibration_gold',label='candidate_consistent'),
                    actor='curator-three')

    def test_curator_cannot_be_prior_blind_reviewer(self):
        self.submit(action('calibration_vote',label='candidate_consistent'),actor='reviewer-one')
        self.submit(action('calibration_vote',label='candidate_consistent'),actor='reviewer-two')
        self.reject('review_access_denied',self.submit,
                    action('calibration_gold',label='candidate_consistent'),
                    actor='reviewer-one',authority=FakeAuthority(role='reviewer'))

    def test_missing_or_forged_event_target_refused(self):
        self.reject('review_target_unavailable',self.submit,
                    action('appeal',target='1'*64),actor='original-owner')

    def test_changed_review_chain_bytes_are_rejected(self):
        original,_=self.submit()
        self.db.rows[0]=(*self.db.rows[0][:12],
                         {**self.db.rows[0][12],'may_publish':True},
                         self.db.rows[0][13])
        self.reject('review_chain_corrupt',self.submit,action())

    def test_missing_parent_or_sequence_corruption_fails_closed(self):
        self.submit()
        row=list(self.db.rows[0]);row[0]=3
        self.db.rows[0]=tuple(row)
        self.reject('review_chain_corrupt',self.submit,action())

    def test_exact_byte_replay_and_rehash_forgery_fail(self):
        event,_=self.submit()
        self.assertEqual(replay_candidate_review_event(
            event.to_json(),event.event_sha256,event),event)
        altered=event.as_dict()
        altered['decision_code']='review_candidate_for_public_release'
        raw=json.dumps(altered,sort_keys=True,separators=(',',':'))
        digest=canonical_hash(altered)
        self.reject('review_replay_invalid',replay_candidate_review_event,
                    raw,digest,event)

    def test_arbitrary_text_and_unknown_revision_edit_rejected(self):
        self.reject('invalid_review_action',ReviewAction,
                    str(uuid4()),'change_suggestion','clarify_uncertainty',
                    edit_code='rewrite: guarantee 98% uplift')
        self.reject('invalid_review_action',ReviewAction,
                    str(uuid4()),'review_decision','evidence_consistent',
                    decision_code='publish_now')

    def test_untrusted_reviewer_text_never_reaches_event(self):
        event,_=self.submit()
        text=event.to_json()
        for forbidden in ('source_discovery_pairs','source_rows_sha256',
                          'model_response_json','video_id','private_notes',
                          'raw_insight_text','Authorization','http://'):
            self.assertNotIn(forbidden,text)
        self.assertEqual(event.as_dict()['review_policy'],'internal_candidate_only')

    def test_inconsistent_auth_and_audit_source_closed_before_sql(self):
        fake=_audit(PROJECT,GEN,SERIES,2,HYP,VHASH,PROMPT,'numeric_or_scope_contradiction')
        self.reject('review_quarantine_unapprovable',self.submit,report=fake)

    def test_database_error_does_not_publish_or_retry(self):
        self.db.fail=True
        self.reject('review_chain_unavailable',self.submit)

    def test_sql_has_project_fks_chain_guard_and_no_public_release(self):
        sql=(Path(__file__).resolve().parents[1]/
             'intelligence/migrations/206_pie_human_review_events.sql').read_text('utf-8')
        for needle in ('CREATE TABLE pie_candidate_insight_review_events',
                       'FOREIGN KEY (project_id,generation_sha256)',
                       'FOREIGN KEY (project_id,verification_sha256)',
                       'pie_candidate_review_chain_guard_insert',
                       'pie_candidate_review_event_immutable',
                       'review_edit_version','request_sha256',
                       'pie_candidate_review_one_calibration_vote_per_reviewer',
                       "AND s.owner_id=NEW.actor_id",
                       'newer.revision>source_revision',
                       'pie_pattern_registry_invalidations i',
                       "event_payload->'may_publish'"):
            self.assertIn(needle,sql)
        self.assertEqual(sql.count('$pie32$'),2)
        self.assertNotIn('ALTER TABLE ',sql)

    def test_sql_disallows_public_review_status_or_unilateral_gold(self):
        sql=(Path(__file__).resolve().parents[1]/
             'intelligence/migrations/206_pie_human_review_events.sql').read_text('utf-8')
        self.assertIn("'review_candidate_internal'",sql)
        self.assertIn("'candidate_consistent','candidate_contradictory','uncertain'",sql)
        self.assertIn('IF voters_count<2 OR agreeing_count<>voters_count THEN',sql)
        self.assertIn('PIE calibration label set frozen after curator consensus',sql)
        self.assertIn('PIE curator cannot label own prior vote',sql)


@unittest.skipUnless(os.getenv('CM_TEST_PG_DSN'),'Disposable CM_TEST_PG_DSN not set')
class HumanReviewPG17Optional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.db=psycopg.connect(os.environ['CM_TEST_PG_DSN'],autocommit=True)
        name=cls.db.execute('SELECT current_database()').fetchone()[0]
        if 'test' not in name or name in ('postgres','template1','contentmachine'):
            cls.db.close()
            raise AssertionError('Refuse non-disposable PostgreSQL')
        migrate.apply_migrations(cls.db)
    @classmethod
    def tearDownClass(cls):
        if not cls.db.closed:cls.db.close()

    def test_new_review_chain_migrated_and_trigger_present(self):
        from intelligence import migrate
        self.assertEqual(migrate.apply_migrations(self.db),[])
        self.assertEqual(self.db.execute(
            "SELECT to_regclass('pie_candidate_insight_review_events')").fetchone()[0],
            'pie_candidate_insight_review_events')
        names={v[0] for v in self.db.execute(
            """SELECT tgname FROM pg_trigger
                WHERE tgrelid='pie_candidate_insight_review_events'::regclass
                  AND NOT tgisinternal""").fetchall()}
        self.assertIn('pie_candidate_review_event_immutable',names)
        self.assertIn('pie_candidate_review_chain_guard_insert',names)

    def test_direct_sql_forged_review_cannot_reference_unknown_generation(self):
        from psycopg.types.json import Jsonb
        project=uuid4().hex
        self.db.execute(
            """INSERT INTO project_registry
               (id,name,profile_revision,profile_hash,created_at,updated_at)
               VALUES(%s,'PIE stage32 isolated',1,%s,now(),now())""",
            (project,'a'*64))
        try:
            with self.assertRaises(Exception):
                self.db.execute(
                    """INSERT INTO pie_candidate_insight_review_events
                       (project_id,generation_sha256,verification_sha256,
                        event_seq,event_sha256,request_sha256,actor_id,actor_role,
                        grant_sha256,event_kind,reason_code,decision_code,event_payload)
                       VALUES(%s,%s,%s,1,%s,%s,'fake-reviewer','reviewer',%s,
                              'review_decision','evidence_consistent',
                              'review_candidate_internal',%s)""",
                    (project,'b'*64,'c'*64,'d'*64,'e'*64,'f'*64,
                     Jsonb({'may_publish':False})))
        finally:
            self.db.execute('DELETE FROM project_registry WHERE id=%s',(project,))

if __name__=='__main__':
    unittest.main()
