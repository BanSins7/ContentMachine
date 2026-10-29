"""UA-MOD-07 mock QDP membership and immutable handoff tests.

Positive fixtures simulate deployed peer QDP 016/040-045 + LIVE SRE,
NOT live data in standalone main. Real PG17 baseline must report unavailable.
"""
from __future__ import annotations

import copy
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

from intelligence.unified_analytics import AnalyticsBoundaryError, InputScope
from intelligence.unified_analytics.dto import ReadState
from intelligence.unified_analytics.ports import UAEPorts, CapabilityState
from intelligence.unified_analytics.qdp_adapter import (
    QDPQualifiedPins, QDPQualifiedSetReader, _qdp_hash,
    REQUIRED_RELATIONS, PROBE_SQL, RUN_SQL, GROUP_SQL, LINEAGE_SQL,
    COVERAGE_SQL, INVALIDATION_SQL,
)
from intelligence.unified_analytics.qdp_handoff import (
    QDPHandoffEvent, QDPHandoffOutcome, register_verified_handoff,
    EVENT_SQL, INSERT_SQL, STORED_SQL, QUARANTINE_SQL,
)

NOW = datetime(2026,10,26,12,tzinfo=timezone.utc)
PROJECT="a"*32
QDP="00112233-4455-4677-8899-aabbccddeeff"
SRE="11112222-3333-4444-8555-666677778888"
EVENT="22223333-4444-4555-8666-777788889999"
K1="10000000-0000-4000-8000-000000000001"
K2="10000000-0000-4000-8000-000000000002"
SCOPE=InputScope(PROJECT,17,"licensed_source",5,"video",QDP,"a"*64)


def example():
    prepare=[]
    for idx, group in enumerate("ABC"):
        inputs={
            "result_schema":"qdp-preparation/v2","group":group,
            "sre_content_version_id":81,"sre_content_sha256":"c"*64,
            "sre_discovery_id":66,"enrichment_source_id":SCOPE.source_id,
            "source_policy_version_id":5,"metadata_id":70,
        }
        if group in ("A","B"):
            inputs.update(media_asset_id=71, media_sha256="d"*64,
                          media_kind="video",media_size_bytes=120)
        else:
            inputs.update(metric_snapshot_ids=[201,202],
                          metric_history_sha256="e"*64,
                          metric_definition={"name":"views","version":"1"},
                          score_config_id=12)
        prepare.append(dict(analysis_group=group,run_id=UUID(QDP),
                            project_id=PROJECT,video_id=17,state="ready",
                            reason_code=None,input_sha256=_qdp_hash(inputs),
                            inputs=inputs,source_id=SCOPE.source_id,
                            source_policy_version_id=5,metadata_id=70,
                            media_asset_id=71 if group in ("A","B") else None))
    selected={
        "status":"qualified","content_version_id":81,
        "content_sha256":"c"*64,"category_keys":[K1],
        "result_ids":[101],"source_ids":[SCOPE.source_id],
        "source_policy_version_ids":[5],"metadata_observation_ids":[70],
        "selection_sha256":"f"*64,
    }
    manifest={
        "schema_version":"qdp-run-manifest/v1","project_id":PROJECT,
        "sre_run_id":SRE,"taxonomy_version_id":18,
        "taxonomy_sha256":"b"*64,"selection_sha256":"f"*64,
        "expected_videos":1,"expected_pairs":2,
        "by_video":{"17":selected},"totals":{"qualified":1,"needs_review":0,"excluded":0},
    }
    md=_qdp_hash(manifest)
    pins=QDPQualifiedPins(
        scope=SCOPE,sre_run_id=SRE,sre_taxonomy_version_id=18,
        taxonomy_sha256="b"*64,qdp_manifest_sha256=md,
        qdp_selected_sha256="f"*64,sre_content_version_id=81,
        sre_content_sha256="c"*64,metadata_id=70,
        preparation_shas=tuple(p["input_sha256"] for p in prepare),
        approved_category_keys=(K1,),sre_result_ids=(101,),
        group_c_snapshot_ids=(201,202),group_c_config_id=12,
    )
    run=dict(
        run_id=UUID(QDP),project_id=PROJECT,item_project_id=PROJECT,
        video_id=17,source_id=SCOPE.source_id,
        source_policy_version_id=5,content_kind="video",
        metadata_id=70,metadata_video_id=17,
        metadata_source_id=SCOPE.source_id,metadata_policy_id=5,
        run_status="completed",cancel_requested=False,item_status="ready",
        total_qualified=1,total_review=0,total_excluded=0,
        sre_run_id=UUID(SRE),sre_execution_mode="live",sre_status="completed",
        taxonomy_version_id=18,sre_taxonomy_version_id=18,
        taxonomy_status="approved",active_taxonomy_id=18,
        input_sha256=SCOPE.qdp_input_sha256,
        taxonomy_sha256="b"*64,selected_sha256="f"*64,
        manifest_sha256=md,manifest=manifest,
        expected_videos=1,expected_pairs=2,
        sre_category_keys=[K1],sre_result_ids=[101],
        metadata_current=True,metadata_deleted_at=None,
        metadata_expires_at=NOW+timedelta(days=2),
        enrichment_expires_at=NOW+timedelta(days=2),
    )
    lineage=[
        dict(category_key=K1,sre_result_id=101,content_version_id=81,
             content_sha256="c"*64,evidence_sha256="1"*64,
             model_decision="match",effective_decision="match",
             human_label_id=None,original_sre_run=UUID(SRE),
             original_project=PROJECT,result_video=17,result_category=K1,
             result_content_version=81,result_model_decision="match",
             current_label_id=None,current_human_label=None),
        dict(category_key=K2,sre_result_id=102,content_version_id=81,
             content_sha256="c"*64,evidence_sha256="2"*64,
             model_decision="no_match",effective_decision="no_match",
             human_label_id=None,original_sre_run=UUID(SRE),
             original_project=PROJECT,result_video=17,result_category=K2,
             result_content_version=81,result_model_decision="no_match",
             current_label_id=None,current_human_label=None),
    ]
    payload={
        "schema_version":"qdp-event/v1","event_type":"preparation_ready",
        "project_id":PROJECT,"run_id":QDP,"video_id":17,
        "analysis_group":"A","state":"ready","reason_code":None,
        "input_sha256":pins.preparation_shas[0],
    }
    outbox=dict(
        event_id=UUID(EVENT),project_id=PROJECT,run_id=UUID(QDP),
        video_id=17,analysis_group="A",event_type="preparation_ready",
        payload=payload,payload_sha256=_qdp_hash(payload),
    )
    return pins,run,prepare,lineage,{"is_invalidated":False},outbox


class _Cursor:
    def __init__(self,rows):
        self.rows=rows
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return self.rows


class FakeDB:
    def __init__(self,data):
        self.data=data
        self.calls=[]
        self.tables=True
        self.fail=None
        self.stored=None
        self.quarantine=[]
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if self.fail==sql:
            raise RuntimeError("postgres password=hush")
        if sql==PROBE_SQL:
            return _Cursor([{k:k if self.tables else None for k in REQUIRED_RELATIONS}])
        if sql==RUN_SQL:
            return _Cursor([self.data[1]] if self.data[1] is not None else [])
        if sql==GROUP_SQL:
            return _Cursor(self.data[2])
        if sql==LINEAGE_SQL:
            return _Cursor(self.data[3])
        if sql==COVERAGE_SQL:
            pairs = self.data[3]
            return _Cursor([{
                "covered_pairs":len(pairs),
                "covered_videos":len({x["result_video"] for x in pairs}),
                "qualified_items":1,
            }])
        if sql==INVALIDATION_SQL:
            return _Cursor([self.data[4]] if self.data[4] is not None else [])
        if sql==EVENT_SQL:
            return _Cursor([self.data[5]] if self.data[5] is not None else [])
        if sql==INSERT_SQL:
            if self.stored is not None:
                return _Cursor([])
            self.stored=dict(event_id=UUID(args[0]),project_id=args[1],
                             qdp_run_id=UUID(args[2]),video_id=args[3],
                             analysis_group=args[4],input_sha256=args[5],
                             payload_sha256=args[6],verified_reference_sha256=args[7])
            return _Cursor([{"event_id":UUID(args[0])}])
        if sql==STORED_SQL:
            return _Cursor([self.stored] if self.stored else [])
        if sql==QUARANTINE_SQL:
            self.quarantine.append(args)
            return _Cursor([])
        raise AssertionError("Unexpected QDP SQL")


class QDPQualifiedSetTests(unittest.TestCase):
    def make(self,data=None):
        d=copy.deepcopy(data if data is not None else example())
        fake=FakeDB(d)
        return QDPQualifiedSetReader(fake,d[0],as_of=NOW),fake,d
    def denial(self,data,reason):
        reader,_,_=self.make(data)
        got=reader.read_qualified_membership(SCOPE)
        self.assertIs(got.state,ReadState.MISSING)
        self.assertEqual(got.reason_code,reason)
        self.assertIsNone(got.reference)

    def test_future_verified_qdp_positive_is_conditional_mock(self):
        reader,db,_=self.make()
        self.assertIs(reader.capability().state,CapabilityState.DECLARED)
        got=reader.read_qualified_membership(SCOPE)
        self.assertIs(got.state,ReadState.LOCATED)
        self.assertEqual(got.reference.producer_schema,"qdp-handoff/v1")
        self.assertEqual(got,reader.read_qualified_membership(SCOPE))
        self.assertEqual(len(got.reference.lineage_sha256),64)
        self.assertIs(UAEPorts(qdp=reader).read("qdp",SCOPE).state,ReadState.LOCATED)
        self.assertFalse(UAEPorts(qdp=reader).status("qdp").qualified)

    def test_absent_peer_is_unavailable_never_inferred_from_ack(self):
        reader,db,_=self.make()
        db.tables=False
        got=reader.read_qualified_membership(SCOPE)
        self.assertIs(got.state,ReadState.UNAVAILABLE)
        self.assertEqual(got.reason_code,"qdp_schema_missing")
        self.assertNotIn(RUN_SQL,[s for s,_ in db.calls])
        db.tables=True
        db.fail=PROBE_SQL
        self.assertEqual(reader.capability().reason_code,"probe_failed")

    def test_incomplete_run_ack_partial_unqualified_denied(self):
        for name,value in (
            ("run_status","partial"),("run_status","running"),
            ("cancel_requested",True),("item_status","partial"),
            ("item_status","blocked"),("total_qualified",0),
            ("sre_execution_mode","offline_fixture"),("sre_status","partial"),
            ("taxonomy_status","draft"),("active_taxonomy_id",99),
        ):
            with self.subTest(field=name):
                data=list(example())
                data[1][name]=value
                self.denial(tuple(data),"qdp_not_qualified_or_sre_not_live")

    def test_cross_project_source_policy_video_kind_metadata(self):
        for name,value in (
            ("project_id","f"*32),("item_project_id","f"*32),
            ("video_id",99),("source_id","other"),
            ("source_policy_version_id",44),("content_kind","photo_carousel"),
            ("metadata_id",999),("metadata_policy_id",999),
        ):
            with self.subTest(field=name):
                data=list(example())
                data[1][name]=value
                self.denial(tuple(data),"qdp_source_or_project_mismatch")

    def test_run_manifest_sha_and_selection_version(self):
        for name,value in (
            ("input_sha256","0"*64),("selected_sha256","1"*64),
            ("taxonomy_sha256","2"*64),("manifest_sha256","3"*64),
        ):
            with self.subTest(field=name):
                data=list(example())
                data[1][name]=value
                self.denial(tuple(data),"qdp_manifest_or_run_sha_mismatch")
        data=list(example())
        data[1]["manifest"]["expected_pairs"]=123
        self.denial(tuple(data),"qdp_manifest_tampered")

    def test_exact_qualified_video_sre_content_approval_and_source(self):
        for key,value in (
            ("status","needs_review"),("content_sha256","1"*64),
            ("category_keys",[K2]),("source_ids",["other"]),
            ("metadata_observation_ids",[90]),
        ):
            with self.subTest(key=key):
                data=list(example())
                data[1]["manifest"]["by_video"]["17"][key]=value
                data[1]["manifest_sha256"]=_qdp_hash(data[1]["manifest"])
                data[0]=replace(data[0],qdp_manifest_sha256=data[1]["manifest_sha256"])
                self.denial(tuple(data),"qdp_qualified_video_pin_mismatch")

    def test_full_immutable_sre_pair_coverage_mandatory(self):
        d=list(example())
        d[3].pop()
        self.denial(tuple(d),"qdp_run_matrix_incomplete")
        d=list(example())
        d[1]["expected_pairs"]=9
        d[1]["manifest"]["expected_pairs"]=9
        d[1]["manifest_sha256"]=_qdp_hash(d[1]["manifest"])
        d[0]=replace(d[0],qdp_manifest_sha256=d[1]["manifest_sha256"])
        self.denial(tuple(d),"qdp_run_matrix_incomplete")

    def test_revocations_expiry_and_missing_groups(self):
        d=list(example());d[4]["is_invalidated"]=True
        self.denial(tuple(d),"qdp_input_invalidated")
        d=list(example());d[1]["metadata_expires_at"]=NOW
        self.denial(tuple(d),"qdp_source_expired")
        d=list(example());d[2].pop()
        self.denial(tuple(d),"qdp_current_abc_preparations_missing")

    def test_independent_A_B_C_preparation_hashes_and_rights(self):
        for ix in range(3):
            d=list(example())
            d[2][ix]["input_sha256"]="f"*64
            self.denial(tuple(d),"qdp_group_input_mismatch")
        d=list(example());d[2][2]["inputs"]["score_config_id"]=99
        self.denial(tuple(d),"qdp_group_input_mismatch")
        d=list(example());d[2][0]["state"]="blocked"
        self.denial(tuple(d),"qdp_group_input_mismatch")
        d=list(example());d[2][0]["inputs"]["media_asset_id"]=999
        d[2][0]["input_sha256"]=_qdp_hash(d[2][0]["inputs"])
        d[0]=replace(d[0],preparation_shas=(d[2][0]["input_sha256"],*d[0].preparation_shas[1:]))
        self.denial(tuple(d),"qdp_ab_media_lineage_mismatch")

    def test_lineage_exact_sre_run_model_and_human_change(self):
        for key,val,reason in (
            ("original_project","f"*32,"qdp_sre_lineage_mismatch"),
            ("result_content_version",4,"qdp_sre_lineage_mismatch"),
            ("result_model_decision","no_match","qdp_sre_lineage_mismatch"),
            ("human_label_id",100,"qdp_human_review_superseded"),
            ("effective_decision","no_match","qdp_human_review_superseded"),
        ):
            d=list(example());d[3][0][key]=val
            self.denial(tuple(d),reason)
        d=list(example());d[3][0]["sre_result_id"]=777
        self.denial(tuple(d),"qdp_qualified_set_changed")

    def test_wrong_pins_scope_and_real_database_failure(self):
        p=example()[0]
        for kw in (
            {"preparation_shas":("a"*64,)},
            {"approved_category_keys":(K2,K1)},
            {"group_c_snapshot_ids":(201,201)},
            {"sre_result_ids":(True,)},
            {"sre_run_id":"not_uuid"},
        ):
            with self.subTest(kw=kw),self.assertRaises(AnalyticsBoundaryError):
                replace(p,**kw)
        reader,db,_=self.make()
        with self.assertRaises(AnalyticsBoundaryError):
            reader.read_qualified_membership(replace(SCOPE,project_id="f"*32))
        db.fail=RUN_SQL
        with self.assertRaises(AnalyticsBoundaryError) as err:
            reader.read_qualified_membership(SCOPE)
        self.assertEqual(err.exception.code,"qdp_read_failed")
        self.assertNotIn("password",str(err.exception))


class QDPHandoffTests(unittest.TestCase):
    def make(self):
        data=copy.deepcopy(example())
        db=FakeDB(data)
        reader=QDPQualifiedSetReader(db,data[0],as_of=NOW)
        event=QDPHandoffEvent(
            EVENT,SCOPE,"A",data[0].preparation_shas[0],
            data[5]["payload_sha256"])
        return db,reader,event

    def test_first_and_duplicate_identical_event_idempotent(self):
        db,r,e=self.make()
        first=register_verified_handoff(db,r,e)
        self.assertEqual(first.state,"accepted")
        self.assertEqual(register_verified_handoff(db,r,e).state,"replayed")
        self.assertEqual(db.quarantine,[])
        self.assertEqual(len([s for s,_ in db.calls if s==INSERT_SQL]),2)

    def test_reused_id_different_payload_hash_quarantined_not_accepted(self):
        db,r,e=self.make()
        self.assertEqual(register_verified_handoff(db,r,e).state,"accepted")
        malicious=replace(e,payload_sha256="f"*64)
        outcome=register_verified_handoff(db,r,malicious)
        self.assertEqual(outcome.state,"quarantined")
        self.assertEqual(outcome.reason_code,"event_payload_collision")
        self.assertEqual(len(db.quarantine),1)
        self.assertEqual(db.stored["payload_sha256"],e.payload_sha256)

    def test_current_revoked_qdp_denies_even_old_successful_handoff(self):
        db,r,e=self.make()
        register_verified_handoff(db,r,e)
        db.data[4]["is_invalidated"]=True
        with self.assertRaises(AnalyticsBoundaryError) as x:
            register_verified_handoff(db,r,e)
        self.assertEqual(x.exception.code,"qdp_handoff_unqualified")

    def test_outbox_altered_or_missing_never_accepted(self):
        for variant in ("missing","bad_event","bad_payload"):
            db,r,e=self.make()
            if variant=="missing":
                db.data=(*db.data[:5],None)
            if variant=="bad_event":
                db.data[5]["event_type"]="run_completed"
            if variant=="bad_payload":
                db.data[5]["payload"]["state"]="blocked"
            with self.subTest(variant=variant),self.assertRaises(AnalyticsBoundaryError):
                register_verified_handoff(db,r,e)
            self.assertIsNone(db.stored)

    def test_handoff_scope_preparation_and_payload_validation(self):
        db,r,e=self.make()
        with self.assertRaises(AnalyticsBoundaryError):
            register_verified_handoff(db,r,replace(e,preparation_sha256="0"*64))
        with self.assertRaises(AnalyticsBoundaryError):
            register_verified_handoff(FakeDB(example()),r,e)
        for kw in (
            {"event_id":"wrong"},{"group":"D"},{"payload_sha256":"f"},
            {"schema_version":"unknown"},
        ):
            with self.subTest(kw=kw),self.assertRaises(AnalyticsBoundaryError):
                replace(e,**kw)


@unittest.skipUnless(os.environ.get("CM_TEST_PG_DSN"),"Disposable PG17 only")
class QDPStandalonePG17Tests(unittest.TestCase):
    def test_unmerged_qdp_tables_never_claim_current_qualified_set(self):
        import psycopg
        from psycopg.rows import dict_row
        from intelligence import migrate
        if os.environ.get("CM_TEST_PG_DSN")==os.environ.get("CM_DB_DSN"):
            self.fail("Refusing production DSN")
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as conn:
            ver=int(conn.execute("SHOW server_version_num").fetchone()[0])
            self.assertTrue(170000<=ver<180000)
            migrate.apply_migrations(conn)
            conn.row_factory=dict_row
            pins=example()[0]
            reader=QDPQualifiedSetReader(conn,pins,as_of=NOW)
            self.assertIs(reader.capability().state,CapabilityState.UNAVAILABLE)
            self.assertIs(reader.read_qualified_membership(SCOPE).state,ReadState.UNAVAILABLE)
            for table in ("uae_qdp_handoff_inbox","uae_qdp_handoff_quarantine"):
                self.assertIsNotNone(conn.execute("SELECT to_regclass(%s) AS tbl",(table,)).fetchone()["tbl"])


if __name__=="__main__":
    unittest.main()
