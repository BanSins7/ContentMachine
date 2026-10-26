"""GA-MOD-08: adversarial read-only PSI reuse, strict provenance and cache."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4
import unittest
from unittest.mock import Mock

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.adapters.psi_reuse import (
    PSI_TRANSCRIPT, PSI_VISION, GA_VISION,
    PSIAssetBinding, PSIReuseError, PSIReuseCache, PostgresPSIReader,
    normalize_transcript, normalize_vision, ms_to_seconds, seconds_to_ms,
)


def link():
    return PSIAssetBinding(
        project_id="a"*32,video_id=17,media_sha256="b"*64,
        media_byte_size=12345,psi_source_id=str(uuid4()),
        psi_source_version_id=str(uuid4()),
        psi_acquisition_job_id=str(uuid4()),
    )


def transcript(binding):
    return {
        "schema_version": PSI_TRANSCRIPT,
        "normalizer_version":"transcript-normalizer/1.0.0",
        "video_sha256":binding.media_sha256,"source":"asr",
        "platform":"youtube","native_id":"abcdefghijk",
        "engine":"local_whisper","model":"faster-whisper",
        "model_version":"medium-v3",
        "speech_status":"speech_detected","processing_status":"completed",
        "detected_language":"ru","languages":["ru"],
        "timing_coverage":{"duration_ms":5000,"first_start_ms":250,
                           "last_end_ms":2500,"speech_timed_ms":1750,
                           "fraction_of_video":0.35},
        "source_text":{"srt":None,"txt":None},
        "segments":[
            {"start_ms":250,"end_ms":1000,"text":"Привет","language":"ru",
             "source":"asr","confidence":0.75},
            {"start_ms":1500,"end_ms":2500,"text":"всем","language":"ru",
             "source":"asr"},
        ],
        "warnings":[],"segment_count":2,
    }


def vision(binding):
    return {
        "schema_version":PSI_VISION,
        "status":"completed","video_sha256":binding.media_sha256,
        "video_source_version_id":binding.psi_source_version_id,
        "acquisition_job_id":binding.psi_acquisition_job_id,
        "probe":{"duration_ms":5000},
        "video_duration_ms":5000,
        "frame_sampling":{"method":"early_focus_uniform_scene_cut",
                          "sampled_frames":1,"requested_frames":8,
                          "all_frames_analyzed":False,
                          "cuts_scan_limit_ms":5000,
                          "sampled_instants_ms":[1300]},
        "scenes":[{"scene_id":"sc001","start_ms":0,"end_ms":5000,
                   "boundary_kind":"sampling_bin","frame_ids":["f001"]}],
        "frames":[{"frame_id":"f001","time_ms":1300,"scene_id":"sc001",
                   "storage_ref":"vision_assets/fixture/f001.jpg",
                   "sha256":"c"*64,"byte_size":50_000,
                   "time_precision":"sample_only",
                   "ocr":{"status":"ok","text":"BUY","engine":"tesseract",
                          "error":None,"language_hint":"eng",
                          "start_ms":1300,"end_ms":1340,
                          "coverage_kind":"single_frame",
                          "confidence":None}}],
        "warnings":[],"engine_versions":{"schema":PSI_VISION},
        "trust_level":"local_frame_sample",
    }


def record(binding,payload,*,config="d"*64,job="completed"):
    return {
        "project_id":binding.project_id,"source_id":binding.psi_source_id,
        "source_version_id":binding.psi_source_version_id,
        "acquisition_job_id":binding.psi_acquisition_job_id,
        "output_id":str(uuid4()),"schema_version":payload["schema_version"],
        "output":payload,"output_sha256":canonical_sha256(payload),
        "config_sha256":config,"job_state":job,"rights_ok":True,
    }


class PSI08Contracts(unittest.TestCase):
    def assertCode(self,code,call):
        with self.assertRaises(PSIReuseError) as error:
            call()
        self.assertEqual(error.exception.code,code)

    def test_distinct_psi_source_version_and_discovery_id(self):
        b=link()
        self.assertEqual(b.video_id,17)
        self.assertEqual(b.lineage()["discovery_video_id"],17)
        self.assertNotEqual(b.psi_source_version_id,str(b.video_id))
        self.assertCode("invalid_psi_uuid",lambda:PSIAssetBinding(
            b.project_id,b.video_id,b.media_sha256,b.media_byte_size,
            b.psi_source_id,"17",b.psi_acquisition_job_id))
        self.assertCode("invalid_discovery_video_id",lambda:PSIAssetBinding(
            b.project_id,True,b.media_sha256,b.media_byte_size,
            b.psi_source_id,b.psi_source_version_id,b.psi_acquisition_job_id))

    def test_exact_milliseconds_seconds_no_floating_point_guessing(self):
        for value in (0,1,250,1301,600000):
            converted=ms_to_seconds(value)
            self.assertEqual(seconds_to_ms(converted),value)
        for value in (True,0.1,-1,600001,None):
            self.assertCode("invalid_psi_time",lambda:ms_to_seconds(value))
        for value in ("1.5","1.0005","-1.000","NaN",None):
            self.assertCode("invalid_ga_seconds",lambda:seconds_to_ms(value))

    def test_transcript_timed_sparse_reuse_and_confidence(self):
        b=link();p=transcript(b);r=record(b,p)
        normalized=normalize_transcript(r,b,expected_config_sha256="d"*64,
                                        expected_model_version="medium-v3")
        self.assertEqual(normalized["schema_version"],"ga-psi-transcript-reuse/v1")
        self.assertEqual(normalized["status"],"partial")
        self.assertEqual(normalized["segments"][0]["start_sec"],"0.250")
        self.assertEqual(normalized["segments"][0]["confidence"],"0.75")
        self.assertEqual(normalized["segments"][1]["confidence"],None)
        self.assertFalse(normalized["complete_speech_coverage"])
        self.assertFalse(normalized["verified_no_speech"])
        self.assertEqual(normalized["upstream"]["config_sha256"],"d"*64)
        self.assertEqual(normalized["evidence_count"],2)

    def test_missing_or_untimed_speech_never_claims_absence(self):
        b=link();p=transcript(b)
        p["source"]="none";p["speech_status"]="no_speech"
        p["segments"]=[];p["segment_count"]=0
        p["timing_coverage"]={"duration_ms":5000,"first_start_ms":None,
                              "last_end_ms":None,"speech_timed_ms":0,
                              "fraction_of_video":0}
        normalized=normalize_transcript(record(b,p),b)
        self.assertEqual(normalized["status"],"unavailable")
        self.assertFalse(normalized["verified_no_speech"])
        self.assertIsNone(normalized["evidence_count"])

    def test_transcript_sha_ids_versions_and_overlaps_fail(self):
        b=link();p=transcript(b)
        r=record(b,p)
        self.assertCode("psi_output_digest_mismatch",lambda:normalize_transcript(
            dict(r,output_sha256="f"*64),b))
        self.assertCode("psi_config_mismatch",lambda:normalize_transcript(
            r,b,expected_config_sha256="e"*64))
        self.assertCode("psi_model_version_mismatch",lambda:normalize_transcript(
            r,b,expected_model_version="wrong"))
        self.assertCode("psi_lineage_mismatch",lambda:normalize_transcript(
            dict(r,source_version_id=str(uuid4())),b))
        self.assertCode("psi_lineage_mismatch",lambda:normalize_transcript(
            dict(r,project_id="f"*32),b))
        bad=deepcopy(p);bad["video_sha256"]="f"*64
        self.assertCode("psi_media_hash_mismatch",lambda:normalize_transcript(
            record(b,bad),b))
        bad=deepcopy(p);bad["segments"][1]["start_ms"]=500
        self.assertCode("psi_segment_timing_invalid",lambda:normalize_transcript(
            record(b,bad),b))
        bad=deepcopy(p);bad["segments"][0]["confidence"]=True
        self.assertCode("psi_confidence_invalid",lambda:normalize_transcript(
            record(b,bad),b))
        self.assertCode("psi_rights_revoked",lambda:normalize_transcript(
            dict(r,rights_ok=False),b))
        self.assertCode("psi_schema_or_state_mismatch",lambda:normalize_transcript(
            dict(r,job_state="failed"),b))

    def test_psi_vision_only_single_frame_ocr_partial(self):
        b=link();p=vision(b)
        got=normalize_vision(record(b,p),b,expected_config_sha256="d"*64)
        self.assertEqual(got["status"],"partial")
        self.assertEqual(got["ocr_spans"][0]["observed_sec"],"1.300")
        self.assertEqual(got["ocr_spans"][0]["sample_end_sec"],"1.340")
        self.assertIsNone(got["ocr_spans"][0]["confidence"])
        self.assertEqual(got["visual_temporal_coverage"],"unknown")
        self.assertEqual(got["ocr_temporal_coverage"],"unknown")
        self.assertEqual(got["scene_coverage"],"unknown")
        self.assertEqual(got["sampled_frames"],1)

    def test_vision_cannot_inflate_sparse_ocr_or_model_confidence(self):
        b=link();p=vision(b)
        invalid=deepcopy(p)
        invalid["frame_sampling"]["all_frames_analyzed"]=True
        self.assertCode("psi_visual_coverage_untrusted",lambda:normalize_vision(
            record(b,invalid),b))
        invalid=deepcopy(p)
        invalid["frames"][0]["ocr"]["coverage_kind"]="whole_scene"
        self.assertCode("psi_ocr_coverage_invalid",lambda:normalize_vision(
            record(b,invalid),b))
        invalid=deepcopy(p)
        invalid["frames"][0]["ocr"]["confidence"]=0.98
        self.assertCode("psi_uncalibrated_confidence",lambda:normalize_vision(
            record(b,invalid),b))
        invalid=deepcopy(p)
        invalid["frames"][0]["ocr"]["end_ms"]=1500
        self.assertCode("psi_frame_timing_invalid",lambda:normalize_vision(
            record(b,invalid),b))
        invalid=deepcopy(p)
        invalid["video_sha256"]="f"*64
        self.assertCode("psi_media_hash_mismatch",lambda:normalize_vision(
            record(b,invalid),b))
        invalid=deepcopy(p)
        invalid["frames"][0]["ocr"]["status"]="no_text_detected"
        invalid["frames"][0]["ocr"]["text"]=""
        got=normalize_vision(record(b,invalid),b)
        self.assertEqual(got["status"],"unavailable")
        self.assertEqual(got["ocr_spans"],[])


class DummyConn:
    def __init__(self,row=()):
        self.row=row
        self.sql=[]
    def execute(self,sql,params):
        self.sql.append((sql,params))
        if sql=="SELECT to_regclass(%s)":
            return DummyCursor([(params[0],)])
        if "SELECT EXISTS(" in sql:
            return DummyCursor([(True,)])
        return DummyCursor(self.row)


class DummyCursor:
    def __init__(self,rows):
        self.rows=list(rows)
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return list(self.rows)


class PSI08SQLReader(unittest.TestCase):
    def test_capability_requires_exact_peer_version_and_schema(self):
        b=link()
        r=PostgresPSIReader(DummyConn(),b,kind="vision",observed_version=PSI_VISION)
        self.assertEqual(r.capability().state,"available")
        self.assertEqual(r.capability().observed_version,GA_VISION)
        self.assertEqual(PostgresPSIReader(DummyConn(),b,kind="vision",
                         observed_version=GA_VISION).capability().state,"incompatible")
        self.assertEqual(PostgresPSIReader(DummyConn(),b,kind="vision",
                         observed_version=None).capability().state,"unavailable")
        self.assertEqual(PostgresPSIReader(DummyConn(),b,kind="transcript",
                         observed_version=PSI_TRANSCRIPT).capability().state,"available")

    def test_authoritative_source_and_acquisition_binding(self):
        b=link();p=transcript(b)
        row=(str(uuid4()),p,canonical_sha256(p),"completed",True,None,
             b.psi_source_version_id,b.media_sha256,b.media_sha256,b.media_byte_size,
             b.psi_acquisition_job_id,b.psi_source_version_id,
             {"can_analyze":True},{"can_analyze":True},"d"*64,None,False)
        conn=DummyConn((row,))
        r=PostgresPSIReader(conn,b,kind="transcript",observed_version=PSI_TRANSCRIPT)
        self.assertEqual(r.read_verified(b.media_sha256)["segments"][0]["start_sec"],"0.250")
        sql=conn.sql[-1][0]
        self.assertIn("source_processing_outputs",sql)
        self.assertIn("project_video_acquisition_runs",sql)
        self.assertIn("project_source_versions",sql)
        self.assertIn("project_transcript_runs",sql)
        self.assertIn("FOR SHARE",sql)
        self.assertNotIn("qualified_data",sql)
        with self.assertRaises(PSIReuseError):
            r.read_verified("e"*64)

    def test_revoked_source_wrong_asset_and_wrong_config_return_no_reuse(self):
        b=link();p=vision(b)
        baseline=(str(uuid4()),p,canonical_sha256(p),"completed",True,None,
                  b.psi_source_version_id,b.media_sha256,b.media_sha256,b.media_byte_size,
                  b.psi_acquisition_job_id,b.psi_source_version_id,
                  {"can_analyze":True},{"can_analyze":True},"d"*64,"prepare",False)
        def read(row,**opts):
            r=PostgresPSIReader(DummyConn((row,)),b,kind="vision",observed_version=PSI_VISION)
            return r.read_verified(b.media_sha256,**opts)
        self.assertEqual(read(baseline)["status"],"partial")
        missing=list(baseline);missing[12]={"can_analyze":False}
        self.assertIsNone(read(tuple(missing)))
        bad=list(baseline);bad[8]="f"*64
        self.assertIsNone(read(tuple(bad)))
        self.assertIsNone(read(baseline,expected_config_sha256="e"*64))
        self.assertIsNone(read(baseline[:-2]+("analyze",False)))

    def test_discovery_project_media_recheck_blocks_psi_reuse(self):
        b=link();p=transcript(b)
        row=(str(uuid4()),p,canonical_sha256(p),"completed",True,None,
             b.psi_source_version_id,b.media_sha256,b.media_sha256,b.media_byte_size,
             b.psi_acquisition_job_id,b.psi_source_version_id,
             {"can_analyze":True},{"can_analyze":True},"d"*64,None,False)
        class NoD1(DummyConn):
            def execute(self,sql,params):
                if "SELECT EXISTS(" in sql:
                    return DummyCursor([(False,)])
                return super().execute(sql,params)
        db=NoD1((row,))
        reader=PostgresPSIReader(db,b,kind="transcript",observed_version=PSI_TRANSCRIPT)
        self.assertIsNone(reader.read_verified(b.media_sha256))
        self.assertFalse(any("FROM source_processing_outputs" in sql for sql,_ in db.sql))
        self.assertTrue(any("sre_project_videos" in sql for sql,_ in db.sql))

    def test_missing_psi_tables_fail_closed_not_auto_install(self):
        b=link()
        class Missing(DummyConn):
            def execute(self,sql,params):
                if sql=="SELECT to_regclass(%s)":
                    return DummyCursor([(None,)])
                raise AssertionError("Should never query PSI without schema")
        r=PostgresPSIReader(Missing(),b,kind="vision",observed_version=PSI_VISION)
        self.assertEqual(r.capability().state,"unavailable")
        self.assertIsNone(r.read_verified(b.media_sha256))


class PSI08Service(unittest.TestCase):
    def test_reuse_is_disabled_by_default_with_no_query(self):
        from intelligence.group_a.service import GroupAService
        b=link();reader=PostgresPSIReader(
            DummyConn(),b,kind="transcript",observed_version=PSI_TRANSCRIPT)
        result=GroupAService().reuse_psi(b,transcript_reader=reader)
        self.assertEqual(result["state"],"unavailable")
        self.assertEqual(result["reason_code"],"psi_reuse_disabled")

    def test_enabled_no_psi_output_falls_back_to_local_future_work(self):
        from intelligence.group_a.service import GroupAService
        from intelligence.group_a.config import GASettings
        b=link();service=GroupAService(settings=GASettings(allow_psi_reuse=True))
        out=service.reuse_psi(b)
        self.assertEqual(out["state"],"unavailable")
        self.assertEqual(out["reason_code"],"psi_verified_outputs_missing")
        self.assertIsNone(out["vision"])
        self.assertIsNone(out["transcript"])

    def test_reader_binding_mismatch_is_never_guessed_or_reused(self):
        from intelligence.group_a.service import GroupAService
        from intelligence.group_a.config import GASettings
        b=link();other=link()
        service=GroupAService(settings=GASettings(allow_psi_reuse=True))
        reader=PostgresPSIReader(DummyConn(),other,kind="vision",
                                 observed_version=PSI_VISION)
        with self.assertRaises(TypeError):
            service.reuse_psi(b,vision_reader=reader)
        with self.assertRaises(TypeError):
            service.reuse_psi(b,transcript_reader=reader)

    def test_memo_rejects_forged_full_coverage_and_provenance(self):
        b=link();p=vision(b);r=record(b,p)
        result=normalize_vision(r,b)
        args=dict(kind="vision",output_sha256=r["output_sha256"],
                  config_sha256=r["config_sha256"],model_version=None,
                  observed_version=PSI_VISION)
        cache=PSIReuseCache()
        cache.put(b,result,**args)
        forged=deepcopy(result);forged["ocr_temporal_coverage"]="complete"
        with self.assertRaises(PSIReuseError):
            cache.put(b,forged,**args)
        forged=deepcopy(result)
        forged["upstream"]["output_sha256"]="f"*64
        with self.assertRaises(PSIReuseError):
            cache.put(b,forged,**args)


class PSI08Cache(unittest.TestCase):
    def test_fenced_cache_keys_and_current_rights_every_read(self):
        b=link();p=transcript(b);r=record(b,p)
        normalized=normalize_transcript(r,b)
        cache=PSIReuseCache(max_items=2)
        args={"kind":"transcript","output_sha256":r["output_sha256"],
              "config_sha256":r["config_sha256"],
              "model_version":p["model_version"],"observed_version":PSI_TRANSCRIPT}
        cache.put(b,normalized,**args)
        calls=Mock(return_value=True)
        cached=cache.get(b,verify_current=calls,**args)
        self.assertEqual(cached["segments"][0]["text"],"Привет")
        self.assertEqual(calls.call_count,1)
        cached["segments"][0]["text"]="malicious mutation"
        self.assertEqual(cache.get(b,verify_current=Mock(return_value=True),**args)
                         ["segments"][0]["text"],"Привет")
        self.assertIsNone(cache.get(b,verify_current=Mock(return_value=True),
                                    **dict(args,config_sha256="f"*64)))
        with self.assertRaises(PSIReuseError) as ctx:
            cache.get(b,verify_current=Mock(return_value=False),**args)
        self.assertEqual(ctx.exception.code,"psi_rights_revoked")
        self.assertIsNone(cache.get(b,verify_current=Mock(return_value=True),**args))


if __name__=="__main__":
    unittest.main()
