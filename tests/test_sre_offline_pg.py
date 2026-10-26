"""S4 real PG17 E2E: two-review RTB -> D1 -> queued offline fake -> results.

No network or paid SRE calls. Reuses deterministic RTB provider fixture only.
Run only with disposable CM_TEST_PG_DSN.
"""
from __future__ import annotations
import os
import unittest
from uuid import uuid4

from intelligence import migrate
from intelligence.relevance import repository
from intelligence.relevance.intake import attach_discovery
from intelligence.relevance.offline_runs import (
    create_offline_run, get_offline_run, get_offline_results, cancel_offline_run,
)
from intelligence.relevance.offline_worker import (
    claim_item, finish_item, work_once, heartbeat, reap_exhausted,
)
from intelligence.relevance.presets import strong_only
from intelligence.relevance.v1_contracts import SREContractError


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"disposable PostgreSQL required")
class S4OfflinePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            migrate.apply_migrations(conn)

    def setUp(self):
        import psycopg
        from test_taxonomy_deep_db import DeepDB
        from intelligence.taxonomy_builder.deep_repository import (
            advance_deep, first_review, final_review, get_deep,
        )
        self.fixture=DeepDB("test_complete_two_human_reviews_saturation_and_atomic_sre_tree")
        self.fixture.dsn=self.dsn
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.pid=self.fixture.project["id"]
        self.fixture.prepare()
        rid=self.fixture.rid
        self.assertEqual(advance_deep(self.pid,rid,sender=self.fixture.sender)["status"],"review1")
        first=get_deep(self.pid,rid)["nodes"]
        first_review(self.pid,rid,[{"id":n["id"],"action":"approve"} for n in first])
        self.assertEqual(advance_deep(self.pid,rid,sender=self.fixture.sender)["status"],"expand_pending")
        self.assertEqual(advance_deep(self.pid,rid,sender=self.fixture.sender)["status"],"review2")
        final=get_deep(self.pid,rid)["nodes"]
        self.assertEqual(final_review(self.pid,rid,[{"id":n["id"],"action":"approve"} for n in final])
                         ["status"],"approved")
        self.conn=psycopg.connect(self.dsn)
        self.addCleanup(self.conn.close)
        self.pub=str(self.conn.execute(
            "SELECT builder_publication_id FROM tb_deep_publications WHERE project_id=%s AND run_id=%s::uuid",
            (self.pid,rid)).fetchone()[0])
        self.pipeline=repository.save_pipeline(self.conn,self.pid,"offline-strong",strong_only(),
                                               "owner",activate=False)
        self.source="s4-local-"+uuid4().hex
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_store_metadata,
               can_analyze_media,retention_days)
               VALUES(%s,'offline_test','manual','enabled',TRUE,FALSE,30)""",
            (self.source,))
        self.policy=self.conn.execute(
            """INSERT INTO source_policy_versions
               (source_id,version,is_active,permissions,retention_policy,reviewed_at)
               VALUES (%s,1,TRUE,
               '{"can_store_metadata":true,"can_semantic_classify":true}'::jsonb,
               '{"retention_days":20}'::jsonb,now()) RETURNING id""",
            (self.source,)).fetchone()[0]
        self.conn.commit()

    def add_video(self, title):
        name=uuid4().hex[:11]
        vid=self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url)
               VALUES ('youtube',%s,%s) RETURNING id""",
            (name,"https://www.youtube.com/watch?v="+name)).fetchone()[0]
        did=self.conn.execute(
            "INSERT INTO discoveries(video_id,source_id,method) VALUES(%s,%s,'manual_import') RETURNING id",
            (vid,self.source)).fetchone()[0]
        self.conn.execute(
            """INSERT INTO source_video_metadata
               (video_id,source_id,source_policy_version_id,fetched_at,title,is_current)
               VALUES (%s,%s,%s,now(),%s,TRUE)""",
            (vid,self.source,self.policy,title))
        self.conn.commit()
        return attach_discovery(self.conn,self.pid,did)

    def test_full_pinned_run_replay_multi_label_and_run_immutability(self):
        one=self.add_video("Проверка понятности фотоучёта")
        two=self.add_video("Обманчивые лечебные обещания")
        ids=[one["content_version_id"],two["content_version_id"]]
        new=create_offline_run(self.conn,self.pid,self.pub,ids,self.pipeline,"run-qa-1")
        run=new["run_id"]
        self.assertEqual(new["items"],2)
        replay=create_offline_run(self.conn,self.pid,self.pub,ids,self.pipeline,"run-qa-1")
        self.assertEqual(replay["run_id"],run)
        self.assertTrue(replay["replayed"])
        with self.assertRaises(SREContractError):
            create_offline_run(self.conn,self.pid,self.pub,ids[:1],self.pipeline,"run-qa-1")
        while work_once(self.conn):
            pass
        detail=get_offline_run(self.conn,self.pid,run)
        self.assertEqual(detail["status"],"completed")
        self.assertEqual({i["state"] for i in detail["items"]},{"completed"})
        rows=self.conn.execute(
            """SELECT video_id,decision,evidence,stage_trace
               FROM sre_classification_results
               WHERE run_id=%s::uuid ORDER BY video_id,category_key""",
            (run,)).fetchall()
        self.assertEqual(len(rows),6)
        self.assertEqual(len(get_offline_results(self.conn,self.pid,run)["results"]),6)
        self.assertEqual(self.conn.execute("SELECT execution_mode FROM sre_classification_runs WHERE id=%s::uuid",(run,)).fetchone()[0],"offline_fixture")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM sre_live_classification_results WHERE run_id=%s::uuid",(run,)).fetchone()[0],0)
        self.assertEqual(sum(x[1]=="match" for x in rows),1)
        self.assertEqual(sum(x[1]=="no_match" for x in rows),3)
        self.assertEqual(sum(x[1]=="uncertain" for x in rows),2)
        self.assertTrue(all(row[3][0]["synthetic"] is True for row in rows))
        self.assertTrue(all(row[3][0]["provider"]=="offline_fixture" for row in rows))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_stage_executions WHERE run_id=%s::uuid",(run,)
        ).fetchone()[0],2)
        self.assertEqual(str(self.conn.execute(
            "SELECT cost_usd FROM sre_classification_runs WHERE id=%s::uuid",(run,)
        ).fetchone()[0]),"0.000000")
        self.assertFalse(work_once(self.conn))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (run,)).fetchone()[0],6)
        self.assertEqual(cancel_offline_run(self.conn,self.pid,run),"completed")
        with self.assertRaises(SREContractError):
            get_offline_run(self.conn,self.fixture.other["id"],run)
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE sre_offline_runs SET taxonomy_sha256=%s WHERE run_id=%s::uuid",
                    ("0"*64,run))
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE sre_offline_items SET content_sha256=%s WHERE run_id=%s::uuid",
                    ("0"*64,run))

    def test_m13_pinned_c9_d1_real_batches_full_matrix_and_settlement_denials(self):
        """Actual PostgreSQL17 C9/D1, immutable memberships and no phantom labels."""
        from intelligence.relevance.live_planner import create_live_run
        from intelligence.relevance.live_batch_manifest import (
            materialize_live_batches, get_live_batch_coverage,
        )
        self.conn.execute(
            """UPDATE source_policy_versions
               SET permissions=jsonb_set(permissions,'{can_external_llm}','true'::jsonb)
               WHERE id=%s""",(self.policy,),
        )
        a=self.add_video("M13 подтверждённое видео A")
        b=self.add_video("M13 подтверждённое видео B")
        rid=create_live_run(
            self.conn,self.pid,self.pub,
            [a["content_version_id"],b["content_version_id"]],
            self.pipeline,"m13-"+uuid4().hex,
        )["run_id"]
        first=materialize_live_batches(self.conn,self.pid,rid,batch_size=1)
        self.assertEqual(first["status"],"pending_consent")
        self.assertEqual(first["batches"],2)
        self.assertEqual(first["items"],2)
        self.assertEqual(first["hypotheses"],3)
        self.assertEqual(first["expected_pairs"],6)
        self.assertEqual(first["evaluated_pairs"],0)
        self.assertFalse(first["execution_allowed"])
        replay=materialize_live_batches(self.conn,self.pid,rid,batch_size=1)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["batch_ids"],replay["batch_ids"])
        coverage=get_live_batch_coverage(self.conn,self.pid,rid)
        self.assertEqual(coverage["coverage"]["total_pairs"],6)
        self.assertEqual(coverage["coverage"]["evaluated_pairs"],0)
        self.assertEqual(coverage["coverage"]["not_evaluated_pairs"],6)
        self.assertEqual(coverage["coverage"]["blocked_pairs"],0)
        self.assertFalse(coverage["coverage"]["complete"])
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_live_batch_members
               WHERE project_id=%s AND run_id=%s::uuid""",
            (self.pid,rid),
        ).fetchone()[0],2)
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_live_expected_pairs
               WHERE project_id=%s AND run_id=%s::uuid""",
            (self.pid,rid),
        ).fetchone()[0],6)

        def denied(action):
            with self.assertRaises(Exception):
                with self.conn.transaction():
                    action()

        # Missing 1 of 3 C9 decisions MUST NOT become completed or partial.
        item=self.conn.execute(
            """SELECT id FROM sre_live_items
               WHERE project_id=%s AND run_id=%s::uuid ORDER BY id LIMIT 1""",
            (self.pid,rid),
        ).fetchone()[0]
        denied(lambda:self.conn.execute(
            "UPDATE sre_live_items SET state='completed' WHERE id=%s",(item,)
        ))
        denied(lambda:self.conn.execute(
            "UPDATE sre_live_items SET state='partial' WHERE id=%s",(item,)
        ))
        denied(lambda:self.conn.execute(
            "UPDATE sre_live_batches SET state='completed' WHERE batch_id=%s::uuid",
            (first["batch_ids"][0],),
        ))
        denied(lambda:self.conn.execute(
            "UPDATE sre_live_batches SET state='partial' WHERE batch_id=%s::uuid",
            (first["batch_ids"][0],),
        ))
        denied(lambda:self.conn.execute(
            """UPDATE sre_live_runs SET state='completed',finished_items=2
               WHERE run_id=%s::uuid""",(rid,),
        ))
        denied(lambda:self.conn.execute(
            """UPDATE sre_live_runs SET state='partial',finished_items=1
               WHERE run_id=%s::uuid""",(rid,),
        ))
        denied(lambda:self.conn.execute(
            """INSERT INTO sre_live_expected_pairs
               (run_id,project_id,batch_id,item_id,category_key)
               VALUES (%s::uuid,%s,%s::uuid,%s,%s::uuid)""",
            (rid,self.pid,first["batch_ids"][0],item,
             "55555555-5555-4555-8555-555555555555"),
        ))
        denied(lambda:self.conn.execute(
            """DELETE FROM sre_live_expected_pairs
               WHERE run_id=%s::uuid AND item_id=%s""",(rid,item),
        ))
        denied(lambda:self.conn.execute(
            """UPDATE sre_live_batch_members SET ordinal=100
               WHERE run_id=%s::uuid AND item_id=%s""",(rid,item),
        ))
        denied(lambda:self.conn.execute(
            """UPDATE sre_live_expected_pairs SET category_key=%s::uuid
               WHERE run_id=%s::uuid AND item_id=%s""",
            ("55555555-5555-4555-8555-555555555555",rid,item),
        ))
        with self.assertRaises(SREContractError):
            materialize_live_batches(self.conn,self.pid,rid,batch_size=2)
        with self.assertRaises(SREContractError):
            get_live_batch_coverage(self.conn,self.fixture.other["id"],rid)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (rid,),
        ).fetchone()[0],0)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_live_expected_pairs WHERE run_id=%s::uuid",
            (rid,),
        ).fetchone()[0],6)

    def test_m13_parallel_repeatable_batch_materialization(self):
        """Two PostgreSQL connections agree on the same locked batch layout."""
        from concurrent.futures import ThreadPoolExecutor
        import psycopg
        from intelligence.relevance.live_planner import create_live_run
        from intelligence.relevance.live_batch_manifest import materialize_live_batches
        self.conn.execute(
            """UPDATE source_policy_versions
               SET permissions=jsonb_set(permissions,'{can_external_llm}','true'::jsonb)
               WHERE id=%s""",(self.policy,),
        )
        item=self.add_video("M13 одновременная разметка пакетов")
        rid=create_live_run(
            self.conn,self.pid,self.pub,[item["content_version_id"]],
            self.pipeline,"m13-parallel-"+uuid4().hex,
        )["run_id"]
        self.conn.commit()
        def worker(_):
            with psycopg.connect(self.dsn) as c:
                result=materialize_live_batches(c,self.pid,rid,batch_size=1)
                c.commit()
                return result
        with ThreadPoolExecutor(max_workers=2) as ex:
            result=list(ex.map(worker,[1,2]))
        self.assertEqual(len({tuple(r["batch_ids"]) for r in result}),1)
        self.assertEqual(sorted(x["replayed"] for x in result),[False,True])
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_live_expected_pairs WHERE run_id=%s::uuid",
            (rid,),
        ).fetchone()[0],3)

    def test_m12_live_plan_idempotent_replay_conflict_and_rights(self):
        """Real PG17 C9+D1: consent-gated enqueue, no paid/provider/result side effects."""
        from intelligence.relevance.live_planner import create_live_run,get_live_plan
        from intelligence.relevance.v1_contracts import fingerprint
        one=self.add_video("M12 проверка закреплённой версии 1")
        two=self.add_video("M12 проверка закреплённой версии 2")
        ids=[one["content_version_id"],two["content_version_id"]]
        # S3 metadata storage alone does not permit outbound LLM requests.
        with self.assertRaises(SREContractError) as denied:
            create_live_run(self.conn,self.pid,self.pub,ids,self.pipeline,
                            "m12-no-rights")
        self.assertEqual(denied.exception.code,"rights_denied")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_live_runs WHERE project_id=%s",(self.pid,)
        ).fetchone()[0],0)
        self.conn.execute(
            """UPDATE source_policy_versions
               SET permissions=jsonb_set(permissions,'{can_external_llm}','true'::jsonb)
               WHERE id=%s""",(self.policy,),
        )
        key="m12-live-"+uuid4().hex
        fresh=create_live_run(self.conn,self.pid,self.pub,ids,self.pipeline,key)
        self.assertEqual(fresh["status"],"pending_consent")
        self.assertFalse(fresh["replayed"])
        self.assertEqual(fresh["items"],2)
        self.assertEqual(fresh["hypotheses"],3)
        self.assertEqual(fresh["execution_mode"],"live")
        rid=fresh["run_id"]
        replay=create_live_run(self.conn,self.pid,self.pub,ids[::-1]+ids[:1],
                               self.pipeline,key)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["run_id"],rid)
        self.assertEqual(replay["request_sha256"],fresh["request_sha256"])
        with self.assertRaises(SREContractError) as mismatch:
            create_live_run(self.conn,self.pid,self.pub,ids[:1],self.pipeline,key)
        self.assertEqual(mismatch.exception.code,"idempotency_conflict")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_live_runs WHERE project_id=%s",(self.pid,)
        ).fetchone()[0],1)
        stored=self.conn.execute(
            """SELECT manifest,manifest_sha256,request_sha256,execution_mode,
                      state,expected_items
               FROM sre_live_runs WHERE run_id=%s::uuid AND project_id=%s""",
            (rid,self.pid),
        ).fetchone()
        self.assertEqual(stored[1],fingerprint(stored[0]))
        self.assertEqual(stored[2],stored[1])
        self.assertEqual(stored[3:6],("live","pending_consent",2))
        self.assertEqual(stored[0]["selected_content_versions"],sorted(ids))
        self.assertEqual(stored[0]["schema_version"],"sre-live-plan/v1")
        self.assertEqual(stored[0]["execution_mode"],"live")
        self.assertEqual(len(stored[0]["expected_hypothesis_keys"]),3)
        saved=self.conn.execute(
            """SELECT video_id,content_version_id,content_sha256,source_id,
                      source_policy_version_id,discovery_id,state,manifest_sha256
               FROM sre_live_items WHERE project_id=%s AND run_id=%s::uuid
               ORDER BY video_id""",(self.pid,rid),
        ).fetchall()
        self.assertEqual(len(saved),2)
        self.assertEqual({row[6] for row in saved},{"not_evaluated"})
        self.assertTrue(all(row[7]==stored[1] for row in saved))
        self.assertEqual({row[1] for row in saved},set(ids))
        self.assertEqual({row[2] for row in saved},
                         {one["content_sha256"],two["content_sha256"]})
        self.assertTrue(all(row[3]==self.source for row in saved))
        self.assertTrue(all(row[4]==self.policy for row in saved))
        status=get_live_plan(self.conn,self.pid,rid)
        self.assertEqual(status["status"],"pending_consent")
        self.assertEqual({x["state"] for x in status["items"]},{"not_evaluated"})
        with self.assertRaises(SREContractError):
            get_live_plan(self.conn,self.fixture.other["id"],rid)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_live_batches WHERE run_id=%s::uuid",(rid,),
        ).fetchone()[0],0)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (rid,),
        ).fetchone()[0],0)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_runs WHERE id=%s::uuid",
            (rid,),
        ).fetchone()[0],0)
        self.conn.execute(
            "UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",
            (self.policy,),
        )
        with self.assertRaises(SREContractError) as revoked:
            create_live_run(self.conn,self.pid,self.pub,ids,self.pipeline,
                            "m12-revoked-"+uuid4().hex)
        self.assertEqual(revoked.exception.code,"rights_denied")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_live_runs WHERE project_id=%s",(self.pid,)
        ).fetchone()[0],1)

    def test_m12_two_planners_same_project_key_cannot_duplicate_live_run(self):
        """Real PG17 row lock serializes concurrent independent connections."""
        from concurrent.futures import ThreadPoolExecutor
        import psycopg
        from intelligence.relevance.live_planner import create_live_run
        item=self.add_video("M12 parallel identical run planning")
        self.conn.execute(
            """UPDATE source_policy_versions
               SET permissions=jsonb_set(permissions,'{can_external_llm}','true'::jsonb)
               WHERE id=%s""",(self.policy,),
        )
        self.conn.commit()
        key="m12-race-"+uuid4().hex
        def worker(_):
            with psycopg.connect(self.dsn) as connection:
                result=create_live_run(
                    connection,self.pid,self.pub,
                    [item["content_version_id"]],self.pipeline,key)
                connection.commit()
                return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(worker,[0,1]))
        self.assertEqual(len({x["run_id"] for x in results}),1)
        self.assertEqual(sorted(x["replayed"] for x in results),[False,True])
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_live_runs
               WHERE project_id=%s AND idempotency_key=%s""",
            (self.pid,key),
        ).fetchone()[0],1)
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_live_items
               WHERE run_id=%s::uuid""",(results[0]["run_id"],),
        ).fetchone()[0],1)

    def test_m11_live_core_cross_tenant_origin_immutability_and_lease(self):
        """M11 real PG17 C9/D1 provenance, SHA/FK/lease tamper and no paid runs."""
        from json import dumps
        from intelligence.relevance.hypothesis_context import build_classifier_contexts
        from intelligence.relevance.taxonomy_adapter import read_authorized_taxonomy
        from intelligence.relevance.v1_contracts import fingerprint
        tax=self.conn.execute(
            """SELECT sre_taxonomy_version_id FROM tb_sre_taxonomy_links
               WHERE project_id=%s AND builder_publication_id=%s::uuid""",
            (self.pid,self.pub),
        ).fetchone()[0]
        ctx=build_classifier_contexts(read_authorized_taxonomy(self.conn,self.pid,self.pub))
        keys=sorted(c["category_key"] for c in ctx)
        a=self.add_video("M11 реальная версия A")
        b=self.add_video("M11 реальная версия B")
        rid,batch_id=str(uuid4()),str(uuid4())
        manifest={"versions":[a["content_version_id"],b["content_version_id"]],
                  "keys":keys}
        ins_run="""INSERT INTO sre_live_runs
            (run_id,project_id,publication_id,taxonomy_version_id,taxonomy_sha256,
             pipeline_config_id,pipeline_sha256,request_sha256,manifest,
             manifest_sha256,idempotency_key,expected_items)
            VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)"""
        args=(rid,self.pid,self.pub,tax,"a"*64,self.pipeline,"b"*64,
              fingerprint(manifest),dumps(manifest),fingerprint(manifest),
              "m11-"+uuid4().hex,2)
        self.conn.execute(ins_run,args)
        self.assertEqual(self.conn.execute(
            "SELECT execution_mode,state FROM sre_live_runs WHERE run_id=%s::uuid",
            (rid,),
        ).fetchone(),("live","planned"))
        # Anonymous/PUBLIC may not directly read or mutate LIVE tables.
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM information_schema.role_table_grants
               WHERE grantee='PUBLIC'
               AND table_name=ANY(%s::text[])""",
            (["sre_live_runs","sre_live_items","sre_live_batches"],),
        ).fetchone()[0],0)

        def insert_item(snap,project=None,checksum=None,discovery=None,
                        revision=None,origin=None):
            ver=snap["content_version_id"]
            rev,obs=self.conn.execute(
                "SELECT revision,metadata_observation_id FROM sre_content_versions WHERE id=%s",
                (ver,),
            ).fetchone()
            return self.conn.execute(
                """INSERT INTO sre_live_items
                  (run_id,project_id,video_id,content_version_id,content_sha256,
                   source_id,source_policy_version_id,discovery_id,
                   metadata_observation_id,content_revision,origin,
                   expected_hypothesis_keys,contexts_sha256,manifest_sha256)
                  VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          %s::jsonb,%s::jsonb,%s,%s) RETURNING id""",
                (rid,project or self.pid,snap["video_id"],ver,
                 checksum or snap["content_sha256"],snap["source_id"],
                 snap["source_policy_version_id"],
                 discovery or snap["provenance"]["discovery_id"],obs,
                 revision or rev,dumps(origin if origin is not None
                                       else snap["provenance"]),dumps(keys),
                 "c"*64,fingerprint(manifest)),
            ).fetchone()[0]

        item=insert_item(a)
        other_item=insert_item(b)
        self.conn.execute(
            """INSERT INTO sre_live_batches
              (batch_id,run_id,project_id,ordinal,manifest,manifest_sha256,input_sha256)
              VALUES (%s::uuid,%s::uuid,%s,0,%s::jsonb,%s,%s)""",
            (batch_id,rid,self.pid,dumps({"items":[item,other_item]}),"d"*64,"e"*64),
        )
        self.conn.commit()
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_runs WHERE id=%s::uuid",(rid,),
        ).fetchone()[0],0)

        def denied(action):
            with self.assertRaises(Exception):
                with self.conn.transaction():
                    action()

        foreign=self.fixture.other["id"]
        for change in (
            lambda row:row.__setitem__(1,foreign),
            lambda row:row.__setitem__(3,tax+9999999),
            lambda row:row.__setitem__(5,self.pipeline+9999999),
        ):
            row=list(args);row[0]=str(uuid4());row[10]="m11-invalid-"+uuid4().hex
            change(row)
            denied(lambda row=row:self.conn.execute(ins_run,tuple(row)))
        denied(lambda:insert_item(a,project=foreign))
        denied(lambda:insert_item(a,checksum="f"*64))
        denied(lambda:insert_item(a,discovery=b["provenance"]["discovery_id"]))
        denied(lambda:insert_item(a,revision=999))
        denied(lambda:insert_item(a,origin={"forged":"lineage"}))
        denied(lambda:self.conn.execute(
            """INSERT INTO sre_live_batches
              (batch_id,run_id,project_id,ordinal,manifest,manifest_sha256,input_sha256)
              VALUES (%s::uuid,%s::uuid,%s,1,'{}'::jsonb,%s,%s)""",
            (str(uuid4()),rid,foreign,"d"*64,"e"*64),
        ))
        for table,field,pk,key in (
            ("sre_live_runs","manifest_sha256","run_id",rid),
            ("sre_live_runs","taxonomy_sha256","run_id",rid),
            ("sre_live_items","content_sha256","id",item),
            ("sre_live_items","origin","id",item),
            ("sre_live_batches","manifest_sha256","batch_id",batch_id),
            ("sre_live_batches","input_sha256","batch_id",batch_id),
        ):
            sql=("UPDATE "+table+" SET "+field+"=%s::jsonb WHERE "+pk+"=%s"
                 if field=="origin" else
                 "UPDATE "+table+" SET "+field+"=%s WHERE "+pk+"=%s")
            bad=dumps({"forged":True}) if field=="origin" else "f"*64
            denied(lambda sql=sql,bad=bad,key=key:self.conn.execute(sql,(bad,key)))
        for table,pk,key in (
            ("sre_live_runs","run_id",rid),
            ("sre_live_items","id",item),
            ("sre_live_batches","batch_id",batch_id),
        ):
            denied(lambda table=table,pk=pk,key=key:self.conn.execute(
                "DELETE FROM "+table+" WHERE "+pk+"=%s",(key,),
            ))
        self.conn.execute("UPDATE sre_live_items SET state='queued' WHERE id=%s",(item,))
        token=str(uuid4())
        self.conn.execute(
            """UPDATE sre_live_items SET state='running',lease_generation=1,
               worker_token=%s::uuid,lease_until=now()+interval '1 minute'
               WHERE id=%s""",(token,item),
        )
        denied(lambda:self.conn.execute(
            "UPDATE sre_live_items SET worker_token=%s::uuid WHERE id=%s",
            (str(uuid4()),item),
        ))
        denied(lambda:self.conn.execute(
            "UPDATE sre_live_items SET lease_generation=0 WHERE id=%s",(item,),
        ))
        denied(lambda:self.conn.execute(
            "UPDATE sre_live_items SET lease_generation=3 WHERE id=%s",(item,),
        ))
        self.assertEqual(self.conn.execute(
            "SELECT lease_generation,worker_token::text FROM sre_live_items WHERE id=%s",
            (item,),
        ).fetchone(),(1,token))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_live_batches WHERE run_id=%s::uuid",(rid,),
        ).fetchone()[0],1)

    def test_m09_real_pg_preview_version_dedup_full_pairs_and_revocation(self):
        from intelligence.relevance.dataset_preview import (
            read_dataset_preview, load_dataset_preview,
        )
        from intelligence.relevance.v1_contracts import fingerprint
        one=self.add_video("Видео с источником и проверяемым заголовком")
        two=self.add_video("Другой заголовок")
        first_id=one["content_version_id"]
        second_id=two["content_version_id"]
        # Same D1 discovery/video but a changed source-specific observation
        # legitimately produces a new content version; preview exposes the
        # intentionally discarded revision and never doubles classifier pairs.
        self.conn.execute(
            """UPDATE source_video_metadata SET title='Вторая версия заголовка'
               WHERE video_id=%s AND source_id=%s""",
            (one["video_id"],self.source),
        )
        updated=attach_discovery(
            self.conn,self.pid,one["provenance"]["discovery_id"]
        )
        newer_id=updated["content_version_id"]
        self.assertNotEqual(newer_id,first_id)
        self.assertEqual(updated["video_id"],one["video_id"])
        ids=[first_id,second_id,newer_id,newer_id]
        result=read_dataset_preview(self.conn,self.pid,self.pub,ids)
        self.assertEqual(result["counts"]["requested_references"],4)
        self.assertEqual(result["counts"]["unique_versions"],3)
        self.assertEqual(result["counts"]["duplicate_references"],1)
        self.assertEqual(result["counts"]["deduplicated_video_versions"],1)
        self.assertEqual(result["counts"]["selected_videos"],2)
        self.assertEqual(result["counts"]["hypotheses"],3)
        self.assertEqual(result["counts"]["total_pairs"],6)
        self.assertEqual(result["counts"]["eligible_pairs"],6)
        self.assertEqual(result["counts"]["blocked_pairs"],0)
        self.assertEqual(
            result["preview_sha256"],
            fingerprint({k:v for k,v in result.items() if k!="preview_sha256"})
        )
        self.assertEqual(
            result["deduplicated_video_versions"],
            [{"video_id":one["video_id"],"content_version_id":first_id,
              "kept_content_version_id":newer_id,
              "reason":"superseded_in_selection"}],
        )
        self.assertEqual({p["content_version_id"] for p in result["pairs"]},
                         {newer_id,second_id})
        self.assertTrue(all(p["status"]=="eligible" for p in result["pairs"]))
        self.assertFalse(result["execution_allowed"])
        self.assertTrue(all(
            "transcript_unavailable" in video["warnings"]
            for video in result["videos"]
        ))
        # Verify the actual production entrypoint, with one read-only
        # transaction. Data must be committed for a separate connection.
        self.conn.commit()
        import psycopg
        other=load_dataset_preview(
            self.pid,self.pub,ids,connect=lambda:psycopg.connect(self.dsn)
        )
        self.assertEqual(other,result)
        self.conn.execute(
            "UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",
            (self.policy,),
        )
        blocked=read_dataset_preview(self.conn,self.pid,self.pub,ids)
        self.assertEqual(blocked["counts"]["total_pairs"],6)
        self.assertEqual(blocked["counts"]["eligible_pairs"],0)
        self.assertEqual(blocked["counts"]["blocked_pairs"],6)
        self.assertTrue(all(p["reason"]=="rights_unavailable"
                            for p in blocked["pairs"]))
        self.assertTrue(all(v["content_sha256"] is None
                            for v in blocked["videos"]))
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM sre_offline_runs WHERE project_id=%s",
                (self.pid,),
            ).fetchone()[0],0
        )

    def test_m09_project_isolation_and_expired_observation_keeps_pairs(self):
        from intelligence.relevance.dataset_preview import read_dataset_preview
        sample=self.add_video("Истекающее наблюдение")
        cid=sample["content_version_id"]
        from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
        with self.assertRaises((SREContractError,PipelineError)):
            read_dataset_preview(self.conn,self.fixture.other["id"],self.pub,[cid])
        # Foreign project cannot use its own/other's publication as an oracle.
        self.conn.execute(
            """UPDATE source_video_metadata SET is_current=FALSE,
               deleted_at=now() WHERE video_id=%s AND source_id=%s""",
            (sample["video_id"],self.source),
        )
        result=read_dataset_preview(self.conn,self.pid,self.pub,[cid])
        self.assertEqual(result["counts"]["total_pairs"],3)
        self.assertEqual(result["counts"]["eligible_pairs"],0)
        self.assertEqual(result["counts"]["blocked_pairs"],3)
        self.assertTrue(all(
            p["reason"]=="rights_unavailable" for p in result["pairs"]
        ))

    def test_m08_worker_refuses_d1_lineage_change_after_run_is_planned(self):
        """A pinned S3 hash alone cannot certify a D1 source after mutation."""
        from intelligence.relevance.offline_worker import finish_item
        snap=self.add_video("Проверяем реальную D1 цепочку")
        cid=snap["content_version_id"]
        rid=create_offline_run(
            self.conn,self.pid,self.pub,[cid],self.pipeline,"run-m08-lineage",
        )["run_id"]
        claim=claim_item(self.conn)
        self.assertIsNotNone(claim)
        # The immutable S3 row still has its original discovery provenance.
        # Alter D1 query identity between planning and worker completion.
        foreign_query=self.conn.execute(
            """INSERT INTO search_queries(source_id,query_text,method)
               VALUES (%s,'changed query after S3 intake','manual')
               RETURNING id""",(self.source,),
        ).fetchone()[0]
        self.conn.execute(
            """UPDATE discoveries SET search_query_id=%s
               WHERE id=%s""",(foreign_query,snap["provenance"]["discovery_id"]),
        )
        with self.assertRaises(SREContractError) as mismatch:
            finish_item(self.conn,claim)
        self.assertEqual(mismatch.exception.code,"snapshot_mismatch")
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_classification_results
               WHERE run_id=%s::uuid""",(rid,),
        ).fetchone()[0],0)
        self.assertEqual(self.conn.execute(
            """SELECT count(*) FROM sre_stage_executions
               WHERE run_id=%s::uuid""",(rid,),
        ).fetchone()[0],0)
        self.assertEqual(get_offline_results(
            self.conn,self.pid,rid)["results"],[])

    def test_revocation_partial_denial_and_rights_block_after_claim(self):
        a=self.add_video("Проверка понятности фотоучёта")
        b=self.add_video("Только текст о еде")
        r=create_offline_run(self.conn,self.pid,self.pub,[a["content_version_id"],b["content_version_id"]],
                             self.pipeline,"run-rights")["run_id"]
        first=claim_item(self.conn)
        self.assertTrue(finish_item(self.conn,first))
        second=claim_item(self.conn)
        # Revoke a selected source observation while another video is queued;
        # use a separate policy source in more granular scenarios.
        self.conn.execute(
            "UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",
            (self.policy,))
        self.assertTrue(finish_item(self.conn,second))
        self.assertEqual(get_offline_run(self.conn,self.pid,r)["status"],"partial")
        from intelligence.relevance.storage import record_result
        from intelligence.relevance.contracts import ContractError
        with self.assertRaises(ContractError):
            record_result(self.conn,self.pid,r,first["video_id"],a["content_version_id"],
                          str(uuid4()),"no_match","Closed run",[],[])
        self.assertEqual(get_offline_results(self.conn,self.pid,r)["revoked_results_hidden"],3)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (r,)).fetchone()[0],3)
        self.assertEqual(get_offline_run(self.conn,self.pid,r)["items"][1]["state"],
                         "blocked_rights")

    def test_crash_rollback_retry_and_stale_worker_fencing(self):
        content=self.add_video("Проверка понятности фотоучёта")
        r=create_offline_run(self.conn,self.pid,self.pub,[content["content_version_id"]],
                             self.pipeline,"run-recover")["run_id"]
        first=claim_item(self.conn)
        self.assertTrue(heartbeat(self.conn,first))
        with self.assertRaises(RuntimeError):
            work_fault = finish_item(self.conn,first,fault="before_commit")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (r,)).fetchone()[0],0)
        # Connection failed inside a transaction only; rollback permits retry.
        self.assertTrue(heartbeat(self.conn,first))
        self.assertTrue(finish_item(self.conn,first))
        self.assertFalse(finish_item(self.conn,first))
        self.assertEqual(get_offline_run(self.conn,self.pid,r)["status"],"completed")

        second=self.add_video("Тема без явного совпадения")
        other=create_offline_run(self.conn,self.pid,self.pub,[second["content_version_id"]],
                                 self.pipeline,"run-fence")["run_id"]
        old=claim_item(self.conn)
        self.conn.execute(
            "UPDATE sre_offline_items SET lease_until=now()-interval '2 seconds' WHERE id=%s",
            (old["item_id"],))
        new=claim_item(self.conn)
        self.assertNotEqual(old["worker_token"],new["worker_token"])
        self.assertGreater(new["generation"],old["generation"])
        self.assertFalse(heartbeat(self.conn,old))
        self.assertFalse(finish_item(self.conn,old))
        self.assertTrue(finish_item(self.conn,new))
        self.assertEqual(get_offline_run(self.conn,self.pid,other)["status"],"completed")

    def test_exhausted_lease_and_two_workers_claim_distinct_tasks(self):
        import psycopg
        a=self.add_video("Проверка понятности фотоучёта")
        b=self.add_video("Другая задача")
        rid=create_offline_run(self.conn,self.pid,self.pub,
            [a["content_version_id"],b["content_version_id"]],
            self.pipeline,"run-concurrent")["run_id"]
        self.conn.commit()
        with psycopg.connect(self.dsn) as worker2:
            first=claim_item(self.conn)
            self.conn.commit()
            second=claim_item(worker2)
            self.assertIsNotNone(second)
            self.assertNotEqual(first["item_id"],second["item_id"])
            self.assertFalse(claim_item(worker2))
            self.assertTrue(finish_item(self.conn,first))
            self.conn.commit()
            self.assertTrue(finish_item(worker2,second))
        self.assertEqual(get_offline_run(self.conn,self.pid,rid)["status"],"completed")

        c=self.add_video("Проверить устаревшую попытку")
        rerun=create_offline_run(self.conn,self.pid,self.pub,
             [c["content_version_id"]],self.pipeline,"run-exhaust")["run_id"]
        old=claim_item(self.conn)
        self.conn.execute("""UPDATE sre_offline_items SET attempts=max_attempts,
             lease_until=now()-interval '1 minute' WHERE id=%s""",
             (old["item_id"],))
        self.assertEqual(reap_exhausted(self.conn),1)
        self.assertEqual(get_offline_run(self.conn,self.pid,rerun)["status"],"failed")
        self.assertFalse(finish_item(self.conn,old))
        self.assertFalse(claim_item(self.conn))

    def test_cancel_while_claimed_prevents_any_commit(self):
        a=self.add_video("Тема")
        r=create_offline_run(self.conn,self.pid,self.pub,[a["content_version_id"]],
                             self.pipeline,"run-cancel")["run_id"]
        claim=claim_item(self.conn)
        self.assertEqual(cancel_offline_run(self.conn,self.pid,r),"cancelled")
        self.assertFalse(finish_item(self.conn,claim))
        self.assertEqual(get_offline_run(self.conn,self.pid,r)["status"],"cancelled")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (r,)).fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
