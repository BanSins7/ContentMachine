"""SP-16B: REAL PostgreSQL17 QV -> SP atomic inbox/ACK, replay & concurrency.

Only enabled in an explicitly marked disposable cm_sp16_test database. The
actual SP12 055 migration is installed into a randomly named private test
schema with minimal project/QV FK parents; the real production
ingest_qv_to_sp_inbox() and get_sp_handoff_status() functions run with psycopg,
not a mocked SQL connection. SP11's deterministic QA fixture provides a
projector; this does NOT claim that original production embeddings were run.
Never invokes paid LLM, Collector, network searches or fake external receipts.

Fresh/full application migrations are already applied by the PG17 CI job.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import os
import unittest
from uuid import uuid4

from intelligence.search_planner.handoff import (
    VerifiedHandoffProjector, ingest_qv_to_sp_inbox, get_sp_handoff_status,
    deliver_next_held_chunk,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical, sha256
from test_search_planner_stage10_task_planner import PID, QV, GEN, OUTBOX
from test_search_planner_stage11_handoff import synthetic_trusted_source

APPROVED_DB = "cm_sp16_test"
MIGRATION = (Path(__file__).resolve().parents[1] /
             "intelligence/migrations/055_sp_handoff_inbox.sql")


class SP16BRealInboxPostgres17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("CM_SP16_RUN_PG17") != "1":
            raise unittest.SkipTest("Real SP16B PostgreSQL17 test not explicitly enabled")
        dsn = os.getenv("CM_TEST_PG_DSN")
        if not dsn:
            raise AssertionError("SP16B requires actual disposable PostgreSQL connection")
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        if conninfo_to_dict(dsn).get("dbname") != APPROVED_DB:
            raise AssertionError("SP16B refuses non-disposable database")
        cls.pg, cls.dsn = psycopg, dsn
        cls.schema = "sp16b_" + uuid4().hex[:18]
        cls.schema_created = False
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            ver = int(conn.execute("SHOW server_version_num").fetchone()[0])
            if not 170000 <= ver < 180000:
                raise AssertionError("Real PostgreSQL17 required")
            applied = {
                row[0] for row in conn.execute(
                    "SELECT version FROM public.schema_migrations"
                ).fetchall()
            }
            if "055_sp_handoff_inbox.sql" not in applied:
                raise AssertionError("Full original SP12 migration not installed")
            conn.execute(f"CREATE SCHEMA {cls.schema}")
            cls.schema_created = True
        try:
            with cls.connect() as conn:
                conn.execute("CREATE TABLE project_registry (id TEXT PRIMARY KEY)")
                conn.execute("""CREATE TABLE qv_runs (
                    id UUID PRIMARY KEY,project_id TEXT NOT NULL
                    REFERENCES project_registry(id),
                    generation_id UUID NOT NULL,
                    embedding_mode TEXT NOT NULL,
                    selected_count INTEGER NOT NULL,
                    UNIQUE(id,project_id)
                )""")
                conn.execute("""CREATE TABLE qv_outbox (
                    id UUID PRIMARY KEY,run_id UUID NOT NULL REFERENCES qv_runs(id),
                    delivered_at TIMESTAMPTZ,
                    event_type TEXT NOT NULL,
                    payload_pointer JSONB NOT NULL,
                    delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK(delivery_attempts>=0),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")
                conn.execute("""CREATE FUNCTION sqg_reject_immutable_write()
                    RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN
                        RAISE EXCEPTION 'Cannot mutate immutable SP evidence';
                    END $$""")
                # Test the actual checked-in 055 SQL, not a simplified handoff table.
                conn.execute(MIGRATION.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO project_registry(id) VALUES (%s)", (PID,))
                conn.execute(
                    """INSERT INTO qv_runs(id,project_id,generation_id,
                                          embedding_mode,selected_count)
                       VALUES (%s::uuid,%s,%s::uuid,'semantic_local',117)""",
                    (QV, PID, GEN),
                )
                conn.execute(
                    """INSERT INTO qv_outbox(id,run_id,event_type,payload_pointer)
                       VALUES (%s::uuid,%s::uuid,'validated_queries_ready/v1',
                               %s::jsonb)""",
                    (OUTBOX,QV,canonical(cls.pointer(117))),
                )
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def connect(cls):
        return cls.pg.connect(
            cls.dsn,connect_timeout=5,
            options=f"-c search_path={cls.schema} -c statement_timeout=20000",
        )

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "schema_created", False):
            with cls.pg.connect(cls.dsn,connect_timeout=5) as conn:
                conn.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE")
            cls.schema_created = False

    @staticmethod
    def pointer(n):
        return {
            "schema_version":"validated_queries/v1",
            "validation_run_id":QV,"generation_id":GEN,
            "embedding_mode":"semantic_local","selected_count":n,
        }

    def setUp(self):
        self.source = synthetic_trusted_source(117)
        self.projector = VerifiedHandoffProjector(
            planning_reader=lambda pid, vid: deepcopy(self.source)
        )
        with self.connect() as conn:
            # Every test begins with the same untouched authoritative QV event.
            # Production 055 prevents DELETE of immutable source/evidence.
            # Reset disposable test-only tables via TRUNCATE, never product DB.
            conn.execute("""TRUNCATE TABLE
                sp_delivery_receipts,sp_delivery_attempts,sp_handoff_query_links,
                sp_handoff_chunks,sp_handoff_events CASCADE""")
            conn.execute("UPDATE qv_outbox SET delivered_at=NULL,delivery_attempts=0")
            conn.execute(
                "UPDATE qv_outbox SET payload_pointer=%s::jsonb",
                (canonical(self.pointer(117)),),
            )
            conn.execute("UPDATE qv_runs SET selected_count=117")

    def _seed_zero_work(self):
        self.source = synthetic_trusted_source(0)
        with self.connect() as conn:
            conn.execute("UPDATE qv_runs SET selected_count=0")
            conn.execute(
                "UPDATE qv_outbox SET payload_pointer=%s::jsonb",
                (canonical(self.pointer(0)),),
            )

    def _ingest(self, *, failpoint=None):
        return ingest_qv_to_sp_inbox(
            PID,QV,projector=self.projector,connect=self.connect,
            failpoint=failpoint,
        )

    def _state(self):
        with self.connect() as conn:
            event = conn.execute(
                """SELECT id,status,selected_count,chunk_count
                   FROM sp_handoff_events"""
            ).fetchall()
            chunks = conn.execute(
                """SELECT chunk_index,chunk_sha256,selected_count,chunk
                   FROM sp_handoff_chunks ORDER BY chunk_index"""
            ).fetchall()
            links = conn.execute(
                """SELECT candidate_id,chunk_index,qv_hypothesis_id,
                          provenance,provenance_sha256
                   FROM sp_handoff_query_links ORDER BY chunk_index,candidate_id"""
            ).fetchall()
            outbox = conn.execute(
                "SELECT delivered_at,delivery_attempts FROM qv_outbox"
            ).fetchone()
            receipts = conn.execute(
                "SELECT COUNT(*) FROM sp_delivery_receipts"
            ).fetchone()[0]
            attempts = conn.execute(
                "SELECT COUNT(*) FROM sp_delivery_attempts"
            ).fetchone()[0]
        return event,chunks,links,outbox,receipts,attempts

    def test_a_117_queries_commit_three_chunks_all_links_and_one_qv_ack(self):
        result = self._ingest()
        self.assertEqual(result["status"], "awaiting_collector")
        self.assertEqual(result["selected_count"], 117)
        self.assertEqual(result["chunk_count"], 3)
        self.assertFalse(result["collector_accepted"])
        self.assertFalse(result["external_search_executed"])
        events,chunks,links,outbox,receipts,attempts = self._state()
        self.assertEqual(len(events), 1)
        self.assertEqual([x[2] for x in chunks], [50,50,17])
        self.assertEqual([x[0] for x in chunks], [0,1,2])
        self.assertEqual(len(links), 117)
        self.assertEqual(len({str(row[0]) for row in links}), 117)
        self.assertEqual({str(row[0]) for row in links},
                         set(result_id for result_id in
                             self.projector.preview(PID,QV)["selected_query_ids"]))
        for candidate_id,index,hyp,provenance,digest in links:
            self.assertEqual(provenance["query_id"], str(candidate_id))
            self.assertEqual(provenance["selected_candidate"]["hypothesis_id"], hyp)
            self.assertEqual(sha256(provenance), digest)
        for index,digest,n,payload in chunks:
            self.assertEqual(payload["chunk_sha256"], digest)
            self.assertEqual(sha256({k:v for k,v in payload.items()
                                    if k!="chunk_sha256"}), digest)
        self.assertIsNotNone(outbox[0])
        self.assertEqual(outbox[1], 1)
        self.assertEqual((receipts,attempts),(0,0))
        read = get_sp_handoff_status(PID,result["event_id"],connect=self.connect)
        self.assertEqual(read["status"],"awaiting_collector")
        self.assertEqual(read["collector_accepted_chunks"], [])
        self.assertFalse(read["external_search_executed"])

    def test_b_replay_after_success_is_one_event_one_ack(self):
        first = self._ingest()
        second = self._ingest()
        self.assertTrue(second["reused"])
        self.assertEqual(first["event_id"],second["event_id"])
        event,chunks,links,outbox,receipts,attempts = self._state()
        self.assertEqual((len(event),len(chunks),len(links)),(1,3,117))
        self.assertEqual(outbox[1],1)
        self.assertEqual((receipts,attempts),(0,0))

    def test_c_crash_after_inbox_before_qv_ack_rolls_back_everything(self):
        def crash(stage):
            if stage == "after_inbox_before_qv_ack":
                raise RuntimeError("simulate transaction abort before QV ACK")
        with self.assertRaises(RuntimeError):
            self._ingest(failpoint=crash)
        event,chunks,links,outbox,receipts,attempts = self._state()
        self.assertEqual((len(event),len(chunks),len(links)),(0,0,0))
        self.assertIsNone(outbox[0])
        self.assertEqual(outbox[1],0)
        retry = self._ingest()
        self.assertFalse(retry["reused"])
        self.assertEqual(self._state()[3][1],1)

    def test_d_crash_after_ack_before_commit_rolls_back_qv_and_inbox(self):
        def crash(stage):
            if stage == "after_qv_ack_before_commit":
                raise RuntimeError("simulate transaction abort after QV ACK")
        with self.assertRaises(RuntimeError):
            self._ingest(failpoint=crash)
        events,chunks,links,outbox,_,_ = self._state()
        self.assertEqual((len(events),len(chunks),len(links)),(0,0,0))
        self.assertIsNone(outbox[0])
        self.assertEqual(outbox[1],0)
        self.assertFalse(self._ingest()["reused"])

    def test_e_eight_concurrent_ingestors_produce_one_sp_inbox(self):
        # Projector runs without a DB lock, but authoritative QV lock serializes
        # actual inserts/ACK. Simulates eight independent DB connections.
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _:self._ingest(),range(8)))
        self.assertEqual(sum(not x["reused"] for x in outcomes),1)
        self.assertEqual(len({x["event_id"] for x in outcomes}),1)
        events,chunks,links,outbox,receipts,attempts = self._state()
        self.assertEqual((len(events),len(chunks),len(links)),(1,3,117))
        self.assertEqual(outbox[1],1)
        self.assertEqual((receipts,attempts),(0,0))

    def test_f_zero_selected_creates_no_work_and_no_collector_receipt(self):
        self._seed_zero_work()
        result = self._ingest()
        self.assertEqual(result["status"],"no_work")
        self.assertEqual((result["selected_count"],result["chunk_count"]),(0,0))
        events,chunks,links,outbox,receipts,attempts = self._state()
        self.assertEqual((len(events),len(chunks),len(links)),(1,0,0))
        self.assertEqual(events[0][1],"no_work")
        self.assertEqual(outbox[1],1)
        self.assertIsNotNone(outbox[0])
        self.assertEqual((receipts,attempts),(0,0))
        self.assertTrue(self._ingest()["reused"])

    def test_g_foreign_project_cannot_read_or_ack_qv_source(self):
        foreign = "b"*32
        with self.connect() as conn:
            conn.execute("INSERT INTO project_registry(id) VALUES (%s)",(foreign,))
        with self.assertRaises(PipelineError):
            ingest_qv_to_sp_inbox(
                foreign,QV,projector=self.projector,connect=self.connect
            )
        events,chunks,links,outbox,_,_ = self._state()
        self.assertEqual((len(events),len(chunks),len(links)),(0,0,0))
        self.assertIsNone(outbox[0])

    def test_h_forged_durable_qv_pointer_cannot_ack_or_insert(self):
        with self.connect() as conn:
            conn.execute(
                "UPDATE qv_outbox SET payload_pointer=%s::jsonb",
                (canonical({**self.pointer(117),"generation_id":str(uuid4())}),),
            )
        with self.assertRaises(PipelineError):
            self._ingest()
        events,chunks,links,outbox,_,_ = self._state()
        self.assertEqual((len(events),len(chunks),len(links)),(0,0,0))
        self.assertIsNone(outbox[0])

    def test_i_stored_chunks_cannot_be_mutated_or_deleted(self):
        self._ingest()
        with self.connect() as conn:
            chunk_id = conn.execute(
                "SELECT id FROM sp_handoff_chunks LIMIT 1"
            ).fetchone()[0]
        with self.assertRaises(self.pg.Error):
            with self.connect() as conn:
                conn.execute(
                    "UPDATE sp_handoff_chunks SET selected_count=1 WHERE id=%s",
                    (chunk_id,),
                )
        with self.assertRaises(self.pg.Error):
            with self.connect() as conn:
                conn.execute(
                    "DELETE FROM sp_handoff_chunks WHERE id=%s",
                    (chunk_id,),
                )
        with self.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sp_handoff_query_links"
            ).fetchone()[0], 117)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sp_handoff_chunks"
            ).fetchone()[0], 3)

    def test_j_no_real_collector_returns_awaiting_not_fake_receipt(self):
        item = self._ingest()
        result = deliver_next_held_chunk(
            PID,item["event_id"],collector_port=None,
            owner_action_guard=None,connect=self.connect,
        )
        self.assertEqual(result["status"],"awaiting_collector")
        self.assertFalse(result["collector_available"])
        self.assertFalse(result["provider_execution_authorized"])
        self.assertEqual(result["external_source_calls"],0)
        self.assertEqual(self._state()[4:6],(0,0))

    def test_k_completed_sp_is_not_collector_acceptance(self):
        saved=self._ingest()
        status=get_sp_handoff_status(PID,saved["event_id"],connect=self.connect)
        self.assertEqual(status["collector_accepted_chunks"],[])
        self.assertEqual(status["delivery_attempts"],[])
        self.assertEqual(status["status"],"awaiting_collector")
        self.assertFalse(status["external_search_executed"])


if __name__ == "__main__":
    unittest.main()
