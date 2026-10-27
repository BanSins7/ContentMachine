"""GA13 sparse text tracking gold fixtures, gap truth and OCR integration."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import unittest

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.extractors.text_tracking import (
    TrackConfig, TextTrackingError, track_screen_text,
)

try:
    import jsonschema
except ImportError:
    jsonschema=None

PROJECT="a"*32
RUN="11111111-2222-4333-8444-555555555555"


def wrd(text, x=20, y=65, width=100, height=22, line="l000001",confidence="91.50"):
    return {"text":text,"confidence_pct":confidence,"line_ref":line,
            "bbox":{"x":x,"y":y,"width":width,"height":height}}


def framespec(time,index,words=(),state=None,width=600,height=200):
    status=state or ("text_observed" if words else "no_text_detected")
    words=list(words)
    status_text=("text_observed" if words else
        "no_text_detected_on_this_frame_only" if status=="no_text_detected"
        else "unknown")
    if status in ("failed","unavailable","skipped_budget"):
        words=[];status_text="unknown"
    return {"sample_index":index,
            "observed_sec":time,"width":width,"height":height,
            "state":status,"text_status":status_text,
            "words":words,"word_count":len(words) if status in (
                "text_observed","no_text_detected","partial_language") else None,
            "languages_used":["eng","rus"] if status in ("text_observed","no_text_detected") else
                ["eng"] if status=="partial_language" else [],
            "engine_rotation":0 if status in (
                "text_observed","no_text_detected","partial_language") else None,
            "reason_code":None if status in ("text_observed","no_text_detected",
                "partial_language") else "ocr_fixture_unavailable",
            "mean_engine_confidence_pct":"91.50" if words else None,
            "frame_sha256":f"{index:064x}",
            "frame_ref":f"ga-frame:f{index:03d}-{index:064x}",
        }


def fixture(frames,*,project=PROJECT,run=RUN):
    source_frames=[]
    for f in frames:
        source_frames.append({
            "sample_index":f["sample_index"],"observed_sec":f["observed_sec"],
            "width":f["width"],"height":f["height"],
            "frame_ref":f["frame_ref"],"png_sha256":f["frame_sha256"],
            "requested_sec":f["observed_sec"],
            "observed_pts_ticks":str(int(float(f["observed_sec"])*1000)),
            "png_byte_size":100,
            "perceptual_hash":"0"*16,
        })
    manifest={
        "schema_version":"group-a-frame-manifest/v1",
        "method_version":"group-a-frame-sampling-v1",
        "source_media_sha256":"b"*64,"source_byte_size":999999,
        "source_duration_sec":"10.000",
        "source_stream_index":0,"source_timebase":"1/1000",
        "config":{"budget":len(frames),"max_edge":512,"early_focus":3,
                  "duplicate_hamming":5,"duplicate_color_l1":24},
        "coverage":{"type":"sparse_observations",
            "visual_temporal_coverage":"unknown",
            "ocr_temporal_coverage":"unknown",
            "requested":len(frames),"retained":len(frames),
            "duplicates_suppressed":0,"not_observed":0,
            "sampled_span_sec":f"{float(frames[-1]['observed_sec'])-float(frames[0]['observed_sec']):.3f}",
            "unsampled_intervals_present":True},
        "frames":source_frames,
    }
    manifest["manifest_sha256"]=canonical_sha256(manifest)
    counts={
        "attempted":sum(f["state"]!="skipped_budget" for f in frames),
        "observed":sum(f["state"] in (
            "text_observed","no_text_detected","partial_language") for f in frames),
        "failed":sum(f["state"]=="failed" for f in frames),
        "unavailable":sum(f["state"]=="unavailable" for f in frames),
        "skipped_budget":sum(f["state"]=="skipped_budget" for f in frames),
        "partial_language":sum(f["state"]=="partial_language" for f in frames),
        "text_observed":sum(f["state"]=="text_observed" for f in frames),
        "no_text_detected":sum(f["state"]=="no_text_detected" for f in frames),
        "cached":0,
    }
    ocr={
        "schema_version":"ga-frame-ocr/v1",
        "method_version":"group-a-local-frame-ocr-v1",
        "source_media_sha256":"b"*64,
        "frame_manifest_sha256":manifest["manifest_sha256"],
        "project_id":project,"run_id":run,
        "state":"partial" if counts["observed"] else "unavailable",
        "temporal_coverage":"unknown",
        "screen_text_absence":"unknown",
        "unsampled_intervals_present":True,
        "model":{"engine":"tesseract","engine_version":"5.5.0",
                 "model_sha256":"c"*64,"device":"cpu"},
        "config":{"languages":["eng","rus"],"frame_budget":len(frames),
                  "timeout_sec":15,"batch_timeout_sec":150,
                  "scale":2,"psm":6,"rotations":[0],"preprocess":"none",
                  "missing_language":"fail","cpu_threads":1,"device":"cpu"},
        "counts":counts,"frames":frames,
    }
    ocr["observation_sha256"]=canonical_sha256(ocr)
    return ocr,manifest


def track(rows,*,config=None,allow=lambda _:True):
    obs,manifest=fixture(rows)
    return track_screen_text(obs,manifest,config=config,verify_current=allow)


class GA13Gold(unittest.TestCase):
    def test_identical_caption_three_frames_one_occurrence(self):
        rows=[framespec("0.100",1,[wrd("Hello",x=20,width=85),
                                    wrd("world",x=115,width=90)]),
              framespec("0.500",2,[wrd("Hello",x=21,width=85),
                                    wrd("world",x=116,width=90)]),
              framespec("0.900",3,[wrd("Hello",x=22,width=85),
                                    wrd("world",x=117,width=90)])]
        got=track(rows)
        self.assertEqual(len(got["tracks"]),1)
        line=got["tracks"][0]
        self.assertEqual(line["observation_count"],3)
        self.assertEqual(line["word_count"],2)
        self.assertEqual(got["sampled_screen_word_count"],2)
        self.assertIsNone(got["video_wide_screen_word_count"])
        self.assertEqual(got["first_text_observed_sec"],"0.100")
        self.assertIsNone(got["first_text_true_onset_sec"])
        self.assertEqual(line["sampled_span_sec"],"0.800")
        self.assertFalse(line["continuity_verified"])
        self.assertEqual(line["time_semantics"],"observed_instants_only")
        self.assertEqual(got["a003_screen_text_projection"],[])
        self.assertEqual(got["a003_publication_state"],"blocked_unverified_continuity")
        self.assertFalse(got["continuous_intervals_verified"])
        self.assertEqual(got["temporal_coverage"],"unknown")
        self.assertEqual(got["full_video_text_absence"],"unknown")
        self.assertEqual(got["observation_sha256"],canonical_sha256(
            {k:v for k,v in got.items() if k!="observation_sha256"}))

    def test_flash_one_frame_never_has_positive_duration(self):
        rows=[framespec("0.100",1),framespec("0.200",2,[wrd("FLASH")]),
              framespec("0.300",3)]
        got=track(rows)
        self.assertEqual(len(got["tracks"]),1)
        row=got["tracks"][0]
        self.assertEqual(row["first_observed_sec"],"0.200")
        self.assertEqual(row["last_observed_sec"],"0.200")
        self.assertEqual(row["sampled_span_sec"],"0.000")
        self.assertEqual(row["closure_reason"],"absent_in_sampled_frame")
        self.assertEqual(got["a003_screen_text_projection"],[])

    def test_reappearance_after_observed_blank_is_new_occurrence(self):
        rows=[framespec("0.100",1,[wrd("START")]),
              framespec("0.500",2),
              framespec("0.900",3,[wrd("START")])]
        out=track(rows)
        self.assertEqual(len(out["tracks"]),2)
        self.assertEqual(out["sampled_screen_word_count"],2)
        self.assertEqual(out["counts"]["distinct_occurrences"],2)
        self.assertEqual(out["tracks"][0]["closure_reason"],"absent_in_sampled_frame")
        self.assertEqual(out["tracks"][1]["closure_reason"],"video_sampling_ended")

    def test_unknown_frame_splits_track_but_does_not_claim_absence(self):
        for state in ("failed","unavailable","skipped_budget"):
            with self.subTest(state=state):
                rows=[framespec("0.100",1,[wrd("SUBTITLE")]),
                      framespec("0.300",2,state=state),
                      framespec("0.500",3,[wrd("SUBTITLE")])]
                result=track(rows)
                self.assertEqual(len(result["tracks"]),2)
                self.assertEqual(result["tracks"][0]["closure_reason"],"ocr_gap_unknown")
                self.assertEqual(result["full_video_text_absence"],"unknown")

    def test_long_sparse_gap_forbids_continuous_track(self):
        rows=[framespec("0.100",1,[wrd("HEADLINE")]),
              framespec("4.000",2,[wrd("HEADLINE")])]
        result=track(rows)
        self.assertEqual(len(result["tracks"]),2)
        self.assertEqual(result["tracks"][0]["closure_reason"],"sample_gap_exceeded")
        self.assertEqual(result["sampled_screen_word_count"],2)
        shorter=track(rows,config=TrackConfig(max_gap_sec="5.000"))
        self.assertEqual(len(shorter["tracks"]),1)
        self.assertEqual(shorter["tracks"][0]["intervening_coverage"],"unknown")
        self.assertEqual(shorter["a003_screen_text_projection"],[])

    def test_two_simultaneous_matching_lines_remain_distinct(self):
        rows=[
            framespec("0.100",1,[wrd("HELLO",x=20,y=20,line="l000001"),
                                  wrd("HELLO",x=20,y=145,line="l000002")]),
            framespec("0.500",2,[wrd("HELLO",x=22,y=21,line="l000001"),
                                  wrd("HELLO",x=22,y=146,line="l000002")]),
        ]
        result=track(rows)
        self.assertEqual(len(result["tracks"]),2)
        self.assertEqual([t["observation_count"] for t in result["tracks"]],[2,2])
        self.assertEqual(result["sampled_screen_word_count"],2)

    def test_same_frame_duplicate_near_identical_boxes_deduped(self):
        line1=wrd("HELLO",x=20,y=65,line="l000001")
        line2=wrd("HELLO",x=21,y=65,line="l000002")
        result=track([framespec("0.100",1,[line1,line2])])
        self.assertEqual(len(result["tracks"]),1)
        self.assertEqual(result["counts"]["duplicate_lines_suppressed"],1)
        self.assertEqual(result["sampled_screen_word_count"],1)

    def test_moving_overlay_fade_and_minor_ocr_edit_distance(self):
        rows=[framespec("0.100",1,[wrd("Subscribe now",x=30,y=80,width=190)]),
              framespec("0.400",2,[wrd("Subscrlbe now",x=41,y=82,width=190)]),
              framespec("0.800",3,[wrd("Subscribe now",x=51,y=84,width=190)])]
        result=track(rows)
        self.assertEqual(len(result["tracks"]),1)
        self.assertEqual(result["tracks"][0]["observation_count"],3)
        self.assertEqual(result["tracks"][0]["text"],"Subscribe now")
        self.assertEqual(result["tracks"][0]["word_count"],2)

    def test_different_short_words_are_not_fuzzy_merged(self):
        rows=[framespec("0.100",1,[wrd("YES")]),
              framespec("0.300",2,[wrd("YET")])]
        result=track(rows)
        self.assertEqual(len(result["tracks"]),2)
        self.assertEqual(result["sampled_screen_word_count"],2)

    def test_ru_en_unicode_punctuation_and_line_order(self):
        rows=[framespec("0.100",1,[
            wrd("мир!",x=140,width=85),
            wrd("Привет,",x=20,width=110),
            wrd("HELLO",x=25,y=130,line="l000002"),
        ])]
        result=track(rows)
        self.assertEqual(len(result["tracks"]),2)
        self.assertEqual(result["tracks"][0]["text"],"Привет, мир!")
        self.assertEqual(result["tracks"][0]["normalized_text"],"привет мир")
        self.assertEqual(result["sampled_screen_word_count"],3)

    def test_empty_and_partial_language_never_verify_absence(self):
        rows=[framespec("0.100",1),
              framespec("0.300",2,state="partial_language")]
        result=track(rows)
        self.assertEqual(result["status"],"partial")
        self.assertEqual(result["tracks"],[])
        self.assertIsNone(result["first_text_observed_sec"])
        self.assertIsNone(result["sampled_screen_word_count"])
        self.assertEqual(result["full_video_text_absence"],"unknown")
        rows=[framespec("0.100",1,state="failed")]
        result=track(rows)
        self.assertEqual(result["status"],"unavailable")

    def test_reject_tampered_ocr_digest_even_with_matching_png_ref(self):
        frames=[framespec("0.100",1,[wrd("Hello")])]
        ocr,manifest=fixture(frames)
        bad=deepcopy(ocr)
        bad["frames"][0]["words"][0]["text"]="INJECTED"
        with self.assertRaises(TextTrackingError) as ctx:
            track_screen_text(bad,manifest,verify_current=lambda _:True)
        self.assertEqual(ctx.exception.code,"ocr_digest_mismatch")
        bad=deepcopy(manifest);bad["source_media_sha256"]="f"*64
        with self.assertRaises(TextTrackingError):
            track_screen_text(ocr,bad,verify_current=lambda _:True)

    def test_reject_foreign_frame_wrong_timestamp_and_bad_source_truth(self):
        frames=[framespec("0.100",1,[wrd("Hello")]),
                framespec("0.500",2,[wrd("World")])]
        ocr,manifest=fixture(frames)
        forged=deepcopy(ocr);forged["frames"][0]["frame_sha256"]="f"*64
        forged["observation_sha256"]=canonical_sha256(
            {k:v for k,v in forged.items() if k!="observation_sha256"})
        with self.assertRaises(TextTrackingError) as ctx:
            track_screen_text(forged,manifest,verify_current=lambda _:True)
        self.assertEqual(ctx.exception.code,"ocr_frame_identity_mismatch")
        forged=deepcopy(ocr);forged["frames"][1]["observed_sec"]="0.100"
        forged["observation_sha256"]=canonical_sha256(
            {k:v for k,v in forged.items() if k!="observation_sha256"})
        with self.assertRaises(TextTrackingError):
            track_screen_text(forged,manifest,verify_current=lambda _:True)
        forged=deepcopy(ocr);forged["temporal_coverage"]="complete"
        forged["observation_sha256"]=canonical_sha256(
            {k:v for k,v in forged.items() if k!="observation_sha256"})
        with self.assertRaises(TextTrackingError):
            track_screen_text(forged,manifest,verify_current=lambda _:True)

    def test_current_project_rights_are_mandatory_every_time(self):
        frames=[framespec("0.100",1,[wrd("Text")])]
        ocr,manifest=fixture(frames)
        for verifier in (None,lambda _:False,lambda _:"true"):
            with self.assertRaises(TextTrackingError) as ctx:
                track_screen_text(ocr,manifest,verify_current=verifier)
            self.assertEqual(ctx.exception.code,"tracking_current_rights_unverified")
        verified=[]
        def auth(c):
            verified.append(c);return True
        track_screen_text(ocr,manifest,verify_current=auth)
        self.assertEqual(verified[0]["project_id"],PROJECT)
        self.assertEqual(verified[0]["frame_manifest_sha256"],
                         manifest["manifest_sha256"])

    def test_budget_and_input_validation(self):
        with self.assertRaises(TextTrackingError):
            TrackConfig(similarity_min="NaN")
        with self.assertRaises(TextTrackingError):
            TrackConfig(max_gap_sec="1000")
        frames=[framespec("0.100",1,[wrd("A",line="l000001"),
                                     wrd("B",line="l000002")])]
        ocr,manifest=fixture(frames)
        with self.assertRaises(TextTrackingError) as ctx:
            track_screen_text(ocr,manifest,
                config=TrackConfig(max_tracked_lines=1),
                verify_current=lambda _:True)
        self.assertEqual(ctx.exception.code,"tracking_line_budget_exceeded")

    def test_sha_determinism(self):
        rows=[framespec("0.100",1,[wrd("Repeat")]),
              framespec("0.400",2,[wrd("Repeat",x=21)])]
        self.assertEqual(track(rows),track(rows))


@unittest.skipUnless(jsonschema is not None,"jsonschema test dependency missing")
class GA13Schemas(unittest.TestCase):
    def test_strict_wire_unknown_and_projection(self):
        p=Path(__file__).resolve().parents[1]/"intelligence"/"group_a"/"schemas"/"ga-screen-text-tracking-v1.json"
        document=json.loads(p.read_text("utf-8"))
        jsonschema.Draft202012Validator.check_schema(document)
        candidates=[
            track([framespec("0.100",1,[wrd("Hello")]),
                   framespec("0.300",2,[wrd("Hello")])]),
            track([framespec("0.100",1)]),
            track([framespec("0.100",1,state="failed")]),
            track([framespec("0.100",1,[wrd("Привет",line="l000001"),
                                       wrd("HELLO",y=130,line="l000002")])]),
        ]
        for result in candidates:
            jsonschema.validate(result,document)
        forged=dict(candidates[0],continuous_intervals_verified=True)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,document)
        forged=dict(candidates[0],a003_screen_text_projection=[
            {"start_sec":"0.100","end_sec":"0.300","screen_text":"Hello"}])
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,document)


class GA13Integration(unittest.TestCase):
    def test_actual_ga12_ocr_to_ga13_same_frame_lineage(self):
        try:
            from test_group_a_ocr import make_batch,png,FakeBackend,output,recognized
            from intelligence.group_a.extractors.ocr import analyze_frame_ocr
        except ImportError as exc:
            self.skipTest("GA12 image/Pillow test fixture unavailable: "+str(exc))
        manifest,blobs=make_batch([
            png("HELLO WORLD"),png("HELLO WORLD"),png()],
            times=["0.100","0.400","0.700"])
        backend=FakeBackend([
            output([recognized("HELLO",x=20,width=90),
                    recognized("WORLD",x=120,width=100)]),
            output([recognized("HELLO",x=22,width=90),
                    recognized("WORLD",x=122,width=100)]),
            output([]),
        ])
        ocr=analyze_frame_ocr(manifest,blobs,backend=backend,
                               project_id=PROJECT,run_id=RUN)
        got=track_screen_text(ocr,manifest,verify_current=lambda _:True)
        self.assertEqual(len(got["tracks"]),1)
        self.assertEqual(got["sampled_screen_word_count"],2)
        self.assertEqual(got["tracks"][0]["observation_count"],2)
        self.assertEqual(got["tracks"][0]["closure_reason"],"absent_in_sampled_frame")
        self.assertEqual(got["a003_screen_text_projection"],[])


if __name__=="__main__":
    unittest.main()
