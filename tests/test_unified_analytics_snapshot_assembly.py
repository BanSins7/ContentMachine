"""UA-MOD-14 immutable reference-only snapshots, replay/collision and PG17.

All positive selected contexts are MOCKS, not producer-approved data.
Actual Group C remains frozen_unreviewed so no production qualified writes.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import copy
import os
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError, InputScope, EvidenceRef
from intelligence.unified_analytics.joined_context import (
    JoinedContextAssembler, JoinedSelection, JoinedInputContext,
    CandidateExclusion, REQUIRED_PORTS,
)
from intelligence.unified_analytics.snapshot_assembly import (
    SNAPSHOT_ASSEMBLY_SCHEMA, canonical_decimal, normalize_payload,
    SnapshotEvidence, ImmutableSnapshotAssembler,
    ImmutableSnapshotRepository,
    CLOCK_SQL, REQUEST_SQL, SERIALIZE_SQL, REPLAY_SQL,
    REPLAY_INTEGRITY_SQL, VERSION_SQL, INSERT_SNAPSHOT_SQL,
    INSERT_EVIDENCE_SQL, INSERT_PAYLOAD_SQL,
)
from intelligence.unified_analytics.qualification_orchestrator import (
    QualificationStatus,
)

NOW=datetime(2026,10,27,7,5,tzinfo=timezone.utc)
SCOPE=InputScope(
    "a"*32,17,"licensed_source",9,"video",
    "00112233-4455-4677-8899-aabbccddeeff","b"*64,
)
REQUEST="11112222-3333-4444-8555-666677778888"
FP="a"*64
LINEAGE_SHA="d"*64


def pins():
    """Simulated candidate identity only. Real constructor validates producers."""
    return SimpleNamespace(
        fingerprint=FP,
        lineage=SimpleNamespace(
            sre_run_id="22223333-4444-4555-8666-777788889999",
            sre_taxonomy_version_id=31,
            sre_content_version_id=41,
            sre_decision_sha256="1"*64,
        ),
        qdp=SimpleNamespace(preparation_shas=("2"*64,"3"*64,"4"*64)),
        group_a=SimpleNamespace(
            analysis_id=101,result_sha256="5"*64,
            media_manifest_sha256="6"*64,analysis_version="A3",
        ),
        group_b=SimpleNamespace(
            b_analysis_id=102,model_version="model-v5",
            prompt_version="prompt-v4",taxonomy_version="taxonomy-v3",
            block_evidence_sha256s=tuple(str(i)*64 for i in range(1,9)),
        ),
        group_c=SimpleNamespace(
            calculated_metric_id=103,previous_snapshot_id=201,
            current_snapshot_id=202,source_metric_version="1.0",
            config_id=12,config_version="gc-v1",
            creator_freeze_sha256="7"*64,
            niche_freeze_sha256=None,prior_freeze_sha256="8"*64,
        ),
    )


def future_claim(scope=SCOPE,as_of=NOW):
    refs=tuple(EvidenceRef(
        scope,port,port+":v1","uae-mock-ref/v1","mock-future-v1","c"*64
    ) for port in REQUIRED_PORTS)
    return JoinedInputContext(
        scope,as_of,FP,LINEAGE_SHA,"e"*64,refs,"f"*64,
    )


class FakeAssembler(JoinedContextAssembler):
    def __init__(self,db,*,result=None,as_of=NOW):
        self._conn=db
        self._scope=SCOPE
        self._as_of=as_of
        self._candidates=(pins(),)
        self._selection=(
            result if result is not None
            else JoinedSelection("complete_claims_unapproved",
                                 future_claim(as_of=as_of),(),1)
        )
        self.calls=0
    def select(self):
        self.calls+=1
        return self._selection


class Cursor:
    def __init__(self,rows):
        self.rows=rows
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return self.rows


class FakeDB:
    def __init__(self):
        self.db_now=NOW+timedelta(seconds=2)
        self.request=dict(
            request_id=REQUEST,project_id=SCOPE.project_id,
            video_id=SCOPE.video_id,source_id=SCOPE.source_id,
            source_policy_version_id=SCOPE.source_policy_version_id,
            qdp_run_id=SCOPE.qdp_run_id,qdp_input_sha256=SCOPE.qdp_input_sha256,
            content_kind=SCOPE.content_kind,state="registered",
        )
        self.snapshots={}
        self.evidence=[]
        self.payloads={}
        self._next_id=1
        self.calls=[]
        self.fault=None
        self.tx_depth=0
    @contextmanager
    def transaction(self):
        checkpoint=copy.deepcopy((
            self.snapshots,self.evidence,self.payloads,self._next_id,
        ))
        self.tx_depth+=1
        try:
            yield
        except Exception:
            self.snapshots,self.evidence,self.payloads,self._next_id=checkpoint
            raise
        finally:
            self.tx_depth-=1
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if sql==self.fault:
            raise RuntimeError("connection password=secret_not_for_logs")
        if sql=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ":
            if not self.tx_depth: raise RuntimeError("not in transaction")
            return Cursor([])
        if sql==CLOCK_SQL:
            return Cursor([{"db_now":self.db_now}])
        if sql==REQUEST_SQL:
            return Cursor([self.request] if self.request else [])
        if sql==SERIALIZE_SQL:
            return Cursor([])
        if sql==REPLAY_SQL:
            for sid,s in self.snapshots.items():
                if (s["inputs_sha256"]==args[2]
                    and s["project_id"]==args[0]
                    and s["request_id"]==args[1]):
                    p=self.payloads[sid]
                    return Cursor([{
                        "id":sid,"snapshot_version":s["version"],
                        "status":s["status"],
                        "scope_sha256":s["scope_sha256"],
                        "inputs_sha256":s["inputs_sha256"],
                        "decision_sha256":s["decision_sha256"],
                        "payload":copy.deepcopy(p["payload"]),
                        "payload_sha256":p["payload_sha256"],
                        "evidence_set_sha256":p["evidence_set_sha256"],
                    }])
            return Cursor([])
        if sql==REPLAY_INTEGRITY_SQL:
            return Cursor(sorted((
                r for r in self.evidence if r["snapshot_id"]==args[1]
            ),key=lambda r:r["port"]))
        if sql==VERSION_SQL:
            return Cursor([{"next_version":max((
                s["version"] for s in self.snapshots.values()
                if s["project_id"]==args[0] and s["video_id"]==args[1]
                and s["source_id"]==args[2]
            ),default=0)+1}])
        if sql==INSERT_SNAPSHOT_SQL:
            sid=self._next_id
            self._next_id+=1
            self.snapshots[sid]=dict(
                project_id=args[0],request_id=args[1],
                video_id=args[2],source_id=args[3],version=args[9],
                status=args[12],scope_sha256=args[13],
                inputs_sha256=args[14],decision_sha256=args[15],
            )
            return Cursor([{"id":sid}])
        if sql==INSERT_EVIDENCE_SQL:
            self.evidence.append(dict(
                project_id=args[0],snapshot_id=args[1],
                port=args[2],state=args[3],
                checked=False,is_current=False,
                record_key=args[4],producer_schema=args[5],
                producer_version=args[6],evidence_sha256=args[7],
                lineage_sha256=args[8],reason_code=args[9],
            ))
            return Cursor([])
        if sql==INSERT_PAYLOAD_SQL:
            self.payloads[args[1]]=dict(
                project_id=args[0],
                payload=__import__("json").loads(args[3]),
                payload_sha256=args[4],evidence_set_sha256=args[5],
                decision_sha256=args[6],
            )
            return Cursor([])
        raise AssertionError("Unexpected SQL executed")


class SnapshotAssemblyTests(unittest.TestCase):
    def test_unapproved_all_seven_producer_refs_stored_as_partial(self):
        db=FakeDB()
        asm=FakeAssembler(db)
        draft=ImmutableSnapshotAssembler(asm).assemble()
        self.assertIs(draft.decision.status,QualificationStatus.PARTIAL)
        self.assertFalse(draft.qualified)
        self.assertEqual(tuple(e.port for e in draft.evidence),REQUIRED_PORTS)
        self.assertEqual(set(e.state for e in draft.evidence),{"partial"})
        self.assertEqual(draft.payload["status"],"partial")
        self.assertEqual(draft.payload["schema_version"],SNAPSHOT_ASSEMBLY_SCHEMA)
        self.assertTrue(all(
            not item["checked"] and not item["is_current"]
            for item in draft.payload["evidence"]
        ))
        self.assertEqual(draft.payload["versions"]["group_a_analysis_id"],101)
        self.assertEqual(draft.payload["versions"]["group_b_analysis_id"],102)
        self.assertEqual(draft.payload["versions"]["calculated_metric_id"],103)
        self.assertIsNone(draft.payload["versions"]["niche_freeze_sha256"])
        self.assertNotIn("raw_media",str(draft.payload))
        self.assertEqual(len(draft.payload_sha256),64)

    def test_source_backed_candidate_assembler_called_once_per_draft(self):
        asm=FakeAssembler(FakeDB())
        draft=ImmutableSnapshotAssembler(asm).assemble()
        self.assertEqual(asm.calls,1)
        self.assertEqual(draft.decision.candidate_sha256,FP)

    def test_blocked_snapshot_has_seven_unknown_refs_not_synthetic_zero(self):
        selection=JoinedSelection(
            "blocked",None,
            (CandidateExclusion(FP,"source_permission_intersection_denied"),),1,
        )
        asm=FakeAssembler(FakeDB(),result=selection)
        draft=ImmutableSnapshotAssembler(asm).assemble()
        self.assertEqual(draft.decision.status,QualificationStatus.BLOCKED)
        self.assertTrue(all(x.state=="missing" for x in draft.evidence))
        self.assertIsNone(draft.payload["versions"])
        self.assertTrue(all(x.record_key is None for x in draft.evidence))
        self.assertEqual(
            next(x.reason_code for x in draft.evidence if x.port=="source_policy"),
            "source_permission_intersection_denied",
        )

    def test_partial_missing_group_c_cannot_fabricate_versioned_data(self):
        selection=JoinedSelection(
            "blocked",None,
            (CandidateExclusion(FP,"join_group_c_not_current"),),1,
        )
        draft=ImmutableSnapshotAssembler(
            FakeAssembler(FakeDB(),result=selection)
        ).assemble()
        self.assertEqual(draft.decision.status,QualificationStatus.PARTIAL)
        self.assertIsNone(draft.payload["versions"])
        self.assertTrue(all(x.record_key is None for x in draft.evidence))
        self.assertFalse(draft.qualified)

    def test_byte_identical_replay_same_scope_time_version(self):
        asm=FakeAssembler(FakeDB())
        first=ImmutableSnapshotAssembler(asm).assemble()
        second=ImmutableSnapshotAssembler(asm).assemble()
        self.assertEqual(first.payload,second.payload)
        self.assertEqual(first.payload_sha256,second.payload_sha256)
        self.assertEqual(first.evidence_set_sha256,second.evidence_set_sha256)
        self.assertEqual(first.decision.decision_sha256,
                         second.decision.decision_sha256)

    def test_decimal_normalization_stable_without_float(self):
        self.assertEqual(canonical_decimal(Decimal("001.25000")),"1.25")
        self.assertEqual(canonical_decimal(Decimal("1.25")),"1.25")
        self.assertEqual(canonical_decimal(Decimal("-0.000")),"0")
        self.assertEqual(canonical_decimal(Decimal("1e+3")),"1000")
        self.assertEqual(
            normalize_payload({"z":(Decimal("1.20"),),"a":Decimal("1.2")}),
            {"a":"1.2","z":["1.2"]},
        )
        for bad in (Decimal("NaN"),Decimal("Infinity")):
            with self.assertRaises(AnalyticsBoundaryError):
                canonical_decimal(bad)
        for bad in (1.0,float("nan"),object()):
            with self.assertRaises(AnalyticsBoundaryError):
                normalize_payload({"bad":bad})

    def test_incompatible_or_oversized_payload_denied(self):
        for value in ({"x":[0]*257},{"x":str("a"*8193)},
                      {1:"bad"},[[[[[[[[[[[[[0]]]]]]]]]]]]]):
            with self.subTest(size=type(value).__name__):
                with self.assertRaises(AnalyticsBoundaryError):
                    normalize_payload(value)

    def test_replay_request_id_is_immutable_and_atomic(self):
        db=FakeDB()
        asm=FakeAssembler(db)
        repo=ImmutableSnapshotRepository(asm,REQUEST)
        first=repo.assemble_and_save()
        second=repo.assemble_and_save()
        self.assertEqual(first.snapshot_id,second.snapshot_id)
        self.assertEqual(first.snapshot_version,1)
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(len(db.snapshots),1)
        self.assertEqual(len(db.evidence),7)
        self.assertEqual(len(db.payloads),1)
        self.assertEqual(db.tx_depth,0)

    def test_new_as_of_version_creates_new_historical_snapshot(self):
        db=FakeDB()
        first=ImmutableSnapshotRepository(FakeAssembler(db),REQUEST).assemble_and_save()
        t=NOW+timedelta(milliseconds=100)
        second=ImmutableSnapshotRepository(
            FakeAssembler(db,as_of=t),REQUEST,
        ).assemble_and_save()
        self.assertEqual((first.snapshot_version,second.snapshot_version),(1,2))
        self.assertNotEqual(first.payload_sha256,second.payload_sha256)
        self.assertEqual(len(db.snapshots),2)
        self.assertEqual(len(db.evidence),14)
        self.assertFalse(second.replayed)

    def test_wrong_request_scope_fails_with_no_partial_history(self):
        db=FakeDB()
        db.request["source_id"]="wrong"
        with self.assertRaises(AnalyticsBoundaryError) as e:
            ImmutableSnapshotRepository(FakeAssembler(db),REQUEST).assemble_and_save()
        self.assertEqual(e.exception.code,"snapshot_request_scope_mismatch")
        self.assertEqual(len(db.snapshots),0)
        self.assertEqual(len(db.evidence),0)
        self.assertEqual(len(db.payloads),0)

    def test_bogus_request_uuid_rejected(self):
        with self.assertRaises(AnalyticsBoundaryError):
            ImmutableSnapshotRepository(FakeAssembler(FakeDB()),"fake")
        with self.assertRaises(AnalyticsBoundaryError):
            ImmutableSnapshotAssembler(object())

    def test_resolved_request_allows_only_exact_replay(self):
        db=FakeDB()
        repo=ImmutableSnapshotRepository(FakeAssembler(db),REQUEST)
        old=repo.assemble_and_save()
        db.request["state"]="resolved"
        self.assertTrue(repo.assemble_and_save().replayed)
        with self.assertRaises(AnalyticsBoundaryError) as e:
            ImmutableSnapshotRepository(
                FakeAssembler(db,as_of=NOW+timedelta(seconds=1)),REQUEST
            ).assemble_and_save()
        self.assertEqual(e.exception.code,
                         "snapshot_resolved_request_changed_input")

    def test_stale_or_future_as_of_rejected(self):
        db=FakeDB()
        db.db_now=NOW-timedelta(seconds=1)
        with self.assertRaises(AnalyticsBoundaryError) as e:
            ImmutableSnapshotRepository(FakeAssembler(db),REQUEST).assemble_and_save()
        self.assertEqual(e.exception.code,"snapshot_stale_evaluation_clock")
        db.db_now=NOW+timedelta(seconds=301)
        with self.assertRaises(AnalyticsBoundaryError):
            ImmutableSnapshotRepository(FakeAssembler(db),REQUEST).assemble_and_save()

    def test_no_connection_transaction_refuses_write(self):
        asm=FakeAssembler(SimpleNamespace())
        with self.assertRaises(AnalyticsBoundaryError) as e:
            ImmutableSnapshotRepository(asm,REQUEST).assemble_and_save()
        self.assertEqual(e.exception.code,"snapshot_transaction_required")

    def test_failure_in_middle_of_seven_evidence_is_atomic(self):
        db=FakeDB()
        db.fault=INSERT_EVIDENCE_SQL
        with self.assertRaises(AnalyticsBoundaryError):
            ImmutableSnapshotRepository(FakeAssembler(db),REQUEST).assemble_and_save()
        self.assertEqual(len(db.snapshots),0)
        self.assertEqual(len(db.evidence),0)
        self.assertEqual(len(db.payloads),0)

    def test_failure_on_payload_insert_rolls_back_snapshot_and_seven_ports(self):
        db=FakeDB()
        db.fault=INSERT_PAYLOAD_SQL
        with self.assertRaises(AnalyticsBoundaryError):
            ImmutableSnapshotRepository(FakeAssembler(db),REQUEST).assemble_and_save()
        self.assertEqual(len(db.snapshots),0)
        self.assertEqual(len(db.evidence),0)

    def test_idempotency_collision_rejected_without_new_version(self):
        db=FakeDB()
        repo=ImmutableSnapshotRepository(FakeAssembler(db),REQUEST)
        repo.assemble_and_save()
        db.payloads[1]["payload_sha256"]="f"*64
        with self.assertRaises(AnalyticsBoundaryError) as e:
            repo.assemble_and_save()
        self.assertEqual(e.exception.code,"snapshot_idempotency_collision")
        self.assertEqual(len(db.snapshots),1)

    def test_replay_payload_content_tamper_detected_even_if_digest_unchanged(self):
        db=FakeDB()
        repo=ImmutableSnapshotRepository(FakeAssembler(db),REQUEST)
        repo.assemble_and_save()
        db.payloads[1]["payload"]["status"]="blocked"
        with self.assertRaises(AnalyticsBoundaryError) as e:
            repo.assemble_and_save()
        self.assertEqual(e.exception.code,"snapshot_idempotency_collision")

    def test_excessive_decimal_exponent_rejected_without_allocating(self):
        with self.assertRaises(AnalyticsBoundaryError) as e:
            canonical_decimal(Decimal("1E+9999999"))
        self.assertEqual(e.exception.code,"snapshot_decimal_too_large")

    def test_replay_ref_cannot_be_turned_to_checked_true(self):
        db=FakeDB()
        repo=ImmutableSnapshotRepository(FakeAssembler(db),REQUEST)
        repo.assemble_and_save()
        db.evidence[3]["checked"]=True
        with self.assertRaises(AnalyticsBoundaryError) as e:
            repo.assemble_and_save()
        self.assertEqual(e.exception.code,
                         "snapshot_replay_evidence_approval_forged")

    def test_missing_replay_reference_is_detected(self):
        db=FakeDB()
        repo=ImmutableSnapshotRepository(FakeAssembler(db),REQUEST)
        repo.assemble_and_save()
        db.evidence.pop()
        with self.assertRaises(AnalyticsBoundaryError) as e:
            repo.assemble_and_save()
        self.assertEqual(e.exception.code,"snapshot_replay_evidence_incomplete")

    def test_db_secrets_not_leaked_on_failed_write(self):
        db=FakeDB()
        db.fault=CLOCK_SQL
        with self.assertRaises(AnalyticsBoundaryError) as e:
            ImmutableSnapshotRepository(FakeAssembler(db),REQUEST).assemble_and_save()
        self.assertEqual(e.exception.code,"snapshot_db_unavailable")
        self.assertNotIn("password",str(e.exception))

    def test_no_qualified_is_ever_inserted(self):
        db=FakeDB()
        ImmutableSnapshotRepository(FakeAssembler(db),REQUEST).assemble_and_save()
        self.assertTrue(all(
            s["status"] in ("partial","blocked") for s in db.snapshots.values()
        ))


class SnapshotConcurrencyUnitTests(unittest.TestCase):
    class Conflict(Exception):
        def __init__(self,code,constraint=None):
            super().__init__("synthetic database error - never shown to callers")
            self.sqlstate=code
            self.diag=SimpleNamespace(constraint_name=constraint)

    class RacyDB(FakeDB):
        def __init__(self,code="40001",remaining=1,constraint=None):
            super().__init__()
            self.code=code
            self.remaining=remaining
            self.constraint=constraint
            self.conflicts_seen=0
        def execute(self,sql,args=()):
            if sql==INSERT_SNAPSHOT_SQL and self.remaining>0:
                self.remaining-=1
                self.conflicts_seen+=1
                raise SnapshotConcurrencyUnitTests.Conflict(self.code,self.constraint)
            return super().execute(sql,args)

    def test_pg_serialization_retry_uses_a_fresh_transaction_and_rereads_join(self):
        db=self.RacyDB("40001",remaining=1)
        source=FakeAssembler(db)
        result=ImmutableSnapshotRepository(source,REQUEST).assemble_and_save()
        self.assertEqual(result.snapshot_version,1)
        self.assertEqual(source.calls,2)
        self.assertEqual(db.conflicts_seen,1)
        self.assertEqual(len(db.snapshots),1)
        self.assertEqual(len(db.evidence),7)
        self.assertEqual(db.tx_depth,0)

    def test_pg_deadlock_retry_rechecks_join(self):
        db=self.RacyDB("40P01",remaining=1)
        source=FakeAssembler(db)
        result=ImmutableSnapshotRepository(source,REQUEST).assemble_and_save()
        self.assertFalse(result.replayed)
        self.assertEqual(source.calls,2)

    def test_pg_snapshot_unique_version_conflict_retries_only_for_uae(self):
        db=self.RacyDB(
            "23505",remaining=1,
            constraint="uae_unified_snapshots_project_id_video_id_source_id_snapshot_version_key",
        )
        source=FakeAssembler(db)
        result=ImmutableSnapshotRepository(source,REQUEST).assemble_and_save()
        self.assertEqual(source.calls,2)
        self.assertEqual(result.snapshot_version,1)

    def test_foreign_unique_violation_does_not_retry(self):
        db=self.RacyDB("23505",remaining=1,constraint="other_table_key")
        source=FakeAssembler(db)
        with self.assertRaises(AnalyticsBoundaryError) as exc:
            ImmutableSnapshotRepository(source,REQUEST).assemble_and_save()
        self.assertEqual(exc.exception.code,"snapshot_write_unavailable")
        self.assertEqual(source.calls,1)
        self.assertFalse(db.snapshots)

    def test_four_retry_attempts_are_bounded_and_do_not_emit_half_written_data(self):
        db=self.RacyDB("40001",remaining=999)
        source=FakeAssembler(db)
        with self.assertRaises(AnalyticsBoundaryError) as exc:
            ImmutableSnapshotRepository(source,REQUEST).assemble_and_save()
        self.assertEqual(exc.exception.code,"snapshot_concurrency_exhausted")
        self.assertEqual(source.calls,4)
        self.assertEqual(db.conflicts_seen,4)
        self.assertEqual(len(db.snapshots),0)
        self.assertEqual(len(db.evidence),0)
        self.assertEqual(db.tx_depth,0)

    def test_preexisting_nonidle_transaction_not_treated_as_fresh(self):
        db=FakeDB()
        db.info=SimpleNamespace(transaction_status=2)
        with self.assertRaises(AnalyticsBoundaryError) as exc:
            ImmutableSnapshotRepository(FakeAssembler(db),REQUEST).assemble_and_save()
        self.assertEqual(exc.exception.code,"snapshot_fresh_transaction_required")
        self.assertFalse(db.snapshots)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PostgreSQL 17 only")
class SnapshotAssemblyPG17(unittest.TestCase):
    def test_schema_replay_and_unapproved_payload_is_immutable(self):
        import psycopg
        from psycopg.rows import dict_row
        from intelligence import migrate
        dsn=os.environ["CM_TEST_PG_DSN"]
        if dsn==os.environ.get("CM_DB_DSN"):
            self.fail("Refuse production DB")
        with psycopg.connect(dsn,autocommit=True) as db:
            self.assertTrue(
                170000<=int(db.execute("SHOW server_version_num").fetchone()[0])<180000
            )
            migrate.apply_migrations(db)
            self.assertEqual(migrate.apply_migrations(db),[])
            db.row_factory=dict_row
            self.assertEqual(
                db.execute("SELECT count(*) AS n FROM uae_snapshot_payloads").fetchone()["n"],0
            )
            with self.assertRaises(psycopg.errors.CheckViolation):
                with db.transaction():
                    db.execute(
                        "INSERT INTO uae_snapshot_payloads("
                        "project_id,snapshot_id,assembly_schema,payload,"
                        "payload_sha256,evidence_set_sha256,decision_sha256) "
                        "VALUES(%s,999999,'uae-snapshot-assembly/v1',"
                        "%s::jsonb,%s,%s,%s)",
                        (SCOPE.project_id,
                         '{"schema_version":"uae-snapshot-assembly/v1",'
                         '"status":"partial","evidence":[1,2,3,4,5,6,7],'
                         '"readiness":{},"blockers":[],'
                         '"decision_sha256":"'+("a"*64)+'",'
                         '"evidence_set_sha256":"'+("b"*64)+'"}',
                         "c"*64,"b"*64,"a"*64),
                    )


if __name__=="__main__":
    unittest.main()
