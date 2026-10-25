"""Stage14 A→B9→C cost/consent/one-shot generation and QA (NO paid calls)."""
from __future__ import annotations

import json
import unittest
from copy import deepcopy
from uuid import uuid4

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from test_ke_stage06_request_db import Stage06RequestPG
from test_ke_stage07_execution import Stage07FactPG
from test_ke_stage08_normalization import Stage08PG
from test_ke_stage10_deduplication import Stage10PG
from test_ke_stage11_quality_gate import Stage11PG
from test_ke_stage12_human_review import Stage12PG
from test_ke_stage13_knowledge_snapshot import Stage13PG, pure_fixture
from test_taxonomy_builder_pipeline import SECOND, provider_model

from intelligence.knowledge_extraction.intake import IntakeError
from intelligence.knowledge_extraction.repository import KnowledgeInputRepository
from intelligence.knowledge_extraction.human_review import FactHumanReviewRepository
from intelligence.knowledge_extraction.knowledge_snapshot import KnowledgeSnapshotRepository,build_snapshot,build_c_handoff
from intelligence.taxonomy_builder.knowledge_binding import to_taxonomy_generation_payload
from intelligence.taxonomy_builder.knowledge_execution import (
    KnowledgeCExecutionRepository,_validate_model,_priced_quote
)
from intelligence.taxonomy_builder.catalog import refresh_model_catalog
from intelligence.taxonomy_builder.pipeline_contracts import normalize_capability


def own_product_sender(body):
    """Use only server-scoped and source-pinned own-product text, never video."""
    txt=body["messages"][1]["content"]
    lines=txt.split("FACTS_V3_SOURCE_JSONL_BEGIN\n",1)[1].split("FACTS_V3_SOURCE_JSONL_END\n",1)[0].splitlines()
    matches=[json.loads(line) for line in lines[1:]
             if line.strip() and json.loads(line).get("source_role")=="own_product"]
    if not matches:
        raise AssertionError("Fixture needs A-verified own_product source row")
    row=matches[0]
    quote=row["text"][:min(len(row["text"]),24)]
    claim={"kind":"function","text":"Human verified product capability",
           "source_chunk_id":row.get("chunk_id",row.get("fragment_id")),
           "source_quote":quote,"quote_start":0,"quote_end":len(quote)}
    return {
        "id":"fake-stage14-b3","model":"xiaomi/mimo-v2.6-flash",
        "usage":{"cost":"0.0005","prompt_tokens":350,"completion_tokens":80},
        "choices":[{"finish_reason":"stop",
                   "message":{"content":json.dumps({"facts":[claim]})}}],
    }


def topic_sender(payload):
    """Native RTB taxonomy response with two grounded topics; no external API."""
    content=json.loads(payload["messages"][1]["content"])
    assert content.get("input_mode")=="knowledge_snapshot_v1"
    assert payload["model"]==SECOND
    assert content["approved_fact_ids_by_kind"]["function"]
    fid=content["approved_fact_ids_by_kind"]["function"][0]
    topics=[{
        "name":name,"relation":"direct","origin":"explicit",
        "fact_ids":[fid],
        "definition":"A category based on the reviewed product function",
        "include":"Real demonstration of the function",
        "exclude":"Competitor claims that do not describe this product",
        "positive_examples":["Demo with grounded topic"],
        "negative_examples":["Unrelated product"],
    } for name in ("Feature demonstrations","User onboarding videos")]
    return {
        "id":"synthetic-c-provider-id","model":SECOND,
        "usage":{"cost":"0.0005","prompt_tokens":240,"completion_tokens":500},
        "choices":[{"finish_reason":"stop",
                    "message":{"content":json.dumps({"topics":topics})}}],
    }


class Stage14Pure(unittest.TestCase):
    def test_c_quoted_payload_is_not_extraction_or_approved_spend(self):
        args=pure_fixture()
        snapshot=build_snapshot(**args)
        c=build_c_handoff(snapshot["snapshot"],str(uuid4()),snapshot["snapshot_sha256"])
        adapted=to_taxonomy_generation_payload(c,snapshot["snapshot"])
        entry={
            "model_id":SECOND,"catalog_version_id":123,
            "catalog_sha256":"f"*64,
            "capabilities":normalize_capability(provider_model(SECOND)),
            "pricing":{"prompt":"0.000002","completion":"0.000010"},
        }
        stage,model=_validate_model(entry,4096)
        quote,request_sha,reserved=_priced_quote(
            {"snapshot_id":c["snapshot_id"],
             "snapshot_sha256":snapshot["snapshot_sha256"]},
            {"handoff_id":str(uuid4()),"handoff_sha256":"e"*64},
            adapted,stage,model)
        self.assertEqual(quote["input_mode"],"knowledge_snapshot_v1")
        self.assertEqual(stage["role"],"taxonomy_generation")
        self.assertEqual(stage["prompt_version"],"taxonomy-v2")
        self.assertGreater(reserved,0)
        self.assertFalse(quote["retry_enabled"])
        self.assertEqual(quote["provider_calls"],0)

    def test_c_cannot_select_unsupported_model_or_output_limit(self):
        entry={
            "model_id":SECOND,"catalog_version_id":11,"catalog_sha256":"f"*64,
            "capabilities":normalize_capability(provider_model(SECOND,output=1024)),
            "pricing":{"prompt":"0.000002","completion":"0.000010"},
        }
        for bad in (True,0,500,2000):
            with self.subTest(bad=bad),self.assertRaises(IntakeError):
                _validate_model(entry,bad)


@unittest.skipUnless(DSN,"PostgreSQL17 required for Stage14 C integration")
class Stage14PG(ReadOnlyIntakePG):
    _catalog=Stage06RequestPG._catalog
    _factory=Stage06RequestPG._factory
    _ready=Stage07FactPG._ready
    _completed=Stage08PG._completed
    _complete_b6=Stage10PG._complete_b6
    _completed_b6=Stage11PG._completed_b6
    _grant=Stage12PG._grant
    _vote_all=Stage12PG._vote_all
    _ready_review=Stage13PG._ready_review
    _published=Stage13PG._published

    def setUp(self):
        super().setUp()
        self._catalog(window=100000)
        self.reviewer="stage12-reviewer"

    def _bundle(self, *, large=False):
        if large:
            return Stage06RequestPG._bundle(self,large=True)
        cid=self.approve()
        routes={"source_routes":{
            self.sid:{"role":"own_product","subject_id":self.pid,
                      "confirmed_by":"local_owner"}
        }}
        result=KnowledgeInputRepository(connection_factory=self._factory()).create(
            self.pid,cid,source_routes=routes)
        return result["bundle_id"]

    def _source(self):
        sid=self._published(sender=own_product_sender)
        snap=KnowledgeSnapshotRepository(connection_factory=self._factory())
        pub=snap.publish(self.pid,sid,self.reviewer)
        exported=snap.export_by_id(self.pid,pub["snapshot_id"],self.reviewer)
        self.assertEqual(exported["snapshot_sha256"],pub["snapshot_sha256"])
        with self.assertRaises(IntakeError):
            snap.export_by_id(self.other,pub["snapshot_id"],self.reviewer)
        hand=snap.prepare_c_handoff(self.pid,sid,self.reviewer)
        return sid,pub,hand

    def _catalog_c(self):
        with self.psycopg.connect(DSN) as conn:
            refresh_model_catalog(conn,fetcher=lambda:[provider_model(SECOND)])

    def _repo(self):
        return KnowledgeCExecutionRepository(connection_factory=self._factory())

    def _ready_c(self):
        sid,pub,hand=self._source()
        self._catalog_c()  # historical MiMo catalog no longer needed after B9
        repo=self._repo()
        quote=repo.quote(self.pid,sid,self.reviewer,SECOND,max_output_tokens=4096)
        return repo,sid,pub,hand,quote

    def test_explicit_independent_quote_consent_one_run_and_manual_topic_review(self):
        repo,sid,pub,hand,quote=self._ready_c()
        self.assertTrue(quote["consent_required"])
        with self.psycopg.connect(DSN) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM knowledge_c_generation_runs WHERE project_id=%s",
                (self.pid,)).fetchone()[0],0)
        consent=repo.authorize(
            self.pid,sid,self.reviewer,quote["quote_id"],quote["required_usd"])
        self.assertTrue(consent["created"])
        started=repo.start(self.pid,sid,self.reviewer,
                           quote["quote_id"],consent["consent_id"],
                           "stage14-"+uuid4().hex)
        self.assertEqual(started["status"],"generation_pending")
        self.assertFalse(started["provider_calls"])
        outcome=repo.advance(self.pid,sid,self.reviewer,started["run_id"],
                             sender=topic_sender)
        self.assertEqual(outcome["status"],"topic_review")
        self.assertEqual(outcome["provider_calls"],1)
        inspection=repo.inspect(self.pid,started["run_id"],self.reviewer)
        self.assertEqual(inspection["attempt"]["status"],"completed")
        self.assertEqual(len(inspection["attempt"]["output"]["topics"]),2)
        choices=[{"id":topic["id"],"action":"approve"}
                 for topic in inspection["attempt"]["output"]["topics"]]
        saved=repo.submit_topics(self.pid,sid,self.reviewer,started["run_id"],choices)
        self.assertEqual(saved["approved_topics_count"],2)
        self.assertEqual(repo.inspect(self.pid,started["run_id"],self.reviewer)["status"],
                         "approved")
        with self.psycopg.connect(DSN) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM tb_runs WHERE project_id=%s",(self.pid,),
            ).fetchone()[0],0)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM knowledge_c_generation_attempts WHERE project_id=%s",
                (self.pid,),
            ).fetchone()[0],1)

    def test_quote_requires_c_grant_and_wrong_project_cannot_spend(self):
        repo,sid,pub,hand,quote=self._ready_c()
        with self.assertRaises(IntakeError):
            repo.quote(self.other,sid,self.reviewer,SECOND)
        with self.assertRaises(IntakeError):
            repo.quote(self.pid,sid,"not-a-granted-reviewer",SECOND)
        with self.assertRaises(IntakeError):
            repo.authorize(self.pid,sid,self.reviewer,quote["quote_id"],"0.000001")
        with self.assertRaises(IntakeError):
            repo.start(self.pid,sid,self.reviewer,quote["quote_id"],
                       str(uuid4()),"stage14-"+uuid4().hex)
        with self.psycopg.connect(DSN) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM knowledge_c_generation_runs WHERE project_id=%s",
                (self.pid,)).fetchone()[0],0)

    def test_revoked_c_consent_fails_closed_before_paid_dispatch(self):
        repo,sid,pub,hand,quote=self._ready_c()
        consent=repo.authorize(
            self.pid,sid,self.reviewer,quote["quote_id"],quote["required_usd"])
        repo.revoke(self.pid,consent["consent_id"],self.reviewer)
        with self.assertRaises(IntakeError):
            repo.authorize(self.pid,sid,self.reviewer,quote["quote_id"],quote["required_usd"])
        with self.assertRaises(IntakeError):
            repo.start(self.pid,sid,self.reviewer,quote["quote_id"],
                       consent["consent_id"],"stage14-"+uuid4().hex)

    def test_unknown_billing_holds_reservation_and_disallows_paid_retry(self):
        repo,sid,pub,hand,quote=self._ready_c()
        consent=repo.authorize(
            self.pid,sid,self.reviewer,quote["quote_id"],quote["required_usd"])
        run=repo.start(self.pid,sid,self.reviewer,quote["quote_id"],
                       consent["consent_id"],"stage14-"+uuid4().hex)
        out=repo.advance(
            self.pid,sid,self.reviewer,run["run_id"],
            sender=lambda _body:{"choices":[],"usage":{}})
        self.assertEqual(out["status"],"uncertain")
        self.assertTrue(out["reservation_held"])
        held=repo.inspect(self.pid,run["run_id"],self.reviewer)
        self.assertNotEqual(held["reserved_usd"],"0")
        with self.assertRaises(IntakeError):
            repo.advance(self.pid,sid,self.reviewer,run["run_id"],sender=topic_sender)
        with self.psycopg.connect(DSN) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM knowledge_c_generation_attempts WHERE project_id=%s",
                (self.pid,)).fetchone()[0],1)

    def test_quote_and_consent_immutable_and_historical_source_pins(self):
        repo,sid,pub,hand,quote=self._ready_c()
        consent=repo.authorize(
            self.pid,sid,self.reviewer,quote["quote_id"],quote["required_usd"])
        for sql,ident in (
            ("DELETE FROM knowledge_c_generation_quotes WHERE id=%s",quote["quote_id"]),
            ("UPDATE knowledge_c_generation_quotes SET quoted_usd=99 WHERE id=%s",
             quote["quote_id"]),
            ("DELETE FROM knowledge_c_generation_consents WHERE id=%s",
             consent["consent_id"]),
        ):
            with self.subTest(sql=sql),self.assertRaises(RaiseException):
                with self.psycopg.connect(DSN) as conn:
                    conn.execute(sql,(ident,))
        # Quote can no longer be used when newer catalog returns a different
        # model list and cannot attest to the pinned selected C pricing.
        with self.psycopg.connect(DSN) as conn:
            refresh_model_catalog(conn,fetcher=lambda:[])
        with self.assertRaises(IntakeError):
            repo.start(self.pid,sid,self.reviewer,quote["quote_id"],
                       consent["consent_id"],"stage14-"+uuid4().hex)


if __name__=="__main__":
    unittest.main()
