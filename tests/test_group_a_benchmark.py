"""GA22 pure metric regressions + rights-clear real generated media/ffprobe smoke.

A passing synthetic score NEVER establishes independent real-world ASR/OCR
gold or end-to-end 10k/month capacity. No model weights or network calls.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from intelligence.group_a.benchmark_metrics import (
    BenchmarkError,edit_distance,text_error,score_asr,
    scene_boundaries,subtitle_truth,ocr_recall,event_intervals,
)
from intelligence.group_a.benchmark_fixtures import (
    validate_gold_definition,generate_synthetic_corpus,
    verify_generated_manifest,
)
from intelligence.group_a.benchmark_runner import (
    evaluate_predictions,planning_estimate_10k,
    load_calibration,profile_probe,build_report,canonical_sha,
)

ROOT=Path(__file__).resolve().parents[1]
GOLD=ROOT/"tests/fixtures/group_a_benchmark_gold_v1.json"
CAL=ROOT/"tests/fixtures/group_a_benchmark_calibration_v1.json"


class GA22MetricContracts(unittest.TestCase):
    def test_perfect_scene_and_tolerance_no_double_count(self):
        perfect=scene_boundaries([1,2,4],[1.05,2.05,4.10],tolerance_sec=".25")
        self.assertEqual(perfect["tp"],3)
        self.assertEqual(perfect["fp"],0)
        self.assertEqual(perfect["recall"],1.0)
        cases=scene_boundaries([2,2.20],[2.1],tolerance_sec=".15")
        self.assertEqual((cases["tp"],cases["fp"],cases["fn"]),(1,0,1))
        with self.assertRaisesRegex(BenchmarkError,"scene_points_not_strictly_increasing"):
            scene_boundaries([2,1],[1,2])
        with self.assertRaisesRegex(BenchmarkError,"scene_points_not_strictly_increasing"):
            scene_boundaries([1,1],[1])

    def test_wer_and_cer_use_unicode_ru_en_and_do_not_invent_empty_rates(self):
        en=score_asr("The quick fox","the quick box")
        self.assertEqual(en["wer"]["edit_errors"],1)
        self.assertAlmostEqual(en["wer"]["error_rate"],1/3,places=5)
        ru=score_asr("Привет, мир!","привет, мир!")
        self.assertEqual(ru["cer"]["edit_errors"],0)
        self.assertEqual(ru["wer"]["edit_errors"],0)
        empty=text_error("","fabricated speech",mode="wer")
        self.assertIsNone(empty["error_rate"])
        self.assertGreater(empty["edit_errors"],0)
        self.assertTrue(empty["empty_reference"])
        self.assertEqual(edit_distance(["a","b"],["a","c"]),1)
        with self.assertRaises(BenchmarkError):
            text_error("x"*15001,"",mode="cer")

    def test_ocr_line_recall_one_to_one_with_utf8(self):
        score=ocr_recall(
            ["ПРОВЕРКА РУССКОГО ТЕКСТА","ORIGINAL ENGLISH TEXT"],
            ["проверка русского текста","original english text"]
        )
        self.assertEqual((score["tp"],score["fp"],score["fn"]),(2,0,0))
        one=ocr_recall(["ABCD","ABCD"],["ABCD"])
        self.assertEqual((one["tp"],one["fn"]),(1,1))
        absent=ocr_recall([],[])
        self.assertIsNone(absent["recall"])
        with self.assertRaises(BenchmarkError):
            ocr_recall([""],[""])

    def test_music_sfx_iou_bipartite_and_multilabel(self):
        gold=[
            {"label":"music","start_sec":0,"end_sec":2},
            {"label":"sound_effect","start_sec":1,"end_sec":1.4},
        ]
        predictions=[
            {"label":"sound_effect","start_sec":1.05,"end_sec":1.35},
            {"label":"music","start_sec":.1,"end_sec":2.1},
            {"label":"other","start_sec":1,"end_sec":2},
        ]
        r=event_intervals(gold,predictions,min_iou=".5")
        self.assertEqual(r["micro"]["tp"],2)
        self.assertEqual(r["micro"]["fp"],1)
        self.assertEqual(r["classes"]["music"]["tp"],1)
        self.assertEqual(r["classes"]["sound_effect"]["tp"],1)
        with self.assertRaises(BenchmarkError):
            event_intervals(gold,[{"label":"unknown","start_sec":1,"end_sec":2}])
        with self.assertRaises(BenchmarkError):
            event_intervals(gold,[{"label":"music","start_sec":3,"end_sec":2}])

    def test_subtitle_unknown_abstention_and_true_negative_are_separate(self):
        result=subtitle_truth(
            ["true","false","true","false","unknown"],
            ["true","false","unknown","true","unknown"]
        )
        self.assertEqual((result["tp"],result["fp"],result["fn"],result["tn"]),(1,1,1,1))
        self.assertEqual(result["abstained_positive"],1)
        self.assertEqual(result["gold_unknown"],1)
        self.assertEqual(result["eligible_gold_cases"],4)
        self.assertAlmostEqual(result["coverage"],.75)
        self.assertEqual(subtitle_truth(["unknown"],["true"])["eligible_gold_cases"],0)
        with self.assertRaises(BenchmarkError):
            subtitle_truth(["true"],[])

    def test_strict_gold_and_uncalibrated_release(self):
        gold=validate_gold_definition(GOLD)
        self.assertEqual(gold["schema_version"],"ga22-synthetic-gold/v1")
        self.assertGreaterEqual(len(gold["cases"]),7)
        cal=load_calibration(CAL)
        self.assertFalse(cal["independent_review_verified"])
        self.assertTrue(all(v is None for v in cal["thresholds"].values()))
        self.assertIn("speech",gold["speech_policy"].lower())

    def test_10k_month_is_scenario_never_observed(self):
        empty=planning_estimate_10k(
            full_pipeline_media_seconds=None,worker_slots=1
        )
        self.assertEqual(empty["status"],"unavailable_no_measured_full_pipeline")
        self.assertIsNone(empty["estimated_workers_required"])
        model=planning_estimate_10k(
            full_pipeline_media_seconds=120,worker_slots=2,utilization=.65
        )
        self.assertEqual(model["status"],"hypothetical_model_only_not_observed")
        self.assertFalse(model["observed_capacity_claim"])
        self.assertGreater(model["estimated_assets_per_30d"],10000)
        with self.assertRaises(BenchmarkError):
            planning_estimate_10k(
                full_pipeline_media_seconds=0,worker_slots=4
            )


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg/FFprobe local media fixture dependencies absent")
class GA22GeneratedFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import PIL
            from PIL import ImageFont
            ImageFont.truetype("DejaVuSans.ttf",30)
        except (ImportError,OSError):
            raise unittest.SkipTest("Pillow/DejaVu Sans license-safe local fixture prerequisites missing")
        cls.tmp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root=Path(cls.tmp.name)
        cls.manifest=generate_synthetic_corpus(gold_path=GOLD,output_dir=cls.root)
        cls.cases={case["id"]:case for case in cls.manifest["cases"]}

    def test_sha_pins_media_and_expected_categories(self):
        self.assertTrue(verify_generated_manifest(self.root,self.manifest,GOLD))
        self.assertEqual(self.cases["ru_image"]["expected"]["asr_reference"],None)
        self.assertEqual(self.cases["en_image"]["kind"],"image")
        self.assertIn("mixed_carousel",self.cases)
        self.assertIn("photo_carousel",self.cases)
        self.assertIn("noisy",self.cases["noise_video"]["tags"])
        self.assertIn("dynamic_subtitles",self.cases["dynamic_caption_video"]["tags"])

    def test_hash_corruption_fails_before_any_reporting(self):
        forged=deepcopy(self.manifest)
        forged["cases"][0]["assets"][0]["sha256"]="f"*64
        with self.assertRaisesRegex(BenchmarkError,"corpus_asset_sha_mismatch"):
            verify_generated_manifest(self.root,forged,GOLD)

    def test_real_ffprobe_three_repetitions_do_not_claim_full_pipeline(self):
        out=profile_probe(self.root,self.manifest,repetitions=3)
        self.assertEqual(out["method"],"measured_ffprobe_only_not_full_pipeline")
        self.assertTrue(out["summary"]["invocations"]>=12)
        self.assertIsNone(out["summary"]["p95_wall_sec"])
        self.assertFalse(out["summary"]["full_pipeline_coverage"])
        self.assertIsNone(out["summary"]["gpu_peak_bytes"])
        self.assertTrue(all(r["duration_sec"]>=1 for r in out["samples"]))
        self.assertTrue(all(r["wall_sec"]>=0 for r in out["samples"]))

    def test_predictions_hashed_per_asset_no_asr_gold_invented(self):
        video=self.cases["dynamic_caption_video"]
        case={
            "id":video["id"],
            "asset_sha256":video["assets"][0]["sha256"],
            "engine":{
                "name":"synthetic_fixture_test_no_model",
                "version":"unit-v1","model_sha256":"a"*64,
            },
            "predictions":{
                "scene_boundaries_sec":[2.02],
                "subtitle_presence":"true",
                "ocr_lines":["ORIGINAL ENGLISH TEXT","ПРОВЕРКА РУССКОГО ТЕКСТА"],
                "asr_transcript":"invented words MUST NOT produce WER",
                "audio_events":[],
            },
        }
        submitted={"schema_version":"ga22-predictions/v1","cases":[case]}
        scores=evaluate_predictions(self.manifest,submitted)
        self.assertFalse(scores["human_real_media_accuracy_certified"])
        self.assertFalse(scores["all_prediction_origins_verified"])
        self.assertEqual(scores["results"][0]["metrics"]["scene"]["tp"],1)
        self.assertEqual(scores["results"][0]["metrics"]["asr"]["status"],
                         "not_scored_no_real_speech_gold")
        checked=evaluate_predictions(
            self.manifest,submitted,verify_prediction=lambda _:True
        )
        self.assertTrue(checked["all_prediction_origins_verified"])
        self.assertFalse(checked["human_real_media_accuracy_certified"])
        forged=deepcopy(submitted)
        forged["cases"][0]["asset_sha256"]="e"*64
        with self.assertRaisesRegex(BenchmarkError,"prediction_asset_sha_mismatch"):
            evaluate_predictions(self.manifest,forged)

    def test_full_report_stays_qualified_after_real_probe(self):
        report=build_report(
            self.manifest,GOLD,self.root,calibration_path=CAL,
            probe_repetitions=1
        )
        self.assertEqual(report["release_quality_gate"],"NOT_VERIFIED")
        self.assertEqual(report["real_asr_wer_cer"],
                         "NOT_MEASURED_NO_LICENSED_HUMAN_SPEECH")
        self.assertEqual(report["planning_estimate"]["estimated_workers_required"],None)
        self.assertEqual(report["report_sha256"],
                         canonical_sha({k:v for k,v in report.items()
                                        if k!="report_sha256"}))
        self.assertGreater(report["measurement"]["summary"]["input_bytes_total"],0)


if __name__=="__main__":
    unittest.main()
