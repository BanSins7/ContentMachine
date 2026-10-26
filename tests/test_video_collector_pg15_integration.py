"""VC-MOD-15: disposable *real PostgreSQL 17* D1 ingestion/completion and
outbox integration with the actual 054..057 Collector migration scripts.

We use a minimal D1/QV parent-contract fixture, NOT a fabricated claim of
actual C9/SP/QV model execution. This isolates Collector migrations and the
real worker's SQL against actual PG constraints. Existing schema_v1 tests
independently migrate the FULL current repo and test old→new upgrade.
No provider calls, no live SRE delivery, no production data.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4
from unittest import TestCase, skipUnless, main
import os
import json

from intelligence.migrate import MIGRATIONS

DSN=os.environ.get("CM_TEST_PG_DSN","")
PID="a"*32
FOREIGN="b"*32
GEN="22222222-2222-4222-8222-222222222222"
PUB="11111111-1111-4111-8111-111111111111"
TASK_UUID="12345678-1234-4234-8234-123456789abc"
RUN=77
TASK=31
JOB=91
REQUEST=61
SINGLE="query_home"
# This scoped migration fixture uses the legacy offline query ID only
# for canonical D1 testing; it is NOT a production QV UUID validation test.

PARENTS=(
"""CREATE TABLE project_registry(id text PRIMARY KEY)""",
"""CREATE TABLE tb_taxonomy_publications(id uuid PRIMARY KEY)""",
"""CREATE TABLE sqg_generations(
   id uuid PRIMARY KEY,project_id text NOT NULL REFERENCES project_registry(id),
   publication_id uuid NOT NULL REFERENCES tb_taxonomy_publications(id),
   taxonomy_version integer NOT NULL,taxonomy_sha256 text NOT NULL,
   UNIQUE(id,project_id))""",
"""CREATE TABLE qv_runs(
   id uuid PRIMARY KEY,project_id text REFERENCES project_registry(id),
   generation_id uuid NOT NULL,
   UNIQUE(id,project_id),UNIQUE(id,generation_id))""",
"""CREATE TABLE qv_outbox(id uuid PRIMARY KEY,run_id uuid NOT NULL,
   UNIQUE(id,run_id))""",
"""CREATE TABLE qv_decisions(run_id uuid,candidate_id uuid,
   PRIMARY KEY(run_id,candidate_id))""",
"""CREATE TABLE qv_coverage(run_id uuid,hypothesis_id text,
   PRIMARY KEY(run_id,hypothesis_id))""",
"""CREATE TABLE sources(
   id text PRIMARY KEY,status text NOT NULL,
   retention_days integer,can_store_metadata boolean NOT NULL)""",
"""CREATE TABLE source_policy_versions(
   id bigint PRIMARY KEY,source_id text REFERENCES sources(id),
   is_active boolean NOT NULL,reviewed_at timestamptz,
   permissions jsonb NOT NULL,retention_policy jsonb NOT NULL)""",
"""CREATE TABLE search_runs(
   id bigint PRIMARY KEY,cancel_requested boolean NOT NULL DEFAULT FALSE,
   state text NOT NULL DEFAULT 'running',started_at timestamptz,
   finished_at timestamptz,error_code text)""",
"""CREATE TABLE jobs(
   id bigint PRIMARY KEY,job_type text NOT NULL,state text NOT NULL,
   locked_by text,lease_generation bigint NOT NULL DEFAULT 0,
   lease_until timestamptz,run_after timestamptz NOT NULL DEFAULT now(),
   updated_at timestamptz DEFAULT now(),result jsonb,attempts int DEFAULT 0)""",
"""CREATE TABLE source_requests(
   id bigint PRIMARY KEY,run_id bigint REFERENCES search_runs(id),
   state text NOT NULL,finished_at timestamptz,error_code text)""",
"""CREATE TABLE source_quota_ledger(
   source_id text NOT NULL REFERENCES sources(id),
   bucket_name text NOT NULL,window_start_at timestamptz NOT NULL,
   PRIMARY KEY(source_id,bucket_name,window_start_at))""",
"""CREATE TABLE videos(
   id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
   platform text NOT NULL,platform_video_id text NOT NULL,
   canonical_url text NOT NULL,published_at timestamptz,
   duration_seconds numeric(10,3),title text,content_kind text,
   UNIQUE(platform,platform_video_id))""",
"""CREATE TABLE discoveries(
   id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
   video_id bigint NOT NULL REFERENCES videos(id),
   source_id text NOT NULL REFERENCES sources(id),
   method text NOT NULL,source_rank integer,provider_request_id text,
   run_id bigint,source_request_id bigint,discovery_key text)""",
"""CREATE UNIQUE INDEX d1_discovery_key_uq ON discoveries(discovery_key)
   WHERE discovery_key IS NOT NULL""",
"""CREATE TABLE source_video_metadata(
   id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
   video_id bigint NOT NULL REFERENCES videos(id),
   source_id text NOT NULL REFERENCES sources(id),
   source_policy_version_id bigint,
   fetched_at timestamptz NOT NULL,title text,
   published_at timestamptz,duration_seconds numeric(10,3),
   expires_at timestamptz,deleted_at timestamptz,
   UNIQUE(video_id,source_id,fetched_at))""",
)
VC_FILES=(
"054_collector_planning.sql","055_collector_worker.sql",
"056_collector_qv_manifest.sql","057_collector_discovery_outbox.sql",
)


@skipUnless(DSN,"CM_TEST_PG_DSN (disposable PG17) is required")
class CollectorRealPG15(TestCase):
    def setUp(self):
        import psycopg
        self.psycopg=psycopg
        self.schema="vc15_"+uuid4().hex[:14]
        self.observed_at=datetime.now(timezone.utc).replace(microsecond=0)
        self.db=psycopg.connect(DSN,autocommit=True)
        self.db.execute("CREATE SCHEMA "+self.schema)
        self.db.execute("SET search_path TO "+self.schema+",public")
        try:
            for statement in PARENTS:
                self.db.execute(statement)
            for path in VC_FILES:
                self.db.execute((MIGRATIONS/path).read_text(encoding="utf-8"))
            self._seed()
        except Exception:
            self.tearDown()
            raise

    def tearDown(self):
        if getattr(self,"db",None):
            try:
                self.db.execute("RESET search_path")
                self.db.execute("DROP SCHEMA IF EXISTS "+self.schema+" CASCADE")
            finally:
                self.db.close()
                self.db=None

    def _seed(self):
        c=self.db
        c.execute("INSERT INTO project_registry(id) VALUES (%s),(%s)",
                  (PID,FOREIGN))
        c.execute("INSERT INTO tb_taxonomy_publications(id) VALUES (%s::uuid)",
                  (PUB,))
        c.execute("""INSERT INTO sqg_generations
                (id,project_id,publication_id,taxonomy_version,taxonomy_sha256)
                VALUES (%s::uuid,%s,%s::uuid,3,%s)""",
                  (GEN,PID,PUB,"b"*64))
        c.execute("""INSERT INTO sources(id,status,retention_days,can_store_metadata)
                     VALUES ('dtk_tiktok','enabled',30,TRUE)""")
        perms={"can_store_metadata":True,"automated_collection_authorized":True,
               "upstream_archive_disabled_confirmed":True}
        c.execute("""INSERT INTO source_policy_versions
                     (id,source_id,is_active,reviewed_at,permissions,retention_policy)
                     VALUES (12,'dtk_tiktok',TRUE,now(),%s::jsonb,%s::jsonb)""",
                  (json.dumps(perms),json.dumps({"retention_days":14})))
        c.execute("""INSERT INTO collector_query_sets
                     (id,project_id,generation_id,publication_id,validation_origin,
                      query_set,query_set_sha256)
                     VALUES (%s::uuid,%s,%s::uuid,%s::uuid,
                      'trusted_query_validation/v1',%s::jsonb,%s)""",
                  ("33333333-3333-4333-8333-333333333333",PID,GEN,PUB,
                   '{"schema_version":"validated_query_set/v1"}',"c"*64))
        c.execute("""INSERT INTO collector_plans
                     (id,project_id,query_set_id,source_id,policy_version_id,
                      plan_sha256,snapshot)
                     VALUES (%s::uuid,%s,%s::uuid,'dtk_tiktok',12,%s,%s::jsonb)""",
                  ("44444444-4444-4444-8444-444444444444",PID,
                   "33333333-3333-4333-8333-333333333333","d"*64,"{}"))
        c.execute("""INSERT INTO search_runs(id,state,cancel_requested,started_at)
                     VALUES (77,'running',FALSE,now())""")
        c.execute("""INSERT INTO collector_runs
                     (run_id,project_id,plan_id,query_set_id,policy_version_id,stage)
                     VALUES (77,%s,%s::uuid,%s::uuid,12,'released')""",
                  (PID,"44444444-4444-4444-8444-444444444444",
                   "33333333-3333-4333-8333-333333333333"))
        c.execute("""INSERT INTO jobs(id,job_type,state,locked_by,
                  lease_generation,lease_until)
                  VALUES (91,'collector.search.held','running',
                          'collector-test',5,now()+interval '15 minutes')""")
        c.execute("""INSERT INTO source_requests(id,run_id,state)
                     VALUES (61,77,'started')""")
        c.execute("""INSERT INTO collector_provider_tasks
                     (id,run_id,source_request_id,job_id,task_sha256,
                      state,provider_task_id)
                     OVERRIDING SYSTEM VALUE
                     VALUES (31,77,61,91,%s,'submitted',%s)""",
                  ("e"*64,TASK_UUID))
        c.execute("""INSERT INTO source_quota_ledger
                     (source_id,bucket_name,window_start_at)
                     VALUES ('dtk_tiktok','collector.requests',
                             date_trunc('hour',now()))""")
        c.execute("""INSERT INTO collector_execution_grants
                     (run_id,source_id,policy_version_id,quota_window_start,
                      request_cap,reserved_remaining,consent_key,consent_snapshot)
                     VALUES (77,'dtk_tiktok',12,date_trunc('hour',now()),
                             1,0,'explicit-reviewed-consent','{}'::jsonb)""")
        c.execute("""INSERT INTO collector_task_queries
                     (source_request_id,query_id,hypothesis_id,direction_id)
                     VALUES (61,%s,'hyp_home','dir_home')""",(SINGLE,))

    def context(self):
        from test_video_collector_ingest_v1 import worker_context
        return worker_context()

    def inspected(self,items=None):
        from test_video_collector_inspector_stage9 import inspect,answer,item
        # Each test pins a fresh, UTC-aware observation for deterministic
        # same-page SHA replay on any CI date.
        from unittest.mock import patch
        with patch("test_video_collector_inspector_stage9.FIXED_NOW",
                   self.observed_at):
            return inspect() if items is None else inspect(answer(items))

    def test_real_pg17_inspector_d1_immutable_receipt_outbox_and_worker_ack(self):
        from intelligence.collector.ingest import ingest_page
        from intelligence.collector.completion import finish_from_checkpoint
        from test_video_collector_inspector_stage9 import ITEM_ID
        receipt=ingest_page(self.context(),self.inspected(),connect=lambda:nullcontext(self.db))
        self.assertTrue(receipt["d1_persisted"])
        self.assertFalse(receipt["provider_completed"])
        self.assertEqual(receipt["saved_count"],1)
        p=receipt["page_id"]
        db=self.db
        self.assertEqual(db.execute("SELECT COUNT(*) FROM videos").fetchone()[0],1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0],1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM source_video_metadata").fetchone()[0],1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM collector_ingest_page_videos").fetchone()[0],1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM collector_page_video_queries").fetchone()[0],1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM collector_sre_outbox").fetchone()[0],1)
        kind=db.execute("SELECT state,event_payload->>'relevance_state' FROM collector_sre_outbox").fetchone()
        self.assertEqual(kind,("pending","not_evaluated"))
        md=db.execute("""SELECT expires_at,fetched_at FROM source_video_metadata""").fetchone()
        self.assertEqual(md[0]-md[1],timedelta(days=14))
        self.assertEqual(db.execute(
            "SELECT retention_until FROM collector_ingest_pages WHERE id=%s",(p,)
        ).fetchone()[0],md[0])
        # Invalid owner, lease, missing checkpoint may NEVER complete.
        with self.assertRaises(Exception):
            ingest_page(dict(self.context(),worker_id="stale"),
                        self.inspected(),connect=lambda:nullcontext(db))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM collector_ingest_pages").fetchone()[0],1)
        complete=finish_from_checkpoint(self.context(),receipt=receipt,connect=lambda:nullcontext(db))
        self.assertEqual(complete["status"],"completed")
        self.assertEqual(db.execute(
            "SELECT state FROM collector_provider_tasks WHERE id=31").fetchone()[0],
                         "completed")
        self.assertEqual(db.execute("SELECT state FROM jobs WHERE id=91").fetchone()[0],
                         "completed")
        self.assertEqual(db.execute("SELECT stage FROM collector_runs WHERE run_id=77").fetchone()[0],
                         "completed")
        # Terminal ACK must not acknowledge the SRE event itself.
        self.assertEqual(db.execute("SELECT state FROM collector_sre_outbox").fetchone()[0],
                         "pending")
        self.assertEqual(db.execute("SELECT platform_video_id FROM videos").fetchone()[0],
                         ITEM_ID)

    def test_real_pg17_replay_is_one_page_and_conflicting_sha_fails_closed(self):
        from intelligence.collector.ingest import ingest_page
        from intelligence.collector.contracts import CollectorError
        from test_video_collector_inspector_stage9 import item
        r=ingest_page(self.context(),self.inspected(),connect=lambda:nullcontext(self.db))
        replay=ingest_page(self.context(),self.inspected(),connect=lambda:nullcontext(self.db))
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["page_id"],r["page_id"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM videos").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM collector_sre_outbox").fetchone()[0],1)
        changed=item();changed["title"]="Changed payload"
        with self.assertRaises(CollectorError) as exc:
            ingest_page(self.context(),self.inspected([changed]),
                        connect=lambda:nullcontext(self.db))
        self.assertEqual(exc.exception.code,"ingest_checkpoint_conflict")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM collector_ingest_pages").fetchone()[0],1)

    def test_real_pg17_disposable_constraint_cross_project_and_immutable_audit(self):
        from psycopg import errors
        from intelligence.collector.ingest import ingest_page
        r=ingest_page(self.context(),self.inspected(),connect=lambda:nullcontext(self.db))
        with self.assertRaises(errors.RaiseException):
            self.db.execute("""UPDATE collector_ingest_pages
                               SET page_sha256=%s WHERE id=%s""",
                            ("a"*64,r["page_id"]))
        # Use a DIFFERENT video ID to avoid a primary-key uniqueness
        # error obscuring the actual tenant-scoping composite-FK assertion.
        other_video=self.db.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url)
               VALUES ('tiktok','7250000000000000099',
                       'https://www.tiktok.com/@test/video/7250000000000000099')
               RETURNING id"""
        ).fetchone()[0]
        other_discovery=self.db.execute(
            """INSERT INTO discoveries(video_id,source_id,method,source_rank)
               VALUES (%s,'dtk_tiktok','collector.keyword',1) RETURNING id""",
            (other_video,),
        ).fetchone()[0]
        other_metadata=self.db.execute(
            """INSERT INTO source_video_metadata(video_id,source_id,fetched_at)
               VALUES (%s,'dtk_tiktok',now()) RETURNING id""",
            (other_video,),
        ).fetchone()[0]
        with self.assertRaises(errors.ForeignKeyViolation):
            self.db.execute("""INSERT INTO collector_ingest_page_videos
                (page_id,project_id,source_request_id,video_id,discovery_id,
                 source_metadata_id,quality)
                VALUES (%s,%s,61,%s,%s,%s,'valid')""",
                (r["page_id"],FOREIGN,other_video,other_discovery,other_metadata))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM collector_ingest_page_videos").fetchone()[0],1)

    def test_real_pg17_atomic_rollback_on_violated_retention(self):
        from intelligence.collector.ingest import ingest_page
        from intelligence.collector.contracts import CollectorError
        # Expired observations must fail before video UPSERT.
        # Simulate a reviewed 1-day policy and a DB observation dated
        # more than one day ago, regardless of actual wall-clock test date.
        from unittest.mock import patch
        self.observed_at=datetime.now(timezone.utc)-timedelta(days=3)
        self.db.execute("UPDATE sources SET retention_days=1 WHERE id='dtk_tiktok'")
        with self.assertRaises(CollectorError) as exc:
            ingest_page(self.context(),self.inspected(),connect=lambda:nullcontext(self.db))
        self.assertEqual(exc.exception.code,"ingest_retention_expired")
        for table in ("videos","discoveries","collector_ingest_pages",
                      "collector_sre_outbox"):
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM "+table).fetchone()[0],0)

    def test_real_pg17_two_dispatchers_can_claim_same_sre_event_only_once(self):
        """PG SKIP LOCKED/atomic UPDATE: exactly one worker gets a lease."""
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from intelligence.collector.ingest import ingest_page
        from intelligence.collector.sre_delivery import claim_one
        ingest_page(self.context(),self.inspected(),connect=lambda:nullcontext(self.db))
        barrier=Barrier(2)
        def claim_worker(label):
            with self.psycopg.connect(DSN,autocommit=True) as conn:
                conn.execute("SET search_path TO "+self.schema+",public")
                barrier.wait(timeout=10)
                return claim_one(PID,label,connect=lambda:nullcontext(conn))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(claim_worker,"worker_pg15_a"),
                     pool.submit(claim_worker,"worker_pg15_b")]
            claims=[x.result(timeout=20) for x in futures]
        self.assertEqual(sum(v is not None for v in claims),1)
        winner=next(v for v in claims if v is not None)
        self.assertEqual(winner["attempts"],1)
        self.assertEqual(winner["lease_generation"],1)
        self.assertEqual(self.db.execute(
            "SELECT state FROM collector_sre_outbox").fetchone()[0],"leased")
        self.assertEqual(self.db.execute(
            "SELECT attempts FROM collector_sre_outbox").fetchone()[0],1)

    def test_real_pg17_committed_d1_ack_crash_recovers_without_new_provider(self):
        from intelligence.collector.ingest import ingest_page
        from intelligence.collector.completion import finish_from_checkpoint
        ingest_page(self.context(),self.inspected(),connect=lambda:nullcontext(self.db))
        before={table:self.db.execute("SELECT count(*) FROM "+table).fetchone()[0]
                for table in ("videos","discoveries","collector_ingest_pages",
                              "collector_ingest_page_videos","collector_sre_outbox")}
        done=finish_from_checkpoint(self.context(),receipt=None,
                                    connect=lambda:nullcontext(self.db))
        self.assertEqual(done["status"],"completed")
        self.assertTrue(done["replayed_checkpoint"])
        after={table:self.db.execute("SELECT count(*) FROM "+table).fetchone()[0]
               for table in before}
        self.assertEqual(before,after)
        self.assertEqual(after["collector_sre_outbox"],1)

    def test_real_pg17_lease_and_project_fencing_before_d1_write(self):
        from intelligence.collector.ingest import ingest_page
        from intelligence.collector.contracts import CollectorError
        wrong=dict(self.context(),worker_id="stale")
        with self.assertRaises(CollectorError):
            ingest_page(wrong,self.inspected(),connect=lambda:nullcontext(self.db))
        self.db.execute("""UPDATE collector_runs SET project_id=%s
                           WHERE run_id=77""",(PID,)) # stable same scope
        with self.assertRaises(CollectorError):
            ingest_page(dict(self.context(),generation=4),
                        self.inspected(),connect=lambda:nullcontext(self.db))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM collector_ingest_pages").fetchone()[0],0)


if __name__=="__main__":
    main()
