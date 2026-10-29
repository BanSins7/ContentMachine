"""M38 original PG read-model deny/metadata pagination contracts."""
import unittest
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from intelligence.relevance import read_api as api
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
RID="11111111-1111-4111-8111-111111111111"
PUB="22222222-2222-4222-8222-222222222222"
K1="33333333-3333-4333-8333-333333333333"
K2="44444444-4444-4444-8444-444444444444"
SHA="f"*64
NOW=datetime(2026,10,28,tzinfo=timezone.utc)


class Cursor:
    def __init__(self,row=None,rows=None):
        self.row=row
        self.rows=[] if rows is None else rows
    def fetchone(self):return self.row
    def fetchall(self):return self.rows


class DB:
    def __init__(self):
        self.writes=[]
        self.answers={}
    def execute(self,statement,args=None):
        self.writes.append((statement,args))
        try:response=self.answers[statement]
        except KeyError:raise AssertionError("unexpected SQL "+statement[:120])
        return Cursor(**response)


class M38ReadModelTests(unittest.TestCase):
    def test_health_no_secret_and_never_proves_qa(self):
        db=DB()
        db.answers[api.SQL_WORKSPACE]={"row":(1,)}
        db.answers[api.SQL_SCHEMA]={"row":(True,True,True,True,True)}
        result=api.read_schema_health(db,PID)
        self.assertEqual(result["status"],"schema_available")
        self.assertFalse(result["live_execution_confirmed"])
        self.assertFalse(result["actual_database_migration_hashes_verified"])
        self.assertFalse(result["paid_dispatch_allowed"])
        self.assertTrue(all(sql.startswith("SELECT ") for sql,_ in db.writes))

    def test_run_cursor_page_is_stable_and_project_scoped(self):
        db=DB();rid2="22222222-2222-4222-8222-222222222222"
        db.answers[api.SQL_RUNS]={"rows":[
            (RID,"completed",False,2,2,0,NOW,NOW),
            (rid2,"running",False,2,0,0,NOW,None),
        ]}
        page=api.list_live_runs(db,PID,limit=1)
        self.assertEqual(page["next_cursor"],RID)
        self.assertEqual(page["rows"][0]["state"],"completed")
        self.assertTrue(page["has_more"])
        self.assertEqual(db.writes[0][1],(PID,None,None,2))
        with self.assertRaises(SREContractError):
            api.list_live_runs(db,PID,limit=101)

    def test_content_preview_only_released_metadata_and_current_d1(self):
        content={
            "project_id":PID,"content_version_id":55,"video_id":29,
            "source_id":"dtk_tiktok","source_policy_version_id":11,
            "content_sha256":SHA,"completeness":"full",
            "data":{"title":"T"*300,"description":"D"*2000,
                    "transcript":"PRIVATE TRANSCRIPT",
                    "scenes":["DO NOT EXPOSE"]},
        }
        with patch.object(api,"read_live_content_gate",return_value={"content":content}):
            result=api.read_content_preview(DB(),PID,55)
        self.assertEqual(len(result["title_preview"]),160)
        self.assertEqual(len(result["description_preview"]),320)
        self.assertNotIn("transcript",result)
        self.assertNotIn("data",result)
        self.assertNotIn("PRIVATE",str(result))
        self.assertTrue(result["rights_rechecked"])
        with patch.object(api,"read_live_content_gate",
                          side_effect=SREContractError("rights_denied","source","expired")):
            with self.assertRaises(SREContractError):
                api.read_content_preview(DB(),PID,55)

    def test_taxonomy_only_authorized_approved_c9_and_bounded_nodes(self):
        db=DB()
        bundle={
            "project_id":PID,"publication_id":PUB,"sre_taxonomy_version_id":19,
            "taxonomy_sha256":SHA,
            "hypothesis_keys":[K1,K2],
            "node_projections":[
                {"source_node_id":"H1","category_key":K1,
                 "kind":"hypothesis","parent_category_key":None},
                {"source_node_id":"H2","category_key":K2,
                 "kind":"hypothesis","parent_category_key":None},
            ],"approved_snapshot":{"original_quote":"private source"},
        }
        with patch.object(api,"read_authorized_taxonomy",return_value={}):
            with patch.object(api,"build_c9_bundle",return_value=bundle):
                page=api.read_taxonomy_preview(db,PID,PUB,limit=1)
        self.assertEqual(page["rows"][0]["category_key"],K1)
        self.assertEqual(page["next_cursor"],K1)
        self.assertNotIn("private",str(page))
        self.assertTrue(page["human_approved_c9"])
        with patch.object(api,"read_authorized_taxonomy",
                          side_effect=SREContractError("rights_denied","publication","unapproved")):
            with self.assertRaises(SREContractError):
                api.read_taxonomy_preview(db,PID,PUB)

    def test_results_latest_human_overlay_and_recheck_rights(self):
        db=DB()
        db.answers[api.SQL_RESULTS]={"rows":[
            (4,K1,"match",RID,PUB,"no_match",RID,2,29,55,SHA),
            (5,K2,"uncertain",RID,PUB,None,None,None,30,56,SHA),
        ]}
        with patch.object(api,"_verify_current_d1") as guard:
            page=api.read_verified_results(db,PID,RID,limit=1)
        self.assertEqual(page["rows"][0]["original_model_decision"],"match")
        self.assertEqual(page["rows"][0]["effective_decision"],"no_match")
        self.assertEqual(page["next_cursor"],"4."+K1)
        self.assertEqual(guard.call_count,2) # includes lookahead
        self.assertEqual(db.writes[0][1],(PID,RID,None,None,None,2))
        with patch.object(api,"_verify_current_d1",
                          side_effect=SREContractError("rights_denied","content","revoked")):
            with self.assertRaises(SREContractError):
                api.read_verified_results(db,PID,RID,limit=1)

    def test_queue_selects_only_unreviewed_before_limit(self):
        db=DB()
        db.answers[api.SQL_REVIEW_QUEUE]={"rows":[
            (5,K2,"uncertain",RID,PUB,None,None,None,30,56,SHA),
        ]}
        with patch.object(api,"_verify_current_d1") as guard:
            page=api.read_review_queue(db,PID,RID,limit=1)
        self.assertEqual(len(page["rows"]),1)
        self.assertFalse(page["rows"][0]["human_reviewed"])
        self.assertEqual(guard.call_count,1)
        self.assertIn("h.review_id IS NULL",api.SQL_REVIEW_QUEUE)
        self.assertEqual(db.writes[0][0],api.SQL_REVIEW_QUEUE)

    def test_quote_omits_raw_quote_and_denies_cancelled_revoked(self):
        db=DB()
        db.answers[api.SQL_RUN]={"row":(
            RID,PUB,19,SHA,"queued",False,1,0,0,NOW,None,
        )}
        db.answers[api.SQL_QUOTES]={"rows":[
            (51,3,SHA,SHA,Decimal("0.500000000"),NOW,NOW,29,55,SHA),
        ]}
        with patch.object(api,"read_authorized_taxonomy",return_value={}):
            with patch.object(api,"_verify_current_d1"):
                data=api.read_quote_preview(db,PID,RID)
        self.assertEqual(data["rows"][0]["conditional_max_total_usd"],
                         "0.500000000")
        self.assertNotIn("quote",data["rows"][0])
        self.assertNotIn("consent_id",data["rows"][0])
        self.assertTrue(data["rows"][0]["not_spending_consent"])
        self.assertIn("consent.revoked_at IS NOT NULL",api.SQL_QUOTES)
        self.assertIn("q.expires_at>clock_timestamp()",api.SQL_QUOTES)
        db.answers[api.SQL_RUN]={"row":(
            RID,PUB,19,SHA,"completed",True,1,1,0,NOW,NOW,
        )}
        with self.assertRaises(SREContractError):
            api.read_quote_preview(db,PID,RID)
        self.assertEqual(len(db.writes),3)

    def test_human_review_history_rechecks_each_current_source(self):
        db=DB();db.answers[api.SQL_REVIEWS]={"rows":[
            (RID,3,K1,2,"no_match","match","correct","incorrect_positive",
             NOW,29,55,SHA)
        ]}
        with patch.object(api,"_verify_current_d1") as guard:
            page=api.read_review_history(db,PID,RID)
        self.assertEqual(page["rows"][0]["human_decision"],"no_match")
        self.assertNotIn("reviewer_actor",page["rows"][0])
        self.assertEqual(guard.call_count,1)

    def test_invalid_cross_domain_cursor_rejected(self):
        for after in ("33333333-3333-4333-8333-333333333333",
                      "0."+K1,"-1."+K1,"foo."+K1,"3.invalid"):
            with self.assertRaises(SREContractError):
                api._pair_cursor(after)
        self.assertEqual(api._pair_cursor("9."+K2),(9,K2))


if __name__=="__main__":
    unittest.main()
