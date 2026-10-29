"""GB-MOD-05 pure Group A exact-version, stream and lineage regression tests."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4
from unittest.mock import patch
import unittest

from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.adapters.group_a import (
    GroupAExactPin, read_exact_a, require_a_schema, validate_a_snapshot,
)
from intelligence.group_b.errors import DependencyUnavailable, DependencyContractMismatch
from intelligence.group_b.lineage import bind_exact_group_a

P = "a"*32
Q = str(uuid4())
B = str(uuid4())
GA = str(uuid4())
VID = 41
AID = 73
RID = 19
IN = "b"*64
MAN = "c"*64
MEDIA = "d"*64

def completed_stream():
    return {"status":"complete","evidence_count":1,"verified_absence":False,"reason_code":None}

def complete_snapshot(kind="video",streams=None):
    keys = {"video":("technical","speech","screen_text","audio","subtitles"),
            "photo_carousel":("slides","screen_text")}
    names = keys[kind]
    proof = {"technical":"scene","speech":"transcript","screen_text":"screen_text",
             "audio":"audio","subtitles":"screen_text","slides":"slide"}
    return {
      "schema_version":"group-a-to-b/v1","project_id":P,"qdp_run_id":Q,
      "video_id":VID,"qdp_input_sha256":IN,"manifest_sha256":MAN,
      "content_kind":kind,"analysis_state":"completed",
      "video_a_analysis_id":AID if kind=="video" else None,
      "carousel_a_analysis_id":AID if kind!="video" else None,
      "method_version":"ga-test/1","streams":streams or {k:completed_stream() for k in names},
      "evidence":[{"kind":v,"ref_sha256":"1"*64,"asset_sha256":MEDIA}
                  for v in dict.fromkeys(proof[k] for k in names)],
    }

def kwargs():
    return {"project_id":P,"qdp_run_id":Q,"video_id":VID,"content_kind":"video",
            "input_sha256":IN,"manifest_sha256":MAN,"analysis_id":AID}

def fake_row(snapshot=None,kind="video"):
    snapshot = snapshot or complete_snapshot(kind)
    return {
      "b_run_id":B,"project_id":P,"video_id":VID,"source_id":"source-A",
      "content_type":"video" if kind=="video" else "carousel","b_state":"draft",
      "inbox_id":uuid4(),"b_video_a":None,"b_carousel_a":None,
      "b_sha":None,"b_manifest":None,"b_policy":None,
      "qdp_run_id":Q,"qdp_sha":IN,"ga_run_id":GA,"ga_state":"completed",
      "content_kind":kind,"stage_version":"group-a-assembly/v1",
      "qdp_input_sha256":IN,"manifest_sha256":MAN,"ga_policy":11,
      "expected_media_items":1 if kind=="video" else 2,
      "ga_result_id":RID,"ga_result_state":"completed",
      "result_schema":"group-a-to-b/v1","result_sha256":canonical_hash(snapshot),
      "snapshot":snapshot,"evidence":snapshot["evidence"],"method_version":"ga-test/1",
      "video_a_analysis_id":AID if kind=="video" else None,
      "carousel_a_analysis_id":AID if kind!="video" else None,
      "video_a_state":"completed" if kind=="video" else None,
      "carousel_a_state":"completed" if kind!="video" else None,
      "video_a_source":"source-A" if kind=="video" else None,
      "carousel_a_source":"source-A" if kind!="video" else None,
      "video_a_video":VID if kind=="video" else None,
      "carousel_a_video":VID if kind!="video" else None,
      "video_a_media_asset_id":1 if kind=="video" else None,
      "video_a_policy":11 if kind=="video" else None,
      "carousel_a_policy":11 if kind!="video" else None,
      "video_a_scene_status":"complete" if kind=="video" else None,
      "video_a_speech_status":"complete" if kind=="video" else None,
      "video_a_screen_text_status":"complete" if kind=="video" else None,
      "video_a_audio_status":"complete" if kind=="video" else None,
      "video_a_has_subtitles":"true" if kind=="video" else None,
      "carousel_a_kind":kind if kind!="video" else None,
      "carousel_a_slide_status":"complete" if kind!="video" else None,
      "carousel_a_text_status":"complete" if kind!="video" else None,
    }

def media_row(slide=1,media_kind="video"):
    return (slide,slide,media_kind,MEDIA,VID,"source-A",MEDIA,
            "analysis",None,media_kind,VID,"source-A")

class Result:
    def __init__(self,row=None,rows=None):
        self.value=row
        self.rows=rows
    def fetchone(self):
        return self.value
    def fetchall(self):
        return self.rows if self.rows is not None else []

class FakeReader:
    def __init__(self,row=None,media=None,rights=True,ga_missing=False):
        self.row=row
        self.media=media if media is not None else [media_row()]
        self.rights=rights
        self.ga_missing=ga_missing
    def execute(self,sql,args=()):
        if "to_regclass" in sql:
            return Result(rows=[("ga_results",)] if self.ga_missing else [])
        if "FROM group_b_runs b" in sql:
            return Result(row=self.row)
        if "FROM ga_media_refs mr" in sql:
            return Result(rows=self.media)
        if "FROM carousel_feature_a_items" in sql:
            return Result(rows=[(m[0],m[2],m[1]) for m in self.media])
        if "FROM sources s" in sql:
            return Result(row=(1,) if self.rights else None)
        if "SELECT %s > now()" in sql:
            return Result(row=(True,))
        raise AssertionError("Unexpected SQL read")


class TestExactGroupA(unittest.TestCase):
    def test_completed_stream_matrix_with_evidence(self):
        self.assertEqual(validate_a_snapshot(complete_snapshot(),**kwargs())["analysis_state"],
                         "completed")

    def test_missing_partial_notrun_and_unavailable_are_not_complete(self):
        for state in ("partial","unavailable","not_run"):
            s=complete_snapshot()
            s["streams"]["speech"]={
                "status":state,"evidence_count":None,"verified_absence":False,
                "reason_code":"not_measured"}
            s["analysis_state"]="partial"
            with self.subTest(status=state), self.assertRaises(DependencyUnavailable):
                validate_a_snapshot(s,**kwargs())

    def test_false_zero_or_missing_evidence_is_blocked(self):
        s=complete_snapshot()
        s["streams"]["speech"]={
            "status":"complete","evidence_count":0,"verified_absence":False,"reason_code":None}
        with self.assertRaises(DependencyContractMismatch):
            validate_a_snapshot(s,**kwargs())
        s=complete_snapshot()
        s["evidence"]=[x for x in s["evidence"] if x["kind"]!="transcript"]
        with self.assertRaises(DependencyUnavailable):
            validate_a_snapshot(s,**kwargs())
        s=complete_snapshot()
        s["streams"]["audio"]={"status":"complete","evidence_count":0,
                               "verified_absence":True,"reason_code":None}
        self.assertEqual(validate_a_snapshot(s,**kwargs())["analysis_state"],"completed")

    def test_wrong_group_video_project_id_hash_kind_or_version(self):
        for key,value in (
            ("schema_version","group-a-to-b/v2"),("project_id","f"*32),
            ("qdp_run_id",str(uuid4())),("video_id",444),("qdp_input_sha256","e"*64),
            ("manifest_sha256","e"*64),("content_kind","image_post"),
            ("video_a_analysis_id",999),
            ("carousel_a_analysis_id",88),
        ):
            s=complete_snapshot()
            s[key]=value
            with self.subTest(key=key), self.assertRaises(
                (DependencyUnavailable,DependencyContractMismatch)
            ):
                validate_a_snapshot(s,**kwargs())
        s=complete_snapshot()
        del s["streams"]["technical"]
        with self.assertRaises(DependencyContractMismatch):
            validate_a_snapshot(s,**kwargs())

    def test_missing_ga_schema_returns_typed_blocker(self):
        with self.assertRaises(DependencyUnavailable):
            require_a_schema(FakeReader(ga_missing=True))
        require_a_schema(FakeReader())

    def test_exact_result_pin_and_real_a_status(self):
        row=fake_row()
        got=read_exact_a(FakeReader(row),b_run_id=B,ga_result_id=RID,
                         expected_sha256=row["result_sha256"])
        self.assertEqual(got.a_analysis_id,AID)
        self.assertEqual(got.ga_run_id,GA)
        self.assertEqual(got.media_asset_ids,(1,))
        self.assertEqual(got.source_policy_version_id,11)

    def test_independent_qdp_group_digests_and_existing_complete_replay(self):
        row=fake_row()
        row["qdp_sha"]="e"*64  # B-specific QDP v2 preparation
        class SpyReader(FakeReader):
            sql_text=""
            def execute(self,sql,args=()):
                if "FROM group_b_runs b" in sql:
                    self.sql_text=sql
                return super().execute(sql,args)
        reader=SpyReader(row)
        result=read_exact_a(reader,b_run_id=B,ga_result_id=RID,
                            expected_sha256=row["result_sha256"])
        self.assertEqual(result.ga_result_id,RID)
        self.assertIn("qa.input_sha256=r.qdp_input_sha256",reader.sql_text)
        self.assertIn("qb.input_sha256=i.preparation_sha256",reader.sql_text)
        self.assertNotIn("r.qdp_input_sha256=i.preparation_sha256",reader.sql_text)
        row["b_state"]="completed"
        row["b_video_a"]=AID
        row["b_sha"]=row["result_sha256"]
        row["b_manifest"]=MAN
        row["b_policy"]=11
        self.assertEqual(read_exact_a(FakeReader(row),b_run_id=B,
                         ga_result_id=RID,expected_sha256=row["result_sha256"]).ga_result_id,RID)
        row["b_sha"]="0"*64
        with self.assertRaises(DependencyUnavailable):
            read_exact_a(FakeReader(row),b_run_id=B,
                         ga_result_id=RID,expected_sha256=row["result_sha256"])

    def test_no_latest_result_fallback(self):
        with self.assertRaises(DependencyUnavailable):
            read_exact_a(FakeReader(None),b_run_id=B,ga_result_id=RID,
                         expected_sha256="e"*64)
        with self.assertRaises(DependencyContractMismatch):
            read_exact_a(FakeReader(fake_row()),b_run_id=B,ga_result_id=True,
                         expected_sha256="e"*64)

    def test_incomplete_invalidated_and_swapped_analysis(self):
        cases = [
            ("ga_state","partial"),("ga_state","invalidated"),
            ("video_a_scene_status","not_run"),
            ("video_a_speech_status","partial"),
            ("video_a_media_asset_id",999),
            ("video_a_policy",999),
            ("video_a_has_subtitles","unknown"),
            ("ga_result_state","partial"),("video_a_state","partial"),
            ("video_a_source","foreign"),("video_a_video",VID+1),
            ("qdp_input_sha256","e"*64),("project_id","f"*32),
            ("result_schema","group-a-to-b/v2"),("b_sha","e"*64),
        ]
        for key,val in cases:
            row=fake_row()
            row[key]=val
            with self.subTest(key=key),self.assertRaises(
                (DependencyUnavailable,DependencyContractMismatch)
            ):
                read_exact_a(FakeReader(row),b_run_id=B,ga_result_id=RID,
                             expected_sha256=fake_row()["result_sha256"])

    def test_evidence_must_match_ga_snapshot_and_pinned_asset(self):
        row=fake_row()
        row["evidence"]=[]
        with self.assertRaises(DependencyUnavailable):
            read_exact_a(FakeReader(row),b_run_id=B,ga_result_id=RID,
                         expected_sha256=row["result_sha256"])
        row=fake_row()
        row["method_version"]="a-swapped-method"
        with self.assertRaises(DependencyUnavailable):
            read_exact_a(FakeReader(row),b_run_id=B,ga_result_id=RID,
                         expected_sha256=row["result_sha256"])
        snap=complete_snapshot()
        snap["evidence"][0]["asset_sha256"]="e"*64
        row=fake_row(snap)
        with self.assertRaises(DependencyUnavailable):
            read_exact_a(FakeReader(row),b_run_id=B,ga_result_id=RID,
                         expected_sha256=row["result_sha256"])

    def test_cannot_use_wrong_snapshot_digest(self):
        row=fake_row()
        with self.assertRaises(DependencyUnavailable):
            read_exact_a(FakeReader(row),b_run_id=B,ga_result_id=RID,
                         expected_sha256="f"*64)

    def test_revoked_expired_cross_source_or_incomplete_media(self):
        row=fake_row()
        for media,rights in (
            ([],True),([media_row(2)],True),
            ([tuple("foreign" if j==11 else v for j,v in enumerate(media_row()))],True),
            ([tuple("e"*64 if j==6 else v for j,v in enumerate(media_row()))],True),
            ([media_row()],False),
        ):
            with self.subTest(media=media,rights=rights), self.assertRaises(DependencyUnavailable):
                read_exact_a(FakeReader(row,media,rights),b_run_id=B,
                             ga_result_id=RID,expected_sha256=row["result_sha256"])

    def test_carousel_slide_order_and_media_mix(self):
        row=fake_row(complete_snapshot("photo_carousel"),"photo_carousel")
        expected=row["result_sha256"]
        got=read_exact_a(
            FakeReader(row,[media_row(1,"image"),media_row(2,"image")]),
            b_run_id=B,ga_result_id=RID,expected_sha256=expected)
        self.assertEqual(got.content_type,"carousel")
        self.assertEqual(got.media_asset_ids,(1,2))
        with self.assertRaises(DependencyUnavailable):
            read_exact_a(
                FakeReader(row,[media_row(1,"video"),media_row(2,"image")]),
                b_run_id=B,ga_result_id=RID,expected_sha256=expected)


class FakeBindingConn:
    def __init__(self,existing):
        self.existing=existing
        self.events=[]
    def __enter__(self):
        self.events.append("open")
        return self
    def __exit__(self,*ex):
        self.events.append("commit" if ex[0] is None else "rollback")
        return False
    @contextmanager
    def transaction(self):
        self.events.append("transaction")
        yield
        self.events.append("transaction_done")
    def execute(self,query,args):
        if "FROM group_b_runs WHERE id" in query:
            return Result(row=self.existing)
        if "UPDATE group_b_runs" in query:
            self.events.append("UPDATE")
            return Result(row=(B,))
        raise AssertionError("Unexpected bind SQL")


class TestBindGroupA(unittest.TestCase):
    def pin(self):
        return GroupAExactPin(P,B,GA,RID,AID,"video",11,"e"*64,MAN,5,(1,),(MEDIA,))

    def test_atomic_binding_and_replay(self):
        initial=(P,"draft",None,None,None,None,None)
        conn=FakeBindingConn(initial)
        with patch("intelligence.group_b.lineage.require_runtime_schema"),patch(
            "intelligence.group_b.lineage.read_exact_a",return_value=self.pin()
        ):
            got=bind_exact_group_a(lambda:conn,b_run_id=B,
                                   ga_result_id=RID,expected_sha256="e"*64)
        self.assertEqual(got.a_analysis_id,AID)
        self.assertEqual(conn.events,["open","transaction","UPDATE",
                                      "transaction_done","commit"])
        pinned=(P,"draft",AID,None,"e"*64,MAN,11)
        conn2=FakeBindingConn(pinned)
        with patch("intelligence.group_b.lineage.require_runtime_schema"),patch(
            "intelligence.group_b.lineage.read_exact_a",return_value=self.pin()
        ):
            bind_exact_group_a(lambda:conn2,b_run_id=B,
                               ga_result_id=RID,expected_sha256="e"*64)
        self.assertNotIn("UPDATE",conn2.events)

    def test_cannot_change_existing_pin_or_non_draft_run(self):
        for initial in (
            (P,"draft",42,None,"e"*64,MAN,11),
            (P,"running",None,None,None,None,None),
        ):
            conn=FakeBindingConn(initial)
            with patch("intelligence.group_b.lineage.require_runtime_schema"),patch(
                "intelligence.group_b.lineage.read_exact_a",return_value=self.pin()
            ):
                with self.assertRaises(DependencyUnavailable):
                    bind_exact_group_a(lambda:conn,b_run_id=B,
                                       ga_result_id=RID,expected_sha256="e"*64)
            self.assertNotIn("UPDATE",conn.events)


if __name__=="__main__":
    unittest.main()
