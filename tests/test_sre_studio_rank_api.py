"""M40 interactive localhost rank-all: authenticated, local pinned, no mutations."""
import io
import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from intelligence.relevance import read_api
from intelligence.relevance.http_read_api import LocalSREReadAPI, ROOT
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
RID="11111111-1111-4111-8111-111111111111"
CAT="33333333-3333-4333-8333-333333333333"
SHA="f"*64
RANK_PATH=ROOT+"/projects/"+PID+"/runs/"+RID+"/rankings/5"


class DB:
    autocommit=True
    info=SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE"))
    def __init__(self):self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*a):return False
    @contextmanager
    def transaction(self):yield self
    def execute(self,sql,values=None):
        self.calls.append((sql,values))
        if sql==read_api.SQL_WORKSPACE:
            return SimpleNamespace(fetchone=lambda:(1,))
        if sql=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            return SimpleNamespace(fetchone=lambda:None)
        raise AssertionError("unexpected SQL")


def complete_real_rank():
    return {
      "schema_version":"sre-all-leaf-ranking/v1",
      "run_id":RID,"item_id":5,
      "model_identity_sha256":SHA,"ranking_sha256":SHA,
      "expected_leaf_count":1,"candidate_count":1,"dropped_count":0,
      "ranking_only":True,"paid_calls":0,
      "candidates":[{
        "rank":1,"category_key":CAT,"rank_score":0.8123456789,
        "classification_state":"not_evaluated","is_candidate":True,
      }],
    }


class RankAPITests(unittest.TestCase):
    def setUp(self):
        self.db=DB()
        self.auth=[]
        self.mocks=[]
        self.rank_called=[]
        def authorize(pid,actor,permission):
            self.auth.append((pid,actor,permission))
            return pid==PID and actor=="trusted:local" and (
                permission=="read_sre_api_local_rank")
        self.authorization=authorize
        self.model=lambda pid,actor:{"model_directory":"/trusted/onnx",
            "model_pins":{"trusted_manifest":"verified_by_host"}}
    def app(self,**kwargs):
        return LocalSREReadAPI(connect=lambda:self.db,
           actor_resolver=lambda env:env.get("HOST_VERIFIED_ACTOR"),
           actor_authorizer=self.authorization,enabled=True,**kwargs)
    def send(self,app,*,addr="127.0.0.1",actor="trusted:local",method="GET"):
        replies=[]
        out=b"".join(app({
           "PATH_INFO":RANK_PATH,"REMOTE_ADDR":addr,"HTTP_HOST":"localhost:8765",
           "REQUEST_METHOD":method,"HOST_VERIFIED_ACTOR":actor,
           "QUERY_STRING":"",
        },lambda status,headers:replies.append((status,dict(headers)))))
        return replies[0][0],json.loads(out) if out else None

    def test_model_absent_fail_closed_before_db(self):
        s,p=self.send(self.app())
        self.assertEqual(s,"403 Forbidden")
        self.assertEqual(p,{"error":"access_denied"})
        self.assertEqual(self.db.calls,[])
    def test_rank_quota_denied_before_db_or_onnx(self):
        rank=self.app(model_resolver=self.model,
                      rank_rate_limiter=lambda *x:False)
        status,payload=self.send(rank)
        self.assertEqual(status,"429 Too Many Requests")
        self.assertEqual(payload["error"],"rate_limited")
        self.assertEqual(self.db.calls,[])
    def test_rank_cannot_be_unauthorized_remote_or_called_as_post(self):
        rank=self.app(model_resolver=self.model,
                      rank_rate_limiter=lambda *x:True)
        self.assertEqual(self.send(rank,addr="10.0.0.15")[0],"403 Forbidden")
        self.assertEqual(self.send(rank,actor="forged:human")[0],"403 Forbidden")
        self.assertEqual(self.send(rank,method="POST")[0],"405 Method Not Allowed")
        self.assertEqual(self.db.calls,[])
    def test_local_rank_get_only_after_trusted_config_and_read_only_transaction(self):
        rank=self.app(model_resolver=self.model,
                      rank_rate_limiter=lambda *x:True)
        expected={
           "schema_version":"sre-read-api/v1",
           "rows":[{"rank":1,"category_key":CAT,
                    "rank_score":0.81234568,
                    "classification_state":"not_evaluated"}]}
        with patch.object(read_api,"read_authorized_local_rank",
                          return_value=expected) as mock:
            status,payload=self.send(rank)
        self.assertEqual(status,"200 OK")
        self.assertEqual(payload,expected)
        mock.assert_called_once_with(
            self.db,PID,RID,5,model_directory="/trusted/onnx",
            model_pins={"trusted_manifest":"verified_by_host"})
        self.assertEqual(self.db.calls[0][0],
                         "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.assertTrue(all(sql.startswith(("SELECT ","SET "))
                            for sql,_ in self.db.calls))
        self.assertIn((PID,"trusted:local","read_sre_api_local_rank"),self.auth)
    def test_only_original_onnx_rank_all_enforces_complete_coverage(self):
        with patch("intelligence.relevance.live_leaf_ranking.rank_live_run_item",
                   return_value=complete_real_rank()) as actual:
            result=read_api.read_authorized_local_rank(self.db,PID,RID,5,
                     model_directory="/trusted/onnx",model_pins={})
        self.assertEqual(result["rows"][0]["rank_score"],0.81234568)
        self.assertTrue(result["local_cpu_only"])
        self.assertEqual(result["paid_post_calls"],0)
        actual.assert_called_once()
        for change in (
            {"candidate_count":0},
            {"dropped_count":1},
            {"paid_calls":1},
            {"expected_leaf_count":2},
            {"candidates":[]},
        ):
            modified={**complete_real_rank(),**change}
            with patch("intelligence.relevance.live_leaf_ranking.rank_live_run_item",
                       return_value=modified):
                with self.assertRaises(SREContractError):
                    read_api.read_authorized_local_rank(self.db,PID,RID,5,
                        model_directory="/trusted/onnx",model_pins={})
    def test_no_model_directory_or_client_selected_provider(self):
        for location,pins in (("",{}),("/trusted/onnx","fake")):
            with self.assertRaises(SREContractError):
                read_api.read_authorized_local_rank(self.db,PID,RID,5,
                    model_directory=location,model_pins=pins)


if __name__=="__main__":
    unittest.main()
