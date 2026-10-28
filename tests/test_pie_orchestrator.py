"""PIE-MOD-33: synthetic fenced queue/worker/outbox checks, opt-in disposable PG17.

Mock SQL is not evidence of live concurrency. No paid calls, no credentials.
"""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from pathlib import Path
from uuid import uuid4
import os
import unittest
from intelligence.patterns_insights.contracts import PIEBoundaryError
from intelligence.patterns_insights.research.orchestrator import (
    JobIntent,enqueue_job,claim_deterministic_job,heartbeat_job,
    checkpoint_job,complete_job,fail_job,cancel_job,recover_expired_jobs,
    execute_one_deterministic,_backoff,
)

P='f'*32
R='11111111-1111-1111-1111-111111111111'
H='a'*64
W='worker-test'
NOW=datetime(2026,10,28,13,tzinfo=timezone.utc)


class Result:
    def __init__(self,row=None):self.row=row
    def fetchone(self):return self.row


class Authority:
    def __init__(self,ok=True):self.ok=ok;self.actions=[]
    def require_current_job_access(self,p,a,o):
        self.actions.append((p,a,o));return self.ok


class Database:
    class Transaction:
        def __enter__(self):return None
        def __exit__(self,*args):return False
    def __init__(self):
        self.jobs={};self.specs={};self.keys={};self.events={}
        self.checkpoints={};self.outbox=[];self.run_status='draft'
        self.expired=False;self.calls=[];self.fail_outbox=False
    def transaction(self):return self.Transaction()
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if 'SELECT status FROM pie_research_runs' in sql:
            return Result((self.run_status,) if self.run_status else None)
        if 'INSERT INTO pie_jobs' in sql:
            p,run,k,key,state,max_attempts=args
            if key in self.keys:return Result()
            jid=str(uuid4())
            self.keys[key]=jid
            self.jobs[jid]=dict(project=p,run=run,kind=k,key=key,
                state=state,attempts=0,max_attempts=max_attempts,fence=0,
                leased_by=None,until=None,available=True)
            return Result((jid,))
        if 'j.idempotency_key=%s' in sql:
            jid=self.keys.get(args[1])
            if not jid:return Result()
            j=self.jobs[jid];s=self.specs[jid]
            return Result((jid,j['run'],j['state'],s[0],s[1],s[2],j['max_attempts']))
        if 'INSERT INTO pie_job_execution_specs' in sql:
            project,jid,task,policy,input_sha,spec_sha=args
            self.specs[jid]=(task,input_sha,spec_sha,policy)
            return Result()
        if 'SELECT event_seq,event_sha256 FROM pie_job_orchestration_events' in sql:
            e=self.events.get(args[1],[])
            return Result((len(e),e[-1][0]) if e else None)
        if 'INSERT INTO pie_job_orchestration_events' in sql:
            p,jid,seq,parent,sha,code,fence,doc=args
            self.events.setdefault(jid,[]).append((sha,code,fence,doc.obj))
            return Result((sha,))
        if 'INSERT INTO pie_outbox' in sql:
            if self.fail_outbox:raise RuntimeError('outbox unavailable')
            self.outbox.append(args)
            return Result((str(uuid4()),))
        if 'WITH next_job AS' in sql:
            project,worker,seconds=args
            possibilities=[(jid,j) for jid,j in self.jobs.items()
                if j['project']==project and j['state']=='queued'
                and j['available'] and j['attempts']<j['max_attempts']
                and self.run_status=='draft' and self.specs[jid][3]=='safe_deterministic']
            if not possibilities:return Result()
            jid,j=possibilities[0];j['state']='leased';j['attempts']+=1
            j['fence']+=1;j['leased_by']=worker;j['until']=NOW+timedelta(seconds=seconds)
            return Result((jid,j['run'],j['fence'],j['attempts'],
                           j['max_attempts'],j['until']))
        if 'SELECT task_code,input_sha256 FROM pie_job_execution_specs' in sql:
            s=self.specs[args[1]]
            return Result((s[0],s[1]) if s[3]=='safe_deterministic' else None)
        if 'SELECT j.run_id::text,j.state,j.fence_token' in sql:
            j=self.jobs.get(args[1])
            if j is None:return Result()
            s=self.specs[args[1]]
            return Result((j['run'],j['state'],j['fence'],j['attempts'],
                           j['max_attempts'],j['until'],j['leased_by'],s[0],s[1],
                           self.run_status))
        if 'lease_until=GREATEST' in sql:
            secs,p,jid,fence,worker=args
            j=self.jobs[jid]
            if not self.live(j,p,fence,worker):return Result()
            j['until']=max(j['until'],NOW+timedelta(seconds=secs))
            return Result((j['run'],j['fence']))
        if 'SELECT COALESCE(MAX(checkpoint_seq)' in sql:
            return Result((len(self.checkpoints.get((args[1],args[2]),[]))+1,))
        if 'INSERT INTO pie_job_checkpoints' in sql:
            p,jid,fence,seq,digest,state,doc=args
            self.checkpoints.setdefault((jid,fence),[]).append((digest,doc.obj))
            return Result((digest,))
        if "UPDATE pie_jobs SET state='completed'" in sql:
            p,jid,fence,worker=args;j=self.jobs[jid]
            if not self.live(j,p,fence,worker):return Result()
            j['state']='completed';j['leased_by']=None;j['until']=None
            return Result((j['run'],))
        if 'UPDATE pie_jobs SET state=%s' in sql:
            if len(args)==7:
                state,secs,diff,p,jid,fence,worker=args;j=self.jobs[jid]
                if not self.live(j,p,fence,worker):return Result()
            else:
                state,secs,diff,p,jid,fence=args;j=self.jobs[jid]
                if j['state']!='leased' or j['fence']!=fence:return Result()
            j['state']=state;j['leased_by']=None;j['until']=None
            j['available']=secs is None
            return Result((j['run'],))
        if 'SELECT run_id::text,state,fence_token FROM pie_jobs' in sql:
            j=self.jobs.get(args[1])
            return Result((j['run'],j['state'],j['fence']) if j else None)
        if "UPDATE pie_jobs SET state='cancelled'" in sql:
            p,jid=args;j=self.jobs[jid]
            if j['state'] not in ('queued','leased','blocked'):return Result()
            j['state']='cancelled';j['leased_by']=None;j['until']=None
            return Result((jid,))
        if 'SELECT j.id::text,j.run_id::text,j.fence_token' in sql:
            possible=[(jid,j) for jid,j in self.jobs.items()
                if j['project']==args[0] and j['state']=='leased'
                and self.expired and self.specs[jid][3]=='safe_deterministic']
            if not possible:return Result()
            jid,j=possible[0]
            return Result((jid,j['run'],j['fence'],j['attempts'],
                           j['max_attempts'],self.run_status))
        return Result()
    def live(self,j,p,fence,worker):
        return (j['project']==p and j['state']=='leased' and j['fence']==fence
                and j['leased_by']==worker and not self.expired)


class Handler:
    def __init__(self,fail=False):self.fail=fail;self.calls=[]
    def run_deterministic(self,job):
        self.calls.append(job)
        if self.fail:raise RuntimeError('simulation')
        return H


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.db=Database();self.authority=Authority()
        self.intent=JobIntent(P,R,str(uuid4()),'insight_deterministic',H)
    def reject(self,code,fn,*a,**kw):
        with self.assertRaises(PIEBoundaryError) as ex:fn(*a,**kw)
        self.assertEqual(ex.exception.code,code)
    def enqueue(self,intent=None):
        return enqueue_job(self.db,intent or self.intent,'owner-test',self.authority)
    def claim(self,worker=W):
        return claim_deterministic_job(self.db,P,worker,self.authority,lease_seconds=60)

    def test_durable_enqueue_and_outbox(self):
        x=self.enqueue()
        self.assertEqual(x.state,'queued')
        self.assertFalse(x.automatic_paid_dispatch)
        self.assertEqual(len(self.db.outbox),1)
        self.assertEqual(self.db.events[x.job_id][0][1],'enqueued')
    def test_idempotent_duplicate_enqueue_has_one_outbox(self):
        a=self.enqueue();b=self.enqueue()
        self.assertEqual(a.job_id,b.job_id)
        self.assertTrue(b.idempotent)
        self.assertEqual(len(self.db.outbox),1)
    def test_conflicting_idempotency_input_rejected(self):
        self.enqueue()
        self.reject('job_idempotency_conflict',self.enqueue,
                    replace(self.intent,input_sha256='b'*64))
    def test_paid_handoff_always_blocked(self):
        i=JobIntent(P,R,str(uuid4()),'paid_model_manual_handoff',H,1)
        x=self.enqueue(i)
        self.assertEqual(x.state,'blocked')
        self.assertEqual(self.db.specs[x.job_id][3],'manual_paid_only')
        self.assertIsNone(self.claim())
    def test_paid_handoff_retry_not_configurable(self):
        self.reject('invalid_job_intent',JobIntent,P,R,str(uuid4()),
                    'paid_model_manual_handoff',H,2)
    def test_claim_lease_fence_and_attempt(self):
        self.enqueue();j=self.claim()
        self.assertEqual((j.fence_token,j.attempts),(1,1))
        self.assertEqual(j.leased_by,W)
        self.assertFalse(j.may_issue_paid_request)
    def test_two_workers_cannot_claim_same_live_job(self):
        self.enqueue();self.claim()
        self.assertIsNone(self.claim('worker-two'))
    def test_empty_queue_returns_none(self):
        self.assertIsNone(self.claim())
    def test_host_auth_missing_blocks_sql(self):
        self.reject('job_authority_unavailable',enqueue_job,
                    self.db,self.intent,'owner-test',None)
        self.assertFalse(self.db.calls)
    def test_host_auth_denial_blocks_enqueue(self):
        self.authority.ok=False
        self.reject('job_access_denied',self.enqueue)
        self.assertFalse(self.db.jobs)
    def test_host_auth_denial_blocks_claim(self):
        self.enqueue();self.authority.ok=False
        self.reject('job_access_denied',self.claim)
    def test_run_must_be_draft(self):
        self.db.run_status='cancelled'
        self.reject('job_run_unavailable',self.enqueue)
        self.assertFalse(self.db.jobs)
    def test_claim_filters_cancelled_run(self):
        self.enqueue();self.db.run_status='cancelled'
        self.assertIsNone(self.claim())
    def test_heartbeat_only_current_lease(self):
        self.enqueue();j=self.claim()
        r=heartbeat_job(self.db,j,W,self.authority,extend_seconds=60)
        self.assertEqual(r.fence_token,1)
        self.assertEqual(r.state,'leased')
    def test_heartbeat_duration_bounded(self):
        self.enqueue();j=self.claim()
        self.reject('invalid_job_lease',heartbeat_job,self.db,j,W,self.authority,
                    extend_seconds=1)
    def test_checkpoint_only_opaque_sha(self):
        self.enqueue();j=self.claim()
        digest=checkpoint_job(self.db,j,W,self.authority,H)
        self.assertEqual(len(digest),64)
        self.assertEqual(self.db.events[j.job_id][-1][1],'checkpoint')
    def test_checkpoint_rejects_media_text(self):
        self.enqueue();j=self.claim()
        self.reject('job_checkpoint_invalid',checkpoint_job,self.db,j,W,
                    self.authority,'raw transcript')
    def test_complete_records_event_and_outbox(self):
        self.enqueue();j=self.claim()
        r=complete_job(self.db,j,W,self.authority,H)
        self.assertEqual(r.state,'completed')
        self.assertEqual(len(self.db.outbox),3)
    def test_stale_job_cannot_complete_twice(self):
        self.enqueue();j=self.claim();complete_job(self.db,j,W,self.authority,H)
        self.reject('job_lease_lost',complete_job,self.db,j,W,self.authority,H)
    def test_other_worker_cannot_reuse_fence(self):
        self.enqueue();j=self.claim()
        self.reject('job_lease_invalid',complete_job,self.db,j,'worker-two',
                    self.authority,H)
    def test_stale_fence_rejected(self):
        self.enqueue();j=self.claim()
        self.reject('job_lease_invalid',complete_job,self.db,
                    replace(j,fence_token=0),W,self.authority,H)
    def test_retry_transient_only(self):
        self.enqueue();j=self.claim()
        r=fail_job(self.db,j,W,self.authority,'transient_internal')
        self.assertEqual((r.state,r.retry_after_seconds),('queued',2))
        self.assertEqual(self.db.events[j.job_id][-1][1],'retry_scheduled')
    def test_policy_or_rights_failure_terminal(self):
        self.enqueue();j=self.claim()
        self.assertEqual(fail_job(self.db,j,W,self.authority,'rights_denied').state,'failed')
    def test_max_attempts_one_never_retry(self):
        self.enqueue(replace(self.intent,max_attempts=1));j=self.claim()
        self.assertEqual(fail_job(self.db,j,W,self.authority,'upstream_unavailable').state,'failed')
    def test_unknown_paid_charge_retry_reason_denied(self):
        self.enqueue();j=self.claim()
        self.reject('job_failure_invalid',fail_job,self.db,j,W,self.authority,
                    'unknown_charge_try_again')
    def test_cancel_queued_prevents_claim(self):
        x=self.enqueue()
        self.assertEqual(cancel_job(self.db,P,x.job_id,'owner-test',self.authority).state,
                         'cancelled')
        self.assertIsNone(self.claim())
    def test_cancel_active_fences_old_worker(self):
        self.enqueue();j=self.claim()
        cancel_job(self.db,P,j.job_id,'owner-test',self.authority)
        self.reject('job_lease_lost',complete_job,self.db,j,W,self.authority,H)
    def test_cancel_paid_blocked_never_sends(self):
        i=JobIntent(P,R,str(uuid4()),'paid_model_manual_handoff',H,1)
        x=self.enqueue(i)
        self.assertFalse(cancel_job(self.db,P,x.job_id,'owner-test',
                                    self.authority).can_publish)
    def test_terminal_complete_cannot_cancel(self):
        self.enqueue();j=self.claim()
        complete_job(self.db,j,W,self.authority,H)
        self.reject('job_terminal',cancel_job,self.db,P,j.job_id,
                    'owner-test',self.authority)
    def test_expired_safe_lease_requeued(self):
        self.enqueue();self.claim();self.db.expired=True
        r=recover_expired_jobs(self.db,P,W,self.authority)
        self.assertEqual(len(r),1)
        self.assertEqual((r[0].state,r[0].retry_after_seconds),('queued',2))
    def test_expired_exhausted_lease_terminal(self):
        self.enqueue(replace(self.intent,max_attempts=1));self.claim()
        self.db.expired=True
        self.assertEqual(recover_expired_jobs(self.db,P,W,self.authority)[0].state,'failed')
    def test_expired_recovery_project_isolation(self):
        self.enqueue();self.claim();self.db.expired=True
        self.assertEqual(recover_expired_jobs(self.db,'e'*32,W,self.authority),[])
        self.assertEqual(len(recover_expired_jobs(self.db,P,W,self.authority)),1)
    def test_recovery_paid_never_auto_retries(self):
        self.enqueue(JobIntent(P,R,str(uuid4()),'paid_model_manual_handoff',H,1))
        self.db.expired=True
        self.assertEqual(recover_expired_jobs(self.db,P,W,self.authority),[])
    def test_bounded_backoff_reference_oracle(self):
        self.assertEqual([_backoff(n) for n in (1,2,3,10,20)],
                         [2,4,8,1024,1024])
    def test_execute_safe_handler_once(self):
        self.enqueue();h=Handler()
        result=execute_one_deterministic(self.db,P,W,self.authority,h)
        self.assertEqual(result.state,'completed')
        self.assertEqual(len(h.calls),1)
        self.assertEqual(h.calls[0].task_code,'insight_deterministic')
    def test_execute_handler_exception_terminal(self):
        self.enqueue()
        self.assertEqual(execute_one_deterministic(
            self.db,P,W,self.authority,Handler(fail=True)).state,'failed')
    def test_missing_executor_fails_before_claim(self):
        self.enqueue()
        self.reject('job_handler_unavailable',execute_one_deterministic,
                    self.db,P,W,self.authority,None)
        self.assertEqual(list(self.db.jobs.values())[0]['attempts'],0)
    def test_outbox_event_is_redacted_metadata_only(self):
        x=self.enqueue()
        event=self.db.events[x.job_id][0][3]
        self.assertFalse(event['can_publish'])
        self.assertFalse(event['may_issue_paid_request'])
        self.assertNotIn('video_id',str(event))
        self.assertNotIn('provider_key',str(event))
    def test_static_sql_requires_fences_and_source_scope(self):
        sql=(Path(__file__).resolve().parents[1]/
             'intelligence/migrations/207_pie_orchestrator_queue.sql').read_text('utf8')
        code=(Path(__file__).resolve().parents[1]/
              'intelligence/patterns_insights/research/orchestrator.py').read_text('utf8')
        for needle in ('CREATE TABLE pie_job_execution_specs',
                       'pie_job_checkpoint_fence_guard',
                       'pie_job_transition_guard_update',
                       'manual_paid_only','pie_job_orchestration_event_immutable'):
            self.assertIn(needle,sql)
        for needle in ('FOR UPDATE OF j SKIP LOCKED',
                       "s.execution_policy='safe_deterministic'",
                       'fence_token=j.fence_token+1','INSERT INTO pie_outbox',
                       'lease_until>clock_timestamp()'):
            self.assertIn(needle,code)
    def test_static_sql_functions_are_exactly_delimited(self):
        sql=(Path(__file__).resolve().parents[1]/
             'intelligence/migrations/207_pie_orchestrator_queue.sql').read_text('utf8')
        for name in ('spec','event','checkpoint','transition'):
            self.assertEqual(sql.count('$pie33'+name+'$'),2)


@unittest.skipUnless(os.getenv('CM_TEST_PG_DSN'),'Disposable PG17 DSN not set')
class OrchestratorPG17Optional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.db=psycopg.connect(os.environ['CM_TEST_PG_DSN'],autocommit=True)
        dbname=cls.db.execute('SELECT current_database()').fetchone()[0]
        if 'test' not in dbname or dbname in ('postgres','template1','contentmachine'):
            cls.db.close();raise AssertionError('Use disposable PostgreSQL test DB')
        migrate.apply_migrations(cls.db)
    @classmethod
    def tearDownClass(cls):
        if not cls.db.closed:cls.db.close()
    def test_fresh_upgrade_migration_in_place(self):
        from intelligence import migrate
        self.assertEqual(migrate.apply_migrations(self.db),[])
        for name in ('pie_job_execution_specs','pie_job_orchestration_events',
                     'pie_job_checkpoints'):
            self.assertIsNotNone(
                self.db.execute('SELECT to_regclass(%s)',(name,)).fetchone()[0])
        triggers={x[0] for x in self.db.execute(
            """SELECT tgname FROM pg_trigger WHERE tgrelid='pie_jobs'::regclass
               AND NOT tgisinternal""").fetchall()}
        self.assertIn('pie_job_transition_guard_update',triggers)
    def test_direct_orphan_job_spec_fails_cross_project_fk(self):
        from uuid import uuid4
        project=uuid4().hex
        self.db.execute(
            """INSERT INTO project_registry
               (id,name,profile_revision,profile_hash,created_at,updated_at)
               VALUES(%s,'PIE stage33 test',1,%s,now(),now())""",
            (project,'a'*64))
        try:
            with self.assertRaises(Exception):
                self.db.execute(
                    """INSERT INTO pie_job_execution_specs
                       (project_id,job_id,task_code,execution_policy,
                        input_sha256,spec_sha256)
                       VALUES(%s,%s,'insight_deterministic','safe_deterministic',%s,%s)""",
                    (project,uuid4(),'a'*64,'b'*64))
        finally:
            self.db.execute('DELETE FROM project_registry WHERE id=%s',(project,))


if __name__=='__main__':unittest.main()
