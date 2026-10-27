"""GB-MOD-14 B2 exact-format source coverage, review and unsent request QA.

Fully synthetic verified-lineage fixtures: NO HTTP, model, charges or DB writes.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4
import copy
import unittest

from intelligence.group_b.blocks.b2_format import (
    B2FormatError, B2FormatProposal, VIDEO_FORMATS, CAROUSEL_FORMATS,
    validate_b2_response, unknown_b2, prepare_b2_request,
)
from intelligence.group_b.blocks.b2_schema import B2_OUTPUT_SCHEMA
from intelligence.group_b.context.evidence import (
    MediaByteProof, VerifiedObservation,
)
from intelligence.group_b.context.manifest import MediaEvidenceManifest
from intelligence.group_b.context.builder import ContextPacket, _entry
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.providers.protocols import (
    VerifiedChatResponse, ModelCatalogSnapshot, ModelDescriptor,
)
from intelligence.group_b.providers.openrouter import (
    VerifiedImage, ProviderFailure, _validate_schema, _validate_json_value,
)

PROJECT="a"*32
RUN=str(uuid4())
QDP=str(uuid4())
GA_RUN=str(uuid4())
GA_SHA="b"*64
IMAGE_BYTES=b"\x89PNG\r\n\x1a\n" + b"not-a-real-pixel-fixture"
SOURCE_SHA="c"*64
REQ_SHA="d"*64


def fixture(*,content_kind="video",frame_times=(0,15000,35000,55000),
            slides=(1,2,3)):
    video=content_kind in ("video","slideshow_video")
    if video:
        assets=(MediaByteProof(11,SOURCE_SHA,1000,"video",1,60000),)
        observations=tuple(
            VerifiedObservation(
                "sampled_frame",f"{i+1:064x}",sha256(IMAGE_BYTES).hexdigest(),
                SOURCE_SHA,11,"video_absolute",t,t
            ) for i,t in enumerate(frame_times)
        )
    else:
        assets=tuple(MediaByteProof(10+i,f"{i+10:064x}",1000,"image",i,None)
                     for i in slides)
        observations=tuple(
            VerifiedObservation("slide_ref",f"{i+1:064x}",
                sha256(IMAGE_BYTES).hexdigest(),f"{i+10:064x}",
                10+i,"none",None,None,i)
            for i in slides
        )
    data={
        "schema_version":"group-b-media-evidence/v1",
        "project_id":PROJECT,"qdp_run_id":QDP,"b_run_id":RUN,
        "ga_run_id":GA_RUN,"ga_result_id":16,"ga_result_sha256":GA_SHA,
        "qdp_input_sha256":"e"*64,"media_manifest_sha256":"f"*64,
        "video_id":225,"source_id":"authorized-fixture",
        "source_policy_version_id":3,"content_kind":content_kind,
        "assets":assets,"observations":observations,
    }
    m=MediaEvidenceManifest(**data,manifest_sha256="0"*64)
    return replace(m,manifest_sha256=canonical_hash(m.payload()))


def packet(m,*,include=None):
    refs={x.ref_sha256 for x in m.observations}
    selected=refs if include is None else set(include)
    included=[_entry(o,text_data=None) for o in m.observations
              if o.ref_sha256 in selected]
    excluded=[{"kind":o.kind,"ref_sha256":o.ref_sha256,
               "reason":"image_reference_cap"} for o in m.observations
              if o.ref_sha256 not in selected]
    payload={
        "schema_version":"group-b-context/v1","project_id":PROJECT,
        "b_run_id":RUN,"video_id":m.video_id,"block":"B2",
        "input_content_kind":m.content_kind,
        "ga_result_sha256":GA_SHA,
        "source_evidence_manifest_sha256":m.manifest_sha256,
        "delivery_state":"not_sent",
        "media_delivery_state":"opaque_refs_not_attached",
        "entries":included,"omitted":excluded,
        "coverage":{
            "source_observations":len(m.observations),
            "relevant_observations":len(m.observations),
            "selected_observations":len(included),
            "omitted_relevant":len(excluded),
            "selected_image_refs":len(included),
            "available_frame_refs":len(m.observations) if m.content_kind in ("video","slideshow_video") else 0,
            "available_slide_refs":len(m.observations) if m.content_kind not in ("video","slideshow_video") else 0,
            "coverage_complete":not excluded,
        },
    }
    return ContextPacket(payload,canonical_hash(payload),1024,1400)


def response(candidate):
    return VerifiedChatResponse(
        "fixture/vision",REQ_SHA,"response-1",copy.deepcopy(candidate),
        {"prompt_tokens":500,"completion_tokens":50},"stop",
    )


def labels(m,*,confidence="high",secondary="",primary=None,carousel=None):
    video=m.content_kind in ("video","slideshow_video")
    return {
        "video_primary":("talking_head" if primary is None else primary) if video else "",
        "video_secondary":secondary if video else "",
        "carousel_format":("step_by_step" if carousel is None else carousel) if not video else "",
        "confidence":confidence,
        "evidence_refs":[x.ref_sha256 for x in m.observations],
    }


class Receipt:
    """Trusted verifier FAKE ONLY; real charged receipt not installed."""
    def __init__(self,expected=None,result=True):
        self.expected=expected
        self.result=result
        self.called=0
    def verify_b2_response(self,response_value,context,delivered):
        self.called+=1
        return (self.result is True and
                response_value.request_sha256==REQ_SHA and
                response_value.response_id=="response-1" and
                isinstance(context,ContextPacket) and
                (self.expected is None or delivered==self.expected))


class TestB2(unittest.TestCase):
    def setUp(self):
        self.m=fixture()
        self.p=packet(self.m)
        self.refs=tuple(x.ref_sha256 for x in self.m.observations)
        self.c=labels(self.m)
        self.receipt=Receipt(self.refs)

    def check(self,*,m=None,p=None,c=None,delivery=None,verifier=None):
        m=self.m if m is None else m
        p=self.p if p is None else p
        c=self.c if c is None else c
        actual=self.refs if delivery is None else delivery
        return validate_b2_response(
            manifest=m,packet=p,response=response(c),
            receipt_verifier=self.receipt if verifier is None else verifier,
            delivered_image_refs=actual,
        )

    def deny(self,**kwargs):
        with self.assertRaises(B2FormatError):
            self.check(**kwargs)

    def test_enum_is_exact_12_video_8_carousel_and_schema_is_strict(self):
        self.assertEqual(len(VIDEO_FORMATS),12)
        self.assertEqual(len(CAROUSEL_FORMATS),8)
        self.assertIn("slideshow_video",VIDEO_FORMATS)
        self.assertNotIn("image_post",CAROUSEL_FORMATS)
        self.assertNotIn("ugc",VIDEO_FORMATS)
        _validate_schema(B2_OUTPUT_SCHEMA)
        _validate_json_value(B2_OUTPUT_SCHEMA,self.c)

    def test_video_primary_and_distinct_secondary_one_schema_005_row(self):
        c=labels(self.m,primary="talking_head",secondary="demonstration")
        x=self.check(c=c)
        self.assertTrue(x.verify_digest())
        self.assertTrue(x.authenticated_inference)
        self.assertEqual(x.db_fields(),{
            "video_primary":"talking_head",
            "video_secondary":"demonstration",
            "carousel_format":None,
            "classification_status":"inferred",
            "taxonomy_version":"1.0",
        })
        self.assertEqual(x.payload["evidence_refs"],list(self.refs))
        self.assertEqual(x.payload["review_reasons"],[])
        self.assertNotIn("people",x.payload)
        self.assertNotIn("primary_ugc_type",x.payload)

    def test_video_secondary_optional_and_slideshow_video_is_video(self):
        base=self.check()
        self.assertIsNone(base.db_fields()["video_secondary"])
        m=fixture(content_kind="slideshow_video")
        out=self.check(m=m,p=packet(m),c=labels(m,primary="slideshow_video"),
                       delivery=tuple(o.ref_sha256 for o in m.observations),
                       verifier=Receipt(tuple(o.ref_sha256 for o in m.observations)))
        self.assertEqual(out.db_fields()["video_primary"],"slideshow_video")
        self.assertIsNone(out.db_fields()["carousel_format"])

    def test_carousel_exclusive_labels_and_multislide_proof(self):
        m=fixture(content_kind="photo_carousel")
        p=packet(m)
        refs=tuple(o.ref_sha256 for o in m.observations)
        x=self.check(m=m,p=p,c=labels(m,carousel="comparison"),
                     delivery=refs,verifier=Receipt(refs))
        self.assertEqual(x.db_fields()["carousel_format"],"comparison")
        self.assertIsNone(x.db_fields()["video_primary"])
        self.assertIsNone(x.db_fields()["video_secondary"])
        self.assertEqual(x.db_fields()["classification_status"],"inferred")
        mixed=fixture(content_kind="mixed_carousel")
        self.assertEqual(self.check(
            m=mixed,p=packet(mixed),c=labels(mixed,carousel="storytelling"),
            delivery=refs,verifier=Receipt(refs)
        ).db_fields()["carousel_format"],"storytelling")

    def test_wrong_kind_cross_label_and_identical_formats_rejected(self):
        for updates in (
            {"video_primary":"image_post"},
            {"video_primary":"talking_head","video_secondary":"talking_head"},
            {"carousel_format":"listicle"},
            {"video_primary":""},
        ):
            c={**self.c,**updates}
            with self.subTest(updates=updates),self.assertRaises(B2FormatError):
                self.check(c=c)
        carousel_m=fixture(content_kind="photo_carousel")
        original=labels(carousel_m)
        bad={**original,"video_primary":"talking_head"}
        self.deny(m=carousel_m,p=packet(carousel_m),c=bad,
                  delivery=tuple(o.ref_sha256 for o in carousel_m.observations),
                  verifier=Receipt(tuple(o.ref_sha256 for o in carousel_m.observations)))
        unsupported=fixture(content_kind="image_post")
        with self.assertRaises(B2FormatError):
            unknown_b2(manifest=unsupported,reason="unsupported_content")

    def test_low_confidence_always_needs_review(self):
        x=self.check(c=labels(self.m,confidence="low"))
        self.assertEqual(x.db_fields()["classification_status"],"needs_review")
        self.assertIn("low_model_confidence",x.payload["review_reasons"])
        self.assertTrue(x.authenticated_inference)
        self.deny(c={**self.c,"confidence":"extremely_certain"})

    def test_missing_visual_frames_requires_review_not_fabricated_inferred(self):
        p=packet(self.m,include=self.refs[:2])
        c=labels(self.m)
        c["evidence_refs"]=list(self.refs[:2])
        proposal=self.check(p=p,c=c,delivery=self.refs[:2],
                            verifier=Receipt(self.refs[:2]))
        self.assertEqual(proposal.db_fields()["classification_status"],"needs_review")
        self.assertIn("incomplete_visual_selection",proposal.payload["review_reasons"])
        self.assertIn("incomplete_context_coverage",proposal.payload["review_reasons"])
        self.assertIn("visual_time_coverage_weak",proposal.payload["review_reasons"])
        self.assertEqual(proposal.payload["evidence_refs"],list(self.refs[:2]))

    def test_sparse_single_video_frame_cannot_claim_inferred(self):
        m=fixture(frame_times=(1000,))
        ref=(m.observations[0].ref_sha256,)
        got=self.check(m=m,p=packet(m),c=labels(m),delivery=ref,
                       verifier=Receipt(ref))
        self.assertEqual(got.db_fields()["classification_status"],"needs_review")
        self.assertIn("visual_time_coverage_weak",got.payload["review_reasons"])

    def test_carousel_missing_slide_context_needs_review(self):
        m=fixture(content_kind="photo_carousel")
        refs=tuple(o.ref_sha256 for o in m.observations)
        c=labels(m)
        c["evidence_refs"]=list(refs[:2])
        out=self.check(m=m,p=packet(m,include=refs[:2]),c=c,
                       delivery=refs[:2],verifier=Receipt(refs[:2]))
        self.assertEqual(out.db_fields()["classification_status"],"needs_review")
        self.assertIn("slide_coverage_weak",out.payload["review_reasons"])

    def test_no_image_delivery_or_forged_receipt_blocks_every_result(self):
        self.deny(delivery=())
        self.deny(delivery=self.refs[:-1])
        self.deny(verifier=object())
        self.deny(verifier=Receipt(self.refs,result=False))
        self.deny(delivery=self.refs+("0"*64,))
        self.deny(delivery=self.refs[:-1]+(self.refs[0],))

    def test_tampered_source_ref_sha_asset_or_context_digest_rejected(self):
        p=copy.deepcopy(self.p)
        p.payload["entries"][0]["asset_sha256"]="f"*64
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.deny(p=p)
        p=replace(self.p,packet_sha256="0"*64)
        self.deny(p=p)
        m=replace(self.m,manifest_sha256="0"*64)
        self.deny(m=m)
        p=copy.deepcopy(self.p)
        p.payload["entries"][0]["carrier"]="untrusted_utf8_data"
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.deny(p=p)

    def test_incomplete_but_false_complete_coverage_bit_rejected(self):
        p=copy.deepcopy(self.p)
        p.payload["entries"].pop()
        p.payload["coverage"]["selected_image_refs"]-=1
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.deny(p=p)

    def test_candidate_refs_must_point_to_selected_visual_evidence(self):
        self.deny(c={**self.c,"evidence_refs":[]})
        self.deny(c={**self.c,"evidence_refs":[self.refs[0],self.refs[0]]})
        self.deny(c={**self.c,"evidence_refs":["f"*64]})
        self.deny(c={**self.c,"evidence_refs":[self.refs[0]]})

    def test_unknown_with_no_paid_provider_not_inferred_and_nullable_sql_fields(self):
        v=unknown_b2(manifest=self.m,reason="provider_authorization_missing")
        self.assertTrue(v.verify_digest())
        self.assertFalse(v.authenticated_inference)
        self.assertEqual(v.db_fields()["classification_status"],"unknown")
        self.assertIsNone(v.db_fields()["video_primary"])
        self.assertIsNone(v.db_fields()["video_secondary"])
        self.assertIsNone(v.db_fields()["carousel_format"])

    def test_request_never_sent_and_requires_all_actual_png_bytes(self):
        model=ModelDescriptor(
            "test/vision","e"*64,8000,1024,
            frozenset(("text","image")),frozenset(("text",)),
            frozenset(("structured_outputs",)),
            Decimal("0.000001"),Decimal("0.000002"),
            Decimal("0.0002"),Decimal("0.00001"),
        )
        cat=ModelCatalogSnapshot((model,),"e"*64,"fixture",True)
        with self.assertRaises(ProviderFailure):
            prepare_b2_request(catalog=cat,model=model.slug,
                packet=self.p,max_output_tokens=500)
        attachments=tuple(VerifiedImage(o.ref_sha256,"image/png",IMAGE_BYTES)
                          for o in self.m.observations)
        req=prepare_b2_request(catalog=cat,model=model.slug,
            packet=self.p,max_output_tokens=500,attachments=attachments)
        self.assertTrue(req.verify_digest())
        self.assertEqual(req.body["model"],"test/vision")
        self.assertTrue(req.body["provider"]["require_parameters"])
        self.assertFalse(req.body["stream"])
        self.assertIn("visual_gallery",req.body["messages"][0]["content"])
        self.assertEqual(req.request_sha256,prepare_b2_request(
            catalog=cat,model=model.slug,packet=self.p,
            max_output_tokens=500,attachments=attachments
        ).request_sha256)
        self.assertEqual(len(req.body["messages"][1]["content"]),len(self.refs)+1)
        tampered=attachments[:-1]+(
            VerifiedImage(self.refs[-1],"image/png",b"\x89PNG\r\n\x1a\nbadbytes"),
        )
        with self.assertRaises(ProviderFailure):
            prepare_b2_request(catalog=cat,model=model.slug,
                packet=self.p,max_output_tokens=500,attachments=tampered)

    def test_no_llm_http_or_db_write_on_import_and_validation(self):
        import inspect
        import intelligence.group_b.blocks.b2_format as impl
        code=inspect.getsource(impl)
        self.assertNotIn("post_json(",code)
        self.assertNotIn("INSERT INTO",code)
        self.assertNotIn("OpenRouterTransport(",code)


if __name__=="__main__":
    unittest.main()
