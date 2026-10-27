"""GA17 mixed image/video/carousel adversarial and disposable PG17 tests."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import json
import os
import shutil
import subprocess
import unittest

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.media_manifest import build_manifest, open_verified
from intelligence.group_a.extractors.carousel import CarouselError
from intelligence.group_a.extractors.mixed_carousel import (
    extract_mixed_carousel, validate_mixed_observation, A003_BINDING,
)
from intelligence.group_a.extractors.mixed_carousel_writer import persist_mixed_carousel
from intelligence.group_a.adapters.a003_slide import PinnedA003SlideReader
from intelligence.group_a.extractors.audio_decode import AudioProbe, DecodedAudio

try:
    from PIL import Image
except ImportError:
    Image = None

PROJECT = "a"*32


def still(size=(100,100)):
    with BytesIO() as out:
        Image.new("RGB",size,"white").save(out,"PNG")
        return out.getvalue()


def movie_header():
    # A known ftyp+isom magic for GA05 descriptor/lineage tests; NOT playable.
    # The separately named FFmpeg test supplies a real generated MP4.
    return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp41" + b"\x00"*128


def words(value="Hello"):
    return {"state":"text_observed", "languages_used":["eng","rus"],
            "engine_rotation":0,"error_code":None,
            "words":[{"text":value,"confidence_pct":"91.25",
                      "bbox":{"x":2,"y":2,"width":16,"height":9},
                      "line_ref":"l000001"}]}


class OCR:
    identity={"engine":"tesseract","engine_version":"5.5.0",
              "model_sha256":"c"*64,"device":"cpu"}
    def process(self,buf,cfg):
        assert buf.startswith(b"\x89PNG\r\n\x1a\n")
        return words()


def binding(manifest,item,*,analysis_id=5,state="completed",
            duration_sec="4.000",timeline=None):
    if timeline is None:
        timeline=[
            {"kind":"scene","start_sec":"0.000","end_sec":"2.000"},
            {"kind":"speech","start_sec":"1.000","end_sec":"3.500"},
        ]
    core={
        "schema_version":A003_BINDING,
        "analysis_id":analysis_id,
        "media_asset_id":item["media_asset_id"],
        "source_media_sha256":item["sha256"],
        "video_id":manifest["video_id"],
        "source_id":manifest["source_id"],
        "source_policy_version_id":manifest["source_policy_version_id"],
        "state":state,"duration_sec":duration_sec,"aspect_ratio":"1:1",
        "audio_status":"complete","timeline":timeline,
    }
    return {**core,"binding_sha256":canonical_sha256(core)}


def audio_decoded(item, present):
    if not present:
        probe=AudioProbe(state="absent",stream_index=None,codec_name=None,
                         source_channels=None,source_sample_rate=None,
                         start_time_sec=None,source_time_base=None,duration_sec=None)
        pcm=b""
    else:
        probe=AudioProbe(state="present",stream_index=1,codec_name="aac",
                         source_channels=1,source_sample_rate=48000,
                         start_time_sec="0.000",source_time_base="1/48000",
                         duration_sec="1.000")
        pcm=b"\x00\x01"*16000
    return DecodedAudio(probe=probe,pcm=pcm,source_sha256=item["sha256"])


@unittest.skipUnless(Image is not None,"Pillow required")
class GA17MixedUnit(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.project_id=PROJECT
        self.run_id=str(uuid4())
        self.video_id=72
        self.source_id="ga17-test"
        self.policy_id=33
        self.checked=[]

    def make(self, kinds=("image","video","image"), *, movies=None, ids=None):
        rows=[]
        movies=movies or {}
        ids=ids or list(range(201,201+len(kinds)))
        for index,kind in enumerate(kinds,1):
            raw=(still((100+index*10,100+index*10)) if kind=="image"
                 else movies.get(index,movie_header()))
            name=self.root/("slide"+str(index)+(".png" if kind=="image" else ".mp4"))
            name.write_bytes(raw)
            rows.append({
                "media_asset_id":ids[index-1],"video_id":self.video_id,
                "source_id":self.source_id,"media_kind":kind,
                "local_path":str(name),"sha256":sha256(raw).hexdigest(),
                "byte_size":len(raw)
            })
        pointer={
            "schema_version":"qdp-handoff/v1","event_id":str(uuid4()),
            "project_id":self.project_id,"qdp_run_id":self.run_id,
            "video_id":self.video_id,"analysis_group":"A",
            "input_sha256":"b"*64,
        }
        return build_manifest(
            pointer,source_id=self.source_id,source_policy_version_id=self.policy_id,
            content_kind="mixed_carousel",ordered_assets=rows,roots=[str(self.root)],
            verified_at=datetime(2026,10,27,tzinfo=timezone.utc)
        )

    @contextmanager
    def open_ref(self,ref):
        with open_verified(self.handles[ref],roots=[str(self.root)]) as stream:
            yield stream

    def rights(self,scope):
        self.checked.append(scope)
        return True

    def extract(self,m,handles,*,reader=None,audio_proofs=None,backend=None):
        self.handles=handles
        if reader is None:
            reader=lambda manifest,item:binding(manifest,item)
        return extract_mixed_carousel(
            m,open_ref=self.open_ref,verify_current=self.rights,
            read_a003=reader,backend=OCR() if backend is None else backend,
            audio_proofs=audio_proofs
        )

    def test_image_video_image_completed_with_per_asset_local_times(self):
        manifest,handles=self.make()
        obs=self.extract(manifest,handles)
        self.assertEqual(obs["analysis_state"],"completed")
        self.assertEqual(obs["slide_count"],3)
        self.assertEqual([x["item_type"] for x in obs["items"]],["image","video","image"])
        self.assertEqual([x["duration_sec"] for x in obs["items"]],[None,"4.000",None])
        self.assertEqual([x["video_a_analysis_id"] for x in obs["items"]],[None,5,None])
        self.assertEqual(obs["video_clock_semantics"],"per_asset_local_zero")
        self.assertIsNone(obs["publication_swipe_duration_sec"])
        self.assertIsNone(obs["total_screen_word_count"])
        self.assertEqual(obs["text_status"],"partial")
        self.assertEqual(obs["has_audio"],"unknown")
        self.assertEqual(obs["audio_status"],"unavailable")
        self.assertEqual(len(self.checked),6)
        validate_mixed_observation(manifest,obs)

    def test_multiple_video_slides_with_independent_clocks_and_audio(self):
        manifest,handles=self.make(("image","video","video","image"))
        clips=[item for item in manifest["items"] if item["kind"]=="video"]
        proofs={clips[0]["media_asset_id"]:audio_decoded(clips[0],True),
                clips[1]["media_asset_id"]:audio_decoded(clips[1],False)}
        result=self.extract(manifest,handles,
                            reader=lambda m,i:binding(m,i,analysis_id=i["media_asset_id"]+300),
                            audio_proofs=proofs)
        self.assertEqual(result["has_audio"],"true")
        self.assertEqual(result["audio_status"],"complete")
        self.assertEqual([x["duration_sec"] for x in result["items"]],
                         [None,"4.000","4.000",None])
        self.assertNotEqual(result["items"][1]["video_a_analysis_id"],
                            result["items"][2]["video_a_analysis_id"])
        validate_mixed_observation(manifest,result)

    def test_all_video_audio_absent_is_verified_false_not_unknown(self):
        manifest,handles=self.make()
        clip=manifest["items"][1]
        proof={clip["media_asset_id"]:audio_decoded(clip,False)}
        result=self.extract(manifest,handles,audio_proofs=proof)
        self.assertEqual(result["has_audio"],"false")
        self.assertEqual(result["audio_status"],"complete")

    def test_missing_a003_yields_partial_not_forged_complete(self):
        manifest,handles=self.make()
        result=self.extract(manifest,handles,reader=lambda m,i:None)
        self.assertEqual(result["analysis_state"],"partial")
        self.assertEqual(result["items"][1]["reason_code"],"a003_analysis_missing")
        self.assertIsNone(result["items"][1]["video_a_analysis_id"])
        self.assertIsNone(result["items"][1]["duration_sec"])
        validate_mixed_observation(manifest,result)

    def test_partial_a003_also_yields_partial(self):
        manifest,handles=self.make()
        result=self.extract(manifest,handles,
                            reader=lambda m,i:binding(m,i,state="partial"))
        self.assertEqual(result["analysis_state"],"partial")
        self.assertEqual(result["items"][1]["reason_code"],"a003_analysis_partial")

    def test_foreign_asset_and_wrong_source_hash_rejected(self):
        manifest,handles=self.make()
        def foreign(m,item):
            b=binding(m,item)
            b["media_asset_id"]+=1
            b["binding_sha256"]=canonical_sha256({k:v for k,v in b.items() if k!="binding_sha256"})
            return b
        with self.assertRaisesRegex(CarouselError,"a003_asset_lineage_mismatch"):
            self.extract(manifest,handles,reader=foreign)
        def stale(m,item):
            b=binding(m,item)
            b["source_media_sha256"]="f"*64
            b["binding_sha256"]=canonical_sha256({k:v for k,v in b.items() if k!="binding_sha256"})
            return b
        with self.assertRaisesRegex(CarouselError,"a003_asset_lineage_mismatch"):
            self.extract(manifest,handles,reader=stale)

    def test_video_segments_cannot_cross_asset_duration(self):
        manifest,handles=self.make()
        for entries in (
            [{"kind":"scene","start_sec":"0.000","end_sec":"4.100"}],
            [{"kind":"scene","start_sec":"3.900","end_sec":"3.800"}],
            [{"kind":"scene","start_sec":"-0.100","end_sec":"1.000"}]
        ):
            with self.assertRaises(CarouselError):
                self.extract(manifest,handles,
                             reader=lambda m,i:binding(m,i,timeline=entries))

    def test_no_slide_index_to_seconds_or_image_video_link(self):
        manifest,handles=self.make()
        obs=self.extract(manifest,handles)
        obs["items"][0]["duration_sec"]="1.000"
        obs["observation_sha256"]=canonical_sha256({k:v for k,v in obs.items()
                                                     if k!="observation_sha256"})
        with self.assertRaisesRegex(CarouselError,"mixed_image_claim_invalid"):
            validate_mixed_observation(manifest,obs)

    def test_unknown_audio_cannot_be_forged_by_mismatched_ga09(self):
        manifest,handles=self.make()
        clip=manifest["items"][1]
        proof=audio_decoded(clip,True)
        wrong=DecodedAudio(probe=proof.probe,pcm=proof.pcm,source_sha256="f"*64)
        with self.assertRaisesRegex(CarouselError,"audio_evidence_asset_mismatch"):
            self.extract(manifest,handles,audio_proofs={clip["media_asset_id"]:wrong})

    def test_duplicate_or_unordered_manifest_rejected(self):
        manifest,handles=self.make()
        swapped={**manifest,"items":[dict(x) for x in manifest["items"]]}
        swapped["items"][1]["index"]=1
        swapped["manifest_sha256"]=canonical_sha256({
            k:v for k,v in swapped.items() if k!="manifest_sha256"
        })
        with self.assertRaisesRegex(CarouselError,"invalid_media_manifest"):
            self.extract(swapped,handles)

    def test_video_file_changed_after_manifest_rejected(self):
        manifest,handles=self.make()
        (self.root/"slide2.mp4").write_bytes(movie_header()+b"changed")
        with self.assertRaisesRegex(CarouselError,"video_asset_unavailable"):
            self.extract(manifest,handles)

    def test_image_ocr_missing_does_not_complete_analysis(self):
        manifest,handles=self.make()
        result=self.extract(manifest,handles,backend=None)  # default OCR fixture
        self.assertEqual(result["analysis_state"],"completed")
        self.handles=handles
        blank=extract_mixed_carousel(manifest,open_ref=self.open_ref,
            verify_current=self.rights,read_a003=lambda m,i:binding(m,i),backend=None)
        self.assertEqual(blank["analysis_state"],"partial")
        self.assertEqual(blank["text_status"],"unavailable")

    def test_digest_forgery_and_static_schema(self):
        manifest,handles=self.make()
        result=self.extract(manifest,handles)
        tampered=dict(result,has_audio="true")
        with self.assertRaisesRegex(CarouselError,"mixed_observation_digest_mismatch"):
            validate_mixed_observation(manifest,tampered)
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        path=(Path(__file__).resolve().parents[1]/
              "intelligence/group_a/schemas/ga-mixed-carousel-v1.json")
        schema=json.loads(path.read_text("utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(result)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(schema).validate(dict(result,swipe_seconds="3.000"))

    @unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg not present")
    def test_real_synthetic_mp4_passes_descriptor_validation(self):
        path=self.root/"real.mp4"
        subprocess.run([
            "ffmpeg","-hide_banner","-loglevel","error","-y",
            "-f","lavfi","-i","color=c=red:s=128x128:r=10",
            "-t","0.8","-c:v","mpeg4","-pix_fmt","yuv420p",str(path)
        ],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25)
        manifest,handles=self.make(movies={2:path.read_bytes()})
        result=self.extract(manifest,handles)
        self.assertEqual(result["items"][1]["video_a_analysis_id"],5)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN") and Image is not None,
                     "Disposable PostgreSQL 17 DSN / Pillow not configured")
class GA17PG17(unittest.TestCase):
    def setUp(self):
        import psycopg
        from intelligence.migrate import apply_migrations
        self.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        apply_migrations(self.conn)
        self.addCleanup(self.conn.close)
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source="ga17-"+uuid4().hex
        self.run=str(uuid4())
        token=uuid4().hex
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
               VALUES (%s,'test','owned','enabled',TRUE)""",(self.source,))
        self.policy=self.conn.execute(
            """INSERT INTO source_policy_versions(source_id,version,is_active,permissions)
               VALUES (%s,1,TRUE,'{"can_analyze_media":true}'::jsonb) RETURNING id""",
            (self.source,)
        ).fetchone()[0]
        self.video=self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
               VALUES ('instagram',%s,%s,'mixed_carousel') RETURNING id""",
            (token,"https://instagram.com/p/"+token)
        ).fetchone()[0]
        self.addCleanup(self.conn.execute,"DELETE FROM videos WHERE id=%s",(self.video,))
        self.addCleanup(self.conn.execute,"DELETE FROM sources WHERE id=%s",(self.source,))

    @contextmanager
    def opening(self,ref):
        with open_verified(self.handles[ref],roots=[str(self.root)]) as handle:
            yield handle

    def test_a003_exact_asset_a004_view_replay_immutability_and_source_revocation(self):
        raws=[still(),movie_header(),still((100,150))]
        rows=[]
        for n,(kind,raw) in enumerate(zip(("image","video","image"),raws),1):
            path=self.root/("slide"+str(n)+(".png" if kind=="image" else ".mp4"))
            path.write_bytes(raw)
            aid=self.conn.execute(
                """INSERT INTO media_assets(video_id,source_id,media_kind,local_path,sha256,permission)
                   VALUES (%s,%s,%s,%s,%s,'analysis_allowed') RETURNING id""",
                (self.video,self.source,kind,str(path),sha256(raw).hexdigest())
            ).fetchone()[0]
            rows.append({"media_asset_id":aid,"video_id":self.video,
                         "source_id":self.source,"media_kind":kind,
                         "local_path":str(path),"sha256":sha256(raw).hexdigest(),
                         "byte_size":len(raw)})
        pointer={"schema_version":"qdp-handoff/v1","event_id":str(uuid4()),
                 "project_id":PROJECT,"qdp_run_id":self.run,"video_id":self.video,
                 "analysis_group":"A","input_sha256":"b"*64}
        manifest,self.handles=build_manifest(
            pointer,source_id=self.source,source_policy_version_id=self.policy,
            content_kind="mixed_carousel",ordered_assets=rows,roots=[str(self.root)],
            verified_at=datetime(2026,10,27,tzinfo=timezone.utc)
        )
        video=manifest["items"][1]
        a003_id=self.conn.execute(
            """INSERT INTO video_feature_a_analyses
               (video_id,media_asset_id,source_id,source_policy_version_id,
                analysis_key,analysis_version,method_name,state,measured_at,
                duration_sec,aspect_ratio,measurement_methods,audio_status)
               VALUES (%s,%s,%s,%s,%s,'GA17-fixture','test-verified-A003',
                      'completed',now(),4.000,'1:1',%s::jsonb,'complete')
               RETURNING id""",
            (self.video,video["media_asset_id"],self.source,self.policy,
             uuid4().hex,json.dumps({"ga_asset_sha256":video["sha256"]}))
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO video_feature_a_scenes
               (analysis_id,scene_index,start_sec,end_sec)
               VALUES (%s,1,0.000,4.000)""",(a003_id,)
        )
        reader=PinnedA003SlideReader(self.conn,analysis_ids={video["media_asset_id"]:a003_id})
        seen=[]
        def rights(scope):
            seen.append(scope)
            return True
        result=extract_mixed_carousel(
            manifest,open_ref=self.opening,verify_current=rights,
            read_a003=reader,backend=OCR()
        )
        aid=persist_mixed_carousel(self.conn,manifest,result,verify_current=rights)
        self.assertEqual(persist_mixed_carousel(self.conn,manifest,result,
                                                verify_current=rights),aid)
        summary=self.conn.execute(
            """SELECT state,slide_count,first_text_slide,total_screen_word_count,has_audio
               FROM carousel_feature_a_summary WHERE analysis_id=%s""",(aid,)
        ).fetchone()
        self.assertEqual(summary,("completed",3,None,None,"unknown"))
        linked=self.conn.execute(
            """SELECT slide_index,item_type,video_a_analysis_id,duration_sec
               FROM carousel_feature_a_items WHERE analysis_id=%s ORDER BY slide_index""",
            (aid,)
        ).fetchall()
        self.assertEqual(linked,[
            (1,"image",None,None),(2,"video",a003_id,Decimal("4.000")),(3,"image",None,None)
        ])
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE carousel_feature_a_analyses SET has_audio='true' WHERE id=%s",(aid,))
        # Even though 003 has only convention-based immutability, a timeline
        # edit makes the version-pinned GA17 replay fail closed.
        self.conn.execute(
            "UPDATE video_feature_a_scenes SET end_sec=3.000 WHERE analysis_id=%s",
            (a003_id,)
        )
        with self.assertRaisesRegex(CarouselError,"mixed_a003_binding_mismatch"):
            persist_mixed_carousel(self.conn,manifest,result,verify_current=rights)
        self.conn.execute("UPDATE sources SET can_analyze_media=FALSE WHERE id=%s",(self.source,))
        with self.assertRaises(CarouselError):
            persist_mixed_carousel(self.conn,manifest,result,verify_current=rights)
