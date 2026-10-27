"""GB-MOD-19 source-first B7 positive, negative, tri-state and no-URL invention tests.

All receipts/full-media/slide OCR verifiers are SYNTHETIC test doubles.
No real provider, payment, crawl, original-media decode or SQL here.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from copy import deepcopy
import unittest

from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.context.builder import ContextPacket, _entry
from intelligence.group_b.context.evidence import VerifiedObservation
from intelligence.group_b.providers.protocols import VerifiedChatResponse
from intelligence.group_b.providers.openrouter import _validate_schema,_validate_json_value
from intelligence.group_b.blocks.b7_cta import (
    B7CTAError,CTA_TYPES,DESTINATIONS,SERVICES,
    validate_b7_cta,unknown_b7,
)
from intelligence.group_b.blocks.b7_schema import B7_OUTPUT_SCHEMA
from test_group_b_b2 import fixture

REQ="a"*64
CAPTION="Follow @creator on instagram."


def media(kind="video", with_text=True, repeat=False):
    m=fixture(content_kind=kind)
    if kind in ("video","slideshow_video") and with_text:
        original=m.observations
        extra=[
            VerifiedObservation(
                "transcript_segment","a"*64,sha256(CAPTION.encode()).hexdigest(),
                original[0].asset_sha256,original[0].media_asset_id,
                "video_absolute",0,1500,
            ),
            VerifiedObservation(
                "ocr_span","b"*64,sha256(CAPTION.encode()).hexdigest(),
                original[0].asset_sha256,original[0].media_asset_id,
                "video_absolute",0,1000,None,original[0].ref_sha256,
            ),
        ]
        if repeat:
            extra.append(VerifiedObservation(
                "transcript_segment","c"*64,sha256(CAPTION.encode()).hexdigest(),
                original[1].asset_sha256,original[1].media_asset_id,
                "video_absolute",15000,16500,
            ))
        m=replace(m,observations=m.observations+tuple(extra))
        m=replace(m,manifest_sha256=canonical_hash(m.payload()))
    return m


def context(m, omit=()):
    omissions=set(omit)
    relevant=[o for o in m.observations if o.kind in (
        "transcript_segment","ocr_span","sampled_frame","slide_ref")]
    selected=[o for o in relevant if o.ref_sha256 not in omissions]
    entries=[]
    for o in selected:
        entries.append(_entry(o,text_data=(CAPTION if o.kind in (
            "transcript_segment","ocr_span") else None)))
    removed=[{"kind":o.kind,"ref_sha256":o.ref_sha256,"reason":"observation_cap"}
             for o in relevant if o.ref_sha256 in omissions]
    payload={
        "schema_version":"group-b-context/v1","block":"B7",
        "project_id":m.project_id,"b_run_id":m.b_run_id,
        "video_id":m.video_id,"qdp_run_id":m.qdp_run_id,
        "ga_result_id":m.ga_result_id,
        "ga_result_sha256":m.ga_result_sha256,
        "source_policy_version_id":m.source_policy_version_id,
        "source_evidence_manifest_sha256":m.manifest_sha256,
        "input_content_kind":m.content_kind,
        "delivery_state":"not_sent",
        "media_delivery_state":"opaque_refs_not_attached",
        "entries":entries,"omitted":removed,
        "coverage":{
            "source_observations":len(m.observations),
            "relevant_observations":len(relevant),
            "selected_observations":len(selected),
            "omitted_relevant":len(removed),
            "selected_image_refs":sum(x.kind in ("sampled_frame","slide_ref")
                                       for x in selected),
            "available_frame_refs":sum(x.kind=="sampled_frame" for x in relevant),
            "available_slide_refs":sum(x.kind=="slide_ref" for x in relevant),
            "coverage_complete":not removed,
        },
    }
    return ContextPacket(payload,canonical_hash(payload),1200,2500)


def candidate(m, *, event=True, complete=True, kind="follow", carousel=False):
    rows=[]
    if event:
        if carousel:
            refs=[m.observations[0].ref_sha256]
            spoken=""
            screen="Visit my profile"
            text=screen
        else:
            refs=["a"*64,"b"*64,m.observations[0].ref_sha256]
            spoken=CAPTION
            screen=CAPTION
            text="Follow @creator"
        rows.append({
            "occurrence_index":1,"cta_type":kind,"text":text,
            "spoken_text":spoken,"screen_text":screen,
            "delivery":(["on_screen_text"] if carousel else
                        ["speech","on_screen_text"]),
            "evidence_refs":refs,
            "start_ms":0,"end_ms":0 if carousel else 1500,
            "slide_index":1 if carousel else 0,
            "destination_type":"profile",
            "destination_service":"" if carousel else "instagram",
            "destination_url":"","destination_handle":"" if carousel else "@creator",
            "destination_app_name":"","destination_keyword":"",
            "destination_other_desc":"",
            "confidence":"high",
        })
    return {"occurrences":rows,
            "coverage_claim":"complete" if complete else "partial",
            "confidence":"high"}


def response(raw):
    return VerifiedChatResponse(
        "fixture/vision",REQ,"b7-response-1",deepcopy(raw),
        {"prompt_tokens":100,"completion_tokens":50},"stop",
    )


class Receipt:
    def __init__(self,valid=True):
        self.valid=valid
        self.calls=0
    def verify_b7_response(self,answer,packet,images):
        self.calls+=1
        return (self.valid is True and answer.request_sha256==REQ and
                answer.response_id=="b7-response-1" and
                packet.verify_digest() and type(images) is tuple)


class Full:
    def __init__(self,valid=True):
        self.valid=valid
        self.calls=0
    def verify_b7_full_scan(self,manifest,packet,response_value):
        self.calls+=1
        return self.valid is True and manifest.verify_digest() and packet.verify_digest()


class Slide:
    def __init__(self,valid=True):
        self.valid=valid
    def verify_b7_slide_quote(self,manifest,slide,quote):
        return self.valid is True and slide.kind=="slide_ref" and quote=="Visit my profile"


class TestB7(unittest.TestCase):
    def setUp(self):
        self.m=media()
        self.p=context(self.m)
        self.c=candidate(self.m)

    def check(self,*,m=None,p=None,c=None,receipt=None,scan=None,
              images=None,slide=None):
        m=self.m if m is None else m
        p=self.p if p is None else p
        c=self.c if c is None else c
        if images is None:
            images=tuple(o.ref_sha256 for o in m.observations
                         if o.ref_sha256 in {x["ref_sha256"] for x in p.payload["entries"]}
                         and o.kind in ("sampled_frame","slide_ref"))
        return validate_b7_cta(
            manifest=m,packet=p,response=response(c),
            receipt_verifier=Receipt() if receipt is None else receipt,
            delivered_image_refs=images,
            full_scan_verifier=Full() if scan is None else scan,
            slide_quote_verifier=slide,
        )

    def deny(self,**kwargs):
        with self.assertRaises(B7CTAError):
            self.check(**kwargs)

    def test_exact_sql005_enums_and_strict_openrouter_schema(self):
        self.assertEqual(len(CTA_TYPES),12)
        self.assertEqual(len(DESTINATIONS),9)
        self.assertEqual(len(SERVICES),8)
        _validate_schema(B7_OUTPUT_SCHEMA)
        _validate_json_value(B7_OUTPUT_SCHEMA,self.c)
        self.assertEqual(set(B7_OUTPUT_SCHEMA["properties"]),
                         {"occurrences","coverage_claim","confidence"})
        self.assertNotIn("has_cta",B7_OUTPUT_SCHEMA["properties"])

    def test_one_multichannel_source_verbatim_cta_and_sql_fields(self):
        out=self.check()
        self.assertTrue(out.verify_digest())
        self.assertTrue(out.can_persist)
        self.assertTrue(out.full_scan_attested)
        self.assertEqual(out.analysis_db_fields()["analysis_status"],"complete")
        rows=out.occurrence_db_rows()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["cta_type"],"follow")
        self.assertEqual(rows[0]["delivery"],["speech","on_screen_text"])
        self.assertEqual(rows[0]["start_sec"],0)
        self.assertEqual(rows[0]["end_sec"],1.5)
        self.assertIsNone(rows[0]["slide_index"])
        self.assertEqual(rows[0]["destination_handle"],"@creator")

    def test_zero_cta_false_only_after_authenticated_whole_media(self):
        m=media(with_text=False)
        p=context(m)
        raw=candidate(m,event=False)
        complete=self.check(m=m,p=p,c=raw)
        self.assertTrue(complete.can_persist)
        self.assertEqual(complete.analysis_db_fields()["analysis_status"],"complete")
        self.assertEqual(complete.occurrence_db_rows(),())
        no_scan=self.check(m=m,p=p,c=raw,scan=Full(False))
        self.assertTrue(no_scan.can_persist)
        self.assertFalse(no_scan.full_scan_attested)
        self.assertEqual(no_scan.analysis_db_fields()["analysis_status"],"partial")
        self.assertIn("cta_original_full_scan_unverified",
                      no_scan.payload["review_reasons"])
        absent=unknown_b7(manifest=m,reason="full_video_missing")
        self.assertFalse(absent.can_persist)
        self.assertEqual(absent.payload["analysis_status"],"not_run")

    def test_zero_cta_partial_claim_or_omitted_frame_never_false(self):
        m=media(with_text=False)
        raw=candidate(m,event=False,complete=False)
        no=self.check(m=m,p=context(m),c=raw)
        self.assertEqual(no.analysis_db_fields()["analysis_status"],"partial")
        omitted=[m.observations[-1].ref_sha256]
        partial=self.check(m=m,p=context(m,omit=omitted),
                           c=candidate(m,event=False))
        self.assertEqual(partial.analysis_db_fields()["analysis_status"],"partial")
        self.assertIn("cta_source_selection_incomplete",partial.payload["review_reasons"])

    def test_repeated_identical_cta_preserved_at_two_positions(self):
        m=media(repeat=True)
        p=context(m)
        raw=candidate(m)
        second=deepcopy(raw["occurrences"][0])
        second.update({
            "occurrence_index":2,"spoken_text":CAPTION,
            "screen_text":"","delivery":["speech"],
            "evidence_refs":["c"*64],
            "start_ms":15000,"end_ms":16500,
        })
        raw["occurrences"].append(second)
        out=self.check(m=m,p=p,c=raw)
        self.assertEqual(len(out.occurrence_db_rows()),2)
        self.assertEqual(out.occurrence_db_rows()[1]["start_sec"],15)
        second["start_ms"]=0
        second["end_ms"]=500
        raw["occurrences"][1]=second
        self.deny(m=m,p=p,c=raw)

    def test_carousel_still_image_text_requires_independent_quote(self):
        m=media(kind="photo_carousel",with_text=False)
        p=context(m)
        raw=candidate(m,carousel=True)
        out=self.check(m=m,p=p,c=raw,slide=Slide())
        row=out.occurrence_db_rows()[0]
        self.assertEqual(row["slide_index"],1)
        self.assertIsNone(row["start_sec"])
        self.deny(m=m,p=p,c=raw,slide=Slide(False))
        self.deny(m=m,p=p,c=raw)
        raw["occurrences"][0]["slide_index"]=2
        self.deny(m=m,p=p,c=raw,slide=Slide())

    def test_invalid_scheme_url_fabricated_handle_service_destination(self):
        for field,value in (
            ("destination_handle","@madeup"),
            ("destination_handle","creator"),
            ("destination_url","https://not-there.test/"),
            ("destination_url","javascript:alert(1)"),
            ("destination_service","telegram"),
            ("destination_app_name","ghost_app"),
            ("destination_keyword","secret_word"),
            ("destination_other_desc","phantom"),
        ):
            raw=deepcopy(self.c)
            raw["occurrences"][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(B7CTAError):
                self.check(c=raw)

    def test_disallowed_cross_channel_quotes_and_ref_substitution(self):
        for key,value in (
            ("spoken_text","this line never appeared"),
            ("screen_text","this line never appeared"),
            ("text","Purchase now"),
            ("cta_type","unsupported_cta"),
            ("destination_type","external"),
            ("start_ms",59000),
            ("slide_index",3),
            ("evidence_refs",["f"*64]),
            ("delivery",["on_screen_text","speech"]),
        ):
            raw=deepcopy(self.c)
            raw["occurrences"][0][key]=value
            with self.subTest(key=key),self.assertRaises(B7CTAError):
                self.check(c=raw)

    def test_source_ocr_hash_forged_context_attestation_fail_closed(self):
        packet=deepcopy(self.p)
        packet.payload["entries"][-1]["untrusted_text"]="malicious insertion"
        packet=replace(packet,packet_sha256=canonical_hash(packet.payload))
        self.deny(p=packet)
        p=deepcopy(self.p)
        p.payload["coverage"]["available_frame_refs"]=999
        p=replace(p,packet_sha256=canonical_hash(p.payload))
        self.deny(p=p)

    def test_missing_provider_receipt_or_image_delivery_rejected(self):
        self.deny(receipt=object())
        self.deny(receipt=Receipt(False))
        self.deny(images=())
        self.deny(images=("f"*64,))

    def test_partial_low_confidence_and_unknown_destination(self):
        raw=deepcopy(self.c)
        raw["occurrences"][0]["confidence"]="low"
        out=self.check(c=raw)
        self.assertEqual(out.analysis_db_fields()["analysis_status"],"partial")
        self.assertEqual(out.occurrence_db_rows()[0]["extraction_status"],
                         "needs_review")
        raw=deepcopy(self.c)
        raw["occurrences"][0]["destination_type"]="unknown"
        raw["occurrences"][0]["destination_service"]=""
        raw["occurrences"][0]["destination_handle"]=""
        out=self.check(c=raw)
        self.assertEqual(out.analysis_db_fields()["analysis_status"],"partial")


if __name__=="__main__":
    unittest.main()
