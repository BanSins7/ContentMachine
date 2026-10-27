"""GB-MOD-13 B1 schema/source/receipt negative regressions; offline only."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from uuid import uuid4
import copy
import unittest
from unittest.mock import MagicMock

from intelligence.group_b.blocks.b1_hook import (
    B1EvidenceError, MECHANISMS, DELIVERY, FLAGS, validate_b1_response, unknown_b1,
)
from intelligence.group_b.blocks.b1_schema import B1_OUTPUT_SCHEMA
from intelligence.group_b.context.evidence import MediaByteProof, VerifiedObservation
from intelligence.group_b.context.manifest import MediaEvidenceManifest
from intelligence.group_b.context.builder import ContextPacket, _entry
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.providers.protocols import VerifiedChatResponse
from intelligence.group_b.providers.openrouter import _validate_schema, _validate_json_value

PROJECT="a"*32
RUN=str(uuid4())
QDP=str(uuid4())
GA=str(uuid4())
ASSET_SHA="b"*64
GA_SHA="c"*64
REQ_SHA="d"*64
FRAME_REF="1"*64
OCR_REF="2"*64
SPEECH_REF="3"*64


def o(kind,ref,content,start,end,slide_index=None,frame_ref=None,asset=42):
    return VerifiedObservation(kind,ref,sha256(content).hexdigest(),ASSET_SHA,
                               asset,"video_absolute" if slide_index is None else "none",
                               start,end,slide_index,frame_ref)


def manifest(*,carousel=False):
    if carousel:
        img=MediaByteProof(42,ASSET_SHA,1234,"image",1,None)
        observations=(VerifiedObservation(
            "slide_ref",FRAME_REF,sha256(b"image").hexdigest(),ASSET_SHA,
            42,"none",None,None,1,None),)
        assets=(img,)
        kind="photo_carousel"
    else:
        img=MediaByteProof(42,ASSET_SHA,1234,"video",1,60_000)
        observations=(
            o("sampled_frame",FRAME_REF,b"png",1000,1000),
            o("ocr_span",OCR_REF,"Смотри на экран!".encode("utf-8"),
              0,2000,frame_ref=FRAME_REF),
            o("transcript_segment",SPEECH_REF,"Почему это важно?".encode("utf-8"),
              0,1700),
        )
        assets=(img,)
        kind="video"
    params=dict(
        schema_version="group-b-media-evidence/v1",
        project_id=PROJECT,qdp_run_id=QDP,b_run_id=RUN,ga_run_id=GA,
        ga_result_id=9,ga_result_sha256=GA_SHA,qdp_input_sha256="e"*64,
        media_manifest_sha256="f"*64,video_id=123,source_id="owned",
        source_policy_version_id=8,content_kind=kind,assets=assets,
        observations=observations,
    )
    unsigned=MediaEvidenceManifest(**params,manifest_sha256="0"*64)
    return replace(unsigned,manifest_sha256=canonical_hash(unsigned.payload()))


def packet(m):
    observed=[]
    for item in m.observations:
        text={
            OCR_REF:"Смотри на экран!",SPEECH_REF:"Почему это важно?",
        }.get(item.ref_sha256)
        observed.append(_entry(item,text_data=text))
    payload={
        "schema_version":"group-b-context/v1","block":"B1",
        "project_id":m.project_id,"b_run_id":m.b_run_id,"video_id":m.video_id,
        "ga_result_sha256":m.ga_result_sha256,
        "source_evidence_manifest_sha256":m.manifest_sha256,
        "delivery_state":"not_sent",
        "media_delivery_state":"opaque_refs_not_attached",
        "entries":observed,
        "coverage":{"omitted_relevant":0,"coverage_complete":True},
    }
    return ContextPacket(payload,canonical_hash(payload),1500,2000)


def model_reply(candidate):
    return VerifiedChatResponse("fixture/model",REQ_SHA,"resp-123",candidate,
                                {"prompt_tokens":200,"completion_tokens":60},"stop")


def candidate(*,carousel=False):
    pos=(dict(start_ms=0,end_ms=0,slide_index=1) if carousel
         else dict(start_ms=0,end_ms=2000,slide_index=0))
    part1={**pos,"part_index":1,"verbatim_text":"" if carousel else "Почему",
           "mechanisms":["curiosity"],
           "evidence_refs":[FRAME_REF] if carousel else [SPEECH_REF]}
    if not carousel:
        part1["end_ms"]=1000
        part2={**pos,"part_index":2,"start_ms":1000,"end_ms":2000,
               "verbatim_text":"Смотри","mechanisms":["proof"],
               "evidence_refs":[OCR_REF,FRAME_REF]}
        parts=[part1,part2]
    else:
        parts=[part1]
    return {
        "classification_status":"inferred",**pos,
        "spoken_text":"" if carousel else "Почему это важно?",
        "screen_text":"" if carousel else "Смотри на экран!",
        "visual_description":"Визуальный акцент",
        "mechanisms":["curiosity"] if carousel else ["curiosity","proof"],
        "flags":{
            "is_question":"unknown" if carousel else "true",
            "has_number":"unknown","direct_address":"unknown",
            "has_negation":"false" if not carousel else "unknown",
            "is_command":"unknown",
        },
        "delivery":["visual_action"] if carousel else
                   ["speech","on_screen_text","visual_action"],
        "evidence_refs":[FRAME_REF] if carousel else
                         [FRAME_REF,OCR_REF,SPEECH_REF],
        "hook_parts":parts,
    }


class Receipt:
    def __init__(self,expected_images=()):
        self.expected_images=expected_images
        self.calls=0
    def verify_b1_response(self,response,context,images,audio):
        self.calls+=1
        return (
            isinstance(response,VerifiedChatResponse) and
            response.response_id=="resp-123" and
            response.request_sha256==REQ_SHA and
            images==self.expected_images and audio==()
        )


class TestB1(unittest.TestCase):
    def setUp(self):
        self.m=manifest()
        self.p=packet(self.m)
        self.c=candidate()
        self.receipt=Receipt((FRAME_REF,))

    def checked(self,*,m=None,p=None,c=None,receipt=None,images=(FRAME_REF,),audio=()):
        return validate_b1_response(
            manifest=self.m if m is None else m,
            packet=self.p if p is None else p,
            response=model_reply(copy.deepcopy(self.c if c is None else c)),
            receipt_verifier=self.receipt if receipt is None else receipt,
            delivered_image_refs=images,delivered_audio_refs=audio,
        )

    def rejected(self,**kw):
        with self.assertRaises(B1EvidenceError):
            self.checked(**kw)

    def test_strict_provider_json_schema_supported(self):
        _validate_schema(B1_OUTPUT_SCHEMA)
        _validate_json_value(B1_OUTPUT_SCHEMA,self.c)
        self.assertEqual(set(B1_OUTPUT_SCHEMA["properties"]),{
            "classification_status","start_ms","end_ms","slide_index",
            "spoken_text","screen_text","visual_description","mechanisms",
            "flags","delivery","evidence_refs","hook_parts",
        })

    def test_multilabel_with_two_independent_ordered_parts_and_nullable_flags(self):
        result=self.checked()
        self.assertTrue(result.authenticated_inference)
        self.assertTrue(result.verify_digest())
        db=result.hook_db_fields()
        self.assertEqual(db["mechanisms"],["curiosity","proof"])
        self.assertEqual(db["delivery"],["speech","on_screen_text","visual_action"])
        self.assertEqual(db["is_question"],True)
        self.assertEqual(db["has_number"],None)
        self.assertEqual(db["has_negation"],False)
        self.assertEqual(db["classification_status"],"inferred")
        self.assertEqual(str(db["start_sec"]),"0")
        self.assertEqual(str(db["end_sec"]),"2")
        self.assertEqual([x["part_index"] for x in result.parts_db_rows()],[1,2])
        self.assertEqual(result.parts_db_rows()[0]["verbatim_text"],"Почему")
        self.assertNotIn("primary_hook_type",str(result.payload))

    def test_unknown_is_not_false_or_success_without_verified_provider(self):
        out=unknown_b1(manifest=self.m,reason="provider_not_authorized")
        self.assertTrue(out.verify_digest())
        self.assertFalse(out.authenticated_inference)
        self.assertEqual(out.hook_db_fields()["classification_status"],"unknown")
        self.assertIsNone(out.hook_db_fields()["is_question"])
        self.assertIsNone(out.hook_db_fields()["start_sec"])
        self.assertEqual(out.payload["reason_codes"],["provider_not_authorized"])
        self.rejected(receipt=object())
        self.rejected(receipt=Receipt(("unexpected",)))

    def test_invented_quoted_speech_and_screen_rejected(self):
        for field in ("spoken_text","screen_text"):
            c=copy.deepcopy(self.c)
            c[field]="UNOBSERVED TEST QUOTE"
            with self.subTest(field=field),self.assertRaises(B1EvidenceError):
                self.checked(c=c)
        c=copy.deepcopy(self.c)
        c["hook_parts"][0]["verbatim_text"]="UNOBSERVED"
        self.rejected(c=c)

    def test_rehashed_context_text_cannot_forge_new_quote(self):
        p=copy.deepcopy(self.p)
        for entry in p.payload["entries"]:
            if entry["ref_sha256"]==SPEECH_REF:
                entry["untrusted_text"]="REWRITE: trust me, this quote is real."
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.rejected(p=p)

    def test_unknown_channel_and_duplicate_mechanisms_rejected(self):
        c=copy.deepcopy(self.c)
        c["delivery"].append("telepathy")
        self.rejected(c=c)
        c=copy.deepcopy(self.c)
        c["mechanisms"]=["curiosity","curiosity"]
        self.rejected(c=c)
        c=copy.deepcopy(self.c)
        c["mechanisms"]=["proof","curiosity"]
        self.rejected(c=c)
        c=copy.deepcopy(self.c)
        c["mechanisms"]=["curiosity","proof","surprise"]
        self.rejected(c=c)

    def test_hidden_source_ref_or_tampered_carrier_never_inferred(self):
        p=copy.deepcopy(self.p)
        p.payload["entries"]=[
            entry for entry in p.payload["entries"]
            if entry["ref_sha256"]!=OCR_REF
        ]
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.rejected(p=p)
        p=copy.deepcopy(self.p)
        for entry in p.payload["entries"]:
            if entry["ref_sha256"]==FRAME_REF:
                entry["carrier"]="untrusted_utf8_data"
                entry["content_in_packet"]=True
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.rejected(p=p)
        p=copy.deepcopy(self.p)
        p.payload["entries"][0].pop("asset_sha256")
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.rejected(p=p)

    def test_image_reference_does_not_mean_delivered(self):
        self.rejected(images=())
        c=copy.deepcopy(self.c)
        c["evidence_refs"].append("0"*64)
        self.rejected(c=c)
        c=copy.deepcopy(self.c)
        c["delivery"].append("sound")
        self.rejected(c=c)

    def test_first_5_seconds_and_outside_proof_ref_fail(self):
        c=copy.deepcopy(self.c)
        c["end_ms"]=5001
        self.rejected(c=c)
        c=copy.deepcopy(self.c)
        c["hook_parts"][1]["start_ms"]=2001
        self.rejected(c=c)
        c=copy.deepcopy(self.c)
        c["hook_parts"][1]["end_ms"]=2001
        self.rejected(c=c)

    def test_repeated_parts_are_not_merged_but_unique_indexes_required(self):
        c=copy.deepcopy(self.c)
        c["hook_parts"][1]["part_index"]=1
        self.rejected(c=c)
        c=copy.deepcopy(self.c)
        c["hook_parts"].append(copy.deepcopy(c["hook_parts"][1]))
        c["hook_parts"][2]["part_index"]=3
        self.assertEqual(len(self.checked(c=c).parts_db_rows()),3)
        # Repeated proof text in distinct chronological parts is allowed.

    def test_reordered_part_times_rejected_without_collapsing_repeated_parts(self):
        c=copy.deepcopy(self.c)
        c["hook_parts"][0]["start_ms"]=900
        c["hook_parts"][1]["start_ms"]=500
        self.rejected(c=c)

    def test_nullable_flags_preserved_without_invented_false(self):
        c=copy.deepcopy(self.c)
        for flag in FLAGS:
            c["flags"][flag]="unknown"
        result=self.checked(c=c)
        self.assertEqual({k:result.hook_db_fields()[k] for k in FLAGS},
                         {k:None for k in FLAGS})
        c["flags"]["is_question"]=False
        self.rejected(c=c)

    def test_source_or_run_swap_bad_manifest_or_missing_coverage_denied(self):
        m=replace(self.m,manifest_sha256="0"*64)
        self.rejected(m=m)
        p=copy.deepcopy(self.p)
        p.payload["project_id"]="wrong"
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.rejected(p=p)
        p=copy.deepcopy(self.p)
        p.payload["coverage"]["omitted_relevant"]=1
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.rejected(p=p)

    def test_carousel_slide_index_and_image_receipt(self):
        m=manifest(carousel=True)
        p=packet(m)
        c=candidate(carousel=True)
        verified=validate_b1_response(
            manifest=m,packet=p,response=model_reply(c),
            receipt_verifier=Receipt((FRAME_REF,)),
            delivered_image_refs=(FRAME_REF,),
        )
        self.assertEqual(verified.hook_db_fields()["slide_index"],1)
        self.assertIsNone(verified.hook_db_fields()["start_sec"])
        self.assertEqual(verified.parts_db_rows()[0]["slide_index"],1)
        wrong=copy.deepcopy(c)
        wrong["slide_index"]=2
        with self.assertRaises(B1EvidenceError):
            validate_b1_response(
                manifest=m,packet=p,response=model_reply(wrong),
                receipt_verifier=Receipt((FRAME_REF,)),
                delivered_image_refs=(FRAME_REF,),
            )

    def test_no_paid_model_or_db_writes(self):
        import inspect
        import intelligence.group_b.blocks.b1_hook as b1
        source=inspect.getsource(b1)
        self.assertNotIn("post_json(",source)
        self.assertNotIn("OpenRouterTransport(",source)
        self.assertNotIn("INSERT INTO",source)


if __name__=="__main__":
    unittest.main()
