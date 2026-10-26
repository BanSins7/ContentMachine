"""M07 zero-network unit QA for authoritative D1 project/video lineage."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from intelligence.relevance.ports.d1_provenance import (
    D1_PROVENANCE_VERSION, validate_d1_provenance, read_d1_provenance,
    load_d1_provenance, load_d1_provenance_many,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint
from test_sre_contracts_v1 import PID, content


def fixture():
    c = content()
    c["provenance"] = {
        "discovery_id": 1, "search_query_id": 23, "source_request_id": 4
    }
    # Selected source-specific video, query and request all agree with S3.
    r = (
        55, PID, 29, "collector_tiktok", 10,
        deepcopy(c["provenance"]), 12,
        1, 29, "collector_tiktok", 23, 4, 8,
        8, "collector_tiktok", 23,
        "collector_tiktok",
        29, "collector_tiktok", 10,
        "tiktok", "canonical_video_29",
    )
    return c, r


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return nullcontext()

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("SET TRANSACTION"):
            return self
        if sql.strip().startswith("SELECT"):
            return self
        raise AssertionError("unexpected mutating SQL")

    def fetchall(self):
        return self.rows


class M07D1ProvenanceUnit(TestCase):
    def test_selected_d1_source_request_query_observation_and_hash(self):
        authorized, row = fixture()
        conn = FakeDB([row])
        with patch("intelligence.relevance.intake.read_content", return_value=authorized) as reader:
            item = load_d1_provenance(PID, 55, connect=lambda:conn)
        self.assertEqual(item["schema_version"], D1_PROVENANCE_VERSION)
        self.assertEqual(item["project_id"], PID)
        self.assertEqual(item["video_id"], 29)
        self.assertEqual(item["discovery_id"], 1)
        self.assertEqual(item["source_id"], "collector_tiktok")
        self.assertEqual(item["source_policy_version_id"], 10)
        self.assertEqual(item["source_request_id"], 4)
        self.assertEqual(item["search_run_id"], 8)
        self.assertEqual(item["search_query_id"], 23)
        self.assertEqual(item["metadata_observation_id"], 12)
        self.assertEqual(item["platform"], "tiktok")
        self.assertEqual(item["platform_video_id"], "canonical_video_29")
        self.assertEqual(item["content_version_id"], 55)
        self.assertEqual(item["content_sha256"], authorized["content_sha256"])
        self.assertEqual(item, validate_d1_provenance(item))
        self.assertEqual(item["provenance_sha256"],fingerprint({
            k:v for k,v in item.items() if k!="provenance_sha256"
        }))
        reader.assert_called_once_with(conn, PID, 55, external_llm=False)
        self.assertEqual(len(conn.calls), 2)
        self.assertIn("REPEATABLE READ READ ONLY", conn.calls[0][0])
        self.assertEqual(conn.calls[1][1], (PID,55))
        self.assertIn("sre_project_discoveries", conn.calls[1][0])
        self.assertIn("project_registry", conn.calls[1][0])
        self.assertIn("JOIN videos", conn.calls[1][0])
        self.assertIn("LEFT JOIN source_requests", conn.calls[1][0])
        self.assertNotIn("videos.title", conn.calls[1][0])

    def test_direct_d1_without_collector_request_query_or_observation(self):
        content_row, row = fixture()
        content_row["provenance"] = {
            "discovery_id":1,"search_query_id":None,"source_request_id":None
        }
        values=list(row)
        for i in (10,11,12,13,14,15,16,17,18,19):
            values[i]=None
        values[6]=None
        values[5]=deepcopy(content_row["provenance"])
        # This is a direct/manual D1 source, not a Collector producer.
        content_row["source_id"]="d1_manual"
        values[3]="d1_manual"
        values[9]="d1_manual"
        with patch("intelligence.relevance.intake.read_content",return_value=content_row):
            result = read_d1_provenance(FakeDB([tuple(values)]),PID,55)
        self.assertIsNone(result["source_request_id"])
        self.assertIsNone(result["search_run_id"])
        self.assertIsNone(result["search_query_id"])
        self.assertIsNone(result["metadata_observation_id"])
        self.assertEqual(result["discovery_id"],1)

    def test_invalid_project_content_and_collector_query_uuid_denied(self):
        authorized, row = fixture()
        invalid=[(1, PID),("55",PID),(0,PID),(False,PID),(2**63,PID),(55,"bad")]
        for cid,pid in invalid:
            with self.subTest(cid=cid,pid=pid), self.assertRaises(SREContractError):
                read_d1_provenance(FakeDB([row]), pid, cid)
        with patch("intelligence.relevance.intake.read_content",return_value=authorized):
            original=read_d1_provenance(FakeDB([row]),PID,55)
        for field,value in (
            ("search_query_id",str(uuid4())),
            ("source_request_id",str(uuid4())),
            ("search_run_id",False),
            ("search_run_id",2**63),
            ("metadata_observation_id",True),
            ("video_id","29"),
            ("content_version_id",0),
            ("source_policy_version_id",23.0),
            ("platform","unknown"),
            ("project_id","a"),
            ("schema_version","sre-d1-provenance/v2"),
            ("platform_video_id",""),
        ):
            with self.subTest(field=field),self.assertRaises(SREContractError):
                mutated=deepcopy(original)
                mutated[field]=value
                mutated["provenance_sha256"]=fingerprint({
                    k:v for k,v in mutated.items() if k!="provenance_sha256"})
                validate_d1_provenance(mutated)

    def test_mismatch_existing_d1_lineage_denied_even_when_content_is_authorized(self):
        original, row = fixture()
        def changed(i,value):
            r=list(row);r[i]=value;return tuple(r)
        cases=[
            [changed(1,"b"*32)],          # stored project
            [changed(2,30)],              # SRE video
            [changed(8,30)],              # discovered video
            [changed(3,"foreign_source")],
            [changed(9,"foreign_source")],
            [changed(4,11)],              # policy
            [changed(7,2)],               # selected discovery
            [changed(10,44)],             # D1 query changed
            [changed(11,5)],              # D1 request changed
            [changed(13,100)],            # source request run mismatch
            [changed(14,"foreign_source")],
            [changed(15,3)],              # request query mismatch
            [changed(16,"foreign_source")], # query source mismatch
            [changed(17,33)],             # wrong observation video
            [changed(18,"foreign_source")],
            [changed(19,11)],             # observation policy mismatch
            [changed(5,{"discovery_id":999,"search_query_id":23,
                        "source_request_id":4})],
            [],                           # detached or revoked membership
            [row,row],                    # ambiguous D1 join
        ]
        with patch("intelligence.relevance.intake.read_content",return_value=original):
            for case, rows in enumerate(cases):
                with self.subTest(case=case), self.assertRaises(SREContractError):
                    read_d1_provenance(FakeDB(rows),PID,55)

    def test_duplicate_content_ids_are_deduped_but_distinct_versions_remain(self):
        authorized, row = fixture()
        conn=FakeDB([row])
        with patch("intelligence.relevance.intake.read_content",return_value=authorized):
            result=load_d1_provenance_many(PID,[55,55,55],connect=lambda:conn)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["content_version_id"],55)
        self.assertEqual(len(conn.calls),2)
        self.assertEqual(conn.calls[0][0],"SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with self.assertRaises(SREContractError):
            load_d1_provenance_many(PID,[55,True])
        with self.assertRaises(SREContractError):
            load_d1_provenance_many(PID,[55,2**63])
        with self.assertRaises(SREContractError):
            load_d1_provenance_many(PID,[55,str(uuid4())])
        with self.assertRaises(SREContractError):
            load_d1_provenance_many(PID,[])
        with self.assertRaises(SREContractError):
            load_d1_provenance_many(PID,list(range(1,1002)))

    def test_zero_optional_imports_on_clean_module_import(self):
        import subprocess,sys
        cmd=(
            "import sys; import intelligence.relevance.ports.d1_provenance;"
            "assert 'psycopg' not in sys.modules;"
            "assert 'intelligence.collector' not in sys.modules;"
            "assert 'intelligence.qualified_data' not in sys.modules;"
            "assert 'openrouter' not in sys.modules;"
        )
        proc=subprocess.run([sys.executable,"-c",cmd],capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)


if __name__=="__main__":
    import unittest
    unittest.main()
