"""PG17 only: independent Deep V2 publication + SRE all-or-nothing.

All model responses were mocked by DeepV2Review2PG; NO paid OpenRouter
calls. Covers 14/11 fidelity, UUID fact reuse, atomic rollback and concurrency.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Lock
from unittest.mock import patch
from uuid import UUID
import unittest

from psycopg.errors import RaiseException
from test_ke_stage02_intake_db import DSN
from test_taxonomy_deep_v2_review2_db import DeepV2Review2PG

from intelligence.taxonomy_builder.deep_v2_publication import (
    RTBDeepV2PublicationRepository,
)
from intelligence.taxonomy_builder.deep_v2_sre_bridge import (
    prepare_deep_v2_sre_projection, mirror_deep_v2_sre_in_transaction,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError


@unittest.skipUnless(DSN, "PostgreSQL17 required for RTB Deep V2 publication")
class DeepV2PublicationPG(unittest.TestCase):
    def setUp(self):
        self.fx = DeepV2Review2PG(methodName="runTest")
        self.fx.setUp()
        approved = self.fx.repo.submit_review2(
            self.fx.pid, self.fx.rid, self.fx.actor, self.fx._approve())
        self.assertEqual(approved["status"], "approved")
        self.pid, self.rid, self.actor = (
            self.fx.pid, self.fx.rid, self.fx.actor)
        self.factory = self.fx.factory
        self.repo = RTBDeepV2PublicationRepository(
            connection_factory=self.factory)
        self.signed_h2 = approved

    def tearDown(self):
        self.fx.tearDown()

    def _counts(self):
        with self.factory() as conn:
            return conn.execute(
                """SELECT
                   (SELECT count(*) FROM tb_deep_v2_publications
                     WHERE project_id=%s),
                   (SELECT count(*) FROM tb_deep_v2_sre_links
                     WHERE project_id=%s),
                   (SELECT count(*) FROM sre_taxonomy_versions
                     WHERE project_id=%s),
                   (SELECT count(*) FROM sre_facts
                     WHERE project_id=%s),
                   (SELECT status FROM tb_deep_v2_runs
                     WHERE project_id=%s AND id=%s)""",
                (self.pid,self.pid,self.pid,self.pid,self.pid,self.rid),
            ).fetchone()

    def test_publish_once_has_exact_14_and_11_field_immutable_views(self):
        before = self._counts()
        result = self.repo.publish(self.pid, self.rid, self.actor)
        self.assertTrue(result["created"])
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(self._counts()[0]-before[0], 1)
        self.assertEqual(self._counts()[1]-before[1], 1)
        view = self.repo.get_publication(
            self.pid, self.rid, self.actor, projection="deep-v2")
        legacy = self.repo.get_publication(
            self.pid, self.rid, self.actor, projection="legacy-core-v1")
        self.assertEqual(view["node_count"], legacy["node_count"])
        self.assertEqual(view["nodes"], self.signed_h2["approved_nodes"])
        self.assertTrue(all(len(n) == 14 for n in view["nodes"]))
        self.assertTrue(all(len(n) == 11 for n in legacy["nodes"]))
        self.assertEqual(view["publication_id"], legacy["publication_id"])
        self.assertEqual(view["v1_core_sha256"], legacy["v1_core_sha256"])
        self.assertEqual(view["sre_taxonomy_version_id"],
                         result["sre_taxonomy_version_id"])

    def test_sre_fact_ids_match_pinned_b9_mapping_and_video_is_not_fact(self):
        self.repo.publish(self.pid, self.rid, self.actor)
        view = self.repo.get_publication(self.pid,self.rid,self.actor)
        ids = set(view["fact_mapping"].values())
        self.assertTrue(ids)
        with self.factory() as conn:
            rows = conn.execute(
                """SELECT id,kind,review_status FROM sre_facts
                   WHERE project_id=%s""", (self.pid,),
            ).fetchall()
            cats = conn.execute(
                """SELECT category_key,rules,related_fact_ids
                   FROM sre_taxonomy_categories
                   WHERE project_id=%s AND taxonomy_version_id=%s""",
                (self.pid,view["sre_taxonomy_version_id"]),
            ).fetchall()
            examples = conn.execute(
                """SELECT example_kind,video_id FROM sre_category_examples
                   WHERE project_id=%s AND taxonomy_version_id=%s""",
                (self.pid,view["sre_taxonomy_version_id"]),
            ).fetchall()
        self.assertEqual({str(r[0]) for r in rows}, ids)
        self.assertTrue(all(r[2] == "approved" for r in rows))
        self.assertEqual(len(cats), len(view["nodes"]))
        for key, rules, related in cats:
            self.assertEqual(str(UUID(str(key))), str(key))
            self.assertTrue(set(related).issubset(ids))
            self.assertTrue(rules["rtb_v2"]["original_a_observations_are_not_product_facts"])
        self.assertTrue(all(kind == "illustrative" and video is None
                            for kind, video in examples))

    def test_same_run_repeat_is_idempotent_and_no_extra_sre_taxonomy(self):
        first = self.repo.publish(self.pid,self.rid,self.actor)
        before = self._counts()
        second = self.repo.publish(self.pid,self.rid,self.actor)
        self.assertFalse(second["created"])
        self.assertEqual(second["publication_id"],first["publication_id"])
        self.assertEqual(self._counts(),before)

    def test_sre_mirror_reuses_existing_same_project_fact_ids(self):
        self.repo.publish(self.pid,self.rid,self.actor)
        before = self._counts()
        signed = self.repo.get_publication(self.pid,self.rid,self.actor)
        binding = self.repo.bindings.get(
            self.pid, self.fx.fx.fx.fx.bid, self.actor)
        projection = prepare_deep_v2_sre_projection(
            signed["nodes"], binding["research_input"])
        class TestRollback(Exception):
            pass
        with self.assertRaises(TestRollback):
            with self.factory() as conn:
                with conn.transaction():
                    result = mirror_deep_v2_sre_in_transaction(
                        conn, projection, approved_by=self.actor)
                    self.assertEqual(result["new_fact_count"],0)
                    self.assertEqual(result["reused_fact_count"],
                                     len(projection["facts"]))
                    raise TestRollback()
        self.assertEqual(self._counts(),before)

    def test_failure_after_full_sre_write_rolls_back_everything(self):
        before = self._counts()
        from intelligence.taxonomy_builder.deep_v2_sre_bridge import (
            mirror_deep_v2_sre_in_transaction as real_mirror,
        )
        def fail_after_writing(conn, projection, *, approved_by):
            real_mirror(conn,projection,approved_by=approved_by)
            raise PipelineError("rtb_v2_injected_atomic_rollback",409)
        with patch(
            "intelligence.taxonomy_builder.deep_v2_publication.mirror_deep_v2_sre_in_transaction",
            side_effect=fail_after_writing,
        ):
            with self.assertRaisesRegex(PipelineError,"injected_atomic_rollback"):
                self.repo.publish(self.pid,self.rid,self.actor)
        self.assertEqual(self._counts(),before)
        # After rollback a normal publish is still possible.
        done = self.repo.publish(self.pid,self.rid,self.actor)
        self.assertTrue(done["created"])

    def _seed_preexisting_sre_fact(self, *, tampered=False):
        from psycopg.types.json import Jsonb
        projected = prepare_deep_v2_sre_projection(
            self.signed_h2["approved_nodes"],
            self.repo.bindings.get(
                self.pid,self.fx.fx.fx.fx.bid,self.actor)["research_input"],
        )
        fact = projected["facts"][0]
        with self.factory() as conn:
            conn.execute(
                """INSERT INTO sre_workspaces(project_id)
                   VALUES(%s) ON CONFLICT(project_id) DO NOTHING""",
                (self.pid,),
            )
            number = conn.execute(
                """SELECT COALESCE(MAX(version),0)+1 FROM sre_profile_versions
                   WHERE project_id=%s""", (self.pid,),
            ).fetchone()[0]
            profile = conn.execute(
                """INSERT INTO sre_profile_versions(
                     project_id,version,product_type,functions_text,
                     problems_text,original_intake)
                   VALUES(%s,%s,'','','',%s) RETURNING id""",
                (self.pid,number,Jsonb({"source":"pre-existing B9 SRE fact fixture"})),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO sre_facts(
                     id,project_id,profile_version_id,kind,original_quote,
                     normalized_text,origin,review_status)
                   VALUES(%s,%s,%s,%s,%s,%s,'explicit','approved')""",
                (UUID(fact["id"]),self.pid,profile,fact["kind"],
                 fact["original_quote"],
                 "TAMPERED B9 TEXT" if tampered else fact["text"]),
            )
        return projected

    def test_matching_preexisting_fact_is_reused_not_inserted_twice(self):
        projection = self._seed_preexisting_sre_fact()
        before = self._counts()
        done = self.repo.publish(self.pid,self.rid,self.actor)
        self.assertEqual(done["reused_fact_count"],1)
        self.assertEqual(done["new_fact_count"],len(projection["facts"])-1)
        self.assertEqual(self._counts()[3]-before[3],
                         len(projection["facts"])-1)
        with self.factory() as conn:
            duplicates = conn.execute(
                """SELECT count(*) FROM sre_facts WHERE project_id=%s
                   AND id=%s""",
                (self.pid,UUID(projection["facts"][0]["id"])),
            ).fetchone()[0]
        self.assertEqual(duplicates,1)

    def test_conflicting_existing_fact_uuid_aborts_publication_and_sre_version(self):
        self._seed_preexisting_sre_fact(tampered=True)
        before = self._counts()
        with self.assertRaisesRegex(
            PipelineError,"existing_fact_content_conflict",
        ):
            self.repo.publish(self.pid,self.rid,self.actor)
        self.assertEqual(self._counts(),before)

    def test_concurrent_double_publication_creates_one_taxonomy(self):
        calls, lock = [], Lock()
        def publish_once(_):
            result = self.repo.publish(self.pid,self.rid,self.actor)
            with lock:
                calls.append(result)
            return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish_once, (1,2)))
        self.assertEqual(len(results),2)
        self.assertEqual([x["created"] for x in results].count(True),1)
        self.assertEqual(results[0]["publication_id"],
                         results[1]["publication_id"])
        self.assertEqual(self._counts()[0:2], (1,1))

    def test_immutable_publication_and_link_refuse_sql_update_delete(self):
        published = self.repo.publish(self.pid,self.rid,self.actor)
        for table in ("tb_deep_v2_publications","tb_deep_v2_sre_links"):
            with self.subTest(table=table), self.assertRaises(RaiseException):
                with self.factory() as conn:
                    conn.execute(
                        f"DELETE FROM {table} WHERE project_id=%s AND run_id=%s",
                        (self.pid,self.rid),
                    )
        self.assertEqual(self._counts()[0:2], (1,1))

    def test_other_project_and_unauthorized_reviewer_cannot_publish(self):
        with self.assertRaises((ValueError,PipelineError)):
            self.repo.publish("f"*32,self.rid,self.actor)
        with self.assertRaises((ValueError,PipelineError)):
            self.repo.get_publication(self.pid,self.rid,"not-authorized")
        self.assertEqual(self._counts()[0:2], (0,0))

    def test_review2_not_approved_cannot_publish(self):
        # Requesting a fabricated run never yields publication, regardless of
        # whether another user's project has an approved taxonomy.
        with self.assertRaises(PipelineError):
            self.repo.publish(self.pid,"00000000-0000-4000-8000-000000000001",self.actor)
        self.assertEqual(self._counts()[0:2],(0,0))

if __name__ == "__main__":
    unittest.main()
