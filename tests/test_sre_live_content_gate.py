"""M08: verified live D1/S3 content gates, no provider, no paid API.

The synthetic proof fixtures test only coherence and denial semantics; real
project/source authority is exercised by disposable PostgreSQL17 E2E.
"""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from intelligence.relevance.live_content_gate import (
    GATE_VERSION, validate_media_fidelity, verify_pinned_content,
    read_live_content_gate, load_live_content_gate,
)
from intelligence.relevance.v1_contracts import (
    SREContractError, fingerprint,
)
from test_sre_contracts_v1 import PID, content


def fixtures():
    snap = content()
    snap["content_sha256"] = fingerprint({
        "title": snap["data"]["title"],
        "description": snap["data"]["description"],
        "hashtags": snap["data"]["hashtags"],
    })
    proof = {
        "schema_version": "sre-d1-provenance/v1",
        "project_id":PID,"video_id":snap["video_id"],
        "platform":"youtube","platform_video_id":"fixture29",
        "discovery_id":snap["provenance"]["discovery_id"],
        "source_id":snap["source_id"],
        "source_policy_version_id":snap["source_policy_version_id"],
        "source_request_id":snap["provenance"]["source_request_id"],
        "search_run_id":None,
        "search_query_id":snap["provenance"]["search_query_id"],
        "metadata_observation_id":None,
        "content_version_id":snap["content_version_id"],
        "content_sha256":snap["content_sha256"],
        "completeness":snap["completeness"],
        "expires_at":snap["expires_at"],
    }
    proof["provenance_sha256"] = fingerprint(proof)
    return snap, proof


class FakeDB:
    def __init__(self):
        self.sql=[]
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def transaction(self): return self
    def execute(self,sql,*a):
        self.sql.append(sql)
        return self


class LiveGateUnit(unittest.TestCase):
    def test_metadata_only_is_never_media_evidence(self):
        snap,_=fixtures()
        self.assertEqual(validate_media_fidelity(snap)["completeness"],"metadata_only")
        self.assertIsNone(validate_media_fidelity(snap)["data"]["transcript"])
        for mutation in (
            lambda x: x.update(media_asset_id=99),
            lambda x: x["data"].update(transcript=[
                {"text":"invented","start_ms":0,"end_ms":1000}]),
        ):
            item=copy.deepcopy(snap)
            mutation(item)
            with self.assertRaises(SREContractError):
                validate_media_fidelity(item)

    def test_partial_full_requires_actual_media_and_segments(self):
        snap,_=fixtures()
        for completeness in ("partial","full"):
            x=copy.deepcopy(snap)
            x["completeness"]=completeness
            x["rights"]["can_analyze_media"]=True
            with self.assertRaises(SREContractError):
                validate_media_fidelity(x)
            x["media_asset_id"]=11
            with self.assertRaises(SREContractError):
                validate_media_fidelity(x)
            x["data"]["scenes"]=[{"text":"scene one","start_ms":0,"end_ms":500}]
            self.assertEqual(validate_media_fidelity(x)["completeness"],completeness)
            x["rights"]["can_analyze_media"]=False
            with self.assertRaises(SREContractError):
                validate_media_fidelity(x)

    def test_exact_pins_and_source_provenance_mapping(self):
        snap,proof=fixtures()
        pins={
            "video_id":29,"content_version_id":55,
            "content_sha256":snap["content_sha256"],
            "source_id":snap["source_id"],
            "source_policy_version_id":snap["source_policy_version_id"],
            "discovery_id":proof["discovery_id"],
            "metadata_observation_id":None,
            "completeness":"metadata_only",
            "expires_at":snap["expires_at"],
        }
        self.assertEqual(verify_pinned_content(snap,proof,pins=pins),snap if "media_asset_id" in snap else {
            **snap,"data":{**snap["data"],"transcript":None,"scenes":None},"media_asset_id":None
        })
        for field,value in (
            ("video_id",30),("content_version_id",54),
            ("content_sha256","f"*64),("source_id","other"),
            ("source_policy_version_id",1),("discovery_id",2),
            ("metadata_observation_id",17),("completeness","full"),
            ("expires_at",None),
        ):
            bad=copy.deepcopy(pins);bad[field]=value
            with self.subTest(field=field),self.assertRaises(SREContractError):
                verify_pinned_content(snap,proof,pins=bad)

    def test_self_signed_forged_proof_does_not_match_d1_content(self):
        snap,proof=fixtures()
        for field,value in (
            ("project_id","b"*32),("video_id",30),
            ("source_id","other"),("content_version_id",54),
            ("content_sha256","b"*64),("completeness","full"),
            ("discovery_id",2),("source_request_id",44),
            ("expires_at",None),
        ):
            forged=copy.deepcopy(proof)
            forged[field]=value
            forged["provenance_sha256"]=fingerprint({
                k:v for k,v in forged.items() if k!="provenance_sha256"
            })
            with self.subTest(field=field), self.assertRaises(SREContractError):
                verify_pinned_content(snap,forged)

    def test_outbound_always_checks_external_grant_even_if_flag_false(self):
        snap,proof=fixtures()
        conn=FakeDB()
        with patch("intelligence.relevance.ports.d1_provenance.read_d1_provenance",
                   return_value=proof) as check,patch(
                   "intelligence.relevance.intake.read_content",
                   return_value=snap) as read:
            # A real DB reader would already block external send; secondary
            # guard must still reject synthetic rights false.
            with self.assertRaises(SREContractError) as err:
                read_live_content_gate(conn,PID,55,phase="before_external_send")
        self.assertEqual(err.exception.code,"rights_denied")
        check.assert_called_once_with(conn,PID,55,external_llm=True)
        read.assert_called_once_with(conn,PID,55,external_llm=True)

    def test_readonly_loaded_gate_version_pin_and_no_paid_dispatch(self):
        snap,proof=fixtures()
        conn=FakeDB()
        with patch("intelligence.relevance.ports.d1_provenance.read_d1_provenance",
                   return_value=proof),patch(
                   "intelligence.relevance.intake.read_content",
                   return_value=snap):
            result=load_live_content_gate(
                PID,55,phase="planning",connect=lambda:conn,
                pins={"video_id":29,"content_version_id":55,
                      "content_sha256":snap["content_sha256"]},
            )
        self.assertEqual(result["schema_version"],GATE_VERSION)
        self.assertEqual(result["content_sha256"],snap["content_sha256"])
        self.assertEqual(result["provenance"],proof)
        self.assertEqual(len(result["version_pin_sha256"]),64)
        self.assertEqual(conn.sql,["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"])

    def test_invalid_phase_and_bool_or_malformed_pins_fail_closed(self):
        conn=FakeDB()
        for phase in ("paid",False,0,None,{}, "publish_later"):
            with self.subTest(phase=phase), self.assertRaises(SREContractError):
                read_live_content_gate(conn,PID,55,phase=phase)
        for flag in ("true",1,None):
            with self.subTest(flag=flag), self.assertRaises(SREContractError):
                read_live_content_gate(conn,PID,55,external_llm=flag)
        snap,proof=fixtures()
        for bad in (
            {"video_id":True,"content_version_id":55,"content_sha256":snap["content_sha256"]},
            {"video_id":29,"content_version_id":55},
            {"video_id":29,"content_version_id":55,"content_sha256":"X"*64},
            {"video_id":29,"content_version_id":55,"content_sha256":snap["content_sha256"],
             "secret_admin":True},
        ):
            with self.subTest(bad=bad),self.assertRaises(SREContractError):
                verify_pinned_content(snap,proof,pins=bad)


if __name__=="__main__":
    unittest.main()
