"""UA-MOD-11 current source rights, TTL/revocation and guarded reads.

Positive fixtures are PostgreSQL dict-row *simulations* for source SQL.
Not proof of peer authority or a LIVE UAE publication.
"""
from __future__ import annotations
import copy
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace

from intelligence.unified_analytics import AnalyticsBoundaryError, InputScope
from intelligence.unified_analytics.dto import EvidenceRead, EvidenceRef, ReadState
from intelligence.unified_analytics.ports import UAEPorts, CapabilityState, PortCapability, PORT_SCHEMAS
from intelligence.unified_analytics.source_policy_adapter import (
    SourceRightsPins, SourcePolicyCurrentReader, GuardedUAEReads,
    PROBE_SQL, SOURCE_SQL, METADATA_SQL, METRICS_SQL, MEDIA_SQL,
    SNAPSHOT_IDENTITY_SQL, INVALIDATED_SQL, TABLES, RIGHTS_VERSION,
)

NOW=datetime(2026,10,27,8,tzinfo=timezone.utc)
PROJECT="a"*32
RUN="00112233-4455-4677-8899-aabbccddeeff"
SHA="b"*64
SOURCE="licensed_source"
SCOPE=InputScope(PROJECT,17,SOURCE,9,"video",RUN,SHA)

def example(*,kind="video"):
    scope=replace(SCOPE,content_kind=kind)
    mids=(101,) if kind in ("video","slideshow_video") else (101,102)
    typ=("video",) if len(mids)==1 else (
        ("image","image") if kind=="photo_carousel" else ("image","video")
    )
    pins=SourceRightsPins(scope,70,(201,202),mids)
    src=dict(source_id=SOURCE,source_status="enabled",
             source_updated_at=NOW-timedelta(days=1),
             can_store_metadata=True,can_analyze_media=True,
             can_store_metrics=True,can_derive_scores=True,
             retention_days=40,policy_id=9,policy_source_id=SOURCE,
             is_active=True,reviewed_at=NOW-timedelta(days=2),
             active_policy_count=1,project_link=True,
             permissions={k:True for k in (
                 "can_store_metadata","can_analyze_media",
                 "can_store_metrics","can_derive_scores"
             )},
             retention_policy={"derived_retention_days":10})
    meta=dict(id=70,video_id=17,source_id=SOURCE,
              source_policy_version_id=9,
              fetched_at=NOW-timedelta(days=4),
              published_at=NOW-timedelta(days=5),
              is_current=True,deleted_at=None,
              expires_at=NOW+timedelta(days=1))
    metrics=[
        dict(id=i,video_id=17,source_id=SOURCE,source_policy_version_id=9,
             source_metric_definition="platform_post_views",
             source_metric_version="1.0",data_quality_status="ok",
             precision="exact",observation_time_assumed=False,
             observed_at=NOW-timedelta(days=3)+timedelta(minutes=j),
             collected_at=NOW-timedelta(days=3)+timedelta(minutes=j+1),
             expires_at=NOW+timedelta(days=2))
        for j,i in enumerate((201,202))
    ]
    media=[
        dict(id=i,video_id=17,source_id=SOURCE,
             media_kind=typ[j],sha256=str(j+1)*64,
             permission="local_verified",
             created_at=NOW-timedelta(days=2),
             expires_at=NOW+timedelta(days=3))
        for j,i in enumerate(mids)
    ]
    snapshot=dict(id=11,project_id=PROJECT,video_id=17,source_id=SOURCE,
                  source_policy_version_id=9,qdp_run_id=RUN,
                  qdp_input_sha256=SHA,content_kind=kind,status="qualified")
    return dict(pins=pins,source=src,metadata=meta,metrics=metrics,
                media=media,snapshot=snapshot,invalidated=False)

class Cursor:
    def __init__(self,rows): self.rows=rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows

class FakeConn:
    def __init__(self,data):
        self.data=data
        self.schema=True
        self.fail=None
        self.calls=[]
        self.on_probe=None
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if self.fail==sql:
            raise RuntimeError("DB password=super-secret")
        if sql==PROBE_SQL:
            if self.on_probe:
                self.on_probe(self)
            return Cursor([{t:t if self.schema else None for t in TABLES}])
        if sql==SOURCE_SQL:
            return Cursor([self.data["source"]] if self.data["source"] else [])
        if sql==METADATA_SQL:
            return Cursor([self.data["metadata"]] if self.data["metadata"] else [])
        if sql==METRICS_SQL:
            return Cursor(self.data["metrics"])
        if sql==MEDIA_SQL:
            return Cursor(self.data["media"])
        if sql==SNAPSHOT_IDENTITY_SQL:
            return Cursor([self.data["snapshot"]] if self.data["snapshot"] else [])
        if sql==INVALIDATED_SQL:
            return Cursor([{"invalidated":self.data["invalidated"]}])
        raise AssertionError("Unexpected SQL")

class Peer:
    def __init__(self,conn):
        self.conn=conn
        self.calls=0
        self.during=None
    def capability(self):
        return PortCapability("qdp",CapabilityState.DECLARED,
                              PORT_SCHEMAS["qdp"],"qdp-handoff/v1")
    def read_qualified_membership(self,scope):
        self.calls+=1
        if self.during:
            self.during(self.conn)
        return EvidenceRead("qdp",scope,ReadState.LOCATED,reference=EvidenceRef(
            scope=scope,port="qdp",record_key="qdp:run:17",
            producer_schema="qdp-handoff/v1",producer_version="ready-v1",
            payload_sha256="1"*64))

class RightsTests(unittest.TestCase):
    def setup(self,data=None,*,kind="video"):
        rows=copy.deepcopy(example(kind=kind) if data is None else data)
        db=FakeConn(rows)
        rights=SourcePolicyCurrentReader(db,rows["pins"],as_of=NOW)
        peer=Peer(db)
        guarded=GuardedUAEReads(UAEPorts(source_policy=rights,qdp=peer),rights)
        return db,rights,peer,guarded,rows

    def denied(self,d,code):
        _,reader,_,_,_=self.setup(d)
        with self.assertRaises(AnalyticsBoundaryError) as err:
            reader.check_current_rights(d["pins"].scope)
        self.assertEqual(err.exception.code,code)
        outcome=reader.read_current_policy(d["pins"].scope)
        self.assertIs(outcome.state,ReadState.MISSING)
        self.assertEqual(outcome.reason_code,code)
        self.assertIsNone(outcome.reference)

    def test_exact_four_way_source_policy_intersection_and_ttl(self):
        db,reader,_,guard,d=self.setup()
        rights=reader.check_current_rights(SCOPE)
        self.assertEqual(rights.derived_retention_days,10)
        self.assertEqual(rights.expires_at,NOW+timedelta(days=1))
        self.assertEqual(len(rights.evidence_sha256),64)
        self.assertEqual(reader.check_current_rights(SCOPE),rights)
        ref=reader.read_current_policy(SCOPE)
        self.assertIs(ref.state,ReadState.LOCATED)
        self.assertEqual(ref.reference.producer_schema,RIGHTS_VERSION)
        self.assertIs(UAEPorts(source_policy=reader).read(
            "source_policy",SCOPE).state,ReadState.LOCATED)
        self.assertFalse(UAEPorts(source_policy=reader).status("source_policy").qualified)

    def test_one_denied_permission_blocks_entire_result(self):
        for right in (
            "can_store_metadata","can_analyze_media","can_store_metrics",
            "can_derive_scores"
        ):
            for side in ("source","policy"):
                with self.subTest(right=right,side=side):
                    d=example()
                    if side=="source":
                        d["source"][right]=False
                    else:
                        d["source"]["permissions"][right]=False
                    self.denied(d,"source_permission_intersection_denied")

    def test_policy_revoked_superseded_wrong_version_or_project_membership(self):
        for field,value,expected in (
            ("source_status","revoked","source_policy_revoked_or_superseded"),
            ("policy_id",99,"source_policy_revoked_or_superseded"),
            ("policy_source_id","other","source_policy_revoked_or_superseded"),
            ("is_active",False,"source_policy_revoked_or_superseded"),
            ("active_policy_count",2,"source_policy_revoked_or_superseded"),
            ("project_link",False,"source_project_link_revoked"),
        ):
            d=example()
            d["source"][field]=value
            with self.subTest(field=field):
                self.denied(d,expected)

    def test_unreviewed_policy_or_future_source_update(self):
        for field,value in (
            ("reviewed_at",None),("reviewed_at",NOW+timedelta(seconds=1)),
            ("source_updated_at",NOW+timedelta(seconds=1))
        ):
            d=example()
            d["source"][field]=value
            with self.subTest(field=field):
                self.denied(d,"source_policy_unreviewed")

    def test_retention_intersection_missing_policy_or_bad_ttl(self):
        for side,value in (
            ("source",None),("source",0),
            ("policy",None),("policy",0),("policy",366),
        ):
            d=example()
            if side=="source":
                d["source"]["retention_days"]=value
            else:
                d["source"]["retention_policy"]["derived_retention_days"]=value
            with self.subTest(side=side,value=value):
                self.denied(d,"source_derived_retention_unapproved")
        d=example()
        d["source"]["retention_days"]=2
        self.denied(d,"source_retention_expired")
        d=example()
        d["source"]["retention_days"]=3
        d["source"]["retention_policy"]["derived_retention_days"]=15
        self.denied(d,"source_retention_expired")

    def test_metadata_revocation_and_published_future(self):
        for field,value,code in (
            ("is_current",False,"source_metadata_revoked"),
            ("deleted_at",NOW,"source_metadata_revoked"),
            ("source_policy_version_id",17,"source_metadata_revoked"),
            ("source_id","other","source_metadata_revoked"),
            ("published_at",NOW+timedelta(days=1),"source_metadata_time_invalid"),
            ("expires_at",NOW,"source_retention_expired"),
        ):
            d=example()
            d["metadata"][field]=value
            with self.subTest(field=field):
                self.denied(d,code)

    def test_raw_metric_retracted_expired_or_wrong_unit(self):
        for field,value,code in (
            ("source_id","other","source_metric_invalid_or_revoked"),
            ("source_policy_version_id",88,"source_metric_invalid_or_revoked"),
            ("data_quality_status","retracted","source_metric_invalid_or_revoked"),
            ("data_quality_status","partial","source_metric_invalid_or_revoked"),
            ("source_metric_definition","impressions","source_metric_invalid_or_revoked"),
            ("observation_time_assumed",True,"source_metric_invalid_or_revoked"),
            ("precision","estimated","source_metric_invalid_or_revoked"),
            ("expires_at",NOW,"source_retention_expired"),
            ("collected_at",NOW+timedelta(hours=2),"source_metric_time_invalid"),
        ):
            d=example()
            d["metrics"][0][field]=value
            with self.subTest(field=field):
                self.denied(d,code)

    def test_missing_metric_and_media_identity_or_asset_revocation(self):
        d=example()
        d["metrics"].pop()
        self.denied(d,"source_metric_evidence_missing")
        d=example()
        d["media"].pop()
        self.denied(d,"source_media_evidence_missing")
        for field,value,code in (
            ("source_id","other","source_media_revoked"),
            ("permission","metadata_only","source_media_revoked"),
            ("sha256","F"*64,"source_media_revoked"),
            ("expires_at",NOW,"source_retention_expired"),
            ("created_at",NOW+timedelta(seconds=2),"source_media_time_invalid"),
            ("media_kind","unknown","source_media_kind_invalid"),
        ):
            d=example()
            d["media"][0][field]=value
            with self.subTest(field=field):
                self.denied(d,code)

    def test_video_photo_mixed_kind_and_order(self):
        for kind in ("photo_carousel","mixed_carousel","slideshow_video"):
            _,reader,_,_,d=self.setup(kind=kind)
            self.assertIsInstance(reader.check_current_rights(d["pins"].scope).evidence_sha256,str)
        d=example(kind="photo_carousel")
        d["media"][0]["media_kind"]="video"
        self.denied(d,"source_media_kind_mismatch")
        d=example(kind="mixed_carousel")
        d["media"][1]["media_kind"]="image"
        self.denied(d,"source_media_kind_mismatch")

    def test_unknown_mandatory_pins_never_inferred(self):
        for kw in (
            {"metadata_id":0},{"metric_snapshot_ids":()},
            {"metric_snapshot_ids":(201,201)},
            {"metric_snapshot_ids":(False,)},
            {"media_asset_ids":(101,102)},
        ):
            with self.subTest(kw=kw),self.assertRaises(AnalyticsBoundaryError):
                replace(example()["pins"],**kw)
        d=example()
        db,reader,_,_,_=self.setup(d)
        with self.assertRaises(AnalyticsBoundaryError):
            reader.check_current_rights(replace(SCOPE,project_id="c"*32))
        with self.assertRaises(AnalyticsBoundaryError):
            SourcePolicyCurrentReader(db,d["pins"],as_of=datetime(2026,10,27))

    def test_peer_read_only_happens_when_rights_valid(self):
        _,reader,peer,guard,_=self.setup()
        out=guard.read("qdp",SCOPE)
        self.assertIs(out.state,ReadState.LOCATED)
        self.assertEqual(peer.calls,1)
        self.assertIs(guard.read("source_policy",SCOPE).state,ReadState.LOCATED)

    def test_revoked_before_peer_read_blocks_all_partial_and_positive(self):
        db,reader,peer,guard,_=self.setup()
        db.data["source"]["permissions"]["can_store_metrics"]=False
        result=guard.read("qdp",SCOPE)
        self.assertIs(result.state,ReadState.MISSING)
        self.assertEqual(peer.calls,0)
        self.assertIsNone(result.reference)

    def test_revoked_during_peer_read_must_hide_success(self):
        db,reader,peer,guard,_=self.setup()
        peer.during=lambda con: con.data["metadata"].update(is_current=False)
        out=guard.read("qdp",SCOPE)
        self.assertIs(out.state,ReadState.MISSING)
        self.assertIsNone(out.reference)
        self.assertEqual(peer.calls,1)

    def test_mutated_policy_digest_during_read_must_hide_result(self):
        db,reader,peer,guard,_=self.setup()
        peer.during=lambda con: con.data["source"].update(
            source_updated_at=NOW-timedelta(minutes=2)
        )
        out=guard.read("qdp",SCOPE)
        self.assertIs(out.state,ReadState.MISSING)
        self.assertEqual(out.reason_code,"source_rights_changed_during_read")

    def test_metric_version_change_during_peer_read_does_not_reuse_old_grant(self):
        db,reader,peer,guard,_=self.setup()
        peer.during=lambda con: con.data["metrics"][1].update(
            source_metric_version="2.0"
        )
        got=guard.read("qdp",SCOPE)
        self.assertIs(got.state,ReadState.MISSING)
        self.assertEqual(got.reason_code,"source_rights_changed_during_read")

    def test_qualified_only_at_handoff_and_future_intake(self):
        db,reader,peer,guard,_=self.setup()
        self.assertEqual(guard.authorize_intake(SCOPE),reader.check_current_rights(SCOPE))
        self.assertEqual(guard.authorize_handoff(SCOPE,11),reader.check_current_rights(SCOPE))
        db.data["snapshot"]["status"]="partial"
        with self.assertRaises(AnalyticsBoundaryError) as e:
            guard.authorize_handoff(SCOPE,11)
        self.assertEqual(e.exception.code,"source_snapshot_scope_invalid")
        db.data["snapshot"]["status"]="qualified"
        db.data["source"]["permissions"]["can_derive_scores"]=False
        with self.assertRaises(AnalyticsBoundaryError):
            guard.authorize_intake(SCOPE)

    def test_snapshot_disappears_when_revoked_or_cross_project(self):
        db,reader,peer,guard,d=self.setup()
        self.assertEqual(guard.authorize_snapshot(SCOPE,11),
                         reader.check_current_rights(SCOPE))
        db.data["invalidated"]=True
        with self.assertRaises(AnalyticsBoundaryError) as e:
            guard.authorize_snapshot(SCOPE,11)
        self.assertEqual(e.exception.code,"source_snapshot_invalidated")
        db.data["invalidated"]=False
        db.data["snapshot"]["project_id"]="f"*32
        with self.assertRaises(AnalyticsBoundaryError) as e:
            guard.authorize_snapshot(SCOPE,11)
        self.assertEqual(e.exception.code,"source_snapshot_scope_invalid")

    def test_snapshot_identity_rechecked_even_for_old_qualified_row(self):
        db,reader,peer,guard,d=self.setup()
        db.data["snapshot"]["qdp_input_sha256"]="0"*64
        with self.assertRaises(AnalyticsBoundaryError):
            guard.authorize_snapshot(SCOPE,11)
        db.data["snapshot"]["qdp_input_sha256"]=SHA
        db.data["source"]["is_active"]=False
        with self.assertRaises(AnalyticsBoundaryError) as e:
            guard.authorize_snapshot(SCOPE,11)
        self.assertEqual(e.exception.code,"source_policy_revoked_or_superseded")

    def test_db_error_sanitized_and_missing_policy_no_data(self):
        db,reader,peer,guard,_=self.setup()
        db.data["source"]=None
        self.assertEqual(reader.read_current_policy(SCOPE).reason_code,
                         "source_policy_missing")
        db.data["source"]=example()["source"]
        db.fail=SOURCE_SQL
        with self.assertRaises(AnalyticsBoundaryError) as e:
            reader.check_current_rights(SCOPE)
        self.assertEqual(e.exception.code,"source_read_failed")
        self.assertNotIn("password",str(e.exception))
        self.assertIs(guard.read("qdp",SCOPE).state,ReadState.UNAVAILABLE)

    def test_missing_schema_denies_and_never_tries_peer(self):
        db,reader,peer,guard,d=self.setup()
        db.schema=False
        self.assertEqual(reader.capability().reason_code,"source_schema_missing")
        self.assertEqual(guard.read("qdp",SCOPE).reason_code,
                         "source_schema_unavailable")
        self.assertEqual(peer.calls,0)


@unittest.skipUnless(bool(__import__("os").environ.get("CM_TEST_PG_DSN")),
                     "Disposable PG17 only")
class SourcePolicyPG17(unittest.TestCase):
    def test_uae_main_schema_available_but_no_source_facts_fabricated(self):
        import os
        import psycopg
        from psycopg.rows import dict_row
        from intelligence import migrate
        if os.environ["CM_TEST_PG_DSN"]==os.environ.get("CM_DB_DSN"):
            self.fail("Refusing production DSN")
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as conn:
            self.assertTrue(170000<=int(conn.execute("SHOW server_version_num").fetchone()[0])<180000)
            migrate.apply_migrations(conn)
            conn.row_factory=dict_row
            pins=example()["pins"]
            reader=SourcePolicyCurrentReader(conn,pins,as_of=NOW)
            self.assertIs(reader.capability().state,CapabilityState.DECLARED)
            self.assertIs(reader.read_current_policy(SCOPE).state,ReadState.MISSING)
            self.assertEqual(reader.read_current_policy(SCOPE).reason_code,
                             "source_policy_missing")
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM uae_snapshot_current_rights_candidates"
                ).fetchone()[0],0
            )


if __name__=="__main__":
    unittest.main()
