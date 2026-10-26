"""UA-MOD-09 mock regression for exact Group B B1–B8 reader.

Future-positive mock rows are NOT proof of real GB worker, QDP, GA or human
approvals. The standalone PG17 fixture checks peer schema unavailability.
"""
from __future__ import annotations

import copy
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from intelligence.unified_analytics import AnalyticsBoundaryError, InputScope
from intelligence.unified_analytics.dto import EvidenceRead, EvidenceRef, ReadState
from intelligence.unified_analytics.ports import (
    UAEPorts, PortCapability, CapabilityState, PORT_SCHEMAS,
)
from intelligence.unified_analytics.group_a_adapter import GroupAVerifiedFactsReader
from intelligence.unified_analytics.qdp_adapter import QDPQualifiedSetReader
from intelligence.unified_analytics.group_b_adapter import (
    BLOCKS, B_READER_WIRE, GroupBExactPins, GroupBExactReader,
    REQUIRED_RELATIONS, PROBE_SQL, RUN_SQL, BLOCK_SQL, REVIEW_SQL,
    COMPONENT_SQL, CAROUSEL_NONAPPLICABLE_SQL,
)

NOW=datetime(2026,10,26,15,tzinfo=timezone.utc)
PROJECT="a"*32
QDP="00112233-4455-4677-8899-aabbccddeeff"
GB="11112222-3333-4444-8555-666677778888"
EVENT="22223333-4444-4555-8666-777788889999"
SOURCE="licensed_source"
SHA="a"*64
A_RESULT="c"*64
A_MANIFEST="d"*64
QDP_B="b"*64
SCOPE=InputScope(PROJECT,17,SOURCE,9,"video",QDP,SHA)


def scenario(kind="video"):
    scope=replace(SCOPE,content_kind=kind)
    video=kind in ("video","slideshow_video")
    pins=GroupBExactPins(
        scope=scope,b_run_id=GB,b_analysis_id=41,
        qdp_b_preparation_sha256=QDP_B,
        group_a_analysis_id=31,group_a_result_sha256=A_RESULT,
        group_a_media_manifest_sha256=A_MANIFEST,
        config_sha256="e"*64,analysis_version="b-v2",
        taxonomy_version="taxonomy/3",method_version="model-method-v1",
        event_id=EVENT,event_sha256="f"*64,
        model_name="audited-model",model_version="model-v5",
        prompt_version="prompt-v4",
    )
    a_parent="video_a_analysis_id" if video else "carousel_a_analysis_id"
    a_other="carousel_a_analysis_id" if video else "video_a_analysis_id"
    run=dict(
        run_id=GB,project_id=scope.project_id,video_id=17,
        source_id=SOURCE,source_policy_version_id=9,
        content_type="video" if video else "carousel",
        run_state="completed",result_analysis_id=41,analysis_id=41,
        analysis_video_id=17,analysis_source_id=SOURCE,analysis_policy_id=9,
        analysis_content_type="video" if video else "carousel",
        analysis_state="completed",needs_review=False,
        a_snapshot_sha256=A_RESULT,media_manifest_sha256=A_MANIFEST,
        config_sha256="e"*64,analysis_version="b-v2",
        taxonomy_version="taxonomy/3",method_name="audited-workflow",
        method_version="model-method-v1",model_name="audited-model",
        model_version="model-v5",prompt_version="prompt-v4",
        run_updated_at=NOW-timedelta(minutes=5),
        analyzed_at=NOW-timedelta(minutes=4),unavailable_reasons={},
        inbox_id=EVENT,inbox_status="accepted",
        event_key=EVENT,event_sha256="f"*64,
        preparation_sha256=QDP_B,qdp_run_id=QDP,
        producer_schema="qdp-handoff/v1",receipt_event_id=EVENT,
        receipt_event_sha256="f"*64,
        receipt_preparation_sha256=QDP_B,
        source_state="enabled",can_analyze_media=True,
        policy_active=True,policy_permissions={"can_analyze_media":True},
        **{a_parent:31,a_other:None},
        **{("analysis_video_a_id" if video else "analysis_carousel_a_id"):31,
           ("analysis_carousel_a_id" if video else "analysis_video_a_id"):None},
    )
    blocks=[
        dict(block=b,status="complete",evidence_sha256=str(i+1)*64,error_code=None)
        for i,b in enumerate(BLOCKS)
    ]
    if not video:
        for ix in (4,5):
            blocks[ix].update(status="unavailable",evidence_sha256=None,
                              error_code=("editing_video_only" if ix==4
                                          else "video_subtitles_only"))
    facts={
        "B1":dict(classification_status="inferred",taxonomy_version="taxonomy/3",
                  mechanisms=["curiosity"],delivery=["on_screen_text"],
                  start_sec=Decimal("0") if video else None,
                  end_sec=Decimal("2") if video else None,
                  slide_index=None if video else 1,parts=1),
        "B2":dict(classification_status="inferred",taxonomy_version="taxonomy/3",
                  video_primary="talking_head" if video else None,
                  video_secondary=None,
                  carousel_format=None if video else "step_by_step"),
        "B3":dict(frames=2 if video else 0,intervals=1 if video else 0,
                  slides=0 if video else 2,sampling_rows=1 if video else 0,
                  frame_reviews=0,interval_reviews=0,slide_reviews=0),
        "B4":dict(ai_generated=None,person_visible=True,
                  product_visible=False,logo_visible=False),
        "B5":dict(editing_type="single_take",cut_count=0,
                  cuts_per_minute=Decimal("0"),
                  avg_shot_duration=Decimal("10"),
                  cuts_first_5_sec=0),
        "B6":dict(subtitle_type="none",position=None,font_size=None,
                  font_style=None,text_color=None,highlight_color=None,
                  background_style=None,display_mode=None),
        "B7":dict(analysis_status="complete",taxonomy_version="taxonomy/3",
                  analyzed_at=NOW-timedelta(minutes=5),occurrence_count=0,
                  review_count=0,distinct_count=0,last_occurrence=None),
        "B8":dict(taxonomy_version="taxonomy/3",needs_review=False,
                  category_id=21,topic_id=22,subtopic_id=None,
                  topic_category=21),
    }
    return dict(pins=pins,run=run,blocks=blocks,review=None,facts=facts,
                nonapplicable=dict(editing_rows=0,subtitle_rows=0,
                                   frame_rows=0,interval_rows=0))


class Cursor:
    def __init__(self,rows):
        self.rows=rows
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return self.rows


class DB:
    def __init__(self,data):
        self.data=data
        self.calls=[]
        self.schema=True
        self.fail=None
    def execute(self,sql,params=()):
        self.calls.append((sql,params))
        if sql==self.fail:
            raise RuntimeError("DB password=must-not-surface")
        if sql==PROBE_SQL:
            return Cursor([{n:n if self.schema else None for n in REQUIRED_RELATIONS}])
        if sql==RUN_SQL:
            return Cursor([self.data["run"]] if self.data["run"] else [])
        if sql==BLOCK_SQL:
            return Cursor(self.data["blocks"])
        if sql==REVIEW_SQL:
            return Cursor([self.data["review"]] if self.data["review"] else [])
        for name,statement in COMPONENT_SQL.items():
            if sql==statement:
                value=self.data["facts"].get(name)
                return Cursor([value] if value else [])
        if sql==CAROUSEL_NONAPPLICABLE_SQL:
            return Cursor([self.data["nonapplicable"]])
        raise AssertionError("Unexpected SQL")


class AStub(GroupAVerifiedFactsReader):
    """Mock of *already verified* A reader, never production proof."""
    def __init__(self,conn,pins):
        self._conn=conn
        self._pins=SimpleNamespace(
            scope=pins.scope,analysis_id=pins.group_a_analysis_id,
            result_sha256=pins.group_a_result_sha256,
            media_manifest_sha256=pins.group_a_media_manifest_sha256,
        )
        self.allowed=True
    def capability(self):
        return PortCapability("group_a",CapabilityState.DECLARED,
            PORT_SCHEMAS["group_a"],"group-a-to-b/v1") if self.allowed else (
            PortCapability("group_a",CapabilityState.UNAVAILABLE,
                           reason_code="not_connected"))
    def read_completed_analysis(self,scope):
        if not self.allowed:
            return EvidenceRead("group_a",scope,ReadState.MISSING,
                                reason_code="unavailable")
        return EvidenceRead("group_a",scope,ReadState.LOCATED,reference=EvidenceRef(
            scope=scope,port="group_a",record_key="ga:mock",
            producer_schema="group-a-to-b/v1",producer_version="a-v3",
            payload_sha256=A_RESULT,
        ))


class QDPStub(QDPQualifiedSetReader):
    def __init__(self,conn,pins):
        self._conn=conn
        self._pins=SimpleNamespace(scope=pins.scope,
            preparation_shas=("1"*64,pins.qdp_b_preparation_sha256,"3"*64))
        self.allowed=True
    def capability(self):
        return PortCapability("qdp",CapabilityState.DECLARED,
            PORT_SCHEMAS["qdp"],"qdp-handoff/v1") if self.allowed else (
            PortCapability("qdp",CapabilityState.UNAVAILABLE,
                           reason_code="not_connected"))
    def read_qualified_membership(self,scope):
        if not self.allowed:
            return EvidenceRead("qdp",scope,ReadState.MISSING,
                                reason_code="not_qualified")
        return EvidenceRead("qdp",scope,ReadState.LOCATED,reference=EvidenceRef(
            scope=scope,port="qdp",record_key="qdp:mock",
            producer_schema="qdp-handoff/v1",producer_version="qualified-v1",
            payload_sha256="b"*64,
        ))


class BReaderTests(unittest.TestCase):
    def make(self,kind="video",data=None):
        current=copy.deepcopy(data if data is not None else scenario(kind))
        db=DB(current)
        a=AStub(db,current["pins"])
        q=QDPStub(db,current["pins"])
        reader=GroupBExactReader(db,current["pins"],a,q,as_of=NOW)
        return reader,db,a,q,current

    def denied(self,data,reason):
        r,_,_,_,_=self.make(data=data)
        output=r.read_completed_analysis(data["pins"].scope)
        self.assertIs(output.state,ReadState.MISSING)
        self.assertEqual(output.reason_code,reason)
        self.assertIsNone(output.reference)

    def test_video_all_eight_blocks_completed_positive_is_mock_only(self):
        r,db,a,q,d=self.make()
        self.assertIs(r.capability().state,CapabilityState.DECLARED)
        out=r.read_completed_analysis(SCOPE)
        self.assertIs(out.state,ReadState.LOCATED)
        self.assertEqual(out.reference.producer_schema,B_READER_WIRE)
        self.assertEqual(out,r.read_completed_analysis(SCOPE))
        self.assertEqual(len(out.reference.lineage_sha256),64)
        self.assertEqual(
            sorted(sql for sql,p in db.calls if sql in COMPONENT_SQL.values()),
            sorted(COMPONENT_SQL.values())*2,
        )
        self.assertIs(UAEPorts(group_b=r).read("group_b",SCOPE).state,ReadState.LOCATED)
        self.assertFalse(UAEPorts(group_b=r).status("group_b").qualified)

    def test_carousel_applicable_six_blocks_and_never_invents_editing(self):
        for kind in ("photo_carousel","mixed_carousel"):
            r,db,_,_,d=self.make(kind)
            scope=d["pins"].scope
            got=r.read_completed_analysis(scope)
            self.assertIs(got.state,ReadState.LOCATED)
            queries=[sql for sql,p in db.calls]
            self.assertNotIn(COMPONENT_SQL["B5"],queries)
            self.assertNotIn(COMPONENT_SQL["B6"],queries)
            self.assertIn(CAROUSEL_NONAPPLICABLE_SQL,queries)

    def test_group_b_peer_schema_missing_must_fail_closed(self):
        r,db,_,_,d=self.make()
        db.schema=False
        self.assertIs(r.capability().state,CapabilityState.UNAVAILABLE)
        self.assertEqual(r.read_completed_analysis(SCOPE).reason_code,"gb_schema_missing")
        self.assertNotIn(RUN_SQL,[s for s,p in db.calls])
        db.schema=True
        db.fail=PROBE_SQL
        self.assertEqual(r.capability().reason_code,"gb_probe_failed")

    def test_current_group_a_or_qdp_revocation_denied(self):
        r,db,a,q,d=self.make()
        a.allowed=False
        self.assertEqual(r.read_completed_analysis(SCOPE).reason_code,
                         "gb_upstream_unavailable")
        a.allowed=True
        q.allowed=False
        self.assertEqual(r.read_completed_analysis(SCOPE).reason_code,
                         "gb_upstream_unavailable")

    def test_wrong_b_run_project_source_policy_kind_and_A_parent(self):
        for field,value in (
            ("project_id","f"*32),("video_id",99),
            ("source_id","other"),("source_policy_version_id",99),
            ("content_type","carousel"),("video_a_analysis_id",999),
            ("carousel_a_analysis_id",31),("analysis_video_a_id",777),
            ("a_snapshot_sha256","f"*64),
            ("media_manifest_sha256","0"*64),
            ("result_analysis_id",999),("analysis_policy_id",99),
            ("analysis_content_type","carousel"),
        ):
            with self.subTest(field=field):
                data=scenario()
                data["run"][field]=value
                self.denied(data,"gb_exact_run_analysis_or_receipt_mismatch")

    def test_ack_or_quoted_partial_review_runs_never_count_as_complete(self):
        for field,value in (
            ("run_state","partial"),("run_state","needs_review"),
            ("run_state","running"),("inbox_status","received"),
            ("analysis_state","partial"),("needs_review",True),
            ("receipt_event_id",None),("event_key",QDP),
            ("receipt_preparation_sha256","a"*64),
        ):
            with self.subTest(field=field):
                data=scenario()
                data["run"][field]=value
                self.denied(data,"gb_exact_run_analysis_or_receipt_mismatch")

    def test_method_taxonomy_model_prompt_and_config_are_pinned(self):
        for field,value in (
            ("analysis_version","old"),("taxonomy_version","old"),
            ("method_version","wrong"),("model_name","other"),
            ("model_version","old"),("prompt_version","old"),
            ("config_sha256","d"*64),("method_name",""),
        ):
            with self.subTest(field=field):
                data=scenario()
                data["run"][field]=value
                self.denied(data,"gb_exact_run_analysis_or_receipt_mismatch")

    def test_source_rights_and_future_date_denial(self):
        for field,value,code in (
            ("source_state","revoked","gb_current_source_rights_missing"),
            ("policy_active",False,"gb_current_source_rights_missing"),
            ("can_analyze_media",False,"gb_current_source_rights_missing"),
            ("policy_permissions",{},"gb_current_source_rights_missing"),
            ("analyzed_at",None,"gb_analysis_time_invalid"),
            ("analyzed_at",NOW+timedelta(minutes=1),"gb_analysis_time_invalid"),
            ("run_updated_at",NOW+timedelta(minutes=1),"gb_analysis_time_invalid"),
            ("unavailable_reasons",{"B3":"unavailable"},
             "gb_unavailable_measurements"),
        ):
            with self.subTest(field=field):
                data=scenario()
                data["run"][field]=value
                self.denied(data,code)

    def test_current_human_review_never_silently_approved(self):
        data=scenario()
        data["review"]={"revision":1,"decision_sha256":"a"*64,
                        "decision":{"status":"needs_review"}}
        self.denied(data,"gb_human_review_unresolved")
        data["review"]["decision"]={"status":"approved"}
        # Peer has no observed authenticated decision JSON release contract.
        self.denied(data,"gb_human_review_unresolved")

    def test_eight_block_matrix_mandatory_even_when_top_level_completed(self):
        data=scenario()
        data["blocks"].pop()
        self.denied(data,"gb_block_matrix_incomplete")
        data=scenario()
        data["blocks"][3]["block"]="B3"
        self.denied(data,"gb_block_matrix_incomplete")
        for field,value in (
            ("status","partial"),("status","unavailable"),
            ("evidence_sha256",None),("error_code","model_error"),
        ):
            data=scenario()
            data["blocks"][2][field]=value
            self.denied(data,"gb_applicable_block_incomplete")

    def test_carousel_B5_B6_nonapplicable_cannot_be_synthetic_success(self):
        for ix in (4,5):
            data=scenario("photo_carousel")
            data["blocks"][ix]["status"]="complete"
            data["blocks"][ix]["evidence_sha256"]="a"*64
            self.denied(data,"gb_nonapplicable_block_fabricated")
        data=scenario("photo_carousel")
        data["nonapplicable"]["editing_rows"]=1
        self.denied(data,"gb_carousel_video_only_rows_present")

    def test_b1_b2_b3_missing_or_unknown_are_not_valid_measurements(self):
        for block,field,value in (
            ("B1","classification_status","unknown"),
            ("B1","mechanisms",[]),
            ("B2","classification_status","needs_review"),
            ("B2","video_primary",None),
            ("B3","sampling_rows",0),
            ("B3","frames",0),
            ("B3","interval_reviews",1),
        ):
            data=scenario()
            data["facts"][block][field]=value
            with self.subTest(block=block,field=field):
                self.denied(data,"gb_"+block.lower()+"_fact_incomplete")
        data=scenario()
        data["facts"]["B3"]["intervals"]=0
        data["facts"]["B3"]["frames"]=0
        self.denied(data,"gb_b3_fact_incomplete")

    def test_b4_ai_origin_unknown_is_not_fabricated_false(self):
        r,_,_,_,d=self.make()
        self.assertIsNone(d["facts"]["B4"]["ai_generated"])
        self.assertIs(r.read_completed_analysis(SCOPE).state,ReadState.LOCATED)
        data=scenario()
        data["facts"]["B4"]["person_visible"]=None
        self.denied(data,"gb_b4_fact_incomplete")

    def test_b5_and_b6_video_only_exact_fields(self):
        for block,field,value in (
            ("B5","editing_type","unknown"),
            ("B5","avg_shot_duration",Decimal("0")),
            ("B5","cuts_first_5_sec",True),
            ("B6","subtitle_type","unknown"),
            ("B6","position","bottom"),
        ):
            data=scenario()
            data["facts"][block][field]=value
            with self.subTest(block=block,field=field):
                self.denied(data,"gb_"+block.lower()+"_fact_incomplete")

    def test_b7_zero_cta_only_after_completed_scan(self):
        r,_,_,_,_=self.make()
        self.assertIs(r.read_completed_analysis(SCOPE).state,ReadState.LOCATED)
        for field,value in (
            ("analysis_status","not_run"),("analysis_status","partial"),
            ("analyzed_at",None),("review_count",1),
            ("occurrence_count",1),("last_occurrence",1),
        ):
            data=scenario()
            data["facts"]["B7"][field]=value
            with self.subTest(field=field):
                self.denied(data,"gb_b7_fact_incomplete")
        data=scenario()
        data["facts"]["B7"].update(occurrence_count=2,distinct_count=2,
                                   last_occurrence=2)
        self.assertIs(self.make(data=data)[0].read_completed_analysis(SCOPE).state,
                      ReadState.LOCATED)

    def test_b8_taxonomy_and_human_review_must_be_exact(self):
        for field,value in (
            ("taxonomy_version","other"),("needs_review",True),
            ("category_id",0),("topic_id",0),("topic_category",24),
        ):
            data=scenario()
            data["facts"]["B8"][field]=value
            with self.subTest(field=field):
                self.denied(data,"gb_b8_fact_incomplete")

    def test_pins_invalid_ids_schema_and_dependency_identity(self):
        pins=scenario()["pins"]
        for kw in (
            {"b_run_id":"INVALID"},{"b_analysis_id":True},
            {"event_sha256":"A"*64},{"taxonomy_version":""},
            {"model_version":"bad whitespace"},
        ):
            with self.subTest(kw=kw),self.assertRaises(AnalyticsBoundaryError):
                replace(pins,**kw)
        r,db,a,q,d=self.make()
        with self.assertRaises(AnalyticsBoundaryError):
            r.read_completed_analysis(replace(SCOPE,project_id="f"*32))
        with self.assertRaises(AnalyticsBoundaryError):
            GroupBExactReader(db,replace(pins,group_a_analysis_id=99),a,q,
                              as_of=NOW)
        db.fail=RUN_SQL
        with self.assertRaises(AnalyticsBoundaryError) as e:
            r.read_completed_analysis(SCOPE)
        self.assertEqual(e.exception.code,"gb_database_unavailable")
        self.assertNotIn("password",str(e.exception))


@unittest.skipUnless(os.environ.get("CM_TEST_PG_DSN"),"Disposable PostgreSQL 17 only")
class GroupBStandalonePG17(unittest.TestCase):
    def test_unmerged_group_b_runtime_is_unavailable_not_fabricated(self):
        import psycopg
        from psycopg.rows import dict_row
        from intelligence import migrate
        if os.environ["CM_TEST_PG_DSN"]==os.environ.get("CM_DB_DSN"):
            self.fail("Do not run on production DB")
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as conn:
            version=int(conn.execute("SHOW server_version_num").fetchone()[0])
            self.assertTrue(170000<=version<180000)
            migrate.apply_migrations(conn)
            conn.row_factory=dict_row
            d=scenario()
            db=conn
            a=AStub(db,d["pins"])
            q=QDPStub(db,d["pins"])
            r=GroupBExactReader(db,d["pins"],a,q,as_of=NOW)
            self.assertIs(r.capability().state,CapabilityState.UNAVAILABLE)
            self.assertEqual(r.read_completed_analysis(d["pins"].scope).reason_code,
                             "gb_schema_missing")


if __name__=="__main__":
    unittest.main()
