"""GA-MOD-04: QDP handoff contract, durable ACK and adversarial replay tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
import unittest
from uuid import uuid4

from intelligence.group_a.adapters.qdp import CallbackQDPReader, PostgresQDPReader
from intelligence.group_a.config import GASettings
from intelligence.group_a.contracts_v1 import ContractViolation, canonical_sha256
from intelligence.group_a.handoff import (
    EVENT_SCHEMA, RECEIPT_SCHEMA, GroupAInboxReceiver,
    HandoffRejected, validate_event,
)
from intelligence.group_a.ports import negotiate
from intelligence import migrate

DSN=os.getenv("CM_TEST_PG_DSN")


def pointer():
    return dict(schema_version="qdp-handoff/v1",event_id=str(uuid4()),
                project_id="a"*32,qdp_run_id=str(uuid4()),video_id=123,
                analysis_group="A",input_sha256="b"*64)


def envelope(p):
    return {"schema_version":EVENT_SCHEMA,"pointer":deepcopy(p)}


def signed(e):
    return canonical_sha256(e)


def manifest(p, *, src="fixture:media",policy=1,asset=10):
    data=dict(schema_version="ga-media-manifest/v1",project_id=p["project_id"],
              qdp_run_id=p["qdp_run_id"],video_id=p["video_id"],
              qdp_input_sha256=p["input_sha256"],content_kind="video",
              source_id=src,source_policy_version_id=policy,
              verified_at="2026-10-26T09:00:00Z",
              items=[{"index":1,"kind":"video","media_asset_id":asset,
                      "sha256":"c"*64,"byte_size":100,
                      "verified_local_path_ref":"ga-local:verified",
                      "duration_sec":"4.000"}])
    return dict(data,manifest_sha256=canonical_sha256(data))


class FixedQDP:
    def __init__(self, *,state="ready",version="qdp-handoff/v1",
                 source=None,policy=None,asset=None):
        self.state=state;self.version=version
        self.source=source;self.policy=policy;self.asset=asset
        self.calls=0

    def capability(self):
        return negotiate("qdp",self.version)

    def read_preparation(self,p):
        self.calls+=1
        if self.state=="missing":return None
        return {"pointer":p,"state":self.state,
                "source_id":self.source,"source_policy_version_id":self.policy,
                "media_asset_id":self.asset}


class FixedMedia:
    def __init__(self,data,*,version="ga-media-manifest/v1"):
        self.data=data;self.version=version
    def capability(self):
        return negotiate("media",self.version)
    def read_verified_manifest(self,p):
        return self.data


class NoDB:
    def __call__(self):
        raise AssertionError("Disabled/invalid event must never open database")


class GA04Unit(unittest.TestCase):
    def test_invalid_event_and_digest_are_rejected(self):
        p=pointer()
        event=envelope(p)
        self.assertEqual(validate_event(event,signed(event))[0],p)
        for bad in (
            {"schema_version":"qdp.preparation_ready/v2","pointer":p},
            {"schema_version":EVENT_SCHEMA,"pointer":dict(p,analysis_group="B")},
            {"schema_version":EVENT_SCHEMA,"pointer":dict(p,project_id="A"*32)},
            {"schema_version":EVENT_SCHEMA,"pointer":dict(p,video_id=True)},
            dict(event,unexpected=1),
        ):
            with self.subTest(bad=bad),self.assertRaises(ContractViolation):
                validate_event(bad,signed(bad))
        with self.assertRaises(HandoffRejected) as ctx:
            validate_event(event,"a"*64)
        self.assertEqual(ctx.exception.code,"payload_sha256_mismatch")

    def test_explicit_injection_and_feature_flags(self):
        event=envelope(pointer())
        with self.assertRaises(HandoffRejected) as ctx:
            GroupAInboxReceiver(NoDB(),lambda _:None,lambda _:None).receive(event,signed(event))
        self.assertEqual(ctx.exception.code,"handoff_disabled")
        with self.assertRaises(TypeError):
            GroupAInboxReceiver(None,lambda _:None,lambda _:None,
                                settings=GASettings(enabled=True))
        reader=CallbackQDPReader(lambda p:{"pointer":p,"state":"ready"})
        self.assertEqual(reader.capability().state,"unavailable")
        reader=CallbackQDPReader(lambda p:{"pointer":p,"state":"ready"},
                                 observed_version="qdp-handoff/v2")
        self.assertEqual(reader.capability().state,"incompatible")
        reader=CallbackQDPReader(lambda p:{"pointer":p,"state":"ready"},
                                 observed_version="qdp-handoff/v1")
        self.assertEqual(reader.read_preparation(pointer())["state"],"ready")

    def test_qdp_public_db_contract_not_assumed_installed(self):
        class EmptyDB:
            def execute(self,sql,args):
                self.args=args
                return self
            def fetchone(self):
                return (None,)
        q=PostgresQDPReader(EmptyDB(),observed_version="qdp-handoff/v1")
        self.assertEqual(q.capability().state,"unavailable")
        self.assertEqual(PostgresQDPReader(EmptyDB()).capability().state,"unavailable")
        self.assertEqual(PostgresQDPReader(EmptyDB(),observed_version="qdp-handoff/v2").capability().state,
                         "incompatible")


@unittest.skipUnless(DSN, "Disposable PostgreSQL17 CM_TEST_PG_DSN required")
class GA04Postgres(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.name="ga04_"+uuid4().hex[:16]
        with psycopg.connect(DSN,autocommit=True) as con:
            con.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.name)))
        with self.connect() as con:
            migrate.apply_migrations(con)
            self.pid=uuid4().hex
            self.src="ga04-"+uuid4().hex
            con.execute("""INSERT INTO project_registry
                (id,name,profile_revision,profile_hash,created_at,updated_at)
                VALUES(%s,'GA intake',1,%s,now(),now())""",(self.pid,"d"*64))
            con.execute("INSERT INTO sre_workspaces(project_id) VALUES(%s)",(self.pid,))
            con.execute("""INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                VALUES(%s,'fixture','owned','enabled',true)""",(self.src,))
            from psycopg.types.json import Jsonb
            self.policy=con.execute("""INSERT INTO source_policy_versions
                (source_id,version,is_active,permissions)
                VALUES(%s,1,true,%s) RETURNING id""",
                (self.src,Jsonb({"can_analyze_media":True}))).fetchone()[0]
            self.vid=con.execute("""INSERT INTO videos
                (platform,platform_video_id,canonical_url,content_kind)
                VALUES('youtube',%s,%s,'video') RETURNING id""",
                (uuid4().hex,"https://youtube.test/fixture/"+uuid4().hex)).fetchone()[0]
            con.execute("INSERT INTO sre_project_videos(project_id,video_id) VALUES(%s,%s)",
                        (self.pid,self.vid))
            self.asset=con.execute("""INSERT INTO media_assets
                (video_id,source_id,media_kind,local_path,sha256,permission)
                VALUES(%s,%s,'video','/fixture/media.mp4',%s,'analysis_allowed')
                RETURNING id""",(self.vid,self.src,"c"*64)).fetchone()[0]
        self.p=dict(pointer(),project_id=self.pid,video_id=self.vid)
        self.m=manifest(self.p,src=self.src,policy=self.policy,asset=self.asset)
        self.qdp_state="ready"

    def connect(self):
        import psycopg
        from psycopg import sql
        conn=psycopg.connect(DSN,autocommit=True)
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.name)))
        return conn

    def tearDown(self):
        import psycopg
        from psycopg import sql
        with psycopg.connect(DSN,autocommit=True) as con:
            con.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(self.name)))

    def receiver(self,*,read=None,media=None):
        state=self.qdp_state
        return GroupAInboxReceiver(self.connect,
            qdp_factory=lambda conn:FixedQDP(state=state,source=self.src,
                       policy=self.policy,asset=self.asset) if read is None else read(conn),
            media_factory=lambda conn:FixedMedia(self.m) if media is None else media(conn),
            settings=GASettings(enabled=True,qdp_handoff_enabled=True))

    def counts(self):
        with self.connect() as conn:
            return tuple(conn.execute("SELECT count(*) FROM "+table).fetchone()[0]
                         for table in ("ga_runs","ga_steps","ga_inbox","ga_inbox_conflicts"))

    def test_commit_ack_replay_and_conflicting_payload_quarantine(self):
        evt=envelope(self.p);sig=signed(evt)
        receiver=self.receiver()
        first=receiver.receive(evt,sig)
        self.assertEqual(first["schema_version"],RECEIPT_SCHEMA)
        self.assertEqual(first["state"],"recorded")
        self.assertEqual(first["analysis_state"],"not_started")
        self.assertFalse(first["replayed"])
        self.assertEqual(self.counts(),(1,1,1,0))
        duplicate=receiver.receive(evt,sig)
        self.assertTrue(duplicate["replayed"])
        self.assertEqual(first["run_id"],duplicate["run_id"])
        self.assertEqual(first["job_id"],duplicate["job_id"])
        self.assertEqual(self.counts(),(1,1,1,0))
        self.assertEqual(receiver.read_receipt(self.pid,self.p["event_id"])["run_id"],
                         first["run_id"])
        self.assertIsNone(receiver.read_receipt("f"*32,self.p["event_id"]))
        altered=envelope(dict(self.p,preferred_media_asset_id=self.asset))
        with self.assertRaises(HandoffRejected) as ctx:
            receiver.receive(altered,signed(altered))
        self.assertEqual(ctx.exception.code,"event_id_conflict")
        self.assertEqual(self.counts(),(1,1,1,1))
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT receipt_state FROM ga_inbox").fetchone()[0],
                             "recorded")
        with self.assertRaises(HandoffRejected):
            receiver.receive(altered,signed(altered))
        self.assertEqual(self.counts(),(1,1,1,1))

    def test_nonready_missing_invalid_media_and_tamper_never_acked(self):
        event=envelope(self.p)
        with self.assertRaises(HandoffRejected):
            self.receiver(read=lambda _:FixedQDP(state="blocked",source=self.src,
                            policy=self.policy,asset=self.asset)).receive(event,signed(event))
        with self.assertRaises(HandoffRejected):
            self.receiver(read=lambda _:FixedQDP(state="missing",source=self.src,
                            policy=self.policy,asset=self.asset)).receive(event,signed(event))
        with self.assertRaises(HandoffRejected):
            self.receiver(media=lambda _:FixedMedia(None)).receive(event,signed(event))
        with self.assertRaises(HandoffRejected):
            self.receiver().receive(event,"a"*64)
        tampered=manifest(self.p,src=self.src,policy=self.policy,asset=self.asset)
        tampered["items"][0]["sha256"]="d"*64
        tampered["manifest_sha256"]=canonical_sha256({
            k:v for k,v in tampered.items() if k!="manifest_sha256"})
        with self.assertRaises(Exception):
            self.receiver(media=lambda _:FixedMedia(tampered)).receive(event,signed(event))
        foreign=dict(self.p,project_id="e"*32)
        with self.assertRaises(Exception):
            self.receiver().receive(envelope(foreign),signed(envelope(foreign)))
        self.assertEqual(self.counts(),(0,0,0,0))

    def test_atomic_rollback_when_queue_step_fails(self):
        from psycopg import sql
        with self.connect() as conn:
            conn.execute("""CREATE FUNCTION ga_fixture_fail_step() RETURNS trigger
                         LANGUAGE plpgsql AS $fixture$
                         BEGIN RAISE EXCEPTION 'fixture simulated queue failure'; END;
                         $fixture$""")
            conn.execute("""CREATE TRIGGER ga_fixture_fail_step
                         BEFORE INSERT ON ga_steps FOR EACH ROW
                         EXECUTE FUNCTION ga_fixture_fail_step()""")
        event=envelope(self.p)
        with self.assertRaises(Exception):
            self.receiver().receive(event,signed(event))
        self.assertEqual(self.counts(),(0,0,0,0))
        with self.connect() as conn:
            conn.execute("DROP TRIGGER ga_fixture_fail_step ON ga_steps")
            conn.execute("DROP FUNCTION ga_fixture_fail_step()")
        ack=self.receiver().receive(event,signed(event))
        self.assertEqual(ack["state"],"recorded")
        self.assertEqual(self.counts(),(1,1,1,0))

    def test_concurrent_duplicate_same_job_and_different_event_same_run(self):
        evt=envelope(self.p);sig=signed(evt)
        def call(_):
            return self.receiver().receive(evt,sig)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=list(pool.map(call,(1,2)))
        self.assertEqual(a["run_id"],b["run_id"])
        self.assertEqual(a["job_id"],b["job_id"])
        self.assertEqual({a["replayed"],b["replayed"]},{False,True})
        self.assertEqual(self.counts(),(1,1,1,0))
        newer=envelope(dict(self.p,event_id=str(uuid4())))
        second=self.receiver().receive(newer,signed(newer))
        self.assertEqual(second["run_id"],a["run_id"])
        self.assertEqual(self.counts(),(1,1,2,0))

    def test_opt_in_pg_qdp_reader_rechecks_policy_and_sha(self):
        from psycopg.types.json import Jsonb
        with self.connect() as conn:
            conn.execute("""CREATE TABLE qdp_runs(id UUID PRIMARY KEY,project_id TEXT,
                status TEXT)""")
            conn.execute("""CREATE TABLE qdp_items(run_id UUID,project_id TEXT,
                video_id BIGINT,status TEXT)""")
            conn.execute("""CREATE TABLE qdp_enrichments(run_id UUID,project_id TEXT,
                video_id BIGINT,source_id TEXT,source_policy_version_id BIGINT,
                media_asset_id BIGINT,expires_at TIMESTAMPTZ)""")
            conn.execute("""CREATE TABLE qdp_preparations(run_id UUID,project_id TEXT,
                video_id BIGINT,analysis_group TEXT,state TEXT,
                input_sha256 TEXT,inputs JSONB)""")
            conn.execute("INSERT INTO qdp_runs VALUES(%s,%s,'partial')",
                         (self.p["qdp_run_id"],self.pid))
            conn.execute("INSERT INTO qdp_items VALUES(%s,%s,%s,'partial')",
                         (self.p["qdp_run_id"],self.pid,self.vid))
            conn.execute("""INSERT INTO qdp_enrichments
                (run_id,project_id,video_id,source_id,source_policy_version_id,
                 media_asset_id) VALUES(%s,%s,%s,%s,%s,%s)""",
                 (self.p["qdp_run_id"],self.pid,self.vid,self.src,self.policy,self.asset))
            conn.execute("""INSERT INTO qdp_preparations VALUES
                (%s,%s,%s,'A','ready',%s,%s)""",
                (self.p["qdp_run_id"],self.pid,self.vid,
                 self.p["input_sha256"],Jsonb({"media_asset_id":self.asset})))
            reader=PostgresQDPReader(conn,observed_version="qdp-handoff/v1")
            self.assertEqual(reader.capability().state,"available")
            self.assertEqual(reader.read_preparation(self.p)["state"],"ready")
            conn.execute("""UPDATE qdp_preparations SET input_sha256=%s""",("f"*64,))
            self.assertEqual(reader.read_preparation(self.p)["state"],"blocked")
            conn.execute("""UPDATE qdp_preparations SET input_sha256=%s""",(self.p["input_sha256"],))
            conn.execute("UPDATE sources SET status='disabled' WHERE id=%s",(self.src,))
            self.assertEqual(reader.read_preparation(self.p)["state"],"blocked")


if __name__=="__main__":
    unittest.main()
