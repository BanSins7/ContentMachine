"""GB-MOD-17 B4 source-backed positive/negative/AI origin truth table.

Synthetic provider/absence/origin receipt objects are TEST DOUBLES, not
production evidence or authorization. No HTTP, paid model or DB in tests.
"""
from __future__ import annotations
import copy
from dataclasses import replace
from decimal import Decimal
import unittest

from intelligence.group_b.blocks.b4_visual import (
    B4VisualError, FEATURES, unknown_b4, validate_b4_visual,
    prepare_b4_request,
)
from intelligence.group_b.blocks.b4_schema import B4_OUTPUT_SCHEMA
from intelligence.group_b.context.builder import ContextPacket
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.providers.protocols import (
    VerifiedChatResponse, ModelDescriptor, ModelCatalogSnapshot,
)
from intelligence.group_b.providers.openrouter import (
    VerifiedImage, ProviderFailure, _validate_schema, _validate_json_value,
)
from test_group_b_b2 import fixture,packet as b2_packet,IMAGE_BYTES

REQUEST_SHA="d"*64


def packet(manifest,*,include=None):
    p=b2_packet(manifest,include=include)
    raw=copy.deepcopy(p.payload)
    raw["block"]="B4"
    return replace(p,payload=raw,packet_sha256=canonical_hash(raw))


def answer(data):
    return VerifiedChatResponse(
        "fixture/vision",REQUEST_SHA,"b4-test-response",copy.deepcopy(data),
        {"prompt_tokens":180,"completion_tokens":35},"stop",
    )


def labels(refs,*,statuses=("present","present","present"),
           confidence="high"):
    return {
        feature:{
            "assessment":state,
            "confidence":confidence,
            "evidence_refs":[refs[i]] if state=="present" else [],
        }
        for i,(feature,state) in enumerate(zip(FEATURES,statuses))
    }


class Receipt:
    def __init__(self,refs,*,valid=True):
        self.refs=refs
        self.valid=valid
        self.calls=0
    def verify_b4_response(self,response,p,delivered):
        self.calls+=1
        return (
            self.valid is True and
            delivered==self.refs and
            response.request_sha256==REQUEST_SHA and
            response.response_id=="b4-test-response" and
            isinstance(p,ContextPacket)
        )


class WholeMedia:
    def __init__(self,approved=()):
        self.approved=set(approved)
        self.calls=[]
    def verify_whole_media_absence(self,manifest,feature,refs):
        self.calls.append((feature,refs))
        return feature in self.approved and len(refs)>0


class Origin:
    def __init__(self,status=None,claim=None):
        self.status=status
        self.claim=claim
    def verify_ai_origin(self,manifest):
        if self.status is None:
            return None
        return {
            "status":self.status,
            "claim_type":self.claim,
            "evidence_sha256":"e"*64,
            "verified":True,
        }


class TestB4(unittest.TestCase):
    def setUp(self):
        self.m=fixture()
        self.refs=tuple(x.ref_sha256 for x in self.m.observations)
        self.p=packet(self.m)
        self.raw=labels(self.refs)
        self.receipt=Receipt(self.refs)

    def check(self,*,manifest=None,p=None,raw=None,delivered=None,
              receipt=None,absence=None,origin=None):
        return validate_b4_visual(
            manifest=self.m if manifest is None else manifest,
            packet=self.p if p is None else p,
            response=answer(self.raw if raw is None else raw),
            receipt_verifier=self.receipt if receipt is None else receipt,
            delivered_image_refs=self.refs if delivered is None else delivered,
            absence_verifier=absence,
            origin_verifier=origin,
        )

    def deny(self,**kwargs):
        with self.assertRaises(B4VisualError):
            self.check(**kwargs)

    def test_schema_has_three_features_and_exact_four_sql_columns(self):
        _validate_schema(B4_OUTPUT_SCHEMA)
        _validate_json_value(B4_OUTPUT_SCHEMA,self.raw)
        self.assertEqual(tuple(B4_OUTPUT_SCHEMA["properties"]),FEATURES)
        self.assertNotIn("ai_generated",B4_OUTPUT_SCHEMA["properties"])
        output=self.check()
        self.assertTrue(output.authenticated_visual_pass)
        self.assertTrue(output.verify_digest())
        self.assertTrue(output.can_persist)
        self.assertEqual(output.db_fields(),{
            "ai_generated":None,"person_visible":True,
            "product_visible":True,"logo_visible":True,
        })
        self.assertEqual(output.payload["classification_status"],"inferred")
        self.assertEqual(output.payload["review_reasons"],[])

    def test_visual_style_or_model_can_never_claim_ai_generated(self):
        invalid=copy.deepcopy(self.raw)
        invalid["ai_generated"]=True
        self.deny(raw=invalid)
        output=self.check()
        self.assertIsNone(output.db_fields()["ai_generated"])
        self.assertIsNone(output.payload["ai_origin_evidence_sha256"])

    def test_ai_true_and_false_only_from_two_distinct_attested_origin_types(self):
        yes=self.check(origin=Origin("generated","signed_generation_provenance"))
        self.assertIs(yes.db_fields()["ai_generated"],True)
        self.assertEqual(yes.payload["ai_origin_evidence_sha256"],"e"*64)
        no=self.check(origin=Origin("not_generated","full_chain_verified_non_ai"))
        self.assertIs(no.db_fields()["ai_generated"],False)
        for bad in (
            Origin("generated","full_chain_verified_non_ai"),
            Origin("not_generated","signed_generation_provenance"),
            Origin("obviously_ai","signed_generation_provenance"),
        ):
            with self.assertRaises(B4VisualError):
                self.check(origin=bad)
        self.assertIsNone(self.check(origin=Origin()).db_fields()["ai_generated"])

    def test_title_only_no_media_cannot_be_a_visual_pass(self):
        m=fixture(frame_times=())
        p=packet(m)
        with self.assertRaises(B4VisualError):
            self.check(manifest=m,p=p,raw=labels((),statuses=(
                "unknown","unknown","unknown")),delivered=(),
                receipt=Receipt(()))

    def test_absent_predictions_from_sampled_video_never_default_to_false(self):
        raw=labels(self.refs,statuses=("absent","present","present"))
        out=self.check(raw=raw)
        self.assertIsNone(out.payload["person_visible"])
        self.assertEqual(out.payload["product_visible"],True)
        self.assertTrue(out.authenticated_visual_pass)
        self.assertFalse(out.can_persist)
        self.assertIn("person_visible_absence_unverified",out.payload["review_reasons"])
        with self.assertRaises(B4VisualError):
            out.db_fields()

    def test_real_independent_whole_media_absence_allows_false(self):
        raw=labels(self.refs,statuses=("absent","present","absent"))
        host=WholeMedia(("person_visible","logo_visible"))
        out=self.check(raw=raw,absence=host)
        self.assertTrue(out.can_persist)
        self.assertEqual(out.db_fields(),{
            "ai_generated":None,"person_visible":False,
            "product_visible":True,"logo_visible":False,
        })
        self.assertEqual([t[0] for t in host.calls],
                         ["person_visible","logo_visible"])

    def test_negative_claim_unverified_even_with_all_selected_frames(self):
        raw=labels(self.refs,statuses=("absent","absent","absent"))
        out=self.check(raw=raw,absence=WholeMedia(("person_visible",)))
        self.assertIs(out.payload["person_visible"],False)
        self.assertIsNone(out.payload["product_visible"])
        self.assertIsNone(out.payload["logo_visible"])
        self.assertFalse(out.can_persist)

    def test_missing_selected_frame_forces_review_even_when_every_selected_passed(self):
        selected=self.refs[:2]
        p=packet(self.m,include=selected)
        c=labels(selected,statuses=("present","present","unknown"))
        out=self.check(p=p,raw=c,delivered=selected,receipt=Receipt(selected))
        self.assertFalse(out.can_persist)
        self.assertIn("visual_selection_incomplete",out.payload["review_reasons"])
        self.assertIn("video_temporal_coverage_weak",out.payload["review_reasons"])
        self.assertIsNone(out.payload["logo_visible"])
        raw=labels(selected,statuses=("absent","present","unknown"))
        out=self.check(p=p,raw=raw,delivered=selected,receipt=Receipt(selected),
                       absence=WholeMedia(("person_visible",)))
        self.assertIsNone(out.payload["person_visible"])

    def test_low_confidence_or_unknown_never_writes_an_invented_bool(self):
        low=labels(self.refs,confidence="low")
        out=self.check(raw=low)
        self.assertEqual(out.payload["classification_status"],"needs_review")
        self.assertFalse(out.can_persist)
        unknown=labels(self.refs,statuses=("present","unknown","present"))
        out=self.check(raw=unknown)
        self.assertIsNone(out.payload["product_visible"])
        self.assertFalse(out.can_persist)

    def test_missing_delivery_forged_receipt_and_unknown_image_sha_denied(self):
        self.deny(delivered=())
        self.deny(delivered=self.refs[:-1])
        self.deny(delivered=self.refs+("f"*64,))
        self.deny(receipt=object())
        self.deny(receipt=Receipt(self.refs,valid=False))
        p=copy.deepcopy(self.p)
        p.payload["entries"][0]["asset_sha256"]="f"*64
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.deny(p=p)
        self.deny(manifest=replace(self.m,manifest_sha256="0"*64))

    def test_forged_complete_coverage_and_hidden_evidence_denied(self):
        p=copy.deepcopy(self.p)
        p.payload["entries"].pop()
        p.payload["coverage"]["selected_observations"]-=1
        p.payload["coverage"]["selected_image_refs"]-=1
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.deny(p=p)
        p=copy.deepcopy(self.p)
        p.payload["entries"][0]["carrier"]="untrusted_utf8_data"
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.deny(p=p)

    def test_ai_origin_unknown_result_is_not_written_as_false(self):
        out=unknown_b4(manifest=self.m,reason="provider_not_approved")
        self.assertTrue(out.verify_digest())
        self.assertFalse(out.authenticated_visual_pass)
        self.assertFalse(out.can_persist)
        self.assertIsNone(out.payload["ai_generated"])
        self.assertIsNone(out.payload["person_visible"])
        with self.assertRaises(B4VisualError):
            out.db_fields()

    def test_wrong_provenance_qdp_ga_policy_or_source_rejected(self):
        for field,bad in (
            ("qdp_run_id","00000000-0000-4000-8000-000000000000"),
            ("ga_result_id",100000),
            ("source_policy_version_id",9999),
            ("input_content_kind","mixed_carousel"),
        ):
            p=copy.deepcopy(self.p)
            p.payload[field]=bad
            p=replace(p,packet_sha256=canonical_hash(p.payload))
            with self.subTest(field=field),self.assertRaises(B4VisualError):
                self.check(p=p)

    def test_carousel_requires_all_original_slides_for_no_review(self):
        m=fixture(content_kind="photo_carousel")
        refs=tuple(x.ref_sha256 for x in m.observations)
        out=self.check(manifest=m,p=packet(m),raw=labels(refs),
                       delivered=refs,receipt=Receipt(refs))
        self.assertTrue(out.can_persist)
        p=packet(m,include=refs[:2])
        partial=self.check(manifest=m,p=p,delivered=refs[:2],
            raw=labels(refs[:2],statuses=("present","present","unknown")),
            receipt=Receipt(refs[:2]))
        self.assertFalse(partial.can_persist)
        self.assertIn("carousel_slide_coverage_weak",partial.payload["review_reasons"])

    def test_mixed_carousel_video_slide_uses_original_time_pinned_frame(self):
        from intelligence.group_b.context.evidence import (
            MediaByteProof,VerifiedObservation,
        )
        from hashlib import sha256
        m=fixture(content_kind="mixed_carousel",slides=(1,2,3))
        media=list(m.assets)
        visuals=list(m.observations)
        video_hash="9"*64
        media[1]=MediaByteProof(media[1].asset_id,video_hash,500,
                                "video",2,2000)
        visuals[1]=VerifiedObservation(
            "sampled_frame",visuals[1].ref_sha256,
            sha256(IMAGE_BYTES).hexdigest(),video_hash,media[1].asset_id,
            "video_absolute",1000,1000,2,None
        )
        m=replace(m,assets=tuple(media),observations=tuple(visuals))
        m=replace(m,manifest_sha256=canonical_hash(m.payload()))
        refs=tuple(v.ref_sha256 for v in m.observations)
        result=self.check(
            manifest=m,p=packet(m),raw=labels(refs),
            delivered=refs,receipt=Receipt(refs)
        )
        self.assertTrue(result.can_persist)
        self.assertEqual(result.db_fields()["logo_visible"],True)
        bad=replace(m,observations=tuple(
            replace(x,start_ms=2500,end_ms=2500) if x.kind=="sampled_frame"
            else x for x in m.observations
        ))
        bad=replace(bad,manifest_sha256=canonical_hash(bad.payload()))
        with self.assertRaises(B4VisualError):
            self.check(manifest=bad,p=packet(bad),raw=labels(refs),
                       delivered=refs,receipt=Receipt(refs))

    def test_unsent_strict_vision_request_requires_real_sha_pinned_image_bytes(self):
        model=ModelDescriptor(
            "fixture/vision","f"*64,32000,1024,
            frozenset(("text","image")),frozenset(("text",)),
            frozenset(("structured_outputs",)),
            Decimal("0.000001"),Decimal("0.000002"),
            Decimal("0.001"),Decimal("0.00001"),
        )
        catalog=ModelCatalogSnapshot((model,),"f"*64,"offline",True)
        with self.assertRaises(ProviderFailure):
            prepare_b4_request(catalog=catalog,model=model.slug,
                               packet=self.p,max_output_tokens=512)
        attachments=tuple(VerifiedImage(r,"image/png",IMAGE_BYTES) for r in self.refs)
        planned=prepare_b4_request(
            catalog=catalog,model=model.slug,packet=self.p,
            max_output_tokens=512,attachments=attachments
        )
        self.assertTrue(planned.verify_digest())
        self.assertIn("DO NOT classify",planned.body["messages"][0]["content"])
        self.assertTrue(planned.body["provider"]["require_parameters"])
        self.assertFalse(planned.body["stream"])
        self.assertEqual(planned.request_sha256,prepare_b4_request(
            catalog=catalog,model=model.slug,packet=self.p,
            max_output_tokens=512,attachments=attachments
        ).request_sha256)
        with self.assertRaises(ProviderFailure):
            prepare_b4_request(catalog=catalog,model=model.slug,
                packet=self.p,max_output_tokens=512,
                attachments=attachments[:-1]+(
                    VerifiedImage(self.refs[-1],"image/png",
                                  b"\x89PNG\r\n\x1a\nsubstituted"),
                ))

    def test_module_no_provider_post_and_no_sql_writes(self):
        import inspect
        import intelligence.group_b.blocks.b4_visual as impl
        data=inspect.getsource(impl)
        self.assertNotIn("post_json(",data)
        self.assertNotIn("INSERT INTO",data)


if __name__=="__main__":
    unittest.main()
