"""M18 pure cache contract tests; synthetic vectors are TEST MATH ONLY.

Production always demands trusted SHA-pinned M16 files and actual D1/C9 DB
authorization. These fake-DB tests do not count as genuine ONNX acceptance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from copy import deepcopy
from pathlib import Path
import unittest

from intelligence.relevance.local_embedding_cache import (
    CACHE_VERSION, MAX_PROJECT_ENTRIES, MAX_TTL_SECONDS, MAX_VECTORS,
    _cache_key, _vectors_from_row, _materialize_vectors,
    rank_live_run_item_cached, purge_local_embedding_cache,
)
from intelligence.relevance.local_embedding import validate_local_model_pins
from intelligence.relevance.live_leaf_ranking import _rank_inputs
from intelligence.relevance.ports.approved_c9 import load_approved_c9
from intelligence.relevance.v1_contracts import fingerprint,SREContractError
from test_sre_taxonomy_adapter import FakeConnection,db_fixtures,PID,PUB

RID="11111111-1111-4111-8111-111111111111"
PIN={
    "schema_version":"sre-local-embedding/v1","backend":"onnxruntime-cpu",
    "model_name":"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "revision":"1"*40,"model_sha256":"a"*64,"tokenizer_sha256":"b"*64,
    "output_name":"last_hidden_state","pooling":"masked_mean","dimensions":3,
    "max_tokens":128,"pad_token":"[PAD]",
}


class QueryResult:
    def __init__(self,value=None,rows=0):
        self.value=value
        self.rowcount=rows
    def fetchone(self):return self.value


class FakeCacheDB:
    """Minimal deterministic SQL contract executor, no real model/auth claim."""
    def __init__(self):
        self.calls=[]
        self.entries={}
        self.project=PID
        self.policy_active=True
        self.policy=9
        self.misses=0
        self.bound_entries=0

    def transaction(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False

    def execute(self,sql,params):
        self.calls.append((sql,params))
        if "FROM sre_workspaces WHERE project_id=%s FOR UPDATE" in sql:
            return QueryResult((1,) if params[0]==self.project else None)
        if "SELECT i.source_policy_version_id" in sql:
            return QueryResult((self.policy,) if params[0]==PID else None)
        if "SELECT p.id FROM source_policy_versions" in sql:
            return QueryResult((self.policy,) if self.policy_active and params[0]==self.policy else None)
        if "SELECT vectors,vectors_sha256" in sql:
            return QueryResult(self.entries.get(tuple(params)))
        if "DELETE FROM sre_local_embedding_cache" in sql:
            if "expires_at<=clock_timestamp()" in sql:
                # no expired entries in deterministic fake fixture
                return QueryResult(None,0)
            if "OFFSET %s" in sql:
                self.bound_entries+=1
                return QueryResult(None,0)
            matches=[key for key in self.entries
                     if key[0]==params[0] and
                     (len(params)<2 or key[7]==params[1])]
            for key in matches:del self.entries[key]
            return QueryResult(None,len(matches))
        if "INSERT INTO sre_local_embedding_cache" in sql:
            key=tuple(params[:12])
            vector_sha,count,dim,raw=params[12:16]
            self.entries.setdefault(
                key,(__import__("json").loads(raw),vector_sha,count,dim,
                     datetime.now(timezone.utc)+timedelta(hours=1))
            )
            self.misses+=1
            return QueryResult(None,1)
        raise AssertionError("Unexpected SQL "+sql)


class CacheM18Unit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c9=load_approved_c9(PID,PUB,connect=lambda:FakeConnection(db_fixtures()))
        cls.content={
            "schema_version":"sre-content-input/v1",
            "project_id":PID,"video_id":101,"content_version_id":22,
            "source_id":"unit_source","source_policy_version_id":9,
            "content_sha256":"d"*64,"completeness":"metadata_only",
            "media_asset_id":None,"expires_at":None,
            "rights":{"can_store_metadata":True,"can_semantic_classify":True,
                      "can_external_llm":False,"can_analyze_media":False},
            "data":{"title":"Recorded title","description":"Recorded description",
                    "hashtags":["#fitness"],"transcript":None,"scenes":None},
            "provenance":{"discovery_id":8,"search_query_id":None,
                          "source_request_id":None},
        }
        cls.gate={"version_pin_sha256":"a"*64}
        cls.identity=validate_local_model_pins(PIN).identity_sha256()
        cls.features,cls.texts=_rank_inputs(cls.c9,cls.content)

    def _patches(self,*,fake_vectors=True):
        import contextlib
        stack=contextlib.ExitStack()
        stack.enter_context(patch(
            "intelligence.relevance.local_embedding_cache._authorize_live_rank_inputs",
            return_value=(self.c9,deepcopy(self.content),self.gate,RID,55),
        ))
        stack.enter_context(patch(
            "intelligence.relevance.local_embedding_cache.verify_local_model_files",
            return_value=(validate_local_model_pins(PIN),Path("/trusted/test-mock-model")),
        ))
        if fake_vectors:
            vectors=[(1.0,0.0,0.0) for _ in self.texts]
            stack.enter_context(patch(
                "intelligence.relevance.local_embedding_cache.load_local_embedding_backend",
                return_value=type("UnitNotARealBackend",(),{
                    "model_identity_sha256":self.identity,
                })(),
            ))
            stack.enter_context(patch(
                "intelligence.relevance.local_embedding_cache._embed_one_or_more",
                return_value=vectors,
            ))
        return stack

    def _run(self,db,*,ttl=3600):
        return rank_live_run_item_cached(
            db,PID,RID,55,model_directory="/trusted/test-mock-model",
            model_pins=PIN,ttl_seconds=ttl,
        )

    def test_cold_then_hot_same_pinned_results_and_no_duplicate_inference(self):
        db=FakeCacheDB()
        with self._patches():
            first=self._run(db)
            self.assertFalse(first["cache_hit"])
            self.assertEqual(first["cache_version"],CACHE_VERSION)
            self.assertEqual(first["candidate_count"],len(self.c9["hypothesis_keys"]))
            self.assertEqual(first["dropped_count"],0)
            self.assertEqual(first["paid_calls"],0)
            self.assertFalse(first["dispatch_allowed"])
            self.assertEqual(db.misses,1)
            self.assertEqual(len(db.entries),1)
            with patch(
                "intelligence.relevance.local_embedding_cache.load_local_embedding_backend",
                side_effect=AssertionError("HOT cache may not run inference"),
            ):
                second=self._run(db)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["candidates"],second["candidates"])
        self.assertEqual(first["cache_key_sha256"],second["cache_key_sha256"])
        self.assertNotEqual(first["ranking_sha256"],second["ranking_sha256"]) # cache_hit provenance differs
        self.assertEqual(db.misses,1)
        sql=[stmt for stmt,_ in db.calls]
        self.assertLess(next(i for i,s in enumerate(sql) if "FROM sre_workspaces" in s),
                        next(i for i,s in enumerate(sql) if "FROM source_policy_versions" in s))
        self.assertTrue(any("ON CONFLICT DO NOTHING" in x for x in sql))

    def test_identity_key_includes_all_scopes_and_c9_context_policy(self):
        key=_cache_key(self.c9,self.content,self.identity,self.texts)
        self.assertEqual(len(key),12)
        self.assertEqual(key[0],PID)
        self.assertEqual(key[1],PUB)
        other=deepcopy(self.content);other["content_version_id"]=99
        self.assertNotEqual(_cache_key(self.c9,other,self.identity,self.texts),key)
        other=deepcopy(self.content);other["source_policy_version_id"]=10
        self.assertNotEqual(_cache_key(self.c9,other,self.identity,self.texts),key)
        other=deepcopy(self.content);other["content_sha256"]="f"*64
        self.assertNotEqual(_cache_key(self.c9,other,self.identity,self.texts),key)
        self.assertNotEqual(_cache_key(self.c9,self.content,"f"*64,self.texts),key)
        self.assertNotEqual(_cache_key(self.c9,self.content,self.identity,self.texts+["changed"]),key)
        changed=deepcopy(self.c9);changed["taxonomy_sha256"]="e"*64
        self.assertNotEqual(_cache_key(changed,self.content,self.identity,self.texts),key)
        changed=deepcopy(self.c9);changed["contexts"][0]["context_sha256"]="e"*64
        self.assertNotEqual(_cache_key(changed,self.content,self.identity,self.texts),key)
        other=deepcopy(self.content);other["project_id"]="b"*32
        with self.assertRaises(SREContractError):
            _cache_key(self.c9,other,self.identity,self.texts)

    def test_revoked_rights_and_missing_model_fail_before_cache_read(self):
        db=FakeCacheDB()
        with self._patches():
            self._run(db)
        db.policy_active=False
        with patch(
            "intelligence.relevance.local_embedding_cache._authorize_live_rank_inputs",
            side_effect=AssertionError("rights check should deny first"),
        ):
            with self.assertRaises(SREContractError) as e:
                self._run(db)
        self.assertEqual(e.exception.code,"rights_denied")
        self.assertEqual(len(db.entries),1)  # physically present, NEVER served
        db.policy_active=True
        with patch(
            "intelligence.relevance.local_embedding_cache._authorize_live_rank_inputs",
            side_effect=AssertionError("D1 gate must reject first"),
        ):
            with self.assertRaises(AssertionError):
                self._run(db)
        with patch(
            "intelligence.relevance.local_embedding_cache._authorize_live_rank_inputs",
            return_value=(self.c9,self.content,self.gate,RID,55),
        ),patch(
            "intelligence.relevance.local_embedding_cache.verify_local_model_files",
            side_effect=SREContractError("local_model_blocked","files","missing"),
        ):
            with self.assertRaises(SREContractError) as e:
                self._run(db)
        self.assertEqual(e.exception.code,"local_model_blocked")

    def test_delete_revoked_or_exact_content_without_loading_d1(self):
        db=FakeCacheDB()
        with self._patches():
            self._run(db)
        out=purge_local_embedding_cache(db,PID,content_version_id=22)
        self.assertEqual(out["deleted_entries"],1)
        self.assertFalse(db.entries)
        with self.assertRaises(SREContractError):
            purge_local_embedding_cache(db,PID)
        with self.assertRaises(SREContractError):
            purge_local_embedding_cache(db,"b"*32,source_policy_version_id=9)
        with self.assertRaises(SREContractError):
            purge_local_embedding_cache(db,PID,expired_only="true")
        out=purge_local_embedding_cache(db,PID,source_policy_version_id=9)
        self.assertEqual(out["deleted_entries"],0)

    def test_corrupt_cache_sha_shape_and_nan_are_rejected_not_reused(self):
        db=FakeCacheDB()
        with self._patches():self._run(db)
        key=next(iter(db.entries))
        row=db.entries[key]
        bad=[
            (row[0], "f"*64,row[2],row[3],row[4]),
            (row[0][:2],row[1],row[2],row[3],row[4]),
            ([["NaN",0,0]]*row[2],row[1],row[2],row[3],row[4]),
            ([[2,0,0]]*row[2],row[1],row[2],row[3],row[4]),
        ]
        for payload in bad:
            db.entries[key]=payload
            with self._patches(fake_vectors=False):
                with self.assertRaises(SREContractError):
                    self._run(db)
        db.entries[key]=row

    def test_ttl_size_limits_and_stored_vector_shape(self):
        for ttl in (True,0,-1,86401,1.2,"3600"):
            with self.subTest(ttl=ttl),self.assertRaises(SREContractError):
                self._run(FakeCacheDB(),ttl=ttl)
        normalized,payload,sha=_materialize_vectors(
            [(3.0,4.0),(0.0,5.0)],dimensions=2,expected_count=2,
        )
        self.assertEqual(_vectors_from_row(
            (normalized,sha,2,2,None),2,2),
            tuple(tuple(v) for v in normalized),
        )
        with self.assertRaises(SREContractError):
            _materialize_vectors([[1.0,0.0]]*(MAX_VECTORS+1),
                                 dimensions=2,expected_count=MAX_VECTORS+1)
        self.assertEqual(MAX_PROJECT_ENTRIES,1000)
        self.assertEqual(MAX_TTL_SECONDS,86400)


if __name__=="__main__":
    unittest.main()
