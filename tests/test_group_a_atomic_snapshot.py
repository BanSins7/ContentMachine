"""GA18 pure snapshot truth and conditional disposable PG17 transaction E2E."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import json
import os
import unittest

from intelligence.group_a.contracts import Scene, TechnicalMeasurement
from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.media_manifest import build_manifest
from intelligence.group_a.validation import (
    SnapshotError, bind_technical
)
from intelligence.group_a.assembly import (
    assemble_video_snapshot, validate_video_snapshot
)
from intelligence.group_a.snapshot_repository import persist_group_a_snapshot

PROJECT="a"*32
EXPECTED="b"*64


def authorized(scope):
    return True


def origin(snapshot):
    return snapshot["schema_version"]=="ga-video-snapshot/v1"


def manifest_fixture(root, *, source_id="ga18-test", video_id=901,
                     policy_id=33, count=1, video_kind="video"):
    ordered=[]
    for index in range(count):
        binary=(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp41"
                +bytes([index+1])*128)
        path=root/("clip"+str(index+1)+".mp4")
        path.write_bytes(binary)
        ordered.append({
            "media_asset_id":index+301,"video_id":video_id,
            "source_id":source_id,"media_kind":"video",
            "local_path":str(path),"sha256":sha256(binary).hexdigest(),
            "byte_size":len(binary),
        })
    pointer={"schema_version":"qdp-handoff/v1",
             "event_id":str(uuid4()),"project_id":PROJECT,
             "qdp_run_id":str(uuid4()),"video_id":video_id,
             "analysis_group":"A","input_sha256":EXPECTED}
    return build_manifest(
        pointer,source_id=source_id,source_policy_version_id=policy_id,
        content_kind=video_kind,ordered_assets=ordered,roots=[str(root)],
        verified_at=datetime(2026,10,27,tzinfo=timezone.utc)
    )


def technical(manifest,idx=0,duration="4.000"):
    value=Decimal(duration)
    m=TechnicalMeasurement(
        duration_sec=value,aspect_ratio="1:1",
        scenes=(Scene(index=1,start_sec=Decimal("0"),end_sec=value),)
    )
    return bind_technical(m,asset_sha=manifest["items"][idx]["sha256"])


def observed(schema,sha,**values):
    data={"schema_version":schema,"source_media_sha256":sha,**values}
    return {**data,"observation_sha256":canonical_sha256(data)}


def speech(sha):
    return observed(
        "ga-speech-metrics/v1",sha,
        spoken_words=2,speech_duration_sec="1.000",
        video_duration_sec="4.000",clock_status="verified_video_timebase",
        video_clock_offset_sec="0.000",
        a003_speech_projection={
            "speech_status":"partial","spoken_words":2,
            "speech_duration_sec":"1.000",
        },
        a003_speech_segments_projection=[
            {"start_sec":"0.500","end_sec":"1.500",
             "spoken_text":"hello world","method_name":"ga11-asr",
             "confidence_pct":None}
        ],
    )


def screen(sha):
    return observed(
        "ga-screen-text-tracking/v1",sha,
        status="partial",source_duration_sec="4.000",
        sampling_truth="sparse_instants_only",
        continuous_intervals_verified=False,
        tracks=[],sampled_screen_word_count=None,
        a003_screen_text_projection=[],
    )


def audio(sha):
    return observed(
        "ga-audio-events/v1",sha,
        state="classified_partial",a003_mapping_state="verified_audio_video_timebase",
        video_clock_state="verified_video_timebase",
        video_duration_sec="4.000",
        a003_audio_events_projection=[
            {"event_type":"music","start_sec":"0.000","end_sec":"2.000",
             "label":"music","method_name":"ga15", "confidence_pct":None}
        ]
    )


def subtitle(sha):
    return observed(
        "ga-subtitle-presence/v1",sha,
        has_subtitles="true",a003_has_subtitles_projection="true",
        proof_kind="embedded_subtitle_stream",
    )


def certification(stream,sha,evidence_sha,absence=False):
    data={
        "schema_version":"ga-stream-coverage/v1","stream":stream,
        "source_media_sha256":sha,"observation_sha256":evidence_sha,
        "full_video_coverage":True,"scope":"whole_verified_video_asset",
        "verified_absence":absence,"method":"independent-gold-human-review-v1",
    }
    return {**data,"certificate_sha256":canonical_sha256(data)}


def certified_bundle(manifest,idx=0,*,measured_at=None):
    sha=manifest["items"][idx]["sha256"]
    sp=speech(sha)
    txt=screen(sha)
    aud=audio(sha)
    subs=subtitle(sha)
    certificates={
        name:certification(name,sha,x["observation_sha256"])
        for name,x in (("speech",sp),("screen_text",txt),("audio",aud))
    }
    return assemble_video_snapshot(
        manifest,media_asset_id=manifest["items"][idx]["media_asset_id"],
        technical=technical(manifest,idx),speech=sp,screen_text=txt,
        audio_events=aud,subtitles=subs,certificates=certificates,
        verify_coverage=lambda proof:proof["scope"]=="whole_verified_video_asset",
        verify_current=authorized,measured_at=measured_at or
            datetime(2026,10,27,19,0,tzinfo=timezone.utc)
    )


class GA18AssemblyUnit(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.manifest,_=manifest_fixture(self.root)

    def test_technical_only_always_partial_no_fabricated_zero(self):
        snapshot=assemble_video_snapshot(
            self.manifest,media_asset_id=301,
            technical=technical(self.manifest),
            verify_current=authorized,
            measured_at=datetime(2026,10,27,19,0,tzinfo=timezone.utc)
        )
        self.assertEqual(snapshot["analysis_state"],"partial")
        self.assertEqual(snapshot["scene_status"],"complete")
        self.assertEqual(snapshot["speech_status"],"unavailable")
        self.assertEqual(snapshot["screen_text_status"],"unavailable")
        self.assertEqual(snapshot["audio_status"],"unavailable")
        self.assertEqual(snapshot["has_subtitles"],"unknown")
        self.assertIsNone(snapshot["spoken_words"])
        self.assertIsNone(snapshot["speech_duration_sec"])
        self.assertIsNone(snapshot["cta_start_sec"])
        self.assertEqual(snapshot["scenes"][0]["start_sec"],"0.000")
        self.assertEqual(snapshot["scenes"][0]["end_sec"],"4.000")
        validate_video_snapshot(self.manifest,snapshot)

    def test_partial_asr_sparse_text_audio_never_claim_full_coverage(self):
        sha=self.manifest["items"][0]["sha256"]
        snapshot=assemble_video_snapshot(
            self.manifest,media_asset_id=301,
            technical=technical(self.manifest),
            speech=speech(sha),screen_text=screen(sha),
            audio_events=audio(sha),subtitles=subtitle(sha),
            verify_current=authorized
        )
        self.assertEqual(snapshot["analysis_state"],"partial")
        self.assertEqual(snapshot["spoken_words"],2)
        self.assertEqual(snapshot["speech_duration_sec"],"1.000")
        self.assertEqual(len(snapshot["speech_segments"]),1)
        self.assertEqual(len(snapshot["audio_events"]),1)
        self.assertEqual(snapshot["screen_text_segments"],[])
        self.assertEqual(snapshot["scene_status"],"complete")
        self.assertEqual(snapshot["speech_status"],"partial")
        validate_video_snapshot(self.manifest,snapshot)

    def test_independently_verified_complete_video(self):
        snapshot=certified_bundle(self.manifest)
        self.assertEqual(snapshot["analysis_state"],"completed")
        self.assertEqual(snapshot["has_subtitles"],"true")
        self.assertEqual(snapshot["measurement_methods"]["ga_asset_sha256"],
                         self.manifest["items"][0]["sha256"])
        self.assertEqual(set(snapshot["coverage_certificates"]),
                         {"speech","screen_text","audio"})
        validate_video_snapshot(self.manifest,snapshot)

    def test_coverage_callback_denial_and_missing_certificate(self):
        sha=self.manifest["items"][0]["sha256"]
        s=speech(sha)
        cert=certification("speech",sha,s["observation_sha256"])
        with self.assertRaisesRegex(SnapshotError,"coverage_authorization_denied"):
            assemble_video_snapshot(
                self.manifest,media_asset_id=301,technical=technical(self.manifest),
                speech=s,certificates={"speech":cert},
                verify_coverage=lambda _:False,verify_current=authorized
            )
        with self.assertRaisesRegex(SnapshotError,"coverage_verifier_required"):
            assemble_video_snapshot(
                self.manifest,media_asset_id=301,technical=technical(self.manifest),
                speech=s,certificates={"speech":cert},verify_current=authorized
            )

    def test_cross_asset_stream_rejected(self):
        with self.assertRaisesRegex(SnapshotError,"cross_asset_stream"):
            assemble_video_snapshot(
                self.manifest,media_asset_id=301,technical=technical(self.manifest),
                speech=speech("f"*64),verify_current=authorized
            )

    def test_illegal_scene_gap_and_segment_over_video(self):
        m=technical(self.manifest)
        m["measurement"]["scenes"]=[
            {"scene_index":1,"start_sec":"0.000","end_sec":"2.000"},
            {"scene_index":2,"start_sec":"2.500","end_sec":"4.000"},
        ]
        m["observation_sha256"]=canonical_sha256({k:v for k,v in m.items()
                                                  if k!="observation_sha256"})
        with self.assertRaisesRegex(SnapshotError,"technical_scene_coverage_invalid"):
            assemble_video_snapshot(self.manifest,media_asset_id=301,
                                    technical=m,verify_current=authorized)
        sha=self.manifest["items"][0]["sha256"]
        sp=speech(sha)
        sp["a003_speech_segments_projection"][0]["end_sec"]="4.001"
        sp["observation_sha256"]=canonical_sha256({k:v for k,v in sp.items()
                                                   if k!="observation_sha256"})
        with self.assertRaisesRegex(SnapshotError,"segment_outside_video"):
            assemble_video_snapshot(self.manifest,media_asset_id=301,
                                    technical=technical(self.manifest),
                                    speech=sp,verify_current=authorized)

    def test_recomputed_digest_does_not_bypass_completion_gate(self):
        result=assemble_video_snapshot(
            self.manifest,media_asset_id=301,
            technical=technical(self.manifest),verify_current=authorized
        )
        result["analysis_state"]="completed"
        result["observation_sha256"]=canonical_sha256({k:v for k,v in result.items()
                                                       if k!="observation_sha256"})
        with self.assertRaisesRegex(SnapshotError,"snapshot_false_completion"):
            validate_video_snapshot(self.manifest,result)

    def test_subtitles_false_rejected_without_dense_negative_review(self):
        sha=self.manifest["items"][0]["sha256"]
        fake=observed(
            "ga-subtitle-presence/v1",sha,
            has_subtitles="false",a003_has_subtitles_projection="false",
            proof_kind="sparse_no_text_detected"
        )
        with self.assertRaisesRegex(SnapshotError,"subtitle_false_without_dense_review"):
            assemble_video_snapshot(
                self.manifest,media_asset_id=301,
                technical=technical(self.manifest),subtitles=fake,
                verify_current=authorized
            )

    def test_duplicate_speech_segments_rejected_without_double_count(self):
        sha=self.manifest["items"][0]["sha256"]
        data=speech(sha)
        data["a003_speech_segments_projection"].append(
            dict(data["a003_speech_segments_projection"][0])
        )
        data["observation_sha256"]=canonical_sha256({
            k:v for k,v in data.items() if k!="observation_sha256"
        })
        with self.assertRaisesRegex(SnapshotError,"duplicate_measured_segment"):
            assemble_video_snapshot(
                self.manifest,media_asset_id=301,
                technical=technical(self.manifest),
                speech=data,verify_current=authorized,
            )

    def test_snapshot_json_schema_if_jsonschema_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema extra not installed")
        schema_path=(Path(__file__).resolve().parents[1]/
                     "intelligence/group_a/schemas/ga-video-snapshot-v1.json")
        schema=json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        snapshot=certified_bundle(self.manifest)
        jsonschema.Draft202012Validator(schema).validate(snapshot)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(schema).validate(
                dict(snapshot,cta_start_sec="1.000")
            )

    def test_duplicate_video_snapshots_rejected_before_sql(self):
        one=certified_bundle(self.manifest)
        class DB:
            def transaction(self):
                raise AssertionError("DB must not be contacted")
        with self.assertRaisesRegex(SnapshotError,"duplicate_video_snapshot_asset"):
            persist_group_a_snapshot(
                DB(),self.manifest,video_snapshots=[one,one],
                verify_current=authorized,verify_origin=origin,
                verify_coverage=lambda _:True
            )

    def test_wrong_asset_kind_and_revoked_rights(self):
        with self.assertRaisesRegex(SnapshotError,"media_rights_unverified"):
            assemble_video_snapshot(
                self.manifest,media_asset_id=301,
                technical=technical(self.manifest),
                verify_current=lambda _:False
            )

    def test_digest_lineage_and_ambiguous_media_versions(self):
        snapshot=certified_bundle(self.manifest)
        forged=dict(snapshot,source_media_sha256="f"*64)
        with self.assertRaisesRegex(SnapshotError,"video_snapshot_lineage_mismatch"):
            validate_video_snapshot(self.manifest,forged)
        forged=dict(snapshot,has_subtitles="false")
        with self.assertRaisesRegex(SnapshotError,"video_snapshot_digest_mismatch"):
            validate_video_snapshot(self.manifest,forged)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),
                     "Disposable PostgreSQL 17 DSN not configured")
class GA18PG17(unittest.TestCase):
    def setUp(self):
        import psycopg
        from intelligence.migrate import apply_migrations
        self.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        apply_migrations(self.conn)
        self.addCleanup(self.conn.close)
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source="ga18-"+uuid4().hex
        token=uuid4().hex
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
               VALUES (%s,'test','owned','enabled',TRUE)""",(self.source,)
        )
        self.policy=self.conn.execute(
            """INSERT INTO source_policy_versions(source_id,version,is_active,permissions)
               VALUES (%s,1,TRUE,'{"can_analyze_media":true}'::jsonb) RETURNING id""",
            (self.source,)
        ).fetchone()[0]
        self.video=self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
               VALUES ('instagram',%s,%s,'video') RETURNING id""",
            (token,"https://www.instagram.com/p/"+token)
        ).fetchone()[0]
        blob=b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp41"+b"x"*128
        path=self.root/"asset.mp4"
        path.write_bytes(blob)
        self.asset=self.conn.execute(
            """INSERT INTO media_assets(video_id,source_id,media_kind,local_path,sha256,permission)
               VALUES (%s,%s,'video',%s,%s,'analysis_allowed') RETURNING id""",
            (self.video,self.source,str(path),sha256(blob).hexdigest())
        ).fetchone()[0]
        pointer={"schema_version":"qdp-handoff/v1","event_id":str(uuid4()),
                 "project_id":PROJECT,"qdp_run_id":str(uuid4()),
                 "video_id":self.video,"analysis_group":"A","input_sha256":EXPECTED}
        self.manifest,_=build_manifest(
            pointer,source_id=self.source,source_policy_version_id=self.policy,
            content_kind="video",ordered_assets=[
                {"media_asset_id":self.asset,"video_id":self.video,
                 "source_id":self.source,"media_kind":"video",
                 "local_path":str(path),"sha256":sha256(blob).hexdigest(),
                 "byte_size":len(blob)}
            ],roots=[str(self.root)],
            verified_at=datetime(2026,10,27,tzinfo=timezone.utc)
        )
        self.addCleanup(self.conn.execute,"DELETE FROM sources WHERE id=%s",(self.source,))
        self.addCleanup(self.conn.execute,"DELETE FROM videos WHERE id=%s",(self.video,))

    def test_immutable_completed_a003_real_summary_and_replay(self):
        snap=certified_bundle(self.manifest)
        kwargs={"video_snapshots":[snap],"verify_current":authorized,
                "verify_origin":origin,"verify_coverage":lambda _:True}
        result=persist_group_a_snapshot(self.conn,self.manifest,**kwargs)
        aid=result["video_analysis_ids"][self.asset]
        self.assertEqual(persist_group_a_snapshot(self.conn,self.manifest,**kwargs),result)
        summary=self.conn.execute(
            """SELECT state,scene_count,spoken_words,speech_wpm,
                      speech_coverage_pct,first_text_sec,first_speech_sec,
                      has_subtitles,cta_start_sec
               FROM video_feature_a_summary WHERE analysis_id=%s""",
            (aid,)
        ).fetchone()
        self.assertEqual(summary[0],"completed")
        self.assertEqual(summary[1],1)
        self.assertEqual(summary[2],2)
        self.assertEqual(summary[3],Decimal("120.00"))
        self.assertEqual(summary[4],Decimal("25.00"))
        self.assertIsNone(summary[5])  # GA13 sparse instants, NO invented onset
        self.assertEqual(summary[6],Decimal("0.500"))
        self.assertEqual(summary[7],"true")
        self.assertIsNone(summary[8])
        for sql in (
            "UPDATE video_feature_a_analyses SET duration_sec=5 WHERE id=%s",
            "UPDATE video_feature_a_scenes SET end_sec=3 WHERE analysis_id=%s",
            "DELETE FROM video_feature_a_scenes WHERE analysis_id=%s",
        ):
            with self.assertRaises(Exception):
                self.conn.execute(sql,(aid,))
        rec=self.conn.execute(
            "SELECT snapshot_sha256,source_media_sha256 FROM ga18_video_snapshot_receipts WHERE analysis_id=%s",
            (aid,)
        ).fetchone()
        self.assertEqual(rec,(snap["observation_sha256"],
                              self.manifest["items"][0]["sha256"]))

    def test_transaction_rollback_on_revoked_origin_after_write(self):
        snap=certified_bundle(self.manifest)
        calls=[0]
        def fail_at_second_check(data):
            calls[0]+=1
            return calls[0]==1
        with self.assertRaisesRegex(SnapshotError,"snapshot_revoked_before_commit"):
            persist_group_a_snapshot(
                self.conn,self.manifest,video_snapshots=[snap],
                verify_current=authorized,verify_origin=fail_at_second_check,
                verify_coverage=lambda _:True,
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM video_feature_a_analyses WHERE analysis_key=%s",
                ("ga18-"+snap["observation_sha256"],)
            ).fetchone()[0],0
        )

    def test_partial_is_not_completed_even_with_complete_scenes(self):
        snap=assemble_video_snapshot(
            self.manifest,media_asset_id=self.asset,
            technical=technical(self.manifest),verify_current=authorized,
            measured_at=datetime(2026,10,27,19,0,tzinfo=timezone.utc)
        )
        result=persist_group_a_snapshot(
            self.conn,self.manifest,video_snapshots=[snap],
            verify_current=authorized,verify_origin=origin
        )
        aid=result["video_analysis_ids"][self.asset]
        summary=self.conn.execute(
            "SELECT state,scene_count,spoken_words,speech_wpm,first_speech_sec FROM video_feature_a_summary WHERE analysis_id=%s",
            (aid,)
        ).fetchone()
        self.assertEqual(summary,("partial",1,None,None,None))


try:
    from PIL import Image
except ImportError:
    Image=None


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN") and Image is not None,
                     "Disposable PG17 DSN + Pillow not configured")
class GA18MixedPG17(unittest.TestCase):
    """One transaction creates video A003 and linked mixed carousel A004."""
    def setUp(self):
        GA18PG17.setUp(self)
        self.conn.execute(
            "UPDATE videos SET content_kind='mixed_carousel' WHERE id=%s",
            (self.video,)
        )
        from io import BytesIO
        stills=[]
        for idx in (1,3):
            with BytesIO() as b:
                Image.new("RGB",(100+idx,110+idx),"white").save(b,"PNG")
                payload=b.getvalue()
            p=self.root/("slide"+str(idx)+".png")
            p.write_bytes(payload)
            aid=self.conn.execute(
                """INSERT INTO media_assets
                   (video_id,source_id,media_kind,local_path,sha256,permission)
                   VALUES (%s,%s,'image',%s,%s,'analysis_allowed')
                   RETURNING id""",
                (self.video,self.source,str(p),sha256(payload).hexdigest())
            ).fetchone()[0]
            stills.append({
                "media_asset_id":aid,"video_id":self.video,
                "source_id":self.source,"media_kind":"image",
                "local_path":str(p),"sha256":sha256(payload).hexdigest(),
                "byte_size":len(payload)
            })
        path=self.root/"asset.mp4"
        payload=path.read_bytes()
        video={
            "media_asset_id":self.asset,"video_id":self.video,
            "source_id":self.source,"media_kind":"video",
            "local_path":str(path),"sha256":sha256(payload).hexdigest(),
            "byte_size":len(payload)
        }
        pointer={"schema_version":"qdp-handoff/v1","event_id":str(uuid4()),
                 "project_id":PROJECT,"qdp_run_id":str(uuid4()),
                 "video_id":self.video,"analysis_group":"A",
                 "input_sha256":EXPECTED}
        self.manifest,self.refs=build_manifest(
            pointer,source_id=self.source,source_policy_version_id=self.policy,
            content_kind="mixed_carousel",ordered_assets=[
                stills[0],video,stills[1]
            ],roots=[str(self.root)],
            verified_at=datetime(2026,10,27,tzinfo=timezone.utc)
        )

    @contextmanager
    def opened(self,ref):
        from intelligence.group_a.media_manifest import open_verified
        with open_verified(self.refs[ref],roots=[str(self.root)]) as stream:
            yield stream

    def test_a003_and_mixed_a004_single_tx_completed_and_replay(self):
        from intelligence.group_a.extractors.mixed_carousel import (
            extract_mixed_carousel
        )
        class OCR:
            identity={"engine":"tesseract","engine_version":"5.5.0",
                      "model_sha256":"c"*64,"device":"cpu"}
            def process(self,blob,config):
                return {
                    "state":"text_observed","languages_used":["eng","rus"],
                    "engine_rotation":0,"error_code":None,
                    "words":[{"text":"hello","confidence_pct":"92.00",
                              "bbox":{"x":1,"y":1,"width":20,"height":10},
                              "line_ref":"l1"}],
                }
        prepared=extract_mixed_carousel(
            self.manifest,open_ref=self.opened,
            verify_current=authorized,read_a003=lambda m,i:None,
            backend=OCR()
        )
        self.assertEqual(prepared["analysis_state"],"partial")
        snap=certified_bundle(self.manifest,idx=1)
        result=persist_group_a_snapshot(
            self.conn,self.manifest,video_snapshots=[snap],
            prepared_carousel=prepared,verify_current=authorized,
            verify_origin=origin,verify_coverage=lambda _:True
        )
        aid=result["video_analysis_ids"][self.asset]
        cid=result["carousel_analysis_id"]
        self.assertIsInstance(aid,int)
        self.assertIsInstance(cid,int)
        summary=self.conn.execute(
            """SELECT state,slide_count,text_status,total_screen_word_count,
                      has_audio
               FROM carousel_feature_a_summary WHERE analysis_id=%s""",
            (cid,)
        ).fetchone()
        self.assertEqual(summary,("completed",3,"partial",None,"unknown"))
        slide=self.conn.execute(
            """SELECT video_a_analysis_id,duration_sec,item_type
               FROM carousel_feature_a_items
               WHERE analysis_id=%s AND slide_index=2""",
            (cid,)
        ).fetchone()
        self.assertEqual(slide,(aid,Decimal("4.000"),"video"))
        self.assertEqual(
            persist_group_a_snapshot(
                self.conn,self.manifest,video_snapshots=[snap],
                prepared_carousel=prepared,verify_current=authorized,
                verify_origin=origin,verify_coverage=lambda _:True
            ),result
        )
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE carousel_feature_a_analyses SET has_audio='true' WHERE id=%s",
                (cid,)
            )
